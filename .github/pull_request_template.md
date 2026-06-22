## What this PR does

<!-- One or two sentences. What changed and why. -->

## Review checklist

- [ ] All tests pass: `.venv/bin/pytest tests/ -v`
- [ ] Every new query uses `current_user.active_family_id` — not bare `current_user.family_id`
- [ ] New object lookups verify the returned object belongs to the active family
- [ ] New write routes have `@login_required` + the correct role decorator
- [ ] Paid features have `@requires_plan` on the route (not just hidden in the template)
- [ ] No CSS, layout, font, color, or spacing changes (those go on the `design` branch)
- [ ] If a model changed: migration file exists and `flask db upgrade` runs clean
- [ ] Any email send checks `NotificationPreference.is_enabled()` first

## Notes for Jeremy

<!-- Anything he should know before merging — edge cases, follow-up items, things to test manually. -->
