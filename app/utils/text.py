import re
import secrets


def slugify(value: str, fallback_prefix: str = "item") -> str:
    value = re.sub(r"[^a-zA-Z0-9]+", "-", value or "").strip("-").lower()
    return value or f"{fallback_prefix}-{secrets.token_hex(4)}"


def unique_slug(db, model, base_value: str, fallback_prefix: str = "item") -> str:
    base = slugify(base_value, fallback_prefix)
    slug = base
    suffix = 1
    while db.query(model).filter(model.slug == slug).first():
        suffix += 1
        slug = f"{base}-{suffix}"
    return slug
