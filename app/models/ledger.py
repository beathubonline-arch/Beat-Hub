import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


# ============================================================
# CREATOR WITHDRAWAL STATUS
# ============================================================

class WithdrawalStatus(str, enum.Enum):
    """
    IMPORTANT:

    The PostgreSQL database already stores the enum MEMBERS
    as uppercase names:

        PENDING
        APPROVED
        PROCESSING
        PAID
        REJECTED

    SQLAlchemy Enum stores the enum member names by default.

    Therefore DO NOT change these to lowercase enum values.
    """

    PENDING = "pending"
    APPROVED = "approved"
    PROCESSING = "processing"
    PAID = "paid"
    REJECTED = "rejected"


# ============================================================
# ADMIN WITHDRAWAL STATUS
# ============================================================

class AdminWithdrawalStatus(str, enum.Enum):
    """
    Status for BeatHub/platform withdrawals.

    Kept as a separate enum because the admin withdrawal table
    was created separately in the database.
    """

    PENDING = "pending"
    APPROVED = "approved"
    PROCESSING = "processing"
    PAID = "paid"
    REJECTED = "rejected"


# ============================================================
# CREATOR LEDGER
# ============================================================

class CreatorLedgerEntry(Base):
    """
    Append-only financial ledger for creators.

    Positive:
        money credited to creator.

    Negative:
        money deducted from creator.
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
# CREATOR WITHDRAWAL REQUEST
# ============================================================

class WithdrawalRequest(Base):
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

    # IMPORTANT:
    #
    # No values_callable here.
    #
    # SQLAlchemy will use the enum member names:
    #
    # PENDING
    # APPROVED
    # PROCESSING
    # PAID
    # REJECTED
    #
    # This matches the existing PostgreSQL enum.

    status: Mapped[WithdrawalStatus] = mapped_column(
        Enum(
            WithdrawalStatus,
            name="withdrawalstatus",
            native_enum=True,
            create_constraint=False,
            validate_strings=True,
        ),
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
    BeatHub's own platform withdrawal.

    This is separate from creator withdrawals.
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

    status: Mapped[AdminWithdrawalStatus] = mapped_column(
        Enum(
            AdminWithdrawalStatus,
            name="adminwithdrawalstatus",
            native_enum=True,
            create_constraint=False,
            validate_strings=True,
        ),
        nullable=False,
        default=AdminWithdrawalStatus.PENDING,
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
