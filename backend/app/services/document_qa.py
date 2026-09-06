import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from ..models import Chunk
from .rag import sources_from_chunks


APOSTROPHE_RE = re.compile("[’‘ʻ`ʼ]")
NUMBER = r"(-?\d+(?:[.,]\d+)?)"
LABEL_VALUE_RE = re.compile(
    rf"(?P<label>[A-Za-zÀ-žO‘’ʻ'`ʼ\s-]{{3,60}}?)\s*:\s*{NUMBER}(?:\s*ta)?",
    re.IGNORECASE,
)
STOP = {
    "asosiy", "ko'rsatkich", "ko'rsatkichlar", "necha", "qancha", "foizi", "foiz",
    "hujjat", "tanlangan", "haqida", "uchun", "qanday", "qaysi", "bilan", "gapda",
    "izohlang", "ayting", "ma'lumot", "bor", "edi", "ning", "jami",
}


@dataclass(frozen=True)
class DeterministicAnswer:
    answer: str
    sources: list[dict]
    structured: dict


def _normalized(value: str) -> str:
    return re.sub(r"\s+", " ", APOSTROPHE_RE.sub("'", value).lower()).strip()


def _format_decimal(value: Decimal) -> str:
    text = format(value.quantize(Decimal("0.01")), "f").rstrip("0").rstrip(".")
    return text.replace(".", ",")


def _metrics(chunks: list[Chunk]) -> list[tuple[str, Decimal, Chunk]]:
    result: list[tuple[str, Decimal, Chunk]] = []
    for chunk in chunks:
        text = re.sub(r"\s+", " ", chunk.text)
        for match in LABEL_VALUE_RE.finditer(text):
            label = _normalized(match.group("label")).strip(" -")
            # PDF rows can bleed into a label. Keep the final meaningful phrase.
            label = re.split(r"[.;]", label)[-1].strip()
            try:
                value = Decimal(match.group(2).replace(",", "."))
            except InvalidOperation:
                continue
            result.append((label, value, chunk))
    return result


def _find_metric(metrics: list[tuple[str, Decimal, Chunk]], *needles: str):
    normalized_needles = tuple(_normalized(value) for value in needles)
    for label, value, chunk in metrics:
        if any(needle in label for needle in normalized_needles):
            return label, value, chunk
    return None


def _source_numbers(chunks: list[Chunk]) -> tuple[list[dict], dict[str, int]]:
    unique: list[Chunk] = []
    for chunk in chunks:
        if chunk.id not in {item.id for item in unique}:
            unique.append(chunk)
    sources = sources_from_chunks(unique)
    return sources, {chunk.id: index + 1 for index, chunk in enumerate(unique)}


def _question_tokens(value: str) -> set[str]:
    return {
        token for token in re.findall(r"[a-z0-9']+", _normalized(value))
        if len(token) > 2 and token not in STOP
    }


# "12-bo‘limda jami murojaatlar nechta?" names WHICH occurrence is wanted. The
# deterministic extractor scans the whole document and would answer with the first
# match, so a qualified question must not take this path.
QUALIFIER_RE = re.compile(
    r"\b(\d{1,3})\s*[-‑–—]?\s*(bo['‘’ʻ`]?lim|band|modda|bob|chorak|sahifa|varaq)\w*",
    re.IGNORECASE,
)


def _stems(value: str) -> set[str]:
    """Prefix stems, so agglutinative suffixes do not break the overlap test."""
    return {token[:5] for token in _question_tokens(value) if len(token) >= 5}


