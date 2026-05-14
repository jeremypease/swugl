from flask import Blueprint, render_template, redirect, url_for, flash, request, current_app
from flask_login import login_user, logout_user, login_required, current_user
from .models import Family, User, Person, SpouseRelationship, Event, EventMeal, EventMealItem, EventAssignment, EventSleepingSpot
from .forms import LoginForm, RegistrationForm, ProfileForm, SpouseForm, EndSpouseForm, SpouseInviteForm, ForgotPasswordForm, ResetPasswordForm, AddPersonForm, RelativeForm, FamilySettingsForm, EditPersonForm, EventForm, EventMealForm, EventMealFamilyAssignForm, EventMealItemForm, EventMealAssignForm, EventAssignmentForm, EventSleepingSpotForm, EventSleepingAssignForm
from .email import send_verification_email, send_pending_notification, send_approval_notification, send_spouse_confirmation_email, send_spouse_invitation_email, send_password_reset_email
from datetime import date, datetime, timedelta
from functools import wraps
from urllib.parse import urlparse
from . import db
import secrets
import re
import os

main = Blueprint('main', __name__)

def format_phone(raw):
    if not raw:
        return None
    digits = re.sub(r'\D', '', raw)
    if len(digits) == 10:
        return f"{digits[:3]}-{digits[3:6]}-{digits[6:]}"
    elif len(digits) == 11 and digits[0] == '1':
        return f"{digits[1:4]}-{digits[4:7]}-{digits[7:]}"
    return raw

def format_birthplace(raw):
    if not raw:
        return None
    parts = [p.strip() for p in raw.split(',')]
    if len(parts) >= 2:
        city = parts[0].title()
        state = parts[1].upper()
        extra = [p for p in parts[2:] if p]
        if extra:
            return f"{city}, {state}, {', '.join(extra)}"
        return f"{city}, {state}"
    return raw.title()

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin:
            flash('You do not have permission to access that page.', 'error')
            return redirect(url_for('main.index'))
        return f(*args, **kwargs)
    return decorated_function

def _gender_term(person, male, female, neutral):
    if person.gender == 'Male':
        return male
    if person.gender == 'Female':
        return female
    return neutral

def get_relationship(a, b):
    """Return a relationship label from person a's perspective of person b."""
    if not a or not b or a.id == b.id:
        return None
    a_spouse = a.get_active_spouse()
    # Spouse
    if a_spouse and a_spouse.id == b.id:
        return _gender_term(b, 'Husband', 'Wife', 'Spouse')
    # Parent
    if b in a.parents:
        return _gender_term(b, 'Father', 'Mother', 'Parent')
    # Child
    if b in a.children:
        return _gender_term(b, 'Son', 'Daughter', 'Child')
    # Sibling
    shared = set(p.id for p in a.parents) & set(p.id for p in b.parents)
    if shared:
        return _gender_term(b, 'Brother', 'Sister', 'Sibling')
    # Grandparent
    for p in a.parents:
        if b in p.parents:
            return _gender_term(b, 'Grandfather', 'Grandmother', 'Grandparent')
    # Grandchild
    for c in a.children:
        if b in c.children:
            return _gender_term(b, 'Grandson', 'Granddaughter', 'Grandchild')
    # Great-grandparent
    for p in a.parents:
        for gp in p.parents:
            if b in gp.parents:
                return _gender_term(b, 'Great-grandfather', 'Great-grandmother', 'Great-grandparent')
    # Aunt / Uncle
    for p in a.parents:
        for gp in p.parents:
            for s in gp.children:
                if s.id != p.id and s.id == b.id:
                    return _gender_term(b, 'Uncle', 'Aunt', 'Aunt/Uncle')
    # Niece / Nephew
    for p in a.parents:
        for s in p.children:
            if s.id != a.id and b in s.children:
                return _gender_term(b, 'Nephew', 'Niece', 'Niece/Nephew')
    # Child-in-law
    for c in a.children:
        c_spouse = c.get_active_spouse()
        if c_spouse and c_spouse.id == b.id:
            return _gender_term(b, 'Son-in-law', 'Daughter-in-law', 'Child-in-law')
    # Grandchild-in-law
    for c in a.children:
        for gc in c.children:
            gc_spouse = gc.get_active_spouse()
            if gc_spouse and gc_spouse.id == b.id:
                return _gender_term(b, 'Grandson-in-law', 'Granddaughter-in-law', 'Grandchild-in-law')
    # In-law relationships via spouse
    if a_spouse:
        if b in a_spouse.parents:
            return _gender_term(b, 'Father-in-law', 'Mother-in-law', 'Parent-in-law')
        for sp in a_spouse.parents:
            for s in sp.children:
                if s.id != a_spouse.id and s.id == b.id:
                    return _gender_term(b, 'Brother-in-law', 'Sister-in-law', 'Sibling-in-law')
            if b in sp.parents:
                return _gender_term(b, 'Grandfather-in-law', 'Grandmother-in-law', 'Grandparent-in-law')
    # Cousin
    for p in a.parents:
        for gp in p.parents:
            for ps in gp.children:
                if ps.id != p.id and b in ps.children:
                    return 'Cousin'
    return None

def build_tree_node(person, family_id, visited=None):
    if visited is None:
        visited = set()
    if person.id in visited:
        return None
    visited.add(person.id)
    spouse = person.get_active_spouse()
    if spouse:
        visited.add(spouse.id)
    all_children = list(person.children)
    if spouse:
        for c in spouse.children:
            if c not in all_children and c.id not in visited:
                all_children.append(c)
    all_children = [c for c in all_children if c.family_id == family_id]
    all_children.sort(key=lambda c: c.birthday or date.max)
    children = [n for c in all_children if (n := build_tree_node(c, family_id, visited))]
    return {"person": person, "spouse": spouse, "children": children}

def get_core_ids(node):
    if not node:
        return set()
    ids = {node['person'].id}
    if node['spouse']:
        ids.add(node['spouse'].id)
    for child in node['children']:
        ids |= get_core_ids(child)
    return ids

