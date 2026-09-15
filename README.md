# AGORA — citizen voice → national priority

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
./run.sh                      # or: python3 -m uvicorn agora.main:app --reload
```

Open <http://127.0.0.1:8000>. The database seeds itself on first run with ~6,250
synthetic multilingual requests across India, Brazil and South Africa (~8s).

| Page | What it is |
|---|---|
| `/` | Overview |
| `/citizen` | Citizen intake — voice, text, WhatsApp and SMS/IVR channels |
| `/dashboard` | Policy dashboard — map, recommendations, weights, budget simulator |
| `/review` | Human review queue for low-confidence classifications |
| `/api/docs` | Interactive OpenAPI documentation |

```bash
python3 tests/test_agora.py   # 20 tests, no test runner required
```

---

## The idea in one paragraph

Most "citizen feedback" systems rank by volume. That measures **who is able to
complain**, not **where need is greatest** — so public money follows the
already-connected and the system launders inequity as evidence. AGORA corrects
demand by an expected-participation index (smartphone access, literacy,
urbanisation), scores deficit and vulnerability from administrative data that
needs no citizen to speak at all, and then **discounts priority where money is
already committed**. What survives is the thing a planner actually wants: places
with real need, real demand, and no one currently funding them.

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

Two engines behind one interface. The **offline heuristic engine** (default) does
script-based language identification, stem-tolerant lexicon classification across
19 languages, urgency grading, population extraction and PII redaction with no
network call — because rural intake must work on a bad uplink and a demo must not
depend on someone else's API. The **Claude adapter** (`ANTHROPIC_API_KEY`) adds
real translation and handles code-mixed, misspelt and idiomatic text. Any failure
degrades to the offline result rather than dropping a citizen's request.

Three hard limits:

- **PII redaction is never delegated to a model.** A deterministic regex pass runs
  on every request, and the LLM only ever receives already-redacted text.
- **Low confidence does not become a funding recommendation.** Anything below
  threshold is held for a human; a request in review carries reduced weight and a
  rejected one carries none.
- **Disagreement escalates.** When both engines run and reach different sectors,
  the request goes to a human even if both were individually confident.

Every AI decision and human override is written to an append-only audit log.

## Adding a country

One JSON file in `agora/packs/`, no code change. It declares the administrative
hierarchy, languages, sectors, hex-map layout, demographic and infrastructure
indices, and the public investment pipeline. India, Brazil and South Africa ship
as worked examples; `build_packs.py` regenerates them reproducibly.

## Where XVoice fits

Voice is the channel that reaches the populations this platform flags as silent —
low literacy, low smartphone penetration, languages no mainstream ASR covers.
The demo uses the browser's `SpeechRecognition` as a stand-in. **XVoice** is the
production intake layer: on-device recognition across a far wider language set.
The contract is identical — audio in, text plus a language tag out — so nothing
downstream changes when it is swapped in.

---

## ⚠ Data provenance

Administrative names and approximate populations are **real**. Every index
(literacy, urbanisation, poverty, smartphone penetration, per-sector
infrastructure) and **every investment record is synthetic demonstration data**
generated by `agora/packs/build_packs.py`. They are calibrated to plausible
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
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | System design, data sources, scaling path |
| [`docs/PRIORITIZATION.md`](docs/PRIORITIZATION.md) | The scoring maths, in full, with worked examples |
| [`docs/DPG_COMPLIANCE.md`](docs/DPG_COMPLIANCE.md) | Digital Public Good standard, indicator by indicator |
| [`docs/PITCH.md`](docs/PITCH.md) | Six-minute demo script |

## Licence

MIT — a Digital Public Good has to be forkable by any government that wants it.
