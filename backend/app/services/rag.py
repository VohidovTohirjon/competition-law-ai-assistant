import asyncio
import hashlib
import math
import re
import threading
from functools import lru_cache

from fastapi import HTTPException, status
from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session, joinedload

from ..config import get_settings
from ..models import Chunk, Document, NhhDocument, Role, User
from .legal_intent import (is_abuse_heading, is_agreement_heading,
                           is_dominance_heading, is_negotiation_heading,
                           is_trade_heading, legal_concepts)


# In a law, an article heading is the whole start of a block: "13-modda. Ustun mavqe".
# Anything else that mentions "modda" is body text, including the amendment clauses of
# the closing articles, which quote other codes ("1) 178-modda quyidagi tahrirda ...").
# Departmental acts number their sections "3-band" rather than "13-modda", so both are
# accepted; the anchor is what matters, since an amendment clause always starts with its
# own enumerator ("1) 178-modda ...") and therefore cannot match.
LEGAL_HEADING_RE = re.compile(
    r"^\s*(\d{1,3}\s*[-‐‑‒–—]?\s*(?:modda|band|модда|банд|статья)\b[^\n]{0,90})",
    re.IGNORECASE)
# An ordinary document has no articles; its own numbered section headings are kept so
# the UI can cite a meaningful location.
DOCUMENT_HEADING_RE = re.compile(
    r"(?:^|\n)\s*((?:\d+[.-]?\s*)?(?:modda|band|модда|банд)[^\n]{0,80})", re.IGNORECASE)
DOCUMENT_SECTION_RE = re.compile(r"\s*\d{1,3}[.)]\s+[^\n]{2,100}\s*")


def chunk_text(content: str, size: int = 1300, overlap: int = 180, *,
               legal: bool = False) -> list[dict]:
    """Split a document into indexable chunks, tracking the heading each chunk sits under.

    `legal=True` applies the strict statutory heading rule; without it a law's amendment
    clauses were indexed as articles of that law and later cited as such.
    """
    blocks = [b.strip() for b in re.split(r"\n{2,}", content) if b.strip()]
    chunks: list[dict] = []
    current = ""
    page = None
    article = None
    for block in blocks:
        page_match = re.search(r"\[Sahifa\s+(\d+)\]", block, re.I)
        if page_match:
            if current:
                chunks.append({"text": current, "page": page, "article": article})
                current = ""
            page = int(page_match.group(1))
        heading = (LEGAL_HEADING_RE.match(block) if legal
                   else DOCUMENT_HEADING_RE.search(block)
                   or DOCUMENT_SECTION_RE.fullmatch(block))
        if heading:
            if current:
                chunks.append({"text": current, "page": page, "article": article})
                current = ""
            article = (heading.group(1) if heading.lastindex else heading.group(0)).strip()
        candidate = f"{current}\n\n{block}".strip()
        if len(candidate) <= size:
            current = candidate
            continue
        if current:
            chunks.append({"text": current, "page": page, "article": article})
        current = (current[-overlap:] + "\n" + block).strip() if current else block
        while len(current) > size:
            cut = current.rfind(" ", 0, size)
            cut = cut if cut > size // 2 else size
            piece = current[:cut].strip()
            chunks.append({"text": piece, "page": page, "article": article})
            current = current[max(0, cut - overlap):].strip()
    if current:
        chunks.append({"text": current, "page": page, "article": article})
    return chunks


