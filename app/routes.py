from flask import Blueprint, render_template, redirect, url_for, flash, request, current_app, send_file, session, abort, jsonify
from flask_login import login_user, logout_user, login_required, current_user
from .models import Family, User, Person, ParentRelationship, PARENT_ROLES, SpouseRelationship, Event, EventMeal, EventMealItem, EventAssignment, AssignmentTask, ASSIGNMENT_CATEGORIES, EventRSVP, EventSleepingSpot, SPOT_TYPES, EventComment, Announcement, Album, Photo, Poll, NotificationPreference, NOTIFICATION_EVENTS, UserPodMembership, CalendarToken, EventPaymentConfig, EventPaymentRecord, FamilyPayoutAccount, Location, Document, DOCUMENT_CATEGORIES, ChatMessage
from .forms import LoginForm, RegistrationForm, ProfileForm, SpouseForm, EndSpouseForm, SpouseInviteForm, ForgotPasswordForm, ResetPasswordForm, AddPersonForm, RelativeForm, AddParentForm, FamilySettingsForm, EditPersonForm, EventForm, EventCommentForm, EventMealForm, EventMealFamilyAssignForm, EventMealItemForm, EventMealSelfSignupForm, EventMealAssignForm, EventAssignmentForm, EventAssignmentAdminAssignForm, EventSleepingSpotForm, EventSleepingAssignForm, GENDER_CHOICES_DEFAULT, GENDER_CHOICES_EXPANDED, PRONOUN_CHOICES, AnnouncementForm, AlbumForm, PhotoUploadForm, SupportForm, ChatMessageForm
from .email import send_verification_email, send_pending_notification, send_approval_notification, send_spouse_confirmation_email, send_spouse_invitation_email, send_password_reset_email, send_member_invitation_email, send_welcome_email, send_support_email, send_pod_added_email
from datetime import date, datetime, timedelta
from functools import wraps
from urllib.parse import urlparse
from . import db, limiter
from .billing import requires_plan, family_has_paid_access, FREE_MEMBER_LIMIT, FREE_EVENT_LIMIT
from .storage import upload_photo, delete_object, get_object_bytes
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
        has_members = member_count > 1
        has_photo = len(recent_photos) > 0
        steps = [
            ('members', 'Add your first family member', url_for('main.members'), has_members),
            ('photo',   'Upload a photo', url_for('main.albums'), has_photo),
        ]
        incomplete = [s for s in steps if not s[3]]
        if incomplete:
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
                           now=datetime.now())

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
    if form.validate_on_submit():
        family.name = form.family_name.data
        family.require_member_approval = form.require_member_approval.data
        family.has_lgbtq_options = form.has_lgbtq_options.data
        family.enable_polls = form.enable_polls.data
        family.enable_greeting_cards = form.enable_greeting_cards.data
        family.enable_chat = form.enable_chat.data
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
        from .notifications import notify_family
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
    from .models import PhotoTag
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
    from .models import PhotoTag
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
    from .models import PhotoTag
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

# ── Checklists ───────────────────────────────────────────────────────────────

@main.route('/checklists')
@login_required
def checklists():
    from .models import Checklist
    all_lists = Checklist.query.filter_by(family_id=current_user.active_family_id)\
        .order_by(Checklist.created_at.desc()).all()
    upcoming_events = Event.query.filter_by(family_id=current_user.active_family_id)\
        .filter(Event.start_date >= date.today()).order_by(Event.start_date).limit(10).all()
    active_lists = [cl for cl in all_lists if not cl.items or cl.done_count < len(cl.items)]
    completed_lists = [cl for cl in all_lists if cl.items and cl.done_count == len(cl.items)]
    return render_template('checklists.html', active_lists=active_lists,
                           completed_lists=completed_lists, events=upcoming_events)


@main.route('/checklists/new', methods=['POST'])
@login_required
def create_checklist():
    from .models import Checklist
    title = request.form.get('title', '').strip()
    list_type = request.form.get('list_type', 'general')
    event_id = request.form.get('event_id', type=int)
    if not title:
        flash('Please enter a title.', 'error')
        return redirect(url_for('main.checklists'))
    cl = Checklist(
        family_id=current_user.active_family_id,
        created_by_id=current_user.person.id if current_user.person else None,
        title=title,
        list_type=list_type,
        event_id=event_id or None,
    )
    db.session.add(cl)
    db.session.commit()
    return redirect(url_for('main.checklist_detail', checklist_id=cl.id))


@main.route('/checklists/<int:checklist_id>')
@login_required
def checklist_detail(checklist_id):
    from .models import Checklist
    cl = db.session.get(Checklist, checklist_id)
    if not cl or cl.family_id != current_user.active_family_id:
        abort(404)
    return render_template('checklist_detail.html', checklist=cl)


@main.route('/checklists/<int:checklist_id>/items/add', methods=['POST'])
@login_required
def checklist_add_item(checklist_id):
    from .models import Checklist, ChecklistItem
    cl = db.session.get(Checklist, checklist_id)
    if not cl or cl.family_id != current_user.active_family_id:
        abort(404)
    label = request.form.get('label', '').strip()
    if label:
        db.session.add(ChecklistItem(checklist_id=checklist_id, label=label))
        db.session.commit()
    return redirect(url_for('main.checklist_detail', checklist_id=checklist_id))


@main.route('/checklists/<int:checklist_id>/items/<int:item_id>/toggle', methods=['POST'])
@login_required
def checklist_toggle_item(checklist_id, item_id):
    from .models import ChecklistItem
    wants_json = 'application/json' in request.headers.get('Accept', '')
    item = db.session.get(ChecklistItem, item_id)
    if not item or item.checklist.family_id != current_user.active_family_id:
        abort(404)
    item.is_done = not item.is_done
    item.claimed_by_id = current_user.person.id if (item.is_done and current_user.person) else None
    db.session.commit()
    if wants_json:
        return jsonify({
            'is_done': item.is_done,
            'claimed_by': item.claimed_by.get_display_name() if item.claimed_by else None,
        })
    return redirect(url_for('main.checklist_detail', checklist_id=checklist_id))


@main.route('/checklists/<int:checklist_id>/items/<int:item_id>/delete', methods=['POST'])
@login_required
def checklist_delete_item(checklist_id, item_id):
    from .models import ChecklistItem
    item = db.session.get(ChecklistItem, item_id)
    if not item or item.checklist.family_id != current_user.active_family_id:
        abort(404)
    if not current_user.active_is_admin and not (current_user.person and item.checklist.created_by_id == current_user.person.id):
        abort(403)
    db.session.delete(item)
    db.session.commit()
    return redirect(url_for('main.checklist_detail', checklist_id=checklist_id))


@main.route('/checklists/<int:checklist_id>/delete', methods=['POST'])
@login_required
def checklist_delete(checklist_id):
    from .models import Checklist
    cl = db.session.get(Checklist, checklist_id)
    if not cl or cl.family_id != current_user.active_family_id:
        abort(404)
    is_creator = current_user.person and cl.created_by_id == current_user.person.id
    if not current_user.active_is_admin and not is_creator:
        abort(403)
    db.session.delete(cl)
    db.session.commit()
    flash('Checklist deleted.', 'info')
    return redirect(url_for('main.checklists'))


@main.route('/checklists/<int:checklist_id>/rename', methods=['POST'])
@login_required
def checklist_rename(checklist_id):
    from .models import Checklist
    cl = db.session.get(Checklist, checklist_id)
    if not cl or cl.family_id != current_user.active_family_id:
        abort(404)
    is_creator = current_user.person and cl.created_by_id == current_user.person.id
    if not current_user.active_is_admin and not is_creator:
        abort(403)
    title = request.form.get('title', '').strip()
    if title:
        cl.title = title
        db.session.commit()
    return redirect(url_for('main.checklist_detail', checklist_id=checklist_id))


@main.route('/checklists/<int:checklist_id>/clear-done', methods=['POST'])
@login_required
def checklist_clear_done(checklist_id):
    from .models import Checklist, ChecklistItem
    cl = db.session.get(Checklist, checklist_id)
    if not cl or cl.family_id != current_user.active_family_id:
        abort(404)
    is_creator = current_user.person and cl.created_by_id == current_user.person.id
    if not current_user.active_is_admin and not is_creator:
        abort(403)
    ChecklistItem.query.filter_by(checklist_id=checklist_id, is_done=True).delete()
    db.session.commit()
    return redirect(url_for('main.checklist_detail', checklist_id=checklist_id))


# ── Polls ─────────────────────────────────────────────────────────────────────

@main.route('/polls')
@login_required
def polls():
    if not current_user.active_family.enable_polls:
        flash('Polls are disabled for this family.', 'error')
        return redirect(url_for('main.home'))
    from .models import Poll, PollVote
    from sqlalchemy import func as _func
    all_polls = Poll.query.filter_by(family_id=current_user.active_family_id)\
        .order_by(Poll.created_at.desc()).all()
    poll_ids = [p.id for p in all_polls]
    voter_counts = {}
    if poll_ids:
        rows = db.session.query(
            PollVote.poll_id, _func.count(_func.distinct(PollVote.person_id))
        ).filter(PollVote.poll_id.in_(poll_ids)).group_by(PollVote.poll_id).all()
        voter_counts = {pid: cnt for pid, cnt in rows}
    my_voted_ids = set()
    if current_user.person and poll_ids:
        voted = db.session.query(PollVote.poll_id).filter(
            PollVote.poll_id.in_(poll_ids),
            PollVote.person_id == current_user.person.id
        ).distinct().all()
        my_voted_ids = {r.poll_id for r in voted}
    active_polls = [p for p in all_polls if not p.is_closed]
    closed_polls = [p for p in all_polls if p.is_closed]
    return render_template('polls.html', active_polls=active_polls, closed_polls=closed_polls,
                           voter_counts=voter_counts, my_voted_ids=my_voted_ids,
                           today=date.today())


@main.route('/polls/new', methods=['POST'])
@login_required
def create_poll():
    from .models import Poll, PollOption
    question = request.form.get('question', '').strip()
    closes_at_str = request.form.get('closes_at', '').strip()
    options = [o.strip() for o in request.form.getlist('options') if o.strip()]
    if not question or len(options) < 2:
        flash('A poll needs a question and at least 2 options.', 'error')
        return redirect(url_for('main.polls'))
    closes_at = None
    if closes_at_str:
        try:
            from datetime import date as dt_date
            closes_at = dt_date.fromisoformat(closes_at_str)
        except ValueError:
            pass
    poll = Poll(
        family_id=current_user.active_family_id,
        created_by_id=current_user.person.id if current_user.person else None,
        question=question,
        closes_at=closes_at,
    )
    db.session.add(poll)
    db.session.flush()
    for label in options:
        db.session.add(PollOption(poll_id=poll.id, label=label))
    db.session.commit()
    from .notifications import notify_family
    actor = current_user.person.get_display_name() if current_user.person else 'Someone'
    notify_family(
        current_user.active_family_id, 'new_poll',
        title=f'{actor} added a poll',
        body=question,
        url=url_for('main.polls'),
        exclude_user_id=current_user.id,
    )
    return redirect(url_for('main.poll_detail', poll_id=poll.id))


@main.route('/polls/<int:poll_id>')
@login_required
def poll_detail(poll_id):
    from .models import Poll, PollVote
    poll = db.session.get(Poll, poll_id)
    if not poll or poll.family_id != current_user.active_family_id:
        abort(404)
    my_votes = set()
    if current_user.person:
        my_votes = {v.option_id for v in PollVote.query.filter_by(
            poll_id=poll_id, person_id=current_user.person.id
        ).all()}
    return render_template('poll_detail.html', poll=poll, my_votes=my_votes)


@main.route('/polls/<int:poll_id>/vote', methods=['POST'])
@login_required
def vote_poll(poll_id):
    from .models import Poll, PollVote
    poll = db.session.get(Poll, poll_id)
    if not poll or poll.family_id != current_user.active_family_id or poll.is_closed:
        abort(404)
    if not current_user.person:
        flash('Link your family profile to vote.', 'error')
        return redirect(url_for('main.poll_detail', poll_id=poll_id))
    option_ids = [int(x) for x in request.form.getlist('options') if x.isdigit()]
    valid_ids = {o.id for o in poll.options}
    # Remove all existing votes then re-add selected
    PollVote.query.filter_by(poll_id=poll_id, person_id=current_user.person.id).delete()
    for oid in option_ids:
        if oid in valid_ids:
            db.session.add(PollVote(poll_id=poll_id, option_id=oid,
                                    person_id=current_user.person.id))
    db.session.commit()
    return redirect(url_for('main.poll_detail', poll_id=poll_id))


