from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import get_db
from app.utils.deps import require_user, require_admin

router = APIRouter(tags=["merchandise-account"])

MERCH_ORDER_TABLE = "beathub_merchandise_orders"
MERCH_TABLE = "beathub_merchandise"


def _esc(value) -> str:
    import html
    return html.escape(str(value or ""))


def _merch_financials(db: Session):
    """Return completed merchandise gross, commission and creator earnings."""
    row = db.execute(
        text(
            f"""
            SELECT
                COALESCE(SUM(total_amount), 0) AS gross,
                COALESCE(SUM(commission_amount), 0) AS commission,
                COALESCE(SUM(net_amount), 0) AS creator
            FROM {MERCH_ORDER_TABLE}
            WHERE status = 'paid'
            """
        )
    ).mappings().first()

    return (
        Decimal(str(row["gross"] or 0)),
        Decimal(str(row["commission"] or 0)),
        Decimal(str(row["creator"] or 0)),
    )


def _patch_admin_financials():
    """Make the existing admin financial helper include paid merchandise.

    This keeps the existing admin routes/templates intact while making
    merchandise part of the same platform accounting totals. The music
    Order model remains untouched and merchandise is added exactly once.
    """
    from app.routers import admin

    original = admin.get_platform_financials

    if getattr(original, "_beathub_merch_integrated", False):
        return

    def get_platform_financials_with_merch(db: Session):
        music_gross, music_commission, music_creator = original(db)
        merch_gross, merch_commission, merch_creator = _merch_financials(db)
        return (
            music_gross + merch_gross,
            music_commission + merch_commission,
            music_creator + merch_creator,
        )

    get_platform_financials_with_merch._beathub_merch_integrated = True
    admin.get_platform_financials = get_platform_financials_with_merch


# main.py imports all routers before registering them. This keeps the existing
# admin router and all of its withdrawal logic intact while extending its
# financial calculation to include paid merchandise.
_patch_admin_financials()


