"""Business rules. Every write runs inside write_tx (BEGIN IMMEDIATE); `now` is always passed in."""

import sqlite3
import statistics
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone

from .db import write_tx
from .errors import DomainError

TS_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

DECLINE_REASONS = {
    "booked_elsewhere": "Booked something else",
    "wanted_siblings_together": "Wanted family members placed together",
    "schedule_changed": "Schedule no longer works",
    "other": "Other",
}

# Placeholder thresholds for "should we build automated promotion?" — to agree with stakeholders.
THRESHOLD_FREED_PCT = 0.05
THRESHOLD_CONVERSION = 0.60
THRESHOLD_TIME_TO_FILL_H = 48


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime(TS_FORMAT)


def parse_ts(value: str) -> datetime:
    return datetime.strptime(value, TS_FORMAT).replace(tzinfo=timezone.utc)


def friendly_ts(value: str) -> str:
    dt = parse_ts(value)
    return f"{dt:%b} {dt.day}, {dt:%-I:%M %p} UTC"


def age_on(dob: date, on: date) -> int:
    """Whole years. A Feb 29 birthday counts as passed on Mar 1 in non-leap years."""
    return on.year - dob.year - ((on.month, on.day) < (dob.month, dob.day))


def age_range_label(min_age: int | None, max_age: int | None) -> str:
    if min_age is None and max_age is None:
        return "All ages"
    if max_age is None:
        return f"Ages {min_age}+"
    if min_age is None:
        return f"Up to age {max_age}"
    return f"Ages {min_age}–{max_age}"


def public_seats(capacity: int, enrolled: int, waiting: int) -> int:
    """Freed seats are held for the waitlist: the public only sees seats when nobody is waiting."""
    return 0 if waiting else max(capacity - enrolled, 0)


@dataclass
class Outcome:
    participant_id: int
    name: str
    ok: bool
    message: str
    code: str | None = None


# ---------------------------------------------------------------- helpers


def _log(conn, type_, now, *, section_id=None, household_id=None, participant_id=None, request_id=None):
    conn.execute(
        "INSERT INTO events (type, section_id, household_id, participant_id, request_id, at) VALUES (?, ?, ?, ?, ?, ?)",
        (type_, section_id, household_id, participant_id, request_id, iso(now)),
    )


def _section(conn, section_id: int) -> sqlite3.Row:
    row = conn.execute(
        """SELECT s.*, p.name AS program_name, p.registration_opens_at
           FROM sections s JOIN programs p ON p.id = s.program_id WHERE s.id = ?""",
        (section_id,),
    ).fetchone()
    if row is None:
        raise DomainError("NOT_FOUND", "Section not found.")
    return row


def _participant(conn, participant_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM participants WHERE id = ?", (participant_id,)).fetchone()
    if row is None:
        raise DomainError("NOT_FOUND", "Participant not found.")
    return row


def _counts(conn, section_id: int) -> tuple[int, int]:
    enrolled = conn.execute(
        "SELECT COUNT(*) FROM registrations WHERE section_id = ? AND status = 'enrolled'", (section_id,)
    ).fetchone()[0]
    waiting = conn.execute(
        "SELECT COUNT(*) FROM waitlist_entries WHERE section_id = ? AND status = 'waiting'", (section_id,)
    ).fetchone()[0]
    return enrolled, waiting


def _is_enrolled(conn, section_id: int, participant_id: int) -> bool:
    return conn.execute(
        "SELECT 1 FROM registrations WHERE section_id = ? AND participant_id = ? AND status = 'enrolled'",
        (section_id, participant_id),
    ).fetchone() is not None


def _is_waiting(conn, section_id: int, participant_id: int) -> bool:
    return conn.execute(
        "SELECT 1 FROM waitlist_entries WHERE section_id = ? AND participant_id = ? AND status = 'waiting'",
        (section_id, participant_id),
    ).fetchone() is not None


def _head(conn, section_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM waitlist_entries WHERE section_id = ? AND status = 'waiting' ORDER BY id LIMIT 1",
        (section_id,),
    ).fetchone()


def _position(conn, entry: sqlite3.Row) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM waitlist_entries WHERE section_id = ? AND status = 'waiting' AND id <= ?",
        (entry["section_id"], entry["id"]),
    ).fetchone()[0]


