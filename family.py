from dataclasses import dataclass, asdict
from datetime import date
from typing import Optional, List
import json
import os

@dataclass
class Person:
    name: str
    gender: str
    birthday: [date]
    maiden_name: Optional[str] = None
    relationship: [str] = None  # legacy label (your perspective)
    spouse_name: Optional[str] = None
    parent_names: Optional[List[str]] = None  # actual parents only
    deathday: Optional[date] = None

people: List[Person] = []

# Will hold "who is looking" so relationships are computed from their perspective
current_viewer: Optional[Person] = None

# --- Core queries ---

def get_oldest_person(people_list: List[Person]) -> Optional[Person]:
    return min(
        (p for p in people_list if p.birthday is not None),
        key=lambda p: p.birthday,
        default=None,
    )

def get_youngest_person(people_list: List[Person]) -> Optional[Person]:
    return max(
        (p for p in people_list if p.birthday is not None),
        key=lambda p: p.birthday,
        default=None,
    )

def get_person_age(person: Person) -> Optional[int]:
    if person.birthday is None:
        return None

    # Use deathday if set; otherwise today's date
    end = person.deathday or date.today()
    age = end.year - person.birthday.year
    if (end.month, end.day) < (person.birthday.month, person.birthday.day):
        age -= 1
    return age

def get_person_gender(person: Person) -> str:
    if person.gender == "Female":
        return "her"
    if person.gender == "Male":
        return "him"
    return "them"

def get_children(person: Person, people_list: List[Person]) -> List[Person]:
    """Direct children of this person (by actual parents)."""
    return [
        p
        for p in people_list
        if p.parent_names is not None and person.name in p.parent_names
    ]

def get_descendants(person: Person, people_list: List[Person]) -> List[Person]:
    """All descendants (children, grandchildren, etc.) following parent links."""
    descendants: List[Person] = []
    seen_names = set()
    stack = [person]

    while stack:
        current = stack.pop()
        children = get_children(current, people_list)
        for child in children:
            if child.name not in seen_names:
                seen_names.add(child.name)
                descendants.append(child)
                stack.append(child)

    return descendants

# --- JSON save/load helpers ---

def person_to_dict(p: Person) -> dict:
    d = asdict(p)

    # Normalize birthday
    if p.birthday is not None:
        d["birthday"] = p.birthday.isoformat()
    else:
        d["birthday"] = None

    # Normalize deathday
    if p.deathday is not None:
        d["deathday"] = p.deathday.isoformat()
    else:
        d["deathday"] = None

    return d

def person_from_dict(d: dict) -> Person:
    bday_str = d.get("birthday")
    birthday = date.fromisoformat(bday_str) if bday_str else None

    death_str = d.get("deathday")
    deathday = date.fromisoformat(death_str) if death_str else None

    return Person(
        name=d["name"],
        gender=d["gender"],
        birthday=birthday,
        maiden_name=d.get("maiden_name"),
        relationship=d.get("relationship"),
        spouse_name=d.get("spouse_name"),
        parent_names=d.get("parent_names"),
        deathday=deathday,
    )

def save_to_json(filename: str = "family.json") -> None:
    data = [person_to_dict(p) for p in people]
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    print(f"Saved {len(people)} people to {filename}.")

def load_from_json(filename: str = "family.json") -> None:
    global people
    if not os.path.exists(filename):
        print(f"No file named {filename} found.")
        return
    with open(filename, "r", encoding="utf-8") as f:
        data = json.load(f)
    people = [person_from_dict(d) for d in data]
    print(f"Loaded {len(people)} people from {filename}.")

# --- Basic interactive helpers ---

def find_person_by_name(name: str) -> Optional[Person]:
    for p in people:
        if p.name == name:
            return p
    return None

