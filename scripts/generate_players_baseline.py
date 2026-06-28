"""
generate_players_baseline.py
============================
Project : 2026 FIFA World Cup Group C — Injury Prediction Platform
Purpose : Generate deterministic baseline physiological and wellness profiles
          for 78 players (Brazil 26 + Haiti 26 + Scotland 26).
          Morocco squad is pending official FIFA confirmation and will be added later.

Scientific Sources
------------------
- Foster (2001): session RPE (sRPE) methodology for training load quantification
- Hooper & Mackinnon (1995): subjective wellness questionnaire
  (fatigue, sleep quality, stress, muscle soreness — 1–7 Likert scale)
- Gabbett (2016): Acute:Chronic Workload Ratio (ACWR) and injury risk models
- Buchheit (2014): HRV monitoring in elite football — age-based normative ranges
- Bradley et al. (2009): GPS-derived positional demands in the Premier League
- Dellal et al. (2010): physical and technical activity demands across competition levels

Usage
-----
    python scripts/generate_players_baseline.py

Output
------
    data/players_baseline.json

Reproducibility
---------------
All numeric fields are produced by distribute_value(), which spreads each metric
evenly across its scientific range using the player's 0-based index within their
national squad. No randomness is used for the player data; random.seed(42) is set
as a precaution in case any downstream extension adds stochastic logic.
"""

import json
import random
import unicodedata
from datetime import date
from pathlib import Path

random.seed(42)

# ─── PATHS ────────────────────────────────────────────────────────────────────

_SCRIPT_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = _SCRIPT_DIR.parent / "data"
OUTPUT_FILE = OUTPUT_DIR / "players_baseline.json"
GENERATOR_VERSION = "1.1.0"

# ─── SCIENTIFIC CONSTANTS ─────────────────────────────────────────────────────

# Buchheit (2014) — HRV (RMSSD, ms) normative ranges by age bracket
HRV_RANGES_BY_AGE: dict[str, tuple[float, float]] = {
    "under_23": (80.0, 92.0),
    "23_29":    (72.0, 88.0),
    "30_33":    (68.0, 82.0),
    "34_plus":  (62.0, 76.0),
}

# Buchheit (2014) — resting heart rate (bpm) by age bracket
RESTING_HR_RANGES_BY_AGE: dict[str, tuple[int, int]] = {
    "under_23": (44, 48),
    "23_29":    (46, 51),
    "30_33":    (48, 53),
    "34_plus":  (49, 56),
}

# Bradley (2009), Dellal (2010) — max sprint speed (km/h) by position
# Age correction applied post-distribution: −0.5 if age ≥ 33; −1.0 if age ≥ 36
SPRINT_SPEED_RANGES_BY_POSITION: dict[str, tuple[float, float]] = {
    "Goalkeeper":    (28.0, 31.5),
    "Center Back":   (31.5, 33.5),
    "Full Back":     (33.0, 35.5),
    "Defensive Mid": (31.5, 33.5),
    "Central Mid":   (32.0, 34.5),
    "Attacking Mid": (32.5, 35.0),
    "Winger":        (34.5, 37.0),
    "Striker":       (33.0, 35.5),
}

# Buchheit (2014) — VO2max (ml/kg/min) by position
# Age correction: −2.0 if age ≥ 33; −3.5 if age ≥ 36
VO2_MAX_RANGES_BY_POSITION: dict[str, tuple[float, float]] = {
    "Goalkeeper":    (52.0, 58.0),
    "Center Back":   (55.0, 60.0),
    "Full Back":     (58.0, 63.0),
    "Defensive Mid": (60.0, 64.0),
    "Central Mid":   (60.5, 65.5),
    "Attacking Mid": (60.0, 65.0),
    "Winger":        (58.5, 63.5),
    "Striker":       (56.5, 62.0),
}

# Bradley (2009) — total distance per match (km) by position
DISTANCE_MATCH_RANGES_BY_POSITION: dict[str, tuple[float, float]] = {
    "Goalkeeper":    (4.0,  5.0),
    "Center Back":   (9.0,  10.0),
    "Full Back":     (10.0, 11.0),
    "Defensive Mid": (10.0, 11.5),
    "Central Mid":   (11.0, 12.5),
    "Attacking Mid": (10.5, 11.8),
    "Winger":        (10.0, 11.0),
    "Striker":       (9.5,  10.5),
}

# Gabbett (2016) — high-intensity distance per match (m, >19.8 km/h) by position
HI_DISTANCE_RANGES_BY_POSITION: dict[str, tuple[int, int]] = {
    "Goalkeeper":    (50,   150),
    "Center Back":   (400,  600),
    "Full Back":     (700,  900),
    "Defensive Mid": (600,  800),
    "Central Mid":   (800,  1100),
    "Attacking Mid": (850,  1100),
    "Winger":        (900,  1200),
    "Striker":       (700,  1000),
}

# Bradley (2009) — sprint count per match (>25.2 km/h) by position
SPRINTS_RANGES_BY_POSITION: dict[str, tuple[int, int]] = {
    "Goalkeeper":    (1,  3),
    "Center Back":   (5,  10),
    "Full Back":     (15, 25),
    "Defensive Mid": (8,  15),
    "Central Mid":   (12, 20),
    "Attacking Mid": (15, 22),
    "Winger":        (20, 30),
    "Striker":       (15, 25),
}

