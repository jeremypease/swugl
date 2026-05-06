from app import create_app, db
from app.models import User, Person
from datetime import date

app = create_app()

def seed_admin():
    """Create admin account and person record if no users exist yet."""
    if User.query.count() == 0:
        # Create your Person record first
        person = Person(
            name="Jeremy Pease",
            gender="Male",
            birthday=date(1979, 7, 20),  # update with your real birthday
            email="jeremypease@me.com",     # update with your real email
        )
        db.session.add(person)
        db.session.flush()  # gets person.id without committing

        # Create your admin User account
        admin = User(
            first_name="Jeremy",
            last_name="Pease",
            email="jeremypease@me.com",     # update with your real email
            phone="801-857-7980",       # update with your real phone
            email_verified=True,
            status='approved',
            is_admin=True,
            person_id=person.id
        )
        admin.set_password("IwillfollowH1m")  # update with a real password
        db.session.add(admin)
        db.session.commit()
        print("Admin account created.")

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        seed_admin()
    app.run(debug=True)
