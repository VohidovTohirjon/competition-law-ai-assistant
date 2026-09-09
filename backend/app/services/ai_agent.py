import re
import logging
import time
from dataclasses import dataclass

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import Chunk, NhhDocument, User
from . import answer_cache
from .analytics import analytics_answer, is_analytics_question
from .llm import llm
from .legal_facts import (deterministic_legal_fact_answer, enumeration_block,
                          missing_enumeration_items, source_enumeration)
from .legal_intent import legal_concepts
from .grounding import (article_label, extractive_document_fallback,
                        extractive_legal_fallback, latin_legal_answer, used_sources,
                        repair_document_citations, validate_cited_answer,
                        validate_legal_answer)
from .rag import (_has_topical_overlap, _requested_article_number, _token_set, _topical_tokens,
                  filter_legal_topic,
                  legal_lexical_fallback, names_foreign_document, search_async,
                  sources_from_chunks, clean_excerpt)


LEGAL_INTENT_PATTERNS = (
    r"\bqonun(?:chilik\w*|iy|da|ning|lar)?\b",
    r"\b(?:normativ|huquqiy|huquq|kodeks)\b",
    r"\b\d+[.-]?\s*(?:modda|band)\b",
    r"\b(?:modda(?:lar|si|ning|da)?|farmon|nizom|sanksiya|javobgarlik)\b",
    r"\b(?:prezident|vazirlar mahkamasi)\s+qarori\b",
    r"\b(?:ustun\s+mavqe\w*|raqobatga\s+qarshi|raqobatni\s+chekl\w*)\b",
    r"\blex\.uz\b",
    r"\b(?:норматив\w*|ҳуқуқий\w*|ҳуқуқ\w*|қонун\w*|кодекс\w*|модда\w*|банд\w*|фармон\w*|низом\w*)\b",
)
LEGAL_INTENT_RE = re.compile("|".join(LEGAL_INTENT_PATTERNS), re.IGNORECASE)
APOSTROPHES_TABLE = str.maketrans({"’": "'", "‘": "'", "ʻ": "'", "`": "'", "ʼ": "'"})
logger = logging.getLogger(__name__)

LEGAL_ANSWER_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["answer_blocks"],
    "properties": {
        "answer_blocks": {
            "type": "array", "minItems": 1, "maxItems": 8,
            "items": {
                "type": "object", "additionalProperties": False,
                "required": ["text", "source_ids"],
                "properties": {
                    "text": {"type": "string"},
                    "source_ids": {
                        "type": "array", "minItems": 1,
                        "items": {"type": "string", "pattern": "^L[1-9][0-9]*$"},
                    },
                },
            },
        },
    },
}


@dataclass
class ChatOutcome:
    answer: str
    sources: list[dict]
    result_kind: str
    warning: str | None
    operation: str
    effective_mode: str
    routed_to_legal: bool


@dataclass
class GroundedGeneration:
    answer: str
    sources: list[dict]
    result_kind: str
    warning: str | None
    failure_reason: str | None = None


def _failure_reason(exc: HTTPException) -> str:
    detail = str(exc.detail).lower()
    if exc.status_code == 429:
        return "rate_limited"
    if exc.status_code == 504:
        return "timeout"
    if "uzunlik" in detail or "uzildi" in detail:
        return "truncated"
    return "provider_unavailable"


def _uncovered_compound_sources(question: str, sources: list[dict],
                                used_citation_ids: tuple[int, ...]) -> tuple[int, ...]:
    """Citation numbers a compound question left unanswered.

    A question naming several legal concepts (dominance *and* anti-competitive
    agreements) is retrieved against several articles on purpose. If the model
    answers only one of them, the reply is incomplete rather than wrong, so it is
    sent back for one correction and otherwise degrades to the extractive answer,
    which cites every verified source. Single-concept questions are untouched.
    """
    if len(sources) < 2 or not legal_concepts(question).is_compound:
        return ()
    used = set(used_citation_ids)
    return tuple(source["citation_number"] for source in sources
                 if source["citation_number"] not in used)


def _friendly_failure(reason: str, subject: str = "") -> str:
    evidence = "hujjat parchalari" if subject.startswith("Hujjat") else "huquqiy asoslar"
    if reason in {"provider_unavailable", "rate_limited", "timeout", "truncated"}:
        return f"AI xizmati vaqtincha mavjud emas. Tekshirilgan {evidence} ko‘rsatildi."
    return ("AI izohi manbalar bilan to‘liq tasdiqlanmagani sababli faqat tekshirilgan "
            f"{evidence} ko‘rsatildi.")


GENERAL_SYSTEM_PROMPT = (
    "Siz O‘zbekiston Respublikasi Raqobatni rivojlantirish va iste’molchilar huquqlarini "
    "himoya qilish qo‘mitasining ichki AI yordamchisisiz. Sohangiz: raqobat huquqi, "
    "antimonopol nazorat, murojaatlar bilan ishlash, rasmiy hujjatlar tayyorlash, tahlil va "
    "boshqaruv masalalari. Xodim yoki rahbarga tajribali mutaxassis kabi to‘liq, aniq va "
    "amaliy javob bering: avval savolga to‘g‘ridan-to‘g‘ri javob, keyin zarur tushuntirish, "
    "kerak bo‘lsa qadamlar yoki bandlar ro‘yxati. Markdown (sarlavhachalar, ro‘yxatlar, qalin "
    "matn) dan o‘qishni osonlashtirish uchun foydalaning. Faqat o‘zbek lotin alifbosida yozing. "
    "Fakt, raqam, sana, modda raqami yoki hujjat nomini uydirmang; aniq normativ asos kerak "
    "bo‘lsa, buni «tekshirilgan manba uchun Huquqiy qidiruv rejimidan foydalaning» deb ayting, "
    "lekin savolga baribir mazmunli javob bering. Javob 150–350 so‘z atrofida bo‘lsin."
)

