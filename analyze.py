#!/usr/bin/env python3
"""
TravelGuard — AI Analysis Pipeline (Gemini)

Takes raw data from ingestion and generates:
- 5-level threat scores (GREEN/YELLOW/ORANGE/RED/PURPLE) per category
- Base layer assessment (general travellers)
- Jewish/Israeli identity layer assessment
- AI summaries and recommendations

Usage:
    python analyze.py
"""

import os
import sys
import json
from datetime import datetime, timezone
from dotenv import load_dotenv
from supabase import create_client, Client
from google import genai
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib

# Load environment variables
load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not all([SUPABASE_URL, SUPABASE_KEY, GEMINI_API_KEY]):
    print("ERROR: Missing environment variables")
    sys.exit(1)

# Initialize clients
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
client = genai.Client(api_key=GEMINI_API_KEY)


def load_israeli_nsc_warnings():
    """Load Israeli NSC warnings config."""
    import yaml
    try:
        with open("israeli_nsc_warnings.yaml", "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
            return data.get("countries", {})
    except FileNotFoundError:
        return {}


def get_nsc_level_for_country(country_name, nsc_data):
    """Get NSC threat level for a country."""
    return nsc_data.get(country_name, {}).get("level", None)


def build_analysis_prompt(country_name, identity_layer, nsc_level=None, base_analysis=None):
    """Build the Gemini analysis prompt."""
    
    base_prompt = f"""You are a travel security analyst. Analyze the current threat situation in {country_name} for {'general travelers' if identity_layer == 'base' else ('solo women travelers' if identity_layer == 'solo_women' else 'Jewish and Israeli travelers')}.

IMPORTANT: Use the 5-level threat scale:
- GREEN (1): Safe / Normal conditions
- YELLOW (2): Exercise Caution / Minor concerns
- ORANGE (3): Heightened Risk / Significant concerns  
- RED (4): High Risk / Reconsider travel
- PURPLE (5): Extreme Risk / Do not travel (war zones, active genocide, zero consular protection)

Analyze these 7 threat categories independently:
1. Armed Conflict
2. Regional Instability
3. Terrorism
4. Civil Strife
5. Crime
6. Health
7. Infrastructure

For each category, assign a threat level (GREEN/YELLOW/ORANGE/RED/PURPLE) based on current conditions.

"""

    if identity_layer == "solo_women":
        base_prompt += f"""
IDENTITY-SPECIFIC ANALYSIS:
You are analyzing threats specifically for SOLO WOMEN TRAVELERS.

CRITICAL COMPARISON RULES:
1. START with the base layer scores as your baseline
2. For EACH category, ask: "Does being a solo woman make this threat WORSE, BETTER, or THE SAME?"
3. ONLY change a score if there's a CLEAR gender-specific reason
4. If you increase a score, you MUST explain why in the reasoning

Base layer assessment (YOUR STARTING POINT):
{format_base_analysis(base_analysis) if base_analysis else "Not available"}

COMMON MISTAKES TO AVOID:
- Armed conflict affects everyone equally → Should be SAME as base unless women specifically targeted
- Civil strife is general population risk → Should be SAME as base unless gender-based violence during unrest
- Infrastructure problems affect everyone → Should be SAME as base
- DO NOT inflate scores just because the traveler is a woman - only if there's ADDITIONAL gender-specific risk

Gender-specific threat factors for solo women:
- Gender-based violence and harassment rates
- Sexual assault statistics and legal protections for victims
- Cultural attitudes: dress codes, behavior restrictions, women's mobility rights
- Legal status: Can women travel alone legally? Are there guardianship laws?
- Safety of public transport/taxis for women traveling alone
- Police response to crimes against women (do they take reports seriously?)
- Healthcare access for women (reproductive health, assault victims)

SCORING GUIDANCE:
- Countries with HIGH rates of gender-based violence: Increase crime by 1-2 levels vs base
- Countries with legal restrictions on women: Note in summary but crime score depends on enforcement
- Countries where armed conflict specifically targets women: Increase armed_conflict (rare)
- Countries with poor legal protections: Increase crime if also high violence rates
- Civil strife should ONLY increase if there's documented gender-based violence during protests/unrest
"""

    elif identity_layer == "jewish_israeli":
        base_prompt += f"""
IDENTITY-SPECIFIC ANALYSIS:
You are analyzing threats specifically for Jewish and Israeli travelers. 

CRITICAL COMPARISON RULES:
1. START with the base layer scores as your baseline
2. For EACH category, ask: "Does being Jewish/Israeli make this threat WORSE, BETTER, or THE SAME?"
3. ONLY change a score if there's a CLEAR identity-specific reason
4. If you increase a score, you MUST explain why in the reasoning

Base layer assessment (YOUR STARTING POINT):
{format_base_analysis(base_analysis) if base_analysis else "Not available"}

COMMON MISTAKES TO AVOID:
- Egypt base=YELLOW armed_conflict, jewish=YELLOW → WRONG if there's antisemitism or Israel tensions
- Saudi base=YELLOW, jewish=YELLOW → WRONG - Saudi bans Israeli passports, should be RED or PURPLE
- DO NOT make them the same unless there's truly no difference

Identity-specific threat factors for Jewish/Israeli travelers:
- Antisemitic incidents, hate crimes, targeted attacks
- Legal barriers: countries banning Israeli passports (Iran, Saudi, Lebanon, Syria, Libya, etc.)
- Proximity to Israel-related conflicts (Gaza, Lebanon, Iran tensions)
- Institutional hostility toward Jews/Israelis
- Israeli embassy presence and consular protection
- Local Jewish community safety and infrastructure
- Recent protests or violence targeting Jews/Israelis

SCORING GUIDANCE:
- Countries that BAN Israeli passports: Minimum RED in all conflict-related categories
- Active antisemitic violence: Increase terrorism/civil strife by 1-2 levels
- Near active Israel conflicts: Increase armed_conflict/regional_instability
- European countries with recent antisemitic attacks: Increase terrorism/crime vs base
"""
        
        if nsc_level:
            base_prompt += f"""
Israeli National Security Council (NSC) Travel Warning Level: {nsc_level}/4
(1=Safe, 2=Caution, 3=Reconsider, 4=Do Not Travel)

Use this as a baseline but adjust based on current news. If recent events contradict the NSC level, note this discrepancy.
"""

    base_prompt += """
Return your analysis as valid JSON with this exact structure:
{
  "armed_conflict": "GREEN|YELLOW|ORANGE|RED|PURPLE",
  "regional_instability": "GREEN|YELLOW|ORANGE|RED|PURPLE",
  "terrorism": "GREEN|YELLOW|ORANGE|RED|PURPLE",
  "civil_strife": "GREEN|YELLOW|ORANGE|RED|PURPLE",
  "crime": "GREEN|YELLOW|ORANGE|RED|PURPLE",
  "health": "GREEN|YELLOW|ORANGE|RED|PURPLE",
  "infrastructure": "GREEN|YELLOW|ORANGE|RED|PURPLE",
  "reasoning": "Brief explanation of key threats driving the scores",
  "summary": "Write 2-3 SHORT paragraphs (150-200 words total). Use simple, direct sentences. Say what's actually happening - specific incidents, dates, numbers. Avoid words like 'complex', 'multifaceted', 'notably', 'furthermore'. Write like you're briefing a friend, not writing a report.",
  "watch_factors": "List 2-4 SPECIFIC upcoming events or ongoing situations that could change the threat level. Include dates/timeframes when known. Examples: 'Presidential election May 15, 2026', 'Israel-Hezbollah ceasefire expires March 1', 'Monsoon season June-September increases flood risk', 'Tensions with [neighbor] over [border dispute] could escalate'. For countries in conflict zones, ALWAYS mention neighboring conflicts and their potential spillover. Be concrete and actionable.",
  "recommendations": {
    "movement_access": "One sentence recommendation",
    "emergency_preparedness": "One sentence recommendation",
    "communications": "One sentence recommendation",
    "health_medical": "One sentence recommendation",
    "crime_personal_safety": "One sentence recommendation",
    "travel_logistics": "One sentence recommendation"
  },
  "sources": [
    "List 3-4 real sources only. Format: 'US State Department Travel Advisory' or 'BBC News: Iran' - NO URLs unless you have the exact one. NEVER write '[Your Country]' or 'local embassy' generically. If you don't have a specific source, skip it."
  ]
}

CRITICAL REQUIREMENTS:
- Write like a human analyst, not an AI
- Avoid AI phrases: "complex environment", "it's important to note", "multifaceted", "notably"
- Be concrete: "12 killed in bombing" not "security incident occurred"
- NO PLACEHOLDERS - never use brackets [ ] or generic descriptions
- Keep summary under 200 words total - be brief
- Sources: US State Dept, UK FCDO, BBC, Reuters, Le Monde OK. NO Al Jazeera, NO RT
- If you can't be specific, don't include it
- WATCH FACTORS: Always consider regional context - neighboring conflicts, upcoming elections, diplomatic tensions, seasonal risks
"""
    
    return base_prompt


def format_base_analysis(analysis):
    """Format base analysis for inclusion in identity prompt."""
    if not analysis:
        return "Not available"
    
    return f"""
Armed Conflict: {analysis.get('armed_conflict', 'N/A')}
Regional Instability: {analysis.get('regional_instability', 'N/A')}
Terrorism: {analysis.get('terrorism', 'N/A')}
Civil Strife: {analysis.get('civil_strife', 'N/A')}
Crime: {analysis.get('crime', 'N/A')}
Health: {analysis.get('health', 'N/A')}
Infrastructure: {analysis.get('infrastructure', 'N/A')}
"""


def analyze_country(country_name, identity_layer="base", base_analysis=None):
    """Run Gemini analysis for a country."""
    
    print(f"\n{'='*60}")
    print(f"Analyzing: {country_name} ({identity_layer} layer)")
    print('='*60)
    
    # Load NSC warnings if analyzing Jewish/Israeli layer
    nsc_data = {}
    nsc_level = None
    if identity_layer == "jewish_israeli":
        nsc_data = load_israeli_nsc_warnings()
        nsc_level = get_nsc_level_for_country(country_name, nsc_data)
        if nsc_level:
            print(f"Israeli NSC Warning Level: {nsc_level}/4")
    
    # Load recent headlines from ingestion
    recent_headlines = []
    try:
        with open("latest_headlines.json", "r", encoding="utf-8") as f:
            headlines_data = json.load(f)
            all_headlines = headlines_data.get("headlines", [])
            
            # Filter headlines relevant to this country
            country_keywords = [country_name.lower()]
            # Add common alternate names
            if country_name == "USA":
                country_keywords.extend(["united states", "america", "us ", "u.s."])
            elif country_name == "United Kingdom":
                country_keywords.extend(["uk", "britain", "british"])
            elif country_name == "Democratic Republic of the Congo":
                country_keywords.extend(["drc", "congo"])
                
            for headline in all_headlines:
                if any(keyword in headline.lower() for keyword in country_keywords):
                    recent_headlines.append(headline)
        
        print(f"Found {len(recent_headlines)} relevant headlines from recent ingestion")
        
    except FileNotFoundError:
        print("[!] No recent headlines found - analysis will rely on Gemini's knowledge only")
    
    # Build prompt
    prompt = build_analysis_prompt(country_name, identity_layer, nsc_level, base_analysis)
    
    # ALWAYS add current date/time first
    current_time = datetime.now(timezone.utc).strftime("%B %d, %Y at %H:%M UTC")
    prompt = f"=== CURRENT DATE/TIME ===\nToday is: {current_time}\n\nIMPORTANT: You are analyzing the situation AS OF this date.\n- Do NOT describe future events (after {current_time}) as if they already happened\n- You CAN mention upcoming future events (e.g., 'elections in March 2026') in watch_factors\n- Only cite past events that actually occurred BEFORE today's date\n\n{prompt}"
    
    # Add recent headlines as context
    if recent_headlines:
        prompt += f"\n\n=== RECENT NEWS CONTEXT ===\n"
        prompt += f"Here are recent headlines about {country_name}:\n\n"
        for i, headline in enumerate(recent_headlines[:30], 1):  # Max 30 headlines
            prompt += f"{i}. {headline}\n"
        prompt += f"\n=== ANALYSIS INSTRUCTIONS ===\n"
        prompt += f"Use BOTH:\n"
        prompt += f"1. Your comprehensive background knowledge of {country_name} (history, geography, political system, etc.)\n"
        prompt += f"2. The recent headlines above for CURRENT conditions and recent developments\n\n"
        prompt += f"If headlines contradict your training data, TRUST THE HEADLINES - they are more recent.\n"
        prompt += f"If headlines don't cover a threat category, use your background knowledge to assess it.\n"
    else:
        current_time = datetime.now(timezone.utc).strftime("%B %d, %Y at %H:%M UTC")
        prompt += f"\n\nToday is: {current_time}\n"
        prompt += f"Analyze {country_name} based on your current knowledge and recent events."
    
    try:
        print("Sending request to Gemini 2.5 Flash...")
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt,
            config={
                'temperature': 0.0,  # Maximum consistency
                'top_p': 0.95,
                'top_k': 40
            }
        )
        
        # Parse JSON response
        response_text = response.text.strip()
        
        # Remove markdown code fences if present
        if response_text.startswith("```json"):
            response_text = response_text[7:]
        if response_text.startswith("```"):
            response_text = response_text[3:]
        if response_text.endswith("```"):
            response_text = response_text[:-3]
        
        response_text = response_text.strip()
        
        analysis = json.loads(response_text)
        
        print("[OK] Analysis complete")
        
        # Self-verification: Check for hallucinations
        print("[>] Running hallucination check...")
        current_time_check = datetime.now(timezone.utc).strftime("%B %d, %Y at %H:%M UTC")
        verify_prompt = f"""You are a fact-checker. The current date is {current_time_check}.

Review this travel security analysis for {country_name} and identify ONLY serious factual errors that would mislead travelers.

CRITICAL: The analysis is dated {current_time_check}. Check if the analysis incorrectly describes future events (dates AFTER {current_time_check}) as if they already happened in the past. This is a fabrication.

ACCEPTABLE: Mentioning upcoming future events in watch_factors (e.g., "elections scheduled for March 2026")
UNACCEPTABLE: Describing future events as past (e.g., "protests occurred in March 2026" when today is February 2026)

Analysis to verify:
{json.dumps(analysis, indent=2)}

Check ONLY for:
- Completely fabricated events (events that never happened)
- Major factual errors (wrong country, wrong continent, impossible statistics)
- Dangerous misinformation (claiming safe when actually dangerous)

IGNORE:
- Minor wording issues or subjective phrasing
- Debate about threat categorization (that's subjective)
- Slightly outdated information that's still generally accurate
- AI-sounding language (we'll fix that separately)

Respond with JSON:
{{
  "has_critical_issues": true/false,
  "problems": ["Only list CRITICAL problems that would endanger travelers"],
  "severity": "CRITICAL|NONE"
}}

Only flag as CRITICAL if the analysis would genuinely mislead or endanger someone.
"""
        
        try:
            verify_response = client.models.generate_content(
                model='gemini-2.5-flash',
                contents=verify_prompt
            )
            
            verify_text = verify_response.text.strip()
            if verify_text.startswith("```json"):
                verify_text = verify_text[7:]
            if verify_text.startswith("```"):
                verify_text = verify_text[3:]
            if verify_text.endswith("```"):
                verify_text = verify_text[:-3]
            verify_text = verify_text.strip()
            
            verification = json.loads(verify_text)
            
            has_critical = verification.get("has_critical_issues", False)
            severity = verification.get("severity", "NONE")
            
            # ONLY block if truly critical
            if has_critical and severity == "CRITICAL":
                print(f"[X] BLOCKED - Critical safety issues detected:")
                for issue in verification.get("problems", []):
                    print(f"    - {issue}")
                print(f"    This analysis will NOT be stored.")
                return None
            else:
                print(f"[OK] Verification passed")
                
        except Exception as ve:
            print(f"[!] Verification check failed: {ve}")
            print(f"[!] Proceeding with analysis (no verification)")
        
        print(f"  Armed Conflict: {analysis.get('armed_conflict')}")
        print(f"  Terrorism: {analysis.get('terrorism')}")
        print(f"  Crime: {analysis.get('crime')}")
        
        return analysis
        
    except json.JSONDecodeError as e:
        print(f"[X] JSON parsing failed: {e}")
        print(f"Raw response: {response_text[:500]}")
        return None
    except Exception as e:
        print(f"[X] Analysis failed: {e}")
        return None


