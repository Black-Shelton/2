# -*- coding: utf-8 -*-
"""Данные игры: карты и кейсы. Трогать логику бота тут не нужно — только цифры/названия.

Правило из README: если шанс 0% — карта НЕ выпадает из «эксперимент»/«призыв»,
её могут выдать только админы.
Цена продажи (sell) у новых карт не была указана в README — стоит сила × 3, меняется здесь.
"""

# ============ ОБЫЧНЫЕ КАРТЫ ============
CARDS = [
    {"name": "noob",                          "file": "noob.jpg",                  "rarity": "Обычная",     "chance": 29.9895, "sell": 5,    "power": 1,    "income": 0},
    {"name": "dual sword noob",               "file": "sword_noob.png",            "rarity": "Обычная",     "chance": 29.9895, "sell": 5,    "power": 2,    "income": 0},
    {"name": "big noob",                      "file": "big_noob.jpg",              "rarity": "Редкая",      "chance": 28,      "sell": 10,   "power": 10,   "income": 0},
    {"name": "guest",                         "file": "Guest.jpg",                 "rarity": "Редкая",      "chance": 10,      "sell": 10,   "power": 15,   "income": 0},
    {"name": "Grappler Noob",                 "file": "Grappler_Noob.png",         "rarity": "Эпическая",   "chance": 6,       "sell": 200,  "power": 80,   "income": 0},
    {"name": "Dummy",                         "file": "Dummy.png",                 "rarity": "Эпическая",   "chance": 6,       "sell": 220,  "power": 90,   "income": 0},
    {"name": "titan noob",                    "file": "titan_noob.png",            "rarity": "Мифическая",  "chance": 3,       "sell": 100,  "power": 50,   "income": 0},
    {"name": "titan guest",                   "file": "titan_guest.png",           "rarity": "Мифическая",  "chance": 3,       "sell": 100,  "power": 60,   "income": 0},
    {"name": "Upgraded Big Noob",             "file": "Upgraded_Big_Noob.jpg",     "rarity": "Легендарная", "chance": 4,       "sell": 900,  "power": 300,  "income": 0},
    {"name": "Noob Mech",                     "file": "Noob_Mech.jpg",             "rarity": "Легендарная", "chance": 4,       "sell": 1200, "power": 350,  "income": 0},
    {"name": "Medic And Scout",               "file": "Medic_And_Scout.png",       "rarity": "Мифическая",  "chance": 0.8,     "sell": 2000, "power": 500,  "income": 0},
    {"name": "The Last Guest",                "file": "The_Last_Guest.png",        "rarity": "Мифическая",  "chance": 0.8,     "sell": 2500, "power": 600,  "income": 0},
    {"name": "1x1x1x1",                       "file": "1x1x1x1.jpg",               "rarity": "Секретная",   "chance": 0,       "sell": 3333, "power": 1111, "income": 1111},
    {"name": "Flippidee",                     "file": "Flippidee.png",             "rarity": "Секретная",   "chance": 0.15,    "sell": 5000, "power": 260,  "income": 0},
    {"name": "Grim Ronin",                    "file": "Grim_Ronin.jpg",            "rarity": "Секретная",   "chance": 0,       "sell": 8000, "power": 300,  "income": 0},
    {"name": "Multiverse Upgrade Titan Noob", "file": "Titan_Noob_Multiverse.png", "rarity": "Секретная",   "chance": 0.03,    "sell": 500,  "power": 5000, "income": 0},
]

