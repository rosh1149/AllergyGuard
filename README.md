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

## Frontend (Part 5)

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
