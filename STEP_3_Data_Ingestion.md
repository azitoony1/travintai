# Travint.ai — Step 3: Data Ingestion Script

**What you're doing:** Setting up your GitHub repository and testing the data ingestion script locally.

**Time estimate:** 15 minutes

---

## Files You Just Got

I've created 3 files for you:
1. **`ingest.py`** — The main ingestion script (polls all your sources)
2. **`sources.yaml`** — Your source configuration (easy to edit)
3. **`requirements.txt`** — Python dependencies

Download all three from the outputs folder.

---

## Part A: Create Your GitHub Repository

1. **Go to GitHub** and sign in
2. **Click the "+" in the top right** → "New repository"
3. **Repository settings:**
   - Name: `travintai`
   - Description: "Travel security intelligence platform"
   - **Private** (recommended for now)
   - ✅ Add a README file
   - ✅ Add .gitignore → choose "Python"
4. **Click "Create repository"**

---

## Part B: Set Up Locally

### 1. Clone Your Repository

Open Terminal (Mac) or Command Prompt (Windows) and run:

```bash
git clone https://github.com/YOUR_USERNAME/travintai.git
cd travintai
```

(Replace `YOUR_USERNAME` with your actual GitHub username)

### 2. Add Your Files

Copy the 3 files I gave you into this folder:
- `ingest.py`
- `sources.yaml`  
- `requirements.txt`

Also copy your `.env` file into this folder.

Your folder should now look like:
```
travintai/
├── .env
├── .gitignore
├── README.md
├── ingest.py
├── sources.yaml
└── requirements.txt
```

### 3. Make Sure .env is in .gitignore

Open `.gitignore` and make sure it contains this line:
```
.env
```

If it doesn't, add it. This prevents your API keys from being uploaded to GitHub.

---

## Part C: Test the Script Locally

### 1. Install Python Dependencies

In your terminal (still in the `travintai` folder):

```bash
pip install -r requirements.txt
```

This installs all the libraries the script needs.

### 2. Run the Ingestion Script

```bash
python ingest.py
```

**What you should see:**

```
╔════════════════════════════════════════╗
║   Travint.ai — Data Ingestion          ║
╚════════════════════════════════════════╝

Started: 2026-02-02T19:30:00.000000 UTC

━━━ GLOBAL BASE SOURCES ━━━

US State Department Travel Advisories
  ✓ Fetched US State Department Travel Advisories

UK FCDO Travel Advice
  ✓ Fetched UK FCDO Travel Advice

[... more sources ...]

━━━ ISRAEL — BASE SOURCES ━━━

Israel Red Alert Feed
  ✓ Fetched Israel Red Alert Feed

[... etc ...]

✓ Ingestion complete: 2026-02-02T19:31:15.000000 UTC
```

**If you see errors:**
- `❌ Missing SUPABASE_URL` → Your `.env` file isn't in the right place or has wrong keys
- `❌ RSS fetch failed` → That source might be temporarily down (not critical for MVP)
- `❌ Failed to get country ID` → Database wasn't set up correctly in Step 2

Most sources should show `✓ Fetched`. A few might fail (websites block scrapers sometimes) — that's okay for testing.

---

## Part D: Push to GitHub

```bash
git add ingest.py sources.yaml requirements.txt
git commit -m "Add data ingestion script"
git push origin main
```

**Do NOT add the .env file.** Your .gitignore should prevent this, but double-check:

```bash
git status
```

If you see `.env` in the list, something's wrong. Run:
```bash
git reset .env
```

---

## ✅ Checklist

- [ ] GitHub repository created
- [ ] Repository cloned to your computer
- [ ] All 3 files + .env in the folder
- [ ] `.env` is in `.gitignore`
- [ ] Dependencies installed (`pip install -r requirements.txt`)
- [ ] Script runs successfully (`python ingest.py`)
- [ ] Files pushed to GitHub (but NOT .env)

---

## What's Next?

**Step 4:** I'll write the AI analysis pipeline (Gemini) that takes the raw data from Step 3 and generates threat scores + summaries for both base and Jewish/Israeli layers.

Let me know when Step 3 is complete!