class Embeddings:
    def __init__(self):
        self._model = None
        self._load_lock = threading.Lock()
        self._encode_lock = threading.Lock()
        self._state = "idle"
        self._message = "Embedding modeli hali ishga tushirilmagan"

    def status(self) -> dict[str, str]:
        return {"state": self._state, "message": self._message}

    def warmup(self) -> None:
        settings = get_settings()
        if settings.embedding_backend == "hash":
            self._state = "ready"
            self._message = "Test embedding mexanizmi tayyor"
            return
        if self._model is not None:
            self._state = "ready"
            self._message = "Embedding modeli tayyor"
            return
        with self._load_lock:
            if self._model is not None:
                return
            self._state = "loading"
            self._message = "Embedding modeli fon rejimida tayyorlanmoqda"
            try:
                from sentence_transformers import SentenceTransformer
                model_kwargs = {}
                if settings.embedding_half_precision:
                    import torch
                    # Halves the resident model (2.2 GB -> 1.1 GB) on Apple/NVIDIA
                    # accelerators; cosine ranking is unchanged in practice.
                    if torch.backends.mps.is_available() or torch.cuda.is_available():
                        model_kwargs["torch_dtype"] = torch.float16
                model = SentenceTransformer(settings.embedding_model, model_kwargs=model_kwargs)
                dimension = model.get_embedding_dimension()
                if dimension != settings.embedding_dimensions:
                    raise ValueError(
                        f"Model o‘lchami {dimension}, bazadagi o‘lcham esa {settings.embedding_dimensions}"
                    )
                # One throwaway encode pays the backend (MPS/CUDA) kernel warm-up now
                # instead of on the first real question.
                model.encode(["tayyorlov"], normalize_embeddings=True, show_progress_bar=False)
                self._model = model
                self._state = "ready"
                self._message = "Embedding modeli tayyor"
            except Exception as exc:
                self._state = "error"
                self._message = "Embedding modelini tayyorlab bo‘lmadi"
                raise HTTPException(
                    status.HTTP_503_SERVICE_UNAVAILABLE,
                    "Embedding modeli ishlamayapti. Model fayllari va sozlamalarni tekshiring",
                ) from exc

    def encode(self, texts: list[str]) -> list[list[float]]:
        settings = get_settings()
        if settings.embedding_backend == "hash":
            self.warmup()
            return [self._hash_vector(value, settings.embedding_dimensions) for value in texts]
        try:
            self.warmup()
            with self._encode_lock:
                return self._model.encode(texts, normalize_embeddings=True, show_progress_bar=False).tolist()
        except HTTPException:
            raise
        except Exception as exc:
            self._state = "error"
            self._message = "Embedding hisoblashda xatolik yuz berdi"
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Embedding modeli ishlamayapti. Sozlamalarni tekshiring") from exc

    @staticmethod
    def _hash_vector(value: str, dims: int) -> list[float]:
        vector = [0.0] * dims
        words = re.findall(r"\w+", value.lower(), flags=re.UNICODE)
        for word in words:
            digest = hashlib.sha256(word.encode()).digest()
            index = int.from_bytes(digest[:4], "big") % dims
            vector[index] += -1.0 if digest[4] & 1 else 1.0
        norm = math.sqrt(sum(x * x for x in vector)) or 1.0
        return [x / norm for x in vector]


embeddings = Embeddings()


def _save_index(db: Session, source: Document | NhhDocument, corpus_type: str,
                pieces: list[dict], vectors: list[list[float]]) -> int:
    predicate = Chunk.document_id == source.id if corpus_type == "document" else Chunk.nhh_id == source.id
    db.execute(delete(Chunk).where(predicate))
    for order, (piece, vector) in enumerate(zip(pieces, vectors)):
        db.add(Chunk(
            corpus_type=corpus_type,
            document_id=source.id if corpus_type == "document" else None,
            nhh_id=source.id if corpus_type == "nhh" else None,
            chunk_order=order,
            text=piece["text"],
            article_clause=piece["article"],
            page=piece["page"],
            embedding=vector,
        ))
    if corpus_type == "nhh":
        source.indexed = True
    db.flush()
    return len(pieces)


def index_document(db: Session, source: Document | NhhDocument, corpus_type: str) -> int:
    pieces = chunk_text(source.parsed_text if corpus_type == "document" else source.original_text,
                        legal=corpus_type == "nhh")
    vectors = embeddings.encode([piece["text"] for piece in pieces])
    return _save_index(db, source, corpus_type, pieces, vectors)


async def index_document_async(db: Session, source: Document | NhhDocument, corpus_type: str) -> int:
    """Compute expensive embeddings off the event loop, then mutate this request's DB session safely."""
    pieces = chunk_text(source.parsed_text if corpus_type == "document" else source.original_text,
                        legal=corpus_type == "nhh")
    vectors = await asyncio.to_thread(embeddings.encode, [piece["text"] for piece in pieces])
    return _save_index(db, source, corpus_type, pieces, vectors)


def _cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


STOP_WORDS = {
    "va", "yoki", "uchun", "haqida", "qanday", "qaysi", "bor", "nima", "bu", "shu",
    "bilan", "bo‘yicha", "bo'yicha", "o‘zbekiston", "o'zbekiston", "qonunchiligida",
}

