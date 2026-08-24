"""전처리 구조화 품질 감사 — **정답셋 없이** 도는 무참조 지표 (11단계 중 11의 앞단).

★왜 필요한가

    두 파이프라인이 같은 이름의 "커버리지"를 쓰는데 재는 게 달랐다.
      · `_3rd_project_4` `coverage_pct` — 첫 재매칭 헤더 이후 정제텍스트의 **내부 재수록률**.
        구조가 망가져도 100%가 나온다.
      · v5 의 목차 제외율 — **목차를 덜 뺄수록 올라간다.**
    둘 다 **"제대로 나눴나"를 못 잰다.**

    2026년 기준 연구를 확인해도 전처리 단독 품질 지표에는 표준이 없다
    (청킹 평가 논문들은 경계 정확도를 독립적으로 재지 않고 검색 성능으로만 잰다).
    그래서 여기서 정의한다.

★설계 근거

    OmniDocBench(CVPR'25 · v1.6 2026-04) 의 **MGAM** — 정답은 고정하고 예측 쪽
      granularity 만 맞춘다. v5 는 조 단위, 3rd 는 항 단위 1,500자라 그대로는 비교가
      성립하지 않는다. 그래서 T 축은 **문자 8-gram 집합**으로 환원해 단위를 지운다.
    HiCoBERT(2026) — 계층적 법률 문서 분할에 Boundary F1 / Span F1.
    DOCR-Inspector(2025) — 정답 없이 **오류 유형별로 센다.** 단일 점수를 만들지 않는다.

★단일 종합 점수를 만들지 않는다
    정답셋이 없으면 가중치를 정당화할 수 없다(코덱스 지적). 유형별로 세어서 낸다.

실행:
    python -m scripts.eval.struct_audit --pipeline v5
    python -m scripts.eval.struct_audit --pipeline v5 --sha 16b227ff95b8 --verbose
"""

from __future__ import annotations

import argparse
import collections
import json
import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_EXTRACTED = _ROOT / "data" / "extracted"
_STRUCTURED = _ROOT / "data" / "structured"

#: 문자 n-gram 길이. 8자면 한국어에서 우연 일치가 거의 없고 계산도 견딘다.
SHINGLE = 8

_WS = re.compile(r"\s+")
#: 사용자 정의 영역(PUA) — 복구 못 한 글리프. 그대로 색인하면 안 된다.
_PUA = re.compile(r"[-\U000F0000-\U000FFFFD]")
#: 인코딩이 깨진 자리.
_REPL = re.compile(r"�")
#: 부록·별표 마커. 이게 조항 **안**에 있으면 그 조가 부록을 삼킨 것이다.
#:
#: ★★**줄머리에 있는 것만** 인정한다. 초안은 위치를 안 봐서 과탐했다 —
#:   표본 5건 중 3건이 **문장 안 인용**이었다:
#:     "「국민건강보험 요양급여의 기준에 관한 규칙」제9조([별표1] 비급여대상)에 의한 …"
#:     "[별표2] "특정부위 분류표" 중에서 회사가 지정한 부위에 발생한 질병 …"
#:   진짜 부록은 **새 줄에서 시작**한다. 조 머리·항 마커와 같은 원칙이다.
#:   (코덱스도 "본문 내 단순 `[별표] 참조`는 제외해야 한다"고 지적했다.)
_ANNEX = re.compile(
    r"^[ \t]{0,6}(?:[\[［]\s*(?:붙\s*임|별\s*표|별\s*첨)|[＜<]\s*붙\s*임"
    r"|(?:질병|재해|특정부위|특정질병)\s*분류표)",
    re.MULTILINE,
)
#: 마커 뒤에 이만큼 이상 남아 있어야 "삼켰다"고 본다. 제목 한 줄만 있는 건 경계 표시다.
ANNEX_MIN_TAIL = 300


def _norm(t: str) -> str:
    return _WS.sub("", t)


def _shingles(t: str) -> set[str]:
    t = _norm(t)
    if len(t) < SHINGLE:
        return {t} if t else set()
    return {t[i:i + SHINGLE] for i in range(len(t) - SHINGLE + 1)}


# ────────────────────────────────────────────────────────────────
# T 축 — 원문 보존. `coverage_pct` 를 대체하는 정직한 버전
# ────────────────────────────────────────────────────────────────
def text_fidelity(source: str, blocks: list[str]) -> dict:
    """T1 재현율 / T2 정밀도.

    ★페이지가 아니라 **문자**로 잰다. 빈 페이지와 면책 조항 페이지를
      같은 1쪽으로 세면 안 된다(그래서 v5 의 "97.1%" 는 후한 값이었다 — 문자로는 83.5%).
    ★집합이라 **중복은 1회만** 센다. 같은 조항을 여러 번 담아도 재현율이 오르지 않는다.
    """
    g = _shingles(source)
    p: set[str] = set()
    for b in blocks:
        p |= _shingles(b)
    if not g:
        return {"T1_recall": 0.0, "T2_precision": 0.0}
    inter = len(g & p)
    return {"T1_recall": inter / len(g),
            "T2_precision": inter / len(p) if p else 0.0}


