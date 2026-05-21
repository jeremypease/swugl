# Peavines / OurPeaPod — Product Roadmap

## Vision

**OurPeaPod** is a private, AI-native family hub — a place for families to stay connected, plan together, and preserve their shared history. The product launches as a hosted SaaS at `ourpeapod.com` where any family can create their own "pod" in minutes. AI is woven into the product from day one, not bolted on later — every feature should consider how intelligence can reduce effort and surface things families didn't know to look for.

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
- [ ] **Wire up Cloudflare R2 or AWS S3 for photo storage** — Railway's filesystem is ephemeral; uploaded photos will be lost on every redeploy. This must be done before any non-test family uses the app, not just before "launch." See Security → Pre-launch must-do for details.

---

## Phase 1 — Public Launch: Multi-Tenant SaaS
*The architectural inflection point. Every other phase depends on this.*

### 1A — Self-Serve Pod Creation
**Route:** `ourpeapod.com/signup` → creates a new family + admin user in one flow.

- [ ] New `signup.html` flow: family name → your info → email verification → dashboard
- [ ] `Family` model gains: `name` (display only — no uniqueness constraint; two families can both be "The Smith Family"), `account_id` (system-generated short unique code, e.g. `pod_a1b2c3` — used for URLs and internal references), `plan` (free/paid), `stripe_customer_id`, `created_at`
- [ ] All pod-scoped URLs use `account_id`, never the display name — e.g. `/p/pod_a1b2c3/events`
- [ ] `account_id` displayed in family settings (admin only) — e.g. "Your pod ID: pod_a1b2c3" — so admins can quote it when contacting support to identify their account unambiguously
- [ ] Onboarding checklist on first login (add members, set patriarch/matriarch, upload a photo)
- [ ] **Onboarding email sequence** — activation lives here, not just in the checklist:
  - Day 0: Welcome email — what to do first (add your first member, set up your tree)
  - Day 3: Nudge if no members have been added — "Your pod is quiet, here's how to invite family"
  - Day 7: Feature highlight — "Did you know you can plan events and assign tasks?"
  - Day 30 (trial end): Upgrade prompt with what they'll lose on free tier
- **Schema note:** The current model assumes one user belongs to one family. A real use case — someone married into a second family — will require a `user_families` junction table. Flag this before Phase 1A schema is finalized so it doesn't require a painful migration later.

### 1B — Pricing & Billing
**Tiers:**

| Feature | Free Pod | Family Plan |
|---------|----------|-------------|
| Price | Free | $9/mo · or · $90/yr *(save 2 months)* |
| Members | Up to 10 | Unlimited |
| Photos | 1 GB storage | 25 GB storage |
| Events | 3 active | Unlimited |
| Chat | — | ✓ |
| Calendar feed | — | ✓ |
| AI features | — | ✓ |
| Mobile app | — | ✓ |

Annual billing offers ~17% off (equivalent to 2 months free). Price anchors are placeholders — validate with early customers before locking in.

**Member cap consideration:** The 10-member limit may frustrate large families (30+ cousins) before they've seen enough value to upgrade. If the first wall they hit is "you can't add your aunt," they bounce rather than pay. Consider whether storage or AI is a less alienating gate, or raise the free cap to 15–20. Decide before 1B ships.

- [ ] **30-day free trial of paid tier on signup** — families experience the full product before hitting any wall; present the upgrade prompt at trial end, not at random feature blocks
- [ ] "X days remaining in your trial" banner in the app header during trial
- [ ] Trial-to-paid conversion email at Day 25 (5-day warning) and Day 30 (trial ended)
- [ ] Integrate **Stripe** (Checkout + Customer Portal)
- [ ] `billing.py` — Stripe webhook handler (subscription created, updated, canceled, payment failed)
- [ ] Support both monthly and annual billing intervals in Stripe (two Price objects per product)
- [ ] Feature gate decorator `@requires_plan('paid')` for paid-only routes
- [ ] `/billing` page for admins: current plan, billing interval, upgrade/downgrade, payment history
- [ ] Annual plan: show "you're saving $18/yr" on upgrade confirmation
- [ ] Grace period (7 days) on failed payment before downgrading
- [ ] **Stripe Tax** — enable at billing setup, before the first real payment is processed. SaaS subscriptions are taxable in ~30 US states and in the EU (VAT). Stripe Tax automates collection and reporting for 0.5% per transaction — far cheaper than the penalty for getting it wrong. Add `automatic_tax: {enabled: true}` to Checkout sessions and confirm the `ourpeapod.com` tax origin address is set in the Stripe dashboard.

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

### 2A — Notification Preferences *(build this first — before 2B, 2C, or 2D ship)*
Without a unified notification system, every new feature that sends email or push notifications becomes its own ad-hoc implementation. Users get spammed or go dark. Both cause churn.

