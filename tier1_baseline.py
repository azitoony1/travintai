#!/usr/bin/env python3
"""
Travint.ai - Tier 1 Baseline Establishment Pipeline

PURPOSE:
  Creates the immutable structural baseline for a country/identity layer.
  This is NOT daily news analysis - it captures deep, slow-moving structural
  conditions: legal environment, historical patterns, institutional factors,
  annual index scores, and government advisory anchors.

  Every country must have an owner-approved baseline before going live.
  This script drafts the baseline. Owner reviews in the admin panel.

WHEN TO RUN:
  - First analysis of a new country (run once per country/layer)
  - Tier 3 rebalancing every 3-6 months (creates a new version_number)

HOW TO RUN:
  Single country, single layer:
    python tier1_baseline.py --country "France" --layer base

  Single country, all layers:
    python tier1_baseline.py --country "France" --all-layers

  All countries, base layer only:
    python tier1_baseline.py --all-countries --layer base

  All countries, all layers (slow - use for first-time setup):
    python tier1_baseline.py --all-countries --all-layers

OUTPUT:
  - Writes to baseline_versions (reviewed_by = 'pending')
  - Writes to score_history (tier = 1)
  - Adds entry to review_queue for owner approval
  - Dashboard shows scores immediately (pending approval noted in admin)
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

# Load environment variables
load_dotenv()

SUPABASE_URL         = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY")
GEMINI_API_KEY       = os.getenv("GEMINI_API_KEY")

if not all([SUPABASE_URL, SUPABASE_SERVICE_KEY, GEMINI_API_KEY]):
    print("[X] Missing environment variables. Check .env for SUPABASE_URL, SUPABASE_SERVICE_KEY, GEMINI_API_KEY")
    sys.exit(1)

# Pipeline uses service key - it has write access
supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
gemini   = genai.Client(api_key=GEMINI_API_KEY)

# All 22 countries in the system
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

ALL_LAYERS = ["base", "jewish_israeli", "solo_women"]
# Future: "lgbtq", "journalists", "aid_workers"

SCORE_LEVELS     = ["GREEN", "YELLOW", "ORANGE", "RED", "PURPLE"]
CONFIDENCE_LEVELS = ["HIGH", "MEDIUM", "LOW", "INSUFFICIENT"]


# =============================================================================
# NSC Warnings Loader
# =============================================================================

def load_nsc_warnings():
    """Load Israeli NSC travel warnings from local YAML config."""
    try:
        with open("israeli_nsc_warnings.yaml", "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
            return data.get("countries", {})
    except FileNotFoundError:
        return {}


# =============================================================================
# Database Helpers
# =============================================================================

def get_country_id(iso_code):
    """Get country UUID from database."""
    try:
        result = supabase.table("countries").select("id").eq("iso_code", iso_code).execute()
        if result.data:
            return result.data[0]["id"]
        print(f"[X] Country {iso_code} not found in database - run schema SQL first")
        return None
    except Exception as e:
        print(f"[X] Database error: {e}")
        return None


def get_latest_baseline_version(country_id, identity_layer):
    """Get the current highest version number for a country/layer baseline."""
    try:
        result = (
            supabase.table("baseline_versions")
            .select("version_number")
            .eq("country_id", country_id)
            .eq("identity_layer", identity_layer)
            .order("version_number", desc=True)
            .limit(1)
            .execute()
        )
        if result.data:
            return result.data[0]["version_number"]
        return 0  # No baseline yet
    except Exception as e:
        print(f"[!] Could not check baseline version: {e}")
        return 0


def baseline_already_exists(country_id, identity_layer):
    """Check if a Tier 1 baseline already exists (and is approved)."""
    try:
        result = (
            supabase.table("baseline_versions")
            .select("id, reviewed_by, version_number")
            .eq("country_id", country_id)
            .eq("identity_layer", identity_layer)
            .order("version_number", desc=True)
            .limit(1)
            .execute()
        )
        if result.data:
            row = result.data[0]
            return True, row.get("reviewed_by"), row.get("version_number")
        return False, None, 0
    except Exception as e:
        print(f"[!] Error checking baseline: {e}")
        return False, None, 0


# =============================================================================
# Scoring Logic (same veto system as before)
# =============================================================================

def calculate_total_score(category_scores):
    """
    Veto-class categories: Armed Conflict, Regional Instability, Terrorism, Civil Strife
    - If any veto category is RED or PURPLE - total is at least that level
    - Otherwise - weighted average (veto categories count double)
    """
    veto_categories  = ["armed_conflict", "regional_instability", "terrorism", "civil_strife"]
    all_categories   = veto_categories + ["crime", "health", "infrastructure"]
    level_to_int     = {"GREEN": 1, "YELLOW": 2, "ORANGE": 3, "RED": 4, "PURPLE": 5}
    int_to_level     = {1: "GREEN", 2: "YELLOW", 3: "ORANGE", 4: "RED", 5: "PURPLE"}

    # Check veto
    max_veto = max(
        level_to_int.get(category_scores.get(cat, "GREEN"), 1)
        for cat in veto_categories
    )
    if max_veto >= 4:
        return int_to_level[max_veto]

    # Weighted average
    weighted_sum = sum(
        level_to_int.get(category_scores.get(cat, "GREEN"), 1) * (2 if cat in veto_categories else 1)
        for cat in all_categories
    )
    total_weight = sum(2 if cat in veto_categories else 1 for cat in all_categories)
    avg = weighted_sum / total_weight

    if avg <= 1.4: return "GREEN"
    if avg <= 2.4: return "YELLOW"
    if avg <= 3.4: return "ORANGE"
    if avg <= 4.4: return "RED"
    return "PURPLE"


# =============================================================================
# Prompt Builder
# =============================================================================

def build_baseline_prompt(country_name, identity_layer, nsc_level=None, base_baseline=None):
    """
    Build the Tier 1 baseline prompt.

    Tier 1 is about STRUCTURAL conditions - not today's news.
    It captures slow-moving factors: legal environment, historical patterns,
    annual indices, institutional stability, demographic tensions.
    This baseline will be the reference point for all future Tier 2 change detection.
    """

    today = datetime.now(timezone.utc).strftime("%B %d, %Y")

    layer_descriptions = {
        "base":           "general international travelers",
        "jewish_israeli": "Jewish and Israeli travelers",
        "solo_women":     "solo women travelers",
        "lgbtq":          "LGBTQ+ travelers",
        "journalists":    "journalists and media workers",
        "aid_workers":    "humanitarian aid workers and NGO staff",
    }
    layer_desc = layer_descriptions.get(identity_layer, "general travelers")

    prompt = f"""You are a senior travel security analyst establishing a STRUCTURAL BASELINE for {country_name}.

