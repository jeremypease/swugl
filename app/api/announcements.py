from flask import jsonify, request
from flask_jwt_extended import jwt_required, current_user

from .. import db
from ..models import Announcement, AnnouncementReaction
from . import api
from .utils import api_family_id, error_response

ALLOWED_EMOJIS = {'❤️', '😂', '😮', '😢', '👏', '🎉'}


def _serialize_announcement(a, my_person_id=None):
    reactions = {}
    for r in a.reactions:
        if r.emoji not in reactions:
            reactions[r.emoji] = {'count': 0, 'mine': False}
        reactions[r.emoji]['count'] += 1
        if my_person_id and r.person_id == my_person_id:
            reactions[r.emoji]['mine'] = True
    return {
        'id': a.id,
        'title': a.title,
        'body': a.body,
        'pinned': a.pinned,
        'created_at': a.created_at.isoformat(),
        'author_name': a.author.get_display_name() if a.author else None,
        'reactions': reactions,
    }


@api.get('/announcements')
@jwt_required()
def list_announcements():
    fid = api_family_id()
    items = (
        Announcement.query
        .filter_by(family_id=fid)
        .order_by(Announcement.pinned.desc(), Announcement.created_at.desc())
        .limit(50)
        .all()
    )
    my_person_id = current_user.person_id
    return jsonify({'announcements': [_serialize_announcement(a, my_person_id) for a in items]})


@api.get('/announcements/<int:ann_id>')
@jwt_required()
def get_announcement(ann_id):
    fid = api_family_id()
    a = db.session.get(Announcement, ann_id)
    if not a or a.family_id != fid:
        return error_response(404, 'not found')
    my_person_id = current_user.person_id
    return jsonify({'announcement': _serialize_announcement(a, my_person_id)})


@api.post('/announcements/<int:ann_id>/react')
@jwt_required()
def react_announcement(ann_id):
    fid = api_family_id()
    a = db.session.get(Announcement, ann_id)
    if not a or a.family_id != fid:
        return error_response(404, 'not found')

    if not current_user.person_id:
        return error_response(400, 'person_required')

    data = request.get_json(silent=True) or {}
    emoji = data.get('emoji', '')
    if emoji not in ALLOWED_EMOJIS:
        return error_response(400, 'invalid_emoji')

    existing = AnnouncementReaction.query.filter_by(
        announcement_id=ann_id, person_id=current_user.person_id, emoji=emoji
    ).first()
    if existing:
        db.session.delete(existing)
        mine = False
    else:
        db.session.add(AnnouncementReaction(
            announcement_id=ann_id, person_id=current_user.person_id, emoji=emoji
        ))
        mine = True
    db.session.commit()

    count = AnnouncementReaction.query.filter_by(announcement_id=ann_id, emoji=emoji).count()
    return jsonify({'emoji': emoji, 'count': count, 'mine': mine})
