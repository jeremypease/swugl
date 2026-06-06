GENDER_CHOICES_DEFAULT = [
    ('', '-- Select --'),
    ('Male', 'Male'),
    ('Female', 'Female'),
]

GENDER_CHOICES_EXPANDED = [
    ('', '-- Select --'),
    ('Male', 'Male'),
    ('Female', 'Female'),
    ('Non-binary', 'Non-binary'),
    ('Genderqueer', 'Genderqueer'),
    ('Genderfluid', 'Genderfluid'),
    ('Transgender Male', 'Transgender Male'),
    ('Transgender Female', 'Transgender Female'),
    ('Intersex', 'Intersex'),
    ('Prefer not to say', 'Prefer not to say'),
]

PRONOUN_CHOICES = [
    ('', '-- Select --'),
    ('He/Him', 'He/Him'),
    ('She/Her', 'She/Her'),
    ('They/Them', 'They/Them'),
    ('Ze/Zir', 'Ze/Zir'),
    ('Any', 'Any'),
]

from flask_wtf import FlaskForm
from flask_wtf.file import FileField, FileAllowed
from wtforms import StringField, PasswordField, BooleanField, SubmitField, DateField, TimeField, TextAreaField, SelectField, IntegerField, HiddenField
from wtforms.validators import DataRequired, Email, Length, EqualTo, Regexp, Optional

class LoginForm(FlaskForm):
    email = StringField('Email', validators=[
        DataRequired(),
        Email()
    ])
    password = PasswordField('Password', validators=[
        DataRequired()
    ])
    remember_me = BooleanField('Remember Me')
    submit = SubmitField('Sign In')


class RegistrationForm(FlaskForm):
    family_name = StringField('Family Name', validators=[Length(max=100)])
    first_name = StringField('First Name', validators=[
        DataRequired(),
        Length(min=2, max=50)
    ])
    last_name = StringField('Last Name', validators=[
        DataRequired(),
        Length(min=2, max=50)
    ])
    email = StringField('Email', validators=[
        DataRequired(),
        Email()
    ])
    phone = StringField('Phone', validators=[
        DataRequired()
    ])
    password = PasswordField('Password', validators=[
        DataRequired(),
        Length(min=8, message='Password must be at least 8 characters'),
        Regexp(r'(?=.*[A-Z])', message='Password must include an uppercase letter'),
        Regexp(r'(?=.*[a-z])', message='Password must include a lowercase letter'),
        Regexp(r'(?=.*\d)', message='Password must include a number'),
        Regexp(r'(?=.*[!@#$%^&*])', message='Password must include a special character (!@#$%^&*)')
    ])
    confirm_password = PasswordField('Confirm Password', validators=[
        DataRequired(),
        EqualTo('password', message='Passwords must match')
    ])
    submit = SubmitField('Register')


class ProfileForm(FlaskForm):
    nickname = StringField('Nickname', validators=[Length(max=50)])
    gender = SelectField('Gender', choices=[
        ('', '-- Select --'),
        ('Male', 'Male'),
        ('Female', 'Female')
    ], validators=[DataRequired()])
    birthday = DateField('Birthday', validators=[DataRequired()])
    birthplace = StringField('Birthplace', validators=[Length(max=100)])
    maiden_name = StringField('Maiden Name', validators=[Length(max=100)])
    phone = StringField('Phone', validators=[Length(max=20)])
    notes = TextAreaField('Notes')
    submit = SubmitField('Save Changes')

class SpouseForm(FlaskForm):
    spouse_id = SelectField('Select Spouse', coerce=int, validators=[DataRequired()])
    marriage_date = DateField('Marriage Date', validators=[Optional()])
    submit = SubmitField('Send Spouse Request')

class EndSpouseForm(FlaskForm):
    status = SelectField('Reason for Ending', choices=[
        ('divorced', 'Divorced'),
        ('deceased', 'Deceased'),
        ('separated', 'Separated'),
        ('annulled', 'Annulled')
    ], validators=[DataRequired()])
    end_date = DateField('End Date', validators=[Optional()])
    submit = SubmitField('Confirm')

class SpouseInviteForm(FlaskForm):
    first_name = StringField('First Name', validators=[DataRequired(), Length(min=2, max=50)])
    last_name = StringField('Last Name', validators=[DataRequired(), Length(min=2, max=50)])
    email = StringField('Email', validators=[DataRequired(), Email()])
    marriage_date = DateField('Marriage Date', validators=[Optional()])
    submit = SubmitField('Send Invitation')

class FamilySettingsForm(FlaskForm):
    family_name = StringField('Family Name', validators=[DataRequired(), Length(max=100)])
    patriarch_id = SelectField('Patriarch', coerce=int)
    matriarch_id = SelectField('Matriarch', coerce=int)
    has_lgbtq_options = BooleanField('Enable expanded gender & pronoun options')
    submit = SubmitField('Save')

