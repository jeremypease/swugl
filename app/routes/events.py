"""Events and every sub-feature: RSVPs, meals, assignments, sleeping,
carpool, surveys, comments, payments, and the AI event parser."""
import re
from datetime import date, datetime, timedelta

from flask import (render_template, request, redirect, url_for, flash, abort,
                   jsonify, current_app, Response)
from flask_login import login_required, current_user

from .. import db
from ..billing import requires_plan, family_has_paid_access, FREE_EVENT_LIMIT
from ..storage import upload_photo, delete_object
from ..models import (Family, User, Person, Event, EventMeal, EventMealItem,
    EventAssignment, AssignmentTask, ASSIGNMENT_CATEGORIES, EventRSVP,
    EventSleepingSpot, SPOT_TYPES, EventComment, EventPaymentConfig,
    EventPaymentRecord, FamilyPayoutAccount, Location, Album, Photo, NotificationPreference,
    CarpoolOffer, EventSurveyResponse, SpouseRelationship, ParentRelationship,
    EventAgendaItem)
from ..forms import (EventForm, EventCommentForm, EventMealForm,
    EventMealFamilyAssignForm, EventMealItemForm, EventMealSelfSignupForm,
    EventMealAssignForm, EventAssignmentForm, EventAssignmentAdminAssignForm,
    EventSleepingSpotForm, EventSleepingAssignForm, EventAgendaItemForm)
from . import main, admin_required, contributor_or_admin_required

# ── Events ────────────────────────────────────────────────────────────────────

@main.route('/events')
@login_required
def events_list():
    from sqlalchemy import func as _func
    today = date.today()
    all_events = Event.query.filter_by(family_id=current_user.active_family_id).order_by(Event.start_date).all()
    upcoming = [e for e in all_events if e.start_date >= today]
    past = [e for e in all_events if e.start_date < today]
    past.reverse()

    # Virtual future occurrences for recurring past events
    class _VirtualOccurrence:
        is_virtual = True
        def __init__(self, event, display_date):
            self._event = event
            self.start_date = display_date
        def __getattr__(self, name):
            return getattr(self._event, name)

    for e in list(past):
        if e.recur_freq or e.is_annual:
            next_d = e.next_occurrence(today - timedelta(days=1))
            if next_d:
                upcoming.append(_VirtualOccurrence(e, next_d))
    upcoming.sort(key=lambda e: e.start_date)
    has_paid_access = family_has_paid_access(current_user.active_family)

    # Current user's RSVP status on each upcoming event
    me = current_user.person
    rsvp_map = {}
    if me and upcoming:
        for row in EventRSVP.query.filter(
            EventRSVP.event_id.in_([e.id for e in upcoming]),
            EventRSVP.person_id == me.id
        ).all():
            rsvp_map[row.event_id] = row.status

    # Yes-RSVP headcounts for all events (single query)
    rsvp_counts = {}
    if all_events:
        for eid, cnt in db.session.query(
            EventRSVP.event_id, _func.count(EventRSVP.id)
        ).filter(
            EventRSVP.event_id.in_([e.id for e in all_events]),
            EventRSVP.status == 'yes'
        ).group_by(EventRSVP.event_id).all():
            rsvp_counts[eid] = cnt

    # Photo counts for past events (single query via album join)
    photo_counts = {}
    if past:
        for eid, cnt in db.session.query(
            Album.event_id, _func.count(Photo.id)
        ).join(Photo, Photo.album_id == Album.id).filter(
            Album.event_id.in_([e.id for e in past])
        ).group_by(Album.event_id).all():
            if eid:
                photo_counts[eid] = cnt

    # Group past events by year for display
    past_by_year = []
    for e in past:
        year = e.start_date.year
        if not past_by_year or past_by_year[-1][0] != year:
            past_by_year.append((year, []))
        past_by_year[-1][1].append(e)

    return render_template('events_list.html', upcoming=upcoming, past=past,
                           past_by_year=past_by_year,
                           has_paid_access=has_paid_access,
                           event_limit=FREE_EVENT_LIMIT,
                           rsvp_map=rsvp_map, rsvp_counts=rsvp_counts,
                           photo_counts=photo_counts,
                           me=me, today=today)


@main.route('/events/ai-parse', methods=['POST'])
@login_required
@admin_required
def event_ai_parse():
    """Parse a natural-language event description into structured fields using Claude."""
    import anthropic as _anthropic
    from flask import jsonify
    description = (request.json or {}).get('description', '').strip()
    if not description:
        return jsonify({'error': 'No description provided'}), 400

    api_key = current_app.config.get('ANTHROPIC_API_KEY')
    if not api_key:
        return jsonify({'error': 'AI not configured'}), 503

    today = date.today().isoformat()
    prompt = f"""Today is {today}. Extract structured event details from the description below.
Return ONLY valid JSON with these exact keys (omit keys you cannot determine):
- name (string)
- kind (one of: Reunion, Holiday, Birthday, Camping, Wedding, Graduation, Other)
- start_date (YYYY-MM-DD)
- end_date (YYYY-MM-DD, only if multi-day)
- start_time (HH:MM 24h, only if mentioned)
- end_time (HH:MM 24h, only if mentioned)
- location (string)
- description (string, a short summary)
- has_meals (true/false)
- has_sleeping (true/false)
- has_assignments (true/false)
- has_carpool (true/false)
- rooms (array of {{"name": string, "capacity": number}} — only if sleeping spots are described)

Description: {description}"""

    try:
        client = _anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model='claude-haiku-4-5-20251001',
            max_tokens=512,
            messages=[{'role': 'user', 'content': prompt}],
        )
        import json as _json
        text = msg.content[0].text.strip()
        # Strip markdown code fences if present
        if text.startswith('```'):
            text = text.split('\n', 1)[1].rsplit('```', 1)[0].strip()
        data = _json.loads(text)
        return jsonify(data)
    except Exception as e:
        current_app.logger.error(f'AI event parse error: {e}')
        return jsonify({'error': 'AI parsing failed'}), 500


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
    except Exception as e:
        current_app.logger.error(f'AI photo caption error: {e}')
        return jsonify({'error': 'AI caption failed'}), 500


def _geocode_location(location_str):
    """Return (lat, lng) for a location string, or (None, None)."""
    if not location_str:
        return None, None
    try:
        import requests as _req
        resp = _req.get(
            'https://nominatim.openstreetmap.org/search',
            params={'q': location_str, 'format': 'json', 'limit': 1},
            headers={'User-Agent': 'swugl-family-app/1.0'},
            timeout=5,
        )
        results = resp.json()
        if results:
            return float(results[0]['lat']), float(results[0]['lon'])
    except Exception:
        pass
    return None, None


@main.route('/events/add', methods=['GET', 'POST'])
@login_required
@admin_required
def event_add():
    family = current_user.active_family
    has_paid_access = family_has_paid_access(family)
    saved_locations = (Location.query
                       .filter_by(family_id=current_user.active_family_id)
                       .order_by(Location.name).all())
    form = EventForm()
    if form.validate_on_submit():
        if not has_paid_access:
            upcoming_count = Event.query.filter(
                Event.family_id == current_user.active_family_id,
                Event.start_date >= date.today()
            ).count()
            if upcoming_count >= FREE_EVENT_LIMIT:
                flash(f'Free plan is limited to {FREE_EVENT_LIMIT} upcoming events. '
                      'Upgrade to create unlimited events.', 'warning')
                return redirect(url_for('billing.billing_page'))
        if form.end_date.data and form.start_date.data and form.end_date.data < form.start_date.data:
            form.end_date.errors.append('End date cannot be before start date.')
            return render_template('event_form.html', form=form, event=None, saved_locations=saved_locations)
        loc_id = int(form.location_id.data) if form.location_id.data else None
        saved_loc = db.session.get(Location, loc_id) if loc_id else None
        if saved_loc and saved_loc.family_id == current_user.active_family_id:
            location = saved_loc.address or saved_loc.name
            lat, lng = saved_loc.lat, saved_loc.lng
        else:
            loc_id = None
            location = form.location.data or None
            lat, lng = _geocode_location(location)
        _paid = family_has_paid_access(current_user.active_family)
        event = Event(
            family_id=current_user.active_family_id,
            name=form.name.data,
            kind=form.kind.data or None,
            description=form.description.data or None,
            location=location,
            location_id=loc_id,
            lat=lat,
            lng=lng,
            start_date=form.start_date.data,
            end_date=form.end_date.data,
            start_time=form.start_time.data,
            end_time=form.end_time.data,
            rsvp_deadline=form.rsvp_deadline.data,
            recur_freq=form.recur_freq.data or None,
            recur_until=form.recur_until.data,
            is_annual=(form.recur_freq.data == 'yearly'),
            # Paid sections silently drop to off on the free plan (the form
            # shows them disabled with an upgrade hint)
            has_meals=form.has_meals.data and _paid,
            has_assignments=form.has_assignments.data and _paid,
            has_sleeping=form.has_sleeping.data and _paid,
            has_carpool=form.has_carpool.data,
        )
        db.session.add(event)
        db.session.flush()

        # Create sleeping spots submitted with the form
        from ..models import EventSleepingSpot
        room_index = 0
        while True:
            room_name = request.form.get(f'rooms[{room_index}][name]', '').strip()
            if not room_name:
                break
            try:
                capacity = int(request.form.get(f'rooms[{room_index}][capacity]', '') or 0) or None
            except ValueError:
                capacity = None
            room_type = request.form.get(f'rooms[{room_index}][type]', '').strip() or None
            if room_type and room_type not in SPOT_TYPES:
                room_type = None
            db.session.add(EventSleepingSpot(event_id=event.id, name=room_name, spot_type=room_type, capacity=capacity))
            room_index += 1
        # Fallback: seed from location template if no rooms were submitted
        if room_index == 0 and event.has_sleeping and saved_loc and saved_loc.sleeping_spots:
            for spot in saved_loc.sleeping_spots:
                db.session.add(EventSleepingSpot(
                    event_id=event.id, name=spot.name,
                    spot_type=spot.spot_type, capacity=spot.capacity,
                ))

        # Seed meals from the day-grid checkboxes on the form
        _MEAL_LABELS = {'breakfast': 'Breakfast', 'lunch': 'Lunch', 'dinner': 'Dinner'}
        _MEAL_TIMES  = {'breakfast': '8:00 AM',   'lunch': '12:00 PM', 'dinner': '6:00 PM'}
        for key in request.form:
            m = re.match(r'^meals\[(\d{4}-\d{2}-\d{2})\]\[(breakfast|lunch|dinner)\]$', key)
            if m:
                try:
                    meal_date_val = date.fromisoformat(m.group(1))
                except ValueError:
                    continue
                meal_type = m.group(2)
                db.session.add(EventMeal(
                    event_id=event.id,
                    name=f'{meal_date_val.strftime("%A")} {_MEAL_LABELS[meal_type]}',
                    meal_date=meal_date_val,
                    meal_time=_MEAL_TIMES[meal_type],
                ))

        # Seed assignments from the task seed list
        for ti in range(50):
            task_title = request.form.get(f'tasks[{ti}][title]', '').strip()
            if not task_title:
                continue
            task_cat = request.form.get(f'tasks[{ti}][category]', '').strip() or None
            if task_cat and task_cat not in ASSIGNMENT_CATEGORIES:
                task_cat = None
            db.session.add(EventAssignment(event_id=event.id, title=task_title[:150], category=task_cat))

        if form.cover_image.data and hasattr(form.cover_image.data, 'filename') and form.cover_image.data.filename:
            key = upload_photo(form.cover_image.data, folder='events')
            if key:
                event.cover_image_path = key
        db.session.commit()
        flash(f'{event.name} has been created.', 'info')
        from ..notifications import notify
        recipients = User.query.filter_by(
            family_id=current_user.active_family_id, status='approved'
        ).filter(User.id != current_user.id).all()
        event_url = url_for('main.event_detail', event_id=event.id, _external=True)
        notify(recipients, 'new_event', event=event, url=event_url)
        return redirect(url_for('main.event_detail', event_id=event.id))
    return render_template('event_form.html', form=form, event=None, saved_locations=saved_locations)


