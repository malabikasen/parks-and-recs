-- Parks & Rec registration schema (SQLite).
-- Timestamps are ISO-8601 UTC strings; dates are YYYY-MM-DD.
-- Design notes: README.md → "Data model".

CREATE TABLE households (                     -- the account (fake login in the demo)
  id    INTEGER PRIMARY KEY,
  name  TEXT NOT NULL,
  email TEXT NOT NULL,
  phone TEXT
);

CREATE TABLE participants (                 -- any household member (kids, or adults for senior programs)
  id            INTEGER PRIMARY KEY,
  household_id  INTEGER NOT NULL REFERENCES households(id),
  first_name    TEXT NOT NULL,
  date_of_birth TEXT NOT NULL                 -- age is derived, never stored
);

CREATE TABLE programs (
  id                    INTEGER PRIMARY KEY,
  name                  TEXT NOT NULL,
  description           TEXT,
  registration_opens_at TEXT NOT NULL
);

CREATE TABLE sections (
  id             INTEGER PRIMARY KEY,
  program_id  INTEGER NOT NULL REFERENCES programs(id),
  name        TEXT NOT NULL,                  -- "Swim Level 2 – Tue"
  schedule    TEXT NOT NULL,                  -- display only: "Tuesdays 4–5pm"
  start_date  TEXT NOT NULL,                  -- age is checked as of this date
  capacity    INTEGER NOT NULL CHECK (capacity > 0),
  min_age     INTEGER CHECK (min_age >= 0),   -- whole years, inclusive; NULL = no bound
  max_age     INTEGER,
  price_cents INTEGER NOT NULL DEFAULT 0 CHECK (price_cents >= 0),  -- display only, no payments
  CHECK (min_age IS NULL OR max_age IS NULL OR min_age <= max_age)
);

-- A seat. Enrolled count is always derived from rows (never a stored counter);
-- writers count + insert inside BEGIN IMMEDIATE so two requests can't take the last seat.
CREATE TABLE registrations (
  id             INTEGER PRIMARY KEY,
  section_id     INTEGER NOT NULL REFERENCES sections(id),
  participant_id INTEGER NOT NULL REFERENCES participants(id),
  status         TEXT NOT NULL CHECK (status IN ('enrolled','dropped')),
  source         TEXT NOT NULL CHECK (source IN ('direct','waitlist')),
  created_at     TEXT NOT NULL,
  dropped_at     TEXT
);
CREATE UNIQUE INDEX one_enrollment ON registrations(section_id, participant_id) WHERE status = 'enrolled';

-- A place in line, one row per participant. Queue order = id order; position is derived.
-- Family members who joined in one action share request_id (captured, not yet acted on).
CREATE TABLE waitlist_entries (
  id             INTEGER PRIMARY KEY,
  section_id     INTEGER NOT NULL REFERENCES sections(id),
  participant_id INTEGER NOT NULL REFERENCES participants(id),
  request_id     TEXT NOT NULL,
  status         TEXT NOT NULL CHECK (status IN ('waiting','enrolled','declined','unreachable','withdrawn')),
  reason         TEXT CHECK (reason IN ('booked_elsewhere','wanted_siblings_together','schedule_changed','other')),
  note           TEXT,
  created_at     TEXT NOT NULL,
  resolved_at    TEXT,
  CHECK ((status = 'declined') = (reason IS NOT NULL))   -- a reason is required exactly when declined
);
CREATE UNIQUE INDEX one_waiting ON waitlist_entries(section_id, participant_id) WHERE status = 'waiting';

-- Append-only log: feeds the metrics and answers "why did they get the spot?"
CREATE TABLE events (
  id             INTEGER PRIMARY KEY,
  type           TEXT NOT NULL CHECK (type IN (
                   'section_full_seen','waitlist_joined','seat_freed',
                   'waitlist_enrolled','waitlist_resolved','registered','dropped')),
  section_id     INTEGER REFERENCES sections(id),
  household_id   INTEGER REFERENCES households(id),
  participant_id INTEGER REFERENCES participants(id),
  request_id     TEXT,
  at             TEXT NOT NULL
);
CREATE INDEX events_by_section ON events(section_id, type);
