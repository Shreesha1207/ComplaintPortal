# Digital Public Good — standard alignment

Assessed against the nine indicators of the
[DPG Standard](https://digitalpublicgoods.net/standard/). Honest status, not
aspirational: a prototype that overstates its compliance is not a credible DPG
candidate.

| # | Indicator | Status | Evidence / gap |
|---|---|---|---|
| 1 | **Relevance to SDGs** | ✅ | Directly serves SDG 6 (water), 9 (infrastructure), 10 (reduced inequalities), 11 (cities), 16.7 (responsive, inclusive decision-making). The equity correction targets 10 specifically. |
| 2 | **Open licence** | ✅ | MIT, in `LICENSE`. |
| 3 | **Clear ownership** | ✅ | Repository owner; no third-party IP. Country packs are separately attributable so a nation owns its own data file. |
| 4 | **Platform independence** | ✅ | Python + SQLite + static HTML. No proprietary runtime, no cloud service, no CDN. LLM enhancement is optional and the platform is fully functional without it. |
| 5 | **Documentation** | ✅ | `README`, `ARCHITECTURE`, `PRIORITIZATION` (full model maths), this file, `PITCH`. Every non-obvious design decision is documented with its reasoning. OpenAPI at `/api/docs`. |
| 6 | **Non-PII data extraction** | ✅ | Deterministic PII redaction at intake; CSV/JSON export carries aggregated, non-personal data. `text_original` is retained for accountability but never exported. |
| 7 | **Privacy & applicable laws** | ⚠️ **Partial** | Redaction, data minimisation, audit logging and a no-protected-attributes rule are implemented. **Missing before deployment:** authentication/RBAC, encryption at rest, retention policy, DPIA, and per-jurisdiction review (India DPDP Act, Brazil LGPD, South Africa POPIA, Russia 152-FZ, China PIPL). |
| 8 | **Standards & best practices** | ✅ | ISO-639-1 languages, ISO-3166 countries, BCP-47 voice tags, OpenAPI 3, REST/JSON, semantic HTML with ARIA, WCAG-oriented colour validated with a runnable contrast/CVD checker. |
| 9 | **Do No Harm by design** | ✅ **by construction** | See below. |

## Indicator 9 in detail

The obvious harm from a platform like this is **automating the misallocation of
public money toward those already best served**, with the authority of data. That
is not a hypothetical: it is the default behaviour of any system that ranks
citizen feedback by volume.

Countermeasures, all implemented and tested:

| Risk | Countermeasure | Verified by |
|---|---|---|
| Participation bias — the connected dominate | Demand divided by expected-participation index | `test_equity_correction_inverts_participation_bias`; measured 1.42× → 0.84× |
| Silent populations disappear | Demand is only 30%; exhaustive matrix; `silent_district` flag | `test_silent_districts_still_surface_without_any_citizen_signal` |
| Announced-but-undelivered projects mask need | λ = 0.6, never 1.0; completed projects excluded from coverage | `test_investment_coverage_discounts_priority_but_never_to_zero_by_default` |
| Opaque ranking that cannot be challenged | Per-factor contributions sum exactly to the index; counterfactual; rationale | `test_contributions_sum_exactly_to_the_priority_index` |
| Hidden politics presented as arithmetic | Weights and λ are API parameters, returned in every response, live-adjustable | `test_weights_actually_move_the_ranking` |
| AI error becomes a funding decision | Confidence threshold → human review; ensemble disagreement escalates; rejected requests carry zero weight | `test_unclassifiable_text_is_routed_to_review` |
| Personal data exposure | Deterministic redaction before storage and before any model call | `test_pii_is_always_redacted` |
| Profiling by protected attribute | Never inferred; explicitly forbidden in the LLM prompt | Prompt in `agora/ai/llm.py` |
| Vendor/model lock-in | Adapter interface; offline engine is the default and always sufficient | `test_llm_engine_degrades_to_heuristic_without_credentials` |

## Reusability across BRICS

A country is one JSON file. India, Brazil and South Africa ship as worked examples
spanning three administrative vocabularies (State/UT · District, State ·
Municipality, Province · District Municipality), three currencies and 21
languages, with no country-specific branches in the code.

What a new country supplies: administrative hierarchy and populations, language
list with BCP-47 tags, hex-map coordinates, demographic and infrastructure
indices, investment pipeline, sector benchmarks, and per-language lexicon entries.
Lexicons are data maintained by a national language team, which is what makes the
platform genuinely forkable rather than nominally open-source.

## Honest gaps

1. **Not production-secure.** No auth, no RBAC, no rate limiting, no encryption at
   rest. This is a prototype and says so.
2. **Legal review not done.** Five BRICS jurisdictions, five data-protection
   regimes. Indicator 7 is genuinely partial.
3. **No independent accuracy evaluation.** Classifier quality is demonstrated, not
   measured against a human-labelled gold set. That evaluation — per language,
   with per-language accuracy published — should precede any real deployment,
   because a classifier that is 90% accurate in Hindi and 60% in Santali would
   quietly recreate the exact inequity the platform exists to correct.
4. **No community governance yet.** A real DPG needs a contribution process, a
   country-pack review procedure, and a body that owns the weights debate.
