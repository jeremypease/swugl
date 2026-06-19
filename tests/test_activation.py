"""
Tests for Theme 1 (activation): expanded onboarding checklist and the
one-step invite from the add-member form.
"""
import pytest
from app import db
from app.models import Family, User, Person


@pytest.fixture()
def new_pod_admin(app):
    """Give the Pease family an account_id so the onboarding checklist shows."""
    with app.app_context():
        fam = User.query.filter_by(email='admin@pease-family.com').first().family
        fam.account_id = 'POD123'
        db.session.commit()


def test_onboarding_checklist_expanded(app, auth_client, new_pod_admin):
    html = auth_client.get('/home').data.decode()
    for label in ('Add your first family member', 'Complete your profile',
                  'Create your first event', 'Upload a photo', 'Add a family location'):
        assert label in html


def test_onboarding_hidden_without_account_id(app, auth_client):
    # Default seed family has no account_id → checklist suppressed
    html = auth_client.get('/home').data.decode()
    assert 'Create your first event' not in html


def test_one_step_invite_creates_person_and_invited_user(app, auth_client):
    auth_client.post('/admin/add-member', data={
        'first_name': 'Invited', 'last_name': 'Cousin',
        'email': 'cousin@example.com', 'gender': 'Female',
        'invite_now': '1',
    }, follow_redirects=True)
    with app.app_context():
        admin = User.query.filter_by(email='admin@pease-family.com').first()
        person = Person.query.filter_by(family_id=admin.family_id, name='Invited Cousin').first()
        assert person is not None
        invited = User.query.filter_by(email='cousin@example.com').first()
        assert invited is not None
        assert invited.status == 'invited'
        assert invited.person_id == person.id


def test_add_member_without_invite_creates_only_person(app, auth_client):
    auth_client.post('/admin/add-member', data={
        'first_name': 'Tree', 'last_name': 'Only',
        'email': 'treeonly@example.com', 'gender': 'Male',
        # invite_now omitted
    }, follow_redirects=True)
    with app.app_context():
        admin = User.query.filter_by(email='admin@pease-family.com').first()
        assert Person.query.filter_by(family_id=admin.family_id, name='Tree Only').first() is not None
        assert User.query.filter_by(email='treeonly@example.com').first() is None


# ── invite expiry + resend (member invitations) ──────────────────────────────

def _invite_target(app, name='Cousin Zed', email='zed@example.com'):
    admin = User.query.filter_by(email='admin@pease-family.com').first()
    p = Person(name=name, family_id=admin.family_id, email=email)
    db.session.add(p); db.session.commit()
    return p.id


def test_invite_sets_30_day_expiry(app, auth_client):
    from datetime import datetime
    with app.app_context():
        pid = _invite_target(app)
    auth_client.post(f'/person/{pid}/invite', follow_redirects=True)
    with app.app_context():
        iu = User.query.filter_by(email='zed@example.com').first()
        assert iu is not None and iu.status == 'invited'
        days = (iu.invitation_token_expiry - datetime.utcnow()).days
        assert 29 <= days <= 30


def test_resend_regenerates_token_without_duplicate(app, auth_client):
    with app.app_context():
        pid = _invite_target(app)
    auth_client.post(f'/person/{pid}/invite', follow_redirects=True)
    with app.app_context():
        iu = User.query.filter_by(email='zed@example.com').first()
        tok1, uid1 = iu.invitation_token, iu.id
    auth_client.post(f'/person/{pid}/invite', follow_redirects=True)  # resend
    with app.app_context():
        rows = User.query.filter_by(email='zed@example.com').all()
        assert len(rows) == 1                 # no duplicate account
        assert rows[0].id == uid1             # same row refreshed
        assert rows[0].invitation_token != tok1   # new token


