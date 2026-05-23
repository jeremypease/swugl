"""Two-factor authentication — passkeys (WebAuthn) and TOTP."""
import base64
import io
import json

import pyotp
import qrcode
import webauthn
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)
from flask import (Blueprint, current_app, flash, jsonify, redirect,
                   render_template, request, session, url_for)
from flask_login import current_user, login_required, login_user

from . import db
from .models import User, UserCredential

tf = Blueprint('tf', __name__)


# ── Helpers ────────────────────────────────────────────────────────────────

def _rp_id():
    return current_app.config.get('WEBAUTHN_RP_ID', 'localhost')

def _rp_name():
    return current_app.config.get('WEBAUTHN_RP_NAME', 'OurPeaPod')

def _origin():
    return current_app.config.get('WEBAUTHN_ORIGIN', 'http://localhost:5000')


def _get_pending_user():
    """Return the user awaiting 2FA, or None if session is missing/stale."""
    uid = session.get('pending_2fa_user_id')
    return db.session.get(User, uid) if uid else None


def _qr_png_b64(uri):
    qr = qrcode.QRCode(version=1, box_size=5, border=4)
    qr.add_data(uri)
    qr.make(fit=True)
    img = qr.make_image(fill_color='black', back_color='white')
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    return base64.b64encode(buf.getvalue()).decode()


# ── Security settings page ─────────────────────────────────────────────────

@tf.route('/profile/security')
@login_required
def security():
    return render_template('profile_security.html',
                           passkeys=current_user.passkeys,
                           totp_enabled=current_user.totp_enabled)


# ── Passkey registration ───────────────────────────────────────────────────

@tf.route('/profile/security/passkeys/register/begin', methods=['POST'])
@login_required
def passkey_register_begin():
    existing = [
        PublicKeyCredentialDescriptor(id=base64.b64decode(c.credential_id + '=='))
        for c in current_user.passkeys
    ]
    options = webauthn.generate_registration_options(
        rp_id=_rp_id(),
        rp_name=_rp_name(),
        user_id=str(current_user.id).encode(),
        user_name=current_user.email,
        user_display_name=current_user.get_full_name(),
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.PREFERRED,
            user_verification=UserVerificationRequirement.PREFERRED,
        ),
        exclude_credentials=existing,
    )
    session['webauthn_reg_challenge'] = base64.b64encode(options.challenge).decode()
    return webauthn.options_to_json(options), 200, {'Content-Type': 'application/json'}


@tf.route('/profile/security/passkeys/register/complete', methods=['POST'])
@login_required
def passkey_register_complete():
    data = request.get_json()
    challenge = base64.b64decode(session.pop('webauthn_reg_challenge', ''))
    try:
        credential = webauthn.parse_registration_credential_json(json.dumps(data['credential']))
        verification = webauthn.verify_registration_response(
            credential=credential,
            expected_challenge=challenge,
            expected_rp_id=_rp_id(),
            expected_origin=_origin(),
        )
    except Exception as e:
        return jsonify({'error': str(e)}), 400

    cred = UserCredential(
        user_id=current_user.id,
        credential_id=base64.b64encode(verification.credential_id).decode().rstrip('='),
        public_key=base64.b64encode(verification.credential_public_key).decode(),
        sign_count=verification.sign_count,
        device_name=data.get('device_name') or 'Passkey',
    )
    db.session.add(cred)
    db.session.commit()
    return jsonify({'ok': True})


@tf.route('/profile/security/passkeys/<int:cred_id>/delete', methods=['POST'])
@login_required
def passkey_delete(cred_id):
    cred = UserCredential.query.filter_by(id=cred_id, user_id=current_user.id).first_or_404()
    db.session.delete(cred)
    db.session.commit()
    flash('Passkey removed.', 'info')
    return redirect(url_for('tf.security'))


# ── TOTP setup ─────────────────────────────────────────────────────────────

@tf.route('/profile/security/totp/setup')
@login_required
def totp_setup():
    if current_user.totp_enabled:
        return redirect(url_for('tf.security'))
    secret = session.get('pending_totp_secret') or pyotp.random_base32()
    session['pending_totp_secret'] = secret
    uri = pyotp.totp.TOTP(secret).provisioning_uri(
        name=current_user.email, issuer_name='OurPeaPod'
    )
    return render_template('totp_setup.html', secret=secret, qr_b64=_qr_png_b64(uri))


