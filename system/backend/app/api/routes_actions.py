"""Действия админки сверх CRUD: перенос/отмена/проведение, афиша, рассылки,
список для мастера, выгрузка в Excel, отзыв согласия."""
from __future__ import annotations

import html
import io
from datetime import date as Date, time as Time
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, Response
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import logs
from ..config import settings
from ..models import (
    Admin,
    Answer,
    Broadcast,
    Event,
    Participant,
    Participation,
    Question,
    Questionnaire,
    SurveyRun,
)
from ..services import broadcasts as bsvc
from ..services import events as esvc
from ..services import export as xsvc
from ..services import participants as psvc
from ..util import audit, fmt_date, fmt_time
from . import crud
from .deps import current_admin, get_db

router = APIRouter(prefix="/api")

POSTER_SIZE = (1080, 1350)
POSTER_MAX_BYTES = 5 * 1024 * 1024


def _get(session: Session, model, obj_id: int):
    obj = session.get(model, obj_id)
    if obj is None:
        raise HTTPException(404, "Запись не найдена")
    return obj


def _bad(exc: Exception):
    raise crud.ValidationFailed(str(exc))


# --- мероприятия ------------------------------------------------------------------------------


class MoveIn(BaseModel):
    date: Date
    time: Optional[Time] = None
    address: str = ""
    reason: str


@router.post("/events/{event_id}/move")
def move_event(event_id: int, body: MoveIn, admin: Admin = Depends(current_admin), session: Session = Depends(get_db)):
    event = _get(session, Event, event_id)
    try:
        b = esvc.move(session, event, new_date=body.date, new_time=body.time, new_address=body.address.strip(),
                      reason=body.reason, admin_id=admin.id)
    except esvc.EventError as exc:
        _bad(exc)
    logs.event("admin.event_moved", event_id=event.id, admin_id=admin.id, broadcast_id=b.id)
    return {"ok": True, "broadcast_id": b.id, "recipients": b.recipients_count}


class ReasonIn(BaseModel):
    reason: str = ""


@router.post("/events/{event_id}/cancel")
def cancel_event(event_id: int, body: ReasonIn, admin: Admin = Depends(current_admin),
                 session: Session = Depends(get_db)):
    event = _get(session, Event, event_id)
    try:
        b = esvc.cancel(session, event, reason=body.reason, admin_id=admin.id)
    except esvc.EventError as exc:
        _bad(exc)
    logs.event("admin.event_cancelled", event_id=event.id, admin_id=admin.id, broadcast_id=b.id)
    return {"ok": True, "broadcast_id": b.id, "recipients": b.recipients_count}


@router.post("/events/{event_id}/complete")
def complete_event(event_id: int, admin: Admin = Depends(current_admin), session: Session = Depends(get_db)):
    event = _get(session, Event, event_id)
    try:
        esvc.complete(session, event, admin_id=admin.id)
    except esvc.EventError as exc:
        _bad(exc)
    logs.event("admin.event_completed", event_id=event.id, admin_id=admin.id)
    return {"ok": True}


@router.post("/events/{event_id}/announce")
def announce_event(event_id: int, admin: Admin = Depends(current_admin), session: Session = Depends(get_db)):
    """Черновик рассылки анонса: получатели — подписанные на тип мероприятия."""
    event = _get(session, Event, event_id)
    b = Broadcast(kind="announcement", subject=f"Анонс: {event.title}", event_id=event.id, author_id=admin.id,
                  recipient_mode="event_types", status="draft")
    b.event_types = [event.event_type]
    session.add(b)
    session.flush()
    audit(session, f"admin:{admin.id}", "broadcast_created", b)
    return {"broadcast_id": b.id}


class AttendanceIn(BaseModel):
    attendance: Optional[str] = None  # came / absent / null — снять отметки


@router.post("/events/{event_id}/attendance")
def bulk_attendance(event_id: int, body: AttendanceIn, admin: Admin = Depends(current_admin),
                    session: Session = Depends(get_db)):
    _get(session, Event, event_id)
    if body.attendance not in (None, "came", "absent"):
        raise crud.ValidationFailed("Неверное значение присутствия")
    parts = list(session.scalars(select(Participation).where(Participation.event_id == event_id,
                                                             Participation.status == "registered")))
    for p in parts:
        p.attendance = body.attendance
    audit(session, f"admin:{admin.id}", "attendance_bulk", None, event_id=event_id, value=body.attendance,
          count=len(parts))
    return {"ok": True, "count": len(parts)}


