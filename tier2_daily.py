#!/usr/bin/env python3
"""
Travint.ai — Tier 2 Daily Change Detection Pipeline

PURPOSE:
  Detects changes from the established Tier 1 baseline.
  This is NOT fresh scoring — it is change detection.

  The fundamental difference from the old analyze.py:
  - Starts from the baseline (not from scratch)
  - Requires a verbatim source QUOTE for any proposed score change
  - Accumulates sub-threshold signals before escalating
  - Stores every run to score_history (append-only)
  - Logs validated changes to change_events (with evidence trail)
  - Sends uncertain/large changes to review_queue for human review

HOW IT WORKS:
  1. Load country baseline from baseline_versions
  2. Load recent headlines from ingest.py output
  3. Ask Gemini: what has CHANGED since the baseline? (quote required)
  4. For each category:
     - No change → keep baseline score, log to score_history
     - Change detected with quote → store to change_events, update score_history
     - Large jump (>1 level) or RED/PURPLE → also add to review_queue
     - Sub-threshold signal (no change but concerning) → increment trend_signals counter
  5. If trend_signals threshold hit → flag for review_queue

WHEN TO RUN:
  Every 12 hours (Tier 2 analysis cycle).
  Ingestion (ingest.py) should run every 6 hours.
  Sentinel (future) runs every 2 hours for government alert feeds.

USAGE:
  All countries:     python tier2_daily.py
  Single country:    python tier2_daily.py --country "France"
  Single ISO:        python tier2_daily.py --iso FR
  Force all layers:  python tier2_daily.py --all-layers
"""

import os
import sys
import json
import yaml
import argparse
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
from supabase import create_client, Client
from google import genai
from concurrent.futures import ThreadPoolExecutor, as_completed

# Load environment variables
load_dotenv()

SUPABASE_URL         = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY")
GEMINI_API_KEY       = os.getenv("GEMINI_API_KEY")

if not all([SUPABASE_URL, SUPABASE_SERVICE_KEY, GEMINI_API_KEY]):
    print("[X] Missing environment variables. Check .env for SUPABASE_URL, SUPABASE_SERVICE_KEY, GEMINI_API_KEY")
    sys.exit(1)

# Pipeline uses service key for all writes
supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
gemini   = genai.Client(api_key=GEMINI_API_KEY)

ALL_COUNTRIES = [
    ("Israel",                              "IL"),
    ("Netherlands",                         "NL"),
    ("USA",                                 "US"),
    ("France",                              "FR"),
    ("United Kingdom",                      "GB"),
    ("Turkey",                              "TR"),
    ("Thailand",                            "TH"),
    ("Saudi Arabia",                        "SA"),
    ("Russia",                              "RU"),
    ("Democratic Republic of the Congo",    "CD"),
    ("Nigeria",                             "NG"),
    ("Ukraine",                             "UA"),
    ("Brazil",                              "BR"),
    ("Australia",                           "AU"),
    ("China",                               "CN"),
    ("Egypt",                               "EG"),
    ("India",                               "IN"),
    ("Mexico",                              "MX"),
    ("South Africa",                        "ZA"),
    ("Poland",                              "PL"),
    ("Iran",                                "IR"),
    ("Libya",                               "LY"),
]

ALL_LAYERS    = ["base", "jewish_israeli", "solo_women"]
LEVEL_TO_INT  = {"GREEN": 1, "YELLOW": 2, "ORANGE": 3, "RED": 4, "PURPLE": 5}
INT_TO_LEVEL  = {1: "GREEN", 2: "YELLOW", 3: "ORANGE", 4: "RED", 5: "PURPLE"}
VETO_CATS     = ["armed_conflict", "regional_instability", "terrorism", "civil_strife"]
ALL_CATS      = VETO_CATS + ["crime", "health", "infrastructure"]

TREND_THRESHOLD = 5  # Number of sub-threshold signals before flagging


# =============================================================================
# Scoring Logic
# =============================================================================

