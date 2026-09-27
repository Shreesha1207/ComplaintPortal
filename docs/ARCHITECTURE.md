# Architecture

## Principles

1. **Degrade, never drop.** Every layer has a working fallback. No uplink, no API
   key, no model — the platform still accepts and classifies a citizen's request.
2. **The country is data, not code.** Adding a nation is one JSON file.
3. **Everything the dashboard knows, the API exposes.** Civil society must be able
   to audit the same numbers the ministry sees.
4. **The AI never has the last word.** Low confidence escalates to a human.
5. **Runnable by a ministry with one laptop.** SQLite, no build step, no CDN.

## Layers

```
┌── CHANNELS ────────────────────────────────────────────────────────────┐
│  Web/PWA · Voice (server STT) · WhatsApp · SMS · IVR · worker · API    │
└───────────────────────────────┬────────────────────────────────────────┘
                                ▼  POST /api/requests
┌── UNDERSTANDING ── app/ai/ ──────────────────────────────────────────┐
│  AnalysisEngine (base.py) — one contract, swappable implementations     │
│    HeuristicEngine   offline, deterministic, 19 languages  [default]    │
│    GroqEngine        Groq; real translation, messy text     [optional]   │
│  redact → identify language → classify → extract → score confidence     │
└───────────────────────────────┬────────────────────────────────────────┘
                                ▼  Unified Request Envelope
┌── STORE ── app/db.py ────────────────────────────────────────────────┐
│  SQLite (WAL). requests + append-only audit_log.                        │
└───────────────────────────────┬────────────────────────────────────────┘
                                ▼
┌── FUSION ── app/analysis/fusion.py ────────────────────────────────────┐
│  requests ⋈ demographics ⋈ infrastructure ⋈ investment pipeline        │
│  → exhaustive (district × sector) matrix                               │
└───────────────────────────────┬────────────────────────────────────────┘
                                ▼
┌── PRIORITISATION ── app/analysis/priority.py, budget.py ───────────────┐
│  equity correction → weighted need → investment discount → flags       │
│  → per-factor contributions, counterfactual, confidence, rollups       │
└───────────────────────────────┬────────────────────────────────────────┘
                                ▼
┌── DELIVERY ── app/main.py, app/web/ ───────────────────────────────┐
│  FastAPI + OpenAPI                                                     │
│    public   /statistics  choropleth + district rollups, no account     │
│    staff    /review      verification queue                            │
│    admin    /dashboard   funding, budget simulator, weights, CSV       │
└────────────────────────────────────────────────────────────────────────┘
```

## Why these technologies

| Choice | Reason |
|---|---|
| **Python + FastAPI** | AI tooling is Python-native; OpenAPI docs come free, which matters for a DPG whose API *is* the product. |
| **SQLite** | A ministry must be able to run this on one laptop. Plain SQL and a thin access layer mean Postgres is a connection string plus a migration, not a rewrite. |
| **No frontend build step** | No npm, no bundler, no CDN. The dashboard renders behind a restrictive government proxy and in a demo room with no uplink. Every mark is hand-drawn SVG. |
| **Hex cartogram, not a real map** | On a geographic map, visual weight tracks land area, so vast empty districts dominate and dense ones vanish — inverting the signal for a demand map. Equal-area tiles give every unit equal weight, and work for any country pack. |
| **Lexicon before LLM** | Determinism (a funding decision must be reproducible at appeal), availability (rural intake on a bad uplink), and cost (tens of millions of requests a year through a frontier model is not a defensible budget line). |

## The Unified Request Envelope

Every channel produces one shape; everything downstream consumes it. It carries
the AI's uncertainty alongside its conclusions, so consumers can decide how much
to trust any single record. See `app/schemas.py`.

Key fields: `language` + `language_confidence`, `text_original` /
`text_redacted` / `text_en`, `sector` + `sector_confidence`, `urgency` +
`urgency_score`, `affected_population`, `ai_confidence`, `ai_engine`,
`ai_rationale`, `pii_types`, `status`.

`text_original` is retained deliberately: translation is lossy, and a citizen's
own words are the record of what they actually said.

## Data sources — demo vs production

The country pack schema is the contract. The demo generator stands in for feeds
that a real deployment would wire up:

| Field | Demo | Production adapter |
|---|---|---|
| Admin hierarchy | Country pack JSON | LGD (India), IBGE (Brazil), municipal demarcation (South Africa) |
| Population, literacy, urbanisation | Approximate census-era values | National census API, NDAP / data.gov.in, IBGE SIDRA, Stats SA |
| Infrastructure indices | Synthetic, coherent with demographics | Sector MIS: JJM (water), PMGSY (roads), HMIS (health), UDISE+ (education) |
| Investment pipeline | Synthetic, modelled on political salience | Budget portals, PFMS, scheme dashboards, state plan documents |
| Sector benchmarks | Country pack constant | National service-level benchmarks |

