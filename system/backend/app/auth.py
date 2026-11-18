"""Пароли администраторов (scrypt) и сессия админки."""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import time
from collections import defaultdict


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(digest).decode()


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, salt_b64, digest_b64 = stored.split("$")
    except ValueError:
        return False
    if algo != "scrypt":
        return False
    salt = base64.b64decode(salt_b64)
    expected = base64.b64decode(digest_b64)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return hmac.compare_digest(digest, expected)


class LoginLimiter:
    """Не больше 10 неудачных входов за 15 минут с одного адреса."""

    def __init__(self, limit: int = 10, window: int = 900):
        self.limit = limit
        self.window = window
        self.fails: dict[str, list[float]] = defaultdict(list)

    def blocked(self, key: str) -> bool:
        now = time.monotonic()
        self.fails[key] = [t for t in self.fails[key] if now - t < self.window]
        return len(self.fails[key]) >= self.limit

    def fail(self, key: str) -> None:
        self.fails[key].append(time.monotonic())

    def reset(self, key: str) -> None:
        self.fails.pop(key, None)


limiter = LoginLimiter()