# ============ ЭКСКЛЮЗИВНЫЕ (из кейсов) ============
# Отдельного кейса для них в Cases.md нет (там только Halloween Crate), поэтому пока их выдают админы.
# Когда будет кейс — добавь его в CASES ниже и положи туда эти карты.
EXCLUSIVE_CARDS = [
    {"name": "Upgrade Large Noob",         "file": "Upgrade_Large_Noob.jpg",         "rarity": "Эксклюзивная", "chance": 28, "sell": 135,  "power": 45,  "income": 0},
    {"name": "Guest Woman",                "file": "Guest_Woman.jpg",                "rarity": "Эксклюзивная", "chance": 21, "sell": 315,  "power": 105, "income": 0},
    {"name": "Spider Noob 2.0",            "file": "Spider_Noob_2.0.jpg",            "rarity": "Эксклюзивная", "chance": 16, "sell": 375,  "power": 125, "income": 0},
    {"name": "Nova Mech",                  "file": "Nova_Mech.jpg",                  "rarity": "Эксклюзивная", "chance": 11, "sell": 570,  "power": 190, "income": 0},
    {"name": "Improved Guest Mech 2.0",    "file": "Improved_Guest_Mech_2.0.jpg",    "rarity": "Эксклюзивная", "chance": 8,  "sell": 660,  "power": 220, "income": 0},
    {"name": "Prototype Mech",             "file": "Prototype_Mech.jpg",             "rarity": "Эксклюзивная", "chance": 7,  "sell": 780,  "power": 260, "income": 0},
    {"name": "Multiverse Titan Guest 2.0", "file": "Multiverse_Titan_Guest_2.0.jpg", "rarity": "Эксклюзивная", "chance": 5,  "sell": 900,  "power": 300, "income": 0},
    {"name": "Wars Upgrade Titan Noob",    "file": "Wars_Upgrade_Titan_Noob.jpg",    "rarity": "Эксклюзивная", "chance": 4,  "sell": 1050, "power": 350, "income": 0},
    {"name": "Engineer",                   "file": "Engineer.jpg",                   "rarity": "Эксклюзивная", "chance": 0,  "sell": 600,  "power": 200, "income": 440},
    {"name": "Chief Engineer",             "file": "Chief_Engineer.jpg",             "rarity": "Эксклюзивная", "chance": 0,  "sell": 1200, "power": 400, "income": 880},
]

# ============ ХЕЛЛОУИН (кейс Halloween Crate) ============
HALLOWEEN_CARDS = [
    {"name": "Big Halloween berserker noob",      "file": "big_Halloween_berserker_noob.jpg",      "rarity": "Halloween", "chance": 12.2, "sell": 3600, "power": 1200, "hp": 1250, "income": 0},
    {"name": "Halloween Experiment Noob Ghost",   "file": "Halloween_Experiment_Noob_Ghost.jpg",   "rarity": "Halloween", "chance": 25,   "sell": 1950, "power": 650,  "hp": 1000, "income": 0},
    {"name": "Halloween guest experiment",        "file": "Halloween_guest_experiment.jpg",        "rarity": "Halloween", "chance": 40,   "sell": 1350, "power": 450,  "hp": 500,  "income": 0},
    {"name": "Mech noob for Halloween",           "file": "Mech_noob_for_Halloween.jpg",           "rarity": "Halloween", "chance": 5,    "sell": 4800, "power": 1600, "hp": 1800, "income": 0},
    {"name": "Halloween titan boss noob",         "file": "Halloween_titan_boss_noob.jpg",         "rarity": "Halloween", "chance": 1,    "sell": 6000, "power": 2000, "hp": 2222, "income": 0},
    {"name": "Mutated titan Halloween guest",     "file": "Mutated_titan_Halloween_guest.jpg",     "rarity": "Halloween", "chance": 0.5,  "sell": 9000, "power": 3000, "hp": 6700, "income": 0},
    {"name": "Big laser noob for Halloween",      "file": "Big_laser_noob_for_Halloween.jpg",      "rarity": "Halloween", "chance": 16,   "sell": 2400, "power": 800,  "hp": 1000, "income": 0},
    {"name": "Mutated noob titan for Halloween",  "file": "Mutated_noob_titan_for_Halloween.jpg",  "rarity": "Halloween", "chance": 0.5,  "sell": 6900, "power": 2300, "hp": 2500, "income": 0},
]

# ============ КЕЙСЫ ============
# Формат: "id_без_подчёркиваний": {"name", "price", "currency": "coins"|"candy", "cards": [...]}
# currency не указан = монеты. Кейс "raid" (если появится) в магазине не показывается — это зашито в логике.
CASES = {
    "halloween": {"name": "Halloween Crate", "price": 200, "currency": "candy", "cards": HALLOWEEN_CARDS},
}

CASE_CARDS = EXCLUSIVE_CARDS + [c for case in CASES.values() for c in case["cards"]]


def find_card_by_name(name: str):
    """Ищет карту по точному названию (админка, продажа, база)."""
    n = name.lower()
    if n == "grim":  # старое название
        n = "grim ronin"
    for c in CARDS + CASE_CARDS:
        if c["name"].lower() == n:
            return c
    return None


def get_income_cards():
    """Карты, которые можно экипировать на базу."""
    return [c for c in CARDS + CASE_CARDS if c.get("income", 0) > 0 or c["name"] == "Grim Ronin"]
