"""Streamlit dashboard — 노영우 컨설턴트 vs 경쟁자 비교 분석.

내 계정(`my_account` in config.json)을 기준으로 두고, 나머지 추적 계정을
경쟁군으로 보아 시장 추세 안에서 내 위치를 진단한다.
하루 여러 회 수집된 데이터를 기반으로 일중 추이도 함께 표시한다.
"""

from __future__ import annotations

import json
from datetime import timedelta
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


def latest_record(df: pd.DataFrame) -> pd.DataFrame:
    """Latest single record per username, regardless of date."""
    return (
        df.sort_values("timestamp")
        .groupby("username", as_index=False)
        .tail(1)
        .reset_index(drop=True)
    )


def closest_before(df: pd.DataFrame, target_ts: pd.Timestamp) -> pd.DataFrame:
    """For each username, the record with the largest timestamp <= target_ts."""
    sub = df[df["timestamp"] <= target_ts]
    if sub.empty:
        return sub
    return (
        sub.sort_values("timestamp")
        .groupby("username", as_index=False)
        .tail(1)
        .reset_index(drop=True)
    )


def comparison_table(
    df: pd.DataFrame, my_account: str, latest_ts: pd.Timestamp, today
) -> pd.DataFrame:
    """전 계정에 대해 24시간 전·오늘 첫 기록 대비 변화를 한 표로."""
    latest = latest_record(df).rename(columns={"followers": "현재 팔로워", "timestamp": "최신 시각"})

    # 24시간 전 기준점 (각 계정별 그 시점 이전의 최신 기록)
    yesterday = closest_before(df, latest_ts - timedelta(hours=24))[
        ["username", "followers", "timestamp"]
    ].rename(columns={"followers": "24h 전 팔로워", "timestamp": "24h 전 시각"})

    # 오늘 첫 기록 (각 계정별 today 일자의 최소 timestamp)
    today_df = df[df["date"] == today]
    if today_df.empty:
        first_today = pd.DataFrame(columns=["username", "오늘 첫 기록", "오늘 첫 시각"])
    else:
        first_today = (
            today_df.sort_values("timestamp")
            .groupby("username", as_index=False)
            .head(1)[["username", "followers", "timestamp"]]
            .rename(columns={"followers": "오늘 첫 기록", "timestamp": "오늘 첫 시각"})
        )

    out = latest.merge(yesterday, on="username", how="left").merge(
        first_today, on="username", how="left"
    )

    out["24h 증감"] = (out["현재 팔로워"] - out["24h 전 팔로워"]).astype("Int64")
    out["24h 증감률(%)"] = (
        (out["현재 팔로워"] - out["24h 전 팔로워"]) / out["24h 전 팔로워"] * 100
    ).round(2)
    out["오늘 변화"] = (out["현재 팔로워"] - out["오늘 첫 기록"]).astype("Int64")
    out["역할"] = out["username"].apply(lambda u: "🎯 나" if u == my_account else "경쟁자")

    return out


def follower_rank(snap: pd.DataFrame, username: str) -> int | None:
    if snap.empty or username not in snap["username"].values:
        return None
    s = snap.sort_values("followers", ascending=False).reset_index(drop=True)
    return int(s.index[s["username"] == username][0]) + 1


def classify_trend(my_delta: float | None, comp_delta_avg: float | None) -> tuple[str, str]:
    """Return (badge_emoji_label, explanation) based on me vs competitors."""
    if my_delta is None or comp_delta_avg is None or pd.isna(my_delta) or pd.isna(comp_delta_avg):
        return ("⏳ 데이터 부족", "최소 24시간치 기록이 모이면 인사이트가 표시됩니다.")

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

latest_ts = df["timestamp"].max()
today = latest_ts.date()

compare_df = comparison_table(df, my_account, latest_ts, today)
my_row = (
    compare_df[compare_df["username"] == my_account].iloc[0]
    if my_account and my_account in compare_df["username"].values
    else None
)
competitors = compare_df[compare_df["username"] != my_account].copy()