- [ ] `NotificationPreference` model: `user_id`, `event_type` (new_event, rsvp_reminder, new_member, chat_message, announcement, digest), `channel` (email, push), `enabled` (bool)
- [ ] `/profile/notifications` settings page — one toggle per event type per channel
- [ ] Default preferences set at signup (sensible defaults: digest on, per-message chat off)
- [ ] All notification-sending code goes through a central `notify(user, event_type, payload)` helper that checks preferences before sending
- [ ] Weekly digest email: upcoming events, recent activity, birthdays this week (replaces per-event spam for lower-frequency users)

### 2B — Family Chat
A simple threaded group chat scoped to the family. Not a replacement for iMessage — a place for family-specific conversation that stays in the pod.

- [ ] `Message` model: `id`, `family_id`, `person_id`, `body`, `created_at`, `thread_id` (nullable)
- [ ] Real-time delivery via **Flask-SocketIO** (WebSocket) — fall back to polling for older clients
- [ ] UI: chat panel accessible from sidebar, shows last 100 messages, infinite scroll up
- [ ] Threads: reply to any message to start a thread (collapsed by default)
- [ ] Notifications: badge on sidebar icon when there are unread messages
- [ ] Push notifications on mobile (Phase 3 dependency)
- [ ] Paid tier only

### 2C — Recipes & Gift Ideas
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

### 2D — Event Planning Improvements
Building on the existing Events feature.

- [ ] RSVP system: Yes / No / Maybe per person, with headcount display
- [ ] RSVP deadline + reminder email X days before event
- [ ] Event comments/discussion thread
- [ ] Cover photo for events (upload or choose from albums)
- [ ] Recurring events (annual — e.g. "Pease Christmas" auto-creates each year)
- [ ] Email/push notifications when a new event is created
- [ ] Event templates (save a past event as a template for next year)
- [ ] **Timezone support** — store event times with timezone; display in each member's local time for distributed families

### 2E — GEDCOM Import
Many families already have their tree in Ancestry.com, MyHeritage, or FamilySearch. Re-entering every relationship manually is a hard sell. GEDCOM import removes the biggest adoption barrier for genealogy-minded families.

- [ ] File upload at `/family/import` (admin only) — accepts `.ged` / `.gedcom` files
- [ ] Parser: extract individuals (INDI records) and family units (FAM records) into People + relationships
- [ ] Preview page before import: "Found 47 people, 18 families — review before importing"
- [ ] Conflict resolution: if a person already exists (matched by name + birth year), prompt to merge or skip
- [ ] Import log: show what was created, what was skipped, what needs manual review
- [ ] GEDCOM export as the inverse — lets families take their data with them (also satisfies the data portability / GDPR right-to-portability requirement)

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
  - Automatically includes: user email, family name, `account_id`, browser/OS (from user-agent) — the `account_id` is what support uses to look up the pod in the platform admin, not the display name
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

## Phase 6 — AI Features
*AI is woven into OurPeaPod from day one — not bolted on later. All AI features are paid tier only. The goal is to make the product feel intelligent without requiring families to think about AI at all.*

**Platform:** Built on the **Claude API** (Anthropic SDK). Model selection: default to the latest Claude Sonnet for a balance of quality and cost; surface model choice in platform admin for tuning.

### 6A — Family Newsletter Generator
Every family has a story — most of it gets lost in group chats. The newsletter generator pulls together recent events, announcements, new members, and milestones into a readable family update.

- [ ] Aggregate activity from the past 30 days: new events, RSVPs, announcements, photos added, new members
- [ ] `POST /ai/newsletter/preview` — sends activity summary to Claude, returns a draft newsletter in markdown
- [ ] Admin reviews and edits the draft before sending
- [ ] One-click send to all family members via email (SendGrid)
- [ ] Newsletter archive at `/newsletters` — past issues stored and viewable in the app
- [ ] Paid tier only

### 6B — Smart Event Planning
Planning a family reunion means dozens of decisions. AI reduces the cognitive load by suggesting what to bring, who to assign tasks to, and what meals have worked before.

- [ ] **Task suggestions**: when creating an event, AI suggests a task list based on event type, size, and location (e.g. "It's a 3-day camping trip for 18 people — here are 12 suggested tasks")
- [ ] **Meal suggestions**: suggest dishes and quantities based on group size and season, drawing on the family's Recipe collection if available
- [ ] **Sleeping arrangement draft**: given a guest list and available rooms, suggest an arrangement
- [ ] All suggestions are editable — AI proposes, admin decides
- [ ] Paid tier only

### 6C — Family Tree Q&A
The family tree is a graph — AI can traverse it in natural language so members don't have to.

