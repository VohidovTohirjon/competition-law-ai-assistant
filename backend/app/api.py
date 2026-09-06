import io
import logging
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlparse

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import Response
from fastapi.security import OAuth2PasswordRequestForm
from docx.image.image import Image as DocxImage
from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session, joinedload, selectinload

from .config import get_settings
from .database import get_db
from .models import (AiHistory, AuditLog, Chunk, Document, NhhDocument,
                     OrganizationProfile, Role, Task, TaskEvent, TaskPriority, TaskStatus, User)
from .schemas import (AiResponse, AnalysisRequest, AuditOut, ChatRequest, DocumentOut,
                      DraftRequest, HistoryOut, NhhOut, NhhUpdate, TaskCreate, TaskEventOut,
                      TaskOut, TaskUpdate, TokenOut, UserCreate, UserOut, UserUpdate,
                      SystemStatusOut, AiReadinessOut, LlmProviderStatusOut,
                      OrganizationProfileOut, OrganizationProfileUpdate)
from .security import (burn_password_check, create_token, current_user, hash_password,
                       login_throttle, require_roles, verify_password)
from .services.documents import MIMES, receive_upload
from .services.document_analysis import analyze_contradictions
from .services.document_qa import answer_document_question
from .services.drafting import create_response_letter
from .services.export import make_docx, make_internal_evidence_docx
from .services.llm import llm
from .services import answer_cache
from .services.ai_agent import (distinct_source_chunks, grounded_context,
                                generate_grounded_document, generate_grounded_legal,
                                has_legal_intent, prefer_article_starts, run_chat,
                                source_based_draft)
from .services.rag import (embeddings, index_document, index_document_async,
                           filter_legal_topic, legal_lexical_fallback, search_async,
                           sources_from_chunks)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api")
NHH_CATEGORIES = {
    "Qonun", "Prezident farmoni", "Prezident qarori", "Vazirlar Mahkamasi qarori",
    "Nizom", "Buyruq", "Idoraviy (ichki) hujjat",
}
OFFICIAL_SOURCE_HOSTS = {
    "lex.uz", "www.lex.uz", "gov.uz", "www.gov.uz", "regulation.gov.uz",
    "parliament.gov.uz", "www.parliament.gov.uz", "senat.uz", "www.senat.uz",
    "minjust.uz", "www.minjust.uz",
}


def verified_official_url(value: str | None) -> str:
    url = (value or "").strip()
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in OFFICIAL_SOURCE_HOSTS:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "NHH uchun tasdiqlangan rasmiy HTTPS manba havolasini kiriting (Lex.uz afzal)",
        )
    return url


def organization_profile(db: Session) -> OrganizationProfile:
    profile = db.scalar(select(OrganizationProfile).order_by(OrganizationProfile.updated_at).limit(1))
    if profile:
        return profile
    profile = OrganizationProfile()
    db.add(profile)
    db.commit()
    db.refresh(profile)
    return profile


def enrich_export_sources(db: Session, sources: list[dict] | None) -> list[dict]:
    """Backfill legal metadata for history rows created before source metadata existed."""
    enriched: list[dict] = []
    for source in sources or []:
        item = dict(source)
        if item.get("evidence_type") == "nhh" and item.get("document_id"):
            nhh = db.get(NhhDocument, item["document_id"])
            if nhh:
                item.setdefault("document_type", nhh.category)
                item.setdefault("official_number", nhh.official_number)
        enriched.append(item)
    return enriched


OFFICIAL_PROFILE_FIELDS = {
    "organization_name": "tashkilot nomi",
    "address": "pochta manzili",
    "phone": "telefon",
    "email": "e-pochta",
    "website": "veb-sayt",
    "outgoing_prefix": "chiqish raqami prefiksi",
    "signatory_name": "imzolovchi F.I.Sh.",
    "signatory_title": "imzolovchi lavozimi",
}


FALLBACK_OFFICIAL_BLOCKER = (
    "AI tomonidan tasdiqlangan matn (loyiha zaxira rejimda, tekshirilgan parchalardan tayyorlangan)"
)


def missing_official_fields(profile: OrganizationProfile, structured: dict | None = None,
                            result_status: str | None = None) -> list[str]:
    missing = [label for field, label in OFFICIAL_PROFILE_FIELDS.items()
               if not str(getattr(profile, field, "") or "").strip()]
    if result_status == "fallback" and (structured or {}).get("fallback_reason") not in (
            None, "confidential_external_blocked"):
        # The text came from the extractive fallback after a provider or validation
        # failure, not from a validated generation: exportable as a draft, never as a
        # finished outgoing letter. A letter produced locally by the confidentiality
        # policy is a deliberate, complete result and stays exportable.
        missing.append(FALLBACK_OFFICIAL_BLOCKER)
    values = structured or {}
    for field, label in (("document_date", "hujjat sanasi"),
                         ("outgoing_number", "chiqish raqami"),
                         ("recipient", "qabul qiluvchi")):
        if not str(values.get(field) or "").strip():
            missing.append(label)
    return missing


def safe_filename(value: str, fallback: str) -> str:
    cleaned = re.sub(r"[\x00-\x1f\x7f]", "", Path(value or fallback).name).replace('"', "").strip()
    return (cleaned or fallback)[:255]


def attachment_header(filename: str) -> str:
    suffix = Path(filename).suffix[:10]
    return f"attachment; filename=\"hujjat{suffix}\"; filename*=UTF-8''{quote(filename)}"


def document_or_404(db: Session, document_id: str, user: User) -> Document:
    document = db.get(Document, document_id)
    if not document:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Hujjat topilmadi")
    if (user.role != Role.administrator and document.owner_id != user.id
            and document.is_confidential):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Ushbu hujjatga kirish ruxsati yo‘q")
    return document


