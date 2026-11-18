from datetime import date, time, timedelta

from sqlalchemy import select

from app import db
from app.models import (
    Answer,
    Appeal,
    Consent,
    Event,
    EventType,
    Interest,
    Participant,
    Participation,
    Question,
    Subscription,
    SurveyRefusal,
    SurveyRun,
)
from app.util import today_local
from tests.conftest import User


def q(model, **filters):
    with db.session_scope() as s:
        stmt = select(model)
        for k, v in filters.items():
            stmt = stmt.where(getattr(model, k) == v)
        return list(s.scalars(stmt))


def make_event(type_identifier="game", days=7, seats=None, **kw):
    with db.session_scope() as s:
        t = s.scalar(select(EventType).where(EventType.identifier == type_identifier))
        e = Event(title=kw.pop("title", "Эпоха перемен"), event_type_id=t.id, date=today_local() + timedelta(days=days),
                  time=kw.pop("time", time(19, 0)), address=kw.pop("address", "Москва"), seats_limit=seats, **kw)
        s.add(e)
        s.flush()
        return e.id


# --- регистрация --------------------------------------------------------------------


def test_registration_happy_path(user, tg):
    user.start()
    assert tg.keys()[:2] == ["01-start", "02-name-confirm"]
    assert user.buttons() == ["reg:name_yes", "reg:name_no"]
    user.press("reg:name_yes")
    assert tg.last().key == "03-phone-request" and tg.last().request_contact
    user.contact("8 (900) 123-45-67")
    assert tg.last().key == "06-contact-phone-offer"
    assert "phone=+79001234567" in tg.last().text
    assert "messenger=Телеграм" in tg.last().text
    user.press("reg:contact_yes")
    assert tg.last().key == "09-consent"
    assert "https://example.org/consent" in tg.last().text
    msg = user.press("reg:consent_yes")
    assert msg.key == "11-registered" and msg.menu is True
    assert "Игры, Лекции" in msg.text

    [p] = q(Participant)
    assert (p.first_name, p.last_name, p.messenger_phone, p.contact_phone) == ("Иван", "Петров", "+79001234567",
                                                                               "+79001234567")
    assert p.telegram_id == "1001" and p.consent and p.status == "active"
    [c] = q(Consent)
    assert c.channel == "telegram" and c.status == "given"
    subs = q(Subscription)
    assert len(subs) == 2 and all(s.subscribed for s in subs)


def test_registration_requires_phone_and_typed_name(tg):
    u = User(tg, "2002", first="", last="")
    u.start()
    assert tg.last().key == "02-name-confirm" and not tg.last().buttons
    u.text("Мария")  # без фамилии — просим ещё раз
    assert tg.last().key == "02-name-confirm"
    u.text("Мария Иванова")
    assert tg.last().key == "03-phone-request"
    u.text("+79001112233")  # номер руками не принимается
    assert tg.last().key == "04-phone-required"
    u.text("не дам")
    assert tg.last().key == "03-phone-request"
    u.contact("+79001112233", own=False)  # чужой контакт
    assert tg.last().key == "03-phone-request"
    u.contact("+79001112233")
    u.press("reg:contact_no")
    assert tg.last().key == "07-contact-phone-ask"
    u.text("позвоните мне")
    assert tg.last().key == "08-contact-phone-required"
    u.text("мой номер 8-916-000-11-22")
    assert tg.last().key == "09-consent"
    u.press("reg:consent_yes")
    [p] = q(Participant)
    assert p.contact_phone == "+79160001122" and p.messenger_phone == "+79001112233"
    assert (p.first_name, p.last_name) == ("Мария", "Иванова")


def test_consent_declined_creates_nothing(user, tg):
    user.start()
    user.press("reg:name_yes")
    user.contact("+79001234567")
    user.press("reg:contact_yes")
    user.press("reg:consent_no")
    assert tg.last().key == "10-consent-declined"
    assert q(Participant) == []
    user.start()
    assert tg.keys()[-2:] == ["01-start", "02-name-confirm"]


def test_repeated_start_continues_step(user, tg):
    user.start()
    user.press("reg:name_yes")
    user.start()
    assert tg.last().key == "03-phone-request"


def test_second_messenger_links_card(tg, mx):
    User(tg, "1001").register("+79001234567")
    m = User(mx, "m-77")
    m.start()
    m.press("reg:name_yes")
    m.contact("+79001234567")
    assert mx.last().key == "05-known-card"
    assert "name=Иван Петров" in mx.last().text
    m.press("reg:card_yes")
    m.press("reg:contact_yes")
    m.press("reg:consent_yes")
    [p] = q(Participant)
    assert p.telegram_id == "1001" and p.max_id == "m-77"
    assert len(q(Consent)) == 2


def test_second_messenger_new_card_on_no(tg, mx):
    User(tg, "1001").register("+79001234567")
    m = User(mx, "m-77")
    m.start()
    m.press("reg:name_yes")
    m.contact("+79001234567")
    m.press("reg:card_no")
    m.press("reg:contact_yes")
    m.press("reg:consent_yes")
    assert len(q(Participant)) == 2  # совпадение телефона карточки не склеивает


