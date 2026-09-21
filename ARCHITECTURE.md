# Architecture

*A plain-language walkthrough of what we built and why. If you only read one
document in this repo, read this one.*

> For the full scoring mathematics, see [`docs/PRIORITIZATION.md`](docs/PRIORITIZATION.md).
> For a terser, implementation-focused version of this document, see
> [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md). For the demo script, see
> [`docs/PITCH.md`](docs/PITCH.md). For how voice input works and what XVoice
> integration still needs, see [`docs/VOICE.md`](docs/VOICE.md).

---

## 1. What problem this solves

Governments run complaint portals. Citizens file tickets, tickets get closed,
and nobody ends up smarter about **where to build next**. That's because a
complaint count answers the wrong question — it tells you who was loud, not
who was underserved.

This is not a complaint portal. It's a pipeline that turns citizen voices,
in any language, through any channel, into a ranked, explainable account of
*where the need is greatest and why* — and it goes out of its
way to surface the places that **never show up in complaint data at all**,
because those are the places most likely to be genuinely forgotten.

## 2. The one-paragraph idea

If you rank public investment by how many complaints a district files, you are
really ranking by **who has a smartphone, can read, and lives in a city** —
because those are the people who *can* file a complaint. Fund the loudest
districts and you fund the ones already best served, and you get to call it
"data-driven." This platform corrects for it: it divides citizen demand by an
*expected-participation index*, scores infrastructure deficit and vulnerability
straight from administrative data (which needs nobody to speak), and writes out
the exact math behind every ranking so it can be challenged, not just trusted.

It stops there, deliberately. There is no budget, no committed-investment figure
and no allocation anywhere in the system. It says where the need is; what a
government spends on it is a separate decision this platform does not make and
holds no data about.

## 3. Who uses it, and how

```
┌─────────────────────────────────────────────────────────────────────┐
│  CITIZEN                                                             │
│  Speaks or types a development request in their own language,        │
│  through whichever channel they already use — voice, WhatsApp,       │
│  a web form, or a basic SMS/IVR line that needs no smartphone.       │
└──────────────────────────────┬────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────────┐
│  SYSTEM                                                               │
│  Understands the request, strips personal information, classifies    │
│  it, fuses it with population and infrastructure data for that       │
│  exact district, and scores it against every other unmet need in     │
│  the country — with the reasoning attached, not hidden.              │
└──────────────────────────────┬────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────────┐
│  POLICYMAKER                                                         │
│  Opens a map, sees where need is highest, opens any ranked need to   │
│  see exactly why it ranked there, and adjusts the ranking weights    │
│  live to see how sensitive the order is to the policy behind it.     │
└─────────────────────────────────────────────────────────────────────┘
```

## 4. End-to-end system diagram

```
 CHANNELS                     UNDERSTANDING                    STORAGE
┌──────────────┐   text    ┌──────────────────────┐   record  ┌───────────────┐
│ Voice (STT)   │──────────▶│  AI Analysis Engine   │──────────▶│  SQLite        │
│ Text / Web    │           │                       │           │  requests +    │
│ WhatsApp      │           │  1. redact PII        │           │  audit_log     │
│ SMS / IVR     │           │  2. detect language   │           │  (append-only) │
│ Field worker  │           │  3. classify sector    │           └───────┬───────┘
│ API           │           │  4. grade urgency      │                   │
└──────────────┘           │  5. estimate reach      │                   │
                            │  6. score confidence    │                   │
                            │                         │                   │
                            │  offline engine (default)│                  │
                            │  + optional Groq engine  │                  │
                            └──────────────────────┘                   │
                                                                         │
                                                                         ▼
 ┌───────────────────────────┐   join    ┌─────────────────────────────────┐
 │  COUNTRY PACK (per nation) │──────────▶│  FUSION                          │
 │  demographics               │           │  every (district × sector) cell, │
 │  infrastructure indices     │           │  even ones with zero requests —  │
 │  languages, sectors, map    │           │  silence must not disappear      │
 │  (no budget, no currency)   │           └───────────────┬──────────────────┘
 └───────────────────────────┘                            │
                                                              ▼
                                            ┌─────────────────────────────────┐
                                            │  PRIORITISATION ENGINE           │
                                            │  equity-correct demand            │
                                            │  → weight 5 factors               │
                                            │  → flag unmet need & silence      │
                                            │  → attach the exact math          │
                                            └───────────────┬──────────────────┘
                                                              ▼
                                            ┌─────────────────────────────────┐
                                            │  DELIVERY                        │
                                            │  National dashboard (map,         │
                                            │  ranking, ranking weights)        │
                                            │  Human review queue               │
                                            │  Open REST API (/api/docs)        │
                                            │  CSV export                       │
                                            └─────────────────────────────────┘
```