@main.route('/polls/<int:poll_id>/delete', methods=['POST'])
@login_required
def delete_poll(poll_id):
    from .models import Poll
    poll = db.session.get(Poll, poll_id)
    if not poll or poll.family_id != current_user.active_family_id:
        abort(404)
    is_creator = current_user.person and poll.created_by_id == current_user.person.id
    if not current_user.active_is_admin and not is_creator:
        abort(403)
    db.session.delete(poll)
    db.session.commit()
    flash('Poll deleted.', 'info')
    return redirect(url_for('main.polls'))


# ── Greeting Cards ────────────────────────────────────────────────────────────

CARD_OCCASIONS = [
    ('birthday', 'Birthday'),
    ('anniversary', 'Anniversary'),
    ('milestone', 'Milestone'),
    ('custom', 'Other'),
]

@main.route('/cards')
@login_required
def cards():
    if not current_user.active_family.enable_greeting_cards:
        flash('Greeting cards are disabled for this family.', 'error')
        return redirect(url_for('main.home'))
    from .models import GreetingCard, CardSignature
    all_cards = GreetingCard.query.filter_by(family_id=current_user.active_family_id)\
        .order_by(GreetingCard.created_at.desc()).all()
    people = Person.query.filter_by(
        family_id=current_user.active_family_id, in_directory=True
    ).order_by(Person.name).all()
    my_person_id = current_user.person.id if current_user.person else None
    visible = [c for c in all_cards if c.recipient_id != my_person_id]
    my_signed_ids = set()
    if my_person_id:
        sigs = CardSignature.query.filter(
            CardSignature.card_id.in_([c.id for c in visible]),
            CardSignature.person_id == my_person_id
        ).all()
        my_signed_ids = {s.card_id for s in sigs}
    active_cards = [c for c in visible if not c.sent_at]
    sent_cards = [c for c in visible if c.sent_at]
    return render_template('cards.html', active_cards=active_cards, sent_cards=sent_cards,
                           people=people, occasions=CARD_OCCASIONS,
                           my_signed_ids=my_signed_ids, today=date.today())


@main.route('/cards/new', methods=['POST'])
@login_required
def create_card():
    from .models import GreetingCard
    recipient_id = request.form.get('recipient_id', type=int)
    occasion = request.form.get('occasion', 'custom')
    title = request.form.get('title', '').strip()
    send_date_str = request.form.get('send_date', '').strip()
    if not recipient_id or not title:
        flash('Please fill in all required fields.', 'error')
        return redirect(url_for('main.cards'))
    recipient = db.session.get(Person, recipient_id)
    if not recipient or recipient.family_id != current_user.active_family_id:
        abort(404)
    send_date = None
    if send_date_str:
        try:
            from datetime import date as dt_date
            send_date = dt_date.fromisoformat(send_date_str)
        except ValueError:
            pass
    card = GreetingCard(
        family_id=current_user.active_family_id,
        recipient_id=recipient_id,
        created_by_id=current_user.person.id if current_user.person else None,
        occasion=occasion,
        title=title,
        send_date=send_date,
    )
    db.session.add(card)
    db.session.commit()
    from .notifications import notify_family
    actor = current_user.person.get_display_name() if current_user.person else 'Someone'
    # Exclude the recipient's own account — it's a surprise card.
    recipient_user_id = recipient.user.id if recipient.user else None
    notify_family(
        current_user.active_family_id, 'new_card',
        title=f'{actor} started a card for {recipient.get_display_name()}',
        body='Add your signature before it’s sent.',
        url=url_for('main.card_detail', card_id=card.id),
        exclude_user_id=current_user.id,
        exclude_user_ids=[recipient_user_id] if recipient_user_id else None,
    )
    flash(f'Card created! Invite the family to sign it.', 'success')
    return redirect(url_for('main.card_detail', card_id=card.id))


@main.route('/cards/<int:card_id>')
@login_required
def card_detail(card_id):
    from .models import GreetingCard, CardSignature
    card = db.session.get(GreetingCard, card_id)
    if not card or card.family_id != current_user.active_family_id:
        abort(404)
    # Block recipient from viewing their own card
    if current_user.person and card.recipient_id == current_user.person.id:
        flash('This card is for you — no peeking! 🎉', 'info')
        return redirect(url_for('main.cards'))
    my_signature = None
    if current_user.person:
        my_signature = CardSignature.query.filter_by(
            card_id=card_id, person_id=current_user.person.id
        ).first()
    is_creator = current_user.person and card.created_by_id == current_user.person.id
    unsigned_members = []
    if current_user.active_is_admin or is_creator:
        signed_ids = {s.person_id for s in card.signatures}
        all_members = Person.query.filter_by(
            family_id=current_user.active_family_id, in_directory=True
        ).order_by(Person.name).all()
        unsigned_members = [p for p in all_members
                            if p.id not in signed_ids and p.id != card.recipient_id]
    return render_template('card_detail.html', card=card, my_signature=my_signature,
                           occasions=dict(CARD_OCCASIONS),
                           unsigned_members=unsigned_members,
                           is_creator=is_creator)


@main.route('/cards/<int:card_id>/sign', methods=['POST'])
@login_required
def sign_card(card_id):
    from .models import GreetingCard, CardSignature
    card = db.session.get(GreetingCard, card_id)
    if not card or card.family_id != current_user.active_family_id:
        abort(404)
    if not current_user.person:
        flash('Link your family profile to sign cards.', 'error')
        return redirect(url_for('main.card_detail', card_id=card_id))
    if card.recipient_id == current_user.person.id:
        abort(403)
    message = request.form.get('message', '').strip()
    if not message:
        flash('Please write a message.', 'error')
        return redirect(url_for('main.card_detail', card_id=card_id))
    existing = CardSignature.query.filter_by(
        card_id=card_id, person_id=current_user.person.id
    ).first()
    if existing:
        existing.message = message
    else:
        db.session.add(CardSignature(
            card_id=card_id, person_id=current_user.person.id, message=message
        ))
    db.session.commit()
    flash('Your message has been added to the card!', 'success')
    return redirect(url_for('main.card_detail', card_id=card_id))


@main.route('/cards/<int:card_id>/delete', methods=['POST'])
@login_required
def delete_card(card_id):
    from .models import GreetingCard
    card = db.session.get(GreetingCard, card_id)
    if not card or card.family_id != current_user.active_family_id:
        abort(404)
    is_creator = current_user.person and card.created_by_id == current_user.person.id
    if not current_user.active_is_admin and not is_creator:
        abort(403)
    db.session.delete(card)
    db.session.commit()
    flash('Card deleted.', 'info')
    return redirect(url_for('main.cards'))


@main.route('/cards/<int:card_id>/mark-sent', methods=['POST'])
@login_required
def mark_card_sent(card_id):
    from .models import GreetingCard
    card = db.session.get(GreetingCard, card_id)
    if not card or card.family_id != current_user.active_family_id:
        abort(404)
    is_creator = current_user.person and card.created_by_id == current_user.person.id
    if not current_user.active_is_admin and not is_creator:
        abort(403)
    card.sent_at = datetime.utcnow()
    db.session.commit()
    flash('Card marked as sent.', 'info')
    return redirect(url_for('main.card_detail', card_id=card_id))


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
        from .notifications import notify
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
    from .models import AnnouncementReaction
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
    from .notifications import notify_family
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
    from .models import LocationSleepingSpot
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
    from .models import LocationSleepingSpot
    loc = db.session.get(Location, loc_id)
    spot = db.session.get(LocationSleepingSpot, spot_id)
    if not loc or loc.family_id != current_user.active_family_id or not spot or spot.location_id != loc_id:
        flash('Room not found.', 'error')
        return redirect(url_for('main.admin_locations'))
    db.session.delete(spot)
    db.session.commit()
    return redirect(url_for('main.admin_locations'))


@main.route('/person/<int:person_id>/invite', methods=['POST'])
@login_required
@contributor_or_admin_required
def invite_person(person_id):
    person = db.session.get(Person, person_id)
    if not person or person.family_id != current_user.active_family_id:
        flash('Person not found.', 'error')
        return redirect(url_for('main.home'))
    if person.user:
        flash(f'{person.get_display_name()} already has an account.', 'error')
        return redirect(url_for('main.person_detail', person_id=person_id))
    if person.deathday:
        flash('Cannot invite a deceased person.', 'error')
        return redirect(url_for('main.person_detail', person_id=person_id))
    email = (person.email or request.form.get('email', '')).strip()
    if not email:
        flash('Add an email address to this person before sending an invitation.', 'error')
        return redirect(url_for('main.person_edit', person_id=person_id))
    existing_user = User.query.filter_by(email=email).first()
    if existing_user:
        # User already has an account — add them to this pod if not already a member.
        already = UserPodMembership.query.filter_by(
            user_id=existing_user.id, family_id=current_user.active_family_id
        ).first()
        if already:
            flash(f'{person.get_display_name()} is already a member of this circle.', 'error')
        else:
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
            flash(f'{person.get_display_name()} has been added to this circle.', 'info')
        return redirect(url_for('main.person_detail', person_id=person_id))
    names = person.name.strip().split()
    first = names[0]
    last = ' '.join(names[1:]) if len(names) > 1 else ''
    token = secrets.token_urlsafe(32)
    invited_user = User(
        family_id=current_user.active_family_id,
        email=email,
        first_name=first,
        last_name=last,
        password_hash='',
        status='invited',
        invitation_token=_hash_token(token),
        invitation_token_expiry=datetime.utcnow() + timedelta(days=7),
        person_id=person.id,
    )
    db.session.add(invited_user)
    db.session.commit()
    inviting_name = current_user.person.get_display_name() if current_user.person else current_user.get_full_name()
    if current_app.config.get('MAIL_ENABLED'):
        send_member_invitation_email(
            inviting_name, first, current_user.active_family.name,
            email, url_for('main.register_invited', token=token, _external=True)
        )
    flash(f'Invitation sent to {email}.', 'info')
    return redirect(url_for('main.person_detail', person_id=person_id))

@main.route('/profile')
@login_required
def profile():
    person = current_user.person
    if not person:
        flash('No profile found. Please contact the admin.', 'error')
        return redirect(url_for('main.home'))
    return render_template('profile.html', person=person, relationship=None, parent_roles=PARENT_ROLES, spouse_roles=SPOUSE_ROLES)

@main.route('/profile/delete-account', methods=['POST'])
@login_required
@limiter.limit('5 per hour')
def delete_account():
    from .account import delete_user_account, LastAdminError
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
    from .models import Notification
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
    from .models import Notification
    n = db.session.get(Notification, nid)
    if n and n.user_id == current_user.id and not n.read_at:
        n.read_at = datetime.utcnow()
        db.session.commit()
    return redirect(n.url or url_for('main.notifications'))


@main.route('/notifications/read-all', methods=['POST'])
@login_required
def notifications_read_all():
    from .models import Notification
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
    from .models import PhotoTag
    tagged_photos = PhotoTag.query.filter_by(person_id=person_id)\
        .join(Photo).filter(Photo.family_id == current_user.active_family_id)\
        .order_by(Photo.created_at.desc()).limit(6).all()
    tagged_photos = [t.photo for t in tagged_photos]
    return render_template('profile.html', person=person, relationship=relationship,
                           parent_roles=PARENT_ROLES, spouse_roles=SPOUSE_ROLES,
                           tagged_photos=tagged_photos)

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
        # Only update email if person has no login account (otherwise email = login email)
        if not person.user:
            person.email = form.email.data or None
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
    results = []
    if q:
        results = Person.query.filter(
            Person.family_id == current_user.active_family_id,
            db.or_(
                Person.name.ilike(f'%{q}%'),
                Person.nickname.ilike(f'%{q}%'),
            )
        ).order_by(Person.name).all()
    return render_template('search.html', q=q, results=results)

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
            invitation_token_expiry=datetime.utcnow() + timedelta(days=7),
            invited_by_id=current_user.id,
            family_id=current_user.active_family_id,
            person_id=spouse_person.id,
        )
        db.session.add(invited_user)
        db.session.commit()
        if current_app.config.get('MAIL_ENABLED'):
            send_spouse_invitation_email(
                person,
                invite_form.email.data,
                url_for('main.register_invited', token=invitation_token, _external=True)
            )
        flash(f'Invitation sent to {full_name} at {invite_form.email.data}.', 'info')
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


# ── Documents ─────────────────────────────────────────────────────────────────

@main.route('/documents')
@login_required
def documents_list():
    fid = current_user.active_family_id
    docs = Document.query.filter_by(family_id=fid).order_by(Document.uploaded_at.desc()).all()
    from itertools import groupby as _groupby
    grouped = {}
    for d in docs:
        cat = d.category or 'Other'
        grouped.setdefault(cat, []).append(d)
    ordered = [(cat, grouped[cat]) for cat in DOCUMENT_CATEGORIES if cat in grouped]
    return render_template('documents.html', grouped=ordered, categories=DOCUMENT_CATEGORIES)


