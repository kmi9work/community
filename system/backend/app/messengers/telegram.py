"""Телеграм: Bot API (docs/form/telegram.md)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import httpx

from .base import MENU_ITEMS, Button, Incoming, OutMessage, SendError, SendResult

TEXT_LIMIT = 4096
CAPTION_LIMIT = 1024


class TelegramAdapter:
    channel = "telegram"
    text_limit = TEXT_LIMIT
    caption_limit = CAPTION_LIMIT

    def __init__(self, token: str, api: str = "https://api.telegram.org", client: Optional[httpx.Client] = None):
        self.token = token
        self.base = f"{api.rstrip('/')}/bot{token}"
        self.client = client or httpx.Client(timeout=httpx.Timeout(20, connect=10))

    # --- транспорт -----------------------------------------------------------------

    def call(self, method: str, data: dict | None = None, files: dict | None = None) -> dict:
        try:
            if files:
                form = {k: (json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else str(v))
                        for k, v in (data or {}).items() if v is not None}
                resp = self.client.post(f"{self.base}/{method}", data=form, files=files)
            else:
                resp = self.client.post(f"{self.base}/{method}", json={k: v for k, v in (data or {}).items()
                                                                      if v is not None})
        except httpx.HTTPError as exc:
            raise SendError(f"network: {type(exc).__name__}") from exc
        try:
            body = resp.json()
        except ValueError:
            raise SendError(f"http {resp.status_code}", code=resp.status_code, permanent=resp.status_code < 500)
        if body.get("ok"):
            return body.get("result") or {}
        code = int(body.get("error_code") or resp.status_code)
        desc = str(body.get("description") or "")
        retry_after = float((body.get("parameters") or {}).get("retry_after") or 0)
        blocked = code == 403 and ("blocked" in desc or "deactivated" in desc or "kicked" in desc)
        raise SendError(f"{code}: {desc}"[:500], code=code, retry_after=retry_after, blocked=blocked,
                        permanent=code in (400, 403) and not retry_after)

    # --- клавиатуры -------------------------------------------------------------------

    @staticmethod
    def _inline(buttons: list[list[Button]]) -> dict:
        rows = []
        for row in buttons:
            out = []
            for b in row:
                if b.url:
                    out.append({"text": b.text, "url": b.url})
                else:
                    out.append({"text": b.text, "callback_data": b.data})
            rows.append(out)
        return {"inline_keyboard": rows}

    def _markup(self, msg: OutMessage) -> dict | None:
        if msg.request_contact:
            return {
                "keyboard": [[{"text": "Отправить телефон", "request_contact": True}]],
                "resize_keyboard": True,
                "one_time_keyboard": True,
            }
        if msg.buttons:
            return self._inline(msg.buttons)
        if msg.menu is True:
            return {
                "keyboard": [[{"text": MENU_ITEMS[0]}, {"text": MENU_ITEMS[1]}],
                             [{"text": MENU_ITEMS[2]}, {"text": MENU_ITEMS[3]}]],
                "resize_keyboard": True,
                "is_persistent": True,
            }
        if msg.menu is False:
            return {"remove_keyboard": True}
        return None

    # --- операции ------------------------------------------------------------------------

    def send(self, chat_id: str, msg: OutMessage) -> SendResult:
        markup = self._markup(msg)
        if msg.photo:
            data = {"chat_id": chat_id, "caption": msg.text, "parse_mode": "HTML", "reply_markup": markup}
            if msg.photo.telegram_file_id:
                data["photo"] = msg.photo.telegram_file_id
                result = self.call("sendPhoto", data)
                return SendResult(message_id=str(result.get("message_id", "")))
            path = Path(msg.photo.path)
            with path.open("rb") as fh:
                result = self.call("sendPhoto", data, files={"photo": (path.name, fh)})
            sizes = result.get("photo") or []
            file_id = sizes[-1]["file_id"] if sizes else ""
            return SendResult(message_id=str(result.get("message_id", "")), telegram_file_id=file_id)
        result = self.call("sendMessage", {
            "chat_id": chat_id,
            "text": msg.text,
            "parse_mode": "HTML",
            "reply_markup": markup,
            "link_preview_options": {"is_disabled": True},
        })
        return SendResult(message_id=str(result.get("message_id", "")))

    def edit(self, chat_id: str, message_id: str, msg: OutMessage) -> None:
        try:
            self.call("editMessageText", {
                "chat_id": chat_id,
                "message_id": int(message_id),
                "text": msg.text,
                "parse_mode": "HTML",
                "reply_markup": self._inline(msg.buttons) if msg.buttons else {"inline_keyboard": []},
                "link_preview_options": {"is_disabled": True},
            })
        except SendError as exc:
            if "message is not modified" in str(exc):
                return
            raise

    def answer_callback(self, callback_id: str, notice: str = "", chat_id: str = "") -> None:
        if not callback_id:
            return
        self.call("answerCallbackQuery", {"callback_query_id": callback_id, "text": notice[:200] or None})

    # --- разбор входящих ------------------------------------------------------------------

    def parse(self, update: dict) -> Optional[Incoming]:
        key = f"u:{update.get('update_id')}"
        if "message" in update:
            m = update["message"]
            chat = m.get("chat") or {}
            if chat.get("type") != "private":
                return None
            user = m.get("from") or {}
            base = dict(
                channel=self.channel,
                user_id=str(user.get("id")),
                chat_id=str(chat.get("id")),
                first_name=user.get("first_name") or "",
                last_name=user.get("last_name") or "",
                message_id=str(m.get("message_id", "")),
                dedup_key=key,
            )
            if m.get("contact"):
                c = m["contact"]
                own = str(c.get("user_id") or "") == str(user.get("id"))
                return Incoming(kind="contact", contact_phone=c.get("phone_number") or "", contact_is_own=own, **base)
            text = m.get("text")
            if text is None:
                return Incoming(kind="other", **base)
            if text.startswith("/start"):
                payload = text[6:].strip()
                return Incoming(kind="start", text=text, start_payload=payload, **base)
            return Incoming(kind="text", text=text, **base)
        if "callback_query" in update:
            q = update["callback_query"]
            user = q.get("from") or {}
            message = q.get("message") or {}
            return Incoming(
                channel=self.channel,
                kind="callback",
                user_id=str(user.get("id")),
                chat_id=str((message.get("chat") or {}).get("id") or user.get("id")),
                callback_data=q.get("data") or "",
                callback_id=str(q.get("id")),
                message_id=str(message.get("message_id", "")),
                first_name=user.get("first_name") or "",
                last_name=user.get("last_name") or "",
                dedup_key=key,
            )
        if "my_chat_member" in update:
            mc = update["my_chat_member"]
            chat = mc.get("chat") or {}
            if chat.get("type") != "private":
                return None
            status = (mc.get("new_chat_member") or {}).get("status")
            user = mc.get("from") or {}
            kind = "stopped" if status in ("kicked", "left") else "resumed" if status == "member" else "other"
            return Incoming(channel=self.channel, kind=kind, user_id=str(user.get("id")),
                            chat_id=str(chat.get("id")), dedup_key=key)
        return None

    # --- служебное -----------------------------------------------------------------------

    def set_webhook(self, url: str, secret: str) -> dict:
        return self.call("setWebhook", {
            "url": url,
            "secret_token": secret or None,
            "allowed_updates": ["message", "callback_query", "my_chat_member"],
            "drop_pending_updates": False,
        })

    def delete_webhook(self) -> dict:
        return self.call("deleteWebhook", {})

    def get_updates(self, offset: int, timeout: int = 25) -> list[dict]:
        try:
            resp = self.client.post(f"{self.base}/getUpdates", json={
                "offset": offset, "timeout": timeout,
                "allowed_updates": ["message", "callback_query", "my_chat_member"],
            }, timeout=timeout + 10)
            body = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise SendError(f"network: {type(exc).__name__}") from exc
        if not body.get("ok"):
            raise SendError(str(body.get("description")))
        return body.get("result") or []
