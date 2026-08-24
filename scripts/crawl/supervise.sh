#!/usr/bin/env bash
# 브라우저 수집기 감시 루프 — 죽으면 다시 띄운다.
#
# ★왜 필요한가
#   Playwright 드라이버가 장시간 실행에서 끊긴다. 배치 단위로 브라우저를 새로 띄우게
#   고쳤지만 프로세스 자체가 죽는 경우가 남는다. 이미 받은 URL 은 건너뛰므로
#   다시 띄우면 그 지점부터 이어간다 — 중복 수집이 없다.
#
# 사용: bash scripts/crawl/supervise.sh <site> <batch_pages> <max_loops>
set -u
SITE="${1:?site}"
BATCH="${2:-10}"
MAX="${3:-200}"
LOG="/tmp/sv_${SITE}.log"
LOCK="/tmp/sv_${SITE}.lock"

# ★중복 실행 방지. 이게 없어서 같은 사이트를 12개 프로세스가 동시에 긁었고,
#   기록이 9,574행까지 부풀고(실제 파일 2,260개) 서로 파일을 덮어 잘린 PDF 가 생겼다.
if ! mkdir "$LOCK" 2>/dev/null; then
  # ★좀비 락 처리. 프로세스가 죽으면 trap 이 안 돌 수 있어 락만 남는다.
  #   PID 를 적어 두고, 그 프로세스가 없으면 락을 회수한다.
  old_pid=$(cat "$LOCK/pid" 2>/dev/null || echo "")
  if [ -n "$old_pid" ] && kill -0 "$old_pid" 2>/dev/null; then
    echo "이미 실행 중입니다: $SITE (pid=$old_pid)" >&2
    exit 1
  fi
  echo "좀비 락 회수: $SITE (죽은 pid=$old_pid)" >&2
  rm -rf "$LOCK"
  mkdir "$LOCK" || exit 1
fi
echo $$ > "$LOCK/pid"
trap 'rmdir "$LOCK" 2>/dev/null' EXIT INT TERM

: > "$LOG"
for i in $(seq 1 "$MAX"); do
  echo "=== [$i/$MAX] $(date +%H:%M:%S) 시작 ===" >> "$LOG"
  # ★출력이 cp949 로 나가면 한글 상품명에서 UnicodeEncodeError 가 나고 프로세스가 죽는다.
  #   (메리츠가 매 배치마다 그렇게 죽고 있었다 — 수집 로직이 아니라 **로그 출력**이 원인이었다)
  PYTHONIOENCODING=utf-8 python -u -m scripts.crawl.browser_collector --site "$SITE" --batch-pages "$BATCH" >> "$LOG" 2>&1
  rc=$?
  n=$(grep -c '\[OK\]' "$LOG" 2>/dev/null || echo 0)
  echo "=== [$i] 종료 rc=$rc 누적OK=$n ===" >> "$LOG"
  # 정상 종료(rc=0)이고 새로 받은 게 없으면 더 받을 게 없다는 뜻이다.
  if [ "$rc" -eq 0 ]; then
    last=$(tail -40 "$LOG" | grep -c '\[OK\]' || echo 0)
    if [ "$last" -eq 0 ]; then
      echo "=== 새로 받은 것이 없어 종료한다 ===" >> "$LOG"
      break
    fi
  fi
  sleep 3
done
echo "=== 감시 종료: 누적 OK $(grep -c '\[OK\]' "$LOG") ===" >> "$LOG"
