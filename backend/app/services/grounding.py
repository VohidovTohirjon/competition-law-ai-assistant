import re
from dataclasses import dataclass


APOSTROPHES = str.maketrans({
    "’": "'", "‘": "'", "ʻ": "'", "`": "'", "ʼ": "'",
    "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-",
})
CITATION_RE = re.compile(r"\[(\d+(?:\s*,\s*\d+)*)\]")
ARTICLE_RE = re.compile(
    r"\b(\d{1,3})\s*[-‐‑‒–—.]?\s*(modda|modd|modde|модда|статья)(?:si|ning|da)?\b",
    re.IGNORECASE,
)
DOCUMENT_ID_RE = re.compile(
    r"\b(?:O['‘’ʻʼ`]?RQ|ЎРҚ)\s*[-‐‑‒–—]?\s*\d+[A-Za-zА-Яа-я-]*\b|№\s*[\dIVXLCDMА-ЯA-Z-]+",
    re.IGNORECASE,
)
LEGAL_YEAR_RE = re.compile(
    r"\b(?:19|20)\d{2}\s*[-‐‑‒–—]?\s*yil\b(?=[^.!?\n]{0,70}(?:qonun|O['‘’ʻʼ`]?RQ|№))",
    re.IGNORECASE,
)
QUOTED_RE = re.compile(r"[“\"]([^”\"]{20,500})[”\"]")
LEGAL_TITLE_RE = re.compile(
    r"(?:O['‘’ʻʼ`]?zbekiston\s+Respublikasi\s+)?"
    r"([A-ZОЎҚҒҲ][^.!?\n]{2,110}?(?:to['‘’ʻʼ`]?g['‘’ʻʼ`]?risidagi\s+qonun|qonuni)\b)"
)


# Machine-readable violation kinds. A "repairable" failure means the evidence itself
# is sound and only the presentation went wrong, so one cheap correction round is
# worth its latency. Everything else means the model asserted something the evidence
# does not support; re-prompting is unlikely to help and the verified extractive
# answer is both faster and safer.
REPAIRABLE_VIOLATIONS = frozenset({
    "schema_invalid", "coverage_incomplete", "citation_missing", "article_uncited",
    # The figure IS in the displayed evidence, just under a different citation number:
    # a presentation error one correction round can fix.
    "citation_misassigned",
})


@dataclass(frozen=True)
class GroundingValidation:
    valid: bool
    answer: str
    used_citation_ids: tuple[int, ...]
    violations: tuple[str, ...]
    codes: tuple[str, ...] = ()

    @property
    def repairable(self) -> bool:
        """True when every failure is a presentation problem, not a factual one."""
        return bool(self.codes) and all(code in REPAIRABLE_VIOLATIONS for code in self.codes)


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.translate(APOSTROPHES).lower()).strip()


def normalize_generated_terminology(answer: str) -> str:
    """Normalize only assistant prose; authentic source excerpts are never passed here."""
    return re.sub(
        r"\b(\d{1,3})\s*[-‐‑‒–—.]?\s*(?:modda|modd|modde)\b",
        lambda match: f"{match.group(1)}-modda",
        answer,
        flags=re.IGNORECASE,
    )


def article_label(source: dict) -> str:
    raw = source.get("article_or_clause") or ""
    match = re.match(r"\s*(\d{1,3})\s*[-‐‑‒–—.]?\s*(?:modda|модда|статья)\b", raw, re.IGNORECASE)
    if match:
        return f"{match.group(1)}-modda"
    band = re.search(r"\b(\d{1,3})\s*[-‐‑‒–—.]?\s*(?:band|банд)\b", raw, re.IGNORECASE)
    if band:
        return f"{band.group(1)}-band"
    return "Modda yoki band aniqlanmagan"


def _citations(answer: str) -> tuple[int, ...]:
    result: list[int] = []
    for match in CITATION_RE.finditer(answer):
        for value in match.group(1).split(","):
            number = int(value.strip())
            if number not in result:
                result.append(number)
    return tuple(result)


def _evidence_of(source: dict) -> str:
    """The complete stored evidence for a source.

    The model is prompted with `full_excerpt` (the whole article/chunk), while the
    700-character `excerpt` is only the card preview. Validating against the preview
    rejected correct answers whose figure sat past the preview cut (13-modda's
    "40 foiz" threshold, 27-modda's turnover amounts).
    """
    return source.get("full_excerpt") or source.get("excerpt") or ""


