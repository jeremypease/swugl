# Swugl

Private family hub SaaS. Live at swugl.com. Flask + SQLAlchemy + PostgreSQL (Railway) / SQLite (local).

## Development workflow

**Never push directly to `main`.** All changes go through this sequence:

1. **Branch** — create a feature branch (`git checkout -b feature/short-name`)
2. **Build** — implement the change; run `.venv/bin/pytest tests/ -v` until all tests pass
3. **Self-review** — run through the review checklist below before opening a PR
4. **PR** — open a pull request and present the checklist results to Jeremy
5. **Jeremy merges** — Railway auto-deploys on merge to `main`

### Review checklist (run before every PR)

- [ ] All tests pass: `.venv/bin/pytest tests/ -v`
- [ ] Every new query uses `current_user.active_family_id` — not bare `current_user.family_id`
- [ ] New object lookups verify the returned object belongs to the active family before using it
- [ ] New routes that write data have `@login_required` + `@admin_required` or `@contributor_or_admin_required`
- [ ] Paid features have `@requires_plan` on the route (not just hidden in the template)
- [ ] No CSS, layout, font, color, or spacing changes — those go on the `design` branch
- [ ] If a model changed: migration file exists in `migrations/versions/` and `flask db upgrade` runs clean
- [ ] Any email send checks `NotificationPreference.is_enabled()` first

If any item is not clean, fix it before opening the PR.

## Before starting new feature work

Check the roadmap memory (`project_roadmap.md`) before implementing anything new. Verify:
1. Is the feature free-tier or paid-only? Apply `@requires_plan` or a plan gate if it's paid.
2. Does it conflict with a planned architecture decision (e.g. billing gating, access tiers)?
3. Is it already marked ✅ done or in-progress somewhere?

Flag any conflicts to the user before implementing rather than discovering them after.

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