Today's date: {today}

AUDIENCE: {layer_desc}

=== WHAT A TIER 1 BASELINE IS ===

This is NOT daily news analysis. You are capturing deep, slow-moving structural conditions:
- Legal and institutional environment (laws, enforcement, judicial independence)
- Historical conflict and crime patterns (not last week - the past 5-10 years)
- Annual index scores (RSF press freedom, UNODC homicide rates, ILGA LGBTQ+ rights, etc.)
- Government advisory anchor levels (US State Dept, UK FCDO, Israeli NSC if applicable)
- Demographic tensions and structural grievances that create long-term risk
- Infrastructure quality and systemic health system capacity

What a Tier 1 baseline is NOT:
- A reaction to last week's news
- A summary of current events
- Something that changes every few months (that's Tier 2's job)

CRITICAL — USE YOUR GOOGLE SEARCH ACCESS:
Search for current conditions in {country_name} RIGHT NOW. Your training data may be
1-2 years out of date. A war, a coup, or a major attack that happened 6 months ago
is now a STRUCTURAL REALITY and must be reflected in the baseline.

Specifically search for:
- "{country_name} security situation {today[:4]}"
- "{country_name} travel advisory {today[:4]}"
- "{country_name} terrorism attack {today[:4]}"
- Any active armed conflicts or wars involving {country_name}
- Any significant incidents in the past 24 months that permanently changed conditions

MAJOR INCIDENTS RULE: If a significant attack, conflict, or security event occurred
in the past 24 months that changed the structural threat picture, it MUST be reflected
in the scores — even if it looks like a "current event". A country where a major
terror attack killed 6 people last year has a different structural terrorism score
than a country where none occurred. Do not undercount recent history.

=== SCORING SCALE ===

GREEN  (1): Safe / Normal structural conditions
YELLOW (2): Elevated structural risk / Exercise caution
ORANGE (3): Significant structural risk / Heightened precautions
RED    (4): High structural risk / Reconsider travel
PURPLE (5): Extreme risk / Do not travel (active war, systematic targeting, no consular protection)

=== 7 SECURITY CATEGORIES ===

1. Armed Conflict      - active war, military operations, territorial disputes with violence
2. Regional Instability - neighboring conflicts with spillover potential; geopolitical tensions
3. Terrorism           - organized terrorist groups, attack frequency, targeting patterns
4. Civil Strife        - political violence, social unrest, protest movements with violence risk
5. Crime               - organized crime, street crime, kidnapping, corruption affecting travelers
6. Health              - disease risk, healthcare system quality, medical access for foreigners
7. Infrastructure      - road safety, transport reliability, power/water/communications
"""

    # Identity-specific instructions
    if identity_layer == "jewish_israeli":
        prompt += f"""
