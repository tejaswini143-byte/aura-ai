"""Live remote opportunity discovery through Jobicy's public API.

The feed is cached for one hour, capped at 200 source records per fetch, and
always retains the canonical Jobicy listing URL and source attribution.
"""
from __future__ import annotations

import asyncio
import html
import re
import time
from html.parser import HTMLParser
from typing import Any

import httpx

JOBICY_URL = "https://jobicy.com/api/v2/remote-jobs"
CACHE_SECONDS = 60 * 60
MAX_FEED_SIZE = 200
_cache: list[dict[str, Any]] | None = None
_cache_at = 0.0
_cache_lock = asyncio.Lock()


class _TextOnly(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in {"script", "style"}:
            self.hidden += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style"} and self.hidden:
            self.hidden -= 1

    def handle_data(self, data: str) -> None:
        if not self.hidden:
            cleaned = data.strip()
            if cleaned:
                self.parts.append(cleaned)


def _plain_text(value: Any, limit: int = 1200) -> str:
    parser = _TextOnly()
    try:
        parser.feed(str(value or ""))
        text = " ".join(parser.parts)
    except Exception:
        text = html.unescape(re.sub(r"<[^>]*>", " ", str(value or "")))
    return re.sub(r"\s+", " ", text).strip()[:limit]


def normalize_job(job: dict[str, Any]) -> dict[str, Any] | None:
    title = _plain_text(job.get("jobTitle") or job.get("title"), 200)
    company = _plain_text(job.get("companyName") or job.get("company_name"), 200)
    url = str(job.get("url") or "").strip()
    if not title or not company or not url.startswith("https://jobicy.com/"):
        return None
    geo = _plain_text(job.get("jobGeo") or job.get("candidate_required_location") or "Remote", 160)
    types = job.get("jobType") or job.get("job_type") or []
    if isinstance(types, str):
        types = [types]
    type_text = " ".join(str(item) for item in types).casefold()
    title_lower = title.casefold()
    is_internship = any(word in (type_text + " " + title_lower) for word in ("intern", "trainee", "apprentice"))
    industries = job.get("jobIndustry") or job.get("category") or []
    if isinstance(industries, str):
        industries = [industries]
    tags = job.get("jobTags") or job.get("tags") or []
    if isinstance(tags, str):
        tags = [tags]
    salary = ""
    low, high = job.get("annualSalaryMin") or job.get("salaryMin"), job.get("annualSalaryMax") or job.get("salaryMax")
    currency = str(job.get("salaryCurrency") or "").strip()
    period = str(job.get("salaryPeriod") or "").strip()
    if low is not None or high is not None:
        amount = "–".join(str(v) for v in (low, high) if v is not None)
        salary = " ".join(part for part in (currency, amount, period) if part)
    elif job.get("salary"):
        salary = _plain_text(job.get("salary"), 100)
    return {
        "id": "jobicy-" + str(job.get("id") or url.rsplit("-", 1)[-1]),
        "title": title,
        "organization": company,
        "type": "Internship" if is_internship else "Job",
        "url": url,
        "location": geo,
        "remote_mode": "Remote",
        "description": _plain_text(job.get("jobExcerpt") or job.get("jobDescription") or job.get("description"), 1600),
        "eligibility": _plain_text(job.get("jobLevel") or "", 300),
        "skills": ", ".join(_plain_text(tag, 80) for tag in tags[:12]) if isinstance(tags, list) else "",
        "source": "Jobicy",
        "stipend": salary,
        "status": "Live listing",
        "is_verified": 0,
        "created_at": str(job.get("pubDate") or job.get("publication_date") or ""),
        "published_at": str(job.get("pubDate") or job.get("publication_date") or ""),
        "source_notice": "Listing supplied by Jobicy. Open the source listing for current details and application instructions.",
    }


async def _fetch_feed() -> list[dict[str, Any]]:
    global _cache, _cache_at
    async with _cache_lock:
        if _cache is not None and time.monotonic() - _cache_at < CACHE_SECONDS:
            return _cache
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=False) as client:
            response = await client.get(
                JOBICY_URL,
                params={"count": MAX_FEED_SIZE, "geo": "anywhere"},
                headers={"Accept": "application/json", "User-Agent": "AURA-AI-Opportunity-Radar/1.0"},
            )
            response.raise_for_status()
            payload = response.json()
        if payload.get("success") is False:
            raise ValueError("Jobicy did not return a successful feed response.")
        raw_jobs = payload.get("jobs")
        if not isinstance(raw_jobs, list):
            raise ValueError("Jobicy returned an unexpected feed format.")
        normalized = [item for job in raw_jobs[:MAX_FEED_SIZE] if isinstance(job, dict) if (item := normalize_job(job))]
        _cache = normalized
        _cache_at = time.monotonic()
        return normalized


async def search_live_opportunities(search: str = "", location: str = "anywhere", limit: int = 30) -> dict[str, Any]:
    """Return live remote listings, filtered from a cached public feed."""
    jobs = await _fetch_feed()
    query = re.sub(r"\s+", " ", search.casefold()).strip()
    location_query = location.casefold().strip()
    filtered: list[dict[str, Any]] = []
    for job in jobs:
        text = " ".join((job["title"], job["organization"], job["description"], job["skills"], job["eligibility"])).casefold()
        if query and query not in text:
            continue
        geo = job["location"].casefold()
        if location_query in {"india", "hyderabad", "bengaluru", "bangalore"}:
            if not any(place in geo for place in ("india", "asia", "apac", "worldwide", "anywhere", "global")):
                continue
        elif location_query not in {"", "anywhere", "all"} and location_query not in geo:
            continue
        filtered.append(job)
        if len(filtered) >= max(1, min(int(limit), 50)):
            break
    return {
        "available": True,
        "source": "Jobicy",
        "source_url": "https://jobicy.com/",
        "source_notice": "Listings are provided by Jobicy. AURA links to the original listing and does not claim these postings as its own. Remote roles may have geographic restrictions; verify eligibility on the source page.",
        "count": len(filtered),
        "results": filtered,
        "cache_policy": "Source refreshes at most once per hour per running backend process.",
        "cache_ttl_seconds": CACHE_SECONDS,
        "feed_window": "Recent listings from the source feed; this is not a guarantee that a role is still open.",
    }


def clear_cache_for_tests() -> None:
    global _cache, _cache_at
    _cache = None
    _cache_at = 0.0
