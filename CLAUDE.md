# Swugl

Private family hub SaaS. Live at swugl.com. Flask + SQLAlchemy + PostgreSQL (Railway) / SQLite (local).

## Working on this codebase

**Before starting any task**, check `project_roadmap.md` and verify:
1. Is the feature free-tier or paid-only? Apply `@requires_plan` or a plan gate if it's paid.
2. Does it conflict with a planned architecture decision (e.g. billing gating, access tiers)?
3. Is it already marked ✅ done or in-progress somewhere?

Flag any conflicts before implementing rather than discovering them after.

**After every change**, review it as a senior engineer would in a PR. Check for:
- **Security**: Is any user input, file path, or data trusted without validation? Are secrets hardcoded or logged?
- **Simplicity**: Is this the simplest solution? Would a future reader understand it in 6 months?
- **Error handling**: What happens if this fails? Are errors caught and handled gracefully?
- **Data integrity**: Could this corrupt or lose data? (Especially anything touching the DB or file storage.)
- **Readability**: Are names clear? Is anything that needs a comment missing one?

**Stop and flag immediately** if you see:
- Writes or deletes without a safety check or rollback path
- Hardcoded file paths that will break outside local development
- Missing input validation on anything from a user or form
- Anything that exposes internal file structure or data through the front-end
- Secrets or API keys in code or logs

**Before saying a task is done**, briefly summarize: (1) what changed, and (2) anything noticed during review that could be a future issue.

## Commands

```bash
flask run                        # dev server
.venv/bin/pytest tests/ -v       # test suite (54 tests)
flask db upgrade                 # run pending migrations
flask db migrate -m "message"    # generate migration after model change
flask email-sequence --dry-run   # preview onboarding emails
flask digest --dry-run           # preview weekly digest
```

## Branches

- `main` — production, auto-deploys to Railway on push
- `design` — Jeffrey's branch; owns all CSS/visual/layout work

**Don't touch on `main`:** CSS files, visual/layout structure, font/color/spacing tokens, marketing copy.  
**Safe on `main`:** routes, models, forms, migrations, email templates, JS logic, config, infra.

## Architecture

**Blueprints:** `main` (routes.py), `billing`, `platform` (admin), `oauth`, `tf` (2FA), `api` (/api/v1)

**Multi-tenancy:** Each `Family` is one pod. Users belong to a family via `User.family_id` (home pod) and `UserPodMembership` (multi-pod, partially built). Always use `current_user.active_family_id` in queries — never bare `current_user.family_id`. The `active_family_id` property reads from session and validates membership.

**Access control decorators:** `@admin_required`, `@contributor_or_admin_required`, `@requires_plan` — all in routes.py.

**Billing plans:** `free` / `trial` / `paid` / `past_due`. Check access with `family_has_paid_access(family)` in billing.py. The `trial_ends_at` field doubles as grace-period start timestamp when `plan == 'past_due'` (set by the `invoice.payment_failed` webhook).

**CLI commands need request context** for `url_for(_external=True)` — wrap with `with _request_ctx():` (see commands.py).

## Key conventions

- User role: `is_admin=True` — NOT `role='admin'` (there is no `role` column on User)
- User contributor: `is_delegate=True`
- Photo storage: Cloudflare R2 via `upload_photo()` / `photo_url()` in storage.py
- Transactional email: Resend via `app/email.py`
- In-app notifications: `create_notification()` in notifications.py
- Notification preferences: `NotificationPreference.is_enabled(user_id, event_type)` — always check before sending emails
- WeatherKit forecast: `get_event_weather(event)` in weather.py — requires `WEATHERKIT_KEY_ID`, `WEATHERKIT_SERVICE_ID`, `WEATHERKIT_PRIVATE_KEY` env vars

## Template structure

Large templates are split into Jinja2 includes:
- `event_detail.html` includes partials from `templates/event/` (_meals, _assignments, _sleeping, _carpool, _rsvp, _edit_modal)

## Migrations

After changing models: `flask db migrate -m "description"` then `flask db upgrade`. If multiple heads appear: create a merge migration with `down_revision = ('head1', 'head2')` and empty up/downgrade functions.

## Tests

```bash
.venv/bin/pytest tests/ -v
```

Four test files: `test_smoke.py` (core paths + isolation), `test_billing.py` (Stripe webhooks + access gates), `test_notifications.py` (preferences route), `test_auth_2fa.py` (2FA pages + API JWT auth).

## Deployment

Railway auto-deploys on push to `main`. Migrations run automatically before gunicorn starts. Env vars set in Railway dashboard. Domain: swugl.com (Cloudflare DNS → Railway).

Registration currently closed (`REGISTRATION_OPEN=false` in Railway env vars). Flip to `true` when ready for real users.

## Code style

- Keep functions small and single-purpose
- Prefer clear over clever
- Add a short comment only when the "why" isn't obvious from the code
- Don't introduce new dependencies without a good reason — check if the standard library covers it first
