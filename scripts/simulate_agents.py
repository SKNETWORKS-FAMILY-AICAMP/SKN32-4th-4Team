"""가상 에이전트 시뮬레이터 CLI — 관리자 화면과 **같은 실행기**를 쓴다.

실행:
    python -m scripts.simulate_agents --agents 20 --cases 3
    python -m scripts.simulate_agents --agents 15 --cases 3 --codes S72.0 --auto-verify
    python -m scripts.simulate_agents --reset          # 합성 트랙만 비운다

★생성 규칙은 여기 없다

    전부 `app/adapters/demo_simulator.py` 에 있다. CLI 와 화면이 각자 구현하면
    "화면에서 돌린 것"과 "CLI 로 돌린 것"이 달라지고, 어느 쪽이 맞는지 아무도 모른다.
    이 파일은 **인자를 받아 넘기고 진행 상황을 출력할 뿐**이다.

★★실제 트랙을 건드릴 방법이 없다

    제출은 `/v1/demo/observations` 로만 가고, 승격·초기화는 합성 경로만 아는
    함수로만 한다. 계획서 §5-1 의 "합성 생성기는 실제 저장소에 쓸 권한이 없다".
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
os.chdir(_ROOT)
sys.path.insert(0, str(_ROOT))

from app.adapters import demo_simulator as sim  # noqa: E402
from app.core.errors import AppError  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="가상 에이전트 시뮬레이터(합성 트랙 전용)")
    ap.add_argument("--base", default="", help="고객 웹 서버 주소(비우면 설정값)")
    ap.add_argument("--agents", type=int, default=12)
    ap.add_argument("--cases", type=int, default=3, help="에이전트당 보고 건수")
    ap.add_argument("--seed", type=int, default=20260804)
    #: ★코드를 좁히면 한 코호트에 표본이 몰려 **최소표본 게이트를 넘는 순간**을 보여줄 수 있다.
    #:   기본값(8종 분산)으로는 코드당 10건 남짓이라 게이트에 계속 걸린다 — 그게 정상이다.
    ap.add_argument("--codes", default="", help="쉼표로 구분한 KCD 코드(비우면 8종 무작위)")
    ap.add_argument("--delay", type=float, default=0.15, help="요청 간 간격(초)")
    ap.add_argument("--auto-verify", action="store_true",
                    help="합성 형식·범위 정합성 게이트 통과분만 자동 승격")
    ap.add_argument("--reset", action="store_true", help="합성 트랙을 비우고 종료")
    args = ap.parse_args()

    if args.reset:
        r = sim.reset()
        for d in r["removed"]:
            print(f"  삭제: {d}")
        print("합성 트랙을 비웠습니다. 실제 트랙(data/external·data/cohort/verified_real)은 그대로입니다.")
        return 0

    from app.core.config import get_settings

    base = args.base.strip() or get_settings().CUSTOMER_BASE_URL
    codes = [c.strip().upper() for c in args.codes.split(",") if c.strip()] or list(sim.CODES)

    print(f"가상 에이전트 {args.agents}대 × {args.cases}건 → {base}")
    print(f"질병기호: {', '.join(codes)}")
    print(f"승격: {'합성 정합성 검사 통과분' if args.auto_verify else '없음(대시보드에서 수동 검수)'}")
    if args.auto_verify:
        print("주의: 보험금 지급 진위나 보장 여부를 자동 승인하는 기능이 아닙니다.")
    print("-" * 70)

    try:
        sim.start(base=base, agents=args.agents, cases=args.cases, codes=codes,
                  delay_ms=int(args.delay * 1000), auto_verify=args.auto_verify,
                  seed=args.seed)
    except AppError as e:
        print(f"시작하지 못했습니다: {e}")
        return 1

    #: 실행기는 스레드로 돈다. CLI 는 끝날 때까지 진행 상황을 찍는다.
    last = -1
    while True:
        st = sim.status()
        done = st["submitted"] + st["duplicated"] + st["failed"]
        if done != last:
            print(f"\r  진행 {done}/{st['planned']} · 제출 {st['submitted']} · "
                  f"승격 {st['promoted']} · 검증거절 {st.get('rejected', 0)} · "
                  f"중복 {st['duplicated']} · 실패 {st['failed']}",
                  end="", flush=True)
            last = done
        if not st["running"]:
            break
        time.sleep(0.2)

    st = sim.status()
    print()
    print("-" * 70)
    print(f"종료 사유: {st['stopped_by']}" + (f" · {st['last_error']}" if st["last_error"] else ""))

    from app.adapters import demo_submission_store as demo

    c = demo.counts()
    print(f"합성 트랙 누적: 제출 {c['submitted']} · 승격 {c['promoted']} · 대기 {c['pending']}")
    print("★승격된 것만 /v1/demo/cohorts 의 n 에 들어갑니다. 실제 트랙은 그대로 0 입니다.")
    return 0 if st["stopped_by"] != "error" else 1


if __name__ == "__main__":
    raise SystemExit(main())