def is_open(section: sqlite3.Row, now: datetime) -> bool:
    return now >= parse_ts(section["registration_opens_at"])


def age_problem(section: sqlite3.Row, participant: sqlite3.Row) -> str | None:
    age = age_on(date.fromisoformat(participant["date_of_birth"]), date.fromisoformat(section["start_date"]))
    lo, hi = section["min_age"], section["max_age"]
    if (lo is not None and age < lo) or (hi is not None and age > hi):
        return (
            f"{participant['first_name']} will be {age} when the section starts; "
            f"this section is {age_range_label(lo, hi).lower()}."
        )
    return None


def _check_can_join(conn, section, participant, now: datetime) -> None:
    """Rules shared by registering and joining the waitlist."""
    name = participant["first_name"]
    if not is_open(section, now):
        raise DomainError("REGISTRATION_NOT_OPEN", f"Registration opens {friendly_ts(section['registration_opens_at'])}.")
    problem = age_problem(section, participant)
    if problem:
        raise DomainError("AGE_INELIGIBLE", problem)
    if _is_enrolled(conn, section["id"], participant["id"]):
        raise DomainError("ALREADY_REGISTERED", f"{name} is already enrolled here.")
    if _is_waiting(conn, section["id"], participant["id"]):
        raise DomainError("ALREADY_WAITLISTED", f"{name} is already on the waitlist here.")


def _for_each_participant(conn, household_id, participant_ids, action) -> list[Outcome]:
    """Run `action` per participant; one participant's failure doesn't block the others."""
    if not participant_ids:
        raise DomainError("NO_PARTICIPANTS", "Pick at least one person.")
    outcomes = []
    for pid in dict.fromkeys(participant_ids):  # de-dupe, keep order
        name = f"#{pid}"
        try:
            participant = _participant(conn, pid)
            if participant["household_id"] != household_id:
                raise DomainError("NOT_IN_HOUSEHOLD", "That person isn't in your household.")
            name = participant["first_name"]
            outcomes.append(Outcome(pid, name, True, action(participant)))
        except DomainError as e:
            outcomes.append(Outcome(pid, name, False, e.message, e.code))
    return outcomes


# ---------------------------------------------------------------- parent actions


def _enroll_one(conn, section, participant, household_id: int, now: datetime) -> str:
    _check_can_join(conn, section, participant, now)
    enrolled, waiting = _counts(conn, section["id"])
    if public_seats(section["capacity"], enrolled, waiting) == 0:
        raise DomainError("SECTION_FULL", f"No seat left for {participant['first_name']}. You can join the waitlist.")
    conn.execute(
        "INSERT INTO registrations (section_id, participant_id, status, source, created_at) "
        "VALUES (?, ?, 'enrolled', 'direct', ?)",
        (section["id"], participant["id"], iso(now)),
    )
    _log(conn, "registered", now, section_id=section["id"], household_id=household_id, participant_id=participant["id"])
    return f"{participant['first_name']} is enrolled."


def _mark_full_seen(conn, section_id: int, household_id: int, now: datetime) -> None:
    """Idempotent: one section_full_seen per household per section."""
    conn.execute(
        """INSERT INTO events (type, section_id, household_id, at)
           SELECT 'section_full_seen', ?, ?, ? WHERE NOT EXISTS (
             SELECT 1 FROM events WHERE type = 'section_full_seen' AND section_id = ? AND household_id = ?)""",
        (section_id, household_id, iso(now), section_id, household_id),
    )


