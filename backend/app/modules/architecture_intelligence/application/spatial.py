from __future__ import annotations

import hashlib
import json
from collections import defaultdict, deque
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.persistence import append_audit
from app.modules.architecture_intelligence.domain.models import (
    ArchitecturalSpace,
    ArchitectureMetric,
    PrivacyLevel,
    SpaceKind,
    SpatialGraphAnalysis,
    SpatialGraphInput,
    SpatialIssue,
)
from app.modules.architecture_intelligence.infrastructure.models import (
    ArchitecturalZoneMembershipORM,
    ArchitecturalZoneORM,
    ArchitectureAnalysisRunORM,
)
from app.modules.project_graph.domain.models import MeasurementType, calculate_measurement
from app.modules.project_graph.infrastructure.models import (
    BuildingElementORM,
    ElementRelationshipORM,
    ProjectBuildingORM,
    ProjectLevelORM,
)
from app.modules.projects.infrastructure.models import ProjectORM


class SpatialGraphError(ValueError):
    pass


class ArchitectureSpatialGraphService:
    def __init__(self, session: Session, config_path: Path | None = None) -> None:
        self._session = session
        path = (
            config_path or Path(__file__).resolve().parents[1] / "config" / "architecture_qa.yaml"
        )
        with path.open("r", encoding="utf-8") as stream:
            config = yaml.safe_load(stream)
        if not isinstance(config, dict):
            raise SpatialGraphError("INVALID_ARCHITECTURE_QA_CONFIG")
        self._config = config
        self.analysis_version = str(config["analysis_version"])

    def build_and_analyze(self, data: SpatialGraphInput) -> SpatialGraphAnalysis:
        self._validate_context(data)
        checksum = self._input_checksum(data)
        existing = self._session.scalar(
            select(ArchitectureAnalysisRunORM).where(
                ArchitectureAnalysisRunORM.project_id == data.project_id,
                ArchitectureAnalysisRunORM.analysis_version == self.analysis_version,
                ArchitectureAnalysisRunORM.input_checksum == checksum,
            )
        )
        if existing is not None:
            return self._result(existing, reused=True)

        spaces = self._create_spaces(data)
        relationships = self._create_connections(data, spaces)
        zones = self._create_zones(data, spaces)
        metrics, issues, assumptions = self._analyze(
            data, spaces=spaces, relationships=relationships
        )
        run = ArchitectureAnalysisRunORM(
            project_id=data.project_id,
            analysis_version=self.analysis_version,
            input_checksum=checksum,
            metrics_json=[self._metric_json(item) for item in metrics],
            issues_json=[asdict(item) for item in issues],
            assumptions_json=list(assumptions),
            reviewer=data.reviewer,
        )
        self._session.add(run)
        self._session.flush()
        run.metrics_json = [
            *run.metrics_json,
            {
                "code": "SPATIAL_GRAPH_COUNTS",
                "value": str(len(spaces)),
                "unit": "spaces",
                "source": "PROJECT_GRAPH",
                "zone_count": len(zones),
                "connection_count": len(relationships),
                "space_ids": {key: row.id for key, row in spaces.items()},
                "zone_ids": {row.zone_key: row.id for row in zones},
                "relationship_ids": [row.id for row in relationships],
            },
        ]
        append_audit(
            self._session,
            action="ARCHITECTURE_SPATIAL_GRAPH_ANALYZED",
            aggregate_id=run.id,
            detail={
                "project_id": data.project_id,
                "analysis_version": self.analysis_version,
                "input_checksum": checksum,
                "space_count": len(spaces),
                "zone_count": len(zones),
                "issue_count": len(issues),
            },
            actor_type="ENGINEER",
        )
        self._session.flush()
        return self._result(run, reused=False)

    def latest(self, project_id: str) -> SpatialGraphAnalysis:
        row = self._session.scalar(
            select(ArchitectureAnalysisRunORM)
            .where(ArchitectureAnalysisRunORM.project_id == project_id)
            .order_by(ArchitectureAnalysisRunORM.created_at.desc(), ArchitectureAnalysisRunORM.id)
        )
        if row is None:
            raise SpatialGraphError("SPATIAL_GRAPH_NOT_FOUND")
        return self._result(row, reused=True)

    def _validate_context(self, data: SpatialGraphInput) -> None:
        if self._session.get(ProjectORM, data.project_id) is None:
            raise SpatialGraphError("PROJECT_NOT_FOUND")
        if data.gross_area_m2 <= 0:
            raise SpatialGraphError("GROSS_AREA_MUST_BE_POSITIVE")
        if data.building_id:
            building = self._session.get(ProjectBuildingORM, data.building_id)
            if building is None or building.project_id != data.project_id:
                raise SpatialGraphError("BUILDING_NOT_IN_PROJECT")
        if data.level_id:
            level = self._session.get(ProjectLevelORM, data.level_id)
            if level is None or level.project_id != data.project_id:
                raise SpatialGraphError("LEVEL_NOT_IN_PROJECT")
        keys = [item.key for item in data.spaces]
        if not keys or len(keys) != len(set(keys)):
            raise SpatialGraphError("SPACE_KEYS_MUST_BE_UNIQUE")
        known = set(keys)
        for connection in data.connections:
            if connection.source_key == connection.target_key:
                raise SpatialGraphError("SELF_CONNECTION_NOT_ALLOWED")
            if {connection.source_key, connection.target_key} - known:
                raise SpatialGraphError("CONNECTION_SPACE_NOT_FOUND")
        zone_keys = [item.key for item in data.zones]
        if len(zone_keys) != len(set(zone_keys)):
            raise SpatialGraphError("ZONE_KEYS_MUST_BE_UNIQUE")
        for zone in data.zones:
            if set(zone.member_space_keys) - known:
                raise SpatialGraphError("ZONE_SPACE_NOT_FOUND")
        for space in data.spaces:
            self._validated_area(space)

    def _create_spaces(self, data: SpatialGraphInput) -> dict[str, BuildingElementORM]:
        rows: dict[str, BuildingElementORM] = {}
        for space in data.spaces:
            source_id = f"ARCHSPACE:{space.key}"
            existing = self._session.scalar(
                select(BuildingElementORM).where(
                    BuildingElementORM.project_id == data.project_id,
                    BuildingElementORM.source_external_id == source_id,
                    BuildingElementORM.extraction_version == self.analysis_version,
                    ~BuildingElementORM.review_status.in_(("REJECTED", "SUPERSEDED")),
                )
            )
            area = self._validated_area(space)
            geometry = {
                "geometry_type": "POLYGON",
                "points": [list(point) for point in space.boundary_m],
                "closed": True,
                "unit": "m",
            }
            properties = {
                "space_key": space.key,
                "function": space.function,
                "space_kind": space.kind.value,
                "privacy": space.privacy.value,
                "wet_area": space.wet_area,
                "daylight_required": space.daylight_required,
                "has_exterior_opening": space.has_exterior_opening,
                "accessible_required": space.accessible_required,
                "accessible": space.accessible,
            }
            if existing is not None:
                if existing.geometry_json != geometry or existing.properties_json != properties:
                    raise SpatialGraphError("SPATIAL_GRAPH_REVISION_REQUIRED")
                rows[space.key] = existing
                continue
            row = BuildingElementORM(
                project_id=data.project_id,
                building_id=data.building_id,
                level_id=data.level_id,
                discipline="ARCHITECTURAL",
                element_type="ROOM",
                subtype=space.kind.value,
                name=space.name,
                geometry_type="POLYGON",
                geometry_json=geometry,
                area=area,
                properties_json=properties,
                source_type="MANUAL",
                source_document_id=None,
                source_sheet_id=None,
                source_view_region_id=None,
                source_external_id=source_id,
                source_coordinates={"coordinate_system": "PROJECT_METERS"},
                extraction_method="MANUAL_ARCHITECTURAL_PROGRAM",
                extraction_model=None,
                extraction_version=self.analysis_version,
                confidence=Decimal("1"),
                review_status="VERIFIED",
                revision=1,
                reviewer=data.reviewer,
            )
            self._session.add(row)
            self._session.flush()
            rows[space.key] = row
        return rows

    def _create_connections(
        self,
        data: SpatialGraphInput,
        spaces: dict[str, BuildingElementORM],
    ) -> list[ElementRelationshipORM]:
        rows: list[ElementRelationshipORM] = []
        for connection in data.connections:
            left, right = sorted(
                (spaces[connection.source_key], spaces[connection.target_key]),
                key=lambda row: row.id,
            )
            relationship_type = f"SPACE_{connection.connection_type.value}"
            existing = self._session.scalar(
                select(ElementRelationshipORM).where(
                    ElementRelationshipORM.source_element_id == left.id,
                    ElementRelationshipORM.relationship_type == relationship_type,
                    ElementRelationshipORM.target_element_id == right.id,
                )
            )
            properties = {
                "accessible": connection.accessible,
                "source_space_key": connection.source_key,
                "target_space_key": connection.target_key,
            }
            if existing is not None:
                if existing.properties_json != properties:
                    raise SpatialGraphError("SPATIAL_GRAPH_REVISION_REQUIRED")
                rows.append(existing)
                continue
            row = ElementRelationshipORM(
                project_id=data.project_id,
                source_element_id=left.id,
                target_element_id=right.id,
                relationship_type=relationship_type,
                confidence=Decimal("1"),
                source_type="MANUAL",
                properties_json=properties,
            )
            self._session.add(row)
            self._session.flush()
            rows.append(row)
        return rows

    def _create_zones(
        self,
        data: SpatialGraphInput,
        spaces: dict[str, BuildingElementORM],
    ) -> list[ArchitecturalZoneORM]:
        rows: list[ArchitecturalZoneORM] = []
        for zone in data.zones:
            row = self._session.scalar(
                select(ArchitecturalZoneORM).where(
                    ArchitecturalZoneORM.project_id == data.project_id,
                    ArchitecturalZoneORM.zone_key == zone.key,
                )
            )
            if row is None:
                row = ArchitecturalZoneORM(
                    project_id=data.project_id,
                    building_id=data.building_id,
                    level_id=data.level_id,
                    zone_key=zone.key,
                    name=zone.name,
                    zone_type=zone.zone_type,
                    review_status="VERIFIED",
                    source_type="MANUAL",
                )
                self._session.add(row)
                self._session.flush()
            elif row.name != zone.name or row.zone_type != zone.zone_type:
                raise SpatialGraphError("SPATIAL_GRAPH_REVISION_REQUIRED")
            existing_members = set(
                self._session.scalars(
                    select(ArchitecturalZoneMembershipORM.element_id).where(
                        ArchitecturalZoneMembershipORM.zone_id == row.id
                    )
                )
            )
            requested_members = {spaces[key].id for key in zone.member_space_keys}
            if existing_members and existing_members != requested_members:
                raise SpatialGraphError("SPATIAL_GRAPH_REVISION_REQUIRED")
            for key in zone.member_space_keys:
                exists = self._session.scalar(
                    select(ArchitecturalZoneMembershipORM.id).where(
                        ArchitecturalZoneMembershipORM.zone_id == row.id,
                        ArchitecturalZoneMembershipORM.element_id == spaces[key].id,
                    )
                )
                if not exists:
                    self._session.add(
                        ArchitecturalZoneMembershipORM(
                            project_id=data.project_id,
                            zone_id=row.id,
                            element_id=spaces[key].id,
                        )
                    )
            rows.append(row)
        self._session.flush()
        return rows

    def _analyze(
        self,
        data: SpatialGraphInput,
        *,
        spaces: dict[str, BuildingElementORM],
        relationships: list[ElementRelationshipORM],
    ) -> tuple[list[ArchitectureMetric], list[SpatialIssue], list[str]]:
        by_id = {row.id: row for row in spaces.values()}
        adjacency: dict[str, list[tuple[str, ElementRelationshipORM]]] = defaultdict(list)
        for relationship in relationships:
            adjacency[relationship.source_element_id].append(
                (relationship.target_element_id, relationship)
            )
            adjacency[relationship.target_element_id].append(
                (relationship.source_element_id, relationship)
            )
        net_area = sum((row.area or Decimal("0") for row in spaces.values()), Decimal("0"))
        circulation_area = self._kind_area(spaces.values(), SpaceKind.CIRCULATION)
        core_area = self._kind_area(spaces.values(), SpaceKind.CORE)
        metrics = [
            ArchitectureMetric("GROSS_AREA", data.gross_area_m2, "m2", "PROJECT_CONTEXT"),
            ArchitectureMetric("NET_PROGRAM_AREA", net_area, "m2", "PROJECT_GRAPH"),
            ArchitectureMetric(
                "NET_GROSS_RATIO",
                (net_area / data.gross_area_m2).quantize(Decimal("0.0001")),
                "ratio",
                "PROJECT_GRAPH",
            ),
            ArchitectureMetric(
                "CIRCULATION_RATIO",
                (circulation_area / data.gross_area_m2).quantize(Decimal("0.0001")),
                "ratio",
                "PROJECT_GRAPH",
            ),
            ArchitectureMetric(
                "CORE_RATIO",
                (core_area / data.gross_area_m2).quantize(Decimal("0.0001")),
                "ratio",
                "PROJECT_GRAPH",
            ),
            ArchitectureMetric("SPACE_COUNT", Decimal(len(spaces)), "count", "PROJECT_GRAPH"),
        ]
        issues: list[SpatialIssue] = []
        assumptions: list[str] = []
        thresholds = self._config["thresholds"]
        if net_area > data.gross_area_m2:
            issues.append(
                SpatialIssue(
                    "NET_EXCEEDS_GROSS",
                    "HIGH",
                    "Net program area exceeds the supplied gross area.",
                    tuple(row.id for row in spaces.values()),
                )
            )
        if circulation_area / data.gross_area_m2 > Decimal(
            str(thresholds["maximum_circulation_ratio"])
        ):
            issues.append(
                SpatialIssue(
                    "HIGH_CIRCULATION_RATIO",
                    "MEDIUM",
                    "Circulation area exceeds the configured project ratio.",
                    tuple(
                        row.id
                        for row in spaces.values()
                        if row.properties_json["space_kind"] == SpaceKind.CIRCULATION.value
                    ),
                )
            )
        if core_area / data.gross_area_m2 > Decimal(str(thresholds["maximum_core_ratio"])):
            issues.append(
                SpatialIssue(
                    "HIGH_CORE_RATIO",
                    "MEDIUM",
                    "Core area exceeds the configured project ratio.",
                    tuple(
                        row.id
                        for row in spaces.values()
                        if row.properties_json["space_kind"] == SpaceKind.CORE.value
                    ),
                )
            )
        checks = self._config["checks"]
        if checks["unreachable_spaces"]:
            entrances = [
                row.id
                for row in spaces.values()
                if row.properties_json["space_kind"] == SpaceKind.ENTRANCE.value
            ]
            if entrances:
                reached = self._reachable(entrances, adjacency)
                missing = tuple(row.id for row in spaces.values() if row.id not in reached)
                if missing:
                    issues.append(
                        SpatialIssue(
                            "UNREACHABLE_SPACE",
                            "HIGH",
                            "One or more spaces are not connected to an entrance.",
                            missing,
                        )
                    )
            else:
                assumptions.append("Entrance was not identified; reachability was not asserted.")
        if checks["private_public_direct_connection"]:
            for relationship in relationships:
                left = by_id[relationship.source_element_id]
                right = by_id[relationship.target_element_id]
                privacy = {left.properties_json["privacy"], right.properties_json["privacy"]}
                if privacy == {
                    PrivacyLevel.PUBLIC.value,
                    PrivacyLevel.PRIVATE.value,
                } and not relationship.relationship_type.endswith("ADJACENT"):
                    issues.append(
                        SpatialIssue(
                            "PRIVACY_CONFLICT",
                            "MEDIUM",
                            "A private space opens directly into a public space.",
                            (left.id, right.id),
                            (relationship.id,),
                        )
                    )
        for row in spaces.values():
            properties = row.properties_json
            if (
                checks["daylight_readiness"]
                and properties["daylight_required"]
                and not properties["has_exterior_opening"]
            ):
                issues.append(
                    SpatialIssue(
                        "DAYLIGHT_NOT_DEMONSTRATED",
                        "MEDIUM",
                        "Required exterior opening evidence is missing.",
                        (row.id,),
                    )
                )
            if (
                checks["accessibility_readiness"]
                and properties["accessible_required"]
                and not properties["accessible"]
            ):
                issues.append(
                    SpatialIssue(
                        "ACCESSIBILITY_CONFLICT",
                        "HIGH",
                        "The space is marked inaccessible although accessible access is required.",
                        (row.id,),
                    )
                )
            if checks["wet_space_core_connection"] and properties["wet_area"]:
                connected_kinds = {
                    by_id[target].properties_json["space_kind"]
                    for target, _relationship in adjacency.get(row.id, [])
                }
                if not connected_kinds.intersection(
                    {SpaceKind.CORE.value, SpaceKind.SERVICE.value}
                ):
                    issues.append(
                        SpatialIssue(
                            "WET_SPACE_CORE_DISCONNECTED",
                            "LOW",
                            "Wet space has no demonstrated adjacency to a core or service space.",
                            (row.id,),
                        )
                    )
            if (
                checks["dead_end_circulation"]
                and properties["space_kind"] == SpaceKind.CIRCULATION.value
                and len(adjacency.get(row.id, [])) <= 1
            ):
                issues.append(
                    SpatialIssue(
                        "DEAD_END_CIRCULATION_CANDIDATE",
                        "LOW",
                        "Circulation space has one or fewer graph connections.",
                        (row.id,),
                    )
                )
        return metrics, issues, assumptions

    @staticmethod
    def _reachable(
        roots: list[str],
        adjacency: dict[str, list[tuple[str, ElementRelationshipORM]]],
    ) -> set[str]:
        reached = set(roots)
        pending = deque(roots)
        while pending:
            current = pending.popleft()
            for target, relationship in adjacency.get(current, []):
                if not relationship.properties_json.get("accessible", True) or target in reached:
                    continue
                reached.add(target)
                pending.append(target)
        return reached

    @staticmethod
    def _kind_area(rows: Any, kind: SpaceKind) -> Decimal:
        return sum(
            (
                row.area or Decimal("0")
                for row in rows
                if row.properties_json["space_kind"] == kind.value
            ),
            Decimal("0"),
        )

    @staticmethod
    def _validated_area(space: ArchitecturalSpace) -> Decimal:
        points = [list(point) for point in space.boundary_m]
        if len(points) < 3 or len({tuple(point) for point in points}) < 3:
            raise SpatialGraphError("INVALID_SPACE_BOUNDARY")
        if ArchitectureSpatialGraphService._self_intersects(points):
            raise SpatialGraphError("INVALID_SPACE_BOUNDARY")
        area = Decimal(
            str(
                round(
                    calculate_measurement(
                        MeasurementType.AREA,
                        {"points": points},
                        coordinate_scale=1,
                    ),
                    6,
                )
            )
        )
        if area <= 0:
            raise SpatialGraphError("INVALID_SPACE_BOUNDARY")
        return area

    @staticmethod
    def _self_intersects(points: list[list[float]]) -> bool:
        def orientation(a: list[float], b: list[float], c: list[float]) -> float:
            return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])

        edges = list(zip(points, points[1:] + points[:1], strict=True))
        for first_index, (a, b) in enumerate(edges):
            for second_index, (c, d) in enumerate(edges):
                if abs(first_index - second_index) <= 1 or {
                    first_index,
                    second_index,
                } == {0, len(edges) - 1}:
                    continue
                if (
                    orientation(a, b, c) * orientation(a, b, d) < 0
                    and orientation(c, d, a) * orientation(c, d, b) < 0
                ):
                    return True
        return False

    def _input_checksum(self, data: SpatialGraphInput) -> str:
        payload = {
            "project_id": data.project_id,
            "building_id": data.building_id,
            "level_id": data.level_id,
            "gross_area_m2": str(data.gross_area_m2),
            "spaces": [asdict(item) for item in data.spaces],
            "connections": [asdict(item) for item in data.connections],
            "zones": [asdict(item) for item in data.zones],
            "analysis_version": self.analysis_version,
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, default=str, separators=(",", ":")).encode()
        ).hexdigest()

    @staticmethod
    def _metric_json(item: ArchitectureMetric) -> dict[str, str]:
        return {
            "code": item.code,
            "value": str(item.value),
            "unit": item.unit,
            "source": item.source,
        }

    def _result(self, run: ArchitectureAnalysisRunORM, *, reused: bool) -> SpatialGraphAnalysis:
        counts = next(
            (item for item in run.metrics_json if item["code"] == "SPATIAL_GRAPH_COUNTS"),
            {},
        )
        metrics = tuple(
            ArchitectureMetric(
                code=str(item["code"]),
                value=Decimal(str(item["value"])),
                unit=str(item["unit"]),
                source=str(item["source"]),
            )
            for item in run.metrics_json
            if item["code"] != "SPATIAL_GRAPH_COUNTS"
        )
        issues = tuple(
            SpatialIssue(
                code=str(item["code"]),
                severity=str(item["severity"]),
                message=str(item["message"]),
                evidence_element_ids=tuple(item["evidence_element_ids"]),
                evidence_relationship_ids=tuple(item.get("evidence_relationship_ids", [])),
            )
            for item in run.issues_json
        )
        space_ids = dict(counts.get("space_ids", {}))
        zone_ids = dict(counts.get("zone_ids", {}))
        return SpatialGraphAnalysis(
            analysis_id=run.id,
            project_id=run.project_id,
            reused=reused,
            metrics=metrics,
            issues=issues,
            assumptions=tuple(run.assumptions_json),
            spaces=tuple(
                {"key": key, "element_id": value} for key, value in sorted(space_ids.items())
            ),
            zones=tuple({"key": key, "zone_id": value} for key, value in sorted(zone_ids.items())),
            connections=tuple(
                {"relationship_id": value} for value in counts.get("relationship_ids", [])
            ),
            analysis_version=run.analysis_version,
            input_checksum=run.input_checksum,
        )
