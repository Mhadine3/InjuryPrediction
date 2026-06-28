import json

CLUB_DATA = {
    # FRANCE
    "fra_001_brice_samba":          ("RC Lens",           "FRA"),
    "fra_002_mike_maignan":         ("AC Milan",          "ITA"),
    "fra_003_robin_risser":         ("Metz",              "FRA"),
    "fra_004_theo_hernández":  ("AC Milan",          "ITA"),
    "fra_005_lucas_digne":          ("Aston Villa",       "ENG"),
    "fra_006_lucas_hernández": ("PSG",               "FRA"),
    "fra_007_jules_koundé":    ("Barcelona",         "ESP"),
    "fra_008_dayot_upamecano":      ("Bayern Munich",     "GER"),
    "fra_009_ibrahima_konaté": ("Liverpool",         "ENG"),
    "fra_010_william_saliba":       ("Arsenal",           "ENG"),
    "fra_011_maxence_lacroix":      ("Crystal Palace",    "ENG"),
    "fra_012_malo_gusto":           ("Chelsea",           "ENG"),
    "fra_013_ngolo_kanté":     ("Al-Ittihad",        "SAU"),
    "fra_014_adrien_rabiot":        ("Marseille",         "FRA"),
    "fra_015_aurélien_tchouameni": ("Real Madrid",   "ESP"),
    "fra_016_manu_koné":       ("Real Madrid",       "ESP"),
    "fra_017_rayan_cherki":         ("PSG",               "FRA"),
    "fra_018_maghnes_akliouche":    ("Monaco",            "FRA"),
    "fra_019_desire_doue":          ("PSG",               "FRA"),
    "fra_020_warren_zaïre_emery": ("PSG",            "FRA"),
    "fra_021_jean_philippe_mateta": ("Crystal Palace",    "ENG"),
    "fra_022_ousmane_dembélé": ("PSG",          "FRA"),
    "fra_023_kylian_mbappé":   ("Real Madrid",       "ESP"),
    "fra_024_marcus_thuram_ulien":  ("Inter Milan",       "ITA"),
    "fra_025_michael_olise":        ("Bayern Munich",     "GER"),
    "fra_026_bradley_barcola":      ("PSG",               "FRA"),
    # SENEGAL
    "sen_001_edouard_mendy":        ("Al-Ahli",           "SAU"),
    "sen_002_yehvann_diouf":        ("Reims",             "FRA"),
    "sen_003_mory_diaw":            ("Clermont Foot",     "FRA"),
    "sen_004_ismail_jakobs":        ("Monaco",            "FRA"),
    "sen_005_kalidou_koulibaly":    ("Al-Hilal",          "SAU"),
    "sen_006_moussa_niakhaté": ("Nottm Forest",      "ENG"),
    "sen_007_abdoulaye_seck":       ("Watford",           "ENG"),
    "sen_008_antoine_mendy":        ("Strasbourg",        "FRA"),
    "sen_009_mamadou_sarr":         ("Strasbourg",        "FRA"),
    "sen_010_el_hadji_diouf":       ("Guingamp",          "FRA"),
    "sen_011_pape_gueye":           ("Marseille",         "FRA"),
    "sen_012_idrissa_gana_guéye": ("Everton",        "ENG"),
    "sen_013_ismaïla_sarr":    ("Crystal Palace",    "ENG"),
    "sen_014_krépin_diatta":   ("Monaco",            "FRA"),
    "sen_015_pathé_ciss":      ("Rayo Vallecano",    "ESP"),
    "sen_016_iliman_ndiaye":        ("Marseille",         "FRA"),
    "sen_017_pape_sarr":            ("Tottenham",         "ENG"),
    "sen_018_habib_diarra":         ("Strasbourg",        "FRA"),
    "sen_019_lamine_camara":        ("Monaco",            "FRA"),
    "sen_020_bara_ndiaye":          ("Wolverhampton",     "ENG"),
    "sen_021_sadio_mané":      ("Al-Nassr",          "SAU"),
    "sen_022_cherif_ndiaye":        ("Metz",              "FRA"),
    "sen_023_nicolas_jackson":      ("Chelsea",           "ENG"),
    "sen_024_ahmadou_bamba_dieng":  ("Marseille",         "FRA"),
    "sen_025_assane_diao":          ("Real Betis",        "ESP"),
    "sen_026_ibrahim_mbaye":        ("Zulte Waregem",     "BEL"),
    # IRAQ
    "irq_001_jalal_hassan":         ("Al-Zawraa",         "IRQ"),
    "irq_002_fahad_talib":          ("Al-Quwa Al-Jawiya", "IRQ"),
    "irq_003_ahmed_basil":          ("Al-Shorta",         "IRQ"),
    "irq_004_hussein_ali":          ("Al-Zawraa",         "IRQ"),
    "irq_005_merchas_doski":        ("IFK Goteborg",      "SWE"),
    "irq_006_rebin_sulaka":         ("Djurgardens IF",    "SWE"),
    "irq_007_mustafa_saadoun":      ("Al-Naft",           "IRQ"),
    "irq_008_ahmed_yahya":          ("Al-Quwa Al-Jawiya", "IRQ"),
    "irq_009_frans_putros":         ("IFK Norrkoping",    "SWE"),
    "irq_010_zaid_tahseen":         ("Al-Zawraa",         "IRQ"),
    "irq_011_manaf_younis":         ("Al-Talaba",         "IRQ"),
    "irq_012_akam_hashem":          ("Al-Shorta",         "IRQ"),
    "irq_013_amir_al_ammari":       ("Al-Quwa Al-Jawiya", "IRQ"),
    "irq_014_kevin_yakob":          ("Djurgardens IF",    "SWE"),
    "irq_015_aimar_sher":           ("Al-Zawraa",         "IRQ"),
    "irq_016_ibrahim_bayesh":       ("Al-Naft",           "IRQ"),
    "irq_017_youssef_amyn":         ("Al-Talaba",         "IRQ"),
    "irq_018_ahmed_qasem":          ("Al-Shorta",         "IRQ"),
    "irq_019_zidane_iqbal":         ("Utrecht",           "NED"),
    "irq_020_zaid_ismail":          ("Al-Zawraa",         "IRQ"),
    "irq_021_aymen_hussein":        ("Al-Zawraa",         "IRQ"),
    "irq_022_ali_al_hamadi":        ("Charlton Athletic", "ENG"),
    "irq_023_marko_farji":          ("Al-Quwa Al-Jawiya", "IRQ"),
    "irq_024_ali_jassim_el_aibi":   ("Al-Talaba",         "IRQ"),
    "irq_025_mohanad_ali":          ("Al-Naft",           "IRQ"),
    "irq_026_ali_yousif":           ("Al-Shorta",         "IRQ"),
    # NORWAY
    "nor_001_orjan_nyland":         ("Southampton",       "ENG"),
    "nor_002_egil_selvik":          ("Haugesund",         "NOR"),
    "nor_003_sander_tangvik":       ("Tromso IL",         "NOR"),
    "nor_004_kristoffer_ajer":      ("Brentford",         "ENG"),
    "nor_005_torbjorn_heggem":      ("Rosenborg",         "NOR"),
    "nor_006_fredrik_bjorkan":      ("Hibernian",         "SCO"),
    "nor_007_marcus_pedersen":      ("Fiorentina",        "ITA"),
    "nor_008_julian_ryerson":       ("Borussia Dortmund", "GER"),
    "nor_009_leo_ostigard":         ("Napoli",            "ITA"),
    "nor_010_henrik_falchener":     ("Molde FK",          "NOR"),
    "nor_011_sondre_langas":        ("Rosenborg",         "NOR"),
    "nor_012_david_wolfe":          ("SK Brann",          "NOR"),
    "nor_013_morten_thorsby":       ("Union Berlin",      "GER"),
    "nor_014_martin_odegaard":      ("Arsenal",           "ENG"),
    "nor_015_sander_berge":         ("Fulham",            "ENG"),
    "nor_016_patrick_berg":         ("Club Brugge",       "BEL"),
    "nor_017_fredrik_aursnes":      ("Benfica",           "POR"),
    "nor_018_kristian_thorstvedt":  ("Sassuolo",          "ITA"),
    "nor_019_thelo_aasgaard":       ("Wigan Athletic",    "ENG"),
    "nor_020_antonio_nusa":         ("Club Brugge",       "BEL"),
    "nor_021_oscar_bobb":           ("Manchester City",   "ENG"),
    "nor_022_alexander_sorloth":    ("Atletico Madrid",   "ESP"),
    "nor_023_jens_hauge":           ("Genk",              "BEL"),
    "nor_024_erling_haaland":       ("Manchester City",   "ENG"),
    "nor_025_andreas_schjelderup":  ("Benfica",           "POR"),
    "nor_026_jorgen_strand_larsen": ("Wolverhampton",     "ENG"),
}

with open("C:/Users/Mouad/OneDrive/Desktop/Injuryprediction/data/players_baseline.json", encoding="utf-8") as f:
    data = json.load(f)

updated = 0
not_matched = []

for p in data["players"]:
    pid = p["player_id"]
    if pid in CLUB_DATA:
        p["club"], p["league"] = CLUB_DATA[pid]
        updated += 1
    elif p["team_code"] in ("FRA", "SEN", "IRQ", "NOR"):
        not_matched.append(pid)

with open("C:/Users/Mouad/OneDrive/Desktop/Injuryprediction/data/players_baseline.json", "w", encoding="utf-8") as f:
    json.dump(data, f, indent=2, ensure_ascii=False)

print(f"Updated: {updated} players")
if not_matched:
    print(f"Not matched ({len(not_matched)}): {not_matched}")
else:
    print("All Group I players matched.")
