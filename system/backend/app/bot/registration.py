"""Шаг 0: регистрация участника (имя → телефон мессенджера → карточка →
контактный телефон → согласие). Черновик — в DialogState.data, карточка
создаётся только после согласия."""
from __future__ import annotations

from ..messengers.base import Button, OutMessage
from ..models import CHANNELS
from ..services import participants as psvc
from ..texts import render
from ..util import extract_phone, normalize_phone, split_name
from .core import Ctx

S_NAME_CONFIRM = "reg_name_confirm"
S_NAME_INPUT = "reg_name_input"
S_PHONE = "reg_phone"
S_KNOWN_CARD = "reg_known_card"
S_CONTACT_OFFER = "reg_contact_offer"
S_CONTACT_INPUT = "reg_contact_input"
S_CONSENT = "reg_consent"
REG_STATES = {S_NAME_CONFIRM, S_NAME_INPUT, S_PHONE, S_KNOWN_CARD, S_CONTACT_OFFER, S_CONTACT_INPUT, S_CONSENT}


def handle(ctx: Ctx) -> None:
    inc = ctx.inc
    state = ctx.dialog.state
    if state not in REG_STATES:
        # новый пользователь, отказ от согласия или удалённая карточка — начать заново
        ctx.reset_state("new")
        ctx.send(OutMessage(render("01"), key="01-start"))
        ask_name(ctx)
        return
    if inc.kind == "start":
        repeat_step(ctx)
        return
    if inc.kind == "callback":
        on_callback(ctx, inc.callback_data)
        return
    if inc.kind == "contact":
        on_contact(ctx)
        return
    if inc.kind == "text":
        on_text(ctx, inc.text.strip())
        return
    repeat_step(ctx)


# --- шаги -----------------------------------------------------------------------------------


def ask_name(ctx: Ctx) -> None:
    first, last = ctx.inc.first_name.strip(), ctx.inc.last_name.strip()
    if first and last:
        ctx.set_state(S_NAME_CONFIRM, profile_first=first, profile_last=last)
        ctx.send(OutMessage(render("02", {"first_name": first, "last_name": last}),
                            buttons=[[Button("Да", "reg:name_yes"), Button("Ввести заново", "reg:name_no")]],
                            key="02-name-confirm"))
    else:
        ctx.set_state(S_NAME_INPUT)
        ctx.send(OutMessage(render("02", variant=1), key="02-name-confirm"))


def ask_phone(ctx: Ctx) -> None:
    ctx.set_state(S_PHONE)
    ctx.send(OutMessage(render("03", {"first_name": ctx.data.get("first_name")}), request_contact=True,
                        key="03-phone-request"))


def ask_contact(ctx: Ctx) -> None:
    ctx.set_state(S_CONTACT_OFFER)
    ctx.send(OutMessage(
        render("06", {"messenger": CHANNELS[ctx.channel], "phone": ctx.data.get("messenger_phone")}),
        buttons=[[Button("Да", "reg:contact_yes"), Button("Другой номер", "reg:contact_no")]],
        key="06-contact-phone-offer",
    ))


def ask_consent(ctx: Ctx) -> None:
    doc = psvc.current_consent_document(ctx.session)
    if doc is None:
        raise RuntimeError("нет действующей редакции документа о согласии")
    ctx.set_state(S_CONSENT, document_id=doc.id)
    ctx.send(OutMessage(render("09", {"consent_link": doc.url}),
                        buttons=[[Button("Согласен", "reg:consent_yes"), Button("Не согласен", "reg:consent_no")]],
                        key="09-consent"))


def repeat_step(ctx: Ctx) -> None:
    """Повторный /start или непонятное действие — продолжить с текущего шага."""
    state = ctx.dialog.state
    if state == S_NAME_CONFIRM:
        d = ctx.data
        ctx.send(OutMessage(render("02", {"first_name": d.get("profile_first"), "last_name": d.get("profile_last")}),
                            buttons=[[Button("Да", "reg:name_yes"), Button("Ввести заново", "reg:name_no")]],
                            key="02-name-confirm"))
    elif state == S_NAME_INPUT:
        ctx.send(OutMessage(render("02", variant=1), key="02-name-confirm"))
    elif state == S_PHONE:
        ask_phone(ctx)
    elif state == S_KNOWN_CARD:
        _send_known_card(ctx, ctx.data.get("found_name", ""))
    elif state == S_CONTACT_OFFER:
        ask_contact(ctx)
    elif state == S_CONTACT_INPUT:
        _send_contact_ask(ctx)
    elif state == S_CONSENT:
        ask_consent(ctx)


def _send_known_card(ctx: Ctx, name: str) -> None:
    ctx.send(OutMessage(render("05", {"name": name}),
                        buttons=[[Button("Это я", "reg:card_yes"), Button("Нет, я новый", "reg:card_no")]],
                        key="05-known-card"))


def _send_contact_ask(ctx: Ctx) -> None:
    ctx.send(OutMessage(render("07"), buttons=[[Button("Отмена", "reg:contact_back")]], key="07-contact-phone-ask"))