@router.post("/events/{event_id}/poster")
async def upload_poster(event_id: int, file: UploadFile = File(...), admin: Admin = Depends(current_admin),
                        session: Session = Depends(get_db)):
    event = _get(session, Event, event_id)
    data = await file.read(POSTER_MAX_BYTES + 1)
    if len(data) > POSTER_MAX_BYTES:
        raise crud.ValidationFailed("Файл больше 5 МБ", "poster")
    try:
        img = Image.open(io.BytesIO(data))
        fmt = img.format
        size = img.size
        img.verify()
    except (UnidentifiedImageError, OSError):
        raise crud.ValidationFailed("Это не картинка JPEG или PNG", "poster")
    if fmt not in ("JPEG", "PNG"):
        raise crud.ValidationFailed("Формат — JPEG или PNG", "poster")
    if size != POSTER_SIZE:
        raise crud.ValidationFailed(f"Размер — 1080 × 1350 пикселей, у файла {size[0]} × {size[1]}", "poster")
    ext = "jpg" if fmt == "JPEG" else "png"
    name = f"{event.event_type.identifier}-{event.date:%Y-%m-%d}-{event.id}.{ext}"
    folder = settings.media_dir / "posters"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_bytes(data)
    if event.poster and event.poster != name:
        (folder / event.poster).unlink(missing_ok=True)
    event.poster = name
    event.poster_telegram_file_id = ""
    event.poster_max_token = ""
    audit(session, f"admin:{admin.id}", "poster_uploaded", event)
    return {"poster": f"/media/posters/{name}"}


@router.delete("/events/{event_id}/poster")
def delete_poster(event_id: int, admin: Admin = Depends(current_admin), session: Session = Depends(get_db)):
    event = _get(session, Event, event_id)
    if event.poster:
        (settings.media_dir / "posters" / event.poster).unlink(missing_ok=True)
    event.poster = ""
    event.poster_telegram_file_id = ""
    event.poster_max_token = ""
    return {"ok": True}


@router.get("/events/{event_id}/master-list", response_class=HTMLResponse)
def master_list(event_id: int, admin: Admin = Depends(current_admin), session: Session = Depends(get_db)):
    """Печатная страница для мастера: записавшиеся со статусом «записан»."""
    event = _get(session, Event, event_id)
    rows = session.execute(
        select(Participation, Participant).join(Participant, Participant.id == Participation.participant_id)
        .where(Participation.event_id == event.id, Participation.status == "registered")
        .order_by(Participant.last_name, Participant.first_name)
    ).all()
    starred = list(session.scalars(
        select(Question).join(Answer, Answer.question_id == Question.id)
        .where(Answer.event_id == event.id, Question.ask_every_time.is_(True))
        .distinct().order_by(Question.order, Question.id)))
    esc = lambda s: html.escape(str(s or ""))  # noqa: E731
    head = "".join(f"<th>{esc(q.text)}</th>" for q in starred)
    body = []
    for i, (part, p) in enumerate(rows, 1):
        answers = {}
        for a in session.scalars(select(Answer).where(Answer.participant_id == p.id, Answer.event_id == event.id)
                                 .order_by(Answer.id)):
            answers[a.question_id] = a.display()
        cells = "".join(f"<td>{esc(answers.get(q.id, ''))}</td>" for q in starred)
        body.append(f"<tr><td>{i}</td><td>{esc(p.full_name)}</td><td>{esc(p.contact_phone)}</td>"
                    f"<td>{esc(part.role.name if part.role else '')}</td>{cells}<td class='mark'></td></tr>")
    audit(session, f"admin:{admin.id}", "master_list_opened", event)
    when = ", ".join(x for x in (fmt_date(event.date), fmt_time(event.time)) if x)
    page = f"""<!doctype html><html lang="ru"><head><meta charset="utf-8"><title>Список: {esc(event.title)}</title>
<style>body{{font:14px/1.4 system-ui,sans-serif;margin:24px;color:#000}}h1{{font-size:20px;margin:0 0 4px}}
table{{border-collapse:collapse;width:100%;margin-top:16px}}th,td{{border:1px solid #444;padding:4px 6px;vertical-align:top;text-align:left}}
th{{background:#eee}}td.mark{{width:70px}}.note{{color:#555;font-size:12px;margin-top:12px}}
@media print{{button{{display:none}}}}</style></head><body>
<button onclick="window.print()">Печать</button>
<h1>{esc(event.title)}</h1><div>{esc(when)}</div><div>{esc(event.address)}</div>
<table><thead><tr><th>№</th><th>Имя и фамилия</th><th>Контактный телефон</th><th>Роль</th>{head}<th>Явка</th></tr></thead>
<tbody>{''.join(body) or '<tr><td colspan="99">Никто не записан</td></tr>'}</tbody></table>
<p class="note">Содержит персональные данные. Передаётся только организаторам этого мероприятия.</p>
</body></html>"""
    return HTMLResponse(page, headers={"Cache-Control": "no-store"})


# --- рассылки ------------------------------------------------------------------------------------------


