"""HTML UI (Jinja2 + HTMX) and a small JSON API. Routes are thin; rules live in domain.py."""

from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from . import domain
from .db import connect, default_db_path, ensure_db, reset_db
from .errors import DomainError
from .seed import seed

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
templates.env.filters["money"] = lambda cents: "Free" if not cents else f"${cents / 100:,.0f}"
templates.env.filters["pct"] = lambda v: "—" if v is None else f"{v:.0%}"
templates.env.filters["hours"] = lambda v: "—" if v is None else f"{v:.1f}h"
templates.env.filters["when"] = domain.friendly_ts
templates.env.globals["DECLINE_REASONS"] = domain.DECLINE_REASONS


def create_app(db_path: str | None = None, clock: Callable[[], datetime] | None = None) -> FastAPI:
    app = FastAPI(title="Parks & Rec Registration")
    app.state.db_path = db_path or default_db_path()
    app.state.clock = clock or (lambda: datetime.now(timezone.utc))

    def get_conn(request: Request):
        path = request.app.state.db_path
        ensure_db(path, lambda c: seed(c, request.app.state.clock()))
        conn = connect(path)
        try:
            yield conn
        finally:
            conn.close()

    def now(request: Request) -> datetime:
        return request.app.state.clock()

    def current_household(request: Request, conn=Depends(get_conn)):
        """Fake auth: whoever the 'Acting as' cookie says. Every action still checks ownership."""
        try:
            hid = int(request.cookies.get("household_id", ""))
        except ValueError:
            hid = None
        return (hid and domain.household(conn, hid)) or domain.households(conn)[0]

    Conn = Annotated[object, Depends(get_conn)]
    Now = Annotated[datetime, Depends(now)]
    Household = Annotated[object, Depends(current_household)]
    ParticipantIds = Annotated[list[int], Form()]

    def parent_page(request, conn, household, now, template="_parent_main.html", section_messages=None, flash=None):
        cards = domain.program_cards(conn, household["id"], now)
        domain.record_full_seen(conn, household["id"], cards, now)
        return templates.TemplateResponse(request, template, {
            "page": "parent", "households": domain.households(conn), "household": household, "programs": cards,
            "regs": domain.my_registrations(conn, household["id"]),
            "section_messages": section_messages or {}, "flash": flash,
        })

    def staff_page(request, conn, template="_staff_main.html", flash=None):
        return templates.TemplateResponse(request, template, {
            "page": "staff", "households": domain.households(conn), "household": None,
            "queues": domain.staff_queues(conn), "metrics": domain.metrics(conn), "flash": flash,
        })

    def attempt(fn) -> tuple[bool, str]:
        try:
            return True, fn()
        except DomainError as e:
            return False, e.message

    # ------------------------------------------------------------ parent

    @app.get("/")
    def index(request: Request, conn: Conn, household: Household, now: Now):
        return parent_page(request, conn, household, now, template="index.html")

    @app.post("/household")
    def switch_household(household_id: Annotated[int, Form()]):
        response = RedirectResponse("/", status_code=303)
        response.set_cookie("household_id", str(household_id), samesite="lax")
        return response

    @app.post("/sections/{section_id}/register")
    def register(request: Request, section_id: int, conn: Conn, household: Household, now: Now,
                 participant_ids: ParticipantIds = []):
        try:
            outcomes = domain.register(conn, household["id"], section_id, participant_ids, now)
            return parent_page(request, conn, household, now, section_messages={section_id: outcomes})
        except DomainError as e:
            return parent_page(request, conn, household, now, flash=(False, e.message))

    @app.post("/sections/{section_id}/waitlist")
    def join_waitlist(request: Request, section_id: int, conn: Conn, household: Household, now: Now,
                      participant_ids: ParticipantIds = []):
        try:
            outcomes = domain.join_waitlist(conn, household["id"], section_id, participant_ids, now)
            return parent_page(request, conn, household, now, section_messages={section_id: outcomes})
        except DomainError as e:
            return parent_page(request, conn, household, now, flash=(False, e.message))

    @app.post("/registrations/{registration_id}/drop")
    def drop(request: Request, registration_id: int, conn: Conn, household: Household, now: Now):
        flash = attempt(lambda: domain.drop(conn, household["id"], registration_id, now))
        return parent_page(request, conn, household, now, flash=flash)

    @app.post("/waitlist/{entry_id}/withdraw")
    def withdraw(request: Request, entry_id: int, conn: Conn, household: Household, now: Now):
        flash = attempt(lambda: domain.withdraw(conn, household["id"], entry_id, now))
        return parent_page(request, conn, household, now, flash=flash)

    # ------------------------------------------------------------ staff (open in the demo)

    @app.get("/staff")
    def staff(request: Request, conn: Conn):
        return staff_page(request, conn, template="staff.html")

    @app.post("/staff/sections/{section_id}/entries/{entry_id}/enroll")
    def staff_enroll(request: Request, section_id: int, entry_id: int, conn: Conn, now: Now):
        return staff_page(request, conn, flash=attempt(lambda: domain.staff_enroll(conn, section_id, entry_id, now)))

    @app.post("/staff/sections/{section_id}/entries/{entry_id}/resolve")
    def staff_resolve(request: Request, section_id: int, entry_id: int, conn: Conn, now: Now,
                      outcome: Annotated[str, Form()], reason: Annotated[str, Form()] = "",
                      note: Annotated[str, Form()] = ""):
        flash = attempt(lambda: domain.staff_resolve(conn, section_id, entry_id, outcome, reason or None, note, now))
        return staff_page(request, conn, flash=flash)

    # ------------------------------------------------------------ demo + JSON

    @app.post("/demo/reset")
    def demo_reset(request: Request, conn: Conn):
        reset_db(conn, lambda c: seed(c, request.app.state.clock()))
        return RedirectResponse(request.headers.get("referer") or "/", status_code=303)

    @app.get("/api/programs")
    def api_programs(conn: Conn, household: Household, now: Now):
        return {"household": dict(household), "programs": domain.program_cards(conn, household["id"], now)}

    @app.get("/api/metrics")
    def api_metrics(conn: Conn):
        return domain.metrics(conn)

    return app


app = create_app()
