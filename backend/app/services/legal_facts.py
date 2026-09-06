"""Generic deterministic facts extracted from retrieved legal evidence.

This module deliberately knows nothing about a particular law or article number.  It
operates on the sources selected by the RAG layer, so newly indexed legislation gets
the same numeric and list handling without production-code changes.
"""

import re
from dataclasses import dataclass
from decimal import Decimal

from .grounding import _complete_excerpt, _latin_explanation, article_label
from .legal_intent import LegalConcepts, legal_concepts


@dataclass(frozen=True)
class LegalSourceFacts:
    label: str
    text: str
    clauses: tuple[str, ...]
    percentages: tuple[Decimal, ...]
    source: dict


NUMBER_UNITS = {
    "bir": 1, "ikki": 2, "uch": 3, "to'rt": 4, "besh": 5,
    "olti": 6, "yetti": 7, "sakkiz": 8, "to'qqiz": 9,
}
NUMBER_TENS = {
    "o'n": 10, "yigirma": 20, "o'ttiz": 30, "qirq": 40, "ellik": 50,
    "oltmish": 60, "yetmish": 70, "sakson": 80, "to'qson": 90,
}


def _word_number(value: str) -> int | None:
    tokens = re.findall(r"[a-z']+", value.lower())
    total = current = 0
    recognized = False
    for token in tokens:
        if token in NUMBER_UNITS:
            current += NUMBER_UNITS[token]
            recognized = True
        elif token in NUMBER_TENS:
            current += NUMBER_TENS[token]
            recognized = True
        elif token == "yuz":
            current = (current or 1) * 100
            recognized = True
        elif token == "ming":
            total += (current or 1) * 1000
            current = 0
            recognized = True
    return total + current if recognized else None


def _decimal(value: str) -> Decimal:
    return Decimal(value.replace(",", "."))


