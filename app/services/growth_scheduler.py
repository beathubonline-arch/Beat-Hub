"""In-process scheduler for the existing BeatHub web service.

This intentionally creates no additional Render service. The scheduler wakes
once after startup and then periodically while the web process is alive.
"""
from __future__ import annotations

import asyncio
import logging

from app.services.growth_worker import run_once

logger = logging.getLogger("beathub.growth_scheduler")


async def growth_scheduler_loop() -> None:
    await asyncio.sleep(15)
    while True:
        try:
            result = await asyncio.to_thread(run_once, False)
            logger.info("Growth Agent scheduler result: %s", result.get("status"))
        except Exception:
            logger.exception("Growth Agent scheduler loop error")
        await asyncio.sleep(24 * 60 * 60)
