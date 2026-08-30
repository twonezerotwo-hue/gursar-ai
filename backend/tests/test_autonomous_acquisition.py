from __future__ import annotations

import hashlib
import io
import zipfile
from pathlib import Path

import ifcopenshell
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import select

from app.modules.acquisition.application.service import (
    MAPPING_VERSION,
    AcquisitionRuleError,
    DatasetDiscoveryService,
    DatasetDownloadManager,
    DatasetReadinessEngine,
    DiscoveredSource,
    ExternalAnnotationImporter,
    ExternalProjectPackageBuilder,
    IfcProjectHarvester,
    LicenseGate,
    StaticSeedSourceProvider,
    SyntheticProjectFactory,
    load_acquisition_rules,
)
from app.modules.acquisition.infrastructure.models import (
    DiscoveredDatasetSourceORM,
    ExternalAnnotationLineageORM,
    ExternalClassMappingORM,
    ExternalProjectPackageORM,
    IfcHarvestSummaryORM,
    LicenseAssessmentORM,
)
from app.modules.documents.infrastructure.models import (
    DocumentVersionORM,
    ProjectDocumentORM,
)
from app.modules.vision.infrastructure.models import (
    TrainingImageDerivativeORM,
    VisionAnnotationORM,
)

ADMIN = {
    "X-Admin-Key": "test-admin-key",
    "X-Reviewer-Id": "engineer-1",
    "X-Reviewer-Role": "ENGINEER",
}


