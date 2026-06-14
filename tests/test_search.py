"""
Tests for multi-entity search (Theme 3): matches across people, events,
announcements, albums/photos, and documents, scoped to the family.
"""
import pytest
from datetime import date, datetime
from app import db
from app.models import User, Person, Event, Announcement, Album, Photo, Document


@pytest.fixture()
def seeded_content(app):
    """Seed searchable content in the Pease family and the Other family."""
    with app.app_context():
        pease = User.query.filter_by(email='admin@pease-family.com').first()
        other = User.query.filter_by(email='admin@other-family.com').first()
        pf, of = pease.family_id, other.family_id

        db.session.add(Person(name='Zelda Searchable', family_id=pf))
        db.session.add(Event(name='Searchable Reunion', family_id=pf, start_date=date(2026, 7, 1)))
        db.session.add(Announcement(family_id=pf, title='Searchable News', body='hi',
                                    author_id=pease.id))
        alb = Album(family_id=pf, name='Searchable Album')
        db.session.add(alb)
        db.session.add(Document(family_id=pf, title='Searchable Doc', storage_key='k',
                                original_filename='searchable.pdf', file_type='pdf'))
        # Other family content that must NOT leak
        db.session.add(Event(name='Searchable Secret', family_id=of, start_date=date(2026, 8, 1)))
        db.session.commit()


def test_search_empty_query(auth_client):
    r = auth_client.get('/search')
    assert r.status_code == 200
    assert b'Search across' in r.data


def test_search_matches_all_types(auth_client, seeded_content):
    html = auth_client.get('/search?q=Searchable').data.decode()
    assert 'Zelda Searchable' in html        # person
    assert 'Searchable Reunion' in html      # event
    assert 'Searchable News' in html         # announcement
    assert 'Searchable Album' in html        # album
    assert 'Searchable Doc' in html          # document


def test_search_section_headers(auth_client, seeded_content):
    html = auth_client.get('/search?q=Searchable').data.decode()
    for header in ('People', 'Events', 'Photos', 'Announcements', 'Documents'):
        assert '>%s<' % header in html


def test_search_does_not_leak_across_families(auth_client, seeded_content):
    # The Other family's "Searchable Secret" event must not appear for Pease
    html = auth_client.get('/search?q=Searchable').data.decode()
    assert 'Searchable Secret' not in html


def test_search_no_match(auth_client, seeded_content):
    html = auth_client.get('/search?q=zzzznomatch').data.decode()
    assert 'Nothing found' in html


def test_search_album_by_photo_caption(app, auth_client):
    with app.app_context():
        pease = User.query.filter_by(email='admin@pease-family.com').first()
        alb = Album(family_id=pease.family_id, name='Holiday')
        db.session.add(alb)
        db.session.flush()
        db.session.add(Photo(album_id=alb.id, family_id=pease.family_id,
                             path='p.jpg', caption='uniquecaption sunset'))
        db.session.commit()
    html = auth_client.get('/search?q=uniquecaption').data.decode()
    assert 'Holiday' in html  # surfaced as the album the photo lives in
