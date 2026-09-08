"""Backfill the first 29 days of the 30-day acquisition campaign.

Revision ID: growth_campaign_seed_026
Revises: growth_campaign_day30_025

This migration repairs installations where the campaign table existed but only
its final day was persisted. It is idempotent and never removes operational data.
"""
from datetime import date, datetime, timedelta
import uuid

from alembic import op
import sqlalchemy as sa

revision = "growth_campaign_seed_026"
down_revision = "growth_campaign_day30_025"
branch_labels = None
depends_on = None


DAYS = [
    ("Foundation", "all", "Make the offer obvious", "Publish a founder/product introduction and a clear BeatHub CTA.", "Identify 5 relevant artists/producers to research.", "Track content visits and signups."),
    ("Catalog", "youtube", "Show why the catalog matters", "Publish 1 beat showcase with genre/BPM/license context.", "Find 5 artists whose sound fits one showcased beat.", "Track plays, profile visits and prospect saves."),
    ("Education", "shorts", "Teach artists something useful", "Create a short about choosing beats, licensing or recording.", "Research 5 active independent artists; save only strong fits.", "Track views, profile clicks and saves."),
    ("Participation", "instagram", "Create a creator-to-creator loop", "Post a beat challenge or open-verse style prompt using BeatHub inventory.", "Invite a small set of relevant creators to participate manually.", "Track comments, shares and beat plays."),
    ("Producer spotlight", "youtube", "Give producers a reason to share", "Feature one producer and their best current beat.", "Find 5 artists likely to benefit from the featured producer.", "Track producer shares and store visits."),
    ("Artist pain", "shorts", "Solve a real artist problem", "Create a short around a common independent-artist pain point.", "Research artists discussing that problem publicly.", "Track saves, clicks and signups."),
    ("Week 1 review", "analytics", "Keep only what is working", "Repurpose the best-performing hook from days 1-6.", "Prioritize prospects with the strongest fit scores.", "Compare visits, registrations, plays and purchases."),
    ("Beat discovery", "shorts", "Make discovery addictive", "Post a beat-first short with a strong first-second hook.", "Find artists actively releasing or previewing music.", "Track beat-page visits and plays."),
    ("Social proof", "instagram", "Show real activity", "Share a real producer, beat, milestone or user outcome without exaggeration.", "Find creators who can genuinely relate to the proof.", "Track shares and referral visits."),
    ("Matchmaking", "growth-agent", "Demonstrate personalized matching", "Create content showing how a sound gets matched to a beat.", "Scout 10 prospects and match the strongest fits.", "Track saved prospects and matched prospects."),
    ("Community", "discord", "Start conversations, not ads", "Share one useful music-resource post in an appropriate community where allowed.", "Identify communities with active independent artists.", "Track qualified visits and replies."),
    ("Producer education", "shorts", "Help producers earn", "Explain licensing/pricing/store presentation using BeatHub examples.", "Find producers who could benefit from marketplace distribution.", "Track creator signups and uploads."),
    ("Catalog depth", "youtube", "Increase reasons to browse", "Publish a themed multi-beat showcase.", "Match several prospects to the themed catalog.", "Track marketplace sessions and plays."),
    ("Artist spotlight", "instagram", "Celebrate the customer", "Feature an artist journey or creator story with permission.", "Find artists with public release activity and relevant fit.", "Track profile visits and signups."),
    ("Offer clarity", "shorts", "Remove purchase hesitation", "Explain licenses, pricing and what buyers receive.", "Prepare personalized beat recommendations for qualified prospects.", "Track checkout starts and purchases."),
    ("Referral loop", "all", "Give users a reason to share", "Ask creators to share their BeatHub store/beat when genuinely useful.", "Identify existing creators with shareable catalog pages.", "Track referred visits and signups."),
    ("Hook test", "shorts", "Test a new creative angle", "Publish two variants of the same beat hook with different openings.", "Research prospects reacting to the same genre/sound.", "Compare retention and clicks."),
    ("Founder voice", "youtube", "Build trust", "Publish a candid founder update about building BeatHub for creators.", "Use public prospect research to identify communities where the story is relevant.", "Track direct traffic and registrations."),
    ("Producer collaboration", "instagram", "Create cross-audience reach", "Collaborate with one producer/creator on a useful beat breakdown.", "Identify one high-fit creator for a manual collaboration invite.", "Track collaboration reach and visits."),
    ("Artist workflow", "shorts", "Show the path from idea to beat", "Demonstrate search → listen → license → download in a concise story.", "Match prospects who are actively looking for beats.", "Track funnel progression."),
    ("Community proof", "discord", "Earn attention", "Answer a real creator question with useful information before mentioning BeatHub.", "Research communities and relevant public conversations.", "Track qualified traffic and replies."),
    ("Best beat", "youtube", "Concentrate attention", "Feature the strongest current catalog item and its use case.", "Find 10 prospects matching that beat.", "Track plays, store visits and checkout starts."),
    ("Reactivation", "all", "Bring attention back", "Republish the strongest prior concept with a new hook.", "Revisit qualified prospects that have not progressed.", "Track returning visits and funnel movement."),
    ("UGC prompt", "shorts", "Turn viewers into participants", "Post a creator prompt that invites original responses.", "Identify creators likely to respond; outreach remains manual.", "Track responses and qualified visits."),
    ("Catalog story", "instagram", "Make inventory memorable", "Tell the story behind one beat/producer rather than listing features.", "Find artists who fit the story's genre.", "Track shares and beat plays."),
    ("Conversion test", "marketplace", "Improve buyer confidence", "Test a clearer CTA/message around licensing and download value.", "Prioritize high-intent prospects already engaging with relevant beats.", "Track checkout-start to purchase conversion."),
    ("Creator supply", "all", "Grow quality inventory", "Call attention to a producer onboarding opportunity.", "Find 5 producers with public evidence of active catalog creation.", "Track creator signups and published tracks."),
    ("Winner remix", "shorts", "Scale the winning creative", "Rework the best-performing short into two fresh variants.", "Use the winning audience/sound profile for prospect research.", "Track incremental visits and signups."),
    ("Month review", "analytics", "Decide what deserves month 2", "Publish a transparent progress recap using real numbers only.", "Rank prospects by funnel progress and fit.", "Review the full funnel and identify one growth loop to double down on."),
]


