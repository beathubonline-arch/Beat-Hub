import enum
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Numeric,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


# ============================================================
# CREATOR WITHDRAWALS
# ============================================================

class WithdrawalStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    PROCESSING = "processing"
    PAID = "paid"
    REJECTED = "rejected"


class CreatorLedgerEntry(Base):
    """
    Append-only financial ledger for creators.

    Positive amount = creator credit.
    Negative amount = creator debit/withdrawal.
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

    amount: Mapped[Decimal] = mapped_column(
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

    creator_profile = relationship(
        "Profile",
        foreign_keys=[creator_profile_id],
    )

    withdrawal_request = relationship(
        "WithdrawalRequest",
        foreign_keys=[withdrawal_request_id],
    )


class WithdrawalRequest(Base):
    """
    Withdrawal requested by a creator/producer.
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

    amount: Mapped[Decimal] = mapped_column(
        Numeric(12, 2),
        nullable=False,
    )

    phone_number: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )

    status: Mapped[WithdrawalStatus] = mapped_column(
        Enum(WithdrawalStatus),
        default=WithdrawalStatus.PENDING,
        nullable=False,
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
        foreign_keys=[creator_profile_id],
    )


# ============================================================
# ADMIN / BEATHUB PLATFORM WITHDRAWALS
# ============================================================

class AdminWithdrawalStatus(str, enum.Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    PAID = "paid"
    REJECTED = "rejected"


class AdminWithdrawal(Base):
    """
    Money withdrawn by BeatHub itself.

    This is completely separate from creator withdrawals.

    Example:

        BeatHub has KES 50,000 in platform earnings.

        Admin requests:
            KES 10,000
            to 0712345678

        This record tracks that platform withdrawal.
    """

    __tablename__ = "admin_withdrawals"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )

    amount: Mapped[Decimal] = mapped_column(
        Numeric(12, 2),
        nullable=False,
    )

    phone_number: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )

    status: Mapped[AdminWithdrawalStatus] = mapped_column(
        Enum(AdminWithdrawalStatus),
        default=AdminWithdrawalStatus.PENDING,
        nullable=False,
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

    def __repr__(self) -> str:
        return (
            f"<AdminWithdrawal "
            f"{self.amount} -> {self.phone_number} "
            f"({self.status})>"
        )


# ============================================================
# BACKWARD COMPATIBILITY
# ============================================================
#
# Some earlier admin code used PlatformWithdrawal.
# Keep this alias so that code importing PlatformWithdrawal
# will continue to work without another model/table.
#

PlatformWithdrawal = AdminWithdrawal
PlatformWithdrawalStatus = AdminWithdrawalStatus