UZBEK_CYRILLIC_TO_LATIN = str.maketrans({
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "yo",
    "ж": "j", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "x", "ц": "ts", "ч": "ch", "ш": "sh", "ъ": "'", "ь": "",
    "э": "e", "ю": "yu", "я": "ya", "қ": "q", "ғ": "g'", "ҳ": "h", "ў": "o'",
})


@lru_cache(maxsize=2048)
def _normalize_uzbek(value: str) -> str:
    return (value.lower().translate(UZBEK_CYRILLIC_TO_LATIN)
            .replace("’", "'").replace("‘", "'").replace("ʻ", "'").replace("`", "'"))


def _stem_uzbek(token: str) -> str:
    for suffix in ("larining", "larning", "larini", "larga", "lardan", "lari", "ning", "dan", "ga", "da", "ni"):
        if token.endswith(suffix) and len(token) - len(suffix) >= 4:
            return token[:-len(suffix)]
    return token


@lru_cache(maxsize=2048)
def _token_set(value: str) -> frozenset[str]:
    normalized = _normalize_uzbek(value)
    return frozenset(
        _stem_uzbek(token) for token in re.findall(r"[a-z0-9']+", normalized)
        if len(token) > 2 and token not in STOP_WORDS
    )


def _tokens(value: str) -> set[str]:
    # The query is re-scored against every candidate chunk, so the tokenizer result
    # is memoized. A fresh set is handed back so no caller can mutate the cache.
    return set(_token_set(value))


# Structural legal vocabulary occurs in every normative document, so an overlap on
# these words alone says nothing about topical relevance. They still participate in
# ranking; they may just not be the sole reason a chunk is admitted as evidence.
STRUCTURAL_LEGAL_TOKENS = frozenset({
    "modda", "moddasi", "moddada", "moddalar", "moddalari", "modda", "modda",
    "band", "bandi", "bandida", "bandlar", "qism", "qismi", "statya",
    "nechta", "necha", "qancha", "hujjat", "hujjati", "hujjatlar",
})


def _topical_tokens(value: str) -> set[str]:
    """Query tokens that actually carry subject matter."""
    return _tokens(value) - STRUCTURAL_LEGAL_TOKENS


def _has_topical_overlap(query: str, chunk: Chunk) -> bool:
    """True when query and chunk share at least one subject-matter word.

    Without this, "Konstitutsiyada nechta modda bor?" matches an unrelated
    competition-law article purely because both contain the word "modda", and the
    unrelated law is then presented as authoritative evidence.
    """
    topical = _topical_tokens(query)
    if not topical:
        return False
    title = chunk.nhh.title if chunk.nhh else (chunk.document.filename if chunk.document else "")
    haystack = _token_set(f"{title} {chunk.article_clause or ''} {chunk.text}")
    return bool(topical & haystack)


def _requested_article_number(query: str) -> str | None:
    # Suffixed forms ("19-moddaning mazmuni", "13-moddasi") are explicit requests too.
    match = re.search(r"\b(\d{1,3})\s*[-‐‑‒–—.]?\s*(?:modda|модда|статья)\w*", query, re.IGNORECASE)
    return match.group(1) if match else None


# Documents an official may name that are NOT the competition-law corpus. A question
# pinned to "Konstitutsiyaning 1-moddasi" must never be answered with article 1 of
# whatever law happens to be indexed.
FOREIGN_DOCUMENT_RE = re.compile(
    r"konstitutsiya\w*|kodeks\w*|конституция\w*|кодекс\w*|"
    r"(?:mehnat|jinoyat|soliq|fuqarolik|ma'muriy|budjet|bojxona|oila|yer|uy-joy)\s+kodeks",
    re.IGNORECASE,
)


def names_foreign_document(query: str, titles: list[str]) -> bool:
    """True when the question names a legal act that is not in the indexed corpus."""
    normalized = _normalize_uzbek(query)
    if not FOREIGN_DOCUMENT_RE.search(normalized):
        return False
    corpus = " ".join(_normalize_uzbek(title) for title in titles)
    for match in FOREIGN_DOCUMENT_RE.finditer(normalized):
        if match.group(0)[:9] not in corpus:
            return True
    return False


