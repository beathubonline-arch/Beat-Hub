from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

NAIROBI_TZ = ZoneInfo("Africa/Nairobi")
PAYOUT_WEEKDAYS = (1, 3)  # Tuesday, Thursday
PAYOUT_HOUR = 18
PAYOUT_MINIMUM = 500


def now_nairobi() -> datetime:
    return datetime.now(NAIROBI_TZ)


def is_payout_day(now: datetime | None = None) -> bool:
    now = now or now_nairobi()
    return now.weekday() in PAYOUT_WEEKDAYS


def is_payout_window(now: datetime | None = None) -> bool:
    now = now or now_nairobi()
    return is_payout_day(now) and now.hour < PAYOUT_HOUR


def next_payout(now: datetime | None = None) -> datetime:
    now = now or now_nairobi()
    candidate = now.replace(hour=PAYOUT_HOUR, minute=0, second=0, microsecond=0)
    for _ in range(8):
        if candidate.weekday() in PAYOUT_WEEKDAYS and candidate > now:
            return candidate
        candidate += timedelta(days=1)
    return candidate


def payout_label(now: datetime | None = None) -> str:
    return next_payout(now).strftime("%A, %d %b %Y at %I:%M %p EAT")