@main.route('/events/<int:event_id>')
@login_required
def event_detail(event_id):
    event = db.session.get(Event, event_id)
    if not event or event.family_id != current_user.active_family_id:
        flash('Event not found.', 'error')
        return redirect(url_for('main.events_list'))
    all_people = Person.query.filter_by(family_id=current_user.active_family_id).order_by(Person.name).all()
    dir_people = [p for p in all_people if p.in_directory]
    people_choices = [(0, '— Select —')] + [(p.id, p.get_display_name()) for p in dir_people]

    meal_form = EventMealForm()
    meal_item_form = EventMealItemForm()
    # Per-meal family-assign forms (admin only) — deduplicated couples, directory members only
    couple_people = [p for p in dir_people if not p.get_active_spouse() or p.id < p.get_active_spouse().id]
    meal_family_forms = {}
    for meal in event.meals:
        f = EventMealFamilyAssignForm(prefix=f'meal_fam_{meal.id}')
        f.assigned_family_id.choices = [(0, '— None —')] + [(p.id, p.get_couple_name()) for p in couple_people]
        meal_family_forms[meal.id] = f
    # Per-item assign forms — directory members only
    item_assign_forms = {}
    for meal in event.meals:
        for item in meal.items:
            f = EventMealAssignForm(prefix=f'item_{item.id}')
            f.person_id.choices = people_choices
            item_assign_forms[item.id] = f

    assign_form = EventAssignmentForm()
    # Per-assignment admin-assign forms — directory members only
    assign_admin_forms = {}
    if current_user.active_is_admin:
        for a in event.assignments:
            f = EventAssignmentAdminAssignForm(prefix=f'a_{a.id}')
            f.person_id.choices = people_choices
            assign_admin_forms[a.id] = f

    spot_form = EventSleepingSpotForm()
    couple_people = [p for p in dir_people if not p.get_active_spouse() or p.id < p.get_active_spouse().id]
    sleeping_assign_forms = {}
    if current_user.active_is_admin:
        for spot in event.sleeping_spots:
            spot_assigned_ids = {p.id for p in spot.people}
            available = [(p.id, p.get_display_name()) for p in all_people if p.in_directory and p.id not in spot_assigned_ids]
            f = EventSleepingAssignForm(prefix=f'spot_{spot.id}')
            f.person_id.choices = [(0, '— Select —')] + available
            sleeping_assign_forms[spot.id] = f

    self_signup_form = EventMealSelfSignupForm()
    my_person = current_user.person
    event_form = EventForm(obj=event)
    comment_form = EventCommentForm()

    # RSVP data
    rsvp_map = {r.person_id: r.status for r in event.rsvps}
    # Household = self + spouse + minor children (under 18, or unknown age)
    household = []
    if my_person:
        household.append(my_person)
        spouse = my_person.get_active_spouse()
        if spouse:
            household.append(spouse)
        child_ids = set()
        for rel in my_person.child_rels:
            child_ids.add(rel.child_id)
        if spouse:
            for rel in spouse.child_rels:
                child_ids.add(rel.child_id)
        unmarried_children = sorted(
            [p for p in all_people if p.id in child_ids and _in_parent_household(p)],
            key=lambda p: p.get_display_name()
        )
        household.extend(unmarried_children)

    # Build grouped RSVP summary: one entry per household unit
    rsvp_groups = _build_rsvp_groups(event, all_people)
    # Full family groups — always built; used for stats + non-responder list for everyone
    _all_fg = _build_family_groups(all_people, rsvp_map)
    family_groups = _all_fg if (current_user.active_is_admin or current_user.active_is_delegate) else []

    rsvp_stats = {
        'yes_people':  sum(1 for s in rsvp_map.values() if s == 'yes'),
        'maybe_people': sum(1 for s in rsvp_map.values() if s == 'maybe'),
        'no_people':   sum(1 for s in rsvp_map.values() if s == 'no'),
        'yes_households': sum(
            1 for g in _all_fg
            if any(s == 'yes' for _, s in g['adults'] + g['children'])
        ),
    }
    not_responded = [
        g['label'] for g in _all_fg
        if all(s is None for _, s in g['adults'] + g['children'])
    ]

    from ..weather import get_event_weather
    try:
        weather = get_event_weather(event)
    except Exception:
        weather = None

    payment_config = event.payment_config if event.payment_config and event.payment_config.is_active else None
    my_payment = None
    my_charge_cents = None
    if payment_config and current_user.is_authenticated:
        my_payment = EventPaymentRecord.query.filter_by(
            event_id=event.id, payer_user_id=current_user.id
        ).first()
        my_charge_cents = _compute_member_charge(payment_config, current_user)

    # Admin progress stats
    payment_stats = None
    if payment_config and current_user.active_is_admin:
        paid_records = EventPaymentRecord.query.filter_by(event_id=event.id, status='paid').all()
        total_amount = sum(r.amount_cents for r in paid_records)
        payment_stats = {
            'paid_count': len(paid_records),
            'total_cents': total_amount,
        }

    _edr, _cur = [], event.start_date
    while _cur <= (event.end_date or event.start_date):
        _edr.append(_cur)
        _cur += timedelta(days=1)

    agenda_form = EventAgendaItemForm()
    # Group agenda items by date preserving chronological order within each day.
    agenda_days = {}
    for item in event.agenda_items:
        agenda_days.setdefault(item.item_date, []).append(item)

    event_photos = Photo.query.join(Album).filter(
        Album.event_id == event.id,
        Album.family_id == current_user.active_family_id,
    ).order_by(Photo.created_at.asc()).limit(12).all()

    has_paid_access = family_has_paid_access(current_user.family)

    return render_template('event_detail.html',
        event=event,
        event_date_range=_edr,
        event_photos=event_photos,
        has_paid_access=has_paid_access,
        meal_form=meal_form,
        meal_item_form=meal_item_form,
        meal_family_forms=meal_family_forms,
        item_assign_forms=item_assign_forms,
        people_choices=people_choices,
        assign_form=assign_form,
        assign_admin_forms=assign_admin_forms,
        self_signup_form=self_signup_form,
        spot_form=spot_form,
        sleeping_assign_forms=sleeping_assign_forms,
        couple_people=couple_people,
        my_person=my_person,
        event_form=event_form,
        comment_form=comment_form,
        assignment_categories=ASSIGNMENT_CATEGORIES,
        rsvp_map=rsvp_map,
        rsvp_stats=rsvp_stats,
        not_responded=not_responded,
        household=household,
        rsvp_groups=rsvp_groups,
        family_groups=family_groups,
        all_people=all_people,
        weather=weather,
        payment_config=payment_config,
        my_payment=my_payment,
        my_charge_cents=my_charge_cents,
        payment_stats=payment_stats,
        payout_account=current_user.family.payout_account,
        agenda_form=agenda_form,
        agenda_days=agenda_days,
    )


