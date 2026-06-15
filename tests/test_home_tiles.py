"""
Tests for the /home launcher tile badge counts (backend data for #55/#43).
"""
from datetime import date, datetime, timedelta
from flask_login import login_user
from app import db
from app.routes import _home_tile_badges
from app.models import (User, Person, Event, Poll, PollOption, GreetingCard,
                        StoryPrompt, Album, Photo, Announcement)


def _badges_for(app, email='admin@pease-family.com', prev_seen=None, today=None):
    with app.test_request_context():
        user = User.query.filter_by(email=email).first()
        login_user(user)
        return _home_tile_badges(today or date.today(), prev_seen)


def test_home_renders_with_badges(auth_client):
    # The route must compute badges without error.
    assert auth_client.get('/home').status_code == 200


def test_badges_count_actionable_items(app):
    with app.app_context():
        admin = User.query.filter_by(email='admin@pease-family.com').first()
        fid = admin.family_id
        other = Person(name='Other Member', family_id=fid)
        db.session.add(other)
        db.session.flush()
        # upcoming event
        db.session.add(Event(family_id=fid, name='Reunion', start_date=date.today() + timedelta(days=10)))
        # open poll the admin hasn't voted in
        poll = Poll(family_id=fid, question='Pizza?')
        db.session.add(poll); db.session.flush()
        db.session.add_all([PollOption(poll_id=poll.id, label='Yes'),
                            PollOption(poll_id=poll.id, label='No')])
        # unsent card for someone else, admin hasn't signed
        db.session.add(GreetingCard(family_id=fid, recipient_id=other.id,
                                    occasion='birthday', title='Happy Bday'))
        # open story prompt
        db.session.add(StoryPrompt(family_id=fid, person_id=other.id,
                                   question='Tell us a story', source='manual'))
        # recent photo
        alb = Album(family_id=fid, name='A'); db.session.add(alb); db.session.flush()
        db.session.add(Photo(album_id=alb.id, family_id=fid, path='p.jpg'))
        db.session.commit()

        b = _badges_for(app)
        assert b['events'] == 1
        assert b['polls'] == 1
        assert b['cards'] == 1
        assert b['stories'] == 1   # admin (organizer) sees all open prompts
        assert b['photos'] == 1


def test_announcements_badge_uses_last_visit(app):
    with app.app_context():
        admin = User.query.filter_by(email='admin@pease-family.com').first()
        db.session.add(Announcement(family_id=admin.family_id, title='News',
                                    body='hi', author_id=admin.id))
        db.session.commit()
        # First-ever visit (prev_seen None) shows nothing new
        assert _badges_for(app, prev_seen=None)['announcements'] == 0
        # With a past last-visit, the new announcement counts
        b = _badges_for(app, prev_seen=datetime.utcnow() - timedelta(days=1))
        assert b['announcements'] == 1


def test_badges_respect_feature_toggles(app):
    with app.app_context():
        fam = User.query.filter_by(email='admin@pease-family.com').first().family
        fam.enable_polls = False
        fam.enable_stories = False
        db.session.commit()
        b = _badges_for(app)
        assert b['polls'] == 0
        assert b['stories'] == 0
