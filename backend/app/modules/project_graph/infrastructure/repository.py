from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import Any

from sqlalchemy import exists, select
from sqlalchemy.orm import Session, aliased

from app.modules.project_graph.infrastructure.models import (
    BuildingElementORM,
    MeasurementEvidenceORM,
    MeasurementORM,
    ProjectBuildingORM,
    ProjectLevelORM,
    ScaleCalibrationORM,
    TakeoffItemORM,
    ViewRegionORM,
)


class ProjectGraphRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def has_source(self, document_version_id: str) -> bool:
        return (
            self.session.scalar(
                select(BuildingElementORM.id)
                .where(BuildingElementORM.source_document_id == document_version_id)
                .limit(1)
            )
            is not None
        )

    def list_elements(self, project_id: str) -> list[BuildingElementORM]:
        child = aliased(BuildingElementORM)
        return list(
            self.session.scalars(
                select(BuildingElementORM)
                .where(
                    BuildingElementORM.project_id == project_id,
                    ~exists().where(child.supersedes_element_id == BuildingElementORM.id),
                )
                .order_by(BuildingElementORM.element_type, BuildingElementORM.id)
            ).all()
        )

    def list_takeoff(self, project_id: str) -> list[TakeoffItemORM]:
        return list(
            self.session.scalars(
                select(TakeoffItemORM)
                .where(TakeoffItemORM.project_id == project_id)
                .order_by(TakeoffItemORM.service_code, TakeoffItemORM.created_at)
            ).all()
        )

    def evidence_for_takeoff(
        self, item_id: str
    ) -> tuple[TakeoffItemORM, list[MeasurementEvidenceORM]] | None:
        item = self.session.get(TakeoffItemORM, item_id)
        if item is None:
            return None
        evidence = list(
            self.session.scalars(
                select(MeasurementEvidenceORM).where(
                    MeasurementEvidenceORM.measurement_id.in_(item.measurement_ids)
                )
            ).all()
        )
        return item, evidence

    def summary(self, project_id: str) -> dict[str, Any]:
        elements = self.list_elements(project_id)
        levels = {
            row.id: row.name
            for row in self.session.scalars(
                select(ProjectLevelORM).where(ProjectLevelORM.project_id == project_id)
            ).all()
        }
        grouped: dict[str, dict[str, Any]] = defaultdict(
            lambda: {
                "count": 0,
                "area": 0.0,
                "gross_side_area": 0.0,
                "net_side_area": 0.0,
                "volume": 0.0,
                "storeys": defaultdict(int),
            }
        )
        for element in elements:
            data = grouped[element.element_type]
            data["count"] += 1
            data["area"] += float(element.area or 0)
            data["volume"] += float(element.volume or 0)
            quantities = element.properties_json.get("ifc_quantities", {})
            data["gross_side_area"] += float(quantities.get("GrossSideArea", 0))
            data["net_side_area"] += float(quantities.get("NetSideArea", 0))
            data["storeys"][levels.get(element.level_id or "", "Atanmamış")] += 1
        return {
            "project_id": project_id,
            "building_count": self._count(ProjectBuildingORM, project_id),
            "level_count": len(levels),
            "element_count": len(elements),
            "elements": {
                key: {
                    **value,
                    "area": round(value["area"], 6),
                    "gross_side_area": round(value["gross_side_area"], 6),
                    "net_side_area": round(value["net_side_area"], 6),
                    "volume": round(value["volume"], 6),
                    "storeys": dict(value["storeys"]),
                }
                for key, value in grouped.items()
            },
        }

    def create_view_region(self, **values: Any) -> ViewRegionORM:
        row = ViewRegionORM(**values)
        self.session.add(row)
        self.session.flush()
        return row

    def create_calibration(self, **values: Any) -> ScaleCalibrationORM:
        row = ScaleCalibrationORM(**values)
        self.session.add(row)
        self.session.flush()
        return row

    def create_measurement_with_evidence(
        self,
        *,
        measurement_values: dict[str, Any],
        evidence_values: dict[str, Any],
    ) -> tuple[MeasurementORM, MeasurementEvidenceORM]:
        measurement = MeasurementORM(**measurement_values)
        self.session.add(measurement)
        self.session.flush()
        evidence = MeasurementEvidenceORM(measurement_id=measurement.id, **evidence_values)
        self.session.add(evidence)
        self.session.flush()
        return measurement, evidence

    def create_takeoff(self, **values: Any) -> TakeoffItemORM:
        measurement_ids = values.get("measurement_ids", [])
        if measurement_ids:
            measurements = self.session.scalars(
                select(MeasurementORM).where(MeasurementORM.id.in_(measurement_ids))
            ).all()
            if len(measurements) != len(set(measurement_ids)) or any(
                row.measurement_purpose != "ENGINEERING_QUANTITY" for row in measurements
            ):
                raise ValueError("Final takeoff requires ENGINEERING_QUANTITY measurements")
        row = TakeoffItemORM(**values)
        self.session.add(row)
        self.session.flush()
        return row

    def _count(self, model: Any, project_id: str) -> int:
        return len(
            self.session.scalars(select(model.id).where(model.project_id == project_id)).all()
        )


def decimal_or_none(value: float | None) -> Decimal | None:
    return Decimal(str(round(value, 6))) if value is not None else None