def _zip(files: dict[str, bytes]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return output.getvalue()


def _source(session, *, url: str = "https://github.com/test/open.zip"):
    discovered = DiscoveredSource(
        provider="TEST",
        source_url=url,
        repository_url="https://github.com/test/open",
        name="Open floor plans",
        publisher="University",
        formats=("PNG", "JSON"),
        annotation_available=True,
        ground_truth_available=True,
        license_name="CC-BY-4.0",
        license_url="https://creativecommons.org/licenses/by/4.0/",
        reputation_score=90,
    )
    decision = LicenseGate().assess(discovered)
    row = DiscoveredDatasetSourceORM(
        provider="TEST",
        source_url=url,
        repository_url=discovered.repository_url,
        name=discovered.name,
        description="test",
        publisher=discovered.publisher,
        detected_formats_json=["PNG", "JSON"],
        annotation_available=True,
        ground_truth_available=True,
        license_status=decision["rights_status"],
        quality_score=90,
        relevance_score=90,
        reputation_score=90,
        discovery_query="test",
        status="APPROVED",
    )
    session.add(row)
    session.flush()
    session.add(LicenseAssessmentORM(source_id=row.id, **decision))
    session.flush()
    return row


def _ifc() -> bytes:
    model = ifcopenshell.file(schema="IFC4")
    project = model.create_entity("IfcProject", GlobalId=ifcopenshell.guid.new(), Name="Test")
    building = model.create_entity("IfcBuilding", GlobalId=ifcopenshell.guid.new(), Name="Building")
    storey = model.create_entity(
        "IfcBuildingStorey", GlobalId=ifcopenshell.guid.new(), Name="Ground", Elevation=0.0
    )
    wall = model.create_entity("IfcWall", GlobalId=ifcopenshell.guid.new(), Name="W1")
    for parent, children in ((project, [building]), (building, [storey])):
        model.create_entity(
            "IfcRelAggregates",
            GlobalId=ifcopenshell.guid.new(),
            RelatingObject=parent,
            RelatedObjects=children,
        )
    model.create_entity(
        "IfcRelContainedInSpatialStructure",
        GlobalId=ifcopenshell.guid.new(),
        RelatedElements=[wall],
        RelatingStructure=storey,
    )
    return model.to_string().encode()


def test_license_gate_allows_proven_open_and_blocks_unknown_or_research() -> None:
    gate = LicenseGate()
    open_result = gate.assess(
        DiscoveredSource(
            provider="TEST",
            source_url="https://example.org/open",
            name="Open",
            license_name="CC-BY-4.0",
            license_url="https://creativecommons.org/licenses/by/4.0/",
        )
    )
    unknown = gate.assess(
        DiscoveredSource(provider="TEST", source_url="https://example.org/x", name="Unknown")
    )
    research = gate.assess(
        DiscoveredSource(
            provider="TEST",
            source_url="https://example.org/research",
            name="Research",
            license_name="CC-BY-NC-4.0",
        )
    )
    assert open_result["training_use_allowed"] is True
    assert unknown["rights_status"] == "LICENSE_UNKNOWN" and unknown["manual_review_required"]
    assert research["rights_status"] == "RESEARCH_ONLY"
    assert research["training_use_allowed"] is False


def test_discovery_is_idempotent_and_unknown_is_not_approved(app: FastAPI) -> None:
    seeds = [
        {
            "name": "Known",
            "source_url": "https://example.org/known.zip",
            "license_name": "MIT",
            "license_url": "https://example.org/license",
        },
        {"name": "Unknown", "source_url": "https://example.org/unknown.zip"},
    ]
    with app.state.session_factory.begin() as session:
        service = DatasetDiscoveryService(session, [StaticSeedSourceProvider(seeds)])
        first = service.run_discovery(queries=["floor plan"], max_sources=10)
        second = service.run_discovery(queries=["floor plan"], max_sources=10)
        rows = list(session.scalars(select(DiscoveredDatasetSourceORM)))
        assert first["created"] == 2 and second["reused"] == 2 and len(rows) == 2
        unknown = next(row for row in rows if row.name == "Unknown")
        assert unknown.status == "REVIEWING" and unknown.license_status == "LICENSE_UNKNOWN"


def test_safe_download_checksum_duplicate_and_traversal_rejection(app: FastAPI) -> None:
    payload = _zip({"project/plan.png": b"png", "project/annotations.json": b"{}"})
    with app.state.session_factory.begin() as session:
        source = _source(session)
        manager = DatasetDownloadManager(
            session,
            app.state.document_storage,
            max_single_file_bytes=1024 * 1024,
            max_archive_files=20,
            max_total_bytes=2 * 1024 * 1024,
            allowed_domains={"github.com"},
        )
        first, result = manager.download(source.id, fetcher=lambda _url: [payload])
        duplicate, reused = manager.download(source.id, fetcher=lambda _url: [payload])
        assert first is not None and len(first.archive_hash) == 64 and result["files"] == 2
        assert duplicate is not None and reused["error"] == "DUPLICATE_DATASET"

        unsafe_source = _source(session, url="https://github.com/test/unsafe.zip")
        unsafe = _zip({"../escape.json": b"{}"})
        with pytest.raises(AcquisitionRuleError, match="ARCHIVE_UNSAFE"):
            manager.download(unsafe_source.id, fetcher=lambda _url: [unsafe])


def test_package_builder_and_dry_run(client: TestClient, app: FastAPI) -> None:
    with app.state.session_factory.begin() as session:
        source = _source(session)
        manager = DatasetDownloadManager(
            session,
            app.state.document_storage,
            max_single_file_bytes=1024 * 1024,
            max_archive_files=20,
            max_total_bytes=2 * 1024 * 1024,
            allowed_domains={"github.com"},
        )
        manifest, _ = manager.download(
            source.id,
            fetcher=lambda _url: [_zip({"P01/plan.png": b"x", "P01/a.json": b"{}"})],
        )
        assert manifest is not None
        dry = client.post(
            f"/api/v1/training/sources/{source.id}/ingest",
            headers=ADMIN,
            json={"manifest_id": manifest.id, "dry_run": True},
        )
        assert dry.status_code == 200 and dry.json()["would_create_projects"] == 1


def test_external_coco_import_preserves_lineage_and_rejects_invalid(app: FastAPI) -> None:
    with app.state.session_factory.begin() as session:
        SyntheticProjectFactory(session, app.state.document_storage).generate(
            count=1, seed=81, building_type="residential", floors_min=1, floors_max=1
        )
        synthetic_annotation = session.scalar(select(VisionAnnotationORM))
        assert synthetic_annotation is not None
        image = session.get(TrainingImageDerivativeORM, synthetic_annotation.image_derivative_id)
        assert image is not None
        source = _source(session)
        package = ExternalProjectPackageORM(
            source_id=source.id,
            external_project_id="EXT-1",
            project_id=synthetic_annotation.project_id,
            files_json=[],
            annotation_files_json=[],
            project_quality_score=90,
            training_eligibility="ELIGIBLE",
            rights_status="OPEN_LICENSED",
            ingestion_status="INGESTED",
        )
        session.add(package)
        mapping = ExternalClassMappingORM(
            source_id=source.id,
            external_class="LivingRoom",
            gursar_class="ROOM",
            metadata_json={"room_type": "LIVING_ROOM"},
            mapping_confidence=1,
            mapping_method="MANUAL",
            verified=True,
            mapping_version=MAPPING_VERSION,
        )
        session.add(mapping)
        session.flush()
        result = ExternalAnnotationImporter(session).import_coco(
            source_id=source.id,
            package_id=package.id,
            document_version_id=synthetic_annotation.document_version_id,
            sheet_id=synthetic_annotation.sheet_id,
            image_derivative_id=image.id,
            payload={
                "categories": [{"id": 1, "name": "LivingRoom"}],
                "annotations": [{"id": 99, "category_id": 1, "bbox": [10, 10, 100, 100]}],
            },
        )
        lineage = session.scalar(select(ExternalAnnotationLineageORM))
        assert result["imported"] == 1 and lineage is not None
        assert lineage.external_class == "LivingRoom" and lineage.quality_tier == "SILVER"
        with pytest.raises(AcquisitionRuleError, match="LOW_QUALITY_DATASET"):
            ExternalAnnotationImporter(session).import_coco(
                source_id=source.id,
                package_id=package.id,
                document_version_id=synthetic_annotation.document_version_id,
                sheet_id=synthetic_annotation.sheet_id,
                image_derivative_id=image.id,
                payload={"categories": [], "annotations": [{"category_id": 5}]},
            )


def test_external_annotation_format_detection_and_parsing() -> None:
    svg = (
        b'<svg width="100" height="100"><rect data-class="Wall" '
        b'x="10" y="20" width="30" height="40" /></svg>'
    )
    assert ExternalAnnotationImporter.detect_format("plan.svg", svg) == "SVG_SEMANTIC"
    parsed_svg = ExternalAnnotationImporter.parse_svg(svg)
    assert parsed_svg[0]["external_class"] == "Wall"
    parsed_json = ExternalAnnotationImporter.parse_json_geometry(
        {"annotations": [{"class_name": "Door", "bbox_normalized": [0.1, 0.2, 0.2, 0.4]}]}
    )
    assert parsed_json[0]["geometry_type"] == "BBOX"


def test_synthetic_generation_is_reproducible_and_geometry_valid(app: FastAPI) -> None:
    with app.state.session_factory.begin() as session:
        factory = SyntheticProjectFactory(session, app.state.document_storage)
        first = factory.generate(
            count=2, seed=42, building_type="residential", floors_min=1, floors_max=4
        )
        second = factory.generate(
            count=2, seed=42, building_type="residential", floors_min=1, floors_max=4
        )
        assert first["generated"] == 2 and first["annotations"] > 0
        assert second["generated"] == 0 and second["reused"] == 2


def test_synthetic_job_is_background_and_readiness_reports_ratio(client: TestClient) -> None:
    queued = client.post(
        "/api/v1/training/synthetic/projects/generate",
        headers=ADMIN,
        json={
            "count": 1,
            "building_type": "residential",
            "floors": {"min": 1, "max": 2},
            "seed": 9,
        },
    )
    assert queued.status_code == 202 and queued.json()["status"] == "QUEUED"
    completed = client.post(
        f"/api/v1/training/acquisition/jobs/{queued.json()['job_id']}/run", headers=ADMIN
    )
    assert completed.status_code == 200 and completed.json()["status"] == "COMPLETED"
    readiness = client.get("/api/v1/training/vision/readiness").json()
    assert readiness["status"] == "PARTIALLY_READY"
    assert readiness["classes"]["WALL"]["synthetic_ratio"] == 1


def test_discovery_job_dry_run_and_api_status(client: TestClient) -> None:
    queued = client.post(
        "/api/v1/training/acquisition/discover",
        headers=ADMIN,
        json={"dry_run": True, "use_remote_providers": False},
    )
    assert queued.status_code == 202
    run = client.post(
        f"/api/v1/training/acquisition/jobs/{queued.json()['job_id']}/run", headers=ADMIN
    )
    assert run.status_code == 200 and run.json()["result"]["would_discover"] == 3
    status_response = client.get("/api/v1/training/acquisition/status")
    assert status_response.status_code == 200


def test_ifc_harvester_uses_project_graph_and_prevents_duplicate(
    app: FastAPI, tmp_path: Path
) -> None:
    ifc_path = tmp_path / "sample.ifc"
    ifc_bytes = _ifc()
    ifc_path.write_bytes(ifc_bytes)
    with app.state.session_factory.begin() as session:
        source = _source(session)
        SyntheticProjectFactory(session, app.state.document_storage).generate(
            count=1, seed=333, building_type="residential", floors_min=1, floors_max=1
        )
        synthetic = session.get(
            VisionAnnotationORM,
            session.scalar(select(VisionAnnotationORM.id)),
        )
        assert synthetic is not None
        package = ExternalProjectPackageORM(
            source_id=source.id,
            external_project_id="IFC-1",
            project_id=synthetic.project_id,
            files_json=[],
            annotation_files_json=[],
            project_quality_score=80,
            training_eligibility="ELIGIBLE",
            rights_status="OPEN_LICENSED",
            ingestion_status="INGESTED",
        )
        session.add(package)
        document = ProjectDocumentORM(project_id=synthetic.project_id, original_name="sample.ifc")
        session.add(document)
        session.flush()
        version = DocumentVersionORM(
            id="ifc-version-1",
            document_id=document.id,
            project_id=synthetic.project_id,
            version=1,
            sha256=hashlib.sha256(ifc_bytes).hexdigest(),
            size_bytes=len(ifc_bytes),
            media_type="application/x-step",
            storage_key="test/sample.ifc",
            page_count=0,
            status="READY",
        )
        session.add(version)
        session.flush()
        harvester = IfcProjectHarvester(session)
        first, reused_first = harvester.harvest(
            source_id=source.id,
            package_id=package.id,
            document_version_id=version.id,
            path=ifc_path,
        )
        second, reused_second = harvester.harvest(
            source_id=source.id,
            package_id=package.id,
            document_version_id=version.id,
            path=ifc_path,
        )
        assert not reused_first and reused_second and first.id == second.id
        assert first.summary_json["storeys"] == 1
        assert session.scalar(select(IfcHarvestSummaryORM)) is not None


def test_readiness_synthetic_ratio_guard(app: FastAPI) -> None:
    with app.state.session_factory.begin() as session:
        SyntheticProjectFactory(session, app.state.document_storage).generate(
            count=1, seed=456, building_type="residential", floors_min=1, floors_max=1
        )
        rules = dict(load_acquisition_rules()["readiness"])
        rules.update(
            minimum_projects=1,
            minimum_annotations_per_class=1,
            minimum_quality_score=1,
            minimum_gold_silver_ratio=0,
            maximum_synthetic_ratio=0.4,
        )
        result = DatasetReadinessEngine(session, rules).evaluate()
        assert result["classes"]["WALL"]["status"] == "PARTIALLY_READY"


def test_source_ranking_and_family_are_deterministic(app: FastAPI) -> None:
    seed = {
        "name": "Open annotated architectural walls doors windows",
        "source_url": "https://github.com/example/floor-data/archive/main.zip",
        "repository_url": "https://github.com/example/floor-data",
        "formats": ["PNG", "JSON"],
        "annotation_available": True,
        "ground_truth_available": True,
        "license_name": "MIT",
        "license_url": "https://github.com/example/floor-data/blob/main/LICENSE",
        "reputation_score": 90,
    }
    with app.state.session_factory.begin() as session:
        DatasetDiscoveryService(session, [StaticSeedSourceProvider([seed])]).run_discovery(
            queries=["floor plan"], max_sources=1
        )
        row = session.scalar(select(DiscoveredDatasetSourceORM))
        assert row is not None
        assert row.acquisition_priority_score > 50
        assert row.source_family_id and len(row.source_family_id) == 24
        assert row.score_breakdown_json["license_score"] == 25


def test_perdaw_adapter_imports_external_ground_truth(app: FastAPI) -> None:
    image = Image.new("RGB", (224, 224), "white")
    content = io.BytesIO()
    image.save(content, "JPEG")
    archive = _zip(
        {
            "initial/door_0_plan/door_0_plan_224x224.jpg": content.getvalue(),
            "initial/window_0_plan/window_0_plan_224x224.jpg": content.getvalue() + b"x",
        }
    )
    with app.state.session_factory.begin() as session:
        source = _source(session, url="https://github.com/test/perdaw.zip")
        source.name = "PERDAW test"
        manager = DatasetDownloadManager(
            session,
            app.state.document_storage,
            max_single_file_bytes=1024 * 1024,
            max_archive_files=20,
            max_total_bytes=2 * 1024 * 1024,
            allowed_domains={"github.com"},
        )
        manifest, _ = manager.download(source.id, fetcher=lambda _url: [archive])
        assert manifest is not None
        ExternalProjectPackageBuilder(session).build(manifest.id)
        package = session.scalar(
            select(ExternalProjectPackageORM).where(
                ExternalProjectPackageORM.source_id == source.id
            )
        )
        assert package is not None
        result = ExternalAnnotationImporter(session).import_perdaw_archive(
            source_id=source.id,
            package_id=package.id,
            storage=app.state.document_storage,
        )
        assert result["imported"] >= 1
        assert session.scalar(select(ExternalAnnotationLineageORM)) is not None


def test_full_bootstrap_is_queued_as_background_job(client: TestClient) -> None:
    response = client.post(
        "/api/v1/training/acquisition/bootstrap",
        headers=ADMIN,
        json={"mode": "FULL_BOOTSTRAP", "use_remote_providers": False},
    )
    assert response.status_code == 202
    assert response.json()["status"] == "QUEUED"
    assert response.json()["mode"] == "FULL_BOOTSTRAP"


def test_readiness_v2_reports_targets_and_domain_gap(app: FastAPI) -> None:
    with app.state.session_factory.begin() as session:
        SyntheticProjectFactory(session, app.state.document_storage).generate(
            count=1, seed=987, building_type="residential", floors_min=1, floors_max=1
        )
        result = DatasetReadinessEngine(session, load_acquisition_rules()["readiness"]).evaluate()
        assert result["classes"]["WALL"]["target"] == 10000
        assert "SYNTHETIC_DOMINANCE" in result["warnings"]
        assert result["next_recommendation"] == "COLLECT_MORE_REAL_DATA"
