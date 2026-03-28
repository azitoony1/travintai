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

  All countries, all layers (use --workers to parallelize):
    python tier1_baseline.py --all-countries --all-layers --workers 4

  --workers N: run N countries simultaneously (default 1 = sequential).
    Recommended: 3-4. Each worker makes concurrent Gemini API calls.
    Do not exceed 5 — risks more 503 overload errors.

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
from concurrent.futures import ThreadPoolExecutor, as_completed
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

# All 27 countries in the system
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
    ("Iceland",                             "IS"),
    ("Norway",                              "NO"),
    ("Canada",                              "CA"),
    ("Italy",                               "IT"),
    ("Belgium",                             "BE"),
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

    LAYER 1 — HARD VETO:
      armed_conflict PURPLE → total = PURPLE
      armed_conflict RED    → total = RED (no exceptions)

      Rationale: RED means regular incoming fire, or widespread conflict affecting multiple
      regions, or capital/major cities threatened. This is a fundamental travel safety failure
      that cannot be averaged away by low scores in other categories.
      Safe zones within a RED country are handled by the regional scoring system, not by
      softening the national total.

    LAYER 2 — WEIGHTED AVERAGE (applies only when armed_conflict < RED):
      Security/political categories count DOUBLE:
        armed_conflict x2, regional_instability x2, terrorism x2, civil_strife x2,
        legal_risk x2
      Other categories count ONCE:
        crime x1, health x1, infrastructure x1
      Total weight: 13 points

      Weighted avg -> Total
        <= 1.4  -> GREEN
        1.5-2.4 -> YELLOW
        2.5-3.4 -> ORANGE
        3.5-4.4 -> RED
        > 4.4   -> PURPLE

    LAYER 3 — SOFT FLOORS (all 8 categories):
      Applied after the weighted average to prevent systematic under-scoring.

      For EVERY category (security, crime, health, infrastructure):
        PURPLE -> total at least RED
        RED    -> total at least ORANGE

      Rationale: a country where hospitals have physically collapsed (health PURPLE),
      infrastructure is destroyed (infrastructure PURPLE), or >60/100k homicide rate
      (crime PURPLE) is as dangerous for travelers as a conflict zone. Similarly,
      any RED-level failure in any category is a significant travel risk that should
      prevent the total from being YELLOW or lower.

      Note: armed_conflict RED/PURPLE are handled entirely in Layer 1 above.
            By the time Layer 3 runs, ac is at most ORANGE, so ac never triggers here.
      Note: regional_instability is capped at RED in the prompt; the PURPLE floor is
            kept as a safety net in case the model assigns PURPLE anyway.

    RESULT: The highest of (Layer 1 veto | weighted avg | soft floors) wins.
    """
    all_categories = ["armed_conflict", "regional_instability", "terrorism", "civil_strife",
                      "legal_risk", "crime", "health", "infrastructure"]
    security_cats  = {"armed_conflict", "regional_instability", "terrorism", "civil_strife",
                      "legal_risk"}
    level_to_int   = {"GREEN": 1, "YELLOW": 2, "ORANGE": 3, "RED": 4, "PURPLE": 5}
    int_to_level   = {1: "GREEN", 2: "YELLOW", 3: "ORANGE", 4: "RED", 5: "PURPLE"}

    def lvl(cat):
        return level_to_int.get(category_scores.get(cat, "GREEN"), 1)

    # ── LAYER 1: Hard veto — armed_conflict RED and PURPLE ───────────────────
    # PURPLE: full-scale war, no safe zones, evacuation may be impossible.
    # RED: regular incoming fire, widespread conflict, or capital/cities threatened.
    #      RED is a hard veto — the national total cannot be ORANGE or lower when
    #      the country is under active attack. Safe zones within the country are
    #      captured by the regional scoring system, not by softening the total.
    ac = lvl("armed_conflict")
    if ac >= 5:
        return "PURPLE"
    if ac >= 4:
        return "RED"

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

    # ── LAYER 3: Soft floors — all 7 categories ──────────────────────────────
    # Every category at RED forces the total to at least ORANGE.
    # Every category at PURPLE forces the total to at least RED.
    # This applies universally: security, crime, health, and infrastructure.
    # Rationale: a country where hospitals have physically collapsed (health PURPLE)
    # or infrastructure is completely destroyed (infrastructure PURPLE) is as
    # dangerous for a traveler as a conflict zone, even if security is otherwise fine.
    # Note: armed_conflict RED/PURPLE are handled entirely in Layer 1 above.
    #       By the time Layer 3 runs, ac is at most ORANGE, so ac never triggers here.

    floor = 1  # start at GREEN (integer)

    for cat in all_categories:
        v = lvl(cat)
        if v >= 5:   floor = max(floor, 4)  # any PURPLE -> total at least RED
        elif v >= 4: floor = max(floor, 3)  # any RED    -> total at least ORANGE
        elif v >= 3 and cat == "crime": floor = max(floor, 2)  # crime ORANGE -> at least YELLOW

    result_int = max(level_to_int[raw], floor)
    return int_to_level[result_int]


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
        nsc_block = f"\nIsraeli NSC Structural Warning Level for this country: {nsc_level}/4 (1=Safe, 2=Exercise Caution, 3=Reconsider, 4=Do Not Travel)" if nsc_level else ""
        prompt += f"""
=== JEWISH/ISRAELI IDENTITY LAYER ===

Score this country for Jewish travelers of any nationality AND Israeli passport holders.
Where these two groups face different risks, score for the higher-risk of the two and note
the distinction in the narrative.

{f"Base layer scores for reference: {json.dumps(base_baseline.get('scores', {}), indent=2)}" if base_baseline else ""}
{nsc_block}

GENERAL RULE: The base layer is the floor. Identity layer scores can equal or exceed base
scores — never be lower. Score each category using the complete level definitions below,
which integrate both base and identity-specific conditions. Assign the HIGHEST level for
which ANY condition (base OR identity-specific) is met.

CORE QUESTION FOR EVERY CATEGORY: "Does belonging to this identity group (being Jewish/
Israeli, being a solo woman, etc.) create a STRUCTURALLY DIFFERENT risk in this category
compared to a general traveler?" If the answer is NO — inherit base exactly. Only raise
the score if the briefing contains specific evidence that this group faces a meaningfully
different risk in this category. Do not raise a category just because the country is
generally risky — that is already captured in the base layer.

ARMED CONFLICT — inherit base score. Bombs and bullets do not discriminate by identity.
Israeli passport bans affect entry, not in-country conflict risk. Exception: if the
conflict specifically involves deliberate targeting of this identity group as a
military/terrorist objective, this may raise the terrorism score — not armed_conflict.

REGIONAL INSTABILITY — inherit base score exactly. Geographic/regional risk is the same
for all travelers regardless of identity. Never raise this category for any identity layer.

TERRORISM — use these integrated level definitions:

  GREEN: Base GREEN conditions met (no attacks with fatalities in 5+ years, no organised groups)
         AND no documented attacks targeting Jewish individuals, Israeli institutions, or
         Jewish community infrastructure in this country in past 5 years
         AND no organised groups operating here with stated intent to target this group.

  YELLOW: Base YELLOW conditions met (foiled plots, minor incidents, or single lone-wolf
          with no fatalities), OR: antisemitic incidents documented (property crime, harassment,
          non-lethal threats) without organised attack capability against persons,
          OR: Israeli NSC Level 1 (standard precautions).

  ORANGE: Base ORANGE conditions met (organised group with demonstrated capability, 1-2
          attacks with single-digit deaths in past 2 years), OR: an organised group present
          in-country that specifically targets Jews or Israeli institutions with at least 1
          documented attack against a Jewish/Israeli target in past 3 years,
          OR: Israeli NSC Level 2 (Exercise Caution),
          OR: multiple documented antisemitic attacks on persons (not just property) with
          political/ideological motivation in past 2 years, even without a confirmed group,
          OR: systematic state-sponsored antisemitic propaganda or incitement — government
          media, school curricula, or official rhetoric explicitly calling for harm to Jews
          or denial of Israel's right to exist — creating a documented ideological environment
          that demonstrably elevates the risk of attacks against Jewish or Israeli travelers
          even absent a confirmed organised group (Iran is the clearest example).

  RED: Base RED conditions met (organised campaign, repeat attacks, civilian targeting),
       OR: any organised group or state-linked network — including but not limited to
       Hezbollah, IRGC proxies, Al-Qaeda affiliates, extreme-right or extreme-left groups —
       documented as operationally present in-country with stated goal of targeting Jewish
       or Israeli individuals and demonstrated capability to act,
       OR: Israeli NSC Level 3 (Reconsider Travel),
       OR: 2+ organised attacks specifically targeting Jewish/Israeli targets with casualties
       (synagogues, Israeli-flagged businesses, Israeli diplomatic staff) in past 3 years,
       OR: lone-wolf frequency trigger specifically anti-Jewish/Israeli (2+ ideologically
       antisemitic attacks with fatalities in rolling 12 months).

  PURPLE: Israeli NSC Level 4 (Do Not Travel) — this is the definitive PURPLE trigger.
          The Israeli government has determined Israeli nationals face near-certain lethal risk.