def all_document_chunks(db: Session, document_id: str, limit: int = 400) -> list[Chunk]:
    return list(db.scalars(
        select(Chunk)
        .options(joinedload(Chunk.document), joinedload(Chunk.nhh))
        .where(Chunk.document_id == document_id)
        .order_by(Chunk.chunk_order)
        .limit(limit)
    ))


def history_record(db: Session, user: User, operation: str, request: str, response: str,
                   sources: list[dict] | None = None, document_id: str | None = None,
                   result_status: str = "success", structured_data: dict | None = None) -> AiHistory:
    item = AiHistory(user_id=user.id, operation=operation, request_text=request,
                     response_text=response, sources=sources or [], document_id=document_id,
                     status=result_status)
    item.structured_data = structured_data
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def distinct_article_sources(sources: list[dict]) -> list[dict]:
    result = []
    seen = set()
    for source in sources:
        key = (source["document_id"], source.get("article_or_clause") or source["excerpt"][:120])
        if key not in seen:
            seen.add(key)
            result.append({**source, "citation_number": len(result) + 1})
    return result


@router.post("/auth/login", response_model=TokenOut)
def login(form: OAuth2PasswordRequestForm = Depends(), request: Request = None,
          db: Session = Depends(get_db)):
    client = request.client.host if request and request.client else "unknown"
    keys = [f"user:{form.username.strip().lower()}", f"ip:{client}"]
    locked = next((login_throttle.locked_for(key) for key in keys
                   if login_throttle.locked_for(key)), 0)
    if locked:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"Ketma-ket noto‘g‘ri urinishlar sababli kirish vaqtincha bloklandi. "
            f"{locked} soniyadan so‘ng qayta urinib ko‘ring",
        )
    user = db.scalar(select(User).where(User.username == form.username))
    if not user or not user.is_active:
        # Same work as a real check, so a missing account cannot be detected by timing.
        burn_password_check()
        for key in keys:
            login_throttle.record_failure(key)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Login yoki parol noto‘g‘ri")
    if not verify_password(form.password, user.password_hash):
        for key in keys:
            login_throttle.record_failure(key)
        logger.warning("Login failed username=%s client=%s", form.username, client)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Login yoki parol noto‘g‘ri")
    for key in keys:
        login_throttle.record_success(key)
    user.last_login_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(user)
    return TokenOut(access_token=create_token(user), user=UserOut.model_validate(user))


@router.get("/auth/me", response_model=UserOut)
def me(user: User = Depends(current_user)):
    return user


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(user: User = Depends(current_user), db: Session = Depends(get_db)):
    user.token_version += 1
    db.commit()
    return Response(status_code=204)


@router.get("/ai/readiness", response_model=AiReadinessOut)
def ai_readiness(_: User = Depends(current_user), db: Session = Depends(get_db)):
    embedding = embeddings.status()
    legal_sources = db.scalar(select(func.count(NhhDocument.id)).where(
        NhhDocument.is_active.is_(True), NhhDocument.indexed.is_(True)
    )) or 0
    legal_ready = legal_sources > 0 and embedding["state"] == "ready"
    # Employee-facing readiness stays provider-agnostic: no endpoint, model or
    # credential detail ever reaches this response.
    general_ready = llm.configured
    if legal_ready:
        return AiReadinessOut(legal_ready=True, general_ready=general_ready,
                              status="ready", message="Huquqiy yordam tayyor")
    if embedding["state"] in {"idle", "loading"}:
        return AiReadinessOut(legal_ready=False, general_ready=general_ready,
                              status="preparing", message="Huquqiy qidiruv tayyorlanmoqda")
    return AiReadinessOut(legal_ready=False, general_ready=general_ready,
                          status="unavailable", message="Huquqiy manbalar hozircha tayyor emas")


@router.get("/system/status", response_model=SystemStatusOut)
async def system_status(user: User = Depends(require_roles(Role.administrator)),
                        db: Session = Depends(get_db)):
    embedding = embeddings.status()
    accessible_stmt = select(func.count(Document.id))
    if user.role != Role.administrator:
        accessible_stmt = accessible_stmt.where(
            or_(Document.owner_id == user.id, Document.is_confidential.is_(False))
        )
    # Provider diagnostics deliberately carry no base URL and no credential:
    # an administrator gets state and model name only.
    provider_health = await llm.health()
    providers = [
        LlmProviderStatusOut(
            name=item.name, label=item.label, state=item.state, model=item.model,
            detail=item.detail, reachable=item.reachable, model_loaded=item.model_loaded,
            role="primary" if index == 0 else "fallback",
        )
        for index, item in enumerate(provider_health)
    ]
    return SystemStatusOut(
        embedding_state=embedding["state"],
        embedding_message=embedding["message"],
        embedding_model=get_settings().embedding_model,
        llm_provider=llm.provider_name,
        llm_fallback_enabled=get_settings().llm_fallback_enabled,
        llm_configured=llm.configured,
        llm_active_model=llm.active_model,
        llm_providers=providers,
        groq_key_configured=bool(get_settings().groq_api_key),
        groq_model_configured=bool(get_settings().groq_model_list),
        groq_active_model=llm.active_model if llm.provider_name == "groq" else None,
        groq_models_configured=len(get_settings().groq_model_list),
        active_nhh_documents=db.scalar(
            select(func.count(NhhDocument.id)).where(
                NhhDocument.is_active.is_(True), NhhDocument.indexed.is_(True)
            )
        ) or 0,
        nhh_chunks=db.scalar(select(func.count(Chunk.id)).where(Chunk.corpus_type == "nhh")) or 0,
        accessible_documents=db.scalar(accessible_stmt) or 0,
        failed_nhh_documents=db.scalar(select(func.count(NhhDocument.id)).where(
            or_(NhhDocument.extraction_status == "failed", NhhDocument.indexing_status == "failed")
        )) or 0,
        pending_nhh_documents=db.scalar(select(func.count(NhhDocument.id)).where(
            NhhDocument.indexing_status.in_(["pending", "processing"])
        )) or 0,
    )