def _waitlist_one(conn, section, participant, household_id: int, request_id: str, now: datetime) -> str:
    _check_can_join(conn, section, participant, now)
    enrolled, waiting = _counts(conn, section["id"])
    if public_seats(section["capacity"], enrolled, waiting) > 0:
        raise DomainError("SEATS_AVAILABLE", "A seat is open. Register directly instead.")
    cur = conn.execute(
        "INSERT INTO waitlist_entries (section_id, participant_id, request_id, status, created_at) "
        "VALUES (?, ?, ?, 'waiting', ?)",
        (section["id"], participant["id"], request_id, iso(now)),
    )
    # Joining implies they saw it full, so the join rate can never exceed 100%.
    _mark_full_seen(conn, section["id"], household_id, now)
    _log(conn, "waitlist_joined", now, section_id=section["id"], household_id=household_id,
         participant_id=participant["id"], request_id=request_id)
    entry = conn.execute("SELECT * FROM waitlist_entries WHERE id = ?", (cur.lastrowid,)).fetchone()
    return (f"{participant['first_name']} is #{_position(conn, entry)} on the waitlist. "
            "We'll reach out if a seat opens up.")


def register(conn, household_id: int, section_id: int, participant_ids: list[int], now: datetime) -> list[Outcome]:
    with write_tx(conn):
        section = _section(conn, section_id)
        return _for_each_participant(
            conn, household_id, participant_ids, lambda p: _enroll_one(conn, section, p, household_id, now))


def join_waitlist(conn, household_id: int, section_id: int, participant_ids: list[int], now: datetime) -> list[Outcome]:
    request_id = uuid.uuid4().hex[:8]  # shared by everyone joined in this one action
    with write_tx(conn):
        section = _section(conn, section_id)
        return _for_each_participant(
            conn, household_id, participant_ids, lambda p: _waitlist_one(conn, section, p, household_id, request_id, now))


@dataclass
class SplitChoice:
    """More eligible people were selected than there are seats: the parent picks who gets them."""
    section_id: int
    section_name: str
    seats: int
    eligible: list[dict]          # [{"id", "name"}] people who could take a seat
    blocked: list[Outcome]        # selected people who can't register at all (age, duplicate, ...)


def split_needed(conn, household_id: int, section_id: int, participant_ids: list[int], now: datetime) -> SplitChoice | None:
    """Read-only preview for the Register button. Returns a choice when seats < eligible selections."""
    section = _section(conn, section_id)
    enrolled, waiting = _counts(conn, section_id)
    seats = public_seats(section["capacity"], enrolled, waiting)
    eligible, blocked = [], []
    for pid in dict.fromkeys(participant_ids):
        try:
            participant = _participant(conn, pid)
            if participant["household_id"] != household_id:
                raise DomainError("NOT_IN_HOUSEHOLD", "That person isn't in your household.")
            _check_can_join(conn, section, participant, now)
            eligible.append({"id": pid, "name": participant["first_name"]})
        except DomainError as e:
            blocked.append(Outcome(pid, f"#{pid}", False, e.message, e.code))
    if 0 < seats < len(eligible):
        return SplitChoice(section_id, section["name"], seats, eligible, blocked)
    return None


def register_split(conn, household_id: int, section_id: int, enroll_ids: list[int], waitlist_ids: list[int],
                   now: datetime) -> list[Outcome]:
    """Enroll the chosen people and waitlist the rest (sharing one request_id), all or nothing.
    If seats changed while the parent was choosing, nothing is written."""
    enroll_ids = list(dict.fromkeys(enroll_ids))
    waitlist_ids = [pid for pid in dict.fromkeys(waitlist_ids) if pid not in enroll_ids]
    if not enroll_ids:
        raise DomainError("NO_PARTICIPANTS", "Pick who should get the seat.")
    request_id = uuid.uuid4().hex[:8]
    outcomes = []
    with write_tx(conn):
        section = _section(conn, section_id)
        enrolled, waiting = _counts(conn, section_id)
        seats = public_seats(section["capacity"], enrolled, waiting)
        if len(enroll_ids) > seats or (waitlist_ids and len(enroll_ids) < seats):
            raise DomainError("SEATS_CHANGED", f"There {'is' if seats == 1 else 'are'} now {seats} seat(s) left. "
                                               "Please choose again.")
        def own(pid):
            participant = _participant(conn, pid)
            if participant["household_id"] != household_id:
                raise DomainError("NOT_IN_HOUSEHOLD", "That person isn't in your household.")
            return participant

        for pid in enroll_ids:
            p = own(pid)
            outcomes.append(Outcome(pid, p["first_name"], True, _enroll_one(conn, section, p, household_id, now)))
        for pid in waitlist_ids:
            p = own(pid)
            outcomes.append(Outcome(pid, p["first_name"], True,
                                    _waitlist_one(conn, section, p, household_id, request_id, now)))
    return outcomes