Every synthetic field is labelled `source: synthetic-demo` and surfaced in the UI
as `data_notice`. Nothing in the pipeline treats synthetic and real data
differently — swapping in a real feed changes the JSON, not the code.

## Scaling path

The prototype holds ~6,250 requests and scores 1,460 cells in well under a second,
with results cached against a data-version counter that bumps on write.

| Load | Change |
|---|---|
| ~10⁵ requests | Current design. SQLite is comfortable. |
| ~10⁶ | Postgres; move scoring to a scheduled job writing a `priority_cells` table. |
| ~10⁷+ | Queue intake (Kafka/Redis); batch classification via the Message Batches API at ~50% cost; partition by country; read replicas for the dashboard. |

The scoring engine is a pure function of (cells, weights, λ), so it parallelises
per country and per sector with no coordination.

## Security, privacy, responsible AI

- **PII is redacted at intake, deterministically**, before storage and before any
  text leaves the process. The LLM adapter only ever receives redacted text.
- **Location is never inferred from free text.** It comes from the citizen's own
  district selection. Entity extraction is display-only.
- **No protected attributes.** The model never infers caste, religion, ethnicity or
  political affiliation, and the LLM prompt forbids letting perceived group
  membership influence urgency or reach.
- **Append-only audit log.** Every AI decision and human override is recorded, so a
  recommendation can be reconstructed as of any past date and a disputed
  classification traced to whoever changed it.
- **Bias is addressed in the model, not the disclaimer.** The equity correction and
  the `silent_district` flag are the anti-bias mechanism; the measured
  1.42× → 0.84× inversion is the evidence it works.
- **Confidence gates influence.** Below threshold, a request waits for a human.

- **Roles separate money from verification.** Staff authenticate with
  PBKDF2-hashed passwords and server-side sessions. `admin` sees funding,
  the per-recommendation derivation, the budget simulator, the policy weights
  and exports; `reviewer` sees only the verification queue. Citizens never
  authenticate — intake is deliberately anonymous, because a login requirement
  would filter out the low-literacy, shared-phone population the equity
  correction exists to serve, and they see the district and region statistics
  without one, because a platform that collects reports and shows nothing back
  is asking for unpaid labour.
- **The boundary is the data, not the screen.** Every admin analytics response
  carries committed and unfunded amounts, so those endpoints are admin-only
  even where another role might want the rest of the payload.
- **Public statistics are a projection, not an unlocked guard.**
  `/api/analytics/public/*` republishes a hand-picked key set, because the
  admin payload carries three things that must not be public: money
  (`unfunded`, `committed`, `coverage`); district×sector grain, where 39% of
  India's 1,460 cells hold one or two requests and `/api/requests/public`
  already publishes enough to link against; and, in `/api/analytics/cell`,
  `text_original` — the citizen's words **before** PII redaction, reached via a
  `SELECT *`. The published grain stops at the district.
- **Three tests walk the live route table.** The admin surface and the public
  statistics surface are each named as an exact `(path, method)` set, a third
  test fails if any `/api/analytics` or `/api/export` route is on neither list,
  and two more serialise the public projections and grep them for money fields
  and for citizen text. A prefix rule would not have survived
  `/api/analytics/public/*`; naming both sets means a new route fails here until
  its author says which side it is on. The model-list and backfill routes are
  not yet guarded; see the note in the README.

Not yet built, and needed before any real deployment: SSO/2FA and a
password-reset flow, per-country scoping of staff accounts, rate limiting on
intake, duplicate and coordinated-campaign detection, encryption at rest, and a
data-retention policy.

## Repository layout

```
app/
  ai/        base.py · lexicon.py · heuristic.py · groq_engine.py
  analysis/  fusion.py · priority.py · budget.py
  packs/     build_packs.py · build_geo.py
             IN/BR/ZA .json (packs) · IN/BR/ZA .geo.json (region outlines)
  web/       index · citizen · statistics · dashboard · review
             + static/{app.css, viz.js, geomap.js, …}
  auth.py    staff accounts, sessions, role guards
  main.py    FastAPI app and REST API
  db.py      SQLite schema and access
  schemas.py Unified Request Envelope
  seed.py    synthetic multilingual corpus generator
docs/        ARCHITECTURE · PRIORITIZATION · DPG_COMPLIANCE · PITCH
tests/       test_app.py  (52 tests, runs with or without pytest)
```