def answer_document_question(question: str, chunks: list[Chunk]) -> DeterministicAnswer | None:
    """Resolve explicit arithmetic/extractive questions locally; never invent missing facts."""
    q = _normalized(question)
    if QUALIFIER_RE.search(q):
        # Let retrieval + the grounded generator answer, they can see which section.
        return None
    metrics = _metrics(chunks)
    numerator = _find_metric(metrics, "jarayonda")
    denominator = _find_metric(metrics, "jami murojaatlar", "murojaatlar soni")

    # Resolve compact monthly table rows such as "Iyun 49 34 15" locally.
    # The conventional column order is Jami / Ko‘rib chiqilgan / Jarayonda.
    months = ("yanvar", "fevral", "mart", "aprel", "may", "iyun", "iyul",
              "avgust", "sentabr", "oktabr", "noyabr", "dekabr")
    requested_month = next((month for month in months if month in q), None)
    if requested_month and any(term in q for term in ("necha", "nechta", "qancha", "soni")):
        for chunk in chunks:
            match = re.search(
                rf"\b{requested_month}\s+(\d+)\s+(\d+)\s+(\d+)\b",
                _normalized(chunk.text), re.IGNORECASE,
            )
            if not match:
                continue
            column = 3 if "jarayonda" in q else 2 if "ko'rib chiq" in q else 1
            labels = {1: "jami murojaatlar", 2: "ko‘rib chiqilgan murojaatlar",
                      3: "jarayondagi murojaatlar"}
            value = match.group(column)
            sources, _ = _source_numbers([chunk])
            return DeterministicAnswer(
                f"{requested_month.capitalize()} oyida {labels[column]}: {value} ta. [1]",
                sources,
                {"kind": "document_qa", "method": "monthly_table",
                 "month": requested_month, "column": labels[column], "value": value},
            )

    if any(term in q for term in ("foiz", "%")) and "jarayonda" in q:
        if not numerator or not denominator or denominator[1] == 0:
            return DeterministicAnswer("Bu ma’lumot tanlangan hujjatda topilmadi.", [],
                                       {"kind": "document_qa", "method": "not_found"})
        sources, ids = _source_numbers([numerator[2], denominator[2]])
        result = numerator[1] / denominator[1] * Decimal("100")
        citations = " ".join(f"[{ids[item.id]}]" for item in (numerator[2], denominator[2])
                             if f"[{ids[item.id]}]" not in [])
        # If both operands are in one source, do not duplicate the citation.
        citations = " ".join(dict.fromkeys(citations.split()))
        answer = (
            f"Jarayondagi murojaatlar jami murojaatlarning {_format_decimal(result)}%ini tashkil etadi: "
            f"{_format_decimal(numerator[1])} / {_format_decimal(denominator[1])} × 100 = "
            f"{_format_decimal(result)}%. {citations}"
        )
        return DeterministicAnswer(answer, sources, {
            "kind": "document_qa", "method": "percentage",
            "numerator": str(numerator[1]), "denominator": str(denominator[1]),
            "result": str(result),
        })

    requested = None
    if "jarayonda" in q:
        requested = numerator
    elif "jami murojaat" in q or "murojaatlar soni" in q:
        requested = denominator
    if requested and any(term in q for term in ("necha", "nechta", "qancha", "soni")):
        sources, _ = _source_numbers([requested[2]])
        return DeterministicAnswer(
            f"{requested[0].capitalize()}: {_format_decimal(requested[1])} ta. [1]", sources,
            {"kind": "document_qa", "method": "extractive_number", "value": str(requested[1])},
        )

    if re.search(r"\b(?:1\s*[-–]\s*2|bir\s*[-–]?\s*ikki)\s+(?:gap(?:da)?|jumla(?:da)?)", q):
        candidates: list[tuple[str, Chunk]] = []
        for chunk in chunks:
            for sentence in re.split(r"(?<=[.!?])\s+|\n+", chunk.text):
                cleaned = re.sub(r"\s+", " ", sentence).strip()
                if (len(cleaned) >= 18
                        and cleaned.lower() not in {"band qiymat", "ko‘rsatkich qiymat"}
                        and not cleaned.lower().startswith(("demo -", "sahifa"))):
                    candidates.append((cleaned, chunk))
        selected = candidates[:2]
        if selected:
            sources, ids = _source_numbers([item[1] for item in selected])
            answer = " ".join(f"{text} [{ids[chunk.id]}]" for text, chunk in selected)
            return DeterministicAnswer(answer, sources,
                                       {"kind": "document_qa", "method": "extractive_summary"})

    # For a precise fact question with no meaningful term in the selected document,
    # stop locally instead of asking a model to fill the gap from general knowledge.
    fact_question = any(term in q for term in
                        ("necha", "nechta", "qancha", "qachon", "qayer", "kim", "qaysi"))
    if fact_question:
        # Compared on prefix stems: Uzbek is agglutinative, so "ulushi"/"ulushga" and
        # "murojaatda"/"murojaat" are the same word for relevance purposes. An exact
        # token test refused questions the document plainly answers.
        evidence = " ".join(chunk.text for chunk in chunks)
        if not (_stems(q) & _stems(evidence)):
            return DeterministicAnswer("Bu ma’lumot tanlangan hujjatda topilmadi.", [],
                                       {"kind": "document_qa", "method": "not_found"})
    return None
