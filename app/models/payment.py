import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class PaymentStatus(str, enum.Enum):
    PENDING = "pending"
    SUCCESS = "success"
    FAILED = "failed"
    CANCELLED = "cancelled"


class PaymentTransaction(Base):
    """
    Tracks the lifecycle of a single M-Pesa STK Push request tied to an order.
    CheckoutRequestID is the idempotency key used to safely process
    duplicate/retried Daraja callbacks without double-processing.
    """

    __tablename__ = "payment_transactions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    order_id: Mapped[str] = mapped_column(String(36), ForeignKey("orders.id"), unique=True, nullable=False)

    merchant_request_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    checkout_request_id: Mapped[str] = mapped_column(String(100), unique=True, index=True, nullable=False)

    phone_number: Mapped[str] = mapped_column(String(20), nullable=False)
    amount: Mapped[Numeric] = mapped_column(Numeric(12, 2), nullable=False)

    status: Mapped[PaymentStatus] = mapped_column(Enum(PaymentStatus), default=PaymentStatus.PENDING, index=True)

    mpesa_receipt_number: Mapped[str | None] = mapped_column(String(60), nullable=True)
    result_code: Mapped[str | None] = mapped_column(String(10), nullable=True)
    result_desc: Mapped[str | None] = mapped_column(String(300), nullable=True)

    raw_callback_payload: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Guards against the same Daraja callback being processed twice.
    callback_processed: Mapped[bool] = mapped_column(default=False, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    order = relationship("Order", back_populates="payment_transaction")

    def __repr__(self) -> str:
        return f"<PaymentTransaction {self.checkout_request_id} {self.status}>"
