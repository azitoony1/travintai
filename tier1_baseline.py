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
    Total score logic — three layers applied in priority order:

    LAYER 1 — HARD VETO (armed_conflict PURPLE only):
      armed_conflict PURPLE → total PURPLE
      armed_conflict RED does NOT hard veto. RED means serious conflict exists but
      safe zones are identifiable — a traveler can reduce risk by destination choice.
      Only PURPLE (nationwide war, no safe zones, evacuation may be impossible) forces
      the total regardless of other categories.

    LAYER 2 — WEIGHTED AVERAGE (determines base total when no hard veto):
      All 7 categories contribute. Security categories count DOUBLE:
        double weight: armed_conflict, regional_instability, terrorism, civil_strife
        single weight: crime, health, infrastructure
      regional_instability has NO hard veto — a dangerous neighbourhood raises the average
      but does not force a specific total by itself.

    LAYER 3 — SOFT FLOORS (terrorism and civil_strife — floor only, avg can push higher):
      terrorism  PURPLE → total at least RED   (near-weekly attacks = serious, not auto-PURPLE)
      terrorism  RED    → total at least ORANGE
      civil_strife PURPLE → total at least RED (coup/civil war = serious, not auto-PURPLE total)
      civil_strife RED    → total at least ORANGE
      These floors prevent a country with severe terrorism but otherwise normal conditions
      from scoring too low, while not automatically making it PURPLE.

    RESULT: The highest of (hard veto | weighted avg | soft floor) wins.
    """
    all_categories = ["armed_conflict", "regional_instability", "terrorism", "civil_strife",
                      "crime", "health", "infrastructure"]
    security_cats  = {"armed_conflict", "regional_instability", "terrorism", "civil_strife"}
    level_to_int   = {"GREEN": 1, "YELLOW": 2, "ORANGE": 3, "RED": 4, "PURPLE": 5}
    int_to_level   = {1: "GREEN", 2: "YELLOW", 3: "ORANGE", 4: "RED", 5: "PURPLE"}

    def lvl(cat):
        return level_to_int.get(category_scores.get(cat, "GREEN"), 1)

    # ── LAYER 1: Hard veto — armed_conflict PURPLE only ──────────────────────
    # RED does NOT hard veto. RED means "serious conflict, safe zones exist."
    # Only PURPLE (nationwide war, no safe zones, evacuation may be impossible)
    # forces the total. A traveler in a RED armed_conflict country can still
    # meaningfully reduce risk by choosing where to go — so the total is
    # determined by the weighted average and soft floors, not a veto.
    ac = lvl("armed_conflict")
    if ac >= 5:
        return "PURPLE"

    # ── LAYER 2: Weighted average ─────────────────────────────────────────────
    weighted_sum = sum(
        lvl(cat) * (2 if cat in security_cats else 1)
        for cat in all_categories
    )
    total_weight = sum(2 if cat in security_cats else 1 for cat in all_categories)
    avg = weighted_sum / total_weight

    if avg <= 1.4:   raw = "GREEN"
    elif avg <= 2.4: raw = "YELLOW"
    elif avg <= 3.4: raw = "ORANGE"
    elif avg <= 4.4: raw = "RED"
    else:            raw = "PURPLE"

    # ── LAYER 3: Soft floors — terrorism and civil_strife ────────────────────
    ter = lvl("terrorism")
    cs  = lvl("civil_strife")
    max_ter_cs = max(ter, cs)

    if max_ter_cs == 5:   floor = "RED"     # PURPLE terror/strife → at least RED
    elif max_ter_cs == 4: floor = "ORANGE"  # RED terror/strife → at least ORANGE
    else:                 floor = "GREEN"   # no floor

    return int_to_level[max(level_to_int[raw], level_to_int[floor])]


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

GREEN  (1): Normal conditions. Travel with standard precautions.
YELLOW (2): Elevated structural risk. Be aware, make contingency plans.
ORANGE (3): Significant risk. Meaningful precautions required. Some areas to avoid.
RED    (4): High risk. Reconsider travel. Serious documented threats requiring real security
            planning. State protection is partial but functional. Evacuation is possible.
            Well-prepared travelers with clear justification can go.
PURPLE (5): Do not travel. Conditions where even maximum civilian preparation cannot
            reduce risk to an acceptable level. Reserved for: active full-scale war;
            state collapse; or countries where the state itself systematically targets
            foreign nationals. Evacuation may be impossible. Consular protection absent
            or unreliable. This is not "be very careful" — it means do not go.

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

MANDATORY CIVIL STRIFE FLOORS FOR SOLO WOMEN:
These are hard minimums — apply them regardless of recent reforms or reform trajectory:

  Legally enforced dress code with criminal penalties (e.g. mandatory hijab with
  police enforcement, Saudi Arabia, Iran): civil_strife MINIMUM ORANGE.
  Do NOT score GREEN or YELLOW even if enforcement has become less strict recently.
  The law exists and can be applied against a foreign woman traveler.

  Male guardianship laws restricting women's independent movement or hotel check-in
  (Saudi Arabia historically, Afghanistan): civil_strife MINIMUM RED, total MINIMUM RED.
  A solo woman traveler who legally cannot stay in a hotel alone or move freely
  without a male companion faces a structural RED risk regardless of other factors.

  Active crackdown on women's rights with documented arrests of women for dress/behavior
  (Iran 2022-present): civil_strife raises to RED or PURPLE.

TERRORISM: Do NOT raise terrorism above base layer unless the briefing documents
specific deliberate targeting of women by terrorist actors. General terrorism risk
affects all travelers equally. Do not inflate for gender unless there is specific evidence.

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

    # Models tried in order — 2.5 Flash preferred (best quality + search grounding)
    # 1.5 Flash is the stable fallback (also supports search grounding, less demand)
    STEP1_MODELS = ["gemini-2.5-flash", "gemini-1.5-flash"]
    STEP2_MODELS = ["gemini-2.5-flash", "gemini-1.5-flash"]

    import time as _time

    briefing = None
    for attempt in range(1, 4):  # up to 3 retries
        for model_name in STEP1_MODELS:
            try:
                step1_response = gemini.models.generate_content(
                    model=model_name,
                    contents=briefing_prompt,
                    config=genai.types.GenerateContentConfig(
                        tools=[genai.types.Tool(google_search=genai.types.GoogleSearch())],
                        temperature=0.0,
                    )
                )
                briefing = step1_response.text.strip()
                print(f"  [OK] Briefing complete ({len(briefing)} chars) [{model_name}]")
                break  # success — stop trying models
            except Exception as e:
                err = str(e)
                print(f"  [!] Step 1 failed on {model_name} (attempt {attempt}): {err[:80]}")
        if briefing:
            break
        wait = attempt * 30
        print(f"  [!] All models overloaded — waiting {wait}s before retry {attempt+1}/3...")
        _time.sleep(wait)

    if not briefing:
        print(f"  [X] Step 1 failed after 3 attempts — skipping")
        return None

    # ── STEP 2: Scoring from Briefing (no search, pure JSON) ──────────────────
    print(f"  [>] Step 2 — Scoring from briefing...")

    full_prompt = build_baseline_prompt(country_name, identity_layer, nsc_level, base_baseline)

    scoring_prompt = f"""You are a travel security analyst. Score the following country based on the intelligence briefing below.

