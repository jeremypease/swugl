# Peavines / OurPeaPod — Product Roadmap

## Vision

**OurPeaPod** is a private family hub — a place for families to stay connected, plan together, and preserve their shared history. The product launches as a hosted SaaS at `ourpeapod.com` where any family can create their own "pod" in minutes.

---

## Phase 0 — Foundation Cleanup (Pre-Launch)
*Complete before any public-facing work.*

### Goals
- Ensure all existing features are solid, tested, and production-ready
- Establish the multi-tenant data model that everything else depends on

### Work Items
- [ ] Audit all database queries for `family_id` scoping — confirm no cross-family data leakage
- [ ] Add `family_id` indexes on all relevant tables
- [ ] Harden auth: rate-limit login, add email verification on registration
- [ ] Confirm all admin-only routes enforce `@admin_required` consistently
- [ ] Write a basic smoke-test suite for the critical paths (login, profile edit, event CRUD)

---

## Phase 1 — Public Launch: Multi-Tenant SaaS
*The architectural inflection point. Every other phase depends on this.*

### 1A — Self-Serve Pod Creation
**Route:** `ourpeapod.com/signup` → creates a new family + admin user in one flow.

- [ ] New `signup.html` flow: family name → your info → email verification → dashboard
- [ ] `Family` model gains: `slug` (URL-safe name, e.g. `pease`), `plan` (free/paid), `stripe_customer_id`, `created_at`
- [ ] Optional vanity URL: `ourpeapod.com/p/pease` or subdomain `pease.ourpeapod.com` (later)
- [ ] Onboarding checklist on first login (add members, set patriarch/matriarch, upload a photo)

### 1B — Pricing & Billing
**Tiers:**

| Feature | Free Pod | Family Plan ($X/mo) |
|---------|----------|---------------------|
| Members | Up to 10 | Unlimited |
| Photos | 1 GB storage | 25 GB storage |
| Events | 3 active | Unlimited |
| Chat | — | ✓ |
| Calendar feed | — | ✓ |
| Mobile app | — | ✓ |

- [ ] Integrate **Stripe** (Checkout + Customer Portal)
- [ ] `billing.py` — Stripe webhook handler (subscription created, updated, canceled, payment failed)
- [ ] Feature gate decorator `@requires_plan('paid')` for paid-only routes
- [ ] `/billing` page for admins: current plan, upgrade/downgrade, payment history
- [ ] Grace period (7 days) on failed payment before downgrading

### 1C — Public Landing Page
**URL:** `ourpeapod.com` — for visitors who aren't logged in.

- [ ] Hero section: headline, subheadline, "Start your pod free" CTA
- [ ] Features section: family tree, events, photos, chat, calendar (use product screenshots)
- [ ] Pricing section: Free vs. paid tier comparison table
- [ ] Footer: About, Privacy Policy, Terms of Service, Contact
- [ ] Separate CSS from the app CSS — landing page gets its own stylesheet
- [ ] Route logic: `/` → redirect to `/home` if logged in, else serve landing page
- [ ] Privacy Policy and Terms of Service pages (required before taking payments)

---

## Phase 2 — Communication & Content
*Deepens engagement once families are onboarded.*

### 2A — Family Chat
A simple threaded group chat scoped to the family. Not a replacement for iMessage — a place for family-specific conversation that stays in the pod.

- [ ] `Message` model: `id`, `family_id`, `person_id`, `body`, `created_at`, `thread_id` (nullable)
- [ ] Real-time delivery via **Flask-SocketIO** (WebSocket) — fall back to polling for older clients
- [ ] UI: chat panel accessible from sidebar, shows last 100 messages, infinite scroll up
- [ ] Threads: reply to any message to start a thread (collapsed by default)
- [ ] Notifications: badge on sidebar icon when there are unread messages
- [ ] Push notifications on mobile (Phase 3 dependency)
- [ ] Paid tier only

### 2B — Recipes & Gift Ideas
Structured content types that members can create, browse, and save.

**Recipes**
- [ ] `Recipe` model: title, description, ingredients (JSON array), steps (text), photo, author, tags, created_at
- [ ] `/recipes` list + `/recipes/<id>` detail + add/edit for any member
- [ ] Tag filtering (e.g. "Grandma's recipes", "Holiday", "Vegetarian")
- [ ] Print-friendly recipe view

