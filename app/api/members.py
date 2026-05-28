from flask import jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity

from ..models import Person, User
from . import api
from .utils import error_response, api_family_id, serialize_person


@api.route('/members', methods=['GET'])
@jwt_required()
def members_list():
    fid = api_family_id()
    people = (
        Person.query
        .filter_by(family_id=fid, in_directory=True)
        .order_by(Person.last_name, Person.first_name)
        .all()
    )
    return jsonify({'members': [serialize_person(p) for p in people]}), 200


@api.route('/members/<int:person_id>', methods=['GET'])
@jwt_required()
def member_detail(person_id):
    from ..routes import get_relationship

    fid = api_family_id()
    person = Person.query.get(person_id)
    if not person or person.family_id != fid:
        return error_response(404, 'Person not found.', 'not_found')

    user_id = get_jwt_identity()
    user = User.query.get(int(user_id))
    viewer_person = user.person if user else None

    data = serialize_person(person)
    data['relationship'] = get_relationship(viewer_person, person) if viewer_person else None
    return jsonify(data), 200