INTELLIGENCE BRIEFING (sourced from current web search, {today}):
{briefing}

{f"BASE LAYER CONTEXT: {json.dumps(base_baseline.get('scores', {}))}" if base_baseline else ""}

=== CRITICAL RULE: CATEGORY INDEPENDENCE ===

Score each of the 7 categories COMPLETELY INDEPENDENTLY.

A country's score in one category has ZERO automatic effect on any other category.
A country can be at war (armed_conflict PURPLE) and still have:
  - GREEN health        if hospitals are functioning and traveler medical care is available
  - GREEN infrastructure if roads, power, water, and internet work normally
  - YELLOW crime        if criminals are not targeting travelers

DO NOT inflate health, infrastructure, or crime scores just because there is a conflict.
Only score those categories higher if the briefing contains SPECIFIC evidence that
those systems are damaged, collapsed, or directly affected.

EXAMPLES OF CORRECT CATEGORY INDEPENDENCE:
  Israel (March 2026, active war):
    - armed_conflict PURPLE  ✓ (active multi-front war)
    - terrorism PURPLE       ✓ (near-daily attacks, terrorism integral to war)
    - health GREEN           ✓ (world-class hospitals, Hadassah/Sourasky/Sheba operating normally)
    - infrastructure YELLOW  ✓ (roads, power, water, internet all function. Minor disruption
                                from missile alerts. No infrastructure collapse.)
    - crime YELLOW           ✓ (low civilian crime, travelers not targeted by criminals)

  Iran (March 2026, authoritarian state at war):
    - armed_conflict PURPLE  ✓ (direct war with Israel, Israeli airstrikes, Iranian missile attacks)
    - civil_strife PURPLE    ✓ (systematic crackdown, protesters killed, no rule of law for dissent)
    - terrorism PURPLE       ✓ (state sponsor of terror, IRGC operations, foreign hostages)
    - infrastructure ORANGE  ✓ (roads work, power works with cuts, internet heavily censored —
                                NOT collapsed. Iran is a functioning country. Roads, bridges,
                                water systems all operate. Score based on traveler experience,
                                not geopolitical tension.)
    - health ORANGE          ✓ (hospitals function but lack some medicines due to sanctions.
                                Urban hospitals can treat emergencies. NOT collapsed.)
    - crime YELLOW           ✓ (Iran has LOWER street crime than most Middle Eastern countries.
                                Traveler mugging/robbery is rare. NOT PURPLE.)

  Netherlands:
    - crime GREEN            ✓ (~0.9 homicides per 100k — well below YELLOW threshold of 5)
    - terrorism ORANGE       ✓ (2019 Utrecht attack, ongoing credible threats)

  Australia:
    - health GREEN           ✓ (world-class healthcare system — same tier as EU, USA, Japan)
    - infrastructure GREEN   ✓ (road fatality rate ~4/100k, modern utilities — clearly GREEN)
    - crime GREEN            ✓ (~1.8 homicides/100k — clearly GREEN)

  France:
    - armed_conflict GREEN   ✓ (no fighting on French territory)
    - crime YELLOW           ✓ (~1.3 homicides/100k — YELLOW threshold starts at 5)
    - civil_strife ORANGE    ✓ (periodic protests with violence, not RED widespread unrest)

  Poland:
    - armed_conflict YELLOW  ✓ (no fighting on Polish territory despite bordering Ukraine)
    - crime GREEN/YELLOW     ✓ (~0.7 homicides/100k — GREEN by any measure)
    - health YELLOW          ✓ (adequate healthcare, EU member state)

=== STRICT PURPLE LIMITS FOR HEALTH, CRIME, INFRASTRUCTURE ===

NEVER assign PURPLE to health, crime, or infrastructure unless:

  Health PURPLE:    Healthcare system has physically COLLAPSED. Hospitals are bombed/closed.
                    No emergency care available anywhere in major cities.
                    (True examples: Yemen 2023, Syria 2015-2019, Gaza 2024)
                    NOT PURPLE just because: sanctions strain supplies, system is under-funded,
                    or doctors are leaving. Iran, Russia, North Korea = ORANGE at most.

  Crime PURPLE:     Travelers are SYSTEMATICALLY targeted by criminal organisations.
                    No-go zones where criminals control territory and police don't enter.
                    (True examples: parts of Mexico gang territory, parts of DRC)
                    NOT PURPLE just because: the country is authoritarian, or there are
                    protests, or it's a war zone. Iran = YELLOW (low street crime).

  Infrastructure PURPLE: Infrastructure has PHYSICALLY COLLAPSED. No roads, no power,
                    no water, no communications in major cities.
                    (True examples: Libya active war zones, Yemen 2023)
                    NOT PURPLE just because: power cuts are frequent, or roads are poor,
                    or internet is censored. Iran, Russia = ORANGE. Israel = YELLOW.

If you are considering PURPLE for health, crime, or infrastructure, ask yourself:
"Can a traveler in the capital city get emergency medical treatment, walk the streets
without being robbed, and get from A to B?" If yes → not PURPLE.

=== END CRITICAL RULE ===

SCORING SCALE AND DEFINITIONS:

CRITICAL PRINCIPLE — ARMED CONFLICT:
Score what travelers PHYSICALLY ENCOUNTER on the ground, NOT the country's
political or military involvement in a conflict.

The armed_conflict score describes the PHYSICAL THREAT on the country's territory.
Political involvement, sanctions, or diplomatic hostility do NOT raise armed_conflict —
only physical fighting or attacks on the country's territory counts.

armed_conflict RED does NOT automatically make the total RED. It is a serious
contributor to the weighted average but the total score is determined by all 7
categories together. A country can have armed_conflict RED and total ORANGE if
other categories are low.

armed_conflict PURPLE hard-vetoes the total to PURPLE. Use PURPLE only when the
physical threat meets the criteria checklist above (2 of 4 criteria).

