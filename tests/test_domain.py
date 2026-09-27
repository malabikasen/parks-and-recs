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


def test_only_as_many_seats_held_as_people_waiting(conn):
    domain.drop(conn, GARCIA, reg_id(conn, SWIM2_TUE, SOFIA), NOW)       # 1 free, 2 waiting → held
    domain.staff_resolve(conn, SWIM2_TUE, head_id(conn, SWIM2_TUE), "unreachable", None, None, NOW)
    domain.drop(conn, KIM, reg_id(conn, SWIM2_TUE, LEO), NOW)            # 2 free, 1 waiting → 1 held, 1 public
    assert codes(domain.register(conn, PATEL, SWIM2_TUE, [AVA], NOW)) == [None]
    assert codes(domain.register(conn, PATEL, SWIM2_TUE, [BEN], NOW)) == ["SECTION_FULL"]  # Noah's seat
    assert domain.metrics(conn)["seats_freed"] == 1  # Leo's seat went public, so it isn't counted


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
    domain.drop(conn, GARCIA, reg_id(conn, SWIM2_TUE, SOFIA), NOW)  # a seat to offer
    lily_entry = head_id(conn, SWIM2_TUE)
    with pytest.raises(DomainError) as e:
        domain.staff_resolve(conn, SWIM2_TUE, lily_entry, "declined", None, None, NOW)
    assert e.value.code == "REASON_REQUIRED"
    domain.staff_resolve(conn, SWIM2_TUE, lily_entry, "declined", "wanted_siblings_together", "", NOW)
    assert domain._head(conn, SWIM2_TUE)["participant_id"] == NOAH


def test_db_check_rejects_decline_without_reason(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE waitlist_entries SET status='declined' WHERE participant_id=?", (LILY,))


def test_resolving_requires_a_free_seat(conn):
    # No seat → no offer was made → declining/unreachable would skew outreach conversion.
    with pytest.raises(DomainError) as e:
        domain.staff_resolve(conn, SWIM2_TUE, head_id(conn, SWIM2_TUE), "unreachable", None, None, NOW)
    assert e.value.code == "NO_SEAT_AVAILABLE"


def test_seat_returns_to_public_once_waitlist_is_empty(conn):
    domain.drop(conn, KIM, reg_id(conn, SWIM2_TUE, LEO), NOW)
    for _ in range(2):
        domain.staff_resolve(conn, SWIM2_TUE, head_id(conn, SWIM2_TUE), "unreachable", None, None, NOW)
    assert codes(domain.register(conn, PATEL, SWIM2_TUE, [AVA], NOW)) == [None]


# ---------------------------------------------------------------- metrics

def test_metrics_track_outreach_outcomes(conn):
    domain.drop(conn, GARCIA, reg_id(conn, SWIM2_TUE, SOFIA), NOW)
    domain.drop(conn, KIM, reg_id(conn, SWIM2_TUE, LEO), NOW)
    domain.staff_enroll(conn, SWIM2_TUE, head_id(conn, SWIM2_TUE), NOW + timedelta(hours=10))
    domain.staff_resolve(conn, SWIM2_TUE, head_id(conn, SWIM2_TUE), "declined", "booked_elsewhere", "", NOW)
    m = domain.metrics(conn)
    assert m["seats_freed"] == 2
    assert m["outreach_conversion"] == 0.5
    assert m["median_time_to_fill_hours"] == 10
    assert m["declines_by_reason"]["Booked something else"] == 1
    assert m["multi_person_request_share"] == 1.0  # the Nguyen siblings joined together
    assert m["build_automation"] is False


# ---------------------------------------------------------------- regressions from the adversarial review

def test_time_to_fill_ignores_seat_that_went_back_to_public(conn):
    """Reviewer's repro: zip() paired the first freed seat (filled by the public) with a later enrollment."""
    t0 = NOW
    domain.drop(conn, GARCIA, reg_id(conn, SWIM2_TUE, SOFIA), t0)                    # seat_freed #1
    for _ in range(2):
        domain.staff_resolve(conn, SWIM2_TUE, head_id(conn, SWIM2_TUE), "unreachable", None, None, t0)
    domain.register(conn, PATEL, SWIM2_TUE, [AVA], t0 + timedelta(hours=1))          # public takes it
    domain.join_waitlist(conn, PATEL, SWIM2_TUE, [BEN], t0 + timedelta(hours=100))
    domain.drop(conn, KIM, reg_id(conn, SWIM2_TUE, LEO), t0 + timedelta(hours=100))  # seat_freed #2
    domain.staff_enroll(conn, SWIM2_TUE, head_id(conn, SWIM2_TUE), t0 + timedelta(hours=101))
    assert domain.metrics(conn)["median_time_to_fill_hours"] == 1.0


def test_join_rate_never_exceeds_100_percent(conn):
    # Joining without a recorded page view (crafted POST / stale tab) used to give 300%.
    domain.join_waitlist(conn, PATEL, SWIM2_TUE, [AVA], NOW)
    rate = next(s for s in domain.metrics(conn)["sections"] if s["id"] == SWIM2_TUE)["join_rate"]
    assert rate == 1.0


# ---------------------------------------------------------------- choose who gets the last seat(s)

def test_split_needed_when_more_eligible_than_seats(conn):
    choice = domain.split_needed(conn, PATEL, SWIM2_THU, [AVA, BEN, MAYA], NOW)
    assert choice.seats == 1
    assert [p["id"] for p in choice.eligible] == [AVA, BEN]      # Maya is too young, so not a contender
    assert codes(choice.blocked) == ["AGE_INELIGIBLE"]
    assert domain.split_needed(conn, PATEL, SWIM2_THU, [AVA], NOW) is None


def test_register_split_enrolls_choice_and_waitlists_rest_together(conn):
    outcomes = domain.register_split(conn, PATEL, SWIM2_THU, [BEN], [AVA], NOW)
    assert [o.ok for o in outcomes] == [True, True]
    assert domain._is_enrolled(conn, SWIM2_THU, BEN)
    assert domain._head(conn, SWIM2_THU)["participant_id"] == AVA


def test_register_split_is_all_or_nothing_if_seat_taken_meanwhile(conn):
    choice = domain.split_needed(conn, PATEL, SWIM2_THU, [AVA, BEN], NOW)
    assert choice.seats == 1
    # ...while the parent is looking at the popup, someone else takes the seat.
    conn.execute("INSERT INTO participants (id, household_id, first_name, date_of_birth) "
                 "SELECT 99, ?, 'Fast', date_of_birth FROM participants WHERE id = ?", (OKAFOR, ZARA))
    domain.register(conn, OKAFOR, SWIM2_THU, [99], NOW)
    with pytest.raises(DomainError) as e:
        domain.register_split(conn, PATEL, SWIM2_THU, [BEN], [AVA], NOW)
    assert e.value.code == "SEATS_CHANGED"
    assert not domain._is_enrolled(conn, SWIM2_THU, BEN)
    assert not domain._is_waiting(conn, SWIM2_THU, AVA)          # nothing half-written
