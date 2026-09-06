import logging
import re
from dataclasses import dataclass

from fastapi import HTTPException

from ..config import get_settings
from ..models import Chunk
from .grounding import latin_legal_answer
from .llm import llm
from .rag import clean_excerpt, sources_from_chunks


logger = logging.getLogger(__name__)

RESPONSE_LETTER_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["subject", "salutation", "appeal_summary", "legal_basis", "conclusion", "closing"],
    "properties": {
        "subject": {"type": "string"},
        "salutation": {"type": "string"},
        "appeal_summary": {"type": "array", "items": {"type": "string"}, "maxItems": 4},
        "legal_basis": {
            "type": "array", "maxItems": 4,
            "items": {
                "type": "object", "additionalProperties": False,
                "required": ["statement", "source_ids"],
                "properties": {
                    "statement": {"type": "string"},
                    "source_ids": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                },
            },
        },
        "conclusion": {"type": "array", "items": {"type": "string"}, "maxItems": 4},
        "closing": {"type": "string"},
    },
}


@dataclass
class DraftOutcome:
    answer: str
    sources: list[dict]
    structured: dict
    result_kind: str
    warning: str | None
    failure_reason: str | None


def _numbers(value: str) -> set[str]:
    return set(re.findall(r"\b\d+(?:[.,]\d+)?\b", value))


def _sentences(value: str) -> list[str]:
    return [re.sub(r"\s+", " ", item).strip() for item in re.split(r"(?<=[.!?])\s+|\n+", value)
            if item.strip()]


def _legal_label(chunk: Chunk) -> str:
    raw = chunk.article_clause or ""
    match = re.search(r"\b(\d{1,3})\s*[-.]?\s*(?:modda|модда|статья)", raw, re.IGNORECASE)
    return f"{match.group(1)}-modda" if match else "tegishli norma"


def _failure_reason(exc: HTTPException) -> str:
    detail = str(exc.detail).lower()
    if "maxfiy" in detail or "ichki material" in detail:
        return "confidential_external_blocked"
    if exc.status_code == 429:
        return "rate_limited"
    if exc.status_code == 504:
        return "timeout"
    if "uzunlik" in detail or "uzildi" in detail:
        return "truncated"
    if "format" in detail:
        return "validation_failed"
    return "provider_unavailable"


def _user_warning(reason: str) -> str:
    return {
        "rate_limited": "AI xizmati band bo‘lgani uchun tekshirilgan dalillardan xavfsiz loyiha tayyorlandi.",
        "timeout": "AI javobi belgilangan vaqtda olinmagani uchun tekshirilgan dalillardan xavfsiz loyiha tayyorlandi.",
        "truncated": "AI javobi to‘liq kelmagani uchun tekshirilgan dalillardan xavfsiz loyiha tayyorlandi.",
        "validation_failed": "Loyiha faqat tekshirilgan hujjat va huquqiy manbalar asosida tayyorlandi.",
        "insufficient_evidence": "Loyiha mavjud dalillar doirasida tayyorlandi; yuborishdan oldin mas’ul xodim tekshirishi kerak.",
        "provider_unavailable": "AI xizmati vaqtincha mavjud emas; tekshirilgan dalillardan xavfsiz loyiha tayyorlandi.",
        "confidential_external_blocked": (
            "Maxfiy yoki ichki material tashqi AI xizmatiga yuborilmadi; loyiha faqat lokal "
            "ajratilgan dalillar asosida tayyorlandi."
        ),
    }[reason]


def _build_sources(document_chunks: list[Chunk], legal_chunks: list[Chunk]) -> tuple[list[dict], dict[str, int]]:
    document_sources = sources_from_chunks(document_chunks[:4])
    legal_sources = sources_from_chunks(legal_chunks[:3])
    combined = document_sources + legal_sources
    mapping: dict[str, int] = {}
    for index, source in enumerate(combined, 1):
        source["citation_number"] = index
        prefix_index = (document_sources.index(source) + 1 if source in document_sources
                        else legal_sources.index(source) + 1)
        mapping[("D" if source["evidence_type"] == "document" else "L") + str(prefix_index)] = index
    return combined, mapping


