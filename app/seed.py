"""Demo data. All dates are relative to `now` so the demo never goes stale.

Built-in scenarios:
- Swim L2 Tue is FULL with a sibling pair (Nguyen) already waiting together.
- Swim L2 Thu has ONE seat left, so registering the Patel siblings shows a partial fill.
- Maya Patel (4) is too young for Level 2, which shows age gating splitting siblings.
- Basketball opens in 7 days (registration gate).
- Senior Fitness shows an adult participant (June Kim, 67).
"""

from datetime import date, datetime, timedelta, timezone

from .domain import iso


def _years_before(d: date, years: int, days: int = 30) -> str:
    """A date of birth that makes someone exactly `years` old on `d`."""
    try:
        birthday = d.replace(year=d.year - years)
    except ValueError:  # Feb 29
        birthday = d.replace(year=d.year - years, day=28)
    return (birthday - timedelta(days=days)).isoformat()


def seed(conn, now: datetime | None = None) -> None:
    now = now or datetime.now(timezone.utc)
    start = (now + timedelta(days=21)).date()
    opened, opens_later = iso(now - timedelta(days=1)), iso(now + timedelta(days=7))

    conn.executemany("INSERT INTO households (id, name, email, phone) VALUES (?, ?, ?, ?)", [
        (1, "Patel", "priya.patel@example.com", "555-0101"),
        (2, "Kim", "dan.kim@example.com", "555-0102"),
        (3, "Garcia", "rosa.garcia@example.com", "555-0103"),
        (4, "Nguyen", "linh.nguyen@example.com", "555-0104"),
        (5, "Okafor", "chi.okafor@example.com", "555-0105"),
    ])
    conn.executemany("INSERT INTO participants (id, household_id, name, date_of_birth) VALUES (?, ?, ?, ?)", [
        (1, 1, "Ava", _years_before(start, 8)),
        (2, 1, "Ben", _years_before(start, 6)),
        (3, 1, "Maya", _years_before(start, 4)),
        (4, 2, "Leo", _years_before(start, 7)),
        (5, 2, "June", _years_before(start, 67)),
        (6, 3, "Sofia", _years_before(start, 7)),
        (7, 3, "Mateo", _years_before(start, 10)),
        (8, 4, "Lily", _years_before(start, 6)),
        (9, 4, "Noah", _years_before(start, 8)),
        (10, 5, "Zara", _years_before(start, 7)),
    ])
    conn.executemany("INSERT INTO programs (id, name, description, registration_opens_at) VALUES (?, ?, ?, ?)", [
        (1, "Swimming Lessons", "Levels 1–3 at the community pool.", opened),
        (2, "Youth Basketball Camp", "One-week morning camp.", opens_later),
        (3, "Art Studio", "Watercolor and drawing for kids.", opened),
        (4, "Senior Fitness", "Low-impact classes for older adults.", opened),
    ])
    s = start.isoformat()
    conn.executemany(
        "INSERT INTO sections (id, program_id, name, schedule, start_date, capacity, min_age, max_age, price_cents) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", [
            (1, 1, "Swim Level 1 – Sat", "Saturdays 9–10am", s, 3, 4, 6, 6000),
            (2, 1, "Swim Level 2 – Tue", "Tuesdays 4–5pm", s, 2, 6, 8, 7500),
            (3, 1, "Swim Level 2 – Thu", "Thursdays 4–5pm", s, 2, 6, 8, 7500),
            (4, 1, "Swim Level 3 – Wed", "Wednesdays 5–6pm", s, 2, 9, 12, 7500),
            (5, 2, "Basketball Camp – Mornings", "Mon–Fri 9am–12pm", s, 12, 8, 13, 15000),
            (6, 3, "Watercolor for Kids", "Mondays 3:30–4:30pm", s, 6, 5, 10, 4500),
            (7, 4, "Chair Yoga", "Mon & Wed 10–11am", s, 10, 60, None, 0),
        ])

    t = iso(now - timedelta(hours=20))
    for section_id, participant_id, household_id in [(2, 6, 3), (2, 4, 2), (3, 10, 5), (4, 7, 3)]:
        conn.execute("INSERT INTO registrations (section_id, participant_id, status, created_at) "
                     "VALUES (?, ?, 'enrolled', ?)", (section_id, participant_id, t))
        conn.execute("INSERT INTO events (type, section_id, household_id, participant_id, at) VALUES ('registered', ?, ?, ?, ?)",
                     (section_id, household_id, participant_id, t))

    t = iso(now - timedelta(hours=18))
    conn.execute("INSERT INTO events (type, section_id, household_id, at) VALUES ('section_full_seen', 2, 4, ?)", (t,))
    for participant_id in (8, 9):
        conn.execute("INSERT INTO waitlist_entries (section_id, participant_id, request_id, status, created_at) "
                     "VALUES (2, ?, 'seed0001', 'waiting', ?)", (participant_id, t))
        conn.execute("INSERT INTO events (type, section_id, household_id, participant_id, request_id, at) "
                     "VALUES ('waitlist_joined', 2, 4, ?, 'seed0001', ?)", (participant_id, t))
