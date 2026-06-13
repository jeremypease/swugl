from flask import render_template, redirect, url_for, flash, request, current_app, send_file, abort, jsonify
from flask_login import login_required, current_user
from ..models import Photo, Album, Person, Event
from .. import db
from ..storage import upload_photo, delete_object, get_object_bytes
from ..billing import requires_plan, family_has_paid_access
from . import main, admin_required, contributor_or_admin_required
from ..forms import AlbumForm, PhotoUploadForm
import zipfile
import io
import os

ALLOWED_PHOTO_EXTS = {'jpg', 'jpeg', 'png', 'webp', 'gif', 'heic'}

def _save_photo_file(file, album_id):
    """Returns (key, thumb_key) or None on rejected file."""
    result = upload_photo(file, folder=f'albums/{album_id}', with_thumb=True)
    return result if result else None

@main.route('/albums')
@login_required
def albums():
    all_albums = Album.query.filter_by(family_id=current_user.active_family_id)\
        .order_by(Album.created_at.desc()).all()
    form = AlbumForm()
    events = Event.query.filter_by(family_id=current_user.active_family_id).order_by(Event.start_date.desc()).all()
    form.event_id.choices = [(0, '-- None --')] + [(e.id, e.name) for e in events]
    return render_template('albums_list.html', albums=all_albums, form=form,
                           has_paid_access=family_has_paid_access(current_user.family))

@main.route('/albums/add', methods=['POST'])
@login_required
@contributor_or_admin_required
@requires_plan
def add_album():
    events = Event.query.filter_by(family_id=current_user.active_family_id).all()
    form = AlbumForm()
    form.event_id.choices = [(0, '-- None --')] + [(e.id, e.name) for e in events]
    if form.validate_on_submit():
        album = Album(
            family_id=current_user.active_family_id,
            created_by_id=current_user.person.id if current_user.person else None,
            name=form.name.data.strip(),
            description=form.description.data or None,
            year=form.year.data or None,
            event_id=form.event_id.data or None,
        )
        db.session.add(album)
        db.session.commit()
        flash(f'Album "{album.name}" created.', 'info')
        return redirect(url_for('main.album_detail', album_id=album.id))
    return redirect(url_for('main.albums'))

@main.route('/albums/<int:album_id>')
@login_required
def album_detail(album_id):
    album = db.session.get(Album, album_id)
    if not album or album.family_id != current_user.active_family_id:
        flash('Album not found.', 'error')
        return redirect(url_for('main.albums'))
    upload_form = PhotoUploadForm()
    people = Person.query.filter_by(
        family_id=current_user.active_family_id, in_directory=True
    ).order_by(Person.name).all()
    return render_template('album_detail.html', album=album, upload_form=upload_form,
                           people=people)

@main.route('/albums/<int:album_id>/upload', methods=['POST'])
@login_required
@contributor_or_admin_required
@requires_plan
def upload_photos(album_id):
    album = db.session.get(Album, album_id)
    if not album or album.family_id != current_user.active_family_id:
        return redirect(url_for('main.albums'))
    files = request.files.getlist('photos')
    caption = request.form.get('caption', '').strip() or None
    count = 0
    for file in files:
        if file and file.filename:
            result = _save_photo_file(file, album_id)
            if result:
                path, thumb_path = result
                photo = Photo(
                    album_id=album_id,
                    family_id=current_user.active_family_id,
                    uploaded_by_id=current_user.person.id if current_user.person else None,
                    path=path,
                    thumb_path=thumb_path,
                    caption=caption,
                )
                db.session.add(photo)
                count += 1
    if count:
        db.session.commit()
        flash(f'{count} photo{"s" if count != 1 else ""} uploaded.', 'info')
    return redirect(url_for('main.album_detail', album_id=album_id))

