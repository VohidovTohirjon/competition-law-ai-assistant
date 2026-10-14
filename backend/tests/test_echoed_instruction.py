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
