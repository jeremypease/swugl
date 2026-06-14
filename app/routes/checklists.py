"""Shared family checklists: packing, shopping, and general lists."""
from datetime import date

from flask import render_template, request, redirect, url_for, flash, jsonify, abort
from flask_login import login_required, current_user

from .. import db
from ..models import Checklist, ChecklistItem, Event
from . import main


@main.route('/checklists')
@login_required
def checklists():
    all_lists = Checklist.query.filter_by(family_id=current_user.active_family_id)\
        .order_by(Checklist.created_at.desc()).all()
    upcoming_events = Event.query.filter_by(family_id=current_user.active_family_id)\
        .filter(Event.start_date >= date.today()).order_by(Event.start_date).limit(10).all()
    active_lists = [cl for cl in all_lists if not cl.items or cl.done_count < len(cl.items)]
    completed_lists = [cl for cl in all_lists if cl.items and cl.done_count == len(cl.items)]
    return render_template('checklists.html', active_lists=active_lists,
                           completed_lists=completed_lists, events=upcoming_events)


@main.route('/checklists/new', methods=['POST'])
@login_required
def create_checklist():
    title = request.form.get('title', '').strip()
    list_type = request.form.get('list_type', 'general')
    event_id = request.form.get('event_id', type=int)
    if not title:
        flash('Please enter a title.', 'error')
        return redirect(url_for('main.checklists'))
    cl = Checklist(
        family_id=current_user.active_family_id,
        created_by_id=current_user.person.id if current_user.person else None,
        title=title,
        list_type=list_type,
        event_id=event_id or None,
    )
    db.session.add(cl)
    db.session.commit()
    return redirect(url_for('main.checklist_detail', checklist_id=cl.id))


@main.route('/checklists/<int:checklist_id>')
@login_required
def checklist_detail(checklist_id):
    cl = db.session.get(Checklist, checklist_id)
    if not cl or cl.family_id != current_user.active_family_id:
        abort(404)
    return render_template('checklist_detail.html', checklist=cl)


@main.route('/checklists/<int:checklist_id>/items/add', methods=['POST'])
@login_required
def checklist_add_item(checklist_id):
    cl = db.session.get(Checklist, checklist_id)
    if not cl or cl.family_id != current_user.active_family_id:
        abort(404)
    label = request.form.get('label', '').strip()
    if label:
        db.session.add(ChecklistItem(checklist_id=checklist_id, label=label))
        db.session.commit()
    return redirect(url_for('main.checklist_detail', checklist_id=checklist_id))


@main.route('/checklists/<int:checklist_id>/items/<int:item_id>/toggle', methods=['POST'])
@login_required
def checklist_toggle_item(checklist_id, item_id):
    wants_json = 'application/json' in request.headers.get('Accept', '')
    item = db.session.get(ChecklistItem, item_id)
    if not item or item.checklist.family_id != current_user.active_family_id:
        abort(404)
    item.is_done = not item.is_done
    item.claimed_by_id = current_user.person.id if (item.is_done and current_user.person) else None
    db.session.commit()
    if wants_json:
        return jsonify({
            'is_done': item.is_done,
            'claimed_by': item.claimed_by.get_display_name() if item.claimed_by else None,
        })
    return redirect(url_for('main.checklist_detail', checklist_id=checklist_id))


@main.route('/checklists/<int:checklist_id>/items/<int:item_id>/delete', methods=['POST'])
@login_required
def checklist_delete_item(checklist_id, item_id):
    item = db.session.get(ChecklistItem, item_id)
    if not item or item.checklist.family_id != current_user.active_family_id:
        abort(404)
    if not current_user.active_is_admin and not (current_user.person and item.checklist.created_by_id == current_user.person.id):
        abort(403)
    db.session.delete(item)
    db.session.commit()
    return redirect(url_for('main.checklist_detail', checklist_id=checklist_id))


@main.route('/checklists/<int:checklist_id>/delete', methods=['POST'])
@login_required
def checklist_delete(checklist_id):
    cl = db.session.get(Checklist, checklist_id)
    if not cl or cl.family_id != current_user.active_family_id:
        abort(404)
    is_creator = current_user.person and cl.created_by_id == current_user.person.id
    if not current_user.active_is_admin and not is_creator:
        abort(403)
    db.session.delete(cl)
    db.session.commit()
    flash('Checklist deleted.', 'info')
    return redirect(url_for('main.checklists'))


@main.route('/checklists/<int:checklist_id>/rename', methods=['POST'])
@login_required
def checklist_rename(checklist_id):
    cl = db.session.get(Checklist, checklist_id)
    if not cl or cl.family_id != current_user.active_family_id:
        abort(404)
    is_creator = current_user.person and cl.created_by_id == current_user.person.id
    if not current_user.active_is_admin and not is_creator:
        abort(403)
    title = request.form.get('title', '').strip()
    if title:
        cl.title = title
        db.session.commit()
    return redirect(url_for('main.checklist_detail', checklist_id=checklist_id))


@main.route('/checklists/<int:checklist_id>/clear-done', methods=['POST'])
@login_required
def checklist_clear_done(checklist_id):
    cl = db.session.get(Checklist, checklist_id)
    if not cl or cl.family_id != current_user.active_family_id:
        abort(404)
    is_creator = current_user.person and cl.created_by_id == current_user.person.id
    if not current_user.active_is_admin and not is_creator:
        abort(403)
    ChecklistItem.query.filter_by(checklist_id=checklist_id, is_done=True).delete()
    db.session.commit()
    return redirect(url_for('main.checklist_detail', checklist_id=checklist_id))
