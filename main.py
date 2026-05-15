from __future__ import annotations

import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Generator

from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from twilio.twiml.messaging_response import MessagingResponse

from database import SessionLocal, engine
from models import Base, MaintenanceLog


BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

DELIMITER_RE = re.compile(r"[-:,]")


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Create database tables when the application starts."""
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(title="Equipment Maintenance Logging System", lifespan=lifespan)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def build_twiml(message: str) -> Response:
    response = MessagingResponse()
    response.message(message)
    return Response(content=str(response), media_type="application/xml")


@app.post("/sms")
async def sms_webhook(
    Body: str = Form(...),
    From: str = Form(...),
    db: Session = Depends(get_db),
) -> Response:
    """Handle Twilio SMS commands for logging and retrieving maintenance entries."""
    message_body = Body.strip()

    if message_body.upper().startswith("GET "):
        machine_name = message_body[4:].strip()
        if not machine_name:
            return build_twiml("Invalid GET format. Use: GET [Machine Name]")

        logs = (
            db.query(MaintenanceLog)
            .filter(MaintenanceLog.machine_name.ilike(machine_name))
            .order_by(MaintenanceLog.timestamp.desc())
            .limit(3)
            .all()
        )

        if not logs:
            return build_twiml(f"No maintenance records found for {machine_name}.")

        formatted_logs = [
            f"{log.timestamp.strftime('%Y-%m-%d %H:%M UTC')}: {log.task_description}"
            for log in logs
        ]
        return build_twiml(f"Last {len(logs)} logs for {machine_name}:\n" + "\n".join(formatted_logs))

    # Flexible parser: treat any non-GET message as a new log entry
    delimiter_match = DELIMITER_RE.search(message_body)
    if delimiter_match:
        split_index = delimiter_match.start()
        machine_name = message_body[:split_index].strip()
        task_description = message_body[split_index + 1:].strip()
    else:
        parts = message_body.split(" ", 1)
        machine_name = parts[0].strip()
        task_description = parts[1].strip() if len(parts) > 1 else ""

    if not machine_name or not task_description:
        return build_twiml("Could not parse your message. Please use: [Machine Name] - [Task], [Machine Name]: [Task], or [Machine Name], [Task]")

    new_log = MaintenanceLog(
        phone_number=From.strip(),
        machine_name=machine_name,
        task_description=task_description,
    )
    db.add(new_log)
    db.commit()
    db.refresh(new_log)
    return build_twiml(f"Logged maintenance for {machine_name}: {task_description}")


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    """Render a dashboard showing all maintenance logs in reverse chronological order."""
    logs = db.query(MaintenanceLog).order_by(MaintenanceLog.timestamp.desc()).all()
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "logs": logs,
        },
    )
