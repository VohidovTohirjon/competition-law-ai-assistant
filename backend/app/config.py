import os
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(PROJECT_ROOT / ".env", ".env"), extra="ignore")

    # "production" turns on strict startup validation of the deployment config.
    app_env: str = "development"
    database_url: str = "postgresql+psycopg://raqobat:raqobat@localhost:5432/raqobat"
    secret_key: str = ""
    access_token_minutes: int = 480
    data_dir: Path = Path("data")
    max_upload_mb: int = 20
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173,http://localhost:5174,http://127.0.0.1:5174"
    # Which OpenAI-compatible endpoint answers first: "local" (self-hosted vLLM)
    # or "groq". A second provider is only ever tried when explicitly enabled.
    llm_provider: str = "groq"
    llm_fallback_enabled: bool = False
    llm_force_unavailable: bool = False

    local_llm_base_url: str = ""
    local_llm_api_key: str = ""
    local_llm_model: str = "openai/gpt-oss-20b"
    local_llm_models: str = ""
    local_llm_timeout_seconds: float = 120.0
    local_llm_max_tokens: int = 3200
    local_llm_strict_schema: bool = True
    # gpt-oss reasoning depth for the self-hosted server. "low" keeps latency down;
    # raise to "medium" here if answer quality ever needs it.
    local_llm_reasoning_effort: str = "low"

    # Completion budgets per request type. A short chat answer must not reserve the
    # same decode budget as a multi-page official draft.
    llm_max_tokens_general: int = 512
    llm_max_tokens_legal: int = 1024
    llm_max_tokens_document: int = 1024
    llm_max_tokens_drafting: int = 3200

    groq_api_key: str = ""
    groq_model: str = ""
    groq_models: str = (
        "openai/gpt-oss-120b,qwen/qwen3.6-27b,openai/gpt-oss-20b,"
        "groq/compound,groq/compound-mini"
    )
    groq_force_unavailable: bool = False
    # Default deny: confidential/internal text may reach an external provider only
    # after an authorized deployment explicitly enables this policy.
    allow_external_confidential_ai: bool = False
    groq_timeout_seconds: float = 30.0
    # gpt-oss reasoning depth on Groq. "low" is the latency-first default; "medium"
    # noticeably improves citation discipline on legal answers at a small cost.
    groq_reasoning_effort: str = "low"
    groq_max_tokens: int = 3200
    embedding_model: str = "BAAI/bge-m3"
    embedding_backend: str = "sentence_transformers"
    embedding_dimensions: int = 1024
    embedding_warmup_on_startup: bool = True
    # Load the embedding model in float16 on MPS/CUDA to halve its memory footprint.
    embedding_half_precision: bool = True
    context_max_chars: int = 18000
    # Validated chat answers are reused for identical questions (see answer_cache.py).
    answer_cache_enabled: bool = True
    retrieval_min_score: float = 0.48
    retrieval_candidate_limit: int = 60
    # Password given to accounts that the explicitly confirmed local seed command
    # creates. It is never applied to accounts that already exist, is never used by
    # the application at runtime, and must not be set in a production deployment.
    local_seed_password: str = "12345678"

    @property
    def cors_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]

    @property
    def groq_model_list(self) -> list[str]:
        configured = [item.strip() for item in self.groq_models.split(",") if item.strip()]
        if self.groq_model:
            configured = [self.groq_model] + [
                model for model in configured if model != self.groq_model
            ]
        return list(dict.fromkeys(configured))

    @property
    def local_llm_model_list(self) -> list[str]:
        """Primary local model first, then any additional models the server serves."""
        configured = [item.strip() for item in self.local_llm_models.split(",") if item.strip()]
        if self.local_llm_model:
            configured = [self.local_llm_model.strip()] + [
                model for model in configured if model != self.local_llm_model.strip()
            ]
        return [model for model in dict.fromkeys(configured) if model]

    def max_tokens_for(self, kind: str) -> int:
        """Completion budget for a request type; unknown kinds keep the largest."""
        return {
            "general": self.llm_max_tokens_general,
            "legal": self.llm_max_tokens_legal,
            "document": self.llm_max_tokens_document,
            "drafting": self.llm_max_tokens_drafting,
        }.get(kind, self.llm_max_tokens_drafting)

    @property
    def llm_force_unavailable_effective(self) -> bool:
        # `GROQ_FORCE_UNAVAILABLE` predates the provider abstraction and still works.
        return self.llm_force_unavailable or self.groq_force_unavailable


class ConfigurationError(RuntimeError):
    """Raised at startup when a production deployment is misconfigured."""


def validate_production_settings(settings: "Settings", environ: dict | None = None) -> None:
    """Refuse to start a misconfigured production deployment.

    The dangerous case is silent: `LLM_PROVIDER` is absent from the environment, the
    code default ("groq") takes over and a local GPT-OSS deployment quietly starts
    talking to an external provider. In production the provider must be stated
    explicitly and its own required settings must be present.
    """
    if settings.app_env.strip().lower() != "production":
        return
    environ = os.environ if environ is None else environ
    problems: list[str] = []

    if len(settings.secret_key) < 32:
        problems.append("SECRET_KEY kamida 32 belgidan iborat bo‘lishi shart")

    declared = (environ.get("LLM_PROVIDER") or "").strip().lower()
    if not declared:
        problems.append(
            "LLM_PROVIDER aniq ko‘rsatilmagan. Productionda uni aniq yozing "
            "(masalan LLM_PROVIDER=local); standart qiymatga tayanish mumkin emas"
        )
    elif declared not in {"local", "groq"}:
        problems.append(f"LLM_PROVIDER qiymati noto‘g‘ri: {declared!r}. Ruxsat etilgan: local, groq")
    elif declared == "local":
        if not settings.local_llm_base_url.strip():
            problems.append("LLM_PROVIDER=local uchun LOCAL_LLM_BASE_URL to‘ldirilishi shart")
        if not settings.local_llm_model_list:
            problems.append("LLM_PROVIDER=local uchun LOCAL_LLM_MODEL to‘ldirilishi shart")
    elif declared == "groq" and not settings.groq_api_key.strip():
        problems.append(
            "LLM_PROVIDER=groq tanlangan, lekin GROQ_API_KEY bo‘sh. Tashqi provayder "
            "tasodifan yoqilib qolmasligi uchun ishga tushirish to‘xtatildi"
        )

    if settings.llm_fallback_enabled and not settings.groq_api_key.strip():
        problems.append("LLM_FALLBACK_ENABLED=true, lekin zaxira provayder uchun GROQ_API_KEY yo‘q")

    if problems:
        raise ConfigurationError(
            "Production konfiguratsiyasi noto‘g‘ri:\n  - " + "\n  - ".join(problems)
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
