# Citizen development requests → national investment priorities

**A multilingual, multi-channel platform that turns fragmented citizen development
requests into explainable, budget-aware investment recommendations — and, more
usefully, shows governments where nobody is looking.**

Built for the BRICS *Innovation* challenge as a Digital Public Good: open API,
pluggable country packs, no vendor lock-in, runnable offline on one laptop.

```
Citizen ──▶ Voice · Text · WhatsApp · SMS/IVR
              │
              ▼
        Multilingual AI  ── transcribe → identify language → redact PII
              │              → classify sector → extract urgency & reach
              │              → score confidence → escalate if unsure
              ▼
     Unified Request Envelope  (one open schema, every channel)
              │
              ▼
        Fusion  ── + demographics + infrastructure indices + investment pipeline
              │
              ▼
   Prioritisation engine  ── equity-corrected demand, deficit, reach, severity,
              │               vulnerability, discounted by committed money
              ▼
    Policy dashboard · Budget simulator · Open REST API
```

---

## Quick start

```bash
pip install -r requirements.txt
./run.sh                      # or: python3 -m uvicorn app.main:app --reload
```

Open <http://127.0.0.1:8000>. The database seeds itself on first run with ~6,250
synthetic multilingual requests across India, Brazil and South Africa (~8s).

The app opens in citizen mode. Staff sign in from the link at the foot of that
page, or by going to `/login` directly; a citizen is never shown either.

| Page | What it is |
|---|---|
| `/` | Citizen intake — voice, text, WhatsApp and SMS/IVR channels. No account needed |
| `/citizen` | The same page, under its own name |
| `/login` | Staff sign-in — the only way into the two pages below |
| `/dashboard` | Policy dashboard — map, recommendations, weights, budget simulator. Admin only |
| `/review` | Human review queue for low-confidence classifications. Any staff member |
| `/about` | Overview of the platform and its entrances |
| `/api/docs` | Interactive OpenAPI documentation |

```bash
python3 tests/test_app.py   # 40 tests, no test runner required
```

---

## The idea in one paragraph

Most "citizen feedback" systems rank by volume. That measures **who is able to
complain**, not **where need is greatest** — so public money follows the
already-connected and the system launders inequity as evidence. This platform
corrects for it: it divides demand by an expected-participation index (smartphone
access, literacy, urbanisation), scores deficit and vulnerability from
administrative data that needs no citizen to speak at all, and then
**discounts priority where money is already committed**. What survives is the
thing a planner actually wants: places with real need, real demand, and no one
currently funding them.

## Four design decisions worth defending

**1. The loudest voice is not the greatest need.** Demand is divided by expected
participation, so twenty requests from a district where few can file outweigh
twenty from one where most can. On the seeded corpus this inverts the bias:
high-participation districts carry **1.42×** the raw demand signal of
low-participation ones; after correction the ratio is **0.84×**.

**2. Silence is a signal, not an absence.** Dividing by reach cannot help a
district that sends nothing. So demand is only 30% of the score — deficit, reach
and vulnerability come from administrative data. A silent district still ranks,
and is flagged `silent_district`: an outreach gap to close, never a reason to
deprioritise. With **zero** citizen requests the engine independently surfaces
Araria, Sitamarhi and Sukma — genuinely among India's most deprived districts.

**3. Committed money lowers priority — but never to zero.** Coverage discounts
the score by `1 − λ·C` with λ = 0.6, because a budget line is not delivered
infrastructure. λ = 1 would let an *announcement* remove a district from the
queue, which is the exact failure this platform exists to catch. What remains
are `blind_spot`s: strong demand, severe deficit, no money.

**4. Ranking is policy, not physics.** Weights and λ are query parameters, travel
with every API response, and are adjustable live on the dashboard. A ranking
that hides its weights is asserting that its politics are arithmetic.

## What the AI does — and what it is not allowed to do

### What the offline engine actually is

