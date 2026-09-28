# Parks & Rec Registration

Parents browse programs and sign up people in their household for sections (specific classes, like Swim Level 2 on Thursdays). Seats are limited. When a section is full, families can join a **waitlist**, and staff work through it in order.

- **Live demo:** https://parks-and-recs.vercel.app
- **Code:** https://github.com/malabikasen/parks-and-recs
- **Stack:** Python 3.12, FastAPI, Jinja2 + HTMX, SQLite (raw SQL), pytest

## Try it in 2 minutes

There are no real logins. Pick a household from the **Acting as** dropdown. Click **Reset demo data** at any time to start the scenarios over.

1. **Two siblings, one seat.** As *Patel*, open **Swim Level 2 – Thu**. Tick Ava and Ben, then click **Register**. A popup says only 1 seat is left and asks who should get it. Pick one, leave "Put the others on the waitlist together" ticked, and confirm.
2. **Age limits.** Maya is 4, so she isn't listed for Level 2 (ages 6–8). Each card lists only the people who can sign up. Because of age limits, siblings often end up in different sections.
3. **A freed seat is held.** Switch to *Garcia* and drop Sofia from **Swim Level 2 – Tue**. The section now shows 1/2 enrolled but still says **Full**. The seat is being held for the Nguyen siblings, who are already waiting.
4. **Staff go in order.** Open **Staff** and click **Enroll** on #2 (Noah). It's rejected, because Lily (#1) is first in line. Now either enroll Lily, or mark her **Declined** with the reason "Wanted family members placed together" (one seat opened, but there are two siblings). Either way, the outcome is saved.
5. **Metrics.** Scroll down the Staff page to see the numbers that would tell us whether automatic promotion is worth building.
6. **Registration window.** Basketball Camp opens in 7 days, so you can't register for it yet.

## Run locally

```bash
brew install uv          # or see https://docs.astral.sh/uv/
uv sync
uv run uvicorn main:app --reload     # http://localhost:8000 (API docs at /docs)
uv run pytest -q                     # 37 tests, including a 20-thread race for the last seat
```

The database is `data/app.db`. It's created and filled with demo data on the first request. To start over, delete it or click **Reset demo data**.

---

## What I built, and why

