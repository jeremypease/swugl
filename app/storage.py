import os
import uuid
import boto3
from botocore.config import Config
from flask import current_app, url_for

ALLOWED_EXTS = {'jpg', 'jpeg', 'png', 'gif', 'webp', 'heic'}

def _r2_enabled():
    return bool(current_app.config.get('R2_ACCOUNT_ID'))

def _client():
    return boto3.client(
        's3',
        endpoint_url=f"https://{current_app.config['R2_ACCOUNT_ID']}.r2.cloudflarestorage.com",
        aws_access_key_id=current_app.config['R2_ACCESS_KEY_ID'],
        aws_secret_access_key=current_app.config['R2_SECRET_ACCESS_KEY'],
        config=Config(signature_version='s3v4'),
        region_name='auto',
    )

def _content_type(ext):
    return {
        'jpg': 'image/jpeg', 'jpeg': 'image/jpeg',
        'png': 'image/png', 'gif': 'image/gif',
        'webp': 'image/webp', 'heic': 'image/heic',
    }.get(ext, 'application/octet-stream')

def upload_photo(file, folder='photos'):
    """Upload a photo file; returns a storage key (str) or None on bad extension."""
    ext = file.filename.rsplit('.', 1)[-1].lower() if '.' in file.filename else 'jpg'
    if ext not in ALLOWED_EXTS:
        return None
    filename = f"{uuid.uuid4().hex}.{ext}"
    if _r2_enabled():
        key = f"{folder}/{filename}"
        _client().upload_fileobj(
            file,
            current_app.config['R2_BUCKET_NAME'],
            key,
            ExtraArgs={'ContentType': _content_type(ext)},
        )
    else:
        local_dir = os.path.join(current_app.root_path, 'static', 'uploads', folder)
        os.makedirs(local_dir, exist_ok=True)
        file.save(os.path.join(local_dir, filename))
        key = f"uploads/{folder}/{filename}"
    return key

def delete_object(key):
    """Delete a stored object by key. Silently ignores errors."""
    if not key:
        return
    if key.startswith('uploads/'):
        abs_path = os.path.join(current_app.root_path, 'static', key)
        if os.path.exists(abs_path):
            os.remove(abs_path)
    elif _r2_enabled():
        try:
            _client().delete_object(Bucket=current_app.config['R2_BUCKET_NAME'], Key=key)
        except Exception:
            pass

def get_object_bytes(key):
    """Return (bytes, content_type) for a stored object."""
    if key.startswith('uploads/'):
        abs_path = os.path.join(current_app.root_path, 'static', key)
        with open(abs_path, 'rb') as f:
            return f.read(), _content_type(key.rsplit('.', 1)[-1].lower())
    resp = _client().get_object(Bucket=current_app.config['R2_BUCKET_NAME'], Key=key)
    return resp['Body'].read(), resp.get('ContentType', 'application/octet-stream')

def photo_url(key):
    """Return a displayable URL for a stored photo key."""
    if not key:
        return None
    if key.startswith('uploads/'):
        return url_for('static', filename=key)
    public_url = current_app.config.get('R2_PUBLIC_URL', '').rstrip('/')
    if public_url:
        return f"{public_url}/{key}"
    return url_for('main.serve_photo', key=key)