# A legal question asked in "general" mode is the one case where the model has no
# evidence and the strongest incentive to improvise. Left to the ordinary general
# prompt it invented a law title, an article number and a fine range. Here it may
# explain the concept, and nothing else.
GENERAL_LEGAL_SYSTEM_PROMPT = (
    "Siz O‘zbekiston Respublikasi Raqobatni rivojlantirish va iste’molchilar huquqlarini "
    "himoya qilish qo‘mitasining ichki AI yordamchisisiz. Foydalanuvchi «Umumiy savol» "
    "rejimini tanlagan, shuning uchun sizda normativ-huquqiy hujjatlar bazasi YO‘Q va "
    "hech qanday tekshirilgan manba berilmagan.\n"
    "QAT’IY TAQIQ: qonun yoki hujjat nomini yozmang; modda, band yoki hujjat raqamini "
    "yozmang; foiz, jarima miqdori, pul summasi, muddat yoki boshqa aniq huquqiy "
    "ko‘rsatkichni yozmang. Bu ma’lumotlarni eslab qolgan bilimingizdan keltirmang — "
    "ular xato bo‘lishi mumkin va tekshirib bo‘lmaydi.\n"
    "Buning o‘rniga: tushunchani umumiy tarzda, oddiy til bilan tushuntiring (nima "
    "baholanadi, qanday omillar hisobga olinadi, jarayon qanday ketadi), 120–200 so‘z "
    "hajmida, o‘zbek lotin alifbosida. Javob oxirida aniq norma va raqamlar uchun "
    "«Huquqiy qidiruv» rejimidan foydalanish kerakligini bir gapda ayting."
)

# What must never appear in a general-mode answer to a legal question.
LEGAL_SPECIFICS_RE = re.compile(
    r"\b\d{1,3}\s*[-‐‑‒–—.]?\s*(?:modda|band|модда|банд)\w*"          # article/clause reference
    r"|\b\d+(?:[.,]\d+)?\s*(?:%|foiz)"                                  # numeric legal threshold
    r"|\bO['‘’ʻ`]?RQ\s*[-–]?\s*\d+|\bПҚ\s*[-–]?\s*\d+|№\s*\d+"     # act numbers
    r"|\b(?:so['‘’ʻ`]m|baravar\w*|bazaviy hisoblash)"                     # money amounts
    r"|to['‘’ʻ`]?g['‘’ʻ`]?risidagi\s+qonun|\bqonuni(?:ning|da|ga)\b"      # law titles
    r"|\bkodeks\w*|\bnizom(?:i|ida|ning)\b|\bfarmon\w*|\bqarori(?:da|ning)?\b",
    re.IGNORECASE,
)

GENERAL_LEGAL_REDIRECT = (
    "Bu savol aniq huquqiy asos talab qiladi.\n\n"
    "**Umumiy savol** rejimida tizim normativ-huquqiy hujjatlar bazasidan foydalanmaydi, "
    "shuning uchun modda raqami, foiz ko‘rsatkichi yoki jarima miqdori kabi aniq qiymatlar "
    "bu rejimda berilmaydi — tekshirilmagan raqamni ko‘rsatgandan ko‘ra, ko‘rsatmaslik "
    "to‘g‘riroq.\n\n"
    "Yuqoridagi **«Huquqiy qidiruv»** rejimini tanlab, shu savolni qayta yuboring: javob "
    "tegishli modda, rasmiy havola va qonundan olingan matn parchasi bilan beriladi."
)

GENERAL_LEGAL_WARNING = (
    "Umumiy rejim javobi NHH bazasiga asoslanmagan. Aniq modda va raqamlar uchun "
    "«Huquqiy qidiruv» rejimini tanlang."
)

LEGAL_SYSTEM_PROMPT = (
    "Siz Raqobat qo‘mitasining huquqiy yordamchisisiz. Faqat TEKSHIRILGAN MANBALAR bo‘limidagi "
    "parchalarga tayanib, o‘zbek lotin alifbosida to‘liq, tushunarli va professional javob "
    "yozing. Tuzilma: (1) savolga to‘g‘ridan-to‘g‘ri javob; (2) huquqiy asos — qaysi modda nima "
    "deydi, zarur bo‘lsa ro‘yxat shaklida; (3) amaliy izoh yoki chegara (agar manbada bo‘lsa). "
    "Manbada ro‘yxat (mezonlar, shartlar, taqiqlar, jarimalar) bo‘lsa, uning BARCHA bandlarini "
    "qamrab oling, birortasini tushirib qoldirmang. Har bir huquqiy da’vodan keyin manba "
    "identifikatorini bering. Hujjat nomi, raqami, sanasi, modda, band, iqtibos, URL yoki "
    "citation uydirmang. Manbada so‘z bilan yozilgan sonlarni o‘zgartirmang. Manbada mavjud "
    "oqibatlarni (taqiqlanishi, haqiqiy emas deb topilishi, sanksiya) albatta bayon qiling, "
    "lekin manbada aniq yozilmagan jazo, jarima, javobgarlik turi yoki muddatni qo‘shmang: "
    "bunday ma’lumot bo‘lmasa, «taqdim etilgan manbada ko‘rsatilmagan» deb yozing. Agar manbada "
    "huquqlar va majburiyatlar alohida ro‘yxat bo‘lsa, ularni aralashtirmang: «Huquqlar:» va "
    "«Majburiyatlar:» deb ajratib bering. Manbadagi rasmiy atamalarni saqlang. Yetarli asos "
    "bo‘lmasa buni aniq ayting."
)

