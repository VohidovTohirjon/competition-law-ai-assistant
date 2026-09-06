"""Deterministic operational analytics for the leader's own question (spec 4.5).

The technical assignment phrases the analytics module as a question the leader asks in
the same chat window ("Bugungi kun uchun asosiy muammolarni ko'rsat") and requires five
sections: mavjud muammolar, kechikayotgan topshiriqlar, muhim murojaatlar, statistik
ko'rsatkichlar, xavfli holatlar.

The answer is computed from the system's own records, never generated: a leader must be
able to act on these numbers. This is the same deterministic-tool pattern the AI Agent
already uses for legal facts, so the architecture of section 6.4 is unchanged - the
agent answers from data it holds instead of calling the model.
"""

import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..models import Document, NhhDocument, Role, Task, TaskStatus, User

ANALYTICS_PATTERNS = (
    r"asosiy\s+muammo",
    r"muammo(?:lar)?(?:ni|larni)?\s+(?:ko['‘’ʻ`]rsat|chiqar|ayt|bering)",
    r"bugungi\s+kun\s+uchun",
    r"(?:bugun|hozir)\s+.{0,30}\b(?:holat|vaziyat|ahvol)",
    r"kechik(?:ayotgan|kan)\s+topshiriq",
    r"topshiriqlar(?:ning)?\s+(?:holati|ijrosi|ahvoli)",
    r"ish\s+yuklama",
    r"umumiy\s+(?:holat|manzara|ko['‘’ʻ`]rinish)",
    r"dashboard|boshqaruv\s+paneli",
    r"statistik\s+ko['‘’ʻ`]rsatkich",
    r"e['‘’ʻ`]tibor\s+talab",
    r"xavfli\s+holat",
    r"nazorat(?:ga)?\s+.{0,20}\bolinishi\s+kerak",
)
ANALYTICS_RE = re.compile("|".join(ANALYTICS_PATTERNS), re.IGNORECASE)
APOSTROPHES = str.maketrans({"’": "'", "‘": "'", "ʻ": "'", "`": "'", "ʼ": "'"})

PRIORITY_RANK = {"shoshilinch": 0, "yuqori": 1, "odatiy": 2, "past": 3}
PRIORITY_LABEL = {"shoshilinch": "shoshilinch", "yuqori": "yuqori",
                  "odatiy": "odatiy", "past": "past"}


def is_analytics_question(question: str) -> bool:
    """True for an operational question about the committee's own workload."""
    return bool(ANALYTICS_RE.search(question.translate(APOSTROPHES).lower()))


def _day(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%d.%m.%Y")


def _task_line(task: Task, now: datetime) -> str:
    deadline = task.deadline
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=timezone.utc)
    days = (deadline - now).days
    if task.is_overdue:
        timing = f"muddati {abs(days)} kun oldin tugagan"
    elif days == 0:
        timing = "muddati bugun tugaydi"
    else:
        timing = f"muddatiga {days} kun qoldi"
    owner = task.assignee.full_name if task.assignee else "ijrochi tayinlanmagan"
    return (f"- **{task.title}** — {owner}; {timing} ({_day(deadline)}); "
            f"ustuvorlik: {PRIORITY_LABEL.get(task.priority.value, task.priority.value)}; "
            f"holati: {task.status.value}")