@main.route('/')
@login_required
def index():
    people = Person.query.filter_by(family_id=current_user.family_id).order_by(Person.name).all()
    member_count = len(people)
    today = date.today()
    upcoming_birthdays = []
    for person in people:
        if person.birthday:
            try:
                bday = person.birthday.replace(year=today.year)
            except ValueError:
                bday = person.birthday.replace(year=today.year, day=28)
            if bday < today:
                try:
                    bday = person.birthday.replace(year=today.year + 1)
                except ValueError:
                    bday = person.birthday.replace(year=today.year + 1, day=28)
            days = (bday - today).days
            if days <= 30:
                upcoming_birthdays.append((person, bday, days))
    upcoming_birthdays.sort(key=lambda x: x[2])
    upcoming_events = Event.query.filter_by(family_id=current_user.family_id).filter(
        Event.start_date >= today
    ).order_by(Event.start_date).limit(3).all()
    return render_template('index.html', member_count=member_count, family=current_user.family,
                           upcoming_birthdays=upcoming_birthdays, upcoming_events=upcoming_events)

@main.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('main.index'))
    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(email=form.email.data).first()
        if not user or not user.check_password(form.password.data):
            flash('Invalid email or password.', 'error')
            return redirect(url_for('main.login'))
        if not user.email_verified:
            flash('Please verify your email before logging in.', 'error')
            return redirect(url_for('main.login'))
        if user.status != 'approved':
            flash('Your account is pending approval.', 'error')
            return redirect(url_for('main.login'))
        login_user(user, remember=form.remember_me.data)
        next_page = request.args.get('next')
        # Reject absolute URLs to prevent open redirect
        if next_page and urlparse(next_page).netloc != '':
            next_page = None
        return redirect(next_page or url_for('main.index'))
    return render_template('login.html', form=form)

@main.route('/logout', methods=['POST'])
@login_required
def logout():
    logout_user()
    flash('You have been logged out.', 'info')
    return redirect(url_for('main.login'))

@main.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('main.index'))
    form = RegistrationForm()
    if form.validate_on_submit():
        if not form.family_name.data:
            flash('Please enter a family name.', 'error')
            return render_template('register.html', form=form)
        existing_user = User.query.filter_by(email=form.email.data).first()
        if existing_user:
            flash('An account with that email already exists.', 'error')
            return redirect(url_for('main.register'))
        family = Family(name=form.family_name.data)
        db.session.add(family)
        db.session.flush()
        full_name = f"{form.first_name.data} {form.last_name.data}"
        person = Person(
            name=full_name,
            email=form.email.data,
            phone=format_phone(form.phone.data),
            family_id=family.id,
        )
        db.session.add(person)
        db.session.flush()
        token = secrets.token_urlsafe(32)
        user = User(
            first_name=form.first_name.data,
            last_name=form.last_name.data,
            email=form.email.data,
            phone=format_phone(form.phone.data),
            verification_token=token,
            verification_token_expiry=datetime.utcnow() + timedelta(hours=24),
            email_verified=False,
            # Family creator is auto-approved — email verification is the only gate
            status='approved',
            is_admin=True,
            family_id=family.id,
            person_id=person.id,
        )
        user.set_password(form.password.data)
        db.session.add(user)
        db.session.commit()
        if current_app.config.get('MAIL_ENABLED'):
            send_verification_email(user, url_for('main.verify_email', token=token, _external=True))
            flash('Registration successful! Please check your email to verify your account.', 'info')
        else:
            user.email_verified = True
            db.session.commit()
            flash('Registration successful! You can now sign in.', 'info')
        return redirect(url_for('main.login'))
    return render_template('register.html', form=form)

@main.route('/verify/<token>')
def verify_email(token):
    user = User.query.filter_by(verification_token=token).first()
    if not user:
        flash('Invalid or expired verification link.', 'error')
        return redirect(url_for('main.login'))
    if user.verification_token_expiry and user.verification_token_expiry < datetime.utcnow():
        flash('This verification link has expired. Please register again.', 'error')
        return redirect(url_for('main.login'))
    user.email_verified = True
    user.verification_token = None
    user.verification_token_expiry = None
    db.session.commit()
    flash('Email verified! You can now sign in.', 'info')
    return redirect(url_for('main.login'))

@main.route('/register/invite/<token>', methods=['GET', 'POST'])
def register_invited(token):
    invited_user = User.query.filter_by(invitation_token=token).first()
    if not invited_user or invited_user.status != 'invited':
        flash('Invalid or expired invitation link.', 'error')
        return redirect(url_for('main.login'))
    if invited_user.invitation_token_expiry and invited_user.invitation_token_expiry < datetime.utcnow():
        flash('This invitation link has expired. Please ask to be re-invited.', 'error')
        return redirect(url_for('main.login'))

    form = RegistrationForm()

    if request.method == 'GET':
        form.first_name.data = invited_user.first_name
        form.last_name.data = invited_user.last_name
        form.email.data = invited_user.email

    if form.validate_on_submit():
        invited_user.first_name = form.first_name.data
        invited_user.last_name = form.last_name.data
        invited_user.phone = format_phone(form.phone.data)
        invited_user.set_password(form.password.data)
        invited_user.email_verified = True
        invited_user.status = 'approved'
        invited_user.invitation_token = None
        invited_user.invitation_token_expiry = None
        # Sync person name if first/last name changed during registration
        if invited_user.person:
            invited_user.person.name = f"{form.first_name.data} {form.last_name.data}"
        db.session.commit()
        flash('Account created! You can now log in.', 'info')
        return redirect(url_for('main.login'))

    return render_template('register.html', form=form, invited=True)

