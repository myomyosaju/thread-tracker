"""Threads follower tracker (logged-in mode).

Logs into Instagram with a side account, reuses the session via
storage_state.json, then visits each tracked profile on Threads to extract
the exact follower count (e.g. "8,404") instead of the K/M-rounded
anonymous view. Falls back gracefully to anonymous K/M parsing if login
fails.
"""

from __future__ import annotations

import asyncio
import csv
import json
import os
import random
import re
import sys
from datetime import datetime
from pathlib import Path

from playwright.async_api import async_playwright, BrowserContext, Page

ROOT = Path(__file__).resolve().parent
CONFIG_FILE = ROOT / "config.json"
ENV_FILE = ROOT / ".env"
CSV_FILE = ROOT / "followers_data.csv"
STORAGE_STATE_FILE = ROOT / "storage_state.json"
CSV_HEADERS = ["timestamp", "username", "followers"]

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)

# navigator.webdriver 같은 1차 자동화 마커 제거. 완전한 stealth는 아니지만
# Instagram/Threads의 기본 휴리스틱은 우회한다.
STEALTH_INIT_SCRIPT = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
window.chrome = window.chrome || { runtime: {} };
Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
"""


def load_dotenv_if_present() -> None:
    """python-dotenv가 있으면 .env를 자동 로드.  CI에선 secrets로 주입되니 무시."""
    try:
        from dotenv import load_dotenv  # type: ignore
    except ImportError:
        return
    if ENV_FILE.exists():
        load_dotenv(ENV_FILE, override=False)


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
    """프로필 페이지에서 팔로워 수를 추출. 로그인 시 정확한 정수, 비로그인 시 K/M."""
    url = f"https://www.threads.net/@{username}"
    await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)

    # 'followers' 텍스트가 렌더될 때까지 기다림 (로그인 시 정확한 값 필요).
    try:
        await page.locator(":text-matches('followers', 'i'), :text-matches('팔로워', '')").first.wait_for(
            state="attached", timeout=8000
        )
    except Exception:
        pass

    body_text = await page.locator("body").inner_text()

    # 1순위: 콤마 포함 정확한 숫자 ("8,404 followers" / "1,234,567 followers")
    m = re.search(r"(\d{1,3}(?:,\d{3})+)\s*followers", body_text, re.I)
    if m:
        return parse_count(m.group(1))
    m = re.search(r"팔로워\s*(\d{1,3}(?:,\d{3})+)\s*명?", body_text)
    if m:
        return parse_count(m.group(1))

    # 2순위: 순수 정수 ("412 followers") — 콤마 없는 작은 계정
    m = re.search(r"(?<![\d.,KMB])(\d+)\s*followers\b", body_text, re.I)
    if m:
        return parse_count(m.group(1))

    # 3순위: K/M 포함 ("8.4K followers") — 비로그인 fallback
    m = re.search(r"([\d.]+\s*[KMB])\s*followers", body_text, re.I)
    if m:
        return parse_count(m.group(1))

    # 4순위: 한국어 단위 ("5.4만")
    m = re.search(r"팔로워\s*([\d.]+\s*(?:천|만|억))\s*명?", body_text)
    if m:
        return parse_count(m.group(1))

    # 5순위: 메타 description (마지막 수단, 항상 K/M)
    try:
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

    raise RuntimeError(f"follower count not found for @{username}")


async def is_logged_in(context: BrowserContext) -> bool:
    """Threads 홈을 방문해 로그인 상태인지 빠르게 검사."""
    page = await context.new_page()
    try:
        await page.goto("https://www.threads.net/", wait_until="domcontentloaded", timeout=15000)
        if "/login" in page.url:
            return False
        # 로그아웃 상태면 'Log in' 링크가 명시적으로 보임.
        try:
            await page.get_by_role("link", name=re.compile(r"^Log in$", re.I)).first.wait_for(
                state="visible", timeout=2000
            )
            return False
        except Exception:
            return True
    except Exception:
        return False
    finally:
        try:
            await page.close()
        except Exception:
            pass


async def login_to_threads(context: BrowserContext, username: str, password: str) -> bool:
    """Instagram 인증 후 Threads에 공유 쿠키로 로그인. 성공 시 storage_state 저장.

    실패 케이스(2FA / 캡차 / 체크포인트 / 잘못된 비밀번호)는 명확히 stderr에 기록.
    """
    page = await context.new_page()
    try:
        await page.goto(
            "https://www.instagram.com/accounts/login/",
            wait_until="domcontentloaded",
            timeout=30000,
        )

        try:
            await page.locator('input[name="username"]').first.wait_for(
                state="visible", timeout=10000
            )
        except Exception:
            print("[login] 로그인 폼이 로드되지 않음 (네트워크 또는 IG 차단)", file=sys.stderr)
            return False

        await page.locator('input[name="username"]').first.fill(username)
        await asyncio.sleep(random.uniform(0.4, 1.0))
        await page.locator('input[name="password"]').first.fill(password)
        await asyncio.sleep(random.uniform(0.4, 1.0))
        await page.locator('button[type="submit"]').first.click()

        # 로그인 후 URL 전이를 기다림. 30초 내 응답이 없으면 캡차/네트워크 의심.
        try:
            await page.wait_for_url(
                re.compile(r"instagram\.com/(?!accounts/login)"),
                timeout=30000,
            )
        except Exception:
            # URL이 안 바뀌었으면 비밀번호 오류일 가능성 — 화면의 에러 텍스트로 판정.
            try:
                err_text = await page.locator("body").inner_text()
                if re.search(r"(incorrect|wrong|잘못)", err_text, re.I):
                    print("[login] 비밀번호 오류 — 자격증명을 확인하세요", file=sys.stderr)
                else:
                    print("[login] 로그인 응답 시간 초과 (캡차 의심)", file=sys.stderr)
            except Exception:
                print("[login] 로그인 응답 시간 초과", file=sys.stderr)
            return False

        cur = page.url
        if "challenge" in cur:
            print(
                "[login] 의심 로그인 challenge — 부계정에 모바일 알림으로 인증 후 재시도",
                file=sys.stderr,
            )
            return False
        if "two_factor" in cur or "/2fa/" in cur:
            print("[login] 2FA 요구 — 자동 로그인 불가. 부계정의 2FA를 끄세요.", file=sys.stderr)
            return False
        if "checkpoint" in cur:
            print("[login] 계정 체크포인트(차단/제한). IG에 직접 로그인해 해제하세요.", file=sys.stderr)
            return False

        # "Save your login info?", "Turn on notifications" 모달은 무시하고 Threads로 점프.
        await page.goto("https://www.threads.net/", wait_until="domcontentloaded", timeout=20000)

        # 페이지 종료 전에 storage_state를 비워둔 새 page에서 검증.
        await page.close()
        if await is_logged_in(context):
            await context.storage_state(path=str(STORAGE_STATE_FILE))
            print(f"[login] 성공 — @{username}, storage_state 저장")
            return True
        print("[login] Threads 세션 확인 실패", file=sys.stderr)
        return False
    except Exception as e:
        print(f"[login] 예외: {e}", file=sys.stderr)
        try:
            await page.close()
        except Exception:
            pass
        return False


def load_previous_counts() -> dict[str, tuple[datetime, int]]:
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
    load_dotenv_if_present()

    cfg = load_config()
    accounts = cfg.get("accounts", [])
    headless = cfg.get("headless", True)
    timeout_ms = int(cfg.get("timeout_ms", 30000))
    delay_min_ms = int(cfg.get("delay_min_ms", 1000))
    delay_max_ms = int(cfg.get("delay_max_ms", max(delay_min_ms, 3000)))
    concurrency = max(1, int(cfg.get("concurrency", 4)))

    if not accounts:
        print("config.json의 accounts 리스트가 비어 있습니다.", file=sys.stderr)
        return 1

    threads_user = os.environ.get("THREADS_USERNAME", "").strip()
    threads_pass = os.environ.get("THREADS_PASSWORD", "").strip()

    previous = load_previous_counts()
    run_at = datetime.now()
    started = asyncio.get_event_loop().time()

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-features=IsolateOrigins,site-per-process",
            ],
        )

        ctx_kwargs = dict(
            user_agent=USER_AGENT,
            locale="en-US",
            timezone_id="Asia/Seoul",
            viewport={"width": 1366, "height": 900},
            extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
        )
        if STORAGE_STATE_FILE.exists():
            ctx_kwargs["storage_state"] = str(STORAGE_STATE_FILE)
            print("[session] storage_state.json 로드")

        context = await browser.new_context(**ctx_kwargs)
        await context.add_init_script(STEALTH_INIT_SCRIPT)

        # 1) 캐시된 세션이 살아있는지 검증.
        logged_in = await is_logged_in(context)

        # 2) 만료/없음이면 자격증명으로 재로그인 시도.
        if not logged_in:
            if threads_user and threads_pass:
                print(f"[session] 만료/미설정 → @{threads_user}로 로그인 시도")
                logged_in = await login_to_threads(context, threads_user, threads_pass)
            else:
                print(
                    "[session] THREADS_USERNAME/PASSWORD 환경변수가 없습니다 — 비로그인 모드 진행",
                    file=sys.stderr,
                )

        if logged_in:
            print("[mode] 로그인 — 정확한 팔로워 수집")
        else:
            print("[mode] 비로그인 — K/M 단위로만 수집됨")

        sem = asyncio.Semaphore(concurrency)
        try:
            results = await asyncio.gather(
                *(
                    _fetch_one(context, sem, u, timeout_ms, delay_min_ms, delay_max_ms)
                    for u in accounts
                )
            )
        finally:
            # 쿠키 회전 대응 — 매 실행 종료 전 storage_state를 갱신 저장.
            if logged_in:
                try:
                    await context.storage_state(path=str(STORAGE_STATE_FILE))
                except Exception:
                    pass
            await context.close()
            await browser.close()

    for username, followers, err in results:
        if err is None and followers is not None:
            append_row(run_at, username, followers)

    elapsed = asyncio.get_event_loop().time() - started
    mode = "logged-in" if logged_in else "anonymous"

    print(
        f"\n=== Threads 팔로워 트래커  {run_at:%Y-%m-%d %H:%M:%S}  "
        f"(소요 {elapsed:.1f}s, concurrency={concurrency}, mode={mode}) ==="
    )
    failures = 0
    for username, followers, err in results:
        if err is not None:
            failures += 1
            print(f"  @{username:<22} 실패 — {err}")
            continue
        prev = previous.get(username)
        if prev is None:
            print(f"  @{username:<22} {followers:>10,}  (첫 기록)")
        else:
            prev_ts, prev_count = prev
            delta = followers - prev_count
            print(
                f"  @{username:<22} {followers:>10,}  "
                f"({format_delta(delta)} vs {prev_ts:%Y-%m-%d})"
            )
    print()
    return 1 if failures == len(results) else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
