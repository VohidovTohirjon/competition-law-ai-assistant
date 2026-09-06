"""Provider-agnostic access to an OpenAI-compatible chat-completions API.

A self-hosted vLLM server and Groq differ only in base URL, credentials, model pool
and a handful of request quirks, so both are the same client driven by a different
`ProviderProfile`. Nothing above this module knows which provider answered.

The application never silently reaches for a second provider: a fallback is used only
when configuration explicitly enables one.
"""

import asyncio
import json
import logging
import time
from dataclasses import dataclass, replace

import httpx
from fastapi import HTTPException, status

from ..config import get_settings

logger = logging.getLogger(__name__)

# Groq-hosted models known to honour strict OpenAI json_schema response formats.
GROQ_STRICT_SCHEMA_MODELS = frozenset({"openai/gpt-oss-20b", "openai/gpt-oss-120b"})


@dataclass(frozen=True)
class ProviderProfile:
    """Everything that distinguishes one OpenAI-compatible endpoint from another."""

    name: str
    label: str
    base_url: str
    api_key: str
    models: tuple[str, ...]
    timeout_seconds: float
    max_tokens: int
    requires_api_key: bool = True
    # vLLM builds accept `max_tokens`; Groq expects `max_completion_tokens`.
    max_tokens_field: str = "max_completion_tokens"
    # Groq-specific reasoning switches would be rejected by a plain vLLM server.
    send_groq_reasoning_hints: bool = False
    reasoning_effort: str = ""
    strict_schema_models: frozenset[str] = frozenset()
    # A single-model server has no failover partner, so a strict-schema rejection
    # is retried once in plain JSON mode instead of failing the request.
    degrade_schema_on_rejection: bool = False

    @property
    def configured(self) -> bool:
        if not self.base_url or not self.models:
            return False
        return bool(self.api_key) or not self.requires_api_key

    @property
    def chat_url(self) -> str:
        return f"{self.base_url.rstrip('/')}/chat/completions"

    @property
    def models_url(self) -> str:
        return f"{self.base_url.rstrip('/')}/models"

    @property
    def cache_key(self) -> tuple:
        return (self.name, self.base_url, self.api_key, self.models)


@dataclass(frozen=True)
class ProviderHealth:
    """Diagnostic state for one provider. Never carries URLs or credentials."""

    name: str
    label: str
    # not_configured | unreachable | unauthorized | model_missing | ready
    state: str
    model: str | None = None
    detail: str = ""

    @property
    def reachable(self) -> bool:
        return self.state in {"ready", "model_missing"}

    @property
    def model_loaded(self) -> bool:
        return self.state == "ready"


def _groq_profile(settings) -> ProviderProfile:
    return ProviderProfile(
        name="groq",
        label="Groq (tashqi xizmat)",
        base_url="https://api.groq.com/openai/v1",
        api_key=settings.groq_api_key,
        models=tuple(settings.groq_model_list),
        timeout_seconds=settings.groq_timeout_seconds,
        max_tokens=settings.groq_max_tokens,
        requires_api_key=True,
        max_tokens_field="max_completion_tokens",
        send_groq_reasoning_hints=True,
        reasoning_effort=settings.groq_reasoning_effort,
        strict_schema_models=GROQ_STRICT_SCHEMA_MODELS,
    )


def _local_profile(settings) -> ProviderProfile:
    models = tuple(settings.local_llm_model_list)
    return ProviderProfile(
        name="local",
        label="Lokal LLM server",
        base_url=settings.local_llm_base_url,
        api_key=settings.local_llm_api_key,
        models=models,
        timeout_seconds=settings.local_llm_timeout_seconds,
        max_tokens=settings.local_llm_max_tokens,
        # A vLLM server started without --api-key needs no credential.
        requires_api_key=False,
        max_tokens_field="max_tokens",
        send_groq_reasoning_hints=False,
        reasoning_effort=settings.local_llm_reasoning_effort,
        strict_schema_models=frozenset(models) if settings.local_llm_strict_schema else frozenset(),
        degrade_schema_on_rejection=True,
    )