@router.get("/broadcasts/{broadcast_id}/preview")
def broadcast_preview(broadcast_id: int, admin: Admin = Depends(current_admin), session: Session = Depends(get_db)):
    b = _get(session, Broadcast, broadcast_id)
    try:
        return bsvc.preview(session, b)
    except bsvc.BroadcastError as exc:
        _bad(exc)


@router.post("/broadcasts/{broadcast_id}/test")
def broadcast_test(broadcast_id: int, admin: Admin = Depends(current_admin), session: Session = Depends(get_db)):
    b = _get(session, Broadcast, broadcast_id)
    try:
        test = bsvc.launch_test(session, b, admin)
    except bsvc.BroadcastError as exc:
        _bad(exc)
    return {"ok": True, "broadcast_id": test.id}


@router.post("/broadcasts/{broadcast_id}/launch")
def broadcast_launch(broadcast_id: int, admin: Admin = Depends(current_admin), session: Session = Depends(get_db)):
    b = _get(session, Broadcast, broadcast_id)
    try:
        bsvc.launch(session, b, f"admin:{admin.id}")
    except bsvc.BroadcastError as exc:
        _bad(exc)
    return {"ok": True, "recipients": b.recipients_count, "status": b.status}


@router.post("/broadcasts/{broadcast_id}/cancel")
def broadcast_cancel(broadcast_id: int, body: ReasonIn, admin: Admin = Depends(current_admin),
                     session: Session = Depends(get_db)):
    b = _get(session, Broadcast, broadcast_id)
    try:
        bsvc.cancel(session, b, body.reason, f"admin:{admin.id}")
    except bsvc.BroadcastError as exc:
        _bad(exc)
    return {"ok": True}


@router.post("/broadcasts/{broadcast_id}/retry")
def broadcast_retry(broadcast_id: int, admin: Admin = Depends(current_admin), session: Session = Depends(get_db)):
    b = _get(session, Broadcast, broadcast_id)
    try:
        n = bsvc.retry_errors(session, b, f"admin:{admin.id}")
    except bsvc.BroadcastError as exc:
        _bad(exc)
    return {"ok": True, "count": n}


@router.post("/questionnaires/{questionnaire_id}/send")
def questionnaire_send(questionnaire_id: int, admin: Admin = Depends(current_admin),
                       session: Session = Depends(get_db)):
    """Черновик рассылки опроса; получателей администратор выбирает в карточке рассылки."""
    q = _get(session, Questionnaire, questionnaire_id)
    if q.purpose != "survey":
        raise crud.ValidationFailed("Разослать можно только опросник назначения «опрос»")
    b = Broadcast(kind="survey", subject=f"Опрос: {q.title}", questionnaire_id=q.id, author_id=admin.id,
                  recipient_mode="levels", status="draft")
    session.add(b)
    session.flush()
    audit(session, f"admin:{admin.id}", "broadcast_created", b)
    return {"broadcast_id": b.id}


# --- участники ------------------------------------------------------------------------------------------


@router.post("/participants/{participant_id}/revoke-consent")
def revoke_consent(participant_id: int, admin: Admin = Depends(current_admin), session: Session = Depends(get_db)):
    p = _get(session, Participant, participant_id)
    psvc.revoke_consent(session, p, f"admin:{admin.id}")
    return {"ok": True}


# --- выгрузка в Excel --------------------------------------------------------------------------------------


@router.get("/export/runs")
def export_runs(request: Request, admin: Admin = Depends(current_admin), session: Session = Depends(get_db)):
    params = dict(request.query_params)
    if not params.get("f.status"):
        params["f.status"] = "completed"  # по умолчанию — завершённые
    res = crud.get_resource("runs")
    q = crud.list_query(session, res, params)
    total = session.scalar(select(func.count()).select_from(q.order_by(None).subquery())) or 0
    if total > settings.export_row_limit:
        raise HTTPException(413, f"Строк слишком много ({total}). Сузьте фильтр — до {settings.export_row_limit}.")
    runs = list(session.scalars(q.order_by(SurveyRun.started_at, SurveyRun.id)))
    data = xsvc.build(session, runs)
    name = xsvc.filename(runs)
    logs.event("admin.export", admin_id=admin.id, rows=len(runs), questionnaire=params.get("f.questionnaire"))
    audit(session, f"admin:{admin.id}", "export_runs", None, rows=len(runs), filters={
        k: v for k, v in params.items() if k.startswith("f.")})
    return Response(data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"})


# --- афиши (только для вошедших администраторов) -----------------------------------------------------------

media_router = APIRouter()


@media_router.get("/media/posters/{name}")
def poster_file(name: str, admin: Admin = Depends(current_admin)):
    path = (settings.media_dir / "posters" / Path(name).name).resolve()
    if not path.is_file():
        raise HTTPException(404, "Нет файла")
    return FileResponse(path, headers={"Cache-Control": "private, max-age=3600"})