@main.route('/documents/upload', methods=['POST'])
@login_required
def document_upload():
    from .storage import upload_document
    fid = current_user.active_family_id
    f = request.files.get('file')
    if not f or not f.filename:
        flash('No file selected.', 'error')
        return redirect(url_for('main.documents_list'))
    result = upload_document(f)
    if result is None:
        flash('Unsupported file type. Allowed: pdf, jpg, png, gif, webp, heic, txt, doc, docx', 'error')
        return redirect(url_for('main.documents_list'))
    key, ext, size = result
    title = request.form.get('title', '').strip() or f.filename.rsplit('.', 1)[0]
    my_person = current_user.person if current_user.person and current_user.person.family_id == fid else None
    doc = Document(
        family_id=fid,
        uploader_id=my_person.id if my_person else None,
        title=title,
        category=request.form.get('category') or None,
        storage_key=key,
        original_filename=f.filename,
        file_type=ext,
        file_size=size,
        notes=request.form.get('notes', '').strip() or None,
    )
    db.session.add(doc)
    db.session.commit()
    flash('Document uploaded.', 'success')
    return redirect(url_for('main.documents_list'))


@main.route('/documents/<int:doc_id>/view')
@login_required
def document_view(doc_id):
    from .storage import get_object_bytes
    from flask import Response
    doc = db.session.get(Document, doc_id)
    if not doc or doc.family_id != current_user.active_family_id:
        abort(404)
    data, content_type = get_object_bytes(doc.storage_key)
    inline_types = {'application/pdf', 'image/jpeg', 'image/png', 'image/gif', 'image/webp', 'text/plain'}
    disposition = 'inline' if content_type in inline_types else f'attachment; filename="{doc.original_filename}"'
    return Response(data, content_type=content_type,
                    headers={'Content-Disposition': disposition})


@main.route('/documents/<int:doc_id>/delete', methods=['POST'])
@login_required
@admin_required
def document_delete(doc_id):
    from .storage import delete_object
    doc = db.session.get(Document, doc_id)
    if not doc or doc.family_id != current_user.active_family_id:
        abort(404)
    delete_object(doc.storage_key)
    db.session.delete(doc)
    db.session.commit()
    flash('Document deleted.', 'success')
    return redirect(url_for('main.documents_list'))


# ── Events ────────────────────────────────────────────────────────────────────

@main.route('/events')
@login_required
def events_list():
    from sqlalchemy import func as _func
    today = date.today()
    all_events = Event.query.filter_by(family_id=current_user.active_family_id).order_by(Event.start_date).all()
    upcoming = [e for e in all_events if e.start_date >= today]
    past = [e for e in all_events if e.start_date < today]
    past.reverse()

    # Virtual future occurrences for recurring past events
    class _VirtualOccurrence:
        is_virtual = True
        def __init__(self, event, display_date):
            self._event = event
            self.start_date = display_date
        def __getattr__(self, name):
            return getattr(self._event, name)

    for e in list(past):
        if e.recur_freq or e.is_annual:
            next_d = e.next_occurrence(today - timedelta(days=1))
            if next_d:
                upcoming.append(_VirtualOccurrence(e, next_d))
    upcoming.sort(key=lambda e: e.start_date)
    has_paid_access = family_has_paid_access(current_user.active_family)

    # Current user's RSVP status on each upcoming event
    me = current_user.person
    rsvp_map = {}
    if me and upcoming:
        for row in EventRSVP.query.filter(
            EventRSVP.event_id.in_([e.id for e in upcoming]),
            EventRSVP.person_id == me.id
        ).all():
            rsvp_map[row.event_id] = row.status

    # Yes-RSVP headcounts for all events (single query)
    rsvp_counts = {}
    if all_events:
        for eid, cnt in db.session.query(
            EventRSVP.event_id, _func.count(EventRSVP.id)
        ).filter(
            EventRSVP.event_id.in_([e.id for e in all_events]),
            EventRSVP.status == 'yes'
        ).group_by(EventRSVP.event_id).all():
            rsvp_counts[eid] = cnt

    # Photo counts for past events (single query via album join)
    photo_counts = {}
    if past:
        for eid, cnt in db.session.query(
            Album.event_id, _func.count(Photo.id)
        ).join(Photo, Photo.album_id == Album.id).filter(
            Album.event_id.in_([e.id for e in past])
        ).group_by(Album.event_id).all():
            if eid:
                photo_counts[eid] = cnt

    # Group past events by year for display
    past_by_year = []
    for e in past:
        year = e.start_date.year
        if not past_by_year or past_by_year[-1][0] != year:
            past_by_year.append((year, []))
        past_by_year[-1][1].append(e)

    return render_template('events_list.html', upcoming=upcoming, past=past,
                           past_by_year=past_by_year,
                           has_paid_access=has_paid_access,
                           event_limit=FREE_EVENT_LIMIT,
                           rsvp_map=rsvp_map, rsvp_counts=rsvp_counts,
                           photo_counts=photo_counts,
                           me=me, today=today)


@main.route('/events/ai-parse', methods=['POST'])
@login_required
@admin_required
def event_ai_parse():
    """Parse a natural-language event description into structured fields using Claude."""
    import anthropic as _anthropic
    from flask import jsonify
    description = (request.json or {}).get('description', '').strip()
    if not description:
        return jsonify({'error': 'No description provided'}), 400

    api_key = current_app.config.get('ANTHROPIC_API_KEY')
    if not api_key:
        return jsonify({'error': 'AI not configured'}), 503

    today = date.today().isoformat()
    prompt = f"""Today is {today}. Extract structured event details from the description below.
Return ONLY valid JSON with these exact keys (omit keys you cannot determine):
- name (string)
- kind (one of: Reunion, Holiday, Birthday, Camping, Wedding, Graduation, Other)
- start_date (YYYY-MM-DD)
- end_date (YYYY-MM-DD, only if multi-day)
- start_time (HH:MM 24h, only if mentioned)
- end_time (HH:MM 24h, only if mentioned)
- location (string)
- description (string, a short summary)
- has_meals (true/false)
- has_sleeping (true/false)
- has_assignments (true/false)
- has_carpool (true/false)
- rooms (array of {{"name": string, "capacity": number}} — only if sleeping spots are described)

Description: {description}"""

    try:
        client = _anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model='claude-haiku-4-5-20251001',
            max_tokens=512,
            messages=[{'role': 'user', 'content': prompt}],
        )
        import json as _json
        text = msg.content[0].text.strip()
        # Strip markdown code fences if present
        if text.startswith('```'):
            text = text.split('\n', 1)[1].rsplit('```', 1)[0].strip()
        data = _json.loads(text)
        return jsonify(data)
    except Exception as e:
        current_app.logger.error(f'AI event parse error: {e}')
        return jsonify({'error': 'AI parsing failed'}), 500


@main.route('/cards/ai-draft', methods=['POST'])
@login_required
def card_ai_draft():
    from flask import jsonify
    from .ai import draft_card_message
    data = request.json or {}
    recipient_name = data.get('recipient_name', '').strip()
    occasion = data.get('occasion', '').strip()
    if not recipient_name or not occasion:
        return jsonify({'error': 'Missing fields'}), 400
    if not current_app.config.get('ANTHROPIC_API_KEY'):
        return jsonify({'error': 'AI not configured'}), 503
    try:
        message = draft_card_message(recipient_name, occasion, current_user.active_family.name)
        return jsonify({'message': message})
    except Exception as e:
        current_app.logger.error(f'AI card draft error: {e}')
        return jsonify({'error': 'AI draft failed'}), 500


@main.route('/polls/ai-suggest', methods=['POST'])
@login_required
def poll_ai_suggest():
    from flask import jsonify
    from .ai import suggest_poll
    data = request.json or {}
    topic = data.get('topic', '').strip()
    if not topic:
        return jsonify({'error': 'Missing topic'}), 400
    if not current_app.config.get('ANTHROPIC_API_KEY'):
        return jsonify({'error': 'AI not configured'}), 503
    try:
        result = suggest_poll(topic, current_user.active_family.name)
        return jsonify(result)
    except Exception as e:
        current_app.logger.error(f'AI poll suggest error: {e}')
        return jsonify({'error': 'AI suggest failed'}), 500


@main.route('/photos/<int:photo_id>/ai-caption', methods=['POST'])
@login_required
def photo_ai_caption(photo_id):
    from flask import jsonify
    from .ai import suggest_photo_caption
    from .storage import get_object_bytes
    photo = db.session.get(Photo, photo_id)
    if not photo or photo.family_id != current_user.active_family_id:
        return jsonify({'error': 'Not found'}), 404
    if not current_app.config.get('ANTHROPIC_API_KEY'):
        return jsonify({'error': 'AI not configured'}), 503
    try:
        image_bytes, content_type = get_object_bytes(photo.path)
        caption = suggest_photo_caption(image_bytes, content_type)
        return jsonify({'caption': caption})
    except Exception as e:
        current_app.logger.error(f'AI photo caption error: {e}')
        return jsonify({'error': 'AI caption failed'}), 500


def _geocode_location(location_str):
    """Return (lat, lng) for a location string, or (None, None)."""
    if not location_str:
        return None, None
    try:
        import requests as _req
        resp = _req.get(
            'https://nominatim.openstreetmap.org/search',
            params={'q': location_str, 'format': 'json', 'limit': 1},
            headers={'User-Agent': 'swugl-family-app/1.0'},
            timeout=5,
        )
        results = resp.json()
        if results:
            return float(results[0]['lat']), float(results[0]['lon'])
    except Exception:
        pass
    return None, None


@main.route('/events/add', methods=['GET', 'POST'])
@login_required
@admin_required
def event_add():
    family = current_user.active_family
    has_paid_access = family_has_paid_access(family)
    saved_locations = (Location.query
                       .filter_by(family_id=current_user.active_family_id)
                       .order_by(Location.name).all())
    form = EventForm()
    if form.validate_on_submit():
        if not has_paid_access:
            upcoming_count = Event.query.filter(
                Event.family_id == current_user.active_family_id,
                Event.start_date >= date.today()
            ).count()
            if upcoming_count >= FREE_EVENT_LIMIT:
                flash(f'Free plan is limited to {FREE_EVENT_LIMIT} upcoming events. '
                      'Upgrade to create unlimited events.', 'warning')
                return redirect(url_for('billing.billing_page'))
        if form.end_date.data and form.start_date.data and form.end_date.data < form.start_date.data:
            form.end_date.errors.append('End date cannot be before start date.')
            return render_template('event_form.html', form=form, event=None, saved_locations=saved_locations)
        loc_id = int(form.location_id.data) if form.location_id.data else None
        saved_loc = db.session.get(Location, loc_id) if loc_id else None
        if saved_loc and saved_loc.family_id == current_user.active_family_id:
            location = saved_loc.address or saved_loc.name
            lat, lng = saved_loc.lat, saved_loc.lng
        else:
            loc_id = None
            location = form.location.data or None
            lat, lng = _geocode_location(location)
        _paid = family_has_paid_access(current_user.active_family)
        event = Event(
            family_id=current_user.active_family_id,
            name=form.name.data,
            kind=form.kind.data or None,
            description=form.description.data or None,
            location=location,
            location_id=loc_id,
            lat=lat,
            lng=lng,
            start_date=form.start_date.data,
            end_date=form.end_date.data,
            start_time=form.start_time.data,
            end_time=form.end_time.data,
            rsvp_deadline=form.rsvp_deadline.data,
            recur_freq=form.recur_freq.data or None,
            recur_until=form.recur_until.data,
            is_annual=(form.recur_freq.data == 'yearly'),
            # Paid sections silently drop to off on the free plan (the form
            # shows them disabled with an upgrade hint)
            has_meals=form.has_meals.data and _paid,
            has_assignments=form.has_assignments.data and _paid,
            has_sleeping=form.has_sleeping.data and _paid,
            has_carpool=form.has_carpool.data,
        )
        db.session.add(event)
        db.session.flush()

        # Create sleeping spots submitted with the form
        from .models import EventSleepingSpot
        room_index = 0
        while True:
            room_name = request.form.get(f'rooms[{room_index}][name]', '').strip()
            if not room_name:
                break
            try:
                capacity = int(request.form.get(f'rooms[{room_index}][capacity]', '') or 0) or None
            except ValueError:
                capacity = None
            room_type = request.form.get(f'rooms[{room_index}][type]', '').strip() or None
            if room_type and room_type not in SPOT_TYPES:
                room_type = None
            db.session.add(EventSleepingSpot(event_id=event.id, name=room_name, spot_type=room_type, capacity=capacity))
            room_index += 1
        # Fallback: seed from location template if no rooms were submitted
        if room_index == 0 and event.has_sleeping and saved_loc and saved_loc.sleeping_spots:
            for spot in saved_loc.sleeping_spots:
                db.session.add(EventSleepingSpot(
                    event_id=event.id, name=spot.name,
                    spot_type=spot.spot_type, capacity=spot.capacity,
                ))

        # Seed meals from the day-grid checkboxes on the form
        _MEAL_LABELS = {'breakfast': 'Breakfast', 'lunch': 'Lunch', 'dinner': 'Dinner'}
        _MEAL_TIMES  = {'breakfast': '8:00 AM',   'lunch': '12:00 PM', 'dinner': '6:00 PM'}
        for key in request.form:
            m = re.match(r'^meals\[(\d{4}-\d{2}-\d{2})\]\[(breakfast|lunch|dinner)\]$', key)
            if m:
                try:
                    meal_date_val = date.fromisoformat(m.group(1))
                except ValueError:
                    continue
                meal_type = m.group(2)
                db.session.add(EventMeal(
                    event_id=event.id,
                    name=f'{meal_date_val.strftime("%A")} {_MEAL_LABELS[meal_type]}',
                    meal_date=meal_date_val,
                    meal_time=_MEAL_TIMES[meal_type],
                ))

        # Seed assignments from the task seed list
        for ti in range(50):
            task_title = request.form.get(f'tasks[{ti}][title]', '').strip()
            if not task_title:
                continue
            task_cat = request.form.get(f'tasks[{ti}][category]', '').strip() or None
            if task_cat and task_cat not in ASSIGNMENT_CATEGORIES:
                task_cat = None
            db.session.add(EventAssignment(event_id=event.id, title=task_title[:150], category=task_cat))

        if form.cover_image.data and hasattr(form.cover_image.data, 'filename') and form.cover_image.data.filename:
            key = upload_photo(form.cover_image.data, folder='events')
            if key:
                event.cover_image_path = key
        db.session.commit()
        flash(f'{event.name} has been created.', 'info')
        from .notifications import notify
        recipients = User.query.filter_by(
            family_id=current_user.active_family_id, status='approved'
        ).filter(User.id != current_user.id).all()
        event_url = url_for('main.event_detail', event_id=event.id, _external=True)
        notify(recipients, 'new_event', event=event, url=event_url)
        return redirect(url_for('main.event_detail', event_id=event.id))
    return render_template('event_form.html', form=form, event=None, saved_locations=saved_locations)