CIVIL STRIFE — use these integrated level definitions (unrest, protests, political violence only):

  GREEN: Base GREEN conditions met (politically stable, protests rare and peaceful). No
         significant unrest or political violence. No state-condoned mob violence against
         Jewish community.

  YELLOW: Base YELLOW conditions met (occasional protests, quickly dispersed). No significant
          violence. Government rhetoric hostile to Israel/Jews but not translating into
          street violence or state-condoned attacks.

  ORANGE: Base ORANGE conditions met (sustained protests with violence, tear gas, periodic
          arrests, some city areas periodically unsafe). OR: antisemitic mobs or riots
          specifically targeting Jewish institutions or neighborhoods with documented violence
          and inadequate or complicit police response.

  RED: Base RED conditions met (widespread unrest, lethal force against protesters, breakdown
       of rule of law). OR: state-condoned or state-incited mob violence specifically
       targeting the Jewish community (pogroms, government-sanctioned attacks, authorities
       standing aside during attacks on Jewish targets).

  PURPLE: Base PURPLE conditions met (coup, civil war, complete collapse of public order).
          OR: state actively deploys forces against Jewish community with lethal violence.

LEGAL RISK — use these integrated level definitions (state legal threat to this traveler):

  GREEN: Base GREEN conditions met AND no legal restrictions specific to Jewish travelers or
         Israeli passport holders AND Israeli travelers' legal rights respected equally with
         other foreigners AND consular access available.

  YELLOW: Base YELLOW conditions met. OR: government rhetoric hostile to Israel/Jews but no
          legal enforcement creating material risk for travelers. OR: diplomatic tensions
          with Israel that do not translate to legal jeopardy for individual travelers.

  ORANGE: Base ORANGE conditions met (laws actively enforced against foreigners). OR:
          state-sanctioned antisemitism with legal dimension (selective enforcement against
          Jews, institutionally hostile bureaucratic targeting). OR: Israeli embassy/consulate
          expelled or formally absent by state decision — loss of consular protection.
          OR: Jewish travelers subject to systematic heightened scrutiny or obstruction at
          entry points (documented pattern of questioning, delays, targeting based on identity).
          OR: systematic state-sponsored antisemitic propaganda or incitement — government
          media, school curricula, or official rhetoric explicitly calling for harm to Jews
          or denial of Israel's right to exist — creating a documented ideological environment
          that demonstrably elevates risk of attacks against Jewish or Israeli travelers.

  RED: Base RED conditions met (arbitrary detention pattern, state uses foreigners as
       bargaining chips). OR: Israeli passport legally banned — traveler CANNOT ENTER with
       Israeli passport (Iran, Saudi Arabia, Lebanon, Syria, Libya, Yemen, Iraq, Pakistan
       currently). Note: American/EU Jews without Israeli passport may enter some of these
       — distinguish in narrative. OR: active government persecution of Jewish community
       with legal authority (documented arrests, asset seizures, forced closures of Jewish
       institutions). OR: Israeli nationals systematically targeted by authorities (detained
       at border, interrogated based on identity, subject to laws criminalizing Israeli
       association or Israeli passport).

  PURPLE: Base PURPLE conditions met (state systematically targets foreigners with violence
          or indefinite detention). OR: Jewish travelers systematically targeted by state
          forces with violence or indefinite detention with no consular protection available.

CRIME — use these integrated level definitions:

  GREEN: Base GREEN conditions met (<5 homicides/100k, low organised crime) AND no documented
         hate crime pattern targeting Jews specifically AND local Jewish community (if any)
         reports no particular targeting.

  YELLOW: Base YELLOW conditions met (5-15/100k), OR: isolated antisemitic property crime
          (vandalism, graffiti) without pattern of violent personal targeting.

  ORANGE: Base ORANGE conditions met (15-30/100k, or documented kidnapping risk), OR:
          documented pattern of hate crimes targeting Jewish individuals specifically
          (multiple incidents with assault or credible personal threat in past 3 years,
          not just property crime), OR: local Jewish community reports active personal
          targeting requiring heightened personal precautions.

  RED: Base RED conditions met (30-60/100k, or kidnapping targeting foreigners), OR:
       violent hate crimes targeting Jewish/Israeli travelers documented with multiple
       incidents involving physical harm in past 3 years, OR: kidnapping risk specifically
       elevated for Israeli/Jewish travelers (documented incidents or credible group
       modus operandi targeting this group).

  PURPLE: Base PURPLE conditions met (>60/100k or substantial criminal territorial control),
          OR: criminal organisations specifically targeting Israeli/Jewish travelers for
          kidnapping-for-ransom with documented incidents (very rare — requires hard evidence,
          not speculation).

CRIME — inherit base score. Raise using the integrated crime definitions above only when
        hate crime patterns specific to Jewish/Israeli travelers are documented.

HEALTH — inherit base score. Exception: if Israeli passport holders are explicitly denied
access to state hospitals in this country, note it prominently in the narrative but only
raise the score if the denial is systematic and affects emergency care.

INFRASTRUCTURE — inherit base score. Passport restrictions affect entry, not in-country roads.

HARD VETOES (applied after scoring, override everything):
  Israeli passport banned (cannot legally enter) → legal_risk minimum RED, total minimum RED.
  Israeli NSC Level 4 → total PURPLE.

SOFT FLOORS:
  Israeli NSC Level 3 → total minimum RED.
  Israeli NSC Level 2 → total minimum one level above base total.
  Documented organised antisemitic attack in past 24 months → terrorism minimum ORANGE.
"""

    elif identity_layer == "solo_women":
        prompt += f"""
=== SOLO WOMEN IDENTITY LAYER ===

Score this country for women traveling alone without a companion. Group-tour risk
is different — score for independent solo travel.

{f"Base layer scores for reference: {json.dumps(base_baseline.get('scores', {}), indent=2)}" if base_baseline else ""}

GENERAL RULE: The base layer is the floor. Identity layer scores can equal or exceed base
scores — never be lower. Score each category using the complete level definitions below.
Assign the HIGHEST level for which ANY condition (base OR identity-specific) is met.

CORE QUESTION FOR EVERY CATEGORY: "Does traveling as a solo woman create a STRUCTURALLY
DIFFERENT risk in this category compared to a general traveler?" If NO — inherit base
exactly. Only raise if the briefing documents a specific mechanism by which solo women
face meaningfully greater risk in this category. Do not raise just because the country
is generally risky.

ARMED CONFLICT — inherit base score. Conflict risk does not change based on gender.
Exception: if the briefing documents systematic use of sexual violence as a weapon of
war (documented pattern, not isolated incidents), raise one level above base to reflect
the additional targeting risk. Do not raise otherwise.

REGIONAL INSTABILITY — inherit base score exactly. Geographic/regional risk is the same
for all travelers regardless of gender or travel style. Never raise this category.

TERRORISM — inherit base score. Do NOT raise above base unless the briefing documents
specific deliberate targeting of women by terrorist actors. General terrorism risk
affects all travelers equally. The exception is narrow: Taliban-controlled territory where
women's visible presence is itself criminalised and enforcement is violent.

CIVIL STRIFE — use these integrated level definitions (unrest, protests, political violence only):

  GREEN: Base GREEN conditions met (politically stable, no significant unrest). No political
         violence affecting travelers. Women's rights protests, if any, are peaceful.

  YELLOW: Base YELLOW conditions met (occasional protests, quickly dispersed, no significant
          violence). Political tensions exist but do not create risk for travelers.

  ORANGE: Base ORANGE conditions met (sustained protests with violence, tear gas, some areas
          periodically unsafe). OR: active crackdown on women's rights demonstrations with
          violent police response — creating collateral risk for solo women travelers who
          may be in the vicinity or mistakenly associated with protesters.

  RED: Base RED conditions met (widespread unrest, lethal force against protesters,
       significant breakdown of rule of law). OR: government-deployed violence specifically
       targeting women's rights activists or demonstrators at scale, creating genuine risk
       for any woman present in public during crackdown periods.

  PURPLE: Base PURPLE conditions met (coup, civil war, complete collapse of public order).

