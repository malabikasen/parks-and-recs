-- Parks & Rec registration schema (SQLite).
-- Design notes: README.md → "Data model".
--
-- Dates and times: SQLite has no native date type. A declared DATE/TIMESTAMP column is a label,
-- not a guarantee, so each one is paired with a CHECK that enforces the format:
--   DATE      = a calendar date, no time zone        'YYYY-MM-DD'            (date(x) IS x)
--   TIMESTAMP = a moment in time, always UTC         'YYYY-MM-DDTHH:MM:SSZ'  (strftime(...) = x)
-- ISO-8601 strings sort chronologically and work with SQLite's date functions.
-- In Postgres these would be DATE and TIMESTAMPTZ.

CREATE TABLE households (                     -- the account (fake login in the demo)
  id    INTEGER PRIMARY KEY,
  name  TEXT NOT NULL,
  email TEXT NOT NULL UNIQUE COLLATE NOCASE,
  phone TEXT
);

CREATE TABLE participants (                   -- any household member (kids, or adults for senior programs)
  id            INTEGER PRIMARY KEY,
  household_id  INTEGER NOT NULL REFERENCES households(id),
  name          TEXT NOT NULL,
  date_of_birth DATE NOT NULL CHECK (date(date_of_birth) IS date_of_birth)   -- age is derived, never stored
);
CREATE INDEX participants_by_household ON participants(household_id);

CREATE TABLE programs (
  id                    INTEGER PRIMARY KEY,
  name                  TEXT NOT NULL,
  description           TEXT,
  registration_opens_at TIMESTAMP NOT NULL
    CHECK (strftime('%Y-%m-%dT%H:%M:%SZ', registration_opens_at) IS registration_opens_at)
);

CREATE TABLE sections (
  id          INTEGER PRIMARY KEY,
  program_id  INTEGER NOT NULL REFERENCES programs(id),
  name        TEXT NOT NULL,                  -- "Swim Level 2 – Tue"
  schedule    TEXT NOT NULL,                  -- display only: "Tuesdays 4–5pm"
  start_date  DATE NOT NULL CHECK (date(start_date) IS start_date),   -- age is checked as of this date
  capacity    INTEGER NOT NULL CHECK (capacity > 0),
  min_age     INTEGER CHECK (min_age >= 0),   -- whole years, inclusive; NULL = no bound
  max_age     INTEGER CHECK (max_age >= 0),
  price_cents INTEGER NOT NULL DEFAULT 0 CHECK (price_cents >= 0),  -- integer cents, never floats; display only
  CHECK (min_age IS NULL OR max_age IS NULL OR min_age <= max_age)
);
CREATE INDEX sections_by_program ON sections(program_id);

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
  created_at     TIMESTAMP NOT NULL CHECK (strftime('%Y-%m-%dT%H:%M:%SZ', created_at) IS created_at),
  resolved_at    TIMESTAMP CHECK (strftime('%Y-%m-%dT%H:%M:%SZ', resolved_at) IS resolved_at),
  CHECK ((status = 'declined') = (reason IS NOT NULL)),       -- a reason is required exactly when declined
  CHECK ((status = 'waiting') = (resolved_at IS NULL))        -- resolved exactly when no longer waiting
);
CREATE UNIQUE INDEX one_waiting ON waitlist_entries(section_id, participant_id) WHERE status = 'waiting';
CREATE INDEX waitlist_queue ON waitlist_entries(section_id, id) WHERE status = 'waiting';

-- A seat. Enrolled count is always derived from rows (never a stored counter);
-- writers count + insert inside BEGIN IMMEDIATE so two requests can't take the last seat.
CREATE TABLE registrations (
  id                INTEGER PRIMARY KEY,
  section_id        INTEGER NOT NULL REFERENCES sections(id),
  participant_id    INTEGER NOT NULL REFERENCES participants(id),
  waitlist_entry_id INTEGER UNIQUE REFERENCES waitlist_entries(id),  -- NULL = registered directly
  status            TEXT NOT NULL CHECK (status IN ('enrolled','dropped')),
  created_at        TIMESTAMP NOT NULL CHECK (strftime('%Y-%m-%dT%H:%M:%SZ', created_at) IS created_at),
  dropped_at        TIMESTAMP CHECK (strftime('%Y-%m-%dT%H:%M:%SZ', dropped_at) IS dropped_at),
  CHECK ((status = 'dropped') = (dropped_at IS NOT NULL))     -- dropped exactly when dropped_at is set
);
CREATE UNIQUE INDEX one_enrollment ON registrations(section_id, participant_id) WHERE status = 'enrolled';
CREATE INDEX registrations_by_participant ON registrations(participant_id);

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
  at             TIMESTAMP NOT NULL CHECK (strftime('%Y-%m-%dT%H:%M:%SZ', at) IS at)
);
CREATE INDEX events_by_section ON events(section_id, type);