def add_person_interactive():
    print("=== Add a new person ===")
    name = input("Name: ").strip()
    if not name:
        print("Name is required.")
        return

    gender = input("Gender (Male/Female): ").strip() or "Unknown"
    birthday_str = input("Birthday (YYYY-MM-DD, or blank if unknown): ").strip()
    death_str = input("Death day (YYYY-MM-DD, or blank if alive): ").strip()
    maiden_name = input("Maiden name (or blank): ").strip() or None
    relationship = input("Relationship (e.g., Niece, Brother, etc.): ").strip() or None
    spouse_name = input("Spouse name (or blank): ").strip() or None

    parent_names_input = input(
        "Parents (comma-separated names, or blank if unknown): "
    ).strip()
    parent_names = (
        [p.strip() for p in parent_names_input.split(",") if p.strip()]
        if parent_names_input
        else None
    )

    if birthday_str:
        try:
            year, month, day = map(int, birthday_str.split("-"))
            birthday = date(year, month, day)
        except ValueError:
            print("Invalid birth date format. Use YYYY-MM-DD.")
            return
    else:
        birthday = None

    if death_str:
        try:
            y, m, d_ = map(int, death_str.split("-"))
            deathday = date(y, m, d_)
        except ValueError:
            print("Invalid death date format. Use YYYY-MM-DD.")
            return
    else:
        deathday = None

    new_person = Person(
        name=name,
        gender=gender,
        birthday=birthday,
        maiden_name=maiden_name,
        relationship=relationship,
        spouse_name=spouse_name,
        parent_names=parent_names,
        deathday=deathday,
    )
    people.append(new_person)
    print(f"Added {name}.")

def update_person_interactive():
    print("=== Update an existing person ===")
    name = input("Enter the name of the person to update: ").strip()
    person = find_person_by_name(name)
    if not person:
        print(f"No person found with name '{name}'.")
        return

    print(f"Editing {person.name}. Press Enter to keep current value.")
    print(f"Current data: {asdict(person)}")

    new_name = input(f"Name [{person.name}]: ").strip() or person.name
    new_gender = input(f"Gender [{person.gender}]: ").strip() or person.gender

    if person.birthday:
        current_bday_str = person.birthday.isoformat()
    else:
        current_bday_str = ""
    birthday_str = input(
        f"Birthday (YYYY-MM-DD) [{current_bday_str}]: "
    ).strip()

    if birthday_str:
        try:
            year, month, day = map(int, birthday_str.split("-"))
            new_birthday = date(year, month, day)
        except ValueError:
            print("Invalid birth date format. Keeping existing birthday.")
            new_birthday = person.birthday
    else:
        new_birthday = person.birthday

    if person.deathday:
        current_death_str = person.deathday.isoformat()
    else:
        current_death_str = ""
    death_str = input(
        f"Death day (YYYY-MM-DD, blank if alive) [{current_death_str}]: "
    ).strip()

    if death_str:
        try:
            y, m, d_ = map(int, death_str.split("-"))
            new_deathday = date(y, m, d_)
        except ValueError:
            print("Invalid death date format. Keeping existing deathday.")
            new_deathday = person.deathday
    else:
        new_deathday = person.deathday

    new_maiden = input(
        f"Maiden name [{person.maiden_name or ''}]: "
    ).strip() or person.maiden_name

    new_rel = input(
        f"Relationship [{person.relationship or ''}]: "
    ).strip() or person.relationship

    new_spouse = input(
        f"Spouse name [{person.spouse_name or ''}]: "
    ).strip() or person.spouse_name

    current_parents = ", ".join(person.parent_names or [])
    parents_input = input(
        f"Parents (comma-separated) [{current_parents}]: "
    ).strip()

    if parents_input:
        new_parents = [p.strip() for p in parents_input.split(",") if p.strip()]
    else:
        new_parents = person.parent_names

    # Apply updates
    person.name = new_name
    person.gender = new_gender
    person.birthday = new_birthday
    person.deathday = new_deathday
    person.maiden_name = new_maiden
    person.relationship = new_rel
    person.spouse_name = new_spouse
    person.parent_names = new_parents

    print(f"Updated {person.name}.")

def show_oldest_and_youngest():
    oldest = get_oldest_person(people)
    youngest = get_youngest_person(people)

    if oldest:
        oldest_age = get_person_age(oldest)
        print(
            f"Oldest: {oldest.name} (birthday {oldest.birthday}, age {oldest_age})"
        )
    else:
        print("No oldest person (no birthdays set).")

    if youngest:
        youngest_age = get_person_age(youngest)
        print(
            f"Youngest: {youngest.name} (birthday {youngest.birthday}, age {youngest_age})"
        )
    else:
        print("No youngest person (no birthdays set).")

def list_descendants_of_person():
    name = input("Enter the name of the person: ").strip()
    person = find_person_by_name(name)
    if not person:
        print(f"No person found with name '{name}'.")
        return

    desc = get_descendants(person, people)
    if not desc:
        print(f"{person.name} has no recorded descendants.")
        return

    print(f"{person.name}'s descendants:")
    for p in desc:
        print(" -", p.name)