# 최신 시점 스냅샷 — 시장 내 순위 계산용
latest_snap = latest_record(df)
current_rank = follower_rank(latest_snap, my_account) if my_account else None
total_accounts = int(latest_snap["username"].nunique())

# 24시간 전 시점의 순위 (랭크 변동 계산)
prev_snap = closest_before(df, latest_ts - timedelta(hours=24))
prev_rank = follower_rank(prev_snap, my_account) if my_account and not prev_snap.empty else None

leader_row = None
gap_to_leader = None
if my_account and not latest_snap.empty:
    snap_sorted = latest_snap.sort_values("followers", ascending=False)
    leader_row = snap_sorted.iloc[0]
    if leader_row["username"] != my_account and my_row is not None:
        gap_to_leader = int(leader_row["followers"]) - int(my_row["현재 팔로워"])

# ─────────────────────────── 1. 내 계정 섹션 ───────────────────────────
st.markdown("## 🎯 내 계정")
if my_row is None:
    st.info("config.json에 `my_account`를 설정하세요.")
else:
    today_change = (
        int(my_row["오늘 변화"]) if pd.notna(my_row["오늘 변화"]) else None
    )
    delta_24h = int(my_row["24h 증감"]) if pd.notna(my_row["24h 증감"]) else None

    c1, c2, c3 = st.columns(3)
    with c1:
        st.metric(
            label=f"@{my_account} 현재 팔로워",
            value=f"{int(my_row['현재 팔로워']):,}",
            delta=(f"{delta_24h:+,} (24h)" if delta_24h is not None else None),
        )
    with c2:
        if pd.notna(my_row["24h 증감률(%)"]):
            st.metric("24h 증감률", f"{my_row['24h 증감률(%)']:+.2f}%")
        else:
            st.metric("24h 증감률", "—")
    with c3:
        st.metric(
            "오늘 변화 (첫 기록 → 현재)",
            f"{today_change:+,}" if today_change is not None else "—",
        )

    r1, r2, r3 = st.columns(3)
    with r1:
        if current_rank is not None:
            if prev_rank is not None and prev_rank != current_rank:
                move = prev_rank - current_rank
                arrow = "↑" if move > 0 else "↓"
                rank_delta = f"24h 대비 {arrow}{abs(move)}"
            elif prev_rank is not None:
                rank_delta = "24h 대비 —"
            else:
                rank_delta = None
            st.metric(
                "시장 내 순위 (팔로워 기준)",
                f"전체 {total_accounts}명 중 {current_rank}위",
                delta=rank_delta,
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
        st.metric("최신 수집 시각", latest_ts.strftime("%Y-%m-%d %H:%M"))

st.divider()

# ─────────────────────────── 2. 핵심 인사이트 ───────────────────────────
st.markdown("## 🧠 핵심 인사이트")
my_delta = float(my_row["24h 증감"]) if my_row is not None and pd.notna(my_row["24h 증감"]) else None
comp_delta_avg = (
    float(competitors["24h 증감"].dropna().mean())
    if not competitors["24h 증감"].dropna().empty
    else None
)
badge, explanation = classify_trend(my_delta, comp_delta_avg)
i1, i2 = st.columns([1, 2])
with i1:
    st.markdown(f"### {badge}")
with i2:
    st.info(explanation)
    if my_delta is not None and comp_delta_avg is not None:
        st.caption(
            f"내 24h 증감: **{my_delta:+,.0f}**  ·  "
            f"경쟁자 24h 평균 증감: **{comp_delta_avg:+,.0f}**"
        )

st.divider()

# ─────────────────────────── 3. 경쟁자 비교 테이블 ───────────────────────────
st.markdown("## 🥊 경쟁자 비교")
if competitors.empty:
    st.info("경쟁자 데이터가 아직 없습니다.")
else:
    table = competitors[
        [
            "username",
            "현재 팔로워",
            "24h 전 팔로워",
            "24h 증감",
            "24h 증감률(%)",
            "오늘 변화",
            "최신 시각",
        ]
    ].rename(columns={"username": "계정"})
    table = table.sort_values("24h 증감", ascending=False, na_position="last").reset_index(
        drop=True
    )
    st.dataframe(
        table,
        use_container_width=True,
        hide_index=True,
        column_config={
            "현재 팔로워": st.column_config.NumberColumn(format="%d"),
            "24h 전 팔로워": st.column_config.NumberColumn(format="%d"),
            "24h 증감": st.column_config.NumberColumn(format="%+d"),
            "24h 증감률(%)": st.column_config.NumberColumn(format="%+.2f%%"),
            "오늘 변화": st.column_config.NumberColumn(format="%+d"),
            "최신 시각": st.column_config.DatetimeColumn(format="MM-DD HH:mm"),
        },
    )

st.divider()

# ─────────────────────────── 4. 오늘 하루 추이 (시간별) ───────────────────────────
st.markdown("## ⏱️ 오늘 하루 추이 (시간별)")
today_df = df[df["date"] == today].copy()
if today_df.empty or today_df["timestamp"].nunique() < 2:
    st.info("오늘 수집된 기록이 2회 미만이라 시간별 추이를 그릴 수 없습니다.")
else:
    intraday_pivot = today_df.pivot_table(
        index="timestamp", columns="username", values="followers", aggfunc="last"
    ).sort_index()

    h1, h2 = st.columns(2)
    with h1:
        st.markdown(f"**@{my_account} 시간별 추이**")
        if my_account in intraday_pivot.columns:
            me_intraday = intraday_pivot[[my_account]].dropna()
            if len(me_intraday) >= 2:
                st.line_chart(me_intraday, height=320, use_container_width=True)
            else:
                st.info("내 계정 기록이 1개뿐입니다 — 다음 수집 후 표시됩니다.")
        else:
            st.info("오늘 내 계정 기록이 아직 없습니다.")
    with h2:
        st.markdown("**경쟁자 평균 시간별 추이**")
        comp_cols = [c for c in intraday_pivot.columns if c != my_account]
        if comp_cols:
            comp_intraday = intraday_pivot[comp_cols].mean(axis=1).round(0).rename("경쟁자 평균").to_frame()
            if len(comp_intraday) >= 2:
                st.line_chart(comp_intraday, height=320, use_container_width=True)
            else:
                st.info("경쟁자 기록이 1개뿐입니다.")
        else:
            st.info("오늘 경쟁자 기록이 없습니다.")

    with st.expander("계정별 시간별 원본 (전체 보기)"):
        st.line_chart(intraday_pivot, height=380, use_container_width=True)

st.divider()

# ─────────────────────────── 5. 그래프: 일별 나 vs 평균 ───────────────────────────
st.markdown("## 📈 일별 추이 — 나 vs 경쟁자 평균")
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
    if not me_series.empty and not comp_avg_series.empty:
        compare_daily = pd.concat([me_series, comp_avg_series], axis=1).sort_index()
        g1, g2 = st.columns(2)
        with g1:
            st.markdown("**절대값 추이**")
            st.line_chart(compare_daily, height=320, use_container_width=True)
        with g2:
            st.markdown("**상대 추이 (첫날 = 100)**")
            normalized = compare_daily.div(compare_daily.iloc[0]).mul(100).round(2)
            st.line_chart(normalized, height=320, use_container_width=True)
    else:
        st.info("일별 비교 그래프를 그릴 데이터가 부족합니다.")

st.divider()

# ─────────────────────────── 6. 그래프: 시장 추세 ───────────────────────────
st.markdown("## 🌐 전체 컨설턴트 시장 추세 (일별)")
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
        ups = int((competitors["24h 증감"] > 0).sum())
        downs = int((competitors["24h 증감"] < 0).sum())
        flats = int((competitors["24h 증감"] == 0).sum())
        st.caption(f"경쟁자 24h 변화: ↑{ups} / ↓{downs} / ─{flats}")
    else:
        st.info("시장 추세는 최소 2일치 기록이 쌓이면 표시됩니다.")

with st.expander("원본 데이터 보기"):
    st.dataframe(
        df.sort_values("timestamp", ascending=False),
        use_container_width=True,
        hide_index=True,
    )
