# Evaluation benchmark and release criteria

The benchmark measures each layer of the engine separately. Normalization identifies what an
ingredient is, semantic reasoning identifies what it is related to, and evidence fusion decides how
far the available sources support a finding. It also measures the whole system against a curated
allergen dataset.

- **Code:** `backend/app/evaluation/`
- **Datasets:** `backend/app/evaluation/datasets/*.json`
- **Latest report:** [evaluation-report.md](evaluation-report.md)

```bash
cd backend
python -m app.evaluation                       # seed knowledge base; exit code 1 if the gate fails
python -m app.evaluation --kb database         # the live knowledge base, including approved changes
python -m app.evaluation --report report.md --json report.json
python -m app.evaluation --update-baseline     # accept current metrics as the new baseline
```

## Datasets and metrics

| Area | Dataset (cases) | Metrics |
|---|---|---|
| Ingredient normalization | `normalization` (43): written text to normalized text, including OCR photo text | exact-match accuracy; precision and recall of changes made |
| OCR correction | `ocr_correction` (25): misreads, plus real words and unknown words that must be left alone | correction accuracy; false-correction rate |
| Synonym resolution | `synonyms` (49): regional and alternative names (maida, besan, arachis oil, e322...) | mapping precision and recall |
| Ontology mapping | `ontology` (52): phrases with modifiers and forms ("organic whole milk powder", "toasted sesame oil") | concept-mapping precision and recall; FoodOn id coverage (reported, not gated) |
| Semantic reasoning | `reasoning` (79): ingredient to definite and possible allergens, including look-alikes (cocoa, coconut milk, nutmeg, cream of tartar) | relationship accuracy; missed-allergen count |
| Compound ingredients | `compound` (10): nested brackets, class names, headers, source qualifiers | extraction accuracy; item precision and recall |
| Evidence fusion | `fusion` (14): agreeing and conflicting multi-source scenarios | agreement accuracy; conflict-resolution accuracy (conflict detected, resolution recorded, cautious outcome kept); unsafe-absence count |
| Provenance | every fusion, uncertainty and safety finding (120) | traceability coverage (a warning names its source, reference, ingredient, rule and relationship path; NOT LISTED names the verified sources used) |
| Uncertainty | `uncertainty` (14): incomplete, unreadable, unidentified, stale, unverified, ambiguous-identity, general terms | handling accuracy; unsafe-absence count |
| Safety | `safety` (65 scenarios, 86 allergen checks): direct, derived, compound, cross-contact, OCR, conflicting sources, identification, negatives and regressions | **allergen false-negative rate**; false-positive rate; outcome accuracy, overall and per tag |

A **false negative** is any allergen check where the system answers NOT LISTED IN VERIFIED RECORDS but
the expected answer is anything else (present, possible cross-contact or unknown).

## Release criteria

`release_criteria.json` defines two groups of criteria. All of them block a release.

- **Critical (safety):**
  - zero false negatives
  - zero unsafe absences in the fusion and uncertainty scenarios
  - zero lost allergen links
  - 100% uncertainty handling
  - 100% traceability
- **Quality:** minimum accuracy, precision and recall per layer, plus a maximum false-correction
  rate and a maximum false-positive rate.

On top of the criteria, every gated metric must not fall more than 0.005 below `baseline.json`, and
no dataset may lose cases. The gate runs in three places:

1. **CI** (`.github/workflows/ci.yml`): the `safety-gate` job runs on every push and pull request. The
   `release` job (the place for a deployment step) needs it to pass. Make `safety-gate` a required
   check on `main`.
2. **Knowledge changes:** before an approved proposal is published, the critical criteria run
   against the knowledge base as changed. A change that loses an allergen link or causes an unsafe
   absence is rolled back with HTTP 409 and the failing cases.
3. **Tests:** `tests/test_evaluation.py` checks the following:
   - the gate passes on the seed knowledge base
   - the gate fails when allergen links are removed
   - it detects regressions against the baseline and shrinking datasets
   - the knowledge-approval hook blocks a harmful change

Changing an expected result in a dataset changes what "correct" means. It needs the same review as a
code change, and the reason belongs in the case's `note`.

## Findings from the first benchmark run

The benchmark was written from food knowledge, not from the engine's output. Its first run found
these problems, which were then fixed:

| Finding | Effect before the fix | Fix |
|---|---|---|
| A verified record containing "spices", "seasoning" or "natural flavouring" allowed NOT LISTED for mustard and celery (spices), and for milk (seasoning, flavouring) | **4 unsafe absences** in the uncertainty set | General terms are now linked to the allergens they commonly hide, as *possible*. A named source ("spices (cumin, coriander)") replaces these. |
| "beef" was not in the knowledge base | Labels containing beef could never be NOT LISTED (safe, but wrong) | Added beef, pork, turkey and duck |
| "cream of tartar" was read as "cream" plus unknown words | The item was unresolved | Added the concept (potassium bitartrate). Its milk exclusion was already in place. |
| "egg albumen", "durum wheat semolina", "barley malt extract" mapped to a broader or neighbouring concept | Allergens were still found, but the concept was imprecise | Added aliases |
| OCR misreads next to a recognised word ("whey powcler") were not corrected | The allergen was found; the text was not normalized | OCR correction is now also tried there, kept only if no allergen is lost |

The first draft also contained wrong expectations, which were corrected:
- **Ground almonds** are almond flour.
- **"Flour"** alone conventionally means a gluten cereal flour, so gluten is definite and wheat is
  possible.
- **Gianduja** need not contain milk.
- **Free-from claims** are unverifiable by design (rule R4).
- **OCR:** "0" is read as "o", so "pean0ts" cannot become "peanuts".
- **Conflict types:** two fusion scenarios had been described with the wrong type of conflict.

**Known limitation kept in the benchmark:** "pean0ts" is not corrected (0 for u is not a usual OCR
confusion). The item stays unresolved, so peanuts are reported as UNKNOWN, never as NOT LISTED.
