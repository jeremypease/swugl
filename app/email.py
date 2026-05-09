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
