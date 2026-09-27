# Parks & Rec Registration

Parents browse programs and register household members for sections that have limited capacity. When a section fills up, they join a **waitlist**, and staff work through it in order.

**Live demo:** https://parks-and-recs.vercel.app · **Code:** https://github.com/malabikasen/parks-and-recs · **Stack:** Python 3.12, FastAPI, Jinja2 + HTMX, SQLite (raw SQL), pytest

## Try it in 2 minutes

Logins are fake. Pick a household from the **Acting as** dropdown. Use **Reset demo data** at any time to replay the scenarios.

1. **Siblings, one seat left.** As *Patel*, open **Swim Level 2 – Thu**. Tick Ava and Ben, then click **Register**. A popup says *"Only 1 seat left. Who should get it?"*. Pick one, keep "Put the others on the waitlist together" ticked, and confirm.
2. **Age gate.** Maya (4) is greyed out for Level 2, which is for ages 6–8. Because of age limits, siblings often end up in different sections.
3. **A freed seat is held.** Switch to *Garcia* and drop Sofia from **Swim L2 – Tue**. The section now shows 1/2 enrolled but still says **Full**, because the seat is held for the Nguyen siblings already waiting.
4. **Staff go in order.** Open **Staff**. Click **Enroll** on #2 (Noah) and it's rejected. Then either enroll #1 (Lily), or mark her **Declined → "Wanted family members placed together"** (only one seat opened for two siblings). Either way, the outcome is recorded.
5. **Metrics.** Further down the Staff page is the data that would decide whether automated promotion is worth building.
6. **Registration window.** Basketball Camp opens in 7 days, so its sections can't be registered for yet.

## Run locally

```bash
brew install uv          # or see https://docs.astral.sh/uv/
uv sync
uv run uvicorn main:app --reload     # http://localhost:8000 (API docs at /docs)
uv run pytest -q                     # 35 tests, including a 20-thread race for the last seat
```

The database file is `data/app.db`. It's created and seeded on first request. Delete it, or click Reset, to start over.

---

## What I built, and why

| Built | Why |
|---|---|
| Browsing programs → sections, showing seats, waitlist size, age range and open time | Core requirement |
| Registering several household members at once, with **one result per person** | Parents often register more than one child. One ineligible child shouldn't block their siblings. |
| **"Who gets the last seat?" popup** | When there are more eligible children than seats, the parent chooses who gets them, and the others can join the waitlist together. It's all-or-nothing, so if the seat is taken while they decide, nothing is written and they're asked to choose again. |
| **Capacity enforced under concurrency** | The check-then-insert runs inside `BEGIN IMMEDIATE`. A test sends 20 parents at the last seat at once, and exactly one wins. |
| **Age gate**: whole years **as of the section start date** | We store the date of birth, never the age. A child who is 8 at signup but 9 by the first class is judged as 9. |
| **Registration opens at a specific time** per program | Cheap, clearly needed, and it's where the rush to be first happens |
| **Waitlist, with promotion by staff** instead of automatic promotion | See the pushback below. Staff can only act on the **first person in line**, and the server rejects anything out of order, so fairness is enforced by code rather than left to staff discretion. |
| **Freed seats are held for the waitlist, one per person waiting** | When someone drops, the public still sees "Full" while anyone is waiting. Otherwise a newcomer could take the seat the waitlist was promised. Seats beyond the number of people waiting go straight back to the public, so none sit empty for nobody. Staff can only record "declined" or "unreachable" when a seat is actually free to offer. |
| **Decision metrics** on the Staff page | The pushback needs a data-backed way to get to "yes" |

### Data model (`schema.sql`)

I designed the schema one decision at a time. The key choices:

- **Two tables: `registrations` (a seat) and `waitlist_entries` (a place in line).** They have different life cycles. A waitlist entry's *outcome* (enrolled / declined / unreachable / withdrawn) is exactly the data the metrics need, and merging the tables would overwrite it.
- **One queue entry per participant, plus a shared `request_id`.** Family members who join together are linked, but no rule is attached to that yet. It records how often families wait together, without committing to how to promote them (see the open question below).
- **Queue position is derived from `id` order and never stored**, so there's no renumbering to get wrong.
- **The enrolled count is always calculated from rows, never a stored counter.** Nothing can drift. The Postgres equivalent is `SELECT … FOR UPDATE` on the section row.
- **A registration promoted from the waitlist points to the entry it came from** (`registrations.waitlist_entry_id`, UNIQUE, NULL for direct sign-ups), so every seat can be traced back to its place in line, and one entry can't produce two seats.
- **Dates are typed by what they mean.** `DATE` is a calendar date with no time zone (date of birth, section start). `TIMESTAMP` is a moment in time, always UTC (open time, created, resolved). SQLite has no real date type, and a declared `DATE` is only a label, so each column also has a CHECK that enforces the format. That CHECK uses `date(x) IS x`, not `=`. For a value like `'next tuesday'`, `date()` returns NULL, and a NULL CHECK *passes* in SQLite. In Postgres these would be `DATE` and `TIMESTAMPTZ`.
- **The database enforces invariants too:**
  - partial unique indexes: one active enrollment and one active waitlist entry per person per section
  - `CHECK` constraints on statuses, capacity and age bounds
  - `CHECK ((status = 'declined') = (reason IS NOT NULL))`: a decline can't be saved without a reason
  - status and timestamp always agree: `dropped_at` is set exactly when a seat is dropped, and `resolved_at` is set exactly when an entry stops waiting
  - a unique household email (case-insensitive), since the household is the account
  - indexes on the foreign keys and on the waitlist queue