def test_unsubscribe_both_messengers_deletes(tg, mx):
    t = User(tg, "1001")
    t.register("+79001234567")
    m = User(mx, "m-77")
    m.start()
    m.press("reg:name_yes")
    m.contact("+79001234567")
    m.press("reg:card_yes")
    m.press("reg:contact_yes")
    m.press("reg:consent_yes")
    t.stopped()
    [p] = q(Participant)
    assert p.status == "active" and not p.telegram_active
    m.stopped()
    [p] = q(Participant)
    assert p.status == "deleted" and p.first_name == "" and p.telegram_id is None and p.deleted_at


def test_duplicate_update_ignored(user, tg):
    from app.bot.core import dispatch
    from app.messengers.base import Incoming

    inc = Incoming(channel="telegram", kind="start", user_id="5", chat_id="5", dedup_key="u:1")
    with db.session_scope() as s:
        dispatch(s, tg, inc)
    n = len(tg.log)
    with db.session_scope() as s:
        dispatch(s, tg, inc)
    assert len(tg.log) == n


# --- запись на мероприятие ------------------------------------------------------------


def test_join_with_form(user, tg):
    user.register()
    event_id = make_event()
    user.press(f"ev:join:{event_id}")
    msg = tg.last()
    assert msg.key == "27-form-question" and "n=1" in msg.text and "m=4" in msg.text
    # выбор одного
    [q1] = [b for b in user.buttons() if b.startswith("q:")][:1]
    user.press(q1)
    assert tg.last().key == "27-form-question" and "n=2" in tg.last().text
    # открытый ответ: кнопка-вариант не принимается
    user.text("Менеджер")
    msg = tg.last()
    assert "n=3" in msg.text and "max=2" in msg.text
    opts = [b for b in user.buttons() if b.startswith("m:")]
    user.press(opts[0])
    user.press(opts[1].replace(":1", ":1"))
    user.press(opts[2])  # третий — сверх лимита
    assert tg.last().key == "28-form-multi-limit"
    qid = opts[0].split(":")[1]
    user.press(f"g:{qid}")
    assert "n=4" in tg.last().text
    user.text("Новых знакомых")
    assert tg.last().key == "24-event-pending"
    [part] = q(Participation)
    assert part.status == "pending" and part.event_id == event_id
    [run] = q(SurveyRun)
    assert run.status == "completed" and run.event_id == event_id
    answers = q(Answer)
    assert len(answers) == 4
    assert all(a.event_id == event_id for a in answers)


def test_second_join_asks_only_starred(user, tg):
    user.register()
    e1, e2 = make_event(), make_event(days=14, title="Вторая")
    _complete_form(user, tg, e1)
    user.press(f"ev:join:{e2}")
    assert "m=2" in tg.last().text  # только вопросы со звёздочкой
    user.press(f"ev:join:{e1}")
    assert tg.last().key == "25-event-already" and "[25.1]" in tg.last().text


def _complete_form(user, tg, event_id):
    user.press(f"ev:join:{event_id}")
    while tg.last().key == "27-form-question":
        buttons = user.buttons()
        single = [b for b in buttons if b.startswith("q:")]
        multi = [b for b in buttons if b.startswith("m:")]
        if single:
            user.press(single[0])
        elif multi:
            user.press(multi[0])
            user.press("g:" + multi[0].split(":")[1])
        else:
            user.text("ответ")


def test_lecture_has_no_questions(user, tg):
    user.register()
    event_id = make_event("lecture")
    user.press(f"ev:join:{event_id}")
    assert tg.last().key == "24-event-pending"


def test_cancel_own_participation(user, tg):
    """«Мои записи» → «Отменить: (название)»: вопрос, отмена, возможность записаться снова."""
    user.register()
    event_id = make_event("lecture")
    user.press(f"ev:join:{event_id}")
    user.press("me:events")
    assert "ждёт подтверждения" in tg.last().text
    [cancel] = [b for b in user.buttons() if b.startswith("ev:cancel_ask:")]
    user.press(cancel)
    assert tg.last().key == "69-event-cancel"
    [part] = q(Participation)
    [confirm] = [b for b in user.buttons() if b.startswith("ev:cancel:")]
    user.press(confirm)
    assert tg.last().key == "69-event-cancelled"
    with db.session_scope() as s:
        stored = s.get(Participation, part.id)
        assert stored is not None and stored.status == "cancelled"
    user.press(f"ev:join:{event_id}")
    assert tg.last().key == "24-event-pending"


def test_declined_participation_blocks_rejoin(user, tg):
    user.register()
    event_id = make_event("lecture")
    user.press(f"ev:join:{event_id}")
    with db.session_scope() as s:
        stored = s.scalar(select(Participation))
        assert stored is not None
        stored.status = "declined"
    user.press(f"ev:join:{event_id}")
    assert tg.last().key == "25-event-declined"
    assert len(q(Participation)) == 1