- **Browsing.** Programs and their sections, with seats left, waitlist length, age range and opening time. This is the core of the brief.
- **Signing up several people at once.** Parents often register more than one child. Each person gets their own result, so one child who can't sign up doesn't block the others.
- **A "who gets the last seat?" popup.** If more children qualify than there are seats, the parent picks who gets them, and the rest can join the waitlist together. It's all or nothing: if someone else takes the seat while the parent is deciding, nothing is saved and they're asked again.
- **No overbooking when many people click at once.** The seat check and the insert run together inside `BEGIN IMMEDIATE`. In a test, 20 parents go for the last seat at the same moment, and exactly one gets it.
- **Age is judged on the section's start date.** We store date of birth, never age. A child who is 8 at signup but 9 by the first class counts as 9.
- **Registration opens at a set time for each program.** It's cheap, clearly needed, and it's when the rush to be first happens.
- **Staff, not the system, promote people from the waitlist** (see pushback #1). Staff can only act on whoever is first in line, and the server rejects anything else. Fairness is enforced by code, not left to staff.
- **Freed seats are held for the waitlist, one per person waiting.** When someone drops out while others are waiting, the public still sees "Full", so a newcomer can't take the seat. If more seats free up than there are people waiting, the extras go straight back to the public. Staff can only record "declined" or "unreachable" when there's a seat to offer.
- **Decision metrics.** The Staff page tracks the numbers that would tell us when to say yes to automatic promotion.

### Data model (`schema.sql`)

I designed the schema one decision at a time. The main choices:

- **Two tables: `registrations` (seats) and `waitlist_entries` (places in line).** They have different life cycles. A waitlist entry's outcome (enrolled, declined, unreachable or withdrawn) is exactly what the metrics need, and merging the tables would overwrite it.
- **Everyone gets their own waitlist entry.** People who join together share a `request_id`, but no rule depends on that link yet. It records how often families wait together, without deciding how to promote them (see the open question in pushback #1).
- **Counts and positions are calculated, never stored.** Queue position comes from `id` order, so there's no renumbering to get wrong. Enrollment is counted from the rows every time, so there's no stored counter to drift.
- **Each promoted seat points to the waitlist entry it came from.** `registrations.waitlist_entry_id` is UNIQUE, and NULL for direct sign-ups. So every seat can be traced back to its place in line, and one entry can't turn into two seats.
- **Column types say what a date means.** `DATE` is a calendar day with no time zone (date of birth, section start). `TIMESTAMP` is a moment in time, always in UTC (opening time, created, resolved). SQLite has no real date type, so CHECKs enforce the format. They use `date(x) IS x`, not `=`, because `date('next tuesday')` returns NULL and SQLite lets a NULL CHECK pass.
- **The database enforces the rules too, not just the app:**
  - A person can have only one active seat and one active waitlist entry per section (partial unique indexes).
  - Statuses, capacity and age limits must be valid (`CHECK` constraints).
  - Every declined entry has a reason, and no other entry does: `CHECK ((status = 'declined') = (reason IS NOT NULL))`.
  - Status and timestamps always agree. A dropped seat always has a `dropped_at`, and no other seat does. An entry that has stopped waiting always has a `resolved_at`, and a waiting one never does.
  - Household emails are unique, ignoring case, because the household is the account.
- **Smaller choices:**
  - Foreign keys and the waitlist queue are indexed.
  - Money is stored as whole cents, never floats.
  - Decline reasons come from a fixed list (`booked_elsewhere`, `wanted_siblings_together`, `schedule_changed`, `other`). That turns the two worries behind my pushback into numbers I can count.
  - The table is `participants`, not `children`. Senior Fitness is in the brief, so adults need to register too. Age limits already cover "60+" (`min_age = 60`, no maximum).
  - An append-only `events` log records things that have no row of their own, like "a household saw a full section" or "a seat freed up while people were waiting". It also works as an audit trail when someone asks "why did they get the spot?".

## What I pushed back on

### 1. Automatic waitlist promotion

**The ask:** when a seat opens, the next person on the waitlist is automatically registered and **charged**.

**My view:** don't build it yet.

- **Consent.** By the time a seat opens, the family may have booked something else. Charging them without asking again leads to refunds, chargebacks and angry calls.
- **Demand is unproven.** We don't know how often seats free up, or whether families still want them when they do. Joining the waitlist just means "we'll reach out if a seat opens". That captures interest cheaply, and staff do the outreach.
- **Fairness for families is unsolved.** A family that waitlists several children expects them to be promoted together. If only one seat opens, do we offer a partial spot, skip to the next family that fits, or hold seats until enough free up (leaving seats empty and unpaid)? Each is a different idea of "fair", and we should choose before automating anything. For now, staff can just ask the family, and the `wanted_siblings_together` decline reason records what families actually want.

**What would change my mind.** The Staff page shows these live. The thresholds are placeholders to agree with stakeholders.

| Signal | Threshold | Why it matters |
|---|---|---|
| Seats freed while people are waiting, as % of capacity | ≥ 5% | If seats rarely free up, there's nothing to automate |
| Outreach conversion (enrolled ÷ resolved) | ≥ 60% | If it's low, auto-charging would have billed families who'd moved on |
| Median time to fill a freed seat | > 48h | Empty seats and staff time are the real cost of doing it by hand |

Also tracked, without a threshold:
- **Declines by reason.** `booked_elsewhere` shows how often families double-book.
- **Share of requests for more than one person.** Shows how often the siblings problem comes up.
- **Oversubscription** (people waiting ÷ capacity). If it's high but few seats free up, the answer is to **add a section**, not to automate.

Even if every signal says yes, I'd build **automatic offers that families have a limited time to accept**, never automatic charging.

### 2. Sibling discount

**The ask:** 10% off the second child, 20% off the third.

- **The idea hasn't been tested.** It assumes a discount leads to more signups. But these are activities, and not every child in a family wants the same one. The real blocker may be schedules, age limits or interest, not price.
- **The rules aren't clear.**
  - Is it per program or per class?
  - Which child is "second": the next one to sign up, or the one in the cheaper class?
  - Is the discount taken back if the first sibling drops out?
  - Do waitlisted children count?
- **Age limits get in the way.** Siblings are different ages, so they're usually in different sections (like Maya above). A per-section discount would rarely apply.
- **What I'd do instead:** talk to families about what stops them signing up for more classes, and build from what we learn. The schema can already find the right families to ask: households with several age-eligible members but only one enrolled.

### 3. First come, first served

When registration opens, whoever clicks fastest wins. That's fine for now. For popular programs, I'd suggest a short lottery instead, for example shuffling every request made in the first 30 minutes.

## What I deferred

- **Payments and refunds.** Nothing is charged. Prices are for display only.
- **Real login.** A cookie picks the household. Even so, every parent action checks that the person, seat or waitlist entry belongs to that household. The Staff pages are open in the demo.
- **Notifications.** Staff contact families using the details shown on the Staff page.
- **Keeping siblings together automatically.** When signing up, the parent decides using the popup. On the waitlist, it's the open question in pushback #1.
- **Admin screens** for creating programs and sections.
- **More registration rules:** a close date, early registration for residents, and schedule-clash detection (schedules are free text for now).
- **Local time zones.** Times are stored and shown in UTC. A real department would set a time zone for each program.
- **Permanent hosting.** On Vercel, SQLite lives in `/tmp`, which is temporary and separate for each instance. That's fine for a demo with a Reset button. In production I'd use Postgres, and the schema would carry over almost unchanged.

## What I'd do next

### Product: validate before building

1. **Run the waitlist for a term, then decide on automation.** Agree the Staff-page thresholds with stakeholders, then collect a term of real data. Only if freed seats, outreach conversion and time to fill all clear the bar would I build automatic offers, with a time limit to accept and never auto-charging.
2. **Research before any sibling discount.** Talk to 5–8 families, found with a query the schema already supports (households with several age-eligible members but only one enrolled). Learn what actually stops more signups (schedules, age limits, interest or price), and build what the evidence supports.

### Engineering

1. **Postgres and real login.** This comes first, because everything else depends on real accounts and data that lasts.
   - Postgres instead of SQLite in Vercel's `/tmp`.
   - Household accounts (for example, magic-link login) and staff roles, replacing the demo cookie and the open Staff pages.
   - `SELECT … FOR UPDATE` on the section row replaces `BEGIN IMMEDIATE`, and `DATE` / `TIMESTAMPTZ` become native types. Otherwise the schema carries over unchanged.
2. **Outreach notifications.** Email or text the family at the front of the line when a seat is held for them, with a "reply to accept" link. It saves staff time, and it's the natural first step towards automatic offers if the data says yes.

---

## Tools & Process

**Tools:** Claude Code (Opus) in the desktop app. I used it to plan, to write the code, and to click through the UI in its built-in browser, both locally and on Vercel. A separate Claude subagent tried to break the code. uv, pytest, the Vercel CLI and the GitHub CLI did the rest. The full running log is in [NOTES.md](NOTES.md).

**How I used it:** I started in plan mode and made every product decision myself before any code was written. Claude suggested options with trade-offs, and I picked or overrode them. I designed the schema with it one decision at a time, instead of accepting its draft. Then it built in small steps, committing each one, and I tested each flow in the browser.

**Where I overrode or corrected the AI:**
- **Stack.** It suggested TypeScript to match your stack. I chose Python/FastAPI, because I'm faster in it.
- **Waitlist promotion.** It proposed auto-offers with a 48-hour hold. I didn't want to automate anything yet. The waitlist records interest, staff reach out, and the metrics decide later.
- **Siblings on the waitlist.** It treated each waitlist entry as independent. I pointed out that families expect siblings to be promoted together, and asked what happens if only one seat opens. That became the open question in pushback #1, plus the `request_id` column and the `wanted_siblings_together` decline reason.
- **Sibling discount.** It argued the spec was unclear. I questioned the premise itself: nobody has tested whether a discount drives signups.
- **UX: partial sibling signups.** Its first version quietly enrolled whichever child was ticked first. I called that bad UX and asked for the "who gets the last seat?" popup.
- **UX: the sign-up list.** Cards listed every household member, with notes like "(age 8 at start, ages 6–8 only)". I found that confusing and cut it to just the names of people who can sign up. The age range is already on the card, and the server still enforces every rule.
- **Scope.** Mid-plan, I switched from a CLI demo to a small UI deployed live, so reviewers can try it in 2 minutes.
- **Schema.** It caught a gap in what we'd agreed: a `children` table can't hold Senior Fitness adults. I chose to rename it to `participants`.

**My own review of the code:** after the build, I went through the schema myself.
- **I found a bug in the waitlist link.** A seat promoted from the waitlist was only marked `source = 'waitlist'`, not which entry it came from, so staff couldn't trace it back to its place in line. I had that replaced with a `waitlist_entry_id` foreign key.
- **I asked why dates were plain `TEXT`.** Claude explained that SQLite has no date type, so I had the columns declared `DATE` / `TIMESTAMP` to show what they mean, with CHECKs to enforce the format. Testing those CHECKs showed that `'next tuesday'` got through (the NULL gotcha above). Switching to `IS` fixed it, and there's now a test for it.
- **I renamed `participants.first_name` to `name`.**

**"Try to break it" review:** a separate subagent attacked the finished code with real probe scripts. It found **6 confirmed bugs**, all fixed with regression tests:

| What broke | Fix |
|---|---|
| **Time-to-fill was wrong.** It paired events by position, so one seat that went back to the public threw off every later pair: 101h reported instead of 1h, which flipped the decision signal. | Pair each enrollment with the most recent unpaired freed seat |
| **Oversized ids caused server errors.** An id ≥ 2^63 returned a 500, and a huge household cookie broke the page. | Ids are validated on the way in (→ 422), and the cookie is range-checked |
| **The join rate could be skewed.** A drop counted as "saw a full section", and a crafted POST produced a 300% join rate. | No view is recorded after a drop or withdrawal, and joining records the view |
| **Outreach conversion counted declines when no seat had been offered.** My own metrics test had this bug. | **Changed a rule:** an entry can only be resolved when a seat is free |
| **Open redirect** through the `Referer` header on the reset endpoint | Redirect only within the same site |
| **Database setup could race** when several processes handled the first request at once | Schema and seed data are created in one `BEGIN IMMEDIATE` (tested with 4 processes) |

What it tried that held up:
- 30 threads × 40 random operations, with resets every 10ms: no overbooking, and nobody was both enrolled and waiting
- Tampering with another household's data
- XSS
- Edge cases: birthdays, leap days, and the exact second registration opens

It also flagged a design trade-off: if 2 seats freed up with 1 person waiting, both were held. **I changed the rule** so only one seat is held per person waiting, and the extra seat goes back to the public.