- **Money is stored as integer cents**, never floats.
- **Structured decline reasons** (`booked_elsewhere`, `wanted_siblings_together`, `schedule_changed`, `other`) turn the two concerns behind my pushback into numbers I can count.
- **`participants`, not `children`.** Senior Fitness is in the brief, so an adult has to be able to register. Age limits already handle "60+" (`min_age = 60`, no max).
- **An append-only `events` log** records things that have no row of their own ("a household saw a full section", "a seat freed while people were waiting"). It also serves as an audit trail when someone asks "why did they get the spot?".

## What I pushed back on

### 1. "Automatic waitlist promotion: the next person is auto-registered and **charged**"
I don't think this should be built yet.
- **Consent.** By the time a seat opens, the family may have booked something else. Charging them without asking again leads to refunds, chargebacks and angry calls.
- **Unvalidated demand.** We don't know how often seats actually free up, or whether families still want them when they do. The waitlist button ("I'm interested, we'll reach out if a seat opens") captures that demand cheaply, and staff do the outreach.
- **An unsolved fairness problem.** A household that waitlists several children expects them to be promoted **together**. What if only one seat opens? Do we offer a partial spot, skip ahead to the next family that fits, or hold seats until enough free up (leaving seats empty and unpaid)? Each answer defines "fair" differently, and **we should decide before automating anything**. For now staff can just ask the family, and the `wanted_siblings_together` decline reason records what families actually want.

**What would change my mind** (shown live on the Staff page; the thresholds are placeholders to agree with stakeholders):

| Signal | Threshold | Why it matters |
|---|---|---|
| Seats freed while a waitlist exists, as % of capacity | ≥ 5% | If seats rarely free up, automation has nothing to do |
| Outreach conversion (enrolled ÷ resolved) | ≥ 60% | If it's low, auto-charging would have billed families who'd moved on |
| Median time to fill a freed seat | > 48h | Empty paid seats and staff effort are the real cost of doing it by hand |
| *Supporting:* declines by reason, share of multi-person requests, oversubscription (waiting ÷ capacity) | — | `booked_elsewhere` measures double-booking. Multi-person requests show how often the siblings problem comes up. High oversubscription with few freed seats means **add a section**, not automate. |

Even if every signal says yes, I'd build **automated offers with a time-limited consent hold, never automatic charging**.

### 2. "Sibling discount: 10% off the second child, 20% off the third"
- **The premise hasn't been tested.** It assumes a discount drives more signups. But these are activities, and not every child in a household wants the same class. The blocker may be schedules, age limits or interest, not price.
- **The spec is ambiguous.**
  - Is it per program or per class?
  - Is the "second child" ordered by signup time or by price?
  - Is the discount taken back if the first sibling drops?
  - Do waitlisted children count?
- **Age limits undercut it.** Siblings are different ages, so they're usually in *different sections* (see Maya above). A per-section discount would rarely apply.
- **Instead:** run user-feedback sessions on what stops families signing up for more classes, and build from that evidence. The schema can already answer a useful question: *households with several age-eligible members where only one is enrolled.*

### 3. First-come-first-served at the moment registration opens
It rewards whoever clicks fastest. That's fine for now, but for high-demand programs I'd propose a short lottery window, e.g. every request in the first 30 minutes is shuffled.

## What I deferred

- **Payments and refunds.** There's no charging anywhere, and prices are display-only.
- **Real authentication.** A cookie picks the household, but every parent action still checks that the person, registration or waitlist entry belongs to that household. The Staff pages are open in the demo.
- **Notifications.** Staff reach out using the contact details shown on the Staff page.
- **Automatic "keep siblings together".** When registering, the parent chooses via the popup. For waitlist promotion it's the open question above.
- **Admin screens** for creating programs and sections, a registration close date, resident-priority windows, and schedule-conflict detection (the schedule is free text).
- **Local time zones.** Times are stored and shown in UTC. A real department would store its time zone per program.
- **Durable hosting.** On Vercel, SQLite lives in `/tmp`, which is per instance and temporary. That's fine for a demo with a Reset button. In production I'd use Postgres, and the schema carries over almost unchanged.

