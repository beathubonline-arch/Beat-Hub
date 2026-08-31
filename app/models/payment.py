"""BeatHub payment models."""

import enum
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, Enum as SAEnum, ForeignKey, Numeric, String, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class PaymentStatus(str, enum.Enum):
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"


class PaymentTransaction(Base):
    __tablename__ = "payment_transactions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    order_id: Mapped[str] = mapped_column(String(36), ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    merchant_request_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    checkout_request_id: Mapped[str | None] = mapped_column(String(100), nullable=True, unique=True, index=True)
    mpesa_receipt_number: Mapped[str | None] = mapped_column(String(100), nullable=True, unique=True, index=True)
    phone_number: Mapped[str] = mapped_column(String(20), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="KES", server_default="KES", index=True)
    status: Mapped[PaymentStatus] = mapped_column(
        SAEnum(PaymentStatus, name="paymentstatus", native_enum=False,
              values_callable=lambda enum_cls: [member.value for member in enum_cls], length=30),
        default=PaymentStatus.PENDING, nullable=False, index=True,
    )
    result_code: Mapped[int | None] = mapped_column(nullable=True)
    result_description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    callback_processed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    order = relationship("Order", back_populates="payment_transaction")

    def __repr__(self) -> str:
        return f"<PaymentTransaction {self.checkout_request_id} {self.status}>"
