from flask import Blueprint, render_template, redirect, url_for, flash, request, current_app, send_file, session, abort, jsonify
from flask_login import login_user, logout_user, login_required, current_user
from ..models import Family, User, Person, ParentRelationship, PARENT_ROLES, SpouseRelationship, Event, EventMeal, EventMealItem, EventAssignment, AssignmentTask, ASSIGNMENT_CATEGORIES, EventRSVP, EventSleepingSpot, SPOT_TYPES, EventComment, Announcement, Album, Photo, Poll, NotificationPreference, NOTIFICATION_EVENTS, UserPodMembership, CalendarToken, EventPaymentConfig, EventPaymentRecord, FamilyPayoutAccount, Location, Document, DOCUMENT_CATEGORIES, ChatMessage
from ..forms import LoginForm, RegistrationForm, ProfileForm, SpouseForm, EndSpouseForm, SpouseInviteForm, ForgotPasswordForm, ResetPasswordForm, AddPersonForm, RelativeForm, AddParentForm, FamilySettingsForm, EditPersonForm, EventForm, EventCommentForm, EventMealForm, EventMealFamilyAssignForm, EventMealItemForm, EventMealSelfSignupForm, EventMealAssignForm, EventAssignmentForm, EventAssignmentAdminAssignForm, EventSleepingSpotForm, EventSleepingAssignForm, GENDER_CHOICES_DEFAULT, GENDER_CHOICES_EXPANDED, PRONOUN_CHOICES, AnnouncementForm, AlbumForm, PhotoUploadForm, SupportForm, ChatMessageForm
from ..email import send_verification_email, send_pending_notification, send_approval_notification, send_spouse_confirmation_email, send_spouse_invitation_email, send_password_reset_email, send_member_invitation_email, send_welcome_email, send_support_email, send_pod_added_email
from datetime import date, datetime, timedelta
from functools import wraps
from urllib.parse import urlparse
from .. import db, limiter
from ..billing import requires_plan, family_has_paid_access, FREE_MEMBER_LIMIT, FREE_EVENT_LIMIT
from ..storage import upload_photo, delete_object, get_object_bytes
import secrets
import hashlib
import re
import os
from collections import defaultdict


def _hash_token(token: str) -> str:
    """SHA-256 hash a token before storing in the DB.

    The raw token travels in email URLs; only the hash lives in the DB so a
    dump of the database cannot be used to redeem outstanding tokens.
    """
    return hashlib.sha256(token.encode()).hexdigest()


def _ensure_membership(user):
    """Upsert a UserPodMembership for user's home pod. Call after flush so user.id exists."""
    existing = UserPodMembership.query.filter_by(
        user_id=user.id, family_id=user.family_id
    ).first()
    if not existing:
        role = 'admin' if user.is_admin else ('delegate' if user.is_delegate else 'member')
        db.session.add(UserPodMembership(user_id=user.id, family_id=user.family_id, role=role))


import uuid
import zipfile
import io
import csv

main = Blueprint('main', __name__)

def _default_parent_role(person):
    if person.gender == 'Male':   return 'father'
    if person.gender == 'Female': return 'mother'
    return 'parent'

def format_phone(raw):
    if not raw:
        return None
    digits = re.sub(r'\D', '', raw)
    if len(digits) == 10:
        return f"{digits[:3]}-{digits[3:6]}-{digits[6:]}"
    elif len(digits) == 11 and digits[0] == '1':
        return f"{digits[1:4]}-{digits[4:7]}-{digits[7:]}"
    return raw

def format_birthplace(raw):
    if not raw:
        return None
    parts = [p.strip() for p in raw.split(',')]
    if len(parts) >= 2:
        city = parts[0].title()
        state = parts[1].upper()
        extra = [p for p in parts[2:] if p]
        if extra:
            return f"{city}, {state}, {', '.join(extra)}"
        return f"{city}, {state}"
    return raw.title()

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.active_is_admin:
            flash('You do not have permission to access that page.', 'error')
            return redirect(url_for('main.home'))
        return f(*args, **kwargs)
    return decorated_function

def contributor_or_admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or not (current_user.active_is_admin or current_user.active_is_delegate):
            flash('You do not have permission to access that page.', 'error')
            return redirect(url_for('main.home'))
        return f(*args, **kwargs)
    return decorated_function

def _gender_term(person, male, female, neutral):
    if person.gender == 'Male':
        return male
    if person.gender == 'Female':
        return female
    return neutral

def get_relationship(a, b):
    """Return a relationship label from person a's perspective of person b."""
    if not a or not b or a.id == b.id:
        return None
    a_spouse = a.get_active_spouse()
    # Spouse
    if a_spouse and a_spouse.id == b.id:
        return _gender_term(b, 'Husband', 'Wife', 'Spouse')
    # Parent
    if b in a.parents:
        return _gender_term(b, 'Father', 'Mother', 'Parent')
    # Child
    if b in a.children:
        return _gender_term(b, 'Son', 'Daughter', 'Child')
    # Sibling
    shared = set(p.id for p in a.parents) & set(p.id for p in b.parents)
    if shared:
        return _gender_term(b, 'Brother', 'Sister', 'Sibling')
    # Grandparent
    for p in a.parents:
        if b in p.parents:
            return _gender_term(b, 'Grandfather', 'Grandmother', 'Grandparent')
    # Grandchild
    for c in a.children:
        if b in c.children:
            return _gender_term(b, 'Grandson', 'Granddaughter', 'Grandchild')
    # Great-grandparent
    for p in a.parents:
        for gp in p.parents:
            if b in gp.parents:
                return _gender_term(b, 'Great-grandfather', 'Great-grandmother', 'Great-grandparent')
    # Aunt / Uncle
    for p in a.parents:
        for gp in p.parents:
            for s in gp.children:
                if s.id != p.id and s.id == b.id:
                    return _gender_term(b, 'Uncle', 'Aunt', 'Aunt/Uncle')
    # Niece / Nephew
    for p in a.parents:
        for s in p.children:
            if s.id != a.id and b in s.children:
                return _gender_term(b, 'Nephew', 'Niece', 'Niece/Nephew')
    # Child-in-law
    for c in a.children:
        c_spouse = c.get_active_spouse()
        if c_spouse and c_spouse.id == b.id:
            return _gender_term(b, 'Son-in-law', 'Daughter-in-law', 'Child-in-law')
    # Grandchild-in-law
    for c in a.children:
        for gc in c.children:
            gc_spouse = gc.get_active_spouse()
            if gc_spouse and gc_spouse.id == b.id:
                return _gender_term(b, 'Grandson-in-law', 'Granddaughter-in-law', 'Grandchild-in-law')
    # In-law relationships via spouse
    if a_spouse:
        if b in a_spouse.parents:
            return _gender_term(b, 'Father-in-law', 'Mother-in-law', 'Parent-in-law')
        for sp in a_spouse.parents:
            for s in sp.children:
                if s.id != a_spouse.id and s.id == b.id:
                    return _gender_term(b, 'Brother-in-law', 'Sister-in-law', 'Sibling-in-law')
            if b in sp.parents:
                return _gender_term(b, 'Grandfather-in-law', 'Grandmother-in-law', 'Grandparent-in-law')
    # Cousin
    for p in a.parents:
        for gp in p.parents:
            for ps in gp.children:
                if ps.id != p.id and b in ps.children:
                    return 'Cousin'
    return None


@main.route('/members')
@login_required
def members():
    people = Person.query.filter_by(family_id=current_user.active_family_id, in_directory=True).order_by(Person.name).all()
    today = date.today()
    bday_days = {}
    for p in people:
        if p.birthday:
            try:
                bday = p.birthday.replace(year=today.year)
            except ValueError:
                bday = p.birthday.replace(year=today.year, day=28)
            if bday < today:
                try:
                    bday = p.birthday.replace(year=today.year + 1)
                except ValueError:
                    bday = p.birthday.replace(year=today.year + 1, day=28)
            bday_days[p.id] = (bday - today).days
    # Person IDs that have an active user account — shown as indicator for admins
    user_person_ids = set()
    if current_user.active_is_admin:
        user_person_ids = {
            u.person_id for u in User.query.filter_by(
                family_id=current_user.active_family_id, status='approved'
            ).filter(User.person_id.isnot(None)).all()
        }
    return render_template('members.html', people=people, family=current_user.active_family,
                           bday_days=bday_days, user_person_ids=user_person_ids)


@main.route('/members/address-export')
@login_required
def address_export():
    if not current_user.active_is_admin:
        abort(403)
    people = Person.query.filter_by(
        family_id=current_user.active_family_id, in_directory=True
    ).order_by(Person.name).all()
    fmt = request.args.get('format', 'print')
    if fmt == 'csv':
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(['Name', 'Email', 'Phone', 'Address'])
        for p in people:
            if p.address or p.email or p.phone:
                writer.writerow([
                    p.get_display_name(),
                    p.email or '',
                    p.phone or '',
                    p.address or '',
                ])
        buf.seek(0)
        return send_file(
            io.BytesIO(buf.getvalue().encode()),
            mimetype='text/csv',
            as_attachment=True,
            download_name=f'{current_user.active_family.name} - Address List.csv',
        )
    return render_template('address_export.html', people=people, family=current_user.active_family)


@main.route('/.well-known/apple-developer-domain-association.txt')
def apple_domain_association():
    content = current_app.config.get('APPLE_DOMAIN_ASSOCIATION', '')
    if not content:
        abort(404)
    return content, 200, {'Content-Type': 'text/plain'}


@main.route('/')
def index():
    if current_user.is_authenticated:
        return redirect(url_for('main.home'))
    return render_template('index.html')

