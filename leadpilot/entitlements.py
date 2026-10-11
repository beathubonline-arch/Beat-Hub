"""LeadPilot plan entitlements. No payment or user account is provisioned here."""
from dataclasses import dataclass

PLAN_LIMITS = {
    "free": {"monthly_leads": 25, "monthly_drafts": 10, "seats": 1},
    "pro": {"monthly_leads": 500, "monthly_drafts": 200, "seats": 1},
    "business": {"monthly_leads": 3000, "monthly_drafts": 1500, "seats": 5},
    "agency": {"monthly_leads": 15000, "monthly_drafts": 7500, "seats": 20},
}
PAID_STATES = {"active", "trialing"}

def effective_plan(requested_plan: str, subscription_status: str) -> str:
    """Fail closed: only verified active/trialing paid subscriptions unlock paid limits."""
    if requested_plan not in PLAN_LIMITS:
        return "free"
    if requested_plan == "free":
        return "free"
    return requested_plan if subscription_status in PAID_STATES else "free"

def can_use(plan: str, subscription_status: str, metric: str, used: int) -> bool:
    effective = effective_plan(plan, subscription_status)
    if metric not in PLAN_LIMITS[effective] or used < 0:
        return False
    return used < PLAN_LIMITS[effective][metric]
