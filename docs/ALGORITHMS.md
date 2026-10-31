# Algorithms (pseudocode)

The pseudocode follows the code; file references point to the implementation.

## A1. Analysis of one check

`app/engine/service.py`, `app/engine/assess.py`

```text
ANALYZE(sources S, profile P, context ctx) -> result
  for each source s in S:
      items[s] <- NORMALIZE(s.ingredients_text, ocr = (s.type = label photo))
      prep[s]  <- PREPARE(s, items[s], ctx)                     # A3
  conflicts <- COMPARE_LISTS(prep)                               # A4
  mismatch  <- any conflict of kind "ingredient_difference" that is unresolved and large
  eligible  <- { s : prep[s].absence_ok }
  absence_supported <- eligible ≠ ∅ and not mismatch
  E <- COLLECT_EVIDENCE(prep)                                    # evidence refs per allergen
  for each allergen a in P.allergens ∪ P.custom_terms:
      outcome[a], reasons[a] <- DECIDE(E[a], absence_supported)  # A5
      rules[a]  <- names of the rule branch taken
  overall <- most severe outcome over P (PRESENT > CROSS-CONTACT > UNKNOWN > NOT LISTED)
  return outcomes, reasons, provenance, conflicts, limitations, trace
```

## A2. Ingredient normalization

`app/engine/normalize.py`

```text
NORMALIZE(text, ocr) -> list of items
  text <- CLEAN(text)        # lower case, strip headings, percentages, symbols; E-numbers kept
  for each item in PARSE(text):            # splits on commas, keeps "name (components)" as a compound
      if item is an empty or unclosed class name ("colour (", "flavouring ()"):
          mark item incomplete; keep it as a class label
      r <- RESOLVE(item.text)
      if r unresolved and ocr:  r <- RESOLVE(OCR_CORRECT(item.text))   # only for photo text
      if r unresolved:          r <- RESOLVE(SPELLING(item.text))
      if r unresolved:
          c <- FUZZY_CANDIDATES(item.text)                 # RapidFuzz, threshold from settings
          if exactly one c and c links to an allergen:      # a guess may only ADD an allergen
              r <- RESOLVE(c)
          else:
              keep item unresolved, with candidates and reasons
      item.resolution <- r
  return items

OCR_CORRECT(word)
  variants <- every single (and, for words ≥ 5 letters, double) substitution from the
              confusion table (rn↔m, l↔i, 0→o, q↔g, qq→gg, …)
  hits <- variants that are known words
  return the hit if exactly one, else word
```

## A3. Concept mapping and per-source preparation

`app/engine/knowledge.py`, `app/engine/assess.py::_prepare`

```text
RESOLVE(text) -> resolution
  spans <- longest alias matches of known concepts in text
  remove term matches covered by a look-alike exclusion ("eggplant", "coconut", "nutmeg", …)
  for each concept c in spans:
      for each path from c along derived_from / is_a / contains_component / contains_protein
              / is_regulated_as to an allergen a:
          certainty <- definite if every edge on the path is definite and verified, else possible
          record (a, certainty, path text)
  if c is a homonym ("curd"): keep every meaning; allergens of any meaning are reported
  status <- allergen_linked | no_known_allergen | unresolved

PREPARE(s, items, ctx)
  trusted  <- s.type shows presence and (s.type does not depend on identification
              or ctx.identity_confidence ≥ 0.7)
  absence_ok <- s.type can support absence           # SOURCE_POLICY table
               and identity confirmed
               and s.complete
               and s.read_confidence ≥ 0.6
               and items ≠ ∅
               and s is fresh (age ≤ its maximum age)
               and no item is unresolved or incomplete
  return trusted, absence_ok, reason absence was refused
```

## A4. Comparing ingredient lists

`app/engine/assess.py`

```text
COMPARE_LISTS(prep)
  for each pair of trusted sources with ingredient lists:
      A, B <- the concept keys of each list (so "milk solids" and "dried milk" compare equal)
      only_in_A, only_in_B <- A \ B, B \ A
      if both empty: no conflict
      else if the difference is small and allergen-free: conflict recorded, handling "minor", absence allowed
      else: conflict "ingredient_difference", status unresolved -> blocks absence (B.list_mismatch)
      for an allergen shown by one source and not by another: conflict "allergen_disagreement";
          the cautious finding is kept
```

## A5. Decision for one allergen

`app/engine/assess.py::_decide`, `app/engine/rules.py`

```text
DECIDE(evidence refs R, absence_supported)
  if R has presence or declared from a trusted source:            return PRESENT          # R1
  if R has cross-contact (declared or candidate):                 return CROSS-CONTACT    # R2
  if R has a candidate (unverified source, uncertain identity)
     or a possible source ("vegetable oil", "starch", "flavouring"):  return UNKNOWN      # R3
  if R has only a free-from claim:                                return UNKNOWN          # R4
  if absence_supported:                                           return NOT LISTED       # R5
  return UNKNOWN, with the blocker (no eligible source / lists disagree)                  # R6
```

No branch returns "safe". NOT LISTED is reachable only through R5, which needs at least one complete,
fresh, verified, identified and fully readable source and no unresolved list disagreement.

## A6. Restaurant: choosing the record

`app/restaurant/recipes.py`, `app/restaurant/pipeline.py`

```text
RECORD_FOR(restaurant, branch, dish name or photo, time t)
  candidates <- menu items of THIS branch            # another branch's dish is never used
  item <- best fuzzy match of the name (photo-only matches are capped below the trust threshold)
  if no item or match weak: fall back to public recipes (candidates only) -> UNKNOWN at best
  v <- latest recipe version of item with effective_from ≤ t    # scheduled versions wait
  source type <- restaurant_verified if v verified by staff and not older than the limit,
                 else restaurant_unverified (shows allergens, never rules them out)
  return v as an evidence source, with its cross-contact records
```

## A7. External source call

`app/sources/base.py`

```text
CALL(adapter, fn)
  if adapter is remote and its circuit is open and the cool-down has not passed:
      return UNAVAILABLE("skipped: source failing")             # not contacted
  for attempt in 1..MAX_ATTEMPTS:
      result <- fn()   (exceptions become UNAVAILABLE)
      if result is not UNAVAILABLE: break                       # rate limits and not-found are not retried
      sleep BACKOFF × 2^(attempt-1) + jitter
  record failure or success on the circuit (5 failures in a row -> open for 60 s; one trial after)
  return result      # a failed source adds a limitation; its allergens become UNKNOWN, never NOT LISTED
```

## A8. Knowledge change

`app/knowledge/workflow.py`

```text
APPROVE(proposal, approver)
  require approver ≠ proposer                                       # four eyes
  candidate KB <- current KB + proposal
  require VALIDATE_GRAPH(candidate) has no violations              # types, direction, cycles, sources stated
  require every regression case still gives its expected allergens
  require the safety benchmark still passes the release gate
  publish as knowledge version n+1; log who, what and the previous value
```
