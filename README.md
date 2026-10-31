# AllergyGuard

Allergen-information system for packaged food and restaurant dishes. It reports available
evidence and uncertainty; it never certifies food as safe.

## Status

| Part | Scope | Status |
|---|---|---|
| 1 | Foundation: FastAPI, PostgreSQL schema, auth & roles, tenant isolation, allergen knowledge base | Done |
| 2 | Shared allergen analysis engine | Done |
| 3 | Packaged-food pipeline | Done |
| 4 | Restaurant pipeline, fallback, feedback workflow, dashboard API | Done |
| 5 | React + TypeScript frontend | Done |
| A | Ingredient knowledge layer: concepts, FoodOn ids, relationship reasoning, allergen hierarchy, regional lists, versioned maintenance, unresolved queue | Done |
| B | Multi-stage ingredient normalization: cleaning, OCR correction, spelling, synonyms, RapidFuzz fuzzy matching, compound ingredients, canonical concepts, uncertainty kept | Done |

## Run

```bash
cp .env.example .env
docker compose up --build
```

- App: http://localhost:8080
- API docs: http://localhost:8000/docs

### Without Docker

Backend (needs PostgreSQL at `DATABASE_URL` and Tesseract OCR: `apt install tesseract-ocr` /
`brew install tesseract`). Tables and reference data are created on startup.