LEGAL RISK — use these integrated level definitions (state legal threat to solo women travelers):

  GREEN: Base GREEN conditions met AND no legal restrictions on women's independent movement
         or dress AND women can travel, use hotels, and access services independently without
         any legal risk or required permissions.

  YELLOW: Base YELLOW conditions met. OR: strong cultural norms discouraging women traveling
          alone (conservative social expectations) but with no legal enforcement or criminal
          penalties — traveler should be aware but faces no legal jeopardy.

  ORANGE: Base ORANGE conditions met (laws actively enforced against foreigners). OR: legally
          enforced dress code with criminal penalties — mandatory hijab, modesty laws enforced
          by morality police or regular law enforcement. The law exists and CAN be applied to
          a foreign woman traveler. Do NOT score YELLOW even if enforcement has recently
          relaxed — the structural legal risk remains as long as the law is on the books and
          has been applied. OR: laws restricting women's access to certain public spaces, or
          requiring male accompaniment in formal settings (government offices, courts, banks).

  RED: Base RED conditions met (arbitrary detention, state uses foreigners as bargaining
       chips). OR: male guardianship laws (mahram) restricting women's independent movement,
       hotel check-in, or use of transport without a male companion — a solo woman legally
       cannot operate independently. Score RED minimum regardless of how strictly enforced
       currently — the structural legal risk is RED as long as the law exists.
       OR: active crackdown on women's rights with documented arrests of women for dress or
       behavior violations (Iran 2022-present is the clearest active example).

  PURPLE: Base PURPLE conditions met (state systematically targets foreigners with violence
          or indefinite detention). OR: complete legal prohibition of solo women's independent
          movement without male guardian with active violent enforcement and no reliable
          consular protection.

HARD VETO: Country requires male guardian for independent travel → legal_risk minimum RED,
           total minimum RED.
           Country requires male permission to obtain passport or travel abroad → legal_risk
           minimum RED, total minimum RED.

CRIME — use these integrated level definitions:

  GREEN: Base GREEN conditions met (<5 homicides/100k, low organised crime) AND no documented
         structural pattern of gender-based violence affecting travelers AND public transport
         is safe for solo women.

  YELLOW: Base YELLOW conditions met (5-15/100k), OR: some reports of harassment without a
          systematic pattern in tourist areas, OR: general crime risks apply with no specific
          elevated risk for solo women.

  ORANGE: Base ORANGE conditions met (15-30/100k, or documented kidnapping risk), OR:
          documented structural pattern of sexual harassment or assault in public spaces
          that specifically affects travelers — not just isolated incidents but a pattern
          acknowledged by multiple travel advisories or authorities (India, Egypt, parts of
          Morocco are structural examples), OR: femicide rate significantly elevated above
          the general homicide rate, suggesting high gender-based violence culture affecting
          solo travel safety, OR: public transport and taxis documented as unsafe for solo
          women at night with multiple incidents in past 2 years.

  RED: Base RED conditions met (30-60/100k, or kidnapping targeting foreigners), OR:
       documented kidnapping or sexual assault specifically targeting solo foreign women
       travelers (multiple incidents with a pattern, not isolated cases), OR: sexual violence
       risk in tourist areas so significant it requires active security planning beyond
       normal awareness.

  PURPLE: Base PURPLE conditions met (>60/100k or substantial criminal territorial control),
          OR: catastrophic sexual violence risk making solo women's travel functionally
          impossible without private security — reserved for active conflict zones where
          rape is systematically used as a weapon affecting civilian movement.

CRIME — inherit base score. Raise using the integrated crime definitions above when
        structural GBV patterns or targeting of solo women travelers are documented.

HEALTH — inherit base score. Raise if: reproductive healthcare is inaccessible (emergency
contraception unavailable, abortion illegal with no medical exceptions) AND this creates
a genuine safety risk for travelers, OR sexual assault care is non-functional (no forensic
examination, reporting leads to criminalization of victim). Only raise if the gap materially
affects traveler safety outcomes — not just as a political statement about reproductive rights.

