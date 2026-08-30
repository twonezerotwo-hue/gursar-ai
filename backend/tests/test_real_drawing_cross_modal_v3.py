from __future__ import annotations

import hashlib
import io
from pathlib import Path

import ifcopenshell
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from test_real_data_expansion_v2 import _ifc_render_context  # pyright: ignore[reportMissingImports]

from app.core.persistence import new_id
from app.core.security import ReviewPrincipal
from app.modules.acquisition.application.real_data import canonical_source_tier
from app.modules.acquisition.application.real_drawing_v3 import (
    CrossModalAlignmentService,
    IfcHorizontalSliceRendererV2,
    SemanticSvgImporter,
    VectorSemanticCandidateExtractor,
    _opening_relationships,
    _storey_container,
    compare_renderer_versions,
    real_data_status_summary,
)
from app.modules.acquisition.application.service import (
    DatasetCandidateBuilder,
    DatasetReadinessEngine,
    load_acquisition_rules,
)
from app.modules.acquisition.infrastructure.models import (
    CrossModalAnnotationLineageORM,
    CrossModalProjectLinkORM,
    IfcGeometryCacheORM,
)
from app.modules.documents.infrastructure.models import (
    DocumentVersionORM,
    ProjectDocumentORM,
    SheetORM,
)
from app.modules.vision.application.service import VisionDatasetService


def _drawing_version(app: FastAPI, session, *, project_id: str) -> DocumentVersionORM:
    content = b"%PDF-1.4\n% fixture"
    key = f"fixtures/{project_id}/drawing.pdf"
    app.state.document_storage.save(storage_key=key, source=io.BytesIO(content))
    document = ProjectDocumentORM(project_id=project_id, original_name="drawing.pdf")
    session.add(document)
    session.flush()
    version = DocumentVersionORM(
        id=new_id(),
        document_id=document.id,
        project_id=project_id,
        version=1,
        sha256=hashlib.sha256(content).hexdigest(),
        size_bytes=len(content),
        media_type="application/pdf",
        storage_key=key,
        page_count=1,
        status="READY",
    )
    session.add(version)
    session.flush()
    session.add(
        SheetORM(
            document_version_id=version.id,
            page_number=1,
            title="Ground floor plan",
            discipline="ARCHITECTURAL",
            drawing_type="PLAN",
            page_type="VECTOR",
            review_status="REVIEW_REQUIRED",
            width_points=1000,
            height_points=1000,
        )
    )
    session.flush()
    return version


def test_svg_semantics_apply_nested_transform_and_keep_review_status() -> None:
    svg = b"""<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'>
      <g transform='translate(10,20) scale(2)' class='wall'>
        <rect id='wall-1' x='5' y='5' width='10' height='5'/>
      </g></svg>"""
    rows, metadata = SemanticSvgImporter.parse(
        SemanticSvgImporter.__new__(SemanticSvgImporter), svg
    )
    assert metadata["candidate_count"] == 1
    assert rows[0].class_name == "WALL"
    assert rows[0].geometry["bbox"] == [0.2, 0.3, 0.4, 0.4]


@pytest.mark.parametrize(
    ("label", "expected"),
    [("mimari_duvar", "WALL"), ("KAPI_90", "DOOR"), ("pencere", "WINDOW")],
)
def test_svg_semantic_tokens(label: str, expected: str) -> None:
    importer = SemanticSvgImporter.__new__(SemanticSvgImporter)
    assert importer._semantic_class(label) == expected


def test_double_line_wall_is_only_a_review_candidate() -> None:
    rows = VectorSemanticCandidateExtractor().double_line_walls(
        [
            {"start": [0.0, 0.0], "end": [5.0, 0.0]},
            {"start": [0.0, 0.2], "end": [5.0, 0.2]},
        ]
    )
    assert len(rows) == 1
    assert rows[0]["class_name"] == "WALL"
    assert rows[0]["review_status"] == "REVIEW_REQUIRED"


def test_room_polygon_requires_contained_room_text() -> None:
    rows = VectorSemanticCandidateExtractor.rooms(
        [{"polygon": [[0, 0], [5, 0], [5, 4], [0, 4]]}],
        [{"point": [2, 2], "text": "SALON", "room_number": "01", "area_text": "20 m2"}],
    )
    assert rows[0]["class_name"] == "ROOM"
    assert rows[0]["room_name"] == "SALON"


