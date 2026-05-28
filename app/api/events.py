from datetime import date, datetime
from flask import request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity

from .. import db
from ..models import Event, EventRSVP, EventAssignment, User, Person
from . import api
from .utils import (
    error_response, api_family_id,
    serialize_event, serialize_meal, serialize_assignment,
)


def _get_event_or_404(event_id, family_id):
    event = Event.query.get(event_id)
    if not event or event.family_id != family_id:
        return None
    return event


@api.route('/events', methods=['GET'])
@jwt_required()
def events_list():
    fid = api_family_id()
    today = date.today()
    events = (
        Event.query
        .filter_by(family_id=fid)
        .filter(Event.start_date >= today)
        .order_by(Event.start_date)
        .limit(20)
        .all()
    )
    return jsonify({'events': [serialize_event(e) for e in events]}), 200


@api.route('/events/<int:event_id>', methods=['GET'])
@jwt_required()
def event_detail(event_id):
    fid = api_family_id()
    event = _get_event_or_404(event_id, fid)
    if not event:
        return error_response(404, 'Event not found.', 'not_found')

    user_id = get_jwt_identity()
    user = User.query.get(int(user_id))
    my_person_id = user.person_id if user else None

    rsvp_map = {r.person_id: r.status for r in event.rsvps}
    my_rsvp = rsvp_map.get(my_person_id) if my_person_id else None

    data = serialize_event(event)
    data['meals'] = [serialize_meal(m) for m in event.meals] if event.has_meals else []
    data['assignments'] = [serialize_assignment(a) for a in event.assignments] if event.has_assignments else []
    data['rsvp_map'] = {str(k): v for k, v in rsvp_map.items()}
    data['my_rsvp'] = my_rsvp
    return jsonify(data), 200


@api.route('/events/<int:event_id>/rsvp', methods=['POST'])
@jwt_required()
def event_rsvp(event_id):
    fid = api_family_id()
    event = _get_event_or_404(event_id, fid)
    if not event:
        return error_response(404, 'Event not found.', 'not_found')

    data = request.get_json(silent=True) or {}
    person_id = data.get('person_id')
    status = data.get('status')

    if status not in ('yes', 'no', 'maybe'):
        return error_response(400, 'Status must be yes, no, or maybe.', 'invalid_status')

    person = Person.query.get(person_id) if person_id else None
    if not person or person.family_id != fid:
        return error_response(403, 'Person not in your family.', 'forbidden')

    user_id = get_jwt_identity()
    user = User.query.get(int(user_id))
    household_person_ids = {p.id for p in (user.person.household_members() if user and user.person else [])}
    if user and user.person_id:
        household_person_ids.add(user.person_id)
    if person_id not in household_person_ids:
        return error_response(403, 'You may only RSVP for your household members.', 'forbidden')

    rsvp = EventRSVP.query.filter_by(event_id=event_id, person_id=person_id).first()
    if rsvp:
        rsvp.status = status
        rsvp.updated_at = datetime.utcnow()
    else:
        rsvp = EventRSVP(event_id=event_id, person_id=person_id, status=status)
        db.session.add(rsvp)
    db.session.commit()
    return jsonify({'ok': True, 'status': status}), 200


@api.route('/events/<int:event_id>/assignments', methods=['GET'])
@jwt_required()
def event_assignments(event_id):
    fid = api_family_id()
    event = _get_event_or_404(event_id, fid)
    if not event:
        return error_response(404, 'Event not found.', 'not_found')
    return jsonify({'assignments': [serialize_assignment(a) for a in event.assignments]}), 200


@api.route('/events/<int:event_id>/assignments/<int:aid>/claim', methods=['POST'])
@jwt_required()
def assignment_claim(event_id, aid):
    fid = api_family_id()
    event = _get_event_or_404(event_id, fid)
    if not event:
        return error_response(404, 'Event not found.', 'not_found')

    a = EventAssignment.query.get(aid)
    if not a or a.event_id != event_id:
        return error_response(404, 'Assignment not found.', 'not_found')

    user_id = get_jwt_identity()
    user = User.query.get(int(user_id))
    if not user or not user.person_id:
        return error_response(403, 'No person linked to your account.', 'forbidden')

    if a.claimed_by_id:
        return error_response(409, 'Assignment already claimed.', 'already_claimed')

    a.claimed_by_id = user.person_id
    db.session.commit()
    return jsonify({'ok': True}), 200


@api.route('/events/<int:event_id>/assignments/<int:aid>/unclaim', methods=['POST'])
@jwt_required()
def assignment_unclaim(event_id, aid):
    fid = api_family_id()
    event = _get_event_or_404(event_id, fid)
    if not event:
        return error_response(404, 'Event not found.', 'not_found')

    a = EventAssignment.query.get(aid)
    if not a or a.event_id != event_id:
        return error_response(404, 'Assignment not found.', 'not_found')

    user_id = get_jwt_identity()
    user = User.query.get(int(user_id))
    if not user or not user.person_id:
        return error_response(403, 'No person linked to your account.', 'forbidden')

    if a.claimed_by_id != user.person_id:
        return error_response(403, 'You did not claim this assignment.', 'forbidden')

    a.claimed_by_id = None
    db.session.commit()
    return jsonify({'ok': True}), 200