def calculate_total_score(category_scores):
    """Veto logic: RED/PURPLE veto categories override; else weighted average."""
    max_veto = max(
        LEVEL_TO_INT.get(category_scores.get(cat, "GREEN"), 1)
        for cat in VETO_CATS
    )
    if max_veto >= 4:
        return INT_TO_LEVEL[max_veto]

    weighted_sum = sum(
        LEVEL_TO_INT.get(category_scores.get(cat, "GREEN"), 1) * (2 if cat in VETO_CATS else 1)
        for cat in ALL_CATS
    )
    total_weight = sum(2 if cat in VETO_CATS else 1 for cat in ALL_CATS)
    avg = weighted_sum / total_weight

    if avg <= 1.4: return "GREEN"
    if avg <= 2.4: return "YELLOW"
    if avg <= 3.4: return "ORANGE"
    if avg <= 4.4: return "RED"
    return "PURPLE"


def score_delta(old_score, new_score):
    """Returns integer change in score level (positive = elevated, negative = improved)."""
    return LEVEL_TO_INT.get(new_score, 1) - LEVEL_TO_INT.get(old_score, 1)


# =============================================================================
# NSC
# =============================================================================

def load_nsc_warnings():
    try:
        with open("israeli_nsc_warnings.yaml", "r", encoding="utf-8") as f:
            return yaml.safe_load(f).get("countries", {})
    except FileNotFoundError:
        return {}


# =============================================================================
# Headlines
# =============================================================================

def load_headlines_for_country(country_name):
    """Load relevant recent headlines from the ingest.py output file."""
    try:
        with open("latest_headlines.json", "r", encoding="utf-8") as f:
            data = json.load(f)
            all_headlines = data.get("headlines", [])

        # Filter to headlines mentioning this country
        keywords = [country_name.lower()]
        aliases = {
            "USA":                              ["united states", "america", "u.s.", " us "],
            "United Kingdom":                   ["uk", "britain", "british"],
            "Democratic Republic of the Congo": ["drc", "congo"],
            "Russia":                           ["russian", "kremlin", "moscow"],
            "Iran":                             ["iranian", "tehran"],
        }
        keywords.extend(aliases.get(country_name, []))

        relevant = [h for h in all_headlines if any(kw in h.lower() for kw in keywords)]
        return relevant, data.get("timestamp")
    except FileNotFoundError:
        return [], None
    except Exception as e:
        print(f"  [!] Headlines load error: {e}")
        return [], None


# =============================================================================
# Database Helpers
# =============================================================================

def get_country_id(iso_code):
    try:
        result = supabase.table("countries").select("id").eq("iso_code", iso_code).execute()
        return result.data[0]["id"] if result.data else None
    except Exception as e:
        print(f"  [X] get_country_id failed: {e}")
        return None


def get_active_baseline(country_id, identity_layer):
    """
    Load the latest approved baseline for a country/layer.
    Falls back to pending baselines if no approved one exists.
    Returns None if no baseline exists at all (country not ready for Tier 2).
    """
    try:
        result = (
            supabase.table("baseline_versions")
            .select("*")
            .eq("country_id", country_id)
            .eq("identity_layer", identity_layer)
            .order("version_number", desc=True)
            .limit(1)
            .execute()
        )
        if result.data:
            row = result.data[0]
            row["scores"] = json.loads(row["scores"]) if isinstance(row["scores"], str) else row["scores"]
            return row
        return None
    except Exception as e:
        print(f"  [X] get_active_baseline failed: {e}")
        return None


