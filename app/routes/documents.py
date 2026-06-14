"""Shared family documents: list, upload, view, delete."""
from flask import render_template, request, redirect, url_for, flash, abort, Response
from flask_login import login_required, current_user

from .. import db
from ..models import Document, DOCUMENT_CATEGORIES
from ..storage import upload_document, get_object_bytes, delete_object
from . import main, admin_required


@main.route('/documents')
@login_required
def documents_list():
    fid = current_user.active_family_id
    docs = Document.query.filter_by(family_id=fid).order_by(Document.uploaded_at.desc()).all()
    grouped = {}
    for d in docs:
        cat = d.category or 'Other'
        grouped.setdefault(cat, []).append(d)
    ordered = [(cat, grouped[cat]) for cat in DOCUMENT_CATEGORIES if cat in grouped]
    return render_template('documents.html', grouped=ordered, categories=DOCUMENT_CATEGORIES)


@main.route('/documents/upload', methods=['POST'])
@login_required
def document_upload():
    fid = current_user.active_family_id
    f = request.files.get('file')
    if not f or not f.filename:
        flash('No file selected.', 'error')
        return redirect(url_for('main.documents_list'))
    result = upload_document(f)
    if result is None:
        flash('Unsupported file type. Allowed: pdf, jpg, png, gif, webp, heic, txt, doc, docx', 'error')
        return redirect(url_for('main.documents_list'))
    key, ext, size = result
    title = request.form.get('title', '').strip() or f.filename.rsplit('.', 1)[0]
    my_person = current_user.person if current_user.person and current_user.person.family_id == fid else None
    doc = Document(
        family_id=fid,
        uploader_id=my_person.id if my_person else None,
        title=title,
        category=request.form.get('category') or None,
        storage_key=key,
        original_filename=f.filename,
        file_type=ext,
        file_size=size,
        notes=request.form.get('notes', '').strip() or None,
    )
    db.session.add(doc)
    db.session.commit()
    flash('Document uploaded.', 'success')
    return redirect(url_for('main.documents_list'))


@main.route('/documents/<int:doc_id>/view')
@login_required
def document_view(doc_id):
    doc = db.session.get(Document, doc_id)
    if not doc or doc.family_id != current_user.active_family_id:
        abort(404)
    data, content_type = get_object_bytes(doc.storage_key)
    inline_types = {'application/pdf', 'image/jpeg', 'image/png', 'image/gif', 'image/webp', 'text/plain'}
    disposition = 'inline' if content_type in inline_types else f'attachment; filename="{doc.original_filename}"'
    return Response(data, content_type=content_type,
                    headers={'Content-Disposition': disposition})


@main.route('/documents/<int:doc_id>/delete', methods=['POST'])
@login_required
@admin_required
def document_delete(doc_id):
    doc = db.session.get(Document, doc_id)
    if not doc or doc.family_id != current_user.active_family_id:
        abort(404)
    delete_object(doc.storage_key)
    db.session.delete(doc)
    db.session.commit()
    flash('Document deleted.', 'success')
    return redirect(url_for('main.documents_list'))