def _allowed_article_numbers(sources: list[dict]) -> set[int]:
    result: set[int] = set()
    for source in sources:
        raw = f"{source.get('article_or_clause') or ''}\n{_evidence_of(source)}"
        for match in ARTICLE_RE.finditer(raw):
            result.add(int(match.group(1)))
    return result


def _allowed_document_ids(sources: list[dict]) -> set[str]:
    values: set[str] = set()
    for source in sources:
        raw = f"{source.get('document_name') or ''}\n{_evidence_of(source)}"
        values.update(normalize_text(match.group(0)) for match in DOCUMENT_ID_RE.finditer(raw))
    return values


def _title_tokens(value: str) -> set[str]:
    ignored = {"ozbekiston", "respublikasi", "qonun", "qonuni", "togrisidagi", "togrisida"}
    simplified = normalize_text(value).replace("o'zbekiston", "ozbekiston").replace("to'g'risidagi", "togrisidagi").replace("to'g'risida", "togrisida")
    return {token for token in re.findall(r"[a-z0-9]+", simplified) if len(token) > 2 and token not in ignored}


def _title_is_allowed(candidate: str, sources: list[dict]) -> bool:
    candidate_tokens = _title_tokens(candidate)
    if not candidate_tokens:
        return False
    for source in sources:
        allowed_tokens = _title_tokens(source.get("document_name") or "")
        if candidate_tokens <= allowed_tokens:
            return True
    return False


def _quote_is_supported(quote: str, answer: str, sources_by_id: dict[int, dict]) -> bool:
    position = answer.find(quote)
    nearby = answer[position + len(quote):position + len(quote) + 24]
    cited = _citations(nearby)
    if not cited:
        return True  # Not asserted as a legal quotation; prose quotes are allowed.
    normalized_quote = normalize_text(quote)
    return any(
        source_id in sources_by_id
        and normalized_quote in normalize_text(_evidence_of(sources_by_id[source_id]))
        for source_id in cited
    )


# Enumerators ("1.", "2)", "3." at a line start or inline inside one paragraph)
# structure an answer; they assert nothing about the evidence and must not be
# mistaken for an unsupported figure.
INLINE_ENUMERATOR_RE = re.compile(r"(?:(?<=^)|(?<=\s)|(?<=[;:]))\(?\d{1,2}[.)](?=\s)")


def _strip_structure_numbers(prose: str) -> str:
    prose = re.sub(r"(?m)^\s*#{1,6}\s*", "", prose)
    prose = re.sub(r"(?m)^\s*(?:[-*•]\s*)?\d+(?:\.\d+)*(?:[.)]\s*|\s+)", "", prose)
    return INLINE_ENUMERATOR_RE.sub("", prose)


_UNITS_WORDS = {1: "bir", 2: "ikki", 3: "uch", 4: "to'rt", 5: "besh", 6: "olti", 7: "yetti",
                8: "sakkiz", 9: "to'qqiz"}
_TENS_WORDS = {10: "o'n", 20: "yigirma", 30: "o'ttiz", 40: "qirq", 50: "ellik", 60: "oltmish",
               70: "yetmish", 80: "sakson", 90: "to'qson"}


def _number_in_words(value: int) -> str | None:
    """Uzbek words for a whole number up to 999 999 ("o'ttiz ming", "qirq")."""
    if value <= 0 or value >= 1_000_000:
        return None

    def below_thousand(n: int) -> list[str]:
        parts: list[str] = []
        hundreds, rest = divmod(n, 100)
        if hundreds:
            parts.append(("" if hundreds == 1 else _UNITS_WORDS[hundreds] + " ") + "yuz")
        tens, units = divmod(rest, 10)
        if tens:
            parts.append(_TENS_WORDS[tens * 10])
        if units:
            parts.append(_UNITS_WORDS[units])
        return parts

    thousands, rest = divmod(value, 1000)
    words: list[str] = []
    if thousands:
        words.extend(([] if thousands == 1 else below_thousand(thousands)) + ["ming"])
    words.extend(below_thousand(rest))
    return " ".join(words)


def _compact_digits(text: str) -> str:
    """"30 000" / "250 000" are one figure, not a 30 and a 000."""
    return re.sub(r"(?<=\d)[ \u00a0\u202f](?=\d{3}\b)", "", text)