## 5. Walking through it, stage by stage

### Stage 1 — Intake (any channel, any language)

A citizen's request arrives as raw text — either typed directly, transcribed
from speech, or relayed through WhatsApp/SMS. Whatever the channel, it lands
at one API endpoint (`POST /api/requests`) and becomes one shape: the
**Unified Request Envelope**. That's the interoperability seam of the whole
system — a government's existing grievance system, a field worker's app, or a
messaging gateway can all plug into this one endpoint without the pipeline
caring how the text arrived.

### Stage 2 — Understanding (the AI layer)

Every request goes through six steps, always in this order:

1. **Redact** — phone numbers, emails, ID numbers are stripped by
   deterministic pattern matching *before* anything else touches the text.
   This step never depends on a model, because personal data protection
   should never be probabilistic.
2. **Detect language** — by Unicode script first, then marker words to
   disambiguate languages that share a script (Hindi vs. Marathi, both in
   Devanagari).
3. **Classify sector** — matched against a hand-built, stem-tolerant lexicon
   across 19 languages (water, roads, health, education, power, transport,
   housing, digital, agriculture, jobs).
4. **Grade urgency** — critical / high / medium / low, from explicit signals
   in the text ("a death has occurred" outweighs "for three months").
5. **Estimate reach** — how many people this affects, from an explicit number
   in the text or inferred from the scale implied ("our village," "our ward").
6. **Score confidence** — if the classification isn't confident enough, the
   request is held for a human. It is never allowed to influence a published
   decision unsupervised.

There are two interchangeable engines behind this, sharing one interface:

- **Offline heuristic engine** *(default, always on)* — no network call, no
  API key, deterministic. This is what makes the platform work on a bad rural
  uplink and in a demo room with no internet at all.
- **Groq-powered engine** *(optional, `GROQ_API_KEY`)* — adds real translation
  and handles messy, code-mixed, or idiomatic phrasing the lexicon misses. If
  it fails for any reason — no key, bad key, rate limit, network, malformed
  response — the system falls back to the offline engine automatically. The
  demo never breaks because a model call didn't come back.

  Groq serves open-weight models (Llama, Qwen, GPT-OSS and others) over an
  OpenAI-compatible API. Open weights are the right posture for a Digital
  Public Good: a government can eventually self-host the same model instead of
  depending on a vendor. Pick the model with `GROQ_MODEL`; `GET /api/ai/models`
  asks the API what your key can actually reach, so a changed line-up upstream
  can't wedge the deployment. Because the endpoint is OpenAI-compatible,
  pointing `GROQ_BASE_URL` at any other compatible server — including a
  self-hosted one — works without a code change.

When both engines are available and they *disagree* on the sector, that
disagreement itself is treated as a red flag and the request goes to a human,
even if each engine was individually confident.

### Stage 2a — Who is allowed to see what

Two audiences, drawn apart deliberately.

**Citizens never sign in.** Intake is anonymous by design. Requiring an account
to report a broken handpump would filter out precisely the low-literacy,
shared-phone, no-email population the equity correction exists to serve — the
front door would undo the maths behind it.

**Staff sign in, in two roles:**

| | `reviewer` | `admin` |
|---|---|---|
| Verification queue | ✓ | ✓ |
| Read a stored request | ✓ | ✓ |
| Priority rankings, demand map | — | ✓ |
| Full derivation of any ranked cell | — | ✓ |
| Ranking weights, CSV export | — | ✓ |

A block officer confirming a request is real does not thereby get the national
rankings, and does not need them.

**The boundary is the data, not the screen.** With money gone this is no longer
a boundary around funding figures. It is a boundary around the national
aggregate and, in `GET /api/analytics/cell/...`, the stored request rows behind
a cell — which include `text_original`, the citizen's words *before* redaction.
Drawing the line at pages would have left those rows reachable over the API —
which is where they would actually leak. Anything published more widely than
this needs a hand-picked projection, the way `/api/requests/public` already
does it.

Mechanically: PBKDF2-HMAC-SHA256 passwords, opaque session tokens stored only
as SHA-256 hashes, httpOnly `SameSite=Lax` cookies, and lockout after repeated
failures. All standard library — no new dependency to check a password. The
navigation renders from a server-supplied capability map, but that is a
courtesy: the endpoints enforce the boundary themselves, so a stale or tampered
nav grants nothing.

### Stage 3 — Storage

Every request and every human decision on it is written to SQLite — one table
for requests, one **append-only** audit log. Nothing is ever deleted or
silently overwritten; a reviewer's correction is recorded next to the
original AI classification, so a disputed recommendation can always be traced
back to who changed what and when.