@router.get("/roles")
def roles(_: User = Depends(require_roles(Role.administrator))):
    return [
        {"name": "administrator", "label": "Administrator", "permissions": ["Barcha funksiyalar", "Foydalanuvchilar", "NHH bazasi", "Audit"]},
        {"name": "rahbar", "label": "Rahbar", "permissions": ["Dashboard", "AI chat", "Tahlil", "Hisobotlar", "Topshiriqlar"]},
        {"name": "xodim", "label": "Xodim", "permissions": ["AI chat", "Hujjatlar", "Loyihalar", "Topshiriqlar"]},
    ]


@router.get("/users", response_model=list[UserOut])
def list_users(q: str | None = Query(default=None, max_length=120), role: Role | None = None,
               active: bool | None = None,
               _: User = Depends(require_roles(Role.administrator)), db: Session = Depends(get_db)):
    stmt = select(User)
    if q:
        needle = f"%{q.strip()}%"
        stmt = stmt.where(or_(User.username.ilike(needle), User.full_name.ilike(needle)))
    if role:
        stmt = stmt.where(User.role == role)
    if active is not None:
        stmt = stmt.where(User.is_active.is_(active))
    return list(db.scalars(stmt.order_by(User.created_at.desc())))


@router.get("/users/assignees", response_model=list[UserOut])
def list_assignees(_: User = Depends(require_roles(Role.administrator, Role.rahbar)), db: Session = Depends(get_db)):
    return list(db.scalars(select(User).where(User.is_active.is_(True)).order_by(User.full_name)))


@router.post("/users", response_model=UserOut, status_code=201)
def create_user(payload: UserCreate, _: User = Depends(require_roles(Role.administrator)), db: Session = Depends(get_db)):
    if db.scalar(select(User).where(User.username == payload.username)):
        raise HTTPException(status.HTTP_409_CONFLICT, "Bu login band")
    user = User(username=payload.username, full_name=payload.full_name,
                password_hash=hash_password(payload.password), role=payload.role)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@router.patch("/users/{user_id}", response_model=UserOut)
def update_user(user_id: str, payload: UserUpdate, actor: User = Depends(require_roles(Role.administrator)), db: Session = Depends(get_db)):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Foydalanuvchi topilmadi")
    changes = payload.model_dump(exclude_unset=True)
    next_role = changes.get("role", user.role)
    next_active = changes.get("is_active", user.is_active)
    if user.id == actor.id and (not next_active or next_role != Role.administrator):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "O‘zingizning administrator huquqingizni bekor qila olmaysiz")
    if user.role == Role.administrator and user.is_active and (not next_active or next_role != Role.administrator):
        other_admins = db.scalar(select(func.count(User.id)).where(
            User.role == Role.administrator, User.is_active.is_(True), User.id != user.id
        )) or 0
        if other_admins == 0:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Tizimda kamida bitta faol administrator qolishi kerak")
    if "password" in changes:
        user.password_hash = hash_password(changes.pop("password"))
        user.token_version += 1
    if "role" in changes or "is_active" in changes:
        user.token_version += 1
    for key, value in changes.items():
        setattr(user, key, value)
    db.commit()
    db.refresh(user)
    return user


@router.delete("/users/{user_id}", status_code=204)
def delete_user(user_id: str, actor: User = Depends(require_roles(Role.administrator)),
                db: Session = Depends(get_db)):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Foydalanuvchi topilmadi")
    if user.id == actor.id:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "O‘zingizni o‘chira olmaysiz")
    if user.role == Role.administrator and user.is_active:
        active_admins = db.scalar(select(func.count(User.id)).where(
            User.role == Role.administrator, User.is_active.is_(True), User.id != user.id
        )) or 0
        if active_admins == 0:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                                "Tizimda kamida bitta faol administrator qolishi kerak")
    dependencies = sum((
        db.scalar(select(func.count(Document.id)).where(Document.owner_id == user.id)) or 0,
        db.scalar(select(func.count(Task.id)).where(or_(Task.assigned_to == user.id,
                                                        Task.created_by == user.id))) or 0,
        db.scalar(select(func.count(AiHistory.id)).where(AiHistory.user_id == user.id)) or 0,
    ))
    if dependencies:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "Foydalanuvchiga ish yozuvlari bog‘langan; xavfsiz saqlash uchun uni faolsizlantiring")
    db.delete(user)
    db.commit()
    return Response(status_code=204)


@router.get("/documents", response_model=list[DocumentOut])
def list_documents(user: User = Depends(current_user), db: Session = Depends(get_db)):
    stmt = select(Document).order_by(Document.created_at.desc())
    if user.role != Role.administrator:
        stmt = stmt.where(or_(Document.owner_id == user.id, Document.is_confidential.is_(False)))
    return list(db.scalars(stmt))


@router.post("/documents", response_model=DocumentOut, status_code=201)
async def upload_document(file: UploadFile = File(...), category: str = Form("oddiy"),
                          confidential: bool = Form(False), user: User = Depends(current_user), db: Session = Depends(get_db)):
    if category not in {"oddiy", "murojaat", "muhim"}:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Hujjat toifasi noto‘g‘ri")
    kind, stored, data, parsed = await receive_upload(file, "documents")
    document = Document(owner_id=user.id, filename=safe_filename(file.filename or "", f"hujjat.{kind}"),
                        stored_name=stored, media_type=MIMES[kind], size_bytes=len(data),
                        parsed_text=parsed, category=category, is_confidential=confidential)
    db.add(document)
    try:
        db.flush()
        await index_document_async(db, document, "document")
        db.commit()
    except Exception:
        db.rollback()
        path = get_settings().data_dir / "documents" / stored
        if path.exists():
            path.unlink()
        raise
    db.refresh(document)
    return document