class OpenAICompatibleProvider:
    """One endpoint with a priority-ordered model pool and per-model cooldowns."""

    def __init__(self, profile: ProviderProfile):
        self.profile = profile
        self._cooldown_until: dict[str, float] = {}
        self._last_model_used: str | None = None
        self._schema_unsupported: set[str] = set()

    @property
    def configured(self) -> bool:
        return self.profile.configured

    @property
    def active_model(self) -> str | None:
        now = time.monotonic()
        return next(
            (model for model in self.profile.models
             if self._cooldown_until.get(model, 0) <= now),
            None,
        )

    def reset_runtime_state(self) -> None:
        """Clear transient cooldowns; useful for diagnostics and isolated tests."""
        self._cooldown_until.clear()
        self._last_model_used = None
        self._schema_unsupported.clear()

    def _cool_down(self, model: str, seconds: float) -> None:
        self._cooldown_until[model] = max(
            self._cooldown_until.get(model, 0), time.monotonic() + max(seconds, 1),
        )

    @staticmethod
    def _retry_after(response: httpx.Response, default: float = 60.0) -> float:
        try:
            return min(max(float(response.headers.get("retry-after", default)), 1), 86_400)
        except (TypeError, ValueError):
            return default

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.profile.api_key}"} if self.profile.api_key else {}

    def _reasoning_fields(self, model: str) -> dict:
        if self.profile.send_groq_reasoning_hints:
            if model.startswith("openai/gpt-oss-"):
                return {"include_reasoning": False,
                        "reasoning_effort": self.profile.reasoning_effort or "low"}
            if model.startswith("qwen/"):
                return {"reasoning_effort": "none"}
            return {}
        return {"reasoning_effort": self.profile.reasoning_effort} if self.profile.reasoning_effort else {}

    def _payload(self, model: str, system: str, user: str, temperature: float,
                 response_schema: dict | None, *, allow_strict_schema: bool = True,
                 max_tokens: int | None = None) -> dict:
        payload = {
            "model": model,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "temperature": temperature,
            self.profile.max_tokens_field: max_tokens or self.profile.max_tokens,
        }
        payload.update(self._reasoning_fields(model))
        if not response_schema:
            return payload
        strict = (allow_strict_schema and model in self.profile.strict_schema_models
                  and model not in self._schema_unsupported)
        if strict:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "raqobat_structured_result",
                    "strict": True,
                    "schema": response_schema,
                },
            }
        else:
            payload["response_format"] = {"type": "json_object"}
            payload["messages"][1]["content"] = (
                user
                + "\n\nFaqat JSON obyekt qaytaring. Quyidagi JSON sxemaga qat’iy rioya qiling:\n"
                + json.dumps(response_schema, ensure_ascii=False)
            )
        return payload

    async def _post(self, payload: dict) -> httpx.Response:
        async with httpx.AsyncClient(timeout=self.profile.timeout_seconds) as client:
            return await client.post(self.profile.chat_url, headers=self._headers(), json=payload)

    async def probe(self) -> ProviderHealth:
        """Ask the endpoint which models it currently serves."""
        profile = self.profile
        if not profile.configured:
            return ProviderHealth(profile.name, profile.label, "not_configured",
                                  detail="Provayder sozlanmagan")
        wanted = profile.models[0] if profile.models else None
        try:
            async with httpx.AsyncClient(timeout=min(profile.timeout_seconds, 5.0)) as client:
                response = await client.get(profile.models_url, headers=self._headers())
        except httpx.HTTPError as exc:
            logger.warning("LLM probe failed provider=%s error_type=%s", profile.name, type(exc).__name__)
            return ProviderHealth(profile.name, profile.label, "unreachable", wanted,
                                  "Server bilan aloqa yo‘q")
        if response.status_code in {401, 403}:
            return ProviderHealth(profile.name, profile.label, "unauthorized", wanted,
                                  "Autentifikatsiya rad etildi")
        if response.status_code >= 400:
            return ProviderHealth(profile.name, profile.label, "unreachable", wanted,
                                  f"Server {response.status_code} qaytardi")
        try:
            served = {item.get("id") for item in response.json().get("data", [])}
        except (AttributeError, TypeError, ValueError):
            return ProviderHealth(profile.name, profile.label, "unreachable", wanted,
                                  "Model ro‘yxati o‘qib bo‘lmadi")
        if wanted and served and wanted not in served:
            return ProviderHealth(profile.name, profile.label, "model_missing", wanted,
                                  "Server ishlayapti, lekin sozlangan model yuklanmagan")
        return ProviderHealth(profile.name, profile.label, "ready", wanted, "Model tayyor")

    async def generate(self, system: str, user: str, *, temperature: float = 0.15,
                       response_schema: dict | None = None,
                       request_type: str | None = None,
                       max_tokens: int | None = None,
                       _excluded_models: set[str] | None = None) -> str:
        profile = self.profile
        diagnostic_type = request_type or ("structured" if response_schema else "text")
        if not profile.configured:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "AI xizmati sozlanmagan. Administratorga murojaat qiling",
            )

        now = time.monotonic()
        excluded = _excluded_models or set()
        candidates = [
            model for model in profile.models
            if model not in excluded and self._cooldown_until.get(model, 0) <= now
        ]
        if not candidates:
            # Every model is in a rate-limit cooldown. When the nearest one frees up
            # within a few seconds, waiting is far better for the user than a 429.
            pending = [self._cooldown_until[model] - now for model in profile.models
                       if model not in excluded and model in self._cooldown_until]
            wait = min(pending) if pending else 0
            if 0 < wait <= 30:
                logger.info("LLM waiting for cooldown provider=%s seconds=%s",
                            profile.name, round(wait, 1))
                await asyncio.sleep(wait + 0.2)
                now = time.monotonic()
                candidates = [
                    model for model in profile.models
                    if model not in excluded and self._cooldown_until.get(model, 0) <= now
                ]
        if not candidates:
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                "Barcha AI modellarining vaqtinchalik limiti tugagan. Birozdan so‘ng qayta urinib ko‘ring",
            )

        saw_rate_limit = saw_timeout = saw_model_error = False
        last_output_error: str | None = None
        for model_index, model in enumerate(candidates, start=1):
            allow_strict = True
            for attempt in range(2):
                payload = self._payload(model, system, user, temperature, response_schema,
                                        allow_strict_schema=allow_strict, max_tokens=max_tokens)
                started = time.monotonic()
                try:
                    response = await self._post(payload)
                except httpx.TimeoutException:
                    saw_timeout = True
                    self._cool_down(model, 5)
                    logger.warning("LLM failover reason=timeout provider=%s model=%s",
                                   profile.name, model)
                    break
                except httpx.HTTPError as exc:
                    self._cool_down(model, 5)
                    logger.warning("LLM failover reason=connection provider=%s model=%s error_type=%s",
                                   profile.name, model, type(exc).__name__)
                    break

                request_id = (response.headers.get("x-request-id")
                              or response.headers.get("request-id"))
                if response.status_code == 401:
                    logger.error("LLM authentication failure provider=%s status=401 request_id=%s",
                                 profile.name, request_id)
                    raise HTTPException(
                        status.HTTP_503_SERVICE_UNAVAILABLE,
                        "AI xizmati autentifikatsiyasi noto‘g‘ri sozlangan. Administratorga murojaat qiling",
                    )
                if response.status_code == 429:
                    saw_rate_limit = True
                    cooldown = self._retry_after(response)
                    self._cool_down(model, cooldown)
                    logger.warning(
                        "LLM failover reason=rate_limit provider=%s model=%s cooldown_seconds=%s "
                        "request_id=%s", profile.name, model, round(cooldown), request_id,
                    )
                    break
                if response.status_code == 403:
                    saw_model_error = True
                    self._cool_down(model, 3600)
                    logger.warning("LLM failover reason=permission provider=%s model=%s",
                                   profile.name, model)
                    break
                if response.status_code in {400, 404, 422}:
                    body_text = str(getattr(response, "text", "") or "")[:400].replace("\n", " ")
                    # Groq answers 400 "json_validate_failed" when one generation did
                    # not satisfy the strict schema. That is a transient failure of a
                    # single completion, not a broken model: retry once in plain
                    # JSON mode and keep the model in rotation.
                    transient_schema = bool(response_schema) and (
                        "json_validate_failed" in body_text or "failed_generation" in body_text
                    )
                    # A server that rejects strict json_schema is retried once in
                    # plain JSON mode before the model is demoted.
                    if (response_schema and allow_strict and attempt == 0
                            and (profile.degrade_schema_on_rejection or transient_schema)
                            and model in profile.strict_schema_models):
                        if profile.degrade_schema_on_rejection and not transient_schema:
                            self._schema_unsupported.add(model)
                        allow_strict = False
                        logger.warning(
                            "LLM schema degrade provider=%s model=%s status=%s transient=%s",
                            profile.name, model, response.status_code, transient_schema,
                        )
                        continue
                    saw_model_error = True
                    self._cool_down(model, 30 if transient_schema else 300)
                    logger.warning("LLM failover reason=model_or_format provider=%s status=%s model=%s body=%s",
                                   profile.name, response.status_code, model, body_text[:200])
                    break
                if response.status_code >= 500:
                    self._cool_down(model, 5)
                    logger.warning("LLM failover reason=provider provider=%s status=%s model=%s",
                                   profile.name, response.status_code, model)
                    break
                if 400 <= response.status_code < 500:
                    self._cool_down(model, 60)
                    break
                response.raise_for_status()
                try:
                    body = response.json()
                    choice = body["choices"][0]
                    content = choice["message"]["content"].strip()
                    finish_reason = choice.get("finish_reason")
                except (KeyError, IndexError, TypeError, ValueError, AttributeError):
                    last_output_error = "AI xizmati kutilmagan formatda javob qaytardi"
                    self._cool_down(model, 5)
                    break
                if not content:
                    last_output_error = "AI xizmati bo‘sh javob qaytardi"
                    self._cool_down(model, 5)
                    break
                if finish_reason == "length":
                    last_output_error = ("AI javobi uzunlik chegarasida uzildi. "
                                         "Savolni qisqartirib qayta urinib ko‘ring")
                    self._cool_down(model, 5)
                    break
                usage = body.get("usage") if isinstance(body, dict) else None
                self._last_model_used = model
                logger.info(
                    "AI provider request_type=%s provider=%s model_call=%s provider_status=ok "
                    "model=%s input_tokens=%s output_tokens=%s request_id=%s elapsed_ms=%s",
                    diagnostic_type, profile.name, model_index, model,
                    usage.get("prompt_tokens") if isinstance(usage, dict) else None,
                    usage.get("completion_tokens") if isinstance(usage, dict) else None,
                    request_id, round((time.monotonic() - started) * 1000),
                )
                return content

        if last_output_error:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, last_output_error)
        if saw_rate_limit:
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                "Barcha mavjud AI modellarining vaqtinchalik limiti tugagan. Birozdan so‘ng qayta urinib ko‘ring",
            )
        if saw_timeout:
            raise HTTPException(
                status.HTTP_504_GATEWAY_TIMEOUT,
                "AI modellari belgilangan vaqtda javob bermadi. Qayta urinib ko‘ring",
            )
        if saw_model_error:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "Sozlangan AI modellaridan foydalanib bo‘lmadi. Model ruxsatlarini tekshiring",
            )
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "AI xizmatidan vaqtincha javob olinmadi. Qayta urinib ko‘ring",
        )

    async def generate_structured(self, system: str, user: str, schema: dict, *,
                                  max_tokens: int | None = None) -> dict:
        tried: set[str] = set()
        for _ in self.profile.models:
            content = await self.generate(
                system, user, temperature=0.0, response_schema=schema,
                request_type="structured", max_tokens=max_tokens, _excluded_models=tried,
            )
            if self._last_model_used:
                tried.add(self._last_model_used)
            try:
                value = json.loads(content)
            except (TypeError, ValueError):
                logger.warning("LLM structured failover reason=invalid_json provider=%s model=%s",
                               self.profile.name, self._last_model_used)
                continue
            if isinstance(value, dict):
                return value
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            "AI modellari tuzilmali javob formatiga rioya qilmadi",
        )