@main.route('/events/<int:event_id>')
@login_required
def event_detail(event_id):
    event = db.session.get(Event, event_id)
    if not event or event.family_id != current_user.active_family_id:
        flash('Event not found.', 'error')
        return redirect(url_for('main.events_list'))
    all_people = Person.query.filter_by(family_id=current_user.active_family_id).order_by(Person.name).all()
    dir_people = [p for p in all_people if p.in_directory]
    people_choices = [(0, '— Select —')] + [(p.id, p.get_display_name()) for p in dir_people]

    meal_form = EventMealForm()
    meal_item_form = EventMealItemForm()
    # Per-meal family-assign forms (admin only) — deduplicated couples, directory members only
    couple_people = [p for p in dir_people if not p.get_active_spouse() or p.id < p.get_active_spouse().id]
    meal_family_forms = {}
    for meal in event.meals:
        f = EventMealFamilyAssignForm(prefix=f'meal_fam_{meal.id}')
        f.assigned_family_id.choices = [(0, '— None —')] + [(p.id, p.get_couple_name()) for p in couple_people]
        meal_family_forms[meal.id] = f
    # Per-item assign forms — directory members only
    item_assign_forms = {}
    for meal in event.meals:
        for item in meal.items:
            f = EventMealAssignForm(prefix=f'item_{item.id}')
            f.person_id.choices = people_choices
            item_assign_forms[item.id] = f

    assign_form = EventAssignmentForm()
    # Per-assignment admin-assign forms — directory members only
    assign_admin_forms = {}
    if current_user.active_is_admin:
        for a in event.assignments:
            f = EventAssignmentAdminAssignForm(prefix=f'a_{a.id}')
            f.person_id.choices = people_choices
            assign_admin_forms[a.id] = f

    spot_form = EventSleepingSpotForm()
    couple_people = [p for p in dir_people if not p.get_active_spouse() or p.id < p.get_active_spouse().id]
    sleeping_assign_forms = {}
    if current_user.active_is_admin:
        for spot in event.sleeping_spots:
            spot_assigned_ids = {p.id for p in spot.people}
            available = [(p.id, p.get_display_name()) for p in all_people if p.in_directory and p.id not in spot_assigned_ids]
            f = EventSleepingAssignForm(prefix=f'spot_{spot.id}')
            f.person_id.choices = [(0, '— Select —')] + available
            sleeping_assign_forms[spot.id] = f

    self_signup_form = EventMealSelfSignupForm()
    my_person = current_user.person
    event_form = EventForm(obj=event)
    comment_form = EventCommentForm()

    # RSVP data
    rsvp_map = {r.person_id: r.status for r in event.rsvps}
    # Household = self + spouse + minor children (under 18, or unknown age)
    household = []
    if my_person:
        household.append(my_person)
        spouse = my_person.get_active_spouse()
        if spouse:
            household.append(spouse)
        child_ids = set()
        for rel in my_person.child_rels:
            child_ids.add(rel.child_id)
        if spouse:
            for rel in spouse.child_rels:
                child_ids.add(rel.child_id)
        unmarried_children = sorted(
            [p for p in all_people if p.id in child_ids and _in_parent_household(p)],
            key=lambda p: p.get_display_name()
        )
        household.extend(unmarried_children)

    # Build grouped RSVP summary: one entry per household unit
    rsvp_groups = _build_rsvp_groups(event, all_people)
    # Full family groups — always built; used for stats + non-responder list for everyone
    _all_fg = _build_family_groups(all_people, rsvp_map)
    family_groups = _all_fg if (current_user.active_is_admin or current_user.active_is_delegate) else []

    rsvp_stats = {
        'yes_people':  sum(1 for s in rsvp_map.values() if s == 'yes'),
        'maybe_people': sum(1 for s in rsvp_map.values() if s == 'maybe'),
        'no_people':   sum(1 for s in rsvp_map.values() if s == 'no'),
        'yes_households': sum(
            1 for g in _all_fg
            if any(s == 'yes' for _, s in g['adults'] + g['children'])
        ),
    }
    not_responded = [
        g['label'] for g in _all_fg
        if all(s is None for _, s in g['adults'] + g['children'])
    ]

    from .weather import get_event_weather
    try:
        weather = get_event_weather(event)
    except Exception:
        weather = None

    payment_config = event.payment_config if event.payment_config and event.payment_config.is_active else None
    my_payment = None
    my_charge_cents = None
    if payment_config and current_user.is_authenticated:
        my_payment = EventPaymentRecord.query.filter_by(
            event_id=event.id, payer_user_id=current_user.id
        ).first()
        my_charge_cents = _compute_member_charge(payment_config, current_user)

    # Admin progress stats
    payment_stats = None
    if payment_config and current_user.active_is_admin:
        paid_records = EventPaymentRecord.query.filter_by(event_id=event.id, status='paid').all()
        total_amount = sum(r.amount_cents for r in paid_records)
        payment_stats = {
            'paid_count': len(paid_records),
            'total_cents': total_amount,
        }

    _edr, _cur = [], event.start_date
    while _cur <= (event.end_date or event.start_date):
        _edr.append(_cur)
        _cur += timedelta(days=1)

    event_photos = Photo.query.join(Album).filter(
        Album.event_id == event.id,
        Album.family_id == current_user.active_family_id,
    ).order_by(Photo.created_at.asc()).limit(12).all()

    has_paid_access = family_has_paid_access(current_user.family)

    return render_template('event_detail.html',
        event=event,
        event_date_range=_edr,
        event_photos=event_photos,
        has_paid_access=has_paid_access,
        meal_form=meal_form,
        meal_item_form=meal_item_form,
        meal_family_forms=meal_family_forms,
        item_assign_forms=item_assign_forms,
        people_choices=people_choices,
        assign_form=assign_form,
        assign_admin_forms=assign_admin_forms,
        self_signup_form=self_signup_form,
        spot_form=spot_form,
        sleeping_assign_forms=sleeping_assign_forms,
        couple_people=couple_people,
        my_person=my_person,
        event_form=event_form,
        comment_form=comment_form,
        assignment_categories=ASSIGNMENT_CATEGORIES,
        rsvp_map=rsvp_map,
        rsvp_stats=rsvp_stats,
        not_responded=not_responded,
        household=household,
        rsvp_groups=rsvp_groups,
        family_groups=family_groups,
        all_people=all_people,
        weather=weather,
        payment_config=payment_config,
        my_payment=my_payment,
        my_charge_cents=my_charge_cents,
        payment_stats=payment_stats,
        payout_account=current_user.family.payout_account,
    )


@main.route('/events/<int:event_id>/edit', methods=['GET', 'POST'])
@login_required
@admin_required
def event_edit(event_id):
    event = db.session.get(Event, event_id)
    if not event or event.family_id != current_user.active_family_id:
        flash('Event not found.', 'error')
        return redirect(url_for('main.events_list'))
    saved_locations = (Location.query
                       .filter_by(family_id=current_user.active_family_id)
                       .order_by(Location.name).all())
    form = EventForm(obj=event)
    if form.validate_on_submit():
        if form.end_date.data and form.start_date.data and form.end_date.data < form.start_date.data:
            form.end_date.errors.append('End date cannot be before start date.')
            return render_template('event_form.html', form=form, event=event, saved_locations=saved_locations)
        event.name = form.name.data
        event.kind = form.kind.data or None
        event.description = form.description.data or None
        loc_id = int(form.location_id.data) if form.location_id.data else None
        saved_loc = db.session.get(Location, loc_id) if loc_id else None
        if saved_loc and saved_loc.family_id == current_user.active_family_id:
            new_location = saved_loc.address or saved_loc.name
            event.lat, event.lng = saved_loc.lat, saved_loc.lng
            event.location_id = loc_id
        else:
            event.location_id = None
            new_location = form.location.data or None
            if new_location != event.location:
                event.lat, event.lng = _geocode_location(new_location)
        event.location = new_location
        event.start_date = form.start_date.data
        event.end_date = form.end_date.data
        event.start_time = form.start_time.data
        event.end_time = form.end_time.data
        event.rsvp_deadline = form.rsvp_deadline.data
        event.recur_freq = form.recur_freq.data or None
        event.recur_until = form.recur_until.data
        event.is_annual = (form.recur_freq.data == 'yearly')
        # Free plan can turn paid sections off but not on; already-enabled
        # sections survive a downgrade untouched
        _paid = family_has_paid_access(current_user.active_family)
        event.has_meals = form.has_meals.data if _paid else (event.has_meals and form.has_meals.data)
        event.has_assignments = form.has_assignments.data if _paid else (event.has_assignments and form.has_assignments.data)
        event.has_sleeping = form.has_sleeping.data if _paid else (event.has_sleeping and form.has_sleeping.data)
        event.has_carpool = form.has_carpool.data
        if form.remove_cover.data and event.cover_image_path:
            delete_object(event.cover_image_path)
            event.cover_image_path = None
        elif form.cover_image.data and hasattr(form.cover_image.data, 'filename') and form.cover_image.data.filename:
            delete_object(event.cover_image_path)
            key = upload_photo(form.cover_image.data, folder='events')
            if key:
                event.cover_image_path = key
        db.session.commit()
        flash('Event updated.', 'info')
        return redirect(url_for('main.event_detail', event_id=event.id))
    # Pre-populate location_id from the event for the edit form
    if not form.location_id.data and event.location_id:
        form.location_id.data = str(event.location_id)
    return render_template('event_form.html', form=form, event=event, saved_locations=saved_locations)


@main.route('/events/<int:event_id>/payment/setup', methods=['POST'])
@login_required
@admin_required
@requires_plan
def event_payment_setup(event_id):
    event = db.session.get(Event, event_id)
    if not event or event.family_id != current_user.active_family_id:
        flash('Event not found.', 'error')
        return redirect(url_for('main.events_list'))

    try:
        amount_dollars = float(request.form.get('amount_dollars', 0))
        amount_cents = int(round(amount_dollars * 100))
    except (ValueError, TypeError):
        flash('Invalid amount.', 'error')
        return redirect(url_for('main.event_detail', event_id=event_id))

    if amount_cents < 50:
        flash('Amount must be at least $0.50.', 'error')
        return redirect(url_for('main.event_detail', event_id=event_id))

    charge_type = request.form.get('charge_type', 'per_family')
    if charge_type not in ('per_family', 'per_person'):
        charge_type = 'per_family'

    family_cap_cents = None
    if charge_type == 'per_person':
        cap_str = request.form.get('family_cap_dollars', '').strip()
        if cap_str:
            try:
                cap_val = int(round(float(cap_str) * 100))
                if cap_val > amount_cents:
                    family_cap_cents = cap_val
            except (ValueError, TypeError):
                pass

    deadline_str = request.form.get('deadline', '').strip()
    deadline = datetime.strptime(deadline_str, '%Y-%m-%d').date() if deadline_str else None

    config = event.payment_config
    if config:
        config.amount_cents = amount_cents
        config.charge_type = charge_type
        config.family_cap_cents = family_cap_cents
        config.description = request.form.get('description', '').strip()[:200]
        config.deadline = deadline
        config.is_active = True
    else:
        config = EventPaymentConfig(
            event_id=event.id,
            amount_cents=amount_cents,
            charge_type=charge_type,
            family_cap_cents=family_cap_cents,
            description=request.form.get('description', '').strip()[:200],
            deadline=deadline,
        )
        db.session.add(config)

    db.session.commit()
    flash('Payment collection enabled.', 'success')
    return redirect(url_for('main.event_detail', event_id=event_id))


