from datetime import datetime
from flask import jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity

from .. import db
from ..models import Notification, User
from . import api
from .utils import error_response, serialize_notification


def _get_user():
    user_id = get_jwt_identity()
    return User.query.get(int(user_id))


@api.route('/notifications', methods=['GET'])
@jwt_required()
def notifications_list():
    user = _get_user()
    if not user:
        return error_response(401, 'User not found.', 'invalid_credentials')

    notes = (
        Notification.query
        .filter_by(user_id=user.id)
        .order_by(Notification.created_at.desc())
        .limit(50)
        .all()
    )
    unread = sum(1 for n in notes if not n.is_read)
    return jsonify({
        'notifications': [serialize_notification(n) for n in notes],
        'unread_count': unread,
    }), 200


@api.route('/notifications/<int:nid>/read', methods=['POST'])
@jwt_required()
def notification_read(nid):
    user = _get_user()
    if not user:
        return error_response(401, 'User not found.', 'invalid_credentials')

    n = Notification.query.get(nid)
    if not n or n.user_id != user.id:
        return error_response(404, 'Notification not found.', 'not_found')

    if not n.is_read:
        n.read_at = datetime.utcnow()
        db.session.commit()
    return jsonify({'ok': True}), 200


@api.route('/notifications/read-all', methods=['POST'])
@jwt_required()
def notifications_read_all():
    user = _get_user()
    if not user:
        return error_response(401, 'User not found.', 'invalid_credentials')

    now = datetime.utcnow()
    (
        Notification.query
        .filter_by(user_id=user.id, read_at=None)
        .update({'read_at': now})
    )
    db.session.commit()
    return jsonify({'ok': True}), 200
