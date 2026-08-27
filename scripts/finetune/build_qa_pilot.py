# -*- coding: utf-8 -*-
"""승인 QA 파일럿 **300건** 후보를 만든다 — 05D §3-2-1 의 1단계.

    A 180  기존 `exclusion_queries` 212쌍에서 (질의↔정답조항이 이미 확인된 것)
    B  60  S7.1 승인 OCR facts 850건에서
    C  60  기권 4종 × 15건 (판례 골든셋 114 + 결손 주입)

★**이 스크립트는 「초안」만 만든다. 정답을 만들지 않는다.**
  모든 항목은 `decision: ""` 로 나가고 사람이 채운다. 05D §3-3 —
  모델·기계 출력을 검수 없이 학습에 넣지 않는다.

★**A 를 「문서 × KCD 무차별 조합」으로 만들지 않는다**(코덱스 지적 2026-08-26).
  그러면 정답이 확인되지 않은 쌍이 대량으로 섞여 **오답 라벨**이 된다.
  정답 조항이 이미 확인된 212쌍에서만 뽑는다.

사용:
    python -m scripts.pg status          # "연결 OK" 확인 (A 축이 DB 를 읽는다)
    python -m scripts.finetune.build_qa_pilot
    python -m scripts.finetune.build_qa_pilot --out data/finetune/qa_pilot/candidates.jsonl
"""

from __future__ import annotations

import argparse
import json
import pathlib
import random
import sys
from db.postgres.pgvector_index import get_conn

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SEED = 20260826

#: 근거 본문 최소 길이. 이보다 짧으면 제목만 뽑힌 조항이라 판단 근거가 못 된다.
_MIN_EVIDENCE_CHARS = 120

#: A축 층별 상한과 전체 판정 호출 상한. 실측 0.11초/건.
#: ★2026-08-27 — 파일럿 300건에서 본생산으로 넘어가며 올렸다.
#:   층 7개 × 200 = 1,400 이 상한이고, 희소층(`product_not_matched` 등)은
#:   못 채우는 만큼 큰 층에서 메운다. 16,260 조합이 있으므로 재료는 남는다.
_PER_CELL = 200
_MAX_PRECHECKS = 12000
OUT_DEFAULT = ROOT / "data" / "finetune" / "qa_pilot" / "candidates.jsonl"

#: ★결손 4종 — 05D §3-2-1. 「조건 제거」만으로는 부족해서 **대체 근거 부재**까지 검사한다.
#: ★★두 종류는 **실제 조항을 붙인다.** 「조항은 있는데 쓸 수 없다」가 본질인데
#:   근거 0건으로 내보내면 검수자가 볼 것이 없다(2026-08-26 실측으로 고침).
#:   DB 실측(s6): `citation_eligible=false` 발생 265 · `parse_status IS NULL` 발생 7,045.
#:   ★`parse_status != 'ok'` 인 행은 **하나도 없다** — 있는 것은 `ok` 아니면 NULL 이다.
#:     그래서 설명 문구도 「ok 가 아니다」가 아니라 「비어 있다」로 적는다.
ABSTAIN_KINDS = [
    ("no_evidence", "근거 0건",
     "검색에서 인용 가능한 조항이 한 건도 나오지 않았다", None),
    ("ambiguous_product", "판본 모호",
     "같은 시점에 적용 가능한 상품이 여럿이라 하나로 좁혀지지 않는다", None),
    #: ★★2026-08-27 — 어제까지 이 층은 `parse_status IS NULL` 을 썼는데
    #:   다른 세션이 게이트를 다시 돌려 **그런 행이 0건이 됐다**
    #:   (어제 발생 7,045 → 오늘 0. 대신 `citation_eligible IS NULL` 9,228 이 생겼다).
    #:   없는 상태를 근거로 달아 둘 수 없어 **지금 실재하는 상태**로 바꾼다.
    ("document_not_reliable", "게이트 미기입",
     "찾은 조항의 인용 가부(`citation_eligible`)가 정해지지 않아 판정 근거로 쓸 수 없다",
     "o.citation_eligible IS NULL"),
    ("citation_unverified", "인용 불가 표시",
     "찾은 조항이 인용 불가(`citation_eligible=false`)로 표시돼 있다",
     "o.citation_eligible IS FALSE"),
]


