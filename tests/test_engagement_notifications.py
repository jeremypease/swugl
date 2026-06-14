"""
Tests for engagement notifications (Theme 2): notify_family fan-out and the
poll/card/photo/new-member triggers, including surprise-card recipient exclusion.
"""
import pytest
from datetime import date
from app import db
from app.models import User, Person, Notification, Album
from app.notifications import notify_family


def _add_member(family_id, email, name='Second Member'):
    """Add an approved second user to a family; returns (user_id, person_id)."""
    person = Person(name=name, family_id=family_id)
    db.session.add(person)
    db.session.flush()
    user = User(
        family_id=family_id, person_id=person.id,
        first_name=name.split()[0], last_name=name.split()[-1],
        email=email, status='approved', email_verified=True, is_admin=False,
    )
    user.set_password('Password1!')
    db.session.add(user)
    db.session.commit()
    return user.id, person.id


def _notif_count(user_id, event_type):
    return Notification.query.filter_by(user_id=user_id, event_type=event_type).count()


# ── notify_family fan-out ─────────────────────────────────────────────────────

def test_notify_family_excludes_actor_and_listed(app):
    with app.app_context():
        admin = User.query.filter_by(email='admin@pease-family.com').first()
        fid = admin.family_id
        other_id, _ = _add_member(fid, 'm2@pease-family.com')
        excluded_id, _ = _add_member(fid, 'm3@pease-family.com', name='Excluded Person')

        notify_family(fid, 'new_poll', title='hi',
                      exclude_user_id=admin.id, exclude_user_ids=[excluded_id])

        assert _notif_count(admin.id, 'new_poll') == 0       # actor skipped
        assert _notif_count(excluded_id, 'new_poll') == 0    # explicitly excluded
        assert _notif_count(other_id, 'new_poll') == 1       # gets it


def test_notify_family_does_not_leak_across_families(app):
    with app.app_context():
        pease = User.query.filter_by(email='admin@pease-family.com').first()
        other = User.query.filter_by(email='admin@other-family.com').first()
        notify_family(pease.family_id, 'new_poll', title='pease only')
        assert _notif_count(other.id, 'new_poll') == 0


# ── poll trigger ──────────────────────────────────────────────────────────────

def test_create_poll_notifies_other_members(app, auth_client):
    with app.app_context():
        admin = User.query.filter_by(email='admin@pease-family.com').first()
        other_id, _ = _add_member(admin.family_id, 'pollwatcher@pease-family.com')
        admin_id = admin.id

    auth_client.post('/polls/new', data={
        'question': 'Pizza or tacos?',
        'options': ['Pizza', 'Tacos'],
    })

    with app.app_context():
        assert _notif_count(other_id, 'new_poll') == 1
        assert _notif_count(admin_id, 'new_poll') == 0  # actor


# ── card trigger excludes the surprise recipient ──────────────────────────────

def test_create_card_excludes_recipient(app, auth_client):
    with app.app_context():
        admin = User.query.filter_by(email='admin@pease-family.com').first()
        fid = admin.family_id
        signer_id, _ = _add_member(fid, 'signer@pease-family.com', name='Card Signer')
        recipient_uid, recipient_pid = _add_member(fid, 'birthday@pease-family.com',
                                                   name='Birthday Person')
        admin_id = admin.id

    auth_client.post('/cards/new', data={
        'recipient_id': recipient_pid,
        'occasion': 'birthday',
        'title': 'Happy Birthday!',
    })

    with app.app_context():
        assert _notif_count(signer_id, 'new_card') == 1        # other members notified
        assert _notif_count(recipient_uid, 'new_card') == 0    # surprise: recipient not told
        assert _notif_count(admin_id, 'new_card') == 0         # actor


# ── new-member trigger ────────────────────────────────────────────────────────

def test_approve_member_notifies_family(app, auth_client):
    with app.app_context():
        admin = User.query.filter_by(email='admin@pease-family.com').first()
        fid = admin.family_id
        admin_id = admin.id
        # An existing approved member who should hear about the newcomer
        watcher_id, _ = _add_member(fid, 'watcher@pease-family.com', name='Existing Member')
        # A pending user awaiting approval
        person = Person(name='New Joiner', family_id=fid)
        db.session.add(person)
        db.session.flush()
        pending = User(
            family_id=fid, person_id=person.id, first_name='New', last_name='Joiner',
            email='joiner@pease-family.com', status='pending', email_verified=True,
        )
        pending.set_password('Password1!')
        db.session.add(pending)
        db.session.commit()
        pending_id = pending.id

    auth_client.post(f'/admin/approve/{pending_id}')

    with app.app_context():
        assert _notif_count(watcher_id, 'new_member') == 1
        assert _notif_count(admin_id, 'new_member') == 1     # approver is a member too, not the joiner
        assert _notif_count(pending_id, 'new_member') == 0   # the newcomer doesn't notify themselves
