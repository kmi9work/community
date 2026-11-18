"""Сборка сообщений бота: кнопки, анонс, длинные тексты."""
from __future__ import annotations

import html
from typing import Optional

from ..config import settings
from ..messengers.base import Button, OutMessage, Photo
from ..models import Event
from ..texts import render, render_template, template
from ..util import fmt_date, fmt_time

BUTTON_LIMIT = 20
TEXT_LIMITS = {"telegram": 4096, "max": 4000}
CAPTION_LIMITS = {"telegram": 1024, "max": 4000}


def trunc(text: str, limit: int = BUTTON_LIMIT) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def btn_events() -> Button:
    return Button("Мероприятия", "menu:events")


def btn_about() -> Button:
    return Button("О сообществе", "menu:about")


def btn_me() -> Button:
    return Button("Мои данные", "me:data")


def btn_org() -> Button:
    return Button("Написать организатору", "me:org")


def rows(buttons: list[Button], per_row: int = 2) -> list[list[Button]]:
    return [buttons[i: i + per_row] for i in range(0, len(buttons), per_row)]


def split_long(text: str, limit: int) -> list[str]:
    """Разбить длинный текст по абзацам; следующие части — с пометкой «продолжение»."""
    if len(text) <= limit:
        return [text]
    parts: list[str] = []
    current = ""
    prefix = "(продолжение)\n\n"
    for para in text.split("\n"):
        candidate = (current + "\n" + para) if current else para
        budget = limit - (len(prefix) if parts else 0)
        if len(candidate) <= budget:
            current = candidate
            continue
        if current:
            parts.append(current)
        while len(para) > limit - len(prefix):
            parts.append(para[: limit - len(prefix)])
            para = para[limit - len(prefix):]
        current = para
    if current:
        parts.append(current)
    return [p if i == 0 else prefix + p for i, p in enumerate(parts)]


def event_values(event: Event) -> dict:
    return {
        "event_title": event.title,
        "event_date": fmt_date(event.date),
        "event_time": fmt_time(event.time),
        "event_address": event.address,
    }


def poster_path(event: Event) -> Optional[str]:
    if not event.poster:
        return None
    path = settings.media_dir / "posters" / event.poster
    return str(path) if path.is_file() else None


def announcement(event: Event, channel: str, *, with_unsubscribe: bool, description: Optional[str] = None) -> list[OutMessage]:
    """Анонс (22) с афишей; длинное описание — отдельным сообщением (23)."""
    desc = (event.description if description is None else description or "").strip()
    caption = render("22", event_values(event))
    buttons = [Button("Записаться", f"ev:join:{event.id}")]
    if with_unsubscribe:
        label = f"Отписаться: {event.event_type.name}"
        if len(label) > BUTTON_LIMIT:
            label = "Отписаться"
            caption += "\n" + html.escape(f"Направление: {event.event_type.name}", quote=False)
        buttons.append(Button(label, f"ev:unsub:{event.event_type_id}"))
    keyboard = [buttons] if len(buttons) == 1 or all(len(b.text) <= 10 for b in buttons) else [[b] for b in buttons]
    path = poster_path(event)
    limit = CAPTION_LIMITS[channel] if path else TEXT_LIMITS[channel]
    extra: list[OutMessage] = []
    if desc:
        with_desc = caption + "\n\n" + html.escape(desc, quote=False)
        if len(with_desc) <= limit:
            caption = with_desc
        else:
            body = render("23", {"event_title": event.title, "event_description": desc})
            for part in split_long(body, TEXT_LIMITS[channel]):
                extra.append(OutMessage(part, key="23-event-description"))
            extra[-1].buttons = keyboard
    main = OutMessage(caption, buttons=keyboard, key="22-event-announcement")
    if path:
        main.photo = Photo(path=path, telegram_file_id=event.poster_telegram_file_id,
                           max_token=event.poster_max_token)
    return [main] + extra


def question_text(n: int, m: int, text: str) -> str:
    return render("27", {"n": n, "m": m, "question_text": text})


def multi_line(max_choices: Optional[int], selected: int) -> str:
    if max_choices:
        return render_template(template("27", 1), {"max": max_choices, "selected": selected})
    return f"Выбрано: {selected}."