@main.route('/admin/add-member', methods=['GET', 'POST'])
@login_required
@admin_required
def add_member():
    parent1_id = request.args.get('parent1_id', type=int)
    parent2_id = request.args.get('parent2_id', type=int)
    next_page = request.args.get('next')
    parent1 = db.session.get(Person, parent1_id) if parent1_id else None
    parent2 = db.session.get(Person, parent2_id) if parent2_id else None
    if parent1 and parent1.family_id != current_user.family_id:
        parent1 = None
    if parent2 and parent2.family_id != current_user.family_id:
        parent2 = None
    form = AddPersonForm()
    if form.validate_on_submit():
        person = Person(
            name=f"{form.first_name.data} {form.last_name.data}",
            family_id=current_user.family_id,
            email=form.email.data or None,
            phone=format_phone(form.phone.data),
            gender=form.gender.data or None,
            birthday=form.birthday.data,
            birthplace=format_birthplace(form.birthplace.data),
            nickname=form.nickname.data or None,
            maiden_name=form.maiden_name.data or None,
            notes=form.notes.data or None,
        )
        db.session.add(person)
        db.session.flush()
        if parent1:
            parent1.children.append(person)
        if parent2:
            parent2.children.append(person)
        db.session.commit()
        flash(f'{person.name} has been added to the family.', 'info')
        if next_page == 'tree':
            return redirect(url_for('main.family_tree'))
        return redirect(url_for('main.person_detail', person_id=person.id))
    return render_template('add_member.html', form=form, parent1=parent1, parent2=parent2, next_page=next_page)

@main.route('/person/<int:person_id>/add-parent', methods=['GET', 'POST'])
@login_required
def add_parent(person_id):
    person = db.session.get(Person, person_id)
    if not person or person.family_id != current_user.family_id:
        flash('Person not found.', 'error')
        return redirect(url_for('main.index'))
    can_edit = current_user.is_admin or (person.user and person.user == current_user)
    if not can_edit:
        flash('You do not have permission to edit this profile.', 'error')
        return redirect(url_for('main.person_detail', person_id=person_id))
    existing_parent_ids = {p.id for p in person.parents}
    eligible = [
        p for p in Person.query.filter_by(family_id=current_user.family_id).order_by(Person.name).all()
        if p.id != person.id and p.id not in existing_parent_ids
    ]
    form = RelativeForm()
    form.relative_id.choices = [(0, '-- Select --')] + [(p.id, p.get_display_name()) for p in eligible]
    if form.validate_on_submit():
        parent_person = db.session.get(Person, form.relative_id.data)
        if not parent_person or parent_person.family_id != current_user.family_id:
            flash('Person not found.', 'error')
            return redirect(url_for('main.add_parent', person_id=person_id))
        person.parents.append(parent_person)
        db.session.commit()
        flash(f'{parent_person.get_display_name()} added as a parent.', 'info')
        return redirect(url_for('main.person_detail', person_id=person_id))
    return render_template('add_relative.html', form=form, subject=person, action='parent')

@main.route('/person/<int:person_id>/add-child', methods=['GET', 'POST'])
@login_required
def add_child(person_id):
    person = db.session.get(Person, person_id)
    if not person or person.family_id != current_user.family_id:
        flash('Person not found.', 'error')
        return redirect(url_for('main.index'))
    can_edit = current_user.is_admin or (person.user and person.user == current_user)
    if not can_edit:
        flash('You do not have permission to edit this profile.', 'error')
        return redirect(url_for('main.person_detail', person_id=person_id))
    existing_child_ids = {c.id for c in person.children}
    eligible = [
        p for p in Person.query.filter_by(family_id=current_user.family_id).order_by(Person.name).all()
        if p.id != person.id and p.id not in existing_child_ids
    ]
    form = RelativeForm()
    form.relative_id.choices = [(0, '-- Select --')] + [(p.id, p.get_display_name()) for p in eligible]
    next_page = request.args.get('next')
    if form.validate_on_submit():
        child_person = db.session.get(Person, form.relative_id.data)
        if not child_person or child_person.family_id != current_user.family_id:
            flash('Person not found.', 'error')
            return redirect(url_for('main.add_child', person_id=person_id))
        person.children.append(child_person)
        db.session.commit()
        flash(f'{child_person.get_display_name()} added as a child.', 'info')
        if next_page == 'tree':
            return redirect(url_for('main.family_tree'))
        return redirect(url_for('main.person_detail', person_id=person_id))
    return render_template('add_relative.html', form=form, subject=person, action='child', next_page=next_page)

@main.route('/person/<int:person_id>/remove-parent/<int:parent_id>', methods=['POST'])
@login_required
def remove_parent(person_id, parent_id):
    person = db.session.get(Person, person_id)
    if not person or person.family_id != current_user.family_id:
        flash('Person not found.', 'error')
        return redirect(url_for('main.index'))
    can_edit = current_user.is_admin or (person.user and person.user == current_user)
    if not can_edit:
        flash('You do not have permission to edit this profile.', 'error')
        return redirect(url_for('main.person_detail', person_id=person_id))
    parent_person = db.session.get(Person, parent_id)
    if parent_person and parent_person in person.parents:
        person.parents.remove(parent_person)
        db.session.commit()
        flash(f'{parent_person.get_display_name()} removed as a parent.', 'info')
    return redirect(url_for('main.person_detail', person_id=person_id))

@main.route('/person/<int:person_id>/remove-child/<int:child_id>', methods=['POST'])
@login_required
def remove_child(person_id, child_id):
    person = db.session.get(Person, person_id)
    if not person or person.family_id != current_user.family_id:
        flash('Person not found.', 'error')
        return redirect(url_for('main.index'))
    can_edit = current_user.is_admin or (person.user and person.user == current_user)
    if not can_edit:
        flash('You do not have permission to edit this profile.', 'error')
        return redirect(url_for('main.person_detail', person_id=person_id))
    child_person = db.session.get(Person, child_id)
    if child_person and child_person in person.children:
        person.children.remove(child_person)
        db.session.commit()
        flash(f'{child_person.get_display_name()} removed as a child.', 'info')
    return redirect(url_for('main.person_detail', person_id=person_id))