def _matches_requested_article(query: str, chunk: Chunk) -> bool:
    """An explicitly requested article number stays admissible on its own.

    Matched against the chunk's own heading only: a chunk that merely cross-references
    another act's article must not be pinned by that number.
    """
    number = _requested_article_number(query)
    if not number:
        return False
    title = chunk.nhh.title if chunk.nhh else (chunk.document.filename if chunk.document else "")
    if names_foreign_document(query, [title]):
        return False
    return bool(re.match(rf"\s*{re.escape(number)}\s*[-‐‑‒–—.]?\s*(?:modda|модда|статья)\b",
                         chunk.article_clause or "", re.IGNORECASE))


def _lexical_score(query: str, chunk: Chunk) -> float:
    query_tokens = _tokens(query)
    title = chunk.nhh.title if chunk.nhh else chunk.document.filename
    text = f"{title} {chunk.article_clause or ''} {chunk.text}"
    text_tokens = _tokens(text)
    title_tokens = _tokens(title)
    if not query_tokens:
        return 0.0
    overlap = len(query_tokens & text_tokens) / min(len(query_tokens), 8)
    title_precision = len(query_tokens & title_tokens) / max(len(title_tokens), 1)
    query_phrase = " ".join(
        token for token in re.findall(r"[a-z0-9']+", _normalize_uzbek(query))
        if len(token) > 2 and token not in STOP_WORDS
    )
    normalized_text = _normalize_uzbek(text)
    phrase_bonus = 0.12 if query_phrase and query_phrase in normalized_text else 0.0
    return min(1.0, overlap + phrase_bonus + (0.25 * title_precision))


def _title_score(query: str, chunk: Chunk) -> float:
    query_tokens = _tokens(query)
    title = chunk.nhh.title if chunk.nhh else chunk.document.filename
    article_title = f"{title} {chunk.article_clause or ''}"
    title_tokens = _tokens(article_title)
    coverage = len(query_tokens & title_tokens) / max(len(query_tokens), 1)
    normalized_query = _normalize_uzbek(query)
    normalized_title = _normalize_uzbek(article_title)
    phrase_bonus = 0.55 if any(
        phrase in normalized_query and phrase in normalized_title
        for phrase in ("ustun mavqe", "raqobatga qarshi kelishuv", "savdolarda raqobat", "insofsiz raqobat")
    ) else 0.0
    return min(1.0, coverage + phrase_bonus)


def _legal_intent_score(query: str, chunk: Chunk) -> float:
    """Prefer the article that answers the requested legal operation, not a nearby mention."""
    q = _normalize_uzbek(query)
    concepts = legal_concepts(query)
    heading = _normalize_uzbek(chunk.article_clause or "")
    text_value = _normalize_uzbek(chunk.text)
    score = 0.0
    if concepts.dominant and not concepts.abuse and is_dominance_heading(chunk.article_clause or ""):
        score += 0.78
        if "ustun mavqe deb e'tirof" in text_value:
            score += 0.20
    if concepts.abuse and is_abuse_heading(chunk.article_clause or ""):
        score += 0.82
    if concepts.negotiation_power and is_negotiation_heading(chunk.article_clause or ""):
        score += 0.72
    if concepts.agreements and is_agreement_heading(chunk.article_clause or ""):
        score += 0.60
    if concepts.trade_restrictions and is_trade_heading(chunk.article_clause or ""):
        score += 0.60
    # "Qanday javobgarlik / jarima bor?" is answered by the sanction and liability
    # articles, whose vocabulary is far from the conduct the question describes.
    # Cyrillic "санкция" transliterates to "sanktsiya", so both spellings must match.
    if re.search(r"javobgarlik|jarima|sank[t]?siya|jazo|miqdor", q):
        if re.search(r"moliyaviy sank[t]?siya", heading):
            score += 0.85  # the article that lists the actual fine rates
        elif re.search(r"javobgarlik|sank[t]?siya|jarima", heading):
            score += 0.45
    return score


def _maintenance_penalty(query: str, chunk: Chunk) -> float:
    """Demote amendment/repeal boilerplate unless the user explicitly asks for it."""
    normalized_query = _normalize_uzbek(query)
    if any(term in normalized_query for term in ("o'zgartir", "qo'shimcha", "bekor", "kuchini yo'qot")):
        return 0.0
    title = _normalize_uzbek(chunk.article_clause or "")
    maintenance_terms = (
        "o'zgartirish va qo'shimchalar", "o'z kuchini yo'qotgan", "ayrim qonun hujjatlariga",
    )
    return 0.24 if any(term in title for term in maintenance_terms) else 0.0


