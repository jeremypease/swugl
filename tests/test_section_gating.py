"""
Tests for paid-plan gating of event sections (#39).

Meals, assignments, and sleeping arrangements are organizer power tools gated
to paid plans. Carpool stays free. Already-enabled sections survive a downgrade
(turn-on is gated, not turn-off or participation).
"""
import pytest
from datetime import date
from app import db
from app.models import Family, User, Event


def _set_plan(app, plan):
    with app.app_context():
        fam = User.query.filter_by(email='admin@pease-family.com').first().family
        fam.plan = plan
        db.session.commit()


@pytest.fixture()
def event_id(app):
    with app.app_context():
        fam = User.query.filter_by(email='admin@pease-family.com').first().family
        e = Event(family_id=fam.id, name='Reunion', start_date=date(2026, 8, 1))
        db.session.add(e)
        db.session.commit()
        return e.id


# ── enable-section route ──────────────────────────────────────────────────────

@pytest.mark.parametrize('section', ['meals', 'assignments', 'sleeping'])
def test_free_cannot_enable_paid_section(app, auth_client, event_id, section):
    _set_plan(app, 'free')
    rv = auth_client.post(f'/events/{event_id}/enable-section',
                          data={'section': section}, follow_redirects=False)
    assert rv.status_code == 302
    assert '/billing' in rv.headers['Location']
    with app.app_context():
        e = db.session.get(Event, event_id)
        assert getattr(e, f'has_{section}') is False


def test_free_can_enable_carpool(app, auth_client, event_id):
    _set_plan(app, 'free')
    rv = auth_client.post(f'/events/{event_id}/enable-section',
                          data={'section': 'carpool'}, follow_redirects=False)
    assert rv.status_code == 302
    assert '/billing' not in rv.headers['Location']
    with app.app_context():
        assert db.session.get(Event, event_id).has_carpool is True


@pytest.mark.parametrize('section', ['meals', 'assignments', 'sleeping'])
def test_paid_can_enable_paid_section(app, auth_client, event_id, section):
    _set_plan(app, 'paid')
    rv = auth_client.post(f'/events/{event_id}/enable-section',
                          data={'section': section}, follow_redirects=False)
    assert rv.status_code == 302
    with app.app_context():
        assert getattr(db.session.get(Event, event_id), f'has_{section}') is True


# ── downgrade preserves already-enabled sections ──────────────────────────────

def test_downgrade_keeps_enabled_section_then_blocks_reenable(app, auth_client, event_id):
    # Paid admin enables meals
    _set_plan(app, 'paid')
    auth_client.post(f'/events/{event_id}/enable-section', data={'section': 'meals'})
    with app.app_context():
        assert db.session.get(Event, event_id).has_meals is True

    # Family downgrades — section stays on
    _set_plan(app, 'free')
    with app.app_context():
        assert db.session.get(Event, event_id).has_meals is True

    # Free admin disables it, then cannot turn it back on
    auth_client.post(f'/events/{event_id}/disable-section', data={'section': 'meals'})
    with app.app_context():
        assert db.session.get(Event, event_id).has_meals is False
    rv = auth_client.post(f'/events/{event_id}/enable-section',
                          data={'section': 'meals'}, follow_redirects=False)
    assert '/billing' in rv.headers['Location']
    with app.app_context():
        assert db.session.get(Event, event_id).has_meals is False
