from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


def utcnow() -> datetime:
    """Return a timezone-aware UTC timestamp for new maintenance log rows."""
    return datetime.now(timezone.utc)


class MaintenanceLog(Base):
    __tablename__ = "maintenance_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        nullable=False,
        index=True,
    )
    phone_number: Mapped[str] = mapped_column(String, nullable=False)
    machine_name: Mapped[str] = mapped_column(String, nullable=False, index=True)
    engine_hours: Mapped[str | None] = mapped_column(String, nullable=True)
    task_description: Mapped[str] = mapped_column(String, nullable=False)


class AllowedNumber(Base):
    __tablename__ = "allowed_numbers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    phone_number: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    owner_name: Mapped[str] = mapped_column(String, nullable=False)
    has_onboarded: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
