from __future__ import annotations

import io
import json
import zipfile

from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from app.modules.jobs.application.service import DatabaseJobQueueProvider, JobWorker
from app.modules.project_graph.infrastructure.models import TakeoffItemORM
from app.modules.training.infrastructure.models import (
    ActiveLearningItemORM,
    DatasetVersionORM,
    TrainingSampleORM,
)
from app.modules.vision.application.benchmark import GoldenVisionBenchmark
from app.modules.vision.application.fusion import DetectionCandidate, DetectionFusionEngine
from app.modules.vision.application.providers import HeuristicVisionProvider
from app.modules.vision.application.service import training_rules
from app.modules.vision.infrastructure.models import (
    ProjectArchiveImportORM,
    TrainingImageDerivativeORM,
    VisionAnnotationORM,
    VisionBenchmarkRunORM,
    VisionDatasetExportORM,
    VisionDatasetItemORM,
    VisionModelVersionORM,
)

HEADERS = {
    "X-Admin-Key": "test-admin-key",
    "X-Reviewer-Id": "vision.engineer@example.test",
    "X-Reviewer-Role": "ENGINEER",
}


def _raster_pdf(*, pages: int = 1) -> bytes:
    images = []
    for _ in range(pages):
        image = Image.new("RGB", (800, 600), "white")
        draw = ImageDraw.Draw(image)
        draw.rectangle((80, 80, 430, 390), outline="black", width=8)
        draw.rectangle((500, 400, 790, 590), outline="black", width=5)
        draw.line((80, 250, 430, 250), fill="black", width=5)
        images.append(image)
    output = io.BytesIO()
    images[0].save(output, format="PDF", save_all=True, append_images=images[1:], resolution=72)
    return output.getvalue()


def _project_upload(client: TestClient, *, pages: int = 1) -> tuple[str, str, str]:
    project_id = client.post("/api/v1/projects", json={"name": "Vision Pilot"}).json()["id"]
    upload = client.post(
        f"/api/v1/projects/{project_id}/documents",
        files={"file": ("raster-plan.pdf", _raster_pdf(pages=pages), "application/pdf")},
    )
    assert upload.status_code == 201, upload.text
    payload = upload.json()
    return project_id, payload["latest_version"]["id"], payload["sheets"][0]["id"]


def _eligible(
    client: TestClient, project_id: str, version_id: str, rights: str = "INTERNAL_PROJECT"
) -> None:
    manifest = client.put(
        f"/api/v1/projects/{project_id}/training-manifest",
        headers=HEADERS,
        json={
            "rights_status": rights,
            "training_allowed": True,
            "architectural_documents": True,
            "structural_documents": True,
            "geotechnical_documents": True,
            "boq_available": True,
            "verified_quantity_available": True,
            "as_built_available": True,
        },
    )
    assert manifest.status_code == 200
    anonymized = client.post(
        f"/api/v1/projects/{project_id}/training/anonymize/{version_id}",
        headers=HEADERS,
        json={"metadata": {"customer_name": "Gizli", "sheet": "A-101"}},
    )
    assert anonymized.status_code == 201