NO_SOURCES_ANSWER = (
    "Mavjud normativ-huquqiy hujjatlar bazasida ushbu savolga yetarli huquqiy asos "
    "topilmadi. Savolni aniqroq modda, mavzu yoki hujjat nomi bilan qayta yozing yoxud "
    "tegishli NHHni bazaga qo‘shing."
)
NO_BASIS_RE = re.compile(
    r"(?:yetarli\s+asos|asos(?:lar)?)\s+(?:yo['‘’ʻ]q|topilmadi|mavjud\s+emas)|"
    r"ma['‘’ʻ]lumot(?:lar)?\s+(?:yo['‘’ʻ]q|topilmadi|mavjud\s+emas|keltirilmagan)|"
    r"aniqlash\s+imkoni\s+yo['‘’ʻ]q|javob\s+berish\s+imkoni\s+yo['‘’ʻ]q|"
    r"manba(?:lar)?da\s+(?:ko['‘’ʻ]rsatilmagan|yo['‘’ʻ]q)",
    re.IGNORECASE,
)


def _is_no_basis_answer(answer: str) -> bool:
    """A short reply whose only content is "the sources say nothing about this"."""
    prose = re.sub(r"\s*\[[0-9, ]+\]", "", answer).strip()
    return len(prose) <= 200 and bool(NO_BASIS_RE.search(prose))


def has_legal_intent(question: str) -> bool:
    """Route explicit legal/normative requests away from ungrounded general generation."""
    normalized = (question.lower().replace("’", "'").replace("‘", "'")
                  .replace("ʻ", "'").replace("`", "'"))
    concepts = legal_concepts(question)
    return bool(LEGAL_INTENT_RE.search(normalized) or concepts.dominant or concepts.abuse
                or concepts.negotiation_power or concepts.agreements
                or concepts.trade_restrictions)


def _source_identity(chunk: Chunk) -> tuple[str, str]:
    document_id = chunk.nhh_id or chunk.document_id or ""
    article = re.sub(r"\s+", " ", (chunk.article_clause or "").lower()).strip()
    # Collapsing by heading is right for a law (one article = one source card) but wrong
    # for an uploaded document, where every chunk under a section heading would be
    # discarded except the first, hiding most of the document from the analysis.
    if article and chunk.nhh_id:
        return document_id, article
    words = re.findall(r"\w+", chunk.text.lower(), flags=re.UNICODE)
    return document_id, " ".join(words[:24])


def _word_set(value: str) -> set[str]:
    return set(re.findall(r"\w+", value.lower(), flags=re.UNICODE))


def distinct_source_chunks(chunks: list[Chunk], limit: int = 5) -> list[Chunk]:
    """Keep the best-ranked chunk for an article and collapse highly-overlapping excerpts."""
    result: list[Chunk] = []
    identities: set[tuple[str, str]] = set()
    for chunk in chunks:
        identity = _source_identity(chunk)
        if identity in identities:
            continue
        words = _word_set(chunk.text)
        duplicate = False
        for existing in result:
            if (existing.nhh_id or existing.document_id) != (chunk.nhh_id or chunk.document_id):
                continue
            other = _word_set(existing.text)
            union = words | other
            if union and len(words & other) / len(union) >= 0.72:
                duplicate = True
                break
        if duplicate:
            continue
        identities.add(identity)
        result.append(chunk)
        if len(result) >= limit:
            break
    return result


def prefer_article_starts(db: Session, chunks: list[Chunk]) -> list[Chunk]:
    """Display a coherent article beginning instead of a ranked continuation fragment."""
    result: list[Chunk] = []
    for chunk in chunks:
        if not chunk.nhh_id or not chunk.article_clause:
            result.append(chunk)
            continue
        first = db.scalar(
            select(Chunk)
            .where(Chunk.nhh_id == chunk.nhh_id, Chunk.article_clause == chunk.article_clause)
            .order_by(Chunk.chunk_order, Chunk.id)
            .limit(1)
        )
        result.append(first or chunk)
    return result


ARTICLE_COUNT_RE = re.compile(
    r"(?:nechta|necha|qancha)\s+modda|moddalar(?:i)?\s+soni|(?:нечта|неча|қанча)\s+модда|моддалар\s+сони",
    re.IGNORECASE,
)
ARTICLE_HEADING_RE = re.compile(r"^\s*(\d{1,3})\s*[-‐‑‒–—.]?\s*(?:modda|модда|статья)\b", re.IGNORECASE)