LEGAL_TOPIC_TERMS = (
    (("kelishuv", "muvofiqlashtirilgan"), ("kelishuv", "muvofiqlashtir")),
    (("savdo", "tender", "xarid"), ("savdo", "tender", "xarid")),
    (("ustun mavqe",), ("ustun mavqe",)),
    (("insofsiz",), ("insofsiz",)),
    (("sanksiya", "jarima"), ("sanksiya", "jarima")),
)


def filter_legal_topic(chunks: list[Chunk], query: str) -> list[Chunk]:
    """Drop semantically-near but topically wrong articles when the query names a legal concept."""
    normalized_query = _normalize_uzbek(query)
    concepts = legal_concepts(query)
    # "How much is the fine for X?" is answered by the sanctions article, whose wording
    # is far from the conduct the question names, so it must be pulled to the front
    # before any concept-specific narrowing happens. The article that lists the actual
    # rates outranks the one-line liability article.
    if re.search(r"jarima|sank[t]?siya|jazo|javobgarlik", normalized_query):
        headings = {chunk.id: _normalize_uzbek(chunk.article_clause or "") for chunk in chunks}
        rates = [chunk for chunk in chunks if re.search(r"moliyaviy sank[t]?siya", headings[chunk.id])]
        liability = [chunk for chunk in chunks
                     if "javobgarlik" in headings[chunk.id] and chunk not in rates]
        if rates or liability:
            return list(dict.fromkeys(rates + liability + list(chunks)))
    # Combined questions intentionally retain each directly relevant article.
    if concepts.compares_dominance_and_negotiation:
        direct = [chunk for chunk in chunks if is_dominance_heading(chunk.article_clause or "")
                  or is_negotiation_heading(chunk.article_clause or "")]
        return direct or chunks
    if concepts.dominant and concepts.agreements:
        direct = [chunk for chunk in chunks if is_dominance_heading(chunk.article_clause or "")
                  or is_agreement_heading(chunk.article_clause or "")]
        return direct or chunks
    if concepts.abuse:
        direct = [chunk for chunk in chunks if is_abuse_heading(chunk.article_clause or "")]
        return direct or chunks
    if concepts.dominant:
        direct = [chunk for chunk in chunks if is_dominance_heading(chunk.article_clause or "")]
        return direct or chunks
    if concepts.agreements:
        direct = [chunk for chunk in chunks if is_agreement_heading(chunk.article_clause or "")]
        return direct or chunks
    if concepts.trade_restrictions:
        direct = [chunk for chunk in chunks if is_trade_heading(chunk.article_clause or "")]
        return direct or chunks
    evidence_terms: list[str] = []
    for query_terms, source_terms in LEGAL_TOPIC_TERMS:
        if any(term in normalized_query for term in query_terms):
            evidence_terms.extend(source_terms)
    if not evidence_terms:
        return chunks
    focused = [
        chunk for chunk in chunks
        if any(term in _normalize_uzbek(f"{chunk.article_clause or ''} {chunk.text}")
               for term in evidence_terms)
    ]
    focused = focused or chunks
    if "kelishuv" in normalized_query or "muvofiqlashtirilgan" in normalized_query:
        if "javobgarlik" in normalized_query:
            direct = [c for c in focused if "javobgarlik" in _normalize_uzbek(c.article_clause or "")]
        else:
            direct = [c for c in focused if "kelishuv" in _normalize_uzbek(c.article_clause or "")
                      and "javobgarlik" not in _normalize_uzbek(c.article_clause or "")]
        if direct:
            return direct
    if any(term in normalized_query for term in ("savdo", "tender", "xarid")):
        direct = [c for c in focused if "savdo" in _normalize_uzbek(c.article_clause or "")]
        if direct:
            return direct
    if "ustun mavqe" in normalized_query:
        if any(term in normalized_query for term in ("mezon", "aniq", "e'tirof", "ta'rif", "qachon")):
            direct = [c for c in focused if "ustun mavqe" in _normalize_uzbek(c.article_clause or "")
                      and "suiiste'mol" not in _normalize_uzbek(c.article_clause or "")
                      and "muzokara" not in _normalize_uzbek(c.article_clause or "")]
            return direct or focused
        if any(term in normalized_query for term in ("suiiste'mol", "taqiq", "cheklov", "harakat")):
            direct = [c for c in focused if "suiiste'mol" in _normalize_uzbek(c.article_clause or "")]
            return direct or focused
    return focused


