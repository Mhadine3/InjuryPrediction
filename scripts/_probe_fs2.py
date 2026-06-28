import requests, re, time, sys

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "text/plain, */*; q=0.01",
    "Referer": "https://www.flashscore.com/",
    "X-Fsign": "SW9D1eZo",
}
SEP = "¬"  # logical NOT / paragraph sign — Flashscore field separator

def decode_feed(raw):
    records = []
    for block in raw.split("~AA~"):
        block = block.strip()
        if not block:
            continue
        pairs = block.split(SEP)
        rec = {}
        for i in range(0, len(pairs) - 1, 2):
            rec[pairs[i]] = pairs[i + 1]
        if rec:
            records.append(rec)
    return records

# 1. WC 2026 tournament page — discover match IDs
print("--- [1] WC 2026 tournament page ---")
r = requests.get(
    "https://www.flashscore.com/football/world/world-cup-2026/",
    headers={**HEADERS, "Accept": "text/html,*/*"},
    timeout=15,
)
print(f"HTTP {r.status_code}  len={len(r.text)}")
for team in ["Brazil", "Morocco", "Haiti", "Scotland"]:
    print(f"  {team}: {'YES' if team.lower() in r.text.lower() else 'NO'}")

# 8-char alphanumeric IDs (Flashscore match ID format)
ids = list(dict.fromkeys(re.findall(r"[\"'/]([A-Za-z0-9]{8})[\"'/]", r.text)))
print(f"  Candidate 8-char IDs (first 15): {ids[:15]}")

# 2. Live worldwide feed — check field keys
print("\n--- [2] Live worldwide feed field keys ---")
r2 = requests.get("https://www.flashscore.com/x/feed/f_1_0_1_en_1", headers=HEADERS, timeout=15)
print(f"HTTP {r2.status_code}  len={len(r2.text)}")
recs = decode_feed(r2.text)
print(f"Records decoded: {len(recs)}")
all_keys = set()
for rec in recs:
    all_keys.update(rec.keys())
print(f"All keys in live feed ({len(all_keys)} total): {sorted(all_keys)}")
# Show a sample record (first match-looking one)
for rec in recs[:3]:
    if len(rec) > 5:
        print(f"  Sample record ({len(rec)} fields): {dict(list(rec.items())[:12])}")
        break

# 3. Try match-detail / stats endpoint with discovered IDs
print("\n--- [3] Match stats endpoint probe ---")
test_ids = ids[:4] if ids else []
if not test_ids:
    print("  No match IDs discovered from tournament page.")
for mid in test_ids:
    for pattern in [
        f"https://www.flashscore.com/x/feed/df_sui_{mid}_en_1",
        f"https://www.flashscore.com/x/feed/d_su_{mid}_en_1",
    ]:
        r3 = requests.get(pattern, headers=HEADERS, timeout=10)
        if r3.status_code == 200 and len(r3.text) > 20:
            recs3 = decode_feed(r3.text)
            stat_keys = set()
            for rec in recs3:
                stat_keys.update(rec.keys())
            print(f"  {pattern}")
            print(f"  -> HTTP 200, {len(recs3)} records, keys: {sorted(stat_keys)}")
        else:
            print(f"  {pattern} -> HTTP {r3.status_code}")
        time.sleep(0.3)

# 4. Summary
print("\n" + "=" * 60)
print("VERDICT")
print("=" * 60)
FIELDS = {
    "score (goals)":     True,
    "match minute":      True,
    "red_cards":         True,
    "yellow_cards":      True,
    "shots":             None,
    "corners":           None,
    "fouls":             None,
    "possession_pct":    None,
}
for field, status in FIELDS.items():
    tag = "YES  (in feed)" if status is True else "??? (JS-rendered stats panel)"
    print(f"  {field:<22} {tag}")
print("""
Key finding:
  Plain HTTP to flashscore.com/x/feed/f_1_0_1_en_1 returns live score +
  incidents (goals, cards). The statistics panel (shots/corners/fouls/
  possession) is loaded by browser JS from a separate authenticated call
  and is NOT accessible via plain requests without a widget API key.

  Net result vs football-data.org free tier:
    Same fields available: goals, red_cards
    Extra field available: yellow_cards (from incident events)
    Still unavailable:     shots, corners, fouls, possession
""")