```bash
cd backend
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Frontend (Node 20+), in a second terminal. The dev server proxies `/api` to `localhost:8000`.

```bash
cd frontend
npm install
npm run dev
```

Then open http://localhost:5173.

### Tests

```bash
cd backend
pytest -q
```

Uses the `allergyguard_test` database (override with `TEST_DATABASE_URL`). It is dropped and recreated.

Frontend type check and build: `cd frontend && npm run build`. A Playwright browser walkthrough of the
main journeys is in `frontend/e2e/walkthrough.cjs` (instructions at the top of the file).

## Demo accounts (when `SEED_DEMO_DATA=true`)

| Role | Email | Password |
|---|---|---|
| Customer (allergies: milk, peanuts) | customer@demo.allergyguard.app | Demo1234! |
| Restaurant owner (Spice Route Kitchen) | owner@demo.allergyguard.app | Demo1234! |
| Knowledge administrator (proposes changes) | admin@demo.allergyguard.app | Demo1234! |
| Knowledge reviewer (approves changes) | reviewer@demo.allergyguard.app | Demo1234! |

Create real administrators with `python -m app.cli create-admin --email you@example.com` (in `backend/`).

## Key design rules implemented so far

- **Allergies are captured at sign-up.** `POST /api/auth/register/customer` requires an allergy
  profile (standard allergen codes and/or custom terms). Later analyses read it from the logged-in
  account; no request ever passes allergies. Customers can edit it via `PUT /api/me/allergy-profile`.
- **Tenant isolation.** Every restaurant route depends on `RestaurantAccess`, which checks the
  user's membership for the restaurant in the URL. Non-members get 404 (not 403) so ids can't be probed.
- **Roles.** User role: `customer` / `restaurant` / `admin`. Restaurant membership role:
  `owner` (everything), `manager` (edit records, review feedback), `staff` (read-only).
- **Recipe versions are snapshots.** Ingredients, allergen records and cross-contact records hang off
  a `RecipeVersion`; changes create a new version, so history is preserved.
- **Cross-contact is stored separately** from ingredient presence.
- **Customer feedback is unverified by default** and has its own table; trusted records only change
  through an authorized decision, which is written to `audit_records`.

## Allergen analysis engine (Part 2)

`backend/app/engine/` is shared by both pipelines. Pipelines describe what they found as
`EvidenceSource` objects (label text, product database entry, restaurant records, public recipe,
customer suggestion); `assess()` applies fixed rules; the explanation is written afterwards.

**Rules, per allergen (first match wins):**

1. Presence in an authoritative source whose identity is trusted -> **PRESENT**
2. Any cross-contact evidence (restaurant records, "may contain" statements) -> **POSSIBLE CROSS-CONTACT**
3. Presence only in an unverified/candidate source -> **UNKNOWN**
4. Only a "free-from" claim mentions it -> **UNKNOWN**
5. At least one complete, reliable, verified ingredient source, and the sources' ingredient lists
   don't disagree -> **NOT LISTED IN VERIFIED RECORDS** (never "safe")
6. Otherwise -> **UNKNOWN**

**What counts as what:**

| Source | Presence counts as PRESENT | Can support NOT LISTED |
|---|---|---|
| Label photo (OCR) / label typed in | yes | yes, if read reliably (confidence >= 0.6) |
| Product database / manufacturer | yes, if identification confidence >= 0.7 | same condition |
| Restaurant records, verified | yes, if dish match confidence >= 0.7 | same condition |
| Restaurant records, not verified | yes, if dish match confidence >= 0.7 | no |
| Public recipe, customer suggestion | no (UNKNOWN, flagged) | no |

**Also handled:** ingredient synonyms and look-alikes (knowledge base), compound ingredients
(`chocolate (sugar, milk powder)`), "may contain" / "made in a factory that handles" statements,
free-from claims, non-specific ingredients ("spices", "natural flavourings"), conflicts (sources
disagree on an allergen; ingredient lists differ substantially; a restaurant's ingredients indicate an
allergen its own declaration omits), custom allergies, and personalization (the customer's own
allergens first, other allergens found listed separately).

**Explanation:** a template explanation is always produced. If `ANTHROPIC_API_KEY` is set, Claude
(`claude-opus-5-5` by default, low effort, server-side refusal fallback enabled) rewrites it from the
structured result only. Its output is rejected, and the template used instead, if it omits one of the
user's allergens or makes a safety claim ("safe to eat", "allergen-free", "no risk", ...).

## Ingredient knowledge layer (Step A)

The allergen terms of Part 2 are now complemented by a knowledge layer of **ingredient concepts**
(`backend/app/seed/concept_data.py`; 184 concepts, 500+ aliases, 130+ relationships):

- **Typed relationships**: `derived_from`, `is_source_of`, `is_a`, `contains_component`,
  `associated_with_allergen`, each `definite` or `possible`. Findings carry the chain that explains
  them, e.g. *sodium caseinate, derived from casein, is a milk protein, derived from milk*.
  A **possible** link (lecithin may come from soy or egg) gives UNKNOWN, never PRESENT. A recognised
  concept overrides a look-alike term (cocoa butter is not milk), and look-alike exclusions also apply
  to concepts (cream of tartar is not milk).
- **FoodOn ids** (93 concepts): attached only where the concept's own name equals the label of exactly
  one FoodOn class, checked against the class's parents (FoodOn commit `cd73540`, 2026-09-20).
  Reproduce with `backend/scripts/map_foodon.py`; `app/seed/foodon_mappings.json` records the release,
  each class's parents, alias-only candidates for review, and excluded matches with reasons (FoodOn's
  "kiwi" is the bird).
- **Allergen hierarchy**: wheat is a separate allergen (US major allergen) under cereals containing
  gluten. Wheat implies gluten; barley implies gluten but not wheat; "contains gluten" without a named
  source leaves wheat as a possibility.
- **Regional lists** kept apart from the science: EU (Regulation 1169/2011 Annex II), US (FALCPA as
  amended by the FASTER Act), India (FSSAI Labelling and Display Regulations 2020, marked
  **unverified** because the official text could not be retrieved when it was entered). Each allergen
  result says where it is regulated; `GET /api/allergens?jurisdiction=US` filters the sign-up list.
- **Ingredient breakdown** in every result (`ingredients`): `allergen_linked`, `possible_allergen`,
  `ambiguous_source`, `no_known_allergen` or `unresolved`, with concept, FoodOn id and chain.
  Unresolved text is listed as a limitation and queued for review (text and source type only).
- **Versioned maintenance**: changes are proposals (`/api/knowledge/proposals`) approved by a second
  administrator. Approval re-runs the knowledge regression cases (`app/seed/kb_regression.py`) on the
  changed knowledge before publishing; a failing change is not published. Each approval creates a new
  knowledge version with a change-log entry, and every result reports the `knowledge_version` it used.
- Endpoints: `POST /api/knowledge/resolve` (any signed-in user), `GET /api/knowledge/regulatory`, and for
  administrators `summary`, `concepts`, `concepts/{slug}`, `proposals`, `changes`, `unresolved`.

## Ingredient normalization (Step B)

`backend/app/engine/normalize.py` turns raw ingredient text into ingredients traced from what was
written to a canonical concept:

| Stage | What it does |
|---|---|
| 1. Cleaning | Symbols, percentages, `E 322` / `e-322` -> `e322`, whitespace, "Ingredients:" prefix |
| 2. OCR correction | Label photos only. Tesseract confusions (l/i, rn/m, cl/d, 5/s, 0/o, vv/w...), up to two per word, accepted only when exactly one correction is a known word: `Caseln` -> `casein` |
| 3. Spelling | Reviewed regional variants and misspellings (`app/seed/spelling_data.py`): flavor -> flavour, mayonaise -> mayonnaise |
| 4. Synonyms | Exact alias / allergen-term lookup (Step A knowledge base) |
| 5. Fuzzy matching | RapidFuzz similarity for whole names (>= 90) and single words (>= 88), minimum length 5, with a required lead over the runner-up; all configurable (`FUZZY_*` settings) |
| 6. Compound ingredients | Nested brackets, class names (`emulsifier: ...`), headers (`contains 2% or less of:`), and source qualifiers: `lecithins (soya)` means soy only, `vegetable oil (sunflower, rapeseed)` is resolved |
| 7. Canonical concept | Concept slug and FoodOn id |
| 8. Uncertainty kept | Unknown text stays unresolved, is listed as a limitation and queued for review |

**Safety rule for guesses (stages 2 and 5):** a guess may add an allergen finding (the cautious
direction, shown as *'caseln' (read as 'casein', OCR correction)*), but never turns unknown text into
"no known allergen": a harmless guess is kept only as a `suggestion`. Text with unknown words left over
counts as unresolved even when part of it is known ("penaut oil" is not hidden behind "oil").

Every ingredient in a result keeps `original_text`, normalized `text`, `role` and `parent`,
`concept` / `foodon_id`, `match_method` (exact, spelling, ocr_correction, fuzzy, qualified, none),
`match_confidence`, `corrections` and `suggestion`. `POST /api/knowledge/resolve` (with `"ocr": true`
for photo text) shows the same breakdown for any ingredient list.

## Evidence fusion, freshness and history (Step C)

Every source and every finding now says where it came from and how current it is:

- **Provenance.** Sources carry `reference` (e.g. `barcode 5000159407236`,
  `Spice Route / Central / Butter Chicken / recipe v3`, `label photo 1`), `verification`
  (label, verified, unverified, community, public, customer) and `as_of`. Each evidence item repeats
  these, plus `collected_at`, the match confidence and the ids of any conflicts it is part of.
- **Freshness.** Verified restaurant recipes older than `RESTAURANT_RECORD_MAX_AGE_DAYS` (365) and
  product-database entries older than `PRODUCT_RECORD_MAX_AGE_DAYS` (1095, using Open Food Facts
  `last_modified_t`) still report allergens as present, but cannot rule them out.
- **Completeness.** A source with unidentified ingredient text cannot support NOT LISTED. Each source
  reports `completeness` (complete, partial, unavailable, conflicting) and `unresolved_items`.
- **Conflicts.** Each conflict has an `id`, the dates of the sources involved and a `resolution`.
  The cautious finding is always kept. A new kind, `cross_contact_disagreement`, covers cases where
  one source reports cross-contact and another, which assessed it, does not.
- **Basis.** Each allergen outcome lists what it rests on, for example direct_ingredient,
  derived_ingredient, declared_by_source, cross_contact, not_found_in_verified_records or
  insufficient_evidence.
- **Structured cross-contact.** Restaurant records take a `kind` (shared_equipment, shared_fryer,
  preparation_area, utensils, storage, manufacturing_facility, supplier, precautionary_label, other)
  and a `status` (verified, reported, unknown). A recipe version can be marked
  `cross_contact_reviewed`. Without that mark, and with no cross-contact records, the result says that
  cross-contact was not assessed.
- **Recipe versions.** Each version in the dashboard shows `changes` from the previous one:
  ingredients, allergens and cross-contact added or removed. Unreviewed customer reports count only
  against the current version. Reports on an older version are listed as a limitation.
- **Product snapshots.** Each distinct Open Food Facts record is kept in `product_snapshots`, keyed
  by a content hash. If the record changes, the result warns about a possible reformulation and lists
  the ingredients and allergens added or removed.
- **Stored assessments.** Every analysis is saved for the customer, without photos. The record holds
  the outcome per allergen, the sources used, the knowledge version and links to the recipe version
  or product snapshot. `result.assessment_id` identifies it. `GET /api/me/assessments` marks an
  assessment `outdated` when the recipe, the product record or the knowledge base has changed since.

Existing databases are upgraded in place at startup (`app/db/upgrade.py`, which adds the new
columns; new tables are created automatically).

## Source adapters, feedback review and the review queue (Step D)

**Source adapters** (`backend/app/sources/`). Each evidence source is fetched through an adapter that
turns its data into the engine's common `EvidenceSource`:

| Adapter | Source type |
|---|---|
| `open_food_facts` | product database (barcode lookup, name search) |
| `label_photo` / `label_text` | package label (OCR / typed) |
| `restaurant_records` | the restaurant's recipe version |
| `customer_reports` | unreviewed customer reports (candidates only) |
| `public_recipes` | public web recipes (candidates only) |

Every call returns a status (`ok`, `not_found`, `unavailable`, `rate_limited`, `malformed`,
`skipped`), the time it was retrieved and how long it took. A failing or buggy adapter only removes
that source and adds a limitation; it never breaks the analysis. Adapters fetch and convert only.
Normalization and outcomes stay in the engine, so adding a source means writing one adapter.
`GET /api/sources` (admin) lists the adapters with call and failure counts.

**Feedback review** (extends Part 4):
- New categories: `incorrect_allergen` and `outdated_recipe`. Both need a message.
- Staff can confirm, reject, or `clarification_requested` with a question. The customer answers
  through `POST /api/me/feedback/{id}/clarification`, and the suggestion goes back for review.
  Pending reports, including those awaiting an answer, still appear as unreviewed reports. The
  original message is never edited.
- A confirmed `outdated_recipe` publishes the same recipe as an **unverified** version, so it can no
  longer rule allergens out until staff update and verify it.
- A confirmed `incorrect_allergen` is never applied automatically, because removing an allergen makes
  the record less cautious. Staff edit the recipe themselves.
- Each suggestion shows its `history` (submitted, question, answer, decision) and, once confirmed,
  its `impact`: the version it produced, what changed, and how many later customer checks used it.

**Review queue for uncertain input** (`review_items`). The analysis still answers the customer
cautiously, and the uncertain input is kept for a person to look at instead of being forced into a
conclusion:
- Products: several close name matches (the customer is asked to choose via `product_candidates` /
  `needs_confirmation`), low-confidence identification, unreadable labels, labels without an
  "Ingredients" heading.
- Dishes: several close menu matches (`needs_confirmation`), low-confidence matches, and dishes
  customers asked about that are not on the menu.
- An ambiguous match is capped below the identity threshold, so it never counts as verified evidence.
- Repeat occurrences are merged and counted. Only text and scores are stored, with no photos and no
  customer identity.
- Admins use `GET /api/review-queue`, `/summary` (which includes open unresolved ingredients) and
  `POST /api/review-queue/{id}/decision`. Restaurant staff see and resolve their own dish items at
  `/api/restaurants/{id}/review-queue`, and each decision is audited.

## Observability, caching and security (Step E)

**Decision logging.**
- Every allergen outcome lists the decision `rules` that produced it (`app/engine/rules.py`): for
  example `R1.present_ingredient`, `R2.cross_contact_declared`, `R5.not_listed_verified`, or
  `R6.absence_not_supported` with the reason absence was blocked (`B.no_eligible_source`,
  `B.list_mismatch`).
- Every result carries a `trace` of processing stages with timings and counts (barcode, OCR, product
  lookup or dish match, normalization, concept mapping, fusion, decision, explanation) and its total
  `duration_ms`. Both are stored with the assessment.
- Traces and logs contain counts and statuses only: no ingredient text, allergy profile or identity.
- Each request gets an `X-Request-ID`, and each analysis writes one JSON log line
  (`analysis.completed`).

**Metrics.** `GET /api/admin/metrics?hours=24` (admin) reports, from stored assessments:
- volume by kind and outcome
- p50/p95 latency per pipeline and per stage
- share of checks with conflicts, low-confidence identification, unresolved ingredients or failed
  stages
- how often each decision rule fired

It also reports open review items, source-adapter health, cache hit rates, background jobs and
photo-job usage.

**Caching and performance.**
- Normalization results are cached per knowledge-base version (an approved knowledge change starts a
  fresh cache). A repeat list takes about 0.5 ms instead of about 11 ms.
- Open Food Facts answers are cached: products for 6 h, "not found" for 10 min, searches for 1 h.
  Failures are never cached.
- When Open Food Facts is down or rate-limited, the last saved copy of the product record is used,
  labelled with its date. Freshness rules still apply.
- Queue bookkeeping (unresolved ingredients, review queue) runs after the response on a small thread
  pool (`BACKGROUND_INLINE=true` runs it immediately, as the tests do).
- At most `MAX_CONCURRENT_IMAGE_JOBS` photo-processing jobs run at once.

**Security re-check.** See [SECURITY.md](SECURITY.md). Step E adds:
- an automated tenant-isolation sweep over every dashboard route
- failed-login throttling
- security headers and `no-store` caching on API responses
- customer data export and account deletion (`GET /api/me/export`, `DELETE /api/me`)
- a platform audit log for administrator actions

## Evaluation benchmark and release gate (Step F)

`backend/app/evaluation/` measures each layer separately:
- normalization, OCR correction, synonyms and ontology mapping
- semantic reasoning and compound ingredients
- evidence fusion, provenance and uncertainty
- end-to-end safety, using a curated allergen dataset with positive, negative and regression cases

`release_criteria.json` sets the safety criteria (zero allergen false negatives, zero unsafe
absences, full traceability) and the quality thresholds. `baseline.json` blocks regressions. The gate
runs in CI (`.github/workflows/ci.yml`, job `safety-gate`) and before any approved knowledge change
is published. See [docs/EVALUATION.md](docs/EVALUATION.md) for the metrics and what the first run
found, and [docs/evaluation-report.md](docs/evaluation-report.md) for the latest report.

```bash
cd backend && python -m app.evaluation
```

## Stage 2: reasoning and fusion engine

- **Decision table.** One source-policy table drives the engine. `GET /api/knowledge/decision-matrix`
  publishes the sources, rules R1–R6, absence conditions, thresholds, conflict handling and
  cross-contact statuses the engine actually uses.
- **Unverified knowledge** can only raise a *possible* link, never PRESENT.
- **Conflicts.** Sources are compared by matched concept, not wording. Each conflict lists what only one
  source has, its status (resolved or unresolved) and how it was handled. Minor differences without an
  allergen link are explained but don't block NOT LISTED.
- **Provenance.** Each finding also records its relationship steps, FoodOn ID and allergen category.
- **Cross-contact.** Each allergen gets a cross-contact status (reported / none reported after review /
  not assessed) kept apart from its ingredient outcome. Restaurant records can tie a cross-contact risk
  to one ingredient.
- **Tests** cover determinism, and show that a customer's other allergies never change how an allergen
  is assessed.

## Stage 1: knowledge foundations

- **Held-out test set.** `backend/app/evaluation/heldout/` holds 121 cases with ground-truth labels. It
  was written and frozen (SHA-256 manifest) before any Stage 1 change, is only checked for integrity in
  CI, and is run once at the evaluation stage (`python -m app.evaluation.heldout_runner --final`).
- **Concepts.** Every concept now has a type (food source, ingredient, derivative, protein, allergen
  substance, additive, prepared food, general term).
- **Relations.** New relations are `contains_protein` and `is_regulated_as`. Regulatory categories are
  separate from scientific links.
- **Proteins and homonyms.** A protein with several sources links to each only as possible: "gluten" is
  certainly the gluten category but only possibly wheat. Homonyms ("curd") keep every meaning.
- **Graph rules** (types, direction, cycles, unstated sources) check the seed data, refuse bad
  relation proposals immediately, and block approvals.
- **FoodOn mapping changes** go through reviewed `foodon_mapping` proposals that keep the old
  identifier.
- **Incomplete component lists** ("etc.", "and others", "…", an unclosed or empty bracket) are detected
  and stop a source from supporting NOT LISTED.
- **Made foods.** A made food written without its components (dark chocolate, sausage, nougat, pesto,
  curry paste, gravy) can no longer be ruled out. With a complete component list, the listed components
  decide.
- **Unidentified ingredients** keep their rejected candidate matches and the reasons they could not be
  resolved, for administrator review.
- **Development benchmark (v2).** The dark chocolate expectation was changed, with its justification
  recorded, and Stage 1 cases were added. The release gate passes; the baseline was updated.

See [docs/KNOWLEDGE_MODEL.md](docs/KNOWLEDGE_MODEL.md) for the model, the rules and the formal result
semantics.

## Packaged-food pipeline (Part 3)

`POST /api/analyze/packaged` (multipart): up to 3 photos (`images`), and/or a typed `barcode`,
and/or a `product_name`. The customer's saved allergies are applied automatically.

1. **Image checks** - JPEG/PNG/WebP, size limit (`MAX_UPLOAD_MB`), EXIF rotation; resolution, blur,
   darkness and low-contrast issues are reported as limitations.
2. **Barcode** - EAN/UPC read from the photos (zxing-cpp) or typed in; check digit validated.
3. **Label OCR** - Tesseract reads each photo; the ingredient statement is cut out between the
   "Ingredients" heading and the next section (nutrition, storage, best before, ...), keeping any
   "may contain" advice. Without a heading the text is used for presence only (not complete).
4. **Identification** - barcode lookup in Open Food Facts (confidence 0.95); otherwise Claude vision
   reads brand/name/variant from the photos (if `ANTHROPIC_API_KEY` is set) or the typed product
   name is used, then Open Food Facts search picks the closest match (confidence = recognition
   confidence x name similarity). Below 0.7 the database data is treated as a possible match only.
5. **Evidence -> shared engine** - label text (`label_ocr`) and the database entry
   (`product_database`: ingredients, allergen tags, trace tags as cross-contact) go to the engine,
   which compares them, flags disagreements, and produces the result.

The response contains the engine result plus the identified product, the label text that was read,
per-photo quality metrics and a step-by-step status list. If Open Food Facts can't be reached, the
label photo is still analysed and the limitation is shown.

Not implemented: scraping manufacturer websites (unreliable to do generically; Open Food Facts
links to the product page instead).

## Restaurant pipeline, feedback and dashboard (Part 4)

### Customer: checking a restaurant dish

`POST /api/analyze/restaurant` (multipart). Registered restaurant: `restaurant_id` (+ `branch_id`
when it has several branches) and a dish photo, `dish_name` or `menu_item_id`. Unregistered:
`restaurant_name` (+ `location`) and a dish name or photo.

1. **Dish matching** against the branch's available menu:
   - picked from the menu -> confidence 1.0
   - typed dish name -> fuzzy name similarity
   - photo -> Claude vision picks a menu item (needs `ANTHROPIC_API_KEY`); **a photo alone is capped
     at 0.6**, below the 0.7 trust threshold, because a photo isn't proof of the recipe
   - name and photo agree -> boosted
   The top candidates are returned so the app can ask the customer to confirm the dish.
2. **Branch records** for the matched dish's current recipe version (ingredients, allergen records,
   cross-contact) -> engine as `restaurant_verified` or `restaurant_unverified`.
3. **Unreviewed customer reports** for the dish -> engine as `customer_suggestion` (candidate only:
   they can turn "not listed" into "unknown"/"possible cross-contact", never into "present").
4. **Fallback** (unregistered restaurant, or dish not on the menu): Claude searches public recipes
   (web search, needs `ANTHROPIC_API_KEY`) -> `public_recipe` candidates with source links. Never
   saved, never PRESENT or NOT LISTED.
5. **Questions for staff** - a deterministic checklist built from the result, naming common hidden
   sources (e.g. milk: butter, ghee, cream...).

Customer directory: `GET /api/public/restaurants?q=` and
`GET /api/public/restaurants/{id}/branches/{branch_id}/menu` (names only; recipes stay private).

### Feedback and verification

- `POST /api/feedback` - customer suggestion (`missing_ingredient`, `incorrect_ingredient`,
  `missing_allergen`, `cross_contact`, `other`). Stored as **unverified**; trusted records unchanged.
  Duplicate pending suggestions are rejected. `GET /api/me/feedback` shows the outcome.
- `POST /api/restaurants/{id}/feedback/{fid}/decision` (owner/manager):
  - **confirmed** -> a new **verified** recipe version with the change applied (added ingredient
    gets allergen codes from the knowledge base), linked from the suggestion, audited
  - **rejected** -> nothing changes, audited
  - no response -> stays unverified
  Removing an ingredient that isn't in the recipe must be done by editing the recipe directly.

### Dashboard API (`/api/restaurants/{restaurant_id}/...`)

| Area | Endpoints | Role |
|---|---|---|
| Overview | `GET ""` (counts: menu items, unverified recipes, pending feedback, team) | any member |
| Settings | `PATCH ""` | owner |
| Branches | `GET/POST /branches`, `PATCH /branches/{id}` (deactivate with `is_active: false`) | owner (read: any) |
| Team | `GET/POST /team`, `PATCH/DELETE /team/{user_id}`; last owner can't be removed/demoted | owner (read: any) |
| Menu | `GET /menu?branch_id=&status=`, `POST /menu` (with initial recipe), `GET/PATCH/DELETE /menu/{id}` (delete = archive) | owner/manager (read: any) |
| Recipe versions | `POST /menu/{id}/versions` (new snapshot), `POST /menu/{id}/versions/{vid}/verify` | owner/manager |
| Allergen helper | `POST /ingredients/check` (suggests allergen codes for ingredient names) | any member |
| Feedback | `GET /feedback?status=`, `POST /feedback/{id}/decision` | owner/manager (read: any) |
| Audit log | `GET /audit` | owner/manager |

Recipe versions are snapshots: a change always creates a new version and old versions are kept.
Versions report **warnings** when an ingredient name indicates an allergen the recipe doesn't
declare (e.g. "ghee" without milk).

## Frontend (Part 5, extended in Step G)

Screens added in Step G:

- **Results:**
  - Each allergen has a "Why" panel showing the evidence, the source, its verification and date, how
    the ingredient was read, the relationship path and the decision rule.
  - Each source shows its reference, its freshness and completeness, and any unidentified items.
  - Each conflict shows how it was resolved.
  - A "Processing details" table lists every stage with its timing.
- **Customers:**
  - Past checks (`/history`), with "may be outdated" flags.
  - Choosing a product when several match.
  - Answering a restaurant's question about a suggestion, and the two new suggestion types.
  - Downloading their data and deleting their account.
- **Restaurant dashboard:**
  - In the feedback list: asking the customer a question, a "waiting for customer" tab, and each
    suggestion's history and impact.
  - In the recipe editor: the cause and status of each cross-contact record, and a "cross-contact
    reviewed" flag.
  - In the version history: changes between versions.
  - A new page for uncertain dish matches.
- **Administrators** (`/admin`, previously no screens): monitoring, the review queue, unidentified
  ingredients, data-source health and the audit log.

`frontend/e2e/step_g.cjs` walks through all of these in a browser (fresh database with demo data),
including a phone-width check.


React 19 + TypeScript + Vite + React Router, no UI framework (`frontend/src/styles.css`).

**Customer app**
- Sign up with allergies (required; standard allergens + custom ones); edit them under *My allergies*.
- *Packaged food*: up to 3 photos (camera on phones), optional barcode/product name.
- *Restaurant food*: search registered restaurants -> branch -> pick the dish from the menu, or type
  its name / add a photo; or check an unlisted restaurant. Unconfirmed dish matches offer
  "Is your dish one of these?" buttons.
- *Type an ingredient list* for labels that won't scan.
- Results: overall verdict, a card per allergy with the reasons, other allergens found, conflicts,
  limitations, explanation, evidence sources, "Ask the staff" checklist, how it was checked.
- *Suggest a correction* on restaurant dishes; *My suggestions* shows the restaurant's decision.

Outcome colours: Present = red, Possible cross-contact = amber, Unknown = grey,
Not listed in verified records = blue (deliberately not green - it is not a safety signal).

**Restaurant dashboard**: overview (counts needing attention), menu (filter by branch/status;
unverified recipes and pending suggestions flagged), dish page (current recipe with allergen
warnings, mark verified, edit = new version, version history, archive), add dish with the recipe
editor ("suggest allergens from names", per-ingredient allergen toggles, dish-level allergens,
cross-contact), feedback review (confirm/reject with note, explains what confirming will change),
branches, team, settings, audit log. Controls follow the role (owner / manager / staff read-only);
the API enforces the same rules.

## API

| Method | Path | Who |
|---|---|---|
| GET | `/api/health` | public |
| GET | `/api/allergens` | public |
| POST | `/api/auth/register/customer` | public |
| POST | `/api/auth/register/restaurant` | public |
| POST | `/api/auth/login` | public |
| GET | `/api/auth/me` | logged in |
| GET / PUT | `/api/me/allergy-profile` | customer |
| GET | `/api/restaurants/{restaurant_id}/...` | restaurant member (see dashboard table above) |
| POST | `/api/analyze/restaurant` | customer: restaurant dish |
| GET | `/api/public/restaurants`, `.../branches/{id}/menu` | logged in |
| POST | `/api/feedback`, GET `/api/me/feedback` | customer |
| POST | `/api/analyze/packaged` | customer: photos / barcode / product name |
| POST | `/api/analyze/text` | customer: check a typed-in ingredient list against their saved allergies |
| GET / DELETE | `/api/me/assessments`, `/api/me/assessments/{id}` | customer: stored results, with `outdated` flags |
| POST | `/api/me/feedback/{id}/clarification` | customer: answer a restaurant's question |
| GET / POST | `/api/review-queue`, `/summary`, `/{id}/decision` | admin |
| GET / POST | `/api/restaurants/{id}/review-queue`, `/{item_id}/decision` | restaurant staff |
| GET | `/api/sources` | admin: source adapters and their health |
| GET | `/api/admin/metrics`, `/api/admin/audit` | admin: monitoring and platform audit log |
| GET / DELETE | `/api/me/export`, `/api/me` | customer: export data, delete account |

## Layout

```
frontend/src/
  api/        fetch client and response types
  auth/       login state
  components/ result view, allergy picker, photo picker, shared UI
  pages/      customer pages, restaurant dashboard pages

backend/app/
  core/       config, password hashing, JWT
  db/         engine/session, table creation + seeding
  models/     SQLAlchemy models (users, allergens, restaurants, recipes, feedback, audit)
  schemas/    request/response models
  services/   business logic
  restaurant/ restaurant pipeline, recipe versions, feedback workflow, public-recipe fallback
  packaged/   packaged-food pipeline (image checks, barcode, OCR, Open Food Facts, vision)
  engine/     shared allergen analysis engine (text parsing, knowledge-base matching, rules, explanation)
  api/        dependencies (auth, tenant access) and routes
  seed/       allergen knowledge base (14 allergens, synonyms, exclusions) and demo data
```
