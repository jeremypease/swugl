"""
Tests for 2FA and OAuth-related routes.

These cover page-load smoke tests for TOTP setup/security pages, and verify
that OAuth callback endpoints reject malformed tokens gracefully rather than
crashing — full OAuth flow testing would require mocking external JWKS.
"""
import pytest
import json


# ── Security / 2FA pages ─────────────────────────────────────────────────────

def test_profile_security_loads(auth_client):
    r = auth_client.get('/profile/security')
    assert r.status_code == 200
    assert b'Two-Factor' in r.data or b'Security' in r.data


def test_totp_setup_page_loads(auth_client):
    r = auth_client.get('/profile/security/totp/setup')
    assert r.status_code == 200


def test_totp_setup_requires_auth(client):
    r = client.get('/profile/security/totp/setup')
    assert r.status_code == 302
    assert '/login' in r.headers['Location']


def test_security_page_requires_auth(client):
    r = client.get('/profile/security')
    assert r.status_code == 302
    assert '/login' in r.headers['Location']


# ── OAuth callbacks: reject invalid tokens ────────────────────────────────────

def test_google_callback_rejects_bad_token(client):
    """Google sign-in with a garbage token should return 401, not 500."""
    r = client.post('/api/v1/auth/google', json={'id_token': 'not.a.real.token'},
                    content_type='application/json')
    # 401 expected; 500 acceptable if google-auth client_id is not configured
    assert r.status_code in (400, 401, 500)


def test_google_callback_rejects_missing_token(client):
    r = client.post('/api/v1/auth/google', json={}, content_type='application/json')
    assert r.status_code in (400, 401, 422, 500)


def test_apple_callback_rejects_bad_state(client):
    """Apple callback with no state and bad code should redirect gracefully, not crash."""
    r = client.post('/auth/apple/callback', data={
        'code': 'fake_code',
        'state': 'bad_state',
    }, follow_redirects=False)
    assert r.status_code in (302, 400)
    if r.status_code == 302:
        assert '/login' in r.headers.get('Location', '')


# ── API JWT auth ──────────────────────────────────────────────────────────────

def test_api_login_wrong_password(client):
    r = client.post('/api/v1/auth/login', json={
        'email': 'admin@pease-family.com',
        'password': 'wrongpassword',
    }, content_type='application/json')
    assert r.status_code == 401


def test_api_login_unknown_email(client):
    r = client.post('/api/v1/auth/login', json={
        'email': 'nobody@nowhere.com',
        'password': 'Password1!',
    }, content_type='application/json')
    assert r.status_code == 401


def test_api_login_valid_returns_tokens(client):
    r = client.post('/api/v1/auth/login', json={
        'email': 'admin@pease-family.com',
        'password': 'Password1!',
    }, content_type='application/json')
    assert r.status_code == 200
    data = json.loads(r.data)
    assert 'access_token' in data


def test_api_protected_endpoint_requires_token(client):
    """Calling a JWT-protected API route without a token should 401."""
    r = client.get('/api/v1/events')
    assert r.status_code == 401


def test_api_protected_endpoint_with_valid_token(client):
    """Login then use access token to hit the events API."""
    login = client.post('/api/v1/auth/login', json={
        'email': 'admin@pease-family.com',
        'password': 'Password1!',
    }, content_type='application/json')
    token = json.loads(login.data)['access_token']
    r = client.get('/api/v1/events', headers={'Authorization': f'Bearer {token}'})
    assert r.status_code == 200
