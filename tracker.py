"""Threads follower tracker.

Visits each account's public Threads profile, extracts the follower count,
and appends a timestamped row to followers_data.csv. Designed to be run
multiple times per day (every 3h) so intra-day movement can be plotted.
"""

from __future__ import annotations

import asyncio
import csv
import json
import random
import re
import sys
from datetime import datetime
from pathlib import Path

from playwright.async_api import async_playwright, BrowserContext, Page

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


async def fetch_followers(page: Page, username: str, timeout_ms: int) -> int:
    url = f"https://www.threads.net/@{username}"
    await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)

    # 메타 description은 SSR이라 즉시 채워짐. K/M 단위로만 노출되지만 비로그인 환경에서
    # 가장 안정적인 신호.
    meta_locator = page.locator('meta[name="description"]')
    try:
        await meta_locator.first.wait_for(state="attached", timeout=5000)
        meta = await meta_locator.first.get_attribute("content")
        if meta:
            m = re.search(r"([\d.,]+\s*[KMB]?)\s*Followers", meta, re.I)
            if m:
                return parse_count(m.group(1))
            m = re.search(r"팔로워\s*([\d.,]+(?:천|만|억)?)\s*명?", meta)
            if m:
                return parse_count(m.group(1))
    except Exception:
        pass

    # Fallback: 렌더링된 DOM (메타가 비어있는 드문 경우).
    body_text = await page.locator("body").inner_text()
    m = re.search(r"([\d.,]+\s*[KMB]?)\s*followers", body_text, re.I)
    if m:
        return parse_count(m.group(1))
    m = re.search(r"팔로워\s*([\d.,]+(?:천|만|억)?)\s*명?", body_text)
    if m:
        return parse_count(m.group(1))

    raise RuntimeError(f"follower count not found for @{username}")


def load_previous_counts() -> dict[str, tuple[datetime, int]]:
    """Most recent record per user from a date strictly before today (local)."""
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


async def _fetch_one(
    context: BrowserContext,
    sem: asyncio.Semaphore,
    username: str,
    timeout_ms: int,
    delay_min_ms: int,
    delay_max_ms: int,
) -> tuple[str, int | None, str | None]:
    """세마포어로 동시성 제한, 계정당 새 page를 열고 닫음. 작업 후 랜덤 딜레이."""
    async with sem:
        page = await context.new_page()
        try:
            count = await fetch_followers(page, username, timeout_ms)
            return username, count, None
        except Exception as e:
            return username, None, str(e)
        finally:
            try:
                await page.close()
            except Exception:
                pass
            lo = max(0, delay_min_ms)
            hi = max(lo, delay_max_ms)
            if hi > 0:
                await asyncio.sleep(random.uniform(lo, hi) / 1000)


async def main() -> int:
    cfg = load_config()
    accounts = cfg.get("accounts", [])
    headless = cfg.get("headless", True)
    timeout_ms = int(cfg.get("timeout_ms", 30000))
    delay_min_ms = int(cfg.get("delay_min_ms", cfg.get("between_request_delay_ms", 500)))
    delay_max_ms = int(cfg.get("delay_max_ms", max(delay_min_ms, 1500)))
    concurrency = max(1, int(cfg.get("concurrency", 4)))

    if not accounts:
        print("config.json의 accounts 리스트가 비어 있습니다.", file=sys.stderr)
        return 1

    previous = load_previous_counts()
    run_at = datetime.now()
    started = asyncio.get_event_loop().time()

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=headless)
        context = await browser.new_context(
            user_agent=USER_AGENT,
            locale="en-US",
            extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
        )
        sem = asyncio.Semaphore(concurrency)
        try:
            results = await asyncio.gather(
                *(
                    _fetch_one(context, sem, u, timeout_ms, delay_min_ms, delay_max_ms)
                    for u in accounts
                )
            )
        finally:
            await context.close()
            await browser.close()

    for username, followers, err in results:
        if err is None and followers is not None:
            append_row(run_at, username, followers)

    elapsed = asyncio.get_event_loop().time() - started

    print(
        f"\n=== Threads 팔로워 트래커  {run_at:%Y-%m-%d %H:%M:%S}  "
        f"(소요 {elapsed:.1f}s, concurrency={concurrency}) ==="
    )
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