### Stage 4 — Fusion

This is where a citizen's request stops being an isolated data point. For the
country it belongs to, the system loads a **country pack** — one JSON file per
nation containing its administrative hierarchy (states/districts, or
provinces/municipalities), demographic indices (literacy, urbanisation,
poverty, smartphone penetration) and per-sector infrastructure indices. It
carries no budget, no currency and no investment pipeline.

The fusion step builds a grid of **every district crossed with every
sector** — not just the cells that received a request. A district that has
sent nothing still gets a row for every sector. This one design choice is
what keeps silent populations visible instead of vanishing from the analysis.

### Stage 5 — Prioritisation (the core of the project)

Each cell in that grid gets scored on five factors:

| Factor | What it measures |
|---|---|
| **Demand** | How loudly citizens have asked — *corrected* for who is actually able to ask (see §6) |
| **Gap** | How far the measured infrastructure is below the national benchmark |
| **People** | How many people the deficit affects |
| **Severity** | How urgent the requests received were |
| **Vulnerability** | Poverty, low literacy, digital exclusion |

These combine into a single **Need** score, scaled to 0–100. That is the
priority index a reader sees; nothing is added to it or subtracted from it
afterwards.

Two things get explicitly flagged:

- **Unmet need** — real citizen demand against a severe deficit. This is the
  "act now" list. It used to be called a *blind spot* and additionally required
  that no public money was committed to the cell; that clause went when the
  investment data did, so the flag now asserts only the half it can still see.
- **Silent district** — a severe deficit and high vulnerability, but *zero*
  citizen requests received. This is the "go find out why nobody's talking
  to us" list — treated as an outreach failure, never as evidence of low need.

Every number in the final score is attributable: the five factor
contributions are written out per cell and **sum exactly to the priority
index**, along with a plain-language rationale. Nothing is a black box.

### Stage 6 — Delivery

- **National dashboard** — a hex map of the country (equal-area tiles, so
  colour reflects need rather than land area), a ranked list of unmet needs,
  a full breakdown of any cell's math, and live-adjustable ranking weights, so
  the politics behind the order is visible rather than asserted.
- **Human review queue** — every request the AI wasn't confident about,
  waiting for a person to confirm, correct, or reject it.
- **Open REST API** (`/api/docs`) — everything the dashboard shows is also a
  documented API response. A civil-society group or another ministry system
  can pull the exact same numbers without adopting this dashboard.
- **CSV export** — for anyone who just wants the numbers in a spreadsheet.

## 6. The equity correction, explained without jargon

Imagine two districts with an identical, genuine water crisis. District A is
urban, literate, and everyone has a smartphone — 200 people file a complaint.
District B is rural, has patchy literacy, and almost nobody has a smartphone
— 3 people manage to file a complaint, probably by walking to the one place
with signal. A volume-based system ranks District A first and calls it
data-driven decision-making. It isn't — it's measuring who could complain,
not who needs help.

Each district's demand is divided by an **expected-participation index**
built from smartphone penetration, literacy, and urbanisation. That 3-person
signal from District B is worth far more per request than the 200-person
signal from District A, because it represents a much larger population that
mostly *couldn't* speak up at all.

**Measured on this project's seeded data:** before correction, high-
participation districts carried **1.42×** the raw demand signal of low-
participation ones. After correction, that ratio is **0.84×** — the
advantage doesn't just shrink, it flips. That's the number that proves the
mechanism is doing real work, not just a nice idea in a slide.

What this can't fix: a district that sends *zero* requests still has zero
demand signal, however you divide it. That's why demand is capped at 30% of
the total score — the other 70% comes from data that needs no citizen to
speak at all, and why a silent district is explicitly flagged rather than
quietly scored low.

## 7. Technology choices, and why

