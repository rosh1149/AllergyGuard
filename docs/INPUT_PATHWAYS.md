# Input pathways (Stage 3)

## Restaurant recipe versions

- **Effective-from date.** Each recipe version can take effect at a set moment (empty = when saved).
  A check always uses the version in effect *at the moment of the check*. If a later change is
  scheduled, the result says so ("check again after that time").
- **No backdating.** A version cannot take effect in the past, so earlier results stay as they were.
- **No skipping.** A new version cannot be saved to take effect before an already-scheduled one; staff
  edit or follow the scheduled version instead. This also applies to confirming customer feedback.
- **Who and when.** Each version records who created and verified it, and when.
- **Outdated results.** A saved result is flagged outdated when a different version is in effect now.

## Branch-level evidence

- **Same branch only.** A dish is matched only against its own branch's menu. A menu item from another
  branch is refused, even within the same restaurant.
- **Dishes listed elsewhere.** If the dish is on a sister branch's menu, the customer is told where it
  is listed. That branch's records are **not** used: recipes and kitchen practices can differ.
- **Separate feedback.** Customer reports are tied to the dish, branch and recipe version they were
  about. They count only against that version and stay apart from verified records.

## Packaged food: external sources

| Source | When used | Trusted as |
|---|---|---|
| Open Food Facts | barcode or name search | product database (community) |
| Manufacturer page | the brand has an **administrator-approved domain** with a product page pattern | manufacturer evidence |
| Web search result on an approved domain | web search is configured | manufacturer evidence |
| Any other web page | web search is configured | **unverified candidate**: can raise a warning, never confirm or rule out |

**Identity check.** Before any page is used, it must be about the product being checked. Either:

- the barcode appears on the page, or
- the brand appears, the page title is at least a 70 % match for the product name, and the variant
  agrees (a "Dark Chocolate" page is not the page for the plain product).

A rejected page is named in the result with the reason.

**Untrusted text.** Page text is parsed with fixed rules; no model reads it. An instruction hidden in a
page can only ever be an unrecognised ingredient.

**Recording.**

- Each page version used is kept as a snapshot, with its URL, ingredient text and content hash.
- Every fetch is counted in the data-source health view, including pages that couldn't be retrieved,
  rate limits and malformed responses.

**Settings.**

- `MANUFACTURER_PAGES_ENABLED` (on).
- `WEB_SEARCH_PROVIDER=brave` and `WEB_SEARCH_API_KEY=...`: web search stays off without a key.
- Approved sites are managed at **Admin → Manufacturer sites** (`/api/admin/manufacturer-sites`), and
  every change is audited.

## OCR evaluation

`python -m app.evaluation.ocr_eval DIR` evaluates a folder of photos against manually checked
reference text. It reports:

- character and word error rate
- ingredient extraction precision and recall
- photos where an allergen was missed
- results per condition (glare, curved, low light...)
- the most frequent character confusions

**What the first run found.** It ran on a *generated* set: 5 labels under 5 conditions. Labels tilted
by only 4° lost allergens in 3 of 5 cases:

- "INGREDIENTS" was read as "{NGREDIENTS", so the heading wasn't found and a stray bracket swallowed
  the list.
- "egg" was read as "299".

Those allergens came out UNKNOWN, never NOT LISTED, but "butter (milk)" was no longer reported as
present. Two fixes followed:

- **Deskew:** straighten tilted photos before OCR (projection-profile angle estimate, ±8°).
- **Tolerant heading:** accept common misreads of the first letter of "Ingredients".

| Condition (generated) | CER before | CER after | Photos missing an allergen, before → after |
|---|---|---|---|
| clean, blur, low light, JPEG | 0.000 | 0.000 | 0 → 0 |
| rotated 4° | 0.066 | 0.000 | 3 → 0 |

These are generated images: they show the harness works and catch one failure mode, but **they are not
evidence of real-world accuracy.** The latest generated report is
[ocr-evaluation-generated.md](ocr-evaluation-generated.md).

### Running it on real photos

1. Put the photos in a folder, e.g. `real_labels/`.
2. Create `real_labels/manifest.json`:
   ```json
   {"description": "Real packaged-food photos, collected 2026-11 by the project team",
    "cases": [{"image": "p01.jpg", "reference": "<the ingredient statement exactly as printed>",
               "allergens": ["milk", "gluten"], "conditions": ["glare", "curved"], "notes": ""}]}
   ```
   - Type `reference` from the pack, not from OCR output.
   - Use condition tags consistently, e.g. clear, glare, curved, low light, small print, multilingual,
     cluttered background.
3. Run `python -m app.evaluation.ocr_eval real_labels --report docs/ocr-evaluation-real.md`.

Aim for at least 50 photos across the conditions. Record how they were collected.

## Public recipes

- **Only a fallback.** Public recipes are used only for unregistered restaurants or unmatched dishes.
- **Always candidates.** They can make an allergen UNKNOWN, never PRESENT or NOT LISTED.
- **Sanitised.** The ingredient names returned are cleaned: links, instruction-like text, safety
  claims, markup and over-long entries are dropped.
