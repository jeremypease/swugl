import pytest
from datetime import datetime, timedelta
from app.models import ChatMessage, User


# ── helpers ──────────────────────────────────────────────────────────────────

def _send(client, body='Hello world'):
    return client.post('/chat/send', data={'body': body}, follow_redirects=True)



# ── auth guard ───────────────────────────────────────────────────────────────

def test_chat_requires_login(client):
    rv = client.get('/chat', follow_redirects=False)
    assert rv.status_code in (302, 301)


# ── basic access ─────────────────────────────────────────────────────────────

def test_chat_get_paid(auth_client):
    rv = auth_client.get('/chat')
    assert rv.status_code == 200
    assert b'Message the family' in rv.data


def test_chat_upgrade_prompt_for_free(app, auth_client):
    with app.app_context():
        user = User.query.filter_by(email='admin@pease-family.com').first()
        user.family.plan = 'free'
        from app import db
        db.session.commit()
    rv = auth_client.get('/chat')
    assert rv.status_code == 200
    assert b'Upgrade' in rv.data
    # Reset
    with app.app_context():
        user = User.query.filter_by(email='admin@pease-family.com').first()
        user.family.plan = 'paid'
        from app import db
        db.session.commit()


# ── send & poll ───────────────────────────────────────────────────────────────

def test_chat_send_creates_message(app, auth_client):
    _send(auth_client, 'Test message')
    with app.app_context():
        user = User.query.filter_by(email='admin@pease-family.com').first()
        msg = ChatMessage.query.filter_by(family_id=user.family_id, body='Test message').first()
        assert msg is not None


def test_chat_poll_shape(app, auth_client):
    _send(auth_client, 'Poll test')
    rv = auth_client.get('/chat/poll?after=0')
    assert rv.status_code == 200
    data = rv.get_json()
    assert 'messages' in data
    assert 'current_user_id' in data
    if data['messages']:
        m = data['messages'][0]
        for key in ('id', 'body', 'author_name', 'created_at', 'can_edit', 'can_delete'):
            assert key in m


def test_chat_poll_returns_only_new(app, auth_client):
    _send(auth_client, 'First')
    rv = auth_client.get('/chat/poll?after=0')
    data = rv.get_json()
    max_id = max(m['id'] for m in data['messages']) if data['messages'] else 0

    _send(auth_client, 'Second')
    rv2 = auth_client.get(f'/chat/poll?after={max_id}')
    data2 = rv2.get_json()
    assert any(m['body'] == 'Second' for m in data2['messages'])
    assert not any(m['body'] == 'First' for m in data2['messages'])


# ── family isolation ──────────────────────────────────────────────────────────

def test_chat_family_isolation(app, auth_client):
    """Messages are stored with the sender's family_id; the DB query never leaks them."""
    from app.models import ChatMessage as CM
    _send(auth_client, 'Pease secret')
    with app.app_context():
        user = User.query.filter_by(email='admin@pease-family.com').first()
        other_family_id = user.family_id + 1  # Other Family always has the next id
        pease_msgs = CM.query.filter_by(family_id=user.family_id, body='Pease secret').all()
        other_msgs = CM.query.filter_by(family_id=other_family_id, body='Pease secret').all()
        assert len(pease_msgs) == 1
        assert len(other_msgs) == 0


# ── delete ────────────────────────────────────────────────────────────────────

def test_delete_own_message_within_window(app, auth_client):
    _send(auth_client, 'To delete')
    with app.app_context():
        user = User.query.filter_by(email='admin@pease-family.com').first()
        msg = ChatMessage.query.filter_by(family_id=user.family_id, body='To delete').first()
        msg_id = msg.id
    rv = auth_client.post(f'/chat/{msg_id}/delete', follow_redirects=False)
    assert rv.status_code in (302, 200)
    with app.app_context():
        assert ChatMessage.query.get(msg_id) is None


def test_cannot_delete_after_window(app):
    """The model's can_delete returns False after the 2-min window for non-admins."""
    from app.models import ChatMessage as CM
    from app import db
    with app.app_context():
        user = User.query.filter_by(email='admin@pease-family.com').first()
        user.is_admin = False
        msg = CM(family_id=user.family_id, author_id=user.id, body='Stale msg')
        msg.created_at = datetime.utcnow() - timedelta(minutes=5)
        db.session.add(msg)
        db.session.commit()
        # Reload fresh instances to avoid identity-map hits
        user2 = User.query.get(user.id)
        msg2 = CM.query.get(msg.id)
        assert user2.is_admin is False
        assert msg2.can_delete(user2) is False


