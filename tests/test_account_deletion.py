import io
import pytest
from PIL import Image
from app.models import Family, User, Person, ChatMessage
from app import db


def _make_member(app, family_id, email, is_admin=False):
    """Add a second user to a family."""
    person = Person(name='Second Member', family_id=family_id)
    db.session.add(person)
    db.session.flush()
    user = User(
        family_id=family_id,
        person_id=person.id,
        first_name='Second',
        last_name='Member',
        email=email,
        status='approved',
        email_verified=True,
        is_admin=is_admin,
    )
    user.set_password('Password1!')
    db.session.add(user)
    db.session.commit()
    return user.id


# ── route guard ───────────────────────────────────────────────────────────────

def test_delete_requires_confirmation_text(app, auth_client):
    rv = auth_client.post('/profile/delete-account', data={'confirm': 'nope'},
                          follow_redirects=False)
    assert rv.status_code == 302
    with app.app_context():
        assert User.query.filter_by(email='admin@pease-family.com').first() is not None


def test_last_admin_with_other_members_blocked(app, auth_client):
    with app.app_context():
        admin = User.query.filter_by(email='admin@pease-family.com').first()
        _make_member(app, admin.family_id, 'member@pease-family.com', is_admin=False)
    rv = auth_client.post('/profile/delete-account', data={'confirm': 'DELETE'},
                          follow_redirects=False)
    assert rv.status_code == 302
    with app.app_context():
        # Still exists — blocked because no other admin
        assert User.query.filter_by(email='admin@pease-family.com').first() is not None


def test_member_delete_keeps_family_and_person(app):
    # Single test client per test — a second client in the same test doesn't
    # persist its login cookie under Werkzeug 3.x (see test_chat.py history).
    with app.app_context():
        admin = User.query.filter_by(email='admin@pease-family.com').first()
        family_id = admin.family_id
        member_id = _make_member(app, family_id, 'member@pease-family.com', is_admin=True)
        member = db.session.get(User, member_id)
        person_id = member.person_id
        # member authored a chat message — must be deleted with the account
        db.session.add(ChatMessage(family_id=family_id, author_id=member_id, body='bye'))
        db.session.commit()

    # Log in as the second member and delete their own account
    client = app.test_client()
    client.post('/login', data={'email': 'member@pease-family.com', 'password': 'Password1!'})
    rv = client.post('/profile/delete-account', data={'confirm': 'DELETE'},
                     follow_redirects=False)
    assert rv.status_code == 302

    with app.app_context():
        assert db.session.get(User, member_id) is None
        # Person stays in the tree; family untouched
        assert db.session.get(Person, person_id) is not None
        assert db.session.get(Family, family_id) is not None
        assert ChatMessage.query.filter_by(author_id=member_id).count() == 0


def test_sole_user_delete_purges_family(app, auth_client):
    with app.app_context():
        admin = User.query.filter_by(email='admin@pease-family.com').first()
        family_id = admin.family_id
        person_count = Person.query.filter_by(family_id=family_id).count()
        assert person_count > 0
    rv = auth_client.post('/profile/delete-account', data={'confirm': 'DELETE'},
                          follow_redirects=False)
    assert rv.status_code == 302
    with app.app_context():
        assert db.session.get(Family, family_id) is None
        assert User.query.filter_by(email='admin@pease-family.com').first() is None
        assert Person.query.filter_by(family_id=family_id).count() == 0
        # Other family untouched
        assert User.query.filter_by(email='admin@other-family.com').first() is not None


# ── image pipeline (#36/#38) ──────────────────────────────────────────────────

def _jpeg_with_exif(px=3000):
    """An oversized JPEG carrying a GPS EXIF tag."""
    from werkzeug.datastructures import FileStorage
    img = Image.new('RGB', (px, px // 2), 'red')
    exif = Image.Exif()
    exif[0x010F] = 'TestCam'  # Make tag — enough to assert stripping
    buf = io.BytesIO()
    img.save(buf, format='JPEG', exif=exif)
    buf.seek(0)
    return FileStorage(stream=buf, filename='test.jpg')


def test_upload_photo_strips_exif_resizes_and_thumbs(app):
    from app.storage import upload_photo, DISPLAY_MAX_PX, THUMB_MAX_PX
    import os
    with app.app_context():
        result = upload_photo(_jpeg_with_exif(), folder='test', with_thumb=True)
        assert result is not None
        key, thumb_key = result
        assert thumb_key is not None
        root = app.root_path
        display = Image.open(os.path.join(root, 'static', key))
        assert max(display.size) <= DISPLAY_MAX_PX
        assert not display.getexif()
        thumb = Image.open(os.path.join(root, 'static', thumb_key))
        assert max(thumb.size) <= THUMB_MAX_PX
        for k in (key, thumb_key):
            os.remove(os.path.join(root, 'static', k))


def test_upload_photo_rejects_oversize(app):
    from app import storage
    from werkzeug.datastructures import FileStorage
    with app.app_context():
        f = FileStorage(stream=io.BytesIO(b'x' * (storage.MAX_PHOTO_BYTES + 1)),
                        filename='big.jpg')
        assert storage.upload_photo(f, folder='test') is None


# ── plan gating (#39) ─────────────────────────────────────────────────────────

def test_spouse_invite_blocked_at_free_member_limit(app, auth_client):
    from app.billing import FREE_MEMBER_LIMIT
    with app.app_context():
        admin = User.query.filter_by(email='admin@pease-family.com').first()
        admin.family.plan = 'free'
        fid = admin.family_id
        for i in range(FREE_MEMBER_LIMIT):
            db.session.add(Person(name=f'Filler Person{i}', family_id=fid))
        db.session.commit()
    rv = auth_client.post('/spouse/invite', data={
        'first_name': 'New', 'last_name': 'Spouse',
        'email': 'newspouse@example.com',
    }, follow_redirects=False)
    assert rv.status_code == 302
    assert '/billing' in rv.headers['Location']
    with app.app_context():
        assert User.query.filter_by(email='newspouse@example.com').first() is None