def article_count_answer(db: Session, question: str) -> tuple[str, list[dict]] | None:
    """"Nechta modda bor?" is answered by counting the indexed headings, not by the LLM.

    Answered only when the corpus makes the question unambiguous: a single active
    law, or a law the question names by title token overlap.
    """
    normalized = question.translate(APOSTROPHES_TABLE).lower()
    if not ARTICLE_COUNT_RE.search(normalized):
        return None
    documents = list(db.scalars(
        select(NhhDocument).where(NhhDocument.is_active.is_(True), NhhDocument.indexed.is_(True))
    ))
    generic_words = {"qonun", "qonuni", "to", "g", "risida", "risidagi", "o", "rq", "respublikasi",
                     "o'zbekiston", "uzbekiston", "ushbu", "bu", "mazkur"}
    question_tokens = _word_set(normalized)
    named = [doc for doc in documents if (_word_set(doc.title) - generic_words) & question_tokens]
    names_other_document = re.search(
        r"konstitutsiya|kodeks|farmon|qaror|nizom|buyruq|конституц|кодекс|фармон|қарор|низом",
        normalized)
    generic_reference = re.search(r"\b(?:ushbu|bu|mazkur|shu)\s+qonun|\bqonunda\b|ушбу қонун|қонунда",
                                  normalized)
    if named:
        documents = named
    elif names_other_document or not generic_reference:
        # "Konstitutsiyada nechta modda bor?" is about a document we do not hold.
        return None
    if len(documents) != 1:
        return None
    document = documents[0]
    headings = db.scalars(
        select(Chunk.article_clause).where(Chunk.nhh_id == document.id, Chunk.article_clause.isnot(None))
    )
    numbers = sorted({int(match.group(1)) for heading in headings
                      if (match := ARTICLE_HEADING_RE.search(heading or ""))})
    if not numbers:
        return None
    source = {
        "citation_number": 1, "document_id": document.id, "document_name": document.title,
        "article_or_clause": None, "display_label": None,
        "url": document.source_url or f"/api/nhh/{document.id}/download",
        "excerpt": f"Indekslangan modda sarlavhalari: {numbers[0]}-modda … {numbers[-1]}-modda "
                   f"(jami {len(numbers)} ta).",
        "full_excerpt": ", ".join(f"{number}-modda" for number in numbers),
        "page": None, "section": None, "evidence_type": "nhh",
        "document_type": document.category, "official_number": document.official_number,
    }
    answer = (f"«{document.title}» hujjatida jami **{len(numbers)} ta modda** mavjud "
              f"({numbers[0]}-moddadan {numbers[-1]}-moddagacha). [1]")
    return answer, [source]


LIST_QUESTION_RE = re.compile(
    r"mezon|shart|ro'yxat|jarima|sank[t]?siya|taqiq|harakat|holat|hollar|qaysilar|nimalar|turlari",
    re.IGNORECASE)


def _enumeration_is_on_topic(question: str, source: dict, lead: str) -> bool:
    """A list is worth completing when its lead-in or heading names the question's subject."""
    topical = _topical_tokens(question)
    haystack = _token_set(f"{source.get('article_or_clause') or ''} {lead}")
    if topical & haystack:
        return True
    normalized = question.translate(APOSTROPHES_TABLE).lower()
    return bool(LIST_QUESTION_RE.search(normalized)) and not re.search(
        r"asosiy tushuncha|tushunchalar qo", lead.translate(APOSTROPHES_TABLE).lower())


def article_chunks_by_number(db: Session, number: str, limit: int = 12) -> list[Chunk]:
    """All indexed NHH chunks whose heading is the requested article number."""
    pattern = re.compile(rf"^\s*{re.escape(number)}\s*[-‐‑‒–—.]?\s*(?:modda|модда|статья)\b",
                         re.IGNORECASE)
    candidates = db.scalars(
        select(Chunk)
        .join(NhhDocument, NhhDocument.id == Chunk.nhh_id)
        .where(Chunk.corpus_type == "nhh", NhhDocument.is_active.is_(True),
               NhhDocument.indexed.is_(True), Chunk.article_clause.isnot(None),
               Chunk.article_clause.like(f"{number}%"))
        .order_by(Chunk.nhh_id, Chunk.chunk_order)
    )
    return [chunk for chunk in candidates if pattern.search(chunk.article_clause or "")][:limit]


def grounded_context(chunks: list[Chunk]) -> str:
    settings = get_settings()
    parts: list[str] = []
    length = 0
    for index, chunk in enumerate(chunks, 1):
        name = chunk.nhh.title if chunk.nhh else chunk.document.filename
        block = (
            f"[MANBA {index}]\n"
            f"HUJJAT: {name}\n"
            f"MODDA/BAND: {chunk.article_clause or 'aniqlanmagan'}\n"
            f"RASMIY PARCHA:\n{chunk.text}"
        )
        if parts and length + len(block) > settings.context_max_chars:
            break
        parts.append(block)
        length += len(block)
    return "\n\n".join(parts)


def expand_article_sources(db: Session, chunks: list[Chunk], sources: list[dict]) -> list[dict]:
    """Attach the complete stored article to one source card/citation."""
    expanded: list[dict] = []
    for chunk, source in zip(chunks, sources):
        parts = [chunk]
        if chunk.nhh_id and chunk.article_clause:
            parts = list(db.scalars(
                select(Chunk)
                .where(Chunk.nhh_id == chunk.nhh_id,
                       Chunk.article_clause == chunk.article_clause)
                .order_by(Chunk.chunk_order, Chunk.id)
            )) or [chunk]
        full = ""
        for item in parts:
            piece = re.sub(r"\s+", " ", item.text).strip()
            if not piece or piece in full:
                continue
            overlap = 0
            for size in range(min(len(full), len(piece)), 39, -1):
                if full[-size:] == piece[:size]:
                    overlap = size
                    break
            full = f"{full} {piece[overlap:]}".strip()
        expanded.append({**source, "excerpt": clean_excerpt(full, 700),
                         "full_excerpt": re.sub(r"\s+", " ", full).strip()})
    return expanded


def grounded_source_context(sources: list[dict]) -> str:
    settings = get_settings()
    blocks: list[str] = []
    length = 0
    for index, source in enumerate(sources, 1):
        block = (
            f"[MANBA {index}]\nHUJJAT: {source['document_name']}\n"
            f"MODDA/BAND: {source.get('article_or_clause') or 'aniqlanmagan'}\n"
            f"RASMIY PARCHA:\n{source.get('full_excerpt') or source.get('excerpt') or ''}"
        )
        if blocks and length + len(block) > settings.context_max_chars:
            break
        blocks.append(block)
        length += len(block)
    return "\n\n".join(blocks)