**Gift Ideas**
- [ ] `GiftIdea` model: title, description, URL, price_range, for_person_id, added_by_id, is_claimed, claimed_by_id
- [ ] `/gifts` — each member can see what others have suggested for them (but not who claimed it)
- [ ] Claim a gift idea (hidden from the person it's for)
- [ ] Admins can see full claim status

### 2C — Event Planning Improvements
Building on the existing Events feature.

- [ ] RSVP system: Yes / No / Maybe per person, with headcount display
- [ ] RSVP deadline + reminder email X days before event
- [ ] Event comments/discussion thread
- [ ] Cover photo for events (upload or choose from albums)
- [ ] Recurring events (annual — e.g. "Pease Christmas" auto-creates each year)
- [ ] Email/push notifications when a new event is created
- [ ] Event templates (save a past event as a template for next year)

---

## Phase 3 — Platform & Integrations
*Extends the pod beyond the browser.*

### 3A — Shared Family Calendar Feed
An iCal/CalDAV feed that members subscribe to in Apple Calendar, Google Calendar, or Outlook.

- [ ] `GET /family/calendar.ics` — authenticated via a secret token in the URL (not session, since calendar apps don't send cookies)
- [ ] `CalendarToken` model: `person_id`, `token` (UUID), `created_at` — one per person
- [ ] Feed includes all family events in iCalendar format (RFC 5545)
- [ ] `/profile` → "Calendar" tab: shows subscribe link + instructions for Apple/Google/Outlook
- [ ] Regenerate token button (invalidates old link)
- [ ] Events created/edited in the app auto-update in subscribed calendars (clients poll the feed)
- [ ] Paid tier only

### 3B — Progressive Web App (PWA) — Stepping Stone to Native
Before building native apps, ship a PWA to get mobile-friendly fast.

- [ ] `manifest.json` with app name, icons, theme color, `display: standalone`
- [ ] Service worker: cache shell + static assets, offline fallback page
- [ ] "Add to Home Screen" prompt (iOS Safari / Android Chrome)
- [ ] Push notification support via Web Push API (requires HTTPS + service worker)

### 3C — iPhone App (Native)
*Depends on 3B PWA being live and product being stable.*

- [ ] **React Native** (shares logic with Android) or native SwiftUI
- [ ] Auth: token-based (JWT) — add `/api/auth/token` endpoint to Flask backend
- [ ] Core screens: Home, Events, Chat, Members, Profile
- [ ] Push notifications via APNs
- [ ] App Store submission: Apple Developer account ($99/yr), review process (~1-2 weeks)

### 3D — Android App (Native)
- [ ] Shared React Native codebase with iOS, or native Kotlin
- [ ] Push notifications via FCM
- [ ] Google Play Store submission

---

## Phase 4 — Support & Platform Operations
*OurPeaPod (the company) needs its own tools to operate the platform, help users, and stay informed about what's happening across all pods.*

### 4A — In-App Support Channel
The simplest path for users to get help without leaving the app.

- [ ] **"Get help" link** in sidebar footer — visible to all authenticated users
- [ ] **Support request form** at `/support`: subject, description, category (billing, technical, account, other)
  - Routes to `hello@ourpeapod.com` via SendGrid
  - Automatically includes: user email, family name, pod ID, browser/OS (from user-agent)
  - Confirms submission with a flash message
- [ ] **Help center link** — link out to a Notion-based FAQ or help docs (lightweight, no custom build needed initially)
- [ ] **Status page** — a simple hosted status page (Instatus or Statuspage.io) users can check during outages

### 4B — Platform Admin Panel
A separate admin area for OurPeaPod staff — distinct from the family-level admin that pod admins already have.

**Access model:**
- New `is_platform_admin` boolean on the `User` model
- Separate decorator `@platform_admin_required`
- All platform admin actions are written to an audit log
- Platform admins can only be designated by directly editing the database (no UI for this — intentional)

**Dashboard** — `/platform/dashboard`
- Total pods, total users, total active subscriptions
- New pods this week/month
- Recent support requests
- Error rate (from logs)

**Pod management** — `/platform/pods`
- List all families: name, member count, plan, created date, last activity
- Search by family name or admin email
- View any pod's details: members, events, storage used, billing status

**Support mode** — `/platform/pods/<id>/support-view`
- Read-only view of a pod as the family admin sees it — to reproduce issues without disrupting the family
- Every entry into support mode is logged: who accessed, when, why (required reason field)
- Cannot make changes — read-only strictly enforced
- Visible banner: "You are viewing this pod in support mode"

**User lookup** — `/platform/users`
- Find any user across all pods by email
- View account status, pod membership, last login
- Actions: resend verification email, reset password link, unlock account

**Billing management** — `/platform/billing` *(after Phase 1B Stripe is live)*
- View subscription status for any pod
- Apply manual credit or extend trial
- Cancel subscription on behalf of a user

**System announcements** — `/platform/announce`
- Push a notice to all pods (e.g. "Scheduled maintenance Saturday 2am")
- Shown as a dismissible banner in all pods until dismissed or expired

### 4C — Support Documentation
- [ ] Help center (Notion or similar) covering: getting started, inviting members, events, photos, billing
- [ ] In-app contextual help tooltips on complex features (event planning, family tree)
- [ ] `hello@ourpeapod.com` inbox monitored and routed

---

## Phase 5 — Growth & Monetization
*After the product is stable and has real users.*

- [ ] **Referral program**: "Invite another family, get 1 month free"
- [ ] **Family history export**: download your entire pod as a PDF/ZIP archive
- [ ] **Anniversary & birthday reminders**: email digest (weekly "coming up this week")
- [ ] **AI features** (stretch): auto-generate family newsletter from recent activity, suggest gift ideas from past events
- [ ] **Admin analytics dashboard**: member activity, storage used, events per month
- [ ] **Enterprise / extended family tier**: multiple branches, branch admins, cross-branch events

---

## Security

OurPeaPod stores personal information — names, birthdays, addresses, family relationships, photos. Security is not a phase; it's a constant. This section tracks what's in place and what still needs doing.

### Already in place
- CSRF protection (Flask-WTF) on all forms
- Password hashing (Werkzeug `pbkdf2:sha256`)
- `family_id` scoping on all queries — no cross-family data leakage
- Rate limiting: 20/min on login POST, 10/hr on registration and password reset
- Session cookie flags: `Secure`, `HttpOnly`, `SameSite=Lax` in production
- Open redirect prevention in login flow
- Email enumeration prevention in forgot-password (always shows same message)
- Token expiry on invitations, email verification, and password resets
- SQL injection protection via SQLAlchemy ORM
- HTTP security headers: `X-Frame-Options`, `X-Content-Type-Options`, `Referrer-Policy`, `Permissions-Policy`, `Content-Security-Policy`
- Uploaded family photos protected — `/static/uploads/` requires authentication
- File upload extension whitelist (`jpg`, `jpeg`, `png`, `webp`, `gif`, `heic`)
- UUIDs for all uploaded filenames (no guessable paths)
- `SECRET_KEY` required from environment — app refuses to start without it

### Pre-launch must-do
- [ ] **Move uploads off the local filesystem** — photos in `static/uploads/` will vanish on Railway redeploy. Switch to Cloudflare R2 or AWS S3 with private buckets and short-lived signed URLs. Signed URLs are also stronger than the current auth-gateway approach (no URL sharing possible).
- [ ] **File content validation** — currently checks extension only. Add magic-byte validation (e.g. `python-magic`) to confirm the file is actually an image before saving.
- [ ] **Password strength enforcement** — add minimum 10-character requirement and reject the 100 most common passwords.
- [ ] **Dependency vulnerability scanning** — add GitHub Dependabot or run `pip audit` in CI before each deploy.
- [ ] **Secure token comparison** — use `hmac.compare_digest()` when validating invitation/reset tokens to prevent timing attacks.

### Near-term (before paid tier)
- [ ] **Audit log** — record admin actions (approving members, changing roles, deleting content) to a log table. Families deserve to know who did what.
- [ ] **Rate limiting on invitation acceptance** — the `/register/invite/<token>` endpoint has no rate limit; a stolen token could be brute-forced (low risk due to token length, but worth closing).
- [ ] **Account lockout** — after N failed logins from the same IP, lock for X minutes. Flask-Limiter handles IP-level limits; add per-account lockout on top.
- [ ] **Email-change verification** — if a user changes their email, require re-verification of the new address before it takes effect.

### Longer term (before mobile apps / public API)
- [ ] **JWT token security** — API tokens for mobile apps need short expiry, rotation, and revocation.
- [ ] **PII field encryption at rest** — sensitive fields (phone, address) stored in plaintext in the DB. Encrypt with a key stored in env for an extra layer against DB dump attacks.
- [ ] **Penetration test** — before the paid tier goes live, have a third party attempt to break in.
- [ ] **GDPR / data deletion** — implement a formal "delete my data" flow that removes all PII and family tree entries for a user.
- [ ] **Backup verification** — test that Railway's automatic Postgres backups are restorable.

---

## Technical Architecture Notes

### Multi-Tenancy
All queries must include `family_id`. The pattern:
```python
# Always scope to current family
event = Event.query.filter_by(id=event_id, family_id=current_user.family_id).first_or_404()
```
Introduce a `FamilyScoped` mixin or query helper to enforce this at the model layer.

### API Layer (required for mobile apps)
- Add `/api/v1/` Blueprint with JSON responses
- Token auth (`Authorization: Bearer <jwt>`) alongside existing session auth
- Rate limiting on all API routes (Flask-Limiter)

### Storage
- Current: local filesystem for photos
- Target: **S3-compatible object storage** (AWS S3 or Cloudflare R2) before launch
- CDN in front of photos for performance

### Email
- Current: likely SMTP or none
- Target: **SendGrid** or **Postmark** for transactional email (invites, event reminders, billing receipts)

### Hosting
- **Target:** Railway (PaaS) — deploy from GitHub, supports WebSockets, no server management
- **DNS:** GoDaddy → Railway (CNAME to Railway's provided domain)
- **Hostgator:** Can be cancelled once Railway is live; not needed
- HTTPS is required for PWA + Web Push (Railway provides it automatically)

---

## Recommended Execution Order

1. **Go Live** — Get the existing app running on ourpeapod.com
2. **Phase 0** — Harden what exists (1–2 weeks) ✓ done
3. **Phase 1C** — Landing page (1 week) ✓ done
4. **Phase 1A** — Self-serve pod signup (2–3 weeks)
5. **Phase 1B** — Stripe billing (2 weeks)
6. **Phase 4A** — In-app support form (1 day — do this before taking any money)
7. **Phase 4B** — Platform admin panel (2–3 weeks — needed once multiple pods exist)
8. **Phase 3A** — Calendar feed (1 week, quick win)
9. **Phase 2C** — Event improvements (2 weeks)
10. **Phase 2A** — Chat (3–4 weeks)
11. **Phase 2B** — Recipes & gifts (2 weeks)
12. **Phase 3B** — PWA (1 week)
13. **Phase 3C/3D** — Native apps (2–3 months)
14. **Phase 5** — Growth & monetization (ongoing)

---

## Go Live Plan

*Everything needed to get the app running at ourpeapod.com. Target platform: **Railway**.*

### What We Have
- **Domain:** `ourpeapod.com` registered at GoDaddy
- **DNS:** Currently pointing to Hostgator — will be redirected to Railway
- **Hostgator Baby Plan:** Not needed once Railway is live; can be cancelled
- **App:** Flask + SQLite, currently running locally

### Why Railway (not Hostgator)
Hostgator shared hosting can't support WebSockets, which are required for Phase 2A (chat). Starting there and migrating later is the same work done twice. Railway handles deploys from GitHub, provides SSL automatically, supports WebSockets, and has no server management overhead. ~$5–20/mo depending on usage.

---

### Step 1 — Database: Switch from SQLite to PostgreSQL

**This is the most important pre-launch change.** Railway's filesystem is ephemeral — every redeploy wipes local files, including a SQLite database. The fix is to use Railway's built-in PostgreSQL plugin, which is a persistent managed database.

- [ ] Add `psycopg2-binary` to `requirements.txt`
- [ ] Update `config.py` to read `DATABASE_URL` from environment:
  ```python
  SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL', 'sqlite:///local.db')
  ```
  Railway injects `DATABASE_URL` automatically when a Postgres plugin is attached.
- [ ] Test locally with SQLite still (no change to local dev workflow)
- [ ] After Railway deploy: run `flask db upgrade` once via Railway's shell to initialize the schema

> **Note on photos:** Uploaded photo files face the same ephemeral filesystem problem. Short-term workaround: store photos in the database as blobs (not ideal) or skip uploads until Cloudflare R2 / S3 is wired up (see Technical Architecture Notes). For the initial launch with just the Pease family, this can be deferred.

---

### Step 2 — Prepare the App for Production

- [ ] Create `config.py` with a `ProductionConfig` class:
  ```python
  class ProductionConfig:
      SECRET_KEY = os.environ.get('SECRET_KEY')   # never hardcode
      DEBUG = False
      SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL')
      SESSION_COOKIE_SECURE = True
      SESSION_COOKIE_HTTPONLY = True
      WTF_CSRF_ENABLED = True
  ```
- [ ] Create `.env.example` (committed, no real values) and ensure `.env` is in `.gitignore`
- [ ] Add `gunicorn` to `requirements.txt`
- [ ] Create `Procfile` in the project root:
  ```
  web: gunicorn run:app
  ```
- [ ] Confirm `requirements.txt` is up to date: `pip freeze > requirements.txt`

---

### Step 3 — Push to GitHub

Railway deploys from a GitHub repository.

- [ ] Create a GitHub repo (private) if one doesn't exist
- [ ] Push the project: `git remote add origin <repo-url> && git push -u origin main`
- [ ] Confirm `.env`, `instance/`, and `*.db` files are in `.gitignore` and not committed

---

### Step 4 — Set Up Railway

- [ ] Sign up at [railway.app](https://railway.app) (GitHub login)
- [ ] New Project → "Deploy from GitHub repo" → select the repo
- [ ] Add a **PostgreSQL plugin** to the project (Railway dashboard → + New → Database → PostgreSQL)
  - Railway automatically sets `DATABASE_URL` in the app's environment
- [ ] Set remaining environment variables in Railway → Variables:
  - `SECRET_KEY` — generate with `python3 -c "import secrets; print(secrets.token_hex(32))"`
  - `FLASK_ENV=production`
  - `MAIL_*` keys (once email is configured in Step 5)
- [ ] Railway will auto-detect the `Procfile` and deploy on every push to `main`
- [ ] Open the Railway-provided URL (e.g. `ourpeapod-production.up.railway.app`) and verify it works
- [ ] Run the initial migration via Railway shell:
  ```bash
  flask db upgrade
  ```

---

### Step 5 — Email

- [ ] Sign up for **SendGrid** free tier (100 emails/day free) or **Postmark**
- [ ] Add to Railway environment variables:
  ```
  MAIL_SERVER=smtp.sendgrid.net
  MAIL_PORT=587
  MAIL_USE_TLS=true
  MAIL_USERNAME=apikey
  MAIL_PASSWORD=<sendgrid_api_key>
  MAIL_DEFAULT_SENDER=noreply@ourpeapod.com
  ```
- [ ] Verify `noreply@ourpeapod.com` as a sender in SendGrid (requires DNS TXT records in GoDaddy)
- [ ] Test: send a member invite from the live site and confirm delivery

---

### Step 6 — Custom Domain & DNS

- [ ] In Railway → Settings → Domains → Add custom domain: `ourpeapod.com`
- [ ] Railway will show a CNAME target (e.g. `abc123.up.railway.app`)
- [ ] In **GoDaddy** DNS:
  - Remove the current A record pointing to Hostgator
  - Add CNAME: `www` → Railway's provided domain
  - For the apex domain (`ourpeapod.com`), GoDaddy supports ALIAS/ANAME — add that pointing to Railway, or redirect apex → www
- [ ] Railway provisions SSL (Let's Encrypt) automatically once DNS propagates (~5–30 min)
- [ ] Verify `https://ourpeapod.com` loads with a valid SSL cert
- [ ] Cancel Hostgator Baby Plan once confirmed working

---

### Step 7 — Smoke Test

Before sharing the URL with anyone:

- [ ] Register a new account, log in, navigate all main pages
- [ ] Admin: create a family member, invite via email, approve
- [ ] Create an event with all three sections (meals, assignments, sleeping)
- [ ] Upload a photo to an album
- [ ] Post an announcement
- [ ] Log out, log back in
- [ ] Test on mobile (Safari iOS + Chrome Android)
- [ ] Check browser console for JS errors
- [ ] Confirm no stack traces or `DEBUG` output visible to users

---

### Step 8 — Ongoing Deployment Workflow

Once live, deploying is just:

```bash
git push origin main
```

Railway detects the push and redeploys automatically. Migrations still need a manual step:

```bash
# In Railway dashboard → shell, or via Railway CLI:
railway run flask db upgrade
```

- [ ] Install Railway CLI: `npm i -g @railway/cli` (for running one-off commands locally against prod)
- [ ] Document this in a `DEPLOY.md` file

---

*Last updated: 2026-05-19*