ARMED CONFLICT — Score based on conflict ON THE COUNTRY'S TERRITORY or directly threatening it.
  GREEN:  No armed conflict. Country is not at war.
  YELLOW: Localized or low-level conflict in remote border areas only. Does not affect
          traveler movement. OR overseas military deployment with zero home-soil fighting.
  ORANGE: Active conflict in parts of the country. Traveler movement restricted in conflict
          zones. Capital and major cities safe. Under 500 deaths/month nationally.
  RED:    Widespread conflict affecting multiple regions OR capital/large cities threatened.
          (Either condition alone is sufficient — does not require both.)
          OR regular missile/rocket/airstrike attacks on populated areas regardless of
          death count — even intercepted missiles count if they are routine and ongoing.
          At least one identifiable safe region with significant population exists —
          a traveler can still meaningfully reduce physical risk by choosing destination.
  PURPLE: State has effectively collapsed AND/OR active ground combat in the capital city
          with no functioning civil authority AND no meaningful safe zones AND evacuation
          is genuinely impossible. Attacks are nationwide and no civil defense exists to
          help civilians protect themselves. A traveler CANNOT reduce physical conflict
          risk by any reasonable preparation or destination choice.
          This is NOT simply "at war". A country fighting back effectively with a
          functioning government, military, sirens, shelters, and open airports is RED.
          PURPLE is for collapsed states: Haiti, Somalia, Sudan (Khartoum during RSF
          offensive), Gaza 2024, Syria 2015-2019. NOT for Israel, Ukraine, Lebanon.
  NOTE: Overseas military deployment = YELLOW at most, never RED or PURPLE.
  NOTE: Regular incoming missiles = RED minimum, even if mostly intercepted.
  NOTE: Being the aggressor/initiator of a war fought on another country's soil = YELLOW.
  NOTE: Active war + functioning state + civil defense + open airport = RED, not PURPLE.

REGIONAL INSTABILITY — Score based on how much neighboring/regional conflicts affect THIS country.
  GREEN:  Stable neighborhood. No meaningful spillover risk.
  YELLOW: Some regional tensions. Low direct spillover risk.
  ORANGE: Active regional conflict with documented spillover (refugees, cross-border incidents).
  RED:    Direct threat from regional conflict. Missile/attack risk from neighbors.
          Borders under pressure. Meaningful chance of being drawn into conflict.
  PURPLE: Country is a direct participant or frontline state in a regional war.

TERRORISM — Score based on active terrorist threat to travelers.
  GREEN:  No credible threat. No significant attacks in 5+ years. No active organised groups.
  YELLOW: Low-level threat. Foiled plots or minor incidents only. No fatalities in 3+ years.
          OR a single isolated incident with no evidence of repeat capability.
  ORANGE: Credible active threat. An organised group with demonstrated capability exists.
          1-2 attacks with casualties (1-4 deaths each) in past 2 years. OR a single
          major lone-wolf attack (even with many deaths) with NO evidence of organised
          group or repeat threat — treat as ORANGE until further attacks occur.
  RED:    Active organised campaign. Multiple attacks in past 2 years with deaths. OR
          3+ attacks (any scale, including foiled) in past 12 months by an identified group
          with stated intent to continue. OR persistent monthly incidents (even if low
          casualty) by an active group. OR systematic targeting of a specific ethnic/
          national group in multiple separate incidents by organised actors.
          KEY: RED requires evidence of an organised group with proven capability AND
          ongoing motivation — a single lone-wolf attack does NOT automatically = RED,
          even if it killed 6+ people.
  PURPLE: Extreme, sustained campaign. Weekly or near-weekly attacks of any scale by
          organised actors. OR 3+ attacks with multiple deaths (2+) each in the past year.
          OR terrorism is integral to an active war (attacks are part of military campaign).
          PURPLE is the superlative of RED — it requires both frequency AND evidence of
          sustained organised capability, not just a single catastrophic event.
  NOTE: Frequency + organised group = key discriminators. Scale alone is not sufficient.

CIVIL STRIFE — Score based on political violence affecting travelers.
  GREEN:  Stable. Protests are peaceful and rare.
  YELLOW: Occasional protests. No significant violence. Travelers easily avoid.
  ORANGE: Sustained protests with violence. Some parts of cities unsafe. Travel precautions needed.
  RED:    Widespread unrest. Riots, political violence. Significant parts of country unstable.
  PURPLE: Coup, civil war, or collapse of public order. Do not travel.

CRIME — Score based on crime rates affecting travelers specifically.
  GREEN:  Low crime. Safe for travelers with normal precautions. Petty theft possible.
  YELLOW: Moderate crime. Pickpocketing in tourist areas. Standard urban awareness. Rare
          violent incidents. Travelers are not a specific target.
  ORANGE: Elevated crime. Robbery, assault, vehicle theft possible. Avoid certain areas.
          Kidnapping risk limited to specific provinces. Heightened precautions needed.
  RED:    High crime. Documented kidnapping-for-ransom targeting foreigners. Violent crime
          common. Criminal gangs active. Significant precautions required.
  PURPLE: Criminal organisations exercise SUBSTANTIAL territorial control over multiple
          states, provinces, or large regions — state has effectively ceded governance of
          significant territory. Travelers face systemic risk throughout those regions.
          NOTE: A criminal gang controlling a neighbourhood, or cartel activity in one
          city, does NOT = PURPLE. Substantial means entire regions/states where the
          state cannot operate. (Examples: parts of DRC, certain Mexican states.
          The USA overall is NOT PURPLE even with gang violence in specific cities.)

HEALTH — Score based on disease risk and healthcare access for travelers.
  GREEN:  Good healthcare. Standard vaccinations sufficient. No significant disease risk.
  YELLOW: Adequate healthcare in cities. Some rural limitations. Minor disease considerations.
  ORANGE: Limited healthcare outside major cities. Some disease risk (malaria, dengue, etc.).
  RED:    Poor healthcare infrastructure. Active disease outbreaks. Medical evacuation likely needed.
  PURPLE: Healthcare system collapsed. Epidemic/pandemic conditions. Extreme medical risk.

INFRASTRUCTURE — Score based on the PHYSICAL STATE of roads, power, water, and transport.
  GREEN:  Modern, reliable infrastructure. Safe roads, reliable power/water/internet.
  YELLOW: Generally good with some gaps. Rural roads less safe. Urban utilities reliable.
  ORANGE: Unreliable infrastructure. Frequent power/water outages. Roads dangerous in regions.
  RED:    Poor infrastructure nationwide. OR infrastructure physically damaged by conflict.
  PURPLE: Infrastructure PHYSICALLY COLLAPSED in major cities. No roads, power, water, comms.
  CRITICAL: Score infrastructure on PHYSICAL state only — not on security conditions.
  Missile alerts, curfews, and security restrictions are armed_conflict factors.
  A country where roads/power/water/internet function normally = GREEN or YELLOW on
  infrastructure even during an active war. Only score higher if the briefing contains
  SPECIFIC evidence of physical infrastructure destruction or system-wide failure.
  Israel (March 2026) = YELLOW infrastructure (roads work, power/water/internet normal,
  minor disruption from alerts). Iran = ORANGE (sanctions cause shortages, not collapse).

QUANTITATIVE THRESHOLDS:

IMPORTANT — APPROXIMATE ANCHORS, NOT HARD CUTOFFS:
These numbers are calibration anchors, not scientific thresholds. Real-world data is
noisy — especially outside OECD countries where reporting is incomplete or delayed.
When data quality is LOW, use the number as a guide but apply judgment. A country
reporting 14.8 homicides/100k with poor reporting systems may be effectively ORANGE.
A country at 31/100k with well-documented data and good enforcement may trend toward
RED but warrant MEDIUM confidence. Confidence levels matter as much as the scores.

TERRORISM thresholds:
  GREEN:  0 attacks with fatalities in past 5 years. No credible active groups operating.
  YELLOW: 0-1 deaths from terrorism in past 3 years. Foiled plots only. OR a single
          isolated lone-wolf attack with no evidence of organised group behind it and
          no further attacks since. Lone-wolf = YELLOW until repeat pattern emerges.
  ORANGE: Organised group with demonstrated attack capability exists in the country.
          1-2 attacks with 1-4 deaths each in past 2 years by an organised actor.
          A lone-wolf attack with high death count (even 6+) but NO organised group
          = ORANGE, not RED. Do not score RED for a lone-wolf incident alone.
  RED:    Active organised campaign with documented repeat intent. Requires BOTH:
          (a) an identified organised group with stated/demonstrated ongoing intent, AND
          (b) multiple attacks in past 2 years (2+) with deaths, OR 3+ attacks in past
          12 months by the same or affiliated group, OR persistent monthly incidents.
          Systematic targeting of a specific ethnic/national group in multiple incidents
          by organised actors also = RED.
  PURPLE: Sustained high-frequency organised campaign: weekly/near-weekly attacks of any
          scale by organised actors. OR 3+ attacks each with 2+ deaths in the past year
          by organised groups. OR terrorism is integral to an active war (military campaign).
          PURPLE = superlative of RED. Both frequency AND organised capability required.

ARMED CONFLICT thresholds:
  GREEN:  No fighting on national territory.
  YELLOW: Historical/frozen conflict in remote border areas only. OR overseas military
          deployment with zero home-territory fighting.
  ORANGE: Active conflict in less than ~20% of territory. Capital and major cities safe.
          Under ~500 conflict deaths per month (approximate — use judgment on data quality).
  RED:    Widespread conflict affecting multiple regions. OR regular missile/rocket attacks
          OR airstrikes on populated areas — regardless of death count. Capital or major
          cities threatened. OR ~500+ conflict deaths per month nationally.
          NOTE: Regular incoming missiles/rockets = RED minimum, even if intercepted.
  PURPLE: State collapse AND active ground combat in capital with no civil defense AND
          no safe zones AND evacuation impossible. Same standard as main checklist.
          A war-fighting nation with functioning government, military, shelters, and
          open airports is RED. PURPLE = collapsed state (Haiti, Somalia, Sudan/RSF
          offensive, Gaza 2024, Syria 2015). NOT Israel, NOT Ukraine, NOT Lebanon.

  CROSS-BORDER KINETIC TIE-BREAKER (regional_instability vs armed_conflict boundary):
  Use this when kinetic activity originates from outside the country but hits its territory:
  If 3+ documented kinetic incidents (missile strikes, drone attacks, shelling, armed
  incursions) originating from outside this country and deliberately targeting its territory
  have occurred in the past 12 months → armed_conflict minimum ORANGE, regardless of
  whether attacks were intercepted. This distinguishes grey-zone conflict (which crosses
  into armed_conflict territory) from mere regional tension (regional_instability only).
  Examples: Houthi missiles at Saudi Arabia = armed_conflict ORANGE+; Ukrainian drones
  hitting Belgorod = armed_conflict ORANGE for Russia (not just regional_instability).

CRIME thresholds (intentional homicide rate per 100,000/year as primary anchor — supplement
with kidnapping risk, organised crime penetration, and armed robbery patterns):
  GREEN:  Under 5 per 100k. Low organised crime. Travelers safe with normal precautions.
          Petty theft possible but violent crime against travelers rare.
  YELLOW: 5-15 per 100k. Petty theft, pickpocketing common in tourist areas. Standard
          urban awareness needed. Violent crime against travelers uncommon.
  ORANGE: 15-30 per 100k. OR kidnapping risk in specific provinces/areas. Robbery and
          carjacking possible. Avoid certain neighbourhoods. Heightened precautions.
  RED:    30-60 per 100k. OR documented kidnapping-for-ransom targeting foreigners.
          OR criminal organisations controlling significant territory traveler may cross.
          Significant security precautions required. High-value areas/vehicles targeted.
  PURPLE: Over 60 per 100k. OR criminal organisations exercise SUBSTANTIAL territorial
          control over multiple states/provinces — meaning the state has effectively
          ceded governance of significant geographic areas (not just neighbourhoods).
          Examples: parts of DRC (no state presence), certain Mexican states (cartel rule).
          NOT PURPLE: gang violence in specific city neighbourhoods; cartel presence in
          one city; high crime with state still functioning nationally. The USA is NOT
          PURPLE despite gang violence. Mexico overall is RED (not PURPLE) despite cartels.
  NOTE: Homicide rate is the anchor. Supplement with kidnapping risk and territorial
  control evidence. A country needs MULTIPLE serious crime factors to reach PURPLE.