| Choice | Why |
|---|---|
| **Python + FastAPI** | AI/NLP tooling is Python-native; FastAPI generates full interactive API docs for free — important for a project whose open API *is* part of the product. |
| **SQLite** | Runs on one laptop with no setup. A ministry pilot shouldn't need a database administrator. The access layer is thin enough that moving to Postgres later is a connection-string change, not a rewrite. |
| **No frontend build step** | No npm, no bundler, no CDN dependency. Every chart is hand-drawn SVG. This has to render behind a restrictive government network proxy and in a demo room with no internet — and it does. |
| **Hex cartogram instead of a real map** | On a real geographic map, land area dominates what you see — a huge, sparsely populated district visually swamps a small, dense one. Equal-area tiles give every administrative unit the same visual weight, so colour reflects *need*, not geography. |
| **A lexicon-based engine before an LLM** | Three reasons: a published statistic has to be reproducible the same way every time (an LLM call isn't guaranteed to be); rural intake has to keep working with no internet; and running every request in a country through a frontier model isn't a defensible line item at national scale. The LLM is an upgrade layered on top, not a dependency underneath. |

## 8. What's real data and what's synthetic

First, a distinction that's easy to lose: **the code is not a simulation.**
Every piece of logic here — language detection, classification, PII redaction,
the scoring maths — is real, production-shaped code doing real computation.
Point it at real data tomorrow and it produces real answers, no rewrite.

What's standing in for reality is the **data**:

| Thing | Status |
|---|---|
| Classification, scoring, ranking, redaction, the API, the dashboard | **Real code.** Runs for real, tested, deterministic. |
| Administrative names + populations (IN/BR/ZA) | **Real.** |
| Infrastructure indices, demographics beyond population | **Synthetic.** Generated by `app/packs/build_packs.py`. |
| The 6,250 seeded citizen requests | **Synthetic.** Generated by `app/seed.py`, then run through the *same* intake pipeline a live request uses. |

**Real:** administrative names (states, districts, provinces, municipalities)
and approximate population figures for India, Brazil, and South Africa.

**Synthetic, generated for this demo:** every infrastructure index and every
demographic percentage beyond population. They're generated to be internally
consistent and realistic in shape, but they are demo data and must never be
quoted as real statistics.

They are labelled as such in the API (`/api/countries/{code}` → `data_notice`),
but in the UI that notice is rendered **only on the admin dashboard**. Anything
that puts these figures in front of a wider audience has to carry the notice
with them.

This matters because it's the difference between "the platform works" and
"the platform is lying about India." It works. It is not lying, because it
never claims the numbers are real.

## 9. Where this can't be trusted yet (and knows it)

A prototype should say what it hasn't built, not just what it has:

- **Authentication is in, but it is the simple kind.** Staff sign in with a
  username and password (PBKDF2, server-side sessions, lockout after repeated
  failures) and roles separate national analytics from verification — see §5a.
  What it
  does *not* have: single sign-on, 2FA, password-reset flow, or per-country
  scoping of staff accounts. A ministry will want all four.
- **No duplicate or coordinated-submission detection.** A single actor could
  currently inflate demand for one district by submitting many requests.
  (The equity correction blunts this for a *high-participation* district
  doing it, but doesn't stop it.)
- **No encryption at rest, no formal data-retention policy.** Fine for a
  demo database; not fine for real citizen data.
- **Demographic and infrastructure data is synthetic**, as above. Swapping in
  real national data sources is a data-engineering task, not a code change —
  the schema is already the contract — but it hasn't been done.

These are listed in full, with what a production fix looks like for each, in
[`docs/DPG_COMPLIANCE.md`](docs/DPG_COMPLIANCE.md).

## 10. Repository map

```
app/
├── ai/                   Language understanding
│   ├── base.py             the shared engine interface
│   ├── lexicon.py          sector/urgency terms across 19 languages
│   ├── heuristic.py        offline engine (default)
│   └── groq_engine.py      Groq-powered engine (optional upgrade)
├── analysis/             The analytical core
│   ├── fusion.py            joins requests to country data
│   └── priority.py          the scoring model (see docs/PRIORITIZATION.md)
├── packs/                One JSON file per country — the "add a nation" seam
│   ├── build_packs.py      generator (reproducible, documented assumptions)
│   ├── IN.json / BR.json / ZA.json
├── web/                  No-build-step frontend
│   ├── index / citizen / dashboard / review .html
│   └── static/              app.css, viz.js (hand-rolled SVG charts), page scripts
├── auth.py               staff accounts, sessions, role guards
├── main.py               FastAPI app — every route the system exposes
├── db.py                 SQLite schema + access (requests, audit_log)
├── schemas.py             the Unified Request Envelope
└── seed.py                synthetic multilingual demo corpus generator

docs/
├── ARCHITECTURE.md        terser, implementation-focused version of this file
├── PRIORITIZATION.md      the full scoring maths, with worked examples
├── DPG_COMPLIANCE.md      Digital Public Good standard, checked honestly
└── PITCH.md                six-minute demo script

tests/test_app.py       41 tests — the load-bearing claims, not just CRUD
```

## 11. Running it

```bash
pip install -r requirements.txt
./run.sh
```

Open `http://127.0.0.1:8000`. The database seeds itself on first run with
~6,250 synthetic multilingual requests across all three countries (~8 seconds).
Every number quoted in this document reproduces exactly from that seed.

```bash
python3 tests/test_app.py     # 41/41 — no test runner required
```

---

*Administrative names and populations are real. Every index in this deployment
is synthetic demonstration data. See §8.*