class AddPersonForm(FlaskForm):
    first_name = StringField('First Name', validators=[DataRequired(), Length(2, 50)])
    last_name = StringField('Last Name', validators=[DataRequired(), Length(2, 50)])
    email = StringField('Email', validators=[Optional(), Email()])
    phone = StringField('Phone', validators=[Optional()])
    gender = SelectField('Gender', choices=GENDER_CHOICES_DEFAULT)
    birthday = DateField('Birthday', validators=[])
    birthplace = StringField('Birthplace', validators=[Optional(), Length(max=100)])
    nickname = StringField('Nickname', validators=[Optional(), Length(max=50)])
    maiden_name = StringField('Maiden Name', validators=[Optional(), Length(max=100)])
    notes = TextAreaField('Notes')
    submit = SubmitField('Add Member')

class EditPersonForm(FlaskForm):
    name = StringField('Full Name', validators=[DataRequired(), Length(max=100)])
    nickname = StringField('Nickname / Goes By', validators=[Optional(), Length(max=50)])
    gender = SelectField('Gender', choices=GENDER_CHOICES_DEFAULT, validate_choice=False)
    pronouns = SelectField('Pronouns', choices=PRONOUN_CHOICES, validate_choice=False)
    birthday = DateField('Birthday', validators=[Optional()])
    birthplace = StringField('Birthplace', validators=[Optional(), Length(max=100)])
    maiden_name = StringField('Maiden Name', validators=[Optional(), Length(max=100)])
    occupation = StringField('Occupation', validators=[Optional(), Length(max=100)])
    email = StringField('Email', validators=[Optional(), Email()])
    phone = StringField('Phone', validators=[Optional(), Length(max=20)])
    address = StringField('Home Address', validators=[Optional(), Length(max=200)])
    deathday = DateField('Date of Passing', validators=[Optional()])
    deathplace = StringField('Place of Passing', validators=[Optional(), Length(max=100)])
    notes = TextAreaField('Notes')
    photo = FileField('Profile Photo', validators=[Optional(), FileAllowed(['jpg', 'jpeg', 'png', 'webp'], 'Images only.')])
    remove_photo = BooleanField('Remove current photo')
    submit = SubmitField('Save Changes')

class RelativeForm(FlaskForm):
    relative_id = SelectField('Select', coerce=int, validators=[DataRequired()])
    submit = SubmitField('Add')

class AddParentForm(FlaskForm):
    relative_id = SelectField('Select Person', coerce=int, validators=[DataRequired()])
    role = SelectField('Role', choices=[
        ('father',          'Father'),
        ('mother',          'Mother'),
        ('stepfather',      'Step-Father'),
        ('stepmother',      'Step-Mother'),
        ('adoptive_father', 'Adoptive Father'),
        ('adoptive_mother', 'Adoptive Mother'),
        ('guardian',        'Guardian'),
        ('parent',          'Parent'),
    ], validators=[DataRequired()])
    submit = SubmitField('Add')

class ForgotPasswordForm(FlaskForm):
    email = StringField('Email', validators=[DataRequired(), Email()])
    submit = SubmitField('Send Reset Link')

EVENT_KIND_CHOICES = [
    ('', '— Optional —'),
    ('Reunion', 'Reunion'),
    ('Holiday', 'Holiday'),
    ('Birthday', 'Birthday'),
    ('Camping', 'Camping'),
    ('Wedding', 'Wedding'),
    ('Graduation', 'Graduation'),
    ('Other', 'Other'),
]

class EventForm(FlaskForm):
    name = StringField('Event Name', validators=[DataRequired(), Length(max=150)])
    kind = SelectField('Type', choices=EVENT_KIND_CHOICES, validators=[Optional()])
    description = TextAreaField('Description')
    location = StringField('Location', validators=[Optional(), Length(max=200)])
    start_date = DateField('Start Date', validators=[DataRequired()])
    end_date = DateField('End Date', validators=[Optional()])
    start_time = TimeField('Start Time', validators=[Optional()], format='%H:%M')
    end_time = TimeField('End Time', validators=[Optional()], format='%H:%M')
    rsvp_deadline = DateField('RSVP Deadline', validators=[Optional()])
    is_annual = BooleanField('Repeats annually')
    has_meals = BooleanField('Meals')
    has_assignments = BooleanField('Assignments')
    has_sleeping = BooleanField('Sleeping Arrangements')
    has_carpool = BooleanField('Carpool')
    cover_image = FileField('Cover Image', validators=[Optional(), FileAllowed(['jpg', 'jpeg', 'png', 'webp'], 'Images only.')])
    remove_cover = BooleanField('Remove cover image')
    submit = SubmitField('Save Event')

class EventCommentForm(FlaskForm):
    body = StringField('Comment', validators=[DataRequired(), Length(max=1000)])
    submit = SubmitField('Post')

class EventMealForm(FlaskForm):
    name = StringField('Meal Name', validators=[DataRequired(), Length(max=150)])
    meal_date = DateField('Date', validators=[Optional()])
    meal_time = StringField('Time', validators=[Optional(), Length(max=20)])
    notes = TextAreaField('Notes')
    submit = SubmitField('Add Meal')