@main.route('/events/<int:event_id>/payment/disable', methods=['POST'])
@login_required
@admin_required
def event_payment_disable(event_id):
    event = db.session.get(Event, event_id)
    if not event or event.family_id != current_user.active_family_id:
        flash('Event not found.', 'error')
        return redirect(url_for('main.events_list'))
    if event.payment_config:
        event.payment_config.is_active = False
        db.session.commit()
    flash('Payment collection disabled.', 'info')
    return redirect(url_for('main.event_detail', event_id=event_id))


@main.route('/events/<int:event_id>/payment/checkout', methods=['POST'])
@login_required
def event_payment_checkout(event_id):
    from .billing import _stripe
    event = db.session.get(Event, event_id)
    if not event or event.family_id != current_user.active_family_id:
        flash('Event not found.', 'error')
        return redirect(url_for('main.events_list'))

    config = event.payment_config
    if not config or not config.is_active:
        flash('Payment is not enabled for this event.', 'error')
        return redirect(url_for('main.event_detail', event_id=event_id))

    existing = EventPaymentRecord.query.filter_by(
        event_id=event.id, payer_user_id=current_user.id
    ).first()
    if existing and existing.status == 'paid':
        flash('You have already paid for this event.', 'info')
        return redirect(url_for('main.event_detail', event_id=event_id))

    charge_amount = _compute_member_charge(config, current_user)
    yes_in_household = 0
    if config.charge_type == 'per_person':
        household_ids = _get_household_ids(current_user.person)
        yes_in_household = EventRSVP.query.filter(
            EventRSVP.event_id == event.id,
            EventRSVP.status == 'yes',
            EventRSVP.person_id.in_(household_ids),
        ).count() if household_ids else 1

    s = _stripe()
    if not s:
        flash('Payment processing is not configured yet.', 'error')
        return redirect(url_for('main.event_detail', event_id=event_id))

    record = existing or EventPaymentRecord(
        event_id=event.id,
        payer_user_id=current_user.id,
        amount_cents=charge_amount,
        status='pending',
    )
    if not existing:
        db.session.add(record)
    else:
        record.amount_cents = charge_amount
        record.status = 'pending'
    db.session.commit()

    description = config.description or event.name
    if config.charge_type == 'per_person' and yes_in_household > 1:
        capped = config.family_cap_cents and charge_amount < config.amount_cents * yes_in_household
        if capped:
            description = f'{description} ({yes_in_household} people, capped at ${charge_amount/100:.2f})'
        else:
            description = f'{description} ({yes_in_household} people × ${config.amount_dollars:.2f})'

    family = current_user.family
    kwargs = dict(
        mode='payment',
        line_items=[{
            'price_data': {
                'currency': 'usd',
                'unit_amount': charge_amount,
                'product_data': {'name': description},
            },
            'quantity': 1,
        }],
        success_url=url_for('main.event_payment_success', event_id=event_id, _external=True) + '?session_id={CHECKOUT_SESSION_ID}',
        cancel_url=url_for('main.event_detail', event_id=event_id, _external=True),
        client_reference_id=str(current_user.id),
        metadata={
            'payment_type': 'event',
            'event_id': str(event.id),
            'payer_user_id': str(current_user.id),
            'family_id': str(family.id),
        },
    )
    if family.stripe_customer_id:
        kwargs['customer'] = family.stripe_customer_id
    else:
        kwargs['customer_email'] = current_user.email

    try:
        session_obj = s.checkout.Session.create(**kwargs)
        record.stripe_checkout_session_id = session_obj.id
        db.session.commit()
        return redirect(session_obj.url, code=303)
    except Exception as e:
        flash('Could not start checkout. Please try again.', 'error')
        current_app.logger.error(f'Stripe event checkout error: {e}')
        return redirect(url_for('main.event_detail', event_id=event_id))


@main.route('/events/<int:event_id>/payment/success')
@login_required
def event_payment_success(event_id):
    event = db.session.get(Event, event_id)
    if not event or event.family_id != current_user.active_family_id:
        return redirect(url_for('main.events_list'))
    flash('Payment received! You\'re all set.', 'success')
    return redirect(url_for('main.event_detail', event_id=event_id))


def _compute_member_charge(config, user):
    """Return charge in cents for this user given event payment config."""
    if config.charge_type == 'per_person':
        hids = _get_household_ids(user.person)
        yes_count = EventRSVP.query.filter(
            EventRSVP.event_id == config.event_id,
            EventRSVP.status == 'yes',
            EventRSVP.person_id.in_(hids),
        ).count() if hids else 1
        total = config.amount_cents * max(1, yes_count)
        if config.family_cap_cents:
            total = min(total, config.family_cap_cents)
        return total
    return config.amount_cents


@main.route('/events/<int:event_id>/delete', methods=['POST'])
@login_required
@admin_required
def event_delete(event_id):
    event = db.session.get(Event, event_id)
    if not event or event.family_id != current_user.active_family_id:
        flash('Event not found.', 'error')
        return redirect(url_for('main.events_list'))
    db.session.delete(event)
    db.session.commit()
    flash(f'{event.name} has been deleted.', 'info')
    return redirect(url_for('main.events_list'))


@main.route('/events/<int:event_id>/enable-section', methods=['POST'])
@login_required
@admin_required
def event_enable_section(event_id):
    event = db.session.get(Event, event_id)
    if not event or event.family_id != current_user.active_family_id:
        return redirect(url_for('main.events_list'))
    section = request.form.get('section')
    # Meals/assignments/sleeping are organizer power tools — paid plan only.
    # Carpool stays free. Already-enabled sections keep working after a
    # downgrade; only turning new ones on is gated.
    if section in ('meals', 'assignments', 'sleeping') and not family_has_paid_access(current_user.active_family):
        flash('Meal planning, assignments, and sleeping arrangements are paid features. '
              'Upgrade to enable them.', 'warning')
        return redirect(url_for('billing.billing_page'))
    if section == 'meals':
        event.has_meals = True
    elif section == 'assignments':
        event.has_assignments = True
    elif section == 'sleeping':
        event.has_sleeping = True
    elif section == 'carpool':
        event.has_carpool = True
    db.session.commit()
    return redirect(url_for('main.event_detail', event_id=event_id))


@main.route('/events/<int:event_id>/disable-section', methods=['POST'])
@login_required
@admin_required
def event_disable_section(event_id):
    event = db.session.get(Event, event_id)
    if not event or event.family_id != current_user.active_family_id:
        return redirect(url_for('main.events_list'))
    section = request.form.get('section')
    if section == 'meals':
        event.has_meals = False
    elif section == 'assignments':
        event.has_assignments = False
    elif section == 'sleeping':
        event.has_sleeping = False
    elif section == 'carpool':
        event.has_carpool = False
    db.session.commit()
    return redirect(url_for('main.event_detail', event_id=event_id))


# ── Carpool ───────────────────────────────────────────────────────────────────

@main.route('/events/<int:event_id>/carpool/offer', methods=['POST'])
@login_required
def carpool_offer(event_id):
    from .models import CarpoolOffer
    event = db.session.get(Event, event_id)
    if not event or event.family_id != current_user.active_family_id or not current_user.person:
        return redirect(url_for('main.events_list'))
    role = request.form.get('role', 'rider')
    if role not in ('driver', 'rider'):
        role = 'rider'
    seats = request.form.get('seats', type=int)
    departure_from = request.form.get('departure_from', '').strip()[:150] or None
    notes = request.form.get('notes', '').strip()[:200] or None
    existing = CarpoolOffer.query.filter_by(event_id=event_id, person_id=current_user.person.id).first()
    if existing:
        existing.role = role
        existing.seats = seats if role == 'driver' else None
        existing.departure_from = departure_from if role == 'driver' else None
        existing.notes = notes
        if role == 'driver':
            existing.passenger_of_id = None  # switching to driver clears any ride claim
    else:
        db.session.add(CarpoolOffer(
            event_id=event_id, person_id=current_user.person.id,
            role=role,
            seats=seats if role == 'driver' else None,
            departure_from=departure_from if role == 'driver' else None,
            notes=notes,
        ))
    db.session.commit()
    return redirect(url_for('main.event_detail', event_id=event_id))


@main.route('/events/<int:event_id>/carpool/remove', methods=['POST'])
@login_required
def carpool_remove(event_id):
    from .models import CarpoolOffer
    if current_user.person:
        # Clear any passengers assigned to this person's driver offer first
        offer = CarpoolOffer.query.filter_by(event_id=event_id, person_id=current_user.person.id).first()
        if offer:
            CarpoolOffer.query.filter_by(passenger_of_id=offer.id).update({'passenger_of_id': None})
            db.session.delete(offer)
            db.session.commit()
    return redirect(url_for('main.event_detail', event_id=event_id))


@main.route('/events/<int:event_id>/carpool/<int:offer_id>/claim', methods=['POST'])
@login_required
def carpool_claim_seat(event_id, offer_id):
    from .models import CarpoolOffer
    event = db.session.get(Event, event_id)
    if not event or event.family_id != current_user.active_family_id or not current_user.person:
        return redirect(url_for('main.events_list'))
    driver_offer = db.session.get(CarpoolOffer, offer_id)
    if not driver_offer or driver_offer.event_id != event_id or driver_offer.role != 'driver':
        flash('Driver not found.', 'error')
        return redirect(url_for('main.event_detail', event_id=event_id))
    # Check capacity
    if driver_offer.seats:
        taken = CarpoolOffer.query.filter_by(passenger_of_id=offer_id).count()
        if taken >= driver_offer.seats:
            flash(f'{driver_offer.person.get_display_name()}\'s car is full.', 'error')
            return redirect(url_for('main.event_detail', event_id=event_id))
    existing = CarpoolOffer.query.filter_by(event_id=event_id, person_id=current_user.person.id).first()
    if existing:
        existing.role = 'rider'
        existing.passenger_of_id = offer_id
        existing.seats = None
        existing.departure_from = None
    else:
        db.session.add(CarpoolOffer(
            event_id=event_id, person_id=current_user.person.id,
            role='rider', passenger_of_id=offer_id,
        ))
    db.session.commit()
    flash(f'Seat claimed with {driver_offer.person.get_display_name()}.', 'info')
    return redirect(url_for('main.event_detail', event_id=event_id))


@main.route('/events/<int:event_id>/carpool/unclaim', methods=['POST'])
@login_required
def carpool_unclaim(event_id):
    from .models import CarpoolOffer
    if current_user.person:
        offer = CarpoolOffer.query.filter_by(event_id=event_id, person_id=current_user.person.id).first()
        if offer:
            offer.passenger_of_id = None
            db.session.commit()
    return redirect(url_for('main.event_detail', event_id=event_id))


@main.route('/events/<int:event_id>/carpool/<int:offer_id>/assign-rider', methods=['POST'])
@login_required
@admin_required
def carpool_assign_rider(event_id, offer_id):
    from .models import CarpoolOffer
    event = db.session.get(Event, event_id)
    if not event or event.family_id != current_user.active_family_id:
        return redirect(url_for('main.events_list'))
    driver_offer = db.session.get(CarpoolOffer, offer_id)
    if not driver_offer or driver_offer.event_id != event_id or driver_offer.role != 'driver':
        return redirect(url_for('main.event_detail', event_id=event_id))
    try:
        rider_offer_id = int(request.form.get('rider_offer_id', 0))
    except (ValueError, TypeError):
        rider_offer_id = 0
    if not rider_offer_id:
        return redirect(url_for('main.event_detail', event_id=event_id))
    rider_offer = db.session.get(CarpoolOffer, rider_offer_id)
    if not rider_offer or rider_offer.event_id != event_id or rider_offer.role != 'rider':
        return redirect(url_for('main.event_detail', event_id=event_id))
    if driver_offer.seats:
        taken = CarpoolOffer.query.filter_by(passenger_of_id=offer_id).count()
        if taken >= driver_offer.seats and rider_offer.passenger_of_id != offer_id:
            flash(f'{driver_offer.person.get_display_name()}\'s car is full.', 'error')
            return redirect(url_for('main.event_detail', event_id=event_id))
    rider_offer.passenger_of_id = offer_id
    db.session.commit()
    flash(f'{rider_offer.person.get_display_name()} assigned to {driver_offer.person.get_display_name()}.', 'info')
    return redirect(url_for('main.event_detail', event_id=event_id))