def calculate_total_score(category_scores):
    """
    Apply veto logic to calculate total country score.
    
    Veto-class categories: Armed Conflict, Regional Instability, Terrorism, Civil Strife
    
    Rules:
    - If any veto category is RED or PURPLE → total is at least that level
    - If highest veto category is ORANGE or below → use weighted average of all categories
    - Non-veto categories (Crime, Health, Infrastructure) never trigger veto
    """
    
    veto_categories = ["armed_conflict", "regional_instability", "terrorism", "civil_strife"]
    all_categories = ["armed_conflict", "regional_instability", "terrorism", "civil_strife", 
                      "crime", "health", "infrastructure"]
    
    # Score hierarchy
    level_hierarchy = {"GREEN": 1, "YELLOW": 2, "ORANGE": 3, "RED": 4, "PURPLE": 5}
    reverse_hierarchy = {1: "GREEN", 2: "YELLOW", 3: "ORANGE", 4: "RED", 5: "PURPLE"}
    
    # Check if any veto category is RED or PURPLE
    max_veto_level = 1
    for category in veto_categories:
        score = category_scores.get(category, "GREEN")
        level_value = level_hierarchy.get(score, 1)
        if level_value >= 4:  # RED (4) or PURPLE (5)
            if level_value > max_veto_level:
                max_veto_level = level_value
    
    # If veto triggered (RED or PURPLE found), return that level
    if max_veto_level >= 4:
        return reverse_hierarchy[max_veto_level]
    
    # Otherwise, calculate weighted average
    # Veto categories count double
    total_weight = 0
    weighted_sum = 0
    
    for category in all_categories:
        score = category_scores.get(category, "GREEN")
        level_value = level_hierarchy.get(score, 1)
        weight = 2 if category in veto_categories else 1
        weighted_sum += level_value * weight
        total_weight += weight
    
    # Calculate average and round
    avg = weighted_sum / total_weight
    
    # Round to nearest level
    if avg <= 1.4:
        return "GREEN"
    elif avg <= 2.4:
        return "YELLOW"
    elif avg <= 3.4:
        return "ORANGE"
    elif avg <= 4.4:
        return "RED"
    else:
        return "PURPLE"


