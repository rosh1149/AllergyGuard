# AllergyGuard evaluation report

Generated 2026-10-31 14:15 UTC against the seed knowledge base.

**Release gate: PASSED** (52/52 checks passed)

## Metrics

| Area | Cases | Metric | Value | Release criterion |
|---|---|---|---|---|
| Ingredient normalization | 43 | exact_match_accuracy | 0.9767 | >= 0.95 (quality) |
|  |  | precision | 1.0 | >= 0.95 (quality) |
|  |  | recall | 0.9667 | >= 0.9 (quality) |
| OCR correction | 25 | correction_accuracy | 0.8889 | >= 0.85 (quality) |
|  |  | false_correction_rate | 0.0 | <= 0.02 (quality) |
| Synonym resolution | 49 | precision | 1.0 | >= 0.98 (quality) |
|  |  | recall | 1.0 | >= 0.95 (quality) |
| Ontology (concept) mapping | 52 | precision | 1.0 | >= 0.97 (quality) |
|  |  | recall | 1.0 | >= 0.95 (quality) |
|  |  | foodon_id_coverage | 0.4808 |  |
| Semantic reasoning | 89 | relationship_accuracy | 1.0 | >= 0.97 (quality) |
|  |  | missed_allergen_count | 0 | <= 0 (critical) |
| Compound ingredients | 12 | extraction_accuracy | 1.0 | >= 0.9 (quality) |
|  |  | item_precision | 1.0 |  |
|  |  | item_recall | 1.0 |  |
| Evidence fusion | 14 | agreement_accuracy | 1.0 | >= 0.95 (quality) |
|  |  | conflict_resolution_accuracy | 1.0 | >= 0.95 (quality) |
|  |  | unsafe_absence_count | 0 | <= 0 (critical) |
| Provenance | 128 | traceability_coverage | 1.0 | >= 1.0 (critical) |
| Uncertainty | 20 | handling_accuracy | 1.0 | >= 1.0 (critical) |
|  |  | unsafe_absence_count | 0 | <= 0 (critical) |
| Safety (end-to-end allergen dataset) | 65 | false_negative_rate | 0.0 |  |
|  |  | false_negative_count | 0 | <= 0 (critical) |
|  |  | false_positive_rate | 0.0 | <= 0.05 (quality) |
|  |  | outcome_accuracy | 1.0 | >= 0.95 (quality) |
|  |  | positive_checks | 65 |  |
|  |  | negative_checks | 21 |  |
|  |  | accuracy_compound | 1.0 |  |
|  |  | accuracy_conflict | 1.0 |  |
|  |  | accuracy_cross_contact | 1.0 |  |
|  |  | accuracy_derived | 1.0 |  |
|  |  | accuracy_direct | 1.0 |  |
|  |  | accuracy_fuzzy | 1.0 |  |
|  |  | accuracy_identification | 1.0 |  |
|  |  | accuracy_negative | 1.0 |  |
|  |  | accuracy_ocr | 1.0 |  |
|  |  | accuracy_regression | 1.0 |  |
|  |  | accuracy_restaurant | 1.0 |  |
|  |  | accuracy_uncertain | 1.0 |  |

## Cases not matching the expected result

**Ingredient normalization**
- 'pean0ts': expected 'peanuts', got 'pean0ts'

**OCR correction**
- 'pean0ts': expected correction to 'peanuts', got 'pean0ts'

