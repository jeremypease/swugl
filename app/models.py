from . import db
from flask_login import UserMixin
from datetime import date

class Person(db.Model):
    __tablename__ = 'people'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    gender = db.Column(db.String(10))
    birthday = db.Column(db.Date)
    nickname = db.Column(db.String(50))
    birthplace = db.Column(db.String(100))
    maiden_name = db.Column(db.String(100))
    spouse_name = db.Column(db.String(100))
    deathday = db.Column(db.Date)
    deathplace = db.Column(db.String(100))
    occupation = db.Column(db.String(100))
    email = db.Column(db.String(120))
    phone = db.Column(db.String(20))
    photo_path = db.Column(db.String(200))
    notes = db.Column(db.Text)

    def get_display_name(self):
        return self.nickname if self.nickname else self.name

    def get_age(self):
        end = self.deathday or date.today()
        age = end.year - self.birthday.year
        if (end.month, end.day) < (self.birthday.month, self.birthday.day):
            age -= 1
        return age
