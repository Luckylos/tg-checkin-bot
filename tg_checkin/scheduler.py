from __future__ import annotations

import asyncio
import hashlib
import logging
import time

import pytz
from apscheduler.triggers.cron import CronTrigger

from .models import JobConfig


def cron_trigger(expr: str, timezone: str) -> CronTrigger:
    fields = expr.split()
    if len(fields) == 5:
        minute, hour, day, month, day_of_week = fields
        second = "0"
    elif len(fields) == 6:
        second, minute, hour, day, month, day_of_week = fields
    else:
        raise ValueError(f"cron must have 5 or 6 fields, got: {expr}")
    return CronTrigger(
        second=second,
        minute=minute,
        hour=hour,
        day=day,
        month=month,
        day_of_week=day_of_week,
        timezone=pytz.timezone(timezone),
    )


def stable_stagger_offset(job: JobConfig) -> int:
    if job.stagger_seconds <= 0:
        return 0
    if job.stagger_mode == "random":
        return int(time.time_ns() % (job.stagger_seconds + 1))
    seed = f"{job.name}:{job.chat_id}:{job.cron}:{job.message}".encode("utf-8")
    return int.from_bytes(hashlib.sha256(seed).digest()[:8], "big") % (job.stagger_seconds + 1)


async def maybe_stagger_job(job: JobConfig) -> None:
    offset = stable_stagger_offset(job)
    if offset > 0:
        logging.getLogger("tg-checkin").info(
            "stagger job=%s chat_id=%s offset_seconds=%s mode=%s",
            job.name,
            job.chat_id,
            offset,
            job.stagger_mode,
        )
        await asyncio.sleep(offset)