INFRASTRUCTURE — inherit base score. The physical road network is the same for all travelers.
If public transport is specifically unsafe for women, score that under crime, not infrastructure.
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
    "legal_risk":            "GREEN|YELLOW|ORANGE|RED|PURPLE",
    "crime":                 "GREEN|YELLOW|ORANGE|RED|PURPLE",
    "health":                "GREEN|YELLOW|ORANGE|RED|PURPLE",
    "infrastructure":        "GREEN|YELLOW|ORANGE|RED|PURPLE"
  },
  "stability_justifications": {
    "armed_conflict":        "Why this is the structural baseline. What factors make it stable at this level. What specific change would move it up or down.",
    "regional_instability":  "...",
    "terrorism":             "...",
    "civil_strife":          "...",
    "legal_risk":            "...",
    "crime":                 "...",
    "health":                "...",
    "infrastructure":        "..."
  },
  "confidence_levels": {
    "armed_conflict":        "HIGH|MEDIUM|LOW|INSUFFICIENT",
    "regional_instability":  "HIGH|MEDIUM|LOW|INSUFFICIENT",
    "terrorism":             "HIGH|MEDIUM|LOW|INSUFFICIENT",
    "civil_strife":          "HIGH|MEDIUM|LOW|INSUFFICIENT",
    "legal_risk":            "HIGH|MEDIUM|LOW|INSUFFICIENT",
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
4. CIVIL STRIFE: Political violence, coups, riots, sustained protests, breakdown of public order? (NOT legal system issues — those go in #5)
5. LEGAL RISK: What does the STATE do to foreign travelers who are legally inside the country? Arbitrary detention of foreigners (NOT border entry bans), criminalization of tourist behavior, consular access obstruction, documented use of foreigners as diplomatic bargaining chips? NOTE: wartime military measures (curfews, checkpoints, military zones, conscription of dual nationals) are armed_conflict factors — do NOT include them here. Entry bans for certain nationalities are border policy, not in-country legal risk.
6. CRIME: Organized crime, kidnapping, violent crime rates for travelers?
7. HEALTH: Disease outbreaks, healthcare quality, medical access?
8. INFRASTRUCTURE: Road safety, power/water reliability, transport quality?

Be specific: name actual groups, cite specific incidents with dates, give statistics where known.
If there is an active war or major conflict, describe it clearly — do not soften it.
This briefing will be used to assign threat scores. Accuracy is critical."""

    # Step 1 (briefing with Google Search grounding): 2.5 Flash confirmed to support grounding.
    # 3.x preview models may not support search grounding — keep 2.x for Step 1.
    # Step 2 (JSON scoring, no search): 3.1 Flash is faster and cheaper than Pro.
    # Falls back to 2.5 Flash, then 2.0 Flash if 3.1 Flash is unavailable.
    # Note: gemini-1.5-flash is deprecated (404 NOT_FOUND as of 2026-03).
    STEP1_MODELS = ["gemini-2.5-flash", "gemini-2.0-flash"]
    STEP2_MODELS = ["gemini-3-flash-preview", "gemini-3.1-flash-lite-preview", "gemini-2.5-flash", "gemini-2.0-flash"]

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
  Country at war with active missiles but functioning hospitals and roads:
    - armed_conflict HIGH    ✓ (score based on actual conflict intensity and safe-zone availability)
    - health LOWER           ✓ (score whether traveler CAN get medical care — if hospitals are
                                open and treating patients, score YELLOW or GREEN, not PURPLE)
    - infrastructure LOWER   ✓ (score physical state of roads/power/water/internet — if they
                                function, score accordingly, even if sirens go off daily)
    - crime LOWER            ✓ (score whether criminals target travelers, not whether country is at war)

  Country with severe political repression but law-abiding streets:
    - civil_strife/legal_risk HIGH ✓ (government persecution is real)
    - crime LOWER                  ✓ (street crime rate may be low — score it independently)

  Country with high general crime but no armed conflict:
    - crime HIGH             ✓ (score the actual homicide rate and kidnapping pattern)
    - armed_conflict GREEN   ✓ (no fighting on territory — score it independently)

  Apply this logic to every country: what does the BRIEFING say specifically about
  each category? Score each one on its own evidence, never by inference from others.

=== STRICT PURPLE LIMITS FOR HEALTH, CRIME, INFRASTRUCTURE ===

NEVER assign PURPLE to health, crime, or infrastructure unless:

  Health PURPLE:    Healthcare system has physically COLLAPSED. Hospitals are bombed/closed.
                    No emergency care available anywhere in major cities.
                    NOT PURPLE just because: sanctions strain supplies, system is under-funded,
                    doctors are leaving, or hospitals are overwhelmed with casualties.
                    If a traveler who has a medical emergency can get treated at a hospital
                    in a major city — even imperfectly — it is NOT PURPLE.

  Crime PURPLE:     Travelers face SYSTEMATIC inescapable targeting by criminal organisations
                    with substantial territorial control across multiple large regions.
                    State has effectively ceded governance of significant geographic areas.
                    NOT PURPLE just because: the country is authoritarian, there are protests,
                    it is a war zone, or gang violence exists in city neighborhoods.
                    Ask: can a traveler move through the country without crossing criminal-
                    controlled territory? If yes in most areas → not PURPLE.

  Infrastructure PURPLE: Infrastructure has PHYSICALLY COLLAPSED in major cities.
                    No reliable roads, power, water, or communications available.
                    NOT PURPLE just because: power cuts are frequent, roads are poor,
                    internet is censored, or the country is under military pressure.
                    If utilities function (even unreliably) and roads are passable → not PURPLE.

If you are considering PURPLE for health, crime, or infrastructure, ask yourself:
"Can a traveler in the capital city get emergency medical treatment, walk the streets
without being robbed, and get from A to B?" If yes → not PURPLE.

=== END CRITICAL RULE ===

SCORING SCALE AND DEFINITIONS:

FUNDAMENTAL PRINCIPLE — TRAVELER'S-EYE VIEW:
Every score reflects what a visitor physically present inside this country will encounter.
This is NOT a geopolitical assessment. We do not care what a country does abroad, what
alliances it holds, what conflicts it supports, or what its foreign policy is — UNLESS
those things directly create risk for someone standing on its soil.

  Iran sponsors Hezbollah in Lebanon → irrelevant to Iran's terrorism score.
  Russia invades Ukraine → irrelevant to Russia's armed_conflict score (no fighting in Moscow).
  Saudi Arabia funds Wahhabi groups abroad → irrelevant unless those groups attack inside Saudi.
  USA conducts airstrikes in Syria → irrelevant to USA's armed_conflict score.
  Israel fights Hezbollah in Lebanon → that's Lebanon's armed_conflict, not Israel's regional score.

The ONLY question for each category: what does a traveler inside this country experience?

CRITICAL PRINCIPLE — SCORE EACH CATEGORY INDEPENDENTLY:
Each of the 7 categories has its own specific definition. Do NOT let one high-scoring
category "contaminate" others. A country can be armed_conflict RED and terrorism YELLOW
(the war is the risk, not non-state terrorist groups). It can be civil_strife GREEN and
armed_conflict RED (the government is functioning well while fighting an external war).
Evaluate each category on its own indicators. Do not reason "this country is dangerous
overall, so all categories must be high."

WRONG: "This country is at war and sponsors terrorism abroad, therefore terrorism = PURPLE."
RIGHT: "What organised non-state groups are conducting attacks on civilians INSIDE this
        country? Score based on group presence, attack frequency, and casualty pattern —
        not the country's geopolitical role or foreign activities."

WRONG: "This country is in a war zone, so civil_strife must be RED or PURPLE."
RIGHT: "Score civil_strife on the internal political situation: does the government
        repress its own citizens? Are there riots, coups, mass protests with violence?
        A country can have HIGH armed_conflict and LOW civil_strife simultaneously."

WRONG: "State A's missiles hit Country B = Country B terrorism PURPLE."
RIGHT: "State military attacks between countries = armed_conflict, never terrorism.
        Terrorism requires non-state actors operating independently of state military action."

CRITICAL PRINCIPLE — ARMED CONFLICT:
Score what travelers PHYSICALLY ENCOUNTER on the ground, NOT the country's
political or military involvement in a conflict.

The armed_conflict score describes the PHYSICAL THREAT on the country's territory.
Political involvement, sanctions, or diplomatic hostility do NOT raise armed_conflict —
only physical fighting or attacks on the country's territory counts.

armed_conflict RED hard-vetoes the total to RED. This is intentional: a country under
regular incoming fire, widespread active conflict, or with its capital/major cities
threatened cannot score ORANGE overall. Safe zones within a RED country are captured
by the regional scoring breakdown, not by softening the national total.

armed_conflict PURPLE hard-vetoes the total to PURPLE. Use PURPLE only when the
physical threat meets the criteria checklist (2 of 4 criteria).

ARMED CONFLICT — Score based on conflict ON THE COUNTRY'S TERRITORY or directly threatening it.
  GREEN:  No armed conflict on national territory. Military may exist but is not engaged
          in fighting, or is deployed overseas with no risk of spillover home.
  YELLOW: Localized or frozen conflict in remote border areas that does not affect traveler
          movement. OR overseas military deployment with zero home-soil fighting and only a
          remote possibility of spillover into the country.
  ORANGE: Active conflict confined to part of the country, with capital and major cities
          safe and accessible. Conflict zones are known and avoidable with reasonable planning.
          OR a dormant/frozen military conflict (ceasefire holding) where credible escalation
          signals have emerged in the past 12 months — meaning the situation is actively
          unstable, not just historically unresolved. A ceasefire frozen for decades with no
          recent escalation stays YELLOW, not ORANGE.
          OR overseas military deployment that is likely to lead to attacks inside the country.
          Key rule: ORANGE is the minimum if there is active fighting in any part of the country.
  RED:    Widespread conflict affecting multiple major regions. OR capital or large cities
          directly threatened. OR regular missile, rocket, drone, or airstrike attacks on
          populated areas — regardless of interception rate, if incoming fire is routine and
          ongoing, this is RED minimum.
          Key rule: RED hard-vetoes the total score to RED. This is handled in the scoring
          engine — do not adjust your category scores to avoid this outcome.
  PURPLE: Full-scale war. Active fighting in or near the capital or major cities. Daily
          incoming fire. Territory actively contested across multiple fronts. No civilian
          movement reliably safe. Meets at least 2 of 4 PURPLE criteria (see checklist).
          Apply the 2-of-4 PURPLE criteria checklist rigorously. Key discriminator:
          RED = safe zones are identifiable, a traveler who chooses carefully can reduce
          their risk (e.g. avoiding the north, staying in a city with good air defense).
          PURPLE = no destination choice within the country meaningfully reduces risk —
          the entire country is under threat that civilian preparation cannot mitigate.
          A functioning government, operational civil defense, and open airports weigh
          against PURPLE — but are not automatic disqualifiers if the other criteria are
          strongly met (e.g. daily ballistic missile attacks hitting major cities despite
          interception, with no safe zone identifiable anywhere in the country).
  NOTE: Overseas military deployment = YELLOW at most.
  NOTE: Regular incoming missiles/rockets/drones = RED minimum, even if mostly intercepted.
  NOTE: Being the aggressor in a war fought on another country's soil = YELLOW for this country.
  NOTE: Functioning state + civil defense + open airports = RED maximum, not PURPLE.

REGIONAL INSTABILITY — Score the impact of NEIGHBORING conflicts on travelers INSIDE this country.
  The question is: does the regional situation create measurable risk for someone visiting HERE?
  Being geographically near a conflict does NOT automatically raise this score — show the
  actual traveler-affecting mechanism (cross-border fire, refugee-driven security pressure,
  economic collapse from sanctions/blockade, etc.).

  OVERSEAS ASSETS RULE: Attacks on this country's military bases, ships, embassies, or
  personnel located IN ANOTHER COUNTRY do NOT raise this country's regional_instability.
  The risk from such attacks belongs to the country where the attack occurred. Ask only:
  "Has conflict from outside reached travelers standing on THIS country's soil?"

  IDENTITY LAYER RULE — HARD: regional_instability is ALWAYS inherited from the base layer
  exactly. Never raise it for any identity group (jewish_israeli, solo_women, etc.).
  Geography-driven risk does not change based on who the traveler is. Missiles, cross-border
  armed groups, and refugee flows affect all travelers equally regardless of identity.

  MAXIMUM SCORE IS RED. Regional instability does not go to PURPLE. If a country has been
  drawn so fully into a regional conflict that it is effectively a direct participant, that
  risk is scored under armed_conflict — which will also rise accordingly.

  GREEN:  Stable neighborhood. No active wars in bordering countries with spillover.
          OR the country is geographically distant from regional flashpoints.
  YELLOW: Regional tensions or a low-level conflict nearby. Some diplomatic tensions
          or refugee flows, but no security impact on travelers inside this country.
  ORANGE: Active conflict in a neighboring country with DOCUMENTED, SPECIFIC spillover INTO
          this country. Spillover must be concrete — not theoretical or potential:
            - Significant refugee flows that are documented as creating measurable security
              pressure inside this country (not just humanitarian presence)
            - Cross-border armed incidents that have actually occurred on this country's soil
            - Armed groups physically operating across the border into this country
            - Economic crisis directly traceable to the regional conflict that affects
              traveler safety (not just higher prices or general economic strain)
          NOT ORANGE: Geographic proximity to a conflict. Diplomatic tensions. Being part of
          an alliance that supports a warring party. Theoretical retaliation risk.

  RED:    Direct kinetic threat that has ALREADY materialised on this country's territory:
          - Missiles or drone strikes originating from outside have actually hit this country
          - Armed groups have crossed the border and are operating inside this country
          - The country is actively providing military support to a warring party AND has
            suffered documented retaliatory strikes on its own soil as a result
          CRITICAL: Potential retaliation is NOT RED. Diplomatic support for a war is NOT RED.
          Being a NATO member near a conflict is NOT RED. RED requires actual kinetic events
          already having occurred on the country's territory — not just risk of them.

  ISLAND NATIONS AND GEOGRAPHIC BARRIERS: For countries where physical cross-border
  spillover is structurally constrained (islands, countries separated by sea or large
  geographic buffers), the bar for ORANGE is higher. "Neighbouring conflict" must involve
  a concrete documented mechanism by which the conflict affects travelers inside this
  country. Geographic proximity across water does not automatically create spillover.

  IDENTITY LAYERS: Regional instability is geography-driven and gender/identity-neutral.
  Do NOT raise regional_instability for identity layers (solo_women, jewish_israeli, etc.)
  unless the briefing documents a specific mechanism by which the regional situation creates
  a DIFFERENT risk for that identity group vs. general travelers. In practice this is almost
  never the case — regional instability should almost always be inherited from base.

  NOTE: This category is capped at RED. Do not assign PURPLE.

TERRORISM — Score based on organised non-state actors attacking civilians INSIDE this country.
  The question is: will a traveler be targeted by a terrorist group while visiting?

  WHAT COUNTS AS TERRORISM (for this score):
    - Non-state actors (IS, Al-Qaeda, Boko Haram, ETA-type groups, etc.) conducting attacks
      on civilians or civilian infrastructure inside the country.
    - Politically-motivated violence by non-state groups against the civilian population,
      including foreign travelers.

  WHAT DOES NOT COUNT AS TERRORISM (do NOT score here — use armed_conflict instead):
    - Military strikes between states: missiles, airstrikes, drone attacks by one country
      against another are ARMED CONFLICT, not terrorism. Score them under armed_conflict.
    - A country being a "state sponsor of terrorism" abroad does NOT raise its terrorism
      score. Iran sponsoring Hezbollah in Lebanon is not terrorism against visitors in Tehran.
    - Wartime framing: one side calling the other's military operations "terrorism" is
      irrelevant. Score what organised non-state groups are doing to civilians inside
      the country's borders.
    - Proxy groups operating in OTHER countries on behalf of this government: score in
      those countries' terrorism scores, not here.
    - ACTIVE WAR CONTEXT — CRITICAL: When a country is in an active armed conflict (armed_conflict
      RED or PURPLE), rockets, missiles, and military-style attacks by the WARRING PARTIES
      (including non-state armed groups like Hamas or Hezbollah engaged as belligerents in
      that specific conflict) are ARMED CONFLICT events, not terrorism. Do NOT double-count
      them under terrorism. The terrorism score for such a country should reflect only attacks
      by groups SEPARATE FROM the main armed conflict — e.g. IS sleeper cells conducting
      bombings unrelated to the main war, or domestic extremist groups.
      Example: In a country at war, the terrorism score should reflect knife attacks,
      lone-wolf stabbings, shootings, bombings by groups SEPARATE from the main conflict —
      not rocket/missile attacks by the warring parties, which are already in armed_conflict.
      Apply the PURPLE terrorism threshold (near-weekly organised non-war attacks) only to
      non-war domestic terrorism, not to war-context military operations by belligerents.

  CALIBRATION EXAMPLES:
    Ukraine (March 2026): Russia's missile strikes = armed_conflict. IS/Salafi groups in
      Ukraine? No sustained presence. Terrorism = YELLOW or ORANGE at most.
    Iran (March 2026): IRGC operations abroad = not terrorism inside Iran. IS has attacked
      Iranian targets (Ahvaz 2018, Kerman 2024 shrine attack). Score based on those
      incidents. Iran terrorism ≠ PURPLE. Likely ORANGE (organised IS capability demonstrated
      inside Iran, intermittent attacks, but not a sustained campaign against visitors).
    Nigeria: Boko Haram/ISWAP conduct weekly attacks in NE. = PURPLE in NE; RED nationally.
    France: IS-inspired lone-wolf and cell attacks; multiple incidents in recent years. = RED.
    Netherlands (March 2026): Ashab al-Yamin (Iran-linked) conducted ~4 small explosive
      device attacks with zero casualties. Organised group confirmed, demonstrated device
      capability, but NO deaths or injuries. = ORANGE (group + capability, but zero-casualty
      attacks do not meet RED condition (b)). NCTV Level 4 informs context but does not
      override the casualty requirement in our framework.

  GREEN:  No credible non-state threat. No attacks with fatalities in 5+ years.
          No organised groups with stated intent to attack inside the country.
  YELLOW: Very low threat. Only foiled plots, minor incidents, OR a single isolated
          lone-wolf attack with no fatalities and no organised group. Credible threat
          level exists but no demonstrated kill capability or repeat pattern.
  ORANGE: A credible organised non-state group exists with demonstrated attack capability
          inside the country. 1-2 attacks with 1-4 deaths each in past 2 years.
          OR a single lone-wolf attack with fatalities, politically/ideologically motivated,
          with no organised group confirmed and no recurrence within 12 months. A second
          such lone-wolf attack within 12 months triggers RED (see RED rule below).
  RED:    Active organised non-state campaign. Requires BOTH:
          (a) identified organised group with stated/demonstrated ongoing intent, AND
          (b) multiple attacks WITH DEATHS in past 2 years, OR 3+ attacks in past 12 months
              WITH FATALITIES OR MULTIPLE INJURED, OR persistent monthly incidents WITH
              CASUALTIES (deaths or injuries).
          Attacks with zero casualties — even many of them — do NOT satisfy condition (b).
          A group that has demonstrated the ability to detonate devices but has not killed
          or injured anyone scores ORANGE (demonstrated capability, no proven lethality).
          AND attacks are not exclusively focused on military targets — civilian targets,
          civilian infrastructure, or attacks in public areas must be part of the pattern.
          NOTE: Targeting of specific ethnic or national groups is scored in identity layers
          (jewish_israeli, etc.), NOT in the base layer. Base layer terrorism scores risk
          to the general traveler population from political/ideological violence.
          LONE-WOLF FREQUENCY TRIGGER: Even without a confirmed organised group, RED
          applies if there are 2+ separate lone-wolf attacks with fatalities, each
          politically or ideologically motivated, in any rolling 12-month period.
          Rationale: if the ideological environment produces two or more independent fatal
          attacks in a year, the structural radicalization risk is RED regardless of
          whether a coordinating organisation exists.
          KEY: A single lone-wolf attack, even with many deaths, stays ORANGE.
  PURPLE: Sustained high-frequency organised campaign. Weekly or near-weekly attacks by
          organised non-state actors inside the country. OR 3+ attacks with 2+ deaths each
          in the past year by the same or affiliated group.
          AND attacks are not exclusively focused on military targets.
          PURPLE is the superlative of RED. Requires frequency AND organised capability.
          Do NOT apply PURPLE because a country is at war or sponsors terrorism abroad.
  NOTE: State military operations between countries = armed_conflict, never terrorism here.
  NOTE: A group that primarily attacks military targets but also conducts attacks in civilian
        areas meets the "not exclusively military" requirement (e.g., if the group occasionally
        car-bombs cities alongside military operations).
  NOTE: National government threat level ratings (NCTV Level 4, MI5 SEVERE, OCAM Level 3,
        etc.) are INPUTS to the intelligence briefing — they inform context and help identify
        groups and incidents. They do NOT directly determine the terrorism score and cannot
        substitute for our framework criteria. A country with NCTV Level 4 but only
        zero-casualty attacks = ORANGE, not RED. Apply our criteria independently.
  NOTE: Cross-country consistency — if the same group is active in two neighbouring countries
        with similar attack patterns and casualty profiles, both countries should score at
        the same level for terrorism. If your scores differ for neighbouring countries with
        the same threat actor, re-examine your reasoning.

GOVERNANCE & LEGAL CLIMATE [DB field: civil_strife] — Score internal political violence,
  mass unrest, and governmental collapse affecting travelers' physical safety.
  This covers ONLY: protests, riots, coups, government crackdowns involving violence,
  political violence between factions, and collapse of public order.
  It does NOT cover: war (armed_conflict), terrorism by non-state groups (terrorism),
  criminal violence (crime), OR state legal persecution of travelers (legal_risk).
  Legal persecution, arbitrary detention, and criminalization of traveler behavior belong
  in legal_risk — do NOT score them here. Do not double-count across categories.

  GREEN:  Politically stable. Protests are rare and peaceful. Government transitions follow
          established rules. No political violence.
  YELLOW: Occasional protests or political tensions. Demonstrations are peaceful or quickly
          dispersed. No significant violence between political actors or between state and citizens.
  ORANGE: Sustained protests with episodes of violence. Tear gas, water cannons, periodic
          arrests of protesters. Some city areas periodically unsafe due to civil unrest.
          OR: political situation is tense with documented risk of rapid deterioration
          (e.g. contested elections, recent coup attempt, factional violence in recent months).
  RED:    Widespread unrest or sustained political violence affecting major cities. Government
          using lethal force against civilian protesters (not armed combatants — those are
          armed_conflict). OR: significant breakdown of public order in parts of the country
          due to political conflict, such that travelers face risk from the chaos itself.
  PURPLE: Coup, active civil war at the political/governance level, or complete collapse of
          public order. Government has lost control of significant urban territory to competing
          political factions. Emergency laws suspending civil rights with violent nationwide
          enforcement against the civilian population.

  IMPORTANT: Wartime legal measures (curfews, martial law in conflict zones, military
  checkpoints) are armed_conflict factors, NOT civil_strife. Do not score a country's
  civil_strife higher simply because it is at war. Ukraine's civil_strife may be GREEN
  (strong democratic government, high public support for war effort, no internal repression
  of civilians) even while armed_conflict is PURPLE. Score each category independently.

LEGAL RISK [DB field: legal_risk] — Score the legal and institutional threat the STATE
  poses to travelers who are INSIDE the country: criminalization of behaviors, arbitrary
  detention, compelled compliance, denial of legal rights.
  Distinct from civil_strife (which covers unrest, riots, political violence by the population)
  and crime (which covers non-state criminal actors).

  SCOPE — what this category covers and does NOT cover:
  COVERS: Laws actively enforced against foreign visitors, documented arbitrary detention of
          travelers, state use of foreigners as political bargaining chips, criminalization of
          identity or behavior with real enforcement against tourists.
  DOES NOT COVER:
    — Entry bans or visa restrictions: a country that refuses entry to certain nationalities
      is exercising border control, not creating legal risk for travelers who are inside.
      Score legal_risk based on what happens AFTER a traveler legally enters.
    — War-related emergency measures: military curfews, checkpoints, restricted military zones,
      conscription of dual nationals — these are armed_conflict factors, NOT legal_risk.
      A country at war may still score GREEN or YELLOW on legal_risk if its legal system
      treats foreign visitors normally. Do not raise legal_risk because a country is at war.
    — Laws that exist on the books but are never or rarely enforced against tourists.
      Score demonstrated enforcement patterns, not theoretical legal exposure.

  DECISION RULE — ask before scoring:
    Has the state detained foreign nationals (tourists/business travelers) WITHOUT cause in
    the past 3 years, or used them as diplomatic leverage?        → YES = at least RED
    Does the state actively enforce behavioral laws (dress, conduct, content) against
    foreign tourists with documented consequences?                → YES = at least ORANGE
    Do laws exist that could apply to travelers but enforcement
    against tourists is rare or undocumented?                     → YES = YELLOW at most
    Does the state treat foreign tourists with standard rule-of-law protections?
                                                                  → YES = GREEN

  GREEN:  Strong rule of law protecting travelers. No documented pattern of arbitrary detention
          of foreigners. State does not criminalize ordinary traveler behavior. Travelers'
          legal rights respected and consular access guaranteed without obstruction.

  YELLOW: Generally functional legal protections. Some laws could theoretically apply to
          travelers (photography restrictions, alcohol rules, dress expectations) but are
          rarely if ever enforced against foreign tourists. Minor bureaucratic friction.
          Consular access functional. Travelers with no local political profile face no
          meaningful legal jeopardy.

  ORANGE: Laws that could meaningfully affect travelers are actively enforced against foreigners.
          Mandatory behavioral requirements (dress codes, conduct laws, content restrictions)
          with documented enforcement against tourists. State has detained foreigners briefly
          for minor infractions. Travelers must actively comply with specific local laws to
          avoid legal exposure. Consular access sometimes delayed or complicated.

  RED:    Documented pattern of arbitrary detention of foreign nationals, including for
          political reasons or as diplomatic leverage. Risk of arrest for activities legal
          in the traveler's home country is real and documented. State has used foreign
          nationals as bargaining chips (not merely a legal risk on paper — actual incidents).
          Consular access not guaranteed. Certain nationalities or identities specifically
          targeted by authorities with documented recent cases.
          Note: legal_risk RED forces total score to at least ORANGE (soft floor applies).

  PURPLE: State systematically exposes ordinary foreign travelers to near-certain legal
          jeopardy. The state itself is the primary threat. No reliable consular protection.
          Reserved for the most extreme cases — not for countries that are authoritarian
          toward their own citizens, but for countries where being a foreign traveler
          itself creates a high probability of detention, prosecution, or harm by the state.
          Note: legal_risk PURPLE forces total score to at least RED (soft floor applies).

CRIME — Score criminal risk to travelers specifically. Anchor on homicide rate + kidnap risk.
  Score the national picture. If crime risk varies significantly by region (e.g. safe
  tourist zones vs. dangerous interior), note this in the narrative and use the regional
  scoring system — do not lower the national score to reflect only the safe zones.
  Do NOT conflate with terrorism (political violence) or civil strife (political unrest).
  Note: crime ORANGE forces total score to at least YELLOW (soft floor applies).

  GREEN:  Under 5 homicides/100k/year. Petty theft possible in tourist areas but violent
          crime against travelers rare. No kidnap risk. Normal urban precautions sufficient.
  YELLOW: 5-15 homicides/100k/year. Pickpocketing common in tourist areas. Opportunistic
          crime possible. Violent crime against travelers uncommon. Standard awareness needed.
  ORANGE: 15-30 homicides/100k/year. OR documented kidnapping in specific provinces.
          Robbery and assault realistic risks. Certain areas and times to avoid.
  RED:    30-60 homicides/100k/year. OR documented kidnapping-for-ransom specifically
          targeting foreign nationals. OR criminal organisations controlling significant
          territory that travelers may need to cross. Serious precautions required.
          Note: crime RED forces total score to at least ORANGE (soft floor applies).
  PURPLE: Over 60 homicides/100k/year. OR criminal organisations exercise SUBSTANTIAL
          territorial control over MULTIPLE large provinces — state has effectively ceded
          governance of significant geographic areas. Travelers face systemic inescapable risk.
          NOT PURPLE: gang presence in city neighbourhoods; cartel active in one city;
          high crime rate with a functioning state. Apply the 60/100k threshold and the
          substantial territorial control test independently for each country.
          Note: crime PURPLE forces total score to at least RED (soft floor applies).

HEALTH — Score based on traveler's ability to access safe medical care and avoid serious disease.
  Score what a traveler in a MAJOR CITY would experience — not worst-case rural areas.
  Score the PHYSICAL CAPABILITY of the healthcare system, not its workload or stress level.

  DECISION RULE — ask before scoring above YELLOW:
    Can a foreign traveler with a medical emergency (broken leg, heart attack, appendicitis)
    get treatment at a hospital in the capital or a major city?
      YES, reliably and to a high standard                        → GREEN
      YES, but with limitations, delays, or rural gaps            → YELLOW
      YES, but quality is poor and evacuation likely needed       → ORANGE
      UNCERTAIN — some hospitals open but system severely degraded → RED
      NO — hospitals in major cities are non-functional           → PURPLE

  CRITICAL WAR-CONTEXT RULE: A hospital treating large numbers of war casualties is STILL
  A FUNCTIONING HOSPITAL. "Under strain", "overwhelmed", or "stretched by the conflict"
  does NOT change the score unless the hospital has physically stopped treating civilian
  patients. Score whether a traveler CAN get care — not whether the system is stressed.
  A country at war whose capital hospitals remain open and treating patients scores based
  on their underlying capability, not the volume of wartime demand placed on them.

  GREEN:  High-income country with fully functional hospitals. Standard vaccinations sufficient.
          No active disease outbreaks. Medical care of reliable quality in major cities.
  YELLOW: Adequate healthcare in major cities. Some rural limitations. Minor endemic disease
          considerations. Travel health insurance advisable. Emergency care accessible in cities.
  ORANGE: Limited or variable healthcare outside major cities. Active endemic diseases requiring
          prophylaxis (malaria, dengue, cholera). Medical evacuation insurance strongly recommended.
  RED:    Poor healthcare infrastructure even in major cities. Standard surgical care not reliably
          available or safe. Active epidemic or disease outbreak affecting travelers.
          Soft floor: health RED -> total at least ORANGE.
  PURPLE: Healthcare system has PHYSICALLY COLLAPSED in major cities — hospitals non-functional
          (bombed, closed, critically out of supplies). A traveler with a medical emergency has
          nowhere to go. This is an extreme threshold.
          NOT PURPLE: strained, overwhelmed, or busy hospitals that are still open and treating
          patients. NOT PURPLE: a country at war where hospitals in the capital still function.
          Soft floor: health PURPLE -> total at least RED.

INFRASTRUCTURE — Score based on the PHYSICAL STATE of roads, power, water, and transport.
  Score ONLY physical capability — not security conditions, not missile alerts, not curfews.

  ESCALATION GATE — answer these before scoring above YELLOW:
    Are major city roads physically impassable (destroyed, not just security-restricted)?  YES/NO
    Is the power grid failing in major cities on a sustained basis (not just alerts)?      YES/NO
    Is water supply non-functional in major cities?                                        YES/NO
    Is internet/mobile communications down in major cities?                                YES/NO

    All NO  → YELLOW at most. Stop here.
    1-2 YES → ORANGE (utilities unreliable in meaningful ways)
    3-4 YES affecting major cities → RED (system-wide degradation)
    All YES and ongoing with no functioning alternatives → PURPLE

  CRITICAL: Missile alerts, curfews, and security restrictions are armed_conflict factors —
  they do NOT affect infrastructure score. A country where roads are drivable, power is
  on, water flows, and internet works in major cities scores YELLOW at most regardless of
  the security situation. Score the physical state of the systems, not the threat context
  around them. "Infrastructure is under attack" is NOT the same as "infrastructure has
  collapsed." Score what has actually failed, based on the gate questions above.

  GREEN:  Modern, reliable infrastructure. Safe roads, reliable power/water/internet in cities.
  YELLOW: Generally good with some gaps. Urban utilities reliable. Some rural road/utility gaps.
          A country at war whose physical urban infrastructure still functions scores here.
  ORANGE: Unreliable infrastructure in significant parts of the country. Frequent unpredictable
          power/water outages in cities, OR dangerous road conditions widely affecting travelers.
  RED:    System-wide physical degradation. Utilities unreliable throughout the country, OR
          infrastructure physically damaged by conflict causing documented ongoing failure in
          major cities (not just localized or temporary damage).
          Soft floor: infrastructure RED -> total at least ORANGE.
  PURPLE: Infrastructure PHYSICALLY COLLAPSED in major cities. Roads impassable, power/water/
          comms non-functional on a sustained basis. A traveler cannot move or communicate.
          Soft floor: infrastructure PURPLE -> total at least RED.

QUANTITATIVE THRESHOLDS:

IMPORTANT — APPROXIMATE ANCHORS, NOT HARD CUTOFFS:
These numbers are calibration anchors, not scientific thresholds. Real-world data is
noisy — especially outside OECD countries where reporting is incomplete or delayed.
When data quality is LOW, use the number as a guide but apply judgment. A country
reporting 14.8 homicides/100k with poor reporting systems may be effectively ORANGE.
A country at 31/100k with well-documented data and good enforcement may trend toward
RED but warrant MEDIUM confidence. Confidence levels matter as much as the scores.

TERRORISM thresholds (attacks by non-state actors inside the country against civilians):
  CRITICAL: Do NOT score military strikes between states here. Missiles, airstrikes,
  and drone attacks between state militaries = armed_conflict. "State sponsor of
  terrorism" status does NOT raise a country's terrorism score. Score only what
  organised non-state groups are doing INSIDE this country to civilians.

  GREEN:  0 attacks with fatalities in past 5 years. No credible active non-state groups.
  YELLOW: 0-1 deaths from non-state actor terrorism in past 3 years. OR a single
          isolated lone-wolf attack with no organised group. No repeat pattern.
  ORANGE: Organised non-state group with demonstrated attack capability inside the country.
          1-2 attacks with 1-4 deaths each in past 2 years. OR a single high-casualty
          lone-wolf attack with NO organised group — ORANGE until pattern emerges.
  RED:    Active organised non-state campaign: identified group + multiple attacks in past
          2 years with deaths, OR 3+ attacks in past 12 months, OR persistent monthly
          incidents — AND not exclusively targeting military targets.
          ALSO RED: 2+ lone-wolf attacks with fatalities, politically/ideologically
          motivated, in any rolling 12-month period — even without confirmed organised group.
  PURPLE: Sustained weekly/near-weekly attacks by organised non-state actors inside the
          country — AND not exclusively targeting military targets. OR 3+ attacks with
          2+ deaths each in past year by same group.
          Do NOT apply because country is at war or sponsors terrorism abroad.
          Nigeria NE (Boko Haram), Mali (JNIM) = PURPLE examples.
          Ukraine, Iran, Saudi Arabia = NOT PURPLE on this criterion.

ARMED CONFLICT thresholds:
  GREEN:  No armed conflict on national territory. Military not engaged domestically.
  YELLOW: Localized/frozen conflict in remote border areas not affecting traveler movement.
          OR overseas military deployment with zero home-soil fighting and only a remote
          possibility of spillover. A frozen conflict stable for years with no recent
          escalation signals stays YELLOW.
  ORANGE: Active conflict confined to part of the country; capital and major cities safe
          and accessible; conflict zones known and avoidable.
          OR a dormant/frozen conflict where credible escalation signals exist in the
          past 12 months (ceasefire violations, military mobilization, breakdown of
          peace talks). A purely historical frozen conflict with no recent escalation = YELLOW.
          OR overseas deployment likely to lead to attacks inside the country.
          Key rule: ORANGE minimum if there is any active fighting on national territory.
  RED:    Widespread conflict affecting multiple major regions. OR capital or large cities
          directly threatened. OR regular missile, rocket, drone, or airstrike attacks on
          populated areas — regardless of interception rate, if incoming fire is routine.
          Key rule: RED minimum for routine incoming fire. RED hard-vetoes the total.
  PURPLE: Full-scale war meeting at least 2 of 4 PURPLE criteria (see checklist).
          A war-fighting nation with functioning government, military, shelters, and open
          airports = RED, not PURPLE. PURPLE = collapsed states only.

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
  Score the national picture. Where crime risk varies sharply by region, note it in the
  narrative and use regional scoring — do not lower the national score to reflect only
  the safe tourist zones.
  GREEN:  Under 5 per 100k. Low organised crime. Travelers safe with normal precautions.
  YELLOW: 5-15 per 100k. Petty theft common. Violent crime against travelers uncommon.
  ORANGE: 15-30 per 100k. OR kidnapping risk in specific provinces/areas.
  RED:    30-60 per 100k. OR documented kidnapping-for-ransom targeting foreigners.
          OR criminal organisations controlling significant territory travelers may cross.
          Soft floor: crime RED -> total at least ORANGE.
  PURPLE: Over 60 per 100k. OR criminal organisations exercise SUBSTANTIAL territorial
          control over multiple states/provinces — state has effectively ceded governance
          of significant geographic areas.
          NOT PURPLE: gang presence in neighbourhoods; cartel active in one city; high
          crime rate with a functioning state that still controls most territory.
          Soft floor: crime PURPLE -> total at least RED.

HEALTH thresholds:
  GREEN:  High-income country with functional hospital system. Routine vaccinations sufficient.
          No active disease outbreaks. Emergency care reliably available in major cities.
  YELLOW: Adequate urban healthcare. Some rural limitations. Minor endemic disease risk.
  ORANGE: Limited healthcare outside major cities. Active endemic diseases (malaria, dengue,
          cholera in specific regions). Medical evacuation insurance strongly recommended.
  RED:    Poor healthcare infrastructure nationwide. Active epidemic or outbreak. Standard
          surgical care not reliably available. Medical evacuation very likely needed.
          Soft floor: health RED -> total at least ORANGE.
  PURPLE: Healthcare system has physically collapsed. No safe medical care available in
          major cities. (Yemen 2023, Gaza 2024, Syria 2015-2019 = true PURPLE.)
          NOT PURPLE if hospitals are open and treating patients, even under strain.
          Soft floor: health PURPLE -> total at least RED.

INFRASTRUCTURE thresholds (road fatality rate per 100,000/year as primary anchor — also
consider power/water reliability, internet access, and transport system reliability):
  GREEN:  Road fatality rate under 10 per 100k/year. Reliable power, water, internet.
  YELLOW: Road deaths 10-20 per 100k. Generally reliable utilities. Some rural/seasonal gaps.
  ORANGE: Road deaths 20-30 per 100k. OR frequent power/water outages. Rural roads dangerous.
  RED:    Road deaths over 30 per 100k. OR utilities unreliable nationwide. OR infrastructure
          physically damaged by conflict/disaster with consequences for travelers.
          Soft floor: infrastructure RED -> total at least ORANGE.
  PURPLE: Infrastructure physically collapsed in major cities. No reliable roads, power,
          water, or communications. Movement impossible without private logistics.
          Soft floor: infrastructure PURPLE -> total at least RED.

CALIBRATION ANCHORS — apply these data thresholds to whatever the briefing contains:

  Crime rate anchor: <5 homicides/100k = GREEN. 5-15 = YELLOW. 15-30 = ORANGE.
  30-60 = RED. >60 = PURPLE threshold (also requires territorial control test).

  Infrastructure anchor: Road fatality rate <10/100k = GREEN. 10-20 = YELLOW.
  20-30 = ORANGE. >30 = RED. Physical collapse of utilities in major cities = PURPLE.

  Health anchor: Hospitals open and treating patients in capital = GREEN or YELLOW.
  Poor quality but functional = ORANGE. Active epidemic = RED. Bombed/closed = PURPLE.

  Armed conflict anchor: No fighting on territory = GREEN/YELLOW. Conflict in one
  region, capital safe = ORANGE. Routine missile attacks on populated areas OR widespread
  multi-region conflict OR capital threatened = RED minimum. Full-scale war with no
  identifiable safe zone across the country = apply the 2-of-4 PURPLE checklist.

  Country is the WAR AGGRESSOR fighting on another country's soil: the fighting is NOT
  on its own territory. Score armed_conflict based only on what reaches ITS soil
  (retaliatory strikes, cross-border incidents) — not based on its role in the conflict.

  Country with overseas military deployment only (no home-soil fighting): armed_conflict
  YELLOW at most, regardless of the scale of the overseas operation.

  Terrorism in an active war context: attacks by warring parties (rockets, missiles,
  military-style operations by the conflict's belligerents) = armed_conflict. Score
  terrorism only on non-war non-state actor attacks against civilians (sleeper cells,
  domestic extremists, criminal-political violence unrelated to the main war).

TOTAL SCORE LOGIC (Python calculates this — for your reference only):
  LAYER 1 — Hard veto:
    armed_conflict PURPLE → total = PURPLE
    armed_conflict RED    → total = RED (hard veto, no exceptions)
  LAYER 2 — Weighted average (only reached when armed_conflict < RED):
    Security categories x2 weight; crime/health/infrastructure x1 weight.
    Avg <= 1.4 -> GREEN | 1.5-2.4 -> YELLOW | 2.5-3.4 -> ORANGE | 3.5-4.4 -> RED | >4.4 -> PURPLE
  LAYER 3 — Soft floors (ALL 7 categories):
    Any category PURPLE -> total at least RED
    Any category RED    -> total at least ORANGE
    Highest of (Layer 1 | weighted avg | soft floors) wins.
  Note: regional_instability is capped at RED — the model should not assign PURPLE to it.

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
This ceiling applies even under heavy daily missile or drone attacks, UNLESS the attacks
are so pervasive and widespread that no destination choice within the country meaningfully
reduces risk — in that case, the 2-of-4 PURPLE checklist takes precedence.

PURPLE total requires armed_conflict PURPLE (hard-veto) OR a combination of PURPLE
sub-scores driving the weighted average to 4.5+ while satisfying the checklist above.

RED means: serious documented risk requiring real security planning. Well-prepared
travelers with clear justification can go. The state provides partial but real
protection. Evacuation is possible though may be disrupted. Risk is serious but
mitigable. RED covers countries with widespread conflict and a functioning state,
high-crime environments, and authoritarian states that are dangerous but not collapsed.

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

REGIONAL FORMAT EXAMPLES (structure only — do not use these as score anchors):

  Country with active frontline conflict:
    Region "Active Frontline Zone" (areas within direct combat range):
      armed_conflict: one or two levels above country level → compute total accordingly
    Region "Rear/Western Areas" (far from fighting, normal urban life):
      armed_conflict: one level below country level → compute total accordingly
    Region "Capital / Major Commercial City" (if same as country baseline):
      [omit — no material difference from country level]

  Country with concentrated cartel/criminal control in specific states:
    Region "Cartel Heartland States":
      crime: one or two levels above country level, possibly civil_strife elevated
    Region "Tourist Corridors / Coastal Resorts":
      crime: one level below country level (heavy security presence, tourist-focused)

  Country with regional separatist or ethnic conflict in one part:
    Region "Conflict Province":
      armed_conflict, terrorism: elevated above country level
    Region "Rest of Country":
      [omit if same as country baseline]

Use the briefing evidence to identify the actual zones for the specific country being assessed.
Name regions descriptively (e.g. "Northern Border Region", "Eastern Provinces", "Capital Area").

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
    step2_model_used = None
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
                step2_model_used = model_name
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
        print(f"  [OK] Scoring complete [{step2_model_used}]")
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
    Thread-safe: multiple countries can run concurrently via --workers N.
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
                    "civil_strife", "legal_risk", "crime", "health", "infrastructure"]
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
    parser.add_argument("--workers",       type=int, default=1,
                        help="Number of countries to process in parallel (default: 1). "
                             "Recommended: 3-4 for all-countries runs. Each worker makes "
                             "concurrent Gemini API calls so stay under 5 to avoid 503s.")

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

    # Run baselines — sequential (workers=1) or parallel (workers>1)
    success = 0
    failed  = 0
    workers = max(1, min(args.workers, len(countries)))

    if workers == 1:
        # Sequential — simple loop, clean output
        for country_name, iso_code in countries:
            ok = run_country_baseline(country_name, iso_code, layers, force=args.force)
            if ok:
                success += 1
            else:
                failed += 1
    else:
        # Parallel — run up to `workers` countries simultaneously.
        # Within each country, layers still run in order (base before identity layers).
        # Output lines may interleave between countries — this is expected.
        # Each line is atomic (Python GIL), so no torn output.
        print(f"  [PARALLEL] Running {len(countries)} countries with {workers} workers\n")
        with ThreadPoolExecutor(max_workers=workers) as executor:
            future_map = {
                executor.submit(run_country_baseline, country_name, iso_code, layers, args.force):
                    (country_name, iso_code)
                for country_name, iso_code in countries
            }
            for future in as_completed(future_map):
                country_name, iso_code = future_map[future]
                try:
                    ok = future.result()
                    if ok:
                        success += 1
                    else:
                        failed += 1
                except Exception as exc:
                    print(f"\n[X] {country_name} ({iso_code}) raised an exception: {exc}")
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