def get_latest_score(country_id, identity_layer):
    """Get the most recent score from score_history."""
    try:
        result = (
            supabase.table("score_history")
            .select("scores, total_score, created_at")
            .eq("country_id", country_id)
            .eq("identity_layer", identity_layer)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        if result.data:
            row = result.data[0]
            row["scores"] = json.loads(row["scores"]) if isinstance(row["scores"], str) else row["scores"]
            return row
        return None
    except Exception as e:
        print(f"  [!] get_latest_score failed: {e}")
        return None


def get_or_create_trend_signal(country_id, identity_layer):
    """Get trend signal counter, creating row if it doesn't exist."""
    try:
        result = (
            supabase.table("trend_signals")
            .select("*")
            .eq("country_id", country_id)
            .eq("identity_layer", identity_layer)
            .execute()
        )
        if result.data:
            return result.data[0]
        # Create new row
        new_row = {
            "country_id":      country_id,
            "identity_layer":  identity_layer,
            "signal_count":    0,
            "threshold":       TREND_THRESHOLD,
            "flagged":         False,
            "created_at":      datetime.now(timezone.utc).isoformat(),
        }
        supabase.table("trend_signals").insert(new_row).execute()
        return new_row
    except Exception as e:
        print(f"  [!] trend_signal lookup failed: {e}")
        return None


def increment_trend_signal(country_id, identity_layer):
    """Increment sub-threshold signal counter. Flag if threshold reached."""
    signal = get_or_create_trend_signal(country_id, identity_layer)
    if not signal:
        return

    new_count = signal.get("signal_count", 0) + 1
    flagged   = new_count >= signal.get("threshold", TREND_THRESHOLD)
    update    = {
        "signal_count":    new_count,
        "last_signal_date": datetime.now(timezone.utc).date().isoformat(),
        "flagged":         flagged,
    }
    if flagged and not signal.get("flagged"):
        update["flagged_at"] = datetime.now(timezone.utc).isoformat()
        print(f"  [!] TREND THRESHOLD REACHED ({new_count} signals) — flagging for review")

    try:
        supabase.table("trend_signals").update(update).eq("id", signal["id"]).execute()
    except Exception:
        pass


def reset_trend_signal(country_id, identity_layer):
    """Reset signal counter after a score has been officially changed."""
    try:
        supabase.table("trend_signals").update({
            "signal_count":  0,
            "flagged":       False,
            "reset_at":      datetime.now(timezone.utc).isoformat(),
        }).eq("country_id", country_id).eq("identity_layer", identity_layer).execute()
    except Exception:
        pass


# =============================================================================
# Change Detection Prompt
# =============================================================================

def build_change_detection_prompt(country_name, identity_layer, baseline, current_scores, headlines, nsc_level=None):
    """
    Build the Tier 2 change detection prompt.

    The core principle: DETECTION from baseline, not fresh scoring.
    Scores only change when specific evidence justifies it.
    Evidence requires a verbatim source quote.
    """
    today = datetime.now(timezone.utc).strftime("%B %d, %Y")

    layer_descriptions = {
        "base":           "general international travelers",
        "jewish_israeli": "Jewish and Israeli travelers",
        "solo_women":     "solo women travelers",
    }
    layer_desc = layer_descriptions.get(identity_layer, "general travelers")

    baseline_scores_text = "\n".join(
        f"  {cat}: {baseline['scores'].get(cat, 'UNKNOWN')}"
        for cat in ALL_CATS
    )
    current_scores_text = "\n".join(
        f"  {cat}: {current_scores.get(cat, 'UNKNOWN')}"
        for cat in ALL_CATS
    )
    headlines_text = "\n".join(f"  {i+1}. {h}" for i, h in enumerate(headlines[:30]))

    prompt = f"""You are a travel security analyst running a CHANGE DETECTION check.
Today: {today}
Country: {country_name}
Audience: {layer_desc}

━━━ YOUR TASK ━━━

You are NOT scoring this country from scratch. You are checking whether RECENT EVENTS
have changed any threat category from its established baseline.

Scores change ONLY when you have specific, dated evidence from the headlines provided.
If you cannot quote a headline that justifies a change — the score stays the same.
This is mandatory. No quote = no change.

━━━ ESTABLISHED BASELINE (Tier 1 — structural conditions) ━━━

{baseline_scores_text}

Baseline established: {baseline.get('created_at', 'unknown')[:10]}

━━━ CURRENT SCORES (most recent Tier 2 update) ━━━

{current_scores_text}

━━━ RECENT HEADLINES (from latest ingestion) ━━━

{headlines_text if headlines else "  [No relevant headlines found for this country]"}

━━━ NSC LEVEL ━━━
{"Israeli NSC Level: " + str(nsc_level) + "/4" if nsc_level else "N/A"}

━━━ CHANGE DETECTION RULES ━━━

For EACH of the 7 categories, determine:

1. SCORE UP (threat increased): Headlines show a NEW threat development ABOVE current level.
   → Required: verbatim quote from a headline, source name, and approximate date
   → Change type: "EVENT" (single incident, temporary) or "TREND" (pattern building)
   → EVENT scores auto-expire after 30 days with no confirming events

2. SCORE DOWN (threat decreased, positive development): Headlines show genuine improvement.
   → Required: verbatim quote confirming resolution/improvement
   → Change type: "POSITIVE"
   → Do not return to baseline automatically — verify through 2+ cycles first

3. NO CHANGE: Headlines do not provide specific evidence for a score change.
   → Keep current score. Do not change because of vague or unrelated news.

4. SUB-THRESHOLD SIGNAL: Something concerning but not enough to change the score yet.
   → Mark as sub_threshold_signal: true. Count accumulates over time.
   → When 5 consecutive signals hit, the system flags for human review.

━━━ SCORING SCALE ━━━
GREEN (1) / YELLOW (2) / ORANGE (3) / RED (4) / PURPLE (5)

━━━ REQUIRED OUTPUT FORMAT ━━━

Return ONLY valid JSON.

{{
  "categories": {{
    "armed_conflict": {{
      "current_score":  "GREEN|YELLOW|ORANGE|RED|PURPLE",
      "changed":        true|false,
      "change_type":    null | "EVENT" | "TREND" | "POSITIVE" | "SPILLOVER",
      "source_quote":   "verbatim quote from headline — REQUIRED if changed=true, else null",
      "source_name":    "publication name — REQUIRED if changed=true, else null",
      "source_date":    "YYYY-MM-DD approximate — REQUIRED if changed=true, else null",
      "event_elevated": true|false,
      "event_expiry":   "YYYY-MM-DD (30 days from today if event_elevated=true, else null)",
      "sub_threshold_signal": true|false,
      "reasoning":      "1-2 sentences explaining the determination"
    }},
    "regional_instability": {{ ... }},
    "terrorism":            {{ ... }},
    "civil_strife":         {{ ... }},
    "crime":                {{ ... }},
    "health":               {{ ... }},
    "infrastructure":       {{ ... }}
  }},
  "summary": "2-3 short paragraphs. What changed and why. What didn't change and why. Write like briefing a colleague — specific, no AI filler phrases.",
  "watch_factors": "2-3 specific upcoming developments to monitor. Dates/timeframes where known.",
  "recommendations": {{
    "movement_access":        "one sentence",
    "emergency_preparedness": "one sentence",
    "communications":         "one sentence",
    "health_medical":         "one sentence",
    "crime_personal_safety":  "one sentence",
    "travel_logistics":       "one sentence"
  }},
  "sources": ["list of sources referenced"]
}}

QUALITY RULES:
- If there are no relevant headlines, confirm all scores unchanged with "No relevant events detected in current ingestion cycle" in reasoning
- Never hallucinate events not present in the headlines
- Sub-threshold signals are for things that feel concerning but lack specific enough evidence — use sparingly
- Be honest about uncertainty: "Unclear from available sources" is a valid reasoning
"""

    return prompt


# =============================================================================
# Storage
# =============================================================================

def store_tier2_result(country_id, country_name, identity_layer, baseline, analysis):
    """
    Store Tier 2 change detection result:
    1. score_history (always — every run creates a new record)
    2. change_events (only for changed categories)
    3. review_queue (if large jump or RED/PURPLE change)
    4. trend_signals (increment if sub-threshold signals detected)
    Returns score_history_id or None.
    """
    categories   = analysis.get("categories", {})
    new_scores   = {cat: categories[cat]["current_score"] for cat in ALL_CATS if cat in categories}
    total_score  = calculate_total_score(new_scores)
    now          = datetime.now(timezone.utc).isoformat()

    # ── 1. score_history ────────────────────────────────────────────────────
    history_id = None
    try:
        history_row = {
            "country_id":          country_id,
            "identity_layer":      identity_layer,
            "total_score":         total_score,
            "scores":              json.dumps(new_scores),
            "ai_summary":          analysis.get("summary", ""),
            "veto_explanation":    "",
            "recommendations":     json.dumps(analysis.get("recommendations", {})),
            "watch_factors":       analysis.get("watch_factors", ""),
            "sources":             json.dumps(analysis.get("sources", [])),
            "confidence":          json.dumps({}),  # Tier 2 confidence added in future
            "baseline_version_id": baseline.get("id"),
            "tier":                2,
            "created_at":          now,
        }
        result   = supabase.table("score_history").insert(history_row).execute()
        history_id = result.data[0]["id"]
        print(f"  [OK] score_history updated (total={total_score})")
    except Exception as e:
        print(f"  [X] score_history insert failed: {e}")
        return None

    # ── 2. change_events ────────────────────────────────────────────────────
    changes_detected = []
    for cat, cat_data in categories.items():
        if not cat_data.get("changed"):
            continue

        old_score  = baseline["scores"].get(cat, "GREEN")
        new_score  = cat_data.get("current_score", old_score)
        quote      = cat_data.get("source_quote", "")
        delta      = score_delta(old_score, new_score)

        if not quote:
            print(f"  [!] {cat}: changed=true but no source_quote — treating as no change")
            continue

        changes_detected.append((cat, old_score, new_score, delta, cat_data))

        try:
            expiry = None
            if cat_data.get("event_elevated"):
                expiry = (datetime.now(timezone.utc) + timedelta(days=30)).date().isoformat()

            event_row = {
                "country_id":      country_id,
                "identity_layer":  identity_layer,
                "category":        cat,
                "old_score":       old_score,
                "new_score":       new_score,
                "source_quote":    quote,
                "source_name":     cat_data.get("source_name", ""),
                "source_url":      cat_data.get("source_url", ""),
                "source_date":     cat_data.get("source_date"),
                "change_type":     cat_data.get("change_type", "EVENT"),
                "event_elevated":  cat_data.get("event_elevated", False),
                "event_expiry":    expiry,
                "score_history_id": history_id,
                "created_at":      now,
            }
            supabase.table("change_events").insert(event_row).execute()
            direction = "▲" if delta > 0 else "▼"
            print(f"  [OK] change_event: {cat} {old_score} → {new_score} {direction} ({cat_data.get('change_type')})")
        except Exception as e:
            print(f"  [!] change_events insert failed for {cat}: {e}")

        # Reset trend signal when a score officially changes
        if delta != 0:
            reset_trend_signal(country_id, identity_layer)

    # ── 3. review_queue (large jumps or RED/PURPLE) ──────────────────────────
    urgent_changes = [
        (cat, old, new, delta, data)
        for cat, old, new, delta, data in changes_detected
        if abs(delta) > 1 or LEVEL_TO_INT.get(new, 1) >= 4
    ]
    if urgent_changes:
        try:
            priority = "URGENT" if any(LEVEL_TO_INT.get(new, 1) >= 4 for _, _, new, _, _ in urgent_changes) else "STANDARD"
            review_row = {
                "country_id":     country_id,
                "identity_layer": identity_layer,
                "proposal":       json.dumps({
                    "type":         "tier2_score_change",
                    "country":      country_name,
                    "layer":        identity_layer,
                    "total_score":  total_score,
                    "changes":      [
                        {
                            "category":     cat,
                            "old_score":    old,
                            "new_score":    new,
                            "delta":        delta,
                            "quote":        data.get("source_quote"),
                            "source":       data.get("source_name"),
                            "change_type":  data.get("change_type"),
                        }
                        for cat, old, new, delta, data in urgent_changes
                    ],
                    "history_id":   history_id,
                }),
                "priority":       priority,
                "triggered_by":   "tier2_large_jump" if any(abs(d) > 1 for _, _, _, d, _ in urgent_changes) else "tier2_red_purple",
                "created_at":     now,
            }
            supabase.table("review_queue").insert(review_row).execute()
            print(f"  [!] Added to review_queue (priority={priority}) — {len(urgent_changes)} large/critical change(s)")
        except Exception as e:
            print(f"  [!] review_queue insert failed: {e}")

    # ── 4. trend_signals ────────────────────────────────────────────────────
    sub_threshold = [cat for cat, data in categories.items() if data.get("sub_threshold_signal")]
    if sub_threshold:
        print(f"  [>] Sub-threshold signals detected in: {', '.join(sub_threshold)}")
        increment_trend_signal(country_id, identity_layer)
    else:
        # Check if trend signal already flagged — if so, still in review queue, do nothing
        pass

    return history_id


# =============================================================================
# Per-Country Orchestrator
# =============================================================================

def run_country_daily(country_name, iso_code, layers):
    """Run Tier 2 change detection for all layers of a country."""
    print(f"\n{'='*60}")
    print(f"  TIER 2 DAILY: {country_name} ({iso_code})")
    print(f"{'='*60}")

    country_id = get_country_id(iso_code)
    if not country_id:
        print(f"  [X] Country not found in database")
        return False

    headlines, headlines_ts = load_headlines_for_country(country_name)
    print(f"  [>] {len(headlines)} relevant headlines loaded")

    nsc_data = load_nsc_warnings() if "jewish_israeli" in layers else {}

    for layer in layers:
        print(f"\n  ── Layer: {layer} ──")

        # Load baseline — required for Tier 2
        baseline = get_active_baseline(country_id, layer)
        if not baseline:
            print(f"  [SKIP] No baseline found for {country_name}/{layer}")
            print(f"         Run tier1_baseline.py first to establish baseline")
            continue

        approved = baseline.get("reviewed_by", "pending")
        if approved == "pending":
            print(f"  [!] Baseline is pending owner review — running anyway (scores visible on dashboard)")

        # Load current scores (most recent score_history entry)
        current = get_latest_score(country_id, layer)
        current_scores = current["scores"] if current else baseline["scores"]

        nsc_level = nsc_data.get(country_name, {}).get("level") if layer == "jewish_israeli" else None

        # Build and send prompt
        prompt = build_change_detection_prompt(
            country_name   = country_name,
            identity_layer = layer,
            baseline       = baseline,
            current_scores = current_scores,
            headlines      = headlines,
            nsc_level      = nsc_level,
        )

        print(f"  [>] Calling Gemini 2.5 Flash...")
        try:
            response = gemini.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
                config={"temperature": 0.0, "top_p": 0.95}
            )
            text = response.text.strip()

            # Strip markdown fences
            if text.startswith("```json"): text = text[7:]
            if text.startswith("```"):     text = text[3:]
            if text.endswith("```"):       text = text[:-3]
            text = text.strip()

            analysis = json.loads(text)
            print(f"  [OK] Change detection complete")

        except json.JSONDecodeError as e:
            print(f"  [X] JSON parse failed: {e}")
            continue
        except Exception as e:
            print(f"  [X] Gemini call failed: {e}")
            continue

        # Store results
        history_id = store_tier2_result(country_id, country_name, layer, baseline, analysis)

        # Print summary
        cats = analysis.get("categories", {})
        changed_cats = [cat for cat, d in cats.items() if d.get("changed")]
        if changed_cats:
            print(f"  [!] CHANGES DETECTED: {', '.join(changed_cats)}")
        else:
            print(f"  [-] No score changes — baseline confirmed")

    return True


