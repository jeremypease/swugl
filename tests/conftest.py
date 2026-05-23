import pytest
import os
import tempfile
from sqlalchemy.pool import NullPool

os.environ.setdefault('SECRET_KEY', 'test-secret-key')
os.environ.setdefault('DATABASE_URL', '')
os.environ.setdefault('FLASK_ENV', 'testing')

from app import create_app, db as _db
from app.models import Family, User, Person, Event, UserCredential
from datetime import date


@pytest.fixture()
def app():
    db_fd, db_path = tempfile.mkstemp(suffix='.db')
    os.close(db_fd)
    application = create_app({
        'TESTING': True,
        'SQLALCHEMY_DATABASE_URI': f'sqlite:///{db_path}',
        'SQLALCHEMY_ENGINE_OPTIONS': {'poolclass': NullPool},
        'WTF_CSRF_ENABLED': False,
    })
    with application.app_context():
        _db.create_all()
        _seed()
        yield application
        _db.session.remove()
    os.unlink(db_path)


def _seed():
    """Create two families with one admin each for isolation tests."""
    for fname, email, password in [
        ('Pease Family', 'admin@pease-family.com', 'Password1!'),
        ('Other Family', 'admin@other-family.com', 'Password1!'),
    ]:
        family = Family(name=fname)
        _db.session.add(family)
        _db.session.flush()
        person = Person(name='Admin', family_id=family.id)
        _db.session.add(person)
        _db.session.flush()
        user = User(
            family_id=family.id,
            person_id=person.id,
            first_name='Admin',
            last_name=fname.split()[0],
            email=email,
            status='approved',
            email_verified=True,
            is_admin=True,
        )
        user.set_password(password)
        _db.session.add(user)
    _db.session.commit()


@pytest.fixture()
def client(app):
    return app.test_client()


def _login(app, email, password):
    client = app.test_client()
    client.post('/login', data={'email': email, 'password': password})
    return client


@pytest.fixture()
def auth_client(app):
    """Test client logged in as the Pease family admin."""
    return _login(app, 'admin@pease-family.com', 'Password1!')


@pytest.fixture()
def other_auth_client(app):
    """Test client logged in as the Other Family admin."""
    return _login(app, 'admin@other-family.com', 'Password1!')


@pytest.fixture()
def seeded_event_id(app):
    """A Pease-family event pre-seeded for detail/delete tests."""
    user = User.query.filter_by(email='admin@pease-family.com').first()
    event = Event(
        family_id=user.family_id,
        name='Seeded Test Event',
        start_date=date(2026, 7, 4),
    )
    _db.session.add(event)
    _db.session.commit()
    return event.id
