from __future__ import annotations

import hashlib
import io
from pathlib import Path

import ifcopenshell
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.persistence import new_id
from app.modules.acquisition.application.real_data import (
    Ifc2DGroundTruthRenderer,
    RealDataRuleError,
    ResPlanAnnotationImporter,
    UpstreamLicenseLineageService,
    canonical_source_tier,
)
from app.modules.acquisition.application.service import (
    DatasetCandidateBuilder,
    DatasetReadinessEngine,
    DiscoveredSource,
    LicenseGate,
    SyntheticProjectFactory,
    load_acquisition_rules,
)
from app.modules.acquisition.infrastructure.models import (
    DiscoveredDatasetSourceORM,
    ExternalProjectPackageORM,
    Ifc2DAnnotationLineageORM,
    Ifc2DRenderORM,
    LicenseAssessmentORM,
)
from app.modules.documents.infrastructure.models import DocumentVersionORM, ProjectDocumentORM
from app.modules.projects.infrastructure.models import ProjectORM
from app.modules.training.infrastructure.models import DatasetVersionORM
from app.modules.vision.infrastructure.models import VisionAnnotationORM, VisionDatasetItemORM

ADMIN = {
    "X-Admin-Key": "test-admin-key",
    "X-Reviewer-Id": "engineer-1",
    "X-Reviewer-Role": "ENGINEER",
}


def _source(session, *, name: str, license_name: str = "CC-BY-4.0"):
    discovered = DiscoveredSource(
        provider="TEST",
        source_url=f"https://github.com/test/{name}.zip",
        repository_url=f"https://github.com/test/{name}",
        name=name,
        publisher="Test publisher",
        formats=("JSON",),
        annotation_available=True,
        ground_truth_available=True,
        license_name=license_name,
        license_url="https://example.test/license",
        reputation_score=90,
    )
    decision = LicenseGate().assess(discovered)
    row = DiscoveredDatasetSourceORM(
        provider="TEST",
        source_url=discovered.source_url,
        repository_url=discovered.repository_url,
        name=name,
        description="fixture",
        publisher=discovered.publisher,
        detected_formats_json=list(discovered.formats),
        annotation_available=True,
        ground_truth_available=True,
        license_status=decision["rights_status"],
        quality_score=95,
        relevance_score=95,
        reputation_score=95,
        discovery_query="test",
        status="APPROVED",
        source_family_id=f"family-{name}",
    )
    session.add(row)
    session.flush()
    session.add(LicenseAssessmentORM(source_id=row.id, **decision))
    session.flush()
    return row


def _allow_lineage(session, source: DiscoveredDatasetSourceORM, license_name: str = "CC-BY-4.0"):
    return UpstreamLicenseLineageService(session).record(
        source_id=source.id,
        declared_license=license_name,
        lineage_verified=True,
    )


@pytest.mark.parametrize("license_name", ["MIT", "CC-BY-4.0", "APACHE-2.0"])
def test_verified_permissive_root_lineage_is_training_eligible(
    app: FastAPI, license_name: str
) -> None:
    with app.state.session_factory.begin() as session:
        source = _source(session, name=f"root-{license_name}", license_name=license_name)
        row = _allow_lineage(session, source, license_name)
        assert row.training_allowed
        assert row.effective_license == license_name
        assert row.conflict_code is None


@pytest.mark.parametrize("license_name", ["CC-BY-NC-4.0", "RESEARCH-ONLY"])
def test_noncommercial_or_research_lineage_is_blocked(
    app: FastAPI, license_name: str
) -> None:
    with app.state.session_factory.begin() as session:
        source = _source(session, name=f"blocked-{license_name}", license_name=license_name)
        row = _allow_lineage(session, source, license_name)
        assert not row.training_allowed
        assert row.manual_review_required


def test_child_mit_over_noncommercial_parent_is_conflict(app: FastAPI) -> None:
    with app.state.session_factory.begin() as session:
        parent = _source(session, name="parent-nc", license_name="CC-BY-NC-4.0")
        _allow_lineage(session, parent, "CC-BY-NC-4.0")
        child = _source(session, name="child-mit", license_name="MIT")
        row = UpstreamLicenseLineageService(session).record(
            source_id=child.id,
            parent_source_id=parent.id,
            declared_license="MIT",
            lineage_verified=True,
        )
        assert row.conflict_code == "DERIVED_DATASET_LICENSE_CONFLICT"
        assert not row.training_allowed
        assert child.license_status == "DERIVED_DATASET_LICENSE_CONFLICT"


