import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class OrderStatus(str, enum.Enum):
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"
    REJECTED = "rejected"


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    order_number: Mapped[str] = mapped_column(String(40), unique=True, index=True, nullable=False)
    buyer_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    track_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("tracks.id"), nullable=True, index=True)
    album_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("albums.id"), nullable=True, index=True)
    sales_model_at_purchase: Mapped[str] = mapped_column(String(20), nullable=False)
    gross_amount: Mapped[Numeric] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="KES", server_default="KES", index=True)
    commission_amount: Mapped[Numeric] = mapped_column(Numeric(12, 2), nullable=False)
    net_amount: Mapped[Numeric] = mapped_column(Numeric(12, 2), nullable=False)
    commission_percent_at_purchase: Mapped[Numeric] = mapped_column(Numeric(5, 2), nullable=False)
    status: Mapped[OrderStatus] = mapped_column(Enum(OrderStatus), default=OrderStatus.PENDING, nullable=False, index=True)
    phone_number: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    buyer = relationship("User", foreign_keys=[buyer_id])
    track = relationship("Track", foreign_keys=[track_id])
    album = relationship("Album", foreign_keys=[album_id])
    payment_transaction = relationship("PaymentTransaction", back_populates="order", uselist=False)
    license = relationship("License", back_populates="order", uselist=False)

    def __repr__(self) -> str:
        return f"<Order {self.order_number} {self.status}>"


class License(Base):
    __tablename__ = "licenses"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    order_id: Mapped[str] = mapped_column(String(36), ForeignKey("orders.id"), unique=True, nullable=False)
    buyer_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    track_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("tracks.id"), nullable=True, index=True)
    album_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("albums.id"), nullable=True, index=True)
    granted_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    order = relationship("Order", back_populates="license")


class ExclusiveOwnershipLock(Base):
    __tablename__ = "exclusive_ownership_locks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    track_id: Mapped[str] = mapped_column(String(36), ForeignKey("tracks.id"), unique=True, nullable=False, index=True)
    order_id: Mapped[str] = mapped_column(String(36), ForeignKey("orders.id"), unique=True, nullable=False)
    locked_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
