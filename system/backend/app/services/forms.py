"""Форма: отбор вопросов для анкеты записи и опроса."""
from __future__ import annotations

from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Answer, Event, Participant, Question, Questionnaire, SurveyRun


def registration_questionnaire(session: Session) -> Optional[Questionnaire]:
    return session.scalar(
        select(Questionnaire)
        .where(Questionnaire.purpose == "registration", Questionnaire.is_active.is_(True))
        .order_by(Questionnaire.id)
    )


def answered_question_ids(session: Session, participant_id: int) -> set[int]:
    """Вопросы, на которые участник уже отвечал — в любом опроснике и мероприятии."""
    return set(session.scalars(select(Answer.question_id).where(Answer.participant_id == participant_id).distinct()))


def _filter(questions: list[Question], answered: set[int]) -> list[Question]:
    result = []
    for q in questions:
        if not q.is_active:
            continue
        if q.answer_type in ("single", "multi") and not q.active_options():
            continue
        if q.ask_every_time or q.id not in answered:
            result.append(q)
    return result


def registration_questions(session: Session, participant: Participant, event: Event) -> tuple[Optional[Questionnaire], list[Question]]:
    questionnaire = registration_questionnaire(session)
    if questionnaire is None:
        return None, []
    questions = [
        q for q in questionnaire.questions
        if not q.event_types or event.event_type_id in {t.id for t in q.event_types}
    ]
    return questionnaire, _filter(questions, answered_question_ids(session, participant.id))


def survey_questions(session: Session, participant: Participant, questionnaire: Questionnaire) -> list[Question]:
    return _filter(list(questionnaire.questions), answered_question_ids(session, participant.id))


def started_run(session: Session, participant_id: int, questionnaire_id: int,
                event_id: Optional[int]) -> Optional[SurveyRun]:
    q = select(SurveyRun).where(
        SurveyRun.participant_id == participant_id,
        SurveyRun.questionnaire_id == questionnaire_id,
        SurveyRun.status == "started",
    )
    q = q.where(SurveyRun.event_id == event_id) if event_id else q.where(SurveyRun.event_id.is_(None))
    return session.scalar(q.order_by(SurveyRun.id.desc()))


def latest_started_run(session: Session, participant_id: int) -> Optional[SurveyRun]:
    return session.scalar(
        select(SurveyRun)
        .where(SurveyRun.participant_id == participant_id, SurveyRun.status == "started")
        .order_by(SurveyRun.started_at.desc(), SurveyRun.id.desc())
    )