def source_matches_answer(sources: list[dict], question: str = "") -> str:
    answer, _ = extractive_legal_fallback(sources, question=question)
    return answer


def deterministic_legal_answer(question: str, sources: list[dict]) -> tuple[str, list[dict]] | None:
    return deterministic_legal_fact_answer(question, sources)


def _render_legal_blocks(value: dict, sources: list[dict]) -> tuple[str, tuple[int, ...]] | None:
    blocks = value.get("answer_blocks")
    if not isinstance(blocks, list) or not blocks:
        return None
    allowed = {f"L{index}": source["citation_number"] for index, source in enumerate(sources, 1)}
    lines: list[str] = []
    used: list[int] = []
    for block in blocks:
        if not isinstance(block, dict) or not isinstance(block.get("text"), str):
            return None
        ids = block.get("source_ids")
        if not isinstance(ids, list) or not ids or any(value not in allowed for value in ids):
            return None
        citations = []
        for source_id in ids:
            number = allowed[source_id]
            if number not in used:
                used.append(number)
            citations.append(str(number))
        text = re.sub(r"[\u200b\u200c\u200d\ufeff]", "", block["text"])
        text = re.sub(r"\s*[\[(](?:L?\d+(?:\s*,\s*L?\d+)*)[\])]", "", text)
        text = re.sub(r"(?<![\w-])L\d{1,2}(?![\w-])", "", text).strip()
        if not text:
            return None
        marker = f"[{', '.join(citations)}]"
        # The validator checks citations line by line; a block that spans several
        # lines (a heading plus a list) gets the marker on every content line.
        rendered_lines = []
        for line in text.splitlines():
            stripped = line.rstrip()
            if not stripped.strip():
                rendered_lines.append("")
            elif re.fullmatch(r"\s*(?:\*\*[^*]+\*\*:?|#{1,4}\s.*)", stripped):
                rendered_lines.append(stripped)  # a bare heading carries no claim
            else:
                rendered_lines.append(f"{stripped} {marker}")
        lines.append("\n".join(rendered_lines))
    return "\n\n".join(lines), tuple(used)


async def generate_grounded_legal(system: str, prompt: str,
                                  sources: list[dict], *,
                                  question: str = "",
                                  additional_evidence: str = "",
                                  compact_prompt: str | None = None,
                                  budget_kind: str = "legal",
                                  allow_external: bool = True) -> GroundedGeneration:
    """Bounded generate -> validate -> one correction -> extractive fallback pipeline."""
    if not allow_external:
        answer, verified = extractive_legal_fallback(sources, question=question)
        return GroundedGeneration(
            answer, verified, "source_matches",
            "Maxfiy yoki ichki ma’lumot tashqi AI xizmatiga yuborilmadi; faqat lokal tekshirilgan parchalar ko‘rsatildi.",
            "confidential_external_blocked",
        )
    last_violations: tuple[str, ...] = ()
    budget = get_settings().max_tokens_for(budget_kind)
    try:
        for attempt in range(2):
            request = re.sub(r"\[MANBA\s+(\d+)\]", r"[L\1]", prompt)
            if attempt:
                request += (
                    "\n\nOLDINGI JAVOB VALIDATSIYADAN O‘TMADI. Quyidagi xatolarni tuzating va "
                    "faqat berilgan manbalarga tayangan yangi to‘liq javob qaytaring:\n- "
                    + "\n- ".join(last_violations)
                )
            structured = await llm.generate_structured(
                system + " Javobni answer_blocks tuzilmasida qaytaring. Har bir blokda faqat "
                "manbada mavjud da'voni yozing va L1, L2 ko‘rinishidagi source_ids bering. "
                "O‘zbek lotin yozuvida tabiiy va ixcham bayon qiling.",
                request, LEGAL_ANSWER_SCHEMA, max_tokens=budget,
            )
            rendered = _render_legal_blocks(structured, sources)
            if not rendered:
                last_violations = ("Javob bloklari yoki manba identifikatorlari yaroqsiz",)
                continue
            generated, _ = rendered
            validation = validate_legal_answer(generated, sources, additional_evidence)
            uncovered = _uncovered_compound_sources(question, sources, validation.used_citation_ids)
            if validation.valid and not uncovered:
                return GroundedGeneration(
                    latin_legal_answer(validation.answer),
                    used_sources(sources, validation.used_citation_ids),
                    "ok",
                    None,
                )
            last_violations = validation.violations or (
                "Savol bir nechta huquqiy tushunchani qamrab oladi; har bir tekshirilgan "
                "manbaga alohida javob bloki va citation bering: "
                + ", ".join(f"[{value}]" for value in uncovered),
            )
            logger.warning(
                "Grounding validation failed request_type=legal attempt=%s violations=%s answer=%r",
                attempt + 1, "; ".join(last_violations), generated[:400],
            )
            # A factual grounding failure (invented article, citation, number, date,
            # quote or legal identifier) is not worth a second generation: the
            # verified extractive answer is both faster and safer. Only a
            # presentation-level problem earns a correction round.
            repairable = validation.repairable if validation.violations else bool(uncovered)
            if not repairable:
                logger.info(
                    "Skipping correction call request_type=legal reason=hard_grounding_failure "
                    "codes=%s", ",".join(validation.codes),
                )
                break
    except HTTPException as exc:
        reason = _failure_reason(exc)
        answer, verified = extractive_legal_fallback(sources, question=question)
        return GroundedGeneration(answer, verified, "source_matches",
                                  _friendly_failure(reason, "Huquqiy javob"), reason)

    answer, verified = extractive_legal_fallback(sources, question=question)
    return GroundedGeneration(answer, verified, "source_matches",
                              _friendly_failure("validation_failed", "Huquqiy javob"),
                              "validation_failed")


