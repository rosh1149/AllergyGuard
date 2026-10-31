# Database migrations

The schema is managed with [Alembic](https://alembic.sqlalchemy.org/). Migrations live in
`backend/migrations/versions/`; the application runs them at startup (`app.db.migrate.migrate`), and
they can be run by hand from `backend/`:

```bash
alembic upgrade head          # apply every migration not yet applied
alembic downgrade -1          # roll back the most recent one
alembic current               # the revision the database is at
alembic upgrade head --sql    # print the SQL for review instead of running it
```

The database URL comes from `DATABASE_URL`, as for the application.

## Revisions

| Revision | What it does | Rollback |
|---|---|---|
| 0001 | Baseline: every table, index and enum type as of the first Alembic release | Drops all of them |
| 0002 | Index on `assessments (kind, created_at)` for the administrators' assessment lookup | Drops the index |

## Starting points

`migrate()` handles three cases, all inside one transaction, so a failure leaves the database as it was:

1. **Empty database**: all migrations run from 0001.
2. **Database already under Alembic**: only the missing migrations run.
3. **Database created before Alembic** (by `create_all` in earlier versions): the frozen legacy
   upgrader (`app/db/upgrade.py`) adds the columns it knows about, any missing baseline table is
   created, the database is stamped at 0001, and later migrations run. Existing data is kept.

## Writing a migration

```bash
alembic revision --autogenerate -m "short description"   # then review the generated file
```

* Give it the next number as its revision id (`--rev-id 0003`).
* Every migration has a working `downgrade()`.
* Use `if_not_exists=True` / `if_exists=True` when adding or dropping indexes and columns, so the
  migration also applies to adopted pre-Alembic databases.
* Data changes (backfills) go in the migration too, in batches for large tables.
* `tests/test_migrations.py` checks that the migrations produce exactly the schema the models describe,
  that every migration rolls back, that a failed migration rolls back as a whole, and that a
  pre-Alembic database is adopted without losing data. It fails if a model changes without a migration.

## Test database safety

The tests drop and recreate the schema of the database they run against. `tests/conftest.py` refuses
to start unless that database's name ends in `_test`.