@main.route('/events/<int:event_id>/photos/upload', methods=['POST'])
@login_required
@requires_plan
def event_upload_photos(event_id):
    event = db.session.get(Event, event_id)
    if not event or event.family_id != current_user.active_family_id:
        return redirect(url_for('main.events_list'))
    # Find or auto-create the primary event album
    album = Album.query.filter_by(event_id=event_id, family_id=current_user.active_family_id).first()
    if not album:
        album = Album(
            family_id=current_user.active_family_id,
            created_by_id=current_user.person.id if current_user.person else None,
            name=event.name,
            event_id=event_id,
        )
        db.session.add(album)
        db.session.flush()
    files = request.files.getlist('photos')
    count = 0
    for file in files:
        if file and file.filename:
            ext = file.filename.rsplit('.', 1)[-1].lower() if '.' in file.filename else ''
            if ext in ALLOWED_PHOTO_EXTS:
                result = _save_photo_file(file, album.id)
                if result:
                    path, thumb_path = result
                    db.session.add(Photo(
                        album_id=album.id,
                        family_id=current_user.active_family_id,
                        uploaded_by_id=current_user.person.id if current_user.person else None,
                        path=path,
                        thumb_path=thumb_path,
                    ))
                    count += 1
    if count:
        db.session.commit()
        flash(f'{count} photo{"s" if count != 1 else ""} added.', 'info')
    else:
        db.session.rollback()
    return redirect(url_for('main.event_detail', event_id=event_id))


@main.route('/albums/<int:album_id>/download')
@login_required
def download_album(album_id):
    album = db.session.get(Album, album_id)
    if not album or album.family_id != current_user.active_family_id:
        return redirect(url_for('main.albums'))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for photo in album.photos:
            try:
                data, _ = get_object_bytes(photo.path)
                zf.writestr(os.path.basename(photo.path), data)
            except Exception:
                current_app.logger.warning('Skipping photo %s from zip: fetch failed', photo.id)
    buf.seek(0)
    safe_name = ''.join(c if c.isalnum() or c in ' -_' else '_' for c in album.name)
    return send_file(buf, mimetype='application/zip',
                     as_attachment=True, download_name=f'{safe_name}.zip')

@main.route('/albums/<int:album_id>/photos/<int:photo_id>/delete', methods=['POST'])
@login_required
def delete_photo(album_id, photo_id):
    photo = db.session.get(Photo, photo_id)
    if not photo or photo.family_id != current_user.active_family_id:
        return redirect(url_for('main.album_detail', album_id=album_id))
    can_delete = current_user.active_is_admin or (current_user.person and photo.uploaded_by_id == current_user.person.id)
    if can_delete:
        delete_object(photo.path)
        delete_object(photo.thumb_path)
        db.session.delete(photo)
        db.session.commit()
        flash('Photo deleted.', 'info')
    return redirect(url_for('main.album_detail', album_id=album_id))

@main.route('/photos/<int:photo_id>/tag', methods=['POST'])
@login_required
def photo_tag(photo_id):
    from ..models import PhotoTag
    photo = db.session.get(Photo, photo_id)
    if not photo or photo.family_id != current_user.active_family_id:
        abort(404)
    person_id = request.form.get('person_id', type=int)
    if person_id:
        person = db.session.get(Person, person_id)
        if person and person.family_id == current_user.active_family_id:
            existing = PhotoTag.query.filter_by(photo_id=photo_id, person_id=person_id).first()
            if not existing:
                db.session.add(PhotoTag(
                    photo_id=photo_id, person_id=person_id,
                    tagged_by_id=current_user.person.id if current_user.person else None,
                ))
                db.session.commit()
    open_idx = request.form.get('open_idx', '')
    return redirect(url_for('main.album_detail', album_id=photo.album_id,
                            _anchor=f'photo-{photo_id}') + (f'?open={open_idx}' if open_idx else ''))


@main.route('/photos/<int:photo_id>/tags/<int:tag_id>/remove', methods=['POST'])
@login_required
def photo_untag(photo_id, tag_id):
    from ..models import PhotoTag
    tag = db.session.get(PhotoTag, tag_id)
    if not tag or tag.photo.family_id != current_user.active_family_id:
        abort(404)
    is_tagger = current_user.person and tag.tagged_by_id == current_user.person.id
    is_tagged = current_user.person and tag.person_id == current_user.person.id
    if not (current_user.active_is_admin or is_tagger or is_tagged):
        abort(403)
    photo = tag.photo
    db.session.delete(tag)
    db.session.commit()
    open_idx = request.form.get('open_idx', '')
    return redirect(url_for('main.album_detail', album_id=photo.album_id)
                    + (f'?open={open_idx}' if open_idx else ''))


