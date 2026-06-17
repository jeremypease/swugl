"""
Tests for merging duplicate Person records (app/people_merge.py).

The headline guarantee: after a merge, NO column anywhere that references
people.id still points at the removed person — proven generically against the
schema, so it stays true as new person-FK tables are added.
"""
import pytest
from datetime import date
from app import db
from app.people_merge import merge_person_records, _person_fk_columns
from app.models import (
    User, Person, Family, Event, EventRSVP, Announcement, Poll, Album, Photo,
    Document, Checklist, StoryPrompt, EventComment, ParentRelationship, GreetingCard,
)


def _fam_id():
    return User.query.filter_by(email='admin@pease-family.com').first().family_id


def _populate(person, fid):
    """Give `person` a row in a spread of person-FK tables (attribution + dedup)."""
    db.session.add(Announcement(family_id=fid, title='By dup', body='x', author_id=person.id))
    db.session.add(Poll(family_id=fid, question='Dup poll?', created_by_id=person.id))
    alb = Album(family_id=fid, name='Dup album', created_by_id=person.id)
    db.session.add(alb); db.session.flush()
    db.session.add(Photo(album_id=alb.id, family_id=fid, path='d.jpg', uploaded_by_id=person.id))
    db.session.add(Document(family_id=fid, title='Dup doc', storage_key='k',
                            original_filename='d.pdf', file_type='pdf', uploader_id=person.id))
    db.session.add(Checklist(family_id=fid, title='Dup list', created_by_id=person.id))
    db.session.add(StoryPrompt(family_id=fid, person_id=person.id, question='Q', source='manual'))
    ev = Event(family_id=fid, name='Dup event', start_date=date(2026, 8, 1))
    db.session.add(ev); db.session.flush()
    db.session.add(EventComment(event_id=ev.id, person_id=person.id, body='hi'))
    db.session.add(EventRSVP(event_id=ev.id, person_id=person.id, status='yes'))
    db.session.commit()
    return ev.id


def test_merge_transfers_account_and_content_then_deletes(app):
    with app.app_context():
        fid = _fam_id()
        keep = Person(name='Jeremy Pease', family_id=fid)
        remove = Person(name='Jeremy Pease', family_id=fid)
        db.session.add_all([keep, remove]); db.session.flush()
        # give the duplicate its own login account
        u = User(family_id=fid, person_id=remove.id, first_name='Jeremy', last_name='Pease',
                 email='dup@pease-family.com', status='approved', email_verified=True)
        u.set_password('Password1!'); db.session.add(u)
        _populate(remove, fid)
        keep_id, remove_id, uid = keep.id, remove.id, u.id

        log = merge_person_records(keep, remove)
        db.session.commit()

        assert db.session.get(Person, remove_id) is None        # duplicate gone
        assert db.session.get(User, uid).person_id == keep_id    # account moved
        assert Announcement.query.filter_by(author_id=keep_id).count() == 1
        assert Poll.query.filter_by(created_by_id=keep_id).count() == 1
        assert Photo.query.filter_by(uploaded_by_id=keep_id).count() == 1
        assert StoryPrompt.query.filter_by(person_id=keep_id).count() == 1
        assert EventComment.query.filter_by(person_id=keep_id).count() == 1
        assert any('account' in line for line in log)


def test_no_person_reference_dangles_after_merge(app):
    """Completeness guarantee, checked against the live schema."""
    with app.app_context():
        fid = _fam_id()
        keep = Person(name='Keep', family_id=fid)
        remove = Person(name='Remove', family_id=fid)
        db.session.add_all([keep, remove]); db.session.flush()
        _populate(remove, fid)
        remove_id = remove.id

        merge_person_records(keep, remove)
        db.session.commit()

        for table, col in _person_fk_columns():
            n = db.session.execute(
                db.text(f'SELECT COUNT(*) FROM {table} WHERE {col} = :r'), {'r': remove_id}
            ).scalar()
            assert n == 0, f'{table}.{col} still references the removed person'


def test_merge_dedups_shared_rows(app):
    with app.app_context():
        fid = _fam_id()
        keep = Person(name='K', family_id=fid); remove = Person(name='R', family_id=fid)
        db.session.add_all([keep, remove]); db.session.flush()
        ev = Event(family_id=fid, name='E', start_date=date(2026, 8, 1))
        db.session.add(ev); db.session.flush()
        # BOTH RSVP'd the same event — must collapse to one, no error
        db.session.add_all([EventRSVP(event_id=ev.id, person_id=keep.id, status='yes'),
                            EventRSVP(event_id=ev.id, person_id=remove.id, status='no')])
        db.session.commit()
        eid, keep_id = ev.id, keep.id

        merge_person_records(keep, remove)
        db.session.commit()
        assert EventRSVP.query.filter_by(event_id=eid).count() == 1
        assert EventRSVP.query.filter_by(event_id=eid, person_id=keep_id).count() == 1


def test_merge_rejects_self_and_cross_family(app):
    with app.app_context():
        fid = _fam_id()
        p = Person(name='Solo', family_id=fid); db.session.add(p); db.session.flush()
        with pytest.raises(ValueError):
            merge_person_records(p, p)
        other_fid = User.query.filter_by(email='admin@other-family.com').first().family_id
        op = Person(name='Other', family_id=other_fid); db.session.add(op); db.session.flush()
        with pytest.raises(ValueError):
            merge_person_records(p, op)


# ── admin web tool ────────────────────────────────────────────────────────────

def _two_jeremys(app):
    fid = _fam_id()
    keep = Person(name='Jeremy Pease', family_id=fid)
    dup = Person(name='Jeremy Pease', family_id=fid)
    db.session.add_all([keep, dup]); db.session.flush()
    db.session.add(Announcement(family_id=fid, title='By dup', body='x', author_id=dup.id))
    db.session.commit()
    return keep.id, dup.id


def test_merge_tool_preview_does_not_delete(app, auth_client):
    with app.app_context():
        kid, did = _two_jeremys(app)
    r = auth_client.post('/admin/merge-people', data={'keep_id': kid, 'remove_id': did})
    assert r.status_code == 200
    assert b'what will happen' in r.data.lower()
    with app.app_context():
        assert db.session.get(Person, did) is not None  # preview only — still there


def test_merge_tool_confirm_merges(app, auth_client):
    with app.app_context():
        kid, did = _two_jeremys(app)
    auth_client.post('/admin/merge-people',
                     data={'keep_id': kid, 'remove_id': did, 'confirm': '1'},
                     follow_redirects=True)
    with app.app_context():
        assert db.session.get(Person, did) is None
        assert Announcement.query.filter_by(author_id=kid).count() == 1


def test_merge_tool_requires_admin(app, client):
    # logged-out → redirected to login
    assert client.get('/admin/merge-people').status_code == 302


def test_merge_tool_rejects_same_person(app, auth_client):
    with app.app_context():
        kid, _ = _two_jeremys(app)
    r = auth_client.post('/admin/merge-people',
                         data={'keep_id': kid, 'remove_id': kid}, follow_redirects=True)
    assert b'different people' in r.data
