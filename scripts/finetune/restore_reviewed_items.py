# -*- coding: utf-8 -*-
"""사람이 검수했는데 **후보 재생성으로 없어진 항목**을 되살린다.

    python -m scripts.pg status                     # "연결 OK" 확인
    python -m scripts.finetune.restore_reviewed_items

★★왜 필요한가 (2026-08-27)

    팀원 다섯이 300건을 검수해 돌려줬는데, 그 사이에 이쪽에서 **A축을
    검색 정답셋 → 엔진 판정 기반으로 갈아엎었다.** 그래서 176건이
    「현재 후보에 없는 항목」이 되어 근거로 못 쓰게 됐다.
    **사람 손이 176번 들어간 일을 날린 것이고, 원인은 이쪽에 있다.**

    다행히 옛 `item_id` 에 복원에 필요한 것이 다 들어 있다.

        A:593987e051e2:alt:2111479a
          └ probe_id ────────┘ └ content_hash 앞 8자리
        B:823789501858
          └ approved fact candidate_id 뒤 12자리

    실측(2026-08-27) — A축 **144/144** 가 `retrieval_probes.json` 에서 되살아난다.

★★되살린 것은 **다른 축(`D`)으로 둔다.** 과업이 다르기 때문이다.

        새 A축   「엔진 판정 위에 얹힌 **고객 문장**이 맞나」   ← 생성 대상
        옛 A축   「이 **조항**이 질의의 근거로 맞나」          ← 근거 적합성

    같은 축에 섞으면 모델이 두 과업을 한 이름으로 배운다. `D` 로 갈라 둔다.
    ★B축은 과업이 같으므로 `B` 그대로 되살린다.

★출력은 별도 파일이다 — `build_qa_pilot` 이 `candidates.jsonl` 을 덮어써도 안 날아간다.
  뒤 단계는 `candidates_all.jsonl`(둘을 합친 것)을 본다.
"""

from __future__ import annotations

import argparse
import collections
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

REVIEW_DIR = ROOT / "docs" / "review" / "qa_pilot_pkg"
CAND = ROOT / "data" / "finetune" / "qa_pilot" / "candidates.jsonl"
OUT = ROOT / "data" / "finetune" / "qa_pilot" / "candidates_restored.jsonl"
MERGED = ROOT / "data" / "finetune" / "qa_pilot" / "candidates_all.jsonl"

_OLD_A = re.compile(r"^A:([0-9a-f]{12}:(?:own|alt)):([0-9a-f]{8})$")
_OLD_B = re.compile(r"^B:([0-9a-f]{12})$")

#: 근거 본문이 이보다 짧으면 제목만 뽑힌 조항이라 판단 근거가 못 된다(build_qa_pilot 과 같은 값).
_MIN_EVIDENCE_CHARS = 120


