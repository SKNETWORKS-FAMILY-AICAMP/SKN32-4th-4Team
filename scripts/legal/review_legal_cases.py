# -*- coding: utf-8 -*-
"""판례·금감원 정규화 레코드의 사람 검토 큐를 만들고, 검토 결과를 원장에 반영한다.

`data/legal/legal_case_normalized_final.jsonl` 은 LLM이 생성한 정규화 결과이고
전량 `verified_by: "unreviewed"` 다. 08-14 재검증에서 결론반전·오분류 10건이
나온 전례가 있어(디버그 리포트 참조), 사람이 원문 대조 없이는 이 데이터를
C축 hard-negative·gold 평가셋 어디에도 쓸 수 없다(CLAUDE.md §5).

이 도구는 확정하지 않는다. `--queue` 는 검토용 작업 파일만 만들고,
`--html` 은 사람이 쓰기 쉬운 오프라인 화면을 만들며, `--apply` 는 그 작업
파일에 사람이 적어 넣은 verdict 만 원장에 반영한다.

실행:
    python -m scripts.legal.review_legal_cases --queue
    python -m scripts.legal.review_legal_cases --html
    # docs/review/legal_case_human_review_20260825.html 에서 검토 후 JSON 내려받기
    python -m scripts.legal.review_legal_cases --apply --reviewed-by "홍길동"
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_LEDGER = _ROOT / "data" / "legal" / "legal_case_normalized_final.jsonl"
_QUEUE = _ROOT / "data" / "legal" / "human_review_queue.json"
_REPORTS = _ROOT / "docs" / "reports"
_REVIEW_HTML = _ROOT / "docs" / "review" / "legal_case_human_review_20260825.html"
_ASSIGNMENTS = _ROOT / "docs" / "handoff" / "21_판례_금감원_5인_검토분담표.md"

_ALLOWED_VERDICTS = {"confirmed", "corrected", "rejected"}


def _load_ledger() -> list[dict]:
    if not _LEDGER.exists():
        raise SystemExit(f"원장이 없습니다: {_LEDGER}")
    rows = []
    with _LEDGER.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _write_ledger(rows: list[dict]) -> None:
    with _LEDGER.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def build_html() -> None:
    if not _QUEUE.exists():
        raise SystemExit(f"작업 파일이 없습니다: {_QUEUE} — 먼저 --queue 를 실행하세요.")
    from scripts.legal.legal_review_html import (
        build_review_items,
        render_assignment_markdown,
        render_review_html,
    )

    queue = json.loads(_QUEUE.read_text(encoding="utf-8"))
    rows = _load_ledger()
    items = build_review_items(queue, rows, _LEDGER.parent)
    _REVIEW_HTML.parent.mkdir(parents=True, exist_ok=True)
    _REVIEW_HTML.write_text(render_review_html(items, queue), encoding="utf-8")
    _ASSIGNMENTS.parent.mkdir(parents=True, exist_ok=True)
    _ASSIGNMENTS.write_text(render_assignment_markdown(items), encoding="utf-8")
    by_source = Counter(item["source"] for item in items)
    by_level = Counter(item["source_level"] for item in items)
    print(f"사람 검토 화면 {len(items)}건 → {_REVIEW_HTML.relative_to(_ROOT)}")
    print(f"5인 분담표 → {_ASSIGNMENTS.relative_to(_ROOT)}")
    print(f"출처별: {dict(by_source)}")
    print(f"원문 형태별: {dict(by_level)}")


def build_queue() -> None:
    rows = _load_ledger()
    unreviewed = [r for r in rows if r.get("verified_by") == "unreviewed"]

    queue = []
    for r in unreviewed:
        case = r.get("case") or {}
        queue.append(
            {
                "case_id": case.get("id", ""),
                "source": case.get("source", ""),
                "authority_grade": case.get("authority_grade", ""),
                "date": case.get("date", ""),
                "finality": case.get("finality", ""),
                "issues": [
                    {"issue_id": i.get("issue_id"), "쟁점문구": i.get("쟁점문구", "")}
                    for i in (r.get("issues") or [])
                ],
                "holdings": [
                    {
                        "issue_id": h.get("issue_id"),
                        "결론": h.get("결론", ""),
                        "법리_요약": h.get("법리_요약", ""),
                        "confidence": h.get("confidence", ""),
                    }
                    for h in (r.get("holdings") or [])
                ],
                #: ★사람이 채운다. 빈 값이면 --apply 가 건너뛴다.
                "verdict": "",
                "note": "",
            }
        )

    if not queue:
        print("검토 대기(unreviewed) 0건 — 만들 큐가 없습니다.")
        return

    _QUEUE.parent.mkdir(parents=True, exist_ok=True)
    _QUEUE.write_text(
        json.dumps(queue, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    by_source = Counter(item["source"] for item in queue)
    stamp = date.today().isoformat()
    _REPORTS.mkdir(parents=True, exist_ok=True)
    (_REPORTS / f"{stamp}_판례_사람검토_큐_생성.md").write_text(
        "\n".join(
            [
                f"# 판례·금감원 사람 검토 큐 생성 — {stamp}",
                "",
                f"- 대상 원장: `{_LEDGER.relative_to(_ROOT)}`",
                f"- 검토 대기 {len(queue)}건 (전체 {len(rows)}건 중)",
                f"- 출처별: {dict(by_source)}",
                f"- 작업 파일: `{_QUEUE.relative_to(_ROOT)}` — 각 항목의 `verdict`"
                "(confirmed/corrected/rejected)와 `note`를 채운 뒤 --apply 실행",
                "",
                "## 남은 절차",
                "",
                "1. 사람이 `holdings.법리_요약`을 원문(law.go.kr/금감원 게시판)과 대조",
                "2. 결론이 맞으면 `verdict: confirmed`",
                "3. 결론이 틀렸으면 `verdict: corrected` + `note`에 올바른 결론·근거",
                "4. 무관하거나 근거 불충분이면 `verdict: rejected` + `note`에 사유",
                "5. `python -m scripts.legal.review_legal_cases --apply --reviewed-by <이름>`",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"검토 대기 {len(queue)}건 → {_QUEUE.relative_to(_ROOT)}")
    print(f"출처별: {dict(by_source)}")


def apply_review(reviewed_by: str) -> None:
    if not reviewed_by.strip():
        raise SystemExit("--apply 에는 --reviewed-by 가 필수입니다.")
    if not _QUEUE.exists():
        raise SystemExit(f"작업 파일이 없습니다: {_QUEUE} — 먼저 --queue 를 실행하세요.")

    decisions = json.loads(_QUEUE.read_text(encoding="utf-8"))
    rows = _load_ledger()
    by_id = {(r.get("case") or {}).get("id", ""): r for r in rows}

    applied = Counter()
    skipped_empty = 0
    unmatched: list[str] = []
    invalid: list[tuple[str, str]] = []
    missing_notes: list[str] = []

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for item in decisions:
        verdict = (item.get("verdict") or "").strip()
        case_id = item.get("case_id", "")
        if not verdict:
            skipped_empty += 1
            continue
        if verdict not in _ALLOWED_VERDICTS:
            invalid.append((case_id, verdict))
            continue
        note = str(item.get("note") or "").strip()
        if verdict in {"corrected", "rejected"} and not note:
            missing_notes.append(case_id)
            continue
        row = by_id.get(case_id)
        if row is None:
            unmatched.append(case_id)
            continue
        row["verified_by"] = "human"
        row["review_verdict"] = verdict
        row["review_note"] = note
        row["reviewed_by"] = reviewed_by.strip()
        row["reviewed_at"] = now
        applied[verdict] += 1

    #: ★조용한 스킵을 만들지 않는다 — 잘못된 verdict는 세어서 실패로 보고한다.
    if invalid:
        raise SystemExit(
            f"허용되지 않은 verdict {len(invalid)}건: {invalid[:10]} "
            f"(허용값: {sorted(_ALLOWED_VERDICTS)})"
        )
    if missing_notes:
        raise SystemExit(
            f"수정 필요/제외인데 이유가 없는 항목 {len(missing_notes)}건: {missing_notes[:10]}"
        )

    _write_ledger(rows)
    print(f"반영 완료: {dict(applied)}")
    print(f"미기재(verdict 빈칸) 건너뜀: {skipped_empty}")
    if unmatched:
        print(f"★원장에 없는 case_id {len(unmatched)}건: {unmatched}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--queue", action="store_true", help="검토 대기 큐를 만든다(쓰지 않음)")
    ap.add_argument("--apply", action="store_true", help="채워진 verdict를 원장에 반영한다")
    ap.add_argument("--html", action="store_true", help="브라우저용 사람 검토 화면을 만든다")
    ap.add_argument("--reviewed-by", default="", help="누가 검토했나. --apply 에 필수")
    a = ap.parse_args()

    if sum((a.queue, a.apply, a.html)) != 1:
        raise SystemExit("--queue, --html, --apply 중 하나만 지정하세요.")

    if a.queue:
        build_queue()
    elif a.html:
        build_html()
    else:
        apply_review(a.reviewed_by)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
