from . import db, login_manager
from flask_login import UserMixin
from flask import session, has_request_context
from datetime import date, datetime
from werkzeug.security import generate_password_hash, check_password_hash

PARENT_ROLES = [
    ('father',          'Father'),
    ('mother',          'Mother'),
    ('stepfather',      'Step-Father'),
    ('stepmother',      'Step-Mother'),
    ('adoptive_father', 'Adoptive Father'),
    ('adoptive_mother', 'Adoptive Mother'),
    ('guardian',        'Guardian'),
    ('parent',          'Parent'),
]
PARENT_ROLE_LABELS = dict(PARENT_ROLES)

class ParentRelationship(db.Model):
    __tablename__ = 'parent_relationships'
    parent_id = db.Column(db.Integer, db.ForeignKey('people.id'), primary_key=True)
    child_id  = db.Column(db.Integer, db.ForeignKey('people.id'), primary_key=True)
    role      = db.Column(db.String(30), default='parent')

    parent = db.relationship('Person', foreign_keys=[parent_id])
    child  = db.relationship('Person', foreign_keys=[child_id])

    @property
    def role_display(self):
        return PARENT_ROLE_LABELS.get(self.role, self.role.replace('_', ' ').title())

class Family(db.Model):
    __tablename__ = 'families'

    id = db.Column(db.Integer, primary_key=True)
    account_id = db.Column(db.String(12), unique=True, nullable=True, index=True)
    name = db.Column(db.String(100), nullable=False)
    plan = db.Column(db.String(20), nullable=False, default='free')
    trial_ends_at = db.Column(db.DateTime, nullable=True)
    stripe_customer_id = db.Column(db.String(100), nullable=True)
    stripe_subscription_id = db.Column(db.String(100), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Email sequence tracking — prevents duplicate sends
    email_nudge3_sent = db.Column(db.Boolean, nullable=False, server_default='false')
    email_nudge7_sent = db.Column(db.Boolean, nullable=False, server_default='false')
    email_trial_warning_sent = db.Column(db.Boolean, nullable=False, server_default='false')
    email_trial_ended_sent = db.Column(db.Boolean, nullable=False, server_default='false')

    has_lgbtq_options = db.Column(db.Boolean, default=False, nullable=False)
    require_member_approval = db.Column(db.Boolean, default=False, nullable=False, server_default='false')
    enable_polls = db.Column(db.Boolean, default=True, nullable=False, server_default='true')
    enable_greeting_cards = db.Column(db.Boolean, default=True, nullable=False, server_default='true')
    enable_chat = db.Column(db.Boolean, default=True, nullable=False, server_default='true')
    enable_stories = db.Column(db.Boolean, default=True, nullable=False, server_default='true')

    people = db.relationship('Person', back_populates='family', foreign_keys='Person.family_id')
    users = db.relationship('User', back_populates='family', foreign_keys='User.family_id')
    pod_members = db.relationship('UserPodMembership', back_populates='family', cascade='all, delete-orphan')


class SpouseRelationship(db.Model):
    __tablename__ = 'spouse_relationships'

    id = db.Column(db.Integer, primary_key=True)
    person1_id = db.Column(db.Integer, db.ForeignKey('people.id'), nullable=False)
    person2_id = db.Column(db.Integer, db.ForeignKey('people.id'), nullable=False)
    status = db.Column(db.String(20), default='active')  # active, deceased, divorced, separated, annulled
    role_for_person1 = db.Column(db.String(20), default='spouse')  # husband, wife, spouse, partner
    role_for_person2 = db.Column(db.String(20), default='spouse')
    marriage_date = db.Column(db.Date)
    end_date = db.Column(db.Date)
    confirmed = db.Column(db.Boolean, default=False)
    confirmation_token = db.Column(db.String(100))
    confirmation_token_expiry = db.Column(db.DateTime)

    person1 = db.relationship('Person', foreign_keys=[person1_id], backref='spouse_relationships_as_p1')
    person2 = db.relationship('Person', foreign_keys=[person2_id], backref='spouse_relationships_as_p2')

    def get_spouse_of(self, person):
        if self.person1_id == person.id:
            return self.person2
        return self.person1

    def get_role_for(self, person):
        return self.role_for_person1 if self.person1_id == person.id else self.role_for_person2

    def set_role_for(self, person, role):
        if self.person1_id == person.id:
            self.role_for_person1 = role
        else:
            self.role_for_person2 = role

class Person(db.Model):
    __tablename__ = 'people'

    id = db.Column(db.Integer, primary_key=True)
    family_id = db.Column(db.Integer, db.ForeignKey('families.id'), nullable=False, index=True)
    name = db.Column(db.String(100), nullable=False)
    gender = db.Column(db.String(10))
    birthday = db.Column(db.Date)
    nickname = db.Column(db.String(50))
    birthplace = db.Column(db.String(100))
    maiden_name = db.Column(db.String(100))
    deathday = db.Column(db.Date)
    deathplace = db.Column(db.String(100))
    occupation = db.Column(db.String(100))
    email = db.Column(db.String(120))
    phone = db.Column(db.String(20))
    address = db.Column(db.String(200))
    photo_path = db.Column(db.String(200))
    photo_position = db.Column(db.String(20), default='50% 30%')
    notes = db.Column(db.Text)
    pronouns = db.Column(db.String(50))
    in_directory = db.Column(db.Boolean, default=True, nullable=False)
    # Family Stories opt-in (per-person, since account-less elders participate too)
    stories_enabled = db.Column(db.Boolean, default=False, nullable=False, server_default='false')
    story_last_prompted_at = db.Column(db.DateTime, nullable=True)

    family = db.relationship('Family', back_populates='people', foreign_keys='Person.family_id')

    # Parent/child via ParentRelationship
    child_rels  = db.relationship('ParentRelationship', foreign_keys='ParentRelationship.parent_id', backref='parent_person', cascade='all, delete-orphan')
    parent_rels = db.relationship('ParentRelationship', foreign_keys='ParentRelationship.child_id',  backref='child_person',  cascade='all, delete-orphan')

    @property
    def children(self):
        return [r.child for r in self.child_rels]

    @property
    def parents(self):
        return [r.parent for r in self.parent_rels]

    def get_active_spouse(self):
        """Return the confirmed active spouse if one exists."""
        for rel in self.spouse_relationships_as_p1 + self.spouse_relationships_as_p2:
            if rel.status == 'active' and rel.confirmed:
                return rel.get_spouse_of(self)
        return None

    def get_active_spouse_relationship(self):
        for rel in self.spouse_relationships_as_p1 + self.spouse_relationships_as_p2:
            if rel.status == 'active' and rel.confirmed:
                return rel
        return None

    def get_pending_spouse_request(self):
        """Return any unconfirmed spouse relationship."""
        for rel in self.spouse_relationships_as_p1 + self.spouse_relationships_as_p2:
            if not rel.confirmed:
                return rel
        return None

    # Link to user account
    user = db.relationship('User', back_populates='person', uselist=False)

    def get_display_name(self):
        if self.nickname:
            last = self.name.split()[-1]
            return f"{self.nickname} {last}"
        return self.name

    def get_couple_name(self):
        """Return 'First & SpouseFirst Last' if active spouse exists, else display name."""
        spouse = self.get_active_spouse()
        my_first = self.nickname if self.nickname else self.name.split()[0]
        last = self.name.split()[-1]
        if spouse:
            sp_first = spouse.nickname if spouse.nickname else spouse.name.split()[0]
            return f"{my_first} & {sp_first} {last}"
        return self.get_display_name()

    def get_age(self):
        if not self.birthday:
            return None
        end = self.deathday or date.today()
        age = end.year - self.birthday.year
        if (end.month, end.day) < (self.birthday.month, self.birthday.day):
            age -= 1
        return age


class UserPodMembership(db.Model):
    """One row per (user, family) pair — replaces the single User.family_id FK
    for multi-pod support.  The original User.family_id remains as the
    'home' pod used as a fallback when no session value is present."""
    __tablename__ = 'user_pod_memberships'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    family_id = db.Column(db.Integer, db.ForeignKey('families.id'), nullable=False)
    role = db.Column(db.String(20), nullable=False, default='member')  # 'admin' | 'member' | 'delegate'
    joined_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship('User', back_populates='memberships')
    family = db.relationship('Family', back_populates='pod_members')

    __table_args__ = (db.UniqueConstraint('user_id', 'family_id'),)


class PlatformAuditLog(db.Model):
    __tablename__ = 'platform_audit_logs'

    id = db.Column(db.Integer, primary_key=True)
    actor_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    action = db.Column(db.String(100), nullable=False)
    target_type = db.Column(db.String(50), nullable=True)
    target_id = db.Column(db.Integer, nullable=True)
    detail = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    actor = db.relationship('User', foreign_keys=[actor_id])


class SystemAnnouncement(db.Model):
    __tablename__ = 'system_announcements'

    id = db.Column(db.Integer, primary_key=True)
    body = db.Column(db.Text, nullable=False)
    expires_at = db.Column(db.DateTime, nullable=True)
    is_active = db.Column(db.Boolean, default=True, nullable=False, server_default='1')
    created_by_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    created_by = db.relationship('User', foreign_keys=[created_by_id])

    @classmethod
    def get_current(cls):
        now = datetime.utcnow()
        return cls.query.filter(
            cls.is_active == True,
            db.or_(cls.expires_at == None, cls.expires_at > now)
        ).order_by(cls.created_at.desc()).first()


class SupportNote(db.Model):
    """Internal notes written by platform admins on a pod's support history."""
    __tablename__ = 'support_notes'

    id = db.Column(db.Integer, primary_key=True)
    pod_id = db.Column(db.Integer, db.ForeignKey('families.id'), nullable=False, index=True)
    author_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    body = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    author = db.relationship('User', foreign_keys=[author_id])
    pod = db.relationship('Family', foreign_keys=[pod_id])


class User(UserMixin, db.Model):
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    family_id = db.Column(db.Integer, db.ForeignKey('families.id'), nullable=False, index=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    first_name = db.Column(db.String(50), nullable=False)
    last_name = db.Column(db.String(50), nullable=False)
    phone = db.Column(db.String(20))

    # Email verification
    email_verified = db.Column(db.Boolean, default=False)
    verification_token = db.Column(db.String(100))
    verification_token_expiry = db.Column(db.DateTime)

    # Approval
    status = db.Column(db.String(20), default='pending')  # pending/approved/rejected/invited
    approved_by_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    approved_date = db.Column(db.Date)

    # Roles
    is_admin = db.Column(db.Boolean, default=False)
    is_delegate = db.Column(db.Boolean, default=False)
    is_platform_admin = db.Column(db.Boolean, nullable=False, default=False, server_default='0')

    # Link to person in family tree
    person_id = db.Column(db.Integer, db.ForeignKey('people.id'))
    person = db.relationship('Person', back_populates='user')

    family = db.relationship('Family', back_populates='users')

    # Invitation tracking
    invitation_token = db.Column(db.String(100))
    invitation_token_expiry = db.Column(db.DateTime)
    invited_by_id = db.Column(db.Integer, db.ForeignKey('users.id'))

    # Password reset
    reset_token = db.Column(db.String(100))
    reset_token_expiry = db.Column(db.DateTime)

    # Two-factor authentication
    totp_secret = db.Column(db.String(64), nullable=True)
    totp_enabled = db.Column(db.Boolean, nullable=False, server_default='0')
    chat_last_seen_at = db.Column(db.DateTime, nullable=True)
    home_last_seen_at = db.Column(db.DateTime, nullable=True)
    passkeys = db.relationship('UserCredential', backref='user', cascade='all, delete-orphan')

    # Multi-pod memberships
    memberships = db.relationship('UserPodMembership', back_populates='user',
                                  cascade='all, delete-orphan', lazy='select')

    @property
    def has_2fa(self):
        return self.totp_enabled or bool(self.passkeys)

    @property
    def active_family_id(self):
        """Family the user is currently browsing — reads from session, validated
        against actual memberships, falls back to home family_id.
        Platform admins in support mode bypass the membership check."""
        if has_request_context():
            fid = session.get('active_family_id')
            if fid is not None:
                if session.get('support_mode') and self.is_platform_admin:
                    return fid  # bypass membership check for support mode
                if any(m.family_id == fid for m in self.memberships):
                    return fid
        return self.family_id

    @property
    def active_family(self):
        fid = self.active_family_id
        if fid == self.family_id:
            return self.family
        return db.session.get(Family, fid)

    @property
    def active_is_admin(self):
        if has_request_context() and session.get('support_mode') and self.is_platform_admin:
            return True  # platform admin sees full admin view in support mode
        fid = self.active_family_id
        for m in self.memberships:
            if m.family_id == fid:
                return m.role == 'admin'
        return self.is_admin

    @property
    def active_is_delegate(self):
        if has_request_context() and session.get('support_mode') and self.is_platform_admin:
            return True
        fid = self.active_family_id
        for m in self.memberships:
            if m.family_id == fid:
                return m.role in ('admin', 'delegate')
        return self.is_admin or self.is_delegate

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def get_full_name(self):
        return f"{self.first_name} {self.last_name}"


# Notification event types and their email-channel defaults.
NOTIFICATION_EVENTS = {
    'digest':        {'label': 'Weekly digest',          'default': True,  'in_app': False},
    'new_event':     {'label': 'New event created',      'default': True,  'in_app': True},
    'announcement':  {'label': 'New announcement',       'default': True,  'in_app': True},
    'new_member':    {'label': 'New member joins',        'default': True,  'in_app': True},
    'rsvp_reminder': {'label': 'RSVP reminder',          'default': True,  'in_app': True},
    'assignment':    {'label': 'Task or meal assignment', 'default': True,  'in_app': True},
    'event_comment': {'label': 'New comment on event',   'default': True,  'in_app': True},
    'chat_message':  {'label': 'New chat message',        'default': True,  'in_app': True},
    # Engagement notifications — in-app/bell only (email default False to avoid
    # inbox fatigue). is_enabled() falls back to these defaults, so existing
    # users get them with no pref re-seed.
    'new_poll':      {'label': 'New poll',               'default': True,  'in_app': True},
    'new_card':      {'label': 'New greeting card',      'default': True,  'in_app': True},
    'new_photos':    {'label': 'New photos added',       'default': True,  'in_app': True},
    'story_prompt':  {'label': 'Your weekly story prompt', 'default': True, 'in_app': True},
    'new_story':     {'label': 'A family story was shared', 'default': True, 'in_app': True},
}


class NotificationPreference(db.Model):
    __tablename__ = 'notification_preferences'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    event_type = db.Column(db.String(50), nullable=False)
    channel = db.Column(db.String(20), nullable=False, default='email')
    enabled = db.Column(db.Boolean, nullable=False, default=True)

    user = db.relationship('User', backref=db.backref('notification_prefs', lazy='dynamic'))

    __table_args__ = (db.UniqueConstraint('user_id', 'event_type', 'channel'),)

    @classmethod
    def seed_defaults(cls, user_id):
        """Create default preferences for a new user. Safe to call multiple times."""
        for event_type, meta in NOTIFICATION_EVENTS.items():
            for channel in ('email', 'in_app'):
                if channel == 'in_app' and not meta.get('in_app'):
                    continue
                exists = cls.query.filter_by(
                    user_id=user_id, event_type=event_type, channel=channel
                ).first()
                if not exists:
                    db.session.add(cls(
                        user_id=user_id,
                        event_type=event_type,
                        channel=channel,
                        enabled=meta['default'],
                    ))

    @classmethod
    def is_enabled(cls, user_id, event_type, channel='email'):
        pref = cls.query.filter_by(
            user_id=user_id, event_type=event_type, channel=channel
        ).first()
        if pref is not None:
            return pref.enabled
        meta = NOTIFICATION_EVENTS.get(event_type)
        return meta['default'] if meta else False


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


class Notification(db.Model):
    __tablename__ = 'notifications'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    event_type = db.Column(db.String(50), nullable=False)
    title = db.Column(db.String(200), nullable=False)
    body = db.Column(db.Text, nullable=True)
    url = db.Column(db.String(500), nullable=True)
    read_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    user = db.relationship('User', backref=db.backref('notifications', lazy='dynamic'))

    @property
    def is_read(self):
        return self.read_at is not None


class UserDevice(db.Model):
    """Registered push-notification devices for a user (iOS / Android)."""
    __tablename__ = 'user_devices'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    platform = db.Column(db.String(10), nullable=False)  # 'ios' | 'android'
    token = db.Column(db.String(512), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_seen_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship('User', backref=db.backref('devices', lazy='dynamic'))

    __table_args__ = (db.UniqueConstraint('user_id', 'token', name='uq_user_device_token'),)


class ApiTokenBlocklist(db.Model):
    __tablename__ = 'api_token_blocklist'

    id = db.Column(db.Integer, primary_key=True)
    jti = db.Column(db.String(36), nullable=False, unique=True, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class AppVersion(db.Model):
    __tablename__ = 'app_versions'

    id = db.Column(db.Integer, primary_key=True)
    version = db.Column(db.String(50), nullable=False, unique=True)
    title = db.Column(db.String(200), nullable=False, default='')
    changes = db.Column(db.Text, nullable=False, default='[]')  # JSON array
    released_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    is_current = db.Column(db.Boolean, default=False, nullable=False)

    def changes_list(self):
        import json
        try:
            return json.loads(self.changes or '[]')
        except Exception:
            return []


class SystemConfig(db.Model):
    """Platform-wide key-value configuration, editable at runtime."""
    __tablename__ = 'system_config'

    key = db.Column(db.String(100), primary_key=True)
    value = db.Column(db.String(500), nullable=True)

    @classmethod
    def get(cls, key, default=None):
        row = cls.query.filter_by(key=key).first()
        return row.value if row else default

    @classmethod
    def set(cls, key, value):
        row = cls.query.filter_by(key=key).first()
        if row:
            row.value = value
        else:
            db.session.add(cls(key=key, value=value))
        db.session.commit()


class OAuthAccount(db.Model):
    """Links a third-party OAuth identity (Google, Apple) to a User."""
    __tablename__ = 'oauth_accounts'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    provider = db.Column(db.String(32), nullable=False)       # 'google', 'apple'
    provider_user_id = db.Column(db.String(256), nullable=False)

    __table_args__ = (
        db.UniqueConstraint('provider', 'provider_user_id', name='uq_oauth_provider_uid'),
    )

    user = db.relationship('User', backref=db.backref('oauth_accounts', cascade='all, delete-orphan'))


class UserCredential(db.Model):
    """Stores a WebAuthn passkey (one row per registered device)."""
    __tablename__ = 'user_credentials'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    credential_id = db.Column(db.Text, nullable=False, unique=True)  # base64url
    public_key = db.Column(db.Text, nullable=False)                  # base64
    sign_count = db.Column(db.Integer, nullable=False, default=0)
    device_name = db.Column(db.String(100), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


event_sleeping_assignments = db.Table('event_sleeping_assignments',
    db.Column('spot_id', db.Integer, db.ForeignKey('event_sleeping_spots.id'), primary_key=True),
    db.Column('person_id', db.Integer, db.ForeignKey('people.id'), primary_key=True)
)


class Location(db.Model):
    """Reusable named locations for events (e.g. "Grandma's House")."""
    __tablename__ = 'locations'

    id = db.Column(db.Integer, primary_key=True)
    family_id = db.Column(db.Integer, db.ForeignKey('families.id'), nullable=False, index=True)
    name = db.Column(db.String(150), nullable=False)
    address = db.Column(db.String(200), nullable=True)
    lat = db.Column(db.Float, nullable=True)
    lng = db.Column(db.Float, nullable=True)
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    sleeping_spots = db.relationship('LocationSleepingSpot', backref='location',
                                     cascade='all, delete-orphan', order_by='LocationSleepingSpot.sort_order')


class LocationSleepingSpot(db.Model):
    """Reusable sleeping room template attached to a saved location."""
    __tablename__ = 'location_sleeping_spots'

    id = db.Column(db.Integer, primary_key=True)
    location_id = db.Column(db.Integer, db.ForeignKey('locations.id'), nullable=False, index=True)
    name = db.Column(db.String(150), nullable=False)
    spot_type = db.Column(db.String(50), nullable=True)
    capacity = db.Column(db.Integer, nullable=True)
    sort_order = db.Column(db.Integer, default=0, nullable=False)


class Event(db.Model):
    __tablename__ = 'events'

    id = db.Column(db.Integer, primary_key=True)
    family_id = db.Column(db.Integer, db.ForeignKey('families.id'), nullable=False, index=True)
    name = db.Column(db.String(150), nullable=False)
    description = db.Column(db.Text, nullable=True)
    location = db.Column(db.String(200), nullable=True)
    location_id = db.Column(db.Integer, db.ForeignKey('locations.id'), nullable=True)
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=True)
    kind = db.Column(db.String(50), nullable=True)
    rsvp_deadline = db.Column(db.Date, nullable=True)
    is_annual = db.Column(db.Boolean, default=False, nullable=False, server_default='0')
    recur_freq = db.Column(db.String(20), nullable=True)   # 'weekly', 'monthly', 'yearly'
    recur_until = db.Column(db.Date, nullable=True)
    has_meals = db.Column(db.Boolean, default=False)
    has_assignments = db.Column(db.Boolean, default=False)
    has_sleeping = db.Column(db.Boolean, default=False)
    has_carpool = db.Column(db.Boolean, default=False)
    start_time = db.Column(db.Time, nullable=True)
    end_time = db.Column(db.Time, nullable=True)
    lat = db.Column(db.Float, nullable=True)
    lng = db.Column(db.Float, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    cover_image_path = db.Column(db.String(200), nullable=True)

    saved_location = db.relationship('Location', backref='events', foreign_keys=[location_id])

    meals = db.relationship('EventMeal', backref='event', cascade='all, delete-orphan', order_by='EventMeal.meal_date')
    assignments = db.relationship('EventAssignment', backref='event', cascade='all, delete-orphan')
    sleeping_spots = db.relationship('EventSleepingSpot', backref='event', cascade='all, delete-orphan')
    rsvps = db.relationship('EventRSVP', backref='event', cascade='all, delete-orphan')
    comments = db.relationship('EventComment', backref='event', cascade='all, delete-orphan', order_by='EventComment.created_at')
    carpool_offers = db.relationship('CarpoolOffer', backref='event', cascade='all, delete-orphan')
    survey_responses = db.relationship('EventSurveyResponse', backref='event', cascade='all, delete-orphan')
    payment_config = db.relationship('EventPaymentConfig', uselist=False, back_populates='event', cascade='all, delete-orphan')
    payment_records = db.relationship('EventPaymentRecord', back_populates='event', cascade='all, delete-orphan')

    def next_occurrence(self, after_date):
        """Return the next occurrence date after `after_date`, or None if expired/not recurring."""
        freq = self.recur_freq or ('yearly' if self.is_annual else None)
        if not freq:
            return None
        from dateutil.relativedelta import relativedelta
        from datetime import timedelta as _td
        d = self.start_date
        if freq == 'weekly':
            while d <= after_date:
                d += _td(weeks=1)
        elif freq == 'monthly':
            while d <= after_date:
                d += relativedelta(months=1)
        elif freq == 'yearly':
            while d <= after_date:
                d += relativedelta(years=1)
        else:
            return None
        if self.recur_until and d > self.recur_until:
            return None
        return d

    def date_range_display(self):
        if not self.end_date or self.end_date == self.start_date:
            return self.start_date.strftime('%B %-d, %Y')
        if self.start_date.year == self.end_date.year and self.start_date.month == self.end_date.month:
            return f"{self.start_date.strftime('%B %-d')}–{self.end_date.strftime('%-d, %Y')}"
        return f"{self.start_date.strftime('%B %-d')} – {self.end_date.strftime('%B %-d, %Y')}"


class EventMeal(db.Model):
    __tablename__ = 'event_meals'

    id = db.Column(db.Integer, primary_key=True)
    event_id = db.Column(db.Integer, db.ForeignKey('events.id'), nullable=False)
    name = db.Column(db.String(150), nullable=False)
    meal_date = db.Column(db.Date, nullable=True)
    meal_time = db.Column(db.String(20), nullable=True)
    notes = db.Column(db.Text, nullable=True)

    assigned_family_id = db.Column(db.Integer, db.ForeignKey('people.id'), nullable=True)
    assigned_family = db.relationship('Person', foreign_keys=[assigned_family_id])
    items = db.relationship('EventMealItem', backref='meal', cascade='all, delete-orphan', order_by='EventMealItem.is_cleanup, EventMealItem.id')


class EventMealItem(db.Model):
    __tablename__ = 'event_meal_items'

    id = db.Column(db.Integer, primary_key=True)
    meal_id = db.Column(db.Integer, db.ForeignKey('event_meals.id'), nullable=False)
    label = db.Column(db.String(150), nullable=False)
    quantity = db.Column(db.Integer, nullable=True)
    is_cleanup = db.Column(db.Boolean, default=False)
    assigned_to_id = db.Column(db.Integer, db.ForeignKey('people.id'), nullable=True)

    assigned_to = db.relationship('Person')


ASSIGNMENT_CATEGORIES = ['General', 'Setup', 'Cleanup', 'Food', 'Errands', 'Other']
SPOT_TYPES = ['Bedroom', 'Couch', 'Air mattress', 'Tent', 'Cabin bunk', 'Other']


class EventRSVP(db.Model):
    __tablename__ = 'event_rsvps'

    id = db.Column(db.Integer, primary_key=True)
    event_id = db.Column(db.Integer, db.ForeignKey('events.id'), nullable=False)
    person_id = db.Column(db.Integer, db.ForeignKey('people.id'), nullable=False)
    status = db.Column(db.String(10), nullable=False, default='yes')  # 'yes', 'no', 'maybe'
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    person = db.relationship('Person')
    __table_args__ = (db.UniqueConstraint('event_id', 'person_id'),)

class EventAssignment(db.Model):
    __tablename__ = 'event_assignments'

    id = db.Column(db.Integer, primary_key=True)
    event_id = db.Column(db.Integer, db.ForeignKey('events.id'), nullable=False)
    title = db.Column(db.String(150), nullable=False)
    description = db.Column(db.Text, nullable=True)
    category = db.Column(db.String(50), nullable=True)
    due_date = db.Column(db.Date, nullable=True)
    claimed_by_id = db.Column(db.Integer, db.ForeignKey('people.id'), nullable=True)
    is_done = db.Column(db.Boolean, default=False)

    claimed_by = db.relationship('Person')
    tasks = db.relationship('AssignmentTask', backref='assignment',
                            cascade='all, delete-orphan', order_by='AssignmentTask.id')


class AssignmentTask(db.Model):
    __tablename__ = 'assignment_tasks'

    id = db.Column(db.Integer, primary_key=True)
    assignment_id = db.Column(db.Integer, db.ForeignKey('event_assignments.id'), nullable=False, index=True)
    label = db.Column(db.String(200), nullable=False)
    is_done = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class EventSleepingSpot(db.Model):
    __tablename__ = 'event_sleeping_spots'

    id = db.Column(db.Integer, primary_key=True)
    event_id = db.Column(db.Integer, db.ForeignKey('events.id'), nullable=False)
    name = db.Column(db.String(150), nullable=False)
    spot_type = db.Column(db.String(50), nullable=True)
    capacity = db.Column(db.Integer, nullable=True)
    notes = db.Column(db.Text, nullable=True)

    people = db.relationship('Person', secondary='event_sleeping_assignments')


class EventComment(db.Model):
    __tablename__ = 'event_comments'

    id = db.Column(db.Integer, primary_key=True)
    event_id = db.Column(db.Integer, db.ForeignKey('events.id'), nullable=False)
    person_id = db.Column(db.Integer, db.ForeignKey('people.id'), nullable=False)
    body = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    person = db.relationship('Person')


class ChatMessage(db.Model):
    __tablename__ = 'chat_messages'

    id         = db.Column(db.Integer, primary_key=True)
    family_id  = db.Column(db.Integer, db.ForeignKey('families.id'), nullable=False, index=True)
    author_id  = db.Column(db.Integer, db.ForeignKey('users.id'),    nullable=False, index=True)
    body       = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)
    edited_at  = db.Column(db.DateTime, nullable=True)

    author = db.relationship('User', backref=db.backref('chat_messages', lazy='dynamic'))

    EDIT_WINDOW   = 15 * 60  # seconds
    DELETE_WINDOW =  2 * 60  # seconds; admin has no time limit

    def can_edit(self, user):
        if self.author_id != user.id:
            return False
        return (datetime.utcnow() - self.created_at).total_seconds() < self.EDIT_WINDOW

    def can_delete(self, user):
        if user.active_is_admin:
            return True
        if self.author_id != user.id:
            return False
        return (datetime.utcnow() - self.created_at).total_seconds() < self.DELETE_WINDOW


class Announcement(db.Model):
    __tablename__ = 'announcements'

    id = db.Column(db.Integer, primary_key=True)
    family_id = db.Column(db.Integer, db.ForeignKey('families.id'), nullable=False, index=True)
    author_id = db.Column(db.Integer, db.ForeignKey('people.id'), nullable=True)
    title = db.Column(db.String(150), nullable=False)
    body = db.Column(db.Text, nullable=False)
    pinned = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    author = db.relationship('Person')
    reactions = db.relationship('AnnouncementReaction', backref='announcement',
                                cascade='all, delete-orphan')


class Poll(db.Model):
    __tablename__ = 'polls'

    id = db.Column(db.Integer, primary_key=True)
    family_id = db.Column(db.Integer, db.ForeignKey('families.id'), nullable=False, index=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey('people.id'), nullable=True)
    question = db.Column(db.String(250), nullable=False)
    closes_at = db.Column(db.Date, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    created_by = db.relationship('Person')
    options = db.relationship('PollOption', backref='poll', cascade='all, delete-orphan',
                              order_by='PollOption.id')

    @property
    def is_closed(self):
        return bool(self.closes_at and self.closes_at < date.today())

    @property
    def total_voters(self):
        return db.session.query(PollVote.person_id).filter_by(poll_id=self.id).distinct().count()


class PollOption(db.Model):
    __tablename__ = 'poll_options'

    id = db.Column(db.Integer, primary_key=True)
    poll_id = db.Column(db.Integer, db.ForeignKey('polls.id'), nullable=False, index=True)
    label = db.Column(db.String(150), nullable=False)

    votes = db.relationship('PollVote', backref='option', cascade='all, delete-orphan')

    @property
    def vote_count(self):
        return len(self.votes)


class PollVote(db.Model):
    __tablename__ = 'poll_votes'

    id = db.Column(db.Integer, primary_key=True)
    poll_id = db.Column(db.Integer, db.ForeignKey('polls.id'), nullable=False, index=True)
    option_id = db.Column(db.Integer, db.ForeignKey('poll_options.id'), nullable=False)
    person_id = db.Column(db.Integer, db.ForeignKey('people.id'), nullable=False)

    __table_args__ = (
        db.UniqueConstraint('option_id', 'person_id', name='uq_poll_vote'),
    )

    person = db.relationship('Person')


class GreetingCard(db.Model):
    __tablename__ = 'greeting_cards'

    id = db.Column(db.Integer, primary_key=True)
    family_id = db.Column(db.Integer, db.ForeignKey('families.id'), nullable=False, index=True)
    recipient_id = db.Column(db.Integer, db.ForeignKey('people.id'), nullable=False)
    created_by_id = db.Column(db.Integer, db.ForeignKey('people.id'), nullable=True)
    occasion = db.Column(db.String(50), nullable=False)  # birthday, anniversary, milestone, custom
    title = db.Column(db.String(150), nullable=False)
    send_date = db.Column(db.Date, nullable=True)
    sent_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    recipient = db.relationship('Person', foreign_keys=[recipient_id])
    created_by = db.relationship('Person', foreign_keys=[created_by_id])
    signatures = db.relationship('CardSignature', backref='card',
                                 cascade='all, delete-orphan',
                                 order_by='CardSignature.created_at')


class CardSignature(db.Model):
    __tablename__ = 'card_signatures'

    id = db.Column(db.Integer, primary_key=True)
    card_id = db.Column(db.Integer, db.ForeignKey('greeting_cards.id'), nullable=False, index=True)
    person_id = db.Column(db.Integer, db.ForeignKey('people.id'), nullable=False)
    message = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint('card_id', 'person_id', name='uq_card_signature'),
    )

    person = db.relationship('Person')


class StoryPrompt(db.Model):
    """A weekly AI-generated (or admin-assigned) story question for one person."""
    __tablename__ = 'story_prompts'

    id = db.Column(db.Integer, primary_key=True)
    family_id = db.Column(db.Integer, db.ForeignKey('families.id'), nullable=False, index=True)
    person_id = db.Column(db.Integer, db.ForeignKey('people.id'), nullable=False, index=True)  # the subject
    question = db.Column(db.Text, nullable=False)
    source = db.Column(db.String(10), nullable=False, default='auto')  # 'auto' | 'manual'
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    answered_at = db.Column(db.DateTime, nullable=True)

    person = db.relationship('Person')
    responses = db.relationship('StoryResponse', backref='prompt',
                                cascade='all, delete-orphan')

    @property
    def response(self):
        """The single answer to this prompt, if any."""
        return self.responses[0] if self.responses else None


class StoryResponse(db.Model):
    """A person's answer to a story prompt (one per prompt; answered_by may be a proxy)."""
    __tablename__ = 'story_responses'

    id = db.Column(db.Integer, primary_key=True)
    prompt_id = db.Column(db.Integer, db.ForeignKey('story_prompts.id'), nullable=False, unique=True)
    answer = db.Column(db.Text, nullable=False)
    answered_by_id = db.Column(db.Integer, db.ForeignKey('people.id'), nullable=True)  # self or proxy
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    answered_by = db.relationship('Person')


class AnnouncementReaction(db.Model):
    __tablename__ = 'announcement_reactions'

    id = db.Column(db.Integer, primary_key=True)
    announcement_id = db.Column(db.Integer, db.ForeignKey('announcements.id'), nullable=False, index=True)
    person_id = db.Column(db.Integer, db.ForeignKey('people.id'), nullable=False)
    emoji = db.Column(db.String(10), nullable=False)

    __table_args__ = (
        db.UniqueConstraint('announcement_id', 'person_id', 'emoji', name='uq_reaction'),
    )

    person = db.relationship('Person')


class Album(db.Model):
    __tablename__ = 'albums'

    id = db.Column(db.Integer, primary_key=True)
    family_id = db.Column(db.Integer, db.ForeignKey('families.id'), nullable=False, index=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey('people.id'), nullable=True)
    name = db.Column(db.String(150), nullable=False)
    description = db.Column(db.Text, nullable=True)
    year = db.Column(db.Integer, nullable=True)
    event_id = db.Column(db.Integer, db.ForeignKey('events.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    created_by = db.relationship('Person')
    event = db.relationship('Event', backref='albums')
    photos = db.relationship('Photo', backref='album', cascade='all, delete-orphan',
                             order_by='Photo.created_at')

    @property
    def cover(self):
        return self.photos[0] if self.photos else None

    @property
    def photo_count(self):
        return len(self.photos)


class CalendarToken(db.Model):
    """One persistent token per user — used to authenticate iCal feed subscriptions."""
    __tablename__ = 'calendar_tokens'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), unique=True, nullable=False)
    token = db.Column(db.String(64), unique=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship('User', backref=db.backref('calendar_token', uselist=False))


class Photo(db.Model):
    __tablename__ = 'photos'

    id = db.Column(db.Integer, primary_key=True)
    album_id = db.Column(db.Integer, db.ForeignKey('albums.id'), nullable=False)
    family_id = db.Column(db.Integer, db.ForeignKey('families.id'), nullable=False, index=True)
    uploaded_by_id = db.Column(db.Integer, db.ForeignKey('people.id'), nullable=True)
    path = db.Column(db.String(300), nullable=False)
    thumb_path = db.Column(db.String(300), nullable=True)
    caption = db.Column(db.String(300), nullable=True)
    taken_date = db.Column(db.Date, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    uploaded_by = db.relationship('Person', foreign_keys=[uploaded_by_id])
    tags = db.relationship('PhotoTag', backref='photo', cascade='all, delete-orphan',
                           order_by='PhotoTag.created_at')


# ── Photo tags ────────────────────────────────────────────────────────────────

class PhotoTag(db.Model):
    __tablename__ = 'photo_tags'

    id = db.Column(db.Integer, primary_key=True)
    photo_id = db.Column(db.Integer, db.ForeignKey('photos.id'), nullable=False, index=True)
    person_id = db.Column(db.Integer, db.ForeignKey('people.id'), nullable=False)
    tagged_by_id = db.Column(db.Integer, db.ForeignKey('people.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint('photo_id', 'person_id', name='uq_photo_tag'),
    )

    person = db.relationship('Person', foreign_keys=[person_id])


# ── Carpool ───────────────────────────────────────────────────────────────────

class CarpoolOffer(db.Model):
    __tablename__ = 'carpool_offers'

    id = db.Column(db.Integer, primary_key=True)
    event_id = db.Column(db.Integer, db.ForeignKey('events.id'), nullable=False, index=True)
    person_id = db.Column(db.Integer, db.ForeignKey('people.id'), nullable=False)
    role = db.Column(db.String(10), nullable=False)  # 'driver' or 'rider'
    seats = db.Column(db.Integer, nullable=True)      # drivers only
    departure_from = db.Column(db.String(150), nullable=True)  # drivers: city/area departing from
    passenger_of_id = db.Column(db.Integer, db.ForeignKey('carpool_offers.id'), nullable=True)  # riders: matched driver offer
    notes = db.Column(db.String(200), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint('event_id', 'person_id', name='uq_carpool_offer'),
    )

    person = db.relationship('Person', foreign_keys=[person_id])


# ── Documents ────────────────────────────────────────────────────────────────

DOCUMENT_CATEGORIES = ['Legal', 'Recipes', 'Letters', 'Certificates', 'Other']

class Document(db.Model):
    __tablename__ = 'documents'

    id = db.Column(db.Integer, primary_key=True)
    family_id = db.Column(db.Integer, db.ForeignKey('families.id'), nullable=False, index=True)
    uploader_id = db.Column(db.Integer, db.ForeignKey('people.id'), nullable=True)
    title = db.Column(db.String(200), nullable=False)
    category = db.Column(db.String(50), nullable=True)
    storage_key = db.Column(db.String(300), nullable=False)
    original_filename = db.Column(db.String(200), nullable=False)
    file_type = db.Column(db.String(20), nullable=False)
    file_size = db.Column(db.Integer, nullable=True)
    notes = db.Column(db.Text, nullable=True)
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    uploader = db.relationship('Person')


# ── Checklists ────────────────────────────────────────────────────────────────

class Checklist(db.Model):
    __tablename__ = 'checklists'

    id = db.Column(db.Integer, primary_key=True)
    family_id = db.Column(db.Integer, db.ForeignKey('families.id'), nullable=False, index=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey('people.id'), nullable=True)
    event_id = db.Column(db.Integer, db.ForeignKey('events.id'), nullable=True)
    title = db.Column(db.String(150), nullable=False)
    list_type = db.Column(db.String(20), default='general')  # packing, shopping, general
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    created_by = db.relationship('Person')
    event = db.relationship('Event')
    items = db.relationship('ChecklistItem', backref='checklist',
                            cascade='all, delete-orphan',
                            order_by='ChecklistItem.created_at')

    @property
    def done_count(self):
        return sum(1 for i in self.items if i.is_done)


class ChecklistItem(db.Model):
    __tablename__ = 'checklist_items'

    id = db.Column(db.Integer, primary_key=True)
    checklist_id = db.Column(db.Integer, db.ForeignKey('checklists.id'), nullable=False, index=True)
    label = db.Column(db.String(200), nullable=False)
    is_done = db.Column(db.Boolean, default=False, nullable=False)
    claimed_by_id = db.Column(db.Integer, db.ForeignKey('people.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    claimed_by = db.relationship('Person')


# ── Post-event survey ─────────────────────────────────────────────────────────

class EventSurveyResponse(db.Model):
    __tablename__ = 'event_survey_responses'

    id = db.Column(db.Integer, primary_key=True)
    event_id = db.Column(db.Integer, db.ForeignKey('events.id'), nullable=False, index=True)
    person_id = db.Column(db.Integer, db.ForeignKey('people.id'), nullable=False)
    rating = db.Column(db.Integer, nullable=False)   # 1–5
    what_worked = db.Column(db.Text, nullable=True)
    suggestions = db.Column(db.Text, nullable=True)
    submitted_at = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint('event_id', 'person_id', name='uq_survey_response'),
    )

    person = db.relationship('Person')


# ── Event payment collection ───────────────────────────────────────────────────

class FamilyPayoutAccount(db.Model):
    """Stripe Express connected account for a family admin to receive event payouts."""
    __tablename__ = 'family_payout_accounts'

    id = db.Column(db.Integer, primary_key=True)
    family_id = db.Column(db.Integer, db.ForeignKey('families.id'), nullable=False, unique=True)
    stripe_account_id = db.Column(db.String(100), nullable=False)  # acct_xxx
    onboarding_complete = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    family = db.relationship('Family', backref=db.backref('payout_account', uselist=False))


class EventPaymentConfig(db.Model):
    """Per-event payment settings configured by the admin."""
    __tablename__ = 'event_payment_configs'

    id = db.Column(db.Integer, primary_key=True)
    event_id = db.Column(db.Integer, db.ForeignKey('events.id'), nullable=False, unique=True)
    amount_cents = db.Column(db.Integer, nullable=False)  # e.g. 2500 = $25.00
    charge_type = db.Column(db.String(20), nullable=False, default='per_family')  # 'per_family' | 'per_person'
    family_cap_cents = db.Column(db.Integer, nullable=True)  # max charge per household for per_person mode
    description = db.Column(db.String(200), nullable=True)  # shown on Stripe Checkout page
    deadline = db.Column(db.Date, nullable=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True, server_default='1')
    payout_transfer_id = db.Column(db.String(200), nullable=True)  # Stripe Transfer ID once paid out
    payout_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    event = db.relationship('Event', back_populates='payment_config')

    @property
    def amount_dollars(self):
        return self.amount_cents / 100


class EventPaymentRecord(db.Model):
    """One row per payer per event — tracks Stripe session and payment status."""
    __tablename__ = 'event_payment_records'

    id = db.Column(db.Integer, primary_key=True)
    event_id = db.Column(db.Integer, db.ForeignKey('events.id'), nullable=False, index=True)
    # Nullable: set to NULL when the payer deletes their account (the family
    # keeps the financial record, the person link is erased).
    payer_user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True, index=True)
    amount_cents = db.Column(db.Integer, nullable=False)
    net_cents = db.Column(db.Integer, nullable=True)  # actual net after Stripe fees; populated by webhook
    stripe_checkout_session_id = db.Column(db.String(200), nullable=True, unique=True, index=True)
    stripe_payment_intent_id = db.Column(db.String(200), nullable=True, index=True)
    status = db.Column(db.String(20), nullable=False, default='pending')  # 'pending' | 'paid' | 'refunded'
    paid_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    event = db.relationship('Event', back_populates='payment_records')
    payer = db.relationship('User', backref=db.backref('event_payments', lazy='dynamic'))

    __table_args__ = (db.UniqueConstraint('event_id', 'payer_user_id'),)

    @property
    def amount_dollars(self):
        return self.amount_cents / 100

    @property
    def net_dollars(self):
        if self.net_cents is not None:
            return self.net_cents / 100
        return None