def analytics_answer(db: Session, user: User) -> tuple[str, list[dict]]:
    """Render the five sections the assignment requires, scoped to the user's role."""
    now = datetime.now(timezone.utc)
    task_stmt = select(Task)
    personal = user.role == Role.xodim
    if personal:
        task_stmt = task_stmt.where(Task.assigned_to == user.id)
    tasks = list(db.scalars(task_stmt.order_by(Task.deadline.asc())))
    open_tasks = [task for task in tasks
                  if task.status not in {TaskStatus.bajarildi, TaskStatus.bekor_qilindi}]
    overdue = [task for task in open_tasks if task.is_overdue]
    soon = [task for task in open_tasks
            if not task.is_overdue and task.deadline.astimezone(timezone.utc) <= now + timedelta(days=3)]
    ranked = sorted(open_tasks, key=lambda task: (not task.is_overdue,
                                                  PRIORITY_RANK.get(task.priority.value, 9),
                                                  task.deadline))

    document_stmt = select(Document).where(Document.category.in_(["murojaat", "muhim"]))
    if user.role != Role.administrator:
        document_stmt = document_stmt.where(
            or_(Document.owner_id == user.id, Document.is_confidential.is_(False))
        )
    documents = list(db.scalars(document_stmt.order_by(Document.created_at.desc()).limit(5)))

    completed = sum(task.status == TaskStatus.bajarildi for task in tasks)
    active_nhh = db.scalar(
        select(func.count(NhhDocument.id)).where(NhhDocument.is_active.is_(True))
    ) or 0

    scope = "Sizga biriktirilgan" if personal else "Qo‘mita bo‘yicha"
    lines = [f"## {scope} holat — {_day(now)}", ""]

    lines.append("**Mavjud muammolar**")
    lines.extend([_task_line(task, now) for task in ranked[:5]]
                 or ["- Ochiq topshiriqlar yo‘q."])
    if len(open_tasks) > 5:
        lines.append(f"- … jami {len(open_tasks)} ta ochiq topshiriq.")
    lines.append("")

    lines.append("**Kechikayotgan topshiriqlar**")
    lines.extend([_task_line(task, now) for task in overdue[:5]]
                 or ["- Kechikkan topshiriq yo‘q."])
    if len(overdue) > 5:
        lines.append(f"- … jami {len(overdue)} ta kechikkan topshiriq.")
    lines.append("")

    lines.append("**Muhim murojaatlar**")
    lines.extend([f"- **{document.filename}** — {document.category}, "
                  f"{_day(document.created_at)} da yuklangan" for document in documents]
                 or ["- Murojaat yoki muhim hujjat yo‘q."])
    lines.append("")

    lines.append("**Statistik ko‘rsatkichlar**")
    lines.append(f"- Topshiriqlar: jami {len(tasks)}, ochiq {len(open_tasks)}, "
                 f"bajarilgan {completed}, kechikkan {len(overdue)}")
    lines.append(f"- Yaqin 3 kun ichida muddati tugaydigan topshiriqlar: {len(soon)}")
    lines.append(f"- Faol normativ-huquqiy hujjatlar bazasi: {active_nhh} ta hujjat")
    lines.append("")

    lines.append("**E’tibor talab qiladigan holatlar**")
    risky = [task for task in overdue
             if task.priority.value in {"shoshilinch", "yuqori"}] or overdue[:3]
    urgent_soon = [task for task in soon if task.priority.value in {"shoshilinch", "yuqori"}]
    risk_lines = [f"- Kechikkan va ustuvor: **{task.title}** "
                  f"({PRIORITY_LABEL.get(task.priority.value, task.priority.value)})"
                  for task in risky[:5]]
    risk_lines.extend(f"- Muddati yaqin va ustuvor: **{task.title}**" for task in urgent_soon[:3])
    if not personal:
        idle = db.scalar(select(func.count(User.id)).where(
            User.role == Role.xodim, User.is_active.is_(True)
        )) or 0
        if idle and len(open_tasks) / idle >= 3:
            risk_lines.append(f"- Ish yuklamasi yuqori: {idle} ta xodimga {len(open_tasks)} ta "
                              "ochiq topshiriq to‘g‘ri keladi")
    lines.extend(risk_lines or ["- Xavfli holat aniqlanmadi."])

    sources = [{
        "citation_number": index + 1,
        "document_id": document.id,
        "document_name": document.filename,
        "article_or_clause": None,
        "display_label": None,
        "url": f"/api/documents/{document.id}/download",
        "excerpt": (document.parsed_text or "")[:400],
        "full_excerpt": (document.parsed_text or "")[:1200],
        "page": None,
        "section": document.category,
        "evidence_type": "document",
        "document_type": document.category,
        "official_number": None,
    } for index, document in enumerate(documents)]
    return "\n".join(lines), sources
