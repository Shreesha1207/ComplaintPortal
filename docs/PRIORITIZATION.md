# The prioritisation model

Everything the dashboard ranks comes from this. It is written out in full because
a recommendation that cannot be challenged is not decision support — it is an
oracle, and no ministry should fund one.

## Unit of analysis

One **(district, sector)** cell. That is what a government actually funds: not
"Bihar", not "water", but *water in Sitamarhi*. For India that is 146 districts ×
10 sectors = 1,460 cells.

The grid is built **exhaustively from the country pack, never from the requests**.
A district that has sent nothing still produces ten rows. If the matrix were built
from incoming requests, silence would vanish from the analysis entirely — which is
the single most consequential way a system like this fails.

## The score

```
Need  = w_D·D + w_G·G + w_P·P + w_S·S + w_V·V         (weights renormalised to 1)
Index = 100 · Need · (1 − λ·C)
```

| | Factor | Definition | Default |
|---|---|---|---|
| **D** | Demand | citizen request intensity per capita, equity-corrected, rank-normalised | 0.30 |
| **G** | Gap | `(benchmark − infrastructure index) / benchmark`, clamped to [0,1] | 0.25 |
| **P** | People | `log₁₀(1+population) / log₁₀(1+max population)` | 0.15 |
| **S** | Severity | mean urgency weight of the requests received | 0.15 |
| **V** | Vulnerability | `0.50·poverty + 0.30·(1−literacy) + 0.20·(1−smartphone)` | 0.15 |
| **C** | Coverage | committed investment ÷ investment required to close G, clamped to [0,1] | — |
| **λ** | Discount | how far committed money suppresses priority | 0.60 |

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

## The investment discount

```
required = population × capex_per_capita × sector_cost_weight × G
C        = clamp(committed / required, 0, 1)      # planned + ongoing only
Index    = 100 · Need · (1 − λ·C)
```

Completed projects are excluded: they are already reflected in the infrastructure
index, so counting them again would discount the same spend twice.

**Why λ = 0.6 and not 1.0.** Committed money is not delivered infrastructure.
Budget lines slip, get reallocated, and under-execute. At λ = 1 a district drops
out of the queue the moment a project is *announced* — precisely the failure mode
this platform exists to catch. At λ = 0.6, full funding removes 60% of priority
and leaves 40% as residual risk.

Worked example (Bhopal / jobs, coverage 1.00, need 0.609):

| λ | Index |
|---|---|
| 0.0 | 60.91 |
| 0.6 | 24.36 |
| 1.0 | 0.00 |

## Flags

| Flag | Condition | Reading |
|---|---|---|
| `blind_spot` | D ≥ 0.60 and G ≥ 0.50 and C < 0.10 | Loud demand, severe deficit, no money. **Act.** |
| `silent_district` | 0 requests, G ≥ 0.50, V ≥ 0.50 | Severe deficit, no voice heard. **Reach out.** |
| `well_covered` | C ≥ 0.80 | Already funded — monitor delivery, don't re-fund. |

## Explainability

Every cell carries `contributions[factor] = 100 · w · factor · (1 − λ·C)`, which
**sum exactly to the index** (asserted in the test suite to within 0.05). So any
recommendation can be answered precisely: *which term produced this, and what
would change it.* Each cell also carries a counterfactual — the score if committed
money were fully disbursed — and the citizen requests behind it.

## Recommendation confidence

Distinct from the AI's per-request confidence. This is how much a policymaker
should trust *this cell's ranking*:

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

## Budget allocation

Greedy descent of the ranking, with partial funding of the last item. Greedy is
correct here rather than a placeholder: *the ranking is the policy*, so spending
straight down it is exactly what a policymaker is asking to simulate.

Three strategies, presented together because they genuinely disagree:

| Strategy | ₹50,000 cr reaches | Cost/person |
|---|---|---|
| Worst-first | 45 districts, 92.8M people | ₹5,390 |
| Value for money | 109 districts, 221.5M people | ₹2,257 |
| Blind spots only | 20 districts, 42.2M people | ₹5,860 |

Neither of the first two is *correct*. Value-for-money reaches 2.4× the people and
systematically skips the hardest cases. Quantifying that trade-off and handing it
to a human is more honest than hard-coding one and calling the output "optimal".

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
5. **Cost model is coarse.** `capex_per_capita × cost_weight × gap` is a planning
   approximation, not an engineering estimate.
