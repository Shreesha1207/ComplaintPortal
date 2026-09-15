# Demo script — six minutes

Setup: `./run.sh`, two browser tabs (`/citizen`, `/dashboard`). Works fully
offline. Say the word "complaint" once, to reject it.

---

## 0:00 — The reframe (45s)

> "Every government already has complaint portals. They don't have a spending
> problem for lack of feedback — they have it because feedback can't tell them
> **where to build**.
>
> The difference we built for is this: a complaint portal ranks by volume.
> Volume measures **who is able to complain**. If you fund the loudest districts,
> you fund the ones that already have smartphones, literacy and roads — and you
> call it evidence-based. That's not a neutral system. It launders inequality."

## 0:45 — Intake, in the citizen's language (75s)

On `/citizen`:

1. **Voice tab** — speak, or use a preloaded example. Point at the language
   detection and the extracted sector and urgency.
2. Show the **PII chip**: "The phone number is already gone. Redaction is
   deterministic regex, before storage, before anything reaches a model. We do
   not trust a probabilistic system as the *only* line of defence on personal data."
3. **SMS / IVR tab** — send one.

> "This is the channel that matters. No smartphone, no data plan, and as IVR, no
> literacy requirement either. It's how you reach the people the dashboard is
> about to flag as silent."

Mention XVoice once: *the browser's recogniser is the stand-in; XVoice is the
production layer — on-device, far wider language coverage. Same contract, so
nothing downstream changes.*

## 2:00 — The map (45s)

Switch to `/dashboard`.

> "Equal-area tiles, not a geographic map — on a real map, empty districts
> dominate by land area and dense ones vanish, which inverts a demand signal."

Point at Bihar and Odisha (darkest), Kerala and Chandigarh (lightest).

> "We didn't hand-tune that. It falls out of the model — and it's the right answer."

## 2:45 — The money slide (90s)

Click the top recommendation.

> "Priority 78 for roads in Sitamarhi. Here's the entire derivation."

Point at the stacked bar.

> "Five factors, weighted, summing **exactly** to 78. Citizen demand contributed
> 22 points, infrastructure deficit 18. Two citizen requests in Hindi, one
> critical — they're listed below, in the citizen's own words.
>
> And the line that makes this decision intelligence: **no public investment is
> committed here.** Where money *is* committed, we discount priority — but only by
> 60%, never 100%, because a budget line is not delivered infrastructure. If λ
> were 1, announcing a project would remove a district from the queue. That's the
> failure we're built to catch."

Then the two flags:

> "**36 blind spots** — loud demand, severe deficit, no money.
> **47 silent districts** — severe deficit, and we have heard *nothing*. Those
> aren't low priority. Those are an outreach gap. The system says 'go find out',
> not 'nobody complained'."

## 4:15 — The equity correction (60s)

> "Demand is divided by an expected-participation index built from smartphone
> access, literacy and urbanisation. Twenty requests from an excluded district
> outweigh twenty from a connected one.
>
> Measured on this data: raw, high-participation districts carry **1.42×** the
> demand signal. After correction, **0.84×**. It doesn't just shrink the bias —
> it inverts it.
>
> And it can't fix zero. So demand is only 30% of the score. Deficit, reach and
> vulnerability come from administrative data and need nobody to speak at all.
> Turn citizen data off entirely and the engine still surfaces Araria, Sitamarhi
> and Sukma — three of India's most deprived districts. That's a test in the suite."

## 5:15 — Weights and budget (45s)

Drag a weight slider; the ranking recomputes live.

> "Weights are API parameters. They ship with every response. A ranking that hides
> its weights is claiming its politics are arithmetic."

Drag the budget envelope.

> "₹50,000 crore. Worst-first reaches 93 million people at ₹5,390 each.
> Value-for-money reaches 222 million at ₹2,257 — and skips the hardest cases.
> We show both. That trade-off is a political judgement and the model shouldn't
> quietly settle it."

## 6:00 — Close (20s)

> "Three countries, 21 languages, no country-specific code — adding a nation is
> one JSON file. Open API, MIT licence, runs offline on a laptop.
>
> Complaint portals tell you what people said. This tells you where to build, why,
> and who you haven't heard from."

---

## Questions you will get

**"Is the data real?"**
Administrative names and populations, yes. Every index and investment record is
synthetic and labelled as such in the UI and the API. The schema is the contract —
production adapters for census, NDAP, IBGE, Stats SA and budget portals are
specified in `ARCHITECTURE.md`. We chose to be obvious about it rather than let
you assume.

**"What if people game it?"**
Today: confidence gating, human review, and status weighting. Not yet built:
duplicate and coordinated-campaign detection. That's the top gap and it's listed
in the limitations — a single actor should not be able to manufacture demand
signal. Note the equity correction cuts both ways: brigading from a
high-participation district is *divided down*.

**"Why not just use an LLM for everything?"**
Determinism — a funding decision must reproduce at appeal. Availability — rural
intake on a bad uplink. Cost — tens of millions of requests a year through a
frontier model is not a defensible budget line. So the lexicon is the floor and
Claude is the enhancement; and when both run and disagree, that disagreement
escalates to a human.

**"How is this different from a grievance redressal system?"**
A grievance system closes tickets. This one never closes a ticket — it aggregates
into *where should the next project go*, fuses with infrastructure and budget data,
and tells you what is **not** being funded.

**"What's genuinely novel?"**
Three things: the participation correction (measured, inverting), treating silence
as a flagged signal rather than an absence, and the investment-coverage discount
that turns a demand ranking into an unmet-need ranking.

**"Could another country actually use it?"**
Brazil and South Africa are in the repo now, with different administrative
vocabularies and currencies, and no branches in the code.
