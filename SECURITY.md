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

## Personal data

- **Photos** are processed in memory and never stored.
- **Assessments** store outcomes, the sources used and counts, but not photos.
- **Review queue and unresolved-ingredient queue** store the uncertain text and scores, with no
  customer identity.
- **Logs** record events with counts and statuses only: no names, emails, allergy profiles or
  ingredient text (`app/core/observability.py`). Each request has an `X-Request-ID` for tracing.
- **Export and deletion.**
  - `GET /api/me/export` returns everything stored about a customer.
  - `DELETE /api/me`, with the customer's password, deletes the account, profile and assessments.
    Suggestions already sent to restaurants are kept without the customer's identity.

## Other protections

- **Passwords and tokens.** Passwords are hashed with bcrypt. Tokens are signed JWTs (HS256) and
  expire. A disabled or deleted account is refused even with a valid token.
- **Failed logins.** After `LOGIN_MAX_FAILURES` (5) failed logins for one email from one client,
  further attempts are refused for `LOGIN_LOCKOUT_MINUTES` (15) with HTTP 429. This limit is held
  per server process; use a shared store (e.g. Redis) when running several workers.
- **Response headers.** API responses send `Cache-Control: no-store`, `X-Content-Type-Options:
  nosniff`, `X-Frame-Options: DENY` and `Referrer-Policy: no-referrer`.
- **Uploads.** Size and count are limited, every upload is decoded and checked as an image, and only
  a few photo jobs run at once (HTTP 503 with `Retry-After` when the server is busy).
- **Audit trail.**
  - Restaurant changes and decisions go to the restaurant's audit log.
  - Administrator actions (knowledge proposals and decisions, unresolved-ingredient and review
    decisions) go to the platform audit log at `GET /api/admin/audit`.
