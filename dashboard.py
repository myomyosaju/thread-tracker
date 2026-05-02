"""Streamlit dashboard — 노영우 컨설턴트 vs 경쟁자 비교 분석.

내 계정(`my_account` in config.json)을 기준으로 두고, 나머지 추적 계정을
경쟁군으로 보아 시장 추세 안에서 내 위치를 진단한다.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent
CSV_FILE = ROOT / "followers_data.csv"
CONFIG_FILE = ROOT / "config.json"

st.set_page_config(
    page_title="컨설턴트 팔로워 비교 대시보드",
    page_icon="📊",
    layout="wide",
)


@st.cache_data(ttl=300)
def load_config(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


@st.cache_data(ttl=300)
def load_data(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=["timestamp", "username", "followers"])
    df = pd.read_csv(path)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df["followers"] = pd.to_numeric(df["followers"], errors="coerce")
    df = df.dropna(subset=["followers"])
    df["followers"] = df["followers"].astype(int)
    df["date"] = df["timestamp"].dt.date
    return df.sort_values("timestamp")


def daily_latest(df: pd.DataFrame) -> pd.DataFrame:
    """Keep only the latest record per (username, date)."""
    return (
        df.sort_values("timestamp")
        .groupby(["username", "date"], as_index=False)
        .tail(1)
        .reset_index(drop=True)
    )


def latest_two_days(df: pd.DataFrame) -> pd.DataFrame:
    """For each username, return latest follower count and the previous day's."""
    daily = daily_latest(df).sort_values(["username", "date"])
    daily["prev_followers"] = daily.groupby("username")["followers"].shift(1)
    daily["prev_date"] = daily.groupby("username")["date"].shift(1)
    latest = daily.groupby("username", as_index=False).tail(1).copy()
    latest["delta"] = latest["followers"] - latest["prev_followers"]
    latest["delta_pct"] = (
        (latest["followers"] - latest["prev_followers"])
        / latest["prev_followers"]
        * 100
    )
    return latest


def follower_rank_on(daily: pd.DataFrame, on_date, username: str) -> int | None:
    """1-based rank by followers (desc) on a given date. None if user absent."""
    snap = daily[daily["date"] == on_date].copy()
    if snap.empty or username not in snap["username"].values:
        return None
    snap = snap.sort_values("followers", ascending=False).reset_index(drop=True)
    return int(snap.index[snap["username"] == username][0]) + 1


def classify_trend(my_delta: float | None, comp_delta_avg: float | None) -> tuple[str, str]:
    """Return (badge_emoji_label, explanation) based on me vs competitors."""
    if my_delta is None or comp_delta_avg is None or pd.isna(my_delta) or pd.isna(comp_delta_avg):
        return ("⏳ 데이터 부족", "최소 2일치 기록이 모이면 인사이트가 표시됩니다.")

    me_down = my_delta < 0
    comp_down = comp_delta_avg < 0
    me_up = my_delta > 0
    comp_up = comp_delta_avg > 0

    if me_down and comp_down:
        return (
            "🌧️ 다같이 감소 중 → 계절성 이슈",
            "시장 전체가 빠지고 있습니다. 개별 콘텐츠 문제라기보다 플랫폼/계절 영향일 가능성이 큽니다.",
        )
    if me_down and not comp_down:
        return (
            "🚨 나만 감소 → 원인 분석 필요",
            "경쟁자들은 유지/증가 중인데 내 계정만 빠지고 있습니다. 최근 콘텐츠·포지셔닝을 점검하세요.",
        )
    if me_up and comp_up and my_delta < comp_delta_avg:
        return (
            "📈 같이 성장하지만 뒤쳐짐 → 가속 필요",
            "시장은 좋은데 내 성장 속도가 평균 이하입니다. 성과 좋은 경쟁자 콘텐츠를 벤치마킹하세요.",
        )
    if me_up and comp_up:
        return (
            "🚀 동반 성장 — 페이스 유지",
            "시장과 함께 성장 중이며 페이스도 양호합니다. 현재 전략을 유지하세요.",
        )
    if me_up and comp_down:
        return (
            "🏆 나만 성장 → 강점 강화",
            "시장 역성장 속에서 내 계정만 늘고 있습니다. 무엇이 먹히고 있는지 분석해 더 밀어붙이세요.",
        )
    if not me_up and comp_up:
        return (
            "🔍 경쟁자만 증가 → 벤치마킹 필요",
            "내 계정은 정체인데 경쟁자들이 치고 나가고 있습니다. 최근 그들의 콘텐츠를 분석하세요.",
        )
    return ("➖ 변동 없음", "유의미한 변화가 감지되지 않았습니다.")


cfg = load_config(CONFIG_FILE)
my_account = cfg.get("my_account", "")
df = load_data(CSV_FILE)