@router.get("/documents/{document_id}", response_model=DocumentOut)
def get_document(document_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return document_or_404(db, document_id, user)


@router.get("/documents/{document_id}/download")
def download_document(document_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    document = document_or_404(db, document_id, user)
    path = get_settings().data_dir / "documents" / document.stored_name
    if not path.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Hujjat fayli topilmadi")
    return Response(path.read_bytes(), media_type=document.media_type,
                    headers={"Content-Disposition": attachment_header(document.filename)})


@router.post("/documents/{document_id}/analyze", response_model=AiResponse)
async def analyze_document(document_id: str, payload: AnalysisRequest, user: User = Depends(current_user), db: Session = Depends(get_db)):
    document = document_or_404(db, document_id, user)
    structured = None
    # Validate before the dict lookup: a missing question used to raise KeyError -> 500,
    # and a whitespace-only question reached the model as an empty request.
    question = (payload.question or "").strip()
    if payload.operation == "qa" and not question:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Savol kiriting")
    query = question or {
        "summary": "hujjatning qisqacha mazmuni",
        "key_points": "hujjatning eng muhim bandlari",
        "contradictions": "hujjat ichidagi qarama-qarshi yoki o‘zaro mos kelmaydigan fikrlar",
    }[payload.operation]
    deterministic = None
    if payload.operation == "qa":
        deterministic = answer_document_question(query, all_document_chunks(db, document.id))
    if deterministic:
        answer, sources, structured = deterministic.answer, deterministic.sources, deterministic.structured
        result_kind, warning = ("ok", None) if sources else ("no_sources", None)
        chunks = []
    elif payload.operation in {"summary", "key_points", "contradictions"}:
        chunks = all_document_chunks(db, document.id)
    else:
        chunks = await search_async(db, query, user, "document", document.id, 7)
    if deterministic:
        pass
    elif not chunks:
        answer = "Bu ma’lumot tanlangan hujjatda topilmadi."
        sources: list[dict] = []
        result_kind = "no_sources"
        warning = None
    else:
        # Contradiction detection compares statements across the WHOLE document; it
        # verifies every quote against its own chunk, so more evidence cannot make it
        # wrong, while less evidence made it answer "no contradictions found" falsely.
        if payload.operation == "contradictions":
            answer, sources, structured = analyze_contradictions(
                distinct_source_chunks(chunks, limit=60))
            result_kind = "ok"
            warning = None
        else:
            # Summary/key points/QA need enough of the document to be faithful; the
            # context budget in `grounded_context` still bounds what is actually sent.
            evidence_chunks = distinct_source_chunks(chunks, limit=24)
            sources = sources_from_chunks(evidence_chunks)
            instructions = {
                "summary": "Hujjatning ixcham, faktlarga sodiq qisqacha mazmunini yozing.",
                "key_points": "Hujjatning asosiy bandlarini tartibli ro‘yxat qiling.",
                "qa": "Savolga faqat berilgan hujjat parchalari asosida javob bering; ma’lumot bo‘lmasa buni aniq ayting.",
            }[payload.operation]
            generation = await generate_grounded_document(
                "Siz hujjat tahlilchisisiz. O‘zbek lotin alifbosida yozing. Fakt uydirmang. "
                "Har bir fakt, raqam, sana yoki xulosadan keyin aynan mos [1] citation yozing. " + instructions,
                f"SO‘ROV:\n{query}\n\nHUJJAT PARCHALARI:\n{grounded_context(evidence_chunks)}",
                sources,
                allow_external=(get_settings().allow_external_confidential_ai
                                or not document.is_confidential),
            )
            answer = generation.answer
            sources = generation.sources
            result_kind = generation.result_kind
            warning = generation.warning
    item = history_record(
        db, user, f"document_{payload.operation}", query, answer, sources, document.id,
        result_status="fallback" if result_kind == "source_matches" else "success",
        structured_data=structured,
    )
    return AiResponse(history_id=item.id, answer=answer, sources=sources,
                      result_kind=result_kind, warning=warning,
                      evidence_context="document", structured=structured)


@router.post("/chat", response_model=AiResponse)
async def chat(payload: ChatRequest, request: Request, user: User = Depends(current_user),
               db: Session = Depends(get_db)):
    mode = payload.resolved_mode
    try:
        outcome = await run_chat(db, user, payload.question, mode)
    except HTTPException:
        if not await request.is_disconnected():
            history_record(db, user,
                           "general_chat" if mode == "general" else "legal_chat",
                           payload.question, "", result_status="failure")
        raise
    if await request.is_disconnected():
        raise HTTPException(499, "So‘rov foydalanuvchi tomonidan bekor qilindi")
    item = history_record(db, user, outcome.operation, payload.question,
                          outcome.answer, outcome.sources,
                          result_status="fallback" if outcome.result_kind == "source_matches" else "success")
    return AiResponse(history_id=item.id, answer=outcome.answer, sources=outcome.sources,
                      result_kind=outcome.result_kind, warning=outcome.warning,
                      effective_mode=outcome.effective_mode,
                      routed_to_legal=outcome.routed_to_legal,
                      evidence_context=("analytics" if outcome.operation == "analytics_chat"
                                        else "legal" if outcome.effective_mode == "legal"
                                        else "general"))


@router.post("/drafts", response_model=AiResponse)
async def create_draft(payload: DraftRequest, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if payload.kind == "response_letter" and not payload.document_id:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Javob xati uchun murojaat hujjatini tanlang")
    document = document_or_404(db, payload.document_id, user) if payload.document_id else None
    document_chunks = all_document_chunks(db, document.id) if document else []
    base = grounded_context(document_chunks) if document_chunks else payload.instruction
    # The instruction alone ("Ushbu murojaat bo‘yicha hisobot tayyorla") carries no legal
    # keyword, but the attached appeal does. Judging by the instruction only sent the
    # report/ma'lumotnoma/brief/analytical flows down the ungrounded path, where the
    # model invented article numbers.
    legal_required = (payload.kind == "response_letter"
                      or has_legal_intent(payload.instruction)
                      or (bool(document_chunks) and has_legal_intent(base[:4000])))
    chunks = await search_async(db, f"{payload.instruction}\n{base[:2000]}", user, "nhh", None, 10) if legal_required else []
    if legal_required and not chunks:
        chunks = legal_lexical_fallback(db, f"{payload.instruction}\n{base[:2000]}", 10)
    if chunks:
        chunks = filter_legal_topic(chunks, f"{payload.instruction}\n{base[:2000]}")
    chunks = prefer_article_starts(db, distinct_source_chunks(chunks, limit=3))
    if legal_required and not chunks:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Huquqiy mazmundagi loyiha uchun mavjud NHH bazasida yetarli rasmiy asos topilmadi",
        )
    labels = {
        "response_letter": "Javob xati loyihasi", "report": "Hisobot", "info_note": "Ma’lumotnoma",
        "brief": "Qisqa ma’lumot", "analytical_conclusion": "Tahliliy xulosa",
    }
    # Confidential material policy is the same for every draft kind: internal NHH text
    # or a confidential appeal must not reach an external provider.
    contains_internal_nhh = any(
        chunk.nhh and chunk.nhh.category == "Idoraviy (ichki) hujjat" for chunk in chunks
    )
    external_allowed = get_settings().allow_external_confidential_ai or not (
        (document and document.is_confidential) or contains_internal_nhh
    )
    if payload.kind == "response_letter":
        outcome = await create_response_letter(
            payload.instruction, document_chunks, chunks, allow_external=external_allowed,
        )
        outcome.structured.update({
            "fallback_reason": outcome.failure_reason,
            "recipient": payload.recipient.strip(),
            "document_date": payload.document_date.isoformat() if payload.document_date else "",
            "outgoing_number": payload.outgoing_number.strip(),
        })
        profile = organization_profile(db)
        missing = missing_official_fields(
            profile, outcome.structured,
            "fallback" if outcome.result_kind == "source_matches" else "success")
        item = history_record(
            db, user, payload.kind, payload.instruction, outcome.answer, outcome.sources,
            document.id if document else None,
            result_status="fallback" if outcome.result_kind == "source_matches" else "success",
            structured_data=outcome.structured,
        )
        return AiResponse(
            history_id=item.id, answer=outcome.answer, sources=outcome.sources,
            export_url=f"/api/history/{item.id}/export", result_kind=outcome.result_kind,
            official_export_url=(None if missing else f"/api/history/{item.id}/export?official=true"),
            internal_evidence_url=f"/api/history/{item.id}/evidence-export",
            export_missing_fields=missing,
            warning=outcome.warning, effective_mode="legal", evidence_context="draft",
            structured=outcome.structured,
        )

    system = ("Siz rasmiy hujjat loyihasini tayyorlaysiz. O‘zbek lotin alifbosida, aniq va professional yozing. "
              "Faqat berilgan ma’lumotlardan foydalaning, yetishmagan faktlarni uydirmang. Sana, chiqish "
              "raqami, qabul qiluvchi tashkilot yoki mansabdor shaxs dalilda bo‘lmasa ularni o‘ylab topmang; "
              "zarur joyda [sana], [chiqish raqami], [qabul qiluvchi] kabi ochiq placeholder ishlating. "
              "Natijani 650 so‘zdan oshirmang, rasmiy manba parchalarini to‘liq ko‘chirmang, faqat zarur "
              "xulosani citation bilan ixcham bayon qiling. Murojaatda bayon etilgan da’voni "
              "isbotlangan fakt deb e’lon qilmang: qoidabuzarlik sodir etilgani, qonun buzilgani "
              "yoki javobgarlik haqida qat’iy xulosa chiqarmang, «tasdiqlangan taqdirda ... "
              "baholanishi mumkin» kabi shartli tilni ishlating. ")
    if legal_required:
        system += "Huquqiy asos sifatida faqat berilgan NHH manbalaridan foydalaning va [1] shaklida belgilang."
    prompt = f"TURI: {labels[payload.kind]}\nTOPSHIRIQ: {payload.instruction}\n\nASOSIY MA’LUMOT:\n{base}"
    if chunks:
        prompt += f"\n\nNHH MANBALARI:\n{grounded_context(chunks)}"
    sources = sources_from_chunks(chunks)
    warning = None
    result_kind = "ok"
    if legal_required:
        generation = await generate_grounded_legal(
            system, prompt, sources, additional_evidence=base, budget_kind="drafting",
            allow_external=external_allowed,
        )
        answer = generation.answer
        sources = generation.sources
        warning = generation.warning
        result_kind = generation.result_kind
        if result_kind == "source_matches" and warning:
            answer = source_based_draft(labels[payload.kind], payload.instruction, base, sources)
    else:
        if not external_allowed:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "Maxfiy hujjat asosidagi generativ loyiha uchun tasdiqlangan lokal AI xizmati sozlanmagan",
            )
        answer = await llm.generate(system, prompt,
                                    max_tokens=get_settings().max_tokens_for("drafting"))
    item = history_record(db, user, payload.kind, payload.instruction, answer, sources,
                          document.id if document else None,
                          result_status="fallback" if result_kind == "source_matches" else "success")
    return AiResponse(history_id=item.id, answer=answer, sources=sources,
                      export_url=f"/api/history/{item.id}/export", result_kind=result_kind,
                      warning=warning, effective_mode="legal" if legal_required else "general",
                      evidence_context="draft")


@router.get("/history", response_model=list[HistoryOut])
def list_history(user: User = Depends(current_user), db: Session = Depends(get_db)):
    stmt = select(AiHistory).order_by(AiHistory.created_at.desc())
    if user.role != Role.administrator:
        stmt = stmt.where(AiHistory.user_id == user.id)
    return list(db.scalars(stmt.limit(200)))


@router.get("/history/{history_id}/export")
def export_history(history_id: str, official: bool = Query(False),
                   user: User = Depends(current_user), db: Session = Depends(get_db)):
    item = db.get(AiHistory, history_id)
    if not item:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Natija topilmadi")
    if user.role != Role.administrator and item.user_id != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Ushbu natijaga kirish ruxsati yo‘q")
    try:
        titles = {"response_letter": "Javob xati loyihasi", "report": "Hisobot",
                  "info_note": "Ma’lumotnoma", "brief": "Qisqa ma’lumot",
                  "analytical_conclusion": "Tahliliy xulosa"}
        profile = organization_profile(db)
        if official and item.operation == "response_letter":
            missing = missing_official_fields(profile, item.structured_data, item.status)
            if missing:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_CONTENT,
                    "Rasmiy eksport uchun quyidagi maydonlarni to‘ldiring: " + ", ".join(missing),
                )
        sources = enrich_export_sources(db, item.sources)
        content = make_docx(
            titles.get(item.operation, "Raqobat AI Assistant natijasi"),
            item.response_text, sources, item.structured_data, profile,
            draft=not official,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "DOCX faylini yaratib bo‘lmadi") from exc
    return Response(content, media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    headers={"Content-Disposition": f'attachment; filename="raqobat-natija-{item.id[:8]}.docx"'})


