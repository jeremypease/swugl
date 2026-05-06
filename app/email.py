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

def send_verification_email(user, token):
    subject = "Verify your Pease Vine email address"
    html_content = f"""
    <h2>Welcome to Pease Vine, {user.first_name}!</h2>
    <p>Please verify your email address by clicking the link below:</p>
    <p><a href="http://127.0.0.1:5000/verify/{token}">Verify Email Address</a></p>
    <p>This link will expire in 24 hours.</p>
    <p>If you did not register for Pease Vine, please ignore this email.</p>
    """
    return send_email(user.email, subject, html_content)

def send_approval_notification(user):
    subject = "Your Pease Vine account has been approved!"
    html_content = f"""
    <h2>Welcome to Pease Vine, {user.first_name}!</h2>
    <p>Your account has been approved. You can now log in at:</p>
    <p><a href="http://127.0.0.1:5000/login">Sign In to Pease Vine</a></p>
    """
    return send_email(user.email, subject, html_content)

def send_pending_notification(admin_email, new_user):
    subject = "New Pease Vine registration pending approval"
    html_content = f"""
    <h2>New Registration</h2>
    <p>{new_user.get_full_name()} ({new_user.email}) has registered and is awaiting approval.</p>
    <p><a href="http://127.0.0.1:5000/admin/users">Review pending users</a></p>
    """
    return send_email(admin_email, subject, html_content)
