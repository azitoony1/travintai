#!/usr/bin/env python3
"""
Travint.ai — Smart Analysis Trigger

Workflow:
1. Read headlines from latest ingestion (saved by ingest.py)
2. Use Gemini Flash to triage: THREAT or SAFE?
3. If THREAT detected → run analyze.py
4. If SAFE → skip (save money)

Usage:
    python trigger.py
"""

import os
import sys
import json
import subprocess
from datetime import datetime, timezone
from dotenv import load_dotenv
from google import genai

# Load environment variables
load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not GEMINI_API_KEY:
    print("ERROR: Missing GEMINI_API_KEY")
    sys.exit(1)

client = genai.Client(api_key=GEMINI_API_KEY)


def load_headlines():
    """Load headlines from the latest ingestion run."""
    try:
        with open("latest_headlines.json", "r", encoding="utf-8") as f:
            content = f.read().strip()
            if not content:
                print("[!]  Headlines file is empty")
                return []
            
            data = json.loads(content)
            headlines = data.get("headlines", [])
            
            if not headlines:
                print("[!]  No headlines in file")
                return []
            
            return headlines
            
    except FileNotFoundError:
        print("[X] No headlines file found")
        print("   Run ingest.py first to generate latest_headlines.json")
        return []
    except json.JSONDecodeError as e:
        print(f"[X] Headlines file is malformed: {e}")
        print("   Run ingest.py again to regenerate")
        return []


def get_new_headlines(current_headlines):
    """
    Compare current headlines to previous run and return only NEW headlines.
    """
    try:
        with open("previous_headlines.json", "r", encoding="utf-8") as f:
            previous_data = json.load(f)
            previous_headlines = set(previous_data.get("headlines", []))
    except (FileNotFoundError, json.JSONDecodeError):
        # No previous run - all headlines are new
        print("   (First run - analyzing all headlines)")
        return current_headlines
    
    # Find new headlines
    current_set = set(current_headlines)
    new_headlines = [h for h in current_headlines if h not in previous_headlines]
    
    if not new_headlines:
        print("   No new headlines since last run")
        return []
    
    print(f"   Found {len(new_headlines)} NEW headlines (out of {len(current_headlines)} total)")
    
    return new_headlines


def save_current_headlines_as_previous(headlines):
    """Save current headlines for comparison in next run."""
    try:
        with open("previous_headlines.json", "w", encoding="utf-8") as f:
            json.dump({
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "headlines": headlines
            }, f, indent=2)
    except Exception as e:
        print(f"[!]  Could not save headlines for next comparison: {e}")


def triage_headlines(headlines):
    """
    Use Gemini Flash to determine if headlines contain security threats.
    Returns: (should_trigger: bool, reason: str)
    """
    
    if not headlines:
        return False, "No headlines to analyze"
    
    # Build prompt with headlines
    headlines_text = "\n".join([f"- {h}" for h in headlines[:40]])
    
    prompt = f"""You are a travel security analyst. Review these news headlines and determine if ANY indicate security threats that would require updating travel advisories.

Security threats that REQUIRE analysis:
- Armed attacks, terrorism, bombings, shootings
- War, military operations, airstrikes, invasions  
- Rocket/missile alerts, air raid sirens
- Major civil unrest, riots, coups, political violence
- Government travel warnings or advisories
- Hostage situations, kidnappings
- Antisemitic attacks, hate crimes against specific groups
- Embassy evacuations, border closures

NOT security threats (SKIP these):
- Routine accidents, traffic, weather
- Economic news, business, stock markets
- Sports, entertainment, culture
- Political speeches without violence
- Minor crimes

Headlines:
{headlines_text}

Respond with ONE of these:
THREAT - Headlines indicate security threats requiring immediate analysis update
SAFE - No security threats detected, skip analysis

Then on next line, briefly explain (1-2 sentences).
"""

    try:
        print("[>] Triaging headlines with Gemini Flash...")
        
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt
        )
        
        result = response.text.strip()
        print(f"   Result: {result[:80]}...")
        
        should_trigger = result.upper().startswith("THREAT")
        reason = result.split("\n", 1)[1] if "\n" in result else result
        
        return should_trigger, reason
        
    except Exception as e:
        print(f"[X] Triage failed: {e}")
        # On error, don't trigger (conservative)
        return False, f"Error: {e}"


def run_full_analysis():
    """Execute tier2_daily.py to run Tier 2 change detection."""

    print("\n" + "="*60)
    print("[!!] TRIGGERING TIER 2 CHANGE DETECTION")
    print("="*60 + "\n")

    try:
        result = subprocess.run(
            ["python", "tier2_daily.py"],
            capture_output=True,
            text=True,
            timeout=300  # 5 min timeout
        )
        
        print(result.stdout)
        
        if result.returncode == 0:
            print("\n[OK] Full analysis completed")
            return True
        else:
            print(f"\n[X] Analysis failed: {result.stderr}")
            return False
            
    except subprocess.TimeoutExpired:
        print("[X] Analysis timed out")
        return False
    except Exception as e:
        print(f"[X] Failed to run analysis: {e}")
        return False


def main():
    """Main trigger logic."""
    
    print("============================================")
    print("   Travint.ai — Smart Trigger           =")
    print("============================================")
    print(f"\nStarted: {datetime.now(timezone.utc).isoformat()} UTC\n")
    
    # Load headlines from ingestion
    all_headlines = load_headlines()
    
    if not all_headlines:
        print("[!]  No headlines available")
        print("   Run: python ingest.py")
        print("   Then run: python trigger.py")
        sys.exit(0)
    
    print(f"[*] Loaded {len(all_headlines)} headlines from latest ingestion")
    
    # Get only NEW headlines since last run
    new_headlines = get_new_headlines(all_headlines)
    
    if not new_headlines:
        print("\n[OK] No new headlines to analyze")
        print("   Waiting for next ingestion cycle")
        sys.exit(0)
    
    # Triage with Gemini Flash (only new headlines)
    should_trigger, reason = triage_headlines(new_headlines)
    
    print("\n" + "="*60)
    
    if should_trigger:
        print("[OK] TRIGGER DECISION: Security threat detected")
        print(f"   Reason: {reason}")
        print("="*60)
        
        # Run full analysis
        success = run_full_analysis()
        
        # Save headlines for next comparison
        save_current_headlines_as_previous(all_headlines)
        
        if success:
            print("\n[OK] Trigger system completed successfully")
        else:
            print("\n[X] Trigger system completed with errors")
    else:
        print("[-]  SKIP DECISION: No security threats")
        print(f"   Reason: {reason}")
        print("="*60)
        print("\n   Waiting for next cycle or manual analysis run")
        
        # Still save headlines for next comparison
        save_current_headlines_as_previous(all_headlines)
    
    print(f"\n[OK] Complete: {datetime.now(timezone.utc).isoformat()} UTC\n")


if __name__ == "__main__":
    main()
