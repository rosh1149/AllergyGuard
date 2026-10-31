# Research results (Stage 4)

**Final run.**

- **When:** 2026-10-31, commit `41dc8e4`, knowledge base `86cbd298d055`.
- **Where:** [experiments/2026-10-31-final/](experiments/2026-10-31-final/) holds `report.md`,
  `results.json` and `manifest.json`. The manifest records dataset hashes, settings and tool versions.
- **Data:** the held-out test set (121 cases, 169 checks), frozen before the Stage 1 changes, was
  evaluated once. The engine was not changed after seeing the results.
- **Re-running:** `python -m app.evaluation.experiments --final OUT_DIR` repeats it.

## 1. Proposed system vs baselines (held-out scenarios, 69 allergen checks)

A *positive* means the allergen is present, may be present by cross-contact, or cannot be determined
from the evidence: the system must warn. A *negative* means the allergen is absent and the evidence is
complete and verified. A method warns unless it answers NOT LISTED. A false negative is a missed warning
(unsafe).

| Method | Recall (95 % CI) | Specificity (95 % CI) | F1 | False negatives | False positives | Unknown share |
|---|---|---|---|---|---|---|
| **AllergyGuard** | **53/53 = 1.000 (0.932–1.000)** | 13/16 = 0.81 (0.57–0.93) | **0.973** | **0** | 3 | 30 % |
| B3 knowledge-based mapping | 38/53 = 0.717 (0.58–0.82) | 16/16 (0.81–1.00) | 0.835 | 15 | 0 | 0 |
| B2 synonym matching | 36/53 = 0.679 (0.55–0.79) | 16/16 (0.81–1.00) | 0.809 | 17 | 0 | 0 |
| B1 exact name matching | 19/53 = 0.358 (0.24–0.49) | 15/16 (0.72–0.99) | 0.520 | 34 | 1 | 0 |

**Every baseline missed warnings that AllergyGuard gave.** The reverse never happened. Exact McNemar
tests on the 53 positives:

| Comparison | Discordant checks | p |
|---|---|---|
| AllergyGuard vs B1 | 34 : 0 | 1.2 × 10⁻¹⁰ |
| AllergyGuard vs B2 | 17 : 0 | 1.5 × 10⁻⁵ |
| AllergyGuard vs B3 | 15 : 0 | 6.1 × 10⁻⁵ |

**What it costs.** AllergyGuard answered *Unknown* for 21 of 69 checks (30 %) and over-warned on 3 of
16 negatives. All three over-warnings were caused by an ingredient the knowledge base does not know
(§4). Its exact outcome (present / cross-contact / absent / undeterminable) matched the truth in 63 of
69 checks (91.3 %). The 6 mismatches are listed in §4. All of them still warned.

**Ingredient level** (does an ingredient carry the right allergens?):

| Method | Held-out ingredients: link recall / false-link rate | Held-out OCR text: link recall / false-link rate |
|---|---|---|
| AllergyGuard | 0.983 / 0.045 | 0.929 / 0.400 |
| B3 knowledge | 0.983 / 0.045 | 0.286 / 0.000 |
| B2 synonyms | 0.983 / 0.045 | 0.071 / 0.000 |
| B1 exact | 0.373 / 0.045 | 0.000 / 0.000 |

On clean ingredient names, the curated synonym list (B2) already matches the knowledge layer. The gain
of the full system there comes from evidence handling, not from recognising more names. On misread
photo text, AllergyGuard's OCR correction recovers most links (0.93 vs 0.29). The cost is two
cautious false links on one misread item (§4).

## 2. Ablations (one component switched off; held-out scenarios)

| Switched off | False negatives (unsafe) | False positives | Unknown share | What it shows |
|---|---|---|---|---|
| nothing (full system) | 0 | 3 | 30 % | |
| **uncertainty handling** | **10** | 0 | 12 % | the most important safety component: unresolved, incomplete, stale or possible evidence must block NOT LISTED |
| source reliability | 3 | 3 | 22 % | unverified and possibly-mismatched sources must not rule allergens out |
| ontology reasoning | 2 | 3 | 28 % | relationships matter where the curated term list has no entry (unstated sources, general terms, homonyms) |
| synonym normalization | 0 | 4 | 35 % | no unsafe effect here, but on the development set normalization accuracy falls from 0.977 to 0.349 and answers become less specific |
| conflict detection | 0 | 3 | 30 % | no effect on the held-out set (only 4 multi-source scenarios); 1 unsafe absence on the development set |
| compound parsing | 0 | 3 | 30 % | no effect on outcomes here: concept matching finds bracketed components anyway. Parsing matters for source qualifiers and incomplete lists (development set) |

