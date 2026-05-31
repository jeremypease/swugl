import json
import time
import requests as http_requests
import jwt as pyjwt
from flask import Blueprint, redirect, url_for, flash, session, current_app, abort, request
from flask_login import login_user, login_required, current_user
from authlib.integrations.flask_client import OAuth

from . import db
from .models import User, OAuthAccount

oauth_bp = Blueprint('oauth', __name__)
oauth = OAuth()


def _apple_client_secret(app):
    private_key = app.config.get('APPLE_PRIVATE_KEY', '').replace('\\n', '\n')
    now = int(time.time())
    payload = {
        'iss': app.config['APPLE_TEAM_ID'],
        'iat': now,
        'exp': now + 86400 * 180,
        'aud': 'https://appleid.apple.com',
        'sub': app.config['APPLE_CLIENT_ID'],
    }
    return pyjwt.encode(
        payload, private_key, algorithm='ES256',
        headers={'kid': app.config['APPLE_KEY_ID']},
    )


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
    if app.config.get('APPLE_CLIENT_ID'):
        try:
            apple_secret = _apple_client_secret(app)
            decoded = pyjwt.decode(apple_secret, options={"verify_signature": False})
            app.logger.info(
                f'Apple client secret claims: iss={decoded.get("iss")} '
                f'sub={decoded.get("sub")} aud={decoded.get("aud")} '
                f'exp={decoded.get("exp")} kid={pyjwt.get_unverified_header(apple_secret).get("kid")}'
            )
            oauth.register(
                name='apple',
                client_id=app.config['APPLE_CLIENT_ID'],
                client_secret=apple_secret,
                server_metadata_url='https://appleid.apple.com/.well-known/openid-configuration',
                client_kwargs={
                    'scope': 'name email',
                    'response_mode': 'form_post',
                    'token_endpoint_auth_method': 'client_secret_post',
                },
            )
        except Exception as e:
            app.logger.error(f'Apple Sign-In failed to initialize: {e}')


# ── Google ────────────────────────────────────────────────────────────────────

@oauth_bp.route('/auth/google')
def google_login():
    if not current_app.config.get('GOOGLE_CLIENT_ID'):
        flash('Google sign-in is not configured.', 'error')
        return redirect(url_for('main.login'))
    if current_user.is_authenticated:
        return redirect(url_for('main.home'))
    redirect_uri = url_for('oauth.google_callback', _external=True)
    return oauth.google.authorize_redirect(redirect_uri)


@oauth_bp.route('/profile/security/oauth/link/google')
@login_required
def google_link():
    if not current_app.config.get('GOOGLE_CLIENT_ID'):
        flash('Google sign-in is not configured.', 'error')
        return redirect(url_for('tf.security'))
    session['oauth_action'] = 'link'
    redirect_uri = url_for('oauth.google_callback', _external=True)
    return oauth.google.authorize_redirect(redirect_uri)


@oauth_bp.route('/auth/google/callback')
def google_callback():
    if not current_app.config.get('GOOGLE_CLIENT_ID'):
        return redirect(url_for('main.login'))

    linking = session.pop('oauth_action', None) == 'link'

    try:
        token = oauth.google.authorize_access_token()
    except Exception:
        flash('Google sign-in failed. Please try again.', 'error')
        return redirect(url_for('tf.security') if linking else url_for('main.login'))

    userinfo = token.get('userinfo')
    if not userinfo or not userinfo.get('email_verified'):
        flash('Google sign-in failed: email not verified with Google.', 'error')
        return redirect(url_for('tf.security') if linking else url_for('main.login'))

    provider_id = userinfo['sub']
    email = userinfo['email']

    if linking:
        if not current_user.is_authenticated:
            abort(401)
        existing = OAuthAccount.query.filter_by(
            provider='google', provider_user_id=provider_id
        ).first()
        if existing and existing.user_id != current_user.id:
            flash('That Google account is already linked to a different user.', 'error')
            return redirect(url_for('tf.security'))
        if not existing:
            link = OAuthAccount(user_id=current_user.id, provider='google',
                                provider_user_id=provider_id)
            db.session.add(link)
            db.session.commit()
        flash('Google account connected.', 'info')
        return redirect(url_for('tf.security'))

    link = OAuthAccount.query.filter_by(provider='google', provider_user_id=provider_id).first()
    if link:
        user = link.user
    else:
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


# ── Apple ─────────────────────────────────────────────────────────────────────

@oauth_bp.route('/auth/apple')
def apple_login():
    if not current_app.config.get('APPLE_CLIENT_ID'):
        flash('Apple sign-in is not configured.', 'error')
        return redirect(url_for('main.login'))
    if current_user.is_authenticated:
        return redirect(url_for('main.home'))
    redirect_uri = url_for('oauth.apple_callback', _external=True)
    current_app.logger.info(f'Apple login redirect_uri: {redirect_uri}')
    return oauth.apple.authorize_redirect(redirect_uri)


@oauth_bp.route('/profile/security/oauth/link/apple')
@login_required
def apple_link():
    if not current_app.config.get('APPLE_CLIENT_ID'):
        flash('Apple sign-in is not configured.', 'error')
        return redirect(url_for('tf.security'))
    session['oauth_action'] = 'link'
    redirect_uri = url_for('oauth.apple_callback', _external=True)
    return oauth.apple.authorize_redirect(redirect_uri)


