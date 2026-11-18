"""Webhook Телеграм и MAX, /healthz, /metrics."""
from __future__ import annotations

import hmac

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy import text

from .. import logs, metrics, texts
from ..bot.core import dispatch
from ..config import settings
from ..db import session_scope
from ..messengers import get_adapter

router = APIRouter()


def _check_secret(expected: str, given: str | None) -> None:
    if expected and not hmac.compare_digest(expected, given or ""):
        raise HTTPException(403, "bad secret")


def _handle(channel: str, payload: dict) -> None:
    adapter = get_adapter(channel)
    if adapter is None:
        raise HTTPException(503, "bot not configured")
    try:
        inc = adapter.parse(payload)
    except Exception:  # noqa: BLE001 — формат события изменился
        metrics.INBOUND.labels(channel, "parse_error").inc()
        logs.error("inbound.parse_failed", channel=channel)
        return
    try:
        with session_scope() as session:
            dispatch(session, adapter, inc)
    except Exception:  # noqa: BLE001 — мессенджеру всегда 200, иначе он повторит событие
        logs.error("inbound.failed", channel=channel)


@router.post("/webhook/telegram")
async def telegram_webhook(request: Request):
    _check_secret(settings.telegram_webhook_secret, request.headers.get("x-telegram-bot-api-secret-token"))
    payload = await request.json()
    from starlette.concurrency import run_in_threadpool

    await run_in_threadpool(_handle, "telegram", payload)
    return {"ok": True}


@router.post("/webhook/max")
async def max_webhook(request: Request):
    _check_secret(settings.max_webhook_secret, request.headers.get("x-max-bot-api-secret"))
    payload = await request.json()
    from starlette.concurrency import run_in_threadpool

    await run_in_threadpool(_handle, "max", payload)
    return {"ok": True}


@router.get("/healthz")
def healthz():
    try:
        with session_scope() as s:
            s.execute(text("select 1"))
    except Exception:  # noqa: BLE001
        raise HTTPException(503, "db unavailable")
    return {"ok": True, "texts": texts.available(),
            "telegram": bool(settings.telegram_token), "max": bool(settings.max_token)}


@router.get("/metrics")
def metrics_endpoint(request: Request):
    # только с самого сервера: порт api слушает 127.0.0.1, а всё, что пришло через
    # nginx, несёт заголовки X-Forwarded-For / X-Real-IP (и /metrics nginx не пропускает)
    if request.headers.get("x-forwarded-for") or request.headers.get("x-real-ip"):
        raise HTTPException(404)
    return PlainTextResponse(metrics.render(), media_type="text/plain; version=0.0.4")