def drop(conn, household_id: int, registration_id: int, now: datetime) -> str:
    with write_tx(conn):
        reg = conn.execute(
            """SELECT r.*, p.household_id, p.first_name FROM registrations r
               JOIN participants p ON p.id = r.participant_id WHERE r.id = ?""",
            (registration_id,),
        ).fetchone()
        if reg is None or reg["household_id"] != household_id:
            raise DomainError("NOT_FOUND", "Registration not found.")
        if reg["status"] != "enrolled":
            raise DomainError("NOT_ENROLLED", "That registration was already dropped.")
        conn.execute("UPDATE registrations SET status = 'dropped', dropped_at = ? WHERE id = ?", (iso(now), registration_id))
        _log(conn, "dropped", now, section_id=reg["section_id"], household_id=household_id, participant_id=reg["participant_id"])
        _, waiting = _counts(conn, reg["section_id"])
        if waiting:
            # Seat is held for staff outreach, not released to the public.
            _log(conn, "seat_freed", now, section_id=reg["section_id"])
        return f"{reg['first_name']} was dropped."


def withdraw(conn, household_id: int, entry_id: int, now: datetime) -> str:
    with write_tx(conn):
        entry = conn.execute(
            """SELECT w.*, p.household_id, p.first_name FROM waitlist_entries w
               JOIN participants p ON p.id = w.participant_id WHERE w.id = ?""",
            (entry_id,),
        ).fetchone()
        if entry is None or entry["household_id"] != household_id or entry["status"] != "waiting":
            raise DomainError("NOT_FOUND", "Waitlist entry not found.")
        conn.execute("UPDATE waitlist_entries SET status = 'withdrawn', resolved_at = ? WHERE id = ?", (iso(now), entry_id))
        _log(conn, "waitlist_resolved", now, section_id=entry["section_id"], household_id=household_id,
             participant_id=entry["participant_id"], request_id=entry["request_id"])
        return f"{entry['first_name']} left the waitlist."


# ---------------------------------------------------------------- staff actions


def _require_head(conn, section_id: int, entry_id: int) -> sqlite3.Row:
    """Fairness is enforced here: staff can only act on the first person in line.
    Passing the entry id the staff member *saw* also rejects stale clicks."""
    head = _head(conn, section_id)
    if head is None or head["id"] != entry_id:
        raise DomainError("NOT_HEAD_OF_QUEUE", "Only the first person in line can be enrolled or resolved.")
    return head


def staff_enroll(conn, section_id: int, entry_id: int, now: datetime) -> str:
    with write_tx(conn):
        section = _section(conn, section_id)
        head = _require_head(conn, section_id, entry_id)
        enrolled, _ = _counts(conn, section_id)
        if enrolled >= section["capacity"]:
            raise DomainError("NO_SEAT_AVAILABLE", "No free seat yet. Wait for someone to drop.")
        participant = _participant(conn, head["participant_id"])
        if _is_enrolled(conn, section_id, participant["id"]):
            raise DomainError("ALREADY_REGISTERED", f"{participant['first_name']} is already enrolled.")
        conn.execute(
            "INSERT INTO registrations (section_id, participant_id, status, source, created_at) "
            "VALUES (?, ?, 'enrolled', 'waitlist', ?)",
            (section_id, participant["id"], iso(now)),
        )
        conn.execute("UPDATE waitlist_entries SET status = 'enrolled', resolved_at = ? WHERE id = ?", (iso(now), entry_id))
        _log(conn, "waitlist_enrolled", now, section_id=section_id, household_id=participant["household_id"],
             participant_id=participant["id"], request_id=head["request_id"])
        return f"{participant['first_name']} enrolled from the waitlist."


