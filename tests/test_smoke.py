"""
Smoke tests for critical paths: auth, profile, events, family_id isolation.
Run with: .venv/bin/pytest tests/ -v
"""
import pytest
from app import db
from app.models import Event, Person, User
from datetime import date


# ── Public pages ──────────────────────────────────────────────────────────────

def test_landing_page(client):
    r = client.get('/')
    assert r.status_code == 200
    assert b'Swugl' in r.data

def test_privacy_page(client):
    r = client.get('/privacy')
    assert r.status_code == 200

def test_terms_page(client):
    r = client.get('/terms')
    assert r.status_code == 200

def test_home_redirects_to_login_when_unauthenticated(client):
    r = client.get('/home')
    assert r.status_code == 302
    assert '/login' in r.headers['Location']


# ── Authentication ─────────────────────────────────────────────────────────────

def test_login_valid(client):
    r = client.post('/login', data={
        'email': 'admin@pease-family.com',
        'password': 'Password1!',
    }, follow_redirects=True)
    assert r.status_code == 200
    assert b'login' not in r.data.lower() or b'sign in' not in r.data.lower()

def test_login_wrong_password(app):
    """Wrong password on a logged-out client should stay on the login page."""
    with app.test_client() as c:
        # Ensure no session
        r = c.post('/login', data={
            'email': 'admin@pease-family.com',
            'password': 'wrongpassword',
        })
        assert r.status_code == 302
        assert 'login' in r.headers['Location']

def test_login_unknown_email(app):
    """Unknown email should redirect back to login, not crash."""
    with app.test_client() as c:
        r = c.post('/login', data={
            'email': 'nobody@nowhere.com',
            'password': 'Password1!',
        })
        assert r.status_code == 302
        assert 'login' in r.headers['Location']


# ── Authenticated pages ────────────────────────────────────────────────────────

def test_home_loads_when_authenticated(auth_client):
    r = auth_client.get('/home')
    assert r.status_code == 200

def test_profile_page_loads(auth_client):
    r = auth_client.get('/profile')
    assert r.status_code == 200

def test_members_page_loads(auth_client):
    r = auth_client.get('/members')
    assert r.status_code == 200

def test_albums_page_loads(auth_client):
    r = auth_client.get('/albums')
    assert r.status_code == 200

def test_events_page_loads(auth_client):
    r = auth_client.get('/events')
    assert r.status_code == 200


# ── Event CRUD ─────────────────────────────────────────────────────────────────

def test_create_event(app, auth_client):
    r = auth_client.post('/events/add', data={
        'name': 'Smoke Test Reunion',
        'start_date': '2026-07-04',
        'end_date': '',
        'location': 'Test City, UT',
        'description': '',
        'has_meals': 'y',
        'has_assignments': '',
        'has_sleeping': '',
    }, follow_redirects=True)
    assert r.status_code == 200
    with app.app_context():
        event = Event.query.filter_by(name='Smoke Test Reunion').first()
        assert event is not None
        assert event.has_meals is True


def test_event_detail_loads(seeded_event_id, auth_client):
    r = auth_client.get(f'/events/{seeded_event_id}')
    assert r.status_code == 200


def test_delete_event(app, seeded_event_id, auth_client):
    r = auth_client.post(f'/events/{seeded_event_id}/delete', follow_redirects=True)
    assert r.status_code == 200
    with app.app_context():
        assert db.session.get(Event, seeded_event_id) is None


# ── Family isolation (cross-family access prevention) ─────────────────────────

def test_cannot_view_other_familys_event(app, auth_client, other_auth_client):
    """Other family creates an event; Pease family should get a redirect, not the event."""
    with app.app_context():
        other_user = User.query.filter_by(email='admin@other-family.com').first()
        event = Event(
            family_id=other_user.family_id,
            name='Other Family Private Event',
            start_date=date(2026, 8, 1),
        )
        db.session.add(event)
        db.session.commit()
        event_id = event.id

    r = auth_client.get(f'/events/{event_id}', follow_redirects=False)
    assert r.status_code == 302  # redirected away, not served

    with app.app_context():
        db.session.get(Event, event_id) and db.session.delete(db.session.get(Event, event_id))
        db.session.commit()


def test_cannot_view_other_familys_person(app, auth_client):
    """Attempt to view a Person record from another family."""
    with app.app_context():
        other_user = User.query.filter_by(email='admin@other-family.com').first()
        other_person = Person.query.filter_by(family_id=other_user.family_id).first()
        person_id = other_person.id

    r = auth_client.get(f'/person/{person_id}', follow_redirects=False)
    assert r.status_code == 302


def test_cannot_edit_other_familys_person(app, auth_client):
    """Attempt to POST edits to a Person from another family."""
    with app.app_context():
        other_user = User.query.filter_by(email='admin@other-family.com').first()
        other_person = Person.query.filter_by(family_id=other_user.family_id).first()
        person_id = other_person.id

    r = auth_client.post(f'/person/{person_id}/edit', data={'name': 'Hacked'}, follow_redirects=False)
    assert r.status_code in (302, 403)

    with app.app_context():
        person = db.session.get(Person, person_id)
        assert person.name != 'Hacked'
