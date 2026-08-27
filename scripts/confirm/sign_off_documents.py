# -*- coding: utf-8 -*-
"""확정 원장에 **사람 최종승인**을 기록한다.

★왜 도구로 만드나

    판정 게이트(`app/core/domain/identification_mode.py`)는 원장의 `confirmed_by` 에
    **「대기」라는 낱말이 있는지**로만 승인 여부를 본다(`_PENDING_MARK`).
    손으로 고치면 —
      · 「대기」를 지우다 다른 문구에 그 글자가 남아 **승인이 안 된 채 승인한 줄 안다**
      · 누가·언제·어떤 범위로 승인했는지가 **안 남는다**
    둘 다 §0 위반이다. 그래서 한곳에서 처리한다.

★★**일괄 승인과 개별 검토를 구분해 적는다.**

    1,355건을 사람이 한 장씩 볼 수는 없다. 그건 현실이고, 일괄 승인이 잘못도 아니다.
    잘못은 **일괄 승인을 개별 검토인 것처럼 적는 것**이다.
    그래서 `signoff_scope` 를 반드시 남긴다 —

        "bulk"        한 번에 승인. 문서별로 사람이 본 것이 아니다.
        "per_document" 문서마다 사람이 확인했다.

    나중에 이 원장을 읽는 사람이 근거 강도를 오해하면 「보장됩니다」가 틀린 근거 위에 선다.

★되돌리기

    `--apply` 전에 원장을 `backups/` 에 복사한다. 되돌리려면 그 파일을 덮어쓰면 된다.

사용:

    python -m scripts.confirm.sign_off_documents                     # 조회만
    python -m scripts.confirm.sign_off_documents --apply \
        --by "프로젝트 소유자" --scope bulk \
        --note "기계대조 결과를 한 번에 승인. 개별 문서 확인 아님"
"""

from __future__ import annotations

import argparse
import collections
import json
import pathlib
import shutil
import sys
from datetime import datetime, timezone

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_LEDGER = _ROOT / "config" / "confirmed_documents.jsonl"
_BACKUPS = _ROOT / "backups"

#: ★게이트와 **같은 판정**을 쓴다. 여기서 따로 문자열을 비교하면 둘이 갈린다.
sys.path.insert(0, str(_ROOT))


def _pending(entry: dict) -> bool:
    from app.core.domain import identification_mode as im

    return im.is_pending_signoff(entry)


#: ★★**「대기」는 문장 끝에만 있지 않다** (2026-08-27 실측하고 고쳤다).
#:
#:   처음 판은 `" · 사람 최종승인 대기"` 접미사만 지웠다. 그런데 원장에는
#:   `"claude-code(사람 최종승인 대기) — _rivals()의 self-shadow 매처 결함…"`
#:   처럼 **괄호 안**에 든 것이 있었다. 1,355건 중 1건이 그래서 안 지워졌고,
#:   게이트는 여전히 승인 전으로 읽었다.
#:   ★그 1건을 **도구가 스스로 잡았다** — 쓴 뒤 게이트로 다시 판정했기 때문이다.
#:     안 했으면 「1,355건 승인 완료」라고 말하고 넘어갔을 것이다.
_PENDING_FORMS = (" · 사람 최종승인 대기", "(사람 최종승인 대기)", "사람 최종승인 대기")


def _strip_pending(text: str) -> str:
    for form in _PENDING_FORMS:
        text = text.replace(form, "")
    #: 지운 자리에 남는 구분자·빈칸을 정리한다.
    while "  " in text:
        text = text.replace("  ", " ")
    return text.strip().strip("·").strip()


