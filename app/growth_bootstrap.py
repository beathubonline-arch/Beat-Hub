from __future__ import annotations

import asyncio
import logging

from app.services.growth_scheduler import growth_scheduler_loop

logger = logging.getLogger("beathub.growth_bootstrap")


def start_growth_scheduler(app) -> None:
    """Start exactly one scheduler task per FastAPI process."""
    if getattr(app.state, "growth_scheduler_task", None) is not None:
        return
    app.state.growth_scheduler_task = asyncio.create_task(growth_scheduler_loop())
    logger.info("Growth Agent zero-budget scheduler started")