def store_analysis(country_id, identity_layer, analysis):
    """Store analysis results in Supabase."""
    
    total_score = calculate_total_score(analysis)
    
    data = {
        "country_id": country_id,
        "identity_layer": identity_layer,
        "total_score": total_score,
        "armed_conflict": analysis.get("armed_conflict"),
        "regional_instability": analysis.get("regional_instability"),
        "terrorism": analysis.get("terrorism"),
        "civil_strife": analysis.get("civil_strife"),
        "crime": analysis.get("crime"),
        "health": analysis.get("health"),
        "infrastructure": analysis.get("infrastructure"),
        "ai_summary": analysis.get("summary"),
        "veto_explanation": analysis.get("reasoning"),
        "recommendations": json.dumps(analysis.get("recommendations", {})),
        "watch_factors": analysis.get("watch_factors", ""),
        "sources": json.dumps(analysis.get("sources", [])),
        "scored_at": datetime.now(timezone.utc).isoformat()
    }
    
    try:
        # Use upsert with on_conflict parameter to update existing records
        result = supabase.table("scores").upsert(
            data,
            on_conflict="country_id,identity_layer"
        ).execute()
        
        print(f"[OK] Stored in database: {total_score}")
        return True
    except Exception as e:
        print(f"[X] Database error: {e}")
        return False


