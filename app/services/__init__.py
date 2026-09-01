"""Application services package."""

# Importing this package installs the post-commit admin notification hooks.
from app.services import admin_event_notifications as _admin_event_notifications  # noqa: F401,E402