**No model, and no machine learning at all.** Its only imports are `math`, `re`
and `unicodedata` — standard library. It is Unicode script ranges for language
identification, a hand-written 1,000-term lexicon across 19 languages for
classification, and regex for PII and population extraction. That is why it is
deterministic, instant, and works with no network.

The honest limit of that design: **it categorises, it does not translate.** With
no model configured, `text_en` is a labelled gloss —
`[ta] healthcare access — reported as critical` — not the citizen's words. The
platform says so (`translated: false`) rather than passing a category label off
as a translation.

### Translation

A Tamil request is useless to an official who reads Hindi. With a model
configured, every request is translated twice:

- `text_en` — English, the cross-country pivot, so a Tamil and a Zulu request
  can be compared at all.
- `text_local` — the country's **link language**, declared per country pack
  (`hi` for India, `pt` for Brazil, `en` for South Africa). Translating only to
  English makes the platform legible to donors and illegible to the ministry
  using it.

Both appear under the original text in the dashboard and the review queue.
Requests already stored with a gloss can be upgraded in place, without wiping
the database:

```bash
curl -X POST "localhost:8000/api/translate/backfill?country=IN&limit=100"
```

Two engines behind one interface. The **offline engine** (default) does
script-based language identification, stem-tolerant lexicon classification across
19 languages, urgency grading, population extraction and PII redaction with no
network call — because rural intake must work on a bad uplink and a demo must not
depend on someone else's API. The **Groq adapter** (optional) adds real
translation and handles code-mixed, misspelt and idiomatic text. Any failure
degrades to the offline result rather than dropping a citizen's request.

### Running without any API key

**The platform is fully functional with no key and no internet.** That is the
default path, not a crippled fallback: every request still gets a language, a
sector, an urgency, an affected-population estimate and PII redaction. The
offline engine is what the seeded 6,250-request corpus was classified with, and
what every number in these docs was produced from.

### Adding Groq (optional)

```bash
export GROQ_API_KEY=gsk_...              # from console.groq.com
export GROQ_MODEL=llama-3.3-70b-versatile   # optional; pick any model your key can reach
./run.sh
```

Model line-ups on hosted providers change, so nothing is hardcoded as gospel —
`GET /api/ai/models` asks Groq what your key can actually reach and tells you
which engine is currently live:

```bash
curl localhost:8000/api/ai/models
```

Other knobs: `GROQ_BASE_URL` (defaults to `https://api.groq.com/openai/v1`, so any
OpenAI-compatible endpoint works) and `GROQ_TIMEOUT` (seconds).

The adapter talks to Groq over the Python standard library — no SDK, no extra
dependency. `requirements.txt` stays at three packages whether or not you use it.

Three hard limits:

- **PII redaction is never delegated to a model.** A deterministic regex pass runs
  on every request, and the LLM only ever receives already-redacted text.
- **Low confidence does not become a funding recommendation.** Anything below
  threshold is held for a human; a request in review carries reduced weight and a
  rejected one carries none.
- **Disagreement escalates.** When both engines run and reach different sectors,
  the request goes to a human even if both were individually confident.

Every AI decision and human override is written to an append-only audit log.

## Roles: citizens vs staff

**Citizens never sign in.** Intake is anonymous on purpose — requiring an
account to report a broken handpump would filter out exactly the low-literacy,
shared-phone population the equity correction exists to serve.

**Staff sign in at `/login`**, in two roles:

| | `reviewer` | `admin` |
|---|---|---|
| Verification queue | ✓ | ✓ |
| Priority rankings and demand map | — | ✓ |
| **Funding, budget simulator, exports** | — | ✓ |

The first time you open `/login`, the page asks you to create the administrator
account: pick a username and password there and you are signed in straight
away. Nothing needs to be set in the environment, and no password is ever
printed to a log. Once that account exists the page becomes an ordinary
sign-in form, and the setup endpoint refuses to run again.

For an unattended deployment, where nobody is at a browser to complete that
screen, set the account in the environment instead and it is created on
startup:

```bash
export ADMIN_USERNAME=admin ADMIN_PASSWORD='choose-something-real'
./run.sh
```

### Configuration with `.env`

Put settings in a `.env` file in the project root and they are loaded at
startup — no extra package needed, and a real environment variable always wins
over a line in the file. `.env.example` lists everything you can set; copy it
to `.env` to begin. `.env` is gitignored.

```bash
cp .env.example .env     # then edit, e.g. GROQ_API_KEY=gsk_...
```

Add a verification officer:

```bash
python3 -c "from app import db, auth; db.init_db(); \
  auth.create_user('officer', 'their-password', 'reviewer', 'Block Officer')"
```

The boundary is drawn at **money, not pages**: every analytics response carries
committed and unfunded figures, so those endpoints are admin-only even over the
API. A test parses the route table and fails if any funding route is missing its
guard, so a new endpoint cannot leak by omission. The same test pins the other
half of the split: citizen intake must keep working with no account.

That guard covers the funding surface, which is sound: every analytics and
export route is admin-only today, and the test now holds it that way. Two
other routes are a different matter -- they carry no guard at all, by
omission rather than by design, and are open to anyone who can reach the
server: `GET /api/ai/models`, which reveals which model is configured, and
`POST /api/translate/backfill`, which spends translation API credits. The
unauthenticated examples above use them as-is. Guard both before any real
deployment.

## Adding a country

One JSON file in `app/packs/`, no code change. It declares the administrative
hierarchy, languages, sectors, hex-map layout, demographic and infrastructure
indices, and the public investment pipeline. India, Brazil and South Africa ship
as worked examples; `build_packs.py` regenerates them reproducibly.

## Voice input

Voice is the channel that reaches the populations this platform flags as silent —
low literacy, low smartphone penetration, languages no mainstream ASR covers. So
it has to actually work, which means transcribing **server-side** rather than
relying on the browser's own engine (Chrome/Safari only, secure-origin only, and
weakest on exactly the languages that matter here).

```bash
export GROQ_API_KEY=gsk_...    # Whisper large v3, ~99 languages — works today
./run.sh
```

`GET /api/voice/status` reports which path a browser will take and why; when
voice cannot work, the UI names the specific reason instead of showing a dead
microphone.

**XVoice (xvoicekeyboard.com) is not yet integrated.** There is a configured,
tested adapter slot for it, but its real API contract has not been verified —
see [`docs/VOICE.md`](docs/VOICE.md) for exactly what is needed to finish it,
including the case where XVoice is an on-device keyboard, in which case no
server adapter is wanted at all.

---

## ⚠ Data provenance

Administrative names and approximate populations are **real**. Every index
(literacy, urbanisation, poverty, smartphone penetration, per-sector
infrastructure) and **every investment record is synthetic demonstration data**
generated by `app/packs/build_packs.py`. They are calibrated to plausible
ranges so the prototype behaves realistically. **They are not official statistics
and must not be cited.** Production adapters for real sources are described in
`docs/ARCHITECTURE.md`.

One modelling assumption is deliberate and documented: investment is generated as
a function of urbanisation and literacy — of political salience — rather than of
need. That reproduces a well-observed pattern, and it is what creates the blind
spots the platform is built to find.

## Documentation

| | |
|---|---|
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | **Start here.** Plain-language walkthrough of what we built and why |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Terser, implementation-focused version — data sources, scaling path |
| [`docs/PRIORITIZATION.md`](docs/PRIORITIZATION.md) | The scoring maths, in full, with worked examples |
| [`docs/DPG_COMPLIANCE.md`](docs/DPG_COMPLIANCE.md) | Digital Public Good standard, indicator by indicator |
| [`docs/VOICE.md`](docs/VOICE.md) | Voice input: how it works, why it used to fail, and what XVoice integration needs |
| [`docs/PITCH.md`](docs/PITCH.md) | Six-minute demo script |

## Licence

MIT — a Digital Public Good has to be forkable by any government that wants it.
