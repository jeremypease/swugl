from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_user, logout_user, login_required, current_user
from .models import User, Person
from .forms import LoginForm, RegistrationForm
from .email import send_verification_email, send_pending_notification, send_approval_notification
from datetime import date
from . import db
import secrets

main = Blueprint('main', __name__)

@main.route('/')
@login_required
def index():
    people = Person.query.order_by(Person.name).all()
    return render_template('index.html', people=people)

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
        return redirect(next_page or url_for('main.index'))

    return render_template('login.html', form=form)

@main.route('/logout')
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
        # Check if email already exists
        existing_user = User.query.filter_by(email=form.email.data).first()
        if existing_user:
            flash('An account with that email already exists.', 'error')
            return redirect(url_for('main.register'))

        # Check if person already exists in tree
        full_name = f"{form.first_name.data} {form.last_name.data}"
        person = Person.query.filter_by(name=full_name).first()

        # Create person if not found
        if not person:
            person = Person(
                name=full_name,
                email=form.email.data,
            )
            db.session.add(person)
            db.session.flush()

        # Create user account
        token = secrets.token_urlsafe(32)
        user = User(
            first_name=form.first_name.data,
            last_name=form.last_name.data,
            email=form.email.data,
            phone=form.phone.data,
            verification_token=token,
            email_verified=False,
            status='pending',
            person_id=person.id
        )
        user.set_password(form.password.data)
        db.session.add(user)
        db.session.commit()

        # In development, auto-verify email
        import os
        if os.environ.get('FLASK_ENV') == 'production':
            send_verification_email(user, token)
            admins = User.query.filter_by(is_admin=True, status='approved').all()
            for admin in admins:
                send_pending_notification(admin.email, user)
            flash('Registration successful! Please check your email to verify your account.', 'info')
        else:
            user.email_verified = True
            db.session.commit()
            flash('Registration successful! Your account is pending admin approval.', 'info')

        return redirect(url_for('main.login'))



    return render_template('register.html', form=form)

@main.route('/verify/<token>')
def verify_email(token):
    user = User.query.filter_by(verification_token=token).first()
    if not user:
        flash('Invalid or expired verification link.', 'error')
        return redirect(url_for('main.login'))

    user.email_verified = True
    user.verification_token = None
    db.session.commit()

    flash('Email verified! Your account is pending admin approval.', 'info')
    return redirect(url_for('main.login'))

from functools import wraps

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin:
            flash('You do not have permission to access that page.', 'error')
            return redirect(url_for('main.index'))
        return f(*args, **kwargs)
    return decorated_function

@main.route('/admin/users')
@login_required
@admin_required
def admin_users():
    pending = User.query.filter_by(status='pending').all()
    users = User.query.order_by(User.last_name).all()
    return render_template('admin_users.html', pending=pending, users=users)

@main.route('/admin/approve/<int:user_id>', methods=['POST'])
@login_required
@admin_required
def approve_user(user_id):
    user = User.query.get(user_id)
    if not user:
        flash('User not found.', 'error')
        return redirect(url_for('main.admin_users'))

    user.status = 'approved'
    user.approved_by_id = current_user.id
    user.approved_date = date.today()
    db.session.commit()

    import os
    if os.environ.get('FLASK_ENV') == 'production':
        send_approval_notification(user)

    flash(f'{user.get_full_name()} has been approved.', 'info')
    return redirect(url_for('main.admin_users'))

@main.route('/admin/reject/<int:user_id>', methods=['POST'])
@login_required
@admin_required
def reject_user(user_id):
    user = User.query.get(user_id)
    if not user:
        flash('User not found.', 'error')
        return redirect(url_for('main.admin_users'))

    user.status = 'rejected'
    db.session.commit()

    flash(f'{user.get_full_name()} has been rejected.', 'info')
    return redirect(url_for('main.admin_users'))
