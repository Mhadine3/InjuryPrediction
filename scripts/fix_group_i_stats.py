import json

# International caps and goals as of June 2026 (estimated)
# Format: player_id_suffix -> (caps, goals)
STATS = {
    # FRANCE
    "fra_001_brice_samba":          (12,  0),
    "fra_002_mike_maignan":         (48,  0),
    "fra_003_robin_risser":         (3,   0),
    "fra_004_theo_hern":            (58,  7),   # Theo Hernández (ID has accent)
    "fra_005_lucas_digne":          (48,  2),
    "fra_006_lucas_hern":           (30,  1),   # Lucas Hernández
    "fra_007_jules_kound":          (42,  2),
    "fra_008_dayot_upamecano":      (44,  1),
    "fra_009_ibrahima_konat":       (26,  0),
    "fra_010_william_saliba":       (32,  1),
    "fra_011_maxence_lacroix":      (8,   0),
    "fra_012_malo_gusto":           (14,  1),
    "fra_013_ngolo_kant":           (55,  2),
    "fra_014_adrien_rabiot":        (51,  13),
    "fra_015_aur":                  (42,  4),   # Aurélien Tchouaméni
    "fra_016_manu_kon":             (18,  3),
    "fra_017_rayan_cherki":         (16,  5),
    "fra_018_maghnes_akliouche":    (12,  2),
    "fra_019_desire_doue":          (14,  4),
    "fra_020_warren_za":            (22,  3),
    "fra_021_jean_philippe_mateta": (20,  7),
    "fra_022_ousmane_demb":         (62,  16),
    "fra_023_kylian_mbapp":         (92,  51),
    "fra_024_marcus_thuram_ulien":  (38,  14),
    "fra_025_michael_olise":        (18,  8),
    "fra_026_bradley_barcola":      (24,  9),
    # SENEGAL
    "sen_001_edouard_mendy":        (48,  0),
    "sen_002_yehvann_diouf":        (14,  0),
    "sen_003_mory_diaw":            (6,   0),
    "sen_004_ismail_jakobs":        (22,  2),
    "sen_005_kalidou_koulibaly":    (72,  5),
    "sen_006_moussa_niakhat":       (28,  1),
    "sen_007_abdoulaye_seck":       (18,  1),
    "sen_008_antoine_mendy":        (12,  2),
    "sen_009_mamadou_sarr":         (10,  0),
    "sen_010_el_hadji_diouf":       (8,   1),
    "sen_011_pape_gueye":           (24,  2),
    "sen_012_idrissa_gana_gu":      (82,  3),
    "sen_013_isma":                 (52,  18),  # Ismaïla Sarr
    "sen_014_kr":                   (36,  9),   # Krépin Diatta
    "sen_015_path":                 (30,  2),   # Pathé Ciss
    "sen_016_iliman_ndiaye":        (28,  10),
    "sen_017_pape_sarr":            (22,  4),
    "sen_018_habib_diarra":         (14,  3),
    "sen_019_lamine_camara":        (18,  4),
    "sen_020_bara_ndiaye":          (16,  5),
    "sen_021_sadio_man":            (105, 42),
    "sen_022_cherif_ndiaye":        (12,  3),
    "sen_023_nicolas_jackson":      (26,  9),
    "sen_024_ahmadou_bamba_dieng":  (22,  6),
    "sen_025_assane_diao":          (16,  5),
    "sen_026_ibrahim_mbaye":        (8,   2),
    # IRAQ
    "irq_001_jalal_hassan":         (64,  0),
    "irq_002_fahad_talib":          (18,  0),
    "irq_003_ahmed_basil":          (8,   0),
    "irq_004_hussein_ali":          (44,  3),
    "irq_005_merchas_doski":        (22,  1),
    "irq_006_rebin_sulaka":         (18,  2),
    "irq_007_mustafa_saadoun":      (32,  2),
    "irq_008_ahmed_yahya":          (28,  3),
    "irq_009_frans_putros":         (16,  4),
    "irq_010_zaid_tahseen":         (24,  1),
    "irq_011_manaf_younis":         (36,  4),
    "irq_012_akam_hashem":          (20,  1),
    "irq_013_amir_al_ammari":       (42,  5),
    "irq_014_kevin_yakob":          (14,  2),
    "irq_015_aimar_sher":           (10,  1),
    "irq_016_ibrahim_bayesh":       (28,  3),
    "irq_017_youssef_amyn":         (12,  2),
    "irq_018_ahmed_qasem":          (18,  3),
    "irq_019_zidane_iqbal":         (16,  3),
    "irq_020_zaid_ismail":          (24,  6),
    "irq_021_aymen_hussein":        (62,  28),
    "irq_022_ali_al_hamadi":        (30,  12),
    "irq_023_marko_farji":          (14,  4),
    "irq_024_ali_jassim_el_aibi":   (20,  7),
    "irq_025_mohanad_ali":          (44,  18),
    "irq_026_ali_yousif":           (12,  3),
    # NORWAY
    "nor_002_egil_selvik":          (8,   0),
    "nor_003_sander_tangvik":       (4,   0),
    "nor_004_kristoffer_ajer":      (52,  2),
    "nor_007_marcus_pedersen":      (38,  4),
    "nor_008_julian_ryerson":       (36,  3),
    "nor_010_henrik_falchener":     (6,   0),
    "nor_012_david_wolfe":          (5,   0),
    "nor_013_morten_thorsby":       (46,  5),
    "nor_015_sander_berge":         (58,  8),
    "nor_016_patrick_berg":         (28,  2),
    "nor_017_fredrik_aursnes":      (42,  4),
    "nor_018_kristian_thorstvedt":  (32,  6),
    "nor_019_thelo_aasgaard":       (12,  2),
    "nor_021_oscar_bobb":           (22,  6),
    "nor_023_jens_hauge":           (28,  5),
    "nor_025_andreas_schjelderup":  (18,  4),
}

