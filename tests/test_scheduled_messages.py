"""
Future / scheduled messages: compose, recipient-hidden-until-delivery, cancel,
and the deliver-messages cron.
"""
from datetime import date, timedelta
from app import db
from app.models import User, Person, ScheduledMessage, Notification
from app.commands import deliver_messages


def _admin():
    return User.query.filter_by(email='admin@pease-family.com').first()


def _recipient_with_account(app, email='kid@pease-family.com'):
    admin = _admin()
    p = Person(name='Kid Pease', family_id=admin.family_id)
    db.session.add(p); db.session.flush()
    u = User(family_id=admin.family_id, person_id=p.id, first_name='Kid',
             last_name='Pease', email=email, status='approved',
             email_verified=True, is_admin=False)
    u.set_password('Password1!')
    db.session.add(u); db.session.commit()
    return u.id, p.id


def _schedule(app, recipient_pid, deliver_on, author_uid=None, body='hello future'):
    author_uid = author_uid or _admin().id
    m = ScheduledMessage(family_id=_admin().family_id, author_user_id=author_uid,
                         recipient_person_id=recipient_pid, body=body, deliver_on=deliver_on)
    db.session.add(m); db.session.commit()
    return m.id


# ── compose ──────────────────────────────────────────────────────────────────

def test_compose_creates_future_message(app, auth_client):
    with app.app_context():
        _uid, pid = _recipient_with_account(app)
    r = auth_client.post('/messages/new', data={
        'recipient_id': pid, 'deliver_on': '2030-05-01',
        'subject': 'Happy 18th', 'body': "Here's what I want you to know…",
    }, follow_redirects=True)
    assert r.status_code == 200
    with app.app_context():
        m = ScheduledMessage.query.filter_by(recipient_person_id=pid).first()
        assert m is not None and m.deliver_on == date(2030, 5, 1) and m.delivered_at is None


def test_compose_rejects_non_future_date(app, auth_client):
    with app.app_context():
        _uid, pid = _recipient_with_account(app)
    r = auth_client.post('/messages/new', data={
        'recipient_id': pid, 'deliver_on': date.today().isoformat(), 'body': 'nope',
    }, follow_redirects=True)
    assert b'future' in r.data.lower()
    with app.app_context():
        assert ScheduledMessage.query.filter_by(recipient_person_id=pid).count() == 0


# ── recipient hidden until delivery ──────────────────────────────────────────

def test_recipient_cannot_view_before_delivery(app):
    with app.app_context():
        uid, pid = _recipient_with_account(app)
        mid = _schedule(app, pid, date.today() + timedelta(days=30))
    client = app.test_client()
    client.post('/login', data={'email': 'kid@pease-family.com', 'password': 'Password1!'})
    assert client.get(f'/messages/{mid}').status_code == 403  # not yet delivered


def test_author_can_view_and_cancel_before_delivery(app, auth_client):
    with app.app_context():
        _uid, pid = _recipient_with_account(app)
        mid = _schedule(app, pid, date.today() + timedelta(days=30))
    assert auth_client.get(f'/messages/{mid}').status_code == 200
    auth_client.post(f'/messages/{mid}/cancel', follow_redirects=True)
    with app.app_context():
        assert db.session.get(ScheduledMessage, mid) is None


def test_outsider_cannot_view(app, other_auth_client):
    with app.app_context():
        _uid, pid = _recipient_with_account(app)
        mid = _schedule(app, pid, date.today() + timedelta(days=30))
    assert other_auth_client.get(f'/messages/{mid}').status_code in (403, 404)


# ── delivery cron ────────────────────────────────────────────────────────────

def test_deliver_messages_delivers_due_and_notifies(app):
    with app.app_context():
        uid, pid = _recipient_with_account(app)
        due = _schedule(app, pid, date.today() - timedelta(days=1), body='its time')
        future = _schedule(app, pid, date.today() + timedelta(days=10), body='later')
    app.test_cli_runner().invoke(deliver_messages, [])
    with app.app_context():
        assert db.session.get(ScheduledMessage, due).delivered_at is not None
        assert db.session.get(ScheduledMessage, future).delivered_at is None
        assert Notification.query.filter_by(user_id=uid, event_type='scheduled_message').count() == 1
        # now the recipient may read it
    client = app.test_client()
    client.post('/login', data={'email': 'kid@pease-family.com', 'password': 'Password1!'})
    assert client.get(f'/messages/{due}').status_code == 200


def test_deliver_holds_message_for_account_less_recipient(app):
    with app.app_context():
        admin = _admin()
        p = Person(name='No Account', family_id=admin.family_id)  # no User
        db.session.add(p); db.session.flush()
        mid = _schedule(app, p.id, date.today() - timedelta(days=1))
    app.test_cli_runner().invoke(deliver_messages, [])
    with app.app_context():
        assert db.session.get(ScheduledMessage, mid).delivered_at is None  # held, not lost
