from pathlib import Path

from app.services.pricing import format_money


ROOT = Path(__file__).resolve().parents[1]


def test_money_formatting_keeps_usd_distinct_from_kes():
    assert format_money("40", "USD") == "$ 40.00"
    assert format_money("40", "KES") == "KSh 40.00"


def test_music_price_routes_use_recorded_currency():
    checks = {
        "app/templates/track_detail.html": "{{ '$' if (track.currency|default('KES')) == 'USD' else 'KSh' }}",
        "app/templates/profile_detail.html": "{{ '$' if (track.currency|default('KES')) == 'USD' else 'KSh' }}",
        "app/templates/marketplace.html": "{{ '$' if (item.currency|default('KES')) == 'USD' else 'KSh' }}",
        "app/templates/creator_sales_history.html": "{{ '$' if sale.currency == 'USD' else 'KSh' }}",
        "app/templates/account_purchases.html": "{{ '$' if currency == 'USD' else 'KSh' }}",
    }
    for relative_path, marker in checks.items():
        content = (ROOT / relative_path).read_text(encoding="utf-8")
        assert marker in content, f"Missing currency-aware price display in {relative_path}"


def test_marketplace_hot_picks_preserve_currency():
    content = (ROOT / "app/routers/marketplace.py").read_text(encoding="utf-8")
    assert '"currency": item["currency"]' in content