def _qr_data_uri(text):
    """A PNG data-URI QR code for `text`, for the printable schedule (#74)."""
    import io, base64, qrcode
    img = qrcode.make(text, box_size=6, border=2)
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    return 'data:image/png;base64,' + base64.b64encode(buf.getvalue()).decode()


@main.route('/events/<int:event_id>/schedule')
@login_required
def event_schedule(event_id):
    """Clean, printer-/share-friendly schedule for an event (#74)."""
    event = db.session.get(Event, event_id)
    if not event:
        abort(404)
    if event.family_id != current_user.active_family_id:
        abort(403)
    drivers = [o for o in event.carpool_offers if o.role == 'driver']
    riders = [o for o in event.carpool_offers if o.role == 'rider']
    # Group meals by day (dated first, undated last) so multi-day events read
    # as a day-by-day agenda.
    from collections import OrderedDict
    meal_days = OrderedDict()
    for m in sorted(event.meals,
                    key=lambda m: (m.meal_date is None, m.meal_date or date.min, m.meal_time or '')):
        meal_days.setdefault(m.meal_date, []).append(m)
    # Group agenda items by date, same pattern as meals.
    agenda_days = {}
    for item in event.agenda_items:
        agenda_days.setdefault(item.item_date, []).append(item)
    live_url = url_for('main.event_detail', event_id=event.id, _external=True)
    return render_template('event/schedule.html', event=event, meal_days=meal_days,
                           agenda_days=agenda_days,
                           drivers=drivers, riders=riders,
                           live_url=live_url, qr=_qr_data_uri(live_url))


@main.route('/events/<int:event_id>/edit', methods=['GET', 'POST'])
@login_required
@admin_required
def event_edit(event_id):
    event = db.session.get(Event, event_id)
    if not event or event.family_id != current_user.active_family_id:
        flash('Event not found.', 'error')
        return redirect(url_for('main.events_list'))
    saved_locations = (Location.query
                       .filter_by(family_id=current_user.active_family_id)
                       .order_by(Location.name).all())
    form = EventForm(obj=event)
    if form.validate_on_submit():
        if form.end_date.data and form.start_date.data and form.end_date.data < form.start_date.data:
            form.end_date.errors.append('End date cannot be before start date.')
            return render_template('event_form.html', form=form, event=event, saved_locations=saved_locations)
        # Snapshot the fields worth notifying about, so a typo fix stays quiet.
        _before = (event.start_date, event.start_time, event.end_date,
                   event.end_time, event.location)
        event.name = form.name.data
        event.kind = form.kind.data or None
        event.description = form.description.data or None
        loc_id = int(form.location_id.data) if form.location_id.data else None
        saved_loc = db.session.get(Location, loc_id) if loc_id else None
        if saved_loc and saved_loc.family_id == current_user.active_family_id:
            new_location = saved_loc.address or saved_loc.name
            event.lat, event.lng = saved_loc.lat, saved_loc.lng
            event.location_id = loc_id
        else:
            event.location_id = None
            new_location = form.location.data or None
            if new_location != event.location:
                event.lat, event.lng = _geocode_location(new_location)
        event.location = new_location
        event.start_date = form.start_date.data
        event.end_date = form.end_date.data
        event.start_time = form.start_time.data
        event.end_time = form.end_time.data
        event.rsvp_deadline = form.rsvp_deadline.data
        event.recur_freq = form.recur_freq.data or None
        event.recur_until = form.recur_until.data
        event.is_annual = (form.recur_freq.data == 'yearly')
        # Free plan can turn paid sections off but not on; already-enabled
        # sections survive a downgrade untouched
        _paid = family_has_paid_access(current_user.active_family)
        event.has_meals = form.has_meals.data if _paid else (event.has_meals and form.has_meals.data)
        event.has_assignments = form.has_assignments.data if _paid else (event.has_assignments and form.has_assignments.data)
        event.has_sleeping = form.has_sleeping.data if _paid else (event.has_sleeping and form.has_sleeping.data)
        event.has_carpool = form.has_carpool.data
        if form.remove_cover.data and event.cover_image_path:
            delete_object(event.cover_image_path)
            event.cover_image_path = None
        elif form.cover_image.data and hasattr(form.cover_image.data, 'filename') and form.cover_image.data.filename:
            delete_object(event.cover_image_path)
            key = upload_photo(form.cover_image.data, folder='events')
            if key:
                event.cover_image_path = key
        db.session.commit()
        # Only ping the family when the date/time/place actually moved.
        if _before != (event.start_date, event.start_time, event.end_date,
                       event.end_time, event.location):
            from ..notifications import notify_family
            notify_family(
                event.family_id, 'event_updated',
                title=f'{event.name} was updated',
                body=event.date_range_display(),
                url=url_for('main.event_detail', event_id=event.id, _external=True),
                exclude_user_id=current_user.id,
            )
        flash('Event updated.', 'info')
        return redirect(url_for('main.event_detail', event_id=event.id))
    # Pre-populate location_id from the event for the edit form
    if not form.location_id.data and event.location_id:
        form.location_id.data = str(event.location_id)
    return render_template('event_form.html', form=form, event=event, saved_locations=saved_locations)


@main.route('/events/<int:event_id>/payment/setup', methods=['POST'])
@login_required
@admin_required
@requires_plan
def event_payment_setup(event_id):
    event = db.session.get(Event, event_id)
    if not event or event.family_id != current_user.active_family_id:
        flash('Event not found.', 'error')
        return redirect(url_for('main.events_list'))

    try:
        amount_dollars = float(request.form.get('amount_dollars', 0))
        amount_cents = int(round(amount_dollars * 100))
    except (ValueError, TypeError):
        flash('Invalid amount.', 'error')
        return redirect(url_for('main.event_detail', event_id=event_id))

    if amount_cents < 50:
        flash('Amount must be at least $0.50.', 'error')
        return redirect(url_for('main.event_detail', event_id=event_id))

    charge_type = request.form.get('charge_type', 'per_family')
    if charge_type not in ('per_family', 'per_person'):
        charge_type = 'per_family'

    family_cap_cents = None
    if charge_type == 'per_person':
        cap_str = request.form.get('family_cap_dollars', '').strip()
        if cap_str:
            try:
                cap_val = int(round(float(cap_str) * 100))
                if cap_val > amount_cents:
                    family_cap_cents = cap_val
            except (ValueError, TypeError):
                pass

    deadline_str = request.form.get('deadline', '').strip()
    deadline = datetime.strptime(deadline_str, '%Y-%m-%d').date() if deadline_str else None

    config = event.payment_config
    if config:
        config.amount_cents = amount_cents
        config.charge_type = charge_type
        config.family_cap_cents = family_cap_cents
        config.description = request.form.get('description', '').strip()[:200]
        config.deadline = deadline
        config.is_active = True
    else:
        config = EventPaymentConfig(
            event_id=event.id,
            amount_cents=amount_cents,
            charge_type=charge_type,
            family_cap_cents=family_cap_cents,
            description=request.form.get('description', '').strip()[:200],
            deadline=deadline,
        )
        db.session.add(config)

    db.session.commit()
    flash('Payment collection enabled.', 'success')
    return redirect(url_for('main.event_detail', event_id=event_id))


@main.route('/events/<int:event_id>/payment/disable', methods=['POST'])
@login_required
@admin_required
def event_payment_disable(event_id):
    event = db.session.get(Event, event_id)
    if not event or event.family_id != current_user.active_family_id:
        flash('Event not found.', 'error')
        return redirect(url_for('main.events_list'))
    if event.payment_config:
        event.payment_config.is_active = False
        db.session.commit()
    flash('Payment collection disabled.', 'info')
    return redirect(url_for('main.event_detail', event_id=event_id))


@main.route('/events/<int:event_id>/payment/checkout', methods=['POST'])
@login_required
def event_payment_checkout(event_id):
    from ..billing import _stripe
    event = db.session.get(Event, event_id)
    if not event or event.family_id != current_user.active_family_id:
        flash('Event not found.', 'error')
        return redirect(url_for('main.events_list'))

    config = event.payment_config
    if not config or not config.is_active:
        flash('Payment is not enabled for this event.', 'error')
        return redirect(url_for('main.event_detail', event_id=event_id))

    existing = EventPaymentRecord.query.filter_by(
        event_id=event.id, payer_user_id=current_user.id
    ).first()
    if existing and existing.status == 'paid':
        flash('You have already paid for this event.', 'info')
        return redirect(url_for('main.event_detail', event_id=event_id))

    charge_amount = _compute_member_charge(config, current_user)
    yes_in_household = 0
    if config.charge_type == 'per_person':
        household_ids = _get_household_ids(current_user.person)
        yes_in_household = EventRSVP.query.filter(
            EventRSVP.event_id == event.id,
            EventRSVP.status == 'yes',
            EventRSVP.person_id.in_(household_ids),
        ).count() if household_ids else 1

    s = _stripe()
    if not s:
        flash('Payment processing is not configured yet.', 'error')
        return redirect(url_for('main.event_detail', event_id=event_id))

    record = existing or EventPaymentRecord(
        event_id=event.id,
        payer_user_id=current_user.id,
        amount_cents=charge_amount,
        status='pending',
    )
    if not existing:
        db.session.add(record)
    else:
        record.amount_cents = charge_amount
        record.status = 'pending'
    db.session.commit()

    description = config.description or event.name
    if config.charge_type == 'per_person' and yes_in_household > 1:
        capped = config.family_cap_cents and charge_amount < config.amount_cents * yes_in_household
        if capped:
            description = f'{description} ({yes_in_household} people, capped at ${charge_amount/100:.2f})'
        else:
            description = f'{description} ({yes_in_household} people × ${config.amount_dollars:.2f})'

    family = current_user.family
    kwargs = dict(
        mode='payment',
        line_items=[{
            'price_data': {
                'currency': 'usd',
                'unit_amount': charge_amount,
                'product_data': {'name': description},
            },
            'quantity': 1,
        }],
        success_url=url_for('main.event_payment_success', event_id=event_id, _external=True) + '?session_id={CHECKOUT_SESSION_ID}',
        cancel_url=url_for('main.event_detail', event_id=event_id, _external=True),
        client_reference_id=str(current_user.id),
        metadata={
            'payment_type': 'event',
            'event_id': str(event.id),
            'payer_user_id': str(current_user.id),
            'family_id': str(family.id),
        },
    )
    if family.stripe_customer_id:
        kwargs['customer'] = family.stripe_customer_id
    else:
        kwargs['customer_email'] = current_user.email

    try:
        session_obj = s.checkout.Session.create(**kwargs)
        record.stripe_checkout_session_id = session_obj.id
        db.session.commit()
        return redirect(session_obj.url, code=303)
    except Exception as e:
        flash('Could not start checkout. Please try again.', 'error')
        current_app.logger.error(f'Stripe event checkout error: {e}')
        return redirect(url_for('main.event_detail', event_id=event_id))


