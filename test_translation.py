import sys
import os

# Add the project directory to sys.path
sys.path.append('/home/ubuntu/Deckview')

from hsguru_fetch import translate_deck_name

def test_translation():
    archetypes = {
        "warrior": "Воин",
        "odyn warrior": "Один Воин",
        "mage": "Маг",
        "big spelling mage": "Биг Маг"
    }

    test_cases = [
        ("Warrior", "Воин"),
        ("odyn warrior", "Один Воин"),
        ("Odyn Warrior", "Один Воин"),
        ("Custom Odyn Warrior Deck", "Один Воин"),
        ("Big Spelling Mage", "Биг Маг"),
        ("Unknown Class", "Unknown Class")
    ]

    print("Testing translation logic...")
    for input_name, expected in test_cases:
        result = translate_deck_name(input_name, archetypes)
        status = "PASS" if result == expected else f"FAIL (got {result})"
        print(f"Input: '{input_name}' -> Output: '{result}' | {status}")

if __name__ == "__main__":
    test_translation()