def _fallback_structure(document_chunks: list[Chunk], legal_chunks: list[Chunk]) -> dict:
    document_text = " ".join(chunk.text for chunk in document_chunks)
    ignored_exact = {"murojaat mazmuni", "so‘rov", "ilova sifatida mavjud demo dalillar"}
    candidates = [
        s for s in _sentences(document_text)
        if s.lower() not in ignored_exact
        and not s.lower().startswith(("demo -", "ushbu hujjat faqat"))
        and "haqiqiy murojaat emas" not in s.lower()
        and " | " not in s
    ]
    summary = ["Qo‘mitaga kelib tushgan murojaat ko‘rib chiqildi."]
    for sentence in candidates[:3]:
        # Headings and title fragments are not statements; and the appellant writes in
        # the first person, which must never appear as the Committee's own voice.
        if len(sentence) < 60 and not sentence.rstrip().endswith((".", "!", "?")):
            continue
        reported = re.sub(r"\b(\w+?)(?:amiz|aymiz|ymiz|miz)\b", r"\1gani bildirilgan", sentence)
        summary.append("Murojaatda ko‘rsatilishicha, " + reported[:1].lower() + reported[1:])
    if len(summary) == 1:
        summary.append("Murojaat mazmuni hujjatdagi mavjud ma’lumotlar asosida ko‘rib chiqildi.")
    legal_basis = []
    for index, chunk in enumerate(legal_chunks[:3], 1):
        label = _legal_label(chunk)
        legal_basis.append({
            "statement": (
                f"{label} murojaatda bayon etilgan holatlarni huquqiy baholash uchun "
                "tegishli norma sifatida ko‘rib chiqiladi."
            ),
            "source_ids": [f"L{index}"],
        })
    primary_label = _legal_label(legal_chunks[0]) if legal_chunks else "tegishli norma"
    primary_citation = len(document_chunks[:4]) + 1 if legal_chunks else None
    return {
        "kind": "response_letter",
        "subject": "Murojaatni ko‘rib chiqish natijalari haqida",
        "salutation": "Hurmatli murojaat etuvchi!",
        "appeal_summary": summary,
        "legal_basis": legal_basis,
        "conclusion": [
            f"Murojaatda bayon etilgan holatlar tasdiqlangan taqdirda, ular {primary_label} "
            f"doirasida huquqiy baholanishi mumkin."
            f"{' [' + str(primary_citation) + ']' if primary_citation else ''}",
            "Yakuniy huquqiy baho faqat vakolatli ko‘rib chiqishda tasdiqlangan holatlarga ko‘ra beriladi.",
        ],
        "closing": "[Ism]\n[Lavozim]\n[Tashkilot]",
        "review_note": "Ishchi loyiha. Yuborishdan oldin mas’ul xodim tahriri va huquqiy tekshiruvi talab etiladi.",
    }