@main.route('/events/<int:event_id>/payment/success')
@login_required
def event_payment_success(event_id):
    event = db.session.get(Event, event_id)
    if not event or event.family_id != current_user.active_family_id:
        return redirect(url_for('main.events_list'))
    flash('Payment received! You\'re all set.', 'success')
    return redirect(url_for('main.event_detail', event_id=event_id))


def _compute_member_charge(config, user):
    """Return charge in cents for this user given event payment config."""
    if config.charge_type == 'per_person':
        hids = _get_household_ids(user.person)
        yes_count = EventRSVP.query.filter(
            EventRSVP.event_id == config.event_id,
            EventRSVP.status == 'yes',
            EventRSVP.person_id.in_(hids),
        ).count() if hids else 1
        total = config.amount_cents * max(1, yes_count)
        if config.family_cap_cents:
            total = min(total, config.family_cap_cents)
        return total
    return config.amount_cents


@main.route('/events/<int:event_id>/delete', methods=['POST'])
@login_required
@admin_required
def event_delete(event_id):
    event = db.session.get(Event, event_id)
    if not event or event.family_id != current_user.active_family_id:
        flash('Event not found.', 'error')
        return redirect(url_for('main.events_list'))
    db.session.delete(event)
    db.session.commit()
    flash(f'{event.name} has been deleted.', 'info')
    return redirect(url_for('main.events_list'))


@main.route('/events/<int:event_id>/enable-section', methods=['POST'])
@login_required
@admin_required
def event_enable_section(event_id):
    event = db.session.get(Event, event_id)
    if not event or event.family_id != current_user.active_family_id:
        return redirect(url_for('main.events_list'))
    section = request.form.get('section')
    # Meals/assignments/sleeping are organizer power tools — paid plan only.
    # Carpool stays free. Already-enabled sections keep working after a
    # downgrade; only turning new ones on is gated.
    if section in ('meals', 'assignments', 'sleeping') and not family_has_paid_access(current_user.active_family):
        flash('Meal planning, assignments, and sleeping arrangements are paid features. '
              'Upgrade to enable them.', 'warning')
        return redirect(url_for('billing.billing_page'))
    if section == 'meals':
        event.has_meals = True
    elif section == 'assignments':
        event.has_assignments = True
    elif section == 'sleeping':
        event.has_sleeping = True
    elif section == 'carpool':
        event.has_carpool = True
    elif section == 'agenda':
        event.has_agenda = True
    db.session.commit()
    return redirect(url_for('main.event_detail', event_id=event_id))


@main.route('/events/<int:event_id>/disable-section', methods=['POST'])
@login_required
@admin_required
def event_disable_section(event_id):
    event = db.session.get(Event, event_id)
    if not event or event.family_id != current_user.active_family_id:
        return redirect(url_for('main.events_list'))
    section = request.form.get('section')
    if section == 'meals':
        event.has_meals = False
    elif section == 'assignments':
        event.has_assignments = False
    elif section == 'sleeping':
        event.has_sleeping = False
    elif section == 'carpool':
        event.has_carpool = False
    elif section == 'agenda':
        event.has_agenda = False
    db.session.commit()
    return redirect(url_for('main.event_detail', event_id=event_id))


# ── Carpool ───────────────────────────────────────────────────────────────────

@main.route('/events/<int:event_id>/carpool/offer', methods=['POST'])
@login_required
def carpool_offer(event_id):
    from ..models import CarpoolOffer
    event = db.session.get(Event, event_id)
    if not event or event.family_id != current_user.active_family_id or not current_user.person:
        return redirect(url_for('main.events_list'))
    role = request.form.get('role', 'rider')
    if role not in ('driver', 'rider'):
        role = 'rider'
    seats = request.form.get('seats', type=int)
    departure_from = request.form.get('departure_from', '').strip()[:150] or None
    notes = request.form.get('notes', '').strip()[:200] or None
    existing = CarpoolOffer.query.filter_by(event_id=event_id, person_id=current_user.person.id).first()
    if existing:
        existing.role = role
        existing.seats = seats if role == 'driver' else None
        existing.departure_from = departure_from if role == 'driver' else None
        existing.notes = notes
        if role == 'driver':
            existing.passenger_of_id = None  # switching to driver clears any ride claim
    else:
        db.session.add(CarpoolOffer(
            event_id=event_id, person_id=current_user.person.id,
            role=role,
            seats=seats if role == 'driver' else None,
            departure_from=departure_from if role == 'driver' else None,
            notes=notes,
        ))
    db.session.commit()
    return redirect(url_for('main.event_detail', event_id=event_id))


@main.route('/events/<int:event_id>/carpool/remove', methods=['POST'])
@login_required
def carpool_remove(event_id):
    from ..models import CarpoolOffer
    if current_user.person:
        # Clear any passengers assigned to this person's driver offer first
        offer = CarpoolOffer.query.filter_by(event_id=event_id, person_id=current_user.person.id).first()
        if offer:
            CarpoolOffer.query.filter_by(passenger_of_id=offer.id).update({'passenger_of_id': None})
            db.session.delete(offer)
            db.session.commit()
    return redirect(url_for('main.event_detail', event_id=event_id))


@main.route('/events/<int:event_id>/carpool/<int:offer_id>/claim', methods=['POST'])
@login_required
def carpool_claim_seat(event_id, offer_id):
    from ..models import CarpoolOffer
    event = db.session.get(Event, event_id)
    if not event or event.family_id != current_user.active_family_id or not current_user.person:
        return redirect(url_for('main.events_list'))
    driver_offer = db.session.get(CarpoolOffer, offer_id)
    if not driver_offer or driver_offer.event_id != event_id or driver_offer.role != 'driver':
        flash('Driver not found.', 'error')
        return redirect(url_for('main.event_detail', event_id=event_id))
    # Check capacity
    if driver_offer.seats:
        taken = CarpoolOffer.query.filter_by(passenger_of_id=offer_id).count()
        if taken >= driver_offer.seats:
            flash(f'{driver_offer.person.get_display_name()}\'s car is full.', 'error')
            return redirect(url_for('main.event_detail', event_id=event_id))
    existing = CarpoolOffer.query.filter_by(event_id=event_id, person_id=current_user.person.id).first()
    if existing:
        existing.role = 'rider'
        existing.passenger_of_id = offer_id
        existing.seats = None
        existing.departure_from = None
    else:
        db.session.add(CarpoolOffer(
            event_id=event_id, person_id=current_user.person.id,
            role='rider', passenger_of_id=offer_id,
        ))
    db.session.commit()
    flash(f'Seat claimed with {driver_offer.person.get_display_name()}.', 'info')
    return redirect(url_for('main.event_detail', event_id=event_id))


@main.route('/events/<int:event_id>/carpool/unclaim', methods=['POST'])
@login_required
def carpool_unclaim(event_id):
    from ..models import CarpoolOffer
    if current_user.person:
        offer = CarpoolOffer.query.filter_by(event_id=event_id, person_id=current_user.person.id).first()
        if offer:
            offer.passenger_of_id = None
            db.session.commit()
    return redirect(url_for('main.event_detail', event_id=event_id))