# --- Relationship helpers (viewer-aware) ---

def get_parents(person: Person) -> List[Person]:
    if not person.parent_names:
        return []
    return [p for p in people if p.name in person.parent_names]

def get_spouse(person: Person) -> Optional[Person]:
    if not person.spouse_name:
        return None
    return find_person_by_name(person.spouse_name)

def get_siblings(person: Person) -> List[Person]:
    if not person.parent_names:
        return []
    return [
        p
        for p in people
        if p is not person and p.parent_names == person.parent_names
    ]

def describe_relationship(viewer: Person, target: Person) -> str:
    """Return a simple relationship string from viewer to target."""
    if viewer is target:
        return "self"

    parents_v = get_parents(viewer)
    parents_t = get_parents(target)
    children_v = get_children(viewer, people)
    spouse_v = get_spouse(viewer)
    spouse_t = get_spouse(target)

    # Direct parent / child
    if target in parents_v:
        # viewer is child of target
        if target.gender == "Male":
            return "father"
        elif target.gender == "Female":
            return "mother"
        return "parent"
    if viewer in parents_t:
        # target is child of viewer
        if target.gender == "Male":
            return "son"
        elif target.gender == "Female":
            return "daughter"
        return "child"

    # Grandparent / grandchild
    for c in children_v:
        if target in get_children(c, people):
            # target is grandchild of viewer
            if target.gender == "Male":
                return "grandson"
            elif target.gender == "Female":
                return "granddaughter"
            return "grandchild"
    for p in parents_v:
        if target in get_parents(p):
            # target is grandparent of viewer
            if target.gender == "Male":
                return "grandfather"
            elif target.gender == "Female":
                return "grandmother"
            return "grandparent"

    # Sibling
    if parents_v and parents_t and parents_v == parents_t:
        if target.gender == "Male":
            return "brother"
        elif target.gender == "Female":
            return "sister"
        return "sibling"

    # Spouse
    if spouse_v is target:
        if target.gender == "Male":
            return "husband"
        elif target.gender == "Female":
            return "wife"
        return "spouse"

    # Child-in-law (son-in-law / daughter-in-law)
    for c in children_v:
        if spouse_t is c:
            if target.gender == "Male":
                return "son-in-law"
            elif target.gender == "Female":
                return "daughter-in-law"
            return "child-in-law"

    # Sibling-in-law (spouse's siblings OR siblings' spouses)
    if spouse_v:
        # spouse's siblings
        if target in get_siblings(spouse_v):
            if target.gender == "Male":
                return "brother-in-law"
            elif target.gender == "Female":
                return "sister-in-law"
            return "sibling-in-law"
    # siblings' spouses
    for sib in get_siblings(viewer):
        if spouse_t is sib:
            if target.gender == "Male":
                return "brother-in-law"
            elif target.gender == "Female":
                return "sister-in-law"
            return "sibling-in-law"

    # Niece / nephew (child of sibling)
    for sib in get_siblings(viewer):
        if target in get_children(sib, people):
            if target.gender == "Male":
                return "nephew"
            elif target.gender == "Female":
                return "niece"
            return "sibling's child"

    # Aunt / uncle (viewer -> target, i.e., target is sibling of a parent)
    aunts_uncles: List[Person] = []
    for parent in parents_v:
        for sib in get_siblings(parent):
            if sib not in aunts_uncles:
                aunts_uncles.append(sib)
            sp = get_spouse(sib)
            if sp and sp not in aunts_uncles:
                aunts_uncles.append(sp)

    if target in aunts_uncles:
        if target.gender == "Male":
            return "uncle"
        elif target.gender == "Female":
            return "aunt"
        return "aunt/uncle"

    # Cousin (viewer -> target: child of aunt/uncle)
    for au in aunts_uncles:
        if target in get_children(au, people):
            return "cousin"

    # Fallback: unknown / distant relative
    return "relative"

def choose_viewer():
    global current_viewer
    name = input("Who are you? Enter your full name: ").strip()
    person = find_person_by_name(name)
    if not person:
        print(f"No person found with name '{name}'.")
        current_viewer = None
        return
    current_viewer = person
    print(f"Viewer set to {current_viewer.name}.")

# --- Viewer-aware displays ---