# ────────────────────────────────────────────────────────────────
# S 축 — 구조 모순. ★코덱스가 설계한 신호들. 내 초안(문장 끝 부호)보다 낫다
# ────────────────────────────────────────────────────────────────
#: ★내 초안 `B1 문장중간 절단`(종결어미+마침표)은 **폐기했다.**
#:   v5 0.381 / 3rd 0.379 로 신호가 없었다. 조항이 표·목록으로 끝나는 경우가 많아
#:   정상까지 절단으로 세기 때문이다. 아래 신호들은 실제로 갈린다(§실측).
def structure_faults(blocks: list[dict]) -> dict:
    """조 블록 목록에서 구조 모순을 센다.

    blocks 원소: {no: int|None, kind: str, title: str, text: str}
      kind — 번호 체계(`article` / `numbered`). ★섞어서 비교하면 안 된다.
             `제5조` 다음 `4-1.` 을 역행으로 세면 거짓 경고가 쏟아진다(코덱스 지적).
    """
    n = len(blocks)
    if not n:
        return {}
    aba = gap = embedded = annex = 0
    #: ★어느 **조항**이 걸렸는지 기록한다. 문서 단위로만 세면
    #:   결함 4개 때문에 문서의 조항 155개를 통째로 버리게 된다
    #:   (실측: 문서 게이트 897조항 0.42% → 조항 게이트 168,523조항 93.95%).
    gated: set[int] = set()
    #: 번호 체계별로 따로 본다.
    by_kind: dict[str, list[tuple[int, int]]] = collections.defaultdict(list)
    for i, b in enumerate(blocks):
        if isinstance(b.get("no"), int):
            by_kind[b.get("kind", "article")].append((i, b["no"]))

    for seq in by_kind.values():
        for i in range(2, len(seq)):
            #: A-B-A 재진입 — `제4 → 제5 → 제4`. 부모 오귀속의 가장 선명한 신호.
            if seq[i][1] == seq[i - 2][1] and seq[i][1] != seq[i - 1][1]:
                aba += 1
                #: ★첫 A 는 살리고 **B 와 두 번째 A** 를 끈다(코덱스 합의).
                #:   어느 경계가 거짓인지 A-B-A 만으로는 확정할 수 없기 때문이다.
                gated |= {seq[i - 1][0], seq[i][0]}
        for a, b in zip(seq, seq[1:]):
            #: 번호 비연속 — `제18 → 제20`. ★단독 사용 금지.
            #:   원문 자체의 결번·발췌 문서도 걸린다(저정밀 신호).
            #:   ★그래서 `gated` 에 **넣지 않는다.** 검수 우선순위일 뿐이다.
            if b[1] > a[1] + 1:
                gap += 1

    for bi, b in enumerate(blocks):
        body = b["text"]
        #: ★블록 **안**에 다른 조 머리가 매몰 — 경계를 놓친 확정 신호.
        #:   첫 줄(자기 머리)은 빼고 센다.
        tail = body.split("\n", 1)[1] if "\n" in body else ""
        n_emb = len(re.findall(r"^[ \t]{0,6}제\s*\d{1,3}\s*조[（(\[【]", tail, re.M))
        if n_emb:
            embedded += n_emb
            #: ★삼킨 조항(carrier)은 끈다. 삼켜진 조항은 **복구되지 않는다** —
            #:   별도 ordinal 로 존재하지 않기 때문이다(코덱스). 아래 목록에 남긴다.
            gated.add(bi)
        #: 부록 흡수 — 붙임·별표·분류표가 **줄머리에서** 시작하고
        #:   그 뒤로 본문이 이어지면 그 조가 부록을 삼킨 것이다.
        m = _ANNEX.search(body)
        if m and len(body) - m.start() >= ANNEX_MIN_TAIL:
            annex += 1
            gated.add(bi)

    return {"gated_ordinals": sorted(gated),
            "S1_aba_reentry": aba, "S2_number_gap": gap,
            "S3_embedded_header": embedded, "S4_annex_absorption": annex,
            "n_blocks": n}


# ────────────────────────────────────────────────────────────────
# C 축 — 인용 건전성. 판정 근거로 쓸 수 있나
# ────────────────────────────────────────────────────────────────
def citation_faults(blocks: list[dict]) -> dict:
    """C1 인용 유일성 위반.

    ★같은 인용 문자열이 한 문서에서 여러 곳을 가리키면
      "제4조 제1항에 따르면" 이 **어디를 가리키는지 모른다.**
      실측: 3rd 는 54%가 중복(이어짐 청크에 같은 citation 을 붙인다), v5 는 3.5%.
    """
    n = len(blocks)
    if not n:
        return {}
    c = collections.Counter(b["cite"] for b in blocks if b.get("cite"))
    return {"C1_dup_citation": sum(v - 1 for v in c.values() if v > 1)}