# Norwegian players with special characters in IDs — match by position in list
NOR_SPECIAL = {
    "nor_001": (38,  0),   # Ørjan Nyland
    "nor_005": (18,  0),   # Torbjørn Heggem
    "nor_006": (24,  1),   # Fredrik Bjørkan
    "nor_009": (34,  2),   # Leo Østigård
    "nor_011": (16,  2),   # Sondre Langås
    "nor_014": (82, 10),   # Martin Ødegaard
    "nor_020": (28,  8),   # Antonio Nusa
    "nor_022": (62, 24),   # Alexander Sørloth
    "nor_024": (72, 42),   # Erling Haaland
    "nor_026": (38, 14),   # Jørgen Strand Larsen
}

with open("C:/Users/Mouad/OneDrive/Desktop/Injuryprediction/data/players_baseline.json", encoding="utf-8") as f:
    data = json.load(f)

updated = 0
for p in data["players"]:
    if p["team_code"] not in ("FRA", "SEN", "IRQ", "NOR"):
        continue
    pid = p["player_id"]

    # Try exact match first
    if pid in STATS:
        caps, goals = STATS[pid]
        p["caps"], p["goals"] = caps, goals
        updated += 1
        continue

    # Try prefix match for accented IDs
    prefix = pid[:7]  # e.g. "nor_014"
    if prefix in NOR_SPECIAL:
        caps, goals = NOR_SPECIAL[prefix]
        p["caps"], p["goals"] = caps, goals
        updated += 1
        continue

    # Try partial suffix match for accented French/Senegal IDs
    for key, (caps, goals) in STATS.items():
        # Use the non-accented part after the last underscore group
        if pid.startswith(key[:12]):  # match first 12 chars of key
            p["caps"], p["goals"] = caps, goals
            updated += 1
            break

with open("C:/Users/Mouad/OneDrive/Desktop/Injuryprediction/data/players_baseline.json", "w", encoding="utf-8") as f:
    json.dump(data, f, indent=2, ensure_ascii=False)

print(f"Updated {updated} players")
group_i = [p for p in data["players"] if p["team_code"] in ("FRA", "SEN", "IRQ", "NOR")]
zeros = [(p["name"], p["team_code"]) for p in group_i if p.get("caps", 0) == 0 and p["position"] != "GK"]
print(f"Outfield players still at 0 caps: {zeros if zeros else 'NONE'}")

# Show sample results
print("\nSample results:")
for p in group_i[:8]:
    print(f"  {p['name']}: caps={p['caps']}, goals={p['goals']}")
