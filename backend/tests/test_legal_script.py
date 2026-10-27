"""The generated legal answer must reach the user in Uzbek Latin.

The official NHH corpus is stored in Cyrillic, so a provider that mirrors the
source script back produced Cyrillic answers for Latin questions while the
extractive fallback for the very same question answered in Latin.
"""

import pytest

from app.services.ai_agent import generate_grounded_legal
from app.services.grounding import (CYRILLIC_RE, extractive_legal_fallback,
                                    latin_legal_answer)
from app.services.llm import llm

SOURCES = [{
    "citation_number": 1,
    "document_id": "n1",
    "document_name": "Raqobat to‘g‘risida (O‘RQ-850)",
    "article_or_clause": "13-модда. Устун мавқе",
    "display_label": "13-modda",
    "url": "https://lex.uz/docs/6518381",
    "excerpt": "13-модда. Устун мавқе деб эътироф этиш мезонлари бозордаги улуш билан белгиланади.",
    "full_excerpt": "13-модда. Устун мавқе деб эътироф этиш мезонлари бозордаги улуш билан белгиланади.",
    "evidence_type": "nhh",
}]


def test_transliteration_preserves_layout_citations_and_latin_only_text():
    source = "Устун мавқе — бозордаги ҳолат [1]\n\n- Иккинчи банд [1]"
    result = latin_legal_answer(source)
    assert not CYRILLIC_RE.search(result)
    assert result.splitlines() == [
        "Ustun mavqe — bozordagi holat [1]", "", "- Ikkinchi band [1]",
    ]
    # Text that is already Latin is returned untouched, byte for byte.
    latin = "13-modda bo‘yicha shartlar [1]\n\n- Ikkinchi band [1]"
    assert latin_legal_answer(latin) == latin


@pytest.mark.anyio
async def test_generated_legal_answer_is_latin_even_when_provider_mirrors_cyrillic(monkeypatch):
    async def cyrillic_provider(_system: str, _user: str, _schema: dict, **_options) -> dict:
        return {"answer_blocks": [{
            "text": "Устун мавқе бозордаги улуш билан эътироф этилади",
            "source_ids": ["L1"],
        }]}

    monkeypatch.setattr(llm, "generate_structured", cyrillic_provider)
    result = await generate_grounded_legal("system", "prompt", SOURCES,
                                           question="ustun mavqe mezonlari")
    assert result.result_kind == "ok"
    assert not CYRILLIC_RE.search(result.answer)
    assert "Ustun mavqe" in result.answer
    assert "[1]" in result.answer


def test_extractive_fallback_for_the_same_source_is_latin_too():
    answer, used = extractive_legal_fallback(SOURCES, question="ustun mavqe mezonlari")
    assert used == [SOURCES[0]]
    assert not CYRILLIC_RE.search(answer)


def test_source_cards_keep_the_official_cyrillic_script():
    """Transliteration is for prose only; the evidence card stays authentic."""
    _, used = extractive_legal_fallback(SOURCES, question="ustun mavqe mezonlari")
    assert CYRILLIC_RE.search(used[0]["excerpt"])
    assert used[0]["article_or_clause"] == "13-модда. Устун мавқе"


def test_misspelt_yurituvchi_is_corrected():
    assert latin_legal_answer("xo‘jalik yo‘rituvchi subyekt") == "xo‘jalik yurituvchi subyekt"
    assert latin_legal_answer("Xo‘jalik Yo'rituvchi") == "Xo‘jalik Yurituvchi"


def test_insafsiz_is_corrected_to_the_statute_term():
    assert latin_legal_answer("Insafsiz raqobat taqiqlanadi") == "Insofsiz raqobat taqiqlanadi"
