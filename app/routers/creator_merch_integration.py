"""Bridge paid merchandise sales into the existing creator dashboard.

The merchandise checkout has its own order table and payment settlement path.
The creator dashboard historically read only music ``Order`` rows, so a paid
hoodie could succeed in Paystack and the merchandise ledger while remaining
invisible to the creator's sales/balance cards.

This module deliberately wraps the existing dashboard calculation instead of
replacing it. Music orders, withdrawal rules, authentication and all existing
routes remain unchanged. Merchandise is added once from the canonical paid
merchandise order table.
"""

from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace

from sqlalchemy import text


MERCH_ORDER_TABLE = "beathub_merchandise_orders"
MERCH_TABLE = "beathub_merchandise"


def _decimal(value) -> Decimal:
    if value is None:
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal("0")


def _paid_merchandise_stats(db, profile_id):
    """Return creator-owned paid merchandise totals, count and recent rows."""
    rows = db.execute(
        text(
            f"""
            SELECT
                o.id,
                o.total_amount,
                o.commission_amount,
                o.net_amount,
                o.created_at,
                o.paid_at,
                o.quantity,
                m.name AS product_name
            FROM {MERCH_ORDER_TABLE} o
            JOIN {MERCH_TABLE} m ON m.id = o.product_id
            WHERE m.creator_profile_id = :profile_id
              AND o.status = 'paid'
            ORDER BY COALESCE(o.paid_at, o.created_at) DESC
            """
        ),
        {"profile_id": str(profile_id)},
    ).mappings().all()

    gross = sum((_decimal(row["total_amount"]) for row in rows), Decimal("0"))
    commission = sum((_decimal(row["commission_amount"]) for row in rows), Decimal("0"))
    net = sum((_decimal(row["net_amount"]) for row in rows), Decimal("0"))

    recent = []
    for row in rows[:8]:
        completed_at = row["paid_at"] or row["created_at"] or datetime.min
        product_name = str(row["product_name"] or "Merchandise")
        # The existing dashboard template expects ``order.track.title`` and
        # ``order.net_amount``. Keep that template/API contract intact with a
        # tiny read-only adapter rather than changing the existing music Order
        # model or template structure.
        recent.append(
            SimpleNamespace(
                id=str(row["id"]),
                track=SimpleNamespace(title=f"Merch: {product_name}"),
                net_amount=_decimal(row["net_amount"]),
                gross_amount=_decimal(row["total_amount"]),
                commission_amount=_decimal(row["commission_amount"]),
                completed_at=completed_at,
                created_at=row["created_at"] or completed_at,
                merchandise=True,
                quantity=int(row["quantity"] or 1),
            )
        )

    return gross, commission, net, len(rows), recent


def patch_creator_dashboard():
    """Extend the existing dashboard stats with paid merchandise exactly once."""
    from app.routers import dashboard

    original = dashboard._creator_stats
    if getattr(original, "_beathub_merch_integrated", False):
        return

    def creator_stats_with_merch(db, profile_id):
        stats = original(db, profile_id)
        old_net = _decimal(stats.get("net_earnings"))
        old_available = _decimal(stats.get("available_balance"))
        pending = _decimal(stats.get("pending_withdrawal"))
        withdrawn = max(Decimal("0"), old_net - old_available - pending)

        merch_gross, merch_commission, merch_net, merch_count, merch_recent = _paid_merchandise_stats(
            db, profile_id
        )

        stats["total_sales"] = int(stats.get("total_sales", 0)) + merch_count
        stats["gross_revenue"] = _decimal(stats.get("gross_revenue")) + merch_gross
        stats["platform_commission"] = _decimal(stats.get("platform_commission")) + merch_commission
        stats["net_earnings"] = old_net + merch_net

        # Preserve the dashboard's existing withdrawal/pending deductions,
        # then add only the new merchandise net earnings to the available
        # balance. No withdrawal records are changed here.
        stats["available_balance"] = max(
            Decimal("0"),
            stats["net_earnings"] - withdrawn - pending,
        )

        combined = list(stats.get("recent_orders") or []) + merch_recent
        combined.sort(
            key=lambda item: (
                getattr(item, "completed_at", None)
                or getattr(item, "created_at", None)
                or datetime.min
            ),
            reverse=True,
        )
        stats["recent_orders"] = combined[:8]
        stats["recent_merchandise_sales"] = merch_recent
        return stats

    creator_stats_with_merch._beathub_merch_integrated = True
    dashboard._creator_stats = creator_stats_with_merch


# Imported for side effect by app.routers.__init__ before main.py registers
# the dashboard router. This preserves the existing dashboard route and UI.
patch_creator_dashboard()
