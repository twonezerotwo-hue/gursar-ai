from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.persistence import append_audit, append_outbox
from app.modules.documents.infrastructure.models import DocumentVersionORM, SheetORM
from app.modules.project_graph.application.ifc import IfcQuantityExtractor, discipline_for
from app.modules.project_graph.domain.models import (
    MeasurementType,
    ReviewStatus,
    ScaleMethod,
    SourceType,
    calculate_measurement,
)
from app.modules.project_graph.infrastructure.models import (
    BuildingElementORM,
    MeasurementEvidenceORM,
    MeasurementORM,
    ProjectBuildingORM,
    ProjectLevelORM,
    QuantityConflictORM,
    ScaleCalibrationORM,
    TakeoffItemORM,
    ViewRegionORM,
)
from app.modules.project_graph.infrastructure.repository import (
    ProjectGraphRepository,
    decimal_or_none,
)
from app.modules.projects.infrastructure.models import ProjectORM


class ProjectGraphNotFoundError(LookupError):
    pass


class ProjectDocumentVersionError(ValueError):
    pass


class InvalidMeasurementError(ValueError):
    pass


class ProjectGraphService:
    algorithm_version = "project-graph-v1"

    def __init__(self, session: Session) -> None:
        self._session = session
        self._repository = ProjectGraphRepository(session)
        self._ifc = IfcQuantityExtractor()

    def analyze_ifc(
        self,
        *,
        project_id: str,
        document_version_id: str,
        path: Path,
    ) -> dict[str, Any]:
        self._require_project(project_id)
        version = self._session.get(DocumentVersionORM, document_version_id)
        if version is None or version.project_id != project_id:
            raise ProjectDocumentVersionError(document_version_id)
        if version.media_type != "application/x-step":
            raise ProjectDocumentVersionError("The document version is not an IFC STEP model")
        if self._repository.has_source(document_version_id):
            return {**self._repository.summary(project_id), "reused": True}

        extraction = self._ifc.extract(path)
        building_rows: dict[str, ProjectBuildingORM] = {}
        for item in extraction.buildings:
            row = ProjectBuildingORM(
                project_id=project_id,
                name=item.name,
                source_key=item.source_key,
                source_document_id=document_version_id,
            )
            self._session.add(row)
            self._session.flush()
            building_rows[item.source_key] = row
        default_building = next(iter(building_rows.values()), None)

        level_rows: dict[str, ProjectLevelORM] = {}
        for item in extraction.levels:
            row = ProjectLevelORM(
                project_id=project_id,
                building_id=default_building.id if default_building else None,
                name=item.name,
                elevation=decimal_or_none(item.elevation),
                source_key=item.source_key,
            )
            self._session.add(row)
            self._session.flush()
            level_rows[item.source_key] = row

        grouped_measurements: dict[tuple[str, str, str], list[MeasurementORM]] = defaultdict(list)
        for item in extraction.elements:
            dimensions = item.dimensions
            element = BuildingElementORM(
                project_id=project_id,
                building_id=default_building.id if default_building else None,
                level_id=(
                    level_rows[item.level_source_key].id
                    if item.level_source_key in level_rows
                    else None
                ),
                discipline=discipline_for(item.element_type).value,
                element_type=item.element_type.value,
                subtype=item.predefined_type or item.object_type or item.ifc_class,
                name=item.name,
                geometry_type="IFC_BREP" if item.geometry_quantities else None,
                geometry_json={"geometry_quantities": item.geometry_quantities},
                length=decimal_or_none(dimensions.get("length")),
                width=decimal_or_none(dimensions.get("width")),
                height=decimal_or_none(dimensions.get("height")),
                thickness=decimal_or_none(dimensions.get("thickness")),
                area=decimal_or_none(dimensions.get("area")),
                volume=decimal_or_none(dimensions.get("volume")),
                material=item.material,
                properties_json={
                    "ifc_class": item.ifc_class,
                    "object_type": item.object_type,
                    "property_sets": item.properties,
                    "ifc_quantities": item.stated_quantities,
                    "geometry_quantities": item.geometry_quantities,
                },
                source_type=SourceType.IFC.value,
                source_document_id=document_version_id,
                source_external_id=item.source_key,
                source_coordinates={"ifc_global_id": item.source_key},
                extraction_method=item.extraction_method,
                extraction_model="IfcOpenShell",
                extraction_version=self._ifc.version,
                confidence=Decimal("0.98")
                if item.extraction_method == "IFC_QUANTITY_SET"
                else Decimal("0.90"),
                review_status=ReviewStatus.AUTO.value,
            )
            self._session.add(element)
            self._session.flush()
            self._record_ifc_quantity_conflicts(
                project_id=project_id,
                element=element,
                stated=item.stated_quantities,
                geometry=item.geometry_quantities,
            )
            candidates = [
                (MeasurementType.COUNT, 1.0, "adet"),
                (MeasurementType.LENGTH, dimensions.get("length"), "m"),
                (MeasurementType.AREA, dimensions.get("area"), "m2"),
                (MeasurementType.VOLUME, dimensions.get("volume"), "m3"),
            ]
            for measurement_type, value, unit in candidates:
                if value is None:
                    continue
                measurement = self._create_ifc_measurement(
                    project_id=project_id,
                    document_version_id=document_version_id,
                    element=element,
                    measurement_type=measurement_type,
                    value=float(value),
                    unit=unit,
                    method=item.extraction_method,
                )
                grouped_measurements[(element.element_type, measurement_type.value, unit)].append(
                    measurement
                )

        for (element_type, measurement_type, unit), measurements in grouped_measurements.items():
            self._repository.create_takeoff(
                project_id=project_id,
                service_code=f"ifc.{element_type.lower()}.{measurement_type.lower()}",
                description=f"{element_type} {measurement_type}",
                quantity=sum((item.calculated_value for item in measurements), Decimal("0")),
                unit=unit,
                measurement_ids=[item.id for item in measurements],
                calculation_rule="SUM(EVIDENCE_BACKED_IFC_MEASUREMENTS)",
                calculation_rule_version=self.algorithm_version,
                price_reference=None,
                quantity_source="IFC_QUANTITY",
                review_status=ReviewStatus.AUTO.value,
                revision=1,
            )

        append_audit(
            self._session,
            action="IFC_PROJECT_GRAPH_EXTRACTED",
            aggregate_id=project_id,
            detail={
                "document_version_id": document_version_id,
                "schema": extraction.schema,
                "elements": len(extraction.elements),
            },
        )
        append_outbox(
            self._session,
            event_type="project.graph.updated.v1",
            aggregate_id=project_id,
            payload={"source": "IFC", "document_version_id": document_version_id},
        )
        return {**self._repository.summary(project_id), "reused": False}

    def graph(self, project_id: str) -> dict[str, Any]:
        self._require_project(project_id)
        return self._repository.summary(project_id)

    def elements(self, project_id: str) -> list[BuildingElementORM]:
        self._require_project(project_id)
        return self._repository.list_elements(project_id)

    def takeoff(self, project_id: str) -> list[TakeoffItemORM]:
        self._require_project(project_id)
        return self._repository.list_takeoff(project_id)

    def evidence(self, item_id: str) -> tuple[TakeoffItemORM, list[MeasurementEvidenceORM]]:
        result = self._repository.evidence_for_takeoff(item_id)
        if result is None:
            raise ProjectGraphNotFoundError(item_id)
        return result

    def create_view_region(
        self,
        *,
        project_id: str,
        sheet_id: str,
        bbox_normalized: list[float],
        view_type: str,
        level_name: str | None,
    ) -> ViewRegionORM:
        self._require_sheet(project_id, sheet_id)
        if len(bbox_normalized) != 4 or any(value < 0 or value > 1 for value in bbox_normalized):
            raise InvalidMeasurementError("bbox_normalized must contain four values from 0 to 1")
        x1, y1, x2, y2 = bbox_normalized
        if x2 <= x1 or y2 <= y1:
            raise InvalidMeasurementError("bbox_normalized has invalid bounds")
        return self._repository.create_view_region(
            sheet_id=sheet_id,
            bbox_normalized=bbox_normalized,
            view_type=view_type,
            level_name=level_name,
            scale=None,
            scale_confidence=None,
        )

    def create_calibration(
        self,
        *,
        project_id: str,
        view_region_id: str,
        method: ScaleMethod,
        known_distance: float,
        pixel_distance: float,
        drawing_units: str,
        confidence: float,
        verified: bool,
        reviewer: str | None,
    ) -> ScaleCalibrationORM:
        region = self._require_view_region(project_id, view_region_id)
        if known_distance <= 0 or pixel_distance <= 0:
            raise InvalidMeasurementError("Calibration distances must be positive")
        calibration = self._repository.create_calibration(
            view_region_id=view_region_id,
            method=method.value,
            known_distance=Decimal(str(known_distance)),
            pixel_distance=Decimal(str(pixel_distance)),
            drawing_units=drawing_units,
            scale_ratio=Decimal(str(known_distance / pixel_distance)),
            confidence=Decimal(str(confidence)),
            verified=verified,
            reviewer=reviewer,
        )
        if verified:
            region.scale_status = "VERIFIED"
            region.review_status = "VERIFIED"
            region.scale_confidence = Decimal("1")
            region.metadata_json = {
                **(region.metadata_json or {}),
                "scale_calibration_id": calibration.id,
            }
        return calibration

    def create_manual_measurement(
        self,
        *,
        project_id: str,
        measurement_type: MeasurementType,
        geometry: dict[str, Any],
        unit: str,
        service_code: str,
        description: str,
        view_region_id: str | None,
        scale_calibration_id: str | None,
    ) -> tuple[MeasurementORM, MeasurementEvidenceORM, TakeoffItemORM]:
        self._require_project(project_id)
        calibration = None
        scale = 1.0
        if scale_calibration_id:
            calibration = self._session.get(ScaleCalibrationORM, scale_calibration_id)
            if calibration is None or not self._view_region_belongs_to_project(
                project_id, calibration.view_region_id
            ):
                raise InvalidMeasurementError("Scale calibration does not belong to project")
            scale = float(calibration.scale_ratio)
        elif measurement_type is not MeasurementType.COUNT:
            raise InvalidMeasurementError(
                "A scale calibration is required for geometric measurement"
            )
        if view_region_id and not self._view_region_belongs_to_project(project_id, view_region_id):
            raise InvalidMeasurementError("View region does not belong to project")
        source_document_id = source_sheet_id = None
        page_number = None
        if view_region_id:
            region = self._session.get(ViewRegionORM, view_region_id)
            sheet = self._session.get(SheetORM, region.sheet_id) if region else None
            if sheet:
                source_sheet_id = sheet.id
                source_document_id = sheet.document_version_id
                page_number = sheet.page_number
        try:
            raw = calculate_measurement(measurement_type, geometry, coordinate_scale=1.0)
            calculated = calculate_measurement(measurement_type, geometry, coordinate_scale=scale)
        except ValueError as exc:
            raise InvalidMeasurementError(str(exc)) from exc
        measurement, evidence = self._repository.create_measurement_with_evidence(
            measurement_values={
                "project_id": project_id,
                "element_id": None,
                "measurement_type": measurement_type.value,
                "geometry_json": geometry,
                "raw_value": Decimal(str(round(raw, 6))),
                "unit": unit,
                "scale_calibration_id": scale_calibration_id,
                "calculated_value": Decimal(str(round(calculated, 6))),
                "source_document_id": source_document_id,
                "source_sheet_id": source_sheet_id,
                "source_view_region_id": view_region_id,
                "method": SourceType.MANUAL.value,
                "algorithm_version": self.algorithm_version,
                "confidence": Decimal("1.0")
                if calibration and calibration.verified
                else Decimal("0.85"),
                "review_status": ReviewStatus.VERIFIED.value,
                "revision": 1,
            },
            evidence_values={
                "source_coordinates": {
                    "document_version_id": source_document_id,
                    "sheet_id": source_sheet_id,
                    "view_region_id": view_region_id,
                },
                "geometry_json": geometry,
                "page_number": page_number,
                "view_region_id": view_region_id,
                "scale_json": {"ratio": scale, "calibration_id": scale_calibration_id},
                "thumbnail_reference": None,
                "algorithm": "SERVER_GEOMETRY_CALCULATION",
                "model_version": self.algorithm_version,
                "review_status": ReviewStatus.VERIFIED.value,
            },
        )
        takeoff = self._repository.create_takeoff(
            project_id=project_id,
            service_code=service_code,
            description=description,
            quantity=measurement.calculated_value,
            unit=unit,
            measurement_ids=[measurement.id],
            calculation_rule="SERVER_RECALCULATED_MANUAL_GEOMETRY",
            calculation_rule_version=self.algorithm_version,
            price_reference=None,
            quantity_source="VERIFIED_PROJECT_MEASUREMENT",
            review_status=ReviewStatus.VERIFIED.value,
            revision=1,
        )
        return measurement, evidence, takeoff

    def _create_ifc_measurement(
        self,
        *,
        project_id: str,
        document_version_id: str,
        element: BuildingElementORM,
        measurement_type: MeasurementType,
        value: float,
        unit: str,
        method: str,
    ) -> MeasurementORM:
        measurement, _ = self._repository.create_measurement_with_evidence(
            measurement_values={
                "project_id": project_id,
                "element_id": element.id,
                "measurement_type": measurement_type.value,
                "geometry_json": element.geometry_json,
                "raw_value": Decimal(str(value)),
                "unit": unit,
                "scale_calibration_id": None,
                "calculated_value": Decimal(str(value)),
                "source_document_id": document_version_id,
                "source_sheet_id": None,
                "source_view_region_id": None,
                "method": method,
                "algorithm_version": self._ifc.version,
                "confidence": element.confidence,
                "review_status": ReviewStatus.AUTO.value,
                "revision": 1,
            },
            evidence_values={
                "source_coordinates": {"ifc_global_id": element.source_external_id},
                "geometry_json": element.geometry_json,
                "page_number": None,
                "view_region_id": None,
                "scale_json": {"method": "IFC_NATIVE", "unit": unit},
                "thumbnail_reference": None,
                "algorithm": method,
                "model_version": self._ifc.version,
                "review_status": ReviewStatus.AUTO.value,
            },
        )
        return measurement

    def _record_ifc_quantity_conflicts(
        self,
        *,
        project_id: str,
        element: BuildingElementORM,
        stated: dict[str, float],
        geometry: dict[str, float],
    ) -> None:
        comparisons = (
            ("volume", ("NetVolume", "GrossVolume", "Volume"), "GeometryVolume"),
            (
                "area",
                ("NetSideArea", "GrossSideArea", "NetArea", "GrossArea", "Area"),
                "GeometrySurfaceArea",
            ),
        )
        for quantity_name, stated_keys, geometry_key in comparisons:
            stated_value = next((stated[key] for key in stated_keys if key in stated), None)
            calculated_value = geometry.get(geometry_key)
            if stated_value is None or calculated_value is None or stated_value == 0:
                continue
            difference = abs(stated_value - calculated_value) / abs(stated_value)
            if difference <= 0.05:
                continue
            self._session.add(
                QuantityConflictORM(
                    project_id=project_id,
                    element_id=element.id,
                    quantity_name=quantity_name,
                    stated_value=Decimal(str(round(stated_value, 6))),
                    calculated_value=Decimal(str(round(calculated_value, 6))),
                    difference_ratio=Decimal(str(round(difference, 6))),
                    status="OPEN",
                    detail_json={
                        "stated_source": "IFC_QUANTITY_SET",
                        "calculated_source": "IFC_GEOMETRY_CALCULATION",
                        "threshold": 0.05,
                    },
                )
            )

    def _require_project(self, project_id: str) -> None:
        if self._session.get(ProjectORM, project_id) is None:
            raise ProjectGraphNotFoundError(project_id)

    def _require_sheet(self, project_id: str, sheet_id: str) -> SheetORM:
        sheet = self._session.scalar(
            select(SheetORM)
            .join(DocumentVersionORM, DocumentVersionORM.id == SheetORM.document_version_id)
            .where(SheetORM.id == sheet_id, DocumentVersionORM.project_id == project_id)
        )
        if sheet is None:
            raise ProjectGraphNotFoundError(sheet_id)
        return sheet

    def _require_view_region(self, project_id: str, view_region_id: str) -> ViewRegionORM:
        region = self._session.get(ViewRegionORM, view_region_id)
        if region is None or not self._view_region_belongs_to_project(project_id, region.id):
            raise ProjectGraphNotFoundError(view_region_id)
        return region

    def _view_region_belongs_to_project(self, project_id: str, view_region_id: str) -> bool:
        return (
            self._session.scalar(
                select(ViewRegionORM.id)
                .join(SheetORM, SheetORM.id == ViewRegionORM.sheet_id)
                .join(DocumentVersionORM, DocumentVersionORM.id == SheetORM.document_version_id)
                .where(
                    ViewRegionORM.id == view_region_id, DocumentVersionORM.project_id == project_id
                )
            )
            is not None
        )
