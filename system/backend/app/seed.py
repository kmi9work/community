"""Начальное наполнение CRM из personal_docs/ (повторный запуск ничего не дублирует)."""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import logs
from .auth import hash_password
from .config import settings
from .models import (
    ActivityType,
    Admin,
    AnswerOption,
    ConsentDocument,
    EventType,
    GameRole,
    Level,
    Question,
    Questionnaire,
    SavedView,
)

# identifier типа мероприятия = имя файла в personal_docs/questions/
KNOWN_TYPE_IDS = {
    "игры": "game",
    "лекции": "lecture",
    "деловые игры": "business_game",
    "клуб кейсы": "cases_club",
    "рефлексивный семинар": "seminar",
}
ROLES = [
    ("Знать", "nobility", "participant"),
    ("Купец", "merchant", "participant"),
    ("Главный мастер", "chief_master", "organizer"),
    ("Мастер политики", "politics_master", "organizer"),
    ("Мастер экономики", "economy_master", "organizer"),
    ("Подмастерье политики", "politics_apprentice", "organizer"),
    ("Подмастерье экономики", "economy_apprentice", "organizer"),
]
SYSTEM_VIEWS = [
    ("events", "Предстоящие игры", {"filters": {"when": "upcoming", "event_type__identifier": "game"}, "sort": "date"}),
    ("deliveries", "Ошибки доставки", {"filters": {"status": "error"}, "sort": "-id"}),
    ("runs", "Брошенные прохождения", {"filters": {"status": "started"}, "sort": "started_at"}),
    ("participations", "Ждут подтверждения", {"filters": {"status": "pending"}, "sort": "registered_at"}),
    ("participations", "Отменили запись", {"filters": {"status": "cancelled"}, "sort": "-registered_at"}),
    ("subscriptions", "Отписались", {"filters": {"subscribed": "false"}, "sort": "-changed_at"}),
    ("appeals", "Необработанные обращения", {"filters": {"processed": "false"}, "sort": "-created_at"}),
]
TRANSLIT = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
                    ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o", "p", "r", "s",
                     "t", "u", "f", "h", "ts", "ch", "sh", "sch", "", "y", "", "e", "yu", "ya"]))
NUMBERS = {"одного": 1, "двух": 2, "трёх": 3, "трех": 3, "четырёх": 4, "четырех": 4, "пяти": 5}


def slug(name: str) -> str:
    low = name.strip().lower()
    if low in KNOWN_TYPE_IDS:
        return KNOWN_TYPE_IDS[low]
    out = "".join(TRANSLIT.get(ch, ch) for ch in low)
    return re.sub(r"[^a-z0-9]+", "_", out).strip("_") or "type"


def parse_levels(text: str) -> list[dict]:
    levels: list[dict] = []
    for line in text.splitlines():
        m = re.match(r"\s*\d+\.\s*\*\*(\w+)\.\s*(.+?)\*\*", line)
        if m:
            levels.append({"identifier": m.group(1).lower(), "name": m.group(2).strip(), "types": [],
                           "activities": []})
            continue
        if not levels:
            continue
        m = re.match(r"\s*Типы мероприятий:\s*(.+)", line)
        if m:
            levels[-1]["types"] = [(a or b).strip() for a, b in re.findall(r"\"([^\"]+)\"|«([^»]+)»", m.group(1))]
            continue
        m = re.match(r"\s*-\s*\d+\.\d+\.?\s*(.+)", line)
        if m:
            levels[-1]["activities"].append(m.group(1).strip())
    return levels


def parse_questions(text: str) -> list[dict]:
    questions: list[dict] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("- "):
            if questions:
                questions[-1]["options"].append(line[2:].strip())
            continue
        if line.startswith("(") and questions:
            if "ввод" in line.lower() or "текст" in line.lower():
                questions[-1]["type"] = "open"
            continue
        every = line.startswith("*")
        body = line.lstrip("*").strip()
        qtype, max_choices = None, None
        hint = re.search(r"\(([^)]*)\)\s*$", body)
        if hint:
            h = hint.group(1).lower()
            if "одного" in h and "выбор" in h:
                qtype = "single"
            elif "выбрать" in h or "нескольк" in h:
                qtype = "multi"
                num = re.search(r"до\s+(\d+|\w+)", h)
                if num:
                    word = num.group(1)
                    max_choices = int(word) if word.isdigit() else NUMBERS.get(word)
            body = body[: hint.start()].strip()
        questions.append({"text": body, "every": every, "type": qtype, "max": max_choices, "options": []})
    for q in questions:
        if q["type"] is None:
            q["type"] = "single" if q["options"] else "open"
    return questions