# ── Survey ────────────────────────────────────────────────────────────────────

@main.route('/events/<int:event_id>/survey', methods=['GET', 'POST'])
@login_required
def event_survey(event_id):
    from .models import EventSurveyResponse
    event = db.session.get(Event, event_id)
    if not event or event.family_id != current_user.active_family_id:
        abort(404)
    # Only available after event has passed
    if event.start_date > date.today():
        flash('The survey will be available after the event.', 'info')
        return redirect(url_for('main.event_detail', event_id=event_id))
    my_response = None
    if current_user.person:
        my_response = EventSurveyResponse.query.filter_by(
            event_id=event_id, person_id=current_user.person.id
        ).first()
    if request.method == 'POST':
        if not current_user.person:
            flash('Link your family profile to submit a survey.', 'error')
            return redirect(url_for('main.event_survey', event_id=event_id))
        rating = request.form.get('rating', type=int)
        if not rating or rating < 1 or rating > 5:
            flash('Please select a rating.', 'error')
            return redirect(url_for('main.event_survey', event_id=event_id))
        what_worked = request.form.get('what_worked', '').strip() or None
        suggestions = request.form.get('suggestions', '').strip() or None
        if my_response:
            my_response.rating = rating
            my_response.what_worked = what_worked
            my_response.suggestions = suggestions
        else:
            db.session.add(EventSurveyResponse(
                event_id=event_id, person_id=current_user.person.id,
                rating=rating, what_worked=what_worked, suggestions=suggestions,
            ))
        db.session.commit()
        flash('Thanks for your feedback!', 'success')
        return redirect(url_for('main.event_detail', event_id=event_id))
    return render_template('event_survey.html', event=event, my_response=my_response)


@main.route('/events/<int:event_id>/survey/results')
@login_required
@admin_required
def event_survey_results(event_id):
    from .models import EventSurveyResponse
    event = db.session.get(Event, event_id)
    if not event or event.family_id != current_user.active_family_id:
        abort(404)
    responses = EventSurveyResponse.query.filter_by(event_id=event_id)\
        .order_by(EventSurveyResponse.submitted_at.desc()).all()
    avg_rating = (sum(r.rating for r in responses) / len(responses)) if responses else None
    return render_template('event_survey_results.html', event=event,
                           responses=responses, avg_rating=avg_rating)


# ── Comments ──────────────────────────────────────────────────────────────────

@main.route('/events/<int:event_id>/comments', methods=['POST'])
@login_required
def event_comment_add(event_id):
    event = db.session.get(Event, event_id)
    if not event or event.family_id != current_user.active_family_id:
        return redirect(url_for('main.events_list'))
    if not current_user.person:
        flash('You need a family profile to comment.', 'error')
        return redirect(url_for('main.event_detail', event_id=event_id))
    form = EventCommentForm()
    if form.validate_on_submit():
        comment = EventComment(
            event_id=event_id,
            person_id=current_user.person.id,
            body=form.body.data.strip(),
        )
        db.session.add(comment)
        db.session.commit()

        from .notifications import create_notification
        from .models import NotificationPreference, EventRSVP, Person
        from .email import send_event_comment_notification
        commenter_name = current_user.person.get_display_name()
        commenter_person_id = current_user.person.id
        attendee_person_ids = {
            r.person_id for r in
            EventRSVP.query.filter(
                EventRSVP.event_id == event_id,
                EventRSVP.status.in_(['yes', 'maybe']),
            ).all()
        }
        body_preview = comment.body[:100] + ('…' if len(comment.body) > 100 else '')
        notif_title = f'{commenter_name} commented on {event.name}'
        event_url = url_for('main.event_detail', event_id=event_id)
        for person_id in attendee_person_ids:
            if person_id == commenter_person_id:
                continue
            person = db.session.get(Person, person_id)
            if not person or not person.user:
                continue
            recipient = person.user
            # In-app + push (create_notification checks in_app preference internally)
            create_notification(recipient, 'event_comment',
                                title=notif_title, body=body_preview, url=event_url)
            # Email
            if (current_app.config.get('MAIL_ENABLED')
                    and NotificationPreference.is_enabled(recipient.id, 'event_comment', 'email')):
                send_event_comment_notification(
                    recipient, commenter_name, event, comment.body, event_url)
    return redirect(url_for('main.event_detail', event_id=event_id))


@main.route('/events/<int:event_id>/comments/<int:comment_id>/delete', methods=['POST'])
@login_required
def event_comment_delete(event_id, comment_id):
    event = db.session.get(Event, event_id)
    comment = db.session.get(EventComment, comment_id)
    if not event or event.family_id != current_user.active_family_id or not comment or comment.event_id != event_id:
        return redirect(url_for('main.events_list'))
    can_delete = current_user.active_is_admin or (current_user.person and comment.person_id == current_user.person.id)
    if can_delete:
        db.session.delete(comment)
        db.session.commit()
    return redirect(url_for('main.event_detail', event_id=event_id))


# ── RSVPs ─────────────────────────────────────────────────────────────────────

@main.route('/events/<int:event_id>/rsvp', methods=['POST'])
@login_required
def event_rsvp(event_id):
    event = db.session.get(Event, event_id)
    if not event or event.family_id != current_user.active_family_id:
        return redirect(url_for('main.events_list'))
    person_id = request.form.get('person_id', type=int)
    status = request.form.get('status')
    if not person_id or status not in ('yes', 'no', 'maybe', 'clear'):
        return redirect(url_for('main.event_detail', event_id=event_id))
    # Verify this person belongs to the family
    person = db.session.get(Person, person_id)
    if not person or person.family_id != current_user.active_family_id:
        return redirect(url_for('main.event_detail', event_id=event_id))
    # Members can only RSVP their own household; admins can RSVP anyone
    if not current_user.active_is_admin:
        household_ids = _get_household_ids(current_user.person)
        if person_id not in household_ids:
            return redirect(url_for('main.event_detail', event_id=event_id))
    rsvp = EventRSVP.query.filter_by(event_id=event_id, person_id=person_id).first()
    if status == 'clear':
        if rsvp:
            db.session.delete(rsvp)
            db.session.commit()
    elif rsvp:
        rsvp.status = status
        db.session.commit()
    else:
        db.session.add(EventRSVP(event_id=event_id, person_id=person_id, status=status))
        db.session.commit()
    return redirect(url_for('main.event_detail', event_id=event_id))


def _get_household_ids(person):
    """Return set of person IDs that a user can RSVP for (self + spouse + unmarried children)."""
    if not person:
        return set()
    ids = {person.id}
    spouse = person.get_active_spouse()
    if spouse:
        ids.add(spouse.id)
    for rel in person.child_rels:
        child = rel.child
        if child and _in_parent_household(child):
            ids.add(child.id)
    if spouse:
        for rel in spouse.child_rels:
            child = rel.child
            if child and _in_parent_household(child):
                ids.add(child.id)
    return ids


def _in_parent_household(person):
    """True if person has no active spouse — unmarried children belong to their parent's household."""
    return person.get_active_spouse() is None


def _build_family_groups(all_people, rsvp_map):
    """
    Full family list for admin/contributor RSVP view — every directory household,
    whether or not they've responded. Each group has adults + expandable children.
    """
    dir_people = [p for p in all_people if p.in_directory]

    # Map child_id -> parent people (so we can skip children when building heads)
    child_parent_map = {}
    for p in dir_people:
        for rel in p.child_rels:
            child = rel.child
            if child and child.in_directory and _in_parent_household(child):
                child_parent_map.setdefault(child.id, []).append(p)

    seen = set()
    groups = []

    for p in sorted(dir_people, key=lambda x: x.get_display_name()):
        if p.id in seen:
            continue
        # Skip unmarried children of directory members — they appear under parents
        if p.id in child_parent_map:
            seen.add(p.id)
            continue

        seen.add(p.id)
        adults = [(p, rsvp_map.get(p.id))]

        spouse = p.get_active_spouse()
        if spouse and spouse.in_directory and spouse.id not in seen:
            adults.append((spouse, rsvp_map.get(spouse.id)))
            seen.add(spouse.id)

        # Collect unmarried children from both partners
        child_ids = set()
        for rel in p.child_rels:
            if rel.child and rel.child.in_directory and _in_parent_household(rel.child):
                child_ids.add(rel.child_id)
        if spouse:
            for rel in spouse.child_rels:
                if rel.child and rel.child.in_directory and _in_parent_household(rel.child):
                    child_ids.add(rel.child_id)

        children = sorted(
            [(pp, rsvp_map.get(pp.id)) for pp in dir_people if pp.id in child_ids],
            key=lambda x: x[0].get_display_name()
        )
        for child, _ in children:
            seen.add(child.id)

        label = p.get_couple_name() if (spouse and spouse.in_directory) else p.get_display_name()
        groups.append(dict(label=label, adults=adults, children=children))

    return groups


def _build_rsvp_groups(event, all_people):
    """
    Group RSVPs by household unit for the summary display.
    Returns list of dicts: {label, yes: [names], maybe: [names], no: [names], total}
    One entry per household (couple unit or lone adult), ordered by most-going first.
    """
    people_map = {p.id: p for p in all_people}
    rsvp_map = {r.person_id: r.status for r in event.rsvps}
    seen = set()
    groups = []

    # Process in alphabetical order so output is stable
    responded = sorted(
        [r for r in event.rsvps],
        key=lambda r: r.person.get_display_name()
    )

    for rsvp in responded:
        person = rsvp.person
        if person.id in seen or not person.in_directory:
            continue

        # Gather the full household IDs
        hh_ids = _get_household_ids(person)
        # Also include household of spouse to avoid duplicates
        spouse = person.get_active_spouse()
        if spouse:
            hh_ids |= _get_household_ids(spouse)

        # Mark all as seen
        seen |= hh_ids

        # Collect responses within this household
        yes_names, maybe_names, no_names = [], [], []
        for pid in hh_ids:
            p = people_map.get(pid)
            if not p or not p.in_directory:
                continue
            s = rsvp_map.get(pid)
            if s == 'yes':
                yes_names.append(p.get_display_name())
            elif s == 'maybe':
                maybe_names.append(p.get_display_name())
            elif s == 'no':
                no_names.append(p.get_display_name())

        if not (yes_names or maybe_names or no_names):
            continue

        # Label: couple name or single name
        if spouse and spouse.in_directory:
            label = person.get_couple_name()
        else:
            label = person.get_display_name()

        groups.append(dict(label=label, yes=sorted(yes_names),
                           maybe=sorted(maybe_names), no=sorted(no_names)))

    # Sort: groups with most "yes" first
    groups.sort(key=lambda g: (-len(g['yes']), -len(g['maybe']), g['label']))
    return groups


# ── Meals ─────────────────────────────────────────────────────────────────────

@main.route('/events/<int:event_id>/meals/add', methods=['POST'])
@login_required
@admin_required
def event_meal_add(event_id):
    event = db.session.get(Event, event_id)
    if not event or event.family_id != current_user.active_family_id:
        flash('Event not found.', 'error')
        return redirect(url_for('main.events_list'))
    form = EventMealForm()
    if form.validate_on_submit():
        meal = EventMeal(
            event_id=event_id,
            name=form.name.data,
            meal_date=form.meal_date.data,
            meal_time=form.meal_time.data or None,
            notes=form.notes.data or None,
        )
        db.session.add(meal)
        db.session.commit()
        flash(f'{meal.name} added.', 'info')
    return redirect(url_for('main.event_detail', event_id=event_id))


@main.route('/events/<int:event_id>/meals/<int:meal_id>/delete', methods=['POST'])
@login_required
@admin_required
def event_meal_delete(event_id, meal_id):
    meal = db.session.get(EventMeal, meal_id)
    if not meal or meal.event.family_id != current_user.active_family_id:
        flash('Meal not found.', 'error')
        return redirect(url_for('main.event_detail', event_id=event_id))
    db.session.delete(meal)
    db.session.commit()
    flash('Meal removed.', 'info')
    return redirect(url_for('main.event_detail', event_id=event_id))


@main.route('/events/<int:event_id>/meals/<int:meal_id>/assign-family', methods=['POST'])
@login_required
@admin_required
def event_meal_assign_family(event_id, meal_id):
    meal = db.session.get(EventMeal, meal_id)
    if not meal or meal.event.family_id != current_user.active_family_id:
        flash('Meal not found.', 'error')
        return redirect(url_for('main.event_detail', event_id=event_id))
    all_people = Person.query.filter_by(family_id=current_user.active_family_id).order_by(Person.name).all()
    couple_people = [p for p in all_people if p.in_directory and (not p.get_active_spouse() or p.id < p.get_active_spouse().id)]
    form = EventMealFamilyAssignForm(prefix=f'meal_fam_{meal_id}')
    form.assigned_family_id.choices = [(0, '— None —')] + [(p.id, p.get_couple_name()) for p in couple_people]
    if form.validate_on_submit():
        pid = form.assigned_family_id.data
        meal.assigned_family_id = pid if pid else None
        db.session.commit()
    return redirect(url_for('main.event_detail', event_id=event_id))


