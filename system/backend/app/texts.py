"""Тексты сообщений бота из personal_docs/messages/ (одно сообщение — один файл).

Текст — блоки ```text ... ``` файла по порядку (варианты сообщения).
Подстановки {{имя}}; если значения нет, убирается «, {{имя}}» или вся строка.
Шаблоны в HTML-разметке Телеграм; значения экранируются.
"""
from __future__ import annotations

import html
import re
from functools import lru_cache
from pathlib import Path

from .config import settings

BLOCK_RE = re.compile(r"```text\n(.*?)\n```", re.S)
VAR_RE = re.compile(r"\{\{\s*(\w+)\s*\}\}")


class TextsMissing(RuntimeError):
    pass


@lru_cache(maxsize=1)
def _load(directory: str) -> dict[str, list[str]]:
    path = Path(directory)
    result: dict[str, list[str]] = {}
    if not path.is_dir():
        return result
    for file in sorted(path.glob("*.md")):
        if file.name.lower() == "readme.md":
            continue
        blocks = BLOCK_RE.findall(file.read_text(encoding="utf-8"))
        if not blocks:
            continue
        result[file.stem] = blocks
        prefix = file.stem.split("-", 1)[0]
        result.setdefault(prefix, blocks)
    return result


def reload() -> None:
    _load.cache_clear()


def templates() -> dict[str, list[str]]:
    return _load(str(settings.personal_docs_dir / "messages"))


def available() -> bool:
    return bool(templates())


def template(key: str, variant: int = 0) -> str:
    blocks = templates().get(key) or templates().get(key.split("-", 1)[0])
    if not blocks:
        raise TextsMissing(f"нет текста сообщения {key}")
    return blocks[min(variant, len(blocks) - 1)]


def render_template(tpl: str, values: dict | None = None, raw: set[str] | None = None) -> str:
    """Подставить значения. raw — переменные, которые уже в HTML (не экранировать)."""
    values = values or {}
    raw = raw or set()

    def val(name: str) -> str:
        v = values.get(name)
        if v is None:
            return ""
        s = str(v)
        return s if name in raw else html.escape(s, quote=False)

    lines_out: list[str] = []
    for line in tpl.split("\n"):
        names = VAR_RE.findall(line)
        if names:
            # «, {{x}}» с пустым значением — убрать вместе с запятой
            line = re.sub(
                r",\s*\{\{\s*(\w+)\s*\}\}",
                lambda m: "" if not val(m.group(1)) else m.group(0),
                line,
            )
            names = VAR_RE.findall(line)
            if any(not val(n) for n in names):
                if all(not val(n) for n in names) or len(names) == 1:
                    continue
            line = VAR_RE.sub(lambda m: val(m.group(1)), line)
        lines_out.append(line)
    text = "\n".join(lines_out)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text


def render(key: str, values: dict | None = None, variant: int = 0, raw: set[str] | None = None) -> str:
    return render_template(template(key, variant), values, raw)


def read_doc(name: str) -> str:
    path = settings.personal_docs_dir / name
    return path.read_text(encoding="utf-8").strip() if path.is_file() else ""


ALLOWED_TAGS = ("b", "i", "u", "s", "code", "pre")


def sanitize_html(text: str) -> str:
    """Текст администратора: экранируем всё, затем возвращаем разрешённые теги."""
    escaped = html.escape(text or "", quote=False)
    for tag in ALLOWED_TAGS:
        escaped = re.sub(rf"&lt;(/?){tag}&gt;", rf"<\1{tag}>", escaped)
    escaped = re.sub(
        r'&lt;a href="(https?://[^"<>\s]+)"&gt;(.*?)&lt;/a&gt;',
        lambda m: f'<a href="{m.group(1)}">{m.group(2)}</a>',
        escaped,
        flags=re.S,
    )
    return escaped


def strip_html(text: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", text or ""))