# Dellal (2010) — high-intensity acceleration/deceleration events per match by position
ACCEL_DECEL_RANGES_BY_POSITION: dict[str, tuple[int, int]] = {
    "Goalkeeper":    (15, 25),
    "Center Back":   (40, 55),
    "Full Back":     (55, 70),
    "Defensive Mid": (60, 75),
    "Central Mid":   (70, 85),
    "Attacking Mid": (70, 82),
    "Winger":        (60, 80),
    "Striker":       (55, 70),
}

SCIENTIFIC_SOURCES: list[str] = [
    "Foster 2001 - sRPE methodology",
    "Hooper & Mackinnon 1995 - wellness questionnaire",
    "Gabbett 2016 - ACWR injury risk",
    "Buchheit 2014 - HRV monitoring",
    "Bradley 2009 - GPS positional demands",
    "Dellal 2010 - football match load",
]

# ─── SQUAD DATA ───────────────────────────────────────────────────────────────

BRAZIL_SQUAD: list[dict] = [
    {"pos": "GK", "name": "Alisson",            "dob": "1992-10-02", "age": 33, "caps": 76,  "goals": 0,  "club": "Liverpool",              "league": "ENG", "captain": False},
    {"pos": "GK", "name": "Ederson",            "dob": "1993-08-17", "age": 32, "caps": 31,  "goals": 0,  "club": "Fenerbahçe",             "league": "TUR", "captain": False},
    {"pos": "GK", "name": "Weverton",           "dob": "1987-12-13", "age": 38, "caps": 10,  "goals": 0,  "club": "Grêmio",                "league": "BRA", "captain": False},
    {"pos": "DF", "name": "Marquinhos",         "dob": "1994-05-14", "age": 32, "caps": 104, "goals": 7,  "club": "Paris Saint-Germain",    "league": "FRA", "captain": False},
    {"pos": "DF", "name": "Danilo Luiz",        "dob": "1991-07-15", "age": 34, "caps": 68,  "goals": 1,  "club": "Flamengo",               "league": "BRA", "captain": False},
    {"pos": "DF", "name": "Alex Sandro",        "dob": "1991-01-26", "age": 35, "caps": 43,  "goals": 2,  "club": "Flamengo",               "league": "BRA", "captain": False},
    {"pos": "DF", "name": "Gabriel Magalhães",  "dob": "1997-12-19", "age": 28, "caps": 17,  "goals": 1,  "club": "Arsenal",                "league": "ENG", "captain": False},
    {"pos": "DF", "name": "Bremer",             "dob": "1997-03-18", "age": 29, "caps": 6,   "goals": 1,  "club": "Juventus",               "league": "ITA", "captain": False},
    {"pos": "DF", "name": "Wesley",             "dob": "2003-09-06", "age": 22, "caps": 6,   "goals": 0,  "club": "Roma",                   "league": "ITA", "captain": False},
    {"pos": "DF", "name": "Roger Ibañez",       "dob": "1998-11-23", "age": 27, "caps": 5,   "goals": 0,  "club": "Al-Ahli",                "league": "SAU", "captain": False},
    {"pos": "DF", "name": "Douglas Santos",     "dob": "1994-03-22", "age": 32, "caps": 5,   "goals": 0,  "club": "Zenit Saint Petersburg", "league": "RUS", "captain": False},
    {"pos": "DF", "name": "Léo Pereira",        "dob": "1996-01-31", "age": 30, "caps": 2,   "goals": 0,  "club": "Flamengo",               "league": "BRA", "captain": False},
    {"pos": "MF", "name": "Casemiro",           "dob": "1992-02-23", "age": 34, "caps": 84,  "goals": 8,  "club": "Manchester United",      "league": "ENG", "captain": True},
    {"pos": "MF", "name": "Lucas Paquetá",      "dob": "1997-08-27", "age": 28, "caps": 61,  "goals": 12, "club": "Flamengo",               "league": "BRA", "captain": False},
    {"pos": "MF", "name": "Bruno Guimarães",    "dob": "1997-11-16", "age": 28, "caps": 41,  "goals": 2,  "club": "Newcastle United",       "league": "ENG", "captain": False},
    {"pos": "MF", "name": "Fabinho",            "dob": "1993-10-23", "age": 32, "caps": 31,  "goals": 0,  "club": "Al-Ittihad",             "league": "SAU", "captain": False},
    {"pos": "MF", "name": "Danilo Santos",      "dob": "2001-04-29", "age": 25, "caps": 2,   "goals": 1,  "club": "Botafogo",               "league": "BRA", "captain": False},
    {"pos": "FW", "name": "Neymar",             "dob": "1992-02-05", "age": 34, "caps": 128, "goals": 79, "club": "Santos",                 "league": "BRA", "captain": False},
    {"pos": "FW", "name": "Vinícius Júnior",    "dob": "2000-07-12", "age": 25, "caps": 47,  "goals": 8,  "club": "Real Madrid",            "league": "ESP", "captain": False},
    {"pos": "FW", "name": "Raphinha",           "dob": "1996-12-14", "age": 29, "caps": 37,  "goals": 11, "club": "Barcelona",              "league": "ESP", "captain": False},
    {"pos": "FW", "name": "Gabriel Martinelli", "dob": "2001-06-18", "age": 24, "caps": 22,  "goals": 4,  "club": "Arsenal",                "league": "ENG", "captain": False},
    {"pos": "FW", "name": "Matheus Cunha",      "dob": "1999-05-27", "age": 27, "caps": 21,  "goals": 1,  "club": "Manchester United",      "league": "ENG", "captain": False},
    {"pos": "FW", "name": "Endrick",            "dob": "2006-07-21", "age": 19, "caps": 15,  "goals": 3,  "club": "Lyon",                   "league": "FRA", "captain": False},
    {"pos": "FW", "name": "Luiz Henrique",      "dob": "2001-01-02", "age": 25, "caps": 13,  "goals": 2,  "club": "Zenit Saint Petersburg", "league": "RUS", "captain": False},
    {"pos": "FW", "name": "Igor Thiago",        "dob": "2001-06-26", "age": 24, "caps": 2,   "goals": 1,  "club": "Brentford",              "league": "ENG", "captain": False},
    {"pos": "FW", "name": "Rayan",              "dob": "2006-08-03", "age": 19, "caps": 1,   "goals": 0,  "club": "Bournemouth",            "league": "ENG", "captain": False},
]

