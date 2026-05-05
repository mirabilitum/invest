import logging
import feedparser
from src.config import get

log = logging.getLogger("invest.data.news")


def fetch_rss_headlines(feed_url: str, limit: int = 10) -> list[dict]:
    """Fetch headlines from a single RSS feed."""
    items = []
    try:
        feed = feedparser.parse(feed_url)
        for entry in feed.entries[:limit]:
            items.append({
                "title": entry.get("title", ""),
                "link": entry.get("link", ""),
                "published": entry.get("published", ""),
                "summary": entry.get("summary", ""),
            })
    except Exception as e:
        log.warning("RSS fetch failed for %s: %s", feed_url, e)
    return items


def fetch_all_headlines() -> list[dict]:
    """Fetch headlines from all configured RSS feeds."""
    feeds = get("news.rss_feeds", [])
    all_items = []
    for url in feeds:
        all_items.extend(fetch_rss_headlines(url))
    log.debug("Fetched %d headlines from %d feeds", len(all_items), len(feeds))
    return all_items


def scan_keywords(headlines: list[dict]) -> list[str]:
    """Scan headlines for alert keywords. Returns list of matched keywords."""
    keywords = get("news.keywords_alert", [])
    matched = []
    for h in headlines:
        text = h.get("title", "") + h.get("summary", "")
        for kw in keywords:
            if kw in text and kw not in matched:
                matched.append(kw)
    return matched


def has_breaking_news(headlines: list[dict]) -> bool:
    """Return True if any headline matches alert keywords -- triggers event channel."""
    return len(scan_keywords(headlines)) > 0
