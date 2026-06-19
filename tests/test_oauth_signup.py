"""
OAuth account creation — two flows:

  A. New self-serve user creates their own circle via Apple/Google
     (gated by REGISTRATION_OPEN, finished on /auth/complete-signup).
  B. An invited user activates their pending account via Apple/Google,
     authorized by the invite token (no email match required).
"""
from datetime import datetime, timedelta
from app import db
from app.models import (User, Person, Family, OAuthAccount, UserPodMembership,
                        NotificationPreference)
from app.oauth import (_create_pod_from_oauth, _lookup_invited_user,
                       _activate_invited_via_oauth)
from app.routes import finalize_invited_activation


# ── A. new-pod creation ──────────────────────────────────────────────────────

def test_create_pod_from_oauth_builds_admin_circle(app):
    with app.app_context():
        user = _create_pod_from_oauth(
            'google', 'g-sub-1', 'founder@new.com', 'New', 'Founder', 'The Founder Family')
        db.session.refresh(user)
        assert user.is_admin and user.status == 'approved' and user.email_verified
        assert user.password_hash == ''                      # OAuth-only account
        fam = db.session.get(Family, user.family_id)
        assert fam.name == 'The Founder Family' and fam.plan == 'trial'
        assert fam.trial_ends_at is not None                 # trial started immediately
        assert db.session.get(Person, user.person_id).email == 'founder@new.com'
        assert OAuthAccount.query.filter_by(
            provider='google', provider_user_id='g-sub-1', user_id=user.id).count() == 1
        assert UserPodMembership.query.filter_by(
            user_id=user.id, family_id=user.family_id).count() == 1
        assert NotificationPreference.query.filter_by(user_id=user.id).count() > 0


def test_complete_signup_gated_when_registration_closed(app, client):
    app.config['REGISTRATION_OPEN'] = False
    with client.session_transaction() as sess:
        sess['pending_oauth'] = {'provider': 'google', 'provider_id': 'g-2',
                                 'email': 'x@new.com', 'first_name': 'X', 'last_name': 'Y'}
    r = client.get('/auth/complete-signup')
    assert r.status_code == 403            # registration_closed page
    with app.app_context():
        assert User.query.filter_by(email='x@new.com').first() is None


def test_complete_signup_creates_account_when_open(app, client):
    app.config['REGISTRATION_OPEN'] = True
    with client.session_transaction() as sess:
        sess['pending_oauth'] = {'provider': 'google', 'provider_id': 'g-3',
                                 'email': 'opener@new.com', 'first_name': 'Op', 'last_name': 'Ener'}
    r = client.post('/auth/complete-signup',
                    data={'family_name': 'Opener Clan', 'first_name': 'Op', 'last_name': 'Ener'})
    assert r.status_code == 302 and '/home' in r.headers['Location']
    with app.app_context():
        u = User.query.filter_by(email='opener@new.com').first()
        assert u is not None and u.is_admin and u.family.name == 'Opener Clan'


def test_complete_signup_without_pending_redirects_to_login(app, client):
    app.config['REGISTRATION_OPEN'] = True
    r = client.get('/auth/complete-signup')
    assert r.status_code == 302 and '/login' in r.headers['Location']


# ── B. invited activation ────────────────────────────────────────────────────

def _invited(app, email='cousin@fam.com', token='rawtok123'):
    from app.routes import _hash_token
    admin = User.query.filter_by(email='admin@pease-family.com').first()
    p = Person(name='Cousin Vee', family_id=admin.family_id, email=email)
    db.session.add(p); db.session.flush()
    u = User(family_id=admin.family_id, person_id=p.id, first_name='Cousin',
             last_name='Vee', email=email, status='invited', password_hash='',
             invitation_token=_hash_token(token),
             invitation_token_expiry=datetime.utcnow() + timedelta(days=7))
    db.session.add(u); db.session.commit()
    return u.id, token


def test_lookup_invited_user_honors_token_and_expiry(app):
    with app.app_context():
        uid, token = _invited(app)
        assert _lookup_invited_user(token).id == uid
        assert _lookup_invited_user('wrong-token') is None
        # expire it → no longer resolvable
        u = db.session.get(User, uid)
        u.invitation_token_expiry = datetime.utcnow() - timedelta(days=1)
        db.session.commit()
        assert _lookup_invited_user(token) is None


def test_finalize_invited_activation_approves(app):
    with app.app_context():
        uid, _ = _invited(app)
        with app.test_request_context():
            status = finalize_invited_activation(db.session.get(User, uid))
        assert status == 'approved'
        u = db.session.get(User, uid)
        assert u.status == 'approved' and u.email_verified
        assert u.invitation_token is None
        assert UserPodMembership.query.filter_by(user_id=uid, family_id=u.family_id).count() == 1


def test_activate_invited_via_oauth_links_and_approves(app):
    """Token is the authorization — a mismatched OAuth email still activates."""
    with app.app_context():
        uid, _ = _invited(app, email='invited@fam.com')
        with app.test_request_context():
            _activate_invited_via_oauth(db.session.get(User, uid), 'apple', 'apple-sub-9')
        u = db.session.get(User, uid)
        assert u.status == 'approved' and u.email_verified
        assert OAuthAccount.query.filter_by(
            provider='apple', provider_user_id='apple-sub-9', user_id=uid).count() == 1


def test_activate_invited_rejects_oauth_linked_elsewhere(app):
    """An Apple/Google identity already tied to another user can't claim an invite."""
    with app.app_context():
        other = User.query.filter_by(email='admin@pease-family.com').first()
        db.session.add(OAuthAccount(user_id=other.id, provider='apple', provider_user_id='taken-sub'))
        db.session.commit()
        uid, _ = _invited(app, email='invited2@fam.com')
        with app.test_request_context():
            _activate_invited_via_oauth(db.session.get(User, uid), 'apple', 'taken-sub')
        u = db.session.get(User, uid)
        assert u.status == 'invited'    # untouched — activation refused