HAITI_SQUAD: list[dict] = [
    {"pos": "GK", "name": "Johny Placide",         "dob": "1988-01-29", "age": 38, "caps": 79, "goals": 0,  "club": "Bastia",                      "league": "FRA", "captain": True},
    {"pos": "GK", "name": "Alexandre Pierre",      "dob": "2001-02-25", "age": 25, "caps": 14, "goals": 0,  "club": "Sochaux",                     "league": "FRA", "captain": False},
    {"pos": "GK", "name": "Josué Duverger",        "dob": "2000-04-27", "age": 26, "caps": 6,  "goals": 0,  "club": "Cosmos Koblenz",              "league": "GER", "captain": False},
    {"pos": "DF", "name": "Ricardo Adé",           "dob": "1990-05-21", "age": 36, "caps": 57, "goals": 2,  "club": "LDU Quito",                   "league": "ECU", "captain": False},
    {"pos": "DF", "name": "Carlens Arcus",         "dob": "1996-06-28", "age": 29, "caps": 51, "goals": 1,  "club": "Angers",                      "league": "FRA", "captain": False},
    {"pos": "DF", "name": "Martin Expérience",     "dob": "1999-03-09", "age": 27, "caps": 19, "goals": 0,  "club": "Nancy",                       "league": "FRA", "captain": False},
    {"pos": "DF", "name": "Jean-Kévin Duverne",    "dob": "1997-07-12", "age": 28, "caps": 15, "goals": 1,  "club": "Gent",                        "league": "BEL", "captain": False},
    {"pos": "DF", "name": "Duke Lacroix",          "dob": "1993-10-14", "age": 32, "caps": 14, "goals": 2,  "club": "Colorado Springs Switchbacks", "league": "USA", "captain": False},
    {"pos": "DF", "name": "Wilguens Paugain",      "dob": "2001-08-24", "age": 24, "caps": 6,  "goals": 0,  "club": "Zulte Waregem",               "league": "BEL", "captain": False},
    {"pos": "DF", "name": "Hannes Delcroix",       "dob": "1999-02-28", "age": 27, "caps": 5,  "goals": 0,  "club": "Lugano",                      "league": "SUI", "captain": False},
    {"pos": "DF", "name": "Keeto Thermoncy",       "dob": "2006-03-29", "age": 20, "caps": 1,  "goals": 0,  "club": "Young Boys",                  "league": "SUI", "captain": False},
    {"pos": "MF", "name": "Leverton Pierre",       "dob": "1998-03-09", "age": 28, "caps": 33, "goals": 0,  "club": "Vizela",                      "league": "POR", "captain": False},
    {"pos": "MF", "name": "Danley Jean Jacques",   "dob": "2000-05-20", "age": 26, "caps": 28, "goals": 6,  "club": "Philadelphia Union",           "league": "USA", "captain": False},
    {"pos": "MF", "name": "Carl Sainté",           "dob": "2002-08-09", "age": 23, "caps": 25, "goals": 0,  "club": "El Paso Locomotive FC",       "league": "USA", "captain": False},
    {"pos": "MF", "name": "Jean-Ricner Bellegarde","dob": "1998-06-27", "age": 27, "caps": 8,  "goals": 0,  "club": "Wolverhampton Wanderers",      "league": "ENG", "captain": False},
    {"pos": "MF", "name": "Woodensky Pierre",      "dob": "2004-12-30", "age": 21, "caps": 1,  "goals": 0,  "club": "Violette",                    "league": "HAI", "captain": False},
    {"pos": "MF", "name": "Dominique Simon",       "dob": "2000-07-29", "age": 25, "caps": 0,  "goals": 0,  "club": "Tatran Prešov",               "league": "SVK", "captain": False},
    {"pos": "FW", "name": "Duckens Nazon",         "dob": "1994-04-07", "age": 32, "caps": 76, "goals": 44, "club": "Esteghlal",                   "league": "IRN", "captain": False},
    {"pos": "FW", "name": "Frantzdy Pierrot",      "dob": "1995-03-29", "age": 31, "caps": 49, "goals": 33, "club": "Çaykur Rizespor",             "league": "TUR", "captain": False},
    {"pos": "FW", "name": "Derrick Etienne Jr.",   "dob": "1996-11-25", "age": 29, "caps": 46, "goals": 8,  "club": "Toronto FC",                  "league": "CAN", "captain": False},
    {"pos": "FW", "name": "Louicius Deedson",      "dob": "2001-02-11", "age": 25, "caps": 30, "goals": 10, "club": "FC Dallas",                   "league": "USA", "captain": False},
    {"pos": "FW", "name": "Ruben Providence",      "dob": "2001-07-07", "age": 24, "caps": 13, "goals": 2,  "club": "Almere City",                 "league": "NED", "captain": False},
    {"pos": "FW", "name": "Josué Casimir",         "dob": "2001-09-24", "age": 24, "caps": 5,  "goals": 0,  "club": "Auxerre",                     "league": "FRA", "captain": False},
    {"pos": "FW", "name": "Yassin Fortuné",        "dob": "1999-01-30", "age": 27, "caps": 3,  "goals": 0,  "club": "Vizela",                      "league": "POR", "captain": False},
    {"pos": "FW", "name": "Wilson Isidor",         "dob": "2000-08-27", "age": 25, "caps": 2,  "goals": 1,  "club": "Sunderland",                  "league": "ENG", "captain": False},
    {"pos": "FW", "name": "Lenny Joseph",          "dob": "2000-10-12", "age": 25, "caps": 0,  "goals": 0,  "club": "Ferencváros",                 "league": "HUN", "captain": False},
]