async def generate_grounded_document(system: str, prompt: str,
                                     sources: list[dict], *,
                                     allow_external: bool = True) -> GroundedGeneration:
    """Require every document-analysis result to map to displayed evidence."""
    if not allow_external:
        answer, verified = extractive_document_fallback(
            sources,
            "Maxfiy hujjat matni tashqi AI xizmatiga yuborilmadi; lokal ajratilgan asl parchalar ko‘rsatildi.",
        )
        return GroundedGeneration(
            answer, verified, "source_matches",
            "Maxfiy hujjat uchun tashqi AI ishlatilmadi; faqat lokal tekshirilgan parchalar ko‘rsatildi.",
            "confidential_external_blocked",
        )
    last_violations: tuple[str, ...] = ()
    budget = get_settings().max_tokens_for("document")
    try:
        for attempt in range(2):
            request = prompt
            if attempt:
                request += (
                    "\n\nOLDINGI JAVOB DALIL TEKSHIRUVIDAN O‘TMADI. Xatolarni tuzating va "
                    "har bir fakt yoki xulosadan keyin mos [1] citation yozing:\n- "
                    + "\n- ".join(last_violations)
                )
            generated = await llm.generate(system, request, temperature=0.0,
                                           max_tokens=budget)
            # "fakt[1]" -> "fakt [1]": purely typographic, keeps the citation intact.
            generated = re.sub(r"(?<=[^\s\[(])(\[\d+(?:\s*,\s*\d+)*\])", r" \1", generated)
            generated = repair_document_citations(generated, sources)
            validation = validate_cited_answer(generated, sources)
            if validation.valid:
                return GroundedGeneration(
                    validation.answer,
                    used_sources(sources, validation.used_citation_ids),
                    "ok",
                    None,
                )
            last_violations = validation.violations
            # Same rule as the legal path: only a missing-citation style problem is
            # worth re-prompting. An unsupported number goes straight to evidence.
            if not validation.repairable:
                logger.info(
                    "Skipping correction call request_type=document "
                    "reason=hard_grounding_failure codes=%s", ",".join(validation.codes),
                )
                break
    except HTTPException as exc:
        answer, verified = extractive_document_fallback(
            sources, "AI tahlili yaratilmadi; asl hujjat parchalari ko‘rsatildi."
        )
        reason = _failure_reason(exc)
        return GroundedGeneration(answer, verified, "source_matches",
                                  _friendly_failure(reason, "Hujjat tahlili"), reason)

    answer, verified = extractive_document_fallback(
        sources,
        "AI tahlili dalillar bilan bog‘lanmagani uchun qabul qilinmadi; asl parchalar ko‘rsatildi.",
    )
    return GroundedGeneration(answer, verified, "source_matches",
                              _friendly_failure("validation_failed", "Hujjat tahlili"),
                              "validation_failed")


def source_based_draft(title: str, instruction: str, base: str, sources: list[dict]) -> str:
    """Produce a usable, explicitly limited draft when the generation provider is unavailable."""
    lines = [
        f"# {title}",
        "",
        "> Ushbu loyiha AI izohi manba tekshiruvidan o‘tmagani yoki AI xizmati vaqtincha "
        "mavjud bo‘lmagani sababli faqat topshiriq matni va tekshirilgan NHH parchalaridan "
        "avtomatik shakllantirildi. "
        "Yuborishdan oldin mas’ul xodim tahriri va huquqiy tekshiruvi talab etiladi.",
        "",
        "## Topshiriq",
        instruction,
        "",
        "## Asosiy hujjat mazmuni",
        re.sub(r"\s+", " ", base).strip()[:1800],
        "",
        "## Tekshirilgan huquqiy asoslar",
    ]
    for source in sources:
        citation = source["citation_number"]
        article = article_label(source)
        excerpt = re.sub(r"\s+", " ", source["excerpt"]).strip()
        lines.append(f"- [{citation}] **{article}** — {excerpt[:500]}")
    lines.extend([
        "",
        "## Javob loyihasi",
        "Murojaatingiz ko‘rib chiqildi. Unda bayon etilgan masalalar yuqorida ko‘rsatilgan "
        "normativ-huquqiy manbalar asosida vakolat doirasida qo‘shimcha huquqiy tekshiruvdan "
        "o‘tkaziladi. Yakuniy javobda aniqlangan holatlar, qo‘llanadigan norma va xulosa mas’ul "
        "xodim tomonidan aniq to‘ldirilishi lozim.",
    ])
    return "\n".join(lines)


def _evidence_is_on_topic(question: str, chunks: list[Chunk]) -> bool:
    """Guard against a semantically-close but topically-unrelated law.

    A question the concept model does not recognise ("Konstitutsiyada nechta modda
    bor?") can still clear the vector similarity floor against an unrelated statute,
    because legal texts resemble each other. When the question names no known legal
    concept, no explicit article, and shares no subject-matter word with any selected
    chunk, there is no evidence — only resemblance. Retrieval thresholds are
    untouched; this only refuses to present the result as authority.
    """
    if not chunks:
        return False
    concepts = legal_concepts(question)
    if concepts.distinct_topics or _requested_article_number(question):
        return True
    return any(_has_topical_overlap(question, chunk) for chunk in chunks)


