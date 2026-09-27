import sqlite3
from datetime import date, timedelta

import pytest

from app import domain
from app.domain import iso, parse_ts
from app.errors import DomainError
from conftest import (AVA, BASKETBALL, BEN, GARCIA, KIM, LEO, LILY, MATEO, MAYA, NGUYEN, NOAH, NOW, OKAFOR,
                      PATEL, SOFIA, SWIM2_THU, SWIM2_TUE, SWIM3, ZARA)


def codes(outcomes):
    return [o.code for o in outcomes]


def reg_id(conn, section_id, participant_id):
    return conn.execute("SELECT id FROM registrations WHERE section_id=? AND participant_id=? AND status='enrolled'",
                        (section_id, participant_id)).fetchone()[0]


def head_id(conn, section_id):
    return domain._head(conn, section_id)["id"]


# ---------------------------------------------------------------- capacity + household registration

def test_siblings_partial_fill_when_one_seat_left(conn):
    outcomes = domain.register(conn, PATEL, SWIM2_THU, [AVA, BEN], NOW)
    assert codes(outcomes) == [None, "SECTION_FULL"]


def test_one_bad_participant_does_not_block_siblings(conn):
    outcomes = domain.register(conn, PATEL, SWIM2_THU, [MAYA, AVA], NOW)
    assert codes(outcomes) == ["AGE_INELIGIBLE", None]


def test_duplicate_registration_and_waitlist(conn):
    domain.register(conn, PATEL, SWIM2_THU, [AVA], NOW)
    assert codes(domain.register(conn, PATEL, SWIM2_THU, [AVA], NOW)) == ["ALREADY_REGISTERED"]
    domain.join_waitlist(conn, PATEL, SWIM2_TUE, [AVA], NOW)
    assert codes(domain.join_waitlist(conn, PATEL, SWIM2_TUE, [AVA], NOW)) == ["ALREADY_WAITLISTED"]


def test_cannot_act_for_another_household(conn):
    assert codes(domain.register(conn, PATEL, SWIM2_THU, [ZARA], NOW)) == ["NOT_IN_HOUSEHOLD"]
    with pytest.raises(DomainError) as e:
        domain.drop(conn, PATEL, reg_id(conn, SWIM2_TUE, SOFIA), NOW)
    assert e.value.code == "NOT_FOUND"


# ---------------------------------------------------------------- age gate + open time

def _add_participant(conn, dob: date) -> int:
    return conn.execute("INSERT INTO participants (household_id, first_name, date_of_birth) VALUES (?, 'Test', ?)",
                        (PATEL, dob.isoformat())).lastrowid


def test_age_is_checked_on_section_start_date_inclusive(conn):
    start = date.fromisoformat(conn.execute("SELECT start_date FROM sections WHERE id=?", (SWIM3,)).fetchone()[0])
    turns_9_on_start = _add_participant(conn, start.replace(year=start.year - 9))
    turns_9_day_after = _add_participant(conn, start.replace(year=start.year - 9) + timedelta(days=1))
    outcomes = domain.register(conn, PATEL, SWIM3, [turns_9_on_start, turns_9_day_after], NOW)  # Swim 3 is ages 9–12
    assert codes(outcomes) == [None, "AGE_INELIGIBLE"]


def test_age_on_leap_day_birthday():
    assert domain.age_on(date(2016, 2, 29), date(2026, 2, 28)) == 9
    assert domain.age_on(date(2016, 2, 29), date(2026, 3, 1)) == 10


def test_registration_gate_opens_exactly_at_opens_at(conn):
    opens_at = parse_ts(conn.execute("SELECT registration_opens_at FROM programs WHERE id=2").fetchone()[0])
    assert codes(domain.register(conn, GARCIA, BASKETBALL, [MATEO], opens_at - timedelta(seconds=1))) == ["REGISTRATION_NOT_OPEN"]
    assert codes(domain.register(conn, GARCIA, BASKETBALL, [MATEO], opens_at)) == [None]


# ---------------------------------------------------------------- waitlist: held seats + fairness

