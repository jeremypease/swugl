import unittest
import family
from datetime import date

class TestFamilyRelationships(unittest.TestCase):
    def setUp(self):
        """
        Build a small, self-contained family graph in memory:

        Harold Pease  ── Jeannene Pease
            │
            ├── Jeremy Pease ── Moriah Pease
            │         │
            │         └── Tyler Pease
            │
            └── ShawnaLee Emerson ── Ryan Emerson
                      │
                      └── Eliza Emerson
        """
        # Short alias
        Person = family.Person

        harold = Person(
            name="Harold Pease",
            gender="Male",
            birthday=date(1947, 3, 23),
            parent_names=None,
            spouse_name="Jeannene Pease",
        )
        jeannene = Person(
            name="Jeannene Pease",
            gender="Female",
            birthday=date(1950, 1, 15),
            parent_names=None,
            spouse_name="Harold Pease",
        )
        jeremy = Person(
            name="Jeremy Pease",
            gender="Male",
            birthday=date(1979, 7, 20),
            parent_names=["Harold Pease", "Jeannene Pease"],
            spouse_name="Moriah Pease",
        )
        moriah = Person(
            name="Moriah Pease",
            gender="Female",
            birthday=date(1982, 6, 28),
            parent_names=None,
            spouse_name="Jeremy Pease",
        )
        tyler = Person(
            name="Tyler Pease",
            gender="Male",
            birthday=date(2002, 9, 2),
            parent_names=["Jeremy Pease", "Moriah Pease"],
        )
        shawna = Person(
            name="ShawnaLee Emerson",
            gender="Female",
            birthday=date(1975, 5, 14),
            parent_names=["Harold Pease", "Jeannene Pease"],
            spouse_name="Ryan Emerson",
        )
        ryan = Person(
            name="Ryan Emerson",
            gender="Male",
            birthday=date(1973, 6, 4),
            parent_names=None,
            spouse_name="ShawnaLee Emerson",
        )
        eliza = Person(
            name="Eliza Emerson",
            gender="Female",
            birthday=date(2000, 1, 1),
            parent_names=["Ryan Emerson", "ShawnaLee Emerson"],
        )

        # Install this test data into the family module
        family.people = [
            harold,
            jeannene,
            jeremy,
            moriah,
            tyler,
            shawna,
            ryan,
            eliza,
        ]

        # Expose for convenience
        self.harold = harold
        self.jeannene = jeannene
        self.jeremy = jeremy
        self.moriah = moriah
        self.tyler = tyler
        self.shawna = shawna
        self.ryan = ryan
        self.eliza = eliza

    # --- Basic structure tests ---

    def test_children_of_harold_and_jeannene(self):
        children_h = family.get_children(self.harold, family.people)
        child_names_h = sorted(p.name for p in children_h)
        self.assertEqual(child_names_h, sorted([
            "Jeremy Pease",
            "ShawnaLee Emerson",
        ]))

        children_j = family.get_children(self.jeannene, family.people)
        child_names_j = sorted(p.name for p in children_j)
        self.assertEqual(child_names_j, child_names_h)

    def test_descendants_of_harold(self):
        desc = family.get_descendants(self.harold, family.people)
        names = sorted(p.name for p in desc)
        # Children + grandchildren
        expected = sorted([
            "Jeremy Pease",
            "ShawnaLee Emerson",
            "Tyler Pease",
            "Eliza Emerson",
        ])
        self.assertEqual(names, expected)

    # --- describe_relationship tests ---

    def test_jeremy_to_tyler(self):
        rel = family.describe_relationship(self.jeremy, self.tyler)
        self.assertEqual(rel, "son")

    def test_harold_to_jeremy(self):
        rel = family.describe_relationship(self.harold, self.jeremy)
        self.assertEqual(rel, "son")

    def test_jeremy_to_harold(self):
        rel = family.describe_relationship(self.jeremy, self.harold)
        # Harold is Jeremy's father
        self.assertEqual(rel, "father")

    def test_harold_to_tyler(self):
        rel = family.describe_relationship(self.harold, self.tyler)
        self.assertEqual(rel, "grandson")

    def test_tyler_to_harold(self):
        rel = family.describe_relationship(self.tyler, self.harold)
        self.assertEqual(rel, "grandfather")

    def test_jeremy_to_moriah(self):
        rel = family.describe_relationship(self.jeremy, self.moriah)
        self.assertEqual(rel, "wife")

    def test_moriah_to_jeremy(self):
        rel = family.describe_relationship(self.moriah, self.jeremy)
        self.assertEqual(rel, "husband")

    def test_jeremy_to_shawna(self):
        rel = family.describe_relationship(self.jeremy, self.shawna)
        self.assertEqual(rel, "sister")

    def test_shawna_to_jeremy(self):
        rel = family.describe_relationship(self.shawna, self.jeremy)
        self.assertEqual(rel, "brother")

    def test_jeremy_to_ryan(self):
        rel = family.describe_relationship(self.jeremy, self.ryan)
        self.assertEqual(rel, "brother-in-law")

    def test_harold_to_ryan(self):
        rel = family.describe_relationship(self.harold, self.ryan)
        self.assertEqual(rel, "son-in-law")

    def test_jeremy_to_eliza(self):
        rel = family.describe_relationship(self.jeremy, self.eliza)
        self.assertEqual(rel, "niece")

if __name__ == "__main__":
    unittest.main()