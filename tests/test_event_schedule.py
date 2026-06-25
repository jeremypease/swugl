"""
Printable / shareable event schedule (#74): /events/<id>/schedule renders a
clean standalone page, sections appear only when enabled, multi-day events group
by day, and access is family-only.
"""
from datetime import date
from app import db
from app.models import (User, Event, EventMeal, EventAssignment,
                        EventSleepingSpot, CarpoolOffer)


def _bare_event(name='E'):
    admin = User.query.filter_by(email='admin@pease-family.com').first()
    ev = Event(family_id=admin.family_id, name=name, start_date=date(2026, 8, 1))
    db.session.add(ev); db.session.commit()
    return ev.id


def test_schedule_renders_enabled_sections_grouped_by_day(app, auth_client):
    with app.app_context():
        admin = User.query.filter_by(email='admin@pease-family.com').first()
        ev = Event(family_id=admin.family_id, name='Family Reunion', location='Lake Cabin',
                   start_date=date(2026, 8, 1), end_date=date(2026, 8, 3),
                   has_meals=True, has_assignments=True, has_sleeping=True, has_carpool=True)
        db.session.add(ev); db.session.flush()
        db.session.add(EventMeal(event_id=ev.id, name='Saturday Dinner',
                                 meal_date=date(2026, 8, 1), meal_time='6pm'))
        db.session.add(EventMeal(event_id=ev.id, name='Sunday Brunch',
                                 meal_date=date(2026, 8, 2), meal_time='10am'))
        db.session.add(EventAssignment(event_id=ev.id, title='Bring firewood', category='Supplies'))
        db.session.add(EventSleepingSpot(event_id=ev.id, name='Master Bedroom',
                                         spot_type='bedroom', capacity=2))
        db.session.add(CarpoolOffer(event_id=ev.id, person_id=admin.person_id,
                                    role='driver', seats=3, departure_from='Provo'))
        db.session.commit()
        eid = ev.id
    r = auth_client.get(f'/events/{eid}/schedule')
    assert r.status_code == 200
    html = r.data.decode()
    assert 'Family Reunion' in html and 'Lake Cabin' in html
    assert 'Saturday Dinner' in html and 'Sunday Brunch' in html
    assert 'Bring firewood' in html and 'Master Bedroom' in html and 'Provo' in html
    # multi-day grouping → both day headers rendered
    assert 'August 1' in html and 'August 2' in html
    # standalone print page — no app shell
    assert 'class="sidebar"' not in html
    # QR back to the live event
    assert 'data:image/png;base64,' in html and f'/events/{eid}' in html


def test_schedule_hides_disabled_sections(app, auth_client):
    with app.app_context():
        eid = _bare_event('Simple Event')   # no has_* flags
    html = auth_client.get(f'/events/{eid}/schedule').data.decode()
    assert 'Simple Event' in html
    for label in ('Assignments', 'Sleeping', 'Carpool', 'Meals'):
        assert label not in html


def test_schedule_requires_login(app, client):
    with app.app_context():
        eid = _bare_event()
    assert client.get(f'/events/{eid}/schedule').status_code == 302


def test_schedule_403_for_other_family(app, other_auth_client):
    with app.app_context():
        eid = _bare_event()
    assert other_auth_client.get(f'/events/{eid}/schedule').status_code == 403


def test_schedule_404_for_missing_event(app, auth_client):
    assert auth_client.get('/events/999999/schedule').status_code == 404


def test_print_button_on_event_detail(app, auth_client):
    with app.app_context():
        eid = _bare_event()
    html = auth_client.get(f'/events/{eid}').data.decode()
    assert f'/events/{eid}/schedule' in html and 'Print Schedule' in html