@main.route('/events/<int:event_id>/carpool/<int:offer_id>/assign-rider', methods=['POST'])
@login_required
@admin_required
def carpool_assign_rider(event_id, offer_id):
    from ..models import CarpoolOffer
    event = db.session.get(Event, event_id)
    if not event or event.family_id != current_user.active_family_id:
        return redirect(url_for('main.events_list'))
    driver_offer = db.session.get(CarpoolOffer, offer_id)
    if not driver_offer or driver_offer.event_id != event_id or driver_offer.role != 'driver':
        return redirect(url_for('main.event_detail', event_id=event_id))
    try:
        rider_offer_id = int(request.form.get('rider_offer_id', 0))
    except (ValueError, TypeError):
        rider_offer_id = 0
    if not rider_offer_id:
        return redirect(url_for('main.event_detail', event_id=event_id))
    rider_offer = db.session.get(CarpoolOffer, rider_offer_id)
    if not rider_offer or rider_offer.event_id != event_id or rider_offer.role != 'rider':
        return redirect(url_for('main.event_detail', event_id=event_id))
    if driver_offer.seats:
        taken = CarpoolOffer.query.filter_by(passenger_of_id=offer_id).count()
        if taken >= driver_offer.seats and rider_offer.passenger_of_id != offer_id:
            flash(f'{driver_offer.person.get_display_name()}\'s car is full.', 'error')
            return redirect(url_for('main.event_detail', event_id=event_id))
    rider_offer.passenger_of_id = offer_id
    db.session.commit()
    flash(f'{rider_offer.person.get_display_name()} assigned to {driver_offer.person.get_display_name()}.', 'info')
    return redirect(url_for('main.event_detail', event_id=event_id))


# ── Survey ────────────────────────────────────────────────────────────────────

@main.route('/events/<int:event_id>/survey', methods=['GET', 'POST'])
@login_required
def event_survey(event_id):
    from ..models import EventSurveyResponse
    event = db.session.get(Event, event_id)
    if not event or event.family_id != current_user.active_family_id:
        abort(404)
    # Only available after event has passed
    if event.start_date > date.today():
        flash('The survey will be available after the event.', 'info')
        return redirect(url_for('main.event_detail', event_id=event_id))
    my_response = None
    if current_user.person:
        my_response = EventSurveyResponse.query.filter_by(
            event_id=event_id, person_id=current_user.person.id
        ).first()
    if request.method == 'POST':
        if not current_user.person:
            flash('Link your family profile to submit a survey.', 'error')
            return redirect(url_for('main.event_survey', event_id=event_id))
        rating = request.form.get('rating', type=int)
        if not rating or rating < 1 or rating > 5:
            flash('Please select a rating.', 'error')
            return redirect(url_for('main.event_survey', event_id=event_id))
        what_worked = request.form.get('what_worked', '').strip() or None
        suggestions = request.form.get('suggestions', '').strip() or None
        if my_response:
            my_response.rating = rating
            my_response.what_worked = what_worked
            my_response.suggestions = suggestions
        else:
            db.session.add(EventSurveyResponse(
                event_id=event_id, person_id=current_user.person.id,
                rating=rating, what_worked=what_worked, suggestions=suggestions,
            ))
        db.session.commit()
        flash('Thanks for your feedback!', 'success')
        return redirect(url_for('main.event_detail', event_id=event_id))
    return render_template('event_survey.html', event=event, my_response=my_response)


@main.route('/events/<int:event_id>/survey/results')
@login_required
@admin_required
def event_survey_results(event_id):
    from ..models import EventSurveyResponse
    event = db.session.get(Event, event_id)
    if not event or event.family_id != current_user.active_family_id:
        abort(404)
    responses = EventSurveyResponse.query.filter_by(event_id=event_id)\
        .order_by(EventSurveyResponse.submitted_at.desc()).all()
    avg_rating = (sum(r.rating for r in responses) / len(responses)) if responses else None
    return render_template('event_survey_results.html', event=event,
                           responses=responses, avg_rating=avg_rating)


# ── Comments ──────────────────────────────────────────────────────────────────

@main.route('/events/<int:event_id>/comments', methods=['POST'])
@login_required
def event_comment_add(event_id):
    event = db.session.get(Event, event_id)
    if not event or event.family_id != current_user.active_family_id:
        return redirect(url_for('main.events_list'))
    if not current_user.person:
        flash('You need a family profile to comment.', 'error')
        return redirect(url_for('main.event_detail', event_id=event_id))
    form = EventCommentForm()
    if form.validate_on_submit():
        comment = EventComment(
            event_id=event_id,
            person_id=current_user.person.id,
            body=form.body.data.strip(),
        )
        db.session.add(comment)
        db.session.commit()

        from ..notifications import create_notification
        from ..models import NotificationPreference, EventRSVP, Person
        from ..email import send_event_comment_notification
        commenter_name = current_user.person.get_display_name()
        commenter_person_id = current_user.person.id
        attendee_person_ids = {
            r.person_id for r in
            EventRSVP.query.filter(
                EventRSVP.event_id == event_id,
                EventRSVP.status.in_(['yes', 'maybe']),
            ).all()
        }
        body_preview = comment.body[:100] + ('…' if len(comment.body) > 100 else '')
        notif_title = f'{commenter_name} commented on {event.name}'
        event_url = url_for('main.event_detail', event_id=event_id)
        for person_id in attendee_person_ids:
            if person_id == commenter_person_id:
                continue
            person = db.session.get(Person, person_id)
            if not person or not person.user:
                continue
            recipient = person.user
            # In-app + push (create_notification checks in_app preference internally)
            create_notification(recipient, 'event_comment',
                                title=notif_title, body=body_preview, url=event_url)
            # Email
            if (current_app.config.get('MAIL_ENABLED')
                    and NotificationPreference.is_enabled(recipient.id, 'event_comment', 'email')):
                send_event_comment_notification(
                    recipient, commenter_name, event, comment.body, event_url)
    return redirect(url_for('main.event_detail', event_id=event_id))


@main.route('/events/<int:event_id>/comments/<int:comment_id>/delete', methods=['POST'])
@login_required
def event_comment_delete(event_id, comment_id):
    event = db.session.get(Event, event_id)
    comment = db.session.get(EventComment, comment_id)
    if not event or event.family_id != current_user.active_family_id or not comment or comment.event_id != event_id:
        return redirect(url_for('main.events_list'))
    can_delete = current_user.active_is_admin or (current_user.person and comment.person_id == current_user.person.id)
    if can_delete:
        db.session.delete(comment)
        db.session.commit()
    return redirect(url_for('main.event_detail', event_id=event_id))


# ── RSVPs ─────────────────────────────────────────────────────────────────────

@main.route('/events/<int:event_id>/rsvp', methods=['POST'])
@login_required
def event_rsvp(event_id):
    event = db.session.get(Event, event_id)
    if not event or event.family_id != current_user.active_family_id:
        return redirect(url_for('main.events_list'))
    person_id = request.form.get('person_id', type=int)
    status = request.form.get('status')
    if not person_id or status not in ('yes', 'no', 'maybe', 'clear'):
        return redirect(url_for('main.event_detail', event_id=event_id))
    # Verify this person belongs to the family
    person = db.session.get(Person, person_id)
    if not person or person.family_id != current_user.active_family_id:
        return redirect(url_for('main.event_detail', event_id=event_id))
    # Members can only RSVP their own household; admins can RSVP anyone
    if not current_user.active_is_admin:
        household_ids = _get_household_ids(current_user.person)
        if person_id not in household_ids:
            return redirect(url_for('main.event_detail', event_id=event_id))
    rsvp = EventRSVP.query.filter_by(event_id=event_id, person_id=person_id).first()
    if status == 'clear':
        if rsvp:
            db.session.delete(rsvp)
            db.session.commit()
    elif rsvp:
        rsvp.status = status
        db.session.commit()
    else:
        db.session.add(EventRSVP(event_id=event_id, person_id=person_id, status=status))
        db.session.commit()
    if status != 'clear':
        _notify_rsvp(event, person, status, actor_user_id=current_user.id)
    return redirect(url_for('main.event_detail', event_id=event_id))


def _notify_rsvp(event, person, status, actor_user_id=None):
    """Tell the family's admins that an RSVP came in (web + API share this)."""
    from ..notifications import notify_admins
    notify_admins(
        event.family_id, 'rsvp_received',
        title=f"{person.get_display_name()} RSVP'd {status} to {event.name}",
        url=url_for('main.event_detail', event_id=event.id, _external=True),
        exclude_user_id=actor_user_id,
    )


def _get_household_ids(person):
    """Return set of person IDs that a user can RSVP for (self + spouse + unmarried children)."""
    if not person:
        return set()
    ids = {person.id}
    spouse = person.get_active_spouse()
    if spouse:
        ids.add(spouse.id)
    for rel in person.child_rels:
        child = rel.child
        if child and _in_parent_household(child):
            ids.add(child.id)
    if spouse:
        for rel in spouse.child_rels:
            child = rel.child
            if child and _in_parent_household(child):
                ids.add(child.id)
    return ids


def _in_parent_household(person):
    """True if person has no active spouse — unmarried children belong to their parent's household."""
    return person.get_active_spouse() is None


def _build_family_groups(all_people, rsvp_map):
    """
    Full family list for admin/contributor RSVP view — every directory household,
    whether or not they've responded. Each group has adults + expandable children.
    """
    dir_people = [p for p in all_people if p.in_directory]

    # Map child_id -> parent people (so we can skip children when building heads)
    child_parent_map = {}
    for p in dir_people:
        for rel in p.child_rels:
            child = rel.child
            if child and child.in_directory and _in_parent_household(child):
                child_parent_map.setdefault(child.id, []).append(p)

    seen = set()
    groups = []

    for p in sorted(dir_people, key=lambda x: x.get_display_name()):
        if p.id in seen:
            continue
        # Skip unmarried children of directory members — they appear under parents
        if p.id in child_parent_map:
            seen.add(p.id)
            continue

        seen.add(p.id)
        adults = [(p, rsvp_map.get(p.id))]

        spouse = p.get_active_spouse()
        if spouse and spouse.in_directory and spouse.id not in seen:
            adults.append((spouse, rsvp_map.get(spouse.id)))
            seen.add(spouse.id)

        # Collect unmarried children from both partners
        child_ids = set()
        for rel in p.child_rels:
            if rel.child and rel.child.in_directory and _in_parent_household(rel.child):
                child_ids.add(rel.child_id)
        if spouse:
            for rel in spouse.child_rels:
                if rel.child and rel.child.in_directory and _in_parent_household(rel.child):
                    child_ids.add(rel.child_id)

        children = sorted(
            [(pp, rsvp_map.get(pp.id)) for pp in dir_people if pp.id in child_ids],
            key=lambda x: x[0].get_display_name()
        )
        for child, _ in children:
            seen.add(child.id)

        label = p.get_couple_name() if (spouse and spouse.in_directory) else p.get_display_name()
        groups.append(dict(label=label, adults=adults, children=children))

    return groups