=== JEWISH/ISRAELI IDENTITY LAYER ===

START with the base layer structural conditions. Then adjust ONLY where being Jewish or Israeli
creates a meaningfully different structural risk.

{f"Base layer baseline scores: {json.dumps(base_baseline.get('scores', {}), indent=2)}" if base_baseline else ""}

Identity-specific structural factors to assess:
- Legal status of Israeli passport holders (banned countries: Iran, Saudi, Lebanon, Syria, Libya, Yemen, Iraq, Pakistan)
- Structural antisemitism: criminalization, institutional discrimination, hate crime patterns (ADL Global 100, FRA surveys, Kantor Center data)
- Israeli embassy/consulate presence and functional consular protection
- Local Jewish community infrastructure: synagogues, kosher facilities, community organizations
- Historical patterns of violence against Jews/Israelis in this country
- Government/institutional attitudes toward Israel and Jews (official policy, not current events)
"""
        if nsc_level:
            prompt += f"""
Israeli NSC Structural Warning Level: {nsc_level}/4
(1=Safe, 2=Exercise Caution, 3=Reconsider, 4=Do Not Travel)
Use this as one anchor. Note if your structural assessment meaningfully differs.
"""

    elif identity_layer == "solo_women":
        prompt += f"""
=== SOLO WOMEN IDENTITY LAYER ===

START with the base layer structural conditions. Then adjust ONLY where being a solo woman
creates a meaningfully different structural risk.

{f"Base layer baseline scores: {json.dumps(base_baseline.get('scores', {}), indent=2)}" if base_baseline else ""}

Identity-specific structural factors to assess:
- Legal protections for women: rape laws, domestic violence laws, enforcement reality
- Cultural norms: dress codes with legal enforcement, mobility restrictions, guardianship laws
- UNODC statistics on gender-based violence (structural rates, not single incidents)
- Georgetown GIWPS Women Peace & Security Index score
- Safety of public transport and taxis for women traveling alone
- Healthcare access for women (reproductive health, assault victim care)
- Countries where women CANNOT legally travel alone (guardianship systems)

IMPORTANT: Armed Conflict and Regional Instability affect all travelers equally
unless there is documented systematic targeting of women in conflict (e.g., sexual violence
as a weapon of war). Do not inflate these scores just because the traveler is a woman.
"""

    prompt += """
=== REQUIRED OUTPUT FORMAT ===

Return ONLY valid JSON. No markdown, no preamble.

{
  "scores": {
    "armed_conflict":        "GREEN|YELLOW|ORANGE|RED|PURPLE",
    "regional_instability":  "GREEN|YELLOW|ORANGE|RED|PURPLE",
    "terrorism":             "GREEN|YELLOW|ORANGE|RED|PURPLE",
    "civil_strife":          "GREEN|YELLOW|ORANGE|RED|PURPLE",
    "crime":                 "GREEN|YELLOW|ORANGE|RED|PURPLE",
    "health":                "GREEN|YELLOW|ORANGE|RED|PURPLE",
    "infrastructure":        "GREEN|YELLOW|ORANGE|RED|PURPLE"
  },
  "stability_justifications": {
    "armed_conflict":        "Why this is the structural baseline. What factors make it stable at this level. What specific change would move it up or down.",
    "regional_instability":  "...",
    "terrorism":             "...",
    "civil_strife":          "...",
    "crime":                 "...",
    "health":                "...",
    "infrastructure":        "..."
  },
  "confidence_levels": {
    "armed_conflict":        "HIGH|MEDIUM|LOW|INSUFFICIENT",
    "regional_instability":  "HIGH|MEDIUM|LOW|INSUFFICIENT",
    "terrorism":             "HIGH|MEDIUM|LOW|INSUFFICIENT",
    "civil_strife":          "HIGH|MEDIUM|LOW|INSUFFICIENT",
    "crime":                 "HIGH|MEDIUM|LOW|INSUFFICIENT",
    "health":                "HIGH|MEDIUM|LOW|INSUFFICIENT",
    "infrastructure":        "HIGH|MEDIUM|LOW|INSUFFICIENT"
  },
  "confidence_notes": {
    "any_category_with_low_confidence": "Why confidence is low - missing data, conflicting sources, etc."
  },
  "baseline_narrative": "3-4 paragraphs. Write like a human analyst briefing a colleague, not an AI writing a report. Be specific: cite index scores, name specific laws, give historical numbers. Explain what makes this country's structural situation distinctive for this traveler type. Avoid: 'complex', 'multifaceted', 'notably', 'furthermore', 'it is important to note'.",
  "veto_explanation": "If any veto category (armed_conflict, regional_instability, terrorism, civil_strife) is RED or PURPLE, explain why it overrides the overall score. Otherwise explain the weighted average logic.",
  "sources_used": [
    "List the specific sources/indices informing this baseline. Examples: 'US State Dept Level 2 Advisory', 'RSF Press Freedom Index 2024: rank 45/180', 'UNODC homicide rate 8.2/100k (2023)', 'ILGA: same-sex relations criminalized, up to 10 years'",
    "Be specific - include scores/rankings/years where known",
    "3-6 sources minimum"
  ],
  "recommendations": {
    "movement_access":         "One concrete sentence for this traveler type",
    "emergency_preparedness":  "One concrete sentence",
    "communications":          "One concrete sentence",
    "health_medical":          "One concrete sentence",
    "crime_personal_safety":   "One concrete sentence",
    "travel_logistics":        "One concrete sentence"
  },
  "watch_factors": "2-4 SPECIFIC structural factors to monitor. These are slow-moving structural risks, not today's news: 'Presidential election cycle due 2026 - historically accompanied by political violence', 'Peace agreement with [group] signed 2023 - compliance monitoring ongoing', 'Upcoming monsoon season June-September raises health and infrastructure risk annually'"
}

