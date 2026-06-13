from flask import Flask, render_template, request, redirect, url_for, g
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, current_user
from flask_migrate import Migrate
from flask_wtf.csrf import CSRFProtect
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_jwt_extended import JWTManager
from dotenv import load_dotenv
from datetime import datetime, timedelta
import sentry_sdk
from sentry_sdk.integrations.flask import FlaskIntegration
import secrets
import os

load_dotenv()
import certifi
os.environ['SSL_CERT_FILE'] = certifi.where()

sentry_dsn = os.environ.get('SENTRY_DSN')
if sentry_dsn:
    sentry_sdk.init(
        dsn=sentry_dsn,
        integrations=[FlaskIntegration()],
        traces_sample_rate=0.1,
        send_default_pii=False,
    )

db = SQLAlchemy()
login_manager = LoginManager()
migrate = Migrate()
csrf = CSRFProtect()
jwt = JWTManager()
_redis_url = os.environ.get('REDIS_URL')
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[],
    storage_uri=_redis_url or 'memory://',
)

def create_app(test_config=None):
    app = Flask(__name__)

    from werkzeug.middleware.proxy_fix import ProxyFix
    app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

    secret = os.environ.get('SECRET_KEY')
    if not secret:
        raise RuntimeError("SECRET_KEY environment variable is not set")
    app.config['SECRET_KEY'] = secret

    # Database — read from env so Railway/Postgres works; fall back to local SQLite
    database_url = os.environ.get('DATABASE_URL') or 'sqlite:///family.db'
    # SQLAlchemy 1.4+ requires postgresql:// not postgres://
    if database_url.startswith('postgres://'):
        database_url = database_url.replace('postgres://', 'postgresql://', 1)
    app.config['SQLALCHEMY_DATABASE_URI'] = database_url
    app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
        'pool_pre_ping': True,
        'pool_recycle': 300,
    }

    # Apply test overrides before any extension touches the config
    if test_config:
        app.config.update(test_config)

    app.config['REGISTRATION_OPEN'] = os.environ.get('REGISTRATION_OPEN', '').lower() == 'true'

    app.config['RESEND_API_KEY'] = os.environ.get('RESEND_API_KEY')
    app.config['RESEND_FROM_EMAIL'] = os.environ.get('RESEND_FROM_EMAIL', 'Swugl <noreply@swugl.com>')
    app.config['MAIL_ENABLED'] = os.environ.get('MAIL_ENABLED', '').lower() == 'true'

    # Load both key sets so the in-app platform toggle can switch modes at runtime
    for _sfx in ('_TEST', '_LIVE'):
        for _k in ('STRIPE_SECRET_KEY', 'STRIPE_PUBLISHABLE_KEY', 'STRIPE_WEBHOOK_SECRET',
                   'STRIPE_MONTHLY_PRICE_ID', 'STRIPE_ANNUAL_PRICE_ID'):
            app.config[f'{_k}{_sfx}'] = os.environ.get(f'{_k}{_sfx}') or os.environ.get(_k)
    # Default mode from env var; runtime DB override handled by get_stripe_mode() in billing.py
    _stripe_mode = os.environ.get('STRIPE_MODE', 'live').lower()
    app.config['STRIPE_MODE_DEFAULT'] = _stripe_mode

    app.config['SUPPORT_EMAIL'] = os.environ.get('SUPPORT_EMAIL', 'jeremypease@me.com')

    app.config['R2_ACCOUNT_ID'] = os.environ.get('R2_ACCOUNT_ID')
    app.config['R2_ACCESS_KEY_ID'] = os.environ.get('R2_ACCESS_KEY_ID')
    app.config['R2_SECRET_ACCESS_KEY'] = os.environ.get('R2_SECRET_ACCESS_KEY')
    app.config['R2_BUCKET_NAME'] = os.environ.get('R2_BUCKET_NAME')
    app.config['R2_PUBLIC_URL'] = os.environ.get('R2_PUBLIC_URL', '')
    # Photos are served via short-lived presigned URLs (see storage.photo_url).
    _r2_ttl = os.environ.get('R2_SIGNED_URL_TTL')
    if _r2_ttl:
        app.config['R2_SIGNED_URL_TTL'] = int(_r2_ttl)

    app.config['WEBAUTHN_RP_ID'] = os.environ.get('WEBAUTHN_RP_ID', 'localhost')
    app.config['WEBAUTHN_RP_NAME'] = os.environ.get('WEBAUTHN_RP_NAME', 'Swugl')
    app.config['WEBAUTHN_ORIGIN'] = os.environ.get('WEBAUTHN_ORIGIN', 'http://localhost:5000')

    app.config['JWT_SECRET_KEY'] = os.environ.get('JWT_SECRET_KEY') or secret
    app.config['JWT_ACCESS_TOKEN_EXPIRES'] = timedelta(minutes=15)
    app.config['JWT_REFRESH_TOKEN_EXPIRES'] = timedelta(days=30)

    app.config['GOOGLE_CLIENT_ID'] = os.environ.get('GOOGLE_CLIENT_ID')
    app.config['GOOGLE_CLIENT_SECRET'] = os.environ.get('GOOGLE_CLIENT_SECRET')

    app.config['APPLE_CLIENT_ID'] = os.environ.get('APPLE_CLIENT_ID')
    app.config['APPLE_TEAM_ID'] = os.environ.get('APPLE_TEAM_ID')
    app.config['APPLE_KEY_ID'] = os.environ.get('APPLE_KEY_ID')
    app.config['APPLE_PRIVATE_KEY'] = os.environ.get('APPLE_PRIVATE_KEY', '')
    app.config['APPLE_BUNDLE_ID'] = os.environ.get('APPLE_BUNDLE_ID')
    app.config['APPLE_DOMAIN_ASSOCIATION'] = os.environ.get('APPLE_DOMAIN_ASSOCIATION', '')
    # Canonical redirect URI — pins the callback to one URL regardless of whether
    # the user accessed the site via www or non-www, preventing Apple rejections.
    app.config['APPLE_REDIRECT_URI'] = os.environ.get('APPLE_REDIRECT_URI', '')

    app.config['WEATHERKIT_KEY_ID'] = os.environ.get('WEATHERKIT_KEY_ID', '')
    app.config['WEATHERKIT_SERVICE_ID'] = os.environ.get('WEATHERKIT_SERVICE_ID', '')
    app.config['WEATHERKIT_PRIVATE_KEY'] = os.environ.get('WEATHERKIT_PRIVATE_KEY', '')
    app.config['ANTHROPIC_API_KEY'] = os.environ.get('ANTHROPIC_API_KEY', '')
    app.config['APP_VERSION'] = os.environ.get('APP_VERSION', '1.0.0')

    # Request body cap — bulk album uploads send many photos in one multipart
    # POST, so this is well above the 25 MB per-file cap enforced in storage.py.
    app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024

    # Session lifetime
    app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=14)
    app.config['REMEMBER_COOKIE_DURATION'] = timedelta(days=30)

    # Secure cookies in production (HTTPS only)
    # SameSite=None required for Apple Sign-In form_post callback to send session cookie
    if os.environ.get('FLASK_ENV') == 'production':
        app.config['SESSION_COOKIE_SECURE'] = True
        app.config['SESSION_COOKIE_HTTPONLY'] = True
        app.config['SESSION_COOKIE_SAMESITE'] = 'None'
        # Force https:// in all url_for(_external=True) calls. Without this, ProxyFix
        # only helps if Cloudflare reliably sends X-Forwarded-Proto — and when it
        # doesn't, Apple's form_post returns over http:// and the Secure session
        # cookie is never sent, breaking state verification.
        app.config['PREFERRED_URL_SCHEME'] = 'https'

    # Re-apply test overrides: the env-based assignments above clobber any
    # overlapping keys passed in test_config (e.g. R2_ACCOUNT_ID=None so the
    # test suite never touches the real bucket).
    if test_config:
        app.config.update(test_config)

    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    login_manager.login_view = 'main.login'
    login_manager.login_message_category = 'error'
    app.config['USE_SESSION_FOR_NEXT'] = True
    csrf.init_app(app)
    limiter.init_app(app)
    jwt.init_app(app)

    from .models import User, Person, SystemAnnouncement, Notification, ApiTokenBlocklist, SystemConfig, ChatMessage
    from .routes import main
    from .billing import billing
    from .two_factor import tf
    from .platform_routes import platform
    from .oauth import oauth_bp, init_oauth
    from .api import api as api_bp
    from .storage import photo_url
    from .commands import email_sequence, digest, rsvp_reminders, annual_events, merge_persons, prune_chat, story_prompts_cmd
    app.register_blueprint(main)
    app.register_blueprint(billing)
    app.register_blueprint(tf)
    app.register_blueprint(platform)
    app.register_blueprint(oauth_bp)
    app.register_blueprint(api_bp)
    init_oauth(app)
    csrf.exempt(app.view_functions['billing.webhook'])
    # Apple Sign-In POSTs the id_token directly from Apple's servers to this
    # callback using form_post response_mode — Apple never attaches a CSRF token.
    # The callback is safe because it validates the cryptographic JWT signature on
    # the id_token before doing anything, so forged POST bodies are rejected.
    csrf.exempt(app.view_functions['oauth.apple_callback'])
    csrf.exempt(api_bp)

    @jwt.token_in_blocklist_loader
    def check_blocklist(jwt_header, jwt_payload):
        return ApiTokenBlocklist.query.filter_by(jti=jwt_payload['jti']).first() is not None
    app.cli.add_command(email_sequence)
    app.cli.add_command(digest)
    app.cli.add_command(rsvp_reminders)
    app.cli.add_command(annual_events)
    app.cli.add_command(merge_persons)
    app.cli.add_command(prune_chat)
    app.cli.add_command(story_prompts_cmd, name='story-prompts')

    app.jinja_env.globals['photo_url'] = photo_url

    @app.context_processor
    def inject_globals():
        from flask import session as s
        ann = SystemAnnouncement.get_current()
        dismissed = s.get('dismissed_announcements', [])
        active_ann = ann if ann and ann.id not in dismissed else None
        unread = 0
        recent_notifications = []
        if current_user.is_authenticated:
            unread = (
                Notification.query
                .filter_by(user_id=current_user.id, read_at=None)
                .count()
            )
            recent_notifications = (
                Notification.query
                .filter_by(user_id=current_user.id)
                .order_by(Notification.created_at.desc())
                .limit(8)
                .all()
            )
        from .billing import get_stripe_mode, family_has_paid_access
        from .models import AppVersion
        current_ver = AppVersion.query.filter_by(is_current=True).first()
        chat_visible = False
        chat_paid = False
        unread_chat = 0
        paid_access = False
        if current_user.is_authenticated:
            fam = current_user.active_family
            if fam:
                paid_access = family_has_paid_access(fam)
            if fam and fam.enable_chat:
                chat_visible = True
                if paid_access:
                    chat_paid = True
                    last_seen = current_user.chat_last_seen_at
                    q = ChatMessage.query.filter_by(family_id=current_user.active_family_id)
                    unread_chat = q.filter(ChatMessage.created_at > last_seen).count() if last_seen else q.count()
        return {
            'has_paid_access': paid_access,
            'now': datetime.utcnow(),
            'system_announcement': active_ann,
            'support_mode': s.get('support_mode', False),
            'stripe_test_mode': get_stripe_mode() == 'test',
            'unread_notification_count': unread,
            'recent_notifications': recent_notifications,
            'app_version': current_ver.version if current_ver else app.config.get('APP_VERSION', '1.0.0'),
            'chat_visible': chat_visible,
            'chat_paid': chat_paid,
            'unread_chat_count': unread_chat,
        }

    @app.template_filter('datetime_format')
    def datetime_format(ts):
        return datetime.utcfromtimestamp(int(ts)).strftime('%b %d, %Y')

    @app.template_filter('timeago')
    def timeago_filter(dt):
        now = datetime.utcnow()
        s = (now - dt).total_seconds()
        if s < 60:
            return 'just now'
        if s < 3600:
            return f'{int(s // 60)}m ago'
        if s < 86400:
            return f'{int(s // 3600)}h ago'
        if s < 172800:
            return 'yesterday'
        if s < 604800:
            return f'{int(s // 86400)} days ago'
        return dt.strftime('%b %-d')

    # Block unauthenticated access to uploaded family files
    @app.before_request
    def protect_uploads():
        if request.path.startswith('/static/uploads/'):
            if not current_user.is_authenticated:
                return redirect(url_for('main.login', next=request.path))

    # Block writes when a platform admin is browsing in support mode
    @app.before_request
    def block_support_mode_writes():
        from flask_login import current_user as cu
        from flask import session as s
        if (cu.is_authenticated and s.get('support_mode')
                and request.method in ('POST', 'PUT', 'DELETE', 'PATCH')
                and not request.path.startswith('/platform/exit-support')
                and not request.path.startswith('/platform/dismiss-announcement')):
            from flask import abort
            abort(403)

    # Generate a fresh CSP nonce for every request so inline scripts can be
    # whitelisted without 'unsafe-inline'.
    @app.before_request
    def set_csp_nonce():
        g.csp_nonce = secrets.token_urlsafe(16)

    # Security headers on every response
    @app.after_request
    def set_security_headers(response):
        response.headers['X-Frame-Options'] = 'SAMEORIGIN'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
        response.headers['Permissions-Policy'] = 'geolocation=(), microphone=(), camera=()'
        if os.environ.get('FLASK_ENV') == 'production':
            response.headers['Strict-Transport-Security'] = 'max-age=63072000; includeSubDomains'
        nonce = getattr(g, 'csp_nonce', '')
        # Photos load from presigned R2 URLs on the S3 endpoint; allow that
        # origin (plus the legacy public URL if one is still configured).
        img_origins = ["'self'", 'data:']
        acct = app.config.get('R2_ACCOUNT_ID')
        if acct:
            img_origins.append(f"https://{acct}.r2.cloudflarestorage.com")
        r2_url = app.config.get('R2_PUBLIC_URL', '').rstrip('/')
        if r2_url:
            img_origins.append(r2_url)
        img_src = ' '.join(img_origins)
        response.headers['Content-Security-Policy'] = (
            "default-src 'self'; "
            f"script-src 'self' https://unpkg.com 'nonce-{nonce}'; "
            "style-src 'self' https://fonts.googleapis.com https://unpkg.com 'unsafe-inline'; "
            "font-src 'self' https://fonts.gstatic.com; "
            f"img-src {img_src}; "
            "connect-src 'self' https://photon.komoot.io https://nominatim.openstreetmap.org;"
        )
        return response

    @app.errorhandler(404)
    def not_found(e):
        return render_template('errors/404.html'), 404

    @app.errorhandler(413)
    def request_too_large(e):
        return render_template('errors/413.html'), 413

    @app.errorhandler(500)
    def server_error(e):
        return render_template('errors/500.html'), 500

    return app
