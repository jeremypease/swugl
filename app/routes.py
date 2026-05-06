from flask import Blueprint, render_template
from .models import Person

main = Blueprint('main', __name__)

@main.route('/')
def index():
    people = Person.query.order_by(Person.name).all()
    return render_template('index.html', people=people)
