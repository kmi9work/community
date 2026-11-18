"""Метрики Prometheus. /metrics слушается только на 127.0.0.1 (nginx не пропускает)."""
from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Histogram, generate_latest
from prometheus_client.core import GaugeMetricFamily

REGISTRY = CollectorRegistry()

INBOUND = Counter("bot_inbound_events_total", "Входящие события мессенджеров", ["channel", "result"],
                  registry=REGISTRY)
SENDS = Counter("bot_sends_total", "Отправки сообщений", ["channel", "status"], registry=REGISTRY)
HANDLE_SECONDS = Histogram("bot_handle_seconds", "Обработка входящего события", ["channel"], registry=REGISTRY)
REQUEST_SECONDS = Histogram("http_request_seconds", "Запросы API", ["method", "route", "code"], registry=REGISTRY)


class DbCollector:
    """Глубина очереди, возраст самой старой строки, прохождения и рассылки по статусам."""

    def collect(self):
        from sqlalchemy import func, select

        from .db import session_scope
        from .models import Broadcast, Delivery, SurveyRun
        from .services.delivery import queue_stats

        try:
            with session_scope() as s:
                depth, age = queue_stats(s)
                runs = s.execute(select(SurveyRun.status, func.count()).group_by(SurveyRun.status)).all()
                bcs = s.execute(select(Broadcast.status, func.count()).group_by(Broadcast.status)).all()
                dels = s.execute(select(Delivery.status, func.count()).group_by(Delivery.status)).all()
        except Exception:  # noqa: BLE001
            return
        g = GaugeMetricFamily("delivery_queue_depth", "Строк доставки в очереди")
        g.add_metric([], depth)
        yield g
        g = GaugeMetricFamily("delivery_queue_oldest_seconds", "Возраст самой старой строки в очереди")
        g.add_metric([], age)
        yield g
        for name, rows in (("survey_runs", runs), ("broadcasts", bcs), ("deliveries", dels)):
            g = GaugeMetricFamily(f"{name}_by_status", f"{name} по статусам", labels=["status"])
            for status, count in rows:
                g.add_metric([status], count)
            yield g


REGISTRY.register(DbCollector())


def render() -> bytes:
    return generate_latest(REGISTRY)