def _number_supported(number: str, normalized_evidence: str, latin_evidence: str) -> bool:
    if number in normalized_evidence:
        return True
    # Official texts often spell figures out ("қирқ фоиз", "ўттиз минг баравари");
    # the model legitimately renders them as digits.
    if number.isdigit():
        words = _number_in_words(int(number))
        if words and words in latin_evidence:
            return True
    return False


def _unsupported_numbers(answer: str, evidence: str) -> list[str]:
    prose = CITATION_RE.sub("", answer)
    prose = ARTICLE_RE.sub("", prose)
    prose = re.sub(r"\b\d{1,3}\s*[-‐‑‒–—.]?\s*(?:band|банд|qism|қисм|bo‘lim|bo'lim)\w*", "", prose,
                   flags=re.IGNORECASE)
    prose = DOCUMENT_ID_RE.sub("", prose)
    prose = _compact_digits(_strip_structure_numbers(prose))
    claimed = set(re.findall(r"\b\d+(?:[.,]\d+)?\b", prose))
    normalized_evidence = _compact_digits(normalize_text(evidence))
    latin_evidence = normalize_text(_map_cyrillic(evidence)).replace("‘", "'").replace("’", "'")
    return sorted(number for number in claimed
                  if not _number_supported(number, normalized_evidence, latin_evidence))


def validate_legal_answer(answer: str, sources: list[dict],
                          additional_evidence: str = "") -> GroundingValidation:
    checked = normalize_generated_terminology(answer.strip())
    sources_by_id = {source["citation_number"]: source for source in sources}
    allowed_ids = set(sources_by_id)
    used_ids = _citations(checked)
    violations: list[str] = []
    codes: list[str] = []

    def fail(code: str, message: str) -> None:
        codes.append(code)
        violations.append(message)

    invalid_citations = sorted(set(used_ids) - allowed_ids)
    if invalid_citations:
        fail("citation_invalid",
             "Mavjud bo‘lmagan citation: " + ", ".join(map(str, invalid_citations)))
    if sources and not used_ids:
        fail("citation_missing", "Javobda tekshirilgan manba citation’i yo‘q")

    for line in checked.splitlines():
        if re.fullmatch(r"\s*(?:\*\*[^*]+\*\*:?|#{1,4}\s.*)", line.rstrip()):
            continue  # a bare heading such as "**Huquqiy asos**" carries no claim
        if ARTICLE_RE.search(line) and not _citations(line):
            fail("article_uncited", "Modda ko‘rsatilgan bandning o‘zida citation yo‘q")

    allowed_articles = _allowed_article_numbers(sources)
    generated_articles = {int(match.group(1)) for match in ARTICLE_RE.finditer(checked)}
    invented_articles = sorted(generated_articles - allowed_articles)
    if invented_articles:
        fail("article_unsupported",
             "Dalilda yo‘q modda: " + ", ".join(map(str, invented_articles)))

    allowed_document_ids = _allowed_document_ids(sources)
    generated_document_ids = {normalize_text(match.group(0)) for match in DOCUMENT_ID_RE.finditer(checked)}
    invented_document_ids = sorted(generated_document_ids - allowed_document_ids)
    if invented_document_ids:
        fail("document_id_unsupported",
             "Dalilda yo‘q hujjat raqami: " + ", ".join(invented_document_ids))

    for match in LEGAL_YEAR_RE.finditer(checked):
        year = normalize_text(match.group(0))
        if not any(year in normalize_text(_evidence_of(source)) for source in sources):
            fail("date_unsupported", f"Dalilda yo‘q huquqiy sana: {year}")

    for match in LEGAL_TITLE_RE.finditer(checked):
        candidate = match.group(0).strip(" ,:;\"“”")
        if not _title_is_allowed(candidate, sources):
            fail("title_unsupported", f"Dalilda yo‘q hujjat nomi: {candidate}")

    for match in QUOTED_RE.finditer(checked):
        if not _quote_is_supported(match.group(1), checked, sources_by_id):
            fail("quote_unsupported", "Citation bilan berilgan iqtibos manba parchasida topilmadi")

    evidence_text = additional_evidence + "\n" + "\n".join(
        f"{source.get('document_name') or ''} {source.get('article_or_clause') or ''} "
        f"{_evidence_of(source)}" for source in sources
    )
    unsupported_numbers = _unsupported_numbers(checked, evidence_text)
    if unsupported_numbers:
        fail("number_unsupported", "Dalillarda yo‘q raqam: " + ", ".join(unsupported_numbers))

    return GroundingValidation(
        valid=not violations,
        answer=checked,
        used_citation_ids=tuple(value for value in used_ids if value in allowed_ids),
        violations=tuple(dict.fromkeys(violations)),
        codes=tuple(dict.fromkeys(codes)),
    )