def show_person_family():
    name = input("Enter the name of the person: ").strip()
    person = find_person_by_name(name)
    if not person:
        print(f"No person found with name '{name}'.")
        return

    print(f"\nFamily for {person.name}:")

    # Relationship to viewer if set
    if current_viewer:
        rel_text = describe_relationship(current_viewer, person)
        print(f" Relationship to {current_viewer.name}: {rel_text}")

    # Parents
    if person.parent_names:
        print(" Parents:")
        for parent_name in person.parent_names:
            print("  -", parent_name)
    else:
        print(" Parents: (unknown)")

    # Spouse
    if person.spouse_name:
        print(" Spouse:")
        print("  -", person.spouse_name)
    else:
        print(" Spouse: (none recorded)")

    # Children
    children = get_children(person, people)
    if children:
        print(" Children:")
        for child in children:
            print("  -", child.name)
    else:
        print(" Children: (none recorded)")

def list_all_people():
    """List all people sorted by name."""
    if not people:
        print("No people in the family tree.")
        return

    sorted_people = sorted(
        people,
        key=lambda p: (p.name.split()[-1], p.name)
    )

    print("\nAll people in the family tree:")
    for p in sorted_people:
        if current_viewer:
            rel_text = describe_relationship(current_viewer, p)
            rel = f" ({rel_text})"
        else:
            rel = f" ({p.relationship})" if p.relationship else ""
        print(f" - {p.name}{rel}")
    print(f"Total: {len(sorted_people)} people.")

def search_people_by_name():
    """Search people by partial name (case-insensitive)."""
    query = input("Enter part of the name to search for: ").strip().lower()
    if not query:
        print("Search text is empty.")
        return

    matches = [p for p in people if query in p.name.lower()]

    if not matches:
        print("No matches found.")
        return

    print(f"\nMatches for '{query}':")
    for p in matches:
        # Name
        print(p.name)

        # Date + age
        if p.birthday:
            bday_str = p.birthday.isoformat()
            age = get_person_age(p)
            age_str = f"Age: {age}" if age is not None else "Age: unknown"
            print(bday_str)
            print(age_str)
        else:
            print("Birthday: unknown")
            print("Age: unknown")

        # Relationship from current viewer if set
        if current_viewer:
            rel_text = describe_relationship(current_viewer, p)
            print(f"Relationship to {current_viewer.name}: {rel_text}")
        elif p.relationship:
            # Fallback to stored label (your perspective)
            print(f"Stored relationship: {p.relationship}")

        print()  # blank line between people

def list_people_missing_birthdays():
    """List all people who have no birthday recorded."""
    if not people:
        print("No people in the family tree.")
        return

    missing = [p for p in people if p.birthday is None]

    if not missing:
        print("Everyone has a birthday recorded.")
        return

    print("\nPeople without birthdays recorded:")
    for p in missing:
        if current_viewer:
            rel_text = describe_relationship(current_viewer, p)
            rel = f" ({rel_text})"
        else:
            rel = f" ({p.relationship})" if p.relationship else ""
        print(f" - {p.name}{rel}")
    print(f"Total missing birthdays: {len(missing)}")

# --- Menu ---

def main_menu():
    while True:
        print("\n=== Family Tree Menu ===")
        print("0. Set current viewer")
        print("1. List all people")
        print("2. Search people by (partial) name")
        print("3. Show a person's immediate family")
        print("4. List descendants of a person")
        print("5. Show oldest and youngest")
        print("6. Add a new person")
        print("7. Update an existing person")
        print("8. Save to JSON (family.json)")
        print("9. Load from JSON (family.json, replaces current data)")
        print("10. Quit")
        print("11. List people without birthdays")

        choice = input("Choose an option: ").strip()
        if choice == "0":
            choose_viewer()
        elif choice == "1":
            list_all_people()
        elif choice == "2":
            search_people_by_name()
        elif choice == "3":
            show_person_family()
        elif choice == "4":
            list_descendants_of_person()
        elif choice == "5":
            show_oldest_and_youngest()
        elif choice == "6":
            add_person_interactive()
        elif choice == "7":
            update_person_interactive()
        elif choice == "8":
            save_to_json()
        elif choice == "9":
            load_from_json()
        elif choice == "10":
            print("Goodbye.")
            break
        elif choice == "11":
            list_people_missing_birthdays()
        else:
            print("Invalid choice, try again.")

if __name__ == "__main__":
    load_from_json()  # will print a message if file is missing
    main_menu()