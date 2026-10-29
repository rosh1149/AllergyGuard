# AllergyGuard

Allergen-information system for packaged food and restaurant dishes. It reports available
evidence and uncertainty; it never certifies food as safe.

## Status

| Part | Scope | Status |
|---|---|---|
| 1 | Foundation: FastAPI, PostgreSQL schema, auth & roles, tenant isolation, allergen knowledge base | Done |
| 2 | Shared allergen analysis engine | Done |
| 3 | Packaged-food pipeline | Planned |
| 4 | Restaurant pipeline, fallback, feedback workflow, dashboard API | Planned |
| 5 | React + TypeScript frontend | Planned |

## Run

```bash
cp .env.example .env
docker compose up --build
```

API docs: http://localhost:8000/docs

### Without Docker

```bash
cd backend
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Requires a PostgreSQL database at `DATABASE_URL`. Tables and reference data are created on startup.

### Tests

```bash
cd backend
pytest -q
```

Uses the `allergyguard_test` database (override with `TEST_DATABASE_URL`). It is dropped and recreated.

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
| GET | `/api/restaurants/{restaurant_id}` | restaurant member |
| POST | `/api/analyze/text` | customer: check a typed-in ingredient list against their saved allergies |

## Layout

```
backend/app/
  core/       config, password hashing, JWT
  db/         engine/session, table creation + seeding
  models/     SQLAlchemy models (users, allergens, restaurants, recipes, feedback, audit)
  schemas/    request/response models
  services/   business logic
  engine/     shared allergen analysis engine (text parsing, knowledge-base matching, rules, explanation)
  api/        dependencies (auth, tenant access) and routes
  seed/       allergen knowledge base (14 allergens, synonyms, exclusions) and demo data
```
