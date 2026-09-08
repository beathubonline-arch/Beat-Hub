"""In-process scheduler for the existing BeatHub web service."""
from __future__ import annotations

import asyncio
import logging

from app.services.growth_worker_v4 import run_once

logger = logging.getLogger("beathub.growth_scheduler")


async def growth_scheduler_loop() -> None:
    await asyncio.sleep(15)
    while True:
        try:
            result = await asyncio.to_thread(run_once, False)
            logger.info("[BeatHub Growth Agent] scheduler result: %s", result.get("status"))
        except Exception:
            logger.exception("[BeatHub Growth Agent] scheduler loop error")
        await asyncio.sleep(24 * 60 * 60)