def used_sources(sources: list[dict], citation_ids: tuple[int, ...]) -> list[dict]:
    by_id = {source["citation_number"]: source for source in sources}
    return [by_id[value] for value in citation_ids if value in by_id]


def validate_cited_answer(answer: str, sources: list[dict]) -> GroundingValidation:
    """Validate ordinary document analysis without applying legal-title heuristics."""
    checked = normalize_generated_terminology(answer.strip())
    allowed_ids = {source["citation_number"] for source in sources}
    used_ids = _citations(checked)
    violations: list[str] = []
    codes: list[str] = []
    invalid = sorted(set(used_ids) - allowed_ids)
    if invalid:
        codes.append("citation_invalid")
        violations.append("Mavjud bo‘lmagan citation: " + ", ".join(map(str, invalid)))
    if sources and not used_ids:
        codes.append("citation_missing")
        violations.append("Javobda hujjat parchasiga citation yo‘q")
    sources_by_id = {source["citation_number"]: source for source in sources}
    # Markdown commonly places `[1]` after the sentence-ending period. Validate at
    # line/list-item scope so that citation-only fragments cannot evade number checks.
    for statement in checked.splitlines():
        cited = _citations(statement)
        if not cited:
            continue
        without_citations = _compact_digits(_strip_structure_numbers(CITATION_RE.sub("", statement)))
        claimed_numbers = set(re.findall(r"\b\d+(?:[.,]\d+)?\b", without_citations))
        if not claimed_numbers:
            continue
        evidence = _compact_digits(" ".join(
            normalize_text(_evidence_of(sources_by_id[source_id]))
            for source_id in cited if source_id in sources_by_id
        ))
        latin_evidence = normalize_text(_map_cyrillic(evidence))
        unsupported = sorted(number for number in claimed_numbers
                             if not _number_supported(number, evidence, latin_evidence))
        if unsupported:
            shown = _compact_digits(" ".join(normalize_text(_evidence_of(source))
                                             for source in sources))
            shown_latin = normalize_text(_map_cyrillic(shown))
            elsewhere = all(_number_supported(number, shown, shown_latin)
                            for number in unsupported)
            codes.append("citation_misassigned" if elsewhere else "number_unsupported")
            violations.append(
                "Citation qilingan parchada raqam topilmadi: " + ", ".join(unsupported)
            )
    return GroundingValidation(
        valid=not violations,
        answer=checked,
        used_citation_ids=tuple(value for value in used_ids if value in allowed_ids),
        violations=tuple(violations),
        codes=tuple(dict.fromkeys(codes)),
    )


def repair_document_citations(answer: str, sources: list[dict]) -> str:
    """Deterministically remap a numeric claim only when one evidence excerpt uniquely supports it."""
    repaired: list[str] = []
    for line in answer.splitlines():
        cited = _citations(line)
        numbers = set(re.findall(r"\b\d+(?:[.,]\d+)?\b",
                                 _strip_structure_numbers(CITATION_RE.sub("", line))))
        if not cited or not numbers:
            repaired.append(line)
            continue
        candidates = []
        for source in sources:
            evidence = normalize_text(_evidence_of(source))
            if all(number in evidence for number in numbers):
                candidates.append(source["citation_number"])
        if len(candidates) == 1 and candidates[0] not in cited:
            line = CITATION_RE.sub(f"[{candidates[0]}]", line)
        repaired.append(line)
    return "\n".join(repaired)


CYRILLIC_LATIN = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "yo",
    "ж": "j", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "x", "ц": "ts", "ч": "ch", "ш": "sh", "ъ": "'", "ь": "",
    "э": "e", "ю": "yu", "я": "ya", "қ": "q", "ғ": "g'", "ҳ": "h", "ў": "o'",
}


CYRILLIC_RE = re.compile("[" + "".join(CYRILLIC_LATIN) + "]", re.IGNORECASE)


