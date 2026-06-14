"""
Tests for Theme 1 (activation): expanded onboarding checklist and the
one-step invite from the add-member form.
"""
import pytest
from app import db
from app.models import Family, User, Person


@pytest.fixture()
def new_pod_admin(app):
    """Give the Pease family an account_id so the onboarding checklist shows."""
    with app.app_context():
        fam = User.query.filter_by(email='admin@pease-family.com').first().family
        fam.account_id = 'POD123'
        db.session.commit()


def test_onboarding_checklist_expanded(app, auth_client, new_pod_admin):
    html = auth_client.get('/home').data.decode()
    for label in ('Add your first family member', 'Complete your profile',
                  'Create your first event', 'Upload a photo', 'Add a family location'):
        assert label in html


def test_onboarding_hidden_without_account_id(app, auth_client):
    # Default seed family has no account_id → checklist suppressed
    html = auth_client.get('/home').data.decode()
    assert 'Create your first event' not in html


def test_one_step_invite_creates_person_and_invited_user(app, auth_client):
    auth_client.post('/admin/add-member', data={
        'first_name': 'Invited', 'last_name': 'Cousin',
        'email': 'cousin@example.com', 'gender': 'Female',
        'invite_now': '1',
    }, follow_redirects=True)
    with app.app_context():
        admin = User.query.filter_by(email='admin@pease-family.com').first()
        person = Person.query.filter_by(family_id=admin.family_id, name='Invited Cousin').first()
        assert person is not None
        invited = User.query.filter_by(email='cousin@example.com').first()
        assert invited is not None
        assert invited.status == 'invited'
        assert invited.person_id == person.id


def test_add_member_without_invite_creates_only_person(app, auth_client):
    auth_client.post('/admin/add-member', data={
        'first_name': 'Tree', 'last_name': 'Only',
        'email': 'treeonly@example.com', 'gender': 'Male',
        # invite_now omitted
    }, follow_redirects=True)
    with app.app_context():
        admin = User.query.filter_by(email='admin@pease-family.com').first()
        assert Person.query.filter_by(family_id=admin.family_id, name='Tree Only').first() is not None
        assert User.query.filter_by(email='treeonly@example.com').first() is None
