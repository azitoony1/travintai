#!/usr/bin/env python3
"""
fix_floor_violations.py — One-time correction for base floor violations.

Reads the latest score_history for every country × identity layer, applies
max(identity_score, base_score) for each category, recomputes the total,
and writes a corrected row back to score_history if anything changed.

This is NOT an admin override — it applies the same floor enforcement logic
that tier1_baseline.py now runs at analysis time, retroactively to data
that was stored before the fix was added.

Run once:
    python fix_floor_violations.py
"""

import json
import os
from datetime import datetime, timezone
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()
supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_SERVICE_KEY"))

ALL_CATS = ["armed_conflict", "regional_instability", "terrorism",
            "civil_strife", "crime", "health", "infrastructure"]
SECURITY_CATS = {"armed_conflict", "regional_instability", "terrorism", "civil_strife"}
LVL = {"GREEN": 1, "YELLOW": 2, "ORANGE": 3, "RED": 4, "PURPLE": 5}
ILV = {v: k for k, v in LVL.items()}
IDENTITY_LAYERS = ["jewish_israeli", "solo_women"]


def lvl(score):
    return LVL.get(score, 0)


def calculate_total_score(category_scores):
    """Mirrors tier1_baseline.py calculate_total_score exactly."""
    def s(cat):
        return LVL.get(category_scores.get(cat, "GREEN"), 1)

    # Layer 1: hard veto — armed_conflict PURPLE only
    if s("armed_conflict") >= 5:
        return "PURPLE"

    # Layer 2: weighted average
    weighted_sum = sum(
        s(cat) * (2 if cat in SECURITY_CATS else 1)
        for cat in ALL_CATS
    )
    total_weight = sum(2 if cat in SECURITY_CATS else 1 for cat in ALL_CATS)
    avg = weighted_sum / total_weight

    if avg <= 1.4:   raw = "GREEN"
    elif avg <= 2.4: raw = "YELLOW"
    elif avg <= 3.4: raw = "ORANGE"
    elif avg <= 4.4: raw = "RED"
    else:            raw = "PURPLE"

    # Layer 3: soft floors
    ter = s("terrorism")
    cs  = s("civil_strife")
    max_ter_cs = max(ter, cs)
    if max_ter_cs == 5:   floor = "RED"
    elif max_ter_cs == 4: floor = "ORANGE"
    else:                 floor = "GREEN"

    return ILV[max(LVL[raw], LVL[floor])]


def parse_scores(raw):
    """Parse scores whether stored as dict or JSON string."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except Exception:
            return {}
    return {}


def fetch_latest_per_layer(country_id):
    """Return {layer: row} with the latest score_history row per layer."""
    result = (supabase.table("score_history")
              .select("*")
              .eq("country_id", country_id)
              .order("created_at", desc=True)
              .execute())

    latest = {}
    for row in result.data:
        layer = row["identity_layer"]
        if layer not in latest:
            latest[layer] = row
    return latest


def apply_floor(layer_cats, base_cats):
    """Apply base floor to layer category scores. Returns (corrected_cats, floors_applied)."""
    corrected = dict(layer_cats)
    floors_applied = []
    for cat in ALL_CATS:
        base_val = lvl(base_cats.get(cat, "GREEN"))
        layer_val = lvl(corrected.get(cat, "GREEN"))
        if layer_val < base_val:
            old = corrected.get(cat, "GREEN")
            corrected[cat] = ILV[base_val]
            floors_applied.append(f"{cat}: {old} -> {ILV[base_val]}")
    return corrected, floors_applied


def main():
    print("=" * 60)
    print("  Floor Violation Correction")
    print("=" * 60)

    # Fetch all countries
    countries = supabase.table("countries").select("id, name").execute().data
    print(f"\nProcessing {len(countries)} countries...\n")

    total_corrected = 0
    total_countries = 0

    for country in sorted(countries, key=lambda c: c["name"]):
        name = country["name"]
        cid  = country["id"]

        layers = fetch_latest_per_layer(cid)
        base_row = layers.get("base")
        if not base_row:
            print(f"  {name}: no base layer — skipping")
            continue

        base_cats = parse_scores(base_row.get("scores", {}))
        base_total = base_row.get("total_score", "GREEN")
        country_fixed = False

        for layer in IDENTITY_LAYERS:
            id_row = layers.get(layer)
            if not id_row:
                continue

            id_cats = parse_scores(id_row.get("scores", {}))
            id_total = id_row.get("total_score", "GREEN")

            # Apply floor
            corrected_cats, floors = apply_floor(id_cats, base_cats)
            corrected_total = calculate_total_score(corrected_cats)

            # Also enforce total floor
            if lvl(corrected_total) < lvl(base_total):
                corrected_total = base_total
                floors.append(f"total: {id_total} -> {corrected_total} (total floor)")

            if not floors:
                continue  # Nothing to fix for this country+layer

            print(f"  {name} [{layer}]:")
            for f in floors:
                print(f"    {f}")
            if corrected_total != id_total:
                print(f"    total: {id_total} -> {corrected_total}")

            # Write corrected row to score_history
            new_row = {
                "country_id":     cid,
                "identity_layer": layer,
                "total_score":    corrected_total,
                "scores":         json.dumps(corrected_cats),
                "ai_summary":     id_row.get("ai_summary"),
                "veto_explanation": f"[Floor-corrected {datetime.now(timezone.utc).date()}] " +
                                    (id_row.get("veto_explanation") or ""),
                "recommendations": id_row.get("recommendations"),
                "watch_factors":   id_row.get("watch_factors"),
                "sources":         id_row.get("sources"),
                "confidence":      id_row.get("confidence"),
                "baseline_version_id": id_row.get("baseline_version_id"),
                "tier": 1,
            }
            supabase.table("score_history").insert(new_row).execute()

            total_corrected += 1
            country_fixed = True

        if country_fixed:
            total_countries += 1

    print(f"\n{'=' * 60}")
    print(f"  Done. Fixed {total_corrected} layer/country pairs across {total_countries} countries.")
    print(f"  Dashboard will show corrected scores on next reload.")
    print(f"{'=' * 60}\n")


if __name__ == "__main__":
    main()
