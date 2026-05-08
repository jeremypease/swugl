from . import db, login_manager
from flask_login import UserMixin
from datetime import date
from werkzeug.security import generate_password_hash, check_password_hash

# Junction table for parent-child relationships
parent_child = db.Table('parent_child',
    db.Column('parent_id', db.Integer, db.ForeignKey('people.id'), primary_key=True),
    db.Column('child_id', db.Integer, db.ForeignKey('people.id'), primary_key=True)
)

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
    name = db.Column(db.String(100), nullable=False)
    gender = db.Column(db.String(10))
    birthday = db.Column(db.Date)
    nickname = db.Column(db.String(50))
    birthplace = db.Column(db.String(100))
    maiden_name = db.Column(db.String(100))
    spouse_name = db.Column(db.String(100))
    deathday = db.Column(db.Date)
    deathplace = db.Column(db.String(100))
    occupation = db.Column(db.String(100))
    email = db.Column(db.String(120))
    phone = db.Column(db.String(20))
    photo_path = db.Column(db.String(200))
    notes = db.Column(db.Text)

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
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    first_name = db.Column(db.String(50), nullable=False)
    last_name = db.Column(db.String(50), nullable=False)
    phone = db.Column(db.String(20))

    # Email verification
    email_verified = db.Column(db.Boolean, default=False)
    verification_token = db.Column(db.String(100))

    # Approval
    status = db.Column(db.String(20), default='pending')  # pending/approved/rejected
    approved_by_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    approved_date = db.Column(db.Date)

    # Roles
    is_admin = db.Column(db.Boolean, default=False)
    is_delegate = db.Column(db.Boolean, default=False)

    # Link to person in family tree
    person_id = db.Column(db.Integer, db.ForeignKey('people.id'))
    person = db.relationship('Person', back_populates='user')

    # Invitation tracking
    invitation_token = db.Column(db.String(100))
    invited_by_id = db.Column(db.Integer, db.ForeignKey('users.id'))

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def get_full_name(self):
        return f"{self.first_name} {self.last_name}"


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))
