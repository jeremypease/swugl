from . import db, login_manager
from flask_login import UserMixin
from datetime import date, datetime
from werkzeug.security import generate_password_hash, check_password_hash

# Junction table for parent-child relationships
parent_child = db.Table('parent_child',
    db.Column('parent_id', db.Integer, db.ForeignKey('people.id'), primary_key=True),
    db.Column('child_id', db.Integer, db.ForeignKey('people.id'), primary_key=True)
)

class Family(db.Model):
    __tablename__ = 'families'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    patriarch_id = db.Column(db.Integer, db.ForeignKey('people.id'), nullable=True)
    matriarch_id = db.Column(db.Integer, db.ForeignKey('people.id'), nullable=True)

    people = db.relationship('Person', back_populates='family', foreign_keys='Person.family_id')
    users = db.relationship('User', back_populates='family', foreign_keys='User.family_id')
    patriarch = db.relationship('Person', foreign_keys=[patriarch_id])
    matriarch = db.relationship('Person', foreign_keys=[matriarch_id])


class SpouseRelationship(db.Model):
    __tablename__ = 'spouse_relationships'

    id = db.Column(db.Integer, primary_key=True)
    person1_id = db.Column(db.Integer, db.ForeignKey('people.id'), nullable=False)
    person2_id = db.Column(db.Integer, db.ForeignKey('people.id'), nullable=False)
    status = db.Column(db.String(20), default='active')  # active, deceased, divorced, separated, annulled
    marriage_date = db.Column(db.Date)
    end_date = db.Column(db.Date)
    confirmed = db.Column(db.Boolean, default=False)
    confirmation_token = db.Column(db.String(100))
    confirmation_token_expiry = db.Column(db.DateTime)

    person1 = db.relationship('Person', foreign_keys=[person1_id], backref='spouse_relationships_as_p1')
    person2 = db.relationship('Person', foreign_keys=[person2_id], backref='spouse_relationships_as_p2')

    def get_spouse_of(self, person):
        """Return the other person in this relationship."""
        if self.person1_id == person.id:
            return self.person2
        return self.person1

class Person(db.Model):
    __tablename__ = 'people'

    id = db.Column(db.Integer, primary_key=True)
    family_id = db.Column(db.Integer, db.ForeignKey('families.id'), nullable=False)
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
    photo_path = db.Column(db.String(200))
    photo_position = db.Column(db.String(20), default='50% 30%')
    notes = db.Column(db.Text)

    family = db.relationship('Family', back_populates='people', foreign_keys='Person.family_id')

    # Relationships
    children = db.relationship(
        'Person',
        secondary=parent_child,
        primaryjoin=id == parent_child.c.parent_id,
        secondaryjoin=id == parent_child.c.child_id,
        backref='parents'
    )

    def get_active_spouse(self):
        """Return the confirmed active spouse if one exists."""
        for rel in self.spouse_relationships_as_p1 + self.spouse_relationships_as_p2:
            if rel.status == 'active' and rel.confirmed:
                return rel.get_spouse_of(self)
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
        return self.nickname if self.nickname else self.name

    def get_age(self):
        if not self.birthday:
            return None
        end = self.deathday or date.today()
        age = end.year - self.birthday.year
        if (end.month, end.day) < (self.birthday.month, self.birthday.day):
            age -= 1
        return age


class User(UserMixin, db.Model):
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    family_id = db.Column(db.Integer, db.ForeignKey('families.id'), nullable=False)
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

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def get_full_name(self):
        return f"{self.first_name} {self.last_name}"


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


event_sleeping_assignments = db.Table('event_sleeping_assignments',
    db.Column('spot_id', db.Integer, db.ForeignKey('event_sleeping_spots.id'), primary_key=True),
    db.Column('person_id', db.Integer, db.ForeignKey('people.id'), primary_key=True)
)


class Event(db.Model):
    __tablename__ = 'events'

    id = db.Column(db.Integer, primary_key=True)
    family_id = db.Column(db.Integer, db.ForeignKey('families.id'), nullable=False)
    name = db.Column(db.String(150), nullable=False)
    description = db.Column(db.Text, nullable=True)
    location = db.Column(db.String(200), nullable=True)
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=True)
    has_meals = db.Column(db.Boolean, default=False)
    has_assignments = db.Column(db.Boolean, default=False)
    has_sleeping = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    meals = db.relationship('EventMeal', backref='event', cascade='all, delete-orphan', order_by='EventMeal.meal_date')
    assignments = db.relationship('EventAssignment', backref='event', cascade='all, delete-orphan')
    sleeping_spots = db.relationship('EventSleepingSpot', backref='event', cascade='all, delete-orphan')

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
    is_cleanup = db.Column(db.Boolean, default=False)
    assigned_to_id = db.Column(db.Integer, db.ForeignKey('people.id'), nullable=True)

    assigned_to = db.relationship('Person')


class EventAssignment(db.Model):
    __tablename__ = 'event_assignments'

    id = db.Column(db.Integer, primary_key=True)
    event_id = db.Column(db.Integer, db.ForeignKey('events.id'), nullable=False)
    title = db.Column(db.String(150), nullable=False)
    description = db.Column(db.Text, nullable=True)
    claimed_by_id = db.Column(db.Integer, db.ForeignKey('people.id'), nullable=True)
    is_done = db.Column(db.Boolean, default=False)

    claimed_by = db.relationship('Person')


class EventSleepingSpot(db.Model):
    __tablename__ = 'event_sleeping_spots'

    id = db.Column(db.Integer, primary_key=True)
    event_id = db.Column(db.Integer, db.ForeignKey('events.id'), nullable=False)
    name = db.Column(db.String(150), nullable=False)
    capacity = db.Column(db.Integer, nullable=True)
    notes = db.Column(db.Text, nullable=True)

    people = db.relationship('Person', secondary='event_sleeping_assignments')
