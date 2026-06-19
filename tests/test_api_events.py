"""
API (mobile) event RSVP tests.

Regression: /api/v1/events/<id>/rsvp crashed with
`AttributeError: 'Person' object has no attribute 'household_members'`
(Sentry PYTHON-FLASK-J). The endpoint must mirror the web route — admins may
RSVP anyone, members only their own household.
"""
import json
from datetime import date
from app import db
from app.models import User, Person, Event, EventRSVP


def _api_token(client, email='admin@pease-family.com', password='Password1!'):
    r = client.post('/api/v1/auth/login',
                    json={'email': email, 'password': password},
                    content_type='application/json')
    return json.loads(r.data)['access_token']


def _event_and_outsider(app):
    admin = User.query.filter_by(email='admin@pease-family.com').first()
    ev = Event(family_id=admin.family_id, name='RSVP Event', start_date=date(2026, 7, 4))
    outsider = Person(name='Outsider', family_id=admin.family_id)
    db.session.add_all([ev, outsider]); db.session.commit()
    return ev.id, outsider.id


def _member(app, email='member@pease-family.com'):
    admin = User.query.filter_by(email='admin@pease-family.com').first()
    p = Person(name='Member One', family_id=admin.family_id)
    db.session.add(p); db.session.flush()
    u = User(family_id=admin.family_id, person_id=p.id, first_name='Member',
             last_name='One', email=email, status='approved',
             email_verified=True, is_admin=False)
    u.set_password('Password1!')
    db.session.add(u); db.session.commit()
    return u.id, p.id


def _rsvp(client, token, eid, person_id, status):
    return client.post(f'/api/v1/events/{eid}/rsvp',
                       json={'person_id': person_id, 'status': status},
                       headers={'Authorization': f'Bearer {token}'},
                       content_type='application/json')


def test_api_admin_can_rsvp_anyone(app, client):
    with app.app_context():
        eid, outsider_id = _event_and_outsider(app)
    r = _rsvp(client, _api_token(client), eid, outsider_id, 'yes')
    assert r.status_code == 200
    with app.app_context():
        assert EventRSVP.query.filter_by(
            event_id=eid, person_id=outsider_id, status='yes').count() == 1


def test_api_member_can_rsvp_self(app, client):
    """The path that used to crash on Person.household_members()."""
    with app.app_context():
        eid, _ = _event_and_outsider(app)
        _uid, pid = _member(app)
    r = _rsvp(client, _api_token(client, 'member@pease-family.com'), eid, pid, 'maybe')
    assert r.status_code == 200
    with app.app_context():
        assert EventRSVP.query.filter_by(event_id=eid, person_id=pid).count() == 1


def test_api_member_cannot_rsvp_outsider(app, client):
    with app.app_context():
        eid, outsider_id = _event_and_outsider(app)
        _member(app)
    r = _rsvp(client, _api_token(client, 'member@pease-family.com'), eid, outsider_id, 'yes')
    assert r.status_code == 403
