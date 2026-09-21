# The prioritisation model

Everything the dashboard ranks comes from this. It is written out in full because
a ranking that cannot be challenged is not decision support — it is an oracle,
and no ministry should trust one.

This model measures **need**. It holds no budget, no committed-investment figure
and no cost estimate, and it makes no allocation. What a government spends, and
where, is a separate decision it deliberately does not touch.

## Unit of analysis

One **(district, sector)** cell. That is the grain a government acts at: not
"Bihar", not "water", but *water in Sitamarhi*. For India that is 146 districts ×
10 sectors = 1,460 cells.

The grid is built **exhaustively from the country pack, never from the requests**.
A district that has sent nothing still produces ten rows. If the matrix were built
from incoming requests, silence would vanish from the analysis entirely — which is
the single most consequential way a system like this fails.

## The score

```
Need  = w_D·D + w_G·G + w_P·P + w_S·S + w_V·V         (weights renormalised to 1)
Index = 100 · Need
```

| | Factor | Definition | Default |
|---|---|---|---|
| **D** | Demand | citizen request intensity per capita, equity-corrected, rank-normalised | 0.30 |
| **G** | Gap | `(benchmark − infrastructure index) / benchmark`, clamped to [0,1] | 0.25 |
| **P** | People | `log₁₀(1+population) / log₁₀(1+max population)` | 0.15 |
| **S** | Severity | mean urgency weight of the requests received | 0.15 |
| **V** | Vulnerability | `0.50·poverty + 0.30·(1−literacy) + 0.20·(1−smartphone)` | 0.15 |

Urgency weights: critical 1.0, high 0.7, medium 0.4, low 0.15.

### Why rank-normalisation for D

Request counts have a heavy tail — a handful of cells would compress everything
else toward zero under min–max scaling. Ranks are robust to that. Cells with zero
demand are pinned to exactly 0 rather than inheriting a floor from ties.

### Why log-scaling for P

Linear population would make every recommendation a megacity. Log-scaling keeps
scale relevant without letting it dominate.

---

## The equity correction

This is the load-bearing idea, so here is the argument in full.

Raw request volume measures **the ability to complain**. A district where 80% own
smartphones and 85% are literate will out-file a district at 25% and 55% many
times over *at identical real need*. Ranking on raw demand therefore routes public
money toward the already-served — and, worse, dresses that up as evidence.

So:

```
participation = clamp(0.50·smartphone + 0.30·literacy + 0.20·urbanisation, 0.12, 1.0)
density       = (demand_weight / population × 100,000) / participation
D             = rank_normalise(density)
```

Twenty requests from a low-reach district count for more than twenty from a
high-reach one, because they represent a larger submerged population.

**Measured on the seeded corpus** (cells with ≥ 3 requests, top vs bottom quartile
by participation):

| | raw signal | after correction |
|---|---|---|
| High-participation districts | **1.42×** | **0.84×** |

The advantage does not merely shrink — it inverts, which is the correct outcome
if the correction is doing real work.

### What the correction cannot do

Dividing by reach does nothing when the numerator is zero. A district that sends
no requests stays at D = 0 however it is weighted. This is an unavoidable limit of
**any** demand-driven system, and the design answers it structurally rather than
pretending otherwise:

- D is only 30% of the score. G, P and V come from administrative data and need no
  citizen to speak.
- Such cells are flagged **`silent_district`** (`gap ≥ 0.5`, `vulnerability ≥ 0.5`,
  zero requests) so they read as *"we have not heard from here"*, never *"there is
  no need here"*. The correct response is outreach — an IVR campaign, a field
  worker — not deprioritisation.

Verified by test: with **zero** requests loaded, the engine still ranks Araria,
Sitamarhi and Sukma at the top on administrative data alone.

---

## What was removed, and what it cost

The model used to carry a third term: an **investment-coverage discount**. Each
cell estimated the capital required to close its gap, compared that with the
public money already committed there, and multiplied the index by `(1 − λ·C)`
with λ = 0.60. The platform no longer holds investment data at all, so the term
is gone along with the country packs' investment pipeline and currency.

Two things are worth being precise about.

**The ordering barely moved.** Measured on the demo corpus, 48 of the top 50
cells are the same with the discount and without it. Individual cells moved by
up to a thousand ranks, but the head of the list — the part anyone reads — is
essentially unchanged.

**One flag lost half its meaning, and that is the real cost.** `blind_spot`
required `C < 0.10`, so it asserted "loud demand, severe deficit, **and nobody
is paying for it**" — a sharper claim than anything else the platform produced.
Its successor `unmet_need` drops that clause and asserts only the first half. It
did not empty out: the old rule fired on 36 of 1,460 cells and the new one fires
on 38, the two extra being cells the old rule suppressed because money was
committed there. But "nobody is funding this" is no longer something this model
can say, and it cannot be recovered without investment data.

## Flags

| Flag | Condition | Reading |
|---|---|---|
| `unmet_need` | D ≥ 0.60 and G ≥ 0.50 | Loud demand, severe deficit. **Act.** |
| `silent_district` | 0 requests, G ≥ 0.50, V ≥ 0.50 | Severe deficit, no voice heard. **Reach out.** |

## Explainability

Every cell carries `contributions[factor] = 100 · w · factor`, which **sum
exactly to the index** (asserted in the test suite to within 0.05). So any
ranking can be answered precisely: *which term produced this, and what would
change it.* Each cell also carries the citizen requests behind it.

## Recommendation confidence

Distinct from the AI's per-request confidence. This is how much a reader should
trust *this cell's ranking*:

```
requests > 0 :  0.40·min(1, n/12) + 0.30·mean_AI_confidence + 0.30
requests = 0 :  0.45                      # administrative data only
```

Administrative-only conclusions are genuinely usable, but weaker evidence than the
same conclusion corroborated by citizen reports — and the number says so.

## Rollups

- **District** = mean of its three highest sector scores — *how bad are this
  district's worst needs*. A sum would reward breadth, letting ten mild gaps
  outrank one crisis.
- **Region** = population-weighted mean of its districts, so a region is not
  dragged up by one small outlier.

## Known limitations

1. **Demand cannot measure the unheard.** Mitigated structurally (above), not
   solved. Coverage of the silent set is an outreach problem, not a modelling one.
2. **Weights are a political object.** The defaults are a starting point, not a
   finding. They are exposed everywhere precisely so they get argued about.
3. **Infrastructure indices are only as good as their source.** The demo's are
   synthetic. Real deployment inherits the update cadence and biases of the
   national statistical system, and `data_freshness` should be surfaced per cell.
4. **Verification is shallow.** Status is `new`/`review`/`verified`/`rejected`
   with no duplicate detection or coordinated-campaign defence. A national system
   needs both — a single actor should not be able to manufacture demand signal.
5. **The model cannot see what is already being done.** With investment data
   removed it ranks need without any knowledge of which needs are already being
   addressed, so a district with a funded project under way ranks exactly as it
   would with nothing happening at all. That is a deliberate consequence of
   taking money out, not an oversight.