def _read() -> list[dict]:
    return [json.loads(x) for x in _LEDGER.read_text(encoding="utf-8").splitlines() if x.strip()]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="확정 원장에 사람 최종승인을 기록한다")
    ap.add_argument("--apply", action="store_true", help="실제로 쓴다(기본은 조회만)")
    ap.add_argument("--by", default="", help="누가 승인했나. --apply 에 필수")
    ap.add_argument("--scope", choices=("bulk", "per_document"), default="",
                    help="bulk=한 번에 승인(문서별 확인 아님) · per_document=문서마다 확인. --apply 에 필수")
    ap.add_argument("--note", default="", help="원장에 남길 말. --apply 에 필수")
    ap.add_argument("--at", default="", help="승인 시각(ISO). 기본은 지금")
    a = ap.parse_args(argv)

    rows = _read()
    pend = [r for r in rows if _pending(r)]
    ev = collections.Counter(r.get("evidence", "?") for r in pend)
    print(f"[원장] {_LEDGER.relative_to(_ROOT)} · 전체 {len(rows):,}건 · 승인 전 {len(pend):,}건")
    if pend:
        print("       승인 전 항목의 근거 등급 —")
        for k, v in ev.most_common():
            #: ★약한 근거를 **먼저·크게** 보여준다. 승인은 이걸 보고 하는 것이다.
            mark = " ★가장 약한 근거" if k == "name_only" else (
                " ★본문 추출이 막힌 채 확정" if k == "extraction_blocked" else "")
            print(f"         {k:<34} {v:>6,}{mark}")
    if not pend:
        print("       승인할 것이 없습니다.")
        return 0

    if not a.apply:
        print("       (조회만 했다. 기록하려면 --apply --by ... --scope ... --note ...)")
        return 0

    missing = [n for n, v in (("--by", a.by), ("--scope", a.scope), ("--note", a.note)) if not str(v).strip()]
    if missing:
        #: ★누가·어떤 범위로·왜 승인했는지 없이는 기록하지 않는다.
        print(f"★{', '.join(missing)} 가 필요합니다. 승인은 누가 무엇을 했는지 남아야 합니다.")
        return 2

    stamp = a.at.strip() or datetime.now(timezone.utc).isoformat()
    scope_말 = ("일괄승인 — 문서별 개별 확인 아님" if a.scope == "bulk"
                else "문서별 확인")
    mark = f" · 사람 최종승인 {stamp[:10]}({a.by.strip()} {scope_말})"
    if "대기" in mark:
        #: ★★있을 수 없는 일이지만 막아 둔다 — 문구에 그 글자가 남으면
        #:   게이트가 **여전히 승인 전으로 읽는다.** 조용히 통과시키지 않는다.
        print("★남길 문구에 「대기」가 들어 있습니다. 그대로 쓰면 승인으로 인식되지 않습니다.")
        return 2

    _BACKUPS.mkdir(exist_ok=True)
    bak = _BACKUPS / f"confirmed_documents_{stamp[:10].replace('-','')}_before_signoff.jsonl"
    shutil.copy2(_LEDGER, bak)
    print(f"[백업] {bak.relative_to(_ROOT)}  ({len(rows):,}건)")

    n = 0
    for r in rows:
        if not _pending(r):
            continue
        cb = _strip_pending(r.get("confirmed_by") or "")
        #: ★이미 이 승인 표시가 붙어 있으면 **또 붙이지 않는다.**
        #:   실측 2026-08-27: 첫 실행에서 「대기」가 괄호 안에 있던 1건을 못 지워
        #:   다시 돌렸는데, 그때 표시가 두 번 붙을 뻔했다.
        r["confirmed_by"] = cb if mark.strip() in cb else cb + mark
        r["signed_off_at"] = stamp
        r["signed_off_by"] = a.by.strip()
        r["signoff_scope"] = a.scope
        r["signoff_note"] = a.note.strip()
        n += 1

    _LEDGER.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
                       encoding="utf-8")

    #: ★쓴 뒤에 **게이트로 다시 판정한다.** 「지웠으니 됐겠지」로 넘어가지 않는다.
    left = [r for r in _read() if _pending(r)]
    print(f"[기록] {n:,}건 승인 · 범위 {a.scope}")
    if left:
        print(f"★★게이트가 아직 {len(left):,}건을 승인 전으로 봅니다 — 문구를 확인하세요.")
        return 1
    print("[검증] 게이트 재판정 — 승인 전 0건")
    if a.scope == "bulk":
        print("★이 승인은 **일괄**입니다. 문서마다 사람이 본 것이 아니라는 사실이")
        print("  signoff_scope='bulk' 로 원장에 남았습니다. 근거 강도는 evidence 를 보세요.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
