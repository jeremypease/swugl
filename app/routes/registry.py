"""Gift registries — a surprise-safe gift-coordination board for a recipient.

The recipient is blocked from viewing their own registry at every layer (list,
detail, item add, claim) so surprises aren't spoiled. Paid-tier feature."""
from flask import render_template, request, redirect, url_for, flash, abort
from flask_login import login_required, current_user

from .. import db
from ..models import GiftRegistry, GiftRegistryItem, Person, Event
from ..billing import requires_plan
from . import main


def _my_person_id():
    return current_user.person.id if current_user.person else None


def _get_registry_or_404(registry_id):
    reg = db.session.get(GiftRegistry, registry_id)
    if not reg or reg.family_id != current_user.active_family_id:
        abort(404)
    return reg


def _block_recipient(reg):
    """The honoree must never see their own registry."""
    if _my_person_id() is not None and reg.recipient_person_id == _my_person_id():
        abort(403)


@main.route('/registries')
@login_required
@requires_plan
def registries():
    mine = _my_person_id()
    regs = (GiftRegistry.query.filter_by(family_id=current_user.active_family_id)
            .order_by(GiftRegistry.created_at.desc()).all())
    # Hide registries where the viewer is the recipient — they shouldn't even
    # know one exists for them.
    regs = [r for r in regs if r.recipient_person_id != mine]
    return render_template('registries.html', registries=regs)


@main.route('/registries/new', methods=['GET', 'POST'])
@login_required
@requires_plan
def registry_new():
    fid = current_user.active_family_id
    people = Person.query.filter_by(family_id=fid).order_by(Person.name).all()
    events = Event.query.filter_by(family_id=fid).order_by(Event.start_date.desc()).all()
    if request.method == 'POST':
        recipient = db.session.get(Person, request.form.get('recipient_id', type=int) or 0)
        title = (request.form.get('title') or '').strip()
        if not recipient or recipient.family_id != fid:
            flash('Choose who the registry is for.', 'error')
            return render_template('registry_form.html', people=people, events=events)
        if not title:
            flash('Give the registry a title.', 'error')
            return render_template('registry_form.html', people=people, events=events)
        event_id = request.form.get('event_id', type=int) or None
        if event_id:
            ev = db.session.get(Event, event_id)
            if not ev or ev.family_id != fid:
                event_id = None
        reg = GiftRegistry(family_id=fid, recipient_person_id=recipient.id,
                           event_id=event_id, title=title[:150], created_by_id=_my_person_id())
        db.session.add(reg)
        db.session.commit()
        flash('Registry created.', 'info')
        return redirect(url_for('main.registry_detail', registry_id=reg.id))
    return render_template('registry_form.html', people=people, events=events)


@main.route('/registries/<int:registry_id>')
@login_required
@requires_plan
def registry_detail(registry_id):
    reg = _get_registry_or_404(registry_id)
    _block_recipient(reg)
    return render_template('registry_detail.html', reg=reg, my_person_id=_my_person_id())


@main.route('/registries/<int:registry_id>/items', methods=['POST'])
@login_required
@requires_plan
def registry_add_item(registry_id):
    reg = _get_registry_or_404(registry_id)
    _block_recipient(reg)
    name = (request.form.get('name') or '').strip()
    if not name:
        flash('Add a name for the gift.', 'error')
        return redirect(url_for('main.registry_detail', registry_id=reg.id))
    db.session.add(GiftRegistryItem(
        registry_id=reg.id, name=name[:200],
        url=((request.form.get('url') or '').strip()[:500] or None),
        notes=((request.form.get('notes') or '').strip()[:300] or None)))
    db.session.commit()
    flash('Gift idea added.', 'info')
    return redirect(url_for('main.registry_detail', registry_id=reg.id))


@main.route('/registries/items/<int:item_id>/claim', methods=['POST'])
@login_required
@requires_plan
def registry_claim_item(item_id):
    item = db.session.get(GiftRegistryItem, item_id)
    if not item:
        abort(404)
    reg = item.registry
    if reg.family_id != current_user.active_family_id:
        abort(404)
    _block_recipient(reg)
    mine = _my_person_id()
    if item.claimed_by_person_id and item.claimed_by_person_id != mine:
        flash('Someone has already claimed that gift.', 'error')
    elif item.claimed_by_person_id == mine:
        item.claimed_by_person_id = None   # toggle off — I changed my mind
        db.session.commit()
    else:
        item.claimed_by_person_id = mine
        db.session.commit()
    return redirect(url_for('main.registry_detail', registry_id=reg.id))


@main.route('/registries/<int:registry_id>/delete', methods=['POST'])
@login_required
@requires_plan
def registry_delete(registry_id):
    reg = _get_registry_or_404(registry_id)
    _block_recipient(reg)
    if reg.created_by_id != _my_person_id() and not current_user.active_is_admin:
        abort(403)
    db.session.delete(reg)
    db.session.commit()
    flash('Registry deleted.', 'info')
    return redirect(url_for('main.registries'))