SCOTLAND_SQUAD: list[dict] = [
    {"pos": "GK", "name": "Craig Gordon",       "dob": "1982-12-31", "age": 43, "caps": 83, "goals": 0,  "club": "Heart of Midlothian", "league": "SCO", "captain": False},
    {"pos": "GK", "name": "Angus Gunn",         "dob": "1996-01-22", "age": 30, "caps": 21, "goals": 0,  "club": "Nottingham Forest",   "league": "ENG", "captain": False},
    {"pos": "GK", "name": "Liam Kelly",         "dob": "1996-01-23", "age": 30, "caps": 2,  "goals": 0,  "club": "Rangers",             "league": "SCO", "captain": False},
    {"pos": "DF", "name": "Andy Robertson",     "dob": "1994-03-11", "age": 32, "caps": 92, "goals": 4,  "club": "Liverpool",           "league": "ENG", "captain": True},
    {"pos": "DF", "name": "Grant Hanley",       "dob": "1991-11-20", "age": 34, "caps": 66, "goals": 2,  "club": "Hibernian",           "league": "SCO", "captain": False},
    {"pos": "DF", "name": "Kieran Tierney",     "dob": "1997-06-05", "age": 29, "caps": 55, "goals": 2,  "club": "Celtic",              "league": "SCO", "captain": False},
    {"pos": "DF", "name": "Scott McKenna",      "dob": "1996-11-12", "age": 29, "caps": 49, "goals": 1,  "club": "Dinamo Zagreb",       "league": "CRO", "captain": False},
    {"pos": "DF", "name": "Jack Hendry",        "dob": "1995-05-07", "age": 31, "caps": 37, "goals": 3,  "club": "Al-Ettifaq",          "league": "SAU", "captain": False},
    {"pos": "DF", "name": "Nathan Patterson",   "dob": "2001-10-16", "age": 24, "caps": 25, "goals": 1,  "club": "Everton",             "league": "ENG", "captain": False},
    {"pos": "DF", "name": "Anthony Ralston",    "dob": "1998-11-16", "age": 27, "caps": 25, "goals": 1,  "club": "Celtic",              "league": "SCO", "captain": False},
    {"pos": "DF", "name": "John Souttar",       "dob": "1996-09-25", "age": 29, "caps": 22, "goals": 2,  "club": "Rangers",             "league": "SCO", "captain": False},
    {"pos": "DF", "name": "Aaron Hickey",       "dob": "2002-06-10", "age": 24, "caps": 19, "goals": 0,  "club": "Brentford",           "league": "ENG", "captain": False},
    {"pos": "DF", "name": "Dominic Hyam",       "dob": "1995-12-20", "age": 30, "caps": 2,  "goals": 0,  "club": "Wrexham",             "league": "WAL", "captain": False},
    {"pos": "MF", "name": "John McGinn",        "dob": "1994-10-18", "age": 31, "caps": 85, "goals": 20, "club": "Aston Villa",         "league": "ENG", "captain": False},
    {"pos": "MF", "name": "Scott McTominay",    "dob": "1996-12-08", "age": 29, "caps": 69, "goals": 14, "club": "Napoli",              "league": "ITA", "captain": False},
    {"pos": "MF", "name": "Ryan Christie",      "dob": "1995-02-22", "age": 31, "caps": 66, "goals": 9,  "club": "Bournemouth",         "league": "ENG", "captain": False},
    {"pos": "MF", "name": "Kenny McLean",       "dob": "1992-01-08", "age": 34, "caps": 56, "goals": 3,  "club": "Norwich City",        "league": "ENG", "captain": False},
    {"pos": "MF", "name": "Billy Gilmour",      "dob": "2001-06-11", "age": 25, "caps": 45, "goals": 2,  "club": "Napoli",              "league": "ITA", "captain": False},
    {"pos": "MF", "name": "Lewis Ferguson",     "dob": "1999-08-24", "age": 26, "caps": 23, "goals": 1,  "club": "Bologna",             "league": "ITA", "captain": False},
    {"pos": "MF", "name": "Ben Gannon-Doak",    "dob": "2005-11-11", "age": 20, "caps": 12, "goals": 1,  "club": "Bournemouth",         "league": "ENG", "captain": False},
    {"pos": "MF", "name": "Findlay Curtis",     "dob": "2006-06-09", "age": 20, "caps": 1,  "goals": 0,  "club": "Kilmarnock",          "league": "SCO", "captain": False},
    {"pos": "FW", "name": "Lyndon Dykes",       "dob": "1995-10-07", "age": 30, "caps": 50, "goals": 10, "club": "Charlton Athletic",   "league": "ENG", "captain": False},
    {"pos": "FW", "name": "Ché Adams",          "dob": "1996-07-13", "age": 29, "caps": 46, "goals": 11, "club": "Torino",              "league": "ITA", "captain": False},
    {"pos": "FW", "name": "Lawrence Shankland", "dob": "1995-08-10", "age": 30, "caps": 18, "goals": 4,  "club": "Heart of Midlothian", "league": "SCO", "captain": False},
    {"pos": "FW", "name": "George Hirst",       "dob": "1999-02-15", "age": 27, "caps": 8,  "goals": 1,  "club": "Ipswich Town",        "league": "ENG", "captain": False},
    {"pos": "FW", "name": "Ross Stewart",       "dob": "1996-07-11", "age": 29, "caps": 2,  "goals": 0,  "club": "Southampton",         "league": "ENG", "captain": False},
]