def get_country_id(iso_code):
    """Get country UUID from database."""
    try:
        result = supabase.table("countries").select("id").eq("iso_code", iso_code).execute()
        if result.data:
            return result.data[0]["id"]
        return None
    except Exception as e:
        print(f"[X] Failed to get country ID: {e}")
        return None


def should_analyze_country(country_name, country_id):
    """
    Determine if a country needs re-analysis.
    
    Rules:
    1. Never analyzed before → Analyze
    2. Last analysis > 24 hours old → Analyze
    3. Headlines timestamp > last analysis timestamp → Analyze
    4. Otherwise → Skip (use cached)
    """
    
    # Check if country has ever been analyzed and when
    try:
        result = supabase.table("scores").select("scored_at").eq("country_id", country_id).order("scored_at", desc=True).limit(1).execute()
        
        if not result.data:
            # Never analyzed before - always analyze
            print(f"  [NEW] {country_name} has never been analyzed")
            return True
        
        last_scored = result.data[0]["scored_at"]
        last_scored_dt = datetime.fromisoformat(last_scored.replace('Z', '+00:00'))
        hours_since = (datetime.now(timezone.utc) - last_scored_dt).total_seconds() / 3600
        
        # If analysis is > 24 hours old, re-analyze
        if hours_since > 24:
            print(f"  [OLD] {country_name} last analyzed {hours_since:.1f} hours ago")
            return True
            
    except Exception as e:
        print(f"  [!] Could not check analysis history: {e}")
        return True
    
    # Check if headlines are newer than last analysis
    try:
        with open("latest_headlines.json", "r", encoding="utf-8") as f:
            data = json.load(f)
            headlines_timestamp = data.get("timestamp")
            
            if headlines_timestamp:
                headlines_dt = datetime.fromisoformat(headlines_timestamp.replace('Z', '+00:00'))
                
                # If headlines are NEWER than last analysis, re-analyze
                if headlines_dt > last_scored_dt:
                    print(f"  [NEW DATA] {country_name} - headlines from {headlines_dt.strftime('%H:%M')}, analysis from {last_scored_dt.strftime('%H:%M')}")
                    return True
                else:
                    print(f"  [CACHED] {country_name} - analysis is current (last scored {hours_since:.1f}h ago)")
                    return False
                    
    except FileNotFoundError:
        print(f"  [!] No headlines file - will analyze anyway")
        return True
    except Exception as e:
        print(f"  [!] Error checking headlines: {e}")
        return True
    
    # Default to not re-analyzing if we got here
    print(f"  [CACHED] {country_name} - no new data")
    return False


