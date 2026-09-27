# Process log (source for README → Tools & Process)

Running log of what the AI (Claude Code, Opus) proposed and where I overrode or corrected it.

## Planning (Claude Code, plan mode)

| # | AI proposed | What I decided | Why |
|---|---|---|---|
| 1 | TypeScript + Express (matches Rec Tech's stack) | **Python + FastAPI** | I'm faster in Python. They said stack choice isn't penalized. |
| 2 | Waitlist promotion as **offer + 48h hold** (auto-offer, not auto-charge) | **Push back on the feature entirely.** Build an "I'm interested" waitlist; staff reach out manually, in order. | Validate demand before automating. Parents may have double-booked elsewhere by the time a seat opens. |
| 3 | Sibling discount: defer because the rules are ambiguous | Defer, but on **different grounds**: the underlying assumption (discount → more signups) is unvalidated. Not every child in a household wants the same activity. Run user-feedback sessions first. | Push back on the *premise*, not just the spec. |
| 4 | (not raised by AI) | **Multi-child waitlist problem**: a family waitlisting 2 kids expects promotion together. What if only 1 seat opens? Must be worked through before any auto-promotion. | The AI treated waitlist entries as independent. |
| 5 | API + tests + CLI demo script, no UI | Changed my mind: **minimal UI + live Vercel deploy** so reviewers can test in 2 minutes | Reviewer experience matters more than a CLI demo. |
| 6 | AI drafted a full schema | **Rejected the draft and co-designed the schema** one decision at a time (tables, queue unit, capacity enforcement, age rule, accounts, decline reasons, events, registration window) | The data model is what's being evaluated. I wanted to own each choice. |
| 7 | When a seat frees up and people are waiting | **Hold it for staff outreach** (the public sees "Full"); staff may only enroll the head of the queue | Keeps the "we'll reach out" promise, and fairness is enforced by code, not by staff discretion. |
| 8 | Metrics | Required an explicit **metric set + decision rule** for whether automated promotion is worth building | The pushback needs a path to "yes" backed by data. |

## Build

| # | What happened | Outcome |
|---|---|---|
| 9 | While seeding, the AI noticed the co-designed `children` table can't represent **Senior Fitness** participants (adults) | I chose to rename it to `participants` (age limits already handle "60+") |
| 10 | The AI's first version silently **partially filled** siblings: with 1 seat left, the first child ticked got it and the other was told "full" | **I overrode this as bad UX.** I asked for a popup: "Only 1 seat left. Who should get it? Put the others on the waitlist together." It's all-or-nothing, so if the seat is taken while the parent decides, nothing is written. |

## Adversarial review ("try to break it")

I had a separate Claude subagent attack the code: races, authz via cookie tampering, boundaries, metrics, web layer, DB init. It ran real probes. It reported 6 confirmed findings:

| Finding | Severity | What I did |
|---|---|---|
| **Time-to-fill paired events by position (`zip`).** One freed seat that went back to the public shifted every later pair, e.g. 101h reported instead of 1h, flipping the "build automation?" signal. | Medium | **Fixed.** Pair each enrollment with the most recent unpaired freed seat. Added the repro as a test. |
| **Ids ≥ 2^63 → 500 (OverflowError).** A huge household cookie broke the page until the cookie was cleared. | Med-Low | **Fixed.** Ids are validated at the edge (`1 ≤ id ≤ 2^63-1` → 422), and the cookie is range-checked. |
| **Join-rate funnel skew.** Dropping your own child counted as "saw a full section", and a crafted POST could give a 300% join rate. | Low-Med | **Fixed.** No view is recorded after a drop or withdraw, and joining records the view, so the rate can't exceed 100%. |
| **Outreach conversion counted resolutions made with no seat open** (no offer was ever made) | Low | **Fixed by changing a rule:** staff can only mark declined/unreachable when a seat is free, because otherwise it isn't an offer. My own metrics test had this bug. |
| **Open redirect** through `Referer` on `/demo/reset` | Low | **Fixed.** Redirect only within the same site. |
| **`ensure_db` raced across processes** ("table already exists") | Low | **Fixed.** Schema and seed happen in one `BEGIN IMMEDIATE`, with the check inside it. Added a 4-process test. |

What it tried that **held up**:
- 30 threads × 40 random operations on one section, with resets every 10ms: no overbooking, and nobody was both enrolled and waiting.
- Acting on another household's records is always rejected.
- XSS payloads in names and notes are escaped.
- Age on a birthday and on Feb 29, and the exact-second open time, behave correctly.
- Queue positions stay correct after a withdraw.

It also flagged a **design trade-off**: if 2 seats free up with only 1 person waiting, both were held. **I decided to change the rule**, so only one seat is held per person waiting and the surplus goes straight back to the public. `seat_freed` is now only logged for seats that are actually held, which keeps the metrics honest.