def _map_cyrillic(value: str) -> str:
    result: list[str] = []
    for char in value:
        mapped = CYRILLIC_LATIN.get(char.lower())
        if mapped is None:
            result.append(char)
        elif char.isupper():
            result.append(mapped[:1].upper() + mapped[1:])
        else:
            result.append(mapped)
    return "".join(result)


# Cyrillic "ц" transliterates to "ts", which spells Uzbek loanwords wrong
# ("санкция" -> "sanktsiya" instead of "sanksiya").
_TS_LOANWORDS = ((r"(?<=[a-z])ktsiya", "ksiya"), (r"kontsentratsiya", "konsentratsiya"),
                 (r"initsiativ", "tashabbus"), (r"pretenziya", "da’vo"))


def _polish_latin(text: str) -> str:
    for pattern, replacement in _TS_LOANWORDS:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    text = re.sub(r"(?<=\w)'(?=\w)", "’", text)
    text = (text.replace("o’", "o‘").replace("O’", "O‘")
            .replace("g’", "g‘").replace("G’", "G‘"))
    return (text.replace("sub’ekt", "subyekt").replace("Sub’ekt", "Subyekt")
            .replace("ob’ekt", "obyekt").replace("Ob’ekt", "Obyekt"))


def _latin_explanation(value: str) -> str:
    """Transliterate assistant explanation only; source cards retain official script."""
    value = re.sub(r"[\u200b\u200c\u200d\ufeff]", "", value)
    return _polish_latin(re.sub(r"\s+", " ", _map_cyrillic(value)).strip())


# Misspellings and stray English observed in generated Uzbek legal prose. Purely
# lexical, applied after grounding validation, so no claim can change meaning.
GENERATED_TEXT_FIXES = (
    (r"\binsolent\b", "insofsiz"),
    (r"\bva[’'`]?olatli\b|\bvaqolatli\b", "vakolatli"),
    (r"\bmanzarod(ga|ning|ni)?\b", "mansabdor shaxs\1"),
    (r"\bbirleshma(lar)?(ga|ning|ni|si)?\b", "birlashma\1\2"),
    (r"\bxujjat", "hujjat"),
    (r"\bxojalik|\bxo[’'`]jalik|\bhoxjallik|\bxojayl?ik", "xo‘jalik"),
    (r"\bmuzoqara\b", "muzokara"),
    (r"\bsubyektaga\b", "subyektga"),
    (r"\btashkotchi", "tashkilotchi"),
    (r"\bVazirler\b", "Vazirlar"),
    (r"\bkontsentratsiya", "konsentratsiya"),
    (r"\bauktsion", "auksion"),
    (r"\bustat fond|\busta fond", "ustav fondi"),
    (r"asosiy hisoblash miqdor", "bazaviy hisoblash miqdor"),
    (r"\braqobatlaash", "raqobatlash"),
    (r"\buston mavqe", "ustun mavqe"),
    (r"\btabiiiy\b", "tabiiy"),
    (r"\bO[’'`]tkir kalendar", "Oxirgi kalendar"),
    (r"\bnoto[‘’'`]g[‘’'`]ri raqobat", "insofsiz raqobat"),
    (r"\bxususiy subyekt", "xo‘jalik yurituvchi subyekt"),
)


def _fix_generated_terminology(text: str) -> str:
    for pattern, replacement in GENERATED_TEXT_FIXES:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    return re.sub(r"\s+([,.;:])", r"\1", text)


def latin_legal_answer(value: str) -> str:
    """Guarantee Latin-script prose for generated legal answers.

    The official NHH corpus is stored in Uzbek Cyrillic and the provider sometimes
    mirrors that script back despite the instruction to answer in Latin, which left
    generated answers inconsistent with the extractive fallback for the same
    question. Transliteration is a deterministic 1:1 script mapping applied only
    after grounding validation has passed, so it cannot introduce a new claim.
    Line structure and citation markers are preserved, and the source cards keep
    the official script. Scoped to the legal corpus on purpose: document analysis
    and general chat run on user-supplied text whose language is not ours to recast.
    """
    # `_polish_latin` runs on every answer, not only Cyrillic-tainted ones: the
    # apostrophe and terminology cleanup is needed for pure-Latin output too.
    return "\n".join(_fix_generated_terminology(_polish_latin(_map_cyrillic(line)))
                      for line in value.splitlines())