- [ ] Input: natural language question — "How is Harold related to me?", "Who are all of Grandma's grandchildren?"
- [ ] Backend: serialize the relevant portion of the family tree to a structured prompt, send to Claude
- [ ] Response: plain English answer + the relationship path ("Harold is your father's brother — your uncle")
- [ ] Accessible from the family tree page and member profiles
- [ ] Paid tier only

### 6D — Photo Caption Assistant
Photos uploaded without captions are hard to search and eventually lose context. AI suggests captions based on available metadata.

- [ ] When a photo is uploaded or viewed without a caption, show "Suggest a caption" button
- [ ] Claude receives: person names tagged in the photo, event name (if from an event album), date, album name
- [ ] Returns 2–3 caption options for the uploader to choose or edit
- [ ] Batch captioning: admin can run "Caption uncaptioned photos" on an album
- [ ] Paid tier only

### 6E — Biography Draft for Member Profiles
Every family member has a story. Most profiles sit mostly empty. AI can draft a starting biography from the structured data already in the profile.

- [ ] "Draft a bio" button on member profiles (visible to admins and the member themselves)
- [ ] Claude receives: name, birth year, hometown, education, occupation, family relationships, notable life events (from timeline if built)
- [ ] Returns a short paragraph biography — warm, personal, suitable for a family record
- [ ] Member reviews, edits, and saves — AI draft is a starting point, not the final word
- [ ] Paid tier only

### 6F — Gift Idea Suggestions
The gift ideas feature (Phase 2B) gets smarter with AI.

- [ ] When browsing gift ideas for a person, show "AI suggestions" alongside family-submitted ideas
- [ ] Claude receives: person's age, interests (from profile), upcoming occasion (birthday, holiday), past gift ideas
- [ ] Returns 5 gift ideas with brief rationale for each
- [ ] Paid tier only

### 6G — Announcement Drafting
Writing a family announcement is a small but real friction point. AI makes it trivial.

- [ ] On the New Announcement page, offer "Help me write this" — user provides 1–3 bullet points
- [ ] Claude drafts a warm, family-appropriate announcement from the bullets
- [ ] User reviews, edits, and posts — not sent automatically
- [ ] Paid tier only

### 6H — Event Recap Generator
After an event passes, capture the memory before it fades.

- [ ] A "Generate recap" button appears on past events (admin or event creator only)
- [ ] Claude receives: event name, dates, location, attendee list (RSVPs), meal signups, assignment completions, photos added
- [ ] Returns a narrative recap: "25 Peases gathered in Bear Lake last July for the annual reunion..."
- [ ] Recap can be saved to the event and shared as an announcement or newsletter item
- [ ] Paid tier only

---

### AI Design Principles
- **Suggest, don't decide.** AI outputs are always drafts or options — a human approves before anything is saved or sent.
- **Context-first.** Every AI call is grounded in real family data. Generic responses are a failure mode.
- **Paid only.** AI features are the clearest value driver for the paid tier. Free users see that AI features exist but are prompted to upgrade.
- **Cost awareness.** Each AI call is logged with token counts. Platform admin can see aggregate AI spend per pod. At $9/mo per pod, a single newsletter generation + event recap + batch photo captioning could cost more in API fees than the subscription revenue — this must be budgeted before Phase 6 ships. Implement a per-pod monthly token ceiling; when the ceiling is reached, features degrade gracefully ("AI features are resting — available again on [date]") rather than silently failing or charging more.
- **Privacy.** Family data sent to Claude is not used for training (Anthropic API terms). Mention this explicitly in the Privacy Policy.

### AI Token Budget — work items
- [ ] `AIUsage` model: `pod_id`, `feature` (newsletter, event_planning, qa, caption, etc.), `tokens_used`, `created_at`
- [ ] Monthly token ceiling per pod (configurable in platform admin — start conservative, tune from real usage data)
- [ ] Platform admin view: AI spend by pod, by feature, by month — surfaces outliers before they become a cost problem
- [ ] Graceful degradation UI when a pod hits its ceiling

---

## Phase 5 — Growth & Monetization
*After the product is stable and has real users.*

- [ ] **Referral program**: "Invite another family, get 1 month free"
- [ ] **Shareable family moments** — opt-in public links for specific events or announcements that non-members can view without an account (e.g., share a reunion event page with extended family before they join). This is the primary organic growth lever — every shared link is a marketing impression.
- [ ] **Family history export**: download your entire pod as a PDF/ZIP archive (also satisfies GDPR data portability)
- [ ] **Anniversary & birthday reminders**: email digest (weekly "coming up this week")
- [ ] **AI-powered family newsletter**: auto-generate a shareable recap from recent activity (see Phase 6)
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
- [ ] **Move uploads off the local filesystem** — photos in `static/uploads/` will vanish on Railway redeploy. Switch to Cloudflare R2 or AWS S3 with private buckets and short-lived signed URLs. Signed URLs are also stronger than the current auth-gateway approach (no URL sharing possible). *Also tracked in Phase 0 — must be done before any non-test family uses the app.*
- [ ] **File content validation** — currently checks extension only. Add magic-byte validation (e.g. `python-magic`) to confirm the file is actually an image before saving.
- [ ] **Password strength enforcement** — add minimum 10-character requirement and reject the 100 most common passwords.
- [ ] **Dependency vulnerability scanning** — add GitHub Dependabot or run `pip audit` in CI before each deploy.
- [ ] **Secure token comparison** — use `hmac.compare_digest()` when validating invitation/reset tokens to prevent timing attacks.

