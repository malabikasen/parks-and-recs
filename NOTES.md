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
(filled in as we go)

## Adversarial review ("try to break it")
(filled in after review)