def _search_with_vector(db: Session, query: str, vector: list[float], user: User,
                        corpus_type: str, document_id: str | None = None,
                        limit: int = 5) -> list[Chunk]:
    candidate_limit = max(limit, get_settings().retrieval_candidate_limit)
    dialect = db.bind.dialect.name if db.bind else ""
    if dialect == "postgresql":
        clauses = ["c.corpus_type = :corpus"]
        params: dict = {"corpus": corpus_type, "query": str(vector), "lim": candidate_limit}
        if corpus_type == "nhh":
            clauses.append("n.is_active = true AND n.indexed = true")
            join = "JOIN nhh_documents n ON n.id = c.nhh_id"
        else:
            join = "JOIN documents d ON d.id = c.document_id"
            if user.role != Role.administrator:
                clauses.append("(d.owner_id = :uid OR d.is_confidential = false)")
                params["uid"] = user.id
            if document_id:
                clauses.append("d.id = :docid")
                params["docid"] = document_id
        rows = db.execute(text(
            f"SELECT c.id, 1 - (c.embedding <=> CAST(:query AS vector)) AS semantic_score "
            f"FROM chunks c {join} WHERE {' AND '.join(clauses)} "
            "ORDER BY c.embedding <=> CAST(:query AS vector), "
            "COALESCE(c.nhh_id, c.document_id), COALESCE(c.article_clause, ''), c.chunk_order, c.id "
            "LIMIT :lim"
        ), params).all()
        if not rows:
            return []
        ids = [row[0] for row in rows]
        semantic_scores = {row[0]: float(row[1]) for row in rows}
        objects = db.scalars(select(Chunk).options(joinedload(Chunk.document), joinedload(Chunk.nhh)).where(Chunk.id.in_(ids))).all()
        by_id = {item.id: item for item in objects}
        candidates = [by_id[item] for item in ids]
    else:
        stmt = select(Chunk).options(joinedload(Chunk.document), joinedload(Chunk.nhh)).where(Chunk.corpus_type == corpus_type)
        if corpus_type == "nhh":
            stmt = stmt.join(NhhDocument).where(NhhDocument.is_active.is_(True), NhhDocument.indexed.is_(True))
        else:
            stmt = stmt.join(Document)
            if user.role != Role.administrator:
                stmt = stmt.where(
                    (Document.owner_id == user.id) | (Document.is_confidential.is_(False))
                )
            if document_id:
                stmt = stmt.where(Document.id == document_id)
        candidates = list(db.scalars(stmt))
        semantic_scores = {item.id: _cosine(item.embedding, vector) for item in candidates}
    lexical_scores = {item.id: _lexical_score(query, item) for item in candidates}
    title_scores = {item.id: _title_score(query, item) for item in candidates}
    maintenance_penalties = {item.id: _maintenance_penalty(query, item) for item in candidates}
    intent_scores = {item.id: _legal_intent_score(query, item) if corpus_type == "nhh" else 0.0
                     for item in candidates}
    # Lexical/title overlap may only admit a chunk when it is topical: a shared
    # structural word such as "modda" is not evidence of relevance.
    candidates = [
        item for item in candidates
        if semantic_scores[item.id] >= get_settings().retrieval_min_score
        or intent_scores[item.id] > 0
        or (lexical_scores[item.id] >= 0.22 and _has_topical_overlap(query, item))
        or _matches_requested_article(query, item)
    ]
    def stable_rank(item: Chunk):
        score = (
            (0.65 * semantic_scores[item.id])
            + (0.20 * lexical_scores[item.id])
            + (0.15 * title_scores[item.id])
            + intent_scores[item.id]
            - maintenance_penalties[item.id]
        )
        return (
            -round(score, 12), item.nhh_id or item.document_id or "",
            item.article_clause or "", item.chunk_order, item.id,
        )

    ranked = sorted(candidates, key=stable_rank)
    return ranked[:limit]


