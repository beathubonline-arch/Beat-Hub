import ast
from pathlib import Path
import unittest

from app.routers.auth import _safe_next_url

ROOT = Path(__file__).resolve().parents[1]


class ProductLoginReturnTests(unittest.TestCase):
    def test_safe_next_url_allows_internal_product_destinations(self):
        destinations = ["/checkout/track/sample-beat", "/track/sample-track", "/merch/sample-shirt"]
        for destination in destinations:
            self.assertEqual(_safe_next_url(destination), destination)

    def test_safe_next_url_rejects_external_redirects(self):
        for destination in ["https://evil.example/steal", "//evil.example/steal", "javascript:alert(1)", "evil.example/steal"]:
            self.assertEqual(_safe_next_url(destination), "")

    def test_login_page_supplies_encoded_next_url_for_template(self):
        source = (ROOT / "app" / "routers" / "auth.py").read_text(encoding="utf-8")
        self.assertIn('"next_url": quote(safe_next, safe="")', source)
        self.assertIn('url=safe_next or dashboard_url_for_user(user)', source)

    def test_login_template_preserves_next_in_post_action(self):
        source = (ROOT / "app" / "templates" / "login.html").read_text(encoding="utf-8")
        self.assertIn('{% if next_url %}?next={{ next_url|urlencode }}{% endif %}', source)

    def test_product_entry_points_preserve_the_correct_destination(self):
        checkout = (ROOT / "app" / "routers" / "checkout.py").read_text(encoding="utf-8")
        merch = (ROOT / "app" / "templates" / "merchandise_public.html").read_text(encoding="utf-8")
        self.assertIn('next_url=f"/checkout/track/{quote(slug,safe=\'\')}"', checkout)
        self.assertIn('/login?next=/merch/{{ product.slug }}', merch)

    def test_auth_module_compiles(self):
        source = (ROOT / "app" / "routers" / "auth.py").read_text(encoding="utf-8")
        ast.parse(source)


if __name__ == "__main__":
    unittest.main()
