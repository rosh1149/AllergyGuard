# Limitations

What AllergyGuard does not do, what has not been shown yet, and what would close each gap.

## Safety scope

| Limitation | Effect | What would close it |
|---|---|---|
| The system reports what records say; it cannot test food | A wrong or outdated label, recipe or database entry gives a wrong answer | Nothing in software; the result says so and points customers to staff |
| NOT LISTED is not "safe" | Customers may still read it as safe | Wording and disclaimers on every result; user testing of the wording |
| Knowledge coverage is finite | Unknown ingredients give Unknown (over-caution), not misses; 3 held-out over-warnings came from this | Grow the knowledge base through the reviewed proposal workflow |
| Custom allergies match text only | A custom allergy has no concept relations unless an admin adds them | Admin proposals for frequent custom allergies |

## Evidence not yet collected

| Gap | Status | Needed |
|---|---|---|
| Real label photos | OCR measured only on generated images (CER 0 after deskew) | 50+ real photos with transcriptions; `app/evaluation/ocr_eval.py` is ready |
| FSSAI regulation text | Indian list modelled from general sources | The official text to check `regulatory_entries` against |
| Independent labels | All datasets written by the developer | Expert review of `docs/expert-review-sheet.csv` |
| Live web search | Brave adapter tested on simulated responses | An API key |
| User study | No usability or comprehension testing with people with allergies | A small study of how results are understood |

## Statistical limits

- Held-out set: 69 scenario checks. Recall 53/53 has a 95 % interval of 0.93–1.00; a miss rate below
  about 7 % cannot be excluded.
- Conflict detection and compound parsing showed no effect on the held-out set because it has few such
  cases.
- The held-out set has now been used; new held-out data is needed for any further claim.

## Engineering limits

- Login throttling, the photo-job limit and the circuit breakers are held per server process. Several
  workers need a shared store (e.g. Redis).
- Tokens cannot be revoked before they expire.
- Photo processing runs in the API process; at higher photo volume a queue and separate workers would be
  needed (`docs/PERFORMANCE.md`).
- Performance numbers come from one 4-core machine.
- Rate limiting of the analysis endpoints is left to the reverse proxy.

## Open design questions

- H-S30: when two lists disagree and the conflict is unresolved, should an allergen shown by only one side
  be *possible* rather than *present*? The system currently keeps *present* (safe either way).
- H-S32: for a database entry with low identification confidence, is *Unknown* (current) or *present* the
  better label? Left to the expert review.