@router.get("/history/{history_id}/evidence-export")
def export_history_evidence(history_id: str, user: User = Depends(current_user),
                            db: Session = Depends(get_db)):
    item = db.get(AiHistory, history_id)
    if not item:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Natija topilmadi")
    if user.role != Role.administrator and item.user_id != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Ushbu natijaga kirish ruxsati yo‘q")
    content = make_internal_evidence_docx(
        "Javob xati loyihasi bo‘yicha manbalar", enrich_export_sources(db, item.sources),
    )
    return Response(
        content,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="ichki-dalillar-{item.id[:8]}.docx"'},
    )


@router.get("/organization-profile", response_model=OrganizationProfileOut)
def get_organization_profile(_: User = Depends(require_roles(Role.administrator)),
                             db: Session = Depends(get_db)):
    return organization_profile(db)


@router.put("/organization-profile", response_model=OrganizationProfileOut)
def update_organization_profile(payload: OrganizationProfileUpdate,
                                user: User = Depends(require_roles(Role.administrator)),
                                db: Session = Depends(get_db)):
    profile = organization_profile(db)
    # Only the submitted fields change; a partial payload must not blank the rest.
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(profile, key, value.strip())
    profile.updated_by = user.id
    db.commit()
    db.refresh(profile)
    return profile


