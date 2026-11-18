"""Адаптер-заглушка: копит исходящие сообщения (тесты и локальная отладка)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .base import Incoming, OutMessage, SendError, SendResult


@dataclass
class Sent:
    op: str  # send | edit | answer
    chat_id: str
    msg: Optional[OutMessage] = None
    message_id: str = ""
    notice: str = ""


class FakeAdapter:
    def __init__(self, channel: str = "telegram", text_limit: int = 4096, caption_limit: int = 1024):
        self.channel = channel
        self.text_limit = text_limit
        self.caption_limit = caption_limit
        self.log: list[Sent] = []
        self._next = 100
        self.fail_with: Optional[SendError] = None  # следующая отправка упадёт с этой ошибкой

    def send(self, chat_id: str, msg: OutMessage) -> SendResult:
        if self.fail_with:
            err, self.fail_with = self.fail_with, None
            raise err
        self._next += 1
        self.log.append(Sent("send", chat_id, msg, str(self._next)))
        res = SendResult(message_id=str(self._next))
        if msg.photo and not msg.photo.telegram_file_id and self.channel == "telegram":
            res.telegram_file_id = "file-123"
        return res

    def edit(self, chat_id: str, message_id: str, msg: OutMessage) -> None:
        self.log.append(Sent("edit", chat_id, msg, message_id))

    def answer_callback(self, callback_id: str, notice: str = "", chat_id: str = "") -> None:
        self.log.append(Sent("answer", chat_id, notice=notice))

    def parse(self, payload: dict) -> Optional[Incoming]:  # pragma: no cover
        return None

    # помощники для тестов
    def sent(self) -> list[OutMessage]:
        return [s.msg for s in self.log if s.op == "send" and s.msg]

    def keys(self) -> list[str]:
        return [s.msg.key for s in self.log if s.op in ("send", "edit") and s.msg]

    def last(self) -> OutMessage:
        return [s.msg for s in self.log if s.op in ("send", "edit") and s.msg][-1]

    def clear(self) -> None:
        self.log.clear()
