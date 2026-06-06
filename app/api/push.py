from datetime import datetime
from flask import request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity

from .. import db
from ..models import User, UserDevice
from . import api
from .utils import error_response


def _get_user():
    return User.query.get(int(get_jwt_identity()))


@api.route('/push/register', methods=['POST'])
@jwt_required()
def push_register():
    """Register a device push token. Call on app launch / token refresh."""
    user = _get_user()
    if not user:
        return error_response(401, 'User not found.', 'invalid_credentials')

    data = request.get_json(silent=True) or {}
    token = (data.get('token') or '').strip()
    platform = (data.get('platform') or '').lower()

    if not token or platform not in ('ios', 'android'):
        return error_response(400, 'token and platform (ios or android) are required.', 'invalid_params')

    device = UserDevice.query.filter_by(user_id=user.id, token=token).first()
    if device:
        device.platform = platform
        device.last_seen_at = datetime.utcnow()
    else:
        db.session.add(UserDevice(user_id=user.id, platform=platform, token=token))
    db.session.commit()
    return jsonify({'ok': True}), 200


@api.route('/push/unregister', methods=['POST'])
@jwt_required()
def push_unregister():
    """Remove a device token on logout or permission revocation."""
    user = _get_user()
    if not user:
        return error_response(401, 'User not found.', 'invalid_credentials')

    data = request.get_json(silent=True) or {}
    token = (data.get('token') or '').strip()
    if not token:
        return error_response(400, 'token is required.', 'invalid_params')

    UserDevice.query.filter_by(user_id=user.id, token=token).delete()
    db.session.commit()
    return jsonify({'ok': True}), 200