def test_seats_limit(user, tg):
    other = User(tg, "3003", "Пётр", "Сидоров")
    other.register("+79005550000")
    user.register()
    event_id = make_event("lecture", seats=1)
    other.press(f"ev:join:{event_id}")
    user.press(f"ev:join:{event_id}")
    assert tg.last().key == "31-event-full"
    assert len(q(Participation)) == 1


def test_closed_event(user, tg):
    user.register()
    event_id = make_event(days=-1)
    user.press(f"ev:join:{event_id}")
    assert tg.last().key == "32-event-closed"


def test_decline_form(user, tg):
    user.register()
    event_id = make_event()
    user.press(f"ev:join:{event_id}")
    [x] = [b for b in user.buttons() if b.startswith("x:")]
    user.press(x)
    assert tg.last().key == "30-form-declined"
    assert q(Participation) == []
    [run] = q(SurveyRun)
    assert run.status == "declined"
    assert len(q(SurveyRefusal)) == 1


def test_old_button_does_not_duplicate_answer(user, tg):
    user.register()
    event_id = make_event()
    user.press(f"ev:join:{event_id}")
    [q1] = [b for b in user.buttons() if b.startswith("q:")][:1]
    user.press(q1)
    user.press(q1)
    assert len(q(Answer)) == 1
    assert tg.log[-1].op == "answer" and "позади" in tg.log[-1].notice


def test_resume_on_start(user, tg):
    user.register()
    event_id = make_event()
    user.press(f"ev:join:{event_id}")
    [q1] = [b for b in user.buttons() if b.startswith("q:")][:1]
    user.press(q1)
    user.start()
    assert tg.last().key == "27-form-question" and "n=2" in tg.last().text


def test_events_list_and_unsubscribe(user, tg):
    user.register()
    make_event()
    user.text("Мероприятия")
    assert tg.last().key == "20-events-type"
    assert "ev:all" in user.buttons()
    user.press("ev:type:game")
    assert tg.last().key == "22-event-announcement"
    assert [b.data for row in tg.last().buttons for b in row][0].startswith("ev:join:")
    user.press("ev:type:lecture")
    assert tg.last().key == "21-events-none"
    with db.session_scope() as s:
        t = s.scalar(select(EventType).where(EventType.identifier == "game"))
        tid = t.id
    user.press(f"ev:unsub:{tid}")
    assert tg.last().key == "26-unsubscribed"
    sub = [s for s in q(Subscription) if s.event_type_id == tid][0]
    assert not sub.subscribed


# --- интересы, мои данные, обращения ----------------------------------------------------------


def test_interests_subscribe_level(user, tg):
    user.register()
    user.text("О сообществе")
    assert tg.last().key == "50-about"
    user.press("int:start")
    assert tg.last().key == "52-interests-choose"
    level_buttons = [b for b in user.buttons() if b.startswith("int:level:")]
    user.press(level_buttons[1])  # весь второй уровень
    user.press("int:save")
    assert tg.last().key == "51-interests-saved"
    assert len(q(Interest)) == 2
    subscribed = {s.event_type_id for s in q(Subscription) if s.subscribed}
    assert len(subscribed) == 3  # Игры, Лекции, Деловые игры


def test_my_data_edit_and_appeal(user, tg):
    user.register()
    user.text("Мои данные")
    assert tg.last().key == "60-my-data"
    user.press("me:edit_name")
    user.text("Иван Иванов")
    assert tg.keys()[-2:] == ["66-edit-name", "60-my-data"]
    user.press("me:edit_phone")
    user.text("+7 916 111 22 33")
    [p] = q(Participant)
    assert p.last_name == "Иванов" and p.contact_phone == "+79161112233"
    user.press("me:subs")
    assert tg.last().key == "61-my-subscriptions"
    toggles = [b for b in user.buttons() if b.startswith("sub:toggle:")]
    user.press(toggles[0])
    assert tg.log[-1].op in ("edit", "answer")
    user.text("Написать организатору")
    assert tg.last().key == "65-contact-organizer"
    user.text("Можно прийти с другом?")
    assert tg.last().key == "68-appeal-sent"
    [a] = q(Appeal)
    assert a.text == "Можно прийти с другом?" and a.channel == "telegram"


def test_unknown_text_shows_menu(user, tg):
    user.register()
    user.text("привет")
    assert tg.last().key == "12-menu"


def test_revoked_consent_asked_again(user, tg):
    from app.services import participants as psvc

    user.register()
    with db.session_scope() as s:
        psvc.revoke_consent(s, s.scalar(select(Participant)), "admin:1")
    user.text("Мероприятия")
    assert tg.last().key == "09-consent"
    user.press("reg:consent_yes")
    assert tg.last().key == "12-menu"
    [p] = q(Participant)
    assert p.consent
    assert [c.status for c in q(Consent)] == ["revoked", "given"]


def test_question_text_escaped_once(user, tg):
    user.register()
    with db.session_scope() as s:
        question = s.scalar(select(Question).where(Question.order == 1))
        question.text = "Пол & возраст"
    event_id = make_event()
    user.press(f"ev:join:{event_id}")
    assert "Пол &amp; возраст" in tg.last().text and "&amp;amp;" not in tg.last().text
