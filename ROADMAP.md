# Swugl — Product Roadmap

## Vision

**Swugl** is a private family hub — a place for families to stay connected, plan together, and preserve their shared history. The product launches as a hosted SaaS at `swugl.com` where any family can create their own "pod" in minutes.

---

## Phase 0 — Foundation Cleanup ✅ Complete

### Work Items
- [x] Audit all database queries for `family_id` scoping — cross-family isolation verified by smoke tests
- [x] Add `family_id` indexes on all relevant tables
- [x] Harden auth: rate-limit login and password reset, email verification on registration
- [x] Confirm all admin-only routes enforce `@admin_required` consistently
- [x] Smoke-test suite — 18 tests covering auth, event CRUD, cross-family isolation
- [x] **Wire up Cloudflare R2 for photo storage** — photos store to R2 and serve via `/photos/<key>` proxy (login required). R2 public URL deliberately not enabled — see note below.
- [ ] **Re-evaluate R2 photo delivery** — currently photos proxy through Railway (`/photos/<key>`), which is secure (login required) but adds latency. If photo loading feels slow with real family usage, add a custom domain (e.g. `photos.swugl.com`) in R2 → Custom Domains, set `R2_PUBLIC_URL` in Railway, and accept that URLs are publicly accessible to anyone who has them (mitigated by UUID keys).

---

## Phase 1 — Public Launch: Multi-Tenant SaaS ✅ Complete

### 1A — Self-Serve Pod Creation ✅
- [x] Registration flow: family name → account → email verification → dashboard
- [x] `account_id` (short unique pod code) on `Family`; displayed in family settings
- [x] Onboarding checklist on first login
- [x] **Onboarding email sequence** — `flask email-sequence` CLI command (Railway cron, daily at 08:00 UTC):
  - Day 0: Welcome email (sent at registration)
  - Day 3: Nudge if no members added — "Your pod is quiet"
  - Day 7: Feature highlight — events, tree, photos
  - Day 25: Trial warning — "5 days left"
  - Day 30: Trial ended — upgrade prompt
- **Schema note:** One user → one family. A `user_families` junction table will be needed if multi-family membership is ever required.

### 1B — Pricing & Billing ✅
- [x] 30-day free trial of paid tier on signup
- [x] Trial banner in app header (days remaining / expired / past_due)
- [x] Stripe Checkout + Customer Portal (monthly and annual intervals)
- [x] Stripe webhook handler (`billing.py`) — checkout, subscription updated/deleted, payment failed/succeeded
- [x] `family_has_paid_access()` helper + `@requires_plan` decorator
- [x] `/billing` page for admins
- [x] Grace period (7 days) on failed payment before downgrading
- [x] Stripe Tax enabled on all Checkout sessions

### 1C — Public Landing Page ✅
- [x] Hero, features, pricing (monthly/annual toggle), footer
- [x] Privacy Policy and Terms of Service
- [x] `/` → `/home` if logged in, else landing page

### 1D — Multi-Pod Membership
*Do before the user base grows — migrating existing single-family data later is painful.*