def _build_rsvp_groups(event, all_people):
    """
    Group RSVPs by household unit for the summary display.
    Returns list of dicts: {label, yes: [names], maybe: [names], no: [names], total}
    One entry per household (couple unit or lone adult), ordered by most-going first.
    """
    people_map = {p.id: p for p in all_people}
    rsvp_map = {r.person_id: r.status for r in event.rsvps}
    seen = set()
    groups = []

    # Process in alphabetical order so output is stable
    responded = sorted(
        [r for r in event.rsvps],
        key=lambda r: r.person.get_display_name()
    )

    for rsvp in responded:
        person = rsvp.person
        if person.id in seen or not person.in_directory:
            continue

        # Gather the full household IDs
        hh_ids = _get_household_ids(person)
        # Also include household of spouse to avoid duplicates
        spouse = person.get_active_spouse()
        if spouse:
            hh_ids |= _get_household_ids(spouse)

        # Mark all as seen
        seen |= hh_ids

        # Collect responses within this household
        yes_names, maybe_names, no_names = [], [], []
        for pid in hh_ids:
            p = people_map.get(pid)
            if not p or not p.in_directory:
                continue
            s = rsvp_map.get(pid)
            if s == 'yes':
                yes_names.append(p.get_display_name())
            elif s == 'maybe':
                maybe_names.append(p.get_display_name())
            elif s == 'no':
                no_names.append(p.get_display_name())

        if not (yes_names or maybe_names or no_names):
            continue

        # Label: couple name or single name
        if spouse and spouse.in_directory:
            label = person.get_couple_name()
        else:
            label = person.get_display_name()

        groups.append(dict(label=label, yes=sorted(yes_names),
                           maybe=sorted(maybe_names), no=sorted(no_names)))

    # Sort: groups with most "yes" first
    groups.sort(key=lambda g: (-len(g['yes']), -len(g['maybe']), g['label']))
    return groups


# ── Meals ─────────────────────────────────────────────────────────────────────

@main.route('/events/<int:event_id>/meals/add', methods=['POST'])
@login_required
@admin_required
def event_meal_add(event_id):
    event = db.session.get(Event, event_id)
    if not event or event.family_id != current_user.active_family_id:
        flash('Event not found.', 'error')
        return redirect(url_for('main.events_list'))
    form = EventMealForm()
    if form.validate_on_submit():
        meal = EventMeal(
            event_id=event_id,
            name=form.name.data,
            meal_date=form.meal_date.data,
            meal_time=form.meal_time.data or None,
            notes=form.notes.data or None,
        )
        db.session.add(meal)
        db.session.commit()
        flash(f'{meal.name} added.', 'info')
    return redirect(url_for('main.event_detail', event_id=event_id))


@main.route('/events/<int:event_id>/meals/<int:meal_id>/delete', methods=['POST'])
@login_required
@admin_required
def event_meal_delete(event_id, meal_id):
    meal = db.session.get(EventMeal, meal_id)
    if not meal or meal.event.family_id != current_user.active_family_id:
        flash('Meal not found.', 'error')
        return redirect(url_for('main.event_detail', event_id=event_id))
    db.session.delete(meal)
    db.session.commit()
    flash('Meal removed.', 'info')
    return redirect(url_for('main.event_detail', event_id=event_id))


@main.route('/events/<int:event_id>/meals/<int:meal_id>/assign-family', methods=['POST'])
@login_required
@admin_required
def event_meal_assign_family(event_id, meal_id):
    meal = db.session.get(EventMeal, meal_id)
    if not meal or meal.event.family_id != current_user.active_family_id:
        flash('Meal not found.', 'error')
        return redirect(url_for('main.event_detail', event_id=event_id))
    all_people = Person.query.filter_by(family_id=current_user.active_family_id).order_by(Person.name).all()
    couple_people = [p for p in all_people if p.in_directory and (not p.get_active_spouse() or p.id < p.get_active_spouse().id)]
    form = EventMealFamilyAssignForm(prefix=f'meal_fam_{meal_id}')
    form.assigned_family_id.choices = [(0, '— None —')] + [(p.id, p.get_couple_name()) for p in couple_people]
    if form.validate_on_submit():
        pid = form.assigned_family_id.data
        meal.assigned_family_id = pid if pid else None
        db.session.commit()
    return redirect(url_for('main.event_detail', event_id=event_id))


@main.route('/events/<int:event_id>/meals/<int:meal_id>/unassign-family', methods=['POST'])
@login_required
@admin_required
def event_meal_unassign_family(event_id, meal_id):
    meal = db.session.get(EventMeal, meal_id)
    if not meal or meal.event.family_id != current_user.active_family_id:
        flash('Meal not found.', 'error')
        return redirect(url_for('main.event_detail', event_id=event_id))
    meal.assigned_family_id = None
    db.session.commit()
    return redirect(url_for('main.event_detail', event_id=event_id))


@main.route('/events/<int:event_id>/meals/<int:meal_id>/items/add', methods=['POST'])
@login_required
@admin_required
def event_meal_item_add(event_id, meal_id):
    meal = db.session.get(EventMeal, meal_id)
    if not meal or meal.event.family_id != current_user.active_family_id:
        flash('Meal not found.', 'error')
        return redirect(url_for('main.event_detail', event_id=event_id))
    form = EventMealItemForm()
    if form.validate_on_submit():
        item = EventMealItem(
            meal_id=meal_id,
            label=form.label.data,
            quantity=form.quantity.data or None,
            is_cleanup=form.is_cleanup.data,
        )
        db.session.add(item)
        db.session.commit()
    return redirect(url_for('main.event_detail', event_id=event_id))


@main.route('/events/<int:event_id>/meals/<int:meal_id>/self-signup', methods=['POST'])
@login_required
def event_meal_self_signup(event_id, meal_id):
    meal = db.session.get(EventMeal, meal_id)
    if not meal or meal.event.family_id != current_user.active_family_id:
        return redirect(url_for('main.event_detail', event_id=event_id))
    form = EventMealSelfSignupForm()
    if form.validate_on_submit() and current_user.person:
        item = EventMealItem(
            meal_id=meal_id,
            label=form.label.data,
            is_cleanup=False,
            assigned_to_id=current_user.person.id,
        )
        db.session.add(item)
        db.session.commit()
    return redirect(url_for('main.event_detail', event_id=event_id))


@main.route('/events/<int:event_id>/meals/<int:meal_id>/items/<int:item_id>/assign', methods=['POST'])
@login_required
def event_meal_item_assign(event_id, meal_id, item_id):
    item = db.session.get(EventMealItem, item_id)
    if not item or item.meal.event.family_id != current_user.active_family_id:
        flash('Item not found.', 'error')
        return redirect(url_for('main.event_detail', event_id=event_id))
    all_people = Person.query.filter_by(family_id=current_user.active_family_id).order_by(Person.name).all()
    form = EventMealAssignForm(prefix=f'item_{item_id}')
    form.person_id.choices = [(0, '— Select —')] + [(p.id, p.get_display_name()) for p in all_people]
    if form.validate_on_submit() and form.person_id.data:
        person = db.session.get(Person, form.person_id.data)
        if person and person.family_id == current_user.active_family_id:
            prev_id = item.assigned_to_id
            item.assigned_to_id = person.id
            db.session.commit()
            if prev_id != person.id and person.user:
                event_url = url_for('main.event_detail', event_id=event_id, _external=True)
                if (current_app.config.get('MAIL_ENABLED')
                        and NotificationPreference.is_enabled(person.user.id, 'assignment')):
                    from ..email import send_meal_item_assignment_email
                    send_meal_item_assignment_email(person.user, item, item.meal.event, event_url)
                from ..notifications import create_notification
                create_notification(person.user, 'assignment',
                                    title=f'Meal assignment: {item.label}',
                                    body=f'For {item.meal.name} at {item.meal.event.name}',
                                    url=event_url)
    return redirect(url_for('main.event_detail', event_id=event_id))


@main.route('/events/<int:event_id>/meals/<int:meal_id>/items/<int:item_id>/unassign', methods=['POST'])
@login_required
def event_meal_item_unassign(event_id, meal_id, item_id):
    item = db.session.get(EventMealItem, item_id)
    if not item or item.meal.event.family_id != current_user.active_family_id:
        flash('Item not found.', 'error')
        return redirect(url_for('main.event_detail', event_id=event_id))
    if not current_user.active_is_admin:
        flash('Only admins can unassign items.', 'error')
        return redirect(url_for('main.event_detail', event_id=event_id))
    item.assigned_to_id = None
    db.session.commit()
    return redirect(url_for('main.event_detail', event_id=event_id))


