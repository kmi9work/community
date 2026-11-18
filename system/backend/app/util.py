"""Время, даты по-русски, телефоны, аудит."""
from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from .config import settings

MONTHS = [
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
]


def tz() -> ZoneInfo:
    return ZoneInfo(settings.timezone)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def now_local() -> datetime:
    return datetime.now(tz())


def today_local() -> date:
    return now_local().date()


def as_utc(value: datetime | None) -> datetime | None:
    """SQLite отдаёт naive datetime — считаем его UTC."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def event_start(d: date, t: time | None) -> datetime:
    """Начало мероприятия в UTC. Без времени — начало дня."""
    local = datetime.combine(d, t or time(0, 0), tzinfo=tz())
    return local.astimezone(timezone.utc)


def fmt_date(d: date | None, today: date | None = None) -> str:
    if not d:
        return ""
    today = today or today_local()
    text = f"{d.day} {MONTHS[d.month - 1]}"
    if d.year != today.year:
        text += f" {d.year}"
    return text


def fmt_time(t: time | None) -> str:
    return t.strftime("%H:%M") if t else ""


def fmt_datetime_local(value: datetime | None) -> str:
    value = as_utc(value)
    if not value:
        return ""
    return value.astimezone(tz()).strftime("%d.%m.%Y")


PHONE_RE = re.compile(r"\+?\d[\d\s\-()]{8,}\d")


def normalize_phone(raw: str) -> str:
    """Номер к виду +79001234567. Пустая строка, если номера нет."""
    if not raw:
        return ""
    digits = re.sub(r"\D", "", raw)
    if len(digits) < 10 or len(digits) > 15:
        return ""
    if len(digits) == 11 and digits.startswith("8"):
        digits = "7" + digits[1:]
    elif len(digits) == 10:
        digits = "7" + digits
    return "+" + digits


def extract_phone(text: str) -> str:
    match = PHONE_RE.search(text or "")
    return normalize_phone(match.group(0)) if match else ""


def audit(session: Session, actor: str, action: str, obj=None, **details) -> None:
    from .models import AuditLog

    session.add(
        AuditLog(
            actor=actor,
            action=action,
            object_type=type(obj).__tablename__ if obj is not None else "",
            object_id=getattr(obj, "id", None),
            details=details,
        )
    )


def split_name(text: str) -> tuple[str, str]:
    parts = [p for p in re.split(r"\s+", (text or "").strip()) if p]
    if len(parts) < 2:
        return (parts[0] if parts else ""), ""
    return parts[0][:100], " ".join(parts[1:])[:100]


def hours(value: float) -> timedelta:
    return timedelta(hours=value)