def analyze_country_layers(country_name, country_id):
    """
    Analyze all three layers (base + jewish_israeli + solo_women) for a single country.
    Returns results for all layers.
    """
    results = []
    
    # Base layer
    print(f"\n[GENERAL] BASE LAYER: {country_name}")
    base_analysis = analyze_country(country_name, "base")
    if base_analysis:
        if store_analysis(country_id, "base", base_analysis):
            results.append(("base", base_analysis))
    
    # Jewish/Israeli layer (with base context)
    print(f"\n[JEWISH]  JEWISH/ISRAELI LAYER: {country_name}")
    identity_analysis = analyze_country(country_name, "jewish_israeli", base_analysis)
    if identity_analysis:
        if store_analysis(country_id, "jewish_israeli", identity_analysis):
            results.append(("jewish_israeli", identity_analysis))
    
    # Solo Women layer (with base context)
    print(f"\n[WOMEN]   SOLO WOMEN LAYER: {country_name}")
    women_analysis = analyze_country(country_name, "solo_women", base_analysis)
    if women_analysis:
        if store_analysis(country_id, "solo_women", women_analysis):
            results.append(("solo_women", women_analysis))
    
    return country_name, results


def main():
    """Main analysis routine with parallel processing."""
    
    print("="*44)
    print("   TravelGuard — AI Analysis (Gemini)   ")
    print("="*44)
    print(f"\nStarted: {datetime.now(timezone.utc).isoformat()} UTC\n")
    
    # MVP: 20 countries for global coverage
    countries = [
        ("Israel", "IL"),
        ("Netherlands", "NL"),
        ("USA", "US"),
        ("France", "FR"),
        ("United Kingdom", "GB"),
        ("Turkey", "TR"),
        ("Thailand", "TH"),
        ("Saudi Arabia", "SA"),
        ("Russia", "RU"),
        ("Democratic Republic of the Congo", "CD"),
        ("Nigeria", "NG"),
        ("Ukraine", "UA"),
        ("Brazil", "BR"),
        ("Australia", "AU"),
        ("China", "CN"),
        ("Egypt", "EG"),
        ("India", "IN"),
        ("Mexico", "MX"),
        ("South Africa", "ZA"),
        ("Poland", "PL"),
        ("Iran", "IR"),
        ("Libya", "LY")
    ]
    
    # Filter countries that need analysis (incremental updates)
    countries_to_analyze = []
    for country_name, iso_code in countries:
        country_id = get_country_id(iso_code)
        if not country_id:
            print(f"[X] Country {country_name} not found in database")
            continue
        
        # Check if analysis needed
        if should_analyze_country(country_name, country_id):
            countries_to_analyze.append((country_name, iso_code, country_id))
        else:
            print(f"[-]  Skipping {country_name} (no new data)")
    
    if not countries_to_analyze:
        print("\n[OK] No countries need re-analysis")
        print(f"[OK] Complete: {datetime.now(timezone.utc).isoformat()} UTC\n")
        return
    
    print(f"\n[*] Analyzing {len(countries_to_analyze)} countries in parallel...\n")
    
    # Analyze countries in parallel (max 10 threads)
    max_workers = min(10, len(countries_to_analyze))
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Submit all country analyses
        future_to_country = {
            executor.submit(analyze_country_layers, name, cid): name
            for name, iso, cid in countries_to_analyze
        }
        
        # Collect results as they complete
        for future in as_completed(future_to_country):
            country_name = future_to_country[future]
            try:
                name, results = future.result()
                print(f"\n[OK] Completed {name}: {len(results)} layers analyzed")
            except Exception as e:
                print(f"\n[X] {country_name} failed: {e}")
    
    print(f"\n{'='*60}")
    print(f"[OK] Analysis complete: {datetime.now(timezone.utc).isoformat()} UTC")
    print('='*60)


if __name__ == "__main__":
    main()