def _validate_structure(value: dict, document_text: str, legal_text: str,
                        legal_ids: set[str]) -> str | None:
    """None when the letter is acceptable, otherwise a short machine-readable reason."""
    required = {"subject", "salutation", "appeal_summary", "legal_basis", "conclusion", "closing"}
    if not required.issubset(value) or not isinstance(value.get("appeal_summary"), list):
        return "missing_fields"
    # Every figure the letter may state: the appeal's own numbers plus everything the
    # cited legal evidence contains (article numbers, thresholds, deadlines). Checking
    # against the appeal alone rejected correct letters over their own article number.
    doc_numbers = _numbers(document_text) | _numbers(legal_text)
    for statement in value["appeal_summary"]:
        if not isinstance(statement, str):
            return "summary_not_text"
        invented = _numbers(statement) - doc_numbers
        if invented:
            return "summary_number:" + ",".join(sorted(invented))
    for basis in value.get("legal_basis", []):
        if not isinstance(basis, dict) or not basis.get("source_ids"):
            return "legal_basis_uncited"
        unknown = set(basis["source_ids"]) - legal_ids
        if unknown:
            return "legal_basis_source:" + ",".join(sorted(unknown))
    conclusions = " ".join(value.get("conclusion") or []).lower()
    categorical = re.search(
        r"(?:qoidabuzarlik|huquqbuzarlik)\s+(?:sodir etilgan|tasdiqlandi|aniqlandi)|"
        r"qonun\s+buzilgan|javobgarlikka\s+tortilsin|ish\s+qo['‘’]zg['‘’]atilsin",
        conclusions,
    )
    conditional = any(value in conclusions for value in
                      ("tasdiqlangan taqdirda", "mumkin", "ehtimol", "faqat"))
    if categorical and not conditional:
        return "categorical_conclusion"
    return None


IDENTIFIER_RE = re.compile(r"\[?\b([LD]\d{1,2})\b\]?")


def _map_prose_identifiers(structured: dict, citation_map: dict[str, int]) -> None:
    """Turn internal L1/D1 identifiers the model left in prose into display citations.

    An item that consists of nothing but an identifier carries no statement and is
    dropped rather than rendered as a bare "L1" line in an official letter.
    """
    def convert(text: str) -> str:
        return IDENTIFIER_RE.sub(
            lambda match: f"[{citation_map[match.group(1)]}]" if match.group(1) in citation_map else "",
            text,
        ).strip()

    for key in ("appeal_summary", "conclusion"):
        items = structured.get(key)
        if not isinstance(items, list):
            continue
        cleaned = []
        for item in items:
            if not isinstance(item, str):
                continue
            if IDENTIFIER_RE.fullmatch(item.strip()) or not item.strip():
                continue
            cleaned.append(convert(item))
        structured[key] = cleaned


def _latinize_letter(structured: dict) -> None:
    """Keep the outgoing letter in Latin script even when the model mirrors Cyrillic evidence.

    Applied only after structural validation; transliteration is a 1:1 script mapping
    and cannot introduce a number, date or claim that was not already there.
    """
    for key in ("subject", "salutation"):
        if isinstance(structured.get(key), str):
            structured[key] = latin_legal_answer(structured[key])
    for key in ("appeal_summary", "conclusion"):
        if isinstance(structured.get(key), list):
            structured[key] = [latin_legal_answer(item) if isinstance(item, str) else item
                               for item in structured[key]]
    for basis in structured.get("legal_basis") or []:
        if isinstance(basis, dict) and isinstance(basis.get("statement"), str):
            basis["statement"] = latin_legal_answer(basis["statement"])


def render_response_letter(structured: dict, citation_map: dict[str, int]) -> str:
    lines = ["# Javob xati", "", f"**Mavzu:** {structured['subject']}", "",
             structured["salutation"], "", "## Murojaat"]
    lines.extend(f"- {item}" for item in structured["appeal_summary"])
    lines.extend(["", "## Huquqiy asos"])
    for item in structured["legal_basis"]:
        cites = " ".join(f"[{citation_map[value]}]" for value in item["source_ids"] if value in citation_map)
        lines.append(f"- {item['statement']} {cites}".rstrip())
    lines.extend(["", "## Ko‘rib chiqish"])
    lines.extend(f"- {item}" for item in structured["conclusion"])
    lines.extend(["", structured["closing"]])
    if structured.get("review_note"):
        lines.extend(["", f"> {structured['review_note']}"])
    return "\n".join(lines)


