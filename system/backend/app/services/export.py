"""Выгрузка ответов в Excel: широкий лист (прохождение × вопросы) и плоский список."""
from __future__ import annotations

import io
import re
from datetime import datetime

from openpyxl import Workbook
from openpyxl.utils import get_column_letter
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import CHANNELS, RUN_STATUS, Answer, Question, SurveyRun
from ..util import as_utc, tz

FORMULA_START = ("=", "+", "-", "@")


def _cell(ws, row: int, col: int, value) -> None:
    cell = ws.cell(row=row, column=col)
    if isinstance(value, str):
        cell.value = value
        cell.data_type = "s"  # строка, а не формула
        if value.startswith(FORMULA_START):
            cell.quotePrefix = True
    else:
        cell.value = value


def _local(value) -> datetime | None:
    value = as_utc(value)
    return value.astimezone(tz()).replace(tzinfo=None) if value else None


def build(session: Session, runs: list[SurveyRun]) -> bytes:
    run_ids = [r.id for r in runs]
    answers = list(session.scalars(select(Answer).where(Answer.run_id.in_(run_ids)).order_by(Answer.id))) if run_ids else []
    q_ids = {a.question_id for a in answers}
    questionnaire_ids = {r.questionnaire_id for r in runs}
    questions = list(session.scalars(
        select(Question).where((Question.questionnaire_id.in_(questionnaire_ids)) | (Question.id.in_(q_ids)))
        .order_by(Question.questionnaire_id, Question.order, Question.id)
    )) if runs else []

    wb = Workbook()
    wide = wb.active
    wide.title = "Прохождения"
    head = ["Участник", "Контактный телефон", "Опросник", "Мероприятие", "Дата мероприятия", "Статус", "Канал",
            "Начат", "Завершён"]
    for i, title in enumerate(head + [q.text for q in questions], 1):
        _cell(wide, 1, i, title)
    by_run: dict[int, dict[int, Answer]] = {}
    for a in answers:
        by_run.setdefault(a.run_id, {})[a.question_id] = a  # для вопроса со звёздочкой — последний ответ
    for r_i, run in enumerate(runs, 2):
        p = run.participant
        values = [p.label(), p.contact_phone, run.questionnaire.title, run.event.title if run.event else "",
                  run.event.date if run.event else None, RUN_STATUS.get(run.status, run.status),
                  CHANNELS.get(run.channel, run.channel), _local(run.started_at), _local(run.completed_at)]
        row_answers = by_run.get(run.id, {})
        values += [row_answers[q.id].display() if q.id in row_answers else "" for q in questions]
        for c_i, v in enumerate(values, 1):
            _cell(wide, r_i, c_i, v)

    flat = wb.create_sheet("Ответы")
    for i, title in enumerate(["Участник", "Вопрос", "Ответ", "Мероприятие", "Опросник", "Дата ответа"], 1):
        _cell(flat, 1, i, title)
    runs_by_id = {r.id: r for r in runs}
    for r_i, a in enumerate(answers, 2):
        run = runs_by_id[a.run_id]
        for c_i, v in enumerate([run.participant.label(), a.question.text, a.display(),
                                 a.event.title if a.event else "", run.questionnaire.title, _local(a.answered_at)], 1):
            _cell(flat, r_i, c_i, v)

    for ws in (wide, flat):
        for col in range(1, ws.max_column + 1):
            ws.column_dimensions[get_column_letter(col)].width = 24
        ws.freeze_panes = "A2"
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def filename(runs: list[SurveyRun]) -> str:
    names = {r.questionnaire.identifier for r in runs}
    base = names.pop() if len(names) == 1 else "answers"
    base = re.sub(r"[^A-Za-z0-9_-]+", "_", base) or "answers"
    return f"{base}-{datetime.now(tz()):%Y-%m-%d}.xlsx"