class LlmRouter:
    """Chooses the configured primary provider and, only if enabled, a fallback."""

    def __init__(self):
        self._providers: dict[tuple, OpenAICompatibleProvider] = {}

    def _provider(self, profile: ProviderProfile) -> OpenAICompatibleProvider:
        # Cached per profile identity so model cooldowns survive across requests
        # but a configuration change yields a fresh provider.
        existing = self._providers.get(profile.cache_key)
        if existing is None:
            existing = OpenAICompatibleProvider(profile)
            self._providers[profile.cache_key] = existing
        return existing

    def profiles(self) -> list[ProviderProfile]:
        """Primary first, then the fallback when configuration allows one."""
        settings = get_settings()
        builders = {"local": _local_profile, "groq": _groq_profile}
        order = ["local", "groq"] if settings.llm_provider == "local" else ["groq", "local"]
        primary_name = order[0]
        chain = [builders[primary_name](settings)]
        if settings.llm_fallback_enabled:
            fallback = builders[order[1]](settings)
            if fallback.configured:
                chain.append(fallback)
        return chain

    def providers(self) -> list[OpenAICompatibleProvider]:
        return [self._provider(profile) for profile in self.profiles()]

    @property
    def primary(self) -> OpenAICompatibleProvider:
        return self.providers()[0]

    @property
    def configured(self) -> bool:
        return any(provider.configured for provider in self.providers())

    @property
    def provider_name(self) -> str:
        return self.profiles()[0].name

    @property
    def active_model(self) -> str | None:
        return next((provider.active_model for provider in self.providers()
                     if provider.configured and provider.active_model), None)

    def reset_runtime_state(self) -> None:
        for provider in self._providers.values():
            provider.reset_runtime_state()

    def _forced_unavailable(self, request_type: str) -> None:
        logger.warning(
            "AI provider request_type=%s model_call=0 provider_status=forced_unavailable",
            request_type,
        )
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "AI xizmati diagnostika rejimida vaqtincha o‘chirilgan",
        )

    async def _run(self, call, request_type: str):
        if get_settings().llm_force_unavailable_effective:
            self._forced_unavailable(request_type)
        usable = [provider for provider in self.providers() if provider.configured]
        if not usable:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "AI xizmati sozlanmagan. Administratorga murojaat qiling",
            )
        last_error: HTTPException | None = None
        for index, provider in enumerate(usable):
            try:
                return await call(provider)
            except HTTPException as exc:
                last_error = exc
                if index + 1 < len(usable):
                    logger.warning(
                        "LLM provider failover provider=%s status=%s next_provider=%s",
                        provider.profile.name, exc.status_code, usable[index + 1].profile.name,
                    )
                    continue
                raise
        raise last_error  # pragma: no cover - unreachable while `usable` is non-empty

    async def generate(self, system: str, user: str, **options) -> str:
        request_type = options.get("request_type") or (
            "structured" if options.get("response_schema") else "text")
        return await self._run(lambda provider: provider.generate(system, user, **options),
                               request_type)

    async def generate_structured(self, system: str, user: str, schema: dict, *,
                                  max_tokens: int | None = None) -> dict:
        return await self._run(
            lambda provider: provider.generate_structured(system, user, schema,
                                                          max_tokens=max_tokens),
            "structured")

    async def health(self) -> list[ProviderHealth]:
        results: list[ProviderHealth] = []
        for profile in self.profiles():
            provider = self._provider(profile)
            if not profile.configured:
                results.append(ProviderHealth(profile.name, profile.label, "not_configured",
                                              detail="Provayder sozlanmagan"))
                continue
            results.append(await provider.probe())
        return results


llm = LlmRouter()


def groq_profile_for_tests(settings=None) -> ProviderProfile:
    """Build the Groq profile directly; used by provider-level tests."""
    return _groq_profile(settings or get_settings())


def local_profile_for_tests(settings=None) -> ProviderProfile:
    """Build the local vLLM profile directly; used by provider-level tests."""
    return _local_profile(settings or get_settings())


__all__ = [
    "LlmRouter", "OpenAICompatibleProvider", "ProviderHealth", "ProviderProfile",
    "llm", "groq_profile_for_tests", "local_profile_for_tests", "replace",
]
