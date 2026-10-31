# Evaluation datasets

Two separate sets serve two purposes:

| Set | Location | Purpose | May the engine be changed after seeing results? |
|---|---|---|---|
| Development | `backend/app/evaluation/datasets/` | find faults, guide changes, regression gate in CI | yes; each change to an expectation is recorded in the file's `changes` |
| Held-out test | `backend/app/evaluation/heldout/` | final, one-off measurement (Stage 4) | **no**: frozen by SHA-256 manifest on 2026-10-31, before the Stage 1 changes |

There is no separate validation set. Thresholds were set from the development set only. That is a
limitation: the development results are optimistic, and only the held-out results estimate performance
on unseen cases. Both sets were written by the project developer from general food and allergen
knowledge, and the held-out set was written before the changes it tests. This reduces, but does not
remove, the bias of the same person writing the system and the tests.

## Case schema

| Dataset | One case | Label |
|---|---|---|
| normalization, ocr_correction | `input`, optional `ocr` | `expected` normalized text (or `null` = must not be corrected) |
| synonyms, ontology | `input` | `concept` (canonical concept id or `null`) |
| reasoning | `input` | `definite` and `possible` allergens |
| compound | `input` | `items`: (role, text, parent) |
| fusion, uncertainty, safety | `id`, `tags`, `sources` (type, text or items, declarations, cross-contact, completeness, age, confidence), `context` (identity confidence) | `expect`: outcome per allergen; fusion also `conflicts` |
| held-out ingredients, ocr | `id`, `input` | `must` / `must_not`: allergens the ingredient truly carries / does not carry |
| held-out scenarios | as safety | `truth` per allergen: present, cross_contact, absent (with complete verified evidence) or undeterminable |

Every held-out case has a written `justification`. In the development set, justifications exist for the
scenario cases where the reason is not obvious (fusion 11/17, uncertainty 18/20, safety 11/65). The
item-level development cases (normalization, synonyms, ontology, reasoning, compound) have none.

## Counts

| Set | Dataset | Version | Cases | Checks | With justification |
|---|---|---|---|---|---|
| development | normalization | 1 | 43 | 43 | 0 |
| development | ocr_correction | 1 | 25 | 25 | 0 |
| development | synonyms | 1 | 49 | 49 | 0 |
| development | ontology | 1 | 52 | 52 | 0 |
| development | reasoning | 2 | 89 | 89 | 0 |
| development | compound | 2 | 12 | 12 | 0 |
| development | fusion | 2 | 17 | 21 | 11 |
| development | uncertainty | 2 | 20 | 24 | 18 |
| development | safety | 1 | 65 | 86 | 11 |
| **development total** | 9 datasets | | **372** | **401** | |
| held-out | ingredients | 1 | 65 | 81 | 65 |
| held-out | ocr | 1 | 16 | 19 | 16 |
| held-out | scenarios | 1 | 40 | 69 | 40 |
| **held-out total** | 3 datasets | | **121** | **169** | **121** |

**Reconciling the earlier figures.** The report's "351 cases in nine datasets, ten evaluation areas,
86 safety checks" described development version 1:

- *Provenance* is the tenth area, with no dataset of its own: it is measured over the findings of the
  fusion, uncertainty and safety runs.
- *86 safety checks* are the allergen checks inside the 65 safety scenarios.
- Stages 1–2 added 21 development cases (reasoning +10, compound +2, uncertainty +6, fusion +3), giving
  372.

## Expert review

[`expert-review-sheet.csv`](expert-review-sheet.csv) lists every held-out label and every development
safety label, with its justification and empty columns for a reviewer's verdict and note. If a
qualified reviewer disagrees with a held-out label, the label is corrected in a new held-out version and
the experiment is re-run. The original results stay on record next to the corrected ones.