def test_unknown_upstream_requires_manual_review(app: FastAPI) -> None:
    with app.state.session_factory.begin() as session:
        source = _source(session, name="unknown-upstream")
        row = UpstreamLicenseLineageService(session).record(
            source_id=source.id,
            declared_license="CC-BY-4.0",
            upstream_license="UNKNOWN",
            derived_from="withheld listing sources",
            lineage_verified=False,
        )
        assert row.effective_license == "UNKNOWN"
        assert row.manual_review_required and not row.training_allowed


def test_unverified_attribution_lineage_is_a_derived_conflict(app: FastAPI) -> None:
    with app.state.session_factory.begin() as session:
        source = _source(session, name="unverified-attribution")
        row = UpstreamLicenseLineageService(session).record(
            source_id=source.id,
            declared_license="CC-BY-4.0",
            upstream_license="CC-BY-4.0",
            derived_from="upstream floor plans",
            lineage_verified=False,
        )
        assert row.conflict_code == "DERIVED_DATASET_LICENSE_CONFLICT"
        assert row.manual_review_required and not row.training_allowed


def _resplan_records(count: int = 1) -> list[dict]:
    records = []
    for index in range(count):
        shift = index * 20.0
        records.append(
            {
                "id": f"rp-{index}",
                "wall": {
                    "type": "Polygon",
                    "coordinates": [
                        [[shift, 0], [shift + 10, 0], [shift + 10, 1], [shift, 1]]
                    ],
                },
                "living": {
                    "type": "Polygon",
                    "coordinates": [
                        [[shift + 1, 1], [shift + 9, 1], [shift + 9, 8], [shift + 1, 8]]
                    ],
                },
                "door": {
                    "type": "Polygon",
                    "coordinates": [
                        [
                            [shift + 4, 0.2],
                            [shift + 5, 0.2],
                            [shift + 5, 0.8],
                            [shift + 4, 0.8],
                        ]
                    ],
                },
                "window": {
                    "type": "Polygon",
                    "coordinates": [
                        [
                            [shift + 7, 0.2],
                            [shift + 8, 0.2],
                            [shift + 8, 0.8],
                            [shift + 7, 0.8],
                        ]
                    ],
                },
            }
        )
    return records


def test_resplan_adapter_maps_geometry_room_type_and_lineage(app: FastAPI) -> None:
    with app.state.session_factory.begin() as session:
        source = _source(session, name="resplan-fixture")
        _allow_lineage(session, source)
        result = ResPlanAnnotationImporter(session, app.state.document_storage).import_records(
            source_id=source.id, records=_resplan_records(2), limit=2
        )
        assert result["projects_valid"] == 2
        assert result["class_distribution"] == {"WALL": 2, "DOOR": 2, "WINDOW": 2, "ROOM": 2}
        assert result["pilot_accepted"]
        room = session.scalar(
            select(VisionAnnotationORM).where(VisionAnnotationORM.class_name == "ROOM")
        )
        assert room is not None
        assert room.source_type == "REAL_DRAWING_SILVER"
        assert room.geometry_normalized["room_type"] == "living"
        coordinates = [
            value for point in room.geometry_normalized["polygon"] for value in point
        ]
        assert all(0 <= value <= 1 for value in coordinates)


def test_resplan_import_is_idempotent_and_license_guarded(app: FastAPI) -> None:
    with app.state.session_factory.begin() as session:
        blocked = _source(session, name="resplan-blocked")
        UpstreamLicenseLineageService(session).record(
            source_id=blocked.id,
            declared_license="CC-BY-4.0",
            upstream_license="UNKNOWN",
            derived_from="unknown",
            lineage_verified=False,
        )
        with pytest.raises(RealDataRuleError, match="UPSTREAM_LICENSE_LINEAGE_BLOCKED"):
            ResPlanAnnotationImporter(session, app.state.document_storage).import_records(
                source_id=blocked.id, records=_resplan_records(), limit=1
            )
        allowed = _source(session, name="resplan-idempotent")
        _allow_lineage(session, allowed)
        importer = ResPlanAnnotationImporter(session, app.state.document_storage)
        first = importer.import_records(source_id=allowed.id, records=_resplan_records(), limit=1)
        second = importer.import_records(source_id=allowed.id, records=_resplan_records(), limit=1)
        assert first["projects_imported"] == 1
        assert second["projects_reused"] == 1


def _axis(model, x: float, y: float, z: float):
    point = model.create_entity("IfcCartesianPoint", Coordinates=(x, y, z))
    return model.create_entity("IfcAxis2Placement3D", Location=point)


