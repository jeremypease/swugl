from flask import Blueprint, render_template, redirect, url_for, flash, request, current_app
from flask_login import login_user, logout_user, login_required, current_user
from .models import Family, User, Person, SpouseRelationship
from .forms import LoginForm, RegistrationForm, ProfileForm, SpouseForm, EndSpouseForm, SpouseInviteForm
from .email import send_verification_email, send_pending_notification, send_approval_notification, send_spouse_confirmation_email, send_spouse_invitation_email
from datetime import date, datetime, timedelta
from functools import wraps
from urllib.parse import urlparse
from . import db
import secrets
import re

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

@main.route('/')
@login_required
def index():
    people = Person.query.filter_by(family_id=current_user.family_id).order_by(Person.name).all()
    return render_template('index.html', people=people, family=current_user.family)

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

@main.route('/admin/users')
@login_required
@admin_required
def admin_users():
    pending = User.query.filter_by(status='pending', family_id=current_user.family_id).all()
    users = User.query.filter_by(family_id=current_user.family_id).order_by(User.last_name).all()
    return render_template('admin_users.html', pending=pending, users=users)

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
    return render_template('profile.html', person=person)

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
    return render_template('profile.html', person=person)

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
