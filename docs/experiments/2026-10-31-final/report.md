# Experiment results (held-out test set)

Run 2026-10-31T17:15:04+00:00 at commit `41dc8e4507`; knowledge base sha256 `86cbd298d055`; tesseract 5.3.4.

Positive = the allergen is present, may be present by cross-contact, or cannot be determined (the system must warn). Negative = absent and the evidence is complete and verified. A method *warns* unless it says NOT LISTED. A false negative is a missed warning (unsafe).

## Proposed system vs baselines

| Method | Accuracy | Precision | Recall | F1 | False negatives | False positives | Unknown share | Exact outcome | ms/case |
|---|---|---|---|---|---|---|---|---|---|
| proposed | 0.957 | 0.946 | 1.000 | 0.973 | 0 | 3 | 0.304 | 0.913 | 3.771 |
| b1_exact | 0.493 | 0.950 | 0.358 | 0.520 | 34 | 1 | 0.000 | 0.478 | 0.034 |
| b2_synonyms | 0.754 | 1.000 | 0.679 | 0.809 | 17 | 0 | 0.000 | 0.725 | 0.503 |
| b3_knowledge | 0.783 | 1.000 | 0.717 | 0.835 | 15 | 0 | 0.000 | 0.754 | 0.434 |

## Ingredient level (allergen links per ingredient)

| Method | Set | Link recall | False-link rate |
|---|---|---|---|
| proposed | ingredients | 0.983 | 0.045 |
| proposed | ocr | 0.929 | 0.400 |
| b1_exact | ingredients | 0.373 | 0.045 |
| b1_exact | ocr | 0.000 | 0.000 |
| b2_synonyms | ingredients | 0.983 | 0.045 |
| b2_synonyms | ocr | 0.071 | 0.000 |
| b3_knowledge | ingredients | 0.983 | 0.045 |
| b3_knowledge | ocr | 0.286 | 0.000 |

## Ablations (one component switched off)

| Configuration | Accuracy | Recall | False negatives | False positives | Unknown share | Dev safety FN | Dev unsafe absences | Dev reasoning acc. | Dev normalization acc. |
|---|---|---|---|---|---|---|---|---|---|
| full | 0.957 | 1.000 | 0 | 3 | 0.304 | 0 | 0 | 1.000 | 0.977 |
| no_ontology | 0.927 | 0.962 | 2 | 3 | 0.275 | 2 | 10 | 0.832 | 0.977 |
| no_normalization | 0.942 | 1.000 | 0 | 4 | 0.348 | 0 | 1 | 0.933 | 0.349 |
| no_compound | 0.957 | 1.000 | 0 | 3 | 0.304 | 0 | 0 | 0.955 | 0.930 |
| no_source_reliability | 0.913 | 0.943 | 3 | 3 | 0.217 | 2 | 4 | 1.000 | 0.977 |
| no_conflicts | 0.957 | 1.000 | 0 | 3 | 0.304 | 1 | 1 | 1.000 | 0.977 |
| no_uncertainty | 0.855 | 0.811 | 10 | 0 | 0.116 | 6 | 19 | 1.000 | 0.977 |

## Held-out ingredient and OCR sets (proposed)

- heldout_ingredients: 65 cases, link recall 0.983, false-link rate 0.045
  - H-I21 'breadcrumbs': wheat not linked
  - H-I65 'egg plant': eggs wrongly linked
- heldout_ocr: 16 cases, link recall 0.929, false-link rate 0.400
  - H-O13 'egq white': eggs not linked
  - H-O15 'rnaize starch': wheat wrongly linked
  - H-O15 'rnaize starch': gluten wrongly linked

## Performance (engine)

800 runs: mean 3.012 ms, P50 2.858 ms, P95 4.972 ms, max 7.548 ms; 331.8 assessments/s; peak traced memory 0.12 MB; process max RSS 48.7 MB. Engine only (no OCR, no network), single process, warm caches after the first repeat.

## Performance (API under load)

200 requests from 8 concurrent clients: P50 127.2 ms, P95 176.3 ms, max 282.7 ms; 54.6 requests/s; 0 errors; server RSS 117.5 MB. POST /api/analyze/text incl. auth, database writes (stored assessment) and JSON; one uvicorn worker.

## Errors of the proposed system

- false positive (over-warning): H-S02 wheat (truth absent, got UNKNOWN). Tamari is soy-based and normally wheat-free.
- false positive (over-warning): H-S34 milk (truth absent, got UNKNOWN). Coconut milk is not dairy and coconut is not an EU tree nut.
- false positive (over-warning): H-S34 tree_nuts (truth absent, got UNKNOWN). Coconut milk is not dairy and coconut is not an EU tree nut.