def test_resend_works_after_expiry(app, auth_client):
    from datetime import datetime, timedelta
    with app.app_context():
        pid = _invite_target(app)
    auth_client.post(f'/person/{pid}/invite', follow_redirects=True)
    with app.app_context():
        iu = User.query.filter_by(email='zed@example.com').first()
        iu.invitation_token_expiry = datetime.utcnow() - timedelta(days=1)
        db.session.commit()
    auth_client.post(f'/person/{pid}/invite', follow_redirects=True)  # resend after expiry
    with app.app_context():
        iu = User.query.filter_by(email='zed@example.com').first()
        assert iu.invitation_token_expiry > datetime.utcnow()   # not blocked, refreshed


def test_invite_refuses_registered_account(app, auth_client):
    with app.app_context():
        pid = _invite_target(app)
    auth_client.post(f'/person/{pid}/invite', follow_redirects=True)
    with app.app_context():
        iu = User.query.filter_by(email='zed@example.com').first()
        iu.status = 'approved'
        db.session.commit()
    r = auth_client.post(f'/person/{pid}/invite', follow_redirects=True)
    assert b'already has an account' in r.data


# ── fix invited email + cancel invite ────────────────────────────────────────

def test_edit_updates_invited_account_email(app, auth_client):
    with app.app_context():
        pid = _invite_target(app, name='Jeff Pease', email='wrong@icloud.com')
    auth_client.post(f'/person/{pid}/invite', follow_redirects=True)
    auth_client.post(f'/person/{pid}/edit',
                     data={'name': 'Jeff Pease', 'gender': 'Male', 'email': 'jnpease@icloud.com'},
                     follow_redirects=True)
    with app.app_context():
        person = db.session.get(Person, pid)
        assert person.email == 'jnpease@icloud.com'
        assert person.user.email == 'jnpease@icloud.com'   # pending account synced


def test_cancel_invite_removes_account_keeps_person(app, auth_client):
    with app.app_context():
        pid = _invite_target(app, name='Jeff Pease', email='jeff@example.com')
    auth_client.post(f'/person/{pid}/invite', follow_redirects=True)
    with app.app_context():
        uid = db.session.get(Person, pid).user.id
    auth_client.post(f'/admin/cancel-invite/{uid}', follow_redirects=True)
    with app.app_context():
        assert db.session.get(User, uid) is None       # pending account gone
        assert db.session.get(Person, pid) is not None  # person kept


def test_cancel_invite_only_targets_invited(app, auth_client):
    with app.app_context():
        admin = User.query.filter_by(email='admin@pease-family.com').first()
        admin_uid = admin.id
    # admin is approved, not invited → refused, still present
    auth_client.post(f'/admin/cancel-invite/{admin_uid}', follow_redirects=True)
    with app.app_context():
        assert db.session.get(User, admin_uid) is not None


def test_invited_edit_page_shows_email_field(app, auth_client):
    """The edit form must render the email field for an invited person (it was
    hidden behind 'managed through login account')."""
    with app.app_context():
        pid = _invite_target(app, name='Jeffrey Pease', email='wrong@icloud.com')
    auth_client.post(f'/person/{pid}/invite', follow_redirects=True)
    html = auth_client.get(f'/person/{pid}/edit').data.decode()
    assert 'name="email"' in html
    assert 'wrong@icloud.com' in html          # pre-filled, editable
    assert 'managed through' not in html        # the blocking note is gone


def test_cancel_invite_with_dependent_notification(app, auth_client):
    """Regression: an invited account can have a notification; cancelling must
    not crash on the NOT NULL notifications.user_id (Sentry 7559130874)."""
    from app.models import Notification
    with app.app_context():
        pid = _invite_target(app, name='Notif Invitee', email='ni@example.com')
    auth_client.post(f'/person/{pid}/invite', follow_redirects=True)
    with app.app_context():
        uid = db.session.get(Person, pid).user.id
        db.session.add(Notification(user_id=uid, event_type='chat_message',
                                    title='New message', body='hi', url='/chat'))
        db.session.commit()
    r = auth_client.post(f'/admin/cancel-invite/{uid}', follow_redirects=True)
    assert r.status_code == 200
    with app.app_context():
        assert db.session.get(User, uid) is None
        assert Notification.query.filter_by(user_id=uid).count() == 0
        assert db.session.get(Person, pid) is not None