@main.route('/events/<int:event_id>/meals/<int:meal_id>/unassign-family', methods=['POST'])
@login_required
@admin_required
def event_meal_unassign_family(event_id, meal_id):
    meal = db.session.get(EventMeal, meal_id)
    if not meal or meal.event.family_id != current_user.active_family_id:
        flash('Meal not found.', 'error')
        return redirect(url_for('main.event_detail', event_id=event_id))
    meal.assigned_family_id = None
    db.session.commit()
    return redirect(url_for('main.event_detail', event_id=event_id))


@main.route('/events/<int:event_id>/meals/<int:meal_id>/items/add', methods=['POST'])
@login_required
@admin_required
def event_meal_item_add(event_id, meal_id):
    meal = db.session.get(EventMeal, meal_id)
    if not meal or meal.event.family_id != current_user.active_family_id:
        flash('Meal not found.', 'error')
        return redirect(url_for('main.event_detail', event_id=event_id))
    form = EventMealItemForm()
    if form.validate_on_submit():
        item = EventMealItem(
            meal_id=meal_id,
            label=form.label.data,
            quantity=form.quantity.data or None,
            is_cleanup=form.is_cleanup.data,
        )
        db.session.add(item)
        db.session.commit()
    return redirect(url_for('main.event_detail', event_id=event_id))


@main.route('/events/<int:event_id>/meals/<int:meal_id>/self-signup', methods=['POST'])
@login_required
def event_meal_self_signup(event_id, meal_id):
    meal = db.session.get(EventMeal, meal_id)
    if not meal or meal.event.family_id != current_user.active_family_id:
        return redirect(url_for('main.event_detail', event_id=event_id))
    form = EventMealSelfSignupForm()
    if form.validate_on_submit() and current_user.person:
        item = EventMealItem(
            meal_id=meal_id,
            label=form.label.data,
            is_cleanup=False,
            assigned_to_id=current_user.person.id,
        )
        db.session.add(item)
        db.session.commit()
    return redirect(url_for('main.event_detail', event_id=event_id))


@main.route('/events/<int:event_id>/meals/<int:meal_id>/items/<int:item_id>/assign', methods=['POST'])
@login_required
def event_meal_item_assign(event_id, meal_id, item_id):
    item = db.session.get(EventMealItem, item_id)
    if not item or item.meal.event.family_id != current_user.active_family_id:
        flash('Item not found.', 'error')
        return redirect(url_for('main.event_detail', event_id=event_id))
    all_people = Person.query.filter_by(family_id=current_user.active_family_id).order_by(Person.name).all()
    form = EventMealAssignForm(prefix=f'item_{item_id}')
    form.person_id.choices = [(0, '— Select —')] + [(p.id, p.get_display_name()) for p in all_people]
    if form.validate_on_submit() and form.person_id.data:
        person = db.session.get(Person, form.person_id.data)
        if person and person.family_id == current_user.active_family_id:
            prev_id = item.assigned_to_id
            item.assigned_to_id = person.id
            db.session.commit()
            if prev_id != person.id and person.user:
                event_url = url_for('main.event_detail', event_id=event_id, _external=True)
                if (current_app.config.get('MAIL_ENABLED')
                        and NotificationPreference.is_enabled(person.user.id, 'assignment')):
                    from .email import send_meal_item_assignment_email
                    send_meal_item_assignment_email(person.user, item, item.meal.event, event_url)
                from .notifications import create_notification
                create_notification(person.user, 'assignment',
                                    title=f'Meal assignment: {item.label}',
                                    body=f'For {item.meal.name} at {item.meal.event.name}',
                                    url=event_url)
    return redirect(url_for('main.event_detail', event_id=event_id))


@main.route('/events/<int:event_id>/meals/<int:meal_id>/items/<int:item_id>/unassign', methods=['POST'])
@login_required
def event_meal_item_unassign(event_id, meal_id, item_id):
    item = db.session.get(EventMealItem, item_id)
    if not item or item.meal.event.family_id != current_user.active_family_id:
        flash('Item not found.', 'error')
        return redirect(url_for('main.event_detail', event_id=event_id))
    if not current_user.active_is_admin:
        flash('Only admins can unassign items.', 'error')
        return redirect(url_for('main.event_detail', event_id=event_id))
    item.assigned_to_id = None
    db.session.commit()
    return redirect(url_for('main.event_detail', event_id=event_id))


@main.route('/events/<int:event_id>/meals/<int:meal_id>/items/<int:item_id>/delete', methods=['POST'])
@login_required
@admin_required
def event_meal_item_delete(event_id, meal_id, item_id):
    item = db.session.get(EventMealItem, item_id)
    if not item or item.meal.event.family_id != current_user.active_family_id:
        flash('Item not found.', 'error')
        return redirect(url_for('main.event_detail', event_id=event_id))
    db.session.delete(item)
    db.session.commit()
    return redirect(url_for('main.event_detail', event_id=event_id))


# ── Assignments ───────────────────────────────────────────────────────────────

@main.route('/events/<int:event_id>/assignments/add', methods=['POST'])
@login_required
@admin_required
def event_assignment_add(event_id):
    event = db.session.get(Event, event_id)
    if not event or event.family_id != current_user.active_family_id:
        flash('Event not found.', 'error')
        return redirect(url_for('main.events_list'))
    form = EventAssignmentForm()
    if form.validate_on_submit():
        a = EventAssignment(
            event_id=event_id,
            title=form.title.data,
            description=form.description.data or None,
            category=form.category.data or None,
            due_date=form.due_date.data or None,
        )
        db.session.add(a)
        db.session.commit()
        flash(f'Task "{a.title}" added.', 'info')
    return redirect(url_for('main.event_detail', event_id=event_id))


@main.route('/events/<int:event_id>/assignments/bulk-add', methods=['POST'])
@login_required
@admin_required
def event_assignment_bulk_add(event_id):
    event = db.session.get(Event, event_id)
    if not event or event.family_id != current_user.active_family_id:
        flash('Event not found.', 'error')
        return redirect(url_for('main.events_list'))
    raw = request.form.get('bulk_tasks', '')
    added = 0
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        cat = None
        if ' #' in line:
            parts = line.rsplit(' #', 1)
            candidate = parts[1].strip().title()
            if candidate in ASSIGNMENT_CATEGORIES:
                cat = candidate
                line = parts[0].strip()
        if line:
            db.session.add(EventAssignment(event_id=event_id, title=line[:150], category=cat))
            added += 1
    if added:
        db.session.commit()
        flash(f'{added} task{"s" if added != 1 else ""} added.', 'info')
    return redirect(url_for('main.event_detail', event_id=event_id))


@main.route('/events/<int:event_id>/assignments/<int:aid>/claim', methods=['POST'])
@login_required
def event_assignment_claim(event_id, aid):
    a = db.session.get(EventAssignment, aid)
    if not a or a.event.family_id != current_user.active_family_id:
        flash('Task not found.', 'error')
        return redirect(url_for('main.event_detail', event_id=event_id))
    if not current_user.person:
        flash('You need a family profile to claim tasks.', 'error')
        return redirect(url_for('main.event_detail', event_id=event_id))
    if a.claimed_by_id:
        flash('That task is already claimed.', 'error')
        return redirect(url_for('main.event_detail', event_id=event_id))
    a.claimed_by_id = current_user.person.id
    db.session.commit()
    flash(f'You claimed "{a.title}".', 'info')
    return redirect(url_for('main.event_detail', event_id=event_id))


@main.route('/events/<int:event_id>/assignments/<int:aid>/unclaim', methods=['POST'])
@login_required
def event_assignment_unclaim(event_id, aid):
    a = db.session.get(EventAssignment, aid)
    if not a or a.event.family_id != current_user.active_family_id:
        flash('Task not found.', 'error')
        return redirect(url_for('main.event_detail', event_id=event_id))
    is_own = current_user.person and a.claimed_by_id == current_user.person.id
    if not is_own and not current_user.active_is_admin:
        flash('You can only unclaim your own tasks.', 'error')
        return redirect(url_for('main.event_detail', event_id=event_id))
    a.claimed_by_id = None
    a.is_done = False
    db.session.commit()
    flash(f'"{a.title}" is now unclaimed.', 'info')
    return redirect(url_for('main.event_detail', event_id=event_id))


@main.route('/events/<int:event_id>/assignments/<int:aid>/done', methods=['POST'])
@login_required
def event_assignment_done(event_id, aid):
    a = db.session.get(EventAssignment, aid)
    if not a or a.event.family_id != current_user.active_family_id:
        flash('Task not found.', 'error')
        return redirect(url_for('main.event_detail', event_id=event_id))
    is_own = current_user.person and a.claimed_by_id == current_user.person.id
    if not is_own and not current_user.active_is_admin:
        flash('Only the person assigned can mark this done.', 'error')
        return redirect(url_for('main.event_detail', event_id=event_id))
    a.is_done = not a.is_done
    db.session.commit()
    return redirect(url_for('main.event_detail', event_id=event_id))


@main.route('/events/<int:event_id>/assignments/<int:aid>/delete', methods=['POST'])
@login_required
@admin_required
def event_assignment_delete(event_id, aid):
    a = db.session.get(EventAssignment, aid)
    if not a or a.event.family_id != current_user.active_family_id:
        flash('Task not found.', 'error')
        return redirect(url_for('main.event_detail', event_id=event_id))
    db.session.delete(a)
    db.session.commit()
    flash('Task removed.', 'info')
    return redirect(url_for('main.event_detail', event_id=event_id))


@main.route('/events/<int:event_id>/assignments/<int:aid>/assign', methods=['POST'])
@login_required
@admin_required
def event_assignment_admin_assign(event_id, aid):
    a = db.session.get(EventAssignment, aid)
    if not a or a.event.family_id != current_user.active_family_id:
        return redirect(url_for('main.event_detail', event_id=event_id))
    all_people = Person.query.filter_by(family_id=current_user.active_family_id).order_by(Person.name).all()
    form = EventAssignmentAdminAssignForm(prefix=f'a_{aid}')
    form.person_id.choices = [(0, '— Select —')] + [(p.id, p.get_display_name()) for p in all_people]
    if form.validate_on_submit():
        pid = form.person_id.data
        prev_id = a.claimed_by_id
        a.claimed_by_id = pid if pid else None
        a.is_done = False
        db.session.commit()
        if pid and prev_id != pid:
            person = db.session.get(Person, pid)
            if person and person.user:
                event_url = url_for('main.event_detail', event_id=event_id, _external=True)
                if (current_app.config.get('MAIL_ENABLED')
                        and NotificationPreference.is_enabled(person.user.id, 'assignment')):
                    from .email import send_assignment_notification_email
                    send_assignment_notification_email(person.user, a, a.event, event_url)
                from .notifications import create_notification
                create_notification(person.user, 'assignment',
                                    title=f'Task assigned: {a.title}',
                                    body=f'For {a.event.name}',
                                    url=event_url)
    return redirect(url_for('main.event_detail', event_id=event_id))


@main.route('/events/<int:event_id>/assignments/<int:aid>/tasks/add', methods=['POST'])
@login_required
def event_assignment_task_add(event_id, aid):
    a = db.session.get(EventAssignment, aid)
    if not a or a.event.family_id != current_user.active_family_id:
        return redirect(url_for('main.event_detail', event_id=event_id))
    my_person = Person.query.filter_by(family_id=current_user.active_family_id,
                                       user_id=current_user.id).first()
    if not (current_user.active_is_admin or (my_person and a.claimed_by_id == my_person.id)):
        return redirect(url_for('main.event_detail', event_id=event_id))
    label = request.form.get('label', '').strip()
    if label:
        db.session.add(AssignmentTask(assignment_id=aid, label=label))
        db.session.commit()
    return redirect(url_for('main.event_detail', event_id=event_id))


@main.route('/events/<int:event_id>/assignments/<int:aid>/tasks/<int:tid>/toggle', methods=['POST'])
@login_required
def event_assignment_task_toggle(event_id, aid, tid):
    a = db.session.get(EventAssignment, aid)
    if not a or a.event.family_id != current_user.active_family_id:
        return redirect(url_for('main.event_detail', event_id=event_id))
    my_person = Person.query.filter_by(family_id=current_user.active_family_id,
                                       user_id=current_user.id).first()
    if not (current_user.active_is_admin or (my_person and a.claimed_by_id == my_person.id)):
        return redirect(url_for('main.event_detail', event_id=event_id))
    task = db.session.get(AssignmentTask, tid)
    if task and task.assignment_id == aid:
        task.is_done = not task.is_done
        db.session.commit()
    return redirect(url_for('main.event_detail', event_id=event_id))


