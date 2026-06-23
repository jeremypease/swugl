"""
Gift registries: paid-gated, and the recipient is blocked from seeing their
own registry at every layer (list, detail, add, claim).
"""
from datetime import date
from app import db
from app.models import User, Person, GiftRegistry, GiftRegistryItem, Event


def _admin():
    return User.query.filter_by(email='admin@pease-family.com').first()


def _member(app, email='gifter@pease-family.com'):
    admin = _admin()
    p = Person(name='Gifter Pease', family_id=admin.family_id)
    db.session.add(p); db.session.flush()
    u = User(family_id=admin.family_id, person_id=p.id, first_name='Gifter',
             last_name='Pease', email=email, status='approved',
             email_verified=True, is_admin=False)
    u.set_password('Password1!')
    db.session.add(u); db.session.commit()
    return u.id, p.id


def _registry_for(person_id, creator_id=None, title='Birthday'):
    reg = GiftRegistry(family_id=_admin().family_id, recipient_person_id=person_id,
                       title=title, created_by_id=creator_id)
    db.session.add(reg); db.session.commit()
    return reg.id


def _login(app, email):
    c = app.test_client()
    c.post('/login', data={'email': email, 'password': 'Password1!'})
    return c


# ── paid gate ────────────────────────────────────────────────────────────────

def test_registries_require_paid_plan(app, auth_client):
    with app.app_context():
        _admin().family.plan = 'free'
        db.session.commit()
    r = auth_client.get('/registries', follow_redirects=False)
    assert r.status_code == 302 and '/billing' in r.headers['Location']


def test_create_registry_paid(app, auth_client):
    with app.app_context():
        rec = Person(name='Honoree Pease', family_id=_admin().family_id)
        db.session.add(rec); db.session.commit()
        rec_id = rec.id
    r = auth_client.post('/registries/new',
                         data={'title': "Honoree's Birthday", 'recipient_id': rec_id},
                         follow_redirects=True)
    assert r.status_code == 200
    with app.app_context():
        assert GiftRegistry.query.filter_by(recipient_person_id=rec_id).count() == 1


# ── recipient is blocked ─────────────────────────────────────────────────────

def test_recipient_cannot_view_own_registry(app):
    with app.app_context():
        uid, pid = _member(app, 'honoree@pease-family.com')
        rid = _registry_for(pid)
    client = _login(app, 'honoree@pease-family.com')
    assert client.get(f'/registries/{rid}').status_code == 403


def test_recipient_registry_hidden_from_their_list(app):
    with app.app_context():
        uid, pid = _member(app, 'honoree2@pease-family.com')
        rid = _registry_for(pid)
    client = _login(app, 'honoree2@pease-family.com')
    html = client.get('/registries').data.decode()
    assert 'Birthday' not in html        # their own registry is not listed


def test_recipient_cannot_add_or_claim(app):
    with app.app_context():
        uid, pid = _member(app, 'honoree3@pease-family.com')
        rid = _registry_for(pid)
        db.session.add(GiftRegistryItem(registry_id=rid, name='Watch'))
        db.session.commit()
        item_id = GiftRegistryItem.query.filter_by(registry_id=rid).first().id
    client = _login(app, 'honoree3@pease-family.com')
    assert client.post(f'/registries/{rid}/items', data={'name': 'Sneaky'}).status_code == 403
    assert client.post(f'/registries/items/{item_id}/claim').status_code == 403


# ── non-recipient can use it ─────────────────────────────────────────────────

def test_member_can_view_add_and_claim(app, auth_client):
    with app.app_context():
        rec = Person(name='Kid Honoree', family_id=_admin().family_id)
        db.session.add(rec); db.session.flush()
        rid = _registry_for(rec.id, creator_id=_admin().person_id)
    auth_client.post(f'/registries/{rid}/items', data={'name': 'Bike', 'url': 'https://x.com'},
                     follow_redirects=True)
    with app.app_context():
        item = GiftRegistryItem.query.filter_by(registry_id=rid).first()
        assert item is not None
        item_id = item.id
    auth_client.post(f'/registries/items/{item_id}/claim', follow_redirects=True)
    with app.app_context():
        item = db.session.get(GiftRegistryItem, item_id)
        assert item.claimed_by_person_id == _admin().person_id   # admin claimed it
    # claim again → toggles off
    auth_client.post(f'/registries/items/{item_id}/claim', follow_redirects=True)
    with app.app_context():
        assert db.session.get(GiftRegistryItem, item_id).claimed_by_person_id is None


def test_registry_family_isolation(app, other_auth_client):
    with app.app_context():
        rec = Person(name='Iso Honoree', family_id=_admin().family_id)
        db.session.add(rec); db.session.flush()
        rid = _registry_for(rec.id)
    # other-family admin (paid) can't reach this family's registry
    assert other_auth_client.get(f'/registries/{rid}').status_code == 404