@router.get("/account/merchandise-orders", response_class=HTMLResponse)
def merchandise_orders(
    db: Session = Depends(get_db),
    user=Depends(require_user),
):
    rows = db.execute(
        text(
            f"""
            SELECT
                o.id,
                o.quantity,
                o.total_amount,
                o.status,
                o.created_at,
                m.name AS product_name
            FROM {MERCH_ORDER_TABLE} o
            JOIN {MERCH_TABLE} m ON m.id = o.product_id
            WHERE o.buyer_id = :buyer_id
            ORDER BY o.created_at DESC
            LIMIT 100
            """
        ),
        {"buyer_id": str(user.id)},
    ).mappings().all()

    items = []
    for row in rows:
        status = str(row["status"] or "pending_payment").replace("_", " ").title()
        created = row["created_at"]
        if isinstance(created, datetime):
            created_text = created.strftime("%d %b %Y, %H:%M")
        else:
            created_text = str(created or "")

        try:
            total_text = f"{float(row['total_amount'] or 0):.2f}"
        except (TypeError, ValueError):
            total_text = "0.00"

        items.append(
            f"""
            <a class="order" href="/merch/orders/{_esc(row['id'])}">
              <div>
                <strong>{_esc(row['product_name'])}</strong>
                <span>Qty {int(row['quantity'] or 1)} · {_esc(created_text)}</span>
              </div>
              <div class="right">
                <strong>KSh {_esc(total_text)}</strong>
                <span>{_esc(status)}</span>
              </div>
            </a>
            """
        )

    body = "".join(items) if items else """
      <div class="empty">You do not have any merchandise orders yet.</div>
    """

    return HTMLResponse(
        f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>My Merchandise Orders · BeatHub</title>
<style>
body{{margin:0;background:#070709;color:#f7f7f8;font-family:Inter,system-ui,-apple-system,Segoe UI,sans-serif}}
.wrap{{width:min(900px,92%);margin:0 auto;padding:55px 0 80px}}
a{{color:inherit;text-decoration:none}}
.back{{color:#aaa;font-size:13px;font-weight:700}}
h1{{font-size:42px;letter-spacing:-1.8px;margin:24px 0 8px}}
p{{color:#999;line-height:1.6}}
.list{{display:grid;gap:12px;margin-top:28px}}
.order{{display:flex;justify-content:space-between;gap:20px;padding:20px;border:1px solid #292932;border-radius:16px;background:#111116;transition:.2s ease}}
.order:hover{{border-color:#55555f;transform:translateY(-1px)}}
.order strong{{display:block;font-size:14px}}
.order span{{display:block;color:#85858f;font-size:12px;margin-top:5px}}
.right{{text-align:right;white-space:nowrap}}
.empty{{padding:35px;border:1px dashed #34343d;border-radius:16px;color:#8c8c96;text-align:center}}
@media(max-width:600px){{h1{{font-size:34px}}.order{{flex-direction:column}}.right{{text-align:left}}}}
</style></head>
<body><main class="wrap">
<a class="back" href="/account">← Back to My Account</a>
<h1>My Merchandise Orders</h1>
<p>Track merchandise purchases made from your BeatHub account.</p>
<section class="list">{body}</section>
</main></body></html>"""
    )


@router.get("/admin/merchandise", response_class=HTMLResponse)
def admin_merchandise_orders(
    db: Session = Depends(get_db),
    user=Depends(require_admin),
):
    """Admin merchandise sales ledger with completed financial totals."""
    rows = db.execute(
        text(
            f"""
            SELECT
                o.id,
                o.quantity,
                o.unit_price,
                o.total_amount,
                o.commission_amount,
                o.net_amount,
                o.status,
                o.payment_provider,
                o.checkout_request_id,
                o.mpesa_receipt,
                o.created_at,
                o.paid_at,
                m.name AS product_name
            FROM {MERCH_ORDER_TABLE} o
            LEFT JOIN {MERCH_TABLE} m ON m.id = o.product_id
            ORDER BY o.created_at DESC
            LIMIT 200
            """
        )
    ).mappings().all()

    gross, commission, creator = _merch_financials(db)
    completed_count = sum(1 for row in rows if str(row["status"] or "") == "paid")

    rows_html = []
    for row in rows:
        status = str(row["status"] or "pending_payment").replace("_", " ").title()
        rows_html.append(
            f"""
            <tr>
              <td>{_esc(row['product_name'] or 'Unknown product')}</td>
              <td>{int(row['quantity'] or 1)}</td>
              <td>KSh {Decimal(str(row['total_amount'] or 0)):.2f}</td>
              <td>KSh {Decimal(str(row['commission_amount'] or 0)):.2f}</td>
              <td>KSh {Decimal(str(row['net_amount'] or 0)):.2f}</td>
              <td>{_esc(status)}</td>
              <td>{_esc(row['mpesa_receipt'] or row['checkout_request_id'] or '—')}</td>
              <td>{_esc(row['paid_at'] or row['created_at'] or '—')}</td>
            </tr>
            """
        )

    table = "".join(rows_html) or '<tr><td colspan="8">No merchandise orders yet.</td></tr>'

    return HTMLResponse(
        f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Merchandise Sales · BeatHub Admin</title>
<style>
body{{margin:0;background:#070707;color:#f7f7f7;font-family:Inter,system-ui,-apple-system,Segoe UI,sans-serif}}
.wrap{{width:min(1250px,94%);margin:auto;padding:38px 0 70px}}
a{{color:#f4c842;text-decoration:none}}h1{{margin:12px 0 6px}}p{{color:#999}}
.cards{{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin:25px 0}}
.card{{background:#111;border:1px solid #292929;border-radius:14px;padding:18px}}.label{{color:#888;font-size:12px}}.value{{font-size:25px;font-weight:800;margin-top:7px}}
.table-wrap{{overflow:auto;border:1px solid #292929;border-radius:14px;background:#111}}
table{{width:100%;border-collapse:collapse;min-width:1000px}}th,td{{padding:13px;border-bottom:1px solid #222;text-align:left;font-size:12px}}th{{color:#999;font-size:11px;text-transform:uppercase}}
@media(max-width:700px){{.cards{{grid-template-columns:1fr}}}}
</style></head>
<body><main class="wrap">
<a href="/admin">← Admin Dashboard</a><h1>Merchandise Sales</h1>
<p>Paid merchandise orders are included in the main admin financial totals.</p>
<section class="cards">
<div class="card"><div class="label">Paid merchandise sales</div><div class="value">{completed_count}</div></div>
<div class="card"><div class="label">Merchandise gross</div><div class="value">KSh {gross:.2f}</div></div>
<div class="card"><div class="label">BeatHub merchandise commission</div><div class="value">KSh {commission:.2f}</div></div>
</section>
<div class="table-wrap"><table><thead><tr><th>Product</th><th>Qty</th><th>Gross</th><th>Commission</th><th>Creator</th><th>Status</th><th>Payment Ref</th><th>Date</th></tr></thead><tbody>{table}</tbody></table></div>
</main></body></html>"""
    )