@main.route('/events/<int:event_id>/meals/<int:meal_id>/items/<int:item_id>/delete', methods=['POST'])
@login_required
@admin_required
def event_meal_item_delete(event_id, meal_id, item_id):
    item = db.session.get(EventMealItem, item_id)
    if not item or item.meal.event.family_id != current_user.active_family_id:
        flash('Item not found.', 'error')
        return redirect(url_for('main.event_detail', event_id=event_id))
    db.session.delete(item)
    db.session.commit()
    return redirect(url_for('main.event_detail', event_id=event_id))


# ── Assignments ───────────────────────────────────────────────────────────────

@main.route('/events/<int:event_id>/assignments/add', methods=['POST'])
@login_required
@admin_required
def event_assignment_add(event_id):
    event = db.session.get(Event, event_id)
    if not event or event.family_id != current_user.active_family_id:
        flash('Event not found.', 'error')
        return redirect(url_for('main.events_list'))
    form = EventAssignmentForm()
    if form.validate_on_submit():
        a = EventAssignment(
            event_id=event_id,
            title=form.title.data,
            description=form.description.data or None,
            category=form.category.data or None,
            due_date=form.due_date.data or None,
        )
        db.session.add(a)
        db.session.commit()
        flash(f'Task "{a.title}" added.', 'info')
    return redirect(url_for('main.event_detail', event_id=event_id))


@main.route('/events/<int:event_id>/assignments/bulk-add', methods=['POST'])
@login_required
@admin_required
def event_assignment_bulk_add(event_id):
    event = db.session.get(Event, event_id)
    if not event or event.family_id != current_user.active_family_id:
        flash('Event not found.', 'error')
        return redirect(url_for('main.events_list'))
    raw = request.form.get('bulk_tasks', '')
    added = 0
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        cat = None
        if ' #' in line:
            parts = line.rsplit(' #', 1)
            candidate = parts[1].strip().title()
            if candidate in ASSIGNMENT_CATEGORIES:
                cat = candidate
                line = parts[0].strip()
        if line:
            db.session.add(EventAssignment(event_id=event_id, title=line[:150], category=cat))
            added += 1
    if added:
        db.session.commit()
        flash(f'{added} task{"s" if added != 1 else ""} added.', 'info')
    return redirect(url_for('main.event_detail', event_id=event_id))


@main.route('/events/<int:event_id>/assignments/<int:aid>/claim', methods=['POST'])
@login_required
def event_assignment_claim(event_id, aid):
    a = db.session.get(EventAssignment, aid)
    if not a or a.event.family_id != current_user.active_family_id:
        flash('Task not found.', 'error')
        return redirect(url_for('main.event_detail', event_id=event_id))
    if not current_user.person:
        flash('You need a family profile to claim tasks.', 'error')
        return redirect(url_for('main.event_detail', event_id=event_id))
    if a.claimed_by_id:
        flash('That task is already claimed.', 'error')
        return redirect(url_for('main.event_detail', event_id=event_id))
    a.claimed_by_id = current_user.person.id
    db.session.commit()
    flash(f'You claimed "{a.title}".', 'info')
    return redirect(url_for('main.event_detail', event_id=event_id))


@main.route('/events/<int:event_id>/assignments/<int:aid>/unclaim', methods=['POST'])
@login_required
def event_assignment_unclaim(event_id, aid):
    a = db.session.get(EventAssignment, aid)
    if not a or a.event.family_id != current_user.active_family_id:
        flash('Task not found.', 'error')
        return redirect(url_for('main.event_detail', event_id=event_id))
    is_own = current_user.person and a.claimed_by_id == current_user.person.id
    if not is_own and not current_user.active_is_admin:
        flash('You can only unclaim your own tasks.', 'error')
        return redirect(url_for('main.event_detail', event_id=event_id))
    a.claimed_by_id = None
    a.is_done = False
    db.session.commit()
    flash(f'"{a.title}" is now unclaimed.', 'info')
    return redirect(url_for('main.event_detail', event_id=event_id))


@main.route('/events/<int:event_id>/assignments/<int:aid>/done', methods=['POST'])
@login_required
def event_assignment_done(event_id, aid):
    a = db.session.get(EventAssignment, aid)
    if not a or a.event.family_id != current_user.active_family_id:
        flash('Task not found.', 'error')
        return redirect(url_for('main.event_detail', event_id=event_id))
    is_own = current_user.person and a.claimed_by_id == current_user.person.id
    if not is_own and not current_user.active_is_admin:
        flash('Only the person assigned can mark this done.', 'error')
        return redirect(url_for('main.event_detail', event_id=event_id))
    a.is_done = not a.is_done
    db.session.commit()
    return redirect(url_for('main.event_detail', event_id=event_id))


@main.route('/events/<int:event_id>/assignments/<int:aid>/delete', methods=['POST'])
@login_required
@admin_required
def event_assignment_delete(event_id, aid):
    a = db.session.get(EventAssignment, aid)
    if not a or a.event.family_id != current_user.active_family_id:
        flash('Task not found.', 'error')
        return redirect(url_for('main.event_detail', event_id=event_id))
    db.session.delete(a)
    db.session.commit()
    flash('Task removed.', 'info')
    return redirect(url_for('main.event_detail', event_id=event_id))


@main.route('/events/<int:event_id>/assignments/<int:aid>/assign', methods=['POST'])
@login_required
@admin_required
def event_assignment_admin_assign(event_id, aid):
    a = db.session.get(EventAssignment, aid)
    if not a or a.event.family_id != current_user.active_family_id:
        return redirect(url_for('main.event_detail', event_id=event_id))
    all_people = Person.query.filter_by(family_id=current_user.active_family_id).order_by(Person.name).all()
    form = EventAssignmentAdminAssignForm(prefix=f'a_{aid}')
    form.person_id.choices = [(0, '— Select —')] + [(p.id, p.get_display_name()) for p in all_people]
    if form.validate_on_submit():
        pid = form.person_id.data
        prev_id = a.claimed_by_id
        a.claimed_by_id = pid if pid else None
        a.is_done = False
        db.session.commit()
        if pid and prev_id != pid:
            person = db.session.get(Person, pid)
            if person and person.user:
                event_url = url_for('main.event_detail', event_id=event_id, _external=True)
                if (current_app.config.get('MAIL_ENABLED')
                        and NotificationPreference.is_enabled(person.user.id, 'assignment')):
                    from ..email import send_assignment_notification_email
                    send_assignment_notification_email(person.user, a, a.event, event_url)
                from ..notifications import create_notification
                create_notification(person.user, 'assignment',
                                    title=f'Task assigned: {a.title}',
                                    body=f'For {a.event.name}',
                                    url=event_url)
    return redirect(url_for('main.event_detail', event_id=event_id))


@main.route('/events/<int:event_id>/assignments/<int:aid>/tasks/add', methods=['POST'])
@login_required
def event_assignment_task_add(event_id, aid):
    a = db.session.get(EventAssignment, aid)
    if not a or a.event.family_id != current_user.active_family_id:
        return redirect(url_for('main.event_detail', event_id=event_id))
    my_person = Person.query.filter_by(family_id=current_user.active_family_id,
                                       user_id=current_user.id).first()
    if not (current_user.active_is_admin or (my_person and a.claimed_by_id == my_person.id)):
        return redirect(url_for('main.event_detail', event_id=event_id))
    label = request.form.get('label', '').strip()
    if label:
        db.session.add(AssignmentTask(assignment_id=aid, label=label))
        db.session.commit()
    return redirect(url_for('main.event_detail', event_id=event_id))


@main.route('/events/<int:event_id>/assignments/<int:aid>/tasks/<int:tid>/toggle', methods=['POST'])
@login_required
def event_assignment_task_toggle(event_id, aid, tid):
    a = db.session.get(EventAssignment, aid)
    if not a or a.event.family_id != current_user.active_family_id:
        return redirect(url_for('main.event_detail', event_id=event_id))
    my_person = Person.query.filter_by(family_id=current_user.active_family_id,
                                       user_id=current_user.id).first()
    if not (current_user.active_is_admin or (my_person and a.claimed_by_id == my_person.id)):
        return redirect(url_for('main.event_detail', event_id=event_id))
    task = db.session.get(AssignmentTask, tid)
    if task and task.assignment_id == aid:
        task.is_done = not task.is_done
        db.session.commit()
    return redirect(url_for('main.event_detail', event_id=event_id))


@main.route('/events/<int:event_id>/assignments/<int:aid>/tasks/<int:tid>/delete', methods=['POST'])
@login_required
def event_assignment_task_delete(event_id, aid, tid):
    a = db.session.get(EventAssignment, aid)
    if not a or a.event.family_id != current_user.active_family_id:
        return redirect(url_for('main.event_detail', event_id=event_id))
    my_person = Person.query.filter_by(family_id=current_user.active_family_id,
                                       user_id=current_user.id).first()
    if not (current_user.active_is_admin or (my_person and a.claimed_by_id == my_person.id)):
        return redirect(url_for('main.event_detail', event_id=event_id))
    task = db.session.get(AssignmentTask, tid)
    if task and task.assignment_id == aid:
        db.session.delete(task)
        db.session.commit()
    return redirect(url_for('main.event_detail', event_id=event_id))


# ── Sleeping ──────────────────────────────────────────────────────────────────

def _sleeping_remove_from_event(event, person):
    """Remove person from every sleeping spot in this event."""
    for spot in event.sleeping_spots:
        if person in spot.people:
            spot.people.remove(person)