@main.route('/admin/family', methods=['GET', 'POST'])
@login_required
@admin_required
def family_settings():
    family = current_user.family
    people = Person.query.filter_by(family_id=current_user.family_id).order_by(Person.name).all()
    choices = [(0, '-- None --')] + [(p.id, p.get_display_name()) for p in people]
    form = FamilySettingsForm()
    form.patriarch_id.choices = choices
    form.matriarch_id.choices = choices
    if request.method == 'GET':
        form.family_name.data = family.name
        form.patriarch_id.data = family.patriarch_id or 0
        form.matriarch_id.data = family.matriarch_id or 0
    if form.validate_on_submit():
        family.name = form.family_name.data
        family.patriarch_id = form.patriarch_id.data or None
        family.matriarch_id = form.matriarch_id.data or None
        db.session.commit()
        flash('Family settings saved.', 'info')
        return redirect(url_for('main.family_settings'))
    return render_template('family_settings.html', form=form, family=family)

@main.route('/family/tree')
@login_required
def family_tree():
    family = current_user.family
    patriarch = family.patriarch
    matriarch = family.matriarch
    if not patriarch and not matriarch:
        flash('Set a founding couple in Family Settings first.', 'info')
        return redirect(url_for('main.index'))

    # Build root node explicitly so patriarch+matriarch always show as a couple
    # regardless of whether a SpouseRelationship record exists between them.
    visited = set()
    if patriarch:
        visited.add(patriarch.id)
    if matriarch:
        visited.add(matriarch.id)

    all_root_children = list(patriarch.children if patriarch else [])
    if matriarch:
        for c in matriarch.children:
            if c not in all_root_children:
                all_root_children.append(c)
    all_root_children = [c for c in all_root_children if c.family_id == current_user.family_id]
    all_root_children.sort(key=lambda c: c.birthday or date.max)

    tree = {
        "person": patriarch or matriarch,
        "spouse": matriarch if patriarch else None,
        "children": [n for c in all_root_children if (n := build_tree_node(c, current_user.family_id, visited))]
    }

    core_ids = get_core_ids(tree)
    all_people = Person.query.filter_by(family_id=current_user.family_id).all()
    extended = [p for p in all_people if p.id not in core_ids]
    return render_template('family_tree.html', tree=tree, extended=extended, family=family)

@main.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    if current_user.is_authenticated:
        return redirect(url_for('main.index'))
    form = ForgotPasswordForm()
    if form.validate_on_submit():
        user = User.query.filter_by(email=form.email.data).first()
        if user and user.status == 'approved':
            token = secrets.token_urlsafe(32)
            user.reset_token = token
            user.reset_token_expiry = datetime.utcnow() + timedelta(hours=1)
            db.session.commit()
            reset_url = url_for('main.reset_password', token=token, _external=True)
            if current_app.config.get('MAIL_ENABLED'):
                send_password_reset_email(user, reset_url)
            else:
                flash(f'Dev mode — reset link: {reset_url}', 'info')
        # Always show this message to prevent email enumeration
        flash('If that email is registered, a reset link has been sent.', 'info')
        return redirect(url_for('main.login'))
    return render_template('forgot_password.html', form=form)

