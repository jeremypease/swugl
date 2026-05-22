import certifi
import requests
import os
from flask import current_app

def send_email(to_email, subject, html_content):
    """Send an email via SendGrid Web API."""
    api_key = current_app.config['SENDGRID_API_KEY']
    from_email = current_app.config['SENDGRID_FROM_EMAIL']

    data = {
        "personalizations": [{"to": [{"email": to_email}]}],
        "from": {"email": from_email},
        "subject": subject,
        "content": [{"type": "text/html", "value": html_content}]
    }

    try:
        response = requests.post(
            "https://api.sendgrid.com/v3/mail/send",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            },
            json=data,
            verify=certifi.where()
        )
        print(f"Email status: {response.status_code}")
        return response.status_code == 202
    except Exception as e:
        print(f"Email error: {e}")
        return False

def send_verification_email(user, url):
    subject = "Verify your Pease Vine email address"
    html_content = f"""
    <h2>Welcome to Pease Vine, {user.first_name}!</h2>
    <p>Please verify your email address by clicking the link below:</p>
    <p><a href="{url}">Verify Email Address</a></p>
    <p>This link will expire in 24 hours.</p>
    <p>If you did not register for Pease Vine, please ignore this email.</p>
    """
    return send_email(user.email, subject, html_content)

def send_approval_notification(user, url):
    subject = "Your Pease Vine account has been approved!"
    html_content = f"""
    <h2>Welcome to Pease Vine, {user.first_name}!</h2>
    <p>Your account has been approved. You can now log in at:</p>
    <p><a href="{url}">Sign In to Pease Vine</a></p>
    """
    return send_email(user.email, subject, html_content)

def send_pending_notification(admin_email, new_user, url):
    subject = "New Pease Vine registration pending approval"
    html_content = f"""
    <h2>New Registration</h2>
    <p>{new_user.get_full_name()} ({new_user.email}) has registered and is awaiting approval.</p>
    <p><a href="{url}">Review pending users</a></p>
    """
    return send_email(admin_email, subject, html_content)

def send_spouse_confirmation_email(requesting_person, target_user, confirm_url, decline_url):
    subject = "Spouse connection request on Pease Vine"
    html_content = f"""
    <h2>Spouse Connection Request</h2>
    <p>{requesting_person.get_display_name()} has indicated that you are their spouse on Pease Vine.</p>
    <p>Please click the link below to confirm or decline this request:</p>
    <p><a href="{confirm_url}">Confirm Spouse Request</a></p>
    <p><a href="{decline_url}">Decline Spouse Request</a></p>
    """
    return send_email(target_user.email, subject, html_content)

def send_password_reset_email(user, reset_url):
    subject = "Reset your Peavines password"
    html_content = f"""
    <h2>Password Reset</h2>
    <p>Hi {user.first_name},</p>
    <p>We received a request to reset your Peavines password. Click the link below to set a new one:</p>
    <p><a href="{reset_url}">Reset My Password</a></p>
    <p>This link expires in 1 hour. If you didn't request a reset, you can ignore this email.</p>
    """
    return send_email(user.email, subject, html_content)

def send_member_invitation_email(inviting_name, person_first_name, family_name, to_email, url):
    subject = f"You've been invited to join {family_name} on Pease Vine"
    html_content = f"""
    <h2>You're invited to Pease Vine!</h2>
    <p>Hi {person_first_name},</p>
    <p>{inviting_name} has added you to the {family_name} family on Pease Vine
    and would like to invite you to join.</p>
    <p>Click the link below to create your account:</p>
    <p><a href="{url}">Accept Invitation &amp; Register</a></p>
    <p>This link will expire in 7 days.</p>
    """
    return send_email(to_email, subject, html_content)

def send_welcome_email(user, family, dashboard_url):
    subject = f"Welcome to OurPeaPod — your {family.name} pod is ready"
    html_content = f"""
    <h2>You're all set, {user.first_name}!</h2>
    <p>Your email is verified and your <strong>{family.name}</strong> pod is ready to go.</p>
    <p>Here's what to do first:</p>
    <ol>
        <li><strong>Add your first family member</strong> — invite someone or add them yourself from the Members page.</li>
        <li><strong>Build your family tree</strong> — connect parents, children, and spouses.</li>
        <li><strong>Upload a photo</strong> — start your family album.</li>
    </ol>
    <p><a href="{dashboard_url}" style="background:#3D7040;color:#fff;padding:10px 20px;text-decoration:none;border-radius:4px;display:inline-block;">Go to your pod</a></p>
    <p style="color:#666;font-size:13px;">Your pod ID is <strong>{family.account_id}</strong> — keep this handy if you ever need to contact support.</p>
    <p style="color:#666;font-size:13px;">— The OurPeaPod team</p>
    """
    return send_email(user.email, subject, html_content)

def send_support_email(user, family, category, message, support_email):
    category_labels = {
        'billing':   'Billing or subscription',
        'account':   'Account or access issue',
        'technical': 'Technical problem',
        'feature':   'Feature request',
        'other':     'Something else',
    }
    category_label = category_labels.get(category, category)
    pod_id = family.account_id if family and family.account_id else 'n/a'
    subject = f"[Support] {category_label} — {family.name if family else 'Unknown'} ({pod_id})"
    html_content = f"""
    <h2>Support Request</h2>
    <table style="border-collapse:collapse;font-size:14px;">
        <tr><td style="padding:4px 12px 4px 0;color:#666;">From</td><td><strong>{user.get_full_name()}</strong> &lt;{user.email}&gt;</td></tr>
        <tr><td style="padding:4px 12px 4px 0;color:#666;">Pod</td><td>{family.name if family else '—'} (<code>{pod_id}</code>)</td></tr>
        <tr><td style="padding:4px 12px 4px 0;color:#666;">Category</td><td>{category_label}</td></tr>
    </table>
    <hr style="margin:16px 0;">
    <p style="white-space:pre-wrap;">{message}</p>
    """
    api_key = current_app.config['SENDGRID_API_KEY']
    from_email = current_app.config['SENDGRID_FROM_EMAIL']
    data = {
        "personalizations": [{"to": [{"email": support_email}]}],
        "from": {"email": from_email},
        "reply_to": {"email": user.email, "name": user.get_full_name()},
        "subject": subject,
        "content": [{"type": "text/html", "value": html_content}]
    }
    try:
        import certifi as _certifi
        response = requests.post(
            "https://api.sendgrid.com/v3/mail/send",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=data,
            verify=_certifi.where()
        )
        return response.status_code == 202
    except Exception as e:
        print(f"Support email error: {e}")
        return False

def send_spouse_invitation_email(inviting_person, to_email, url):
    subject = "You've been invited to join Pease Vine"
    html_content = f"""
    <h2>You're Invited to Pease Vine!</h2>
    <p>{inviting_person.get_display_name()} has invited you to join Pease Vine,
    a private family connection site.</p>
    <p>Click the link below to create your account:</p>
    <p><a href="{url}">Accept Invitation &amp; Register</a></p>
    <p>This link will expire in 7 days.</p>
    """
    return send_email(to_email, subject, html_content)