st.title("📊 컨설턴트 팔로워 비교 대시보드")
st.caption(f"기준 계정: **@{my_account}**  ·  데이터 소스: `followers_data.csv`")

if df.empty:
    st.warning(
        "`followers_data.csv`가 비어 있거나 없습니다. "
        "먼저 `python tracker.py`를 실행해 데이터를 수집하세요."
    )
    st.stop()

if my_account and my_account not in df["username"].unique():
    st.error(
        f"기준 계정 **@{my_account}** 의 데이터가 CSV에 없습니다. "
        "config.json의 `my_account` 값을 확인하거나 tracker.py를 실행해 데이터를 수집하세요."
    )
    st.stop()

latest = latest_two_days(df)
my_row = latest[latest["username"] == my_account].iloc[0] if my_account else None
competitors = latest[latest["username"] != my_account].copy()

daily_all = daily_latest(df)
latest_date = daily_all["date"].max()
prev_dates = sorted(d for d in daily_all["date"].unique() if d < latest_date)
prev_date = prev_dates[-1] if prev_dates else None

current_rank = follower_rank_on(daily_all, latest_date, my_account) if my_account else None
prev_rank = (
    follower_rank_on(daily_all, prev_date, my_account)
    if my_account and prev_date is not None
    else None
)
total_accounts = int(daily_all[daily_all["date"] == latest_date]["username"].nunique())

leader_row = None
gap_to_leader = None
if my_account:
    snap = daily_all[daily_all["date"] == latest_date].sort_values(
        "followers", ascending=False
    )
    if not snap.empty:
        leader_row = snap.iloc[0]
        if leader_row["username"] != my_account:
            my_followers_now = int(my_row["followers"]) if my_row is not None else 0
            gap_to_leader = int(leader_row["followers"]) - my_followers_now

# ─────────────────────────── 1. 내 계정 섹션 ───────────────────────────
st.markdown("## 🎯 내 계정")
if my_row is None:
    st.info("config.json에 `my_account`를 설정하세요.")
else:
    c1, c2, c3 = st.columns(3)
    with c1:
        st.metric(
            label=f"@{my_account} 현재 팔로워",
            value=f"{int(my_row['followers']):,}",
            delta=(
                f"{int(my_row['delta']):+,} (전일 대비)"
                if pd.notna(my_row["delta"])
                else None
            ),
        )
    with c2:
        if pd.notna(my_row["delta_pct"]):
            st.metric("증감률", f"{my_row['delta_pct']:+.2f}%")
        else:
            st.metric("증감률", "—")
    with c3:
        st.metric("최신 기록일", str(my_row["date"]))

    r1, r2, r3 = st.columns(3)
    with r1:
        if current_rank is not None:
            if prev_rank is not None and prev_rank != current_rank:
                # 순위는 숫자가 작을수록 높음 → 숫자 감소 = 상승
                move = prev_rank - current_rank
                arrow = "↑" if move > 0 else "↓"
                rank_delta = f"전일 대비 {arrow}{abs(move)}"
            elif prev_rank is not None:
                rank_delta = "전일 대비 —"
            else:
                rank_delta = None
            st.metric(
                "시장 내 순위 (팔로워 기준)",
                f"전체 {total_accounts}명 중 {current_rank}위",
                delta=rank_delta,
                delta_color=("normal" if prev_rank is None or prev_rank == current_rank else "normal"),
            )
        else:
            st.metric("시장 내 순위 (팔로워 기준)", "—")
    with r2:
        if leader_row is not None and leader_row["username"] == my_account:
            st.metric("1위와의 격차", "🏆 1위입니다")
        elif gap_to_leader is not None and leader_row is not None:
            st.metric(
                f"1위 @{leader_row['username']} 대비",
                f"-{gap_to_leader:,}명",
            )
        else:
            st.metric("1위와의 격차", "—")
    with r3:
        if not competitors.empty and pd.notna(my_row["delta"]):
            sorted_all = latest.sort_values("delta", ascending=False, na_position="last").reset_index(drop=True)
            growth_rank = sorted_all.index[sorted_all["username"] == my_account][0] + 1
            st.metric("증감 순위 (전체 중)", f"{growth_rank} / {len(latest)} 위")
        else:
            st.metric("증감 순위 (전체 중)", "—")

st.divider()

# ─────────────────────────── 2. 핵심 인사이트 ───────────────────────────
st.markdown("## 🧠 핵심 인사이트")
my_delta = float(my_row["delta"]) if my_row is not None and pd.notna(my_row["delta"]) else None
comp_delta_avg = (
    float(competitors["delta"].mean()) if not competitors["delta"].dropna().empty else None
)
badge, explanation = classify_trend(my_delta, comp_delta_avg)
i1, i2 = st.columns([1, 2])
with i1:
    st.markdown(f"### {badge}")
