"""Shared integrity checks; no network calls or production data at import time."""
import unicodedata
from urllib.parse import urlsplit


def normalized_name(value):
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def safe_url(value):
    try:
        p = urlsplit(value)
        return p.scheme in {"http", "https"} and bool(p.hostname) and not p.username and not p.password and not any(ord(c) < 33 for c in value)
    except ValueError:
        return False


def validate_geography(data, counties):
    errors = []
    constituency_count = ward_count = 0
    if not isinstance(data, dict):
        return {"ok": False, "errors": ["Geography must be an object"], "counties": 0, "constituencies": 0, "wards": 0}
    if set(data) != set(counties):
        errors.append("Canonical county names do not match")
    for county, constituencies in data.items():
        if not isinstance(constituencies, dict) or not constituencies:
            errors.append(f"Missing constituencies: {county}")
            continue
        seen = set()
        for constituency, wards in constituencies.items():
            key = normalized_name(constituency)
            if not key or key in seen:
                errors.append(f"Empty or duplicate constituency: {county}")
            seen.add(key)
            constituency_count += 1
            if not isinstance(wards, list) or not wards:
                errors.append(f"Missing wards: {county}/{constituency}")
                continue
            ward_seen = set()
            for ward in wards:
                key = normalized_name(ward) if isinstance(ward, str) else ""
                if not key or key in ward_seen:
                    errors.append(f"Empty or duplicate ward: {county}/{constituency}")
                ward_seen.add(key)
                ward_count += 1
    if (len(data), constituency_count, ward_count) != (47, 290, 1450):
        errors.append("Expected coverage is 47 counties, 290 constituencies and 1450 wards")
    return {"ok": not errors, "errors": errors, "counties": len(data), "constituencies": constituency_count, "wards": ward_count}


def unique_object(pairs):
    """Reject repeated JSON keys instead of losing evidence while loading."""
    out = {}
    for key, value in pairs:
        if key in out:
            raise ValueError("Duplicate geography key")
        out[key] = value
    return out


def candidate_scope_coverage(geography, rows):
    expected = {('President', '', '', '')}
    for county, constituencies in geography.items():
        if not isinstance(constituencies, dict):
            continue
        for race in ('Governor', 'Senator', 'Woman Representative'):
            expected.add((race, county, '', ''))
        for constituency, wards in constituencies.items():
            expected.add(('Member of Parliament', county, constituency, ''))
            if isinstance(wards, list):
                expected.update(('MCA', county, constituency, w) for w in wards if isinstance(w, str))
    registered = {tuple(row[k] or '' for k in ('race', 'county', 'constituency', 'ward')) for row in rows}
    return {'expected_scopes': len(expected), 'covered_scopes': len(expected & registered), 'missing_scopes': len(expected - registered)}
