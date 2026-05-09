from app import create_app, db
from app.models import Family, User, Person
from datetime import date

app = create_app()

def seed_admin():
    """Create the Pease family, admin account, and person record if no users exist yet."""
    if User.query.count() == 0:
        family = Family(name="Pease Family")
        db.session.add(family)
        db.session.flush()

        person = Person(
            name="Jeremy Pease",
            gender="Male",
            birthday=date(1979, 7, 20),
            email="jeremypease@me.com",
            family_id=family.id,
        )
        db.session.add(person)
        db.session.flush()

        admin = User(
            first_name="Jeremy",
            last_name="Pease",
            email="jeremypease@me.com",
            phone="801-857-7980",
            email_verified=True,
            status='approved',
            is_admin=True,
            family_id=family.id,
            person_id=person.id,
        )
        admin.set_password("IwillfollowH1m")
        db.session.add(admin)
        db.session.commit()
        print("Admin account created.")

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        seed_admin()
    app.run(debug=True)