def _load(p) -> list[dict]:
    p = pathlib.Path(p)
    return ([json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
            if p.exists() else [])


def _pg():
    #: ★★`get_conn()` 을 쓴다 — **직접 `psycopg.connect` 하면 `search_path` 가 안 걸린다.**
    #:   조항 색인은 `PGVECTOR_SCHEMA`(운영은 `vec`)에 있는데, 맨이름 SQL 은
    #:   `public` 만 보고 「테이블이 없다」로 죽거나 **빈 결과를 정상처럼** 돌려준다.
    #:   같은 이유로 2026-08-26 에 4곳을 고쳤는데 여기가 남아 있었다.
    from db.postgres.pgvector_index import get_conn

    return get_conn()


def _clause_schema(cur) -> str:
    cur.execute(
        "SELECT table_schema FROM information_schema.tables"
        " WHERE table_name='policy_clause_content' ORDER BY table_schema LIMIT 1")
    row = cur.fetchone()
    if not row:
        raise SystemExit("policy_clause_content 를 찾지 못했습니다 — python -m scripts.pg status")
    return row[0]


def main() -> int:
    ap = argparse.ArgumentParser(description="없어진 검수 항목 복원")
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args()

    from scripts.finetune.build_qa_pilot import _insurer

    have = {r["item_id"] for r in _load(CAND)}
    missing: dict[str, dict] = {}
    for f in sorted(REVIEW_DIR.glob("qa_pilot_review_part*.jsonl")):
        for r in _load(f):
            if r["item_id"] not in have and r.get("decision"):
                missing.setdefault(r["item_id"], r)
    print(f"되살릴 대상 {len(missing)}건 · 축별 "
          f"{dict(collections.Counter(r['axis'] for r in missing.values()))}")

    probes = json.loads((ROOT / "data/eval/retrieval_probes.json").read_text(encoding="utf-8"))
    by_probe = {q["probe_id"]: q for q in probes["exclusion_queries"]}

    facts_dir = ROOT / "data/work/s7_1_approved_facts"
    facts = {f["candidate_id"][-12:]: f for f in _load(facts_dir / "approved_facts.jsonl")}
    chunks = {c["content_hash"]: c.get("text", "")
              for c in _load(facts_dir / "chunks.jsonl")}

    out: list[dict] = []
    skipped = collections.Counter()

    with _pg() as conn, conn.cursor() as cur:
        sch = _clause_schema(cur)
        for iid, rv in missing.items():
            m = _OLD_A.match(iid)
            if m:
                pid, hpre = m.group(1), m.group(2)
                q = by_probe.get(pid)
                if not q:
                    skipped["probe 없음"] += 1
                    continue
                #: ★옛 id 가 **어느 조항이었는지**를 담고 있다. 그 조항을 그대로 찾는다 —
                #:   다른 gold 를 골라 오면 사람이 검수한 대상과 달라진다.
                cur.execute(
                    f"SELECT content_hash, text FROM {sch}.policy_clause_content"
                    " WHERE content_hash LIKE %s LIMIT 1", (hpre + "%",))
                row = cur.fetchone()
                if not row or len((row[1] or "")) < _MIN_EVIDENCE_CHARS:
                    skipped["조항 본문 없음/짧음"] += 1
                    continue
                h, text = row
                cur.execute(
                    "SELECT insurer, qualified_no, section, title, page_from, page_to,"
                    "       parse_status, citation_eligible"
                    f"  FROM {sch}.policy_clause_occurrence"
                    " WHERE content_hash=%s AND sha256=%s AND index_generation='s6' LIMIT 1",
                    (h, q["sha256"]))
                loc = cur.fetchone()
                if not loc:
                    skipped["발생 없음"] += 1
                    continue
                insurer, qno, section, title, pf, pt, pstatus, elig = loc
                out.append({
                    "axis": "D",
                    "item_id": iid,
                    #: 층은 옛 이름을 유지한다 — 사람이 검수한 단위와 같아야 대조가 된다.
                    "stratum": rv.get("stratum") or f"D:{q.get('kind') or '기타'}",
                    "question": q["query"],
                    "draft_answer": rv.get("draft_answer") or f"「{title or qno}」 조항이 이 질의의 근거입니다.",
                    "draft_source": "retrieval_gold",
                    "evidence": [{
                        "clause_id": f"{q['sha12']}/{qno}#{h[:8]}",
                        "content_hash": h, "sha12": q["sha12"],
                        "insurer": _insurer(insurer), "qualified_no": qno, "section": section,
                        "title": title, "page_from": pf, "page_to": pt,
                        "parse_status": pstatus, "citation_eligible": elig,
                        "text": text,
                    }],
                    "ask": ("이 **조항**이 질의의 근거로 맞나? ★답변 문장이 아니라 "
                            "**근거 적합성**을 봅니다"),
                    "restored_from": "옛 A축(검색 정답셋) — 후보 재생성으로 없어졌던 항목",
                    "decision": "", "note": "",
                })
                continue

            m = _OLD_B.match(iid)
            if m:
                f = facts.get(m.group(1))
                if not f or not chunks.get(f.get("content_hash")):
                    skipped["승인 fact 없음"] += 1
                    continue
                svc = (f.get("service") or ["해당 서비스"])[0]
                plan = f.get("plan") or "해당 유형"
                out.append({
                    "axis": "B",
                    "item_id": iid,
                    "stratum": rv.get("stratum") or f"B:{plan}:{svc}",
                    "question": f"{plan}에서 {svc} 자기부담금은 얼마인가요?",
                    "draft_answer": f"{f.get('amount_formula') or '(금액 미상)'} 입니다.",
                    "draft_source": "approved_ocr_fact",
                    "evidence": [{
                        "clause_id": f"{f.get('document_sha12')}/S7.1승인표사실#{f['content_hash'][:8]}",
                        "content_hash": f["content_hash"], "sha12": f.get("document_sha12"),
                        "insurer": _insurer(f.get("insurer")),
                        "qualified_no": "S7.1 승인 OCR 사실",
                        "section": f.get("category") or "", "title": plan,
                        "page_from": f.get("page_1based"), "page_to": f.get("page_1based"),
                        "parse_status": "ok", "citation_eligible": f.get("citation_eligible"),
                        "text": chunks[f["content_hash"]],
                    }],
                    "ask": "표에서 읽은 금액이 **질문과 짝이 맞나**?",
                    "restored_from": "옛 B축 — 후보 재생성으로 없어졌던 항목",
                    "decision": "", "note": "",
                })
                continue
            skipped["id 형식을 모름"] += 1

    pathlib.Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    with pathlib.Path(args.out).open("w", encoding="utf-8", newline="\n") as f:
        for r in out:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    #: 합친 파일을 만든다 — 뒤 단계는 이것을 본다.
    base = _load(CAND)
    with MERGED.open("w", encoding="utf-8", newline="\n") as f:
        for r in base + out:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    print(f"복원 {len(out)}건 → {pathlib.Path(args.out).name}")
    print(f"합본 {len(base) + len(out)}건 → {MERGED.name}")
    print("  축별:", dict(collections.Counter(r["axis"] for r in out)))
    if skipped:
        #: ★조용히 버리지 않는다. 몇 건을 왜 못 살렸는지 말한다.
        print("  못 살린 것:", dict(skipped))
    return 0


if __name__ == "__main__":
    sys.exit(main())
