"""
BeatHub payment models.

Stores M-Pesa payment transactions associated with BeatHub orders.

IMPORTANT:
- PaymentTransaction represents the payment attempt.
- Order represents the purchase.
- License represents ownership.
- A payment must NOT grant ownership until the M-Pesa callback
  confirms a successful payment.
"""

import enum
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Numeric,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


# ======================================================================
# PAYMENT STATUS
# ======================================================================

class PaymentStatus(str, enum.Enum):
    """
    State of an individual payment transaction.
    """

    PENDING = "pending"

    COMPLETED = "completed"

    FAILED = "failed"


# ======================================================================
# PAYMENT TRANSACTION
# ======================================================================

class PaymentTransaction(Base):
    """
    One M-Pesa payment attempt for one BeatHub order.

    The CheckoutRequestID is the primary correlation identifier
    returned by Safaricom's STK Push API.

    Ownership must NEVER be granted merely because this row exists.

    Ownership is granted only when:
        PaymentStatus.COMPLETED
        AND
        OrderStatus.COMPLETED
    """

    __tablename__ = "payment_transactions"

    # ------------------------------------------------------------------
    # PRIMARY KEY
    # ------------------------------------------------------------------

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )

    # ------------------------------------------------------------------
    # ORDER
    # ------------------------------------------------------------------

    order_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey(
            "orders.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        unique=True,
        index=True,
    )

    # ------------------------------------------------------------------
    # SAFARICOM IDENTIFIERS
    # ------------------------------------------------------------------

    merchant_request_id: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        index=True,
    )

    checkout_request_id: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        unique=True,
        index=True,
    )

    mpesa_receipt_number: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        unique=True,
        index=True,
    )

    # ------------------------------------------------------------------
    # PAYMENT DETAILS
    # ------------------------------------------------------------------

    phone_number: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )

    amount: Mapped[Decimal] = mapped_column(
        Numeric(12, 2),
        nullable=False,
    )

    # ------------------------------------------------------------------
    # STATUS
    # ------------------------------------------------------------------

    status: Mapped[PaymentStatus] = mapped_column(
        SAEnum(
            PaymentStatus,
            name="paymentstatus",
            native_enum=False,
        ),
        default=PaymentStatus.PENDING,
        nullable=False,
        index=True,
    )

    # ------------------------------------------------------------------
    # SAFARICOM RESULT INFORMATION
    # ------------------------------------------------------------------

    result_code: Mapped[int | None] = mapped_column(
        nullable=True,
    )

    result_description: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
    )

    # ------------------------------------------------------------------
    # TIMESTAMPS
    # ------------------------------------------------------------------

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )

    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    # ------------------------------------------------------------------
    # RELATIONSHIP
    # ------------------------------------------------------------------

    order = relationship(
        "Order",
        back_populates="payment_transaction",
    )

    # ------------------------------------------------------------------
    # REPRESENTATION
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        return (
            "<PaymentTransaction "
            f"{self.checkout_request_id} "
            f"{self.status}>"
        )
    