@main.route('/events/<int:event_id>/sleeping/add-spot', methods=['POST'])
@login_required
@admin_required
def event_sleeping_add_spot(event_id):
    event = db.session.get(Event, event_id)
    if not event or event.family_id != current_user.active_family_id:
        flash('Event not found.', 'error')
        return redirect(url_for('main.events_list'))
    form = EventSleepingSpotForm()
    if form.validate_on_submit():
        spot_type = form.spot_type.data or None
        if spot_type and spot_type not in SPOT_TYPES:
            spot_type = None
        spot = EventSleepingSpot(
            event_id=event_id,
            name=form.name.data,
            spot_type=spot_type,
            capacity=form.capacity.data,
            notes=form.notes.data or None,
        )
        db.session.add(spot)
        db.session.commit()
        flash(f'"{spot.name}" added.', 'info')
    return redirect(url_for('main.event_detail', event_id=event_id))


@main.route('/events/<int:event_id>/sleeping/bulk-add', methods=['POST'])
@login_required
@admin_required
def event_sleeping_bulk_add(event_id):
    """Parse a textarea of room names (one per line, optional capacity) and create spots."""
    event = db.session.get(Event, event_id)
    if not event or event.family_id != current_user.active_family_id:
        flash('Event not found.', 'error')
        return redirect(url_for('main.events_list'))
    raw = request.form.get('bulk_rooms', '')
    added = 0
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        # Try to parse trailing number as capacity: "Master bedroom 2" or "Bunk room (4)"
        import re as _re
        m = _re.match(r'^(.+?)\s*[\(\[]?(\d+)[\)\]]?\s*$', line)
        if m:
            name, cap = m.group(1).strip(), int(m.group(2))
        else:
            name, cap = line, None
        if name:
            db.session.add(EventSleepingSpot(event_id=event_id, name=name, capacity=cap))
            added += 1
    if added:
        db.session.commit()
        flash(f'{added} room{"s" if added != 1 else ""} added.', 'info')
    return redirect(url_for('main.event_detail', event_id=event_id))


@main.route('/events/<int:event_id>/sleeping/<int:sid>/assign', methods=['POST'])
@login_required
@admin_required
def event_sleeping_assign(event_id, sid):
    spot = db.session.get(EventSleepingSpot, sid)
    if not spot or spot.event.family_id != current_user.active_family_id:
        flash('Spot not found.', 'error')
        return redirect(url_for('main.event_detail', event_id=event_id))
    form = EventSleepingAssignForm(prefix=f'spot_{sid}')
    eligible = Person.query.filter_by(family_id=current_user.active_family_id).order_by(Person.name).all()
    spot_assigned_ids = {p.id for p in spot.people}
    form.person_id.choices = [(0, '— Select —')] + [(p.id, p.get_display_name()) for p in eligible if p.id not in spot_assigned_ids]
    if form.validate_on_submit() and form.person_id.data:
        person = db.session.get(Person, form.person_id.data)
        if person and person.family_id == current_user.active_family_id:
            if spot.capacity and len(spot.people) >= spot.capacity and person not in spot.people:
                flash(f'"{spot.name}" is at capacity ({spot.capacity}).', 'error')
            elif person not in spot.people:
                _sleeping_remove_from_event(spot.event, person)
                spot.people.append(person)
                db.session.commit()
                flash(f'{person.get_display_name()} assigned to {spot.name}.', 'info')
    return redirect(url_for('main.event_detail', event_id=event_id))


@main.route('/events/<int:event_id>/sleeping/<int:sid>/unassign/<int:pid>', methods=['POST'])
@login_required
@admin_required
def event_sleeping_unassign(event_id, sid, pid):
    spot = db.session.get(EventSleepingSpot, sid)
    if not spot or spot.event.family_id != current_user.active_family_id:
        flash('Spot not found.', 'error')
        return redirect(url_for('main.event_detail', event_id=event_id))
    person = db.session.get(Person, pid)
    if person and person.family_id == current_user.active_family_id and person in spot.people:
        spot.people.remove(person)
        db.session.commit()
        flash(f'{person.get_display_name()} removed from {spot.name}.', 'info')
    return redirect(url_for('main.event_detail', event_id=event_id))


@main.route('/events/<int:event_id>/sleeping/<int:sid>/delete', methods=['POST'])
@login_required
@admin_required
def event_sleeping_delete_spot(event_id, sid):
    spot = db.session.get(EventSleepingSpot, sid)
    if not spot or spot.event.family_id != current_user.active_family_id:
        flash('Spot not found.', 'error')
        return redirect(url_for('main.event_detail', event_id=event_id))
    db.session.delete(spot)
    db.session.commit()
    flash(f'"{spot.name}" removed.', 'info')
    return redirect(url_for('main.event_detail', event_id=event_id))


@main.route('/events/<int:event_id>/sleeping/<int:sid>/assign-household', methods=['POST'])
@login_required
@admin_required
def event_sleeping_assign_household(event_id, sid):
    spot = db.session.get(EventSleepingSpot, sid)
    if not spot or spot.event.family_id != current_user.active_family_id:
        flash('Spot not found.', 'error')
        return redirect(url_for('main.event_detail', event_id=event_id))
    try:
        pid = int(request.form.get('person_id', 0))
    except (ValueError, TypeError):
        pid = 0
    person = pid and db.session.get(Person, pid)
    if not person or person.family_id != current_user.active_family_id:
        return redirect(url_for('main.event_detail', event_id=event_id))
    people_to_add = [person]
    spouse = person.get_active_spouse()
    if spouse and spouse.family_id == current_user.active_family_id:
        people_to_add.append(spouse)
    needed = sum(1 for p in people_to_add if p not in spot.people)
    if spot.capacity and len(spot.people) + needed > spot.capacity:
        flash(f'Not enough space in "{spot.name}" for the whole household.', 'error')
        return redirect(url_for('main.event_detail', event_id=event_id))
    added = []
    for p in people_to_add:
        if p not in spot.people:
            _sleeping_remove_from_event(spot.event, p)
            spot.people.append(p)
            added.append(p.get_display_name())
    if added:
        db.session.commit()
        flash(f'{", ".join(added)} assigned to {spot.name}.', 'info')
    return redirect(url_for('main.event_detail', event_id=event_id))


@main.route('/events/<int:event_id>/sleeping/<int:sid>/self-assign', methods=['POST'])
@login_required
def event_sleeping_self_assign(event_id, sid):
    spot = db.session.get(EventSleepingSpot, sid)
    if not spot or spot.event.family_id != current_user.active_family_id:
        flash('Spot not found.', 'error')
        return redirect(url_for('main.event_detail', event_id=event_id))
    person = current_user.person
    if not person:
        return redirect(url_for('main.event_detail', event_id=event_id))
    if spot.capacity and len(spot.people) >= spot.capacity and person not in spot.people:
        flash(f'"{spot.name}" is full.', 'error')
        return redirect(url_for('main.event_detail', event_id=event_id))
    if person not in spot.people:
        _sleeping_remove_from_event(spot.event, person)
        spot.people.append(person)
        db.session.commit()
        flash(f'You\'ve been placed in {spot.name}.', 'info')
    return redirect(url_for('main.event_detail', event_id=event_id))


@main.route('/events/<int:event_id>/sleeping/<int:sid>/self-unassign', methods=['POST'])
@login_required
def event_sleeping_self_unassign(event_id, sid):
    spot = db.session.get(EventSleepingSpot, sid)
    if not spot or spot.event.family_id != current_user.active_family_id:
        flash('Spot not found.', 'error')
        return redirect(url_for('main.event_detail', event_id=event_id))
    person = current_user.person
    if person and person in spot.people:
        spot.people.remove(person)
        db.session.commit()
        flash(f'You\'ve been removed from {spot.name}.', 'info')
    return redirect(url_for('main.event_detail', event_id=event_id))


# ── Agenda ────────────────────────────────────────────────────────────────────

@main.route('/events/<int:event_id>/agenda/add', methods=['POST'])
@login_required
@admin_required
def event_agenda_add(event_id):
    event = db.session.get(Event, event_id)
    if not event or event.family_id != current_user.active_family_id:
        flash('Event not found.', 'error')
        return redirect(url_for('main.events_list'))
    form = EventAgendaItemForm()
    if form.validate_on_submit():
        item_date_str = request.form.get('item_date') or None
        item_date = date.fromisoformat(item_date_str) if item_date_str else None
        # Determine sort_order: one more than the last item on the same day.
        same_day = [i for i in event.agenda_items if i.item_date == item_date]
        sort_order = max((i.sort_order for i in same_day), default=-1) + 1
        item = EventAgendaItem(
            event_id=event_id,
            item_date=item_date,
            item_time=form.item_time.data.strip() or None,
            title=form.title.data.strip(),
            notes=form.notes.data.strip() or None,
            assigned_to=form.assigned_to.data.strip() or None,
            sort_order=sort_order,
        )
        db.session.add(item)
        db.session.commit()
    return redirect(url_for('main.event_detail', event_id=event_id))


@main.route('/events/<int:event_id>/agenda/<int:item_id>/delete', methods=['POST'])
@login_required
@admin_required
def event_agenda_delete(event_id, item_id):
    item = db.session.get(EventAgendaItem, item_id)
    if not item or item.event.family_id != current_user.active_family_id:
        flash('Item not found.', 'error')
        return redirect(url_for('main.event_detail', event_id=event_id))
    db.session.delete(item)
    db.session.commit()
    return redirect(url_for('main.event_detail', event_id=event_id))