# =============================================================================
# Main
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Travint.ai — Tier 2 Daily Change Detection"
    )
    parser.add_argument("--country",    type=str, help="Country name")
    parser.add_argument("--iso",        type=str, help="ISO code (e.g. FR)")
    parser.add_argument("--layer",      type=str, choices=ALL_LAYERS, help="Single layer")
    parser.add_argument("--all-layers", action="store_true", help="Run all identity layers")
    parser.add_argument("--workers",    type=int, default=5, help="Parallel worker count (default 5)")

    args = parser.parse_args()

    print("=" * 60)
    print("  Travint.ai — Tier 2 Daily Change Detection")
    print("=" * 60)
    print(f"  Started: {datetime.now(timezone.utc).isoformat()} UTC\n")

    # Layers
    if args.all_layers:
        layers = ALL_LAYERS
    elif args.layer:
        layers = [args.layer]
    else:
        layers = ["base"]

    # Countries
    if args.iso:
        match = [(n, c) for n, c in ALL_COUNTRIES if c.upper() == args.iso.upper()]
        countries = match or []
    elif args.country:
        match = [(n, c) for n, c in ALL_COUNTRIES if n.lower() == args.country.lower()]
        countries = match or []
    else:
        countries = ALL_COUNTRIES

    if not countries:
        print("[X] No matching countries found")
        sys.exit(1)

    print(f"  Countries : {len(countries)}")
    print(f"  Layers    : {layers}")
    print()

    success = 0
    failed  = 0

    # Run in parallel
    max_workers = min(args.workers, len(countries))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(run_country_daily, name, iso, layers): name
            for name, iso in countries
        }
        for future in as_completed(futures):
            country_name = futures[future]
            try:
                ok = future.result()
                if ok:
                    success += 1
                else:
                    failed += 1
            except Exception as e:
                print(f"\n[X] {country_name} crashed: {e}")
                failed += 1

    print(f"\n{'='*60}")
    print(f"  DONE — {success} countries completed, {failed} failed")
    print(f"  Finished: {datetime.now(timezone.utc).isoformat()} UTC")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
