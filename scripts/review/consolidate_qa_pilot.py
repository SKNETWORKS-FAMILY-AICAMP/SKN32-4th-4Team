# -*- coding: utf-8 -*-
"""흩어진 검수 결과를 **현재 후보 300건 위에** 모은다.

    python -m scripts.review.consolidate_qa_pilot
    python -m scripts.review.consolidate_qa_pilot --report      # 숫자만

★★왜 다시 만드나 (2026-08-27)

    검수 결과가 네 벌 돌아왔는데 쓸 수 있는 상태가 아니었다.

    ① **파트1·2·4 는 옛 후보 기준이다.** 현재 후보와 15·15·17건만 겹친다.
       A축을 검색 정답셋 → 엔진 판정으로 바꾸면서 항목이 통째로 달라졌다.
       (팀원이 작업하는 중에 후보를 갈아엎은 것은 이쪽 잘못이다.)

    ② **「완성본」이 축 단위로 일괄 결정했다.** 파트1·2·4 가 전부
       「A축 36 전부 승인 · B축 12 전부 수정 · C축 12 전부 수정」이다.
       세 파일의 원본 결정이 A 5·40·8 로 완전히 달랐는데 결과가 같다.
       원본 판단 보존율 파트1 25% · 파트4 18%. **반려 38 · 보류 5 가 전량 사라졌다.**
       반려를 뒤집은 근거는 「검색 정답셋에 있으니 맞다」였는데,
       정답셋에 있다는 것은 **검색이 그걸 찾아야 한다**는 뜻이지
       **그 조항이 고객 질문에 답한다**는 뜻이 아니다. 검수가 물은 것은 후자다.

★★그래서 이 스크립트가 하는 일

    **결정하지 않는다.** 항목마다 **누가 무엇을 근거로 뭐라고 했는지**를 모으고,
    서로 어긋나는지만 표시한다. `decision` 은 전부 비워 둔다(05D §3-3).

        agreed        규칙 제안과 검수 결정이 같다        → 사람은 확인만
        disputed      서로 다르다                        → **사람이 본다**
        review_only   검수만 있고 규칙은 할 말이 없다      → 사람이 본다
        rule_only     규칙 제안만 있고 검수가 없다        → 사람은 확인만
        untouched     아무것도 없다                      → **사람이 본다**

    ★등급은 「얼마나 확실한가」이지 결정이 아니다. 어느 등급도 학습에 바로 못 들어간다.
"""

from __future__ import annotations

import argparse
import collections
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
CAND = ROOT / "data" / "finetune" / "qa_pilot" / "candidates.jsonl"
TRIAGE = ROOT / "data" / "finetune" / "qa_pilot" / "triage.jsonl"
OUT = ROOT / "data" / "finetune" / "qa_pilot" / "consolidated.jsonl"
REVIEW_DIR = ROOT / "docs" / "review" / "qa_pilot_pkg"

#: 돌아온 검수 파일. **출처를 이름으로 남긴다** — 사람인지 기계인지가 판단에 필요하다.
SOURCES = [
    ("팀원(김지혜)", "사람",
     REVIEW_DIR / "qa_pilot_review_part1.jsonl"),
    ("팀원(서유현)", "사람",
     REVIEW_DIR / "qa_pilot_review_part2.jsonl"),
    ("팀원(송채영)", "사람",
     REVIEW_DIR / "qa_pilot_review_part3.jsonl"),
    ("claude-검수보조", "LLM",
     REVIEW_DIR / "qa_pilot_review_part4.jsonl"),
    ("Codex 1차", "LLM",
     REVIEW_DIR / "qa_pilot_review_part5.jsonl"),
]

#: ★「완성본」은 **결정으로 쓰지 않는다**(축 단위 일괄 판정이라). 다만 B축 재띄어쓰기는
#:   원자료 대조가 실제로 들어간 것이라 **문장 후보로만** 받아 둔다.
COMPLETED_DIR = ROOT / "docs" / "review" / "qa_pilot_completed_20260827"


