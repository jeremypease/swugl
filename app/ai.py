import json
import base64
from flask import current_app


def _client():
    import anthropic
    key = current_app.config.get('ANTHROPIC_API_KEY')
    if not key:
        return None
    return anthropic.Anthropic(api_key=key)


def _haiku(messages, max_tokens=200):
    client = _client()
    if not client:
        return None
    msg = client.messages.create(
        model='claude-haiku-4-5-20251001',
        max_tokens=max_tokens,
        messages=messages,
    )
    return msg.content[0].text.strip()


def _parse_json(text):
    if text.startswith('```'):
        text = text.split('\n', 1)[1].rsplit('```', 1)[0].strip()
    return json.loads(text)


def draft_card_message(recipient_name, occasion, family_name=None):
    """Return a warm greeting card message body."""
    ctx = f" in the {family_name} family" if family_name else ""
    prompt = (
        f"Write a warm, heartfelt greeting card message for {recipient_name}{ctx} "
        f"on the occasion of {occasion}. "
        "Keep it genuine and concise — 2-4 sentences. "
        "Write in first person as if the sender is writing it. "
        "No salutations, no sign-offs, just the message body."
    )
    return _haiku([{'role': 'user', 'content': prompt}], max_tokens=200)


def generate_story_prompt(person, recent_questions=None):
    """Return one warm, open-ended life-story question personalized to `person`,
    or None if AI is unavailable. `recent_questions` is a list of prior question
    strings to avoid repeating."""
    bits = []
    age = person.get_age()
    if age:
        bits.append(f"about {age} years old" if not person.deathday else f"who lived to about {age}")
    if person.birthplace:
        bits.append(f"born in {person.birthplace}")
    if person.occupation:
        bits.append(f"worked as {person.occupation}")
    profile = "; ".join(bits) if bits else "a family member"
    avoid = ""
    if recent_questions:
        joined = " | ".join(q for q in recent_questions if q)
        if joined:
            avoid = (" Do NOT repeat or closely resemble any of these previously asked "
                     f"questions: {joined}.")
    prompt = (
        f"Write ONE warm, specific, open-ended question inviting {person.get_display_name()} "
        f"({profile}) to share a memory or story from their life. "
        "Think StoryWorth: questions that draw out vivid personal history "
        "(childhood, family, work, love, lessons, turning points). "
        "It must be a single question, 1-2 sentences, never yes/no, and answerable by anyone "
        "regardless of background." + avoid +
        " Return only the question text — no preamble, no quotes."
    )
    return _haiku([{'role': 'user', 'content': prompt}], max_tokens=150)


def suggest_poll(topic, family_name=None):
    """Return {question, options} for a poll on the given topic."""
    ctx = f" for the {family_name} family" if family_name else ""
    prompt = (
        f"Create a fun, engaging poll{ctx} about: {topic}. "
        'Return ONLY valid JSON with keys "question" (string) and "options" (array of 3-4 short strings). '
        "Keep it light and family-friendly."
    )
    text = _haiku([{'role': 'user', 'content': prompt}], max_tokens=200)
    if text is None:
        return None
    return _parse_json(text)


def narrate_digest(content, family_name):
    """Return a 1-2 sentence friendly intro for the weekly digest, or None."""
    parts = []
    if content.get('upcoming_events'):
        names = [e.name for e in content['upcoming_events'][:3]]
        parts.append(f"upcoming events: {', '.join(names)}")
    if content.get('recent_photo_count'):
        parts.append(f"{content['recent_photo_count']} new photos uploaded")
    if content.get('recent_members'):
        names = [u.first_name for u in content['recent_members'][:2]]
        parts.append(f"new members joining: {', '.join(names)}")
    if content.get('upcoming_birthdays'):
        names = [p.get_display_name() for p, _ in content['upcoming_birthdays'][:2]]
        parts.append(f"birthdays coming up: {', '.join(names)}")
    if not parts:
        return None
    summary = '; '.join(parts)
    prompt = (
        f"Write a warm, friendly 1-2 sentence intro for the {family_name} family's weekly email. "
        f"This week's highlights: {summary}. "
        "Tone: like a friendly family member catching everyone up. "
        "Do not use the word 'digest'. No greetings or sign-offs — just the body sentence(s)."
    )
    return _haiku([{'role': 'user', 'content': prompt}], max_tokens=120)


def generate_story_prompt(family, recent_questions=None):
    """Generate a weekly story prompt question for the family via Claude Haiku."""
    avoid = ''
    if recent_questions:
        bullets = '\n'.join(f'- {q}' for q in recent_questions[:6])
        avoid = f'\n\nRecent questions already asked (do not repeat these topics):\n{bullets}'
    prompt = (
        f"You are helping the {family.name} family preserve their history through guided storytelling.\n\n"
        "Generate ONE thoughtful, open-ended question that will prompt a family member to share a meaningful "
        "personal story or memory. Requirements:\n"
        "- Warm and specific — invite a particular memory, not a general opinion\n"
        "- Answerable by anyone, young or old\n"
        "- Covers childhood, traditions, milestones, relationships, career, or wisdom\n"
        "- 1-2 sentences maximum"
        f"{avoid}\n\n"
        "Return ONLY the question, nothing else."
    )
    return _haiku([{'role': 'user', 'content': prompt}], max_tokens=150)


def suggest_photo_caption(photo_bytes, content_type='image/jpeg'):
    """Return a suggested caption for a photo, or None."""
    client = _client()
    if not client:
        return None
    safe_types = {'image/jpeg', 'image/png', 'image/gif', 'image/webp'}
    media_type = content_type if content_type in safe_types else 'image/jpeg'
    b64 = base64.standard_b64encode(photo_bytes).decode('utf-8')
    msg = client.messages.create(
        model='claude-haiku-4-5-20251001',
        max_tokens=80,
        messages=[{
            'role': 'user',
            'content': [
                {'type': 'image', 'source': {'type': 'base64', 'media_type': media_type, 'data': b64}},
                {'type': 'text', 'text': 'Suggest a short, warm caption for this family photo. One sentence, no hashtags.'},
            ],
        }],
    )
    return msg.content[0].text.strip()
