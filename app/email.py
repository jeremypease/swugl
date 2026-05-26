import resend
from flask import current_app


def _client():
    resend.api_key = current_app.config['RESEND_API_KEY']
    return resend.Emails


def _from():
    return current_app.config.get('RESEND_FROM_EMAIL', 'Peavines <noreply@ourpeapod.com>')


def send_email(to_email, subject, html_content, reply_to=None):
    params = {
        "from": _from(),
        "to": [to_email],
        "subject": subject,
        "html": html_content,
    }
    if reply_to:
        params["reply_to"] = reply_to
    try:
        _client().send(params)
        return True
    except Exception as e:
        print(f"Email error: {e}")
        return False


def send_verification_email(user, url):
    return send_email(
        user.email,
        "Verify your Peavines email address",
        f"""
        <h2>Welcome to Peavines, {user.first_name}!</h2>
        <p>Please verify your email address by clicking the link below:</p>
        <p><a href="{url}">Verify Email Address</a></p>
        <p>This link will expire in 24 hours.</p>
        <p>If you did not register for Peavines, please ignore this email.</p>
        """
    )


def send_approval_notification(user, url):
    return send_email(
        user.email,
        "Your Peavines account has been approved!",
        f"""
        <h2>Welcome to Peavines, {user.first_name}!</h2>
        <p>Your account has been approved. You can now log in at:</p>
        <p><a href="{url}">Sign In to Peavines</a></p>
        """
    )


def send_pending_notification(admin_email, new_user, url):
    return send_email(
        admin_email,
        "New Peavines registration pending approval",
        f"""
        <h2>New Registration</h2>
        <p>{new_user.get_full_name()} ({new_user.email}) has registered and is awaiting approval.</p>
        <p><a href="{url}">Review pending users</a></p>
        """
    )


def send_spouse_confirmation_email(requesting_person, target_user, confirm_url, decline_url):
    return send_email(
        target_user.email,
        "Spouse connection request on Peavines",
        f"""
        <h2>Spouse Connection Request</h2>
        <p>{requesting_person.get_display_name()} has indicated that you are their spouse on Peavines.</p>
        <p><a href="{confirm_url}">Confirm Spouse Request</a></p>
        <p><a href="{decline_url}">Decline Spouse Request</a></p>
        """
    )


def send_password_reset_email(user, reset_url):
    return send_email(
        user.email,
        "Reset your Peavines password",
        f"""
        <h2>Password Reset</h2>
        <p>Hi {user.first_name},</p>
        <p>We received a request to reset your Peavines password. Click the link below to set a new one:</p>
        <p><a href="{reset_url}">Reset My Password</a></p>
        <p>This link expires in 1 hour. If you didn't request a reset, you can ignore this email.</p>
        """
    )


def send_member_invitation_email(inviting_name, person_first_name, family_name, to_email, url):
    return send_email(
        to_email,
        f"You've been invited to join {family_name} on Peavines",
        f"""
        <h2>You're invited to Peavines!</h2>
        <p>Hi {person_first_name},</p>
        <p>{inviting_name} has added you to the {family_name} family on Peavines
        and would like to invite you to join.</p>
        <p><a href="{url}">Accept Invitation &amp; Register</a></p>
        <p>This link will expire in 7 days.</p>
        """
    )


def send_spouse_invitation_email(inviting_person, to_email, url):
    return send_email(
        to_email,
        "You've been invited to join Peavines",
        f"""
        <h2>You're Invited to Peavines!</h2>
        <p>{inviting_person.get_display_name()} has invited you to join Peavines,
        a private family connection site.</p>
        <p><a href="{url}">Accept Invitation &amp; Register</a></p>
        <p>This link will expire in 7 days.</p>
        """
    )