MOROCCO_SQUAD: list[dict] = [
    {"pos": "GK", "name": "Yassine Bounou",        "dob": "1991-04-05", "age": 35, "caps": 89, "goals": 0,  "club": "Al-Hilal",             "league": "SAU", "captain": False},
    {"pos": "GK", "name": "Munir Mohamedi",        "dob": "1989-05-10", "age": 37, "caps": 50, "goals": 0,  "club": "RS Berkane",           "league": "MAR", "captain": False},
    {"pos": "GK", "name": "Ahmed Reda Tagnaouti",  "dob": "1996-04-05", "age": 30, "caps": 3,  "goals": 0,  "club": "AS FAR",               "league": "MAR", "captain": False},
    {"pos": "DF", "name": "Achraf Hakimi",         "dob": "1998-11-04", "age": 27, "caps": 95, "goals": 11, "club": "Paris Saint-Germain",  "league": "FRA", "captain": True},
    {"pos": "DF", "name": "Nayef Aguerd",          "dob": "1996-03-30", "age": 30, "caps": 64, "goals": 2,  "club": "Marseille",            "league": "FRA", "captain": False},
    {"pos": "DF", "name": "Noussair Mazraoui",     "dob": "1997-11-14", "age": 28, "caps": 43, "goals": 2,  "club": "Manchester United",    "league": "ENG", "captain": False},
    {"pos": "DF", "name": "Youssef Belammari",     "dob": "1998-09-20", "age": 27, "caps": 8,  "goals": 0,  "club": "Al Ahly",              "league": "EGY", "captain": False},
    {"pos": "DF", "name": "Anass Salah-Eddine",    "dob": "2002-01-18", "age": 24, "caps": 8,  "goals": 0,  "club": "PSV",                  "league": "NED", "captain": False},
    {"pos": "DF", "name": "Chadi Riad",            "dob": "2003-06-17", "age": 22, "caps": 4,  "goals": 1,  "club": "Crystal Palace",       "league": "ENG", "captain": False},
    {"pos": "DF", "name": "Issa Diop",             "dob": "1997-01-09", "age": 29, "caps": 2,  "goals": 0,  "club": "Fulham",               "league": "ENG", "captain": False},
    {"pos": "DF", "name": "Zakaria El Ouahdi",     "dob": "2001-12-31", "age": 24, "caps": 2,  "goals": 0,  "club": "Genk",                 "league": "BEL", "captain": False},
    {"pos": "DF", "name": "Redouane Halhal",       "dob": "2003-03-05", "age": 23, "caps": 1,  "goals": 0,  "club": "Mechelen",             "league": "BEL", "captain": False},
    {"pos": "MF", "name": "Sofyan Amrabat",        "dob": "1996-08-21", "age": 29, "caps": 73, "goals": 0,  "club": "Real Betis",           "league": "ESP", "captain": False},
    {"pos": "MF", "name": "Azzedine Ounahi",       "dob": "2000-04-19", "age": 26, "caps": 47, "goals": 9,  "club": "Girona",               "league": "ESP", "captain": False},
    {"pos": "MF", "name": "Bilal El Khannouss",    "dob": "2004-05-10", "age": 22, "caps": 35, "goals": 3,  "club": "VfB Stuttgart",        "league": "GER", "captain": False},
    {"pos": "MF", "name": "Ismael Saibari",        "dob": "2001-01-28", "age": 25, "caps": 27, "goals": 7,  "club": "PSV",                  "league": "NED", "captain": False},
    {"pos": "MF", "name": "Neil El Aynaoui",       "dob": "2001-07-02", "age": 24, "caps": 15, "goals": 2,  "club": "Roma",                 "league": "ITA", "captain": False},
    {"pos": "MF", "name": "Samir El Mourabet",     "dob": "2006-08-06", "age": 19, "caps": 2,  "goals": 0,  "club": "Strasbourg",           "league": "FRA", "captain": False},
    {"pos": "MF", "name": "Ayyoub Bouaddi",        "dob": "2007-10-02", "age": 18, "caps": 0,  "goals": 0,  "club": "Lille",                "league": "FRA", "captain": False},
    {"pos": "FW", "name": "Ayoub El Kaabi",        "dob": "1993-06-25", "age": 32, "caps": 55, "goals": 20, "club": "Olympiakos",           "league": "GRE", "captain": False},
    {"pos": "FW", "name": "Abde Ezzalzouli",       "dob": "2001-12-17", "age": 24, "caps": 35, "goals": 2,  "club": "Real Betis",           "league": "ESP", "captain": False},
    {"pos": "FW", "name": "Soufiane Rahimi",       "dob": "1996-06-02", "age": 30, "caps": 25, "goals": 5,  "club": "Al Ain",               "league": "UAE", "captain": False},
    {"pos": "FW", "name": "Brahim Diaz",           "dob": "1999-08-03", "age": 26, "caps": 24, "goals": 13, "club": "Real Madrid",          "league": "ESP", "captain": False},
    {"pos": "FW", "name": "Chemsdine Talbi",       "dob": "2005-05-09", "age": 21, "caps": 5,  "goals": 0,  "club": "Sunderland",           "league": "ENG", "captain": False},
    {"pos": "FW", "name": "Gessime Yassine",       "dob": "2005-11-22", "age": 20, "caps": 2,  "goals": 0,  "club": "Strasbourg",           "league": "FRA", "captain": False},
    {"pos": "FW", "name": "Ayoube Amaimouni",      "dob": "2004-11-30", "age": 21, "caps": 0,  "goals": 0,  "club": "Eintracht Frankfurt",  "league": "GER", "captain": False},
]