@router.post("/organization-profile/logo", response_model=OrganizationProfileOut)
async def upload_organization_logo(
    file: UploadFile = File(...),
    user: User = Depends(require_roles(Role.administrator)),
    db: Session = Depends(get_db),
):
    media_type = (file.content_type or "").lower()
    suffixes = {"image/png": ".png", "image/jpeg": ".jpg"}
    if media_type not in suffixes:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "Logo PNG yoki JPEG formatida bo‘lishi kerak")
    data = await file.read(2_000_001)
    if not data or len(data) > 2_000_000:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Logo hajmi 2 MB dan oshmasligi kerak")
    try:
        DocxImage.from_file(io.BytesIO(data))
    except Exception as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Logo rasmi buzilgan yoki o‘qib bo‘lmaydi") from exc

    profile = organization_profile(db)
    old_name = profile.logo_stored_name
    target = get_settings().data_dir / "organization"
    target.mkdir(parents=True, exist_ok=True)
    stored_name = f"logo-{uuid.uuid4().hex}{suffixes[media_type]}"
    (target / stored_name).write_bytes(data)
    profile.logo_stored_name = stored_name
    profile.updated_by = user.id
    db.commit()
    db.refresh(profile)
    if old_name:
        old_path = target / Path(old_name).name
        if old_path.exists() and old_path != target / stored_name:
            old_path.unlink()
    return profile


@router.delete("/organization-profile/logo", response_model=OrganizationProfileOut)
def delete_organization_logo(
    user: User = Depends(require_roles(Role.administrator)),
    db: Session = Depends(get_db),
):
    profile = organization_profile(db)
    old_name = profile.logo_stored_name
    profile.logo_stored_name = None
    profile.updated_by = user.id
    db.commit()
    db.refresh(profile)
    if old_name:
        path = get_settings().data_dir / "organization" / Path(old_name).name
        if path.exists():
            path.unlink()
    return profile


@router.get("/nhh", response_model=list[NhhOut])
def list_nhh(user: User = Depends(current_user), db: Session = Depends(get_db)):
    stmt = select(NhhDocument).options(selectinload(NhhDocument.chunks)).order_by(NhhDocument.updated_at.desc())
    if user.role != Role.administrator:
        stmt = stmt.where(NhhDocument.is_active.is_(True), NhhDocument.indexed.is_(True))
    return list(db.scalars(stmt))