def send_welcome_email(user, family, dashboard_url):
    return send_email(
        user.email,
        f"Welcome to OurPeaPod — your {family.name} pod is ready",
        f"""
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
    )


def send_nudge_day3_email(admin, family, members_url):
    return send_email(
        admin.email,
        f"Your {family.name} pod is quiet — invite your family",
        f"""
        <h2>Hi {admin.first_name},</h2>
        <p>You created your <strong>{family.name}</strong> pod 3 days ago — great start!</p>
        <p>It looks like it's still just you in there. OurPeaPod is a lot more fun when your family is with you.</p>
        <p>Here's how to bring them in:</p>
        <ul>
            <li><strong>Invite by email</strong> — send a personal invite link from the Members page</li>
            <li><strong>Add them yourself</strong> — add a family member directly, then invite them to claim their profile</li>
        </ul>
        <p><a href="{members_url}" style="background:#3D7040;color:#fff;padding:10px 20px;text-decoration:none;border-radius:4px;display:inline-block;">Invite family members</a></p>
        <p style="color:#666;font-size:13px;">— The OurPeaPod team</p>
        """
    )


def send_nudge_day7_email(admin, family, dashboard_url):
    return send_email(
        admin.email,
        f"3 things OurPeaPod can do for your {family.name}",
        f"""
        <h2>Hi {admin.first_name},</h2>
        <p>Here are a few things worth exploring in your pod this week:</p>
        <ul>
            <li><strong>Plan your next event</strong> — RSVPs, meal sign-ups, task assignments, and sleeping arrangements, all in one place.</li>
            <li><strong>Build your family tree</strong> — connect parents, children, and spouses across generations.</li>
            <li><strong>Start a photo album</strong> — upload family photos so everyone can see them, no group text required.</li>
        </ul>
        <p><a href="{dashboard_url}" style="background:#3D7040;color:#fff;padding:10px 20px;text-decoration:none;border-radius:4px;display:inline-block;">Go to your pod</a></p>
        <p style="color:#666;font-size:13px;">— The OurPeaPod team</p>
        """
    )


def send_trial_warning_email(admin, family, days_left, billing_url):
    return send_email(
        admin.email,
        f"Your OurPeaPod trial ends in {days_left} day{'s' if days_left != 1 else ''}",
        f"""
        <h2>Hi {admin.first_name},</h2>
        <p>Your 30-day free trial for <strong>{family.name}</strong> ends in <strong>{days_left} day{'s' if days_left != 1 else ''}</strong>.</p>
        <p>After your trial, your pod moves to the free tier unless you upgrade. Here's what changes:</p>
        <ul>
            <li>Members capped at 15 (you keep everyone already in your pod)</li>
            <li>Photo storage limited to 1 GB</li>
            <li>Family chat, calendar feed, and mobile app become unavailable</li>
        </ul>
        <p>Upgrade to the Family Plan for $9/month and keep everything.</p>
        <p><a href="{billing_url}" style="background:#3D7040;color:#fff;padding:10px 20px;text-decoration:none;border-radius:4px;display:inline-block;">Upgrade now — $9/mo</a></p>
        <p style="color:#666;font-size:13px;">Questions? Reply to this email — we're happy to help.</p>
        <p style="color:#666;font-size:13px;">— The OurPeaPod team</p>
        """
    )


def send_trial_ended_email(admin, family, billing_url):
    return send_email(
        admin.email,
        f"Your OurPeaPod trial has ended",
        f"""
        <h2>Hi {admin.first_name},</h2>
        <p>Your free trial for <strong>{family.name}</strong> has ended.</p>
        <p>Your family's data is safe — photos, members, the tree, everything is still there. But some features are now limited on the free tier.</p>
        <p>Upgrade to the Family Plan to restore full access:</p>
        <ul>
            <li>Unlimited members</li>
            <li>25 GB photo storage</li>
            <li>Family chat, calendar feed, and mobile app</li>
        </ul>
        <p><a href="{billing_url}" style="background:#3D7040;color:#fff;padding:10px 20px;text-decoration:none;border-radius:4px;display:inline-block;">Choose your plan</a></p>
        <p style="color:#666;font-size:13px;">— The OurPeaPod team</p>
        """
    )


def send_new_event_notification(user, event, url):
    return send_email(
        user.email,
        f"New event: {event.name}",
        f"""
        <h2>New event in {event.family.name}</h2>
        <p><strong>{event.name}</strong> has been added to the calendar.</p>
        <p>
            <strong>Date:</strong> {event.date_range_display()}<br>
            {"<strong>Location:</strong> " + event.location + "<br>" if event.location else ""}
            {("<p>" + event.description + "</p>") if event.description else ""}
        </p>
        <p><a href="{url}">View event →</a></p>
        <p style="font-size:12px;color:#888;">
            You're receiving this because you have new event notifications enabled.
            <a href="{url.split('/events')[0]}/profile/notifications">Manage preferences</a>
        </p>
        """
    )


def send_rsvp_reminder_email(user, event, url):
    deadline = event.rsvp_deadline.strftime('%B %d') if event.rsvp_deadline else ''
    prefs_url = url.split('/events')[0] + '/profile/notifications'
    return send_email(
        user.email,
        f"RSVP reminder: {event.name}",
        f"""
        <h2>Have you RSVPed for {event.name}?</h2>
        <p>The RSVP deadline is <strong>{deadline}</strong> — just 3 days away.</p>
        <p>
            <strong>Date:</strong> {event.date_range_display()}<br>
            {"<strong>Location:</strong> " + event.location + "<br>" if event.location else ""}
        </p>
        <p><a href="{url}">RSVP now →</a></p>
        <p style="font-size:12px;color:#888;">
            You're receiving this because you have RSVP reminders enabled.
            <a href="{prefs_url}">Manage preferences</a>
        </p>
        """
    )


def send_annual_event_cloned_email(admin, new_event, url):
    return send_email(
        admin.email,
        f'Annual event auto-scheduled: {new_event.name}',
        f"""
        <h2>{new_event.name} has been auto-scheduled</h2>
        <p>This annual event was automatically carried forward to <strong>{new_event.date_range_display()}</strong>.</p>
        <p>The event has been pre-populated with last year's structure (meals, tasks, sleeping spots) but all sign-ups have been cleared. Review and update the details before it goes live.</p>
        <p><a href="{url}" style="background:#3D7040;color:#fff;padding:10px 20px;text-decoration:none;border-radius:4px;display:inline-block;">Review event →</a></p>
        <p style="color:#666;font-size:13px;">— The OurPeaPod team</p>
        """
    )


def send_assignment_notification_email(user, assignment, event, url):
    prefs_url = url.split('/events')[0] + '/profile/notifications'
    category = f' ({assignment.category})' if assignment.category else ''
    due = f'<br><strong>Due:</strong> {assignment.due_date.strftime("%B %d")}' if assignment.due_date else ''
    description = f'<p>{assignment.description}</p>' if assignment.description else ''
    return send_email(
        user.email,
        f"You've been assigned a task: {assignment.title}",
        f"""
        <h2>You have a new assignment for {event.name}</h2>
        <p><strong>{assignment.title}</strong>{category}{due}</p>
        {description}
        <p><a href="{url}">View event →</a></p>
        <p style="font-size:12px;color:#888;">
            You're receiving this because you have assignment notifications enabled.
            <a href="{prefs_url}">Manage preferences</a>
        </p>
        """
    )


def send_meal_item_assignment_email(user, item, event, url):
    prefs_url = url.split('/events')[0] + '/profile/notifications'
    meal_name = item.meal.name if item.meal else 'a meal'
    qty = f' (×{item.quantity})' if item.quantity and item.quantity > 1 else ''
    return send_email(
        user.email,
        f"You've been assigned a meal item: {item.label}",
        f"""
        <h2>You have a meal assignment for {event.name}</h2>
        <p>Please bring <strong>{item.label}</strong>{qty} for <strong>{meal_name}</strong>.</p>
        <p><a href="{url}">View event →</a></p>
        <p style="font-size:12px;color:#888;">
            You're receiving this because you have assignment notifications enabled.
            <a href="{prefs_url}">Manage preferences</a>
        </p>
        """
    )


def send_announcement_notification(user, announcement, url):
    author = announcement.author.get_display_name() if announcement.author else 'Someone'
    return send_email(
        user.email,
        f"New announcement: {announcement.title}",
        f"""
        <h2>{announcement.title}</h2>
        <p style="color:#666;font-size:13px;">Posted by {author}</p>
        <p>{announcement.body}</p>
        <p><a href="{url}">View all announcements →</a></p>
        <p style="font-size:12px;color:#888;">
            You're receiving this because you have announcement notifications enabled.
            <a href="{url.split('/announcements')[0]}/profile/notifications">Manage preferences</a>
        </p>
        """
    )


def send_digest_email(user, family, content, dashboard_url):
    upcoming_events = content['upcoming_events']
    upcoming_birthdays = content['upcoming_birthdays']
    upcoming_anniversaries = content['upcoming_anniversaries']
    recent_announcements = content['recent_announcements']
    recent_members = content['recent_members']
    recent_photo_count = content['recent_photo_count']

    notifications_url = dashboard_url.rstrip('/home') + '/profile/notifications'

    sections = []

    if upcoming_events:
        rows = ''.join(
            f'<tr><td style="padding:4px 0;"><strong>{e.name}</strong></td>'
            f'<td style="padding:4px 0 4px 16px;color:#555;">{e.date_range_display()}'
            f'{" · " + e.location if e.location else ""}</td></tr>'
            for e in upcoming_events
        )
        sections.append(f"""
        <h3 style="margin:24px 0 8px;font-size:15px;color:#2d4a1e;">Upcoming events</h3>
        <table style="border-collapse:collapse;font-size:14px;width:100%;">{rows}</table>
        """)

    if upcoming_birthdays or upcoming_anniversaries:
        items = []
        for person, bd in upcoming_birthdays:
            items.append(f'🎂 <strong>{person.get_display_name()}</strong> — {bd.strftime("%B %-d")}')
        for rel, ad in upcoming_anniversaries:
            p1 = rel.person1.get_display_name() if rel.person1 else '?'
            p2 = rel.person2.get_display_name() if rel.person2 else '?'
            items.append(f'💍 <strong>{p1} &amp; {p2}</strong> — {ad.strftime("%B %-d")}')
        sections.append(f"""
        <h3 style="margin:24px 0 8px;font-size:15px;color:#2d4a1e;">This week</h3>
        <ul style="margin:0;padding-left:20px;font-size:14px;line-height:1.8;">
            {''.join(f'<li>{item}</li>' for item in items)}
        </ul>
        """)

    if recent_announcements:
        items = ''.join(
            f'<li style="margin-bottom:8px;"><strong>{a.title}</strong></li>'
            for a in recent_announcements
        )
        sections.append(f"""
        <h3 style="margin:24px 0 8px;font-size:15px;color:#2d4a1e;">Recent announcements</h3>
        <ul style="margin:0;padding-left:20px;font-size:14px;">{items}</ul>
        """)

    if recent_members:
        names = ', '.join(u.get_full_name() for u in recent_members)
        sections.append(f"""
        <h3 style="margin:24px 0 8px;font-size:15px;color:#2d4a1e;">New members</h3>
        <p style="font-size:14px;margin:0;">Welcome to {names}!</p>
        """)

    if recent_photo_count:
        sections.append(f"""
        <h3 style="margin:24px 0 8px;font-size:15px;color:#2d4a1e;">Photos</h3>
        <p style="font-size:14px;margin:0;">{recent_photo_count} new photo{"s" if recent_photo_count != 1 else ""} added this week.</p>
        """)

    body = '\n'.join(sections)
    return send_email(
        user.email,
        f"This week in {family.name}",
        f"""
        <div style="font-family:sans-serif;max-width:560px;margin:0 auto;color:#222;">
            <p style="font-size:13px;color:#888;margin-bottom:4px;">{family.name} · Weekly digest</p>
            <h2 style="margin:0 0 4px;font-size:22px;font-weight:600;">This week in your pod</h2>
            <p style="font-size:14px;color:#555;margin-top:4px;">Hi {user.first_name} — here's what's coming up.</p>
            {body}
            <div style="margin-top:32px;">
                <a href="{dashboard_url}" style="background:#3a6b1e;color:#fff;padding:10px 20px;border-radius:6px;text-decoration:none;font-size:14px;">Go to your pod →</a>
            </div>
            <p style="font-size:11px;color:#aaa;margin-top:32px;">
                You're receiving this weekly digest from OurPeaPod.
                <a href="{notifications_url}" style="color:#888;">Manage preferences</a>
            </p>
        </div>
        """
    )


def send_pod_added_email(user, family_name, dashboard_url):
    return send_email(
        user.email,
        f"You've been added to {family_name} on OurPeaPod",
        f"""
        <h2>You've been added to {family_name}</h2>
        <p>Hi {user.first_name},</p>
        <p>A pod admin has added you to the <strong>{family_name}</strong> family on OurPeaPod.
        Sign in to see the family and switch between your pods.</p>
        <p><a href="{dashboard_url}">Go to OurPeaPod</a></p>
        """
    )


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
    return send_email(support_email, subject, html_content, reply_to=user.email)
