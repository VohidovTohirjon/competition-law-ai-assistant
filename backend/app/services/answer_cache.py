"""Small file-backed cache of validated chat answers.

A question that was already answered and grounded does not need another model call:
the same sources, the same validated text. This keeps repeated questions instant and,
more importantly on a rate-limited provider, keeps them from consuming the token budget
that a new question needs. Only fully successful outcomes are stored, and the key
includes the corpus size so a re-indexed corpus invalidates every entry.
"""

import hashlib
import json
import re
import threading
import time
from pathlib import Path

from ..config import get_settings

CACHE_VERSION = "v3"
TTL_SECONDS = 24 * 3600
MAX_ENTRIES = 400

_lock = threading.Lock()
_loaded: dict | None = None


def _path() -> Path:
    return Path(get_settings().data_dir) / "answer_cache.json"


def _load() -> dict:
    global _loaded
    if _loaded is None:
        try:
            _loaded = json.loads(_path().read_text("utf-8"))
        except (OSError, ValueError):
            _loaded = {}
    return _loaded


def _save(data: dict) -> None:
    try:
        path = _path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False), "utf-8")
    except OSError:
        pass


def normalize_question(question: str) -> str:
    text = question.lower().translate(str.maketrans({"’": "'", "‘": "'", "ʻ": "'", "`": "'", "ʼ": "'"}))
    return re.sub(r"[^\w%]+", " ", text).strip()


def cache_key(mode: str, question: str, corpus_signature: str) -> str:
    raw = f"{CACHE_VERSION}|{mode}|{normalize_question(question)}|{corpus_signature}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def get(key: str) -> dict | None:
    if not get_settings().answer_cache_enabled:
        return None
    with _lock:
        entry = _load().get(key)
        if not entry:
            return None
        if time.time() - entry.get("stored_at", 0) > TTL_SECONDS:
            _load().pop(key, None)
            return None
        return entry.get("outcome")


def put(key: str, outcome: dict) -> None:
    if not get_settings().answer_cache_enabled:
        return
    with _lock:
        data = _load()
        data[key] = {"stored_at": time.time(), "outcome": outcome}
        if len(data) > MAX_ENTRIES:
            for old in sorted(data, key=lambda item: data[item].get("stored_at", 0))[: len(data) - MAX_ENTRIES]:
                data.pop(old, None)
        _save(data)


def clear() -> None:
    global _loaded
    with _lock:
        _loaded = {}
        _save({})
