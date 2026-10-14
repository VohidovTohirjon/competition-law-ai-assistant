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


def test_validator_accepts_latin_answer_over_cyrillic_years_acts_and_articles():
    """The answer is Latin while the statute is Cyrillic: a year, an act number and an
    inflected article reference that the evidence does contain must not be rejected."""
    from app.services.grounding import validate_legal_answer

    source = {
        "citation_number": 1,
        "document_name": "Raqobat to‘g‘risida (O‘RQ-850)",
        "article_or_clause": "45-модда. Айрим қонун ҳужжатларини ўз кучини йўқотган деб топиш",
        "full_excerpt": ("Ўзбекистон Республикасининг 2012 йил 6 январдаги ЎРҚ-319-сонли "
                         "Қонуни 12-моддасининг иккинчи қисми ўз кучини йўқотган деб топилсин."),
    }
    answer = ("2012 yil 6 yanvardagi O‘RQ-319-sonli Qonun 12-moddasining ikkinchi qismi "
              "o‘z kuchini yo‘qotgan deb topiladi [1].")
    result = validate_legal_answer(answer, [source])
    assert result.valid, result.violations

    invented = answer.replace("2012", "2014").replace("319", "391").replace("12-moddasi", "17-moddasi")
    rejected = validate_legal_answer(invented, [source])
    assert {"date_unsupported", "document_id_unsupported", "article_unsupported"} <= set(rejected.codes)


def test_figure_must_appear_as_a_whole_number_in_evidence():
    """"15" is not evidenced by "2015" or "815-sonli", nor 150 by "ikki yuz ellik" (250)."""
    from app.services.grounding import validate_legal_answer

    source = {
        "citation_number": 1,
        "document_name": "Raqobat to‘g‘risida (O‘RQ-850)",
        "article_or_clause": "42-modda. Moliyaviy sanksiyalar",
        "full_excerpt": ("2015 yil 20 avgustdagi 815-sonli qaror. Jarima bazaviy hisoblash "
                         "miqdorining ikki yuz ellik baravari miqdorida, ulush qirq foiz."),
    }
    ok = validate_legal_answer("Ulush 40 foiz, jarima 250 baravar [1].", [source])
    assert ok.valid, ok.violations
    for invented in ("Ulush 15 foiz [1].", "Jarima 150 baravar [1].", "Muddat 20 kun, ulush 81 foiz [1]."):
        result = validate_legal_answer(invented, [source])
        assert "number_unsupported" in result.codes, invented
