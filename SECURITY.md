# AllergyGuard security boundaries

All checks are enforced by the backend. The frontend only hides what a user cannot use.

## Who can see what

| Data | Who can read it | Who can change it |
|---|---|---|
| Customer allergy profile | That customer | That customer |
| Stored assessments (`/api/me/assessments`) | That customer | That customer (delete) |
| Customer's suggestions | That customer; staff of the restaurant concerned (without the customer's identity) | Customer answers questions; staff decide |
| Restaurant recipes, ingredients, cross-contact, team, audit log | Members of that restaurant | Owners and managers (team changes: owners) |
| Public restaurant directory and menus | Any signed-in user. Names, categories and descriptions only, never recipes | Restaurant owners and managers |
| Restaurant review queue (dish matches) | Members of that restaurant | Owners and managers |
| Knowledge base, platform review queue, metrics, source health, admin audit | Administrators | Administrators (knowledge changes need a second administrator) |

- **Tenant isolation.** Every `/api/restaurants/{restaurant_id}/...` route resolves the caller's
  membership of that restaurant before doing anything else. Other restaurants, customers and
  anonymous callers get 404/403/401.
  - `tests/test_step_e.py::test_tenant_isolation_sweep` calls every such route (read from the
    OpenAPI schema, so new routes are covered automatically) as all three and fails on any other
    status.
- **Roles.**
  - Customer-only routes (`/api/me/...`, `/api/analyze/...`) refuse restaurant and admin accounts.
  - Admin routes refuse everyone else.
  - Inside a restaurant, staff can read but not change; managers can change menus, recipes and
    feedback but not the team; only owners manage the team.
  - `tests/test_security.py::test_role_sweep_staff_and_managers` reads each dashboard route's role
    check from its definition and calls it as staff and as a manager: every route a role is not
    allowed must return 403. `test_every_dashboard_route_declares_its_roles` fails if a new
    dashboard route has no role check at all.

## Personal data

- **Photos** are processed in memory and never stored.
- **Assessments** store outcomes, the sources used and counts, but not photos.
- **Review queue and unresolved-ingredient queue** store the uncertain text and scores, with no
  customer identity.
- **Logs** record events with counts and statuses only: no names, emails, allergy profiles or
  ingredient text (`app/core/observability.py`). Each request has an `X-Request-ID` for tracing.
  `test_logs_contain_no_personal_data` signs up, fails a login, runs a check and exports data with
  marked values, and fails if any of them appears in the log output.
- **Export and deletion.**
  - `GET /api/me/export` returns everything stored about a customer.
  - `DELETE /api/me`, with the customer's password, deletes the account, profile and assessments.
    Suggestions already sent to restaurants are kept without the customer's identity.

## Other protections

- **Passwords and tokens.** Passwords are hashed with bcrypt. Tokens are signed JWTs (HS256) that
  must carry an expiry (`JWT_EXPIRE_MINUTES`, default 24 h) and a subject. Expired tokens, tokens
  signed with another key, unsigned (`alg: none`) tokens and tokens for unknown, disabled or deleted
  accounts are refused (tested in `test_security.py`). Tokens are not revoked when a password
  changes; they lapse at expiry.
- **Failed logins.** After `LOGIN_MAX_FAILURES` (5) failed logins for one email from one client,
  further attempts are refused for `LOGIN_LOCKOUT_MINUTES` (15) with HTTP 429. This limit is held
  per server process; use a shared store (e.g. Redis) when running several workers.
- **Response headers.** API responses send `Cache-Control: no-store`, `X-Content-Type-Options:
  nosniff`, `X-Frame-Options: DENY` and `Referrer-Policy: no-referrer`.
- **Uploads.** At most 3 photos of at most `MAX_UPLOAD_MB` (10 MB) each; only JPEG, PNG and WebP.
  Each file is verified as an image, and an image over 60 megapixels is refused from its header,
  before it is decoded, so a small compressed file cannot expand into gigabytes of memory. Only a
  few photo jobs run at once (HTTP 503 with `Retry-After` when the server is busy). Non-images,
  empty files, too many files, oversized files and decompression bombs are tested in
  `test_security.py`.
- **What works without signing in.** Only sign-up, sign-in, the health check, the allergen list and
  the published decision matrix. `test_only_auth_and_health_work_without_signing_in` calls every GET
  route anonymously to check this. The public restaurant directory and menus (for signed-in users)
  are checked to contain no recipes, ingredients, emails, names or allergy data.
- **Audit trail.**
  - Restaurant changes and decisions go to the restaurant's audit log.
  - Administrator actions (knowledge proposals and decisions, unresolved-ingredient and review
    decisions) go to the platform audit log at `GET /api/admin/audit`.

## Database

- Schema changes are Alembic migrations, applied in one transaction at startup (`docs/MIGRATIONS.md`).
- The test suite drops the schema of the database it runs against, so it refuses to start unless
  that database's name ends in `_test`.

## Known limits

- Login throttling and the photo-job limit are per server process.
- Tokens cannot be revoked before they expire (no server-side session list).
- Rate limiting of the analysis endpoints themselves is left to the reverse proxy.