QUALITY RULES:
- No placeholders: never use [Country], [Group], [Organization]
- Be specific: numbers, names, years, rankings
- LOW or INSUFFICIENT confidence is honest and builds trust - do not fake HIGH confidence
- Identity layers: only diverge from base when there is a CLEAR structural reason
- Write baseline_narrative as if briefing a colleague who will act on this information
"""

    return prompt


# =============================================================================
# Analysis Runner
# =============================================================================

def run_baseline_analysis(country_name, identity_layer, nsc_level=None, base_baseline=None):
    """
    TWO-STEP ANALYSIS (as required by CLAUDE.md — scoring and narrative are separate calls):

    Step 1 — Intelligence Briefing (WITH Google Search Grounding):
      Gemini searches the web in real time and produces a factual prose briefing
      of current conditions. This is the ground truth that everything else builds on.
      Search grounding is critical — without it Gemini uses training data that may
      be 1-2+ years out of date.

    Step 2 — Scoring (WITHOUT search, pure JSON):
      A second Gemini call reads the Step 1 briefing and produces clean JSON scores.
      No search tool = no citation markers = clean parseable JSON every time.
      This separation is also per CLAUDE.md: "Scoring and summary are separate LLM calls."

    Returns parsed analysis dict or None on failure.
    """
    today = datetime.now(timezone.utc).strftime("%B %d, %Y")

    layer_descriptions = {
        "base":           "general international travelers",
        "jewish_israeli": "Jewish and Israeli travelers",
        "solo_women":     "solo women travelers",
    }
    layer_desc = layer_descriptions.get(identity_layer, "general travelers")

    # ── STEP 1: Real-time Intelligence Briefing (with Google Search) ──────────
    print(f"  [>] Step 1 — Searching current conditions for {country_name} ({identity_layer})...")

    identity_context = ""
    if identity_layer == "jewish_israeli":
        identity_context = """
Focus on conditions specifically for Jewish and Israeli travelers:
- Is the Israeli passport valid here? (Iran, Saudi, Lebanon, Syria, Libya, Yemen, Iraq, Pakistan ban it)
- Recent antisemitic incidents, attacks, or institutional hostility toward Jews/Israelis
- Israeli embassy/consulate status and functional consular protection
- Local Jewish community safety
- NSC warning level and any Israeli government travel advisories"""
        if nsc_level:
            identity_context += f"\n- Israeli NSC current level: {nsc_level}/4"
    elif identity_layer == "solo_women":
        identity_context = """
Focus on conditions specifically for solo women travelers:
- Legal rights and restrictions for women traveling alone (guardianship laws, dress codes)
- Rates of gender-based violence and sexual assault
- Safety of public transport and taxis for women
- Recent incidents targeting women
- Quality of healthcare for women"""

    briefing_prompt = f"""You are a senior travel security analyst. Today is {today}.

Search for the CURRENT security situation in {country_name} for {layer_desc}.

Search for:
- "{country_name} security situation {today[:4]}"
- "{country_name} travel advisory {today[:4]}"
- "{country_name} armed conflict war {today[:4]}"
- "{country_name} terrorism attack {today[:4]}"
- Any active wars, conflicts, or major incidents in the past 24 months
{identity_context}