## What I'd do next

1. Agree the metric thresholds with stakeholders, and settle the siblings-together promotion rule using the decline-reason data.
2. Postgres + real auth (household accounts, staff roles), with row locks replacing `BEGIN IMMEDIATE`.
3. Email/SMS for outreach, with a "reply to accept" link, which leads naturally to automated offers with a consent hold.
4. Payments that charge only when a family accepts, with idempotency keys.
5. A lottery window for high-demand openings.

---

## Tools & Process

**Tools:** Claude Code (Opus) in the desktop app, used for planning, writing the code, driving the in-app browser to click through the UI locally and on Vercel, and a separate Claude subagent that tried to break the code. uv, pytest, the Vercel CLI and the GitHub CLI handled the rest. The full running log is in [NOTES.md](NOTES.md).

**How I used it:** I started in *plan mode* and made every product call myself before any code was written. Claude proposed options with trade-offs, and I picked or overrode them. I **co-designed the schema** with it one decision at a time instead of accepting its draft. Then it built in small committed steps, and I tested each flow in the browser.

**Where I overrode or corrected the AI:**
- **Stack:** it recommended TypeScript to match your stack. I chose Python/FastAPI, where I'm faster.
- **Waitlist promotion:** it proposed *auto-offer with a 48h hold*. I pushed back on automating anything yet. The waitlist records interest, staff reach out, and metrics decide later.
- **Siblings on the waitlist:** it treated waitlist entries as independent. I raised that **families expect siblings to be promoted together**, and asked what happens if only one seat opens. That became the open question above, plus the `request_id` column and the `wanted_siblings_together` decline reason.
- **Sibling discount:** it argued the spec was ambiguous. I pushed back on the *premise*, that a discount drives signups, which hasn't been tested.
- **Partial sibling registration:** its first version silently enrolled whichever child was ticked first. I called that bad UX and asked for the **"who gets the last seat?" popup**.
- **UI scope:** I changed my mind mid-plan from a CLI demo to a minimal UI deployed live, so reviewers can test it in 2 minutes.
- **Schema:** it caught a gap in what we'd agreed (`children` can't represent Senior Fitness adults), and I chose to rename the table to `participants`.

**My own review of the code:** after the build, I went through the schema myself.
- **I found a bug in the waitlist link.** A seat promoted from the waitlist was only marked `source = 'waitlist'`, not *which* entry it came from, so staff couldn't trace a seat back to its place in line. I had it replaced with a `waitlist_entry_id` foreign key.
- **I questioned why dates were plain `TEXT`.** Claude explained that SQLite has no native date type, so I had the columns declared `DATE` / `TIMESTAMP` to show what they mean, with CHECKs to enforce the format. Testing those CHECKs turned up a gotcha: `'next tuesday'` was accepted, because a NULL CHECK passes. Switching to `IS` fixed it, and it's now a test.
- **I renamed `participants.first_name` to `name`.**

**"Try to break it" review:** a separate subagent attacked the finished code with real probe scripts and found **6 confirmed bugs**, all fixed with regression tests:

| Finding | Fix |
|---|---|
| Time-to-fill paired events by position, so one seat that went back to the public skewed every later pair (101h reported instead of 1h, which flipped the decision signal) | Pair each enrollment with the most recent unpaired freed seat |
| Ids ≥ 2^63 caused a 500. A huge household cookie bricked the page. | Ids validated at the edge (→ 422), cookie range-checked |
| Join-rate funnel skew: a drop counted as "saw full", and a crafted POST gave a 300% join rate | No view recorded after a drop or withdraw, and joining records the view |
| Outreach conversion counted "declined" when no seat had been offered | **Changed a rule:** resolving requires a free seat. My own metrics test had this bug. |
| Open redirect through `Referer` on the reset endpoint | Redirect only within the same site |
| First-request DB init raced across processes | Schema and seed in one `BEGIN IMMEDIATE` (4-process test) |

What it tried that held up:
- 30 threads × 40 random operations with resets every 10ms: no overbooking, and nobody both enrolled and waiting
- cross-household tampering
- XSS
- birthday, leap-day and exact-second boundaries

It also flagged a design trade-off: if 2 seats freed up with 1 person waiting, both were held. **I changed the rule** so only one seat is held per person waiting, and the surplus goes back to the public.
