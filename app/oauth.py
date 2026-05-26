from flask import Blueprint, redirect, url_for, flash, session, current_app
from flask_login import login_user, current_user
from authlib.integrations.flask_client import OAuth

from . import db
from .models import User, OAuthAccount

oauth_bp = Blueprint('oauth', __name__)
oauth = OAuth()


def init_oauth(app):
    oauth.init_app(app)
    if app.config.get('GOOGLE_CLIENT_ID'):
        oauth.register(
            name='google',
            client_id=app.config['GOOGLE_CLIENT_ID'],
            client_secret=app.config['GOOGLE_CLIENT_SECRET'],
            server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
            client_kwargs={'scope': 'openid email profile'},
        )


@oauth_bp.route('/auth/google')
def google_login():
    if not current_app.config.get('GOOGLE_CLIENT_ID'):
        flash('Google sign-in is not configured.', 'error')
        return redirect(url_for('main.login'))
    if current_user.is_authenticated:
        return redirect(url_for('main.home'))
    redirect_uri = url_for('oauth.google_callback', _external=True)
    return oauth.google.authorize_redirect(redirect_uri)


@oauth_bp.route('/auth/google/callback')
def google_callback():
    if not current_app.config.get('GOOGLE_CLIENT_ID'):
        return redirect(url_for('main.login'))

    try:
        token = oauth.google.authorize_access_token()
    except Exception:
        flash('Google sign-in failed. Please try again.', 'error')
        return redirect(url_for('main.login'))

    userinfo = token.get('userinfo')
    if not userinfo or not userinfo.get('email_verified'):
        flash('Google sign-in failed: email not verified with Google.', 'error')
        return redirect(url_for('main.login'))

    provider_id = userinfo['sub']
    email = userinfo['email']

    # 1. Existing OAuth link → log straight in
    link = OAuthAccount.query.filter_by(provider='google', provider_user_id=provider_id).first()
    if link:
        user = link.user
    else:
        # 2. Email match → link this Google account to the existing user
        user = User.query.filter_by(email=email).first()
        if not user:
            flash('No account found for that Google address. '
                  'Please register or ask a family admin to invite you.', 'error')
            return redirect(url_for('main.login'))
        link = OAuthAccount(user_id=user.id, provider='google', provider_user_id=provider_id)
        db.session.add(link)
        db.session.commit()

    if not user.email_verified:
        flash('Please verify your email before signing in.', 'error')
        return redirect(url_for('main.login'))
    if user.status != 'approved':
        flash('Your account is pending approval.', 'error')
        return redirect(url_for('main.login'))

    login_user(user)
    session['active_family_id'] = user.family_id
    return redirect(url_for('main.home'))
