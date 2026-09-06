import re
from dataclasses import dataclass

from ..models import Chunk
from .rag import sources_from_chunks


MONTHS = (
    "yanvar", "fevral", "mart", "aprel", "may", "iyun", "iyul", "avgust",
    "sentabr", "oktabr", "noyabr", "dekabr",
)
DATE_RE = re.compile(
    rf"\b(\d{{1,2}})[-\s]({'|'.join(MONTHS)})(?:[-\s]+(\d{{4}})(?:[-\s]*yil)?)?\b",
    re.IGNORECASE,
)
STOP = {
    "ushbu", "hujjat", "joyda", "boshqa", "deb", "etildi", "qayd", "ekani", "bir",
    "topshiriqning", "hisobot", "kuni", "yil", "yakunida",
}


@dataclass(frozen=True)
class EvidenceStatement:
    text: str
    date: str
    chunk: Chunk


def _sentences(value: str) -> list[str]:
    return [part.strip() for part in re.split(r"(?<=[.!?])\s+|\n+", value) if part.strip()]


def _terms(statement: str) -> set[str]:
    without_dates = DATE_RE.sub(" ", statement.lower())
    return {
        token for token in re.findall(r"[a-zA-ZÀ-žʻ’‘'`]+", without_dates)
        if len(token) >= 4 and token not in STOP
    }


def _same_claim(a: str, b: str) -> bool:
    left, right = _terms(a), _terms(b)
    if {"yakuniy", "muddati"}.issubset(left) and {"yakuniy", "muddati"}.issubset(right):
        return True
    union = left | right
    return bool(union) and len(left & right) / len(union) >= 0.28


def analyze_contradictions(chunks: list[Chunk]) -> tuple[str, list[dict], dict]:
    """Detect verifiable conflicting date claims and return one structured finding per pair."""
    statements: list[EvidenceStatement] = []
    for chunk in chunks:
        for sentence in _sentences(chunk.text):
            match = DATE_RE.search(sentence)
            if match:
                date = f"{int(match.group(1))}-{match.group(2).lower()}-{match.group(3) or ''}"
                statements.append(EvidenceStatement(sentence, date, chunk))

    candidates: dict[tuple[str, str], list[tuple[EvidenceStatement, EvidenceStatement]]] = {}
    order: list[tuple[str, str]] = []
    for index, left in enumerate(statements):
        for right in statements[index + 1:]:
            if left.date == right.date or not _same_claim(left.text, right.text):
                continue
            # Exact substring verification is the final acceptance gate.
            if left.text not in left.chunk.text or right.text not in right.chunk.text:
                continue
            key = tuple(sorted((left.date, right.date)))
            if key not in candidates:
                candidates[key] = []
                order.append(key)
            candidates[key].append((left, right))
    findings: list[tuple[EvidenceStatement, EvidenceStatement]] = []
    for key in order:
        pairs = candidates[key]
        # Two different passages disagreeing is the evidence worth showing; a pair
        # inside one chunk is only used when the document offers nothing better.
        findings.append(next((pair for pair in pairs if pair[0].chunk.id != pair[1].chunk.id),
                             pairs[0]))

    used_chunks: list[Chunk] = []
    for left, right in findings:
        for chunk in (left.chunk, right.chunk):
            if chunk.id not in {item.id for item in used_chunks}:
                used_chunks.append(chunk)
    sources = sources_from_chunks(used_chunks)
    source_number = {chunk.id: index + 1 for index, chunk in enumerate(used_chunks)}

    structured_findings = []
    lines: list[str] = []
    if not findings:
        answer = "Hujjatda tekshiriladigan dalillar asosida aniq qarama-qarshilik topilmadi."
    else:
        lines.append("## Aniqlangan qarama-qarshiliklar")
        for number, (left, right) in enumerate(findings, 1):
            left_id, right_id = source_number[left.chunk.id], source_number[right.chunk.id]
            lines.extend([
                f"### {number}. Yakuniy muddatlar o‘zaro mos emas",
                f"- Birinchi bayon: “{left.text}” [{left_id}]",
                f"- Ikkinchi bayon: “{right.text}” [{right_id}]",
                "- Izoh: Bir topshiriq uchun ikki xil yakuniy muddat ko‘rsatilgan. "
                "Hujjatning o‘zida qaysi sana ustuvor ekani aniqlanmagan.",
            ])
            structured_findings.append({
                "title": "Yakuniy muddatlar o‘zaro mos emas",
                "statement_a": left.text,
                "source_a": left_id,
                "statement_b": right.text,
                "source_b": right_id,
                "explanation": (
                    "Bir topshiriq uchun ikki xil yakuniy muddat ko‘rsatilgan. "
                    "Hujjatning o‘zida qaysi sana ustuvor ekani aniqlanmagan."
                ),
            })
        answer = "\n".join(lines)
    return answer, sources, {"kind": "contradiction_analysis", "findings": structured_findings}