#: ★보험사 표기가 두 갈래다 — 조항 색인은 **한글**(`현대해상`), 승인 OCR 사실은
#:   **슬러그**(`hyundaimarine`). 검수자가 같은 회사를 다른 회사로 읽는다.
#:   대응표는 **추측이 아니라** `data/raw/manifests/<슬러그>.jsonl` 안의 실제 한글값을 세어
#:   만들었다(`config/insurer_slug_map.json`). 없는 슬러그는 **그대로 둔다** — 지어내지 않는다.
_SLUG_MAP = json.loads((ROOT / "config/insurer_slug_map.json").read_text(encoding="utf-8"))


def _insurer(v):
    return _SLUG_MAP.get(v, v)


def _pg():
    import psycopg
    from app.core.config import get_settings
    #: ★★`get_conn()` 을 쓴다 — **직접 `psycopg.connect` 하면 `search_path` 가 안 걸린다**
    #:   (2026-08-26). 조항 색인이 `insurance_real.vec` 로 옮겨진 뒤
    #:   맨이름 SQL 이 `relation "policy_clause_chunk" does not exist` 로 깨진다.
    #:   스키마를 정하는 곳은 한 군데다.
    return get_conn()


def _clause_schema(cur) -> str:
    """`policy_clause_content` 가 **어느 스키마에 있는지** 찾아서 돌려준다.

    ★스키마를 박아 두지 않는다. 2026-08-26 에 다른 세션이 `public` → `vec` 로 옮겼고
      박아 뒀던 쿼리가 `UndefinedTable` 로 죽었다. 위치는 **물어본다.**
    """
    cur.execute(
        "SELECT table_schema FROM information_schema.tables"
        " WHERE table_name='policy_clause_content' ORDER BY table_schema LIMIT 1")
    row = cur.fetchone()
    if not row:
        raise SystemExit(
            "policy_clause_content 테이블을 찾지 못했습니다." + chr(10)
            + "  PGVECTOR_DSN 이 가리키는 DB 에 조항 색인이 있는지 확인하세요:" + chr(10)
            + "    python -m scripts.pg status")
    return row[0]


#: A축 질의 재료 — 질병기호와 사람이 쓸 법한 서술.
_KCD_TEXT = {
    "F32": "우울증으로 입원 치료를 받았습니다",
    "S72.0": "대퇴골이 부러져 수술했습니다",
    "E66": "비만 치료를 받았습니다",
    "N39": "요실금 치료를 받았습니다",
    "I21": "급성심근경색으로 입원했습니다",
    "C50": "유방암 수술을 받았습니다",
    "M51": "추간판탈출증으로 시술받았습니다",
    "K80": "담석증으로 수술했습니다",
    "O00": "임신 관련으로 입원했습니다",
    "Q00": "선천성 질환 치료를 받았습니다",
    "J20": "급성기관지염으로 통원 치료했습니다",
    "K60": "치핵 수술을 받았습니다",
}


def _sale_starts() -> dict[str, str]:
    """`sha256 → 판매개시일`. 매니페스트에서 실측으로 만든다."""
    out: dict[str, str] = {}
    for p in (ROOT / "data/raw/manifests").glob("*.jsonl"):
        for line in p.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            if r.get("sale_start") and r.get("sha256"):
                out[r["sha256"]] = r["sale_start"]
    return out


def _enrolled_after(sale_start: str) -> str:
    """판매개시 **다음 달 15일**을 가입일로 쓴다 — 개시 당일 경계 문제를 피한다."""
    y, m = int(sale_start[:4]), int(sale_start[4:6])
    m += 1
    if m > 12:
        y, m = y + 1, 1
    return f"{y:04d}{m:02d}15"


#: 오늘(생성일). 사고일이 미래가 되지 않도록 여기서 자른다.
_TODAY = "20260826"


