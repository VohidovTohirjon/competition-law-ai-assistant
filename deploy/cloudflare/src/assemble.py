"""Builds site/index.html from src/index.src.html, the real answers in volume.json and facts.json."""
import json
from pathlib import Path

here = Path(__file__).resolve().parent
html = (here / "index.src.html").read_text("utf-8")
volume = json.dumps(json.loads((here / "volume.json").read_text("utf-8")), ensure_ascii=False).replace("</", "<\\/")
facts = json.loads((here / "facts.json").read_text("utf-8"))
html = html.replace("__VOLUME__", volume).replace("__TESTS__", facts["tests"])
(here.parent / "site" / "index.html").write_text(html, "utf-8")
print("site/index.html", len(html) // 1024, "KB")
