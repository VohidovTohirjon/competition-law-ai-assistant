import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from functools import lru_cache


CYRILLIC_TO_LATIN = str.maketrans({
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "yo",
    "ж": "j", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "x", "ц": "ts", "ч": "ch", "ш": "sh", "ъ": "'", "ь": "",
    "э": "e", "ю": "yu", "я": "ya", "қ": "q", "ғ": "g'", "ҳ": "h", "ў": "o'",
})

DOMINANCE_PATTERNS = (
    r"\bustun\s+(?:mavqe|holat|lik)\w*",
    r"\bdominant(?:lik|\s+(?:holat|mavqe|korxona|subyekt|sub'ekt))?\w*",
    r"\bbozor(?:dagi|da)?\s+(?:ustun(?:lik|lig)|hukmron(?:lik|lig))\w*",
    r"\bhukmron\s+(?:holat|mavqe)\w*",
)
AUTOMATIC_TERMS = ("avtomatik", "o'z-o'zidan", "darhol", "shartsiz", "yetarlimi", "hisoblanadimi")
CRITERIA_TERMS = (
    "mezon", "shart", "qaysi hollarda", "qachon", "aniqlash", "e'tirof",
    "hisoblanadi", "necha foiz", "foizdan boshlab",
)


@lru_cache(maxsize=2048)
def normalize_legal_text(value: str) -> str:
    return (re.sub(r"\s+", " ", value.lower().translate(CYRILLIC_TO_LATIN))
            .replace("’", "'").replace("‘", "'").replace("ʻ", "'")
            .replace("`", "'").replace("ʼ", "'").strip())


@dataclass(frozen=True)
class LegalConcepts:
    normalized: str
    dominant: bool
    abuse: bool
    negotiation_power: bool
    agreements: bool
    trade_restrictions: bool
    criteria: bool
    automatic: bool
    no_competitors: bool
    requested_share: Decimal | None

    @property
    def compares_dominance_and_negotiation(self) -> bool:
        return self.dominant and self.negotiation_power and any(
            term in self.normalized for term in ("bir xil", "farq", "nimasi")
        )

    @property
    def distinct_topics(self) -> tuple[str, ...]:
        """Materially distinct legal topics named in a single question.

        `dominant` is deliberately suppressed when the question is about abusing
        that position: "ustun mavqeni suiiste'mol qilish" names one topic (the
        prohibition), not two, and must keep resolving to a single article.
        """
        topics: list[str] = []
        if self.dominant and not self.abuse:
            topics.append("dominance")
        if self.abuse:
            topics.append("abuse")
        if self.negotiation_power:
            topics.append("negotiation")
        if self.agreements:
            topics.append("agreements")
        if self.trade_restrictions:
            topics.append("trade_restrictions")
        return tuple(topics)

    @property
    def is_compound(self) -> bool:
        """A question that legitimately spans more than one article."""
        return len(self.distinct_topics) > 1


@lru_cache(maxsize=1024)
def legal_concepts(value: str) -> LegalConcepts:
    """Parse the legal concepts named in a question.

    Memoized because the retrieval ranker re-scores the same query against every
    candidate chunk; the result is a frozen dataclass so sharing it is safe.
    """
    normalized = normalize_legal_text(value)
    dominant = any(re.search(pattern, normalized) for pattern in DOMINANCE_PATTERNS)
    negotiation = "ustun muzokara" in normalized
    abuse = any(term in normalized for term in ("suiiste'mol", "suiiste’mol"))
    agreements = any(term in normalized for term in
                     ("raqobatga qarshi kelishuv", "kelishuv", "muvofiqlashtirilgan",
                      "kartel", "narx til biriktir", "til biriktir"))
    trade = any(term in normalized for term in ("savdo", "tender", "xarid")) and any(
        term in normalized for term in ("raqobat", "chekl", "talab")
    )
    automatic = any(term in normalized for term in AUTOMATIC_TERMS)
    no_competitors = any(term in normalized for term in
                         ("raqobatchisi mavjud bo'lmagan", "raqobatchilar mavjud emas",
                          "raqobatchisi yo'q", "raqobatchilari yo'q"))
    share = None
    match = re.search(r"\b(\d{1,3}(?:[.,]\d+)?)\s*(?:%|foiz\b)", normalized)
    if match:
        try:
            share = Decimal(match.group(1).replace(",", "."))
        except InvalidOperation:
            share = None
    if share is not None and "bozor ulushi" in normalized:
        dominant = True
    criteria = dominant and (automatic or any(term in normalized for term in CRITERIA_TERMS))
    return LegalConcepts(
        normalized, dominant, abuse, negotiation, agreements, trade, criteria,
        automatic, no_competitors, share,
    )


def is_dominance_heading(value: str) -> bool:
    heading = normalize_legal_text(value)
    return ("ustun mavqe" in heading and "suiiste'mol" not in heading
            and "muzokara" not in heading)


def is_negotiation_heading(value: str) -> bool:
    # The article prohibiting abuse of superior bargaining power also names it in
    # its heading; excluding it keeps a definition question off the sanction norm,
    # mirroring `is_dominance_heading`.
    heading = normalize_legal_text(value)
    return "ustun muzokara" in heading and "suiiste'mol" not in heading


def is_abuse_heading(value: str) -> bool:
    return "suiiste'mol" in normalize_legal_text(value)


def is_agreement_heading(value: str) -> bool:
    heading = normalize_legal_text(value)
    return "kelishuv" in heading and "javobgarlik" not in heading


def is_trade_heading(value: str) -> bool:
    return "savdo" in normalize_legal_text(value)