def staff_resolve(conn, section_id: int, entry_id: int, outcome: str, reason: str | None, note: str | None,
                  now: datetime) -> str:
    if outcome not in ("declined", "unreachable"):
        raise DomainError("INVALID_OUTCOME", "Outcome must be declined or unreachable.")
    if outcome == "declined" and reason not in DECLINE_REASONS:
        raise DomainError("REASON_REQUIRED", "Pick a reason when a family declines.")
    if outcome == "unreachable":
        reason = None
    with write_tx(conn):
        section = _section(conn, section_id)
        head = _require_head(conn, section_id, entry_id)
        enrolled, _ = _counts(conn, section_id)
        if enrolled >= section["capacity"]:
            # Declined/unreachable are answers to an offer; with no free seat there was no offer,
            # and counting it would skew outreach conversion.
            raise DomainError("NO_SEAT_AVAILABLE", "No free seat to offer yet. Reach out once someone drops.")
        conn.execute(
            "UPDATE waitlist_entries SET status = ?, reason = ?, note = ?, resolved_at = ? WHERE id = ?",
            (outcome, reason, (note or "").strip() or None, iso(now), entry_id),
        )
        participant = _participant(conn, head["participant_id"])
        _log(conn, "waitlist_resolved", now, section_id=section_id, household_id=participant["household_id"],
             participant_id=participant["id"], request_id=head["request_id"])
        return f"{participant['first_name']} marked {outcome}."


# ---------------------------------------------------------------- read models


def households(conn) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM households ORDER BY id").fetchall()


