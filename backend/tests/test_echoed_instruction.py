from app.services.ai_agent import strip_echoed_instruction


def test_echoed_task_heading_is_dropped():
    text = ("Qisqacha mazmun (faktlarga sodiq, har bir faktdan keyin [1] citation):\n\n"
            "- “ORIENT SAVDO TEST” MChJ vakili murojaat qilgan [1].")
    assert strip_echoed_instruction(text) == "- “ORIENT SAVDO TEST” MChJ vakili murojaat qilgan [1]."


def test_real_content_heading_is_kept():
    text = "**Qisqacha mazmun**\n\n- Murojaat 2026-yil iyulga oid [1]."
    assert strip_echoed_instruction(text) == text


def test_bold_echoed_heading_is_dropped():
    text = ("**Qisqacha mazmun (faktlarga sodiq, har bir faktdan keyin [1] citation):**\n\n"
            "- “ORIENT SAVDO TEST” MChJ vakili 2026‑yil 3‑iyuldan [1].")
    assert strip_echoed_instruction(text).startswith("- “ORIENT")


def test_text_fixes_are_idempotent_for_cached_answers():
    from app.services.grounding import latin_legal_answer
    text = "**13-modda.** Xo‘jalik yo‘rituvchi subyekt ustun mavqe deb e’tirof etiladi: [1]\n- 40 foiz [1]"
    once = latin_legal_answer(text)
    assert once == latin_legal_answer(once)
    assert "yurituvchi" in once and "[1]" in once


def test_match_reason_is_plain_and_bounded():
    from types import SimpleNamespace
    from app.services.rag import match_reason
    assert match_reason(SimpleNamespace()) is None
    chunk = SimpleNamespace(match_info={"semantic": 0.85, "intent": 0.2, "title": 0.0, "article": False})
    assert match_reason(chunk) == "Savoldagi huquqiy tushuncha shu moddaning mavzusiga mos (ma’no yaqinligi 85%)"
    chunk = SimpleNamespace(match_info={"semantic": 1.7, "intent": 0, "title": 0, "article": True})
    assert match_reason(chunk).endswith("(ma’no yaqinligi 100%)")
