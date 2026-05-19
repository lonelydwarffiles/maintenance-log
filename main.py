from __future__ import annotations

import os
import re
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from secrets import compare_digest
from typing import Generator

from fastapi import Depends, FastAPI, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from twilio.twiml.messaging_response import MessagingResponse

from database import SessionLocal, engine
from models import AllowedNumber, AppSetting, Base, MaintenanceLog


BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
security = HTTPBasic()

DELIMITER_RE = re.compile(r"[-:,]")
E164_RE = re.compile(r"^\+[1-9]\d{1,14}$")
NON_DIGIT_RE = re.compile(r"\D+")
ADMIN_USERNAME = os.getenv("MAINTENANCE_ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("MAINTENANCE_ADMIN_PASSWORD", "admin")
SERVICE_PHONE_NUMBER = os.getenv("MAINTENANCE_SERVICE_PHONE", "").strip()
INITIAL_LOGS_SETTING_KEY = "seeded-admin-logs-2026-05"
INITIAL_LOGS = (
    (datetime(2026, 5, 6, 0, 0, tzinfo=timezone.utc), "299", "2gal hydraulic oil"),
    (datetime(2026, 5, 8, 0, 0, tzinfo=timezone.utc), "308", ".5qt oil"),
    (datetime(2026, 5, 11, 0, 0, tzinfo=timezone.utc), "308", "1-2 gal hydraulic oil"),
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Create database tables when the application starts."""
    Base.metadata.create_all(bind=engine)
    seed_initial_logs()
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
    normalized_value = phone_number.strip()
    digits = NON_DIGIT_RE.sub("", normalized_value)

    if normalized_value.startswith("+"):
        return f"+{digits}" if digits else ""
    if len(digits) == 10:
        return f"+1{digits}"
    if len(digits) == 11 and digits.startswith("1"):
        return f"+{digits}"
    return normalized_value


def require_admin(credentials: HTTPBasicCredentials = Depends(security)) -> str:
    valid_username = compare_digest(credentials.username, ADMIN_USERNAME)
    valid_password = compare_digest(credentials.password, ADMIN_PASSWORD)
    if valid_username and valid_password:
        return credentials.username

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid admin credentials.",
        headers={"WWW-Authenticate": "Basic"},
    )


def seed_initial_logs() -> None:
    db = SessionLocal()
    try:
        existing_setting = (
            db.query(AppSetting)
            .filter(AppSetting.key == INITIAL_LOGS_SETTING_KEY)
            .first()
        )
        if existing_setting is not None:
            return

        for timestamp, machine_name, task_description in INITIAL_LOGS:
            db.add(
                MaintenanceLog(
                    timestamp=timestamp,
                    phone_number="admin",
                    machine_name=machine_name,
                    engine_hours=None,
                    task_description=task_description,
                )
            )

        db.add(AppSetting(key=INITIAL_LOGS_SETTING_KEY, value="true"))
        db.commit()
    finally:
        db.close()


def parse_admin_log_date(log_date: str) -> datetime | None:
    try:
        parsed_date = datetime.strptime(log_date.strip(), "%Y-%m-%d")
    except ValueError:
        return None
    return parsed_date.replace(tzinfo=timezone.utc)


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


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse(url="/dashboard", status_code=302)


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

    response_text = ""
    if not allowed_number.has_onboarded:
        response_text += (
            "Welcome to the Maintenance Log! To log: 'Machine - Hours - Task' "
            "(e.g., '308 - 4500h - oil change'). To search: 'GET Machine'.\n\n"
        )
        allowed_number.has_onboarded = True
        db.commit()

    if message_body.upper().startswith("GET "):
        machine_name = message_body[4:].strip()
        if not machine_name:
            response_text += "Invalid GET format. Use: GET [Machine Name]"
            return build_twiml(response_text)

        logs = (
            db.query(MaintenanceLog)
            .filter(MaintenanceLog.machine_name.ilike(machine_name))
            .order_by(MaintenanceLog.timestamp.desc())
            .limit(3)
            .all()
        )

        if not logs:
            response_text += f"No maintenance records found for {machine_name}."
            return build_twiml(response_text)

        formatted_logs = [
            f"{log.timestamp.strftime('%Y-%m-%d %H:%M UTC')}: {log.task_description}"
            for log in logs
        ]
        response_text += f"Recent {len(logs)} logs for {machine_name}:\n" + "\n".join(formatted_logs)
        return build_twiml(response_text)

    machine_name, engine_hours, task_description = parse_log_message(message_body)

    if not machine_name or not task_description:
        response_text += (
            "Could not parse your message. Please use: [Machine Name] - [Task], "
            "[Machine Name]: [Task], [Machine Name], [Task], or "
            "[Machine Name] - [Engine Hours] - [Task]"
        )
        return build_twiml(response_text)

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
        response_text += f"Logged: {task_description} on {machine_name} at {engine_hours}."
    else:
        response_text += f"Logged: {task_description} on {machine_name}."
    return build_twiml(response_text)


@app.post("/add-number")
def add_number(
    phone_number: str = Form(...),
    owner_name: str = Form(...),
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
) -> RedirectResponse:
    normalized_phone_number = normalize_phone_number(phone_number)
    normalized_owner_name = owner_name.strip()

    if not normalized_owner_name or not E164_RE.fullmatch(normalized_phone_number):
        return RedirectResponse(url="/admin", status_code=303)

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
    return RedirectResponse(url="/admin", status_code=303)


@app.post("/delete-number/{id}")
def delete_number(
    id: int,
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
) -> RedirectResponse:
    allowed_number = db.query(AllowedNumber).filter(AllowedNumber.id == id).first()
    if allowed_number is not None:
        db.delete(allowed_number)
        db.commit()
    return RedirectResponse(url="/admin", status_code=303)


@app.post("/admin/logs")
def add_log(
    log_date: str = Form(...),
    machine_name: str = Form(...),
    engine_hours: str | None = Form(None),
    task_description: str = Form(...),
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
) -> RedirectResponse:
    parsed_date = parse_admin_log_date(log_date)
    normalized_machine_name = machine_name.strip()
    normalized_task_description = task_description.strip()
    normalized_engine_hours = (engine_hours or "").strip() or None

    if parsed_date is None or not normalized_machine_name or not normalized_task_description:
        return RedirectResponse(url="/admin", status_code=303)

    db.add(
        MaintenanceLog(
            timestamp=parsed_date,
            phone_number="admin",
            machine_name=normalized_machine_name,
            engine_hours=normalized_engine_hours,
            task_description=normalized_task_description,
        )
    )
    db.commit()
    return RedirectResponse(url="/admin", status_code=303)


@app.post("/admin/logs/{id}/delete")
def delete_log(
    id: int,
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
) -> RedirectResponse:
    log = db.query(MaintenanceLog).filter(MaintenanceLog.id == id).first()
    if log is not None:
        db.delete(log)
        db.commit()
    return RedirectResponse(url="/admin", status_code=303)


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    """Render a dashboard showing all maintenance logs in reverse chronological order."""
    logs = db.query(MaintenanceLog).order_by(MaintenanceLog.timestamp.desc()).all()
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "logs": logs,
            "is_admin": False,
            "service_phone_number": SERVICE_PHONE_NUMBER,
        },
    )


@app.get("/admin", response_class=HTMLResponse)
def admin_dashboard(
    request: Request,
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
) -> HTMLResponse:
    logs = db.query(MaintenanceLog).order_by(MaintenanceLog.timestamp.desc()).all()
    allowed_numbers = db.query(AllowedNumber).order_by(AllowedNumber.owner_name.asc()).all()
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "logs": logs,
            "allowed_numbers": allowed_numbers,
            "is_admin": True,
            "service_phone_number": SERVICE_PHONE_NUMBER,
        },
    )