HEALTH thresholds:
  GREEN:  High-income country with functional hospital system. Routine vaccinations sufficient.
          No active disease outbreaks. (Australia, EU, USA, Japan, Singapore = GREEN)
  YELLOW: Adequate urban healthcare. Some rural limitations. Minor endemic disease risk
          (e.g. traveler's diarrhoea). Travel health insurance advisable.
  ORANGE: Limited healthcare outside major cities. Active endemic diseases (malaria, dengue,
          cholera in specific regions). Medical evacuation insurance strongly recommended.
  RED:    Poor healthcare infrastructure nationwide. Active epidemic or outbreak.
          Standard surgical care not reliably available. Medical evacuation very likely needed.
  PURPLE: Healthcare system has collapsed or been destroyed. No safe medical care available.

INFRASTRUCTURE thresholds (road fatality rate per 100,000/year as primary anchor — also
consider power/water reliability, internet access, and transport system reliability):
  GREEN:  Road fatality rate under 10 per 100k/year. Reliable power, water, internet.
          Modern transport infrastructure. (Australia, EU, USA, Japan = GREEN)
  YELLOW: Road deaths 10-20 per 100k. Generally reliable utilities. Some rural or
          seasonal gaps. Public transport functional but variable quality.
  ORANGE: Road deaths 20-30 per 100k. OR frequent power outages affecting travel planning.
          OR unreliable public transport. Rural roads dangerous. Water supply variable.
  RED:    Road deaths over 30 per 100k. OR utilities unreliable nationwide.
          OR transport infrastructure significantly damaged (conflict/disaster).
  PURPLE: Infrastructure has collapsed. No reliable transport, power, water, or communications.
          Movement extremely dangerous or impossible without private security.

CALIBRATION EXAMPLES:
  Australia: Crime GREEN (<2 homicides/100k). Health GREEN (excellent hospitals).
  Infrastructure GREEN (modern). Armed_conflict YELLOW (overseas deployment only, no
  home-soil fighting). Terrorism: ORANGE if the Dec 2025 Bondi attack is classified as
  terrorism (6 deaths, lone attacker — motive disputed). YELLOW if classified as criminal/
  mental health incident rather than political violence. Use your judgement on classification.

  Israel (March 2026): PURPLE armed_conflict (multi-front war, daily missile strikes on
  cities — regular incoming rockets = RED minimum; active war = PURPLE). PURPLE terrorism
  (near-daily incidents across the country, terrorism integral to active war).

  France: ORANGE terrorism (2015-2024 sustained attack history; Nice, Paris, Strasbourg;
  active threat level declared). GREEN armed_conflict. YELLOW crime (~1.3 homicides/100k).

  Netherlands: YELLOW overall. Crime YELLOW (~0.9 homicides/100k, organized crime present
  but travelers not targeted). Terrorism ORANGE (credible threats, 2019 Utrecht attack).

  Mexico: RED crime (>25 homicides/100k nationally, cartel kidnapping risk in multiple states,
  cartel territorial control in significant areas). ORANGE civil_strife (cartel violence
  affecting civilian movement). Infrastructure ORANGE (road safety poor in cartel zones).

  Poland: armed_conflict YELLOW — NOT RED. No fighting on Polish territory. Poland is a
  NATO member. Occasional Russian drone incursions into Polish airspace do not constitute
  active armed conflict on Polish soil. regional_instability RED (Ukraine war on border,
  Russian threat, militarisation of Belarus border). crime GREEN (~0.7 homicides/100k).
  health YELLOW (EU member, adequate hospitals). infrastructure GREEN (modern EU roads).
  Total will be ORANGE from weighted average (regional instability RED raises the average
  but does not trigger the armed_conflict hard veto since armed_conflict = YELLOW).

  Russia (March 2026): armed_conflict ORANGE maximum — NOT RED or PURPLE. Russia is the
  aggressor in the Ukraine war, but the active fighting is IN UKRAINE, not on Russian
  territory in any traveler-meaningful way. Some drone strikes have hit border regions
  (Belgorod oblast) and occasionally Moscow suburbs, but there is NO active combat in
  Moscow, St. Petersburg, or other major cities. A traveler to Moscow or St. Petersburg
  is NOT in a war zone. Score ORANGE at most (acknowledging Belgorod border incidents),
  NOT RED or PURPLE. Do NOT let Russia's role as war initiator inflate its armed_conflict
  score — score only what physically threatens travelers on Russian soil.
  Russia total = RED, driven by civil_strife RED (detention of Western nationals, wartime
  repression, arrests of foreigners) and regional_instability PURPLE (Russia IS the
  regional war — it is a frontline actor, not just a neighbor). Armed_conflict at ORANGE
  means the hard veto does NOT fire; the RED total comes from weighted average and soft floors.

  Israel infrastructure (March 2026): YELLOW — NOT RED or ORANGE. Roads, power, water,
  and internet all function normally despite active war. The Iron Dome intercepts most
  missiles. Missile alerts interrupt daily life but do not destroy infrastructure.
  Missile alerts, curfews, and security restrictions are armed_conflict factors — score
  armed_conflict PURPLE for the war, but keep infrastructure YELLOW for the physical
  systems. Do not let war context push infrastructure above YELLOW when systems function.

  United Kingdom: armed_conflict YELLOW. terrorism ORANGE (organised groups exist, threat
  level elevated, but no sustained mass-casualty campaign recently). crime YELLOW
  (~1.2 homicides/100k — clearly below the 5/100k threshold for YELLOW). health GREEN
  (NHS functional). infrastructure GREEN (modern). Total ORANGE from weighted average.

TOTAL SCORE LOGIC (Python calculates this — for your reference only):
  1. armed_conflict PURPLE only → hard veto, total = PURPLE
     armed_conflict RED does NOT hard veto — it contributes to the weighted average.
  2. Otherwise: weighted average (security cats x2, others x1)
  3. terrorism or civil_strife PURPLE → total at least RED
  4. terrorism or civil_strife RED → total at least ORANGE
  regional_instability has NO hard veto — it only affects the weighted average.

=== EVIDENCE GATE FOR INFRASTRUCTURE, HEALTH, AND CRIME ===

For infrastructure, health, and crime — you MUST answer these pre-screening questions
INSIDE the JSON (in the fields below) before assigning a score. The answers LOCK the score:

INFRASTRUCTURE pre-screening (answer YES/NO based strictly on the briefing):
  Q1: Are major roads physically passable in the capital/main cities?
  Q2: Is electricity available in major cities (even with outages)?
  Q3: Is tap water available in major cities?
  Q4: Is mobile/internet connectivity available?
  → If all YES → score GREEN or YELLOW. CANNOT score ORANGE+ without a specific quote
    from the briefing describing physical road/power/water infrastructure damage.
  → ORANGE only if: frequent power outages OR high road death rate confirmed in briefing.
  → RED only if: utilities unreliable EVERYWHERE OR roads physically damaged by war.
  → PURPLE only if: no roads, no power, no water, no comms in MAJOR CITIES.

HEALTH pre-screening (answer YES/NO based strictly on the briefing):
  Q1: Are hospitals in major cities open and treating patients?
  Q2: Can a traveler get emergency surgery if needed?
  → If both YES → score GREEN, YELLOW, or ORANGE at most. CANNOT score RED+ without a
    specific quote describing hospital closures, epidemic, or system-wide failure.

CRIME pre-screening (answer YES/NO based strictly on the briefing):
  Q1: Are criminal organisations systematically robbing/kidnapping travelers on a routine basis?
  Q2: Does the state lack control of MULTIPLE large provinces/states (not just neighbourhoods)?
  → RED requires YES to Q1 OR homicide rate 30-60/100k confirmed in briefing.
  → PURPLE requires YES to BOTH Q1 and Q2 with specific evidence.

=== PURPLE vs RED — CRITERIA CHECKLIST ===

PURPLE is reserved for a very small number of genuinely extreme situations worldwide.
A useful calibration: at any given time, fewer than 10 countries in the world should
score PURPLE. If you find yourself assigning PURPLE to more than that, you are over-scoring.

Reference PURPLE countries (established pre-2026): Syria, Burkina Faso, DRC, Haiti,
Yemen, Sudan, Somalia, Mali, and Nigeria (due to simultaneous crime PURPLE + terrorism PURPLE
driving the weighted average, NOT because it meets state-collapse criteria alone).
Note what is NOT on this list even under active war: Israel, Ukraine, Lebanon.
A country at war with a functioning government is RED, not PURPLE.

Before assigning PURPLE to ANY category or as a total, verify at least 2 of these 4:

  (A) STATE COLLAPSE — The government has lost effective control of large portions of
      its territory to non-state actors or rival factions, OR has ceased to function as
      a state, OR active ground combat is occurring in the capital city with no
      functioning civil authority remaining.
      CRITICAL: A country that is FIGHTING a war effectively is NOT state-collapsed.
      A functioning military, government, and civil defense system means the state is
      intact. Israel during the Iran-Israel war = state intact = criterion (A) NOT met.
      Ukraine during the Russia war = state intact, government functioning = NOT (A).
      (A) is met by: Haiti (gang control, no state), Somalia (Al-Shabaab territorial
      control), Sudan (RSF vs SAF splitting the state), Syria (multiple factions, Assad
      collapse). NOT by Israel, Ukraine, or any country still governed effectively.

  (B) Civilian preparation cannot reduce risk. A professional traveler with security
      training, local contacts, armoured transport, and full situational awareness would
      still face unacceptable risk.
      CRITICAL: If a country has a functioning civil defense system — nationwide sirens,
      bomb shelters, missile defense (e.g. Iron Dome), evacuation drills — then taking
      cover IS meaningful civilian preparation that reduces risk. Civil defense working
      = criterion (B) NOT met. Israel = criterion (B) NOT met (Iron Dome + shelters
      mean a traveler can meaningfully reduce risk by responding to warnings).

  (C) Evacuation is unreliable or dangerous. Commercial flights indefinitely suspended.
      Land borders controlled by non-state actors. Leaving the country is itself a
      high-risk act. A flight disrupted for days or requiring rerouting is NOT criterion (C).
      Criterion (C) IS met by: Gaza 2024 (Rafah crossing closed, airport destroyed),
      Yemen (airports targeted, Houthi blockade), Sudan during Khartoum fighting.

  (D) Consular protection is absent, non-functional, or the state is the threat.
      Your embassy cannot help you meaningfully if you are in trouble.
      OR the government systematically detains, targets, or endangers foreign nationals.

If fewer than 2 apply: score RED at most, not PURPLE.
If 2 or more apply: PURPLE may be justified — but also ask the calibration question:
  "Does this country belong in the same category as Haiti, Somalia, and Gaza?"
  If the answer is "not really", revise down to RED.

FUNCTIONING STATE CEILING — HARD RULE:
A country with ALL of the following cannot be PURPLE for armed_conflict:
  1. A functioning central government (not collapsed, not controlled by militias)
  2. A functioning national military (actively defending, not routed)
  3. Civil defense infrastructure (sirens, shelters, warning systems, missile defense)
     that gives civilians meaningful ability to protect themselves
  4. At least one viable evacuation route (airport operating, or safe land border)
This ceiling applies even under heavy daily missile or drone attacks.
Israel during the Iran-Israel war (2025-2026): meets all 4 → armed_conflict RED.
Ukraine during the Russia war: meets all 4 for non-frontline regions → armed_conflict RED.

PURPLE total requires armed_conflict PURPLE (hard-veto) OR a combination of PURPLE
sub-scores driving the weighted average to 4.5+ while satisfying the checklist above.

RED means: serious documented risk requiring real security planning. Well-prepared
travelers with clear justification can go. The state provides partial but real
protection. Evacuation is possible though may be disrupted. Risk is serious but
mitigable. RED covers active war zones with functioning states (Israel, Ukraine),
high-crime countries, and authoritarian states that are dangerous but not collapsed.

PURPLE means: the state cannot protect you AND you cannot protect yourself AND you
cannot leave reliably. These three things together — not just one or two.

=== SOURCE ARBITRATION HIERARCHY ===

When sources conflict, apply this priority order:

  TIER 1 — Highest authority (government advisory specific to this traveler identity):
    Israeli NSC warning level (for jewish_israeli layer — most specific for Israeli nationals)
    US State Dept Travel Advisory level (authoritative for US nationals, widely respected)
    UK FCDO Travel Advice (authoritative for UK nationals, independently assessed)
    Israeli NSC for general travelers (cross-check for base layer)

  TIER 2 — Specialist indices (methodologically robust, annual):
    UNODC intentional homicide rates (crime anchor — most reliable cross-country data)
    RSF Press Freedom Index (journalist risk)
    ILGA State-Sponsored Homophobia (LGBTQ+ risk)
    Georgetown GIWPS Women Peace & Security Index (solo women)
    Global Peace Index (GPI) (conflict/stability anchor)
    ACLED conflict event database (precise event location and frequency)

  TIER 3 — Supplementary context (informative but less methodologically rigorous):
    News reporting (useful for recent events, but single incidents can distort picture)
    NGO reports (valuable for identity-specific risks, may reflect advocacy bias)
    Academic sources (useful for historical context)

When a TIER 1 source says RED and a TIER 3 source implies YELLOW: follow TIER 1.
When TIER 1 sources conflict with each other (e.g. State Dept Level 2, FCDO higher):
  Use the higher rating as the floor, note the conflict in confidence_notes.
Always cite which tier each source belongs to in sources_used.

=== TEMPORAL CONTEXT — TREND AND ESCALATION ===

Tier 1 is a STRUCTURAL baseline. But the baseline should note the direction of travel.
Answer these for the overall assessment (not per-category):

  trend: Is the security situation IMPROVING, STABLE, or DETERIORATING over the past 6 months?
         Base this on structural indicators, not single incidents.

  escalation_flag: Set to true if there has been a SIGNIFICANT structural change in the
                   past 90 days that has moved or should move the baseline (a new conflict
                   starting, a coup, a peace agreement, a new terrorist campaign).
                   Set to false if conditions are stable or only minor fluctuations.

  escalation_note: If escalation_flag is true, briefly describe the specific change
                   and which categories it affects.

These fields are used by the Tier 2 daily pipeline to prioritise monitoring.
A DETERIORATING + escalation_flag=true country gets daily Tier 2 checks.
A STABLE country may only need weekly Tier 2 scans.

=== REGIONAL SCORING ===

The country-level scores above represent the TYPICAL TRAVELER DESTINATION — major
cities, tourist hubs, business centres. Now identify up to 5 distinct zones within
the country whose security profile differs materially from that baseline.

INCLUDE a region when:
  - Its total score would differ from the country-level total by at least ONE level, OR
  - At least 2 of its category scores differ from the country level by at least one level.

DO NOT invent regions that match the country baseline — omit them.
DO NOT force regions on countries that are genuinely uniform. Netherlands, Australia,
Poland may produce zero regions. That is correct.

For each region, list ONLY the categories that differ from the country level.
Categories not listed are assumed to match the country-level score exactly.

The regional total_score is computed using the same veto/weighted-average/soft-floor
logic as the country total, but applied to the region's category mix (country scores
overridden by the region's specific scores where provided).

REGIONAL CALIBRATION EXAMPLES:
  Ukraine (country: armed_conflict RED, total RED):
    Region "Eastern Frontline" (Kharkiv, Donetsk, Luhansk, Zaporizhzhia frontline):
      armed_conflict: PURPLE → total: PURPLE
    Region "Western Ukraine" (Lviv, Uzhhorod, Ivano-Frankivsk):
      armed_conflict: ORANGE → total: ORANGE

  Israel (country: armed_conflict RED, total RED):
    Region "Northern Border" (Metula, Kiryat Shmona — within 5km of Lebanon border):
      armed_conflict: PURPLE (daily Hezbollah fire, very short warning times) → total: PURPLE
    Region "Gaza Envelope" (within 7km of Gaza border, now depopulated):
      armed_conflict: PURPLE → total: PURPLE
    Region "Tel Aviv / Central" (Tel Aviv, Jerusalem, Beer Sheva):
      [no change from country level — omit this region]

  Nigeria (country: total RED):
    Region "Northeast" (Borno, Yobe, Adamawa states — Boko Haram/ISWAP):
      terrorism: PURPLE, armed_conflict: RED → total: PURPLE
    Region "Northwest" (Katsina, Sokoto, Zamfara — bandit/kidnap belt):
      armed_conflict: RED, crime: PURPLE → total: RED/PURPLE
    Region "Lagos / Abuja":
      crime: ORANGE, terrorism: ORANGE → total: ORANGE

  Mexico (country: total ORANGE):
    Region "Sinaloa / Tamaulipas / Colima" (cartel heartland):
      crime: PURPLE, civil_strife: RED → total: RED
    Region "Cancún / Riviera Maya / Los Cabos" (tourist corridors):
      crime: YELLOW → total: YELLOW

  DRC (country: total RED):
    Region "Eastern Congo" (North Kivu, South Kivu, Ituri — M23/armed groups):
      armed_conflict: PURPLE, health: PURPLE → total: PURPLE
    Region "Kinshasa":
      [no material change from country level — omit]

IDENTITY LAYER REGIONS: If scoring an identity layer (jewish_israeli, solo_women),
regional scores for that layer must be >= the base layer regional scores for the same
geography. Apply the same base-floor principle at the regional level.

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
  "pre_screening": {{
    "infrastructure_q1_roads_passable":     "YES|NO",
    "infrastructure_q2_electricity":        "YES|NO",
    "infrastructure_q3_water":              "YES|NO",
    "infrastructure_q4_internet":           "YES|NO",
    "infrastructure_physical_damage_quote": "Direct quote from briefing describing physical damage, OR 'none found'",
    "health_q1_hospitals_open":             "YES|NO",
    "health_q2_emergency_surgery":          "YES|NO",
    "crime_q1_systematic_traveler_targeting": "YES|NO",
    "crime_q2_state_lost_multiple_provinces": "YES|NO"
  }},
  "stability_justifications": {{
    "armed_conflict":       "Max 40 words. Cite specific evidence. What would change it.",
    "regional_instability": "Max 40 words. Name specific neighbouring conflicts.",
    "terrorism":            "Max 40 words. Name the group or incident. Lone-wolf or organised?",
    "civil_strife":         "Max 40 words. Cite specific unrest events.",
    "crime":                "Max 40 words. Include homicide rate per 100k if known.",
    "health":               "Max 40 words. Hospital access, disease risk.",
    "infrastructure":       "Max 40 words. Physical state of roads/power/water only."
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
  "trend": "IMPROVING|STABLE|DETERIORATING",
  "escalation_flag": false,
  "escalation_note": "If escalation_flag is true: describe the specific structural change and which categories it affects. Otherwise leave empty string.",
  "data_quality": {{
    "overall": "HIGH|MEDIUM|LOW",
    "note": "One sentence on data gaps, source conflicts, or reliability issues. E.g. 'UNODC homicide data 2 years old; civil strife scoring relies on news reporting only.' If data is solid, say so."
  }},
  "baseline_narrative": "3-4 paragraphs. Specific, direct. No AI filler. Based on the briefing.",
  "veto_explanation": "Be explicit: name the EXACT rule that determined the total score. Examples: 'armed_conflict RED triggered hard veto — total forced to RED.' OR 'No hard veto. Weighted average of 3.1 gives ORANGE. civil_strife RED applies soft floor of ORANGE — no change.' OR 'armed_conflict PURPLE hard veto overrides weighted average of 2.8.' Never vague — always name the mechanism.",
  "sources_used": ["2-4 specific sources or incidents from the briefing with dates. Indicate source tier (Tier 1/2/3 per hierarchy above)."],
  "recommendations": {{
    "movement_access":        "one concrete sentence",
    "emergency_preparedness": "one concrete sentence",
    "communications":         "one concrete sentence",
    "health_medical":         "one concrete sentence",
    "crime_personal_safety":  "one concrete sentence",
    "travel_logistics":       "one concrete sentence"
  }},
  "watch_factors": "2-3 specific structural developments to monitor.",
  "regions": [
    {{
      "name": "Zone name (e.g. 'Northeast', 'Northern Border', 'Lagos/Abuja')",
      "geography": "Specific states, provinces, or cities covered — one sentence.",
      "total_score": "GREEN|YELLOW|ORANGE|RED|PURPLE",
      "scores": {{
        "category_name": "SCORE — include ONLY categories that differ from the country-level score"
      }},
      "note": "One sentence: primary reason this zone differs from the country baseline."
    }}
  ]
}}"""

    text = None
    for attempt in range(1, 4):
        for model_name in STEP2_MODELS:
            try:
                step2_response = gemini.models.generate_content(
                    model=model_name,
                    contents=scoring_prompt,
                    config=genai.types.GenerateContentConfig(
                        temperature=0.0,
                        # No search tool — keeps output clean JSON
                    )
                )
                text = step2_response.text.strip()
                break
            except Exception as e:
                err = str(e)
                print(f"  [!] Step 2 failed on {model_name} (attempt {attempt}): {err[:80]}")
        if text:
            break
        wait = attempt * 30
        print(f"  [!] All models overloaded — waiting {wait}s before retry {attempt+1}/3...")
        _time.sleep(wait)

    if not text:
        print(f"  [X] Step 2 failed after 3 attempts")
        return None

    try:
        # Strip markdown fences
        if text.startswith("```json"): text = text[7:]
        if text.startswith("```"):     text = text[3:]
        if text.endswith("```"):       text = text[:-3]
        text = text.strip()

        # Extract outermost JSON block
        start = text.find("{")
        end   = text.rfind("}") + 1
        if start >= 0 and end > start:
            text = text[start:end]

        # Clean common Gemini JSON issues: trailing commas before } or ]
        import re
        text = re.sub(r",\s*([}\]])", r"\1", text)

        # Attempt full parse — use raw_decode so trailing prose after the closing }
        # is silently ignored instead of raising "Extra data". This is the correct
        # fix for Gemini appending explanatory text after the JSON block.
        try:
            analysis, _ = json.JSONDecoder().raw_decode(text)
        except json.JSONDecodeError as e:
            # Fallback: extract just the scores block via regex — scores are the only
            # required field. The rest (narrative, justifications) can be empty strings.
            print(f"  [!] Full JSON parse failed ({e}). Attempting scores-only rescue...")
            score_match = re.search(
                r'"scores"\s*:\s*\{([^}]+)\}', text, re.DOTALL
            )
            if not score_match:
                print(f"  [X] JSON rescue failed — no scores block found")
                print(f"  Raw (first 300): {text[:300]}")
                return None

            scores_raw = "{" + score_match.group(1) + "}"
            scores_raw = re.sub(r",\s*([}\]])", r"\1", scores_raw)
            scores = json.loads(scores_raw)

            # Reconstruct minimal analysis dict
            analysis = {
                "scores": scores,
                "stability_justifications": {},
                "confidence_levels": {},
                "baseline_narrative": "[Parse rescue — narrative unavailable]",
                "veto_explanation":   "[Parse rescue]",
                "sources_used":       [],
                "recommendations":    {},
                "watch_factors":      "",
            }
            print(f"  [!] Scores rescued successfully. Narrative/justifications empty.")

        # Validate: every score field must be a valid level
        valid_levels = {"GREEN", "YELLOW", "ORANGE", "RED", "PURPLE"}
        score_fields = ["armed_conflict", "regional_instability", "terrorism",
                        "civil_strife", "crime", "health", "infrastructure"]
        scores = analysis.get("scores", {})
        for field in score_fields:
            val = scores.get(field, "").strip().upper()
            if val not in valid_levels:
                print(f"  [!] Invalid score '{val}' for {field} — defaulting to ORANGE")
                scores[field] = "ORANGE"

        # Evidence gate: enforce pre-screening answers against scores
        # Infrastructure: if all 4 systems YES + no damage quote -> cap at YELLOW
        # Health: if hospitals open + surgery available -> cap PURPLE at RED
        # Crime: PURPLE requires both Q1+Q2 YES -> otherwise cap at RED
        pre = analysis.get("pre_screening", {})
        level_to_int = {"GREEN": 1, "YELLOW": 2, "ORANGE": 3, "RED": 4, "PURPLE": 5}

        infra_score = scores.get("infrastructure", "GREEN")
        if level_to_int.get(infra_score, 1) >= 3:  # ORANGE, RED, or PURPLE
            roads  = pre.get("infrastructure_q1_roads_passable", "").upper()
            elec   = pre.get("infrastructure_q2_electricity", "").upper()
            water  = pre.get("infrastructure_q3_water", "").upper()
            net    = pre.get("infrastructure_q4_internet", "").upper()
            quote  = pre.get("infrastructure_physical_damage_quote", "").lower()
            systems_up = all(x == "YES" for x in [roads, elec, water, net] if x)
            no_quote   = not quote or quote == "none found" or len(quote) < 20
            if systems_up and no_quote:
                # All 4 systems confirmed YES + no physical damage quote = infrastructure is
                # functioning normally. Cap at YELLOW — if roads/power/water/internet all work,
                # there is no basis for ORANGE or above.
                print(f"  [!] Infrastructure pre-screening: all systems YES + no damage quote "
                      f"-> capping {infra_score} -> YELLOW")
                scores["infrastructure"] = "YELLOW"

        health_score = scores.get("health", "GREEN")
        if level_to_int.get(health_score, 1) >= 5:  # PURPLE only
            hosp = pre.get("health_q1_hospitals_open", "").upper()
            surg = pre.get("health_q2_emergency_surgery", "").upper()
            if hosp == "YES" and surg == "YES":
                # Hospitals open + emergency surgery available = healthcare has NOT physically
                # collapsed. PURPLE requires physical collapse (bombed/closed hospitals).
                # Cap PURPLE -> RED. RED is appropriate when hospitals function but poorly.
                print(f"  [!] Health pre-screening: hospitals open + surgery available "
                      f"-> capping {health_score} -> RED")
                scores["health"] = "RED"

        crime_score = scores.get("crime", "GREEN")
        if level_to_int.get(crime_score, 1) == 5:  # PURPLE only
            target = pre.get("crime_q1_systematic_traveler_targeting", "").upper()
            prov   = pre.get("crime_q2_state_lost_multiple_provinces", "").upper()
            if not (target == "YES" and prov == "YES"):
                print(f"  [!] Crime pre-screening: PURPLE requires both Q1+Q2 YES "
                      f"-> capping PURPLE -> RED")
                scores["crime"] = "RED"

        analysis["scores"] = scores

        # Attach the briefing for audit
        analysis["_briefing"] = briefing
        print(f"  [OK] Scoring complete")
        return analysis

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
            "regions":                  json.dumps(analysis.get("regions", [])),
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
            "regions":              json.dumps(analysis.get("regions", [])),
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

        # ── BASE FLOOR ENFORCEMENT ────────────────────────────────────────────
        # Identity layer scores must be >= base layer scores in every category.
        # The base layer is the floor — belonging to an identity group can only
        # add risk, not remove it (with rare exceptions requiring strong evidence).
        if layer != "base" and base_baseline:
            base_scores  = base_baseline.get("scores", {})
            layer_scores = analysis.get("scores", {})
            cats = ["armed_conflict", "regional_instability", "terrorism",
                    "civil_strife", "crime", "health", "infrastructure"]
            lvl  = {"GREEN": 1, "YELLOW": 2, "ORANGE": 3, "RED": 4, "PURPLE": 5}
            ilv  = {1: "GREEN", 2: "YELLOW", 3: "ORANGE", 4: "RED", 5: "PURPLE"}

            floors_applied = []
            for cat in cats:
                base_int  = lvl.get(base_scores.get(cat, "GREEN"), 1)
                layer_int = lvl.get(layer_scores.get(cat, "GREEN"), 1)
                if layer_int < base_int:
                    layer_scores[cat] = ilv[base_int]
                    floors_applied.append(
                        f"{cat}: {ilv[layer_int]} -> {ilv[base_int]}"
                    )

            if floors_applied:
                print(f"  [!] Base floor applied to {len(floors_applied)} categories: "
                      f"{'; '.join(floors_applied)}")
                analysis["scores"] = layer_scores
        # ─────────────────────────────────────────────────────────────────────

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
