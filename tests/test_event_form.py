"""
Regression tests for the event create/edit form rendering.

event_form.html previously used a Python list comprehension in a Jinja
expression (`[{...} for s in loc.sleeping_spots]`), which Jinja2 cannot compile —
so /events/add and /events/<id>/edit returned 500. These render the forms
(including a saved location with a sleeping spot, which exercises the
data-loc-spots line) to keep them from regressing.
"""
from app import db
from app.models import User, Location, LocationSleepingSpot


def test_event_add_form_renders(auth_client):
    r = auth_client.get('/events/add')
    assert r.status_code == 200
    assert b'name' in r.data


def test_event_edit_form_renders(auth_client, seeded_event_id):
    r = auth_client.get(f'/events/{seeded_event_id}/edit')
    assert r.status_code == 200


def test_event_form_renders_with_saved_location_spots(app, auth_client):
    """Exercises the data-loc-spots line that broke compilation."""
    with app.app_context():
        fam_id = User.query.filter_by(email='admin@pease-family.com').first().family_id
        loc = Location(family_id=fam_id, name='Lake House', address='1 Lake Rd')
        db.session.add(loc)
        db.session.flush()
        db.session.add(LocationSleepingSpot(location_id=loc.id, name='Master',
                                            spot_type='room', capacity=2, sort_order=0))
        db.session.commit()
    r = auth_client.get('/events/add')
    assert r.status_code == 200
    assert b'data-loc-spots' in r.data
    assert b'Lake House' in r.data
