import unittest
from pathlib import Path
from unittest.mock import patch

from starlette.requests import Request

from app.routers import pages


class TestSupportHandling(unittest.TestCase):
    def test_public_support_route_renders_support_page(self):
        request = Request({"type": "http", "method": "GET", "path": "/support", "headers": []})
        with patch.object(pages.templates, "TemplateResponse", return_value="support-response") as render:
            result = pages.support(request, None)
        self.assertEqual(result, "support-response")
        render.assert_called_once()
        self.assertEqual(render.call_args.args[1], "support.html")

    def test_support_page_has_public_mailbox_and_48_hour_target(self):
        template = Path("app/templates/support.html").read_text(encoding="utf-8")
        self.assertIn("support@mybeathub.com", template)
        self.assertIn("48 hours", template)
        self.assertIn("mailto:support@mybeathub.com", template)


if __name__ == "__main__":
    unittest.main()