@main.route('/home')
@login_required
def home():
    people = Person.query.filter_by(family_id=current_user.active_family_id).order_by(Person.name).all()
    member_count = len(people)
    today = date.today()
    upcoming_birthdays = []
    for person in people:
        if person.birthday:
            try:
                bday = person.birthday.replace(year=today.year)
            except ValueError:
                bday = person.birthday.replace(year=today.year, day=28)
            if bday < today:
                try:
                    bday = person.birthday.replace(year=today.year + 1)
                except ValueError:
                    bday = person.birthday.replace(year=today.year + 1, day=28)
            days = (bday - today).days
            if days <= 30:
                upcoming_birthdays.append((person, bday, days))
    upcoming_birthdays.sort(key=lambda x: x[2])
    upcoming_events = Event.query.filter_by(family_id=current_user.active_family_id).filter(
        Event.start_date >= today
    ).order_by(Event.start_date).limit(3).all()
    # Profile completeness nudge
    profile_nudge = []
    me = current_user.person
    if me:
        if not me.photo_path:
            profile_nudge.append(('photo', 'Add a profile photo'))
        if not me.birthday:
            profile_nudge.append(('birthday', 'Add your birthday'))
        if not me.gender:
            profile_nudge.append(('gender', 'Set your gender'))
        if not me.birthplace:
            profile_nudge.append(('birthplace', 'Add your birthplace'))
    pinned = Announcement.query.filter_by(family_id=current_user.active_family_id, pinned=True)\
        .order_by(Announcement.created_at.desc()).all()
    recent = Announcement.query.filter_by(family_id=current_user.active_family_id, pinned=False)\
        .order_by(Announcement.created_at.desc()).limit(3).all()
    home_announcements = pinned + recent
    recent_photos = Photo.query.filter_by(family_id=current_user.active_family_id)\
        .order_by(Photo.created_at.desc()).limit(6).all()
    from sqlalchemy import extract
    on_this_day = Photo.query.filter_by(family_id=current_user.active_family_id).filter(
        db.or_(
            db.and_(
                Photo.taken_date.isnot(None),
                extract('month', Photo.taken_date) == today.month,
                extract('day', Photo.taken_date) == today.day,
                extract('year', Photo.taken_date) < today.year,
            ),
            db.and_(
                Photo.taken_date.is_(None),
                extract('month', Photo.created_at) == today.month,
                extract('day', Photo.created_at) == today.day,
                extract('year', Photo.created_at) < today.year,
            ),
        )
    ).order_by(Photo.taken_date.desc().nullslast(), Photo.created_at.desc()).limit(8).all()
    on_this_day_events = Event.query.filter_by(family_id=current_user.active_family_id).filter(
        extract('month', Event.start_date) == today.month,
        extract('day', Event.start_date) == today.day,
        extract('year', Event.start_date) < today.year,
    ).order_by(Event.start_date.desc()).limit(5).all()
    # Onboarding checklist — only for admins of new pods (families with account_id)
    onboarding = None
    if current_user.active_is_admin and current_user.family and current_user.active_family.account_id:
        event_count = Event.query.filter_by(family_id=current_user.active_family_id).count()
        location_count = Location.query.filter_by(family_id=current_user.active_family_id).count()
        core = [
            ('members',  'Add your first family member', url_for('main.members'),         member_count > 1),
            ('profile',  'Complete your profile',        url_for('main.profile_edit'),    bool(me and me.birthday)),
            ('event',    'Create your first event',      url_for('main.events_list'),     event_count > 0),
            ('photo',    'Upload a photo',               url_for('main.albums'),          len(recent_photos) > 0),
            ('location', 'Add a family location',        url_for('main.admin_locations'), location_count > 0),
        ]
        steps = list(core)
        # Optional suggestion — does not gate the checklist, so it never nags
        # members who aren't adding a spouse/partner.
        if me and not me.get_active_spouse():
            steps.append(('spouse', 'Add your spouse or partner', url_for('main.spouse_add'), False))
        # Show the checklist until every CORE step is done.
        if any(not s[3] for s in core):
            onboarding = steps
    # Activity feed — last 30 days across all content types
    # Announcements are excluded here because they have their own card on the home page.
    _since = datetime.utcnow() - timedelta(days=30)
    _since_date = _since.date()
    _activity = []

    for e in Event.query.filter(
        Event.family_id == current_user.active_family_id,
        Event.created_at >= _since
    ).order_by(Event.created_at.desc()).limit(8).all():
        _activity.append({
            'type': 'event', 'ts': e.created_at, 'actor': None,
            'label': f'New event: {e.name}', 'url': f'/events/{e.id}', 'icon': 'calendar',
        })

    _photos = Photo.query.filter(
        Photo.family_id == current_user.active_family_id,
        Photo.created_at >= _since
    ).order_by(Photo.created_at.desc()).limit(60).all()
    _photo_groups = defaultdict(list)
    for _p in _photos:
        _photo_groups[(_p.album_id, _p.uploaded_by_id, _p.created_at.date())].append(_p)
    for (_aid, _, _d), _grp in _photo_groups.items():
        _latest = max(_grp, key=lambda p: p.created_at)
        _cnt = len(_grp)
        _alb = _grp[0].album
        _activity.append({
            'type': 'photos', 'ts': _latest.created_at, 'actor': _grp[0].uploaded_by,
            'label': f'added {_cnt} photo{"s" if _cnt > 1 else ""} to {_alb.name}',
            'url': f'/albums/{_aid}', 'icon': 'image',
        })

    for c in EventComment.query.join(Event, EventComment.event_id == Event.id).filter(
        Event.family_id == current_user.active_family_id,
        EventComment.created_at >= _since
    ).order_by(EventComment.created_at.desc()).limit(10).all():
        _activity.append({
            'type': 'comment', 'ts': c.created_at, 'actor': c.person,
            'label': f'commented on {c.event.name}', 'url': f'/events/{c.event_id}',
            'icon': 'message-circle',
        })

    for _poll in Poll.query.filter(
        Poll.family_id == current_user.active_family_id,
        Poll.created_at >= _since
    ).order_by(Poll.created_at.desc()).limit(5).all():
        _activity.append({
            'type': 'poll', 'ts': _poll.created_at, 'actor': _poll.created_by,
            'label': f'added a poll: "{_poll.question}"', 'url': '/polls', 'icon': 'bar-chart-2',
        })

    for _u in User.query.filter(
        User.family_id == current_user.active_family_id,
        User.status == 'approved',
        User.approved_date >= _since_date,
    ).order_by(User.approved_date.desc()).limit(10).all():
        if _u.person:
            _ts = datetime(_u.approved_date.year, _u.approved_date.month, _u.approved_date.day, 12, 0)
            _activity.append({
                'type': 'member', 'ts': _ts, 'actor': None,
                'label': f'{_u.person.get_display_name()} joined the circle',
                'url': f'/person/{_u.person_id}', 'icon': 'user-plus',
            })

    _activity.sort(key=lambda x: x['ts'], reverse=True)
    activity_feed = _activity[:15]

    # Mark feed items that arrived since the viewer last loaded home, then
    # advance their marker. Excludes the viewer's own actions so "New" reflects
    # what others did. First-ever visit (prev_seen is None) shows nothing new.
    prev_seen = current_user.home_last_seen_at
    for item in activity_feed:
        actor = item.get('actor')
        is_own = actor is not None and getattr(actor, 'user', None) and actor.user.id == current_user.id
        item['is_new'] = bool(prev_seen and item['ts'] > prev_seen and not is_own)
    current_user.home_last_seen_at = datetime.utcnow()
    db.session.commit()

    # Live badge counts + teasers for the /home launcher tiles (#55/#43)
    tile_badges = _home_tile_badges(today, prev_seen)
    tile_teasers = {'events': _next_event_teaser(upcoming_events, today)}

    # RSVP status map for upcoming events (current user's person)
    rsvp_map = {}
    if me:
        for _ev in upcoming_events:
            _rsvp = EventRSVP.query.filter_by(event_id=_ev.id, person_id=me.id).first()
            rsvp_map[_ev.id] = _rsvp.status if _rsvp else None

    return render_template('home.html', member_count=member_count, family=current_user.active_family,
                           upcoming_birthdays=upcoming_birthdays, upcoming_events=upcoming_events,
                           profile_nudge=profile_nudge, me=me,
                           home_announcements=home_announcements,
                           recent_photos=recent_photos,
                           on_this_day=on_this_day,
                           on_this_day_events=on_this_day_events,
                           onboarding=onboarding,
                           activity_feed=activity_feed,
                           rsvp_map=rsvp_map,
                           tile_badges=tile_badges,
                           tile_teasers=tile_teasers,
                           now=datetime.now())


def _next_event_teaser(upcoming_events, today):
    """Human-readable 'when' for the soonest upcoming event, or None.
    e.g. 'today', 'tomorrow', 'Saturday' (within a week), else 'Aug 1'.
    upcoming_events is already ordered soonest-first."""
    nxt = upcoming_events[0] if upcoming_events else None
    if not nxt or not nxt.start_date:
        return None
    delta = (nxt.start_date - today).days
    if delta <= 0:
        return 'today'
    if delta == 1:
        return 'tomorrow'
    if delta < 7:
        return nxt.start_date.strftime('%A')        # weekday, e.g. "Saturday"
    return nxt.start_date.strftime('%b %-d')         # e.g. "Aug 1"


def _home_tile_badges(today, prev_seen):
    """Action-oriented counts for the /home launcher tiles (#55/#43 — Jeffrey
    drops these into the tiles). Each value is an int; 0 means 'no badge'.
    Chat and notification counts are NOT here — they already come from the
    context processor as unread_chat_count / unread_notification_count.

    Suggested phrasing per tile (Jeffrey's call on final copy):
      events → "N upcoming" · photos → "N new" · announcements → "N new"
      members → "N new this month" · polls → "N to vote" · cards → "N to sign"
      stories → "N to answer"
    Human-readable teasers (e.g. the next event's day) are returned separately
    by _next_event_teaser via the tile_teasers dict.
    """
    from ..models import (Event, Photo, Announcement, GreetingCard,
                          CardSignature, Poll, PollVote, StoryPrompt)
    fam = current_user.active_family
    fid = current_user.active_family_id
    my_pid = current_user.person.id if current_user.person else None
    is_organizer = current_user.active_is_admin or current_user.active_is_delegate

    badges = {
        'events': Event.query.filter(
            Event.family_id == fid, Event.start_date >= today).count(),
        'photos': Photo.query.filter(
            Photo.family_id == fid,
            Photo.created_at >= datetime.utcnow() - timedelta(days=7)).count(),
        'announcements': (Announcement.query.filter(
            Announcement.family_id == fid,
            Announcement.created_at > prev_seen).count() if prev_seen else 0),
        'members': User.query.filter(
            User.family_id == fid, User.status == 'approved',
            User.approved_date >= today.replace(day=1)).count(),
        'polls': 0,
        'cards': 0,
        'stories': 0,
    }

    # Open polls the viewer hasn't voted in
    if fam.enable_polls and my_pid:
        open_polls = [p for p in Poll.query.filter_by(family_id=fid).all() if not p.is_closed]
        if open_polls:
            voted = {v.poll_id for v in PollVote.query.filter(
                PollVote.person_id == my_pid,
                PollVote.poll_id.in_([p.id for p in open_polls])).all()}
            badges['polls'] = sum(1 for p in open_polls if p.id not in voted)

    # Unsent cards the viewer hasn't signed (and isn't the recipient of)
    if fam.enable_greeting_cards and my_pid:
        active = GreetingCard.query.filter(
            GreetingCard.family_id == fid, GreetingCard.sent_at.is_(None),
            GreetingCard.recipient_id != my_pid).all()
        if active:
            signed = {s.card_id for s in CardSignature.query.filter(
                CardSignature.person_id == my_pid,
                CardSignature.card_id.in_([c.id for c in active])).all()}
            badges['cards'] = sum(1 for c in active if c.id not in signed)

    # Open story prompts the viewer can answer (their own, or any if organizer)
    if fam.enable_stories and family_has_paid_access(fam):
        q = StoryPrompt.query.filter_by(family_id=fid, answered_at=None)
        if not is_organizer:
            q = q.filter_by(person_id=my_pid or -1)
        badges['stories'] = q.count()

    return badges