class EventMealFamilyAssignForm(FlaskForm):
    assigned_family_id = SelectField('Assign to family', coerce=int)
    submit = SubmitField('Assign')

class EventMealItemForm(FlaskForm):
    label = StringField('Item', validators=[DataRequired(), Length(max=150)])
    quantity = IntegerField('Qty needed', validators=[Optional()])
    is_cleanup = BooleanField('Cleanup task')
    submit = SubmitField('Add Item')

class EventMealSelfSignupForm(FlaskForm):
    label = StringField('What will you bring?', validators=[DataRequired(), Length(max=150)])
    submit = SubmitField('Sign Up')

class EventMealAssignForm(FlaskForm):
    person_id = SelectField('Assign to', coerce=int, validators=[DataRequired()])
    submit = SubmitField('Assign')

class EventAssignmentForm(FlaskForm):
    title = StringField('Task', validators=[DataRequired(), Length(max=150)])
    description = TextAreaField('Details')
    category = SelectField('Category', choices=[('', '— General —'), ('Setup', 'Setup'), ('Cleanup', 'Cleanup'), ('Food', 'Food'), ('Errands', 'Errands'), ('Other', 'Other')], validators=[Optional()])
    due_date = DateField('Due date', validators=[Optional()])
    submit = SubmitField('Add Task')

class EventAssignmentAdminAssignForm(FlaskForm):
    person_id = SelectField('Assign to', coerce=int, validators=[Optional()])
    submit = SubmitField('Assign')

class EventSleepingSpotForm(FlaskForm):
    name = StringField('Room / Spot', validators=[DataRequired(), Length(max=150)])
    spot_type = SelectField('Type', choices=[('', '— Type —'), ('Bedroom', 'Bedroom'), ('Couch', 'Couch'), ('Air mattress', 'Air mattress'), ('Tent', 'Tent'), ('Cabin bunk', 'Cabin bunk'), ('Other', 'Other')], validators=[Optional()])
    capacity = IntegerField('Capacity', validators=[Optional()])
    notes = TextAreaField('Notes')
    submit = SubmitField('Add Spot')

class EventSleepingAssignForm(FlaskForm):
    person_id = SelectField('Person', coerce=int, validators=[DataRequired()])
    submit = SubmitField('Assign')

class AlbumForm(FlaskForm):
    name = StringField('Album Name', validators=[DataRequired(), Length(max=150)])
    description = TextAreaField('Description', validators=[Optional()])
    year = IntegerField('Year', validators=[Optional()])
    event_id = SelectField('Link to Event', coerce=int, validators=[Optional()])
    submit = SubmitField('Create Album')

class PhotoUploadForm(FlaskForm):
    photos = FileField('Photos', validators=[Optional()])
    caption = StringField('Caption', validators=[Optional(), Length(max=300)])
    submit = SubmitField('Upload')

class AnnouncementForm(FlaskForm):
    title = StringField('Title', validators=[DataRequired(), Length(max=150)])
    body = TextAreaField('Message', validators=[DataRequired()])
    pinned = BooleanField('Pin to top')
    submit = SubmitField('Post')

class ChangePasswordForm(FlaskForm):
    current_password = PasswordField('Current Password', validators=[DataRequired()])
    new_password = PasswordField('New Password', validators=[
        DataRequired(),
        Length(min=8, message='Password must be at least 8 characters'),
        Regexp(r'(?=.*[A-Z])', message='Password must include an uppercase letter'),
        Regexp(r'(?=.*[a-z])', message='Password must include a lowercase letter'),
        Regexp(r'(?=.*\d)', message='Password must include a number'),
        Regexp(r'(?=.*[!@#$%^&*])', message='Password must include a special character (!@#$%^&*)')
    ])
    confirm_password = PasswordField('Confirm Password', validators=[
        DataRequired(),
        EqualTo('new_password', message='Passwords must match')
    ])
    submit = SubmitField('Change Password')

class ResetPasswordForm(FlaskForm):
    password = PasswordField('New Password', validators=[
        DataRequired(),
        Length(min=8, message='Password must be at least 8 characters'),
        Regexp(r'(?=.*[A-Z])', message='Password must include an uppercase letter'),
        Regexp(r'(?=.*[a-z])', message='Password must include a lowercase letter'),
        Regexp(r'(?=.*\d)', message='Password must include a number'),
        Regexp(r'(?=.*[!@#$%^&*])', message='Password must include a special character (!@#$%^&*)')
    ])
    confirm_password = PasswordField('Confirm Password', validators=[
        DataRequired(),
        EqualTo('password', message='Passwords must match')
    ])
    submit = SubmitField('Reset Password')

class SupportForm(FlaskForm):
    category = SelectField('What can we help with?', choices=[
        ('billing',   'Billing or subscription'),
        ('account',   'Account or access issue'),
        ('technical', 'Technical problem'),
        ('feature',   'Feature request'),
        ('other',     'Something else'),
    ], validators=[DataRequired()])
    message = TextAreaField('Message', validators=[DataRequired(), Length(min=10, max=2000)])
    submit = SubmitField('Send Message')