@main.route('/members/<int:person_id>/photos')
@login_required
def person_photos(person_id):
    from ..models import PhotoTag
    person = db.session.get(Person, person_id)
    if not person or person.family_id != current_user.active_family_id:
        abort(404)
    tags = PhotoTag.query.filter_by(person_id=person_id)\
        .join(Photo).filter(Photo.family_id == current_user.active_family_id)\
        .order_by(Photo.created_at.desc()).all()
    photos = [t.photo for t in tags]
    return render_template('person_photos.html', person=person, photos=photos)


@main.route('/albums/<int:album_id>/edit', methods=['POST'])
@login_required
@admin_required
def edit_album(album_id):
    album = db.session.get(Album, album_id)
    if not album or album.family_id != current_user.active_family_id:
        return redirect(url_for('main.albums'))
    name = request.form.get('name', '').strip()
    if name:
        album.name = name
    album.description = request.form.get('description', '').strip() or None
    year_str = request.form.get('year', '').strip()
    album.year = int(year_str) if year_str.isdigit() else None
    db.session.commit()
    flash('Album updated.', 'info')
    return redirect(url_for('main.album_detail', album_id=album_id))


@main.route('/photos/<int:photo_id>/caption', methods=['POST'])
@login_required
def photo_caption(photo_id):
    photo = db.session.get(Photo, photo_id)
    if not photo or photo.family_id != current_user.active_family_id:
        abort(404)
    can_edit = current_user.active_is_admin or (
        current_user.person and photo.uploaded_by_id == current_user.person.id)
    if not can_edit:
        abort(403)
    caption = request.form.get('caption', '').strip() or None
    photo.caption = caption
    db.session.commit()
    return jsonify({'caption': caption or ''})


@main.route('/albums/<int:album_id>/delete', methods=['POST'])
@login_required
@admin_required
def delete_album(album_id):
    album = db.session.get(Album, album_id)
    if not album or album.family_id != current_user.active_family_id:
        return redirect(url_for('main.albums'))
    for photo in album.photos:
        delete_object(photo.path)
        delete_object(photo.thumb_path)
    db.session.delete(album)
    db.session.commit()
    flash('Album deleted.', 'info')
    return redirect(url_for('main.albums'))


@main.route('/photos/<int:photo_id>/ai-caption', methods=['POST'])
@login_required
def photo_ai_caption(photo_id):
    from flask import jsonify
    from ..ai import suggest_photo_caption
    from ..storage import get_object_bytes
    photo = db.session.get(Photo, photo_id)
    if not photo or photo.family_id != current_user.active_family_id:
        return jsonify({'error': 'Not found'}), 404
    if not current_app.config.get('ANTHROPIC_API_KEY'):
        return jsonify({'error': 'AI not configured'}), 503
    try:
        image_bytes, content_type = get_object_bytes(photo.path)
        caption = suggest_photo_caption(image_bytes, content_type)
        return jsonify({'caption': caption})
    except Exception:
        current_app.logger.exception('AI photo caption error')
        return jsonify({'error': 'AI caption failed'}), 500


@main.route('/photos/<path:key>')
@login_required
def serve_photo(key):
    """Proxy route: serves R2 photos through Flask when no R2_PUBLIC_URL is set."""
    from flask import Response, abort
    photo = Photo.query.filter(
        Photo.family_id == current_user.active_family_id,
        db.or_(Photo.path == key, Photo.thumb_path == key),
    ).first()
    person = None if photo else Person.query.filter_by(family_id=current_user.active_family_id, photo_path=key).first()
    if not photo and not person:
        abort(403)
    data, content_type = get_object_bytes(key)
    return Response(data, content_type=content_type)
