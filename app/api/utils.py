from flask import jsonify
from flask_jwt_extended import get_jwt


def error_response(status, message, code=None):
    return jsonify({'error': message, 'code': code or message}), status


def api_family_id():
    claims = get_jwt()
    return claims.get('family_id')


def serialize_user(user):
    return {
        'id': user.id,
        'email': user.email,
        'first_name': user.first_name,
        'last_name': user.last_name,
        'is_admin': user.is_admin,
        'family_id': user.family_id,
        'person_id': user.person_id,
    }


def serialize_event(event):
    return {
        'id': event.id,
        'name': event.name,
        'description': event.description,
        'location': event.location,
        'kind': event.kind,
        'start_date': event.start_date.isoformat() if event.start_date else None,
        'end_date': event.end_date.isoformat() if event.end_date else None,
        'rsvp_deadline': event.rsvp_deadline.isoformat() if event.rsvp_deadline else None,
        'is_annual': event.is_annual,
        'has_meals': event.has_meals,
        'has_assignments': event.has_assignments,
        'has_sleeping': event.has_sleeping,
        'date_range_display': event.date_range_display(),
    }


def serialize_meal(meal):
    return {
        'id': meal.id,
        'name': meal.name,
        'meal_date': meal.meal_date.isoformat() if meal.meal_date else None,
        'meal_time': meal.meal_time,
        'notes': meal.notes,
        'items': [serialize_meal_item(i) for i in meal.items],
    }


def serialize_meal_item(item):
    return {
        'id': item.id,
        'label': item.label,
        'quantity': item.quantity,
        'is_cleanup': item.is_cleanup,
        'assigned_to_id': item.assigned_to_id,
        'assigned_to_name': item.assigned_to.display_name() if item.assigned_to else None,
    }


def serialize_assignment(a):
    return {
        'id': a.id,
        'title': a.title,
        'description': a.description,
        'category': a.category,
        'due_date': a.due_date.isoformat() if a.due_date else None,
        'is_done': a.is_done,
        'claimed_by_id': a.claimed_by_id,
        'claimed_by_name': a.claimed_by.display_name() if a.claimed_by else None,
    }


def serialize_person(person):
    return {
        'id': person.id,
        'name': person.display_name(),
        'first_name': person.first_name,
        'last_name': person.last_name,
        'nickname': person.nickname,
        'gender': person.gender,
        'birthday': person.birthday.isoformat() if person.birthday else None,
        'photo_path': person.photo_path,
        'in_directory': person.in_directory,
    }


def serialize_notification(n):
    return {
        'id': n.id,
        'event_type': n.event_type,
        'title': n.title,
        'body': n.body,
        'url': n.url,
        'is_read': n.is_read,
        'created_at': n.created_at.isoformat(),
    }
