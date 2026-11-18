"""Общий CRUD для разделов админки: метаданные полей, список с поиском,
фильтрами, сортировкой и пагинацией, карточка, создание и правка."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time
from typing import Any, Callable, Optional

from fastapi import HTTPException
from sqlalchemy import Select, func, inspect, or_, select
from sqlalchemy.orm import Session

from ..db import Base
from ..util import as_utc


class ValidationFailed(HTTPException):
    def __init__(self, message: str, field_name: str = ""):
        super().__init__(status_code=422, detail={"message": message, "field": field_name})


@dataclass
class F:
    name: str
    label: str
    type: str = "str"  # str text int float bool date time datetime enum fk m2m image computed
    choices: Optional[dict] = None
    ref: Optional[str] = None  # раздел для fk/m2m
    ref_filter: Optional[dict] = None  # фильтр вариантов (например, вид роли)
    editable: bool = True
    required: bool = False
    immutable: bool = False  # задаётся только при создании (identifier)
    create: bool = True  # поле есть в форме создания
    list: bool = True  # колонка видна по умолчанию
    sortable: bool = True
    getter: Optional[Callable[[Session, Any], Any]] = None
    write_only: bool = False
    help: str = ""

    def meta(self) -> dict:
        return {
            "name": self.name, "label": self.label, "type": self.type, "choices": self.choices,
            "ref": self.ref, "ref_filter": self.ref_filter,
            "editable": self.editable and self.type != "computed", "required": self.required,
            "immutable": self.immutable, "create": self.create and self.type != "computed", "list": self.list,
            "sortable": self.sortable and self.type not in ("computed", "m2m"), "write_only": self.write_only,
            "help": self.help,
        }


@dataclass
class Filter:
    name: str
    label: str
    type: str  # enum bool fk date_range
    choices: Optional[dict] = None
    ref: Optional[str] = None
    apply: Optional[Callable[[Select, Any], Select]] = None  # особый фильтр

    def meta(self) -> dict:
        return {"name": self.name, "label": self.label, "type": self.type, "choices": self.choices, "ref": self.ref}


@dataclass
class Resource:
    name: str
    title: str
    model: type[Base]
    fields: list[F]
    search: Optional[Callable[[str], Any]] = None
    filters: list[Filter] = field(default_factory=list)
    default_sort: str = "-id"
    can_create: bool = True
    can_delete: bool = False
    section: str = ""
    before_save: Optional[Callable[[Session, Any, dict, Any, bool], None]] = None
    after_save: Optional[Callable[[Session, Any, dict, Any, bool], None]] = None
    can_edit: Optional[Callable[[Any], bool]] = None
    base_query: Optional[Callable[[Select], Select]] = None
    label: Optional[Callable[[Any], str]] = None

    def field_map(self) -> dict[str, F]:
        return {f.name: f for f in self.fields}

    def meta(self) -> dict:
        return {
            "name": self.name, "title": self.title, "section": self.section,
            "fields": [f.meta() for f in self.fields],
            "filters": [f.meta() for f in self.filters],
            "default_sort": self.default_sort, "can_create": self.can_create, "can_delete": self.can_delete,
            "searchable": self.search is not None,
        }


REGISTRY: dict[str, Resource] = {}


def register(res: Resource) -> Resource:
    REGISTRY[res.name] = res
    return res


def get_resource(name: str) -> Resource:
    res = REGISTRY.get(name)
    if res is None:
        raise HTTPException(404, "Нет такого раздела")
    return res


# --- сериализация -----------------------------------------------------------------------


def label_of(obj) -> str:
    if obj is None:
        return ""
    return obj.label() if hasattr(obj, "label") else str(obj)


def ref_value(obj) -> Optional[dict]:
    return {"id": obj.id, "label": label_of(obj)} if obj is not None else None


def serialize(session: Session, res: Resource, obj) -> dict:
    out: dict[str, Any] = {"id": obj.id, "_label": res.label(obj) if res.label else label_of(obj)}
    for f in res.fields:
        if f.write_only:
            continue
        if f.getter is not None:
            out[f.name] = f.getter(session, obj)
            continue
        value = getattr(obj, f.name, None)
        if f.type == "fk":
            out[f.name] = ref_value(value)
        elif f.type == "m2m":
            out[f.name] = [ref_value(v) for v in (value or [])]
        elif f.type == "image":
            out[f.name] = f"/media/posters/{value}" if value else ""
        elif isinstance(value, datetime):
            out[f.name] = as_utc(value).isoformat()
        elif isinstance(value, (date, time)):
            out[f.name] = value.isoformat()
        else:
            out[f.name] = value
    if res.can_edit is not None:
        out["_editable"] = res.can_edit(obj)
    return out


# --- список -----------------------------------------------------------------------------


def _column(res: Resource, name: str):
    mapper = inspect(res.model)
    if name in mapper.columns:
        return getattr(res.model, name)
    if name in mapper.relationships:
        rel = mapper.relationships[name]
        if rel.direction.name == "MANYTOONE":
            local = list(rel.local_columns)[0]
            return getattr(res.model, local.key)
    return None


def _parse_value(f: Optional[F], ftype: str, raw: str):
    if ftype == "bool":
        return raw.lower() in ("1", "true", "yes", "да")
    if ftype in ("fk", "int"):
        return int(raw)
    if ftype == "date":
        return date.fromisoformat(raw)
    return raw


def apply_filters(res: Resource, q: Select, params: dict[str, str]) -> Select:
    fmap = res.field_map()
    custom = {f.name: f for f in res.filters}
    for key, raw in params.items():
        if not key.startswith("f.") or raw in ("", None):
            continue
        name = key[2:]
        suffix = ""
        if name.endswith(".from") or name.endswith(".to"):
            name, suffix = name.rsplit(".", 1)
        flt = custom.get(name)
        if flt is not None and flt.apply is not None:
            q = flt.apply(q, raw)
            continue
        column = _column(res, name)
        if column is None:
            continue
        f = fmap.get(name)
        ftype = (flt.type if flt else None) or (f.type if f else "str")
        try:
            if suffix:
                if (f.type if f else ftype) == "datetime":
                    value = datetime.fromisoformat(raw) if "T" in raw else datetime.combine(date.fromisoformat(raw),
                                                                                               time.min)
                    if suffix == "to" and "T" not in raw:
                        value = datetime.combine(date.fromisoformat(raw), time.max)
                else:
                    value = date.fromisoformat(raw)
                q = q.where(column >= value) if suffix == "from" else q.where(column <= value)
                continue
            if raw == "null":
                q = q.where(column.is_(None))
                continue
            values = [_parse_value(f, "fk" if ftype == "fk" else ftype, v) for v in str(raw).split(",") if v != ""]
        except ValueError:
            raise ValidationFailed(f"Неверное значение фильтра «{name}»", name)
        if ftype == "bool":
            q = q.where(column.is_(values[0]))
        elif len(values) == 1:
            q = q.where(column == values[0])
        else:
            q = q.where(column.in_(values))
    return q


def list_query(session: Session, res: Resource, params: dict[str, str]) -> Select:
    q = select(res.model)
    if res.base_query:
        q = res.base_query(q)
    text = (params.get("q") or "").strip()
    if text and res.search is not None:
        q = q.where(res.search(f"%{text}%"))
    return apply_filters(res, q, params)


def list_objects(session: Session, res: Resource, params: dict[str, str]) -> dict:
    q = list_query(session, res, params)
    total = session.scalar(select(func.count()).select_from(q.order_by(None).subquery()))
    sort = params.get("sort") or res.default_sort
    desc = sort.startswith("-")
    column = _column(res, sort.lstrip("-"))
    if column is not None:
        q = q.order_by(column.desc().nulls_last() if desc else column.asc().nulls_last())
    q = q.order_by(res.model.id.desc() if desc else res.model.id)
    try:
        page = max(int(params.get("page") or 1), 1)
        size = min(max(int(params.get("size") or 50), 1), 500)
    except ValueError:
        page, size = 1, 50
    rows = session.scalars(q.offset((page - 1) * size).limit(size)).all()
    return {"items": [serialize(session, res, o) for o in rows], "total": total, "page": page, "size": size}


def options(session: Session, res: Resource, params: dict[str, str], limit: int = 50) -> list[dict]:
    q = list_query(session, res, params)
    ids = params.get("ids")
    if ids:
        q = q.where(res.model.id.in_([int(x) for x in ids.split(",") if x.isdigit()]))
    q = q.order_by(_column(res, res.default_sort.lstrip("-")) or res.model.id).limit(limit)
    return [{"id": o.id, "label": res.label(o) if res.label else label_of(o)} for o in session.scalars(q)]


# --- запись -----------------------------------------------------------------------------


def _convert(session: Session, f: F, value):
    if value is None or value == "":
        if f.type in ("str", "text"):
            return ""
        if f.type == "m2m":
            return []
        return None
    try:
        if f.type == "int":
            return int(value)
        if f.type == "float":
            return float(str(value).replace(",", "."))
        if f.type == "bool":
            return bool(value) if not isinstance(value, str) else value.lower() in ("1", "true", "yes", "да")
        if f.type == "date":
            return date.fromisoformat(value)
        if f.type == "time":
            return time.fromisoformat(value)
        if f.type == "datetime":
            return datetime.fromisoformat(value)
        if f.type == "enum":
            if value not in (f.choices or {}):
                raise ValidationFailed(f"Недопустимое значение поля «{f.label}»", f.name)
            return value
        if f.type == "fk":
            ref = get_resource(f.ref)
            obj = session.get(ref.model, int(value["id"] if isinstance(value, dict) else value))
            if obj is None:
                raise ValidationFailed(f"Не найдено: «{f.label}»", f.name)
            return obj
        if f.type == "m2m":
            ref = get_resource(f.ref)
            ids = [int(v["id"] if isinstance(v, dict) else v) for v in value]
            objs = list(session.scalars(select(ref.model).where(ref.model.id.in_(ids)))) if ids else []
            return objs
    except (TypeError, ValueError, KeyError):
        raise ValidationFailed(f"Неверное значение поля «{f.label}»", f.name)
    return value


def save(session: Session, res: Resource, obj, data: dict, admin, creating: bool):
    fmap = res.field_map()
    if not creating and res.can_edit is not None and not res.can_edit(obj):
        raise ValidationFailed("Запись нельзя изменить в текущем статусе")
    clean: dict[str, Any] = {}
    for name, value in data.items():
        f = fmap.get(name)
        if f is None or f.type in ("computed", "image"):
            continue
        if not f.editable and not (creating and f.create):
            raise ValidationFailed(f"Поле «{f.label}» не редактируется", name)
        if f.immutable and not creating:
            current = getattr(obj, name)
            if value != current:
                raise ValidationFailed(f"Поле «{f.label}» после создания не меняется", name)
            continue
        clean[name] = _convert(session, f, value)
    if res.before_save:
        res.before_save(session, obj, clean, admin, creating)
    for name, value in clean.items():
        f = fmap[name]
        if f.write_only:
            continue
        setattr(obj, name, value)
    if creating:
        for f in res.fields:
            if f.required and f.create and getattr(obj, f.name, None) in (None, ""):
                raise ValidationFailed(f"Заполните поле «{f.label}»", f.name)
        session.add(obj)
    session.flush()
    if res.after_save:
        res.after_save(session, obj, clean, admin, creating)
    session.flush()
    return obj


def search_ilike(*columns):
    def build(pattern: str):
        return or_(*[c.ilike(pattern) for c in columns])

    return build
