"""Реестр адаптеров: по каналу — адаптер мессенджера."""
from __future__ import annotations

from typing import Optional

from ..config import settings
from .base import Adapter

_adapters: dict[str, Adapter] = {}


def get_adapter(channel: str) -> Optional[Adapter]:
    if channel in _adapters:
        return _adapters[channel]
    adapter: Optional[Adapter] = None
    if channel == "telegram" and settings.telegram_token:
        from .telegram import TelegramAdapter

        adapter = TelegramAdapter(settings.telegram_token, settings.telegram_api)
    elif channel == "max" and settings.max_token:
        from .max import MaxAdapter

        adapter = MaxAdapter(settings.max_token, settings.max_api)
    if adapter is not None:
        _adapters[channel] = adapter
    return adapter


def set_adapter(channel: str, adapter: Optional[Adapter]) -> None:
    if adapter is None:
        _adapters.pop(channel, None)
    else:
        _adapters[channel] = adapter


def reset() -> None:
    _adapters.clear()