@router.post("/nhh", response_model=NhhOut, status_code=201)
async def upload_nhh(title: str = Form(...), category: str = Form(...), source_url: str = Form(""),
                     official_number: str | None = Form(None), adoption_date: str | None = Form(None),
                     file: UploadFile = File(...), user: User = Depends(require_roles(Role.administrator)), db: Session = Depends(get_db)):
    if category not in NHH_CATEGORIES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "NHH turi ruxsat etilgan ro‘yxatda yo‘q")
    source_url = ((source_url or "").strip() or None) if category == "Idoraviy (ichki) hujjat" else verified_official_url(source_url)
    kind, stored, _, parsed = await receive_upload(file, "nhh")
    parsed_date = None
    if adoption_date:
        try:
            parsed_date = datetime.strptime(adoption_date, "%Y-%m-%d").date()
        except ValueError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Qabul qilingan sana noto‘g‘ri") from exc
    item = NhhDocument(title=title.strip(), category=category.strip(), source_url=source_url,
                       official_number=(official_number or "").strip() or None,
                       adoption_date=parsed_date,
                       original_filename=safe_filename(file.filename or "", f"nhh.{kind}"), stored_name=stored,
                       original_text=parsed, created_by=user.id, extraction_status="completed",
                       indexing_status="processing")
    db.add(item)
    try:
        db.flush()
        await index_document_async(db, item, "nhh")
        item.indexing_status = "completed"
        item.indexed_at = datetime.now(timezone.utc)
        item.processing_error = None
        db.commit()
    except Exception as exc:
        db.rollback()
        path = get_settings().data_dir / "nhh" / stored
        if path.exists():
            path.unlink()
        raise
    db.refresh(item)
    return item


@router.patch("/nhh/{nhh_id}", response_model=NhhOut)
def update_nhh(nhh_id: str, payload: NhhUpdate, _: User = Depends(require_roles(Role.administrator)), db: Session = Depends(get_db)):
    item = db.get(NhhDocument, nhh_id)
    if not item:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "NHH topilmadi")
    changes = payload.model_dump(exclude_unset=True)
    effective_category = changes.get("category", item.category)
    if effective_category not in NHH_CATEGORIES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "NHH turi ruxsat etilgan ro‘yxatda yo‘q")
    effective_url = changes.get("source_url", item.source_url)
    if effective_category == "Idoraviy (ichki) hujjat":
        if "source_url" in changes:
            changes["source_url"] = (effective_url or "").strip() or None
    else:
        changes["source_url"] = verified_official_url(effective_url)
    for key, value in changes.items():
        setattr(item, key, value)
    db.commit()
    db.refresh(item)
    return item


@router.post("/nhh/{nhh_id}/reindex", response_model=NhhOut)
def reindex_nhh(nhh_id: str, _: User = Depends(require_roles(Role.administrator)), db: Session = Depends(get_db)):
    item = db.get(NhhDocument, nhh_id)
    if not item:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "NHH topilmadi")
    item.indexing_status = "processing"
    item.processing_error = None
    db.commit()
    answer_cache.clear()
    try:
        index_document(db, item, "nhh")
        item.indexing_status = "completed"
        item.indexed_at = datetime.now(timezone.utc)
        db.commit()
    except Exception as exc:
        db.rollback()
        item = db.get(NhhDocument, nhh_id)
        item.indexing_status = "failed"
        item.processing_error = str(exc)[:1000]
        db.commit()
        raise
    db.refresh(item)
    return item


@router.delete("/nhh/{nhh_id}", status_code=204)
def delete_nhh(nhh_id: str, _: User = Depends(require_roles(Role.administrator)), db: Session = Depends(get_db)):
    item = db.get(NhhDocument, nhh_id)
    if not item:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "NHH topilmadi")
    path = get_settings().data_dir / "nhh" / item.stored_name
    db.delete(item)
    db.commit()
    if path.exists():
        path.unlink()
    return Response(status_code=204)


@router.get("/nhh/{nhh_id}/download")
def download_nhh(nhh_id: str, _: User = Depends(current_user), db: Session = Depends(get_db)):
    item = db.get(NhhDocument, nhh_id)
    if not item or not item.is_active:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "NHH topilmadi")
    path = get_settings().data_dir / "nhh" / item.stored_name
    if not path.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "NHH fayli topilmadi")
    return Response(path.read_bytes(), media_type="application/octet-stream",
                    headers={"Content-Disposition": attachment_header(item.original_filename)})


@router.get("/tasks", response_model=list[TaskOut])
def list_tasks(user: User = Depends(current_user), db: Session = Depends(get_db)):
    stmt = select(Task).options(joinedload(Task.assignee), joinedload(Task.creator)).order_by(Task.deadline.asc())
    if user.role == Role.xodim:
        stmt = stmt.where(Task.assigned_to == user.id)
    return list(db.scalars(stmt))


@router.post("/tasks", response_model=TaskOut, status_code=201)
def create_task(payload: TaskCreate, user: User = Depends(require_roles(Role.administrator, Role.rahbar)), db: Session = Depends(get_db)):
    assignee = db.get(User, payload.assigned_to)
    if not assignee or not assignee.is_active:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Faol ijrochi topilmadi")
    if payload.related_document_id:
        document_or_404(db, payload.related_document_id, user)
    task = Task(**payload.model_dump(), created_by=user.id)
    db.add(task)
    db.flush()
    db.add(TaskEvent(task_id=task.id, actor_id=user.id, event_type="created",
                     message="Topshiriq yaratildi", changes={"status": task.status.value}))
    db.commit()
    return db.scalar(select(Task).options(joinedload(Task.assignee), joinedload(Task.creator)).where(Task.id == task.id))