def seed(session: Session) -> None:
    docs = settings.personal_docs_dir
    _seed_levels(session, docs)
    _seed_roles(session)
    _seed_consent(session, docs)
    _seed_questionnaire(session, docs)
    _seed_views(session)
    _seed_admin(session)
    session.flush()


def _seed_levels(session: Session, docs: Path) -> None:
    if session.scalar(select(func.count(Level.id))):
        return
    path = docs / "levels.md"
    if not path.is_file():
        logs.event("seed.skipped", what="levels", reason="нет personal_docs/levels.md")
        return
    types: dict[str, EventType] = {}
    for li, item in enumerate(parse_levels(path.read_text(encoding="utf-8")), 1):
        level = Level(name=item["name"], identifier=item["identifier"], order=li)
        session.add(level)
        session.flush()
        for name in item["types"]:
            if name not in types:
                t = EventType(name=name, identifier=slug(name), level_id=level.id, order=len(types) + 1)
                session.add(t)
                types[name] = t
            level.event_types.append(types[name])
        for ai, name in enumerate(item["activities"], 1):
            act = ActivityType(name=name, identifier=f"{level.identifier}_{ai}", order=ai)
            session.add(act)
            level.activity_types.append(act)
    session.flush()
    logs.event("seed.levels", types=len(types))


def _seed_roles(session: Session) -> None:
    if session.scalar(select(func.count(GameRole.id))):
        return
    for i, (name, ident, kind) in enumerate(ROLES, 1):
        session.add(GameRole(name=name, identifier=ident, kind=kind, order=i, is_active=True))


def _seed_consent(session: Session, docs: Path) -> None:
    if session.scalar(select(func.count(ConsentDocument.id))):
        return
    path = docs / "law.md"
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    m = re.search(r"https?://\S+", text)
    if not m:
        logs.event("seed.skipped", what="consent", reason="нет ссылки в personal_docs/law.md")
        return
    today = date.today()
    session.add(ConsentDocument(title="Согласие на обработку персональных данных", version=f"{today:%Y.%m}",
                                url=m.group(0).rstrip(".,)"), published_at=today, is_current=True))


def _seed_questionnaire(session: Session, docs: Path) -> None:
    if session.scalar(select(func.count(Questionnaire.id)).where(Questionnaire.purpose == "registration")):
        return
    q = Questionnaire(title="Анкета записи на мероприятие", identifier="registration", purpose="registration",
                      description="", is_active=True)
    session.add(q)
    session.flush()
    order = 0
    for file in sorted((docs / "questions").glob("*.md")) if (docs / "questions").is_dir() else []:
        event_type = session.scalar(select(EventType).where(EventType.identifier == file.stem))
        for item in parse_questions(file.read_text(encoding="utf-8")):
            order += 1
            question = Question(questionnaire_id=q.id, text=item["text"], answer_type=item["type"],
                                max_choices=item["max"], required=item["type"] != "open",
                                ask_every_time=item["every"], order=order, is_active=True)
            if event_type is not None:
                question.event_types = [event_type]
            session.add(question)
            session.flush()
            for oi, text in enumerate(item["options"], 1):
                session.add(AnswerOption(question_id=question.id, text=text, order=oi, is_active=True))
    logs.event("seed.questionnaire", questions=order)


def _seed_views(session: Session) -> None:
    for i, (resource, name, params) in enumerate(SYSTEM_VIEWS, 1):
        exists = session.scalar(select(SavedView.id).where(SavedView.resource == resource, SavedView.name == name,
                                                           SavedView.is_system.is_(True)))
        if not exists:
            session.add(SavedView(resource=resource, name=name, params=params, is_system=True, order=i))


def _seed_admin(session: Session) -> None:
    if session.scalar(select(func.count(Admin.id))):
        return
    if not settings.admin_login or not settings.admin_password:
        logs.event("seed.skipped", what="admin", reason="ADMIN_LOGIN/ADMIN_PASSWORD не заданы")
        return
    session.add(Admin(login=settings.admin_login, name="Администратор",
                      password_hash=hash_password(settings.admin_password)))
    logs.event("seed.admin", login=settings.admin_login)