def _incident_after(enrolled_on: str) -> str:
    """사고일은 **가입일 뒤**여야 한다.

    ★★2026-08-26 결함 — `incident_on` 을 `"20240501"` 로 **박아 두었다.**
      2025년 판매 상품은 가입일이 2025-08-15 인데 사고일이 2024-05-01 로 나가
      **사고가 가입보다 먼저인** 항목이 만들어졌다. 검수자가 판단할 수 없는 질문이다.
      (코덱스 검수에서 잡혔다. 상수는 이런 식으로 조용히 틀린다.)
    """
    y, m, d = int(enrolled_on[:4]) + 1, enrolled_on[4:6], enrolled_on[6:8]
    cand = f"{y:04d}{m}{d}"
    return cand if cand <= _TODAY else _TODAY


def build_a(n: int, rnd: random.Random) -> list[dict]:
    """A축 — **엔진이 실제로 낸 판정**의 고객 문장을 검수한다.

    ★★2026-08-26 재설계. 전에는 검색 정답셋(`retrieval_probes.json`)에서 뽑아
      「이 조항이 근거로 맞나」를 물었다. 그건 **검색 평가**이지 우리가 학습시킬
      것이 아니다. 05D §3-2-1 이 요구한 층은 `verdict × reason_code` 인데
      검색 정답셋에는 **엔진 판정이 붙어 있지 않아** 그 층을 만들 수 없었다.

    ★코덱스 결론(2026-08-26)대로 **엔진 판정을 입력으로 준다** —
      `verdict`·`reason_code`·`citations` 는 이미 정해진 것이고,
      사람이 보는 것은 **그 위에 얹힌 고객 문장이 맞는가**다.
      그래야 규칙엔진의 한계가 결함이 아니라 설계가 된다.

    ★층이 **실제로 어떻게 갈리는지 먼저 쟀다**(1,400건 쓸기, 2026-08-26):
        needs_expert:no_evidence            841
        unlikely:excluded_by_clause         263
        needs_expert:citation_unverified    137
        needs_expert:no_version_at_date      69
        needs_documents:exception_applies    56
        needs_expert:document_not_reliable   19
        needs_expert:product_not_matched     14
        needs_expert:ambiguous_product_line   1
      ★`likely_covered` 는 **한 건도 없다.** 실손은 면책 대조라 엔진이
        「보장됩니다」를 단정하지 않는다 — 없는 층을 만들어 내지 않는다.
      ★밑비율대로 뽑으면 `no_evidence` 가 60%를 먹는다. **층을 고르게** 채운다.
    """
    from fastapi.testclient import TestClient

    from app.main import create_app

    client = TestClient(create_app("customer"))
    sale = _sale_starts()
    led = [json.loads(l) for l in
           (ROOT / "config/confirmed_documents.jsonl").read_text(encoding="utf-8").splitlines()
           if l.strip()]
    led = [d for d in led if sale.get(d.get("sha256"))]
    rnd.shuffle(led)

    codes = list(_KCD_TEXT)
    buckets: dict[str, list[dict]] = {}
    seen: set[str] = set()
    tried = no_date = 0
    for i, d in enumerate(led):
        for k, code in enumerate(codes):
            sha = d["sha256"]
            body = {
                "insurer": d["insurer"], "product_name": d.get("product_name"),
                "enrolled_on": (_enr := _enrolled_after(sale[sha])),
                "incident_on": _incident_after(_enr),
                "kcd_codes": [code], "condition_text": _KCD_TEXT[code],
            }
            tried += 1
            r = client.post("/v1/prechecks", json=body)
            if r.status_code != 200:
                continue
            j = r.json()
            cell = f"{j.get('verdict')}:{j.get('reason_code')}"
            key = f"{sha[:12]}:{code}"
            if key in seen:
                continue
            seen.add(key)
            buckets.setdefault(cell, []).append((body, j, d, code))
        #: 층마다 넉넉히 모이면 멈춘다 — 1,400건 쓸기에 158초 걸렸다.
        #: ★★상한을 층당 40 으로 두고 `>= n*2` 를 요구했더니 **도달할 수 없었다**
        #:   (층 8개 × 40 = 320 < 360). 16,260회를 다 도는 30분짜리 루프가 됐다.
        #:   상한과 목표를 **같은 자로** 맞춘다 — 층당 `_PER_CELL`, 합이 n 이면 충분하다.
        if (len(buckets) >= 6
                and sum(min(len(v), _PER_CELL) for v in buckets.values()) >= n):
            break
        if tried >= _MAX_PRECHECKS:
            print(f"[A축] 판정 {tried}회에서 멈춘다 — 층이 더 안 늘었다", flush=True)
            break

    #: 층을 **고르게** 채운다. 모자란 층은 있는 만큼만 쓰고 나머지는 큰 층에서 메운다.
    order = sorted(buckets, key=lambda c: -len(buckets[c]))
    picked: list[tuple] = []
    round_i = 0
    while len(picked) < n and any(len(buckets[c]) > round_i for c in order):
        for c in order:
            if len(picked) >= n:
                break
            if len(buckets[c]) > round_i:
                picked.append(buckets[c][round_i])
        round_i += 1

    out: list[dict] = []
    for body, j, d, code in picked:
        cites = j.get("citations") or []
        ev = []
        if cites:
            c0 = cites[0]
            #: ★인용된 조항은 `parse_status=="ok"` + `citation_eligible is True`
            #:   게이트를 **통과한 것만** 나온다(`app/core/domain/eligibility.py`).
            #:   그래서 여기 값은 추정이 아니라 그 계약에서 따라온다.
            ev = [{
                "clause_id": c0.get("clause_id"), "sha12": d["sha256"][:12],
                "insurer": _insurer(d["insurer"]), "qualified_no": c0.get("qualified_no"),
                "section": c0.get("section"), "title": c0.get("title"),
                "page_from": c0.get("page_from"), "page_to": c0.get("page_to"),
                "parse_status": "ok", "citation_eligible": True,
                "text": c0.get("quote") or "",
            }]
        out.append({
            "axis": "A",
            "item_id": f"A:{d['sha256'][:12]}:{code}",
            "stratum": f"A:{j.get('verdict')}:{j.get('reason_code')}",
            "question": (f"{_insurer(d['insurer'])} 「{d.get('product_name')}」에"
                         f" {body['enrolled_on'][:4]}년 {int(body['enrolled_on'][4:6])}월에 가입했습니다."
                         f" {_KCD_TEXT[code]}({code}). 보장되나요?"),
            "request": body,
            #: ★**엔진 판정은 입력이다.** 화면에서 결정 전에도 보여 준다.
            "engine": {"verdict": j.get("verdict"), "reason_code": j.get("reason_code"),
                       "abstained": j.get("abstained"), "citations": len(cites)},
            "draft_answer": j.get("message") or "",
            "draft_source": "precheck_engine",
            "evidence": ev,
            "ask": ("**이 문장이 고객에게 나가도 되나?** ★판정(아래 엔진 판정)은 이미 정해진 것입니다 — "
                    "바꾸지 마세요. 보는 것은 **그 판정과 근거에 문장이 맞는가**입니다"),
            "decision": "", "note": "",
        })
    print(f"[A축] 판정 {tried}회 · 층 {len(buckets)}개 · 뽑음 {len(out)}건", flush=True)
    for c in order:
        print(f"       {c:44s} 확보 {len(buckets[c]):4d}", flush=True)
    return out[:n]


