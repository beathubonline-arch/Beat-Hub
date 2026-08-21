import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class WithdrawalStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    PROCESSING = "processing"
    PAID = "paid"
    REJECTED = "rejected"


class CreatorLedgerEntry(Base):
    """
    Append-only ledger of creator credits and debits.

    Positive amount:
        creator earns money

    Negative amount:
        creator withdraws money
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
    )

    withdrawal_request_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("withdrawal_requests.id"),
        nullable=True,
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
    )


class WithdrawalRequest(Base):
    """
    Creator withdrawal request.

    This table is ONLY for creator withdrawals.
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

    status: Mapped[WithdrawalStatus] = mapped_column(
        Enum(WithdrawalStatus),
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
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )

    creator_profile = relationship("Profile")


class PlatformWithdrawal(Base):
    """
    BeatHub platform-money withdrawal.

    This is completely separate from creator withdrawals.

    The available platform balance is calculated from completed
    order commissions minus platform withdrawals that have already
    been paid or are currently being processed.
    """

    __tablename__ = "platform_withdrawals"

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

    status: Mapped[WithdrawalStatus] = mapped_column(
        Enum(WithdrawalStatus),
        default=WithdrawalStatus.PENDING,
        nullable=False,
        index=True,
    )

    payout_reference: Mapped[str | None] = mapped_column(
        String(150),
        nullable=True,
    )

    conversation_id: Mapped[str | None] = mapped_column(
        String(150),
        nullable=True,
    )

    originator_conversation_id: Mapped[str | None] = mapped_column(
        String(150),
        nullable=True,
    )

    admin_note: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )
