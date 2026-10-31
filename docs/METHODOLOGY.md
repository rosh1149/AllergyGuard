# Methodology

How AllergyGuard was built and evaluated. Results are in [RESEARCH_RESULTS.md](RESEARCH_RESULTS.md),
limitations in [LIMITATIONS.md](LIMITATIONS.md).

## 1. Research question

Can a deterministic, evidence-based engine report allergen information for packaged and restaurant food
without ever missing a warning, while saying *why* and saying *Unknown* when the evidence cannot settle
the question?

The safety requirement is asymmetric. A missed warning (the system says NOT LISTED while the allergen is
present, possible, or undeterminable) is the failure that matters. Over-warning and Unknown answers are
costs to be reported, not failures.

## 2. Development approach

| Phase | What was built | How it was checked |
|---|---|---|
| Parts 1–5 | API, auth and roles, engine, packaged and restaurant pipelines, frontend | unit and API tests, browser walkthrough |
| Steps A–G | knowledge layer, normalization, evidence fusion, adapters and feedback, observability, benchmark, extended screens | per-step tests, release gate in CI |
| Stage 1 | ontology (concept types, protein and regulatory relations, homonyms, graph rules), incomplete lists | graph validation, regression cases |
| Stage 2 | policy table, conflict kinds, provenance, per-allergen cross-contact, decision matrix | rule tests, fusion dataset v2 |
| Stage 3 | recipe versions with effective dates, manufacturer pages and web search, deskew, OCR evaluation | pathway tests, generated OCR set |
| Stage 4 | datasets, baselines, ablations, held-out evaluation, error analysis | one frozen held-out run |
| Stage 5 | fixes from the error analysis, migrations, security validation, performance, retries, traces | 433 tests, benchmark |

Every change went through the automated tests and the release gate before being committed.

## 3. Datasets

Details in [DATASETS.md](DATASETS.md).

- **Development benchmark** (9 datasets: normalization, OCR correction, synonyms, ontology, reasoning,
  compound, fusion, uncertainty, safety). It was used while building, so its results are optimistic.
  It grows over time; the release gate refuses a dataset that shrinks.
- **Held-out set** (121 cases, 169 checks: ingredients, OCR text, full scenarios). Written before the
  Stage 1 changes, frozen with SHA-256 hashes in `MANIFEST.json`, and run once with
  `--final` (2026-10-31). A test fails if any held-out file changes. Fixes found in its error analysis
  were tested on new development cases, never on the held-out set again.
- **Labels.** A *positive* is any check where the system must warn: present, possible cross-contact or
  undeterminable. A *negative* is an absent allergen backed by complete, verified evidence. All labels
  were written by the developer; `docs/expert-review-sheet.csv` (255 rows) is prepared for independent
  review.

## 4. Compared methods

| Method | Description |
|---|---|
| B1 exact | an allergen is found only if an ingredient name equals a known allergen term |
| B2 synonyms | B1 plus the curated synonym list |
| B3 knowledge | B2 plus concept relations (derived from, is a, contains) |
| AllergyGuard | full system: normalization, ontology, source reliability, conflicts, uncertainty handling |
| Ablations | AllergyGuard with one component switched off (`app/engine/ablation.py`) |

Baselines answer only PRESENT or NOT LISTED, as simple lookup tools do.

## 5. Metrics

- **Recall** on positives (warnings given / warnings required) with Wilson 95 % intervals; the number of
  **false negatives** is the primary safety measure.
- **Specificity** on negatives, **false positives**, **Unknown share** (cost of caution).
- **Exact outcome accuracy** across the four outcomes.
- Ingredient level: **link recall** and **false-link rate**.
- **Exact McNemar tests** on discordant positives between AllergyGuard and each baseline.
- OCR: **character and word error rates** (CER, WER) and allergen recall on generated label images.
- Performance: engine latency (mean, P50, P95), throughput, memory; API latency under 8 concurrent
  clients; photo-job latency and throughput at 1–8 concurrent jobs.

## 6. Reproducibility

- `python -m app.evaluation` runs the development benchmark and the release gate (also in CI).
- `python -m app.evaluation.experiments --final OUT` reruns the held-out experiment and writes
  `report.md`, `results.json` and a manifest (git commit, knowledge-base hash, dataset hashes, settings,
  tool versions).
- `backend/scripts/photo_bench.py` repeats the photo-processing measurement.
- The engine is deterministic: the same evidence gives the same result (tested).

## 7. Threats to validity

- Small held-out set (69 scenario checks): wide intervals; components with few relevant cases cannot show
  an effect.
- Single annotator; expert review pending.
- No real label photos; OCR results come from generated images.
- Knowledge coverage built from general sources, not checked line by line against the FSSAI regulation.
- Performance measured on one 4-core development machine.