# ─── POSITION DETAIL LOOKUP ───────────────────────────────────────────────────

POSITION_DETAIL: dict[str, str] = {
    # Goalkeepers
    "Alisson": "Goalkeeper", "Ederson": "Goalkeeper", "Weverton": "Goalkeeper",
    "Johny Placide": "Goalkeeper", "Alexandre Pierre": "Goalkeeper", "Josué Duverger": "Goalkeeper",
    "Craig Gordon": "Goalkeeper", "Angus Gunn": "Goalkeeper", "Liam Kelly": "Goalkeeper",
    # Center Backs
    "Marquinhos": "Center Back", "Gabriel Magalhães": "Center Back", "Bremer": "Center Back",
    "Roger Ibañez": "Center Back", "Léo Pereira": "Center Back",
    "Ricardo Adé": "Center Back", "Martin Expérience": "Center Back",
    "Hannes Delcroix": "Center Back", "Keeto Thermoncy": "Center Back",
    "Grant Hanley": "Center Back", "Scott McKenna": "Center Back",
    "John Souttar": "Center Back", "Dominic Hyam": "Center Back",
    # Full Backs
    "Danilo Luiz": "Full Back", "Alex Sandro": "Full Back", "Wesley": "Full Back",
    "Douglas Santos": "Full Back", "Carlens Arcus": "Full Back",
    "Jean-Kévin Duverne": "Full Back", "Duke Lacroix": "Full Back",
    "Wilguens Paugain": "Full Back", "Andy Robertson": "Full Back",
    "Kieran Tierney": "Full Back", "Jack Hendry": "Full Back",
    "Nathan Patterson": "Full Back", "Anthony Ralston": "Full Back", "Aaron Hickey": "Full Back",
    # Defensive Mids
    "Casemiro": "Defensive Mid", "Fabinho": "Defensive Mid", "Bruno Guimarães": "Defensive Mid",
    "Leverton Pierre": "Defensive Mid", "Carl Sainté": "Defensive Mid",
    "Woodensky Pierre": "Defensive Mid", "Scott McTominay": "Defensive Mid",
    "Kenny McLean": "Defensive Mid", "Billy Gilmour": "Defensive Mid", "Findlay Curtis": "Defensive Mid",
    # Central Mids
    "Lucas Paquetá": "Central Mid", "Danilo Santos": "Central Mid",
    "Danley Jean Jacques": "Central Mid", "Dominique Simon": "Central Mid",
    "Jean-Ricner Bellegarde": "Central Mid", "John McGinn": "Central Mid", "Lewis Ferguson": "Central Mid",
    # Attacking Mids
    "Ryan Christie": "Attacking Mid",
    # Wingers
    "Vinícius Júnior": "Winger", "Raphinha": "Winger", "Gabriel Martinelli": "Winger",
    "Luiz Henrique": "Winger", "Rayan": "Winger", "Derrick Etienne Jr.": "Winger",
    "Louicius Deedson": "Winger", "Ruben Providence": "Winger",
    "Yassin Fortuné": "Winger", "Ben Gannon-Doak": "Winger",
    # Strikers
    "Neymar": "Striker", "Matheus Cunha": "Striker", "Endrick": "Striker", "Igor Thiago": "Striker",
    "Duckens Nazon": "Striker", "Frantzdy Pierrot": "Striker", "Josué Casimir": "Striker",
    "Wilson Isidor": "Striker", "Lenny Joseph": "Striker",
    "Lyndon Dykes": "Striker", "Ché Adams": "Striker", "Lawrence Shankland": "Striker",
    "George Hirst": "Striker", "Ross Stewart": "Striker",
    # Goalkeepers (Morocco)
    "Yassine Bounou": "Goalkeeper", "Munir Mohamedi": "Goalkeeper", "Ahmed Reda Tagnaouti": "Goalkeeper",
    # Center Backs (Morocco)
    "Nayef Aguerd": "Center Back", "Chadi Riad": "Center Back",
    "Issa Diop": "Center Back", "Redouane Halhal": "Center Back",
    # Full Backs (Morocco)
    "Achraf Hakimi": "Full Back", "Noussair Mazraoui": "Full Back",
    "Youssef Belammari": "Full Back", "Anass Salah-Eddine": "Full Back", "Zakaria El Ouahdi": "Full Back",
    # Defensive Mids (Morocco)
    "Sofyan Amrabat": "Defensive Mid", "Neil El Aynaoui": "Defensive Mid",
    # Central Mids (Morocco)
    "Azzedine Ounahi": "Central Mid", "Ayyoub Bouaddi": "Central Mid",
    # Attacking Mids (Morocco)
    "Bilal El Khannouss": "Attacking Mid", "Samir El Mourabet": "Attacking Mid",
    # Wingers (Morocco)
    "Ismael Saibari": "Winger", "Abde Ezzalzouli": "Winger",
    "Brahim Diaz": "Winger", "Chemsdine Talbi": "Winger", "Gessime Yassine": "Winger",
    # Strikers (Morocco)
    "Ayoub El Kaabi": "Striker", "Soufiane Rahimi": "Striker", "Ayoube Amaimouni": "Striker",
}

