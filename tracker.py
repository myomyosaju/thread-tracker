"""Threads follower tracker.

Visits each account's public Threads profile, extracts the follower count,
appends a timestamped row to followers_data.csv, and prints the
day-over-day delta for each account.
"""

import asyncio
import csv
import json
import re
import sys
from datetime import datetime
from pathlib import Path

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parent
CONFIG_FILE = ROOT / "config.json"
CSV_FILE = ROOT / "followers_data.csv"
CSV_HEADERS = ["timestamp", "username", "followers"]

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)


def load_config() -> dict:
    with CONFIG_FILE.open("r", encoding="utf-8") as f:
        return json.load(f)


def parse_count(text: str) -> int:
    """Convert '12.3K', '1.2M', '546.0만', '1,234,567' into an int."""
    t = text.strip().replace(",", "").replace(" ", "").replace("명", "")
    m = re.match(r"^([\d.]+)(천|만|억)$", t)
    if m:
        mult = {"천": 1_000, "만": 10_000, "억": 100_000_000}[m.group(2)]
        return int(float(m.group(1)) * mult)
    m = re.match(r"^([\d.]+)([KMB])?$", t, re.I)
    if m:
        suffix = (m.group(2) or "").upper()
        mult = {"": 1, "K": 1_000, "M": 1_000_000, "B": 1_000_000_000}[suffix]
        return int(float(m.group(1)) * mult)
    raise ValueError(f"unparseable follower text: {text!r}")


async def fetch_followers(page, username: str, timeout_ms: int) -> int:
    url = f"https://www.threads.net/@{username}"
    await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)

    # The meta description on a public Threads profile carries the count
    # in a stable shape, e.g. "@zuck on Threads. 1.2M Followers. ...".
    # This avoids fragile DOM selectors that change frequently.
    try:
        await page.wait_for_selector('meta[name="description"]', timeout=timeout_ms)
        meta = await page.locator('meta[name="description"]').first.get_attribute("content")
        if meta:
            m = re.search(r"([\d.,]+\s*[KMB]?)\s*Followers", meta, re.I)
            if m:
                return parse_count(m.group(1))
            m = re.search(r"팔로워\s*([\d.,]+(?:천|만|억)?)\s*명?", meta)
            if m:
                return parse_count(m.group(1))
    except Exception:
        pass

    # Fallback: scan the rendered DOM.
    body_text = await page.locator("body").inner_text()
    m = re.search(r"([\d.,]+\s*[KMB]?)\s*followers", body_text, re.I)
    if m:
        return parse_count(m.group(1))
    m = re.search(r"팔로워\s*([\d.,]+(?:천|만|억)?)\s*명?", body_text)
    if m:
        return parse_count(m.group(1))

    raise RuntimeError(f"follower count not found for @{username}")


def load_previous_counts() -> dict[str, tuple[datetime, int]]:
    """Most recent record per user from a date strictly before today."""
    if not CSV_FILE.exists():
        return {}
    today = datetime.now().date()
    by_user: dict[str, tuple[datetime, int]] = {}
    with CSV_FILE.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                ts = datetime.fromisoformat(row["timestamp"])
                count = int(row["followers"])
            except (KeyError, ValueError):
                continue
            if ts.date() >= today:
                continue
            user = row["username"]
            if user not in by_user or ts > by_user[user][0]:
                by_user[user] = (ts, count)
    return by_user


def append_row(timestamp: datetime, username: str, followers: int) -> None:
    new_file = not CSV_FILE.exists()
    with CSV_FILE.open("a", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(CSV_HEADERS)
        w.writerow([timestamp.isoformat(timespec="seconds"), username, followers])


def format_delta(delta: int) -> str:
    if delta > 0:
        return f"+{delta:,}"
    if delta < 0:
        return f"{delta:,}"
    return "±0"


async def main() -> int:
    cfg = load_config()
    accounts = cfg.get("accounts", [])
    headless = cfg.get("headless", True)
    timeout_ms = int(cfg.get("timeout_ms", 60000))
    delay_ms = int(cfg.get("between_request_delay_ms", 1500))

    if not accounts:
        print("config.json의 accounts 리스트가 비어 있습니다.", file=sys.stderr)
        return 1

    previous = load_previous_counts()
    run_at = datetime.now()
    results: list[tuple[str, int | None, str | None]] = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=headless)
        context = await browser.new_context(
            user_agent=USER_AGENT,
            locale="en-US",
            extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
        )
        page = await context.new_page()
        try:
            for i, username in enumerate(accounts):
                try:
                    followers = await fetch_followers(page, username, timeout_ms)
                    append_row(run_at, username, followers)
                    results.append((username, followers, None))
                except Exception as e:
                    results.append((username, None, str(e)))
                if i < len(accounts) - 1 and delay_ms > 0:
                    await asyncio.sleep(delay_ms / 1000)
        finally:
            await context.close()
            await browser.close()

    print(f"\n=== Threads 팔로워 트래커  {run_at:%Y-%m-%d %H:%M:%S} ===")
    failures = 0
    for username, followers, err in results:
        if err is not None:
            failures += 1
            print(f"  @{username:<20} 실패 — {err}")
            continue
        prev = previous.get(username)
        if prev is None:
            print(f"  @{username:<20} {followers:>10,}  (첫 기록)")
        else:
            prev_ts, prev_count = prev
            delta = followers - prev_count
            print(
                f"  @{username:<20} {followers:>10,}  "
                f"({format_delta(delta)} vs {prev_ts:%Y-%m-%d})"
            )
    print()
    return 1 if failures == len(results) else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
