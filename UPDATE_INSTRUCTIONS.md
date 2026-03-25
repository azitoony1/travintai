# Travint.ai - Files to Update on New Computer

## Files to Replace (Download from outputs folder)

### 1. CORE UPDATES (Must Replace)
- **analyze.py** - Improved comparative analysis prompts
- **admin.py** - NEW admin backend with password protection  
- **dashboard.html** - Links tab, muted colors, no X button

### 2. SQL Scripts to Run (Only if not done yet)
Run these in Supabase SQL Editor:

- **add_watch_factors_column.sql**
  ```sql
  ALTER TABLE scores ADD COLUMN IF NOT EXISTS watch_factors TEXT;
  ALTER TABLE scores ADD COLUMN IF NOT EXISTS sources JSONB DEFAULT '[]'::jsonb;
  ```

- **create_notifications_table.sql**
  Creates notifications table for admin backend

- **create_overrides_table.sql** (Optional)
  For manual score corrections

### 3. Install Flask (New Dependency)
```bash
pip install flask --break-system-packages
```

## Files Already Up-to-Date (Don't Touch)
- ingest.py
- sources.yaml
- .env
- add_20_countries.sql

## Quick Test Checklist

### After updating files:

1. **Test Dashboard**
   ```bash
   git add dashboard.html admin.py analyze.py
   git commit -m "Update: admin backend, better analysis, links tab"
   git push
   ```
   Wait 2 min, visit: https://azitoony1.github.io/travintai/dashboard.html
   
   ✓ Map colors more muted?
   ✓ Click country → detail opens?
   ✓ "Useful Links" tab exists?
   ✓ Links clickable?
   ✓ No X button?

2. **Test Admin Backend**
   ```bash
   python admin.py
   ```
   Open: http://localhost:5000
   Login: travelguard2026
   
   ✓ Login page works?
   ✓ Dashboard shows stats?
   ✓ Can click "Smart Update"?

3. **Test Improved Analysis**
   In admin backend:
   - Click "Force Re-analyze All"
   - Wait 20-30 minutes
   - Check dashboard: Saudi Arabia + Jewish layer
   - Should be RED or PURPLE (not YELLOW)

## Known Issues to Watch
- Watch_factors will be empty until you run "Force Re-analyze All"
- Notifications will be empty until threat levels change
- Admin backend only works while `python admin.py` is running

## File Locations
All updated files are in: /mnt/user-data/outputs/
- analyze.py
- admin.py  
- dashboard.html
- *.sql files
