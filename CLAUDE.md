# Swugl

Private family hub SaaS. Live at swugl.com. Flask + SQLAlchemy + PostgreSQL (Railway) / SQLite (local).

## Before starting new feature work

Check the roadmap memory (`ROADMAP.md`) before implementing anything new. Verify:
1. Is the feature free-tier or paid-only? Apply `@requires_plan` or a plan gate if it's paid.
2. Does it conflict with a planned architecture decision (e.g. billing gating, access tiers)?
3. Is it already marked ✅ done or in-progress somewhere?

Also check for context:
- **GitHub issues** (`jeremypease/swugl`) — open issues and enhancement requests may already describe the work or reveal prior decisions.
- **Sentry** — check for active errors related to the area you're touching; a bug report may already exist for the problem.

Flag any conflicts to the user before implementing rather than discovering them after.

## Commands

```bash
flask run                        # dev server
.venv/bin/pytest tests/ -v       # test suite (54+ tests across 11 files)
flask db upgrade                 # run pending migrations
flask db migrate -m "message"    # generate migration after model change
flask email-sequence --dry-run   # preview onboarding emails
flask digest --dry-run           # preview weekly digest
flask rsvp-reminders --dry-run   # preview RSVP reminder emails
flask annual-events --dry-run    # preview annual event cloning
flask merge-persons --keep-id X --remove-id Y  # merge duplicate Person records
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
- Photo storage: Cloudflare R2 via `upload_photo()` / `photo_url()` in storage.py; uses presigned short-lived URLs for secure delivery
- Transactional email: Resend via `app/email.py`
- In-app notifications: `create_notification()` in notifications.py
- Notification preferences: `NotificationPreference.is_enabled(user_id, event_type)` — always check before sending emails
- WeatherKit forecast: `get_event_weather(event)` in weather.py — requires `WEATHERKIT_KEY_ID`, `WEATHERKIT_SERVICE_ID`, `WEATHERKIT_PRIVATE_KEY` env vars
- AI features: Claude Haiku via `app/ai.py` — `ANTHROPIC_API_KEY` required; used for greeting card drafts, poll suggestions, photo captions, digest narration

## Template structure

Large templates are split into Jinja2 includes:
- `event_detail.html` includes partials from `templates/event/` (_meals, _assignments, _sleeping, _carpool, _rsvp, _edit_modal, _comments)
- Platform admin templates live under `templates/platform/`
- `_macros.html` — shared Jinja2 macros used across templates

## Migrations

After changing models: `flask db migrate -m "description"` then `flask db upgrade`. If multiple heads appear: create a merge migration with `down_revision = ('head1', 'head2')` and empty up/downgrade functions. There are currently 56 migration versions.

## Tests

```bash
.venv/bin/pytest tests/ -v
```

11 test files (54+ tests total):

| File | What it covers |
|------|----------------|
| `test_smoke.py` | Core paths, family_id isolation, auth edge cases, open redirect prevention |
| `test_billing.py` | Stripe webhooks (checkout, subscription, payment failures), plan access gates |
| `test_notifications.py` | Notification preferences, digest generation, email channels |
| `test_auth_2fa.py` | 2FA pages, TOTP setup, WebAuthn passkeys, JWT API auth |
| `test_account_deletion.py` | Account deletion and cascading data cleanup |
| `test_event_payment.py` | Event payment config and records |
| `test_chat.py` | Group chat creation, messages, edits |
| `test_section_gating.py` | Paid tier access checks |
| `test_signed_urls.py` | R2 presigned URL generation |

`conftest.py` fixtures: `app`, `client`, `auth_client`, `other_auth_client`, `seeded_event_id`. Two seeded families (Pease, Other) for isolation testing. CSRF disabled; R2 and email disabled in tests.

## Models overview

**models.py** (~1115 lines, 40+ models):

*Users & Auth:* `User`, `UserCredential` (WebAuthn passkeys), `UserPodMembership`, `OAuthAccount`, `NotificationPreference`, `ApiTokenBlocklist`, `UserDevice` (push tokens), `CalendarToken`

*Family & People:* `Family` (billing plan, trial_ends_at, email sequence flags), `Person`, `ParentRelationship`, `SpouseRelationship`

*Events:* `Event` (recurring support, cover images, geo coords), `EventMeal`, `EventMealItem`, `EventRSVP`, `EventAssignment`, `AssignmentTask`, `EventSleepingSpot`, `EventComment`, `EventSurveyResponse`, `CarpoolOffer`, `Location`, `LocationSleepingSpot`

*Content:* `ChatMessage` (15min edit / 2min delete windows), `Announcement`, `AnnouncementReaction`, `Poll`, `PollOption`, `PollVote`, `GreetingCard`, `CardSignature`, `Album`, `Photo`, `PhotoTag`, `Document`, `Checklist`, `ChecklistItem`

*Billing:* `EventPaymentConfig`, `EventPaymentRecord`, `FamilyPayoutAccount`

*Platform Admin:* `PlatformAuditLog`, `SupportNote`, `SystemAnnouncement`, `AppVersion`, `SystemConfig`

**Constants:** `PARENT_ROLES` (8 types), `ASSIGNMENT_CATEGORIES` (6), `SPOT_TYPES` (6), `DOCUMENT_CATEGORIES` (5), `NOTIFICATION_EVENTS` (8 event types)

## API (v1)

REST endpoints under `/api/v1/` for mobile clients. 7 modules in `app/api/`:

- `auth.py` — JWT login/logout, refresh, register
- `chat.py` — Chat message CRUD
- `events.py` — Event listing and detail
- `members.py` — Family member listing
- `notifications.py` — Notification listing and mark-read
- `push.py` — Device token registration for push notifications
- `version.py` — App version check endpoint

JWT tokens use `Flask-JWT-Extended`. Revocation via `ApiTokenBlocklist`.

## App modules

| File | Purpose |
|------|---------|
| `routes.py` | ~4760 lines, ~180 routes, main blueprint |
| `models.py` | ~1115 lines, all SQLAlchemy models |
| `forms.py` | WTForms (registration, events, billing) |
| `commands.py` | 5 Flask CLI scheduled commands |
| `storage.py` | R2 photo upload/download, presigned URLs, HEIC→JPG, thumbnails |
| `email.py` | Resend transactional emails (welcome, trials, digests, RSVP reminders) |
| `notifications.py` | In-app notification creation and weekly digest assembly |
| `billing.py` | Stripe webhooks, subscription lifecycle, plan gating |
| `weather.py` | WeatherKit API for event forecasts |
| `ai.py` | Claude Haiku calls for card drafts, poll suggestions, photo captions, digest narration |
| `account.py` | Account deletion with cascading data cleanup |
| `oauth.py` | Google & Apple Sign-In via authlib |
| `two_factor.py` | WebAuthn passkeys (webauthn 2.7.1) + TOTP (pyotp + QR codes) |
| `platform_routes.py` | Platform admin: pod browsing, support notes, audit log, system announcements, app versions |

## Environment variables

Required env vars (see `.env.example` for the full list):

```
SECRET_KEY                    # Flask secret key
DATABASE_URL                  # Empty → SQLite; set for PostgreSQL
FLASK_ENV                     # development / production
REGISTRATION_OPEN             # true / false
RESEND_API_KEY                # Transactional email
RESEND_FROM_EMAIL
STRIPE_SECRET_KEY
STRIPE_PUBLISHABLE_KEY
STRIPE_WEBHOOK_SECRET
STRIPE_PRICE_ID               # Monthly plan price
GOOGLE_CLIENT_ID
GOOGLE_CLIENT_SECRET
R2_ACCOUNT_ID
R2_ACCESS_KEY_ID
R2_SECRET_ACCESS_KEY
R2_BUCKET_NAME
R2_PUBLIC_URL
ANTHROPIC_API_KEY             # For AI features (ai.py)
WEATHERKIT_KEY_ID
WEATHERKIT_SERVICE_ID
WEATHERKIT_PRIVATE_KEY
SENTRY_DSN                    # Error tracking
REDIS_URL                     # Rate limiting (Flask-Limiter)
JWT_SECRET_KEY                # API JWT tokens
WEBAUTHN_RP_ID                # Relying party ID for passkeys
WEBAUTHN_RP_NAME
APPLE_TEAM_ID                 # Apple Sign-In
APPLE_KEY_ID
APPLE_PRIVATE_KEY
```

## AI features (ai.py)

Model: `claude-haiku-4-5-20251001` via `anthropic` SDK. Four functions:

- `draft_card_message(card, user)` — Draft greeting card message (`POST /cards/ai-draft`)
- `suggest_poll(family, user)` — Suggest poll questions and options (`POST /polls/ai-suggest`)
- `suggest_photo_caption(photo, user)` — Auto-caption a photo (`POST /photos/<id>/ai-caption`)
- `narrate_digest(digest_data)` — Narrative intro for weekly digest email

All AI routes require authentication and use `ANTHROPIC_API_KEY`. The Haiku model is chosen for low latency and cost.

## Paid feature gating

`@requires_plan` decorator gates paid features. Currently paid-only:
- Event meals, assignments, and sleeping spot routes
- Event payment collection (Stripe Connect)
- Likely more per ROADMAP.md

Always check `ROADMAP.md` and use `family_has_paid_access(family)` for inline checks.

## 2FA / Auth security

- WebAuthn passkeys: `UserCredential` model, registration/auth flow in `two_factor.py`
- TOTP: pyotp + QR codes, stored as `user.totp_secret` (encrypted)
- OAuth: Google and Apple Sign-In via authlib, linked via `OAuthAccount`
- Open redirect prevention: login redirect validated strictly against allowed hosts
- Flask debug mode requires explicit `FLASK_DEBUG=1`

## Deployment

Railway auto-deploys on push to `main`. Migrations run automatically before gunicorn starts (`Procfile`: `flask db upgrade && gunicorn family:app`). Env vars set in Railway dashboard. Domain: swugl.com (Cloudflare DNS → Railway).

Registration currently closed (`REGISTRATION_OPEN=false` in Railway env vars). Flip to `true` when ready for real users.

**CI/CD:**
- GitHub Actions: pytest runs on every push (`.github/workflows/ci.yml`)
- CodeQL security scanning (`.github/workflows/codeql.yml`)
- Dependabot: automatic dependency updates
