from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, BooleanField, SubmitField, DateField, TextAreaField
from wtforms.validators import DataRequired, Email, Length, EqualTo, Regexp

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

    from wtforms import DateField, TextAreaField

class ProfileForm(FlaskForm):
    nickname = StringField('Nickname', validators=[Length(max=50)])
    gender = StringField('Gender', validators=[Length(max=10)])
    birthday = DateField('Birthday', validators=[DataRequired()])
    birthplace = StringField('Birthplace', validators=[Length(max=100)])
    maiden_name = StringField('Maiden Name', validators=[Length(max=100)])
    phone = StringField('Phone', validators=[Length(max=20)])
    notes = TextAreaField('Notes')
    submit = SubmitField('Save Changes')