def search(db: Session, query: str, user: User, corpus_type: str,
           document_id: str | None = None, limit: int = 5) -> list[Chunk]:
    vector = embeddings.encode([query])[0]
    return _search_with_vector(db, query, vector, user, corpus_type, document_id, limit)


async def search_async(db: Session, query: str, user: User, corpus_type: str,
                       document_id: str | None = None, limit: int = 5) -> list[Chunk]:
    """Keep model loading/inference off the event loop so health/auth/reload stay responsive."""
    vector = (await asyncio.to_thread(embeddings.encode, [query]))[0]
    return _search_with_vector(db, query, vector, user, corpus_type, document_id, limit)


def legal_lexical_fallback(db: Session, query: str, limit: int = 10) -> list[Chunk]:
    """Deterministic recovery when vector similarity misses an explicit legal phrase."""
    candidates = list(db.scalars(
        select(Chunk)
        .options(joinedload(Chunk.nhh))
        .join(NhhDocument)
        .where(Chunk.corpus_type == "nhh", NhhDocument.is_active.is_(True),
               NhhDocument.indexed.is_(True))
    ))
    scored = []
    for chunk in candidates:
        lexical = _lexical_score(query, chunk)
        title = _title_score(query, chunk)
        intent = _legal_intent_score(query, chunk)
        score = lexical + title + intent - _maintenance_penalty(query, chunk)
        topical = _has_topical_overlap(query, chunk)
        if (intent > 0
                or _matches_requested_article(query, chunk)
                or ((lexical >= 0.18 or title >= 0.3) and topical)):
            scored.append((score, chunk))
    scored.sort(key=lambda item: (-round(item[0], 12), item[1].nhh_id or "",
                                  item[1].article_clause or "", item[1].chunk_order, item[1].id))
    return [chunk for _, chunk in scored[:limit]]


def sources_from_chunks(chunks: list[Chunk]) -> list[dict]:
    result = []
    for citation_number, chunk in enumerate(chunks, 1):
        if chunk.nhh:
            source = chunk.nhh
            url = source.source_url or f"/api/nhh/{source.id}/download"
            title = source.title
            doc_id = source.id
            evidence_type = "nhh"
            section = None
            document_type = source.category
            official_number = source.official_number
        else:
            source = chunk.document
            url = f"/api/documents/{source.id}/download"
            title = source.filename
            doc_id = source.id
            evidence_type = "document"
            section = chunk.article_clause
            document_type = None
            official_number = None
        article_match = re.search(r"\b(\d{1,3})\s*[-‐‑‒–—.]?\s*(?:modda|модда|статья)\b",
                                  chunk.article_clause or "", re.IGNORECASE)
        band_match = re.search(r"\b(\d{1,3})\s*[-‐‑‒–—.]?\s*(?:band|банд)\b",
                               chunk.article_clause or "", re.IGNORECASE)
        display_label = (f"{article_match.group(1)}-modda" if article_match else
                         f"{band_match.group(1)}-band" if band_match else None)
        result.append({
            "citation_number": citation_number,
            "document_id": doc_id,
            "document_name": title,
            "article_or_clause": chunk.article_clause,
            "display_label": display_label,
            "url": url,
            "excerpt": clean_excerpt(chunk.text, 700),
            # The compact preview stays readable; expansion exposes the complete
            # stored evidence chunk without altering official Cyrillic text.
            "full_excerpt": re.sub(r"\s+", " ", chunk.text).strip(),
            "page": chunk.page,
            "section": section,
            "evidence_type": evidence_type,
            "document_type": document_type,
            "official_number": official_number,
        })
    return result


def clean_excerpt(value: str, max_chars: int = 700) -> str:
    """Return a readable, bounded excerpt without cutting through a word."""
    normalized = re.sub(r"\s+", " ", value).strip()
    if len(normalized) <= max_chars:
        return normalized
    window = normalized[:max_chars + 1]
    sentence_end = max(window.rfind(". "), window.rfind("! "), window.rfind("? "))
    if sentence_end >= max_chars // 2:
        return window[:sentence_end + 1].rstrip() + " …"
    word_end = window.rfind(" ")
    return window[:word_end if word_end > 0 else max_chars].rstrip(" ,;:") + " …"
