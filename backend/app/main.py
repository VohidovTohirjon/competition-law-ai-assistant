import asyncio
from contextlib import asynccontextmanager

import logging

import jwt
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .api import router
from .config import get_settings, validate_production_settings
from .database import SessionLocal
from .models import AuditLog
from sqlalchemy import text as sa_text

from .services.rag import embeddings

settings = get_settings()
# Application loggers report provider/model/latency/grounding decisions at INFO;
# uvicorn only configures its own loggers, so the root level is set here.
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Refuse to serve a misconfigured production deployment rather than silently
    # falling back to an external provider.
    validate_production_settings(settings)
    logger.info(
        "startup app_env=%s llm_provider=%s llm_fallback_enabled=%s embedding_backend=%s",
        settings.app_env, settings.llm_provider, settings.llm_fallback_enabled,
        settings.embedding_backend,
    )
    warmup_task = None
    if settings.embedding_warmup_on_startup:
        warmup_task = asyncio.create_task(asyncio.to_thread(embeddings.warmup))
        warmup_task.add_done_callback(lambda task: task.exception() if not task.cancelled() else None)
    yield
    if warmup_task and not warmup_task.done():
        warmup_task.cancel()


app = FastAPI(title="Raqobat AI Assistant", version="1.1.0", docs_url="/api/docs",
              openapi_url="/api/openapi.json", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_list, allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])
app.include_router(router)


@app.get("/api/health")
def health():
    """Liveness plus cheap dependency probes.

    Deliberately does NOT probe the LLM: vLLM can take minutes to load a 20B model
    and Docker must not restart a healthy API because of that. LLM reachability is
    reported by the administrator-only /api/system/status endpoint instead.
    """
    database = "ready"
    try:
        with SessionLocal() as db:
            db.execute(sa_text("SELECT 1"))
    except Exception:
        database = "error"
    return {
        "status": "ishlayapti",
        "app_env": settings.app_env,
        "database": database,
        "embedding": embeddings.status()["state"],
        "llm_provider": settings.llm_provider,
    }


@app.middleware("http")
async def audit_middleware(request: Request, call_next):
    response = None
    try:
        response = await call_next(request)
        return response
    finally:
        # OPTIONS is a browser CORS preflight, not a user action; logging it buried the
        # real entries. Docs endpoints carry no operational meaning either.
        if (request.method != "OPTIONS" and request.url.path.startswith("/api/")
                and request.url.path not in {"/api/docs", "/api/openapi.json"}):
            user_id = None
            authorization = request.headers.get("authorization", "")
            if authorization.startswith("Bearer "):
                try:
                    payload = jwt.decode(authorization[7:], settings.secret_key, algorithms=["HS256"])
                    user_id = payload.get("sub")
                except Exception:
                    pass
            try:
                with SessionLocal() as db:
                    db.add(AuditLog(user_id=user_id, action=request.url.path.rsplit("/", 1)[-1] or "api",
                                    method=request.method, path=request.url.path,
                                    resource=request.path_params.get("document_id") or request.path_params.get("nhh_id") or request.path_params.get("task_id"),
                                    status_code=response.status_code if response else 500))
                    db.commit()
            except Exception:
                pass


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    logger.exception("Unhandled error method=%s path=%s error_type=%s",
                     request.method, request.url.path, type(exc).__name__)
    return JSONResponse(status_code=500, content={"detail": "Tizimda kutilmagan xatolik yuz berdi"})
