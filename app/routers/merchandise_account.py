from datetime import datetime

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import get_db
from app.utils.deps import require_user

router = APIRouter(tags=["merchandise-account"])

MERCH_ORDER_TABLE = "beathub_merchandise_orders"
MERCH_TABLE = "beathub_merchandise"


def _esc(value) -> str:
    import html
    return html.escape(str(value or ""))


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