def _ifc_with_two_storeys(path: Path) -> dict[str, str]:
    model = ifcopenshell.file(schema="IFC4")
    metre = model.create_entity("IfcSIUnit", UnitType="LENGTHUNIT", Name="METRE")
    units = model.create_entity("IfcUnitAssignment", Units=[metre])
    context = model.create_entity(
        "IfcGeometricRepresentationContext",
        ContextIdentifier="Model",
        ContextType="Model",
        CoordinateSpaceDimension=3,
        Precision=1e-5,
        WorldCoordinateSystem=_axis(model, 0.0, 0.0, 0.0),
    )
    project = model.create_entity(
        "IfcProject", GlobalId=ifcopenshell.guid.new(), Name="Fixture", UnitsInContext=units,
        RepresentationContexts=[context]
    )
    building = model.create_entity("IfcBuilding", GlobalId=ifcopenshell.guid.new(), Name="B")
    storeys = [
        model.create_entity(
            "IfcBuildingStorey", GlobalId=ifcopenshell.guid.new(), Name=name, Elevation=elevation
        )
        for name, elevation in (("Ground", 0.0), ("First", 3.0))
    ]
    model.create_entity(
        "IfcRelAggregates",
        GlobalId=ifcopenshell.guid.new(),
        RelatingObject=project,
        RelatedObjects=[building],
    )
    model.create_entity(
        "IfcRelAggregates",
        GlobalId=ifcopenshell.guid.new(),
        RelatingObject=building,
        RelatedObjects=storeys,
    )
    ids: dict[str, str] = {}
    for index, storey in enumerate(storeys):
        profile = model.create_entity(
            "IfcRectangleProfileDef", ProfileType="AREA", XDim=6.0, YDim=0.3
        )
        solid = model.create_entity(
            "IfcExtrudedAreaSolid",
            SweptArea=profile,
            Position=_axis(model, 0.0, 0.0, 0.0),
            ExtrudedDirection=model.create_entity("IfcDirection", DirectionRatios=(0.0, 0.0, 1.0)),
            Depth=3.0,
        )
        representation = model.create_entity(
            "IfcShapeRepresentation",
            ContextOfItems=context,
            RepresentationIdentifier="Body",
            RepresentationType="SweptSolid",
            Items=[solid],
        )
        shape = model.create_entity("IfcProductDefinitionShape", Representations=[representation])
        placement = model.create_entity(
            "IfcLocalPlacement", RelativePlacement=_axis(model, float(index * 8), 0.0, index * 3.0)
        )
        wall = model.create_entity(
            "IfcWall",
            GlobalId=ifcopenshell.guid.new(),
            Name=f"Wall-{index}",
            ObjectPlacement=placement,
            Representation=shape,
        )
        ids[f"wall_{index}"] = wall.GlobalId
        ids[f"storey_{index}"] = storey.GlobalId
        model.create_entity(
            "IfcRelContainedInSpatialStructure",
            GlobalId=ifcopenshell.guid.new(),
            RelatedElements=[wall],
            RelatingStructure=storey,
        )
    path.write_text(model.to_string(), encoding="utf-8")
    return ids


def _ifc_render_context(app: FastAPI, session, tmp_path: Path):
    source = _source(session, name="gni-fixture")
    _allow_lineage(session, source)
    project = ProjectORM(name="GNI fixture", locale="tr", status="ACTIVE")
    session.add(project)
    session.flush()
    package = ExternalProjectPackageORM(
        source_id=source.id,
        external_project_id="gni-model-1",
        project_id=project.id,
        building_type="residential",
        floors=2,
        discipline_availability_json=["ARCHITECTURAL"],
        files_json=[],
        annotation_files_json=[],
        project_quality_score=95,
        training_eligibility="ELIGIBLE",
        rights_status="OPEN_LICENSED",
        ingestion_status="INGESTED",
        completeness_json={"source_family_id": source.source_family_id},
    )
    session.add(package)
    session.flush()
    document = ProjectDocumentORM(project_id=project.id, original_name="fixture.ifc")
    session.add(document)
    session.flush()
    ifc_path = tmp_path / "fixture.ifc"
    ids = _ifc_with_two_storeys(ifc_path)
    content = ifc_path.read_bytes()
    storage_key = "fixtures/real-data/fixture.ifc"
    app.state.document_storage.save(storage_key=storage_key, source=io.BytesIO(content))
    version = DocumentVersionORM(
        id=new_id(),
        document_id=document.id,
        project_id=project.id,
        version=1,
        sha256=hashlib.sha256(content).hexdigest(),
        size_bytes=len(content),
        media_type="application/x-step",
        storage_key=storage_key,
        page_count=0,
        status="READY",
    )
    session.add(version)
    session.flush()
    return source, package, version, ifc_path, ids