@main.route('/login', methods=['GET', 'POST'])
@limiter.limit('20 per minute', methods=['POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('main.home'))
    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(email=form.email.data).first()
        if not user or not user.check_password(form.password.data):
            flash('Invalid email or password.', 'error')
            return redirect(url_for('main.login'))
        if not user.email_verified:
            return redirect(url_for('main.resend_verification', email=user.email))
        if user.status == 'removed':
            flash('This account has been removed. Contact your family admin.', 'error')
            return redirect(url_for('main.login'))
        if user.status != 'approved':
            flash('Your account is pending approval.', 'error')
            return redirect(url_for('main.login'))
        if user.has_2fa:
            session['pending_2fa_user_id'] = user.id
            session['pending_2fa_remember'] = form.remember_me.data
            return redirect(url_for('tf.login_2fa'))
        login_user(user, remember=form.remember_me.data)
        session['active_family_id'] = user.family_id
        next_page = request.args.get('next')
        # Only allow same-site relative paths to prevent open redirect. The bare
        # netloc check misses protocol-relative ("//evil.com") and backslash
        # ("/\\evil.com") tricks that browsers resolve to an external host.
        if next_page and (
            urlparse(next_page).netloc
            or not next_page.startswith('/')
            or next_page.startswith('//')
            or next_page.startswith('/\\')
        ):
            next_page = None
        return redirect(next_page or url_for('main.home'))
    return render_template('login.html', form=form)

@main.route('/logout', methods=['POST'])
@login_required
def logout():
    logout_user()
    session.pop('active_family_id', None)
    flash('You have been logged out.', 'info')
    return redirect(url_for('main.login'))


@main.route('/switch-pod/<int:family_id>', methods=['POST'])
@login_required
def switch_pod(family_id):
    membership = next((m for m in current_user.memberships if m.family_id == family_id), None)
    if not membership:
        flash('You are not a member of that circle.', 'error')
        return redirect(url_for('main.home'))
    session['active_family_id'] = family_id
    return redirect(url_for('main.home'))


@main.route('/register', methods=['GET', 'POST'])
@limiter.limit('10 per hour', methods=['POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('main.home'))
    if not current_app.config.get('REGISTRATION_OPEN'):
        return render_template('registration_closed.html'), 403
    form = RegistrationForm()
    if form.validate_on_submit():
        if not form.family_name.data:
            flash('Please enter a family name.', 'error')
            return render_template('register.html', form=form)
        existing_user = User.query.filter_by(email=form.email.data).first()
        if existing_user:
            flash('An account with that email already exists.', 'error')
            return redirect(url_for('main.register'))
        account_id = 'pod_' + secrets.token_urlsafe(6)
        family = Family(name=form.family_name.data, account_id=account_id,
                        plan='trial', trial_ends_at=None)
        db.session.add(family)
        db.session.flush()
        full_name = f"{form.first_name.data} {form.last_name.data}"
        person = Person(
            name=full_name,
            email=form.email.data,
            phone=format_phone(form.phone.data),
            family_id=family.id,
        )
        db.session.add(person)
        db.session.flush()
        token = secrets.token_urlsafe(32)
        user = User(
            first_name=form.first_name.data,
            last_name=form.last_name.data,
            email=form.email.data,
            phone=format_phone(form.phone.data),
            verification_token=_hash_token(token),
            verification_token_expiry=datetime.utcnow() + timedelta(hours=24),
            email_verified=False,
            # Family creator is auto-approved — email verification is the only gate
            status='approved',
            is_admin=True,
            family_id=family.id,
            person_id=person.id,
        )
        user.set_password(form.password.data)
        db.session.add(user)
        db.session.flush()
        NotificationPreference.seed_defaults(user.id)
        _ensure_membership(user)
        db.session.commit()
        if current_app.config.get('MAIL_ENABLED'):
            send_verification_email(user, url_for('main.verify_email', token=token, _external=True))
            flash('Registration successful! Please check your email to verify your account.', 'info')
        else:
            user.email_verified = True
            family.trial_ends_at = datetime.utcnow() + timedelta(days=30)
            db.session.commit()
            flash('Registration successful! You can now sign in.', 'info')
        return redirect(url_for('main.login'))
    return render_template('register.html', form=form)

@main.route('/verify/<token>')
def verify_email(token):
    user = User.query.filter_by(verification_token=_hash_token(token)).first()
    if not user:
        flash('Invalid or expired verification link.', 'error')
        return redirect(url_for('main.login'))
    if user.verification_token_expiry and user.verification_token_expiry < datetime.utcnow():
        return redirect(url_for('main.resend_verification', email=user.email, expired='1'))
    user.email_verified = True
    user.verification_token = None
    user.verification_token_expiry = None
    if user.is_admin and user.family and user.family.plan == 'trial' and user.family.trial_ends_at is None:
        user.family.trial_ends_at = datetime.utcnow() + timedelta(days=30)
    db.session.commit()
    # Send Day 0 welcome email for new pod admins (families with an account_id)
    if current_app.config.get('MAIL_ENABLED') and user.is_admin and user.family and user.family.account_id:
        send_welcome_email(user, user.family, url_for('main.home', _external=True))
    flash('Email verified! You can now sign in.', 'info')
    return redirect(url_for('main.login'))

@main.route('/resend-verification', methods=['GET', 'POST'])
@limiter.limit('5 per hour', methods=['POST'])
def resend_verification():
    email = request.args.get('email', '') or request.form.get('email', '')
    expired = request.args.get('expired') == '1'
    if request.method == 'POST':
        user = User.query.filter_by(email=email).first()
        if user and not user.email_verified:
            token = secrets.token_urlsafe(32)
            user.verification_token = _hash_token(token)
            user.verification_token_expiry = datetime.utcnow() + timedelta(hours=24)
            db.session.commit()
            if current_app.config.get('MAIL_ENABLED'):
                send_verification_email(
                    user, url_for('main.verify_email', token=token, _external=True)
                )
        flash('Check your email — a new verification link has been sent.', 'info')
        return redirect(url_for('main.login'))
    return render_template('resend_verification.html', email=email, expired=expired)


@main.route('/register/invite/<token>', methods=['GET', 'POST'])
@limiter.limit('10 per hour', methods=['POST'])
def register_invited(token):
    invited_user = User.query.filter_by(invitation_token=_hash_token(token)).first()
    if not invited_user or invited_user.status != 'invited':
        flash('Invalid or expired invitation link.', 'error')
        return redirect(url_for('main.login'))
    if invited_user.invitation_token_expiry and invited_user.invitation_token_expiry < datetime.utcnow():
        flash('This invitation link has expired. Please ask to be re-invited.', 'error')
        return redirect(url_for('main.login'))

    form = RegistrationForm()

    if request.method == 'GET':
        form.first_name.data = invited_user.first_name
        form.last_name.data = invited_user.last_name
        form.email.data = invited_user.email

    if form.validate_on_submit():
        invited_user.first_name = form.first_name.data
        invited_user.last_name = form.last_name.data
        invited_user.phone = format_phone(form.phone.data)
        invited_user.set_password(form.password.data)
        invited_user.email_verified = True
        invited_user.invitation_token = None
        invited_user.invitation_token_expiry = None
        # Sync person name if first/last name changed during registration
        if invited_user.person:
            invited_user.person.name = f"{form.first_name.data} {form.last_name.data}"
        if invited_user.family and invited_user.family.require_member_approval:
            invited_user.status = 'pending'
            db.session.commit()
            if current_app.config.get('MAIL_ENABLED'):
                send_pending_notification(invited_user)
            flash('Account created! An admin will review and approve your access shortly.', 'info')
        else:
            invited_user.status = 'approved'
            NotificationPreference.seed_defaults(invited_user.id)
            _ensure_membership(invited_user)
            db.session.commit()
            _notify_new_member(invited_user)
            flash('Account created! You can now log in.', 'info')
        return redirect(url_for('main.login'))

    return render_template('register.html', form=form, invited=True)

@main.route('/admin/add-member', methods=['GET', 'POST'])
@login_required
@contributor_or_admin_required
def add_member():
    parent1_id = request.args.get('parent1_id', type=int)
    parent2_id = request.args.get('parent2_id', type=int)
    next_page = request.args.get('next')
    purpose = request.args.get('purpose')  # 'parent' = adding someone from outside the family
    parent1 = db.session.get(Person, parent1_id) if parent1_id else None
    parent2 = db.session.get(Person, parent2_id) if parent2_id else None
    if parent1 and parent1.family_id != current_user.active_family_id:
        parent1 = None
    if parent2 and parent2.family_id != current_user.active_family_id:
        parent2 = None
    form = AddPersonForm()
    form.gender.choices = GENDER_CHOICES_EXPANDED if current_user.active_family.has_lgbtq_options else GENDER_CHOICES_DEFAULT
    if form.validate_on_submit():
        first = form.first_name.data.strip()
        last  = form.last_name.data.strip()
        # Duplicate check — skip if user already confirmed
        if not request.form.get('confirm_duplicate'):
            existing = Person.query.filter_by(family_id=current_user.active_family_id).all()
            similar = []
            for p in existing:
                parts = p.name.lower().split()
                p_last  = parts[-1] if parts else ''
                p_first = parts[0]  if parts else ''
                same_last  = p_last  == last.lower()
                same_first = p_first == first.lower()
                close_first = (
                    len(p_first) >= 2 and len(first) >= 2 and
                    p_first[:3] == first.lower()[:3]
                )
                bday_match = (
                    form.birthday.data and p.birthday and
                    form.birthday.data == p.birthday
                )
                if same_last and (same_first or close_first or bday_match):
                    similar.append(p)
            if similar:
                link_spouse_for = request.args.get('link_spouse_for', type=int)
                person_count = Person.query.filter_by(family_id=current_user.active_family_id).count()
                has_paid_access = family_has_paid_access(current_user.active_family)
                return render_template('add_member.html', form=form, parent1=parent1,
                                       parent2=parent2, next_page=next_page, similar=similar,
                                       purpose=purpose, link_spouse_for=link_spouse_for,
                                       person_count=person_count, member_limit=FREE_MEMBER_LIMIT,
                                       has_paid_access=has_paid_access)
        if not family_has_paid_access(current_user.active_family):
            person_count = Person.query.filter_by(family_id=current_user.active_family_id).count()
            if person_count >= FREE_MEMBER_LIMIT:
                flash(f'Free plan is limited to {FREE_MEMBER_LIMIT} family members. '
                      'Upgrade to add unlimited members.', 'warning')
                return redirect(url_for('billing.billing_page'))
        person = Person(
            name=f"{first} {last}",
            family_id=current_user.active_family_id,
            email=form.email.data or None,
            phone=format_phone(form.phone.data),
            gender=form.gender.data or None,
            birthday=form.birthday.data,
            birthplace=format_birthplace(form.birthplace.data),
            nickname=form.nickname.data or None,
            maiden_name=form.maiden_name.data or None,
            notes=form.notes.data or None,
            in_directory=(purpose != 'parent'),
        )
        db.session.add(person)
        db.session.flush()
        if parent1:
            db.session.add(ParentRelationship(parent_id=parent1.id, child_id=person.id, role=_default_parent_role(parent1)))
        if parent2 and request.form.get('include_parent2'):
            db.session.add(ParentRelationship(parent_id=parent2.id, child_id=person.id, role=_default_parent_role(parent2)))
        db.session.commit()
        flash(f'{person.name} has been added to the family.', 'info')
        # One-step invite: if requested and we have an email, send the invitation
        # now instead of making the admin open the profile and click Invite.
        if request.form.get('invite_now') and person.email and not person.deathday:
            msg, category = _send_member_invite(person, person.email)
            flash(msg, category)
        link_spouse_for = request.form.get('link_spouse_for', type=int) or request.args.get('link_spouse_for', type=int)
        if link_spouse_for:
            return redirect(url_for('main.admin_link_spouse', person_id=link_spouse_for))
        return redirect(url_for('main.person_detail', person_id=person.id))
    link_spouse_for = request.args.get('link_spouse_for', type=int)
    person_count = Person.query.filter_by(family_id=current_user.active_family_id).count()
    has_paid_access = family_has_paid_access(current_user.active_family)
    return render_template('add_member.html', form=form, parent1=parent1, parent2=parent2,
                           next_page=next_page, purpose=purpose, link_spouse_for=link_spouse_for,
                           person_count=person_count, member_limit=FREE_MEMBER_LIMIT,
                           has_paid_access=has_paid_access)

@main.route('/person/<int:person_id>/add-parent', methods=['GET', 'POST'])
@login_required
def add_parent(person_id):
    person = db.session.get(Person, person_id)
    if not person or person.family_id != current_user.active_family_id:
        flash('Person not found.', 'error')
        return redirect(url_for('main.home'))
    can_edit = current_user.active_is_admin or (person.user and person.user == current_user)
    if not can_edit:
        flash('You do not have permission to edit this profile.', 'error')
        return redirect(url_for('main.person_detail', person_id=person_id))
    existing_parent_ids = {p.id for p in person.parents}
    eligible = [
        p for p in Person.query.filter_by(family_id=current_user.active_family_id).order_by(Person.name).all()
        if p.id != person.id and p.id not in existing_parent_ids
    ]
    form = AddParentForm()
    form.relative_id.choices = [(0, '-- Select --')] + [(p.id, p.get_display_name()) for p in eligible]
    if form.validate_on_submit():
        parent_person = db.session.get(Person, form.relative_id.data)
        if not parent_person or parent_person.family_id != current_user.active_family_id:
            flash('Person not found.', 'error')
            return redirect(url_for('main.add_parent', person_id=person_id))
        db.session.add(ParentRelationship(parent_id=parent_person.id, child_id=person.id, role=form.role.data))
        db.session.commit()
        flash(f'{parent_person.get_display_name()} added as {form.role.data.replace("_", " ")}.', 'info')
        return redirect(url_for('main.person_detail', person_id=person_id))
    return render_template('add_relative.html', form=form, subject=person, action='parent')

@main.route('/person/<int:person_id>/add-child', methods=['GET', 'POST'])
@login_required
def add_child(person_id):
    person = db.session.get(Person, person_id)
    if not person or person.family_id != current_user.active_family_id:
        flash('Person not found.', 'error')
        return redirect(url_for('main.home'))
    can_edit = current_user.active_is_admin or (person.user and person.user == current_user)
    if not can_edit:
        flash('You do not have permission to edit this profile.', 'error')
        return redirect(url_for('main.person_detail', person_id=person_id))
    existing_child_ids = {c.id for c in person.children}
    eligible = [
        p for p in Person.query.filter_by(family_id=current_user.active_family_id).order_by(Person.name).all()
        if p.id != person.id and p.id not in existing_child_ids
    ]
    form = RelativeForm()
    form.relative_id.choices = [(0, '-- Select --')] + [(p.id, p.get_display_name()) for p in eligible]
    next_page = request.args.get('next')
    if form.validate_on_submit():
        child_person = db.session.get(Person, form.relative_id.data)
        if not child_person or child_person.family_id != current_user.active_family_id:
            flash('Person not found.', 'error')
            return redirect(url_for('main.add_child', person_id=person_id))
        db.session.add(ParentRelationship(parent_id=person.id, child_id=child_person.id, role=_default_parent_role(person)))
        db.session.commit()
        flash(f'{child_person.get_display_name()} added as a child.', 'info')
        return redirect(url_for('main.person_detail', person_id=person_id))
    return render_template('add_relative.html', form=form, subject=person, action='child', next_page=next_page)

@main.route('/person/<int:person_id>/remove-parent/<int:parent_id>', methods=['POST'])
@login_required
def remove_parent(person_id, parent_id):
    person = db.session.get(Person, person_id)
    if not person or person.family_id != current_user.active_family_id:
        flash('Person not found.', 'error')
        return redirect(url_for('main.home'))
    can_edit = current_user.active_is_admin or (person.user and person.user == current_user)
    if not can_edit:
        flash('You do not have permission to edit this profile.', 'error')
        return redirect(url_for('main.person_detail', person_id=person_id))
    parent_person = db.session.get(Person, parent_id)
    if parent_person and parent_person.family_id == current_user.active_family_id:
        ParentRelationship.query.filter_by(parent_id=parent_person.id, child_id=person.id).delete()
        db.session.commit()
        flash(f'{parent_person.get_display_name()} removed as a parent.', 'info')
    return redirect(url_for('main.person_detail', person_id=person_id))

SPOUSE_ROLES = [('husband', 'Husband'), ('wife', 'Wife'), ('spouse', 'Spouse'), ('partner', 'Partner')]

@main.route('/person/<int:person_id>/set-spouse-role', methods=['POST'])
@login_required
def set_spouse_role(person_id):
    person = db.session.get(Person, person_id)
    if not person or person.family_id != current_user.active_family_id:
        return redirect(url_for('main.home'))
    can_edit = current_user.active_is_admin or (person.user and person.user == current_user)
    if not can_edit:
        return redirect(url_for('main.person_detail', person_id=person_id))
    role = request.form.get('role', 'spouse')
    valid = {r for r, _ in SPOUSE_ROLES}
    if role not in valid:
        role = 'spouse'
    sr = person.get_active_spouse_relationship()
    if sr:
        sr.set_role_for(person, role)
        db.session.commit()
    return redirect(url_for('main.person_detail', person_id=person_id))

@main.route('/person/<int:person_id>/set-parent-role/<int:parent_id>', methods=['POST'])
@login_required
def set_parent_role(person_id, parent_id):
    person = db.session.get(Person, person_id)
    if not person or person.family_id != current_user.active_family_id:
        return redirect(url_for('main.home'))
    can_edit = current_user.active_is_admin or (person.user and person.user == current_user)
    if not can_edit:
        return redirect(url_for('main.person_detail', person_id=person_id))
    role = request.form.get('role', 'parent')
    valid_roles = {r for r, _ in PARENT_ROLES}
    if role not in valid_roles:
        role = 'parent'
    parent_person = db.session.get(Person, parent_id)
    if not parent_person or parent_person.family_id != current_user.active_family_id:
        return redirect(url_for('main.person_detail', person_id=person_id))
    pr = ParentRelationship.query.filter_by(parent_id=parent_id, child_id=person_id).first()
    if pr:
        pr.role = role
        db.session.commit()
    return redirect(url_for('main.person_detail', person_id=person_id))

@main.route('/person/<int:person_id>/remove-child/<int:child_id>', methods=['POST'])
@login_required
def remove_child(person_id, child_id):
    person = db.session.get(Person, person_id)
    if not person or person.family_id != current_user.active_family_id:
        flash('Person not found.', 'error')
        return redirect(url_for('main.home'))
    can_edit = current_user.active_is_admin or (person.user and person.user == current_user)
    if not can_edit:
        flash('You do not have permission to edit this profile.', 'error')
        return redirect(url_for('main.person_detail', person_id=person_id))
    child_person = db.session.get(Person, child_id)
    if child_person and child_person.family_id == current_user.active_family_id:
        ParentRelationship.query.filter_by(parent_id=person.id, child_id=child_person.id).delete()
        db.session.commit()
        flash(f'{child_person.get_display_name()} removed as a child.', 'info')
    return redirect(url_for('main.person_detail', person_id=person_id))

@main.route('/admin/family', methods=['GET', 'POST'])
@login_required
@admin_required
def family_settings():
    family = current_user.family
    form = FamilySettingsForm()
    if request.method == 'GET':
        form.family_name.data = family.name
        form.require_member_approval.data = family.require_member_approval
        form.has_lgbtq_options.data = family.has_lgbtq_options
        form.enable_polls.data = family.enable_polls
        form.enable_greeting_cards.data = family.enable_greeting_cards
        form.enable_chat.data = family.enable_chat
        form.enable_stories.data = family.enable_stories
    if form.validate_on_submit():
        family.name = form.family_name.data
        family.require_member_approval = form.require_member_approval.data
        family.has_lgbtq_options = form.has_lgbtq_options.data
        family.enable_polls = form.enable_polls.data
        family.enable_greeting_cards = form.enable_greeting_cards.data
        family.enable_chat = form.enable_chat.data
        family.enable_stories = form.enable_stories.data
        db.session.commit()
        flash('Family settings saved.', 'info')
        return redirect(url_for('main.family_settings'))
    return render_template('family_settings.html', form=form, family=family)


@main.route('/forgot-password', methods=['GET', 'POST'])
@limiter.limit('10 per hour', methods=['POST'])
def forgot_password():
    if current_user.is_authenticated:
        return redirect(url_for('main.home'))
    form = ForgotPasswordForm()
    if form.validate_on_submit():
        user = User.query.filter_by(email=form.email.data).first()
        if user and user.status == 'approved':
            token = secrets.token_urlsafe(32)
            user.reset_token = _hash_token(token)
            user.reset_token_expiry = datetime.utcnow() + timedelta(hours=1)
            db.session.commit()
            reset_url = url_for('main.reset_password', token=token, _external=True)
            if current_app.config.get('MAIL_ENABLED'):
                send_password_reset_email(user, reset_url)
            else:
                flash(f'Dev mode — reset link: {reset_url}', 'info')
        # Always show this message to prevent email enumeration
        flash('If that email is registered, a reset link has been sent.', 'info')
        return redirect(url_for('main.login'))
    return render_template('forgot_password.html', form=form)

@main.route('/reset-password/<token>', methods=['GET', 'POST'])
@limiter.limit('10 per hour', methods=['POST'])
def reset_password(token):
    if current_user.is_authenticated:
        return redirect(url_for('main.home'))
    user = User.query.filter_by(reset_token=_hash_token(token)).first()
    if not user or (user.reset_token_expiry and user.reset_token_expiry < datetime.utcnow()):
        flash('This reset link is invalid or has expired.', 'error')
        return redirect(url_for('main.forgot_password'))
    form = ResetPasswordForm()
    if form.validate_on_submit():
        user.set_password(form.password.data)
        user.reset_token = None
        user.reset_token_expiry = None
        db.session.commit()
        flash('Password reset successfully. You can now sign in.', 'info')
        return redirect(url_for('main.login'))
    return render_template('reset_password.html', form=form)

ALLOWED_PHOTO_EXTS = {'jpg', 'jpeg', 'png', 'webp', 'gif', 'heic'}

def _save_photo_file(file, album_id):
    """Returns (key, thumb_key) or None on rejected file."""
    result = upload_photo(file, folder=f'albums/{album_id}', with_thumb=True)
    return result if result else None

@main.route('/albums')
@login_required
def albums():
    all_albums = Album.query.filter_by(family_id=current_user.active_family_id)\
        .order_by(Album.created_at.desc()).all()
    form = AlbumForm()
    events = Event.query.filter_by(family_id=current_user.active_family_id).order_by(Event.start_date.desc()).all()
    form.event_id.choices = [(0, '-- None --')] + [(e.id, e.name) for e in events]
    return render_template('albums_list.html', albums=all_albums, form=form,
                           has_paid_access=family_has_paid_access(current_user.family))

@main.route('/albums/add', methods=['POST'])
@login_required
@contributor_or_admin_required
@requires_plan
def add_album():
    events = Event.query.filter_by(family_id=current_user.active_family_id).all()
    form = AlbumForm()
    form.event_id.choices = [(0, '-- None --')] + [(e.id, e.name) for e in events]
    if form.validate_on_submit():
        album = Album(
            family_id=current_user.active_family_id,
            created_by_id=current_user.person.id if current_user.person else None,
            name=form.name.data.strip(),
            description=form.description.data or None,
            year=form.year.data or None,
            event_id=form.event_id.data or None,
        )
        db.session.add(album)
        db.session.commit()
        flash(f'Album "{album.name}" created.', 'info')
        return redirect(url_for('main.album_detail', album_id=album.id))
    return redirect(url_for('main.albums'))

@main.route('/albums/<int:album_id>')
@login_required
def album_detail(album_id):
    album = db.session.get(Album, album_id)
    if not album or album.family_id != current_user.active_family_id:
        flash('Album not found.', 'error')
        return redirect(url_for('main.albums'))
    upload_form = PhotoUploadForm()
    people = Person.query.filter_by(
        family_id=current_user.active_family_id, in_directory=True
    ).order_by(Person.name).all()
    return render_template('album_detail.html', album=album, upload_form=upload_form,
                           people=people)

@main.route('/albums/<int:album_id>/upload', methods=['POST'])
@login_required
@contributor_or_admin_required
@requires_plan
def upload_photos(album_id):
    album = db.session.get(Album, album_id)
    if not album or album.family_id != current_user.active_family_id:
        return redirect(url_for('main.albums'))
    files = request.files.getlist('photos')
    caption = request.form.get('caption', '').strip() or None
    count = 0
    for file in files:
        if file and file.filename:
            result = _save_photo_file(file, album_id)
            if result:
                path, thumb_path = result
                photo = Photo(
                    album_id=album_id,
                    family_id=current_user.active_family_id,
                    uploaded_by_id=current_user.person.id if current_user.person else None,
                    path=path,
                    thumb_path=thumb_path,
                    caption=caption,
                )
                db.session.add(photo)
                count += 1
    if count:
        db.session.commit()
        flash(f'{count} photo{"s" if count != 1 else ""} uploaded.', 'info')
        from ..notifications import notify_family
        actor = current_user.person.get_display_name() if current_user.person else 'Someone'
        notify_family(
            current_user.active_family_id, 'new_photos',
            title=f'{actor} added {count} photo{"s" if count != 1 else ""} to {album.name}',
            url=url_for('main.album_detail', album_id=album_id),
            exclude_user_id=current_user.id,
        )
    return redirect(url_for('main.album_detail', album_id=album_id))

@main.route('/events/<int:event_id>/photos/upload', methods=['POST'])
@login_required
@requires_plan
def event_upload_photos(event_id):
    event = db.session.get(Event, event_id)
    if not event or event.family_id != current_user.active_family_id:
        return redirect(url_for('main.events_list'))
    # Find or auto-create the primary event album
    album = Album.query.filter_by(event_id=event_id, family_id=current_user.active_family_id).first()
    if not album:
        album = Album(
            family_id=current_user.active_family_id,
            created_by_id=current_user.person.id if current_user.person else None,
            name=event.name,
            event_id=event_id,
        )
        db.session.add(album)
        db.session.flush()
    files = request.files.getlist('photos')
    count = 0
    for file in files:
        if file and file.filename:
            ext = file.filename.rsplit('.', 1)[-1].lower() if '.' in file.filename else ''
            if ext in ALLOWED_PHOTO_EXTS:
                result = _save_photo_file(file, album.id)
                if result:
                    path, thumb_path = result
                    db.session.add(Photo(
                        album_id=album.id,
                        family_id=current_user.active_family_id,
                        uploaded_by_id=current_user.person.id if current_user.person else None,
                        path=path,
                        thumb_path=thumb_path,
                    ))
                    count += 1
    if count:
        db.session.commit()
        flash(f'{count} photo{"s" if count != 1 else ""} added.', 'info')
    else:
        db.session.rollback()
    return redirect(url_for('main.event_detail', event_id=event_id))


@main.route('/albums/<int:album_id>/download')
@login_required
def download_album(album_id):
    album = db.session.get(Album, album_id)
    if not album or album.family_id != current_user.active_family_id:
        return redirect(url_for('main.albums'))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for photo in album.photos:
            try:
                data, _ = get_object_bytes(photo.path)
                zf.writestr(os.path.basename(photo.path), data)
            except Exception:
                pass
    buf.seek(0)
    safe_name = ''.join(c if c.isalnum() or c in ' -_' else '_' for c in album.name)
    return send_file(buf, mimetype='application/zip',
                     as_attachment=True, download_name=f'{safe_name}.zip')

@main.route('/albums/<int:album_id>/photos/<int:photo_id>/delete', methods=['POST'])
@login_required
def delete_photo(album_id, photo_id):
    photo = db.session.get(Photo, photo_id)
    if not photo or photo.family_id != current_user.active_family_id:
        return redirect(url_for('main.album_detail', album_id=album_id))
    can_delete = current_user.active_is_admin or (current_user.person and photo.uploaded_by_id == current_user.person.id)
    if can_delete:
        delete_object(photo.path)
        delete_object(photo.thumb_path)
        db.session.delete(photo)
        db.session.commit()
        flash('Photo deleted.', 'info')
    return redirect(url_for('main.album_detail', album_id=album_id))

@main.route('/photos/<int:photo_id>/tag', methods=['POST'])
@login_required
def photo_tag(photo_id):
    from ..models import PhotoTag
    photo = db.session.get(Photo, photo_id)
    if not photo or photo.family_id != current_user.active_family_id:
        abort(404)
    person_id = request.form.get('person_id', type=int)
    if person_id:
        person = db.session.get(Person, person_id)
        if person and person.family_id == current_user.active_family_id:
            existing = PhotoTag.query.filter_by(photo_id=photo_id, person_id=person_id).first()
            if not existing:
                db.session.add(PhotoTag(
                    photo_id=photo_id, person_id=person_id,
                    tagged_by_id=current_user.person.id if current_user.person else None,
                ))
                db.session.commit()
    open_idx = request.form.get('open_idx', '')
    return redirect(url_for('main.album_detail', album_id=photo.album_id,
                            _anchor=f'photo-{photo_id}') + (f'?open={open_idx}' if open_idx else ''))


@main.route('/photos/<int:photo_id>/tags/<int:tag_id>/remove', methods=['POST'])
@login_required
def photo_untag(photo_id, tag_id):
    from ..models import PhotoTag
    tag = db.session.get(PhotoTag, tag_id)
    if not tag or tag.photo.family_id != current_user.active_family_id:
        abort(404)
    is_tagger = current_user.person and tag.tagged_by_id == current_user.person.id
    is_tagged = current_user.person and tag.person_id == current_user.person.id
    if current_user.active_is_admin or is_tagger or is_tagged:
        photo = tag.photo
        db.session.delete(tag)
        db.session.commit()
        open_idx = request.form.get('open_idx', '')
        return redirect(url_for('main.album_detail', album_id=photo.album_id)
                        + (f'?open={open_idx}' if open_idx else ''))
    abort(403)


@main.route('/members/<int:person_id>/photos')
@login_required
def person_photos(person_id):
    from ..models import PhotoTag
    person = db.session.get(Person, person_id)
    if not person or person.family_id != current_user.active_family_id:
        abort(404)
    tags = PhotoTag.query.filter_by(person_id=person_id)\
        .join(Photo).filter(Photo.family_id == current_user.active_family_id)\
        .order_by(Photo.created_at.desc()).all()
    photos = [t.photo for t in tags]
    return render_template('person_photos.html', person=person, photos=photos)


@main.route('/albums/<int:album_id>/edit', methods=['POST'])
@login_required
@admin_required
def edit_album(album_id):
    album = db.session.get(Album, album_id)
    if not album or album.family_id != current_user.active_family_id:
        return redirect(url_for('main.albums'))
    name = request.form.get('name', '').strip()
    if name:
        album.name = name
    album.description = request.form.get('description', '').strip() or None
    year_str = request.form.get('year', '').strip()
    album.year = int(year_str) if year_str.isdigit() else None
    db.session.commit()
    flash('Album updated.', 'info')
    return redirect(url_for('main.album_detail', album_id=album_id))


@main.route('/photos/<int:photo_id>/caption', methods=['POST'])
@login_required
def photo_caption(photo_id):
    photo = db.session.get(Photo, photo_id)
    if not photo or photo.family_id != current_user.active_family_id:
        abort(404)
    can_edit = current_user.active_is_admin or (
        current_user.person and photo.uploaded_by_id == current_user.person.id)
    if not can_edit:
        abort(403)
    caption = request.form.get('caption', '').strip() or None
    photo.caption = caption
    db.session.commit()
    return jsonify({'caption': caption or ''})


@main.route('/albums/<int:album_id>/delete', methods=['POST'])
@login_required
@admin_required
def delete_album(album_id):
    album = db.session.get(Album, album_id)
    if not album or album.family_id != current_user.active_family_id:
        return redirect(url_for('main.albums'))
    for photo in album.photos:
        delete_object(photo.path)
        delete_object(photo.thumb_path)
    db.session.delete(album)
    db.session.commit()
    flash('Album deleted.', 'info')
    return redirect(url_for('main.albums'))

# ── Announcements ──────────────────────────────────────────────────────────────

def _rel_time(dt):
    delta = datetime.utcnow() - dt
    if delta.days == 0:
        h = delta.seconds // 3600
        if h == 0:
            m = delta.seconds // 60
            return "just now" if m == 0 else f"{m}m ago"
        return f"{h}h ago"
    if delta.days == 1:
        return "yesterday"
    if delta.days < 7:
        return f"{delta.days} days ago"
    return dt.strftime('%B %-d, %Y')

@main.route('/announcements')
@login_required
def announcements():
    items = Announcement.query.filter_by(family_id=current_user.active_family_id)\
        .order_by(Announcement.pinned.desc(), Announcement.created_at.desc()).all()
    my_person_id = current_user.person.id if current_user.person else None
    reaction_data = {}
    for a in items:
        emojis = {}
        for r in a.reactions:
            if r.emoji not in emojis:
                emojis[r.emoji] = {'count': 0, 'mine': False}
            emojis[r.emoji]['count'] += 1
            if r.person_id == my_person_id:
                emojis[r.emoji]['mine'] = True
        reaction_data[a.id] = emojis
    time_labels = {a.id: _rel_time(a.created_at) for a in items}
    form = AnnouncementForm()
    return render_template('announcements.html', announcements=items, form=form,
                           reaction_data=reaction_data, time_labels=time_labels)

@main.route('/announcements/add', methods=['POST'])
@login_required
@contributor_or_admin_required
def add_announcement():
    form = AnnouncementForm()
    if form.validate_on_submit():
        a = Announcement(
            family_id=current_user.active_family_id,
            author_id=current_user.person.id if current_user.person else None,
            title=form.title.data.strip(),
            body=form.body.data.strip(),
            pinned=form.pinned.data and current_user.active_is_admin,
        )
        db.session.add(a)
        db.session.commit()
        flash('Announcement posted.', 'info')
        from ..notifications import notify
        recipients = User.query.filter_by(
            family_id=current_user.active_family_id, status='approved'
        ).filter(User.id != current_user.id).all()
        ann_url = url_for('main.announcements', _external=True)
        notify(recipients, 'announcement', announcement=a, url=ann_url)
    return redirect(url_for('main.announcements'))

@main.route('/announcements/<int:ann_id>/pin', methods=['POST'])
@login_required
@admin_required
def pin_announcement(ann_id):
    a = db.session.get(Announcement, ann_id)
    if a and a.family_id == current_user.active_family_id:
        a.pinned = not a.pinned
        db.session.commit()
    return redirect(url_for('main.announcements'))

@main.route('/announcements/<int:ann_id>/react', methods=['POST'])
@login_required
def react_announcement(ann_id):
    from ..models import AnnouncementReaction
    wants_json = 'application/json' in request.headers.get('Accept', '')
    a = db.session.get(Announcement, ann_id)
    if not a or a.family_id != current_user.active_family_id:
        return (jsonify({'error': 'not found'}), 404) if wants_json else redirect(url_for('main.announcements'))
    emoji = request.form.get('emoji', '')[:10]
    if not emoji or not current_user.person:
        return (jsonify({'error': 'invalid'}), 400) if wants_json else redirect(url_for('main.announcements'))
    existing = AnnouncementReaction.query.filter_by(
        announcement_id=ann_id, person_id=current_user.person.id, emoji=emoji
    ).first()
    if existing:
        db.session.delete(existing)
        mine = False
    else:
        db.session.add(AnnouncementReaction(
            announcement_id=ann_id, person_id=current_user.person.id, emoji=emoji
        ))
        mine = True
    db.session.commit()
    if wants_json:
        count = AnnouncementReaction.query.filter_by(announcement_id=ann_id, emoji=emoji).count()
        return jsonify({'emoji': emoji, 'count': count, 'mine': mine})
    return redirect(url_for('main.announcements'))


@main.route('/announcements/<int:ann_id>/delete', methods=['POST'])
@login_required
def delete_announcement(ann_id):
    a = db.session.get(Announcement, ann_id)
    if not a or a.family_id != current_user.active_family_id:
        return redirect(url_for('main.announcements'))
    can_delete = current_user.active_is_admin or (current_user.person and a.author_id == current_user.person.id)
    if can_delete:
        db.session.delete(a)
        db.session.commit()
        flash('Announcement deleted.', 'info')
    return redirect(url_for('main.announcements'))

@main.route('/announcements/<int:ann_id>/edit', methods=['POST'])
@login_required
def edit_announcement(ann_id):
    a = db.session.get(Announcement, ann_id)
    if not a or a.family_id != current_user.active_family_id:
        return redirect(url_for('main.announcements'))
    can_edit = current_user.active_is_admin or (current_user.person and a.author_id == current_user.person.id)
    if not can_edit:
        return redirect(url_for('main.announcements'))
    title = request.form.get('title', '').strip()
    body = request.form.get('body', '').strip()
    if title and body:
        a.title = title
        a.body = body
        db.session.commit()
    return redirect(url_for('main.announcements'))

def _notify_new_member(new_user):
    """Tell the rest of the family that someone just joined."""
    from ..notifications import notify_family
    name = new_user.get_full_name()
    fam = new_user.family
    url = url_for('main.person_detail', person_id=new_user.person_id) if new_user.person_id else None
    notify_family(
        new_user.family_id, 'new_member',
        title=f'{name} joined {fam.name if fam else "the family"}',
        url=url,
        exclude_user_id=new_user.id,
    )


@main.route('/admin/users')
@login_required
@admin_required
def admin_users():
    pending = User.query.filter_by(status='pending', family_id=current_user.active_family_id).all()
    people = Person.query.filter_by(family_id=current_user.active_family_id).order_by(Person.name).all()
    non_directory = Person.query.filter_by(family_id=current_user.active_family_id, in_directory=False).order_by(Person.name).all()
    return render_template('admin_users.html', pending=pending, people=people, non_directory=non_directory)


@main.route('/admin/merge-people', methods=['GET', 'POST'])
@login_required
@admin_required
def merge_people():
    """Admin tool to merge two duplicate Person records. Two-step: preview
    (runs the merge in a transaction and rolls back) → confirm (commits)."""
    from ..people_merge import merge_person_records
    fid = current_user.active_family_id
    people = Person.query.filter_by(family_id=fid).order_by(Person.name).all()
    keep_id = request.values.get('keep_id', type=int)
    remove_id = request.values.get('remove_id', type=int)
    preview = None
    if request.method == 'POST':
        keep = db.session.get(Person, keep_id) if keep_id else None
        remove = db.session.get(Person, remove_id) if remove_id else None
        if (not keep or not remove or keep.family_id != fid or remove.family_id != fid):
            flash('Pick two people from this family.', 'error')
        elif keep_id == remove_id:
            flash('Pick two different people to merge.', 'error')
        elif request.form.get('confirm') == '1':
            keep_name, remove_name = keep.name, remove.name
            try:
                merge_person_records(keep, remove)
                db.session.commit()
                flash(f'Merged "{remove_name}" into "{keep_name}". '
                      'The duplicate has been removed and all its history kept.', 'info')
                return redirect(url_for('main.admin_users'))
            except ValueError as e:
                db.session.rollback()
                flash(str(e), 'error')
        else:
            try:
                preview = merge_person_records(keep, remove)  # collect actions…
            except ValueError as e:
                flash(str(e), 'error')
            db.session.rollback()  # …then undo: this was only a preview
    return render_template('merge_people.html', people=people,
                           keep_id=keep_id, remove_id=remove_id, preview=preview)


@main.route('/admin/cancel-invite/<int:user_id>', methods=['POST'])
@login_required
@admin_required
def cancel_invite(user_id):
    """Delete a pending (invited, not-yet-registered) account. The person stays
    in the family tree; only the dangling invitation is removed."""
    user = db.session.get(User, user_id)
    if not user or user.family_id != current_user.active_family_id or user.status != 'invited':
        flash('Pending invitation not found.', 'error')
        return redirect(url_for('main.admin_users'))
    name = user.get_full_name()
    # Clean up dependent rows first — an invited account can still have
    # notifications etc., and notifications.user_id is NOT NULL, so a bare
    # delete makes SQLAlchemy try to null them and Postgres rejects it.
    from ..account import _scrub_user_rows
    _scrub_user_rows(user)
    db.session.delete(user)
    db.session.commit()
    flash(f'Invitation to {name} cancelled. You can re-invite them anytime.', 'info')
    return redirect(url_for('main.admin_users'))


@main.route('/admin/approve/<int:user_id>', methods=['POST'])
@login_required
@admin_required
def approve_user(user_id):
    user = db.session.get(User, user_id)
    if not user or user.family_id != current_user.active_family_id:
        flash('User not found.', 'error')
        return redirect(url_for('main.admin_users'))
    user.status = 'approved'
    user.approved_by_id = current_user.id
    user.approved_date = date.today()
    NotificationPreference.seed_defaults(user.id)
    _ensure_membership(user)
    db.session.commit()
    _notify_new_member(user)
    if current_app.config.get('MAIL_ENABLED'):
        send_approval_notification(user, url_for('main.login', _external=True))
    flash(f'{user.get_full_name()} has been approved.', 'info')
    return redirect(url_for('main.admin_users'))

@main.route('/admin/reject/<int:user_id>', methods=['POST'])
@login_required
@admin_required
def reject_user(user_id):
    user = db.session.get(User, user_id)
    if not user or user.family_id != current_user.active_family_id:
        flash('User not found.', 'error')
        return redirect(url_for('main.admin_users'))
    user.status = 'rejected'
    db.session.commit()
    flash(f'{user.get_full_name()} has been rejected.', 'info')
    return redirect(url_for('main.admin_users'))

@main.route('/admin/set-role/<int:user_id>', methods=['POST'])
@login_required
@admin_required
def set_role(user_id):
    user = db.session.get(User, user_id)
    if not user or user.family_id != current_user.active_family_id or user.id == current_user.id:
        flash('Invalid request.', 'error')
        return redirect(url_for('main.admin_users'))
    role = request.form.get('role')
    user.is_admin = (role == 'admin')
    user.is_delegate = (role == 'contributor')
    membership_role = 'admin' if role == 'admin' else ('delegate' if role == 'contributor' else 'member')
    membership = UserPodMembership.query.filter_by(
        user_id=user.id, family_id=current_user.active_family_id
    ).first()
    if membership:
        membership.role = membership_role
    db.session.commit()
    flash(f'{user.get_full_name()} is now a {role or "member"}.', 'info')
    return redirect(url_for('main.admin_users'))

@main.route('/admin/remove/<int:user_id>', methods=['POST'])
@login_required
@admin_required
def remove_user(user_id):
    user = db.session.get(User, user_id)
    if not user or user.family_id != current_user.active_family_id:
        flash('User not found.', 'error')
        return redirect(url_for('main.admin_users'))
    if user.id == current_user.id:
        flash('You cannot remove your own account.', 'error')
        return redirect(url_for('main.admin_users'))
    if user.is_admin:
        admin_count = User.query.filter_by(
            family_id=current_user.active_family_id,
            is_admin=True,
            status='approved',
        ).count()
        if admin_count <= 1:
            flash('Cannot remove the only admin. Promote another member first.', 'error')
            return redirect(url_for('main.admin_users'))
    user.status = 'removed'
    db.session.commit()
    flash(f'{user.get_full_name()} has been removed.', 'info')
    return redirect(url_for('main.admin_users'))


@main.route('/admin/restore/<int:user_id>', methods=['POST'])
@login_required
@admin_required
def restore_user(user_id):
    user = db.session.get(User, user_id)
    if not user or user.family_id != current_user.active_family_id:
        flash('User not found.', 'error')
        return redirect(url_for('main.admin_users'))
    user.status = 'approved'
    db.session.commit()
    flash(f'{user.get_full_name()} has been restored.', 'info')
    return redirect(url_for('main.admin_users'))


@main.route('/admin/toggle-directory/<int:person_id>', methods=['POST'])
@login_required
@admin_required
def toggle_directory(person_id):
    person = db.session.get(Person, person_id)
    if not person or person.family_id != current_user.active_family_id:
        flash('Person not found.', 'error')
        return redirect(url_for('main.admin_users'))
    person.in_directory = not person.in_directory
    db.session.commit()
    return redirect(url_for('main.admin_users'))

@main.route('/admin/locations')
@login_required
@admin_required
def admin_locations():
    locations = (Location.query
                 .filter_by(family_id=current_user.active_family_id)
                 .order_by(Location.name)
                 .all())
    return render_template('locations.html', locations=locations)


@main.route('/admin/locations/add', methods=['POST'])
@login_required
@admin_required
def location_add():
    name = request.form.get('name', '').strip()
    address = request.form.get('address', '').strip() or None
    notes = request.form.get('notes', '').strip() or None
    if not name:
        flash('Location name is required.', 'error')
        return redirect(url_for('main.admin_locations'))
    lat, lng = _geocode_location(address) if address else (None, None)
    loc = Location(
        family_id=current_user.active_family_id,
        name=name,
        address=address,
        lat=lat,
        lng=lng,
        notes=notes,
    )
    db.session.add(loc)
    db.session.commit()
    flash(f'"{name}" added.', 'info')
    return redirect(url_for('main.admin_locations'))


@main.route('/admin/locations/<int:loc_id>/edit', methods=['POST'])
@login_required
@admin_required
def location_edit(loc_id):
    loc = db.session.get(Location, loc_id)
    if not loc or loc.family_id != current_user.active_family_id:
        flash('Location not found.', 'error')
        return redirect(url_for('main.admin_locations'))
    name = request.form.get('name', '').strip()
    address = request.form.get('address', '').strip() or None
    notes = request.form.get('notes', '').strip() or None
    if not name:
        flash('Location name is required.', 'error')
        return redirect(url_for('main.admin_locations'))
    if address != loc.address:
        loc.lat, loc.lng = _geocode_location(address) if address else (None, None)
    loc.name = name
    loc.address = address
    loc.notes = notes
    db.session.commit()
    flash(f'"{name}" updated.', 'info')
    return redirect(url_for('main.admin_locations'))


@main.route('/admin/locations/<int:loc_id>/delete', methods=['POST'])
@login_required
@admin_required
def location_delete(loc_id):
    loc = db.session.get(Location, loc_id)
    if not loc or loc.family_id != current_user.active_family_id:
        flash('Location not found.', 'error')
        return redirect(url_for('main.admin_locations'))
    # Detach any events linked to this location before deleting
    Event.query.filter_by(location_id=loc_id).update({'location_id': None})
    db.session.delete(loc)
    db.session.commit()
    flash('Location deleted.', 'info')
    return redirect(url_for('main.admin_locations'))


@main.route('/admin/locations/<int:loc_id>/spots/add', methods=['POST'])
@login_required
@admin_required
def location_spot_add(loc_id):
    from ..models import LocationSleepingSpot
    loc = db.session.get(Location, loc_id)
    if not loc or loc.family_id != current_user.active_family_id:
        flash('Location not found.', 'error')
        return redirect(url_for('main.admin_locations'))
    name = request.form.get('name', '').strip()
    if not name:
        flash('Room name is required.', 'error')
        return redirect(url_for('main.admin_locations'))
    spot_type = request.form.get('spot_type', '').strip() or None
    if spot_type and spot_type not in SPOT_TYPES:
        spot_type = None
    try:
        capacity = int(request.form.get('capacity', '') or 0) or None
    except ValueError:
        capacity = None
    sort_order = len(loc.sleeping_spots)
    db.session.add(LocationSleepingSpot(
        location_id=loc_id, name=name, spot_type=spot_type,
        capacity=capacity, sort_order=sort_order,
    ))
    db.session.commit()
    return redirect(url_for('main.admin_locations'))


@main.route('/admin/locations/<int:loc_id>/spots/<int:spot_id>/delete', methods=['POST'])
@login_required
@admin_required
def location_spot_delete(loc_id, spot_id):
    from ..models import LocationSleepingSpot
    loc = db.session.get(Location, loc_id)
    spot = db.session.get(LocationSleepingSpot, spot_id)
    if not loc or loc.family_id != current_user.active_family_id or not spot or spot.location_id != loc_id:
        flash('Room not found.', 'error')
        return redirect(url_for('main.admin_locations'))
    db.session.delete(spot)
    db.session.commit()
    return redirect(url_for('main.admin_locations'))


INVITE_VALID_DAYS = 30  # how long a member invitation link stays valid


def _email_invite_link(invited_user, email):
    """Mint a fresh token on an invited User, persist it, and email the link.
    Used for both first-time invites and resends.

    Returns True only if the invitation email was actually accepted by the
    mail provider. Returns False when mail is disabled or the send failed —
    so callers can tell the admin the truth instead of claiming "sent" when
    nothing left the building."""
    token = secrets.token_urlsafe(32)
    invited_user.invitation_token = _hash_token(token)
    invited_user.invitation_token_expiry = datetime.utcnow() + timedelta(days=INVITE_VALID_DAYS)
    db.session.commit()
    if not current_app.config.get('MAIL_ENABLED'):
        return False
    inviting_name = (current_user.person.get_display_name()
                     if current_user.person else current_user.get_full_name())
    return bool(send_member_invitation_email(
        inviting_name, invited_user.first_name, current_user.active_family.name,
        email, url_for('main.register_invited', token=token, _external=True)
    ))


def _invite_result_message(email, sent, resend=False):
    """Build an honest (message, category) for an invite attempt."""
    if sent:
        return (f'Invitation {"resent" if resend else "sent"} to {email}.', 'info')
    return (f"Couldn't send the invitation email to {email}. The account is "
            f"ready — check mail settings, then use Resend.", 'error')


def _send_member_invite(person, email):
    """Invite (or re-invite) `person` to join the active family via `email`.

    - Already-registered account → refused.
    - Existing invited (not-yet-registered) account → token refreshed and the
      link re-sent (the Resend path).
    - An existing account elsewhere with this email → added to this pod.
    - Otherwise a new invited User is created and emailed a registration link.

    Returns (message, flash_category) for the caller to flash.
    """
    email = (email or '').strip()
    if not email:
        return ('Add an email address to this person before sending an invitation.', 'error')

    # Person already linked to an account in this family.
    if person.user:
        if person.user.status == 'invited':
            # Resend: refresh the token + expiry and email a new link.
            person.user.email = email
            sent = _email_invite_link(person.user, email)
            return _invite_result_message(email, sent, resend=True)
        return (f'{person.get_display_name()} already has an account.', 'error')

    # An account with this email exists elsewhere — add them to this pod.
    existing_user = User.query.filter_by(email=email).first()
    if existing_user:
        already = UserPodMembership.query.filter_by(
            user_id=existing_user.id, family_id=current_user.active_family_id
        ).first()
        if already:
            return (f'{person.get_display_name()} is already a member of this circle.', 'error')
        db.session.add(UserPodMembership(
            user_id=existing_user.id,
            family_id=current_user.active_family_id,
            role='member',
        ))
        person.user = existing_user
        db.session.commit()
        if current_app.config.get('MAIL_ENABLED'):
            send_pod_added_email(
                existing_user,
                current_user.active_family.name,
                url_for('main.home', _external=True),
            )
        return (f'{person.get_display_name()} has been added to this circle.', 'info')

    # Brand-new invite.
    names = person.name.strip().split()
    invited_user = User(
        family_id=current_user.active_family_id,
        email=email,
        first_name=names[0],
        last_name=' '.join(names[1:]) if len(names) > 1 else '',
        password_hash='',
        status='invited',
        person_id=person.id,
    )
    db.session.add(invited_user)
    db.session.flush()
    sent = _email_invite_link(invited_user, email)
    return _invite_result_message(email, sent)


@main.route('/person/<int:person_id>/invite', methods=['POST'])
@login_required
@contributor_or_admin_required
def invite_person(person_id):
    person = db.session.get(Person, person_id)
    if not person or person.family_id != current_user.active_family_id:
        flash('Person not found.', 'error')
        return redirect(url_for('main.home'))
    if person.deathday:
        flash('Cannot invite a deceased person.', 'error')
        return redirect(url_for('main.person_detail', person_id=person_id))
    email = (person.email or request.form.get('email', '')).strip()
    if not email:
        flash('Add an email address to this person before sending an invitation.', 'error')
        return redirect(url_for('main.person_edit', person_id=person_id))
    # _send_member_invite handles fresh-invite vs resend (existing invited
    # account) and refuses only fully-registered accounts.
    msg, category = _send_member_invite(person, email)
    flash(msg, category)
    return redirect(url_for('main.person_detail', person_id=person_id))


def _person_stories(person):
    """Answered Family Stories for a person, newest first (for their profile)."""
    from ..models import StoryPrompt
    return (StoryPrompt.query
            .filter(StoryPrompt.family_id == person.family_id,
                    StoryPrompt.person_id == person.id,
                    StoryPrompt.answered_at.isnot(None))
            .order_by(StoryPrompt.answered_at.desc()).all())


def _stories_available():
    """True when the active family has Stories enabled and paid access."""
    fam = current_user.active_family
    return bool(fam and fam.enable_stories and family_has_paid_access(fam))


@main.route('/profile')
@login_required
def profile():
    person = current_user.person
    if not person:
        flash('No profile found. Please contact the admin.', 'error')
        return redirect(url_for('main.home'))
    return render_template('profile.html', person=person, relationship=None, parent_roles=PARENT_ROLES, spouse_roles=SPOUSE_ROLES,
                           person_stories=_person_stories(person), stories_available=_stories_available())

@main.route('/profile/delete-account', methods=['POST'])
@login_required
@limiter.limit('5 per hour')
def delete_account():
    from ..account import delete_user_account, LastAdminError
    if request.form.get('confirm', '').strip() != 'DELETE':
        flash('Type DELETE in the confirmation box to delete your account.', 'error')
        return redirect(url_for('tf.security'))
    user = User.query.get(current_user.id)
    try:
        result = delete_user_account(user)
    except LastAdminError:
        flash('You are the only admin. Promote another member to admin first '
              '(Members & invites → role), then delete your account.', 'error')
        return redirect(url_for('tf.security'))
    logout_user()
    session.clear()
    if result == 'purged':
        flash('Your account and circle have been permanently deleted.', 'info')
    else:
        flash('Your account has been permanently deleted.', 'info')
    return redirect(url_for('main.index'))

@main.route('/profile/edit', methods=['GET', 'POST'])
@login_required
def profile_edit():
    person = current_user.person
    if not person:
        flash('No profile found. Please contact the admin.', 'error')
        return redirect(url_for('main.home'))
    form = ProfileForm(obj=person)
    if form.validate_on_submit():
        person.nickname = form.nickname.data or None
        person.gender = form.gender.data or None
        person.birthday = form.birthday.data
        person.birthplace = format_birthplace(form.birthplace.data)
        person.maiden_name = form.maiden_name.data or None
        person.occupation = form.occupation.data or None
        person.phone = format_phone(form.phone.data)
        person.notes = form.notes.data or None
        person.email = current_user.email
        db.session.commit()
        flash('Profile updated successfully!', 'info')
        return redirect(url_for('main.profile'))
    return render_template('profile_edit.html', form=form, person=person)


@main.route('/profile/photo', methods=['POST'])
@login_required
def profile_photo():
    person = current_user.person
    if not person:
        abort(404)
    file = request.files.get('photo')
    if file and file.filename:
        if person.photo_path:
            delete_object(person.photo_path)
        key = upload_photo(file, folder='photos')
        if key:
            person.photo_path = key
            db.session.commit()
            flash('Profile photo updated.', 'info')
    return redirect(url_for('main.profile'))


@main.route('/notifications')
@login_required
def notifications():
    from ..models import Notification
    items = (
        Notification.query
        .filter_by(user_id=current_user.id)
        .order_by(Notification.created_at.desc())
        .limit(60)
        .all()
    )
    # Viewing the full list counts as reading it (the slide-over panel does
    # not — there only clicked items are marked read)
    now = datetime.utcnow()
    unread = [n for n in items if n.read_at is None]
    if unread:
        for n in unread:
            n.read_at = now
        db.session.commit()
    return render_template('notifications.html', notifications=items, just_read_ids={n.id for n in unread})


@main.route('/notifications/<int:nid>/read', methods=['POST'])
@login_required
def notification_mark_read(nid):
    from ..models import Notification
    n = db.session.get(Notification, nid)
    if n and n.user_id == current_user.id and not n.read_at:
        n.read_at = datetime.utcnow()
        db.session.commit()
    return redirect(n.url or url_for('main.notifications'))


@main.route('/notifications/read-all', methods=['POST'])
@login_required
def notifications_read_all():
    from ..models import Notification
    (
        Notification.query
        .filter_by(user_id=current_user.id, read_at=None)
        .update({'read_at': datetime.utcnow()})
    )
    db.session.commit()
    return redirect(url_for('main.notifications'))


@main.route('/profile/notifications', methods=['GET', 'POST'])
@login_required
def profile_notifications():
    all_prefs = NotificationPreference.query.filter_by(user_id=current_user.id).all()
    prefs = {(p.event_type, p.channel): p for p in all_prefs}
    if request.method == 'POST':
        for event_type, meta in NOTIFICATION_EVENTS.items():
            for channel in ('email', 'in_app'):
                if channel == 'in_app' and not meta.get('in_app'):
                    continue
                field = f'{event_type}_{channel}'
                enabled = field in request.form
                pref = prefs.get((event_type, channel))
                if pref:
                    pref.enabled = enabled
                else:
                    db.session.add(NotificationPreference(
                        user_id=current_user.id,
                        event_type=event_type,
                        channel=channel,
                        enabled=enabled,
                    ))
        db.session.commit()
        flash('Notification preferences saved.', 'info')
        return redirect(url_for('main.profile_notifications'))
    return render_template('profile_notifications.html',
                           prefs=prefs,
                           events=NOTIFICATION_EVENTS)


@main.route('/person/<int:person_id>')
@login_required
def person_detail(person_id):
    person = db.session.get(Person, person_id)
    if not person or person.family_id != current_user.active_family_id:
        flash('Person not found.', 'error')
        return redirect(url_for('main.home'))
    relationship = get_relationship(current_user.person, person) if current_user.person else None
    from ..models import PhotoTag
    tagged_photos = PhotoTag.query.filter_by(person_id=person_id)\
        .join(Photo).filter(Photo.family_id == current_user.active_family_id)\
        .order_by(Photo.created_at.desc()).limit(6).all()
    tagged_photos = [t.photo for t in tagged_photos]
    return render_template('profile.html', person=person, relationship=relationship,
                           parent_roles=PARENT_ROLES, spouse_roles=SPOUSE_ROLES,
                           tagged_photos=tagged_photos,
                           person_stories=_person_stories(person), stories_available=_stories_available())

@main.route('/person/<int:person_id>/edit', methods=['GET', 'POST'])
@login_required
def person_edit(person_id):
    person = db.session.get(Person, person_id)
    if not person or person.family_id != current_user.active_family_id:
        flash('Person not found.', 'error')
        return redirect(url_for('main.home'))
    can_edit = current_user.active_is_admin or (person.user and person.user == current_user)
    if not can_edit:
        flash('You do not have permission to edit this profile.', 'error')
        return redirect(url_for('main.person_detail', person_id=person_id))
    lgbtq = current_user.active_family.has_lgbtq_options
    form = EditPersonForm(obj=person)
    form.gender.choices = GENDER_CHOICES_EXPANDED if lgbtq else GENDER_CHOICES_DEFAULT
    if form.validate_on_submit():
        person.name = form.name.data.strip()
        person.nickname = form.nickname.data or None
        person.gender = form.gender.data or None
        person.pronouns = form.pronouns.data or None
        person.birthday = form.birthday.data
        person.birthplace = format_birthplace(form.birthplace.data)
        person.maiden_name = form.maiden_name.data or None
        person.occupation = form.occupation.data or None
        person.phone = format_phone(form.phone.data)
        person.address = form.address.data or None
        person.deathday = form.deathday.data
        person.deathplace = form.deathplace.data or None
        person.notes = form.notes.data or None
        # Email is editable while there's no registered account — i.e. no user, or
        # one that's still only invited (so an admin can fix a wrong invite address).
        if not person.user or person.user.status == 'invited':
            new_email = (form.email.data or '').strip() or None
            person.email = new_email
            if person.user and new_email:
                # Keep the pending account's email in sync (collision-safe).
                clash = User.query.filter(User.email == new_email, User.id != person.user.id).first()
                if clash:
                    flash('Another account already uses that email.', 'error')
                    return redirect(url_for('main.person_edit', person_id=person_id))
                person.user.email = new_email
        # Handle photo upload/removal
        if form.remove_photo.data and person.photo_path:
            delete_object(person.photo_path)
            person.photo_path = None
            person.photo_position = '50% 30%'
        elif form.photo.data:
            delete_object(person.photo_path)
            key = upload_photo(form.photo.data, folder='photos')
            if key:
                person.photo_path = key
        db.session.commit()
        flash('Profile updated.', 'info')
        return redirect(url_for('main.person_detail', person_id=person_id))
    return render_template('person_edit.html', form=form, person=person, lgbtq=lgbtq)

@main.route('/search')
@login_required
def search():
    q = request.args.get('q', '').strip()
    results = {'people': [], 'events': [], 'photos': [], 'announcements': [], 'documents': []}
    total = 0
    if q:
        fid = current_user.active_family_id
        like = f'%{q}%'
        results['people'] = Person.query.filter(
            Person.family_id == fid,
            db.or_(Person.name.ilike(like), Person.nickname.ilike(like)),
        ).order_by(Person.name).limit(10).all()

        results['events'] = Event.query.filter(
            Event.family_id == fid,
            db.or_(Event.name.ilike(like), Event.description.ilike(like)),
        ).order_by(Event.start_date.desc().nullslast()).limit(10).all()

        results['announcements'] = Announcement.query.filter(
            Announcement.family_id == fid,
            db.or_(Announcement.title.ilike(like), Announcement.body.ilike(like)),
        ).order_by(Announcement.created_at.desc()).limit(10).all()

        # Photos matched by caption, plus albums matched by name — surfaced as
        # albums so a click lands on the album the photo lives in.
        album_hits = Album.query.filter(
            Album.family_id == fid, Album.name.ilike(like),
        ).limit(10).all()
        caption_photos = Photo.query.filter(
            Photo.family_id == fid, Photo.caption.ilike(like),
        ).order_by(Photo.created_at.desc()).limit(10).all()
        seen_albums = {a.id: a for a in album_hits}
        for p in caption_photos:
            if p.album_id not in seen_albums and p.album:
                seen_albums[p.album_id] = p.album
        results['photos'] = list(seen_albums.values())

        results['documents'] = Document.query.filter(
            Document.family_id == fid,
            db.or_(Document.title.ilike(like), Document.original_filename.ilike(like)),
        ).order_by(Document.uploaded_at.desc()).limit(10).all()

        total = sum(len(v) for v in results.values())
    return render_template('search.html', q=q, results=results, total=total)

@main.route('/person/<int:person_id>/link-spouse', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_link_spouse(person_id):
    person = db.session.get(Person, person_id)
    if not person or person.family_id != current_user.active_family_id:
        flash('Person not found.', 'error')
        return redirect(url_for('main.home'))
    if person.get_active_spouse():
        flash(f'{person.get_display_name()} already has an active spouse.', 'error')
        return redirect(url_for('main.person_detail', person_id=person_id))
    eligible = [
        p for p in Person.query.filter_by(family_id=current_user.active_family_id).order_by(Person.name).all()
        if p.id != person.id and not p.get_active_spouse()
    ]
    form = SpouseForm()
    form.spouse_id.choices = [(0, '-- Select --')] + [(p.id, p.get_display_name()) for p in eligible]
    if form.validate_on_submit():
        spouse_person = db.session.get(Person, form.spouse_id.data)
        if not spouse_person or spouse_person.family_id != current_user.active_family_id:
            flash('Person not found.', 'error')
            return redirect(url_for('main.admin_link_spouse', person_id=person_id))
        rel = SpouseRelationship(
            person1_id=person.id,
            person2_id=spouse_person.id,
            marriage_date=form.marriage_date.data,
            confirmed=True,  # admin links are pre-confirmed
        )
        db.session.add(rel)
        db.session.commit()
        flash(f'{person.get_display_name()} and {spouse_person.get_display_name()} linked as spouses.', 'info')
        return redirect(url_for('main.person_detail', person_id=person_id))
    return render_template('admin_link_spouse.html', form=form, subject=person)

@main.route('/spouse/add', methods=['GET', 'POST'])
@login_required
def spouse_add():
    person = current_user.person
    if not person:
        flash('No profile found.', 'error')
        return redirect(url_for('main.home'))
    active_spouse = person.get_active_spouse()
    if active_spouse:
        flash('You already have an active spouse. Please end that relationship first.', 'error')
        return redirect(url_for('main.profile'))
    eligible = [
        p for p in Person.query.filter_by(family_id=current_user.active_family_id).order_by(Person.name).all()
        if p.id != person.id and not p.get_active_spouse()
    ]
    form = SpouseForm()
    form.spouse_id.choices = [(0, '-- Select --')] + [(p.id, p.get_display_name()) for p in eligible]
    invite_form = SpouseInviteForm()
    if form.submit.data and form.validate_on_submit():
        spouse_person = db.session.get(Person, form.spouse_id.data)
        if not spouse_person or spouse_person.family_id != current_user.active_family_id:
            flash('Person not found.', 'error')
            return redirect(url_for('main.spouse_add'))
        token = secrets.token_urlsafe(32)
        rel = SpouseRelationship(
            person1_id=person.id,
            person2_id=spouse_person.id,
            marriage_date=form.marriage_date.data,
            confirmed=False,
            confirmation_token=token,
            confirmation_token_expiry=datetime.utcnow() + timedelta(days=7)
        )
        db.session.add(rel)
        db.session.commit()
        if spouse_person.user:
            if current_app.config.get('MAIL_ENABLED'):
                send_spouse_confirmation_email(
                    person,
                    spouse_person.user,
                    url_for('main.spouse_confirm', token=token, _external=True),
                    url_for('main.spouse_decline', token=token, _external=True)
                )
            flash(f'Spouse request sent to {spouse_person.get_display_name()}.', 'info')
        else:
            flash(f'Spouse request created. {spouse_person.get_display_name()} will need to confirm when they register.', 'info')
        return redirect(url_for('main.profile'))
    return render_template('spouse_add.html', form=form, invite_form=invite_form)

@main.route('/spouse/invite', methods=['POST'])
@login_required
def spouse_invite():
    person = current_user.person
    if not person:
        flash('No profile found.', 'error')
        return redirect(url_for('main.home'))
    invite_form = SpouseInviteForm()
    if invite_form.validate_on_submit():
        existing = User.query.filter_by(email=invite_form.email.data).first()
        if existing:
            flash('An account with that email already exists.', 'error')
            return redirect(url_for('main.spouse_add'))
        full_name = f"{invite_form.first_name.data} {invite_form.last_name.data}"
        spouse_person = Person.query.filter_by(name=full_name, family_id=current_user.active_family_id).first()
        if not spouse_person and not family_has_paid_access(current_user.active_family):
            person_count = Person.query.filter_by(family_id=current_user.active_family_id).count()
            if person_count >= FREE_MEMBER_LIMIT:
                flash(f'Free plan is limited to {FREE_MEMBER_LIMIT} family members. '
                      'Upgrade to add unlimited members.', 'warning')
                return redirect(url_for('billing.billing_page'))
        if not spouse_person:
            spouse_person = Person(
                name=full_name,
                email=invite_form.email.data,
                family_id=current_user.active_family_id,
            )
            db.session.add(spouse_person)
            db.session.flush()
        invitation_token = secrets.token_urlsafe(32)
        rel = SpouseRelationship(
            person1_id=person.id,
            person2_id=spouse_person.id,
            marriage_date=invite_form.marriage_date.data,
            confirmed=True,
        )
        db.session.add(rel)
        invited_user = User(
            first_name=invite_form.first_name.data,
            last_name=invite_form.last_name.data,
            email=invite_form.email.data,
            password_hash='invited',
            email_verified=False,
            status='invited',
            invitation_token=_hash_token(invitation_token),
            invitation_token_expiry=datetime.utcnow() + timedelta(days=INVITE_VALID_DAYS),
            invited_by_id=current_user.id,
            family_id=current_user.active_family_id,
            person_id=spouse_person.id,
        )
        db.session.add(invited_user)
        db.session.commit()
        sent = False
        if current_app.config.get('MAIL_ENABLED'):
            sent = bool(send_spouse_invitation_email(
                person,
                invite_form.email.data,
                url_for('main.register_invited', token=invitation_token, _external=True)
            ))
        if sent:
            flash(f'Invitation sent to {full_name} at {invite_form.email.data}.', 'info')
        else:
            flash(f"{full_name} was added, but the invitation email to "
                  f"{invite_form.email.data} couldn't be sent. Check mail settings.", 'error')
        return redirect(url_for('main.profile'))
    form = SpouseForm()
    eligible = [
        p for p in Person.query.filter_by(family_id=current_user.active_family_id).order_by(Person.name).all()
        if p.id != person.id and not p.get_active_spouse()
    ]
    form.spouse_id.choices = [(0, '-- Select --')] + [(p.id, p.get_display_name()) for p in eligible]
    return render_template('spouse_add.html', form=form, invite_form=invite_form)

@main.route('/spouse/confirm/<token>', methods=['GET', 'POST'])
@login_required
def spouse_confirm(token):
    rel = SpouseRelationship.query.filter_by(confirmation_token=token).first()
    if not rel:
        flash('Invalid or expired confirmation link.', 'error')
        return redirect(url_for('main.profile'))
    if rel.confirmed:
        flash('This relationship has already been confirmed.', 'info')
        return redirect(url_for('main.profile'))
    if rel.confirmation_token_expiry and rel.confirmation_token_expiry < datetime.utcnow():
        flash('This confirmation link has expired.', 'error')
        return redirect(url_for('main.profile'))
    # Ensure only the intended recipient can confirm
    if rel.person2.user and rel.person2.user != current_user:
        flash('You are not authorized to confirm this relationship.', 'error')
        return redirect(url_for('main.profile'))
    if request.method == 'POST':
        rel.confirmed = True
        rel.confirmation_token = None
        rel.confirmation_token_expiry = None
        db.session.commit()
        flash('Spouse relationship confirmed!', 'info')
        return redirect(url_for('main.profile'))
    return render_template('spouse_confirm.html', rel=rel, token=token)

@main.route('/spouse/decline/<token>', methods=['GET', 'POST'])
@login_required
def spouse_decline(token):
    rel = SpouseRelationship.query.filter_by(confirmation_token=token).first()
    if not rel:
        flash('Invalid or expired confirmation link.', 'error')
        return redirect(url_for('main.profile'))
    if rel.confirmation_token_expiry and rel.confirmation_token_expiry < datetime.utcnow():
        flash('This confirmation link has expired.', 'error')
        return redirect(url_for('main.profile'))
    if rel.person2.user and rel.person2.user != current_user:
        flash('You are not authorized to decline this relationship.', 'error')
        return redirect(url_for('main.profile'))
    if request.method == 'GET':
        # Email decline links land here; show the confirmation page first
        return redirect(url_for('main.spouse_confirm', token=token))
    db.session.delete(rel)
    db.session.commit()
    flash('Spouse request declined.', 'info')
    return redirect(url_for('main.profile'))

@main.route('/spouse/end', methods=['GET', 'POST'])
@login_required
def spouse_end():
    person = current_user.person
    if not person:
        flash('No profile found.', 'error')
        return redirect(url_for('main.home'))
    rel = None
    for r in person.spouse_relationships_as_p1 + person.spouse_relationships_as_p2:
        if r.status == 'active' and r.confirmed:
            rel = r
            break
    if not rel:
        flash('No active spouse relationship found.', 'error')
        return redirect(url_for('main.profile'))
    form = EndSpouseForm()
    if form.validate_on_submit():
        rel.status = form.status.data
        rel.end_date = form.end_date.data
        db.session.commit()
        flash('Spouse relationship updated.', 'info')
        return redirect(url_for('main.profile'))
    return render_template('spouse_end.html', form=form, spouse=rel.get_spouse_of(person))


# ── Timeline ──────────────────────────────────────────────────────────────────

@main.route('/timeline')
@login_required
def timeline():
    fid = current_user.active_family_id
    people = Person.query.filter_by(family_id=fid).order_by(Person.name).all()
    events = Event.query.filter_by(family_id=fid).order_by(Event.start_date).all()

    milestones = []
    for p in people:
        if p.birthday:
            milestones.append({
                'date': p.birthday,
                'type': 'birth',
                'title': f'{p.get_display_name()} born',
                'subtitle': p.birthplace or None,
                'person_id': p.id,
            })
        if p.deathday:
            milestones.append({
                'date': p.deathday,
                'type': 'death',
                'title': f'{p.get_display_name()} passed away',
                'subtitle': p.deathplace or None,
                'person_id': p.id,
            })

    for rel in SpouseRelationship.query.filter(
        (SpouseRelationship.person1_id.in_([p.id for p in people])) |
        (SpouseRelationship.person2_id.in_([p.id for p in people]))
    ).all():
        if rel.marriage_date:
            p1 = db.session.get(Person, rel.person1_id)
            p2 = db.session.get(Person, rel.person2_id)
            if p1 and p2:
                milestones.append({
                    'date': rel.marriage_date,
                    'type': 'marriage',
                    'title': f'{p1.get_display_name()} & {p2.get_display_name()} married',
                    'subtitle': None,
                    'person_id': None,
                })

    for e in events:
        if not e.start_date:
            continue
        milestones.append({
            'date': e.start_date,
            'type': 'event',
            'title': e.name,
            'subtitle': e.location or None,
            'event_id': e.id,
        })

    milestones.sort(key=lambda m: m['date'])

    # Group by year
    from itertools import groupby
    grouped = []
    for year, items in groupby(milestones, key=lambda m: m['date'].year):
        grouped.append({'year': year, 'items': list(items)})

    today = date.today()
    return render_template('timeline.html', grouped=grouped, today=today)


# ── Public pages ───────────────────────────────────────────────────────────────

@main.route('/privacy')
def privacy():
    return render_template('privacy.html')

@main.route('/terms')
def terms():
    return render_template('terms.html')


# ── Support ────────────────────────────────────────────────────────────────────

@main.route('/support', methods=['GET', 'POST'])
@login_required
@limiter.limit('3 per hour')
def support():
    form = SupportForm()
    if form.validate_on_submit():
        support_email = current_app.config.get('SUPPORT_EMAIL', 'jeremypease@me.com')
        if current_app.config.get('RESEND_API_KEY'):
            send_support_email(
                user=current_user,
                family=current_user.active_family,
                category=form.category.data,
                message=form.message.data,
                support_email=support_email
            )
        else:
            current_app.logger.warning(
                'Support form submitted but RESEND_API_KEY is not set — email not sent. '
                f'User: {current_user.email}, family: {current_user.active_family_id}'
            )
        from ..models import Notification
        db.session.add(Notification(
            user_id=current_user.id,
            event_type='support_sent',
            title='Support message received',
            body="We'll get back to you within one business day.",
            url=url_for('main.support'),
        ))
        db.session.commit()
        return redirect(url_for('main.support', sent=1))
    sent = request.args.get('sent', False)
    return render_template('support.html', form=form, sent=sent)

# ── Calendar feed ────────────────────────────────────────────────────────────────

def _ical_escape(text):
    """Escape special characters per RFC 5545."""
    return str(text).replace('\\', '\\\\').replace(';', '\\;').replace(',', '\\,').replace('\n', '\\n')


def _ical_fold(line):
    """Fold long iCal lines at 75 octets per RFC 5545."""
    encoded = line.encode('utf-8')
    if len(encoded) <= 75:
        return line
    chunks = []
    while len(encoded) > 75:
        chunk = encoded[:75]
        # Don't split a multi-byte character
        while len(chunk) > 0 and (chunk[-1] & 0xC0) == 0x80:
            chunk = chunk[:-1]
        chunks.append(chunk.decode('utf-8'))
        encoded = encoded[len(chunk):]
        if encoded:
            encoded = b' ' + encoded
    chunks.append(encoded.decode('utf-8'))
    return '\r\n'.join(chunks)


def _build_ical(family, events):
    lines = [
        'BEGIN:VCALENDAR',
        'VERSION:2.0',
        'PRODID:-//Swugl//Family Calendar//EN',
        'CALSCALE:GREGORIAN',
        'METHOD:PUBLISH',
        f'X-WR-CALNAME:{_ical_escape(family.name)} Events',
        'X-WR-CALDESC:Family events from Swugl',
    ]
    now_stamp = datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')
    for ev in events:
        end_date = (ev.end_date + timedelta(days=1)) if ev.end_date else (ev.start_date + timedelta(days=1))
        vevent = [
            'BEGIN:VEVENT',
            f'UID:event-{ev.id}@swugl.com',
            f'DTSTAMP:{now_stamp}',
            f'DTSTART;VALUE=DATE:{ev.start_date.strftime("%Y%m%d")}',
            f'DTEND;VALUE=DATE:{end_date.strftime("%Y%m%d")}',
            f'SUMMARY:{_ical_escape(ev.name)}',
        ]
        if ev.description:
            vevent.append(f'DESCRIPTION:{_ical_escape(ev.description)}')
        if ev.location:
            vevent.append(f'LOCATION:{_ical_escape(ev.location)}')
        vevent.append(f'URL:{_ical_escape(f"/events/{ev.id}")}')
        vevent.append('END:VEVENT')
        lines.extend(vevent)
    lines.append('END:VCALENDAR')
    return '\r\n'.join(_ical_fold(l) for l in lines) + '\r\n'


@main.route('/family/calendar/<token>.ics')
def calendar_feed(token):
    from flask import Response as Resp
    ct = CalendarToken.query.filter_by(token=token).first_or_404()
    family = ct.user.family
    events = Event.query.filter_by(family_id=family.id).order_by(Event.start_date).all()
    ical = _build_ical(family, events)
    return Resp(ical, content_type='text/calendar; charset=utf-8',
                headers={'Content-Disposition': f'attachment; filename="family.ics"'})


@main.route('/profile/calendar')
@login_required
def profile_calendar():
    ct = CalendarToken.query.filter_by(user_id=current_user.id).first()
    return render_template('profile_calendar.html', cal_token=ct)


@main.route('/profile/calendar/regenerate', methods=['POST'])
@login_required
def regenerate_calendar_token():
    ct = CalendarToken.query.filter_by(user_id=current_user.id).first()
    new_token = uuid.uuid4().hex + uuid.uuid4().hex[:16]
    if ct:
        ct.token = new_token
        ct.created_at = datetime.utcnow()
    else:
        ct = CalendarToken(user_id=current_user.id, token=new_token)
        db.session.add(ct)
    db.session.commit()
    flash('Calendar link regenerated. Update the subscription in your calendar app.', 'info')
    return redirect(url_for('main.profile_calendar'))


# ── PWA ─────────────────────────────────────────────────────────────────────────

@main.route('/manifest.json')
def pwa_manifest():
    from flask import send_from_directory, Response
    import json, os
    path = os.path.join(current_app.root_path, 'static', 'manifest.json')
    with open(path) as f:
        data = f.read()
    return Response(data, content_type='application/manifest+json')


@main.route('/sw.js')
def pwa_sw():
    from flask import send_from_directory, Response, make_response
    import os
    path = os.path.join(current_app.root_path, 'static', 'js', 'sw.js')
    with open(path) as f:
        data = f.read()
    resp = Response(data, content_type='application/javascript')
    resp.headers['Service-Worker-Allowed'] = '/'
    resp.headers['Cache-Control'] = 'no-cache'
    return resp


@main.route('/offline')
def pwa_offline():
    return render_template('offline.html')


@main.route('/photos/<path:key>')
@login_required
def serve_photo(key):
    """Proxy route: serves R2 photos through Flask when no R2_PUBLIC_URL is set."""
    from flask import Response, abort
    photo = Photo.query.filter(
        Photo.family_id == current_user.active_family_id,
        db.or_(Photo.path == key, Photo.thumb_path == key),
    ).first()
    person = None if photo else Person.query.filter_by(family_id=current_user.active_family_id, photo_path=key).first()
    if not photo and not person:
        abort(403)
    data, content_type = get_object_bytes(key)
    return Response(data, content_type=content_type)


# ── Feature route modules ──────────────────────────────────────────────────────
# Imported last so the `main` blueprint and shared helpers above already exist.
# Each module attaches its routes to `main` via @main.route.
from . import chat, checklists, documents, stories, polls, cards, events  # noqa: E402,F401
# _geocode_location lives in events.py but is shared by the location-admin routes above.
from .events import _geocode_location  # noqa: E402,F401