def _fmt(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text.replace(".", ",")


def parse_legal_source(source: dict) -> LegalSourceFacts | None:
    raw = source.get("full_excerpt") or source.get("excerpt") or ""
    text = _latin_explanation(re.sub(r"\s+", " ", raw).strip())
    if not text:
        return None
    # The stored chunk repeats its own heading ("13-modda. Ustun mavqe") before the
    # body; without this the first clause reads "Ustun mavqe Tovar yoki ...".
    heading = _latin_explanation(re.sub(r"\s+", " ", source.get("article_or_clause") or "").strip())
    if heading and text.lower().startswith(heading.lower()):
        text = text[len(heading):].lstrip(" .:;-")
    text = re.sub(r"^\s*\d{1,3}\s*[-.]?\s*modda\.?\s*", "", text, flags=re.IGNORECASE)
    percentages = [_decimal(value) for value in re.findall(r"(\d+(?:[.,]\d+)?)\s*foiz", text,
                                                           flags=re.IGNORECASE)]
    for words in re.findall(r"([a-z‘’'\s-]{2,30})\s+foiz", text, flags=re.IGNORECASE):
        parsed = _word_number(words.replace("‘", "'").replace("’", "'"))
        if parsed is not None:
            percentages.append(Decimal(parsed))
    normalized_clauses: list[str] = []
    for part in re.split(r";|(?<=\.)\s+(?=[A-ZА-ЯO‘G‘])", text):
        clause = part.strip(" -;.")
        if len(clause) >= 25:
            normalized_clauses.append(clause + ("" if clause.endswith(".") else "."))
    return LegalSourceFacts(
        article_label(source), text, tuple(normalized_clauses),
        tuple(dict.fromkeys(percentages)), source,
    )


def _citation(source: dict) -> str:
    return f"[{source['citation_number']}]"


def _share_threshold(facts: LegalSourceFacts) -> Decimal | None:
    patterns = (
        r"(?:bozor(?:dagi)?\s+)?ulushi\s+(\d+(?:[.,]\d+)?)\s*foiz",
        r"(\d+(?:[.,]\d+)?)\s*foiz(?:dan|ga)?\s+(?:teng|ortiq|kam)",
    )
    for pattern in patterns:
        match = re.search(pattern, facts.text, flags=re.IGNORECASE)
        if match:
            return _decimal(match.group(1))
    return facts.percentages[0] if len(facts.percentages) == 1 else None


def _enumerated_clauses(facts: LegalSourceFacts) -> tuple[str, list[str]] | None:
    """The article's own enumeration, in document order.

    A statutory list ("Tovar bozorida quyidagilar ustun mavqe deb e'tirof etiladi: ...")
    must be reproduced whole and in order. Ranking its items by keyword overlap dropped
    two of the four dominance criteria and buried the 40% threshold last.
    """
    for index, clause in enumerate(facts.clauses):
        # The lead-in and its first item are usually one semicolon-delimited clause
        # ("... quyidagilar ustun mavqe deb e'tirof etiladi: agar raqobatchilari ...").
        head, separator, tail = clause.partition(":")
        if not separator or not re.search(r"quyidagi|қуйидаги", head, re.IGNORECASE):
            continue
        items = [item.strip(" .;") for item in ([tail] + list(facts.clauses[index + 1:]))]
        items = [item + ("" if item.endswith(".") else ".") for item in items if len(item) >= 20]
        if items:
            return head.strip() + ":", items
    return None


def _relevant_clauses(facts: LegalSourceFacts, question: str, limit: int = 6) -> list[str]:
    stop = {"qanday", "qaysi", "uchun", "bilan", "bo'yicha", "haqida", "nima", "bor",
            "modda", "band", "qonun", "qonunda", "hollarda", "shartlar", "mezonlar"}
    terms = {word for word in re.findall(r"[a-z0-9‘’']{4,}", question.lower()) if word not in stop}
    ranked = sorted(
        facts.clauses,
        key=lambda clause: sum(term in clause.lower() for term in terms),
        reverse=True,
    )
    return ranked[:limit]


def deterministic_legal_fact_answer(question: str,
                                    sources: list[dict]) -> tuple[str, list[dict]] | None:
    """Answer only facts that can be deterministically read from retrieved sources."""
    concepts: LegalConcepts = legal_concepts(question)
    parsed = [facts for source in sources if (facts := parse_legal_source(source))]
    if not parsed:
        return None

    if concepts.requested_share is not None:
        candidates = [(facts, _share_threshold(facts)) for facts in parsed]
        candidates = [(facts, threshold) for facts, threshold in candidates if threshold is not None]
        if candidates:
            facts, threshold = candidates[0]
            requested = concepts.requested_share
            relation = "qanoatlantiradi" if requested >= threshold else "o‘z-o‘zidan qanoatlantirmaydi"
            answer = (
                f"**To‘g‘ridan-to‘g‘ri javob:** {_fmt(requested)}% ko‘rsatkich manbada qayd "
                f"etilgan {_fmt(threshold)}% mezonni {relation}. {_citation(facts.source)}\n\n"
                "Bu faqat sonli mezon taqqoslanishidir; yakuniy huquqiy bahoda shu normadagi "
                f"boshqa shartlar va istisnolar ham tekshiriladi. {_citation(facts.source)}"
            )
            return answer, [facts.source]

    # "Which article regulates X?" is a lookup, not an explanation: the article label,
    # its official title and its opening sentence answer it exactly and instantly.
    if re.search(r"\b(?:qaysi|qanday)\s+(?:modda|band)|moddalar\s+bor|qaysi\s+moddada", concepts.normalized):
        lines: list[str] = []
        used: list[dict] = []
        for facts in parsed[:3]:
            heading = re.sub(r"\s+", " ", facts.source.get("article_or_clause") or "").strip()
            title = _latin_explanation(re.sub(r"^\s*\d{1,3}\s*[-.]?\s*(?:modda|модда|статья)\.?\s*", "",
                                            heading, flags=re.IGNORECASE))
            opening = _complete_excerpt(facts.text, 260)
            label = f"**{facts.label}**" + (f" («{title}»)" if title else "")
            lines.append(f"{label}: {opening} {_citation(facts.source)}")
            used.append(facts.source)
        if lines:
            return "\n\n".join(lines), used

    # Everything else is written by the model over the verified sources: a raw dump of
    # article clauses is complete but reads like a photocopy, not like an answer. The
    # enumerations below are used to guarantee that the model's answer covers every
    # item, and as the extractive fallback when generation cannot be grounded.
    return None


def source_enumeration(source: dict) -> tuple[str, list[str]] | None:
    """The statutory list an article carries ("quyidagilar ... :" plus its items), if any."""
    facts = parse_legal_source(source)
    return _enumerated_clauses(facts) if facts else None


def enumeration_block(source: dict) -> str | None:
    """Render an article's own list verbatim, every item cited."""
    enumerated = source_enumeration(source)
    if not enumerated:
        return None
    lead, items = enumerated
    lines = [f"**{article_label(source)}.** {lead} {_citation(source)}"]
    lines.extend(f"- {item} {_citation(source)}" for item in items)
    return "\n".join(lines)


def _stem_set(value: str) -> set[str]:
    return {token[:5] for token in re.findall(r"[a-z0-9‘’']{5,}", value.lower())}


def missing_enumeration_items(answer: str, items: list[str]) -> list[str]:
    """Items of a statutory list the answer does not mention.

    An item counts as covered when most of its distinctive stems appear in the
    answer; exact wording is not required, the model may paraphrase.
    """
    answer_stems = _stem_set(answer)
    missing: list[str] = []
    for item in items:
        stems = [stem for stem in _stem_set(item)
                 if stem not in {"xo‘ja", "xo'ja", "yurit", "subye", "shaxs", "guruh", "bo‘ls", "bo'ls",
                                 "yoxud", "undan", "uchun", "bilan", "tomon"}]
        if not stems:
            continue
        hits = sum(stem in answer_stems for stem in stems)
        # Paraphrase is fine ("40 foiz" for "qirq foizni"): two shared stems, or 40% of
        # them, is enough evidence that the item was mentioned.
        if hits < min(2, len(stems)) and hits < len(stems) * 0.4:
            missing.append(item)
    return missing