# ─── HELPER FUNCTIONS ─────────────────────────────────────────────────────────


def get_hrv_age_key(age: int) -> str:
    if age < 23:
        return "under_23"
    elif age <= 29:
        return "23_29"
    elif age <= 33:
        return "30_33"
    return "34_plus"


def get_age_category(age: int) -> str:
    if age < 23:
        return "young"
    elif age <= 29:
        return "prime"
    return "veteran"


def get_experience_level(caps: int) -> str:
    if caps < 10:
        return "rookie"
    elif caps < 40:
        return "regular"
    elif caps < 80:
        return "veteran"
    return "legend"


def get_recovery_speed(age: int) -> str:
    if age < 25:
        return "fast"
    elif age <= 33:
        return "medium"
    return "slow"


def get_mental_resilience(caps: int, is_captain: bool) -> str:
    if is_captain or caps >= 50:
        return "high"
    elif caps >= 15:
        return "medium"
    return "low"


def get_injury_proneness(age: int, caps: int) -> str:
    if age >= 34:
        return "high"
    elif age < 23 and caps < 20:
        return "low"
    return "medium"


def distribute_value(
    min_val: float,
    max_val: float,
    index: int,
    total_in_group: int,
    decimals: int = 1,
) -> float:
    """Spread values evenly across range based on position in group."""
    if total_in_group == 1:
        return round((min_val + max_val) / 2, decimals)
    fraction = index / (total_in_group - 1)
    value = min_val + fraction * (max_val - min_val)
    return round(value, decimals)


def get_surname_slug(name: str) -> str:
    """Return accent-stripped lowercase slug from the last token of the player name."""
    single_names = {
        "Casemiro", "Neymar", "Rayan", "Endrick", "Bremer",
        "Marquinhos", "Fabinho", "Raphinha", "Alisson", "Ederson",
        "Weverton", "Wesley",
    }
    if name in single_names:
        return name.lower()
    parts = name.split()
    surname = parts[-1]
    normalized = unicodedata.normalize("NFKD", surname)
    ascii_str = normalized.encode("ascii", "ignore").decode("ascii")
    return "".join(c for c in ascii_str.lower() if c.isalnum())


def make_player_id(team_code: str, index_1based: int, name: str) -> str:
    return f"{team_code.lower()}_{index_1based:03d}_{get_surname_slug(name)}"


def compute_hrv(age: int, idx: int, total: int) -> float:
    lo, hi = HRV_RANGES_BY_AGE[get_hrv_age_key(age)]
    return distribute_value(lo, hi, idx, total, decimals=1)


def compute_resting_hr(age: int, idx: int, total: int) -> int:
    lo, hi = RESTING_HR_RANGES_BY_AGE[get_hrv_age_key(age)]
    return int(distribute_value(lo, hi, idx, total, decimals=0))


def compute_sprint_speed(position_detail: str, age: int, idx: int, total: int) -> float:
    lo, hi = SPRINT_SPEED_RANGES_BY_POSITION[position_detail]
    base = distribute_value(lo, hi, idx, total, decimals=1)
    if age >= 36:
        base = round(base - 1.0, 1)
    elif age >= 33:
        base = round(base - 0.5, 1)
    return base


def compute_vo2_max(position_detail: str, age: int, idx: int, total: int) -> float:
    lo, hi = VO2_MAX_RANGES_BY_POSITION[position_detail]
    base = distribute_value(lo, hi, idx, total, decimals=1)
    if age >= 36:
        base = round(base - 3.5, 1)
    elif age >= 33:
        base = round(base - 2.0, 1)
    return base


def compute_distance_per_match(position_detail: str, idx: int, total: int) -> float:
    lo, hi = DISTANCE_MATCH_RANGES_BY_POSITION[position_detail]
    return distribute_value(lo, hi, idx, total, decimals=1)


def compute_hi_distance(position_detail: str, idx: int, total: int) -> int:
    lo, hi = HI_DISTANCE_RANGES_BY_POSITION[position_detail]
    return int(distribute_value(lo, hi, idx, total, decimals=0))


def compute_sprints(position_detail: str, idx: int, total: int) -> int:
    lo, hi = SPRINTS_RANGES_BY_POSITION[position_detail]
    return int(distribute_value(lo, hi, idx, total, decimals=0))


def compute_accel_decel(position_detail: str, idx: int, total: int) -> int:
    lo, hi = ACCEL_DECEL_RANGES_BY_POSITION[position_detail]
    return int(distribute_value(lo, hi, idx, total, decimals=0))


def compute_sleep_duration(age: int, idx: int, total: int) -> float:
    if age < 25:
        lo, hi = 7.8, 8.4
    elif age >= 33:
        lo, hi = 7.2, 7.8
    else:
        lo, hi = 7.5, 8.2
    return distribute_value(lo, hi, idx, total, decimals=1)


def compute_sleep_quality(is_captain: bool, idx: int, total: int) -> float:
    lo, hi = (3.8, 4.2) if is_captain else (3.4, 4.2)
    return distribute_value(lo, hi, idx, total, decimals=2)


