"""
Activity feed (app/activity.py): derive-on-read aggregation of recent pod
activity, newest first, with photo grouping and family isolation.
"""
from datetime import datetime, timedelta, date
from app import db
from app.models import (User, Person, Announcement, Event, Poll, Album, Photo,
                        StoryPrompt, StoryResponse)
from app.activity import recent_activity


def _fam(email='admin@pease-family.com'):
    return User.query.filter_by(email=email).first().family_id


def _populate(fid, author_person_id):
    base = datetime(2026, 6, 1, 12, 0, 0)
    db.session.add(Announcement(family_id=fid, title='Picnic news', body='x',
                                author_id=author_person_id, created_at=base))
    db.session.add(Event(family_id=fid, name='Summer BBQ', start_date=date(2026, 8, 1),
                         created_at=base + timedelta(hours=1)))
    db.session.add(Poll(family_id=fid, question='Which weekend?',
                        created_by_id=author_person_id, created_at=base + timedelta(hours=2)))
    alb = Album(family_id=fid, name='Reunion 2026', created_at=base + timedelta(hours=3))
    db.session.add(alb); db.session.flush()
    # three photos in one album, same uploader/day → one grouped item
    for i in range(3):
        db.session.add(Photo(album_id=alb.id, family_id=fid, path=f'p{i}.jpg',
                             uploaded_by_id=author_person_id,
                             created_at=base + timedelta(hours=4, minutes=i)))
    prompt = StoryPrompt(family_id=fid, person_id=author_person_id,
                         question='First job?', source='manual')
    db.session.add(prompt); db.session.flush()
    db.session.add(StoryResponse(prompt_id=prompt.id, answer='Paper route',
                                 answered_by_id=author_person_id,
                                 created_at=base + timedelta(hours=5)))
    db.session.commit()
    return alb.id


def test_feed_aggregates_all_sources_newest_first(app):
    with app.app_context():
        fid = _fam()
        pid = User.query.filter_by(email='admin@pease-family.com').first().person_id
        _populate(fid, pid)
        items = recent_activity(fid)
        kinds = [i.kind for i in items]
        for k in ('announcement', 'event', 'poll', 'album', 'photos', 'story'):
            assert k in kinds, f'missing {k}'
        # newest first
        ts = [i.timestamp for i in items]
        assert ts == sorted(ts, reverse=True)
        # the story (latest) leads, the announcement (earliest) trails
        assert items[0].kind == 'story'


def test_photos_are_grouped_into_one_item(app):
    with app.app_context():
        fid = _fam()
        pid = User.query.filter_by(email='admin@pease-family.com').first().person_id
        _populate(fid, pid)
        photo_items = [i for i in recent_activity(fid) if i.kind == 'photos']
        assert len(photo_items) == 1
        assert 'added 3 photos' in photo_items[0].text


def test_feed_is_family_isolated(app):
    with app.app_context():
        mine = _fam('admin@pease-family.com')
        other = _fam('admin@other-family.com')
        other_pid = User.query.filter_by(email='admin@other-family.com').first().person_id
        _populate(other, other_pid)          # populate the OTHER family only
        assert recent_activity(mine) == []   # my feed sees none of it
        assert len(recent_activity(other)) >= 5


def test_activity_route_renders(app, auth_client):
    with app.app_context():
        fid = _fam()
        pid = User.query.filter_by(email='admin@pease-family.com').first().person_id
        _populate(fid, pid)
    r = auth_client.get('/activity')
    assert r.status_code == 200
    assert b'Summer BBQ' in r.data and b'Activity' in r.data


def test_activity_route_requires_login(app, client):
    assert client.get('/activity').status_code == 302
