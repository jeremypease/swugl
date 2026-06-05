"""
Tests for notification preferences: model helpers, seeding, and route behavior.
"""
import pytest
from app import db
from app.models import User, NotificationPreference, NOTIFICATION_EVENTS


# ── seed_defaults ─────────────────────────────────────────────────────────────

def test_seed_defaults_creates_rows(app):
    with app.app_context():
        user = User.query.filter_by(email='admin@pease-family.com').first()
        NotificationPreference.seed_defaults(user.id)
        db.session.commit()
        prefs = NotificationPreference.query.filter_by(user_id=user.id).all()
        # At least one row per event type
        event_types = {p.event_type for p in prefs}
        assert event_types >= set(NOTIFICATION_EVENTS.keys())


def test_seed_defaults_idempotent(app):
    with app.app_context():
        user = User.query.filter_by(email='admin@pease-family.com').first()
        NotificationPreference.seed_defaults(user.id)
        NotificationPreference.seed_defaults(user.id)  # second call should not duplicate
        db.session.commit()
        count_after = NotificationPreference.query.filter_by(user_id=user.id).count()
        NotificationPreference.seed_defaults(user.id)
        db.session.commit()
        count_third = NotificationPreference.query.filter_by(user_id=user.id).count()
        assert count_after == count_third


# ── is_enabled ────────────────────────────────────────────────────────────────

def test_is_enabled_returns_db_value(app):
    with app.app_context():
        user = User.query.filter_by(email='admin@pease-family.com').first()
        # Explicitly disable digest
        existing = NotificationPreference.query.filter_by(
            user_id=user.id, event_type='digest', channel='email'
        ).first()
        if existing:
            existing.enabled = False
        else:
            db.session.add(NotificationPreference(
                user_id=user.id, event_type='digest', channel='email', enabled=False
            ))
        db.session.commit()
        assert NotificationPreference.is_enabled(user.id, 'digest', 'email') is False


def test_is_enabled_falls_back_to_default(app):
    with app.app_context():
        user = User.query.filter_by(email='admin@pease-family.com').first()
        # Remove any existing pref for new_member so we test the fallback
        NotificationPreference.query.filter_by(
            user_id=user.id, event_type='new_member', channel='email'
        ).delete()
        db.session.commit()
        # default for new_member is False
        result = NotificationPreference.is_enabled(user.id, 'new_member', 'email')
        assert result == NOTIFICATION_EVENTS['new_member']['default']


def test_is_enabled_unknown_event_returns_false(app):
    with app.app_context():
        user = User.query.filter_by(email='admin@pease-family.com').first()
        assert NotificationPreference.is_enabled(user.id, 'nonexistent_event') is False


# ── /profile/notifications route ─────────────────────────────────────────────

def test_notification_prefs_page_loads(auth_client):
    r = auth_client.get('/profile/notifications')
    assert r.status_code == 200
    assert b'Notification' in r.data


def test_notification_prefs_redirects_when_unauthenticated(client):
    r = client.get('/profile/notifications')
    assert r.status_code == 302
    assert '/login' in r.headers['Location']


def test_notification_prefs_save(app, auth_client):
    # POST with only digest_email checked (all others unchecked)
    r = auth_client.post('/profile/notifications', data={
        'digest_email': 'on',
    }, follow_redirects=True)
    assert r.status_code == 200

    with app.app_context():
        user = User.query.filter_by(email='admin@pease-family.com').first()
        # digest email should be enabled
        assert NotificationPreference.is_enabled(user.id, 'digest', 'email') is True
        # new_event email should be disabled (not submitted)
        assert NotificationPreference.is_enabled(user.id, 'new_event', 'email') is False


def test_notification_prefs_toggle(app, auth_client):
    # Enable all, then disable all
    all_fields = {}
    for event_type, meta in NOTIFICATION_EVENTS.items():
        all_fields[f'{event_type}_email'] = 'on'
        if meta.get('in_app'):
            all_fields[f'{event_type}_in_app'] = 'on'

    auth_client.post('/profile/notifications', data=all_fields)

    with app.app_context():
        user = User.query.filter_by(email='admin@pease-family.com').first()
        for event_type in NOTIFICATION_EVENTS:
            assert NotificationPreference.is_enabled(user.id, event_type, 'email') is True

    # Now disable all
    auth_client.post('/profile/notifications', data={})

    with app.app_context():
        user = User.query.filter_by(email='admin@pease-family.com').first()
        for event_type in NOTIFICATION_EVENTS:
            assert NotificationPreference.is_enabled(user.id, event_type, 'email') is False