def compute_fatigue(age: int, idx: int, total: int) -> float:
    lo, hi = (2.6, 3.0) if age >= 33 else (2.2, 2.6)
    return distribute_value(lo, hi, idx, total, decimals=2)


def compute_soreness(age: int, idx: int, total: int) -> float:
    lo, hi = (2.4, 2.8) if age >= 33 else (1.8, 2.3)
    return distribute_value(lo, hi, idx, total, decimals=2)


def compute_stress(caps: int, is_captain: bool, idx: int, total: int) -> float:
    if is_captain or caps < 10:
        lo, hi = 2.8, 3.2
    elif caps >= 50:
        lo, hi = 2.3, 2.7
    else:
        lo, hi = 2.5, 2.9
    return distribute_value(lo, hi, idx, total, decimals=2)


def build_player_profile(
    player: dict,
    team: str,
    team_code: str,
    idx: int,
    total: int,
) -> dict:
    """Assemble the full baseline profile dict for a single player."""
    name = player["name"]
    age = player["age"]
    caps = player["caps"]
    is_captain = player["captain"]
    pos_detail = POSITION_DETAIL[name]

    return {
        "player_id": make_player_id(team_code, idx + 1, name),
        "name": name,
        "team": team,
        "team_code": team_code,
        "position": player["pos"],
        "position_detail": pos_detail,
        "date_of_birth": player["dob"],
        "age": age,
        "caps": caps,
        "goals": player["goals"],
        "club": player["club"],
        "league": player["league"],
        "is_captain": is_captain,
        "traits": {
            "age_category": get_age_category(age),
            "experience_level": get_experience_level(caps),
            "recovery_speed": get_recovery_speed(age),
            "mental_resilience": get_mental_resilience(caps, is_captain),
            "injury_proneness": get_injury_proneness(age, caps),
        },
        "physiology": {
            "hrv_baseline_ms": compute_hrv(age, idx, total),
            "resting_hr_bpm": compute_resting_hr(age, idx, total),
            "sprint_speed_max_kmh": compute_sprint_speed(pos_detail, age, idx, total),
            "vo2_max_ml_kg_min": compute_vo2_max(pos_detail, age, idx, total),
            "distance_per_match_km": compute_distance_per_match(pos_detail, idx, total),
            "hi_distance_per_match_m": compute_hi_distance(pos_detail, idx, total),
            "sprints_per_match": compute_sprints(pos_detail, idx, total),
            "accel_decel_per_match": compute_accel_decel(pos_detail, idx, total),
        },
        "wellness": {
            "sleep_duration_baseline_h": compute_sleep_duration(age, idx, total),
            "sleep_quality_baseline": compute_sleep_quality(is_captain, idx, total),
            "fatigue_baseline": compute_fatigue(age, idx, total),
            "soreness_baseline": compute_soreness(age, idx, total),
            "stress_baseline": compute_stress(caps, is_captain, idx, total),
        },
    }

# ─── MAIN GENERATION FUNCTION ─────────────────────────────────────────────────


def generate_baseline() -> dict:
    """Generate the complete players_baseline dataset for all three teams."""
    teams = [
        ("Brazil",   "BRA", BRAZIL_SQUAD),
        ("Haiti",    "HAI", HAITI_SQUAD),
        ("Scotland", "SCO", SCOTLAND_SQUAD),
        ("Morocco",  "MAR", MOROCCO_SQUAD),
    ]

    players: list[dict] = []
    team_counts: dict[str, int] = {}

    for team_name, team_code, squad in teams:
        total = len(squad)
        team_counts[team_name] = total
        for i, player_data in enumerate(squad):
            profile = build_player_profile(player_data, team_name, team_code, i, total)
            players.append(profile)

    return {
        "metadata": {
            "generated_at": date.today().isoformat(),
            "generator_version": GENERATOR_VERSION,
            "random_seed": 42,
            "total_players": len(players),
            "teams_included": ["Brazil", "Haiti", "Scotland", "Morocco"],
            "team_counts": team_counts,
            "scientific_sources": SCIENTIFIC_SOURCES,
        },
        "players": players,
    }

# ─── VERIFICATION OUTPUT ──────────────────────────────────────────────────────


def print_verification(data: dict, output_path: Path) -> None:
    players = data["players"]
    meta = data["metadata"]

    print(f"\n{'=' * 52}")
    print("  BASELINE GENERATION COMPLETE")
    print(f"{'=' * 52}")
    print(f"Total players written : {meta['total_players']}")

    print("\nTeam counts:")
    for team, count in meta["team_counts"].items():
        print(f"  {team:<10} : {count}")

    captains = [p for p in players if p["is_captain"]]
    print(f"\nCaptain count : {len(captains)}  (must be 4)")
    print(f"Captains      : {', '.join(c['name'] for c in captains)}")

    print("\nPosition counts per team:")
    for team_name in meta["teams_included"]:
        team_players = [p for p in players if p["team"] == team_name]
        pos_counts: dict[str, int] = {}
        for p in team_players:
            pos = p["position"]
            pos_counts[pos] = pos_counts.get(pos, 0) + 1
        summary = "  ".join(f"{pos}={cnt}" for pos, cnt in sorted(pos_counts.items()))
        print(f"  {team_name:<10} : {summary}")

    size_kb = output_path.stat().st_size / 1024
    print(f"\nOutput : {output_path}  ({size_kb:.1f} KB)")
    print(f"{'=' * 52}\n")

# ─── ENTRY POINT ──────────────────────────────────────────────────────────────


if __name__ == "__main__":
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    data = generate_baseline()
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print_verification(data, OUTPUT_FILE)
