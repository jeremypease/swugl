from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, BooleanField, SubmitField, DateField, TextAreaField, SelectField
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
    marriage_date = DateField('Marriage Date', validators=[])
    submit = SubmitField('Send Spouse Request')

class EndSpouseForm(FlaskForm):
    status = SelectField('Reason for Ending', choices=[
        ('divorced', 'Divorced'),
        ('deceased', 'Deceased'),
        ('separated', 'Separated'),
        ('annulled', 'Annulled')
    ], validators=[DataRequired()])
    end_date = DateField('End Date', validators=[])
    submit = SubmitField('Confirm')

class SpouseInviteForm(FlaskForm):
    first_name = StringField('First Name', validators=[DataRequired(), Length(min=2, max=50)])
    last_name = StringField('Last Name', validators=[DataRequired(), Length(min=2, max=50)])
    email = StringField('Email', validators=[DataRequired(), Email()])
    marriage_date = DateField('Marriage Date', validators=[])
    submit = SubmitField('Send Invitation')

class FamilySettingsForm(FlaskForm):
    family_name = StringField('Family Name', validators=[DataRequired(), Length(max=100)])
    patriarch_id = SelectField('Patriarch', coerce=int)
    matriarch_id = SelectField('Matriarch', coerce=int)
    submit = SubmitField('Save')

class AddPersonForm(FlaskForm):
    first_name = StringField('First Name', validators=[DataRequired(), Length(2, 50)])
    last_name = StringField('Last Name', validators=[DataRequired(), Length(2, 50)])
    email = StringField('Email', validators=[Optional(), Email()])
    phone = StringField('Phone', validators=[Optional()])
    gender = SelectField('Gender', choices=[
        ('', '-- Select --'),
        ('Male', 'Male'),
        ('Female', 'Female')
    ])
    birthday = DateField('Birthday', validators=[])
    birthplace = StringField('Birthplace', validators=[Optional(), Length(max=100)])
    nickname = StringField('Nickname', validators=[Optional(), Length(max=50)])
    maiden_name = StringField('Maiden Name', validators=[Optional(), Length(max=100)])
    notes = TextAreaField('Notes')
    submit = SubmitField('Add Member')

class RelativeForm(FlaskForm):
    relative_id = SelectField('Select', coerce=int, validators=[DataRequired()])
    submit = SubmitField('Add')

class ForgotPasswordForm(FlaskForm):
    email = StringField('Email', validators=[DataRequired(), Email()])
    submit = SubmitField('Send Reset Link')

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