def build_b(n: int, rnd: random.Random) -> list[dict]:
    """B축 — 사람이 이미 승인한 OCR 표 사실."""
    base = ROOT / "data/work/s7_1_approved_facts"
    facts = [json.loads(l) for l in (base / "approved_facts.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    chunks = {c["content_hash"]: c.get("text", "")
              for c in (json.loads(l) for l in (base / "chunks.jsonl").read_text(encoding="utf-8").splitlines() if l.strip())}
    facts = [f for f in facts if f.get("serving_eligible") and chunks.get(f.get("content_hash"))]
    rnd.shuffle(facts)

    #: ★그냥 앞에서 n개 자르면 618/850 인 `표준형`이 층을 다 먹는다. 층별로 돌아가며 뽑는다.
    buckets: dict[str, list] = {}
    for f in facts:
        key = f"{f.get('plan') or '기타'}|{(f.get('service') or ['기타'])[0]}"
        buckets.setdefault(key, []).append(f)
    order, picked = sorted(buckets), []
    while len(picked) < n and any(buckets[k] for k in order):
        for k in order:
            if buckets[k] and len(picked) < n:
                picked.append(buckets[k].pop())
    facts = picked

    out = []
    for f in facts[:n]:
        svc = (f.get("service") or ["해당 서비스"])[0]
        plan = f.get("plan") or "해당 유형"
        out.append({
            "axis": "B",
            "item_id": f"B:{f['candidate_id'][-12:]}",
            #: ★`category` 는 850건 **전부 `missed`** 라 층이 안 나뉜다(실측).
            #:   실제로 갈리는 건 `plan`(5종) × `service`(3종)다 — 그걸 층으로 쓴다.
            "stratum": f"B:{plan}:{svc}",
            "question": f"{plan}에서 {svc} 자기부담금은 얼마인가요?",
            "draft_answer": f"{f.get('amount_formula') or '(금액 미상)'} 입니다.",
            "draft_source": "approved_ocr_fact",
            "evidence": [{
                "clause_id": f"{f.get('document_sha12')}/S7.1승인표사실#{f['content_hash'][:8]}",
                "content_hash": f["content_hash"], "sha12": f.get("document_sha12"),
                "insurer": _insurer(f.get("insurer")), "qualified_no": "S7.1 승인 OCR 사실",
                "section": f.get("category") or "", "title": plan,
                "page_from": f.get("page_1based"), "page_to": f.get("page_1based"),
                "parse_status": "ok", "citation_eligible": f.get("citation_eligible"),
                "text": chunks[f["content_hash"]],
            }],
            "ask": "표에서 읽은 금액이 **질문과 짝이 맞나**?",
            "decision": "", "note": "",
        })
    return out


def _unusable_clauses(cur, sch: str, cond: str, n: int) -> list[dict]:
    """근거로 **쓸 수 없다고 표시된** 실제 조항을 가져온다.

    ★지어내지 않는다 — 결손 상황을 흉내 내는 대신 **실제로 그 상태인 행**을 쓴다.
      본문 200자 미만은 제외한다(제목만 뽑힌 조항으로는 판단할 수 없다 — A축과 같은 이유).
    """
    cur.execute(
        f"""SELECT DISTINCT ON (o.content_hash)
                   o.content_hash, o.sha256, o.insurer, o.qualified_no, o.section,
                   o.title, o.page_from, o.page_to, o.parse_status, o.citation_eligible,
                   t.text
              FROM {sch}.policy_clause_occurrence o
              JOIN {sch}.policy_clause_content t ON t.content_hash = o.content_hash
             WHERE o.index_generation='s6' AND {cond} AND length(t.text) >= 200
             ORDER BY o.content_hash
             LIMIT %s""", (n,))
    cols = ("content_hash", "sha256", "insurer", "qualified_no", "section", "title",
            "page_from", "page_to", "parse_status", "citation_eligible", "text")
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def build_c(n: int, rnd: random.Random) -> list[dict]:
    """C축 — 기권이 정답인 사례. 4종을 고르게."""
    legal = json.loads((ROOT / "data/legal/human_review_queue.json").read_text(encoding="utf-8"))
    legal = [x for x in legal if x.get("verdict") in ("confirmed", "corrected")]
    rnd.shuffle(legal)

    per = max(1, n // len(ABSTAIN_KINDS))
    real: dict[str, list[dict]] = {}
    with _pg() as conn, conn.cursor() as cur:
        sch = _clause_schema(cur)
        for code, _ko, _why, cond in ABSTAIN_KINDS:
            if cond:
                real[code] = _unusable_clauses(cur, sch, cond, per)

    out: list[dict] = []
    for ki, (code, _ko, why, cond) in enumerate(ABSTAIN_KINDS):
        rows = real.get(code) or []
        if cond and len(rows) < per:
            #: ★모자라면 **모자란다고 말한다.** 다른 종류로 채우지 않는다.
            print(f"[C축] {code}: {per}건 요청 → 실제 {len(rows)}건만 있다", flush=True)
        for j in range(per):
            item = {
                "axis": "C",
                "item_id": f"C:{code}:{j:02d}",
                "stratum": f"C:{code}",
                "draft_answer": f"판정하지 않았습니다 — {why}.",
                "draft_source": f"abstain_real:{code}" if cond else f"abstain_synth:{code}",
                "evidence": [],
                "decision": "", "note": "",
            }
            if cond:
                if j >= len(rows):
                    continue
                c = rows[j]
                title = (c["title"] or c["qualified_no"] or "이 조항").strip()
                item["question"] = f"「{title}」 내용이 제 보험에 어떻게 적용되나요?"
                item["evidence"] = [{
                    "clause_id": f"{(c['sha256'] or '')[:12]}/{c['qualified_no']}#{c['content_hash'][:8]}",
                    "content_hash": c["content_hash"], "sha12": (c["sha256"] or "")[:12],
                    "insurer": _insurer(c["insurer"]), "qualified_no": c["qualified_no"],
                    "section": c["section"], "title": c["title"],
                    "page_from": c["page_from"], "page_to": c["page_to"],
                    "parse_status": c["parse_status"], "citation_eligible": c["citation_eligible"],
                    "text": c["text"],
                }]
                item["ask"] = ("**기권이 정답인가?** ★조항은 있지만 **근거로 쓸 수 없다고 표시**된 "
                               "항목입니다. 내용이 맞아 보여도 인용하면 안 되는지 봐 주세요")
            else:
                src = legal[(ki * per + j) % len(legal)] if legal else {}
                #: ★`holdings`·`issues` 는 **dict 의 list** 다(`{issue_id, 결론, 법리_요약}` /
                #:   `{issue_id, 쟁점문구}`). 문자열로 가정하고 슬라이스했다가 `KeyError` 로 죽었다.
                hold_list = src.get("holdings") or []
                hold = (hold_list[0].get("법리_요약", "") if isinstance(hold_list[0], dict)
                        else str(hold_list[0])) if hold_list else ""
                iss_list = src.get("issues") or []
                question = (iss_list[0].get("쟁점문구", "") if isinstance(iss_list[0], dict)
                            else str(iss_list[0])) if iss_list else "보장 여부를 알려주세요"
                item["question"] = question[:300]
                item["reference"] = {
                    "case_id": src.get("case_id"), "authority": src.get("authority_grade"),
                    "case_verdict": (hold_list[0].get("결론")
                                     if hold_list and isinstance(hold_list[0], dict) else None),
                    "holding": hold[:400]}
                item["ask"] = ("**기권이 정답인가?** ★대체 근거가 정말 없는지까지 봐 주세요 — "
                               "다른 조항이 답을 갖고 있으면 기권은 오답입니다")
            out.append(item)
    return out[:n]


def build_d(n: int, rnd: random.Random) -> list[dict]:
    """D축 — **이 조항이 질의의 근거로 맞나**(근거 적합성).

    ★A축과 과업이 다르다. A 는 「엔진 판정 위에 얹힌 **고객 문장**이 맞나」이고
      D 는 「이 **조항**이 근거로 맞나」다. 같은 축에 섞으면 모델이 두 과업을
      한 이름으로 배운다.

    ★재료는 검색 정답셋(`retrieval_probes.json`) 212질의 × gold 조항 = **844쌍**이다.
      팀원 다섯이 검수한 116건이 이미 이 축에 있고(복원분), 여기서 더 뽑아 채운다.
    """
    probes = json.loads((ROOT / "data/eval/retrieval_probes.json").read_text(encoding="utf-8"))
    pairs = []
    for q in probes["exclusion_queries"]:
        for h in (q.get("gold_eligible_ids") or q.get("gold_ids") or []):
            pairs.append((q, h))
    rnd.shuffle(pairs)

    #: 이미 복원해 둔 항목과 겹치지 않게 한다 — 같은 것을 두 번 검수시키지 않는다.
    restored = ROOT / "data/finetune/qa_pilot/candidates_restored.jsonl"
    seen = set()
    if restored.exists():
        for line in restored.read_text(encoding="utf-8").splitlines():
            if line.strip():
                seen.add(json.loads(line)["item_id"])

    out, short = [], 0
    with _pg() as conn, conn.cursor() as cur:
        sch = _clause_schema(cur)
        for q, h in pairs:
            if len(out) >= n:
                break
            iid = f"D:{q['probe_id']}:{h[:8]}"
            if iid in seen:
                continue
            cur.execute(f"SELECT text FROM {sch}.policy_clause_content WHERE content_hash=%s", (h,))
            row = cur.fetchone()
            if not row or len(row[0] or "") < _MIN_EVIDENCE_CHARS:
                short += 1
                continue
            cur.execute(
                "SELECT insurer, qualified_no, section, title, page_from, page_to,"
                "       parse_status, citation_eligible"
                f"  FROM {sch}.policy_clause_occurrence"
                " WHERE content_hash=%s AND sha256=%s AND index_generation='s6' LIMIT 1",
                (h, q["sha256"]))
            loc = cur.fetchone()
            if not loc:
                short += 1
                continue
            insurer, qno, section, title, pf, pt, pstatus, elig = loc
            out.append({
                "axis": "D",
                "item_id": iid,
                "stratum": f"D:{q.get('kind') or '기타'}",
                "question": q["query"],
                "draft_answer": f"「{title or qno}」 조항이 이 질의의 근거입니다.",
                "draft_source": "retrieval_gold",
                "evidence": [{
                    "clause_id": f"{q['sha12']}/{qno}#{h[:8]}",
                    "content_hash": h, "sha12": q["sha12"],
                    "insurer": _insurer(insurer), "qualified_no": qno, "section": section,
                    "title": title, "page_from": pf, "page_to": pt,
                    "parse_status": pstatus, "citation_eligible": elig,
                    "text": row[0],
                }],
                "ask": "이 **조항**이 질의의 근거로 맞나? ★답변 문장이 아니라 **근거 적합성**을 봅니다",
                "decision": "", "note": "",
            })
    if short:
        print(f"[D축] 본문이 짧거나 발생이 없어 버린 쌍: {short}건", flush=True)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="승인 QA 파일럿 300건 후보 생성")
    ap.add_argument("--out", default=str(OUT_DEFAULT))
    ap.add_argument("--a", type=int, default=180)
    ap.add_argument("--b", type=int, default=60)
    ap.add_argument("--c", type=int, default=60)
    ap.add_argument("--d", type=int, default=0, help="근거 적합성 축(옛 A축)")
    args = ap.parse_args()

    rnd = random.Random(SEED)
    rows: list[dict] = []
    for label, fn, want in (("A", build_a, args.a), ("B", build_b, args.b),
                            ("C", build_c, args.c), ("D", build_d, args.d)):
        if want <= 0:
            continue
        got = fn(want, rnd)
        #: ★모자라면 **모자란다고 말한다.** 채우려고 다른 데서 끌어오지 않는다.
        if len(got) < want:
            print(f"[경고] {label}축 {want}건 요청 → {len(got)}건만 만들어졌다", flush=True)
        rows.extend(got)

    out = pathlib.Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="\n") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    import collections
    print(f"작성: {out}  {len(rows)}건")
    print("  축별:", dict(collections.Counter(r["axis"] for r in rows)))
    print("  층별:", dict(collections.Counter(r["stratum"] for r in rows)))
    print("\n★전 항목 `decision` 은 비어 있다 — 사람이 채운다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