Write a factual intelligence briefing covering:
1. ARMED CONFLICT: Any active wars, military operations, airstrikes, territorial conflicts?
2. REGIONAL INSTABILITY: Neighboring conflicts with spillover? Geopolitical tensions?
3. TERRORISM: Active groups, recent attacks, threat level?
4. CIVIL STRIFE: Political violence, coups, riots, sustained protests?
5. CRIME: Organized crime, kidnapping, violent crime rates for travelers?
6. HEALTH: Disease outbreaks, healthcare quality, medical access?
7. INFRASTRUCTURE: Road safety, power/water reliability, transport quality?

Be specific: name actual groups, cite specific incidents with dates, give statistics where known.
If there is an active war or major conflict, describe it clearly — do not soften it.
This briefing will be used to assign threat scores. Accuracy is critical."""

    try:
        step1_response = gemini.models.generate_content(
            model="gemini-2.5-flash",
            contents=briefing_prompt,
            config=genai.types.GenerateContentConfig(
                tools=[genai.types.Tool(google_search=genai.types.GoogleSearch())],
                temperature=0.0,
            )
        )
        briefing = step1_response.text.strip()
        print(f"  [OK] Briefing complete ({len(briefing)} chars)")

    except Exception as e:
        print(f"  [X] Step 1 briefing failed: {e}")
        return None

    # ── STEP 2: Scoring from Briefing (no search, pure JSON) ──────────────────
    print(f"  [>] Step 2 — Scoring from briefing...")

    full_prompt = build_baseline_prompt(country_name, identity_layer, nsc_level, base_baseline)

    scoring_prompt = f"""You are a travel security analyst. Score the following country based on the intelligence briefing below.

INTELLIGENCE BRIEFING (sourced from current web search, {today}):
{briefing}

{f"BASE LAYER CONTEXT: {json.dumps(base_baseline.get('scores', {}))}" if base_baseline else ""}

SCORING SCALE AND DEFINITIONS:

ARMED CONFLICT — Score based on conflict ON THE COUNTRY'S TERRITORY or directly threatening it.
  GREEN:  No armed conflict. Country is not at war.
  YELLOW: Localized or low-level conflict in remote border areas. Does not affect travelers.
  ORANGE: Active conflict in parts of the country. Traveler movement restricted in conflict zones.
  RED:    Widespread active conflict. Multiple regions unsafe. Capital or major cities threatened.
  PURPLE: Full-scale war. Active fighting in/near major cities. Do not travel.
  NOTE: If a country's military is deployed ABROAD in an overseas conflict but there is no
  fighting on home soil, this does NOT raise the armed_conflict score above YELLOW at most.

REGIONAL INSTABILITY — Score based on how much neighboring/regional conflicts affect THIS country.
  GREEN:  Stable neighborhood. No meaningful spillover risk.
  YELLOW: Some regional tensions. Low direct spillover risk.
  ORANGE: Active regional conflict with documented spillover (refugees, cross-border incidents).
  RED:    Direct threat from regional conflict. Missile/attack risk. Borders under pressure.
  PURPLE: Country is a direct participant or frontline state in a regional war.

TERRORISM — Score based on active terrorist threat to travelers.
  GREEN:  No credible threat. No significant incidents in 5+ years.
  YELLOW: Low-level threat. Occasional incidents but no sustained campaign.
  ORANGE: Elevated threat. Active groups, multiple incidents in past 2 years.
  RED:    High threat. Recent mass-casualty attacks (multiple deaths). Active ongoing campaign.
  PURPLE: Extreme threat. Systematic targeting. Foreign travelers specifically targeted.

CIVIL STRIFE — Score based on political violence affecting travelers.
  GREEN:  Stable. Protests are peaceful and rare.
  YELLOW: Occasional protests. No significant violence. Travelers easily avoid.
  ORANGE: Sustained protests with violence. Some parts of cities unsafe. Travel precautions needed.
  RED:    Widespread unrest. Riots, political violence. Significant parts of country unstable.
  PURPLE: Coup, civil war, or collapse of public order. Do not travel.

CRIME — Score based on crime rates affecting travelers specifically.
  GREEN:  Low crime. Safe for travelers with normal precautions.
  YELLOW: Moderate crime. Petty theft, pickpocketing. Standard urban precautions.
  ORANGE: Elevated crime. Robbery, assault, vehicle theft. Avoid certain areas. Vary precautions.
  RED:    High crime. Kidnapping risk. Violent crime affecting travelers. Significant precautions.
  PURPLE: Extreme crime. Systematic targeting of foreigners. Criminal no-go zones.

HEALTH — Score based on disease risk and healthcare access for travelers.
  GREEN:  Good healthcare. Standard vaccinations sufficient. No significant disease risk.
  YELLOW: Adequate healthcare in cities. Some rural limitations. Minor disease considerations.
  ORANGE: Limited healthcare outside major cities. Some disease risk (malaria, dengue, etc.).
  RED:    Poor healthcare infrastructure. Active disease outbreaks. Medical evacuation likely needed.
  PURPLE: Healthcare system collapsed. Epidemic/pandemic conditions. Extreme medical risk.

