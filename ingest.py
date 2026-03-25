#!/usr/bin/env python3
"""
Travint.ai — Data Ingestion Script

Polls all configured data sources (RSS feeds, APIs, scraped pages) and stores
the raw content in Supabase for later analysis.

Usage:
    python ingest.py
"""

import os
import sys
import json
import yaml
import feedparser
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timezone
from dotenv import load_dotenv
from supabase import create_client, Client

# Load environment variables
load_dotenv()

SUPABASE_URL         = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY")

if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
    print("ERROR: Missing SUPABASE_URL or SUPABASE_SERVICE_KEY in .env file")
    sys.exit(1)

# Pipeline uses service key — it has write access
supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)


def load_sources_config():
    """Load the sources.yaml configuration file."""
    with open("sources.yaml", "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_israeli_nsc_warnings():
    """Load Israeli NSC travel warnings from local config file."""
    try:
        with open("israeli_nsc_warnings.yaml", "r", encoding="utf-8") as f:
            return yaml.safe_load(f)
    except FileNotFoundError:
        print("  [!]  israeli_nsc_warnings.yaml not found - skipping NSC warnings")
        return None


def fetch_full_article_text(url, timeout=8):
    """
    Fetch and extract readable text from a single article URL.
    Called for each RSS entry to get full content, not just the title.
    Returns extracted text (up to 3000 chars) or empty string on failure.
    """
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        response = requests.get(url, headers=headers, timeout=timeout)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, "lxml")

        # Remove noise elements
        for tag in soup(["script", "style", "nav", "footer", "header", "aside",
                          "form", "button", "iframe", "noscript"]):
            tag.decompose()

        # Try article-specific selectors first (most news sites use these)
        article_body = (
            soup.find("article") or
            soup.find(class_=lambda c: c and any(x in c.lower() for x in ["article-body", "story-body", "post-content", "entry-content", "article-content"])) or
            soup.find("main") or
            soup.find("body")
        )

        if article_body:
            paragraphs = article_body.find_all("p")
            text = " ".join(p.get_text(strip=True) for p in paragraphs if len(p.get_text(strip=True)) > 40)
        else:
            text = soup.get_text(separator=" ", strip=True)

        # Limit length — enough for analysis, not so much it bloats context
        return text[:3000].strip()

    except Exception:
        return ""


def fetch_rss(url, fetch_articles=True):
    """
    Fetch and parse an RSS feed.
    If fetch_articles=True, also fetches full text for each article.
    Full text is the critical improvement over titles-only — it gives the AI
    actual content to quote and reason from.
    """
    try:
        feed = feedparser.parse(url)
        if feed.bozo:
            print(f"  [!]  RSS parse warning for {url}: {feed.bozo_exception}")

        entries = []
        for entry in feed.entries[:15]:  # Limit to 15 most recent
            link    = entry.get("link", "")
            title   = entry.get("title", "")
            summary = entry.get("summary", "")

            # Fetch full article text for richer context
            full_text = ""
            if fetch_articles and link:
                full_text = fetch_full_article_text(link)

            # Use full text if fetched; fall back to RSS summary
            content = full_text if full_text else summary

            entries.append({
                "title":     title,
                "link":      link,
                "published": entry.get("published", ""),
                "summary":   summary,
                "full_text": content,  # This is what analysis uses
            })

        return {
            "feed_title": feed.feed.get("title", ""),
            "entries":    entries,
            "fetched_at": datetime.now(timezone.utc).isoformat()
        }
    except Exception as e:
        print(f"  [X] RSS fetch failed for {url}: {str(e)}")
        return None


def fetch_api(url):
    """Fetch data from an API endpoint."""
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        
        # Try to parse as JSON, fall back to text
        try:
            data = response.json()
        except:
            data = response.text
        
        return {
            "data": data,
            "fetched_at": datetime.now(timezone.utc).isoformat()
        }
    except Exception as e:
        print(f"  [X] API fetch failed for {url}: {str(e)}")
        return None


def fetch_scrape(url):
    """Scrape a web page and extract text content."""
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.content, "lxml")
        
        # Remove script and style elements
        for script in soup(["script", "style", "nav", "footer", "header"]):
            script.decompose()
        
        # Get text
        text = soup.get_text(separator="\n", strip=True)
        
        # Clean up multiple newlines
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        text = "\n".join(lines)
        
        return {
            "text": text[:50000],  # Limit to 50k chars to avoid huge payloads
            "url": url,
            "fetched_at": datetime.now(timezone.utc).isoformat()
        }
    except Exception as e:
        print(f"  [X] Scrape failed for {url}: {str(e)}")
        return None


def fetch_source(source):
    """Fetch data from a source based on its type."""
    source_type = source.get("type")
    url = source.get("url")
    
    if source_type == "rss":
        return fetch_rss(url)
    elif source_type == "api":
        return fetch_api(url)
    elif source_type == "scrape":
        return fetch_scrape(url)
    else:
        print(f"  [!]  Unknown source type: {source_type}")
        return None


def get_country_id(iso_code):
    """Get the UUID for a country by ISO code."""
    try:
        result = supabase.table("countries").select("id").eq("iso_code", iso_code).execute()
        if result.data:
            return result.data[0]["id"]
        return None
    except Exception as e:
        print(f"  [X] Failed to get country ID for {iso_code}: {str(e)}")
        return None