@main.route('/events/<int:event_id>/assignments/<int:aid>/tasks/<int:tid>/delete', methods=['POST'])
@login_required
def event_assignment_task_delete(event_id, aid, tid):
    a = db.session.get(EventAssignment, aid)
    if not a or a.event.family_id != current_user.active_family_id:
        return redirect(url_for('main.event_detail', event_id=event_id))
    my_person = Person.query.filter_by(family_id=current_user.active_family_id,
                                       user_id=current_user.id).first()
    if not (current_user.active_is_admin or (my_person and a.claimed_by_id == my_person.id)):
        return redirect(url_for('main.event_detail', event_id=event_id))
    task = db.session.get(AssignmentTask, tid)
    if task and task.assignment_id == aid:
        db.session.delete(task)
        db.session.commit()
    return redirect(url_for('main.event_detail', event_id=event_id))


# ── Sleeping ──────────────────────────────────────────────────────────────────

def _sleeping_remove_from_event(event, person):
    """Remove person from every sleeping spot in this event."""
    for spot in event.sleeping_spots:
        if person in spot.people:
            spot.people.remove(person)


@main.route('/events/<int:event_id>/sleeping/add-spot', methods=['POST'])
@login_required
@admin_required
def event_sleeping_add_spot(event_id):
    event = db.session.get(Event, event_id)
    if not event or event.family_id != current_user.active_family_id:
        flash('Event not found.', 'error')
        return redirect(url_for('main.events_list'))
    form = EventSleepingSpotForm()
    if form.validate_on_submit():
        spot_type = form.spot_type.data or None
        if spot_type and spot_type not in SPOT_TYPES:
            spot_type = None
        spot = EventSleepingSpot(
            event_id=event_id,
            name=form.name.data,
            spot_type=spot_type,
            capacity=form.capacity.data,
            notes=form.notes.data or None,
        )
        db.session.add(spot)
        db.session.commit()
        flash(f'"{spot.name}" added.', 'info')
    return redirect(url_for('main.event_detail', event_id=event_id))


@main.route('/events/<int:event_id>/sleeping/bulk-add', methods=['POST'])
@login_required
@admin_required
def event_sleeping_bulk_add(event_id):
    """Parse a textarea of room names (one per line, optional capacity) and create spots."""
    event = db.session.get(Event, event_id)
    if not event or event.family_id != current_user.active_family_id:
        flash('Event not found.', 'error')
        return redirect(url_for('main.events_list'))
    raw = request.form.get('bulk_rooms', '')
    added = 0
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        # Try to parse trailing number as capacity: "Master bedroom 2" or "Bunk room (4)"
        import re as _re
        m = _re.match(r'^(.+?)\s*[\(\[]?(\d+)[\)\]]?\s*$', line)
        if m:
            name, cap = m.group(1).strip(), int(m.group(2))
        else:
            name, cap = line, None
        if name:
            db.session.add(EventSleepingSpot(event_id=event_id, name=name, capacity=cap))
            added += 1
    if added:
        db.session.commit()
        flash(f'{added} room{"s" if added != 1 else ""} added.', 'info')
    return redirect(url_for('main.event_detail', event_id=event_id))


@main.route('/events/<int:event_id>/sleeping/<int:sid>/assign', methods=['POST'])
@login_required
@admin_required
def event_sleeping_assign(event_id, sid):
    spot = db.session.get(EventSleepingSpot, sid)
    if not spot or spot.event.family_id != current_user.active_family_id:
        flash('Spot not found.', 'error')
        return redirect(url_for('main.event_detail', event_id=event_id))
    form = EventSleepingAssignForm(prefix=f'spot_{sid}')
    eligible = Person.query.filter_by(family_id=current_user.active_family_id).order_by(Person.name).all()
    spot_assigned_ids = {p.id for p in spot.people}
    form.person_id.choices = [(0, '— Select —')] + [(p.id, p.get_display_name()) for p in eligible if p.id not in spot_assigned_ids]
    if form.validate_on_submit() and form.person_id.data:
        person = db.session.get(Person, form.person_id.data)
        if person and person.family_id == current_user.active_family_id:
            if spot.capacity and len(spot.people) >= spot.capacity and person not in spot.people:
                flash(f'"{spot.name}" is at capacity ({spot.capacity}).', 'error')
            elif person not in spot.people:
                _sleeping_remove_from_event(spot.event, person)
                spot.people.append(person)
                db.session.commit()
                flash(f'{person.get_display_name()} assigned to {spot.name}.', 'info')
    return redirect(url_for('main.event_detail', event_id=event_id))


@main.route('/events/<int:event_id>/sleeping/<int:sid>/unassign/<int:pid>', methods=['POST'])
@login_required
@admin_required
def event_sleeping_unassign(event_id, sid, pid):
    spot = db.session.get(EventSleepingSpot, sid)
    if not spot or spot.event.family_id != current_user.active_family_id:
        flash('Spot not found.', 'error')
        return redirect(url_for('main.event_detail', event_id=event_id))
    person = db.session.get(Person, pid)
    if person and person.family_id == current_user.active_family_id and person in spot.people:
        spot.people.remove(person)
        db.session.commit()
        flash(f'{person.get_display_name()} removed from {spot.name}.', 'info')
    return redirect(url_for('main.event_detail', event_id=event_id))


@main.route('/events/<int:event_id>/sleeping/<int:sid>/delete', methods=['POST'])
@login_required
@admin_required
def event_sleeping_delete_spot(event_id, sid):
    spot = db.session.get(EventSleepingSpot, sid)
    if not spot or spot.event.family_id != current_user.active_family_id:
        flash('Spot not found.', 'error')
        return redirect(url_for('main.event_detail', event_id=event_id))
    db.session.delete(spot)
    db.session.commit()
    flash(f'"{spot.name}" removed.', 'info')
    return redirect(url_for('main.event_detail', event_id=event_id))


@main.route('/events/<int:event_id>/sleeping/<int:sid>/assign-household', methods=['POST'])
@login_required
@admin_required
def event_sleeping_assign_household(event_id, sid):
    spot = db.session.get(EventSleepingSpot, sid)
    if not spot or spot.event.family_id != current_user.active_family_id:
        flash('Spot not found.', 'error')
        return redirect(url_for('main.event_detail', event_id=event_id))
    try:
        pid = int(request.form.get('person_id', 0))
    except (ValueError, TypeError):
        pid = 0
    person = pid and db.session.get(Person, pid)
    if not person or person.family_id != current_user.active_family_id:
        return redirect(url_for('main.event_detail', event_id=event_id))
    people_to_add = [person]
    spouse = person.get_active_spouse()
    if spouse and spouse.family_id == current_user.active_family_id:
        people_to_add.append(spouse)
    needed = sum(1 for p in people_to_add if p not in spot.people)
    if spot.capacity and len(spot.people) + needed > spot.capacity:
        flash(f'Not enough space in "{spot.name}" for the whole household.', 'error')
        return redirect(url_for('main.event_detail', event_id=event_id))
    added = []
    for p in people_to_add:
        if p not in spot.people:
            _sleeping_remove_from_event(spot.event, p)
            spot.people.append(p)
            added.append(p.get_display_name())
    if added:
        db.session.commit()
        flash(f'{", ".join(added)} assigned to {spot.name}.', 'info')
    return redirect(url_for('main.event_detail', event_id=event_id))


@main.route('/events/<int:event_id>/sleeping/<int:sid>/self-assign', methods=['POST'])
@login_required
def event_sleeping_self_assign(event_id, sid):
    spot = db.session.get(EventSleepingSpot, sid)
    if not spot or spot.event.family_id != current_user.active_family_id:
        flash('Spot not found.', 'error')
        return redirect(url_for('main.event_detail', event_id=event_id))
    person = current_user.person
    if not person:
        return redirect(url_for('main.event_detail', event_id=event_id))
    if spot.capacity and len(spot.people) >= spot.capacity and person not in spot.people:
        flash(f'"{spot.name}" is full.', 'error')
        return redirect(url_for('main.event_detail', event_id=event_id))
    if person not in spot.people:
        _sleeping_remove_from_event(spot.event, person)
        spot.people.append(person)
        db.session.commit()
        flash(f'You\'ve been placed in {spot.name}.', 'info')
    return redirect(url_for('main.event_detail', event_id=event_id))


@main.route('/events/<int:event_id>/sleeping/<int:sid>/self-unassign', methods=['POST'])
@login_required
def event_sleeping_self_unassign(event_id, sid):
    spot = db.session.get(EventSleepingSpot, sid)
    if not spot or spot.event.family_id != current_user.active_family_id:
        flash('Spot not found.', 'error')
        return redirect(url_for('main.event_detail', event_id=event_id))
    person = current_user.person
    if person and person in spot.people:
        spot.people.remove(person)
        db.session.commit()
        flash(f'You\'ve been removed from {spot.name}.', 'info')
    return redirect(url_for('main.event_detail', event_id=event_id))


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
        from .models import Notification
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


# ── Chat ──────────────────────────────────────────────────────────────────────

def _notify_chat_members(msg):
    """Send in-app notifications to family members not currently viewing chat."""
    from .notifications import create_notification
    from datetime import timedelta
    cutoff = datetime.utcnow() - timedelta(seconds=10)
    recipients = User.query.filter(
        User.family_id == msg.family_id,
        User.id != msg.author_id,
        db.or_(User.chat_last_seen_at == None, User.chat_last_seen_at < cutoff),
    ).all()
    author_name = msg.author.get_full_name()
    for recipient in recipients:
        create_notification(
            recipient,
            'chat_message',
            title=f'New message from {author_name}',
            body=msg.body[:120],
            url='/chat',
        )


@main.route('/chat')
@login_required
def chat():
    family = current_user.active_family
    if not family or not family.enable_chat:
        abort(404)
    if not family_has_paid_access(family):
        return render_template('chat.html', upgrade_required=True)
    messages = (
        ChatMessage.query
        .filter_by(family_id=current_user.active_family_id)
        .order_by(ChatMessage.created_at.desc())
        .limit(50)
        .all()
    )
    messages = list(reversed(messages))
    current_user.chat_last_seen_at = datetime.utcnow()
    db.session.commit()
    form = ChatMessageForm()
    return render_template('chat.html', messages=messages, form=form, upgrade_required=False)


@main.route('/chat/send', methods=['POST'])
@login_required
@requires_plan
def chat_send():
    form = ChatMessageForm()
    if form.validate_on_submit():
        msg = ChatMessage(
            family_id=current_user.active_family_id,
            author_id=current_user.id,
            body=form.body.data.strip(),
        )
        db.session.add(msg)
        db.session.commit()
        _notify_chat_members(msg)
    return redirect(url_for('main.chat'))


@main.route('/chat/poll')
@login_required
@requires_plan
def chat_poll():
    after_id = request.args.get('after', 0, type=int)
    msgs = (
        ChatMessage.query
        .filter_by(family_id=current_user.active_family_id)
        .filter(ChatMessage.id > after_id)
        .order_by(ChatMessage.created_at.asc())
        .limit(100)
        .all()
    )
    current_user.chat_last_seen_at = datetime.utcnow()
    db.session.commit()
    return jsonify({
        'messages': [
            {
                'id': m.id,
                'body': m.body,
                'author_name': m.author.get_full_name(),
                'author_id': m.author_id,
                'created_at': m.created_at.isoformat(),
                'edited_at': m.edited_at.isoformat() if m.edited_at else None,
                'can_edit': m.can_edit(current_user),
                'edit_url': url_for('main.chat_edit', msg_id=m.id),
                'can_delete': m.can_delete(current_user),
                'delete_url': url_for('main.chat_delete', msg_id=m.id),
            }
            for m in msgs
        ],
        'current_user_id': current_user.id,
    })


@main.route('/chat/<int:msg_id>/edit', methods=['POST'])
@login_required
@requires_plan
def chat_edit(msg_id):
    msg = ChatMessage.query.filter_by(id=msg_id, family_id=current_user.active_family_id).first_or_404()
    if not msg.can_edit(current_user):
        return jsonify({'error': 'Edit window has closed.'}), 403
    body = (request.form.get('body') or '').strip()
    if not body:
        return jsonify({'error': 'Message cannot be empty.'}), 400
    msg.body = body
    msg.edited_at = datetime.utcnow()
    db.session.commit()
    return jsonify({'ok': True, 'body': msg.body, 'edited_at': msg.edited_at.isoformat()})


@main.route('/chat/<int:msg_id>/delete', methods=['POST'])
@login_required
@requires_plan
def chat_delete(msg_id):
    msg = ChatMessage.query.filter_by(id=msg_id, family_id=current_user.active_family_id).first_or_404()
    if not msg.can_delete(current_user):
        abort(403)
    db.session.delete(msg)
    db.session.commit()
    return redirect(url_for('main.chat'))


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
