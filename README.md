# Parks & Rec Registration

Parents browse programs and register household members for sections that have limited capacity. When a section fills up, they join a **waitlist**, and staff work through it in order.

**Live demo:** https://parks-and-recs.vercel.app · **Code:** https://github.com/malabikasen/parks-and-recs · **Stack:** Python 3.12, FastAPI, Jinja2 + HTMX, SQLite (raw SQL), pytest

## Try it in 2 minutes

Logins are fake. Pick a household from the **Acting as** dropdown. Use **Reset demo data** at any time to replay the scenarios.

1. **Siblings, one seat left.** As *Patel*, open **Swim Level 2 – Thu**. Tick Ava and Ben, then click **Register**. Ava gets the last seat, and Ben is told the section is full.
2. **Age gate.** Maya (4) is greyed out for Level 2, which is for ages 6–8. Because of age limits, siblings often end up in different sections.
3. **Waitlist.** Tick Ben and click **I'm interested**. He's now #1 on the Thu waitlist ("We'll reach out if a seat opens up").
4. **A freed seat is held.** Switch to *Garcia* and drop Sofia from **Swim L2 – Tue**. The section now shows 1/2 enrolled but still says **Full**, because the seat is held for the Nguyen siblings already waiting.
5. **Staff go in order.** Open **Staff**. Click **Enroll** on #2 (Noah) and it's rejected. Enroll #1 (Lily). Now Noah is first, but there's no seat, so mark him **Declined → "Wanted family members placed together"**.
6. **Metrics.** Further down the Staff page is the data that would decide whether automated promotion is worth building.
7. **Registration window.** Basketball Camp opens in 7 days, so its sections can't be registered for yet.

## Run locally

```bash
brew install uv          # or see https://docs.astral.sh/uv/
uv sync
uv run uvicorn main:app --reload     # http://localhost:8000 (API docs at /docs)
uv run pytest -q                     # 18 tests, including a 20-thread race for the last seat
```

The database file is `data/app.db`. It's created and seeded on first request. Delete it, or click Reset, to start over.

---

## What I built, and why

| Built | Why |
|---|---|
| Browsing programs → sections, showing seats, waitlist size, age range and open time | Core requirement |
| Registering several household members at once, with **one result per person** | Parents often register more than one child. One ineligible child shouldn't block their siblings. |
| **Capacity enforced under concurrency** | The check-then-insert runs inside `BEGIN IMMEDIATE`. A test sends 20 parents at the last seat at once, and exactly one wins. |
| **Age gate**: whole years **as of the section start date** | We store the date of birth, never the age. A child who is 8 at signup but 9 by the first class is judged as 9. |
| **Registration opens at a specific time** per program | Cheap, clearly needed, and it's where the rush to be first happens |
| **Waitlist, with promotion by staff** instead of automatic promotion | See the pushback below. Staff can only act on the **first person in line**, and the server rejects anything out of order, so fairness is enforced by code rather than left to staff discretion. |
| **A freed seat is held for the waitlist** | When someone drops, the public still sees "Full" while anyone is waiting. Otherwise a newcomer could take the seat the waitlist was promised. |
| **Decision metrics** on the Staff page | The pushback needs a data-backed way to get to "yes" |

### Data model (`schema.sql`)

I designed the schema one decision at a time. The key choices:

- **Two tables: `registrations` (a seat) and `waitlist_entries` (a place in line).** They have different life cycles. A waitlist entry's *outcome* (enrolled / declined / unreachable / withdrawn) is exactly the data the metrics need, and merging the tables would overwrite it.
- **One queue entry per participant, plus a shared `request_id`.** Family members who join together are linked, but no rule is attached to that yet. It records how often families wait together, without committing to how to promote them (see the open question below).
- **Queue position is derived from `id` order and never stored**, so there's no renumbering to get wrong.
- **The enrolled count is always calculated from rows, never a stored counter.** Nothing can drift. The Postgres equivalent is `SELECT … FOR UPDATE` on the section row.
- **The database enforces invariants too:**
  - partial unique indexes: one active enrollment and one active waitlist entry per person per section
  - `CHECK` constraints on statuses and capacity
  - `CHECK ((status = 'declined') = (reason IS NOT NULL))`: a decline can't be saved without a reason
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
- **Keeping siblings together when registering directly.** Right now they can be split, one enrolled and one told it's full. It's the same open question as the waitlist one above.
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

_Filled in from [NOTES.md](NOTES.md) after the adversarial review._