def _load(path) -> list[dict]:
    p = pathlib.Path(path)
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def main() -> int:
    ap = argparse.ArgumentParser(description="검수 결과를 현재 후보 위에 모은다")
    ap.add_argument("--report", action="store_true")
    args = ap.parse_args()

    cand = {r["item_id"]: r for r in _load(CAND)}
    tri = {r["item_id"]: r for r in _load(TRIAGE)}
    if not tri:
        raise SystemExit("규칙 사전분류가 없습니다: python -m scripts.review.triage_qa_pilot")

    #: 출처별 검수를 모은다. **현재 후보에 없는 항목은 버린다** — 없는 것을 근거로 못 쓴다.
    reviews: dict[str, list[dict]] = collections.defaultdict(list)
    dropped = collections.Counter()
    for name, kind, path in SOURCES:
        for r in _load(path):
            if r["item_id"] not in cand:
                dropped[name] += 1
                continue
            if not r.get("decision"):
                continue
            reviews[r["item_id"]].append({
                "출처": name, "종류": kind,
                "decision": r["decision"],
                "reason": r.get("reason") or "",
                "edited_answer": r.get("edited_answer") or "",
                "note": r.get("note") or "",
            })

    #: 「완성본」의 B축 문장만 **후보로** 받아 둔다. 결정은 안 받는다.
    completed_text: dict[str, str] = {}
    for f in sorted(COMPLETED_DIR.glob("qa_pilot_review_part*_completed.jsonl")):
        for r in _load(f):
            if r.get("axis") == "B" and (r.get("edited_answer") or "").strip() \
                    and r["item_id"] in cand:
                completed_text.setdefault(r["item_id"], r["edited_answer"].strip())

    rows = []
    for iid, c in cand.items():
        t = tri.get(iid, {})
        revs = reviews.get(iid, [])
        proposed = t.get("proposed_decision") or ""
        decs = {r["decision"] for r in revs}

        if revs and proposed:
            grade = "agreed" if decs == {proposed} else "disputed"
        elif revs:
            grade = "review_only"
        elif proposed:
            grade = "rule_only"
        else:
            grade = "untouched"

        #: 사람이 **반드시** 봐야 하는가. 등급이 갈리거나 아무 근거가 없으면 그렇다.
        needs_human = grade in ("disputed", "review_only", "untouched")

        #: 문장 후보를 모은다 — **고르지는 않는다.** 사람이 고른다.
        texts = []
        if t.get("proposed_answer"):
            texts.append({"출처": "규칙 제안", "문장": t["proposed_answer"]})
        for r in revs:
            if r["edited_answer"]:
                texts.append({"출처": r["출처"], "문장": r["edited_answer"]})
        if iid in completed_text:
            texts.append({"출처": "Codex 원자료재감사(B축 문장만)", "문장": completed_text[iid]})

        rows.append({
            "item_id": iid,
            "axis": c["axis"],
            "stratum": c["stratum"],
            "grade": grade,
            "needs_human": needs_human,
            "규칙": {
                "제안": proposed,
                "제안근거": t.get("proposed_why", ""),
                "결함": t.get("defects", []),
                "대조": t.get("checks", []),
            },
            "검수": revs,
            "문장후보": texts,
            #: ★언제나 비어 있다. 사람이 채운다.
            "decision": "", "edited_answer": "", "note": "",
        })

    if not args.report:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        with OUT.open("w", encoding="utf-8", newline="\n") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        print(f"작성: {OUT}  {len(rows)}건")

    print()
    print("★버려진 검수 — 현재 후보에 없는 옛 항목(근거로 못 쓴다)")
    for k, v in dropped.most_common():
        print(f"   {k:18s} {v:3d}건")
    print()
    print("등급")
    g = collections.Counter(r["grade"] for r in rows)
    for k in ("agreed", "disputed", "review_only", "rule_only", "untouched"):
        print(f"   {k:12s} {g.get(k, 0):3d}")
    print()
    nh = [r for r in rows if r["needs_human"]]
    print(f"★사람이 반드시 봐야 하는 것: {len(nh)} / {len(rows)}")
    print("   축별:", dict(collections.Counter(r["axis"] for r in nh)))
    print()
    print("불일치 내역(규칙 제안 ↔ 검수 결정)")
    for (p, d), n in collections.Counter(
            (r["규칙"]["제안"], "/".join(sorted({x["decision"] for x in r["검수"]})))
            for r in rows if r["grade"] == "disputed").most_common():
        print(f"   규칙={p:3s} 검수={d:8s} {n:3d}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