**Limitations of these numbers.**

- The held-out set is small (69 scenario checks). Components with few relevant cases (conflicts,
  compound parsing) cannot show an effect, and the intervals in §1 are wide.
- Both datasets were written by the developer, so the labels have not been independently reviewed (see
  [DATASETS.md](DATASETS.md) and the expert review sheet).
- The scenarios contain no real photos. OCR on real labels is still to be measured
  ([INPUT_PATHWAYS.md](INPUT_PATHWAYS.md)).

## 3. Components (development set) and performance

- **Components (development set).** Per-layer results on the development benchmark are in
  [evaluation-report.md](evaluation-report.md). All layers are at or above their release criteria.
  Because these sets were used to make changes, they are optimistic.
- **Engine speed:** mean 3.0 ms, P50 2.9 ms, P95 5.0 ms per assessment; 332 assessments/s in one
  process; peak process memory 49 MB.
- **API under load:** 8 concurrent clients sent 200 `POST /api/analyze/text` requests (including
  authentication and storing the assessment). P50 127 ms, P95 176 ms, max 283 ms; 54.6 requests/s; no
  errors. Server memory was 118 MB with a single worker.
- **OCR:** on generated labels, see [ocr-evaluation-generated.md](ocr-evaluation-generated.md). Real
  photos are needed.

## 4. Error analysis

No error was unsafe. Each one is either the system being cautious, or a gap in what the knowledge base
covers.

| Case | What happened | Cause | Category |
|---|---|---|---|
| H-S02 rice noodles, tamari… (wheat) | UNKNOWN, truth absent | "rice noodles" not in the knowledge base, so unresolved text blocks absence | coverage gap → over-caution |
| H-S34 coconut milk, rice, lemongrass (milk, tree nuts) | UNKNOWN, truth absent | "lemongrass" not in the knowledge base | coverage gap → over-caution |
| H-I65 "egg plant" | eggs linked | "egg plant" written as two words: "egg" matched, "plant" ignored | look-alike gap → over-caution |
| H-O15 "rnaize starch" | possible wheat/gluten | corrected to "maize starch", but there is no "maize starch" alias, so the generic "starch" (source unstated) was matched | alias gap → over-caution |
| H-I21 "breadcrumbs" | gluten linked, wheat **not** linked | breadcrumbs are only in the term list, as gluten | **coverage gap with a latent risk** |
| H-O13 "egq white" | not corrected | q/g is not in the OCR confusion table; the text stays unresolved, so eggs would be UNKNOWN | OCR coverage gap (safe) |
| H-S30 label vs a database list for a different product (milk) | PRESENT, label "undeterminable" | the database (identity 0.9) lists butter; the unresolved list conflict keeps every allergen either list shows as PRESENT | **design question**: should an allergen shown only by one side of an *unresolved* conflict be "possible" rather than "present"? Safe either way |
| H-S32 database entry with identity 0.45 (milk, gluten) | UNKNOWN, label "present" | low identification confidence makes the entry a candidate ("may belong to a different item") | **label question**: if the match is wrong the food may not contain them; UNKNOWN may be the better label. For the expert review |

**The latent risk.** "Breadcrumbs" did not cause a wrong answer in any held-out scenario: the one
containing breadcrumbs listed its wheat flour. But a label reading only "breadcrumbs" could give a
wheat-only allergy NOT LISTED for wheat. This is the most important follow-up.

**Follow-up fixes** (to apply in Stage 5, and then to re-test on *new* cases, not this held-out set):

- link breadcrumbs (and bread, pasta, noodles, couscous-type cereal products) to wheat
- add lemongrass, noodle varieties, "maize starch" and "egg plant" as concepts, aliases or exclusions
- add q→g to the OCR confusion table

**What the 30 % Unknown share means.** Unknown is the honest answer whenever the evidence cannot settle
the question. 17 of the 69 held-out checks (25 %) are labelled *undeterminable*. The system's 21 Unknown
answers break down as:

| Unknown answers | Truth label | Note |
|---|---|---|
| 16 | undeterminable | correct |
| 3 | absent | over-caution from coverage gaps (H-S02, H-S34) |
| 2 | present | the uncertain-identification case H-S32, whose label is itself questionable |

The seventeenth undeterminable check (H-S30) was answered PRESENT.