async def run_chat(db: Session, user: User, question: str, mode: str = "legal") -> ChatOutcome:
    """Answer one chat request in the mode the user explicitly selected.

    The mode is a contract, not a hint. "general" never becomes legal RAG just
    because the text mentions a law, a modda or the constitution: legal-intent
    inference is confined to the opt-in "auto" mode.
    """
    started = time.monotonic()
    if is_analytics_question(question):
        answer, sources = analytics_answer(db, user)
        logger.info("chat mode=%s llm_calls=0 result_kind=ok deterministic=analytics "
                    "elapsed_ms=%s", mode, round((time.monotonic() - started) * 1000))
        return ChatOutcome(answer, sources, "ok", None, "analytics_chat", mode, False)
    inferred_legal = has_legal_intent(question) if mode == "auto" else False
    legal = mode == "legal" or (mode == "auto" and inferred_legal)
    routed = mode == "auto" and inferred_legal
    corpus_signature = "|".join(sorted(db.scalars(
        select(NhhDocument.id).where(NhhDocument.is_active.is_(True), NhhDocument.indexed.is_(True))
    ))) + f"#{db.scalar(select(func.count(Chunk.id)).where(Chunk.corpus_type == 'nhh')) or 0}"
    key = answer_cache.cache_key("legal" if legal else "general", question, corpus_signature)
    cached = answer_cache.get(key)
    if cached:
        logger.info("chat mode=%s cache=hit elapsed_ms=%s", "legal" if legal else "general",
                    round((time.monotonic() - started) * 1000))
        return ChatOutcome(cached["answer"], cached["sources"], "ok", cached.get("warning"),
                           cached["operation"], cached["effective_mode"], routed)
    if not legal:
        # A genuinely general question never touches vector retrieval, legal topic
        # filtering, article deduplication or grounding: one generation, nothing else.
        # The mode stays a contract: a legal question is NOT re-routed to retrieval,
        # but it is also not answered with law recalled from the model's memory.
        legal_topic = has_legal_intent(question)
        warning = GENERAL_LEGAL_WARNING if legal_topic else None
        answer = await llm.generate(
            GENERAL_LEGAL_SYSTEM_PROMPT if legal_topic else GENERAL_SYSTEM_PROMPT, question,
            max_tokens=get_settings().max_tokens_for("general"))
        answer = latin_legal_answer(answer)
        if legal_topic and LEGAL_SPECIFICS_RE.search(answer):
            # The model named a law, an article or a figure it cannot support. Nothing
            # in this answer is verifiable, so none of it is shown.
            logger.warning("chat mode=general refused=unsourced_legal_specifics")
            answer = GENERAL_LEGAL_REDIRECT
        answer_cache.put(key, {"answer": answer, "sources": [], "operation": "general_chat",
                               "effective_mode": "general", "warning": warning})
        logger.info(
            "chat mode=general legal_topic=%s provider=%s model=%s llm_calls=1 "
            "retrieval_calls=0 elapsed_ms=%s", legal_topic, llm.provider_name,
            llm.active_model, round((time.monotonic() - started) * 1000),
        )
        return ChatOutcome(answer, [], "ok", warning, "general_chat", "general", False)

    warning = None
    corpus_count = db.scalar(
        select(func.count(NhhDocument.id)).where(
            NhhDocument.is_active.is_(True), NhhDocument.indexed.is_(True)
        )
    ) or 0
    if corpus_count:
        corpus_titles = list(db.scalars(
            select(NhhDocument.title).where(NhhDocument.is_active.is_(True),
                                            NhhDocument.indexed.is_(True))
        ))
        if names_foreign_document(question, corpus_titles):
            # "Konstitutsiyaning 1-moddasi" must never be answered with article 1 of
            # whatever law happens to be indexed.
            logger.info("chat mode=legal refused=foreign_document elapsed_ms=%s",
                        round((time.monotonic() - started) * 1000))
            return ChatOutcome(NO_SOURCES_ANSWER, [], "no_sources", None, "legal_chat",
                               "legal", routed)
    counted = article_count_answer(db, question) if corpus_count else None
    if counted:
        answer, sources = counted
        logger.info("chat mode=legal llm_calls=0 result_kind=ok deterministic=article_count "
                    "elapsed_ms=%s", round((time.monotonic() - started) * 1000))
        return ChatOutcome(answer, sources, "ok", None, "legal_chat", "legal", routed)
    retrieval_started = time.monotonic()
    chunks = await search_async(db, question, user, "nhh", None, 12) if corpus_count else []
    if corpus_count and not chunks:
        chunks = legal_lexical_fallback(db, question, 12)
    retrieval_ms = round((time.monotonic() - retrieval_started) * 1000)
    requested_article = re.search(
        r"\b(\d{1,3})\s*[-‐‑‒–—.]?\s*(?:modda(?:si|ning|ga|da)?|модда(?:си|нинг|га|да)?)\b",
        question, re.IGNORECASE,
    )
    if requested_article:
        number = requested_article.group(1)
        article_pattern = re.compile(
            rf"\b{re.escape(number)}\s*[-‐‑‒–—.]?\s*(?:modda|модда|статья)\b",
            re.IGNORECASE,
        )
        # An explicitly requested article is a hard constraint. Returning a
        # semantically similar but different article would be legally unsafe.
        chunks = [chunk for chunk in chunks if article_pattern.search(
            f"{chunk.article_clause or ''} {chunk.text[:180]}"
        )]
        # "19-moddaning mazmuni" carries almost no semantics, so the article is often
        # absent from the vector top-N. The corpus is indexed by heading: look the
        # article up directly rather than answering "no sources" for a law we hold.
        if not chunks and corpus_count:
            chunks = article_chunks_by_number(db, number)
    chunks = filter_legal_topic(chunks, question)
    selected = prefer_article_starts(db, distinct_source_chunks(chunks, limit=3))
    if selected and not _evidence_is_on_topic(question, selected):
        logger.info("chat mode=legal dropped_offtopic_evidence=%s",
                    [chunk.article_clause for chunk in selected])
        selected = []
    sources = expand_article_sources(db, selected, sources_from_chunks(selected))
    if not selected:
        if corpus_count == 0:
            answer = (
                "Normativ-huquqiy hujjatlar bazasi hozircha bo‘sh. Huquqiy javob olish uchun "
                "Administrator panelidagi NHH bazasiga tegishli rasmiy hujjatni nomi va manba "
                "havolasi bilan yuklab, indekslash kerak. Manbasiz huquqiy javob yaratilmaydi."
            )
            result_kind = "corpus_empty"
        else:
            answer = NO_SOURCES_ANSWER
            result_kind = "no_sources"
        logger.info(
            "chat mode=legal provider=%s model=%s llm_calls=0 retrieval_ms=%s "
            "result_kind=%s sources=0 elapsed_ms=%s", llm.provider_name, llm.active_model,
            retrieval_ms, result_kind, round((time.monotonic() - started) * 1000),
        )
        return ChatOutcome(answer, [], result_kind, warning, "legal_chat", "legal", routed)

    settings = get_settings()
    deterministic = deterministic_legal_answer(question, sources)
    if deterministic:
        answer, sources = deterministic
        result_kind = "ok"
        warning = None
    internal_source = any(
        chunk.nhh and chunk.nhh.category == "Idoraviy (ichki) hujjat" for chunk in selected
    )
    external_allowed = settings.allow_external_confidential_ai or not internal_source
    if deterministic:
        pass
    elif llm.configured:
        # Only a statutory list ABOUT the question is guaranteed in full. Article 4's
        # glossary is an enumeration too, but appending twenty definitions to
        # "what is unfair competition?" buries the answer instead of completing it.
        enumerations = [(source, enumerated) for source in sources
                        if (enumerated := source_enumeration(source))
                        and _enumeration_is_on_topic(question, source, enumerated[0])]
        list_hint = ""
        for source, enumerated in enumerations:
            if enumerated:
                lead, items = enumerated
                list_hint += (f"\nESLATMA: [MANBA {source['citation_number']}] da {len(items)} bandlik "
                              f"rasmiy ro‘yxat bor («{lead[:80]}»); javobda barcha bandlarni qamrab oling.")
        generation = await generate_grounded_legal(
            LEGAL_SYSTEM_PROMPT,
            f"SAVOL:\n{question}\n\nTEKSHIRILGAN MANBALAR:\n{grounded_source_context(sources)}"
            + list_hint,
            sources,
            question=question,
            compact_prompt=(
                f"SAVOL:\n{question}\n\nTEKSHIRILGAN MANBALAR:\n"
                f"{grounded_source_context(sources[:2])[:4500]}"
            ), allow_external=external_allowed,
        )
        answer = generation.answer
        sources = generation.sources
        result_kind = generation.result_kind
        warning = generation.warning
        if result_kind == "ok":
            # Completeness guarantee: a statutory list the model summarised only in
            # part is appended in full, verbatim and cited, so no criterion, prohibition
            # or fine rate can silently disappear from a legal answer.
            cited = {source["citation_number"] for source in sources}
            for source, enumerated in enumerations:
                if not enumerated or source["citation_number"] not in cited:
                    continue
                missing = missing_enumeration_items(answer, enumerated[1])
                if missing:
                    block = enumeration_block(source)
                    if block:
                        answer = f"{answer}\n\n{block}"
                        logger.info("chat mode=legal appended_enumeration=%s missing_items=%s",
                                    source.get("display_label"), len(missing))
        if result_kind == "ok" and _is_no_basis_answer(answer):
            if legal_concepts(question).distinct_topics or _requested_article_number(question):
                # A real competition-law question whose commentary the model could
                # not ground: the verified article text is still the useful answer.
                answer, sources = extractive_legal_fallback(sources, question=question)
                result_kind = "source_matches"
                warning = ("AI izohi manbada aniq javob topmadi; tekshirilgan huquqiy "
                           "asoslar ko‘rsatildi.")
            else:
                # Off-topic question: the retrieved article is resemblance, not
                # evidence, and must not be shown as the "source" of a non-answer.
                answer = NO_SOURCES_ANSWER
                sources = []
                result_kind = "no_sources"
                warning = None
    else:
        answer = source_matches_answer(sources, question)
        result_kind = "source_matches"
        warning = "AI xizmati vaqtincha mavjud emas. Tekshirilgan huquqiy manbalar ko‘rsatildi."
    logger.info(
        "chat mode=legal provider=%s model=%s llm_used=%s retrieval_ms=%s result_kind=%s "
        "sources=%s elapsed_ms=%s", llm.provider_name, llm.active_model,
        not bool(deterministic), retrieval_ms, result_kind, len(sources),
        round((time.monotonic() - started) * 1000),
    )
    if result_kind == "ok" and sources and not warning:
        answer_cache.put(key, {"answer": answer, "sources": sources, "operation": "legal_chat",
                               "effective_mode": "legal"})
    return ChatOutcome(answer, sources, result_kind, warning, "legal_chat", "legal", routed)
