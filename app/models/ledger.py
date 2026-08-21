import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


# ============================================================
# WITHDRAWAL STATUS
# ============================================================
#
# IMPORTANT:
# These are plain strings.
#
# DO NOT change this back to sqlalchemy.Enum.
#
# Database values are always:
#
#   pending
#   approved
#   processing
#   paid
#   rejected
#
# This prevents PostgreSQL enum-name/value mismatches.
# ============================================================

class WithdrawalStatus:
    PENDING = "pending"
    APPROVED = "approved"
    PROCESSING = "processing"
    PAID = "paid"
    REJECTED = "rejected"

    ALL = (
        PENDING,
        APPROVED,
        PROCESSING,
        PAID,
        REJECTED,
    )


# ============================================================
# BACKWARD COMPATIBILITY
# ============================================================
#
# Some versions of admin.py may still import:
#
#   AdminWithdrawalStatus
#
# Keep this alias so the application cannot crash simply because
# an older router still uses that name.
#
# Both names point to exactly the same status values.
# ============================================================

AdminWithdrawalStatus = WithdrawalStatus


# ============================================================
# CREATOR LEDGER
# ============================================================

class CreatorLedgerEntry(Base):
    """
    Append-only financial ledger for creator earnings.

    Positive amount:
        Creator receives money.

    Negative amount:
        Creator money is deducted, e.g. withdrawal.
    """

    __tablename__ = "creator_ledger_entries"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )

    creator_profile_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("profiles.id"),
        nullable=False,
        index=True,
    )

    order_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("orders.id"),
        nullable=True,
        index=True,
    )

    withdrawal_request_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("withdrawal_requests.id"),
        nullable=True,
        index=True,
    )

    amount: Mapped[Numeric] = mapped_column(
        Numeric(12, 2),
        nullable=False,
    )

    description: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )


# ============================================================
# CREATOR WITHDRAWAL
# ============================================================

class WithdrawalRequest(Base):
    """
    Withdrawal requested by a BeatHub creator.

    This is separate from BeatHub/admin withdrawals.
    """

    __tablename__ = "withdrawal_requests"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )

    creator_profile_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("profiles.id"),
        nullable=False,
        index=True,
    )

    amount: Mapped[Numeric] = mapped_column(
        Numeric(12, 2),
        nullable=False,
    )

    phone_number: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )

    # Plain VARCHAR.
    #
    # This is intentional.
    status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default=WithdrawalStatus.PENDING,
        index=True,
    )

    admin_note: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    payout_reference: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )

    creator_profile = relationship(
        "Profile",
        lazy="joined",
    )


# ============================================================
# ADMIN / PLATFORM WITHDRAWAL
# ============================================================

class AdminWithdrawal(Base):
    """
    Withdrawal of BeatHub's own platform money.

    This is NOT a creator withdrawal.

    Creator:
        WithdrawalRequest

    BeatHub platform/admin:
        AdminWithdrawal
    """

    __tablename__ = "admin_withdrawals"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )

    amount: Mapped[Numeric] = mapped_column(
        Numeric(12, 2),
        nullable=False,
    )

    phone_number: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )

    # Plain VARCHAR.
    status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default=WithdrawalStatus.PENDING,
        index=True,
    )

    admin_note: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    payout_reference: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )
