"""
Tests for presigned R2 photo URLs (#40).

Photos must never be served from a permanent public URL. R2-backed keys get a
short-lived signed URL; local-dev uploads use the static path.
"""
import json
import pytest
from app import storage
from app.storage import photo_url
from app.api.utils import serialize_person
from app.models import User


@pytest.fixture()
def r2_app(app):
    """App context with fake R2 credentials so signing runs (offline)."""
    with app.app_context():
        app.config['R2_ACCOUNT_ID'] = 'testacct'
        app.config['R2_ACCESS_KEY_ID'] = 'AKIAtest'
        app.config['R2_SECRET_ACCESS_KEY'] = 'secrettest'
        app.config['R2_BUCKET_NAME'] = 'testbucket'
        storage._signed_url_cache.clear()
        yield app
        storage._signed_url_cache.clear()


def test_local_upload_uses_static_path(app):
    with app.test_request_context():
        url = photo_url('uploads/photos/abc.jpg')
        assert '/static/uploads/photos/abc.jpg' in url


def test_none_key_returns_none(app):
    with app.app_context():
        assert photo_url(None) is None


def test_r2_key_returns_signed_url(r2_app):
    url = photo_url('photos/abc.jpg')
    assert url.startswith('https://testacct.r2.cloudflarestorage.com/testbucket/photos/abc.jpg')
    assert 'X-Amz-Signature=' in url
    assert 'X-Amz-Expires=' in url


def test_signed_url_is_public_url_free(r2_app):
    # The legacy permanent public URL must not leak through
    r2_app.config['R2_PUBLIC_URL'] = 'https://pub.example.com'
    url = photo_url('photos/abc.jpg')
    assert 'pub.example.com' not in url
    assert 'X-Amz-Signature=' in url


def test_signed_url_stable_within_window(r2_app):
    # Two renders of the same key return an identical URL so the browser can
    # cache the image instead of re-downloading on every page load.
    assert photo_url('photos/abc.jpg') == photo_url('photos/abc.jpg')


def test_signed_url_honors_custom_ttl(r2_app):
    r2_app.config['R2_SIGNED_URL_TTL'] = 900
    storage._signed_url_cache.clear()
    url = photo_url('photos/abc.jpg')
    assert 'X-Amz-Expires=900' in url


def test_serialize_person_includes_signed_photo_url(r2_app):
    person = User.query.filter_by(email='admin@pease-family.com').first().person
    person.photo_path = 'photos/face.jpg'
    data = serialize_person(person)
    assert data['photo_path'] == 'photos/face.jpg'
    assert data['photo_url'].startswith('https://testacct.r2.cloudflarestorage.com')
    assert 'X-Amz-Signature=' in data['photo_url']


def test_serialize_person_no_photo(app):
    with app.app_context():
        person = User.query.filter_by(email='admin@pease-family.com').first().person
        person.photo_path = None
        data = serialize_person(person)
        assert data['photo_url'] is None


def test_members_api_endpoint_serializes(client):
    """End-to-end: /api/v1/members must not 500 on the Person serializer."""
    login = client.post('/api/v1/auth/login', json={
        'email': 'admin@pease-family.com', 'password': 'Password1!',
    }, content_type='application/json')
    token = json.loads(login.data)['access_token']
    r = client.get('/api/v1/members', headers={'Authorization': f'Bearer {token}'})
    assert r.status_code == 200
    members = json.loads(r.data)['members']
    assert len(members) >= 1
    m = members[0]
    assert 'name' in m and 'first_name' in m and 'photo_url' in m
