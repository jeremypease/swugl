"""
Tests for Family Stories (#35): prompt creation, self + proxy answering,
the new_story family notification, paid gating, and isolation.
"""
import pytest
from datetime import date
from app import db
import app.ai as ai_module
from app.models import Family, User, Person, StoryPrompt, StoryResponse, Notification


@pytest.fixture(autouse=True)
def _stub_ai(monkeypatch):
    """No API key in tests — stub the prompt generator to a fixed question."""
    monkeypatch.setattr(ai_module, 'generate_story_prompt',
                        lambda person, recent_questions=None: 'What was your first job like?')


def _add_member(family_id, email, name='Second Member'):
    person = Person(name=name, family_id=family_id)
    db.session.add(person); db.session.flush()
    user = User(family_id=family_id, person_id=person.id, first_name=name.split()[0],
                last_name=name.split()[-1], email=email, status='approved',
                email_verified=True, is_admin=False)
    user.set_password('Password1!')
    db.session.add(user); db.session.commit()
    return user.id, person.id


def _account_less_person(family_id, name='Grandma Rose'):
    p = Person(name=name, family_id=family_id, birthday=date(1940, 5, 1))
    db.session.add(p); db.session.commit()
    return p.id


# ── prompt creation ───────────────────────────────────────────────────────────

def test_new_prompt_creates_story_prompt(app, auth_client):
    with app.app_context():
        admin = User.query.filter_by(email='admin@pease-family.com').first()
        gid = _account_less_person(admin.family_id)
    auth_client.post(f'/stories/person/{gid}/new-prompt', follow_redirects=True)
    with app.app_context():
        pr = StoryPrompt.query.filter_by(person_id=gid).first()
        assert pr is not None
        assert pr.question == 'What was your first job like?'
        assert pr.source == 'manual'
        assert Person.query.get(gid).stories_enabled is True


# ── answering: self + proxy ───────────────────────────────────────────────────

def test_proxy_answer_records_answered_by(app, auth_client):
    with app.app_context():
        admin = User.query.filter_by(email='admin@pease-family.com').first()
        admin_pid = admin.person_id
        gid = _account_less_person(admin.family_id)
    auth_client.post(f'/stories/person/{gid}/new-prompt', follow_redirects=True)
    with app.app_context():
        pid = StoryPrompt.query.filter_by(person_id=gid).first().id
    auth_client.post(f'/stories/{pid}/answer', data={'answer': 'Picking apples.'},
                     follow_redirects=True)
    with app.app_context():
        pr = db.session.get(StoryPrompt, pid)
        assert pr.answered_at is not None
        assert pr.response.answer == 'Picking apples.'
        assert pr.response.answered_by_id == admin_pid   # proxy attribution


def test_answer_notifies_family_not_author(app, auth_client):
    with app.app_context():
        admin = User.query.filter_by(email='admin@pease-family.com').first()
        admin_id = admin.id
        watcher_id, _ = _add_member(admin.family_id, 'sw@pease-family.com')
        gid = _account_less_person(admin.family_id)
    auth_client.post(f'/stories/person/{gid}/new-prompt', follow_redirects=True)
    with app.app_context():
        pid = StoryPrompt.query.filter_by(person_id=gid).first().id
    auth_client.post(f'/stories/{pid}/answer', data={'answer': 'A good story.'},
                     follow_redirects=True)
    with app.app_context():
        assert Notification.query.filter_by(user_id=watcher_id, event_type='new_story').count() == 1
        assert Notification.query.filter_by(user_id=admin_id, event_type='new_story').count() == 0  # author


# ── paid gating ───────────────────────────────────────────────────────────────

def test_free_family_sees_upgrade(app, auth_client):
    with app.app_context():
        fam = User.query.filter_by(email='admin@pease-family.com').first().family
        fam.plan = 'free'
        db.session.commit()
    html = auth_client.get('/stories').data.decode()
    assert 'Upgrade to unlock Family Stories' in html
    assert 'Recent stories' not in html


def test_free_family_cannot_create_prompt(app, auth_client):
    with app.app_context():
        admin = User.query.filter_by(email='admin@pease-family.com').first()
        admin.family.plan = 'free'
        gid = _account_less_person(admin.family_id)
        db.session.commit()
    rv = auth_client.post(f'/stories/person/{gid}/new-prompt', follow_redirects=False)
    assert rv.status_code == 302
    assert '/billing' in rv.headers['Location']
    with app.app_context():
        assert StoryPrompt.query.filter_by(person_id=gid).count() == 0


# ── disabled feature + isolation ──────────────────────────────────────────────

def test_stories_404_when_disabled(app, auth_client):
    with app.app_context():
        fam = User.query.filter_by(email='admin@pease-family.com').first().family
        fam.enable_stories = False
        db.session.commit()
    assert auth_client.get('/stories').status_code == 404


def test_cross_family_prompt_blocked(app, auth_client):
    with app.app_context():
        other = User.query.filter_by(email='admin@other-family.com').first()
        op = StoryPrompt(family_id=other.family_id, person_id=other.person_id,
                         question='Other family question', source='manual')
        db.session.add(op); db.session.commit()
        opid = op.id
    # Pease admin cannot view the other family's prompt
    assert auth_client.get(f'/stories/{opid}').status_code == 404


# ── profile display ───────────────────────────────────────────────────────────

def test_answered_story_shows_on_profile(app, auth_client):
    with app.app_context():
        admin = User.query.filter_by(email='admin@pease-family.com').first()
        gid = _account_less_person(admin.family_id)
    auth_client.post(f'/stories/person/{gid}/new-prompt', follow_redirects=True)
    with app.app_context():
        pid = StoryPrompt.query.filter_by(person_id=gid).first().id
    auth_client.post(f'/stories/{pid}/answer', data={'answer': 'Orchard memories here.'},
                     follow_redirects=True)
    html = auth_client.get(f'/person/{gid}').data.decode()
    assert 'Family Stories' in html
    assert 'Orchard memories here.' in html