@router.patch("/tasks/{task_id}", response_model=TaskOut)
def update_task(task_id: str, payload: TaskUpdate, user: User = Depends(current_user), db: Session = Depends(get_db)):
    task = db.get(Task, task_id)
    if not task:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Topshiriq topilmadi")
    if user.role == Role.xodim and task.assigned_to != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Bu topshiriqni o‘zgartirishga ruxsat yo‘q")
    changes = payload.model_dump(exclude_unset=True)
    comment = (changes.pop("comment", None) or "").strip()
    if user.role == Role.xodim:
        forbidden = set(changes) - {"status"}
        if forbidden:
            raise HTTPException(status.HTTP_403_FORBIDDEN,
                                "Xodim faqat o‘z topshirig‘i holatini va izohini yangilashi mumkin")
    if "assigned_to" in changes:
        assignee = db.get(User, changes["assigned_to"])
        if not assignee or not assignee.is_active:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Faol ijrochi topilmadi")
    if "related_document_id" in changes and changes["related_document_id"]:
        document_or_404(db, changes["related_document_id"], user)
    if "deadline" in changes and (changes["deadline"].tzinfo is None or changes["deadline"].utcoffset() is None):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Muddat vaqt mintaqasi bilan yuborilishi kerak")
    before: dict[str, str | None] = {}
    for key, value in changes.items():
        old = getattr(task, key)
        before[key] = old.value if hasattr(old, "value") else (old.isoformat() if hasattr(old, "isoformat") else old)
        setattr(task, key, value)
    if changes.get("status") == TaskStatus.bajarildi and not task.completed_at:
        task.completed_at = datetime.now(timezone.utc)
    elif "status" in changes and changes["status"] != TaskStatus.bajarildi:
        task.completed_at = None
    if changes:
        rendered = {key: (value.value if hasattr(value, "value") else
                          value.isoformat() if hasattr(value, "isoformat") else value)
                    for key, value in changes.items()}
        db.add(TaskEvent(task_id=task.id, actor_id=user.id, event_type="updated",
                         message=comment or "Topshiriq yangilandi",
                         changes={"before": before, "after": rendered}))
    elif comment:
        db.add(TaskEvent(task_id=task.id, actor_id=user.id, event_type="commented",
                         message=comment, changes={}))
    else:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Yangilanish yoki izoh kiriting")
    db.commit()
    return db.scalar(select(Task).options(joinedload(Task.assignee), joinedload(Task.creator)).where(Task.id == task.id))


@router.get("/tasks/{task_id}/events", response_model=list[TaskEventOut])
def task_events(task_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    task = db.get(Task, task_id)
    if not task:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Topshiriq topilmadi")
    if user.role == Role.xodim and task.assigned_to != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Bu topshiriq tarixiga kirish ruxsati yo‘q")
    return list(db.scalars(select(TaskEvent).where(TaskEvent.task_id == task_id)
                           .order_by(TaskEvent.created_at.asc())))


@router.get("/dashboard")
def dashboard(user: User = Depends(require_roles(Role.administrator, Role.rahbar)), db: Session = Depends(get_db)):
    now = datetime.now(timezone.utc)
    tasks = list(db.scalars(select(Task).order_by(Task.deadline.asc(), Task.created_at.asc())))
    overdue = [task for task in tasks if task.is_overdue]
    important_stmt = select(Document).where(Document.category.in_(["murojaat", "muhim"]))
    if user.role != Role.administrator:
        important_stmt = important_stmt.where(
            or_(Document.owner_id == user.id, Document.is_confidential.is_(False))
        )
    important = list(db.scalars(important_stmt.order_by(Document.created_at.desc()).limit(10)))
    open_tasks = [task for task in tasks if task.status not in {TaskStatus.bajarildi, TaskStatus.bekor_qilindi}]
    task_item = lambda task: {
        "type": "task", "id": task.id, "label": task.title,
        "deadline": task.deadline, "priority": task.priority.value,
    }
    document_item = lambda doc: {
        "type": "document", "id": doc.id, "label": doc.filename, "category": doc.category,
    }
    workers = list(db.scalars(
        select(User)
        .where(User.role == Role.xodim, User.is_active.is_(True))
        .order_by(User.full_name.asc())
    ))
    workload = []
    for worker in workers:
        assigned = [task for task in tasks if task.assigned_to == worker.id]
        completed = sum(task.status == TaskStatus.bajarildi for task in assigned)
        active = sum(task.status not in {TaskStatus.bajarildi, TaskStatus.bekor_qilindi} for task in assigned)
        worker_overdue = sum(task.is_overdue for task in assigned)
        workload.append({
            "user_id": worker.id,
            "full_name": worker.full_name,
            "jami": len(assigned),
            "faol": active,
            "bajarilgan": completed,
            "kechikkan": worker_overdue,
            "progress_percent": round(completed / len(assigned) * 100) if assigned else 0,
        })
    # Overdue first, then by priority, then by deadline: a leader must not have to
    # scroll past routine items to see what is already late.
    priority_rank = {TaskPriority.shoshilinch: 0, TaskPriority.yuqori: 1,
                     TaskPriority.odatiy: 2, TaskPriority.past: 3}
    ranked = sorted(open_tasks, key=lambda task: (not task.is_overdue,
                                                  priority_rank.get(task.priority, 9),
                                                  task.deadline))
    return {
        "mavjud_muammolar": [task_item(task) for task in ranked[:10]],
        "mavjud_muammolar_jami": len(open_tasks),
        "kechikayotgan_topshiriqlar": [task_item(task) for task in overdue],
        "muhim_murojaatlar": [document_item(doc) for doc in important],
        "statistika": {
            "jami_topshiriqlar": len(tasks),
            "bajarilgan_topshiriqlar": sum(task.status == TaskStatus.bajarildi for task in tasks),
            "kechikkan_topshiriqlar": len(overdue),
            "jami_hujjatlar": db.scalar(
                select(func.count(Document.id)) if user.role == Role.administrator else
                select(func.count(Document.id)).where(
                    or_(Document.owner_id == user.id, Document.is_confidential.is_(False))
                )
            ) or 0,
            "faol_nhh": db.scalar(select(func.count(NhhDocument.id)).where(NhhDocument.is_active.is_(True))) or 0,
        },
        "xavfli_holatlar": [{**task_item(task), "label": f"{task.title} — muddati o‘tgan"}
                             for task in overdue],
        "xodimlar_ish_yuklamasi": workload,
        "generated_at": now,
    }


@router.get("/audit", response_model=list[AuditOut])
def audit_logs(_: User = Depends(require_roles(Role.administrator)), db: Session = Depends(get_db)):
    return list(db.scalars(select(AuditLog).order_by(AuditLog.created_at.desc()).limit(500)))