def upgrade() -> None:
    bind = op.get_bind()
    now = datetime.utcnow()
    start = date.today()
    table = sa.table(
        "growth_campaign_days",
        sa.column("id", sa.String()),
        sa.column("day_number", sa.Integer()),
        sa.column("date", sa.Date()),
        sa.column("theme", sa.String()),
        sa.column("primary_channel", sa.String()),
        sa.column("objective", sa.Text()),
        sa.column("content_action", sa.Text()),
        sa.column("outreach_action", sa.Text()),
        sa.column("measurement", sa.Text()),
        sa.column("status", sa.String()),
        sa.column("notes", sa.Text()),
        sa.column("created_at", sa.DateTime()),
        sa.column("updated_at", sa.DateTime()),
    )
    for idx, (theme, channel, objective, content_action, outreach_action, measurement) in enumerate(DAYS, start=1):
        bind.execute(
            sa.text(
                """INSERT INTO growth_campaign_days
                (id, day_number, date, theme, primary_channel, objective, content_action,
                 outreach_action, measurement, status, notes, created_at, updated_at)
                SELECT :id, :day_number, :date, :theme, :channel, :objective, :content_action,
                       :outreach_action, :measurement, 'planned', NULL, :created_at, :updated_at
                WHERE NOT EXISTS (
                    SELECT 1 FROM growth_campaign_days WHERE day_number = :day_number
                )"""
            ),
            {
                "id": str(uuid.uuid4()),
                "day_number": idx,
                "date": start + timedelta(days=idx - 1),
                "theme": theme,
                "channel": channel,
                "objective": objective,
                "content_action": content_action,
                "outreach_action": outreach_action,
                "measurement": measurement,
                "created_at": now,
                "updated_at": now,
            },
        )


def downgrade() -> None:
    pass