@tf.route('/profile/security/totp/enable', methods=['POST'])
@login_required
def totp_enable():
    secret = session.get('pending_totp_secret')
    code = request.form.get('code', '').strip()
    if not secret or not pyotp.TOTP(secret).verify(code, valid_window=1):
        flash('Invalid code — please try again.', 'error')
        return redirect(url_for('tf.totp_setup'))
    current_user.totp_secret = secret
    current_user.totp_enabled = True
    db.session.commit()
    session.pop('pending_totp_secret', None)
    flash('Authenticator app enabled.', 'success')
    return redirect(url_for('tf.security'))


@tf.route('/profile/security/totp/disable', methods=['POST'])
@login_required
def totp_disable():
    code = request.form.get('code', '').strip()
    if not current_user.totp_enabled or not current_user.totp_secret:
        return redirect(url_for('tf.security'))
    if not pyotp.TOTP(current_user.totp_secret).verify(code, valid_window=1):
        flash('Invalid code — TOTP not disabled.', 'error')
        return redirect(url_for('tf.security'))
    current_user.totp_enabled = False
    current_user.totp_secret = None
    db.session.commit()
    flash('Authenticator app removed.', 'info')
    return redirect(url_for('tf.security'))


# ── Login 2FA challenge ────────────────────────────────────────────────────

@tf.route('/login/2fa', methods=['GET', 'POST'])
def login_2fa():
    user = _get_pending_user()
    if not user:
        return redirect(url_for('main.login'))

    # TOTP form submission
    if request.method == 'POST':
        code = request.form.get('code', '').strip()
        if user.totp_enabled and user.totp_secret and pyotp.TOTP(user.totp_secret).verify(code, valid_window=1):
            session.pop('pending_2fa_user_id', None)
            login_user(user, remember=session.pop('pending_2fa_remember', False))
            return redirect(url_for('main.home'))
        flash('Invalid code. Please try again.', 'error')

    return render_template('login_2fa.html',
                           has_passkeys=bool(user.passkeys),
                           has_totp=user.totp_enabled)


@tf.route('/login/2fa/passkey/begin', methods=['POST'])
def login_2fa_passkey_begin():
    user = _get_pending_user()
    if not user:
        return jsonify({'error': 'No pending login'}), 403

    allow = [
        PublicKeyCredentialDescriptor(id=base64.b64decode(c.credential_id + '=='))
        for c in user.passkeys
    ]
    options = webauthn.generate_authentication_options(
        rp_id=_rp_id(),
        allow_credentials=allow,
        user_verification=UserVerificationRequirement.PREFERRED,
    )
    session['webauthn_auth_challenge'] = base64.b64encode(options.challenge).decode()
    return webauthn.options_to_json(options), 200, {'Content-Type': 'application/json'}


@tf.route('/login/2fa/passkey/complete', methods=['POST'])
def login_2fa_passkey_complete():
    user = _get_pending_user()
    if not user:
        return jsonify({'error': 'No pending login'}), 403

    challenge = base64.b64decode(session.pop('webauthn_auth_challenge', ''))
    data = request.get_json()
    raw_id = base64.b64decode(data.get('rawId', '') + '==').rstrip(b'\x00')
    cred_id_b64 = base64.b64encode(raw_id).decode().rstrip('=')

    stored = UserCredential.query.filter_by(
        user_id=user.id, credential_id=cred_id_b64
    ).first()
    if not stored:
        return jsonify({'error': 'Unknown credential'}), 400

    try:
        credential = webauthn.parse_authentication_credential_json(json.dumps(data))
        verification = webauthn.verify_authentication_response(
            credential=credential,
            expected_challenge=challenge,
            expected_rp_id=_rp_id(),
            expected_origin=_origin(),
            credential_public_key=base64.b64decode(stored.public_key),
            credential_current_sign_count=stored.sign_count,
        )
    except Exception as e:
        return jsonify({'error': str(e)}), 400

    stored.sign_count = verification.new_sign_count
    db.session.commit()
    session.pop('pending_2fa_user_id', None)
    login_user(user, remember=session.pop('pending_2fa_remember', False))
    return jsonify({'success': True, 'redirect': url_for('main.home')})
