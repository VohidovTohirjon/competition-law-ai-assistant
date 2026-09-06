import os
import re
import sys
from pathlib import Path

import pytest

TEST_ROOT = Path(__file__).parent
RUN_ID = str(os.getpid())
DB_PATH = Path("/tmp") / f"raqobat-tests-{RUN_ID}.db"
DATA_PATH = Path("/tmp") / f"raqobat-tests-data-{RUN_ID}"
os.environ.update({
    "DATABASE_URL": f"sqlite:///{DB_PATH}",
    "SECRET_KEY": "test-secret-key-that-is-longer-than-thirty-two-characters",
    "EMBEDDING_BACKEND": "hash",
    "EMBEDDING_DIMENSIONS": "64",
    "RETRIEVAL_MIN_SCORE": "0.48",
    "DATA_DIR": str(DATA_PATH),
    "GROQ_API_KEY": "test-key",
    "GROQ_MODEL": "openai/gpt-oss-20b",
    # Pin the code default so a developer's .env tuning cannot leak into the suite.
    "GROQ_REASONING_EFFORT": "low",
    "LLM_MAX_TOKENS_GENERAL": "512",
    "LLM_MAX_TOKENS_LEGAL": "1024",
    "CONTEXT_MAX_CHARS": "18000",
    # Tests monkeypatch the model per case; a reused answer would cross-contaminate them.
    "ANSWER_CACHE_ENABLED": "false",
})
sys.path.insert(0, str(TEST_ROOT.parent))

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.database import Base, SessionLocal, engine
from app.main import app
from app.models import Role, User
from app.security import hash_password
from app.services.llm import llm


@pytest.fixture(autouse=True)
def clean_database(monkeypatch):
    llm.reset_runtime_state()
    engine.dispose()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    DATA_PATH.mkdir(parents=True, exist_ok=True)

    async def fake_generate(system: str, user: str, **_options) -> str:
        if "qarama-qarshi" in system.lower():
            if "15-avgust" in user and "20-avgust" in user:
                return "Qarama-qarshilik: topshirish sanasi bir joyda 15-avgust, boshqa joyda 20-avgust deb ko‘rsatilgan [1]."
            return "Hujjatda mazmuniy qarama-qarshilik topilmadi [1]."
        if "asosiy band" in system.lower():
            return "1. Birinchi muhim band [1].\n2. Ikkinchi muhim band [1]."
        if "qisqacha" in system.lower():
            return "Hujjatning qisqacha mazmuni tayyorlandi [1]."
        return "Berilgan manbalar asosida tekshirilgan javob [1]."

    async def fake_generate_structured(system: str, user: str, schema: dict,
                                       **_options) -> dict:
        # Invoke the currently installed generator so per-test provider/error
        # monkeypatches still exercise the same degradation path.
        raw = await llm.generate(system, user)
        if "appeal_summary" in schema.get("properties", {}):
            return {
                "subject": "Murojaatni ko‘rib chiqish natijalari haqida",
                "salutation": "Hurmatli murojaat etuvchi!",
                "appeal_summary": ["Murojaatda bayon etilgan holatlar ko‘rib chiqildi."],
                "legal_basis": [{"statement": "Tegishli norma doirasida huquqiy baho beriladi.",
                                  "source_ids": ["L1"]}],
                "conclusion": ["Yakuniy baho faqat tasdiqlangan holatlarga ko‘ra beriladi."],
                "closing": "[Ism]\n[Lavozim]\n[Tashkilot]",
            }
        cited = re.findall(r"\[(\d+)\]", raw)
        return {"answer_blocks": [{"text": re.sub(r"\s*\[[0-9, ]+\]", "", raw).strip(),
                                    "source_ids": [f"L{value}" for value in cited] or ["L1"]}]}
    monkeypatch.setattr(llm, "generate", fake_generate)
    monkeypatch.setattr(llm, "generate_structured", fake_generate_structured)
    with SessionLocal() as db:
        db.add_all([
            User(username="admin", full_name="Admin", password_hash=hash_password("Admin123!"), role=Role.administrator),
            User(username="rahbar", full_name="Rahbar", password_hash=hash_password("Rahbar123!"), role=Role.rahbar),
            User(username="xodim", full_name="Xodim", password_hash=hash_password("Xodim123!"), role=Role.xodim),
            User(username="other", full_name="Boshqa xodim", password_hash=hash_password("Other123!"), role=Role.xodim),
        ])
        db.commit()
    yield
    llm.reset_runtime_state()
    engine.dispose()
    Base.metadata.drop_all(engine)


@pytest.fixture
def client():
    with TestClient(app, raise_server_exceptions=False) as value:
        yield value


def login(client, username: str, password: str) -> dict:
    response = client.post("/api/auth/login", data={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.fixture
def admin_headers(client):
    return login(client, "admin", "Admin123!")


@pytest.fixture
def rahbar_headers(client):
    return login(client, "rahbar", "Rahbar123!")


@pytest.fixture
def xodim_headers(client):
    return login(client, "xodim", "Xodim123!")