async def create_response_letter(instruction: str, document_chunks: list[Chunk],
                                 legal_chunks: list[Chunk], *,
                                 allow_external: bool = True) -> DraftOutcome:
    sources, citation_map = _build_sources(document_chunks, legal_chunks)
    document_text = "\n".join(chunk.text for chunk in document_chunks)
    document_evidence = "\n\n".join(
        f"[D{i}] {clean_excerpt(chunk.text, 900)}" for i, chunk in enumerate(document_chunks[:4], 1)
    )
    legal_evidence = "\n\n".join(
        f"[L{i}] {chunk.article_clause or ''}\n{clean_excerpt(chunk.text, 900)}"
        for i, chunk in enumerate(legal_chunks[:3], 1)
    )
    try:
        if not allow_external:
            raise HTTPException(
                503,
                "Maxfiy yoki ichki material tashqi AI xizmatiga yuborilmadi",
            )
        structured = await llm.generate_structured(
            "Rasmiy javob xati tuzing. Bo‘limlar nomini (DOCUMENT_EVIDENCE, LEGAL_EVIDENCE, "
            "TOPSHIRIQ) matnda ishlatmang. Butun matnni faqat o‘zbek lotin alifbosida yozing; "
            "kirill yozuvidagi manba matnini ko‘chirmang, mazmunini lotin yozuvida bayon qiling. "
            "Faqat DOCUMENT_EVIDENCE faktlari va LEGAL_EVIDENCE "
            "normalaridan foydalaning. Murojaatdagi sana, son va nomlarni o‘zgartirmang. "
            "Huquqiy xulosada faqat L manba identifikatorlarini ishlating. Placeholder zarur "
            "bo‘lsa kvadrat qavsda yozing. Murojaatdagi da'voni isbotlangan fakt deb e'lon "
            "qilmang. 'Tasdiqlangan taqdirda ... huquqiy baholanishi mumkin' kabi shartli, "
            "ehtiyotkor til ishlating; protsessual qaror, buyruq yoki sanksiya uydirmang. "
            "Yakunida aynan [Ism], [Lavozim], [Tashkilot] placeholderlarini alohida satrda "
            "bering. 500 so‘zdan oshirmang.",
            f"TOPSHIRIQ:\n{instruction}\n\nDOCUMENT_EVIDENCE:\n{document_evidence}\n\n"
            f"LEGAL_EVIDENCE:\n{legal_evidence}",
            RESPONSE_LETTER_SCHEMA,
            max_tokens=get_settings().max_tokens_for("drafting"),
        )
        structured["kind"] = "response_letter"
        structured["review_note"] = (
            "Ishchi loyiha. Yuborishdan oldin mas’ul xodim tahriri va huquqiy tekshiruvi talab etiladi."
        )
        legal_text = "\n".join(f"{chunk.article_clause or ''} {chunk.text}"
                               for chunk in legal_chunks[:3])
        reason = _validate_structure(structured, document_text, legal_text,
                                     {f"L{i}" for i in range(1, len(legal_chunks[:3]) + 1)})
        if reason:
            logger.warning("Response letter rejected reason=%s", reason)
            raise HTTPException(502, "AI xizmati tuzilmali javob formatiga rioya qilmadi")
        if legal_chunks and not any("tasdiqlangan taqdirda" in item.lower()
                                    for item in structured.get("conclusion", [])):
            structured["conclusion"].insert(
                0,
                f"Murojaatda bayon etilgan holatlar tasdiqlangan taqdirda, ular "
                f"{_legal_label(legal_chunks[0])} doirasida huquqiy baholanishi mumkin. "
                f"[{citation_map['L1']}]",
            )
        structured["closing"] = "[Ism]\n[Lavozim]\n[Tashkilot]"
        _map_prose_identifiers(structured, citation_map)
        _latinize_letter(structured)
        return DraftOutcome(render_response_letter(structured, citation_map), sources, structured,
                            "ok", None, None)
    except HTTPException as exc:
        reason = _failure_reason(exc)
        structured = _fallback_structure(document_chunks, legal_chunks)
        return DraftOutcome(render_response_letter(structured, citation_map), sources, structured,
                            "source_matches", _user_warning(reason), reason)