### Near-term (before paid tier)
- [ ] **Audit log** — record admin actions (approving members, changing roles, deleting content) to a log table. Families deserve to know who did what.
- [ ] **Rate limiting on invitation acceptance** — the `/register/invite/<token>` endpoint has no rate limit; a stolen token could be brute-forced (low risk due to token length, but worth closing).
- [ ] **Account lockout** — after N failed logins from the same IP, lock for X minutes. Flask-Limiter handles IP-level limits; add per-account lockout on top.
- [ ] **Email-change verification** — if a user changes their email, require re-verification of the new address before it takes effect.
- [ ] **GDPR / CCPA data deletion** — implement a formal "delete my data" flow before taking any payments. GDPR applies to any EU resident regardless of where the company is based. CCPA applies to California residents. The flow must remove all PII and family tree entries for the requesting user and confirm deletion by email.

### Longer term (before mobile apps / public API)
- [ ] **JWT token security** — API tokens for mobile apps need short expiry, rotation, and revocation.
- [ ] **PII field encryption at rest** — sensitive fields (phone, address) stored in plaintext in the DB. Encrypt with a key stored in env for an extra layer against DB dump attacks.
- [ ] **Penetration test** — before the paid tier goes live, have a third party attempt to break in.

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
2. **Phase 0** — Harden what exists + wire up S3/R2 for photos (1–2 weeks) ✓ done (except S3)
3. **Phase 1C** — Landing page (1 week) ✓ done
4. **Phase 1A** — Self-serve pod signup + onboarding email sequence (2–3 weeks)
5. **Phase 1B** — Stripe billing + 30-day trial (2 weeks)
6. **Phase 4A** — In-app support form (1 day — do this before taking any money)
7. **Phase 2A** — Notification preferences (1 week — must exist before any Phase 2 feature ships)
8. **Phase 3B** — PWA (1 week — do this before Chat so Chat ships with a native-feeling mobile experience)
9. **Phase 4B** — Platform admin panel (2–3 weeks — needed once multiple pods exist)
10. **Phase 3A** — Calendar feed (1 week, quick win)
11. **Phase 2D** — Event improvements (2 weeks)
12. **Phase 2B** — Chat (3–4 weeks)
13. **Phase 2C** — Recipes & gifts (2 weeks)
14. **Phase 2E** — GEDCOM import (1 week)
15. **Phase 3C/3D** — Native apps (2–3 months)
16. **Phase 6A** — AI newsletter generator (1–2 weeks — first AI feature, high perceived value)
17. **Phase 6B–H** — Remaining AI features (roll out incrementally alongside other phases)
18. **Phase 5** — Growth & monetization (ongoing)

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
- [ ] **Add error monitoring (Sentry)** — sign up for Sentry free tier, add `sentry-sdk[flask]` to `requirements.txt`, initialize in `create_app()`:
  ```python
  import sentry_sdk
  from sentry_sdk.integrations.flask import FlaskIntegration
  sentry_sdk.init(dsn=os.environ.get('SENTRY_DSN'), integrations=[FlaskIntegration()])
  ```
  Add `SENTRY_DSN` to Railway environment variables. Without this, production errors are invisible until a user complains.

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
  - `SENTRY_DSN` — from Sentry project settings
  - `MAIL_*` keys (once email is configured in Step 5)
- [ ] Railway will auto-detect the `Procfile` and deploy on every push to `main`
- [ ] Open the Railway-provided URL (e.g. `ourpeapod-production.up.railway.app`) and verify it works
- [ ] Run the initial migration via Railway shell:
  ```bash
  flask db upgrade
  ```
- [ ] **Verify database backup coverage** — Railway's hobby plan ($5/mo) does not include automatic Postgres backups. Before any real family data goes in, do one of:
  - Upgrade to Railway Pro plan (~$20/mo) which includes point-in-time recovery, **or**
  - Set up a daily `pg_dump` cron job (Railway cron service or GitHub Actions) that writes a compressed dump to Cloudflare R2 or S3. Keep 30 days of daily dumps. Test a restore before going live.
  - This is not optional — a bad migration or accidental delete with no backup means permanent data loss.

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

*Last updated: 2026-05-20 — Third pass: Sentry error monitoring, Railway backup gap, Stripe Tax, family name display-only with account_id for routing*
