"""Streamlit dashboard for the Threads follower tracker.

Reads followers_data.csv (committed alongside this file in the repo) and
renders a follower trend chart and a day-over-day delta table.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent
CSV_FILE = ROOT / "followers_data.csv"

st.set_page_config(
    page_title="Threads Follower Tracker",
    page_icon="📈",
    layout="wide",
)


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
    )


def build_delta_table(df: pd.DataFrame) -> pd.DataFrame:
    daily = daily_latest(df).sort_values(["username", "date"])
    daily["prev_followers"] = daily.groupby("username")["followers"].shift(1)
    daily["prev_date"] = daily.groupby("username")["date"].shift(1)

    latest = daily.groupby("username", as_index=False).tail(1).copy()
    latest["delta"] = (latest["followers"] - latest["prev_followers"]).astype("Int64")
    latest["delta_pct"] = (
        (latest["followers"] - latest["prev_followers"])
        / latest["prev_followers"]
        * 100
    ).round(2)

    out = latest[
        ["username", "date", "followers", "prev_date", "prev_followers", "delta", "delta_pct"]
    ].rename(
        columns={
            "username": "계정",
            "date": "최신 기록일",
            "followers": "현재 팔로워",
            "prev_date": "이전 기록일",
            "prev_followers": "이전 팔로워",
            "delta": "증감",
            "delta_pct": "증감률(%)",
        }
    )
    return out.sort_values("증감", ascending=False, na_position="last").reset_index(drop=True)


df = load_data(CSV_FILE)

st.title("📈 Threads 팔로워 트래커")
st.caption("`tracker.py`가 수집한 `followers_data.csv` 기반 대시보드")

if df.empty:
    st.warning(
        "`followers_data.csv`가 비어 있거나 없습니다. "
        "먼저 `python tracker.py`를 실행해 데이터를 수집하세요."
    )
    st.stop()

accounts = sorted(df["username"].unique().tolist())

with st.sidebar:
    st.header("필터")
    selected = st.multiselect("계정 선택", accounts, default=accounts)
    st.divider()
    st.metric("추적 계정 수", len(accounts))
    st.metric("전체 기록 수", len(df))
    st.metric("최신 수집 시각", df["timestamp"].max().strftime("%Y-%m-%d %H:%M"))

filtered = df[df["username"].isin(selected)] if selected else df.iloc[0:0]

st.subheader("팔로워 추이")
if filtered.empty:
    st.info("표시할 계정을 선택하세요.")
else:
    chart_df = (
        daily_latest(filtered)
        .pivot(index="date", columns="username", values="followers")
        .sort_index()
    )
    st.line_chart(chart_df, height=420, use_container_width=True)

st.subheader("전날 대비 증감")
delta_df = build_delta_table(filtered)
if delta_df.empty:
    st.info("증감을 계산할 데이터가 부족합니다 (최소 2일치 기록 필요).")
else:
    st.dataframe(
        delta_df,
        use_container_width=True,
        hide_index=True,
        column_config={
            "현재 팔로워": st.column_config.NumberColumn(format="%d"),
            "이전 팔로워": st.column_config.NumberColumn(format="%d"),
            "증감": st.column_config.NumberColumn(format="%+d"),
            "증감률(%)": st.column_config.NumberColumn(format="%+.2f%%"),
        },
    )

with st.expander("원본 데이터 보기"):
    st.dataframe(
        filtered.sort_values("timestamp", ascending=False),
        use_container_width=True,
        hide_index=True,
    )