def _complete_excerpt(value: str, max_chars: int = 620) -> str:
    cleaned = re.sub(r"^\s*\d+\s*[-.]?\s*(?:modda|модда|статья)\.?\s*", "", value,
                     flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -:;")
    if len(cleaned) <= max_chars:
        return cleaned
    window = cleaned[:max_chars + 1]
    boundary = max(window.rfind(". "), window.rfind("; "))
    if boundary < max_chars // 2:
        boundary = window.rfind(" ")
    return window[:boundary if boundary > 0 else max_chars].rstrip(" ,;:") + "."


def _criteria_block(source: dict, question: str) -> str | None:
    """Render an article's own enumerated list when its text encodes one.

    Used by the extractive fallback: when generation cannot be grounded, the verified
    list itself is the most useful and the safest answer.
    """
    full = _strip_heading(source.get("full_excerpt") or source.get("excerpt") or "", source)
    normalized_full = re.sub(r"\s+", " ", full).strip()
    marker = re.search(r"(?:quyidagilar|қуйидагилар|quyidagi|қуйидаги)[^:]{0,160}:",
                       normalized_full, re.IGNORECASE)
    if not marker:
        return None
    tail = normalized_full[marker.end():]
    raw_items = [item.strip(" -:;.") for item in re.split(r";", tail) if item.strip()]
    items = []
    for item in raw_items[:12]:
        explanation_item = _latin_explanation(_complete_excerpt(item, 430))
        # A semicolon-separated criterion ends at its first complete
        # sentence; subsequent article paragraphs are not list items.
        if ". " in explanation_item:
            explanation_item = explanation_item.split(". ", 1)[0].rstrip() + "."
        items.append(explanation_item)
    items = [item for item in items if len(item) > 18]
    if not items:
        return None
    lead = _latin_explanation(normalized_full[:marker.end()])
    lines = [f"**{article_label(source)}.** {lead} [{source['citation_number']}]"]
    lines.extend(f"- {item[:1].lower() + item[1:]} [{source['citation_number']}]" for item in items)
    return "\n".join(lines)


def _strip_heading(full: str, source: dict) -> str:
    heading = re.sub(r"\s+", " ", source.get("article_or_clause") or "").strip()
    body = re.sub(r"\s+", " ", full).strip()
    if heading and body.lower().startswith(heading.lower()):
        body = body[len(heading):].lstrip(" .:;-")
    return body


def _excerpt_block(source: dict) -> str:
    """State one article's own wording, verbatim-derived and citation-bound."""
    full = _strip_heading(source.get("full_excerpt") or source.get("excerpt") or "", source)
    label = article_label(source)
    explanation = _latin_explanation(_complete_excerpt(full))
    prefix = f"{label}ga ko‘ra, " if label != "Modda yoki band aniqlanmagan" else "Manbaga ko‘ra, "
    if explanation:
        explanation = explanation[:1].lower() + explanation[1:]
    return f"{prefix}{explanation} [{source['citation_number']}]"


def extractive_legal_fallback(sources: list[dict], reason: str | None = None,
                              question: str = "") -> tuple[str, list[dict]]:
    """Answer from verified evidence; reason is intentionally kept out of user prose.

    Every supplied source is rendered and cited. The sources reaching this point
    have already been topic-filtered, so silently keeping only the first one would
    discard verified evidence — which is what made a compound question ("dominant
    korxona raqobatga qarshi kelishuv qilsa...") collapse from two articles to one
    whenever the provider was unavailable. No content beyond the stored article
    text is ever introduced here.
    """
    if not sources:
        return "Mavjud bazada savolga yetarli huquqiy asos topilmadi.", []
    blocks = [_criteria_block(source, question) or _excerpt_block(source) for source in sources]
    return "\n\n".join(blocks), list(sources)


def extractive_document_fallback(sources: list[dict], reason: str | None = None) -> tuple[str, list[dict]]:
    lines = ["## Hujjatdan tekshirilgan parchalar"]
    if reason:
        lines.extend(["", f"> {reason}"])
    for source in sources:
        citation = source["citation_number"]
        excerpt = re.sub(r"\s+", " ", source.get("excerpt") or "").strip()
        location = source.get("display_label") or source.get("article_or_clause") or "Hujjat parchasi"
        lines.extend(["", f"- **{location}** — {excerpt[:520]} [{citation}]"])
    return "\n".join(lines), list(sources)