with i2:
    st.info(explanation)
    if my_delta is not None and comp_delta_avg is not None:
        st.caption(
            f"내 증감: **{my_delta:+,.0f}**  ·  경쟁자 평균 증감: **{comp_delta_avg:+,.0f}**"
        )

st.divider()

# ─────────────────────────── 3. 경쟁자 비교 테이블 ───────────────────────────
st.markdown("## 🥊 경쟁자 비교")
if competitors.empty:
    st.info("경쟁자 데이터가 아직 없습니다.")
else:
    table = competitors[
        ["username", "followers", "prev_followers", "delta", "delta_pct", "date"]
    ].rename(
        columns={
            "username": "계정",
            "followers": "현재 팔로워",
            "prev_followers": "이전 팔로워",
            "delta": "증감",
            "delta_pct": "증감률(%)",
            "date": "최신 기록일",
        }
    )
    table = table.sort_values("증감", ascending=False, na_position="last").reset_index(drop=True)
    st.dataframe(
        table,
        use_container_width=True,
        hide_index=True,
        column_config={
            "현재 팔로워": st.column_config.NumberColumn(format="%d"),
            "이전 팔로워": st.column_config.NumberColumn(format="%d"),
            "증감": st.column_config.NumberColumn(format="%+d"),
            "증감률(%)": st.column_config.NumberColumn(format="%+.2f%%"),
        },
    )

st.divider()

# ─────────────────────────── 4. 그래프 1: 나 vs 평균 ───────────────────────────
st.markdown("## 📈 나 vs 경쟁자 평균 추이")
daily = daily_latest(df)
if my_account:
    me_series = (
        daily[daily["username"] == my_account]
        .set_index("date")["followers"]
        .rename(f"@{my_account}")
    )
    comp_avg_series = (
        daily[daily["username"] != my_account]
        .groupby("date")["followers"]
        .mean()
        .round(0)
        .rename("경쟁자 평균")
    )
    # Index-normalized view (= 첫 기록일을 100으로 리베이스) so 절대 규모 차이가 추세를 가리지 않음.
    if not me_series.empty and not comp_avg_series.empty:
        compare_df = pd.concat([me_series, comp_avg_series], axis=1).sort_index()
        g1, g2 = st.columns(2)
        with g1:
            st.markdown("**절대값 추이**")
            st.line_chart(compare_df, height=320, use_container_width=True)
        with g2:
            st.markdown("**상대 추이 (첫날 = 100)**")
            normalized = compare_df.div(compare_df.iloc[0]).mul(100).round(2)
            st.line_chart(normalized, height=320, use_container_width=True)
    else:
        st.info("비교 그래프를 그릴 데이터가 부족합니다.")

st.divider()

# ─────────────────────────── 5. 그래프 2: 시장 추세 ───────────────────────────
st.markdown("## 🌐 전체 컨설턴트 시장 추세")
st.caption("모든 계정의 일자별 평균·중앙값·합계를 봐서 다 같이 빠지는지 / 나만 빠지는지 판단합니다.")

market = (
    daily.groupby("date")
    .agg(평균=("followers", "mean"), 중앙값=("followers", "median"), 합계=("followers", "sum"))
    .round(0)
    .sort_index()
)

m1, m2 = st.columns([2, 1])
with m1:
    st.markdown("**시장 평균 / 중앙값**")
    st.line_chart(market[["평균", "중앙값"]], height=320, use_container_width=True)
with m2:
    if len(market) >= 2:
        latest_avg = market["평균"].iloc[-1]
        prev_avg = market["평균"].iloc[-2]
        st.metric(
            "시장 평균 팔로워 (전일 대비)",
            f"{int(latest_avg):,}",
            delta=f"{int(latest_avg - prev_avg):+,}",
        )
        latest_sum = market["합계"].iloc[-1]
        prev_sum = market["합계"].iloc[-2]
        st.metric(
            "시장 합계 팔로워 (전일 대비)",
            f"{int(latest_sum):,}",
            delta=f"{int(latest_sum - prev_sum):+,}",
        )
        ups = int((competitors["delta"] > 0).sum())
        downs = int((competitors["delta"] < 0).sum())
        flats = int((competitors["delta"] == 0).sum())
        st.caption(f"경쟁자 중 ↑{ups} / ↓{downs} / ─{flats}")
    else:
        st.info("시장 추세는 최소 2일치 기록이 쌓이면 표시됩니다.")

with st.expander("원본 데이터 보기"):
    st.dataframe(
        df.sort_values("timestamp", ascending=False),
        use_container_width=True,
        hide_index=True,
    )
