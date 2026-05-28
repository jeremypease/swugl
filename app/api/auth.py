import requests as http
from flask import request, jsonify, current_app
from flask_jwt_extended import (
    create_access_token, create_refresh_token,
    jwt_required, get_jwt_identity, get_jwt,
    decode_token,
)
from datetime import timedelta
import pyotp
import jwt as pyjwt

from .. import db
from ..models import User, OAuthAccount, ApiTokenBlocklist
from . import api
from .utils import error_response, serialize_user


def _issue_tokens(user):
    additional_claims = {'family_id': user.family_id}
    access = create_access_token(identity=str(user.id), additional_claims=additional_claims)
    refresh = create_refresh_token(identity=str(user.id), additional_claims=additional_claims)
    return access, refresh


@api.route('/auth/login', methods=['POST'])
def login():
    data = request.get_json(silent=True) or {}
    email = (data.get('email') or '').strip().lower()
    password = data.get('password') or ''

    user = User.query.filter_by(email=email).first()
    if not user or not user.check_password(password):
        return error_response(401, 'Invalid email or password.', 'invalid_credentials')
    if not user.email_verified:
        return error_response(403, 'Email address not verified.', 'email_not_verified')
    if user.status != 'approved':
        return error_response(403, 'Account is pending approval.', 'account_not_approved')

    if user.totp_enabled:
        partial = create_access_token(
            identity=str(user.id),
            expires_delta=timedelta(minutes=5),
            additional_claims={'partial': True},
        )
        return jsonify({'requires_2fa': True, 'partial_token': partial}), 200

    access, refresh = _issue_tokens(user)
    return jsonify({'access_token': access, 'refresh_token': refresh, 'user': serialize_user(user)}), 200


@api.route('/auth/login/totp', methods=['POST'])
def login_totp():
    data = request.get_json(silent=True) or {}
    partial_token = data.get('partial_token') or ''
    code = str(data.get('code') or '')

    try:
        decoded = decode_token(partial_token)
    except Exception:
        return error_response(401, 'Invalid or expired token.', 'token_invalid')

    if not decoded.get('partial'):
        return error_response(401, 'Not a 2FA partial token.', 'token_invalid')

    user = User.query.get(int(decoded['sub']))
    if not user:
        return error_response(401, 'User not found.', 'invalid_credentials')

    if not pyotp.TOTP(user.totp_secret).verify(code, valid_window=1):
        return error_response(401, 'Invalid TOTP code.', 'invalid_totp')

    access, refresh = _issue_tokens(user)
    return jsonify({'access_token': access, 'refresh_token': refresh, 'user': serialize_user(user)}), 200


@api.route('/auth/refresh', methods=['POST'])
@jwt_required(refresh=True)
def refresh():
    user_id = get_jwt_identity()
    user = User.query.get(int(user_id))
    if not user:
        return error_response(401, 'User not found.', 'invalid_credentials')
    additional_claims = {'family_id': user.family_id}
    access = create_access_token(identity=user_id, additional_claims=additional_claims)
    return jsonify({'access_token': access}), 200


@api.route('/auth/logout', methods=['POST'])
@jwt_required(refresh=True)
def logout():
    jti = get_jwt()['jti']
    db.session.add(ApiTokenBlocklist(jti=jti))
    db.session.commit()
    return jsonify({'ok': True}), 200


@api.route('/auth/google', methods=['POST'])
def google_signin():
    from google.oauth2 import id_token as google_id_token
    from google.auth.transport.requests import Request as GoogleRequest

    data = request.get_json(silent=True) or {}
    token = data.get('id_token') or ''
    client_id = current_app.config.get('GOOGLE_CLIENT_ID')

    if not client_id:
        return error_response(500, 'Google sign-in not configured.', 'not_configured')

    try:
        idinfo = google_id_token.verify_oauth2_token(token, GoogleRequest(), client_id)
    except Exception:
        return error_response(401, 'Invalid Google token.', 'invalid_token')

    google_uid = idinfo['sub']
    email = idinfo.get('email', '').lower()

    oauth = OAuthAccount.query.filter_by(provider='google', provider_user_id=google_uid).first()
    if oauth:
        user = oauth.user
    else:
        user = User.query.filter_by(email=email).first()
        if not user:
            return error_response(404, 'No account linked to this Google identity.', 'no_account')

    if user.status != 'approved':
        return error_response(403, 'Account is pending approval.', 'account_not_approved')

    access, refresh = _issue_tokens(user)
    return jsonify({'access_token': access, 'refresh_token': refresh, 'user': serialize_user(user)}), 200


@api.route('/auth/apple', methods=['POST'])
def apple_signin():
    data = request.get_json(silent=True) or {}
    identity_token = data.get('identity_token') or ''

    bundle_id = current_app.config.get('APPLE_BUNDLE_ID')
    client_id = current_app.config.get('APPLE_CLIENT_ID')

    if not bundle_id and not client_id:
        return error_response(500, 'Apple sign-in not configured.', 'not_configured')

    try:
        jwks_resp = http.get('https://appleid.apple.com/auth/keys', timeout=5)
        jwks_resp.raise_for_status()
        keys = jwks_resp.json()['keys']
    except Exception:
        return error_response(503, 'Could not reach Apple servers.', 'upstream_error')

    try:
        header = pyjwt.get_unverified_header(identity_token)
        kid = header.get('kid')
        key_data = next((k for k in keys if k['kid'] == kid), None)
        if not key_data:
            raise ValueError('No matching Apple public key')
        public_key = pyjwt.algorithms.RSAAlgorithm.from_jwk(key_data)

        valid_audiences = [a for a in [bundle_id, client_id] if a]
        payload = pyjwt.decode(
            identity_token, public_key, algorithms=['RS256'],
            audience=valid_audiences,
            issuer='https://appleid.apple.com',
        )
    except Exception:
        return error_response(401, 'Invalid Apple identity token.', 'invalid_token')

    apple_uid = payload['sub']
    email = payload.get('email', '').lower() or None

    from ..models import OAuthAccount
    oauth = OAuthAccount.query.filter_by(provider='apple', provider_user_id=apple_uid).first()
    if oauth:
        user = oauth.user
    else:
        user = User.query.filter_by(email=email).first() if email else None
        if not user:
            return error_response(404, 'No account linked to this Apple ID.', 'no_account')

    if user.status != 'approved':
        return error_response(403, 'Account is pending approval.', 'account_not_approved')

    access, refresh = _issue_tokens(user)
    return jsonify({'access_token': access, 'refresh_token': refresh, 'user': serialize_user(user)}), 200


@api.route('/auth/me', methods=['GET'])
@jwt_required()
def me():
    user_id = get_jwt_identity()
    user = User.query.get(int(user_id))
    if not user:
        return error_response(401, 'User not found.', 'invalid_credentials')
    data = serialize_user(user)
    data['family_name'] = user.family.name if user.family else None
    return jsonify(data), 200
