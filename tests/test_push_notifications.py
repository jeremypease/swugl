"""
Push notifications (#9): device registration + the new triggers
(event updated, RSVP received, birthday reminders).

Push dispatch itself is gated behind PUSH_ENABLED (off in tests); the
observable, deterministic surface is the in-app Notification rows that
create_notification writes — and which send_push_notification mirrors to
devices in production.
"""
import json
from datetime import date, timedelta
from app import db
from app.models import User, Person, Event, Notification, UserDevice
from app.commands import birthday_reminders


def _api_token(client, email='admin@pease-family.com'):
    r = client.post('/api/v1/auth/login',
                    json={'email': email, 'password': 'Password1!'},
                    content_type='application/json')
    return json.loads(r.data)['access_token']


def _member(app, email='member@pease-family.com', is_admin=False):
    admin = User.query.filter_by(email='admin@pease-family.com').first()
    p = Person(name='Mem Ber', family_id=admin.family_id)
    db.session.add(p); db.session.flush()
    u = User(family_id=admin.family_id, person_id=p.id, first_name='Mem',
             last_name='Ber', email=email, status='approved',
             email_verified=True, is_admin=is_admin)
    u.set_password('Password1!')
    db.session.add(u); db.session.commit()
    return u.id, p.id


# ── device registration ──────────────────────────────────────────────────────

def test_push_register_is_idempotent_then_unregister(app, client):
    tok = _api_token(client)
    hdr = {'Authorization': f'Bearer {tok}'}
    for _ in range(2):  # registering the same token twice → one row
        r = client.post('/api/v1/push/register',
                        json={'token': 'dev-1', 'platform': 'ios'}, headers=hdr)
        assert r.status_code == 200
    with app.app_context():
        assert UserDevice.query.filter_by(token='dev-1').count() == 1
    client.post('/api/v1/push/unregister', json={'token': 'dev-1'}, headers=hdr)
    with app.app_context():
        assert UserDevice.query.filter_by(token='dev-1').count() == 0


def test_push_register_rejects_bad_platform(app, client):
    tok = _api_token(client)
    r = client.post('/api/v1/push/register',
                    json={'token': 'x', 'platform': 'windows'},
                    headers={'Authorization': f'Bearer {tok}'})
    assert r.status_code == 400


# ── event updated ────────────────────────────────────────────────────────────

def _edit_payload(**over):
    base = {'name': 'Reunion', 'kind': '', 'description': '', 'location': '',
            'location_id': '', 'start_date': '2026-08-01', 'end_date': '',
            'start_time': '', 'end_time': '', 'rsvp_deadline': '',
            'recur_freq': '', 'recur_until': ''}
    base.update(over)
    return base


def test_event_update_notifies_family_on_datechange(app, auth_client):
    with app.app_context():
        member_id, _ = _member(app)
        admin = User.query.filter_by(email='admin@pease-family.com').first()
        ev = Event(family_id=admin.family_id, name='Reunion', start_date=date(2026, 8, 1))
        db.session.add(ev); db.session.commit()
        eid = ev.id
    auth_client.post(f'/events/{eid}/edit',
                     data=_edit_payload(start_date='2026-09-15'), follow_redirects=True)
    with app.app_context():
        assert Notification.query.filter_by(
            user_id=member_id, event_type='event_updated').count() == 1


def test_event_update_silent_when_nothing_significant_changes(app, auth_client):
    with app.app_context():
        member_id, _ = _member(app)
        admin = User.query.filter_by(email='admin@pease-family.com').first()
        ev = Event(family_id=admin.family_id, name='Reunion', start_date=date(2026, 8, 1))
        db.session.add(ev); db.session.commit()
        eid = ev.id
    # Same date/place — only the name differs → no event_updated notification.
    auth_client.post(f'/events/{eid}/edit',
                     data=_edit_payload(name='Reunion (renamed)', start_date='2026-08-01'),
                     follow_redirects=True)
    with app.app_context():
        assert Notification.query.filter_by(
            user_id=member_id, event_type='event_updated').count() == 0


# ── RSVP received → admins ───────────────────────────────────────────────────

def test_rsvp_notifies_admins_not_actor(app, client):
    with app.app_context():
        member_uid, member_pid = _member(app)
        admin = User.query.filter_by(email='admin@pease-family.com').first()
        admin_uid = admin.id
        ev = Event(family_id=admin.family_id, name='BBQ', start_date=date(2026, 8, 1))
        db.session.add(ev); db.session.commit()
        eid = ev.id
    tok = _api_token(client, 'member@pease-family.com')
    r = client.post(f'/api/v1/events/{eid}/rsvp',
                    json={'person_id': member_pid, 'status': 'yes'},
                    headers={'Authorization': f'Bearer {tok}'},
                    content_type='application/json')
    assert r.status_code == 200
    with app.app_context():
        assert Notification.query.filter_by(
            user_id=admin_uid, event_type='rsvp_received').count() == 1
        # the member who RSVP'd (the actor) is not notified
        assert Notification.query.filter_by(
            user_id=member_uid, event_type='rsvp_received').count() == 0


# ── birthday reminders job ───────────────────────────────────────────────────

def test_birthday_reminders_dry_run_lists_without_notifying(app):
    with app.app_context():
        admin = User.query.filter_by(email='admin@pease-family.com').first()
        tomorrow = date.today() + timedelta(days=1)
        db.session.add(Person(name='Birthday Kid', family_id=admin.family_id,
                              birthday=date(1990, tomorrow.month, tomorrow.day)))
        db.session.commit()
    out = app.test_cli_runner().invoke(birthday_reminders, ['--dry-run'])
    assert 'Birthday Kid' in out.output
    with app.app_context():
        assert Notification.query.filter_by(event_type='birthday').count() == 0


def test_birthday_reminders_notifies_family(app):
    with app.app_context():
        admin = User.query.filter_by(email='admin@pease-family.com').first()
        admin_uid = admin.id
        tomorrow = date.today() + timedelta(days=1)
        db.session.add(Person(name='Birthday Kid', family_id=admin.family_id,
                              birthday=date(1990, tomorrow.month, tomorrow.day)))
        db.session.commit()
    app.test_cli_runner().invoke(birthday_reminders, [])
    with app.app_context():
        assert Notification.query.filter_by(
            user_id=admin_uid, event_type='birthday').count() == 1