# --- события --------------------------------------------------------------------------------


def on_callback(ctx: Ctx, code: str) -> None:
    state = ctx.dialog.state
    if code == "reg:name_yes" and state == S_NAME_CONFIRM:
        d = ctx.data
        ctx.set_state(state, first_name=d.get("profile_first"), last_name=d.get("profile_last"))
        ctx.edit(ctx.inc.message_id, OutMessage(render("02", {"first_name": d.get("profile_first"),
                                                              "last_name": d.get("profile_last")}), key="02"))
        ask_phone(ctx)
    elif code == "reg:name_no" and state == S_NAME_CONFIRM:
        ctx.set_state(S_NAME_INPUT)
        ctx.send(OutMessage(render("02", variant=1), key="02-name-confirm"))
    elif code in ("reg:card_yes", "reg:card_no") and state == S_KNOWN_CARD:
        link = ctx.data.get("found_id") if code == "reg:card_yes" else None
        ctx.set_state(state, link_id=link)
        ask_contact(ctx)
    elif code == "reg:contact_yes" and state == S_CONTACT_OFFER:
        ctx.set_state(state, contact_phone=ctx.data.get("messenger_phone"))
        ask_consent(ctx)
    elif code == "reg:contact_no" and state == S_CONTACT_OFFER:
        ctx.set_state(S_CONTACT_INPUT)
        _send_contact_ask(ctx)
    elif code == "reg:contact_back" and state == S_CONTACT_INPUT:
        ask_contact(ctx)
    elif code == "reg:consent_yes" and state == S_CONSENT:
        finish(ctx)
    elif code == "reg:consent_no" and state == S_CONSENT:
        ctx.reset_state("declined")
        ctx.send(OutMessage(render("10"), key="10-consent-declined"))
    else:
        # нажатие старой кнопки или кнопки меню до регистрации
        ctx.notice("Сначала завершите регистрацию")
        repeat_step(ctx)


def on_contact(ctx: Ctx) -> None:
    if ctx.dialog.state != S_PHONE:
        repeat_step(ctx)
        return
    phone = normalize_phone(ctx.inc.contact_phone)
    if not ctx.inc.contact_is_own or not phone:
        ask_phone(ctx)
        return
    ctx.set_state(S_PHONE, messenger_phone=phone)
    found = psvc.find_other_channel_card(ctx.session, ctx.channel, phone)
    if found is not None:
        ctx.set_state(S_KNOWN_CARD, found_id=found.id, found_name=found.full_name)
        _send_known_card(ctx, found.full_name)
        return
    ctx.set_state(S_PHONE, link_id=None)
    ask_contact(ctx)


def on_text(ctx: Ctx, text: str) -> None:
    state = ctx.dialog.state
    if state in (S_NAME_INPUT, S_NAME_CONFIRM):
        first, last = split_name(text)
        if not first or not last:
            ctx.set_state(S_NAME_INPUT)
            ctx.send(OutMessage(render("02", variant=1), key="02-name-confirm"))
            return
        ctx.set_state(state, first_name=first, last_name=last)
        ask_phone(ctx)
    elif state == S_PHONE:
        # номер руками не принимается; отказ без телефона объясняем один раз
        if not ctx.data.get("phone_refused"):
            ctx.set_state(S_PHONE, phone_refused=True)
            ctx.send(OutMessage(render("04"), request_contact=True, key="04-phone-required"))
        else:
            ask_phone(ctx)
    elif state == S_CONTACT_INPUT:
        phone = extract_phone(text)
        if not phone:
            ctx.send(OutMessage(render("08") + "\n\nНапример: +7 900 123-45-67",
                                buttons=[[Button("Отмена", "reg:contact_back")]], key="08-contact-phone-required"))
            return
        ctx.set_state(state, contact_phone=phone)
        ask_consent(ctx)
    else:
        repeat_step(ctx)


def finish(ctx: Ctx) -> None:
    from ..models import ConsentDocument

    d = ctx.data
    doc = ctx.session.get(ConsentDocument, d.get("document_id")) if d.get("document_id") else None
    doc = doc or psvc.current_consent_document(ctx.session)
    if doc is None:
        raise RuntimeError("нет документа о согласии")
    participant = psvc.register(
        ctx.session,
        channel=ctx.channel,
        user_id=ctx.inc.user_id,
        first_name=d.get("first_name") or "",
        last_name=d.get("last_name") or "",
        messenger_phone=d.get("messenger_phone") or "",
        contact_phone=d.get("contact_phone") or "",
        link_id=d.get("link_id"),
        document=doc,
    )
    ctx.participant = participant
    ctx.reset_state("idle")
    names = psvc.subscribed_type_names(ctx.session, participant)
    line = (f"Вы подписаны на анонсы: {', '.join(names)}." if names else
            "Пока Вы не подписаны на анонсы — направления можно выбрать в „О сообществе“.")
    ctx.send(OutMessage(render("11", {"first_name": participant.first_name, "subscriptions_line": line}),
                        menu=True, key="11-registered"))