def noise_rates(blocks: list[dict]) -> dict:
    """오염 문자율 — 복구 못 한 PUA 글리프와 인코딩 손실."""
    total = sum(len(b["text"]) for b in blocks) or 1
    pua = sum(len(_PUA.findall(b["text"])) for b in blocks)
    repl = sum(len(_REPL.findall(b["text"])) for b in blocks)
    return {"N1_pua_per_1m": 1_000_000 * pua / total,
            "N2_replacement_per_1m": 1_000_000 * repl / total}


# ────────────────────────────────────────────────────────────────
# 로더 — 파이프라인마다 다른 스키마를 **공통 블록**으로 환원한다
# ────────────────────────────────────────────────────────────────
def load_v5(sha: str) -> list[dict] | None:
    hits = list(_STRUCTURED.rglob(f"s5_*/{sha}.clauses.json"))
    if not hits:
        return None
    doc = json.loads(hits[0].read_text(encoding="utf-8"))
    out = []
    for c in doc["clauses"]:
        no, kind = None, "article"
        m = re.match(r"제(\d{1,3})조", c["clause_no"])
        if m:
            no = int(m.group(1))
        else:
            m = re.match(r"(\d{1,3})(?:-\d{1,2})?\.", c["clause_no"])
            if m:
                no, kind = int(m.group(1)), "numbered"
        out.append({"no": no, "kind": kind, "title": c.get("title", ""),
                    "text": c["text"],
                    #: fallback 산출물엔 `citation` 이 없다(조항이 아니므로).
                    "cite": f'{c["section"]}/{c.get("citation") or c["clause_no"]}'})
    return out


def source_text(sha: str) -> str | None:
    hits = list(_EXTRACTED.rglob(f"s4_*/{sha}.json"))
    if not hits:
        return None
    doc = json.loads(hits[0].read_text(encoding="utf-8"))
    return "\n".join(p["text"] for p in doc["pages"])


def audit_doc(sha: str, blocks: list[dict]) -> dict:
    r: dict = {"sha": sha}
    src = source_text(sha)
    if src:
        r.update(text_fidelity(src, [b["text"] for b in blocks]))
    r.update(structure_faults(blocks))
    r.update(citation_faults(blocks))
    r.update(noise_rates(blocks))
    #: ★인용 가능 여부. **확정 신호만** 쓴다.
    #:   번호 비연속·항호 이상은 저정밀이라 여기 넣지 않는다(검수 우선순위 신호일 뿐).
    r["citation_eligible"] = not (r.get("S1_aba_reentry") or r.get("S3_embedded_header")
                                  or r.get("S4_annex_absorption"))
    return r


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pipeline", default="v5", choices=("v5",))
    ap.add_argument("--sha", help="한 문서만")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    shas = [args.sha] if args.sha else sorted(
        p.name.split(".")[0] for p in _STRUCTURED.rglob("s5_*/*.clauses.json"))
    if args.limit:
        shas = shas[:args.limit]

    rows, agg = [], collections.defaultdict(float)
    docs_with = collections.Counter()
    for sha in shas:
        blocks = load_v5(sha)
        if not blocks:
            continue
        r = audit_doc(sha, blocks)
        rows.append(r)
        for k in ("S1_aba_reentry", "S2_number_gap", "S3_embedded_header",
                  "S4_annex_absorption", "C1_dup_citation", "n_blocks"):
            agg[k] += r.get(k, 0)
        for k in ("S1_aba_reentry", "S3_embedded_header", "S4_annex_absorption"):
            if r.get(k):
                docs_with[k] += 1
        if not r["citation_eligible"]:
            docs_with["citation_ineligible"] += 1
        if args.verbose:
            print(json.dumps(r, ensure_ascii=False))

    nb = agg["n_blocks"] or 1
    print(f"\n문서 {len(rows):,} · 조 블록 {int(nb):,}")
    print("── 확정 신호 (citation_eligible 을 끈다) ──")
    for k in ("S1_aba_reentry", "S3_embedded_header", "S4_annex_absorption"):
        print(f"  {k:24s} {int(agg[k]):>7,} 건 / 1천블록당 {1000 * agg[k] / nb:>7.2f}"
              f" · 문서 {docs_with[k]:,}건({100 * docs_with[k] / max(len(rows), 1):.1f}%)")
    print("── 검수 우선순위 신호 (자동 실패 아님) ──")
    for k in ("S2_number_gap", "C1_dup_citation"):
        print(f"  {k:24s} {int(agg[k]):>7,} 건 / 1천블록당 {1000 * agg[k] / nb:>7.2f}")
    t1 = [r["T1_recall"] for r in rows if "T1_recall" in r]
    if t1:
        t1.sort()
        print(f"── 원문 보존 ──\n  T1_recall 중앙 {t1[len(t1) // 2]:.4f}")
    print(f"\n★ citation_eligible=false : {docs_with['citation_ineligible']:,}건"
          f" ({100 * docs_with['citation_ineligible'] / max(len(rows), 1):.1f}%)")


if __name__ == "__main__":
    import sys

    sys.path.insert(0, str(_ROOT))
    main()