@oauth_bp.route('/auth/apple/callback', methods=['POST'])
def apple_callback():
    if not current_app.config.get('APPLE_CLIENT_ID'):
        return redirect(url_for('main.login'))

    linking = session.pop('oauth_action', None) == 'link'

    # Capture these before authorize_access_token() consumes the form data.
    form_id_token = request.form.get('id_token')
    form_code = request.form.get('code')

    try:
        token = oauth.apple.authorize_access_token()
    except Exception as e:
        # Safari's native Apple Sign-In submits the form_post in a WebKit context
        # that doesn't carry the session cookie, causing a state mismatch. Fall back
        # to a direct token exchange so the callback always succeeds.
        current_app.logger.warning(f'Apple authorize_access_token failed ({e}), trying direct exchange')
        if not form_code:
            current_app.logger.error('Apple callback: no code in form, cannot recover')
            flash('Apple sign-in failed. Please try again.', 'error')
            return redirect(url_for('tf.security') if linking else url_for('main.login'))
        try:
            redirect_uri = url_for('oauth.apple_callback', _external=True)
            client_secret = _apple_client_secret(current_app._get_current_object())
            resp = http_requests.post(
                'https://appleid.apple.com/auth/token',
                data={
                    'client_id': current_app.config['APPLE_CLIENT_ID'],
                    'client_secret': client_secret,
                    'code': form_code,
                    'grant_type': 'authorization_code',
                    'redirect_uri': redirect_uri,
                },
                timeout=10,
            )
            resp.raise_for_status()
            token = resp.json()
            current_app.logger.info(f'Apple direct exchange succeeded, keys={list(token.keys())}')
        except Exception as e2:
            current_app.logger.error(
                f'Apple direct exchange failed: {e2} | '
                f'client_id={current_app.config.get("APPLE_CLIENT_ID")} '
                f'redirect_uri={url_for("oauth.apple_callback", _external=True)}'
            )
            flash('Apple sign-in failed. Please try again.', 'error')
            return redirect(url_for('tf.security') if linking else url_for('main.login'))

    raw_id_token = token.get('id_token')
    current_app.logger.info(
        f'Apple token keys: {list(token.keys())} | '
        f'userinfo: {token.get("userinfo")} | '
        f'id_token present: {bool(raw_id_token)} | '
        f'form id_token present: {bool(form_id_token)}'
    )

    id_token_claims = token.get('userinfo') or {}
    provider_id = id_token_claims.get('sub')

    def _decode_jwt(raw):
        if isinstance(raw, dict):
            return raw
        return pyjwt.decode(str(raw), options={"verify_signature": False})

    # Fallback 1: decode id_token from the token exchange response
    if not provider_id and raw_id_token:
        try:
            claims = _decode_jwt(raw_id_token)
            provider_id = claims.get('sub')
            if provider_id:
                id_token_claims = claims
                current_app.logger.info(f'Apple sub from token response id_token: {provider_id}')
            else:
                current_app.logger.error(f'Apple token response id_token has no sub. claims={claims}')
        except Exception as e:
            current_app.logger.error(f'Apple token response id_token decode failed: {e}')

    # Fallback 2: decode the id_token Apple included in the form_post body
    if not provider_id and form_id_token:
        try:
            claims = _decode_jwt(form_id_token)
            provider_id = claims.get('sub')
            if provider_id:
                id_token_claims = claims
                current_app.logger.info(f'Apple sub from form_post id_token: {provider_id}')
            else:
                current_app.logger.error(f'Apple form_post id_token has no sub. claims={claims}')
        except Exception as e:
            current_app.logger.error(f'Apple form_post id_token decode failed: {e}')

    if not provider_id:
        current_app.logger.error(
            f'Apple sign-in: no sub found. '
            f'token keys={list(token.keys())} form keys={list(request.form.keys())}'
        )
        flash('Apple sign-in failed: no user identifier returned.', 'error')
        return redirect(url_for('tf.security') if linking else url_for('main.login'))

    # Apple sends email in id_token claims on first sign-in only.
    # The 'user' form field (JSON) is also only present on first sign-in.
    email = id_token_claims.get('email')
    user_json = request.form.get('user')
    if user_json and not email:
        try:
            user_data = json.loads(user_json)
            email = user_data.get('email')
        except (ValueError, KeyError):
            pass

    if linking:
        if not current_user.is_authenticated:
            abort(401)
        existing = OAuthAccount.query.filter_by(
            provider='apple', provider_user_id=provider_id
        ).first()
        if existing and existing.user_id != current_user.id:
            flash('That Apple account is already linked to a different user.', 'error')
            return redirect(url_for('tf.security'))
        if not existing:
            link = OAuthAccount(user_id=current_user.id, provider='apple',
                                provider_user_id=provider_id)
            db.session.add(link)
            db.session.commit()
        flash('Apple account connected.', 'info')
        return redirect(url_for('tf.security'))

    link = OAuthAccount.query.filter_by(provider='apple', provider_user_id=provider_id).first()
    if link:
        user = link.user
    else:
        user = User.query.filter_by(email=email).first() if email else None
        if not user:
            flash('No account found for that Apple ID. '
                  'Please register or ask a family admin to invite you.', 'error')
            return redirect(url_for('main.login'))
        link = OAuthAccount(user_id=user.id, provider='apple', provider_user_id=provider_id)
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


# ── Shared unlink ─────────────────────────────────────────────────────────────

@oauth_bp.route('/profile/security/oauth/unlink/<provider>', methods=['POST'])
@login_required
def oauth_unlink(provider):
    link = OAuthAccount.query.filter_by(
        user_id=current_user.id, provider=provider
    ).first_or_404()

    if not current_user.password_hash:
        flash('You cannot unlink your only sign-in method.', 'error')
        return redirect(url_for('tf.security'))

    db.session.delete(link)
    db.session.commit()
    flash(f'{provider.capitalize()} account unlinked.', 'info')
    return redirect(url_for('tf.security'))