@main.route('/reset-password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    if current_user.is_authenticated:
        return redirect(url_for('main.index'))
    user = User.query.filter_by(reset_token=token).first()
    if not user or (user.reset_token_expiry and user.reset_token_expiry < datetime.utcnow()):
        flash('This reset link is invalid or has expired.', 'error')
        return redirect(url_for('main.forgot_password'))
    form = ResetPasswordForm()
    if form.validate_on_submit():
        user.set_password(form.password.data)
        user.reset_token = None
        user.reset_token_expiry = None
        db.session.commit()
        flash('Password reset successfully. You can now sign in.', 'info')
        return redirect(url_for('main.login'))
    return render_template('reset_password.html', form=form)

@main.route('/admin/users')
@login_required
@admin_required
def admin_users():
    pending = User.query.filter_by(status='pending', family_id=current_user.family_id).all()
    people = Person.query.filter_by(family_id=current_user.family_id).order_by(Person.name).all()
    return render_template('admin_users.html', pending=pending, people=people)

@main.route('/admin/approve/<int:user_id>', methods=['POST'])
@login_required
@admin_required
def approve_user(user_id):
    user = db.session.get(User, user_id)
    if not user or user.family_id != current_user.family_id:
        flash('User not found.', 'error')
        return redirect(url_for('main.admin_users'))
    user.status = 'approved'
    user.approved_by_id = current_user.id
    user.approved_date = date.today()
    db.session.commit()
    if current_app.config.get('MAIL_ENABLED'):
        send_approval_notification(user, url_for('main.login', _external=True))
    flash(f'{user.get_full_name()} has been approved.', 'info')
    return redirect(url_for('main.admin_users'))

@main.route('/admin/reject/<int:user_id>', methods=['POST'])
@login_required
@admin_required
def reject_user(user_id):
    user = db.session.get(User, user_id)
    if not user or user.family_id != current_user.family_id:
        flash('User not found.', 'error')
        return redirect(url_for('main.admin_users'))
    user.status = 'rejected'
    db.session.commit()
    flash(f'{user.get_full_name()} has been rejected.', 'info')
    return redirect(url_for('main.admin_users'))

@main.route('/profile')
@login_required
def profile():
    person = current_user.person
    if not person:
        flash('No profile found. Please contact the admin.', 'error')
        return redirect(url_for('main.index'))
    return render_template('profile.html', person=person, relationship=None)

@main.route('/profile/edit', methods=['GET', 'POST'])
@login_required
def profile_edit():
    person = current_user.person
    if not person:
        flash('No profile found. Please contact the admin.', 'error')
        return redirect(url_for('main.index'))
    form = ProfileForm(obj=person)
    if form.validate_on_submit():
        person.nickname = form.nickname.data
        person.gender = form.gender.data
        person.birthday = form.birthday.data
        person.birthplace = format_birthplace(form.birthplace.data)
        person.maiden_name = form.maiden_name.data
        person.phone = format_phone(form.phone.data)
        person.notes = form.notes.data
        person.email = current_user.email  # kept in sync with login account email
        db.session.commit()
        flash('Profile updated successfully!', 'info')
        return redirect(url_for('main.profile'))
    return render_template('profile_edit.html', form=form)

@main.route('/person/<int:person_id>')
@login_required
def person_detail(person_id):
    person = db.session.get(Person, person_id)
    if not person or person.family_id != current_user.family_id:
        flash('Person not found.', 'error')
        return redirect(url_for('main.index'))
    relationship = get_relationship(current_user.person, person) if current_user.person else None
    return render_template('profile.html', person=person, relationship=relationship)

@main.route('/person/<int:person_id>/edit', methods=['GET', 'POST'])
@login_required
def person_edit(person_id):
    person = db.session.get(Person, person_id)
    if not person or person.family_id != current_user.family_id:
        flash('Person not found.', 'error')
        return redirect(url_for('main.index'))
    can_edit = current_user.is_admin or (person.user and person.user == current_user)
    if not can_edit:
        flash('You do not have permission to edit this profile.', 'error')
        return redirect(url_for('main.person_detail', person_id=person_id))
    form = EditPersonForm(obj=person)
    if form.validate_on_submit():
        person.name = form.name.data.strip()
        person.nickname = form.nickname.data or None
        person.gender = form.gender.data or None
        person.birthday = form.birthday.data
        person.birthplace = format_birthplace(form.birthplace.data)
        person.maiden_name = form.maiden_name.data or None
        person.occupation = form.occupation.data or None
        person.phone = format_phone(form.phone.data)
        person.deathday = form.deathday.data
        person.deathplace = form.deathplace.data or None
        person.notes = form.notes.data or None
        # Only update email if person has no login account (otherwise email = login email)
        if not person.user:
            person.email = form.email.data or None
        # Handle photo upload/removal
        if form.remove_photo.data and person.photo_path:
            old_path = os.path.join(current_app.root_path, 'static', person.photo_path)
            if os.path.exists(old_path):
                os.remove(old_path)
            person.photo_path = None
            person.photo_position = '50% 30%'
        elif form.photo.data:
            file = form.photo.data
            ext = file.filename.rsplit('.', 1)[-1].lower()
            upload_dir = os.path.join(current_app.root_path, 'static', 'uploads', 'photos')
            os.makedirs(upload_dir, exist_ok=True)
            filename = f'person_{person_id}.{ext}'
            # Remove old photo if extension changed
            if person.photo_path and person.photo_path != f'uploads/photos/{filename}':
                old_path = os.path.join(current_app.root_path, 'static', person.photo_path)
                if os.path.exists(old_path):
                    os.remove(old_path)
            file.save(os.path.join(upload_dir, filename))
            person.photo_path = f'uploads/photos/{filename}'
        db.session.commit()
        flash('Profile updated.', 'info')
        return redirect(url_for('main.person_detail', person_id=person_id))
    return render_template('person_edit.html', form=form, person=person)

@main.route('/search')
@login_required
def search():
    q = request.args.get('q', '').strip()
    results = []
    if q:
        results = Person.query.filter(
            Person.family_id == current_user.family_id,
            db.or_(
                Person.name.ilike(f'%{q}%'),
                Person.nickname.ilike(f'%{q}%'),
            )
        ).order_by(Person.name).all()
    return render_template('search.html', q=q, results=results)

@main.route('/person/<int:person_id>/link-spouse', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_link_spouse(person_id):
    person = db.session.get(Person, person_id)
    if not person or person.family_id != current_user.family_id:
        flash('Person not found.', 'error')
        return redirect(url_for('main.index'))
    if person.get_active_spouse():
        flash(f'{person.get_display_name()} already has an active spouse.', 'error')
        return redirect(url_for('main.person_detail', person_id=person_id))
    eligible = [
        p for p in Person.query.filter_by(family_id=current_user.family_id).order_by(Person.name).all()
        if p.id != person.id and not p.get_active_spouse()
    ]
    form = SpouseForm()
    form.spouse_id.choices = [(0, '-- Select --')] + [(p.id, p.get_display_name()) for p in eligible]
    if form.validate_on_submit():
        spouse_person = db.session.get(Person, form.spouse_id.data)
        if not spouse_person or spouse_person.family_id != current_user.family_id:
            flash('Person not found.', 'error')
            return redirect(url_for('main.admin_link_spouse', person_id=person_id))
        rel = SpouseRelationship(
            person1_id=person.id,
            person2_id=spouse_person.id,
            marriage_date=form.marriage_date.data,
            confirmed=True,  # admin links are pre-confirmed
        )
        db.session.add(rel)
        db.session.commit()
        flash(f'{person.get_display_name()} and {spouse_person.get_display_name()} linked as spouses.', 'info')
        return redirect(url_for('main.person_detail', person_id=person_id))
    return render_template('admin_link_spouse.html', form=form, subject=person)

@main.route('/spouse/add', methods=['GET', 'POST'])
@login_required
def spouse_add():
    person = current_user.person
    if not person:
        flash('No profile found.', 'error')
        return redirect(url_for('main.index'))
    active_spouse = person.get_active_spouse()
    if active_spouse:
        flash('You already have an active spouse. Please end that relationship first.', 'error')
        return redirect(url_for('main.profile'))
    eligible = [
        p for p in Person.query.filter_by(family_id=current_user.family_id).order_by(Person.name).all()
        if p.id != person.id and not p.get_active_spouse()
    ]
    form = SpouseForm()
    form.spouse_id.choices = [(0, '-- Select --')] + [(p.id, p.get_display_name()) for p in eligible]
    invite_form = SpouseInviteForm()
    if form.submit.data and form.validate_on_submit():
        spouse_person = db.session.get(Person, form.spouse_id.data)
        if not spouse_person or spouse_person.family_id != current_user.family_id:
            flash('Person not found.', 'error')
            return redirect(url_for('main.spouse_add'))
        token = secrets.token_urlsafe(32)
        rel = SpouseRelationship(
            person1_id=person.id,
            person2_id=spouse_person.id,
            marriage_date=form.marriage_date.data,
            confirmed=False,
            confirmation_token=token,
            confirmation_token_expiry=datetime.utcnow() + timedelta(days=7)
        )
        db.session.add(rel)
        db.session.commit()
        if spouse_person.user:
            if current_app.config.get('MAIL_ENABLED'):
                send_spouse_confirmation_email(
                    person,
                    spouse_person.user,
                    url_for('main.spouse_confirm', token=token, _external=True),
                    url_for('main.spouse_decline', token=token, _external=True)
                )
            flash(f'Spouse request sent to {spouse_person.get_display_name()}.', 'info')
        else:
            flash(f'Spouse request created. {spouse_person.get_display_name()} will need to confirm when they register.', 'info')
        return redirect(url_for('main.profile'))
    return render_template('spouse_add.html', form=form, invite_form=invite_form)

@main.route('/spouse/invite', methods=['POST'])
@login_required
def spouse_invite():
    person = current_user.person
    if not person:
        flash('No profile found.', 'error')
        return redirect(url_for('main.index'))
    invite_form = SpouseInviteForm()
    if invite_form.validate_on_submit():
        existing = User.query.filter_by(email=invite_form.email.data).first()
        if existing:
            flash('An account with that email already exists.', 'error')
            return redirect(url_for('main.spouse_add'))
        full_name = f"{invite_form.first_name.data} {invite_form.last_name.data}"
        spouse_person = Person.query.filter_by(name=full_name, family_id=current_user.family_id).first()
        if not spouse_person:
            spouse_person = Person(
                name=full_name,
                email=invite_form.email.data,
                family_id=current_user.family_id,
            )
            db.session.add(spouse_person)
            db.session.flush()
        invitation_token = secrets.token_urlsafe(32)
        rel = SpouseRelationship(
            person1_id=person.id,
            person2_id=spouse_person.id,
            marriage_date=invite_form.marriage_date.data,
            confirmed=True,
        )
        db.session.add(rel)
        invited_user = User(
            first_name=invite_form.first_name.data,
            last_name=invite_form.last_name.data,
            email=invite_form.email.data,
            password_hash='invited',
            email_verified=False,
            status='invited',
            invitation_token=invitation_token,
            invitation_token_expiry=datetime.utcnow() + timedelta(days=7),
            invited_by_id=current_user.id,
            family_id=current_user.family_id,
            person_id=spouse_person.id,
        )
        db.session.add(invited_user)
        db.session.commit()
        if current_app.config.get('MAIL_ENABLED'):
            send_spouse_invitation_email(
                person,
                invite_form.email.data,
                url_for('main.register_invited', token=invitation_token, _external=True)
            )
        flash(f'Invitation sent to {full_name} at {invite_form.email.data}.', 'info')
        return redirect(url_for('main.profile'))
    form = SpouseForm()
    eligible = [
        p for p in Person.query.filter_by(family_id=current_user.family_id).order_by(Person.name).all()
        if p.id != person.id and not p.get_active_spouse()
    ]
    form.spouse_id.choices = [(0, '-- Select --')] + [(p.id, p.get_display_name()) for p in eligible]
    return render_template('spouse_add.html', form=form, invite_form=invite_form)

@main.route('/spouse/confirm/<token>', methods=['GET', 'POST'])
@login_required
def spouse_confirm(token):
    rel = SpouseRelationship.query.filter_by(confirmation_token=token).first()
    if not rel:
        flash('Invalid or expired confirmation link.', 'error')
        return redirect(url_for('main.profile'))
    if rel.confirmed:
        flash('This relationship has already been confirmed.', 'info')
        return redirect(url_for('main.profile'))
    if rel.confirmation_token_expiry and rel.confirmation_token_expiry < datetime.utcnow():
        flash('This confirmation link has expired.', 'error')
        return redirect(url_for('main.profile'))
    # Ensure only the intended recipient can confirm
    if rel.person2.user and rel.person2.user != current_user:
        flash('You are not authorized to confirm this relationship.', 'error')
        return redirect(url_for('main.profile'))
    if request.method == 'POST':
        rel.confirmed = True
        rel.confirmation_token = None
        rel.confirmation_token_expiry = None
        db.session.commit()
        flash('Spouse relationship confirmed!', 'info')
        return redirect(url_for('main.profile'))
    return render_template('spouse_confirm.html', rel=rel, token=token)

@main.route('/spouse/decline/<token>', methods=['GET', 'POST'])
@login_required
def spouse_decline(token):
    rel = SpouseRelationship.query.filter_by(confirmation_token=token).first()
    if not rel:
        flash('Invalid or expired confirmation link.', 'error')
        return redirect(url_for('main.profile'))
    if rel.confirmation_token_expiry and rel.confirmation_token_expiry < datetime.utcnow():
        flash('This confirmation link has expired.', 'error')
        return redirect(url_for('main.profile'))
    if rel.person2.user and rel.person2.user != current_user:
        flash('You are not authorized to decline this relationship.', 'error')
        return redirect(url_for('main.profile'))
    if request.method == 'GET':
        # Email decline links land here; show the confirmation page first
        return redirect(url_for('main.spouse_confirm', token=token))
    db.session.delete(rel)
    db.session.commit()
    flash('Spouse request declined.', 'info')
    return redirect(url_for('main.profile'))

@main.route('/spouse/end', methods=['GET', 'POST'])
@login_required
def spouse_end():
    person = current_user.person
    if not person:
        flash('No profile found.', 'error')
        return redirect(url_for('main.index'))
    rel = None
    for r in person.spouse_relationships_as_p1 + person.spouse_relationships_as_p2:
        if r.status == 'active' and r.confirmed:
            rel = r
            break
    if not rel:
        flash('No active spouse relationship found.', 'error')
        return redirect(url_for('main.profile'))
    form = EndSpouseForm()
    if form.validate_on_submit():
        rel.status = form.status.data
        rel.end_date = form.end_date.data
        db.session.commit()
        flash('Spouse relationship updated.', 'info')
        return redirect(url_for('main.profile'))
    return render_template('spouse_end.html', form=form, spouse=rel.get_spouse_of(person))


# ── Events ────────────────────────────────────────────────────────────────────

@main.route('/events')
@login_required
def events_list():
    today = date.today()
    all_events = Event.query.filter_by(family_id=current_user.family_id).order_by(Event.start_date).all()
    upcoming = [e for e in all_events if e.start_date >= today]
    past = [e for e in all_events if e.start_date < today]
    past.reverse()
    return render_template('events_list.html', upcoming=upcoming, past=past)


@main.route('/events/add', methods=['GET', 'POST'])
@login_required
@admin_required
def event_add():
    form = EventForm()
    if form.validate_on_submit():
        event = Event(
            family_id=current_user.family_id,
            name=form.name.data,
            description=form.description.data or None,
            location=form.location.data or None,
            start_date=form.start_date.data,
            end_date=form.end_date.data,
            has_meals=form.has_meals.data,
            has_assignments=form.has_assignments.data,
            has_sleeping=form.has_sleeping.data,
        )
        db.session.add(event)
        db.session.commit()
        flash(f'{event.name} has been created.', 'info')
        return redirect(url_for('main.event_detail', event_id=event.id))
    return render_template('event_form.html', form=form, event=None)


@main.route('/events/<int:event_id>')
@login_required
def event_detail(event_id):
    event = db.session.get(Event, event_id)
    if not event or event.family_id != current_user.family_id:
        flash('Event not found.', 'error')
        return redirect(url_for('main.events_list'))
    all_people = Person.query.filter_by(family_id=current_user.family_id).order_by(Person.name).all()
    people_choices = [(0, '— Select —')] + [(p.id, p.get_display_name()) for p in all_people]

    meal_form = EventMealForm()
    meal_item_form = EventMealItemForm()
    # Per-meal family-assign forms (admin only)
    meal_family_forms = {}
    for meal in event.meals:
        f = EventMealFamilyAssignForm(prefix=f'meal_fam_{meal.id}')
        f.assigned_family_id.choices = [(0, '— Select family —')] + [(p.id, p.get_display_name()) for p in all_people]
        meal_family_forms[meal.id] = f
    # Per-item assign forms (any member can assign anyone)
    item_assign_forms = {}
    for meal in event.meals:
        for item in meal.items:
            f = EventMealAssignForm(prefix=f'item_{item.id}')
            f.person_id.choices = people_choices
            item_assign_forms[item.id] = f

    assign_form = EventAssignmentForm()
    spot_form = EventSleepingSpotForm()
    sleeping_assign_forms = {}
    if current_user.is_admin:
        eligible = all_people
        for spot in event.sleeping_spots:
            spot_assigned_ids = {p.id for p in spot.people}
            available = [(p.id, p.get_display_name()) for p in eligible if p.id not in spot_assigned_ids]
            f = EventSleepingAssignForm(prefix=f'spot_{spot.id}')
            f.person_id.choices = [(0, '— Select —')] + available
            sleeping_assign_forms[spot.id] = f

    my_person = current_user.person
    return render_template('event_detail.html',
        event=event,
        meal_form=meal_form,
        meal_item_form=meal_item_form,
        meal_family_forms=meal_family_forms,
        item_assign_forms=item_assign_forms,
        people_choices=people_choices,
        assign_form=assign_form,
        spot_form=spot_form,
        sleeping_assign_forms=sleeping_assign_forms,
        my_person=my_person,
    )


@main.route('/events/<int:event_id>/edit', methods=['GET', 'POST'])
@login_required
@admin_required
def event_edit(event_id):
    event = db.session.get(Event, event_id)
    if not event or event.family_id != current_user.family_id:
        flash('Event not found.', 'error')
        return redirect(url_for('main.events_list'))
    form = EventForm(obj=event)
    if form.validate_on_submit():
        event.name = form.name.data
        event.description = form.description.data or None
        event.location = form.location.data or None
        event.start_date = form.start_date.data
        event.end_date = form.end_date.data
        event.has_meals = form.has_meals.data
        event.has_assignments = form.has_assignments.data
        event.has_sleeping = form.has_sleeping.data
        db.session.commit()
        flash('Event updated.', 'info')
        return redirect(url_for('main.event_detail', event_id=event.id))
    return render_template('event_form.html', form=form, event=event)


@main.route('/events/<int:event_id>/delete', methods=['POST'])
@login_required
@admin_required
def event_delete(event_id):
    event = db.session.get(Event, event_id)
    if not event or event.family_id != current_user.family_id:
        flash('Event not found.', 'error')
        return redirect(url_for('main.events_list'))
    db.session.delete(event)
    db.session.commit()
    flash(f'{event.name} has been deleted.', 'info')
    return redirect(url_for('main.events_list'))


# ── Meals ─────────────────────────────────────────────────────────────────────

@main.route('/events/<int:event_id>/meals/add', methods=['POST'])
@login_required
@admin_required
def event_meal_add(event_id):
    event = db.session.get(Event, event_id)
    if not event or event.family_id != current_user.family_id:
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
    if not meal or meal.event.family_id != current_user.family_id:
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
    if not meal or meal.event.family_id != current_user.family_id:
        flash('Meal not found.', 'error')
        return redirect(url_for('main.event_detail', event_id=event_id))
    all_people = Person.query.filter_by(family_id=current_user.family_id).order_by(Person.name).all()
    form = EventMealFamilyAssignForm(prefix=f'meal_fam_{meal_id}')
    form.assigned_family_id.choices = [(0, '— Select family —')] + [(p.id, p.get_display_name()) for p in all_people]
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
    if not meal or meal.event.family_id != current_user.family_id:
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
    if not meal or meal.event.family_id != current_user.family_id:
        flash('Meal not found.', 'error')
        return redirect(url_for('main.event_detail', event_id=event_id))
    form = EventMealItemForm()
    if form.validate_on_submit():
        item = EventMealItem(
            meal_id=meal_id,
            label=form.label.data,
            is_cleanup=form.is_cleanup.data,
        )
        db.session.add(item)
        db.session.commit()
    return redirect(url_for('main.event_detail', event_id=event_id))


@main.route('/events/<int:event_id>/meals/<int:meal_id>/items/<int:item_id>/assign', methods=['POST'])
@login_required
def event_meal_item_assign(event_id, meal_id, item_id):
    item = db.session.get(EventMealItem, item_id)
    if not item or item.meal.event.family_id != current_user.family_id:
        flash('Item not found.', 'error')
        return redirect(url_for('main.event_detail', event_id=event_id))
    all_people = Person.query.filter_by(family_id=current_user.family_id).order_by(Person.name).all()
    form = EventMealAssignForm(prefix=f'item_{item_id}')
    form.person_id.choices = [(0, '— Select —')] + [(p.id, p.get_display_name()) for p in all_people]
    if form.validate_on_submit() and form.person_id.data:
        person = db.session.get(Person, form.person_id.data)
        if person and person.family_id == current_user.family_id:
            item.assigned_to_id = person.id
            db.session.commit()
    return redirect(url_for('main.event_detail', event_id=event_id))


@main.route('/events/<int:event_id>/meals/<int:meal_id>/items/<int:item_id>/unassign', methods=['POST'])
@login_required
def event_meal_item_unassign(event_id, meal_id, item_id):
    item = db.session.get(EventMealItem, item_id)
    if not item or item.meal.event.family_id != current_user.family_id:
        flash('Item not found.', 'error')
        return redirect(url_for('main.event_detail', event_id=event_id))
    if not current_user.is_admin:
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
    if not item or item.meal.event.family_id != current_user.family_id:
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
    if not event or event.family_id != current_user.family_id:
        flash('Event not found.', 'error')
        return redirect(url_for('main.events_list'))
    form = EventAssignmentForm()
    if form.validate_on_submit():
        a = EventAssignment(
            event_id=event_id,
            title=form.title.data,
            description=form.description.data or None,
        )
        db.session.add(a)
        db.session.commit()
        flash(f'Task "{a.title}" added.', 'info')
    return redirect(url_for('main.event_detail', event_id=event_id))


@main.route('/events/<int:event_id>/assignments/<int:aid>/claim', methods=['POST'])
@login_required
def event_assignment_claim(event_id, aid):
    a = db.session.get(EventAssignment, aid)
    if not a or a.event.family_id != current_user.family_id:
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
    if not a or a.event.family_id != current_user.family_id:
        flash('Task not found.', 'error')
        return redirect(url_for('main.event_detail', event_id=event_id))
    is_own = current_user.person and a.claimed_by_id == current_user.person.id
    if not is_own and not current_user.is_admin:
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
    if not a or a.event.family_id != current_user.family_id:
        flash('Task not found.', 'error')
        return redirect(url_for('main.event_detail', event_id=event_id))
    is_own = current_user.person and a.claimed_by_id == current_user.person.id
    if not is_own and not current_user.is_admin:
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
    if not a or a.event.family_id != current_user.family_id:
        flash('Task not found.', 'error')
        return redirect(url_for('main.event_detail', event_id=event_id))
    db.session.delete(a)
    db.session.commit()
    flash('Task removed.', 'info')
    return redirect(url_for('main.event_detail', event_id=event_id))


# ── Sleeping ──────────────────────────────────────────────────────────────────

@main.route('/events/<int:event_id>/sleeping/add-spot', methods=['POST'])
@login_required
@admin_required
def event_sleeping_add_spot(event_id):
    event = db.session.get(Event, event_id)
    if not event or event.family_id != current_user.family_id:
        flash('Event not found.', 'error')
        return redirect(url_for('main.events_list'))
    form = EventSleepingSpotForm()
    if form.validate_on_submit():
        spot = EventSleepingSpot(
            event_id=event_id,
            name=form.name.data,
            capacity=form.capacity.data,
            notes=form.notes.data or None,
        )
        db.session.add(spot)
        db.session.commit()
        flash(f'"{spot.name}" added.', 'info')
    return redirect(url_for('main.event_detail', event_id=event_id))


@main.route('/events/<int:event_id>/sleeping/<int:sid>/assign', methods=['POST'])
@login_required
@admin_required
def event_sleeping_assign(event_id, sid):
    spot = db.session.get(EventSleepingSpot, sid)
    if not spot or spot.event.family_id != current_user.family_id:
        flash('Spot not found.', 'error')
        return redirect(url_for('main.event_detail', event_id=event_id))
    form = EventSleepingAssignForm(prefix=f'spot_{sid}')
    eligible = Person.query.filter_by(family_id=current_user.family_id).order_by(Person.name).all()
    spot_assigned_ids = {p.id for p in spot.people}
    form.person_id.choices = [(0, '— Select —')] + [(p.id, p.get_display_name()) for p in eligible if p.id not in spot_assigned_ids]
    if form.validate_on_submit() and form.person_id.data:
        person = db.session.get(Person, form.person_id.data)
        if person and person.family_id == current_user.family_id and person not in spot.people:
            spot.people.append(person)
            db.session.commit()
            flash(f'{person.get_display_name()} assigned to {spot.name}.', 'info')
    return redirect(url_for('main.event_detail', event_id=event_id))


@main.route('/events/<int:event_id>/sleeping/<int:sid>/unassign/<int:pid>', methods=['POST'])
@login_required
@admin_required
def event_sleeping_unassign(event_id, sid, pid):
    spot = db.session.get(EventSleepingSpot, sid)
    if not spot or spot.event.family_id != current_user.family_id:
        flash('Spot not found.', 'error')
        return redirect(url_for('main.event_detail', event_id=event_id))
    person = db.session.get(Person, pid)
    if person and person in spot.people:
        spot.people.remove(person)
        db.session.commit()
        flash(f'{person.get_display_name()} removed from {spot.name}.', 'info')
    return redirect(url_for('main.event_detail', event_id=event_id))


@main.route('/events/<int:event_id>/sleeping/<int:sid>/delete', methods=['POST'])
@login_required
@admin_required
def event_sleeping_delete_spot(event_id, sid):
    spot = db.session.get(EventSleepingSpot, sid)
    if not spot or spot.event.family_id != current_user.family_id:
        flash('Spot not found.', 'error')
        return redirect(url_for('main.event_detail', event_id=event_id))
    db.session.delete(spot)
    db.session.commit()
    flash(f'"{spot.name}" removed.', 'info')
    return redirect(url_for('main.event_detail', event_id=event_id))
