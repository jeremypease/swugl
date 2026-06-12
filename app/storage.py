import io
import os
import uuid
import boto3
from botocore.config import Config
from flask import current_app, url_for
from PIL import Image, ImageOps

try:
    from pillow_heif import register_heif_opener
    register_heif_opener()
    HEIF_SUPPORTED = True
except ImportError:
    HEIF_SUPPORTED = False

ALLOWED_EXTS = {'jpg', 'jpeg', 'png', 'gif', 'webp', 'heic'}
ALLOWED_DOC_EXTS = {'pdf', 'jpg', 'jpeg', 'png', 'gif', 'webp', 'heic', 'txt', 'doc', 'docx'}

MAX_PHOTO_BYTES = 25 * 1024 * 1024   # per-file cap, checked before processing
DISPLAY_MAX_PX = 2000                # longest side of the stored display image
THUMB_MAX_PX = 400                   # longest side of grid thumbnails
JPEG_QUALITY = 85

# Formats run through the Pillow pipeline (orient, strip EXIF, resize).
# GIFs are stored as-is to preserve animation.
_PROCESSABLE_EXTS = {'jpg', 'jpeg', 'png', 'webp', 'heic'}

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
        'pdf': 'application/pdf',
        'txt': 'text/plain',
        'doc': 'application/msword',
        'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    }.get(ext, 'application/octet-stream')

def _file_size(file):
    file.seek(0, 2)
    size = file.tell()
    file.seek(0)
    return size


def _process_image(file, ext):
    """Run an uploaded image through the privacy/size pipeline.

    Auto-orients, strips all EXIF (phone photos carry GPS of people's homes
    and these files are served from a public CDN), and caps the longest side
    at DISPLAY_MAX_PX. HEIC is converted to JPEG since browsers can't render
    it. Returns (display_bytes, thumb_bytes, out_ext) or None if the image
    can't be decoded.
    """
    try:
        img = Image.open(file)
        img = ImageOps.exif_transpose(img)
        img.load()
    except Exception:
        return None
    out_ext = 'png' if ext == 'png' else 'jpg'
    if out_ext == 'jpg' and img.mode not in ('RGB', 'L'):
        img = img.convert('RGB')

    display = img.copy()
    display.thumbnail((DISPLAY_MAX_PX, DISPLAY_MAX_PX))
    display_buf = io.BytesIO()
    if out_ext == 'png':
        display.save(display_buf, format='PNG')
    else:
        display.save(display_buf, format='JPEG', quality=JPEG_QUALITY)
    display_buf.seek(0)

    thumb = img.copy()
    if thumb.mode not in ('RGB', 'L'):
        thumb = thumb.convert('RGB')
    thumb.thumbnail((THUMB_MAX_PX, THUMB_MAX_PX))
    thumb_buf = io.BytesIO()
    thumb.save(thumb_buf, format='JPEG', quality=JPEG_QUALITY)
    thumb_buf.seek(0)

    return display_buf.getvalue(), thumb_buf.getvalue(), out_ext


def _store(data, key, ext):
    if _r2_enabled():
        _client().put_object(
            Bucket=current_app.config['R2_BUCKET_NAME'],
            Key=key,
            Body=data,
            ContentType=_content_type(ext),
        )
    else:
        abs_path = os.path.join(current_app.root_path, 'static', 'uploads', key)
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        with open(abs_path, 'wb') as f:
            f.write(data)


def upload_photo(file, folder='photos', with_thumb=False):
    """Upload a photo file; returns a storage key (str) or None on bad
    extension, oversize, or undecodable image.

    With with_thumb=True returns (key, thumb_key) instead; thumb_key is None
    for formats that skip processing (GIF).
    """
    ext = file.filename.rsplit('.', 1)[-1].lower() if '.' in file.filename else 'jpg'
    if ext not in ALLOWED_EXTS:
        return None
    if _file_size(file) > MAX_PHOTO_BYTES:
        return None

    if ext in _PROCESSABLE_EXTS and (ext != 'heic' or HEIF_SUPPORTED):
        processed = _process_image(file, ext)
        if processed is None:
            return None
        display_data, thumb_data, out_ext = processed
        base = uuid.uuid4().hex
        rel = f"{folder}/{base}.{out_ext}"
        rel_thumb = f"{folder}/{base}_thumb.jpg"
        _store(display_data, rel, out_ext)
        _store(thumb_data, rel_thumb, 'jpg')
        prefix = '' if _r2_enabled() else 'uploads/'
        key, thumb_key = f"{prefix}{rel}", f"{prefix}{rel_thumb}"
        return (key, thumb_key) if with_thumb else key

    # GIF (or HEIC without decoder support): store unprocessed
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
    return (key, None) if with_thumb else key

def upload_document(file, folder='documents'):
    """Upload a document file; returns (storage_key, file_type, file_size) or None on bad extension."""
    ext = file.filename.rsplit('.', 1)[-1].lower() if '.' in file.filename else ''
    if ext not in ALLOWED_DOC_EXTS:
        return None
    filename = f"{uuid.uuid4().hex}.{ext}"
    file_size = _file_size(file)
    if file_size > MAX_PHOTO_BYTES:
        return None
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
    return key, ext, file_size


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
