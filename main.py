from __future__ import annotations

import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Generator

from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from twilio.twiml.messaging_response import MessagingResponse

from database import SessionLocal, engine
from models import AllowedNumber, Base, MaintenanceLog


BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

DELIMITER_RE = re.compile(r"[-:,]")
E164_RE = re.compile(r"^\+[1-9]\d{1,14}$")


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


def build_empty_twiml() -> Response:
    return Response(content="<Response></Response>", media_type="application/xml")


def normalize_phone_number(phone_number: str) -> str:
    return phone_number.strip()


def parse_log_message(message_body: str) -> tuple[str, str | None, str]:
    parts = [part.strip() for part in DELIMITER_RE.split(message_body, maxsplit=2)]
    non_empty_parts = [part for part in parts if part]

    if len(non_empty_parts) >= 3:
        machine_name, engine_hours, task_description = non_empty_parts[0], non_empty_parts[1], non_empty_parts[2]
        return machine_name, engine_hours, task_description

    if len(non_empty_parts) == 2:
        machine_name, task_description = non_empty_parts
        return machine_name, None, task_description

    parts = message_body.split(" ", 1)
    machine_name = parts[0].strip() if parts else ""
    task_description = parts[1].strip() if len(parts) > 1 else ""
    return machine_name, None, task_description


@app.post("/sms")
async def sms_webhook(
    Body: str = Form(...),
    From: str = Form(...),
    db: Session = Depends(get_db),
) -> Response:
    """Handle Twilio SMS commands for logging and retrieving maintenance entries."""
    message_body = Body.strip()
    from_number = normalize_phone_number(From)

    allowed_number = (
        db.query(AllowedNumber)
        .filter(AllowedNumber.phone_number == from_number)
        .first()
    )
    if allowed_number is None:
        return build_empty_twiml()

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

    machine_name, engine_hours, task_description = parse_log_message(message_body)

    if not machine_name or not task_description:
        return build_twiml(
            "Could not parse your message. Please use: [Machine Name] - [Task], "
            "[Machine Name]: [Task], [Machine Name], [Task], or "
            "[Machine Name] - [Engine Hours] - [Task]"
        )

    new_log = MaintenanceLog(
        phone_number=from_number,
        machine_name=machine_name,
        engine_hours=engine_hours,
        task_description=task_description,
    )
    db.add(new_log)
    db.commit()
    db.refresh(new_log)
    if engine_hours:
        return build_twiml(f"Logged maintenance for {machine_name} at {engine_hours}: {task_description}")
    return build_twiml(f"Logged maintenance for {machine_name}: {task_description}")


@app.post("/add-number")
def add_number(
    phone_number: str = Form(...),
    owner_name: str = Form(...),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    normalized_phone_number = normalize_phone_number(phone_number)
    normalized_owner_name = owner_name.strip()

    if not normalized_owner_name or not E164_RE.fullmatch(normalized_phone_number):
        return RedirectResponse(url="/dashboard", status_code=303)

    existing_number = (
        db.query(AllowedNumber)
        .filter(AllowedNumber.phone_number == normalized_phone_number)
        .first()
    )
    if existing_number is None:
        db.add(
            AllowedNumber(
                phone_number=normalized_phone_number,
                owner_name=normalized_owner_name,
            )
        )
    else:
        existing_number.owner_name = normalized_owner_name

    db.commit()
    return RedirectResponse(url="/dashboard", status_code=303)


@app.post("/delete-number/{id}")
def delete_number(id: int, db: Session = Depends(get_db)) -> RedirectResponse:
    allowed_number = db.query(AllowedNumber).filter(AllowedNumber.id == id).first()
    if allowed_number is not None:
        db.delete(allowed_number)
        db.commit()
    return RedirectResponse(url="/dashboard", status_code=303)


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    """Render a dashboard showing all maintenance logs in reverse chronological order."""
    logs = db.query(MaintenanceLog).order_by(MaintenanceLog.timestamp.desc()).all()
    allowed_numbers = db.query(AllowedNumber).order_by(AllowedNumber.owner_name.asc()).all()
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "logs": logs,
            "allowed_numbers": allowed_numbers,
        },
    )