def test_cross_family_delete_blocked(app, auth_client):
    """The route filter prevents a user from deleting a message from another family."""
    from app.models import ChatMessage as CM, Family
    from app import db
    with app.app_context():
        user = User.query.filter_by(email='admin@pease-family.com').first()
        other_family_id = user.family_id + 1
        # Create a message directly for the other family
        msg = CM(family_id=other_family_id, author_id=user.id, body='Other family msg')
        db.session.add(msg)
        db.session.commit()
        msg_id = msg.id
    # auth_client (Pease admin) tries to delete Other Family's message
    rv = auth_client.post(f'/chat/{msg_id}/delete')
    # first_or_404 should reject it since family_id filter won't match
    assert rv.status_code == 404
    with app.app_context():
        assert CM.query.filter_by(id=msg_id).first() is not None


def test_admin_can_delete_old_message(app, auth_client):
    _send(auth_client, 'Old admin delete')
    with app.app_context():
        from app import db
        user = User.query.filter_by(email='admin@pease-family.com').first()
        msg = ChatMessage.query.filter_by(family_id=user.family_id, body='Old admin delete').first()
        msg.created_at = datetime.utcnow() - timedelta(hours=2)
        db.session.commit()
        msg_id = msg.id
    rv = auth_client.post(f'/chat/{msg_id}/delete', follow_redirects=False)
    assert rv.status_code in (302, 200)
    with app.app_context():
        assert ChatMessage.query.get(msg_id) is None


# ── edit ──────────────────────────────────────────────────────────────────────

def test_edit_own_message_within_window(app, auth_client):
    _send(auth_client, 'Original body')
    with app.app_context():
        user = User.query.filter_by(email='admin@pease-family.com').first()
        msg = ChatMessage.query.filter_by(family_id=user.family_id, body='Original body').first()
        msg_id = msg.id
    rv = auth_client.post(f'/chat/{msg_id}/edit', data={'body': 'Edited body'})
    assert rv.status_code == 200
    data = rv.get_json()
    assert data['ok'] is True
    assert data['body'] == 'Edited body'
    with app.app_context():
        msg = ChatMessage.query.get(msg_id)
        assert msg.body == 'Edited body'
        assert msg.edited_at is not None


def test_edit_rejected_after_window(app, auth_client):
    _send(auth_client, 'Old body')
    with app.app_context():
        from app import db
        user = User.query.filter_by(email='admin@pease-family.com').first()
        msg = ChatMessage.query.filter_by(family_id=user.family_id, body='Old body').first()
        msg.created_at = datetime.utcnow() - timedelta(minutes=20)
        db.session.commit()
        msg_id = msg.id
    rv = auth_client.post(f'/chat/{msg_id}/edit', data={'body': 'Too late'})
    assert rv.status_code == 403


# ── notification collapsing (#41) ─────────────────────────────────────────────

def _add_member(family_id, email):
    from app import db
    from app.models import Person
    person = Person(name='Chat Watcher', family_id=family_id)
    db.session.add(person); db.session.flush()
    user = User(family_id=family_id, person_id=person.id, first_name='Chat',
                last_name='Watcher', email=email, status='approved',
                email_verified=True, is_admin=False)
    user.set_password('Password1!')
    db.session.add(user); db.session.commit()
    return user.id


def test_chat_notifications_collapse_to_one(app, auth_client):
    """Multiple messages produce a single rolling notification per recipient."""
    from app import db
    from app.models import Notification, User
    with app.app_context():
        admin = User.query.filter_by(email='admin@pease-family.com').first()
        watcher_id = _add_member(admin.family_id, 'cw@pease-family.com')

    _send(auth_client, 'First')
    _send(auth_client, 'Second')
    _send(auth_client, 'Third')

    with app.app_context():
        notifs = Notification.query.filter_by(
            user_id=watcher_id, event_type='chat_message', read_at=None
        ).all()
        assert len(notifs) == 1
        assert '3 new messages' in notifs[0].title


def test_opening_chat_clears_chat_notifications(app, auth_client):
    """Opening /chat marks the viewer's unread chat notifications read."""
    from app import db
    from app.models import Notification, User
    with app.app_context():
        admin = User.query.filter_by(email='admin@pease-family.com').first()
        db.session.add(Notification(user_id=admin.id, event_type='chat_message',
                                    title='2 new messages in chat', url='/chat'))
        db.session.commit()
        admin_id = admin.id

    auth_client.get('/chat')

    with app.app_context():
        assert Notification.query.filter_by(
            user_id=admin_id, event_type='chat_message', read_at=None).count() == 0


def test_chat_poll_throttles_seen_writes(app, auth_client):
    """A second poll within the throttle window does not move chat_last_seen_at."""
    from app import db
    from app.models import User

    auth_client.get('/chat/poll?after=0')  # first poll writes (was None)
    with app.app_context():
        db.session.expire_all()
        t1 = User.query.filter_by(email='admin@pease-family.com').first().chat_last_seen_at

    auth_client.get('/chat/poll?after=0')  # immediate second poll — throttled
    with app.app_context():
        db.session.expire_all()
        t2 = User.query.filter_by(email='admin@pease-family.com').first().chat_last_seen_at

    assert t1 is not None
    assert t1 == t2