INFRASTRUCTURE — Score based on travel infrastructure quality and safety.
  GREEN:  Modern infrastructure. Safe roads, reliable transport, good utilities.
  YELLOW: Generally good but some gaps. Rural roads less safe. Minor transport issues.
  ORANGE: Unreliable infrastructure in parts. Road safety concerns. Power/water interruptions.
  RED:    Poor infrastructure. Dangerous roads. Unreliable utilities. Significant disruption risk.
  PURPLE: Infrastructure collapsed. No reliable transport, power, water, or communications.

CALIBRATION EXAMPLES:
  Australia: Generally GREEN/YELLOW on most categories. Even with the December 2025
  Bondi attack, terrorism is ORANGE-RED (not PURPLE). Armed conflict is YELLOW at most
  (overseas deployment, no fighting on home soil).
  Israel (March 2026): PURPLE armed_conflict, PURPLE regional_instability (active war on
  multiple fronts, Iran missiles, direct threat to entire country).
  France: YELLOW overall. ORANGE terrorism (historical attacks, active threat). GREEN armed_conflict.

VETO RULE: If any of armed_conflict, regional_instability, terrorism, or civil_strife
is RED or PURPLE, the total_score must be at least that level.

Return ONLY this JSON (no markdown, no extra text):
{{
  "scores": {{
    "armed_conflict":       "GREEN|YELLOW|ORANGE|RED|PURPLE",
    "regional_instability": "GREEN|YELLOW|ORANGE|RED|PURPLE",
    "terrorism":            "GREEN|YELLOW|ORANGE|RED|PURPLE",
    "civil_strife":         "GREEN|YELLOW|ORANGE|RED|PURPLE",
    "crime":                "GREEN|YELLOW|ORANGE|RED|PURPLE",
    "health":               "GREEN|YELLOW|ORANGE|RED|PURPLE",
    "infrastructure":       "GREEN|YELLOW|ORANGE|RED|PURPLE"
  }},
  "stability_justifications": {{
    "armed_conflict":       "Why this score. What specific evidence from the briefing. What would change it.",
    "regional_instability": "...",
    "terrorism":            "...",
    "civil_strife":         "...",
    "crime":                "...",
    "health":               "...",
    "infrastructure":       "..."
  }},
  "confidence_levels": {{
    "armed_conflict":       "HIGH|MEDIUM|LOW|INSUFFICIENT",
    "regional_instability": "HIGH|MEDIUM|LOW|INSUFFICIENT",
    "terrorism":            "HIGH|MEDIUM|LOW|INSUFFICIENT",
    "civil_strife":         "HIGH|MEDIUM|LOW|INSUFFICIENT",
    "crime":                "HIGH|MEDIUM|LOW|INSUFFICIENT",
    "health":               "HIGH|MEDIUM|LOW|INSUFFICIENT",
    "infrastructure":       "HIGH|MEDIUM|LOW|INSUFFICIENT"
  }},
  "baseline_narrative": "3-4 paragraphs. Specific, direct. No AI filler. Based strictly on the briefing above.",
  "veto_explanation": "Explain the total score calculation — which categories triggered veto (if any), or weighted average result.",
  "sources_used": ["List specific sources and incidents from the briefing. Be concrete."],
  "recommendations": {{
    "movement_access":        "one concrete sentence",
    "emergency_preparedness": "one concrete sentence",
    "communications":         "one concrete sentence",
    "health_medical":         "one concrete sentence",
    "crime_personal_safety":  "one concrete sentence",
    "travel_logistics":       "one concrete sentence"
  }},
  "watch_factors": "2-4 specific developments to monitor with dates/timeframes where known."
}}"""

    try:
        step2_response = gemini.models.generate_content(
            model="gemini-2.5-flash",
            contents=scoring_prompt,
            config=genai.types.GenerateContentConfig(
                temperature=0.0,
                # No search tool here — keeps output clean JSON
            )
        )

        text = step2_response.text.strip()

        # Strip markdown fences
        if text.startswith("```json"): text = text[7:]
        if text.startswith("```"):     text = text[3:]
        if text.endswith("```"):       text = text[:-3]
        text = text.strip()

        # Extract JSON block (find outermost { })
        start = text.find("{")
        end   = text.rfind("}") + 1
        if start >= 0 and end > start:
            text = text[start:end]

        # Clean common Gemini JSON issues: trailing commas before } or ]
        import re
        text = re.sub(r",\s*([}\]])", r"\1", text)

        analysis = json.loads(text)

        # Attach the briefing so it can be stored for audit
        analysis["_briefing"] = briefing
        print(f"  [OK] Scoring complete")
        return analysis

    except json.JSONDecodeError as e:
        print(f"  [X] JSON parse failed: {e}")
        print(f"  Raw (first 300): {text[:300]}")
        return None
    except Exception as e:
        print(f"  [X] Step 2 scoring failed: {e}")
        return None


# =============================================================================
# Storage
# =============================================================================

def store_baseline(country_id, country_name, identity_layer, analysis, version_number):
    """
    Store Tier 1 baseline to:
    1. baseline_versions (immutable, reviewed_by='pending')
    2. score_history (so dashboard shows scores immediately)
    3. review_queue (flags for owner approval)

    Returns baseline_version_id or None on failure.
    """
    scores      = analysis.get("scores", {})
    total_score = calculate_total_score(scores)

    # -- 1. baseline_versions ------------------------------------------------
    try:
        baseline_row = {
            "country_id":               country_id,
            "identity_layer":           identity_layer,
            "version_number":           version_number,
            "scores":                   json.dumps(scores),
            "total_score":              total_score,
            "stability_justifications": json.dumps(analysis.get("stability_justifications", {})),
            "confidence_levels":        json.dumps(analysis.get("confidence_levels", {})),
            "baseline_narrative":       analysis.get("baseline_narrative", ""),
            "sources_used":             json.dumps(analysis.get("sources_used", [])),
            "reviewed_by":              "pending",
            "created_at":               datetime.now(timezone.utc).isoformat(),
        }
        result = supabase.table("baseline_versions").insert(baseline_row).execute()
        baseline_version_id = result.data[0]["id"]
        print(f"  [OK] Stored in baseline_versions (id: {baseline_version_id[:8]}...)")
    except Exception as e:
        print(f"  [X] baseline_versions insert failed: {e}")
        return None

    # -- 2. score_history ----------------------------------------------------
    try:
        history_row = {
            "country_id":           country_id,
            "identity_layer":       identity_layer,
            "total_score":          total_score,
            "scores":               json.dumps(scores),
            "ai_summary":           analysis.get("baseline_narrative", ""),
            "veto_explanation":     analysis.get("veto_explanation", ""),
            "recommendations":      json.dumps(analysis.get("recommendations", {})),
            "watch_factors":        analysis.get("watch_factors", ""),
            "sources":              json.dumps(analysis.get("sources_used", [])),
            "confidence":           json.dumps(analysis.get("confidence_levels", {})),
            "baseline_version_id":  baseline_version_id,
            "tier":                 1,
            "created_at":           datetime.now(timezone.utc).isoformat(),
        }
        supabase.table("score_history").insert(history_row).execute()
        print(f"  [OK] Stored in score_history (tier=1, total={total_score})")
    except Exception as e:
        print(f"  [X] score_history insert failed: {e}")
        # Not fatal - baseline is stored, dashboard can still query it via view

    # -- 3. review_queue -----------------------------------------------------
    try:
        review_row = {
            "country_id":     country_id,
            "identity_layer": identity_layer,
            "proposal":       json.dumps({
                "type":             "tier1_baseline",
                "country":          country_name,
                "layer":            identity_layer,
                "version":          version_number,
                "total_score":      total_score,
                "scores":           scores,
                "baseline_version_id": baseline_version_id,
                "note":             "Tier 1 baseline drafted by AI. Requires owner review before going live."
            }),
            "priority":       "STANDARD",
            "triggered_by":   "tier1_baseline_pipeline",
            "created_at":     datetime.now(timezone.utc).isoformat(),
        }
        supabase.table("review_queue").insert(review_row).execute()
        print(f"  [OK] Added to review_queue for owner approval")
    except Exception as e:
        print(f"  [!] review_queue insert failed (non-fatal): {e}")

    return baseline_version_id


# =============================================================================
# Country/Layer Orchestrator
# =============================================================================

def run_country_baseline(country_name, iso_code, layers, force=False):
    """
    Run Tier 1 baseline for all specified layers of a country.
    Layers are processed in order: base first, then identity layers
    (identity layers receive the base analysis as context).
    """
    print(f"\n{'='*60}")
    print(f"  TIER 1 BASELINE: {country_name} ({iso_code})")
    print(f"  Layers: {', '.join(layers)}")
    print(f"{'='*60}")

    country_id = get_country_id(iso_code)
    if not country_id:
        return False

    nsc_data  = load_nsc_warnings()
    nsc_level = nsc_data.get(country_name, {}).get("level") if "jewish_israeli" in layers else None

    base_baseline = None  # Will hold base analysis for identity layer context

    for layer in layers:
        print(f"\n  -- Layer: {layer} --")

        # Check if baseline already exists
        exists, reviewed_by, current_version = baseline_already_exists(country_id, layer)

        if exists and not force:
            print(f"  [SKIP] Baseline v{current_version} already exists (reviewed_by={reviewed_by})")
            print(f"         Use --force to create a new version (Tier 3 rebalancing)")

            # Load existing base baseline for identity layer context
            if layer == "base":
                try:
                    result = (
                        supabase.table("baseline_versions")
                        .select("scores")
                        .eq("country_id", country_id)
                        .eq("identity_layer", "base")
                        .order("version_number", desc=True)
                        .limit(1)
                        .execute()
                    )
                    if result.data:
                        base_baseline = {"scores": json.loads(result.data[0]["scores"])}
                except Exception:
                    pass
            continue

        next_version = current_version + 1 if force and exists else 1

        if force and exists:
            print(f"  [REBALANCE] Creating v{next_version} (Tier 3 rebalancing)")
        else:
            print(f"  [NEW] Creating v1 baseline")

        # Run analysis
        analysis = run_baseline_analysis(
            country_name  = country_name,
            identity_layer = layer,
            nsc_level     = nsc_level if layer == "jewish_israeli" else None,
            base_baseline = base_baseline if layer != "base" else None,
        )

        if not analysis:
            print(f"  [X] Analysis failed for {country_name}/{layer} - skipping")
            continue

        # Store
        baseline_id = store_baseline(country_id, country_name, layer, analysis, next_version)

        if baseline_id and layer == "base":
            # Save base analysis as context for identity layers
            base_baseline = analysis

        # Print summary
        scores = analysis.get("scores", {})
        total  = calculate_total_score(scores)
        print(f"\n  SCORES ({layer}):")
        print(f"    Total: {total}")
        for cat, score in scores.items():
            print(f"    {cat}: {score}")

    return True


# =============================================================================
# Main
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Travint.ai - Tier 1 Baseline Establishment Pipeline"
    )
    parser.add_argument("--country",       type=str, help="Country name (e.g. 'France')")
    parser.add_argument("--iso",           type=str, help="ISO code (e.g. 'FR') - alternative to --country")
    parser.add_argument("--layer",         type=str, choices=ALL_LAYERS, help="Single identity layer")
    parser.add_argument("--all-layers",    action="store_true", help="Run all identity layers")
    parser.add_argument("--all-countries", action="store_true", help="Run all countries in the system")
    parser.add_argument("--force",         action="store_true",
                        help="Force new baseline version even if one exists (Tier 3 rebalancing)")

    args = parser.parse_args()

    print("=" * 60)
    print("  Travint.ai - Tier 1 Baseline Pipeline")
    print("=" * 60)
    print(f"  Started: {datetime.now(timezone.utc).isoformat()} UTC\n")

    # Determine layers
    if args.all_layers:
        layers = ALL_LAYERS
    elif args.layer:
        layers = [args.layer]
    else:
        layers = ["base"]  # Default to base layer only
        print("[!] No layer specified - defaulting to base layer only")
        print("    Use --layer solo_women or --all-layers to include identity layers\n")

    # Determine countries
    if args.all_countries:
        countries = ALL_COUNTRIES
    elif args.iso:
        match = [(n, c) for n, c in ALL_COUNTRIES if c.upper() == args.iso.upper()]
        if not match:
            print(f"[X] ISO code '{args.iso}' not found in country list")
            sys.exit(1)
        countries = match
    elif args.country:
        match = [(n, c) for n, c in ALL_COUNTRIES if n.lower() == args.country.lower()]
        if not match:
            print(f"[X] Country '{args.country}' not found in country list")
            print(f"    Available: {', '.join(n for n, _ in ALL_COUNTRIES)}")
            sys.exit(1)
        countries = match
    else:
        print("[X] Specify --country, --iso, or --all-countries")
        parser.print_help()
        sys.exit(1)

    print(f"  Countries : {len(countries)}")
    print(f"  Layers    : {layers}")
    print(f"  Force     : {args.force}")
    print()

    # Run baselines
    success = 0
    failed  = 0
    for country_name, iso_code in countries:
        ok = run_country_baseline(country_name, iso_code, layers, force=args.force)
        if ok:
            success += 1
        else:
            failed += 1

    print(f"\n{'='*60}")
    print(f"  DONE - {success} countries completed, {failed} failed")
    print(f"  Finished: {datetime.now(timezone.utc).isoformat()} UTC")
    print(f"{'='*60}")
    print()
    print("  [!]  All baselines are marked 'pending' until owner review.")
    print("     Review in the admin panel - Review Queue.")
    print("     Dashboard already shows the scores.")


if __name__ == "__main__":
    main()