Right now `User.family_id` is a single FK. A person who married into a second family (or has step-families, or helps administer a parent's pod) cannot belong to two pods under one login. The schema note in 1A flagged this; here is the concrete work.

- [ ] `UserPodMembership` join table: `user_id`, `family_id`, `role` (admin / member), `joined_at` — replaces `User.family_id`
- [ ] Add `active_family_id` to the server-side session (set on login to the user's primary pod; updated by switcher)
- [ ] Migrate existing `User.family_id` rows into `UserPodMembership`; keep `User.family_id` as a deprecated read-only column until all routes are updated
- [ ] Replace every `current_user.family_id` in routes (~80 call sites) with `current_user.active_family_id` helper property
- [ ] Pod switcher in top nav — visible only when a user belongs to ≥ 2 pods; shows pod name + avatar, switches `active_family_id` in session and redirects to `/home`
- [ ] Invite flow supports adding a user who already has an account to a second pod (admin sends invite → user accepts → new membership row created)
- [ ] Each pod membership has its own role — being admin in Pod A does not make you admin in Pod B

---

## Phase 2 — Communication & Content
*Deepens engagement once families are onboarded.*

### 2A — Notification Preferences *(build this first — before 2B, 2C, or 2D ship)*
Without a unified notification system, every new feature that sends email or push notifications becomes its own ad-hoc implementation. Users get spammed or go dark. Both cause churn.

- [ ] `NotificationPreference` model: `user_id`, `event_type` (new_event, rsvp_reminder, new_member, chat_message, announcement, birthday_digest, digest), `channel` (email, push), `enabled` (bool)
- [ ] `/profile/notifications` settings page — one toggle per event type per channel
- [ ] Default preferences set at signup (sensible defaults: digest on, per-message chat off)
- [ ] All notification-sending code goes through a central `notify(user, event_type, payload)` helper that checks preferences before sending
- [ ] Weekly digest email: upcoming events, recent activity, new photos, birthdays and anniversaries this week — this is the primary retention driver; members with no reason to open the app will open a good digest
- [ ] **Birthday & anniversary reminders** — birthday/anniversary data is already in the DB; the digest is the delivery mechanism. Show the next 7 days of birthdays/anniversaries in each weekly email. No new model needed — query `Person.birth_date` and `SpouseRelationship.anniversary` within each pod.

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
- [ ] **Embedded Apple Pay / Google Pay on event detail page** — instead of redirecting to Stripe-hosted Checkout, use Stripe's Payment Request Button (or Payment Element) to show a native Apple Pay / Google Pay button inline on the event page. Requires domain verification on swugl.com (`/.well-known/apple-developer-merchantid-domain-association` served via Flask static route) and switching from the hosted Checkout redirect to client-side Stripe.js. Saves the redirect round-trip; higher conversion on mobile Safari.
- [ ] **Instant event payouts** — optional checkbox on the payout form to deliver funds within 30 minutes instead of 1–2 business days. Stripe charges an extra 1.5% (min $0.50) deducted from the admin's payout. Requires the connected account to have an eligible Visa/Mastercard debit card; check availability via Stripe's external accounts API before showing the option. Implementation: after `Transfer.create()`, call `Payout.create(method='instant', stripe_account=acct_id)` on behalf of the connected account.

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

### 3B — Mobile Audit + Progressive Web App (PWA)
Most family members will use Swugl on their phone — checking events, viewing RSVPs, signing up for meals. Before building a PWA, audit every key flow at 390px.

**Mobile audit (prerequisite to PWA ship):**
- [ ] Walk every core flow on an actual phone: login → home → events → meal signup → task claim → sleeping assignment → family tree → member profile → photo upload
- [ ] Tap targets ≥ 44×44px on all interactive elements (see Accessibility)
- [ ] Tables replaced with card/list layouts at small breakpoints (event detail sections are the most likely pain point)
- [ ] Event detail page with all three sections active tested on 390px
- [ ] Sidebar nav collapses/slides correctly on mobile

**PWA:**
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
*Swugl (the company) needs its own tools to operate the platform, help users, and stay informed about what's happening across all pods.*

### 4A — In-App Support Channel
The simplest path for users to get help without leaving the app.

- [ ] **"Get help" link** in sidebar footer — visible to all authenticated users
- [ ] **Support request form** at `/support`: subject, description, category (billing, technical, account, other)
  - Routes to `hello@swugl.com` via Resend
  - Automatically includes: user email, family name, `account_id`, browser/OS (from user-agent) — the `account_id` is what support uses to look up the pod in the platform admin, not the display name
  - Confirms submission with a flash message
- [ ] **Help center link** — link out to a Notion-based FAQ or help docs (lightweight, no custom build needed initially)
- [ ] **Status page** — a simple hosted status page (Instatus or Statuspage.io) users can check during outages

### 4B — Platform Admin Panel
A separate admin area for Swugl staff — distinct from the family-level admin that pod admins already have.

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
- [ ] `hello@swugl.com` inbox monitored and routed

---

## Phase 5 — AI Features
*AI is woven into Swugl from day one — not bolted on later. All AI features are paid tier only. The goal is to make the product feel intelligent without requiring families to think about AI at all.*

**Platform:** Built on the **Claude API** (Anthropic SDK). Model selection: default to the latest Claude Sonnet for a balance of quality and cost; surface model choice in platform admin for tuning.

### 6A — Family Newsletter Generator
Every family has a story — most of it gets lost in group chats. The newsletter generator pulls together recent events, announcements, new members, and milestones into a readable family update.

- [ ] Aggregate activity from the past 30 days: new events, RSVPs, announcements, photos added, new members
- [ ] `POST /ai/newsletter/preview` — sends activity summary to Claude, returns a draft newsletter in markdown
- [ ] Admin reviews and edits the draft before sending
- [ ] One-click send to all family members via email (Resend)
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
- **Cost awareness.** Each AI call is logged with token counts. Platform admin can see aggregate AI spend per pod. At $9/mo per pod, a single newsletter generation + event recap + batch photo captioning could cost more in API fees than the subscription revenue — this must be budgeted before Phase 5 ships. Implement a per-pod monthly token ceiling; when the ceiling is reached, features degrade gracefully ("AI features are resting — available again on [date]") rather than silently failing or charging more.
- **Privacy.** Family data sent to Claude is not used for training (Anthropic API terms). Mention this explicitly in the Privacy Policy.

### AI Token Budget — work items
- [ ] `AIUsage` model: `pod_id`, `feature` (newsletter, event_planning, qa, caption, etc.), `tokens_used`, `created_at`
- [ ] Monthly token ceiling per pod (configurable in platform admin — start conservative, tune from real usage data)
- [ ] Platform admin view: AI spend by pod, by feature, by month — surfaces outliers before they become a cost problem
- [ ] Graceful degradation UI when a pod hits its ceiling

---

## Phase 6 — Growth & Monetization
*After the product is stable and has real users.*

- [ ] **Referral program**: "Invite another family, get 1 month free"
- [ ] **Shareable family moments** — opt-in public links for specific events or announcements that non-members can view without an account (e.g., share a reunion event page with extended family before they join). This is the primary organic growth lever — every shared link is a marketing impression.
- [ ] **Family history export**: download your entire pod as a PDF/ZIP archive (also satisfies GDPR data portability)
- [ ] **AI-powered family newsletter**: auto-generate a shareable recap from recent activity (see Phase 5)
- [ ] **Admin analytics dashboard**: member activity, storage used, events per month
- [ ] **Enterprise / extended family tier**: multiple branches, branch admins, cross-branch events

---

## Security

Swugl stores personal information — names, birthdays, addresses, family relationships, photos. Security is not a phase; it's a constant. This section tracks what's in place and what still needs doing.

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
- [x] **HSTS header** — `Strict-Transport-Security: max-age=63072000; includeSubDomains` added to `set_security_headers()` (production only).
- [x] **Remove `unsafe-inline` from `script-src`** — per-request nonce generated in `before_request`, injected into CSP header and every inline `<script nonce="...">` tag. `unsafe-inline` removed from `script-src`. Note: `style-src` still has `unsafe-inline` — removing it requires migrating 663 inline `style=` attributes to CSS classes (ongoing, tracked in Accessibility).
- [x] **Session lifetime** — `PERMANENT_SESSION_LIFETIME=14d`, `REMEMBER_COOKIE_DURATION=30d` set in `create_app()`.
- [x] **Hash reset/invite/verification tokens before DB storage** — `sha256(token)` stored in DB; raw token travels only in email URLs. Applied to `reset_token`, `verification_token`, and `invitation_token`.

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

## Content Moderation & Member Fairness

Like Security, this is a constant — not a phase. Swugl hosts personal content and family relationships. The platform's job is not to adjudicate family disputes, but to ensure legal compliance, protect members from abuse of power, and give AI a meaningful role in both.

### Policy
- Pod admins are responsible for their pod's content and membership decisions. Swugl does not override admin decisions except in cases of illegal content or documented platform abuse.
- A removed member always receives advance notice and time to export their data before access is revoked.
- Disputes can be escalated to platform support, which can review pod activity and act at the pod level (e.g. suspending a pod) — but will not reinstate individual memberships.

### Legal compliance (all tiers)
- [ ] **CSAM hash-matching on every photo upload** — integrate PhotoDNA or equivalent hash-matching service to compare uploads against known illegal content databases. Required by law before the app is public. Matches trigger immediate removal and a report to NCMEC.
- [ ] Retain a compliance log of all matches and reports.

### AI-powered content moderation (paid tier)
- [ ] **AI image moderation layer** — run every uploaded photo through an AI moderation service (AWS Rekognition or Hive Moderation) in addition to hash-matching. Catches new illegal or harmful content not yet in any hash database. Flag for human review rather than auto-removing.
- [ ] **Context-aware text moderation** — pod admins set a content intent ("family-friendly," "strict," or "default") rather than a manual keyword list. AI interprets context: the same word used in a historical quote vs. an attack is handled differently. A keyword list cannot make that distinction.
- [ ] Pod admin can review flagged content and dismiss or remove.

### Member removal protections
- [ ] **24-hour removal notice** — when an admin removes a member, the member receives an AI-drafted email (warm, not robotic, using the family name and context) notifying them of removal and providing a data export link. Access is revoked after 24 hours.
- [ ] **Data export on removal** — the outgoing member can download their profile, photos, and contributions before access ends.
- [ ] **Anomaly detection** — AI monitors for unusual admin behavior: removing multiple members in a short window, bulk-deleting content, mass role changes. Anomalies are flagged automatically to platform admin for review — no complaint needed.
- [ ] **Support triage** — when a removed member contacts support, AI summarizes the pod's recent activity (membership changes, content deletions, admin actions) so the platform admin understands the situation in under a minute rather than digging through logs.

---

## Accessibility

Swugl's core demographic includes grandparents and older family members. Accessibility is not an edge case — it is the product. WCAG 2.1 AA compliance is the target. Like Security, this is a constant, not a phase.

### Principles
- Every new template ships accessible. Retrofitting later costs 3× more than doing it right the first time.
- Test with a screen reader (VoiceOver on Mac, NVDA on Windows) before marking any UI feature complete.
- Color contrast, keyboard navigation, and readable font sizes are non-negotiable.

### Work items (woven into each phase as features ship)
- [ ] Color contrast: all text meets WCAG AA ratio (4.5:1 for normal text, 3:1 for large)
- [ ] All interactive elements reachable and operable by keyboard alone
- [ ] All images have meaningful `alt` text (AI can suggest alt text for uploaded photos — see Phase 5D)
- [ ] Form inputs have visible labels (no placeholder-only labels)
- [ ] Focus indicators visible on all interactive elements
- [ ] Error messages are descriptive and linked to the relevant field
- [ ] `aria-live` regions for dynamic content updates (chat messages, flash notifications)
- [ ] Mobile tap targets ≥ 44×44px

---

## Technical Architecture Notes

### Multi-Tenancy
All queries must include `family_id`. The pattern:
```python
# Always scope to current family
event = Event.query.filter_by(id=event_id, family_id=current_user.family_id).first_or_404()
```
Introduce a `FamilyScoped` mixin or query helper to enforce this at the model layer.

### Redis (required before Chat and multi-instance scale)
Flask-Limiter uses in-memory storage by default — this breaks as soon as you run more than one server instance. Flask-SocketIO (Chat) also needs a Redis adapter to broadcast messages across instances. Background jobs (Day 3/7 onboarding emails, AI processing) need a queue backed by Redis (Celery or RQ). Add Redis as a Railway plugin before Phase 2B ships; update Flask-Limiter to use `RedisStorage` at the same time.

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
- Target: **Resend** for transactional email (invites, event reminders, billing receipts)

### Hosting
- **Target:** Railway (PaaS) — deploy from GitHub, supports WebSockets, no server management
- **DNS:** GoDaddy (registrar) → Cloudflare (nameservers/DNS) → Railway
- **Hostgator:** Can be cancelled once Railway is live; not needed
- HTTPS is required for PWA + Web Push (Railway provides it automatically)

---

## Recommended Execution Order

1. **Go Live** — Get the existing app running on swugl.com ✓ done
2. **Phase 0** — Harden what exists + wire up S3/R2 for photos ✓ done (except S3)
3. **Phase 1C** — Landing page ✓ done
4. **Phase 1A** — Self-serve pod signup + onboarding email sequence ✓ done
5. **Phase 1B** — Stripe billing + 30-day trial ✓ done
6. **Security pre-launch** — HSTS, CSP unsafe-inline removal, session lifetime, token hashing (days — do before real families use the app)
7. **Phase 1D** — Multi-pod membership (1–2 weeks — do before user base grows; gets painful to migrate later)
8. **Phase 4A** — In-app support form (1 day — do before taking any money) ✓ done
9. **Phase 2A** — Notification preferences + weekly digest + birthday reminders (1–2 weeks — must exist before any Phase 2 feature ships; digest is the #1 retention driver)
10. **Phase 3B** — Mobile audit + PWA (1–2 weeks — do before Chat so Chat ships with a native-feeling mobile experience)
11. **Phase 4B** — Platform admin panel (2–3 weeks — needed once multiple pods exist)
12. **Phase 3A** — Calendar feed (1 week, quick win)
13. **Phase 2D** — Event improvements (2 weeks)
14. **Phase 2B** — Chat (3–4 weeks)
15. **Phase 2C** — Recipes & gifts (2 weeks)
16. **Phase 2E** — GEDCOM import (1 week)
17. **Phase 3C/3D** — Native apps (2–3 months)
18. **Phase 5A** — AI newsletter generator (1–2 weeks — first AI feature, high perceived value)
19. **Phase 5B–H** — Remaining AI features (roll out incrementally alongside other phases)
20. **Phase 6** — Growth & monetization (ongoing)

---

## Go Live Plan

*Everything needed to get the app running at swugl.com. Target platform: **Railway**.*

### What We Have
- **Domain:** `swugl.com` registered at GoDaddy; nameservers point to Cloudflare
- **DNS:** Managed in Cloudflare
- **Email:** Resend domain verified via Cloudflare DNS records — ready to send from `swugl.com`
- **Hosting:** Railway connected through Cloudflare
- **Hostgator Baby Plan:** Not needed once Railway is live; can be cancelled
- **App:** Flask + SQLite, currently running locally

### Why Railway (not Hostgator)
Hostgator shared hosting can't support WebSockets, which are required for Phase 2A (chat). Starting there and migrating later is the same work done twice. Railway handles deploys from GitHub, provides SSL automatically, supports WebSockets, and has no server management overhead. ~$5–20/mo depending on usage.

---

### Step 1 — Database: Switch from SQLite to PostgreSQL ✅

- [x] Add `psycopg2-binary` to `requirements.txt`
- [x] `DATABASE_URL` read from environment; falls back to SQLite locally
- [x] PostgreSQL provisioned on Railway; `DATABASE_URL` auto-injected
- [x] `flask db upgrade` runs automatically on every deploy (Procfile)

> **Note on photos:** Uploaded photo files face the same ephemeral filesystem problem. Must switch to Cloudflare R2 before any non-test family uses the app. See Phase 0.

---

### Step 2 — Prepare the App for Production ✅

- [x] `.env.example` committed; `.env` gitignored
- [x] `gunicorn` in `requirements.txt`; Procfile runs `flask db upgrade && gunicorn`
- [x] Production config (secure cookies, secret key from env) already in `app/__init__.py`
- [ ] **Add error monitoring (Sentry)** — free tier, 5,000 errors/month. Add `sentry-sdk[flask]` to `requirements.txt`, init in `create_app()`, add `SENTRY_DSN` to Railway env vars. Do before smoke test.

---

### Step 3 — Push to GitHub ✅

- [x] Repo on GitHub; Railway deploys automatically on push to `main`
- [x] `.env`, `instance/`, `*.db` gitignored

---

### Step 4 — Set Up Railway ✅

- [x] Project on Railway, connected to GitHub repo, auto-deploys on push to `main`
- [x] PostgreSQL service provisioned; `DATABASE_URL` auto-injected
- [x] All environment variables set (`SECRET_KEY`, `FLASK_ENV`, Resend, Stripe, `SUPPORT_EMAIL`)
- [x] App live at `ourpeapod.com`
- [x] `flask db upgrade` runs automatically on every deploy
- [ ] **Upgrade Railway to Pro plan (~$20/mo)** — includes point-in-time Postgres backups. Do before any non-test family data goes in.

---

### Step 5 — Email ✅

- [x] Resend account set up; `ourpeapod.com` domain verified via Cloudflare DNS
- [x] `RESEND_API_KEY`, `RESEND_FROM_EMAIL`, `MAIL_ENABLED`, `SUPPORT_EMAIL` set in Railway
- [ ] Test: send a member invite from the live site and confirm delivery

---

### Step 6 — Custom Domain & DNS ✅

- [x] `ourpeapod.com` added as custom domain in Railway
- [x] Cloudflare: apex CNAME → Railway; `www` CNAME → `ourpeapod.com` (Cloudflare handles www redirect; Railway plan supports one custom domain)
- [x] SSL provisioned automatically
- [ ] Cancel Hostgator Baby Plan once confirmed no longer needed

---

### Step 7 — Smoke Test ✅

- [x] Register, verify email, log in
- [x] Navigate all main pages
- [x] Add family member, set patriarch/matriarch
- [x] Upload a photo
- [x] Billing page loads, Stripe checkout works
- [x] Support form sends email
- [x] Sign out
- [ ] Test on mobile (Safari iOS + Chrome Android)
- [ ] Create an event with all three sections
- [ ] Post an announcement

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

*Last updated: 2026-05-23 — Added Phase 1D (multi-pod membership), Security pre-launch items (HSTS, CSP unsafe-inline, session lifetime, token hashing), Phase 3B mobile audit, birthday/anniversary reminders moved from Phase 6 into Phase 2A weekly digest, execution order updated*