def test_ifc_horizontal_slice_v2_is_exact_and_cache_idempotent(
    app: FastAPI, tmp_path: Path
) -> None:
    with app.state.session_factory.begin() as session:
        source, package, version, path, _ids = _ifc_render_context(app, session, tmp_path)
        renderer = IfcHorizontalSliceRendererV2(
            session, app.state.document_storage, render_size=256, slice_height=1.2
        )
        first = renderer.render(
            source_id=source.id,
            package_id=package.id,
            document_version_id=version.id,
            path=path,
        )
        cache_count = session.scalar(select(func.count()).select_from(IfcGeometryCacheORM))
        second = renderer.render(
            source_id=source.id,
            package_id=package.id,
            document_version_id=version.id,
            path=path,
        )
        alternate_style = renderer.render(
            source_id=source.id,
            package_id=package.id,
            document_version_id=version.id,
            path=path,
            style="clean_cad_validation",
        )
        assert first["exact_section_annotations"] == 2
        assert first["convex_hull_fallback_annotations"] == 0
        assert second["levels_reused"] == 2
        assert alternate_style["annotations"] == 2
        assert session.scalar(select(func.count()).select_from(IfcGeometryCacheORM)) == cache_count


def test_renderer_v1_v2_comparison_keeps_both_audit_versions(app: FastAPI, tmp_path: Path) -> None:
    from app.modules.acquisition.application.real_data import Ifc2DGroundTruthRenderer

    with app.state.session_factory.begin() as session:
        source, package, version, path, _ids = _ifc_render_context(app, session, tmp_path)
        Ifc2DGroundTruthRenderer(session, app.state.document_storage, render_size=256).render(
            source_id=source.id, package_id=package.id, document_version_id=version.id, path=path
        )
        IfcHorizontalSliceRendererV2(session, app.state.document_storage, render_size=256).render(
            source_id=source.id, package_id=package.id, document_version_id=version.id, path=path
        )
        result = compare_renderer_versions(session, document_version_id=version.id)
        assert result["compared"] == 2
        assert result["mean_iou"] > 0.9


def test_cross_modal_ransac_alignment_projects_one_copy(app: FastAPI, tmp_path: Path) -> None:
    with app.state.session_factory.begin() as session:
        source, package, version, path, ids = _ifc_render_context(app, session, tmp_path)
        _drawing_version(app, session, project_id=package.project_id)
        IfcHorizontalSliceRendererV2(session, app.state.document_storage, render_size=256).render(
            source_id=source.id, package_id=package.id, document_version_id=version.id, path=path
        )
        service = CrossModalAlignmentService(session)
        link = service.detect_pair(package_id=package.id)
        points = [[0.0, 0.0], [1.0, 0.0], [0.0, 1.0], [1.0, 1.0]]
        alignment = service.align(
            link_id=link.id,
            storey_external_id=ids["storey_0"],
            view_region_id=None,
            source_points=points,
            target_points=points,
            manual_verified=True,
        )
        assert alignment.status == "VERIFIED"
        assert alignment.metrics_json["mean_reprojection_error"] <= 1e-6
        first = service.project_annotations(alignment_id=alignment.id)
        second = service.project_annotations(alignment_id=alignment.id)
        assert first["created"] == 1 and second["created"] == 0


def test_cross_modal_alignment_stays_review_required_without_manual_verification(
    app: FastAPI, tmp_path: Path
) -> None:
    with app.state.session_factory.begin() as session:
        _source, package, _version, _path, ids = _ifc_render_context(app, session, tmp_path)
        drawing = _drawing_version(app, session, project_id=package.project_id)
        link = CrossModalProjectLinkORM(
            project_package_id=package.id,
            ifc_document_version_id=_version.id,
            drawing_document_version_id=drawing.id,
            alignment_status="PREDICTED",
            confidence=0.5,
        )
        session.add(link)
        session.flush()
        points = [[0.0, 0.0], [1.0, 0.0], [0.0, 1.0]]
        row = CrossModalAlignmentService(session).align(
            link_id=link.id,
            storey_external_id=ids["storey_0"],
            view_region_id=None,
            source_points=points,
            target_points=points,
        )
        assert row.status == "REVIEW_REQUIRED"


def test_expert_accept_promotes_cross_modal_candidate_to_gold(app: FastAPI, tmp_path: Path) -> None:
    with app.state.session_factory.begin() as session:
        source, package, version, path, ids = _ifc_render_context(app, session, tmp_path)
        _drawing_version(app, session, project_id=package.project_id)
        IfcHorizontalSliceRendererV2(session, app.state.document_storage, render_size=256).render(
            source_id=source.id, package_id=package.id, document_version_id=version.id, path=path
        )
        service = CrossModalAlignmentService(session)
        link = service.detect_pair(package_id=package.id)
        points = [[0.0, 0.0], [1.0, 0.0], [0.0, 1.0]]
        alignment = service.align(
            link_id=link.id,
            storey_external_id=ids["storey_0"],
            view_region_id=None,
            source_points=points,
            target_points=points,
            manual_verified=True,
        )
        service.project_annotations(alignment_id=alignment.id)
        lineage = session.scalar(select(CrossModalAnnotationLineageORM))
        assert lineage is not None
        row = VisionDatasetService(session, app.state.document_storage).review_annotation(
            annotation_id=lineage.annotation_id,
            action="ACCEPT",
            principal=ReviewPrincipal(reviewer_id="engineer-1", role="ENGINEER"),
            correction=None,
        )
        assert canonical_source_tier(row.source_type) == "GURSAR_GOLD"
        assert row.review_status == "VERIFIED"


