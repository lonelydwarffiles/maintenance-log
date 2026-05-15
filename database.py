from __future__ import annotations

import os

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker


DB_PATH = os.getenv("MAINTENANCE_DB_PATH", "maintenance.db")
DATABASE_URL = f"sqlite:///{DB_PATH}"

# SQLite needs this flag so the same database connection can be used safely
# across requests in the FastAPI application.
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()