def test_cannot_join_waitlist_while_seats_are_open(conn):
    assert codes(domain.join_waitlist(conn, PATEL, SWIM2_THU, [AVA], NOW)) == ["SEATS_AVAILABLE"]


def test_family_members_joining_together_share_request_id(conn):
    domain.join_waitlist(conn, PATEL, SWIM2_TUE, [AVA, BEN], NOW)
    ids = {r[0] for r in conn.execute(
        "SELECT request_id FROM waitlist_entries WHERE participant_id IN (?, ?)", (AVA, BEN))}
    assert len(ids) == 1


def test_freed_seat_is_held_for_waitlist_not_public(conn):
    domain.drop(conn, GARCIA, reg_id(conn, SWIM2_TUE, SOFIA), NOW)
    # A newcomer can't grab the seat that's owed to the Nguyens.
    assert codes(domain.register(conn, PATEL, SWIM2_TUE, [AVA], NOW)) == ["SECTION_FULL"]
    assert conn.execute("SELECT COUNT(*) FROM events WHERE type='seat_freed'").fetchone()[0] == 1


def test_staff_must_go_in_order(conn):
    domain.drop(conn, GARCIA, reg_id(conn, SWIM2_TUE, SOFIA), NOW)
    noah_entry = conn.execute("SELECT id FROM waitlist_entries WHERE participant_id=?", (NOAH,)).fetchone()[0]
    with pytest.raises(DomainError) as e:
        domain.staff_enroll(conn, SWIM2_TUE, noah_entry, NOW)
    assert e.value.code == "NOT_HEAD_OF_QUEUE"

    domain.staff_enroll(conn, SWIM2_TUE, head_id(conn, SWIM2_TUE), NOW)  # Lily
    with pytest.raises(DomainError) as e:
        domain.staff_enroll(conn, SWIM2_TUE, head_id(conn, SWIM2_TUE), NOW)  # Noah, but no seat
    assert e.value.code == "NO_SEAT_AVAILABLE"


def test_resolving_head_moves_queue_and_requires_reason(conn):
    lily_entry = head_id(conn, SWIM2_TUE)
    with pytest.raises(DomainError) as e:
        domain.staff_resolve(conn, SWIM2_TUE, lily_entry, "declined", None, None, NOW)
    assert e.value.code == "REASON_REQUIRED"
    domain.staff_resolve(conn, SWIM2_TUE, lily_entry, "declined", "wanted_siblings_together", "", NOW)
    assert domain._head(conn, SWIM2_TUE)["participant_id"] == NOAH


def test_db_check_rejects_decline_without_reason(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE waitlist_entries SET status='declined' WHERE participant_id=?", (LILY,))


def test_seat_returns_to_public_once_waitlist_is_empty(conn):
    for _ in range(2):
        domain.staff_resolve(conn, SWIM2_TUE, head_id(conn, SWIM2_TUE), "unreachable", None, None, NOW)
    domain.drop(conn, KIM, reg_id(conn, SWIM2_TUE, LEO), NOW)
    assert codes(domain.register(conn, PATEL, SWIM2_TUE, [AVA], NOW)) == [None]


# ---------------------------------------------------------------- metrics

def test_metrics_track_outreach_outcomes(conn):
    domain.drop(conn, GARCIA, reg_id(conn, SWIM2_TUE, SOFIA), NOW)
    domain.staff_enroll(conn, SWIM2_TUE, head_id(conn, SWIM2_TUE), NOW + timedelta(hours=10))
    domain.staff_resolve(conn, SWIM2_TUE, head_id(conn, SWIM2_TUE), "declined", "booked_elsewhere", "", NOW)
    m = domain.metrics(conn)
    assert m["seats_freed"] == 1
    assert m["outreach_conversion"] == 0.5
    assert m["median_time_to_fill_hours"] == 10
    assert m["declines_by_reason"]["Booked something else"] == 1
    assert m["multi_person_request_share"] == 1.0  # the Nguyen siblings joined together
    assert m["build_automation"] is False