def test_readiness_v3_reports_cross_modal_and_bim_caps(app: FastAPI) -> None:
    with app.state.session_factory.begin() as session:
        rules = dict(load_acquisition_rules()["readiness"])
        result = DatasetReadinessEngine(session, rules).evaluate()
        assert "cross_modal_reviewed" in result["classes"]["WALL"]
        assert "candidate_bim_derived_ratio" in result["classes"]["WALL"]
        assert result["dataset_candidate_name"] is None


def test_candidate_builder_blocks_partial_foundational_set(app: FastAPI) -> None:
    with app.state.session_factory.begin() as session:
        result = DatasetCandidateBuilder(session).build_ready_only(
            {"ready_classes": ["WALL"], "foundational_classes_ready": False}
        )
        assert result is None


def test_real_data_status_has_v3_operational_counters(app: FastAPI) -> None:
    with app.state.session_factory.begin() as session:
        result = real_data_status_summary(session)
        assert result["real_drawing_projects"] == 0
        assert result["cross_modal_candidates"] == 0
        assert "BIM_DERIVED_SILVER" in result["source_distribution"]


def test_opening_relationships_link_door_and_window_to_host_wall() -> None:
    model = ifcopenshell.file(schema="IFC4")
    wall = model.create_entity("IfcWall", GlobalId=ifcopenshell.guid.new())
    door = model.create_entity("IfcDoor", GlobalId=ifcopenshell.guid.new())
    window = model.create_entity("IfcWindow", GlobalId=ifcopenshell.guid.new())
    for element in (door, window):
        opening = model.create_entity("IfcOpeningElement", GlobalId=ifcopenshell.guid.new())
        model.create_entity(
            "IfcRelVoidsElement",
            GlobalId=ifcopenshell.guid.new(),
            RelatingBuildingElement=wall,
            RelatedOpeningElement=opening,
        )
        model.create_entity(
            "IfcRelFillsElement",
            GlobalId=ifcopenshell.guid.new(),
            RelatingOpeningElement=opening,
            RelatedBuildingElement=element,
        )
    relations = _opening_relationships(model)
    assert relations[door.GlobalId]["host_wall_global_id"] == wall.GlobalId
    assert relations[window.GlobalId]["host_wall_global_id"] == wall.GlobalId


def test_ifc_space_aggregate_resolves_to_storey() -> None:
    model = ifcopenshell.file(schema="IFC4")
    storey = model.create_entity("IfcBuildingStorey", GlobalId=ifcopenshell.guid.new())
    space = model.create_entity("IfcSpace", GlobalId=ifcopenshell.guid.new())
    model.create_entity(
        "IfcRelAggregates",
        GlobalId=ifcopenshell.guid.new(),
        RelatingObject=storey,
        RelatedObjects=[space],
    )
    assert _storey_container(space) == storey


def test_ransac_outlier_fails_strict_alignment_policy(app: FastAPI, tmp_path: Path) -> None:
    with app.state.session_factory.begin() as session:
        _source, package, version, _path, ids = _ifc_render_context(app, session, tmp_path)
        drawing = _drawing_version(app, session, project_id=package.project_id)
        link = CrossModalProjectLinkORM(
            project_package_id=package.id,
            ifc_document_version_id=version.id,
            drawing_document_version_id=drawing.id,
            alignment_status="PREDICTED",
            confidence=0.5,
        )
        session.add(link)
        session.flush()
        row = CrossModalAlignmentService(session, min_inlier_ratio=0.95).align(
            link_id=link.id,
            storey_external_id=ids["storey_0"],
            view_region_id=None,
            source_points=[[0.0, 0.0], [1.0, 0.0], [0.0, 1.0], [1.0, 1.0]],
            target_points=[[0.0, 0.0], [1.0, 0.0], [0.0, 1.0], [99.0, 99.0]],
            manual_verified=True,
        )
        assert row.status == "REVIEW_REQUIRED"
        assert row.metrics_json["inlier_ratio"] < 0.95


def test_real_data_status_endpoint_is_available(client: TestClient) -> None:
    response = client.get("/api/v1/training/real-data/status")
    assert response.status_code == 200
    assert "readiness" in response.json()


def test_cross_modal_candidates_endpoint_requires_engineer(client: TestClient) -> None:
    assert client.get("/api/v1/cross-modal/candidates").status_code == 401
    response = client.get(
        "/api/v1/cross-modal/candidates",
        headers={
            "X-Admin-Key": "test-admin-key",
            "X-Reviewer-Id": "engineer-1",
            "X-Reviewer-Role": "ENGINEER",
        },
    )
    assert response.status_code == 200 and response.json() == []
