from __future__ import annotations

import json

from sqlalchemy.orm import Session

from app.models.growth import GrowthProspect, GrowthTouch
from app.services.growth_acquisition_v6 import build_daily_acquisition_queue
from app.services.growth_contact_v7 import verify_public_contact


async def verify_queue_contacts(db: Session, prospect_ids: list[str] | None = None) -> dict:
    query = db.query(GrowthProspect).filter(GrowthProspect.status.in_(["outreach_ready", "contact_research"]))
    if prospect_ids:
        query = query.filter(GrowthProspect.id.in_(prospect_ids))
    prospects = query.all()
    verified = 0
    research = 0
    rows = []
    for prospect in prospects:
        contact = await verify_public_contact(prospect.public_url, prospect.platform)
        stage = "contact_verified" if contact.get("verified") else "contact_research"
        prospect.status = "outreach_ready" if contact.get("verified") else "contact_research"
        db.add(GrowthTouch(
            prospect_id=prospect.id,
            stage=stage,
            channel=contact.get("contact_type") or prospect.platform or "public_web",
            note=json.dumps(contact, ensure_ascii=False)[:2000],
            approved_by_human=False,
        ))
        if contact.get("verified"):
            verified += 1
        else:
            research += 1
        rows.append({"prospect_id": prospect.id, "name": prospect.name, **contact})
    db.commit()
    return {"verified": verified, "contact_research": research, "contacts": rows}


async def build_contactable_acquisition_queue(db: Session, location: str = "Kenya", limit: int = 8) -> dict:
    """V7: discovery/matching/drafting plus a mandatory public-contact gate."""
    result = await build_daily_acquisition_queue(db, location=location, limit=limit)
    ids = [str(row.get("prospect_id")) for row in result.get("queue", []) if row.get("prospect_id")]
    contact_result = await verify_queue_contacts(db, ids or None)
    result["mode"] = "v7_contactable_public_outreach"
    result["contact_verified"] = contact_result["verified"]
    result["contact_research"] = contact_result["contact_research"]
    result["contacts"] = contact_result["contacts"]
    result["qualified_queue"] = contact_result["verified"]
    result["status"] = "ready" if contact_result["verified"] else "contact_research"
    result["note"] = "Only prospects with a verified public email or supported public DM route remain outreach_ready. No messages are sent automatically."
    return result
