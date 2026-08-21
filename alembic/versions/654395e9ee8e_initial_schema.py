"""BeatHub initial schema.

Revision ID: 654395e9ee8e
Revises:
Create Date: 2026-08-19 02:27:52.073393
"""

from alembic import op
import sqlalchemy as sa


# ---------------------------------------------------------------------
# Revision identifiers
# ---------------------------------------------------------------------

revision = "654395e9ee8e"
down_revision = None
branch_labels = None
depends_on = None


# ---------------------------------------------------------------------
# Upgrade
# ---------------------------------------------------------------------

def upgrade() -> None:

    # ================================================================
    # USERS
    # ================================================================

    op.create_table(
        "users",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("hashed_password", sa.String(length=255), nullable=False),
        sa.Column(
            "role",
            sa.Enum(
                "VISITOR",
                "BUYER",
                "CREATOR",
                "ADMIN",
                name="userrole",
            ),
            nullable=False,
        ),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("is_verified", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("reset_token", sa.String(length=255), nullable=True),
        sa.Column(
            "reset_token_expires",
            sa.DateTime(),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index(
        op.f("ix_users_email"),
        "users",
        ["email"],
        unique=True,
    )

    # ================================================================
    # PROFILES
    # ================================================================

    op.create_table(
        "profiles",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("stage_name", sa.String(length=120), nullable=False),
        sa.Column("slug", sa.String(length=140), nullable=False),
        sa.Column("bio", sa.Text(), nullable=True),
        sa.Column("avatar_path", sa.String(length=500), nullable=True),
        sa.Column("instagram_url", sa.String(length=300), nullable=True),
        sa.Column("twitter_url", sa.String(length=300), nullable=True),
        sa.Column("youtube_url", sa.String(length=300), nullable=True),
        sa.Column("website_url", sa.String(length=300), nullable=True),
        sa.Column("is_producer", sa.Boolean(), nullable=False),
        sa.Column("is_dj", sa.Boolean(), nullable=False),
        sa.Column("is_artist", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id"),
    )

    op.create_index(
        op.f("ix_profiles_slug"),
        "profiles",
        ["slug"],
        unique=True,
    )

    # ================================================================
    # ALBUMS
    # ================================================================

    op.create_table(
        "albums",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column(
            "creator_profile_id",
            sa.String(length=36),
            nullable=False,
        ),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("slug", sa.String(length=220), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("genre", sa.String(length=80), nullable=True),
        sa.Column("artwork_path", sa.String(length=500), nullable=True),
        sa.Column("release_date", sa.DateTime(), nullable=True),
        sa.Column("is_published", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["creator_profile_id"],
            ["profiles.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index(
        op.f("ix_albums_creator_profile_id"),
        "albums",
        ["creator_profile_id"],
        unique=False,
    )

    op.create_index(
        op.f("ix_albums_slug"),
        "albums",
        ["slug"],
        unique=True,
    )

    # ================================================================
    # TRACKS
    # ================================================================

    op.create_table(
        "tracks",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column(
            "creator_profile_id",
            sa.String(length=36),
            nullable=False,
        ),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("slug", sa.String(length=220), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("genre", sa.String(length=80), nullable=True),
        sa.Column("bpm", sa.Integer(), nullable=True),
        sa.Column("tags", sa.String(length=300), nullable=True),
        sa.Column("cover_art_path", sa.String(length=500), nullable=True),
        sa.Column(
            "audio_file_path",
            sa.String(length=500),
            nullable=False,
        ),
        sa.Column(
            "preview_file_path",
            sa.String(length=500),
            nullable=True,
        ),
        sa.Column(
            "price",
            sa.Numeric(precision=12, scale=2),
            nullable=False,
        ),
        sa.Column(
            "sales_model",
            sa.Enum(
                "EXCLUSIVE",
                "NON_EXCLUSIVE",
                name="salesmodel",
            ),
            nullable=False,
        ),
        sa.Column("is_sold", sa.Boolean(), nullable=False),
        sa.Column("is_published", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["creator_profile_id"],
            ["profiles.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index(
        op.f("ix_tracks_creator_profile_id"),
        "tracks",
        ["creator_profile_id"],
        unique=False,
    )

    op.create_index(
        op.f("ix_tracks_slug"),
        "tracks",
        ["slug"],
        unique=True,
    )

    # ================================================================
    # CREATOR WITHDRAWALS
    #
    # IMPORTANT:
    # Store status as VARCHAR rather than PostgreSQL ENUM.
    # This prevents the pending/PENDING mismatch that broke the app.
    # ================================================================

    op.create_table(
        "withdrawal_requests",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column(
            "creator_profile_id",
            sa.String(length=36),
            nullable=False,
        ),
        sa.Column(
            "amount",
            sa.Numeric(precision=12, scale=2),
            nullable=False,
        ),
        sa.Column(
            "phone_number",
            sa.String(length=20),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(length=30),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("admin_note", sa.Text(), nullable=True),
        sa.Column(
            "payout_reference",
            sa.String(length=100),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
        ),
        sa.Column(
            "resolved_at",
            sa.DateTime(),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(
            ["creator_profile_id"],
            ["profiles.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index(
        op.f("ix_withdrawal_requests_creator_profile_id"),
        "withdrawal_requests",
        ["creator_profile_id"],
        unique=False,
    )

    op.create_index(
        op.f("ix_withdrawal_requests_status"),
        "withdrawal_requests",
        ["status"],
        unique=False,
    )

    # ================================================================
    # ALBUM TRACKS
    # ================================================================

    op.create_table(
        "album_tracks",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("album_id", sa.String(length=36), nullable=False),
        sa.Column("track_id", sa.String(length=36), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["album_id"],
            ["albums.id"],
        ),
        sa.ForeignKeyConstraint(
            ["track_id"],
            ["tracks.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index(
        op.f("ix_album_tracks_album_id"),
        "album_tracks",
        ["album_id"],
        unique=False,
    )

    op.create_index(
        op.f("ix_album_tracks_track_id"),
        "album_tracks",
        ["track_id"],
        unique=False,
    )

    # ================================================================
    # ORDERS
    # ================================================================

    op.create_table(
        "orders",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column(
            "order_number",
            sa.String(length=40),
            nullable=False,
        ),
        sa.Column(
            "buyer_id",
            sa.String(length=36),
            nullable=False,
        ),
        sa.Column(
            "track_id",
            sa.String(length=36),
            nullable=True,
        ),
        sa.Column(
            "album_id",
            sa.String(length=36),
            nullable=True,
        ),
        sa.Column(
            "sales_model_at_purchase",
            sa.String(length=20),
            nullable=False,
        ),
        sa.Column(
            "gross_amount",
            sa.Numeric(precision=12, scale=2),
            nullable=False,
        ),
        sa.Column(
            "commission_amount",
            sa.Numeric(precision=12, scale=2),
            nullable=False,
        ),
        sa.Column(
            "net_amount",
            sa.Numeric(precision=12, scale=2),
            nullable=False,
        ),
        sa.Column(
            "commission_percent_at_purchase",
            sa.Numeric(precision=5, scale=2),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "PENDING",
                "COMPLETED",
                "FAILED",
                "REJECTED",
                name="orderstatus",
            ),
            nullable=False,
        ),
        sa.Column(
            "phone_number",
            sa.String(length=20),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["album_id"],
            ["albums.id"],
        ),
        sa.ForeignKeyConstraint(
            ["buyer_id"],
            ["users.id"],
        ),
        sa.ForeignKeyConstraint(
            ["track_id"],
            ["tracks.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index(
        op.f("ix_orders_album_id"),
        "orders",
        ["album_id"],
        unique=False,
    )

    op.create_index(
        op.f("ix_orders_buyer_id"),
        "orders",
        ["buyer_id"],
        unique=False,
    )

    op.create_index(
        op.f("ix_orders_order_number"),
        "orders",
        ["order_number"],
        unique=True,
    )

    op.create_index(
        op.f("ix_orders_status"),
        "orders",
        ["status"],
        unique=False,
    )

    op.create_index(
        op.f("ix_orders_track_id"),
        "orders",
        ["track_id"],
        unique=False,
    )

    # ================================================================
    # CREATOR LEDGER
    # ================================================================

    op.create_table(
        "creator_ledger_entries",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column(
            "creator_profile_id",
            sa.String(length=36),
            nullable=False,
        ),
        sa.Column(
            "order_id",
            sa.String(length=36),
            nullable=True,
        ),
        sa.Column(
            "withdrawal_request_id",
            sa.String(length=36),
            nullable=True,
        ),
        sa.Column(
            "amount",
            sa.Numeric(precision=12, scale=2),
            nullable=False,
        ),
        sa.Column(
            "description",
            sa.String(length=255),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["creator_profile_id"],
            ["profiles.id"],
        ),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["orders.id"],
        ),
        sa.ForeignKeyConstraint(
            ["withdrawal_request_id"],
            ["withdrawal_requests.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index(
        op.f("ix_creator_ledger_entries_creator_profile_id"),
        "creator_ledger_entries",
        ["creator_profile_id"],
        unique=False,
    )

    # ================================================================
    # EXCLUSIVE OWNERSHIP
    # ================================================================

    op.create_table(
        "exclusive_ownership_locks",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("track_id", sa.String(length=36), nullable=False),
        sa.Column("order_id", sa.String(length=36), nullable=False),
        sa.Column("locked_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["orders.id"],
        ),
        sa.ForeignKeyConstraint(
            ["track_id"],
            ["tracks.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("order_id"),
    )

    op.create_index(
        op.f("ix_exclusive_ownership_locks_track_id"),
        "exclusive_ownership_locks",
        ["track_id"],
        unique=True,
    )

    # ================================================================
    # LICENSES
    # ================================================================

    op.create_table(
        "licenses",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("order_id", sa.String(length=36), nullable=False),
        sa.Column("buyer_id", sa.String(length=36), nullable=False),
        sa.Column("track_id", sa.String(length=36), nullable=True),
        sa.Column("album_id", sa.String(length=36), nullable=True),
        sa.Column("granted_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["album_id"],
            ["albums.id"],
        ),
        sa.ForeignKeyConstraint(
            ["buyer_id"],
            ["users.id"],
        ),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["orders.id"],
        ),
        sa.ForeignKeyConstraint(
            ["track_id"],
            ["tracks.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("order_id"),
    )

    op.create_index(
        op.f("ix_licenses_album_id"),
        "licenses",
        ["album_id"],
        unique=False,
    )

    op.create_index(
        op.f("ix_licenses_buyer_id"),
        "licenses",
        ["buyer_id"],
        unique=False,
    )

    op.create_index(
        op.f("ix_licenses_track_id"),
        "licenses",
        ["track_id"],
        unique=False,
    )

    # ================================================================
    # PAYMENT TRANSACTIONS
    # ================================================================

    op.create_table(
        "payment_transactions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("order_id", sa.String(length=36), nullable=False),
        sa.Column(
            "merchant_request_id",
            sa.String(length=100),
            nullable=True,
        ),
        sa.Column(
            "checkout_request_id",
            sa.String(length=100),
            nullable=False,
        ),
        sa.Column(
            "phone_number",
            sa.String(length=20),
            nullable=False,
        ),
        sa.Column(
            "amount",
            sa.Numeric(precision=12, scale=2),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "PENDING",
                "SUCCESS",
                "FAILED",
                "CANCELLED",
                name="paymentstatus",
            ),
            nullable=False,
        ),
        sa.Column(
            "mpesa_receipt_number",
            sa.String(length=60),
            nullable=True,
        ),
        sa.Column(
            "result_code",
            sa.String(length=10),
            nullable=True,
        ),
        sa.Column(
            "result_desc",
            sa.String(length=300),
            nullable=True,
        ),
        sa.Column(
            "raw_callback_payload",
            sa.Text(),
            nullable=True,
        ),
        sa.Column(
            "callback_processed",
            sa.Boolean(),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["orders.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("order_id"),
    )

    op.create_index(
        op.f("ix_payment_transactions_checkout_request_id"),
        "payment_transactions",
        ["checkout_request_id"],
        unique=True,
    )

    op.create_index(
        op.f("ix_payment_transactions_status"),
        "payment_transactions",
        ["status"],
        unique=False,
    )


# ---------------------------------------------------------------------
# Downgrade
# ---------------------------------------------------------------------

def downgrade() -> None:

    op.drop_index(
        op.f("ix_payment_transactions_status"),
        table_name="payment_transactions",
    )

    op.drop_index(
        op.f("ix_payment_transactions_checkout_request_id"),
        table_name="payment_transactions",
    )

    op.drop_table("payment_transactions")

    op.drop_index(
        op.f("ix_licenses_track_id"),
        table_name="licenses",
    )

    op.drop_index(
        op.f("ix_licenses_buyer_id"),
        table_name="licenses",
    )

    op.drop_index(
        op.f("ix_licenses_album_id"),
        table_name="licenses",
    )

    op.drop_table("licenses")

    op.drop_index(
        op.f("ix_exclusive_ownership_locks_track_id"),
        table_name="exclusive_ownership_locks",
    )

    op.drop_table("exclusive_ownership_locks")

    op.drop_index(
        op.f("ix_creator_ledger_entries_creator_profile_id"),
        table_name="creator_ledger_entries",
    )

    op.drop_table("creator_ledger_entries")

    op.drop_index(
        op.f("ix_orders_track_id"),
        table_name="orders",
    )

    op.drop_index(
        op.f("ix_orders_status"),
        table_name="orders",
    )

    op.drop_index(
        op.f("ix_orders_order_number"),
        table_name="orders",
    )

    op.drop_index(
        op.f("ix_orders_buyer_id"),
        table_name="orders",
    )

    op.drop_index(
        op.f("ix_orders_album_id"),
        table_name="orders",
    )

    op.drop_table("orders")

    op.drop_index(
        op.f("ix_album_tracks_track_id"),
        table_name="album_tracks",
    )

    op.drop_index(
        op.f("ix_album_tracks_album_id"),
        table_name="album_tracks",
    )

    op.drop_table("album_tracks")

    op.drop_index(
        op.f("ix_withdrawal_requests_status"),
        table_name="withdrawal_requests",
    )

    op.drop_index(
        op.f("ix_withdrawal_requests_creator_profile_id"),
        table_name="withdrawal_requests",
    )

    op.drop_table("withdrawal_requests")

    op.drop_index(
        op.f("ix_tracks_slug"),
        table_name="tracks",
    )

    op.drop_index(
        op.f("ix_tracks_creator_profile_id"),
        table_name="tracks",
    )

    op.drop_table("tracks")

    op.drop_index(
        op.f("ix_albums_slug"),
        table_name="albums",
    )

    op.drop_index(
        op.f("ix_albums_creator_profile_id"),
        table_name="albums",
    )

    op.drop_table("albums")

    op.drop_index(
        op.f("ix_profiles_slug"),
        table_name="profiles",
    )

    op.drop_table("profiles")

    op.drop_index(
        op.f("ix_users_email"),
        table_name="users",
    )

    op.drop_table("users")
