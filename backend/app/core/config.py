from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Settings:
    environment: str
    database_url: str
    admin_api_key: str
    default_location_code: str
    default_locale: str
    understanding_provider: str
    openai_api_key: str | None
    openai_model: str
    chat_provider: str = "ollama"
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen3:8b"
    ollama_fallback_model: str | None = "qwen3:4b"
    ollama_timeout_seconds: float = 120.0
    ollama_context_window: int = 8192
    quote_first: bool = True
    public_access_username: str | None = None
    public_access_password: str | None = None
    document_storage_root: Path = Path("./data/documents")
    database_backup_root: Path = Path("./data/backups")
    document_max_upload_bytes: int = 50 * 1024 * 1024
    document_max_pdf_pages: int = 500
    document_analysis_max_pages: int = 25
    dxf_max_entities: int = 250_000
    dxf_analysis_timeout_seconds: float = 120.0
    dxf_max_block_recursion_depth: int = 8
    vector_pdf_max_pages: int = 100
    vector_pdf_max_objects_per_page: int = 50_000
    vector_pdf_max_text_objects: int = 100_000
    vector_pdf_max_view_regions: int = 1000
    vector_pdf_analysis_timeout_seconds: float = 180.0
    pdf_render_resolution: int = 144
    vision_enabled: bool = True
    vision_provider: str = "heuristic"
    vision_model_name: str = "opencv-layout-baseline"
    vision_model_version: str = "VISION_INTELLIGENCE_V1"
    vision_device: str = "cpu"
    vision_render_dpi: int = 144
    vision_low_confidence_threshold: float = 0.65
    vision_fusion_iou_threshold: float = 0.50
    vision_coordinate_distance_threshold: float = 0.02
    max_vision_pages_per_job: int = 100
    max_image_pixels: int = 40_000_000
    max_render_dpi: int = 300
    max_concurrent_vision_jobs: int = 1
    analysis_job_max_retries: int = 2
    analysis_job_retry_delay_seconds: int = 5
    acquisition_test_mode: bool = True
    acquisition_max_single_file_mb: int = 256
    acquisition_max_archive_file_count: int = 10_000
    acquisition_max_total_download_bytes: int = 2 * 1024 * 1024 * 1024
    acquisition_download_timeout_seconds: float = 30
    acquisition_max_storage_gb: float = 5
    acquisition_allowed_domains: tuple[str, ...] = (
        "github.com",
        "api.github.com",
        "huggingface.co",
        "raw.githubusercontent.com",
        "buildingsmart.org",
        "technical.buildingsmart.org",
        "codeload.github.com",
        "zenodo.org",
    )
    remote_chat_provider: str | None = None
    groq_api_key: str | None = None
    groq_base_url: str = "https://api.groq.com/openai/v1"
    groq_model: str = "openai/gpt-oss-120b"
    groq_timeout_seconds: float = 45.0
    groq_context_window: int = 32768
    conversation_token_secret: str = "development-conversation-token-secret"
    conversation_session_ttl_seconds: int = 24 * 60 * 60

    @classmethod
    def from_environment(cls) -> Settings:
        return cls(
            environment=os.getenv("GURSAR_ENVIRONMENT", "development"),
            database_url=os.getenv("GURSAR_DATABASE_URL", "sqlite:///./gursar.db"),
            admin_api_key=os.getenv("GURSAR_ADMIN_API_KEY", "local-development-key"),
            default_location_code=os.getenv("GURSAR_DEFAULT_LOCATION_CODE", "TR/ISTANBUL"),
            default_locale=os.getenv("GURSAR_DEFAULT_LOCALE", "tr"),
            understanding_provider=os.getenv("GURSAR_UNDERSTANDING_PROVIDER", "rule_based"),
            openai_api_key=os.getenv("OPENAI_API_KEY") or None,
            openai_model=os.getenv("OPENAI_MODEL", "gpt-5-mini"),
            chat_provider=os.getenv("GURSAR_CHAT_PROVIDER", "ollama"),
            ollama_base_url=os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434"),
            ollama_model=os.getenv("OLLAMA_MODEL", "qwen3:8b"),
            ollama_fallback_model=os.getenv("OLLAMA_FALLBACK_MODEL", "qwen3:4b") or None,
            ollama_timeout_seconds=float(os.getenv("OLLAMA_TIMEOUT_SECONDS", "120")),
            ollama_context_window=int(os.getenv("OLLAMA_CONTEXT_WINDOW", "8192")),
            public_access_username=os.getenv("GURSAR_PUBLIC_ACCESS_USERNAME") or None,
            public_access_password=os.getenv("GURSAR_PUBLIC_ACCESS_PASSWORD") or None,
            document_storage_root=Path(
                os.getenv("GURSAR_DOCUMENT_STORAGE_ROOT", "./data/documents")
            ),
            database_backup_root=Path(os.getenv("GURSAR_DATABASE_BACKUP_ROOT", "./data/backups")),
            document_max_upload_bytes=int(
                os.getenv("GURSAR_DOCUMENT_MAX_UPLOAD_BYTES", str(50 * 1024 * 1024))
            ),
            document_max_pdf_pages=int(os.getenv("GURSAR_DOCUMENT_MAX_PDF_PAGES", "500")),
            document_analysis_max_pages=int(os.getenv("GURSAR_DOCUMENT_ANALYSIS_MAX_PAGES", "25")),
            dxf_max_entities=int(os.getenv("GURSAR_DXF_MAX_ENTITIES", "250000")),
            dxf_analysis_timeout_seconds=float(
                os.getenv("GURSAR_DXF_ANALYSIS_TIMEOUT_SECONDS", "120")
            ),
            dxf_max_block_recursion_depth=int(
                os.getenv("GURSAR_DXF_MAX_BLOCK_RECURSION_DEPTH", "8")
            ),
            vector_pdf_max_pages=int(os.getenv("GURSAR_VECTOR_PDF_MAX_PAGES", "100")),
            vector_pdf_max_objects_per_page=int(
                os.getenv("GURSAR_VECTOR_PDF_MAX_OBJECTS_PER_PAGE", "50000")
            ),
            vector_pdf_max_text_objects=int(
                os.getenv("GURSAR_VECTOR_PDF_MAX_TEXT_OBJECTS", "100000")
            ),
            vector_pdf_max_view_regions=int(
                os.getenv("GURSAR_VECTOR_PDF_MAX_VIEW_REGIONS", "1000")
            ),
            vector_pdf_analysis_timeout_seconds=float(
                os.getenv("GURSAR_VECTOR_PDF_ANALYSIS_TIMEOUT_SECONDS", "180")
            ),
            pdf_render_resolution=int(os.getenv("GURSAR_PDF_RENDER_RESOLUTION", "144")),
            vision_enabled=os.getenv("GURSAR_VISION_ENABLED", "true").lower() == "true",
            vision_provider=os.getenv("GURSAR_VISION_PROVIDER", "heuristic"),
            vision_model_name=os.getenv("GURSAR_VISION_MODEL_NAME", "opencv-layout-baseline"),
            vision_model_version=os.getenv("GURSAR_VISION_MODEL_VERSION", "VISION_INTELLIGENCE_V1"),
            vision_device=os.getenv("GURSAR_VISION_DEVICE", "cpu"),
            vision_render_dpi=int(os.getenv("GURSAR_VISION_RENDER_DPI", "144")),
            vision_low_confidence_threshold=float(
                os.getenv("GURSAR_VISION_LOW_CONFIDENCE_THRESHOLD", "0.65")
            ),
            vision_fusion_iou_threshold=float(
                os.getenv("GURSAR_VISION_FUSION_IOU_THRESHOLD", "0.50")
            ),
            vision_coordinate_distance_threshold=float(
                os.getenv("GURSAR_VISION_COORDINATE_DISTANCE_THRESHOLD", "0.02")
            ),
            max_vision_pages_per_job=int(os.getenv("GURSAR_MAX_VISION_PAGES_PER_JOB", "100")),
            max_image_pixels=int(os.getenv("GURSAR_MAX_IMAGE_PIXELS", "40000000")),
            max_render_dpi=int(os.getenv("GURSAR_MAX_RENDER_DPI", "300")),
            max_concurrent_vision_jobs=int(os.getenv("GURSAR_MAX_CONCURRENT_VISION_JOBS", "1")),
            analysis_job_max_retries=int(os.getenv("GURSAR_ANALYSIS_JOB_MAX_RETRIES", "2")),
            analysis_job_retry_delay_seconds=int(
                os.getenv("GURSAR_ANALYSIS_JOB_RETRY_DELAY_SECONDS", "5")
            ),
            acquisition_test_mode=os.getenv("GURSAR_ACQUISITION_TEST_MODE", "true").lower()
            == "true",
            acquisition_max_single_file_mb=int(
                os.getenv("GURSAR_ACQUISITION_MAX_SINGLE_FILE_MB", "256")
            ),
            acquisition_max_archive_file_count=int(
                os.getenv("GURSAR_ACQUISITION_MAX_ARCHIVE_FILE_COUNT", "10000")
            ),
            acquisition_max_total_download_bytes=int(
                os.getenv("GURSAR_ACQUISITION_MAX_TOTAL_DOWNLOAD_BYTES", str(2 * 1024**3))
            ),
            acquisition_download_timeout_seconds=float(
                os.getenv("GURSAR_ACQUISITION_DOWNLOAD_TIMEOUT_SECONDS", "30")
            ),
            acquisition_max_storage_gb=float(os.getenv("GURSAR_ACQUISITION_MAX_STORAGE_GB", "5")),
            acquisition_allowed_domains=tuple(
                item.strip().lower()
                for item in os.getenv(
                    "GURSAR_ACQUISITION_ALLOWED_DOMAINS",
                    "github.com,api.github.com,huggingface.co,raw.githubusercontent.com,"
                    "buildingsmart.org,technical.buildingsmart.org,codeload.github.com,zenodo.org",
                ).split(",")
                if item.strip()
            ),
            remote_chat_provider=os.getenv("GURSAR_REMOTE_CHAT_PROVIDER") or None,
            groq_api_key=os.getenv("GROQ_API_KEY") or None,
            groq_base_url=os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1"),
            groq_model=os.getenv("GROQ_MODEL", "openai/gpt-oss-120b"),
            groq_timeout_seconds=float(os.getenv("GROQ_TIMEOUT_SECONDS", "45")),
            groq_context_window=int(os.getenv("GROQ_CONTEXT_WINDOW", "32768")),
            conversation_token_secret=os.getenv(
                "GURSAR_CONVERSATION_TOKEN_SECRET",
                "development-conversation-token-secret",
            ),
            conversation_session_ttl_seconds=int(
                os.getenv("GURSAR_CONVERSATION_SESSION_TTL_SECONDS", str(24 * 60 * 60))
            ),
        )
