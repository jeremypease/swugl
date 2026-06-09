from datetime import datetime
from flask import jsonify, request
from flask_jwt_extended import jwt_required, get_jwt_identity

from .. import db
from ..models import ChatMessage, User
from ..billing import family_has_paid_access
from . import api
from .utils import error_response, api_family_id


def _get_user():
    user_id = get_jwt_identity()
    return User.query.get(int(user_id))


def _serialize(msg, user):
    return {
        'id': msg.id,
        'family_id': msg.family_id,
        'author_id': msg.author_id,
        'author_name': msg.author.get_full_name(),
        'body': msg.body,
        'created_at': msg.created_at.isoformat(),
        'edited_at': msg.edited_at.isoformat() if msg.edited_at else None,
        'can_edit': msg.can_edit(user),
        'can_delete': msg.can_delete(user),
    }


@api.route('/chat/messages', methods=['GET'])
@jwt_required()
def chat_messages():
    user = _get_user()
    if not user:
        return error_response(401, 'User not found.', 'invalid_credentials')
    family_id = api_family_id()
    if not family_has_paid_access(user.family):
        return error_response(403, 'Chat requires a paid plan.', 'plan_required')

    after_id = request.args.get('after', 0, type=int)
    before_id = request.args.get('before', None, type=int)

    q = ChatMessage.query.filter_by(family_id=family_id)
    if after_id:
        q = q.filter(ChatMessage.id > after_id).order_by(ChatMessage.created_at.asc())
    elif before_id:
        q = q.filter(ChatMessage.id < before_id).order_by(ChatMessage.created_at.desc())
        msgs = q.limit(50).all()
        user.chat_last_seen_at = datetime.utcnow()
        db.session.commit()
        return jsonify({'messages': [_serialize(m, user) for m in reversed(msgs)]}), 200
    else:
        q = q.order_by(ChatMessage.created_at.desc())

    msgs = q.limit(50).all()
    user.chat_last_seen_at = datetime.utcnow()
    db.session.commit()
    if not after_id:
        msgs = list(reversed(msgs))
    return jsonify({'messages': [_serialize(m, user) for m in msgs]}), 200


@api.route('/chat/messages', methods=['POST'])
@jwt_required()
def chat_send():
    user = _get_user()
    if not user:
        return error_response(401, 'User not found.', 'invalid_credentials')
    family_id = api_family_id()
    if not family_has_paid_access(user.family):
        return error_response(403, 'Chat requires a paid plan.', 'plan_required')

    data = request.get_json(silent=True) or {}
    body = (data.get('body') or '').strip()
    if not body:
        return error_response(400, 'Message body is required.', 'missing_body')
    if len(body) > 2000:
        return error_response(400, 'Message too long.', 'body_too_long')

    msg = ChatMessage(family_id=family_id, author_id=user.id, body=body)
    db.session.add(msg)
    db.session.commit()

    from ..routes import _notify_chat_members
    _notify_chat_members(msg)

    return jsonify({'message': _serialize(msg, user)}), 201


@api.route('/chat/messages/<int:msg_id>', methods=['PATCH'])
@jwt_required()
def chat_edit(msg_id):
    user = _get_user()
    if not user:
        return error_response(401, 'User not found.', 'invalid_credentials')
    family_id = api_family_id()
    if not family_has_paid_access(user.family):
        return error_response(403, 'Chat requires a paid plan.', 'plan_required')

    msg = ChatMessage.query.filter_by(id=msg_id, family_id=family_id).first()
    if not msg:
        return error_response(404, 'Message not found.', 'not_found')
    if not msg.can_edit(user):
        return error_response(403, 'Edit window has closed.', 'edit_window_closed')

    data = request.get_json(silent=True) or {}
    body = (data.get('body') or '').strip()
    if not body:
        return error_response(400, 'Message body is required.', 'missing_body')

    msg.body = body
    msg.edited_at = datetime.utcnow()
    db.session.commit()
    return jsonify({'message': _serialize(msg, user)}), 200


@api.route('/chat/messages/<int:msg_id>', methods=['DELETE'])
@jwt_required()
def chat_delete(msg_id):
    user = _get_user()
    if not user:
        return error_response(401, 'User not found.', 'invalid_credentials')
    family_id = api_family_id()
    if not family_has_paid_access(user.family):
        return error_response(403, 'Chat requires a paid plan.', 'plan_required')

    msg = ChatMessage.query.filter_by(id=msg_id, family_id=family_id).first()
    if not msg:
        return error_response(404, 'Message not found.', 'not_found')
    if not msg.can_delete(user):
        return error_response(403, 'Delete window has closed.', 'delete_window_closed')

    db.session.delete(msg)
    db.session.commit()
    return jsonify({'ok': True}), 200
