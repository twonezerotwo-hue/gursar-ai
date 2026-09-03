from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.persistence import append_audit
from app.modules.project_graph.domain.models import Discipline, ElementType, ReviewStatus
from app.modules.project_graph.infrastructure.models import (
    BuildingElementORM,
    ElementRelationshipORM,
    ProjectLevelORM,
)
from app.modules.projects.infrastructure.models import ProjectORM
from app.modules.structural_intelligence.domain.models import (
    StructuralGraphAnalysis,
    StructuralGraphInput,
    StructuralIssue,
    StructuralMetric,
)
from app.modules.structural_intelligence.infrastructure.models import (
    StructuralAnalysisRunORM,
    StructuralLoadORM,
)


class StructuralGraphError(ValueError):
    pass


class StructuralGraphService:
    def __init__(self, session: Session, config_path: Path | None = None) -> None:
        self._session = session
        path = config_path or Path(__file__).resolve().parents[1] / "config" / "structural_qa.yaml"
        with path.open("r", encoding="utf-8") as stream:
            config = yaml.safe_load(stream)
        if not isinstance(config, dict):
            raise StructuralGraphError("INVALID_STRUCTURAL_QA_CONFIG")
        self._config = config
        self.analysis_version = str(config["analysis_version"])

    def build_and_analyze(self, data: StructuralGraphInput) -> StructuralGraphAnalysis:
        elements = self._validate(data)
        checksum = self._input_checksum(data)
        existing = self._session.scalar(
            select(StructuralAnalysisRunORM).where(
                StructuralAnalysisRunORM.project_id == data.project_id,
                StructuralAnalysisRunORM.analysis_version == self.analysis_version,
                StructuralAnalysisRunORM.input_checksum == checksum,
            )
        )
        if existing is not None:
            return self._result(existing, reused=True)

        relationships = self._create_relationships(data, elements)
        metrics, issues, assumptions = self._analyze(data, elements, relationships)
        run = StructuralAnalysisRunORM(
            project_id=data.project_id,
            analysis_version=self.analysis_version,
            input_checksum=checksum,
            metrics_json=[self._metric_json(metric) for metric in metrics],
            issues_json=[asdict(issue) for issue in issues],
            assumptions_json=assumptions,
            graph_json={
                "element_ids": sorted(elements),
                "relationship_ids": sorted(row.id for row in relationships),
                "load_ids": [],
            },
            reviewer=data.reviewer,
        )
        self._session.add(run)
        self._session.flush()
        loads = [
            StructuralLoadORM(
                project_id=data.project_id,
                analysis_run_id=run.id,
                element_id=load.element_id,
                load_key=load.key,
                load_type=load.load_type.value,
                magnitude_kn=load.magnitude_kn,
                factor=load.factor,
                direction=load.direction,
                source_reference=load.source_reference,
            )
            for load in data.loads
        ]
        self._session.add_all(loads)
        self._session.flush()
        run.graph_json = {**run.graph_json, "load_ids": sorted(load.id for load in loads)}
        append_audit(
            self._session,
            action="STRUCTURAL_LOAD_PATH_ANALYZED",
            aggregate_id=run.id,
            detail={
                "project_id": data.project_id,
                "analysis_version": self.analysis_version,
                "input_checksum": checksum,
                "element_count": len(elements),
                "relationship_count": len(relationships),
                "load_count": len(loads),
                "issue_count": len(issues),
            },
            actor_type="ENGINEER",
        )
        self._session.flush()
        return self._result(run, reused=False)

    def latest(self, project_id: str) -> StructuralGraphAnalysis:
        run = self._session.scalar(
            select(StructuralAnalysisRunORM)
            .where(StructuralAnalysisRunORM.project_id == project_id)
            .order_by(StructuralAnalysisRunORM.created_at.desc(), StructuralAnalysisRunORM.id)
        )
        if run is None:
            raise StructuralGraphError("STRUCTURAL_GRAPH_NOT_FOUND")
        return self._result(run, reused=True)

    def _validate(self, data: StructuralGraphInput) -> dict[str, BuildingElementORM]:
        if self._session.get(ProjectORM, data.project_id) is None:
            raise StructuralGraphError("PROJECT_NOT_FOUND")
        if not data.element_ids or len(data.element_ids) != len(set(data.element_ids)):
            raise StructuralGraphError("ELEMENT_IDS_MUST_BE_UNIQUE")
        elements = {
            row.id: row
            for row in self._session.scalars(
                select(BuildingElementORM).where(BuildingElementORM.id.in_(data.element_ids))
            )
        }
        if set(data.element_ids) != set(elements):
            raise StructuralGraphError("ELEMENT_NOT_FOUND")
        structural_types = {
            ElementType.WALL.value,
            ElementType.COLUMN.value,
            ElementType.SHEAR_WALL.value,
            ElementType.BEAM.value,
            ElementType.SLAB.value,
            ElementType.FOUNDATION.value,
            ElementType.FOOTING.value,
            ElementType.PILE.value,
        }
        for element in elements.values():
            if element.project_id != data.project_id:
                raise StructuralGraphError("ELEMENT_NOT_IN_PROJECT")
            if (
                element.discipline != Discipline.STRUCTURAL.value
                or element.element_type not in structural_types
            ):
                raise StructuralGraphError("NON_STRUCTURAL_ELEMENT")
            if element.review_status in {
                ReviewStatus.REJECTED.value,
                ReviewStatus.SUPERSEDED.value,
            }:
                raise StructuralGraphError("INACTIVE_STRUCTURAL_ELEMENT")
        edge_keys: set[tuple[str, str]] = set()
        for edge in data.supports:
            if edge.supported_element_id == edge.supporting_element_id:
                raise StructuralGraphError("SELF_SUPPORT_NOT_ALLOWED")
            if {edge.supported_element_id, edge.supporting_element_id} - set(elements):
                raise StructuralGraphError("SUPPORT_ELEMENT_NOT_FOUND")
            key = (edge.supported_element_id, edge.supporting_element_id)
            if key in edge_keys:
                raise StructuralGraphError("DUPLICATE_SUPPORT")
            edge_keys.add(key)
        load_keys = [load.key for load in data.loads]
        if len(load_keys) != len(set(load_keys)):
            raise StructuralGraphError("LOAD_KEYS_MUST_BE_UNIQUE")
        for load in data.loads:
            if load.element_id not in elements:
                raise StructuralGraphError("LOAD_ELEMENT_NOT_FOUND")
            if load.magnitude_kn < 0 or load.factor <= 0:
                raise StructuralGraphError("INVALID_LOAD_VALUE")
        return elements

    def _create_relationships(
        self,
        data: StructuralGraphInput,
        elements: dict[str, BuildingElementORM],
    ) -> list[ElementRelationshipORM]:
        rows: list[ElementRelationshipORM] = []
        for edge in data.supports:
            existing = self._session.scalar(
                select(ElementRelationshipORM).where(
                    ElementRelationshipORM.source_element_id == edge.supporting_element_id,
                    ElementRelationshipORM.relationship_type == "STRUCTURAL_SUPPORTS",
                    ElementRelationshipORM.target_element_id == edge.supported_element_id,
                )
            )
            properties = {
                "connection_type": edge.connection_type.value,
                "load_path_enabled": edge.load_path_enabled,
            }
            if existing is not None:
                if existing.properties_json != properties:
                    raise StructuralGraphError("STRUCTURAL_GRAPH_REVISION_REQUIRED")
                rows.append(existing)
                continue
            row = ElementRelationshipORM(
                project_id=data.project_id,
                source_element_id=elements[edge.supporting_element_id].id,
                target_element_id=elements[edge.supported_element_id].id,
                relationship_type="STRUCTURAL_SUPPORTS",
                confidence=Decimal("1"),
                source_type="MANUAL",
                properties_json=properties,
            )
            self._session.add(row)
            self._session.flush()
            rows.append(row)
        return rows

    def _analyze(
        self,
        data: StructuralGraphInput,
        elements: dict[str, BuildingElementORM],
        relationships: list[ElementRelationshipORM],
    ) -> tuple[list[StructuralMetric], list[StructuralIssue], list[str]]:
        edge_by_pair = {
            (row.target_element_id, row.source_element_id): row for row in relationships
        }
        support_graph: dict[str, list[str]] = defaultdict(list)
        for edge in data.supports:
            if edge.load_path_enabled:
                support_graph[edge.supported_element_id].append(edge.supporting_element_id)
        terminals = {
            element_id
            for element_id, element in elements.items()
            if element.element_type in set(self._config["terminal_element_types"])
        }
        issues: list[StructuralIssue] = []
        assumptions = [
            "This result checks declared topology only; it is not a structural design "
            "or capacity approval."
        ]
        allowed_supports = self._config["allowed_supports"]
        tolerance = Decimal(str(self._config["elevation_tolerance_m"]))
        level_ids = {row.level_id for row in elements.values() if row.level_id}
        levels = {
            row.id: row
            for row in self._session.scalars(
                select(ProjectLevelORM).where(ProjectLevelORM.id.in_(level_ids))
            )
        }

        for edge in data.supports:
            supported = elements[edge.supported_element_id]
            supporting = elements[edge.supporting_element_id]
            relationship = edge_by_pair[(supported.id, supporting.id)]
            allowed = set(allowed_supports.get(supported.element_type, []))
            if supporting.element_type not in allowed:
                issues.append(
                    StructuralIssue(
                        "INCOMPATIBLE_SUPPORT_TYPE",
                        "HIGH",
                        f"{supported.element_type} is not configured to bear on "
                        f"{supporting.element_type}.",
                        (supported.id, supporting.id),
                        (relationship.id,),
                    )
                )
            supported_level = levels.get(supported.level_id or "")
            supporting_level = levels.get(supporting.level_id or "")
            if (
                supported_level is not None
                and supporting_level is not None
                and supported_level.elevation is not None
                and supporting_level.elevation is not None
                and supporting_level.elevation > supported_level.elevation + tolerance
            ):
                issues.append(
                    StructuralIssue(
                        "SUPPORT_ABOVE_SUPPORTED_ELEMENT",
                        "HIGH",
                        "The declared supporting element is on a higher level.",
                        (supported.id, supporting.id),
                        (relationship.id,),
                    )
                )

        cycles = self._cycle_nodes(support_graph)
        if cycles:
            issues.append(
                StructuralIssue(
                    "LOAD_PATH_CYCLE",
                    "HIGH",
                    "The declared support graph contains a cycle.",
                    tuple(sorted(cycles)),
                )
            )
        for element_id in sorted(elements):
            if element_id in terminals:
                continue
            direct_supports = support_graph.get(element_id, [])
            if not direct_supports:
                issues.append(
                    StructuralIssue(
                        "UNSUPPORTED_STRUCTURAL_ELEMENT",
                        "HIGH",
                        "The structural element has no declared downward support.",
                        (element_id,),
                    )
                )
            elif not self._reaches_terminal(element_id, support_graph, terminals):
                relation_ids = tuple(
                    edge_by_pair[(element_id, supporter)].id for supporter in direct_supports
                )
                issues.append(
                    StructuralIssue(
                        "LOAD_PATH_NOT_TO_FOUNDATION",
                        "HIGH",
                        "The declared support path does not terminate at foundation, "
                        "footing or pile.",
                        (element_id,),
                        relation_ids,
                    )
                )

        required_properties = self._config["required_properties"]
        for element in elements.values():
            required = required_properties.get(element.element_type, [])
            missing = [name for name in required if not getattr(element, name, None)]
            if missing:
                issues.append(
                    StructuralIssue(
                        "STRUCTURAL_PROPERTIES_INCOMPLETE",
                        "MEDIUM",
                        f"Required structural properties are missing: {', '.join(missing)}.",
                        (element.id,),
                    )
                )
            if element.review_status not in {
                ReviewStatus.VERIFIED.value,
                ReviewStatus.CORRECTED.value,
            }:
                issues.append(
                    StructuralIssue(
                        "STRUCTURAL_ELEMENT_REVIEW_REQUIRED",
                        "MEDIUM",
                        "The load path includes an element that is not expert verified.",
                        (element.id,),
                    )
                )

        factored_load = sum((load.magnitude_kn * load.factor for load in data.loads), Decimal("0"))
        verified_count = sum(
            element.review_status in {ReviewStatus.VERIFIED.value, ReviewStatus.CORRECTED.value}
            for element in elements.values()
        )
        metrics = [
            StructuralMetric(
                "STRUCTURAL_ELEMENT_COUNT", Decimal(len(elements)), "count", "PROJECT_GRAPH"
            ),
            StructuralMetric(
                "SUPPORT_RELATION_COUNT", Decimal(len(relationships)), "count", "PROJECT_GRAPH"
            ),
            StructuralMetric(
                "TERMINAL_SUPPORT_COUNT", Decimal(len(terminals)), "count", "PROJECT_GRAPH"
            ),
            StructuralMetric(
                "DECLARED_LOAD_COUNT", Decimal(len(data.loads)), "count", "USER_INPUT"
            ),
            StructuralMetric("DECLARED_FACTORED_LOAD", factored_load, "kN", "USER_INPUT"),
            StructuralMetric(
                "VERIFIED_ELEMENT_RATIO",
                (Decimal(verified_count) / Decimal(len(elements))).quantize(Decimal("0.0001")),
                "ratio",
                "PROJECT_GRAPH",
            ),
        ]
        return metrics, issues, assumptions

    @staticmethod
    def _cycle_nodes(graph: dict[str, list[str]]) -> set[str]:
        visiting: set[str] = set()
        visited: set[str] = set()
        cycles: set[str] = set()

        def visit(node: str, path: list[str]) -> None:
            if node in visiting:
                start = path.index(node)
                cycles.update(path[start:])
                return
            if node in visited:
                return
            visiting.add(node)
            path.append(node)
            for target in graph.get(node, []):
                visit(target, path)
            path.pop()
            visiting.remove(node)
            visited.add(node)

        for node in graph:
            visit(node, [])
        return cycles

    @staticmethod
    def _reaches_terminal(
        start: str,
        graph: dict[str, list[str]],
        terminals: set[str],
    ) -> bool:
        pending = [start]
        visited: set[str] = set()
        while pending:
            node = pending.pop()
            if node in terminals:
                return True
            if node in visited:
                continue
            visited.add(node)
            pending.extend(graph.get(node, []))
        return False

    def _input_checksum(self, data: StructuralGraphInput) -> str:
        payload = {
            "project_id": data.project_id,
            "element_ids": sorted(data.element_ids),
            "supports": sorted(
                (asdict(edge) for edge in data.supports),
                key=lambda edge: (
                    str(edge["supported_element_id"]),
                    str(edge["supporting_element_id"]),
                ),
            ),
            "loads": sorted(
                (asdict(load) for load in data.loads), key=lambda load: str(load["key"])
            ),
            "analysis_version": self.analysis_version,
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, default=str, separators=(",", ":")).encode()
        ).hexdigest()

    @staticmethod
    def _metric_json(metric: StructuralMetric) -> dict[str, str]:
        return {
            "code": metric.code,
            "value": str(metric.value),
            "unit": metric.unit,
            "source": metric.source,
        }

    @staticmethod
    def _result(run: StructuralAnalysisRunORM, *, reused: bool) -> StructuralGraphAnalysis:
        return StructuralGraphAnalysis(
            analysis_id=run.id,
            project_id=run.project_id,
            reused=reused,
            metrics=tuple(
                StructuralMetric(
                    code=str(item["code"]),
                    value=Decimal(str(item["value"])),
                    unit=str(item["unit"]),
                    source=str(item["source"]),
                )
                for item in run.metrics_json
            ),
            issues=tuple(
                StructuralIssue(
                    code=str(item["code"]),
                    severity=str(item["severity"]),
                    message=str(item["message"]),
                    evidence_element_ids=tuple(item["evidence_element_ids"]),
                    evidence_relationship_ids=tuple(item.get("evidence_relationship_ids", [])),
                )
                for item in run.issues_json
            ),
            assumptions=tuple(run.assumptions_json),
            element_ids=tuple(run.graph_json["element_ids"]),
            relationship_ids=tuple(run.graph_json["relationship_ids"]),
            load_ids=tuple(run.graph_json["load_ids"]),
            analysis_version=run.analysis_version,
            input_checksum=run.input_checksum,
        )
