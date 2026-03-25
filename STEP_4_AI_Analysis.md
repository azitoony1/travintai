# Travint.ai — Step 4: AI Analysis Pipeline

**What you're doing:** Running Gemini to analyze all the data and generate threat scores for both countries (Israel & Netherlands) across both identity layers (base + Jewish/Israeli).

**Time estimate:** 5 minutes

---

## What This Script Does

The `analyze.py` script:
1. Loads the Israeli NSC warnings from your config file
2. For each country (Israel, Netherlands):
   - Runs base layer analysis (general travelers)
   - Runs Jewish/Israeli layer analysis (identity-specific threats)
3. Gemini analyzes 7 threat categories per layer:
   - Armed Conflict
   - Regional Instability
   - Terrorism
   - Civil Strife
   - Crime
   - Health
   - Infrastructure
4. Assigns scores: GREEN / YELLOW / ORANGE / RED / PURPLE
5. Applies veto logic (if Armed Conflict = RED, total = RED)
6. Generates AI summary + 6 recommendations per layer
7. Stores everything in Supabase

---

## Installation

First, install the Gemini library:

```
pip install google-generativeai
```

Then copy the `analyze.py` file to your `travintai` folder.

---

## Run the Analysis

```
python analyze.py
```

**What you'll see:**

```
╔════════════════════════════════════════╗
║   Travint.ai — AI Analysis (Gemini)    ║
╚════════════════════════════════════════╝

============================================================
Analyzing: Israel (base layer)
============================================================
Israeli NSC Warning Level: N/A (base layer doesn't use NSC)
Sending request to Gemini...
✓ Analysis complete
  Armed Conflict: RED
  Terrorism: ORANGE
  Crime: YELLOW
✓ Stored in database: RED

============================================================
Analyzing: Israel (jewish_israeli layer)
============================================================
Israeli NSC Warning Level: N/A (Israel not in NSC warnings - it's for travel abroad)
Sending request to Gemini...
✓ Analysis complete
  Armed Conflict: RED
  Terrorism: ORANGE
  Crime: YELLOW
✓ Stored in database: RED

[... Netherlands base layer ...]
[... Netherlands jewish_israeli layer ...]

✓ Analysis complete: 2026-02-02T22:00:00 UTC
```

---

## Understanding the Output

**For Israel (February 2026):**
- **Base layer:** Likely RED or ORANGE due to ongoing conflict
- **Jewish/Israeli layer:** Similar or slightly different based on specific threats to Jews/Israelis

**For Netherlands:**
- **Base layer:** Likely GREEN or YELLOW (stable country)
- **Jewish/Israeli layer:** Likely YELLOW or ORANGE (antisemitism incidents, Amsterdam attack)

**This difference is the product's value proposition:** Same country, different threat profiles for different identities.

---

## Viewing the Results

The scores are now in your Supabase database in the `scores` table.

**To view them:**

1. Go to your Supabase dashboard
2. Click **Table Editor** → **scores**
3. You should see 4 rows:
   - Israel / base
   - Israel / jewish_israeli
   - Netherlands / base
   - Netherlands / jewish_israeli

Each row contains:
- `total_score` (the final color: GREEN/YELLOW/ORANGE/RED/PURPLE)
- Individual category scores
- `ai_summary` (2-3 paragraph explanation)
- `recommendations` (JSON with 6 categories)
- `veto_explanation` (why the score is what it is)

---

## If It Fails

**"Missing GEMINI_API_KEY":**
- Check your `.env` file has the key

**"JSON parsing failed":**
- Gemini sometimes returns text outside the JSON
- Run it again — usually works on second try

**"Rate limit exceeded":**
- Free tier has limits (15 requests/minute)
- Wait 1 minute and try again

**Gemini returns wrong format:**
- The prompt is tuned for Gemini Pro
- If you're using a different model, you may need to adjust the prompt

---

## How Often Should You Run This?

**For MVP:** 
- Run manually 2x per day (morning + evening)
- Takes 2-3 minutes total

**For production:**
- Set up GitHub Actions to run every 12 hours (we'll do this in Step 8)

---

## What's Next?

**Step 5:** The Scoring Engine — applies veto logic, handles overrides, detects drift.

But first, verify Step 4 worked:
1. Run `python analyze.py`
2. Check Supabase `scores` table has 4 rows
3. Look at the `total_score` and `ai_summary` fields

Let me know when you see the scores in your database!
