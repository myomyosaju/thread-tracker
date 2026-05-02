#!/bin/bash
# Wrapper: tracker.py 실행 → followers_data.csv 변경 시 GitHub push.
# cron / launchd 양쪽에서 동일하게 호출.

set -u
cd /Users/heojiyeong/Desktop/threads-tracker || exit 1

# cron/launchd는 PATH가 거의 비어있으므로 명시적으로 채움
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"
export LANG="en_US.UTF-8"
export LC_ALL="en_US.UTF-8"

LOG="/Users/heojiyeong/Desktop/threads-tracker/tracker.log"
PYTHON="/Users/heojiyeong/Desktop/threads-tracker/.venv/bin/python"

{
  echo ""
  echo "===== $(date '+%Y-%m-%d %H:%M:%S') ====="

  "$PYTHON" tracker.py
  rc=$?
  echo "[tracker.py exit code] $rc"

  if [ $rc -ne 0 ]; then
    echo "[skip git push] tracker 실패"
    exit $rc
  fi

  if git diff --quiet -- followers_data.csv 2>/dev/null; then
    echo "[skip git push] CSV 변경 없음"
    exit 0
  fi

  git add followers_data.csv
  git -c user.name="threads-bot" -c user.email="bot@local" \
    commit -m "data: $(date '+%Y-%m-%d %H:%M')" --quiet
  git push origin main
  echo "[git push] 완료"
} >> "$LOG" 2>&1

# 충전기 미연결(=배터리 모드)일 때만 다시 재취침. 데스크에서 작업 중일 때는 안 끔.
# 주석 해제해서 사용:
# if pmset -g batt | grep -q "Battery Power"; then
#   sleep 30
#   pmset sleepnow >/dev/null 2>&1 &
# fi