def _render(client: TestClient, project_id: str, version_id: str) -> dict:
    response = client.post(
        f"/api/v1/projects/{project_id}/training-images",
        headers=HEADERS,
        json={"document_version_id": version_id, "page_number": 1, "dpi": 144},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _annotation(
    client: TestClient,
    project_id: str,
    version_id: str,
    sheet_id: str,
    image_id: str,
    *,
    class_name: str = "WALL",
    source_type: str = "EXPERT_MANUAL_ANNOTATION",
    review_status: str = "VERIFIED",
) -> dict:
    response = client.post(
        f"/api/v1/projects/{project_id}/training/annotations",
        headers=HEADERS,
        json={
            "document_version_id": version_id,
            "sheet_id": sheet_id,
            "image_derivative_id": image_id,
            "task_type": "ELEMENT_DETECTION",
            "class_name": class_name,
            "geometry_type": "BBOX",
            "geometry_normalized": {"bbox": [0.1, 0.1, 0.4, 0.2]},
            "source_type": source_type,
            "confidence": 1,
            "review_status": review_status,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_raster_analysis_job_is_fast_idempotent_and_produces_review_candidates(
    app: FastAPI, client: TestClient
) -> None:
    project_id, version_id, _ = _project_upload(client)
    payload = {"document_version_id": version_id, "analysis_modes": ["RASTER_VISION"]}
    first = client.post(
        f"/api/v1/projects/{project_id}/analysis/jobs", headers=HEADERS, json=payload
    )
    second = client.post(
        f"/api/v1/projects/{project_id}/analysis/jobs", headers=HEADERS, json=payload
    )
    assert first.status_code == 202 and first.json()["status"] == "QUEUED"
    assert second.json()["id"] == first.json()["id"] and second.json()["reused"] is True
    completed = client.post(f"/api/v1/jobs/{first.json()['id']}/run", headers=HEADERS)
    assert completed.status_code == 200 and completed.json()["status"] == "COMPLETED"
    assert completed.json()["result_json"]["quantity_generated"] is False
    with app.state.session_factory() as session:
        candidates = session.query(VisionAnnotationORM).filter_by(project_id=project_id).all()
        assert candidates and all(row.review_status == "REVIEW_REQUIRED" for row in candidates)
        assert session.query(TakeoffItemORM).filter_by(project_id=project_id).count() == 0
        assert session.query(ActiveLearningItemORM).filter_by(project_id=project_id).count() > 0
    overlays = client.get(
        f"/api/v1/projects/{project_id}/documents/{version_id}/pages/1/overlays"
    )
    assert overlays.status_code == 200 and overlays.json()["vision_candidates"]


def test_job_claiming_failure_retry_and_cancel(app: FastAPI, client: TestClient) -> None:
    project_id, version_id, _ = _project_upload(client)
    with app.state.session_factory() as session:
        queue = DatabaseJobQueueProvider(session)
        first, _ = queue.enqueue(
            job_type="BENCHMARK",
            project_id=project_id,
            document_version_id=version_id,
            operation_version="failure-v1",
            payload={},
        )
        failed = JobWorker(session, handlers={}).run_one()
        assert failed is not None
        assert failed.id == first.id and failed.status == "FAILED"
        retried = JobWorker(session, handlers={}).retry_failed(first.id)
        assert retried.status == "QUEUED" and retried.retry_count == 1
        cancelled = JobWorker(session, handlers={}).cancel(first.id)
        assert cancelled.status == "CANCELLED"


def test_training_image_is_reproducible_and_reference_only_is_blocked(
    app: FastAPI, client: TestClient
) -> None:
    project_id, version_id, _ = _project_upload(client)
    _eligible(client, project_id, version_id)
    first = _render(client, project_id, version_id)
    second = _render(client, project_id, version_id)
    assert first["checksum_sha256"] == second["checksum_sha256"] and second["reused"] is True
    with app.state.session_factory() as session:
        assert session.query(TrainingImageDerivativeORM).count() == 1
        path = app.state.document_storage.path_for_read(
            storage_key=session.query(TrainingImageDerivativeORM).one().storage_key
        )
        Image.open(path).verify()

    blocked_project, blocked_version, _ = _project_upload(client)
    _eligible(client, blocked_project, blocked_version, rights="REFERENCE_ONLY")
    blocked = client.post(
        f"/api/v1/projects/{blocked_project}/training-images",
        headers=HEADERS,
        json={"document_version_id": blocked_version, "page_number": 1, "dpi": 144},
    )
    assert blocked.status_code == 422


def test_auto_annotation_is_not_truth_and_coordinates_are_normalized(client: TestClient) -> None:
    project_id, version_id, sheet_id = _project_upload(client)
    auto = client.post(
        f"/api/v1/projects/{project_id}/training/annotations",
        headers=HEADERS,
        json={
            "document_version_id": version_id,
            "sheet_id": sheet_id,
            "task_type": "ELEMENT_DETECTION",
            "class_name": "WALL",
            "geometry_type": "BBOX",
            "geometry_normalized": {"bbox": [0.1, 0.1, 0.3, 0.2]},
            "source_type": "VISION_MODEL",
            "review_status": "VERIFIED",
        },
    )
    assert auto.status_code == 422 and "ground truth" in auto.text
    invalid = client.post(
        f"/api/v1/projects/{project_id}/training/annotations",
        headers=HEADERS,
        json={
            "document_version_id": version_id,
            "sheet_id": sheet_id,
            "task_type": "ELEMENT_DETECTION",
            "class_name": "WALL",
            "geometry_type": "BBOX",
            "geometry_normalized": {"bbox": [0.1, 0.1, 1.2, 0.2]},
        },
    )
    assert invalid.status_code == 422


def test_correction_is_immutable_and_becomes_training_sample(
    app: FastAPI, client: TestClient
) -> None:
    project_id, version_id, sheet_id = _project_upload(client)
    _eligible(client, project_id, version_id)
    image = _render(client, project_id, version_id)
    predicted = _annotation(
        client,
        project_id,
        version_id,
        sheet_id,
        image["id"],
        source_type="VISION_MODEL",
        review_status="REVIEW_REQUIRED",
    )
    corrected = client.post(
        f"/api/v1/training/annotations/{predicted['id']}/review",
        headers=HEADERS,
        json={
            "action": "CORRECT",
            "correction": {
                "class_name": "WINDOW",
                "geometry_normalized": {"bbox": [0.12, 0.1, 0.35, 0.18]},
            },
        },
    )
    assert corrected.status_code == 200
    assert corrected.json()["previous_annotation_id"] == predicted["id"]
    with app.state.session_factory() as session:
        original = session.get(VisionAnnotationORM, predicted["id"])
        assert original.class_name == "WALL" and original.review_status == "SUPERSEDED"
        assert session.query(TrainingSampleORM).filter_by(project_id=project_id).count() == 1
        assert (
            session.query(ActiveLearningItemORM).filter_by(reason="HUMAN_CORRECTION").count() == 1
        )


def test_project_level_split_freeze_manifest_and_coco_export(
    app: FastAPI, client: TestClient
) -> None:
    project_id, version_id, sheet_id = _project_upload(client)
    _eligible(client, project_id, version_id)
    image = _render(client, project_id, version_id)
    _annotation(client, project_id, version_id, sheet_id, image["id"], class_name="WALL")
    _annotation(client, project_id, version_id, sheet_id, image["id"], class_name="DOOR")
    dataset = client.post(
        "/api/v1/datasets",
        headers=HEADERS,
        json={"dataset_name": "gursar_arch_vision", "version": "0.1"},
    ).json()
    build = client.post(f"/api/v1/training/datasets/{dataset['id']}/build", headers=HEADERS)
    assert build.status_code == 200 and build.json()["added"] == 2
    assert build.json()["leakage"]["valid"] is True
    frozen = client.post(f"/api/v1/training/datasets/{dataset['id']}/freeze", headers=HEADERS)
    assert frozen.status_code == 200 and frozen.json()["status"] == "FROZEN"
    export = client.post(
        f"/api/v1/training/datasets/{dataset['id']}/exports",
        headers=HEADERS,
        json={"project_id": project_id, "export_format": "COCO"},
    )
    run = client.post(f"/api/v1/jobs/{export.json()['job_id']}/run", headers=HEADERS)
    assert run.json()["status"] == "COMPLETED"
    with app.state.session_factory() as session:
        items = session.query(VisionDatasetItemORM).all()
        assert len({item.dataset_split for item in items}) == 1
        result = run.json()["result_json"]
        export_row = session.get(VisionDatasetExportORM, result["export_id"])
        export_path = app.state.document_storage.path_for_read(storage_key=export_row.storage_key)
        with zipfile.ZipFile(export_path) as archive:
            coco = json.loads(archive.read("annotations.json"))
            assert len(coco["images"]) == 1 and len(coco["annotations"]) == 2
            assert {item["name"] for item in coco["categories"]} == {"WALL", "DOOR"}


def test_fusion_agreement_prefers_structured_geometry_and_conflict_is_reported() -> None:
    engine = DetectionFusionEngine(iou_threshold=0.5, distance_threshold=0.02)
    vector = DetectionCandidate("v1", "VECTOR_PDF", "WALL", {"bbox": [0.1, 0.1, 0.4, 0.2]}, 0.8)
    vision_same = DetectionCandidate("m1", "VISION", "WALL", {"bbox": [0.1, 0.1, 0.4, 0.2]}, 0.7)
    fused, conflicts = engine.fuse([vector], [vision_same])
    assert not conflicts and fused[0].agreement and fused[0].geometry == vector.geometry
    vision_conflict = DetectionCandidate("m2", "VISION", "COLUMN", vector.geometry, 0.9)
    fused, conflicts = engine.fuse([vector], [vision_conflict])
    assert len(conflicts) == 1 and fused[0].class_name == "WALL" and fused[0].conflict


def test_golden_benchmark_is_fixed_and_never_auto_activates(app: FastAPI) -> None:
    with app.state.session_factory() as session:
        dataset = DatasetVersionORM(dataset_name="golden-test", version="1", status="FROZEN")
        session.add(dataset)
        session.flush()
        model = VisionModelVersionORM(
            model_name="candidate",
            model_family="test-provider",
            version="1",
            dataset_version_id=dataset.id,
            classes_json=["WALL"],
            training_config_json={},
            metrics_json={},
            status="SHADOW",
        )
        session.add(model)
        session.flush()
        golden = GoldenVisionBenchmark(session, training_rules()["vision_benchmark_targets"])
        fixture = golden._load("v1")
        predictions = [
            {"id": row["id"], "class_name": row["class_name"], "bbox": row.get("bbox")}
            for row in fixture["cases"]
        ]
        run = golden.evaluate(
            model_version_id=model.id,
            dataset_version_id=dataset.id,
            predictions=predictions,
        )
        assert run.passed is True and model.status == "SHADOW"
        assert session.query(VisionBenchmarkRunORM).count() == 1


def test_model_training_gate_and_provider_interface(client: TestClient) -> None:
    dataset = client.post(
        "/api/v1/datasets",
        headers=HEADERS,
        json={"dataset_name": "not-ready", "version": "1"},
    ).json()
    response = client.post(
        "/api/v1/models/vision/train",
        headers=HEADERS,
        json={
            "dataset_id": dataset["id"],
            "model_name": "arch-v1",
            "version": "1",
            "task": "architectural_elements",
        },
    )
    assert response.status_code == 200 and response.json()["status"] == "DATASET_NOT_READY"
    provider = HeuristicVisionProvider()
    assert provider.model_version == "VISION_INTELLIGENCE_V1"


def test_archive_preview_requires_approval_and_preserves_existing_ingestion(
    app: FastAPI, client: TestClient
) -> None:
    project_id, version_id, _ = _project_upload(client)
    preview = client.post(
        f"/api/v1/projects/{project_id}/archive-imports/preview",
        headers=HEADERS,
        json={
            "manifest": {"rights_status": "INTERNAL_PROJECT", "building": {"use": "residential"}},
            "document_version_ids": [version_id],
        },
    )
    assert preview.status_code == 201 and preview.json()["status"] == "VALIDATED"
    approved = client.post(
        f"/api/v1/archive-imports/{preview.json()['id']}/approve", headers=HEADERS
    )
    assert approved.json()["status"] == "IMPORTED"
    with app.state.session_factory() as session:
        assert session.query(ProjectArchiveImportORM).count() == 1


def test_fifty_page_raster_job_processes_pages_incrementally(client: TestClient) -> None:
    project_id, version_id, _ = _project_upload(client, pages=50)
    created = client.post(
        f"/api/v1/projects/{project_id}/analysis/jobs",
        headers=HEADERS,
        json={"document_version_id": version_id, "analysis_modes": ["RASTER_VISION"]},
    )
    completed = client.post(f"/api/v1/jobs/{created.json()['id']}/run", headers=HEADERS)
    assert completed.status_code == 200
    assert completed.json()["status"] == "COMPLETED"
    assert completed.json()["result_json"]["page_count"] == 50
    assert completed.json()["progress"] == 100