def store_source_data(source_name, source_url, country_id, data):
    """Store fetched source data. For MVP, we'll use a simple raw_data table."""
    # Note: For MVP, we're not creating a separate raw_data table in the schema.
    # Instead, we'll just print the data and rely on the analysis step to use it.
    # In production, you'd store this in a raw_data table for audit/replay purposes.
    
    if data:
        print(f"  [OK] Fetched {source_name}")
        # In a real implementation, you'd do:
        # supabase.table("raw_data").insert({...}).execute()
    else:
        print(f"  ✗ Failed to fetch {source_name}")


def ingest_global_sources(config):
    """Ingest all global sources."""
    headlines = []
    
    print("\n━━━ GLOBAL BASE SOURCES ━━━")
    for source in config.get("global_base", []):
        print(f"\n{source['name']}")
        data = fetch_source(source)
        store_source_data(source["name"], source["url"], None, data)
        
        # Extract headlines + article text from RSS data
        if data and "entries" in data:
            for entry in data["entries"][:10]:
                title = entry.get("title", "")
                text  = entry.get("full_text", "") or entry.get("summary", "")
                item  = f"{title}"
                if text:
                    item += f" | {text[:300]}"
                headlines.append(item)
    
    # Global news sources (BBC, Le Monde, Reuters)
    print("\n━━━ GLOBAL NEWS ━━━")
    for source in config.get("global_news", []):
        print(f"\n{source['name']}")
        data = fetch_source(source)
        store_source_data(source["name"], source["url"], None, data)
        
        if data and "entries" in data:
            for entry in data["entries"][:15]:
                title = entry.get("title", "")
                text  = entry.get("full_text", "") or entry.get("summary", "")
                item  = f"{title}"
                if text:
                    item += f" | {text[:400]}"
                headlines.append(item)
    
    print("\n━━━ GLOBAL IDENTITY SOURCES ━━━")
    identity_sources = config.get("global_identity", {})
    for identity_layer, sources in identity_sources.items():
        print(f"\n[{identity_layer}]")
        for source in sources:
            # Skip Israeli NSC if it's a scrape source (we use config file instead)
            if "Israeli NSC" in source['name'] and source['type'] == 'scrape':
                print(f"\n{source['name']}")
                print("  → Using local config file (israeli_nsc_warnings.yaml)")
                nsc_data = load_israeli_nsc_warnings()
                if nsc_data:
                    print(f"  [OK] Loaded {len(nsc_data.get('countries', {}))} countries from NSC warnings")
                continue
            
            print(f"\n{source['name']}")
            data = fetch_source(source)
            store_source_data(source["name"], source["url"], None, data)
            
            # Extract headlines
            if data and "entries" in data:
                for entry in data["entries"][:10]:
                    headlines.append(entry.get("title", ""))
    
    return headlines


def ingest_country_sources(config, country_name, country_code):
    """Ingest sources for a specific country."""
    headlines = []
    
    country_id = get_country_id(country_code)
    if not country_id:
        print(f"[X] Country {country_code} not found in database")
        return headlines
    
    country_config = config.get(country_name.lower(), {})
    
    print(f"\n━━━ {country_name.upper()} — BASE SOURCES ━━━")
    for source in country_config.get("base", []):
        print(f"\n{source['name']}")
        data = fetch_source(source)
        store_source_data(source["name"], source["url"], country_id, data)
        
        # Extract headlines
        if data and "entries" in data:
            for entry in data["entries"][:10]:
                headlines.append(f"[{country_name}] {entry.get('title', '')}")
    
    identity_config = country_config.get("identity", {})
    if identity_config:
        print(f"\n━━━ {country_name.upper()} — IDENTITY SOURCES ━━━")
        for identity_layer, sources in identity_config.items():
            print(f"\n[{identity_layer}]")
            for source in sources:
                print(f"\n{source['name']}")
                data = fetch_source(source)
                store_source_data(source["name"], source["url"], country_id, data)
                
                # Extract headlines
                if data and "entries" in data:
                    for entry in data["entries"][:10]:
                        headlines.append(f"[{country_name}/{identity_layer}] {entry.get('title', '')}")
    
    return headlines


def main():
    """Main ingestion routine."""
    print("============================================")
    print("   Travint.ai — Data Ingestion          =")
    print("============================================")
    print(f"\nStarted: {datetime.now(timezone.utc).isoformat()} UTC")
    
    # Collect headlines for trigger system
    all_headlines = []
    
    # Load configuration
    try:
        config = load_sources_config()
    except Exception as e:
        print(f"[X] Failed to load sources.yaml: {str(e)}")
        sys.exit(1)
    
    # Ingest global sources
    global_headlines = ingest_global_sources(config)
    all_headlines.extend(global_headlines)
    
    # Ingest country-specific sources
    israel_headlines = ingest_country_sources(config, "Israel", "IL")
    all_headlines.extend(israel_headlines)
    
    netherlands_headlines = ingest_country_sources(config, "Netherlands", "NL")
    all_headlines.extend(netherlands_headlines)
    
    # Save headlines for trigger system
    try:
        with open("latest_headlines.json", "w", encoding="utf-8") as f:
            json.dump({
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "headlines": all_headlines[:100]  # Keep most recent 100
            }, f, indent=2)
        print(f"\n[OK] Saved {len(all_headlines)} headlines for trigger system")
    except Exception as e:
        print(f"\n[!]  Failed to save headlines: {e}")
    
    print(f"\n[OK] Ingestion complete: {datetime.now(timezone.utc).isoformat()} UTC")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n")


if __name__ == "__main__":
    main()
