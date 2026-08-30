from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class DiscoveryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    queries: list[str] | None = None
    dry_run: bool = False
    use_remote_providers: bool = False


class BootstrapRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: str = "FULL_BOOTSTRAP"
    use_remote_providers: bool = False


class LicenseReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rights_status: str
    license_name: str
    license_url: str | None = None


class DownloadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dry_run: bool = False


class IngestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    manifest_id: str
    dry_run: bool = False


class ClassMappingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    external_class: str
    gursar_class: str
    metadata: dict[str, str] = {}
    confidence: float = Field(default=1, ge=0, le=1)
    verified: bool = True


class SyntheticGenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    count: int = Field(default=1, ge=1, le=2000)
    building_type: str = "residential"
    floors: dict[str, int] = {"min": 1, "max": 8}
    seed: int = 42


class RealDataJobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_type: str
    source_id: str | None = None
    package_id: str | None = None
    document_version_id: str | None = None
    storage_key: str | None = None
    limit: int = Field(default=20, ge=1, le=17_000)
    render_style: str = "clean_cad"
    renderer_version: str = "IFC_2D_GROUND_TRUTH_V1"
    offset: int = Field(default=0, ge=0)
