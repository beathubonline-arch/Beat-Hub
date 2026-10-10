"""Consent-based BeatHub music-drop lead capture. No unsolicited outbound messaging."""
import hashlib
import re
import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import text
from app.database import engine

router = APIRouter(tags=["music-drops"])
_EMAIL = re.compile(r"^[^\\s@]+@[^\\s@]+\\.[^\\s@]+$")
_READY = False

def ensure_table():
    global _READY
    if _READY:
        return
    with engine.begin() as conn:
        conn.execute(text("""
          CREATE TABLE IF NOT EXISTS beathub_music_drop_leads (
            email VARCHAR(255) PRIMARY KEY,
            source VARCHAR(255) NOT NULL,
            consent_at VARCHAR(40) NOT NULL,
            unsubscribed_at VARCHAR(40),
            unsubscribe_hash VARCHAR(64) NOT NULL
          )
        """))
    _READY = True

@router.post("/api/music-drops/subscribe")
def subscribe(request: Request, email: str = Form(...), consent: str = Form(""), website: str = Form(""), source: str = Form("")):
    if website:
        return JSONResponse({"ok": True})
    email = email.strip().lower()
    if not _EMAIL.fullmatch(email) or len(email) > 255:
        raise HTTPException(status_code=422, detail="Enter a valid email address.")
    if consent != "yes":
        raise HTTPException(status_code=422, detail="Please opt in to receive music promotions.")
    source = source.strip()[:255] if source else "/"
    if not source.startswith("/") or source.startswith("//"):
        source = "/"
    ensure_table()
    token = secrets.token_urlsafe(32)
    digest = hashlib.sha256(token.encode()).hexdigest()
    now = datetime.now(timezone.utc).isoformat()
    with engine.begin() as conn:
        conn.execute(text("""
          INSERT INTO beathub_music_drop_leads(email,source,consent_at,unsubscribed_at,unsubscribe_hash)
          VALUES (:email,:source,:now,NULL,:digest)
          ON CONFLICT(email) DO UPDATE SET
            source=excluded.source, consent_at=excluded.consent_at,
            unsubscribed_at=NULL, unsubscribe_hash=excluded.unsubscribe_hash
        """), {"email":email,"source":source,"now":now,"digest":digest})
    # The token is intentionally returned only to the subscriber; never put it in public admin exports.
    return JSONResponse({"ok":True,"message":"You're on the BeatHub music-drop list.","unsubscribe_url":"/music-drops/unsubscribe?email="+email+"&token="+token},headers={"Cache-Control":"no-store"})

@router.get("/music-drops/unsubscribe")
def unsubscribe(email: str, token: str):
    email=email.strip().lower()
    if len(email)>255 or len(token)>128:
        raise HTTPException(status_code=400, detail="Invalid unsubscribe link.")
    ensure_table()
    digest=hashlib.sha256(token.encode()).hexdigest()
    with engine.begin() as conn:
        result=conn.execute(text("""
          UPDATE beathub_music_drop_leads SET unsubscribed_at=:now
          WHERE email=:email AND unsubscribe_hash=:digest
        """),{"email":email,"digest":digest,"now":datetime.now(timezone.utc).isoformat()})
    return HTMLResponse("<html><body style='font:18px system-ui;background:#111;color:#eee;padding:45px'><h1>BeatHub music drops</h1><p>"+("You have been unsubscribed." if result.rowcount else "This unsubscribe link is no longer valid.")+"</p><a style='color:#e8c85a' href='/'>Back to BeatHub</a></body></html>")