def household(conn, household_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM households WHERE id = ?", (household_id,)).fetchone()


def program_cards(conn, household_id: int, now: datetime) -> list[dict]:
    members = conn.execute("SELECT * FROM participants WHERE household_id = ? ORDER BY id", (household_id,)).fetchall()
    enrolled = {(r["section_id"], r["participant_id"]) for r in conn.execute(
        """SELECT r.section_id, r.participant_id FROM registrations r JOIN participants p ON p.id = r.participant_id
           WHERE p.household_id = ? AND r.status = 'enrolled'""", (household_id,))}
    waiting = {(r["section_id"], r["participant_id"]) for r in conn.execute(
        """SELECT w.section_id, w.participant_id FROM waitlist_entries w JOIN participants p ON p.id = w.participant_id
           WHERE p.household_id = ? AND w.status = 'waiting'""", (household_id,))}

    programs = []
    for program in conn.execute("SELECT * FROM programs ORDER BY id").fetchall():
        sections = []
        for s in conn.execute(
            """SELECT s.*, p.registration_opens_at FROM sections s JOIN programs p ON p.id = s.program_id
               WHERE s.program_id = ? ORDER BY s.id""", (program["id"],)
        ).fetchall():
            n_enrolled, n_waiting = _counts(conn, s["id"])
            seats = public_seats(s["capacity"], n_enrolled, n_waiting)
            state = "opens" if not is_open(s, now) else ("full" if seats == 0 else "open")
            options = []
            for m in members:
                if (s["id"], m["id"]) in enrolled:
                    blocked = "enrolled"
                elif (s["id"], m["id"]) in waiting:
                    blocked = "on waitlist"
                elif age_problem(s, m):
                    blocked = age_range_label(s["min_age"], s["max_age"]).lower() + " only"
                else:
                    blocked = None
                age = age_on(date.fromisoformat(m["date_of_birth"]), date.fromisoformat(s["start_date"]))
                options.append({"id": m["id"], "name": m["first_name"], "age": age, "blocked": blocked})
            sections.append({
                "id": s["id"], "name": s["name"], "schedule": s["schedule"], "start_date": s["start_date"],
                "ages": age_range_label(s["min_age"], s["max_age"]), "price_cents": s["price_cents"],
                "capacity": s["capacity"], "enrolled": n_enrolled, "waiting": n_waiting, "seats": seats,
                "state": state, "opens_at": s["registration_opens_at"], "options": options,
                "can_act": state != "opens" and any(o["blocked"] is None for o in options),
            })
        programs.append({"id": program["id"], "name": program["name"], "description": program["description"],
                         "sections": sections})
    return programs


def record_full_seen(conn, household_id: int, cards: list[dict], now: datetime) -> None:
    """Top of the demand funnel: a household with someone eligible saw a full section.
    Counted once per household per section, not per page view."""
    candidates = [s["id"] for p in cards for s in p["sections"] if s["state"] == "full" and s["can_act"]]
    if not candidates:
        return
    seen = {r[0] for r in conn.execute(
        "SELECT section_id FROM events WHERE type = 'section_full_seen' AND household_id = ?", (household_id,))}
    new = [sid for sid in candidates if sid not in seen]
    if not new:
        return
    with write_tx(conn):
        for sid in new:
            _mark_full_seen(conn, sid, household_id, now)


def my_registrations(conn, household_id: int) -> dict:
    enrolled = conn.execute(
        """SELECT r.id, p.first_name, s.name AS section_name, s.schedule, r.source
           FROM registrations r JOIN participants p ON p.id = r.participant_id JOIN sections s ON s.id = r.section_id
           WHERE p.household_id = ? AND r.status = 'enrolled' ORDER BY r.id""", (household_id,)).fetchall()
    entries = conn.execute(
        """SELECT w.*, p.first_name, s.name AS section_name FROM waitlist_entries w
           JOIN participants p ON p.id = w.participant_id JOIN sections s ON s.id = w.section_id
           WHERE p.household_id = ? AND w.status = 'waiting' ORDER BY w.id""", (household_id,)).fetchall()
    waiting = [{"id": e["id"], "first_name": e["first_name"], "section_name": e["section_name"],
                "position": _position(conn, e)} for e in entries]
    return {"enrolled": enrolled, "waiting": waiting}


def staff_queues(conn) -> list[dict]:
    queues = []
    for s in conn.execute("SELECT * FROM sections ORDER BY id").fetchall():
        rows = conn.execute(
            """SELECT w.*, p.first_name, h.name AS household_name, h.email, h.phone
               FROM waitlist_entries w JOIN participants p ON p.id = w.participant_id
               JOIN households h ON h.id = p.household_id
               WHERE w.section_id = ? AND w.status = 'waiting' ORDER BY w.id""", (s["id"],)).fetchall()
        if not rows:
            continue
        enrolled, _ = _counts(conn, s["id"])
        by_request: dict[str, list[str]] = {}
        for r in rows:
            by_request.setdefault(r["request_id"], []).append(r["first_name"])
        entries = [{
            "id": r["id"], "position": i + 1, "first_name": r["first_name"], "household_name": r["household_name"],
            "email": r["email"], "phone": r["phone"], "joined_at": r["created_at"], "request_id": r["request_id"],
            "together_with": [n for n in by_request[r["request_id"]] if n != r["first_name"]],
        } for i, r in enumerate(rows)]
        queues.append({"id": s["id"], "name": s["name"], "capacity": s["capacity"], "enrolled": enrolled,
                       "free": s["capacity"] - enrolled, "entries": entries})
    return queues


def _time_to_fill_hours(conn) -> list[float]:
    """For each waitlist enrollment, the hours since the most recent still-unpaired freed seat
    in that section. Pairing by position (zip) was wrong: a freed seat that went back to the
    public after the waitlist emptied would shift every later pair (found in adversarial review)."""
    durations = []
    for (section_id,) in conn.execute("SELECT DISTINCT section_id FROM events WHERE type = 'waitlist_enrolled'").fetchall():
        rows = conn.execute(
            "SELECT type, at FROM events WHERE section_id = ? AND type IN ('seat_freed', 'waitlist_enrolled') ORDER BY id",
            (section_id,)).fetchall()
        unpaired: list[datetime] = []
        for type_, at in rows:
            if type_ == "seat_freed":
                unpaired.append(parse_ts(at))
            elif unpaired:
                durations.append((parse_ts(at) - unpaired.pop()).total_seconds() / 3600)
    return durations


def metrics(conn) -> dict:
    sections = []
    for s in conn.execute("SELECT * FROM sections ORDER BY id").fetchall():
        sid = s["id"]
        enrolled, waiting = _counts(conn, sid)
        seen = conn.execute("SELECT COUNT(DISTINCT household_id) FROM events WHERE type = 'section_full_seen' AND section_id = ?", (sid,)).fetchone()[0]
        joined = conn.execute(
            """SELECT COUNT(DISTINCT p.household_id) FROM waitlist_entries w JOIN participants p ON p.id = w.participant_id
               WHERE w.section_id = ?""", (sid,)).fetchone()[0]
        freed = conn.execute("SELECT COUNT(*) FROM events WHERE type = 'seat_freed' AND section_id = ?", (sid,)).fetchone()[0]
        outcomes = dict(conn.execute(
            "SELECT status, COUNT(*) FROM waitlist_entries WHERE section_id = ? GROUP BY status", (sid,)).fetchall())
        sections.append({
            "id": sid, "name": s["name"], "capacity": s["capacity"], "enrolled": enrolled, "waiting": waiting,
            "oversubscription": waiting / s["capacity"], "full_seen": seen, "joined": joined,
            "join_rate": (joined / seen) if seen else None, "seats_freed": freed,
            "outreach_enrolled": outcomes.get("enrolled", 0), "declined": outcomes.get("declined", 0),
            "unreachable": outcomes.get("unreachable", 0),
        })

    total_capacity = sum(s["capacity"] for s in sections) or 1
    freed = sum(s["seats_freed"] for s in sections)
    converted = sum(s["outreach_enrolled"] for s in sections)
    resolved = converted + sum(s["declined"] + s["unreachable"] for s in sections)
    conversion = converted / resolved if resolved else None
    fills = _time_to_fill_hours(conn)
    median_fill = statistics.median(fills) if fills else None
    reasons = dict(conn.execute(
        "SELECT reason, COUNT(*) FROM waitlist_entries WHERE status = 'declined' GROUP BY reason").fetchall())
    request_sizes = [r[0] for r in conn.execute("SELECT COUNT(*) FROM waitlist_entries GROUP BY request_id")]
    multi_share = (sum(1 for n in request_sizes if n > 1) / len(request_sizes)) if request_sizes else None

    decision = [
        {"label": "Seats freed while a waitlist exists (% of capacity)", "value": freed / total_capacity,
         "threshold": f"≥ {THRESHOLD_FREED_PCT:.0%}", "met": freed / total_capacity >= THRESHOLD_FREED_PCT, "fmt": "pct"},
        {"label": "Outreach conversion", "value": conversion, "threshold": f"≥ {THRESHOLD_CONVERSION:.0%}",
         "met": None if conversion is None else conversion >= THRESHOLD_CONVERSION, "fmt": "pct"},
        {"label": "Median time-to-fill (hours)", "value": median_fill, "threshold": f"> {THRESHOLD_TIME_TO_FILL_H}h",
         "met": None if median_fill is None else median_fill > THRESHOLD_TIME_TO_FILL_H, "fmt": "hours"},
    ]
    return {
        "sections": sections,
        "decision": decision,
        "build_automation": all(d["met"] for d in decision),
        "declines_by_reason": {DECLINE_REASONS[k]: reasons.get(k, 0) for k in DECLINE_REASONS},
        "multi_person_request_share": multi_share,
        "seats_freed": freed,
        "outreach_conversion": conversion,
        "median_time_to_fill_hours": median_fill,
    }
