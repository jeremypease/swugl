from datetime import date

from flask import jsonify, request
from flask_jwt_extended import jwt_required, current_user

from .. import db
from ..models import Album, Photo
from ..storage import upload_photo, photo_url
from . import api
from .utils import api_family_id, error_response

_MAX_LIMIT = 100
_DEFAULT_LIMIT = 50


def _serialize_photo(p):
    return {
        'id': p.id,
        'url': photo_url(p.path),
        'thumb_url': photo_url(p.thumb_path) if p.thumb_path else photo_url(p.path),
        'caption': p.caption,
        'taken_date': p.taken_date.isoformat() if p.taken_date else None,
        'uploaded_by_name': p.uploaded_by.get_display_name() if p.uploaded_by else None,
        'created_at': p.created_at.isoformat(),
    }


def _serialize_album(album, include_photos=False):
    cover = album.cover
    data = {
        'id': album.id,
        'name': album.name,
        'description': album.description,
        'year': album.year,
        'photo_count': album.photo_count,
        'cover_url': photo_url(cover.path) if cover else None,
        'cover_thumb_url': photo_url(cover.thumb_path) if cover and cover.thumb_path else (photo_url(cover.path) if cover else None),
        'created_at': album.created_at.isoformat(),
    }
    if include_photos:
        data['photos'] = [_serialize_photo(p) for p in album.photos]
    return data


@api.get('/albums')
@jwt_required()
def list_albums():
    fid = api_family_id()
    albums = (
        Album.query
        .filter_by(family_id=fid)
        .order_by(Album.created_at.desc())
        .all()
    )
    return jsonify({'albums': [_serialize_album(a) for a in albums]})


@api.get('/albums/<int:album_id>')
@jwt_required()
def get_album(album_id):
    fid = api_family_id()
    album = db.session.get(Album, album_id)
    if not album or album.family_id != fid:
        return error_response(404, 'not found')
    return jsonify({'album': _serialize_album(album, include_photos=True)})


@api.get('/albums/<int:album_id>/photos')
@jwt_required()
def list_album_photos(album_id):
    fid = api_family_id()
    album = db.session.get(Album, album_id)
    if not album or album.family_id != fid:
        return error_response(404, 'not found')

    try:
        limit = min(int(request.args.get('limit', _DEFAULT_LIMIT)), _MAX_LIMIT)
    except ValueError:
        limit = _DEFAULT_LIMIT

    before_id = request.args.get('before_id', type=int)

    q = Photo.query.filter_by(album_id=album_id, family_id=fid)
    if before_id:
        q = q.filter(Photo.id < before_id)
    photos = q.order_by(Photo.id.desc()).limit(limit + 1).all()

    has_more = len(photos) > limit
    photos = photos[:limit]

    return jsonify({
        'photos': [_serialize_photo(p) for p in photos],
        'has_more': has_more,
        'next_before_id': photos[-1].id if has_more else None,
    })


@api.post('/albums/<int:album_id>/photos')
@jwt_required()
def upload_album_photo(album_id):
    fid = api_family_id()
    album = db.session.get(Album, album_id)
    if not album or album.family_id != fid:
        return error_response(404, 'not found')

    file = request.files.get('photo')
    if not file or not file.filename:
        return error_response(400, 'no_file')

    result = upload_photo(file, folder=f'albums/{album_id}', with_thumb=True)
    if not result:
        return error_response(400, 'invalid_file')

    path, thumb_path = result

    caption = (request.form.get('caption') or '').strip() or None

    taken_date = None
    taken_date_str = request.form.get('taken_date')
    if taken_date_str:
        try:
            taken_date = date.fromisoformat(taken_date_str)
        except ValueError:
            pass

    photo = Photo(
        album_id=album_id,
        family_id=fid,
        uploaded_by_id=current_user.person_id,
        path=path,
        thumb_path=thumb_path,
        caption=caption,
        taken_date=taken_date,
    )
    db.session.add(photo)
    db.session.commit()

    from ..notifications import notify_family
    actor = current_user.person.get_display_name() if current_user.person else 'Someone'
    notify_family(
        fid, 'new_photos',
        title=f'{actor} added a photo to {album.name}',
        url=f'/albums/{album_id}',
        exclude_user_id=current_user.id,
    )

    return jsonify({'photo': _serialize_photo(photo)}), 201


@api.get('/photos/<int:photo_id>')
@jwt_required()
def get_photo(photo_id):
    fid = api_family_id()
    photo = db.session.get(Photo, photo_id)
    if not photo or photo.family_id != fid:
        return error_response(404, 'not found')
    return jsonify({'photo': _serialize_photo(photo)})
