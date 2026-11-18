"""Общий вид входящих событий и исходящих сообщений для Телеграм и MAX."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Protocol


@dataclass
class Button:
    text: str
    data: str = ""  # код кнопки (callback)
    url: str = ""  # кнопка-ссылка
    request_contact: bool = False


@dataclass
class Photo:
    path: str  # файл на диске
    telegram_file_id: str = ""
    max_token: str = ""


@dataclass
class OutMessage:
    text: str
    buttons: list[list[Button]] = field(default_factory=list)  # inline-клавиатура
    menu: Optional[bool] = None  # True — показать меню участника, False — убрать
    request_contact: bool = False  # кнопка «Отправить телефон»
    photo: Optional[Photo] = None
    key: str = ""  # какой шаблон (для журнала и тестов)


@dataclass
class SendResult:
    message_id: str = ""
    # заполняется адаптером, если афиша загружена впервые
    telegram_file_id: str = ""
    max_token: str = ""


class SendError(Exception):
    """Ошибка мессенджера. retry_after — пауза из ответа «слишком часто»,
    blocked — пользователь заблокировал бота, permanent — повтор бесполезен."""

    def __init__(self, message: str, *, code: int = 0, retry_after: float = 0, blocked: bool = False,
                 permanent: bool = False):
        super().__init__(message)
        self.code = code
        self.retry_after = retry_after
        self.blocked = blocked
        self.permanent = permanent or blocked


@dataclass
class Incoming:
    channel: str
    kind: str  # start | text | callback | contact | stopped | resumed | other
    user_id: str
    chat_id: str = ""
    text: str = ""
    callback_data: str = ""
    callback_id: str = ""
    message_id: str = ""  # сообщение, к которому относится кнопка
    contact_phone: str = ""
    contact_is_own: bool = True
    first_name: str = ""
    last_name: str = ""
    start_payload: str = ""
    dedup_key: str = ""


MENU_EVENTS = "Мероприятия"
MENU_ABOUT = "О сообществе"
MENU_ME = "Мои данные"
MENU_ORG = "Написать организатору"
MENU_ITEMS = (MENU_EVENTS, MENU_ABOUT, MENU_ME, MENU_ORG)


class Adapter(Protocol):
    channel: str
    text_limit: int
    caption_limit: int

    def send(self, chat_id: str, msg: OutMessage) -> SendResult: ...

    def edit(self, chat_id: str, message_id: str, msg: OutMessage) -> None: ...

    def answer_callback(self, callback_id: str, notice: str = "", chat_id: str = "") -> None: ...

    def parse(self, payload: dict) -> Optional[Incoming]: ...
