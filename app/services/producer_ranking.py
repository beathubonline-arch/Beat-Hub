from datetime import datetime, timedelta

from sqlalchemy import case, func
from sqlalchemy.orm import Session

from app.models.engagement import (
    EngagementEvent,
    EngagementType,
)
from app.models.music import Track
from app.models.order import Order, OrderStatus
from app.models.profile import Profile


def get_top_producers(
    db: Session,
    days: int = 30,
    limit: int = 5,
):
    """
    Returns the top producers based on verified marketplace activity
    during the rolling period.

    Scoring:

        Track view       = 1 point
        Preview play     = 2 points
        Purchased download = 5 points
        Completed sale   = 8 points

    Purchases are intentionally weighted heavily because they represent
    actual marketplace conversion rather than passive activity.
    """

    days = max(1, int(days))
    limit = max(1, min(int(limit), 20))

    since = datetime.utcnow() - timedelta(days=days)

    engagement_rows = (
        db.query(
            EngagementEvent.creator_profile_id.label(
                "creator_profile_id"
            ),
            func.sum(
                case(
                    (
                        EngagementEvent.event_type
                        == EngagementType.VIEW,
                        1,
                    ),
                    (
                        EngagementEvent.event_type
                        == EngagementType.PREVIEW_PLAY,
                        2,
                    ),
                    (
                        EngagementEvent.event_type
                        == EngagementType.DOWNLOAD,
                        5,
                    ),
                    else_=0,
                )
            ).label("engagement_score"),
            func.sum(
                case(
                    (
                        EngagementEvent.event_type
                        == EngagementType.VIEW,
                        1,
                    ),
                    else_=0,
                )
            ).label("views"),
            func.sum(
                case(
                    (
                        EngagementEvent.event_type
                        == EngagementType.PREVIEW_PLAY,
                        1,
                    ),
                    else_=0,
                )
            ).label("preview_plays"),
            func.sum(
                case(
                    (
                        EngagementEvent.event_type
                        == EngagementType.DOWNLOAD,
                        1,
                    ),
                    else_=0,
                )
            ).label("downloads"),
        )
        .filter(
            EngagementEvent.created_at >= since
        )
        .group_by(
            EngagementEvent.creator_profile_id
        )
        .all()
    )

    engagement_map = {
        row.creator_profile_id: {
            "engagement_score": int(
                row.engagement_score or 0
            ),
            "views": int(row.views or 0),
            "preview_plays": int(
                row.preview_plays or 0
            ),
            "downloads": int(
                row.downloads or 0
            ),
        }
        for row in engagement_rows
    }

    sales_rows = (
        db.query(
            Track.creator_profile_id.label(
                "creator_profile_id"
            ),
            func.count(Order.id).label(
                "sales"
            ),
        )
        .join(
            Order,
            Order.track_id == Track.id,
        )
        .filter(
            Order.status == OrderStatus.COMPLETED,
            Order.completed_at.isnot(None),
            Order.completed_at >= since,
        )
        .group_by(
            Track.creator_profile_id
        )
        .all()
    )

    sales_map = {
        row.creator_profile_id: int(
            row.sales or 0
        )
        for row in sales_rows
    }

    profile_ids = set(engagement_map)
    profile_ids.update(sales_map)

    if not profile_ids:
        return []

    profiles = (
        db.query(Profile)
        .filter(
            Profile.id.in_(profile_ids),
            Profile.is_producer.is_(True),
        )
        .all()
    )

    profile_map = {
        profile.id: profile
        for profile in profiles
    }

    rankings = []

    for profile_id in profile_ids:

        profile = profile_map.get(profile_id)

        if not profile:
            continue

        engagement = engagement_map.get(
            profile_id,
            {
                "engagement_score": 0,
                "views": 0,
                "preview_plays": 0,
                "downloads": 0,
            },
        )

        sales = sales_map.get(
            profile_id,
            0,
        )

        impact_score = (
            engagement["engagement_score"]
            + (sales * 8)
        )

        rankings.append(
            {
                "profile": profile,
                "impact_score": impact_score,
                "views": engagement["views"],
                "preview_plays": engagement[
                    "preview_plays"
                ],
                "downloads": engagement[
                    "downloads"
                ],
                "sales": sales,
                "days": days,
            }
        )

    rankings.sort(
        key=lambda item: (
            item["impact_score"],
            item["downloads"],
            item["sales"],
            item["preview_plays"],
        ),
        reverse=True,
    )

    for index, item in enumerate(
        rankings[:limit],
        start=1,
    ):
        item["rank"] = index

    return rankings[:limit]
