"""Prometheus-Metriken im Textformat, ohne zusätzliche Bibliothek.

Absichtlich wenige, aber die richtigen: lebt der Bot, kommt der Scheduler hinterher, häufen sich
Fehler. Der Endpunkt ist nur mit Token erreichbar (siehe `ops`), nginx reicht ihn nicht nach aussen.
"""

import time
from dataclasses import dataclass, field

from redis.asyncio import Redis
from sqlalchemy import text

from ggbot import __version__
from ggbot.core.db.engine import Database
from ggbot.core.logging import get_logger
from ggbot.core.status import read_counters, read_status

log = get_logger(__name__)

# Seit über einer Minute fällig und nicht abgearbeitet: der Scheduler hängt oder ist aus.
OVERDUE_SECONDS = 60


@dataclass
class Exposition:
    lines: list[str] = field(default_factory=list)

    def metric(
        self,
        name: str,
        help_text: str,
        kind: str,
        samples: list[tuple[dict[str, str], float | int]],
    ) -> None:
        self.lines.append(f"# HELP {name} {help_text}")
        self.lines.append(f"# TYPE {name} {kind}")
        for labels, value in samples:
            rendered = ",".join(f'{k}="{_escape(v)}"' for k, v in sorted(labels.items()))
            self.lines.append(f"{name}{{{rendered}}} {value}" if rendered else f"{name} {value}")

    def render(self) -> str:
        return "\n".join(self.lines) + "\n"


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


async def _jobs(db: Database) -> tuple[dict[str, int], int, float]:
    async with db.session() as session:
        by_state = {
            row[0]: row[1]
            for row in await session.execute(
                text("SELECT state, count(*) FROM scheduled_jobs GROUP BY state")
            )
        }
        overdue = (
            await session.execute(
                text(
                    "SELECT count(*), coalesce(max(extract(epoch FROM now() - run_at)), 0) "
                    "FROM scheduled_jobs "
                    "WHERE state = 'pending' AND run_at < now() - make_interval(secs => :s)"
                ),
                {"s": OVERDUE_SECONDS},
            )
        ).one()
    return by_state, int(overdue[0]), float(overdue[1])


async def collect(db: Database, redis: Redis) -> str:
    out = Exposition()
    out.metric("gg_info", "Build-Informationen", "gauge", [({"version": __version__}, 1)])

    db_up = 1
    jobs: tuple[dict[str, int], int, float] | None = None
    try:
        jobs = await _jobs(db)
    except Exception:
        log.exception("metrics_db_failed")
        db_up = 0
    redis_up = 1
    bot = None
    counters: dict[str, int] = {}
    try:
        await redis.ping()
        bot = await read_status(redis)
        counters = await read_counters(redis)
    except Exception:
        log.exception("metrics_redis_failed")
        redis_up = 0

    out.metric("gg_db_up", "Datenbank erreichbar", "gauge", [({}, db_up)])
    out.metric("gg_redis_up", "Redis erreichbar", "gauge", [({}, redis_up)])
    out.metric(
        "gg_bot_up",
        "Bot hat ein frisches Lebenszeichen hinterlassen",
        "gauge",
        [({}, int(bot is not None))],
    )
    if bot is not None:
        now = time.time()
        out.metric("gg_bot_guilds", "Server, auf denen der Bot ist", "gauge", [({}, bot.guilds)])
        out.metric("gg_bot_shards", "Shards dieses Bot-Prozesses", "gauge", [({}, bot.shards)])
        if bot.latency_ms is not None:
            out.metric(
                "gg_bot_latency_seconds",
                "Gateway-Latenz",
                "gauge",
                [({}, round(bot.latency_ms / 1000, 4))],
            )
        out.metric(
            "gg_bot_uptime_seconds",
            "Laufzeit seit Start",
            "gauge",
            [({}, round(now - bot.started_at))],
        )
    if counters:
        out.metric(
            "gg_bot_commands_total", "Ausgeführte Befehle", "counter", [({}, counters["commands"])]
        )
        out.metric(
            "gg_bot_command_errors_total",
            "Befehle, die mit einem unerwarteten Fehler endeten",
            "counter",
            [({}, counters["command_errors"])],
        )
    if jobs is not None:
        by_state, overdue, lag = jobs
        states = ("pending", "running", "done", "failed")
        out.metric(
            "gg_scheduler_jobs",
            "Jobs nach Zustand",
            "gauge",
            [({"state": s}, by_state.get(s, 0)) for s in states],
        )
        out.metric(
            "gg_scheduler_overdue_jobs",
            f"Jobs, die seit über {OVERDUE_SECONDS} s fällig sind",
            "gauge",
            [({}, overdue)],
        )
        out.metric(
            "gg_scheduler_oldest_overdue_seconds",
            "Verspätung des ältesten fälligen Jobs",
            "gauge",
            [({}, round(lag))],
        )
    return out.render()