def test_ifc_2d_renderer_is_reproducible_and_preserves_globalid_storey(
    app: FastAPI, tmp_path: Path
) -> None:
    with app.state.session_factory.begin() as session:
        source, package, version, path, ids = _ifc_render_context(app, session, tmp_path)
        renderer = Ifc2DGroundTruthRenderer(session, app.state.document_storage, render_size=512)
        first = renderer.render(
            source_id=source.id,
            package_id=package.id,
            document_version_id=version.id,
            path=path,
        )
        hashes = [row.image_checksum for row in session.scalars(select(Ifc2DRenderORM))]
        second = renderer.render(
            source_id=source.id,
            package_id=package.id,
            document_version_id=version.id,
            path=path,
        )
        assert first["levels_rendered"] == 2 and first["annotations"] == 2
        assert second["levels_reused"] == 2
        assert hashes == [row.image_checksum for row in session.scalars(select(Ifc2DRenderORM))]
        lineages = list(session.scalars(select(Ifc2DAnnotationLineageORM)))
        assert {row.ifc_global_id for row in lineages} == {ids["wall_0"], ids["wall_1"]}
        assert {row.storey_external_id for row in lineages} == {ids["storey_0"], ids["storey_1"]}
        assert all(row.quality_tier == "BIM_DERIVED_SILVER" for row in lineages)


def test_balanced_candidate_downsamples_synthetic_to_at_most_40_percent(app: FastAPI) -> None:
    with app.state.session_factory.begin() as session:
        source = _source(session, name="resplan-candidate")
        _allow_lineage(session, source)
        ResPlanAnnotationImporter(session, app.state.document_storage).import_records(
            source_id=source.id, records=_resplan_records(3), limit=3
        )
        SyntheticProjectFactory(session, app.state.document_storage).generate(
            count=12, seed=8000, building_type="residential", floors_min=1, floors_max=1
        )
        result = DatasetCandidateBuilder(session).build_ready_only({"ready_classes": ["WALL"]})
        assert result is not None
        manifest = result["manifest"]
        assert manifest["synthetic_ratio"] <= 0.40
        assert manifest["synthetic_count"] <= 2
        assert manifest["synthetic_stored"] >= manifest["synthetic_count"]
        dataset = session.get(DatasetVersionORM, result["id"])
        items = list(
            session.scalars(
                select(VisionDatasetItemORM).where(
                    VisionDatasetItemORM.dataset_version_id == dataset.id
                )
            )
        )
        assert len(items) == manifest["sample_count"]


def test_readiness_v2_does_not_bypass_source_diversity(app: FastAPI) -> None:
    with app.state.session_factory.begin() as session:
        source = _source(session, name="single-real-family")
        _allow_lineage(session, source)
        ResPlanAnnotationImporter(session, app.state.document_storage).import_records(
            source_id=source.id, records=_resplan_records(3), limit=3
        )
        rules = dict(load_acquisition_rules()["readiness"])
        rules.update(
            minimum_projects=1,
            minimum_source_families=2,
            minimum_quality_score=1,
            class_targets={"WALL": 1, "ROOM": 1, "DOOR": 1, "WINDOW": 1},
            real_data_targets={
                name: {"real_drawing": 1} for name in ("WALL", "ROOM", "DOOR", "WINDOW")
            },
        )
        result = DatasetReadinessEngine(session, rules).evaluate()
        assert result["classes"]["WALL"]["status"] == "PARTIALLY_READY"
        assert "SOURCE_DIVERSITY_LOW" in result["warnings"]
        assert result["source_distribution"]["REAL_DRAWING_SILVER"] == 12


def test_real_data_background_job_is_idempotent(client: TestClient) -> None:
    payload = {"job_type": "REAL_DATA_READINESS", "limit": 1}
    first = client.post(
        "/api/v1/training/acquisition/real-data/jobs", headers=ADMIN, json=payload
    )
    second = client.post(
        "/api/v1/training/acquisition/real-data/jobs", headers=ADMIN, json=payload
    )
    assert first.status_code == 202 and second.status_code == 202
    assert first.json()["job_id"] == second.json()["job_id"]
    assert second.json()["reused"]


@pytest.mark.parametrize(
    ("source_type", "expected"),
    [
        ("GURSAR_EXPERT_VERIFIED", "GURSAR_GOLD"),
        ("EXTERNAL_GROUND_TRUTH", "EXTERNAL_OBJECT_SILVER"),
        ("SYNTHETIC_GROUND_TRUTH", "SYNTHETIC"),
        ("MODEL_PREDICTION", "WEAK"),
    ],
)
def test_source_tier_normalization(source_type: str, expected: str) -> None:
    assert canonical_source_tier(source_type) == expected
