"""약관 **전문**을 훑어 세대 근거를 찾는다.

★왜 필요한가

    세대는 매니페스트(수집기가 사이트 목록에서 읽은 값)에 적혀 있는데,
    **그게 틀린 것이 실제로 있었다.**

    실측 2026-08-05 — 메리츠화재 7건은 매니페스트가 `20260501`(4세대 구간
    마지막 5일)인데 **문서 표지에 「판매개시 2026. 7. 13」** 이라 적혀 있다.
    7월이면 5세대 시행(2026-05-06) 이후다. 게다가 본문에 5세대의 핵심 표지인
    **「비중증」이 23~54회** 나오고 「4세대」 언급은 0이다.

    ★내가 앞서 이 차이를 보고 「상품 판매개시 ≠ 판본 효력일이니 개정이다」라며
      통과시켰다. 문서가 **「판매개시」라고 명시한** 날짜를 다른 것으로 바꿔 읽은
      것이고, 그 결과 7건의 세대가 한 단계 어긋났다.

★무엇을 근거로 삼나 — **문서가 스스로 말한 것만**

    ① 판매개시/시행일 표기      `판매개시 2026. 7. 13` · `2021년 7월 1일 시행`
    ② 세대를 직접 밝힌 표기      `4세대실손` · `5세대`
    ③ 세대 고유 제도 표지        ★`비중증`(비급여 중증/비중증 분리) = **5세대**
                                 `비급여 특약 분리` + `자기부담 20/30` = 4세대
    ④ 적용대상 구간 표기         `2009년 10월 30일부터 2017년 3월 31일 이전`

    ★③이 가장 강하다. 날짜는 사이트와 문서가 다를 수 있지만
      **제도 자체가 문서에 박혀 있으면** 그건 그 세대의 약관이다.

★한 문서가 **여러 세대에 걸치는** 경우가 있다

    할인특약·전환특약은 「표준화(2세대)와 신실손(3세대) 둘 다 대상」처럼 적는다.
    그건 **모르는 게 아니라 해당 없음**이다 — `not_applicable` 로 구분한다.

쓰는 법:
    python -m scripts.confirm.scan_generation_evidence            # 전수
    python -m scripts.confirm.scan_generation_evidence --limit 50 # 표본
    python -m scripts.confirm.scan_generation_evidence --disagree # 어긋난 것만
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib
import re
import sys
import time

from app.core.domain.clause_text import normalize, term_pattern

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_OUT = _ROOT / "data" / "exports" / "generation_evidence.json"

#: ★★**「시행」만 보고 날짜를 믿으면 안 된다.**
#:
#:   처음엔 `(\d{4})년 (\d{1,2})월 … 시행` 을 판매개시 신호로 넣었다.
#:   그랬더니 216건이 「판매개시 2021-01-01」로 잡혔는데, 실제 문맥은 이것이었다 —
#:
#:     「제8차 개정 한국표준질병･사인분류(통계청 고시 제2020-175호, **2021.1.1 시행**)」
#:
#:   **KCD 고시의 시행일**이다. 보험 판매개시가 아니다. 2025년 7월 상품이
#:   2021년에 판매 시작했을 리 없는데, 신호만 보면 그럴듯해 보인다.
#:
#:   ★그래서 **「판매」라는 말이 붙은 표기만** 받는다. 법령·고시 시행일은
#:     약관 본문에 수십 개씩 나오므로 그것과 섞이면 근거가 아니라 잡음이 된다.
_SALE = (
    #: ★일자는 **구분자 바로 뒤**에서만 읽는다. 공백을 허용하면 줄바꿈 뒤의
    #:   **쪽번호**를 일자로 먹는다 — 실측: 「판매월 2023.01」 다음 줄의 `4` 를
    #:   집어 `20230104` 를 만들었다(원래 `20230101` 이 맞다).
    re.compile(r"판매개시\s*[:：]?\s*(\d{4})\s*[.\-년]\s*(\d{1,2})(?:\s*[.\-월]\s*(\d{1,2})\s*일?)?"),
    #: ★「판매월」은 **월까지만** 밝힌 표기다. 일자를 만들어 붙이지 않는다.
    re.compile(r"판매월\s*[:：]?\s*(\d{4})\s*[.\-년]\s*(\d{1,2})()"),
    re.compile(r"판매일\s*[:：]?\s*(\d{4})\s*[.\-년]\s*(\d{1,2})(?:\s*[.\-월]\s*(\d{1,2})\s*일?)?"),
)

#: 세대를 **직접** 밝힌 표기.
_SAYS_GEN = re.compile(r"([1-5])\s*세대\s*실손")

#: ★★세대 고유 제도 표지 — **날짜보다 강한 근거**다.
#:
#:   `비중증` 은 5세대에서 처음 생겼다. 비급여를 중증/비중증으로 나누고
#:   비중증 자기부담률을 50%로 올린 것이 5세대의 핵심이다.
#:   실측: 5세대 약관에 23~54회 나오고 4세대 이하에는 0회다.
#: ★★공백에 흔들리지 않게 **도메인 정규화**를 쓴다(`app/core/domain/clause_text.py`).
#:   실측 2026-08-05 — 「우선 공제」를 `우선공제` 로 찾아 0건이 나왔고
#:   그걸 「구조가 바뀌었다」고 읽어 **결론이 뒤집혔다.**
_MARKERS = {
    5: (term_pattern("비중증"), 3),
}

#: ★★**표지를 쓰면 안 되는 상품라인이 있다.**
#:
#:   실측 2026-08-05 — 2026-05-06 이후 노후실손 19건 중 **16건이 「비중증」 0회**다.
#:   「중증」은 4~15회 쓰면서 「비중증」은 안 쓴다. 5세대 표준실손의 중증/비중증
#:   분리가 **노후실손에는 그대로 적용되지 않는다**는 뜻이다.
#:
#:   그런데 삼성생명 노후실손 2건에서 「비중증」이 10회 나왔다. 문맥을 열어 보니 —
#:
#:     중지가능 보장종목 (노후실손) | 보장종목 (단체)
#:     질병보장                  | 질병급여형, 중증질병비급여형, **비중증**질병비급여형
#:
#:   **오른쪽은 단체실손**이다. 노후실손 약관이 **다른 상품의 보장종목을 설명**한
#:   표에서 걸린 것이다.
#:
#:   ★이건 앞서 겪은 오탐과 **같은 패턴**이다 — 계약전환용 약관이 전환 대상인
#:     구세대를 설명해서 「표준화이전」 용어 판정이 정확도 11% 였던 그 일.
#:     **약관은 자기 얘기만 하지 않는다.**
#:
#:   → 노후실손에는 이 표지를 쓰지 않는다. 날짜 근거만 쓴다.
_MARKER_SKIP_LINES = frozenset({"senior"})

#: 여러 세대를 **대상으로 삼는다**고 적은 것.
_SPANS = re.compile(r"표준화\s*실손[^\n]{0,40}(계약|보험)")

def _gen_ranges():
    prof = json.loads((_ROOT / "config" / "generation_profiles.json").read_text(encoding="utf-8"))
    out = []
    for g in prof["generations"]:
        d = lambda v: v.replace("-", "") if v else None  # noqa: E731
        out.append((g["generation"], d(g.get("effective_from")), d(g.get("effective_to"))))
    return out


_RANGES = _gen_ranges()


#: ★★**「여러 세대」로 뭉개지 않는다 — 몇 세대까지인지 날짜로 알 수 있다.**
#:
#:   할인특약·전환특약은 적용대상을 최초계약일 구간으로 적는다.
#:     「2009년 10월 30일부터 2017년 3월 31일 이전에 최초계약이 체결된」 → 2세대
#:     「2017년 4월 1일 이후에 최초계약이 체결된」                        → 3세대 이후
#:   → 이 특약은 **2~3세대 대상**이라고 적을 수 있다.
#:
#:   ★세대는 「최초계약 체결일」로 정해진다. 특약이 그 구간을 밝혀 두면
#:     대상 세대를 그대로 읽어낼 수 있다. `not_applicable` 한 마디로 버리면
#:     「이 특약이 누구에게 적용되나」를 다시 사람이 열어 봐야 한다.
_TARGET_RANGE = re.compile(
    r"(20\d{2})년\s*(\d{1,2})월\s*(\d{1,2})일\s*부터\s*"
    r"(20\d{2})년\s*(\d{1,2})월\s*(\d{1,2})일\s*이전에\s*최초계약")
_TARGET_OPEN = re.compile(r"(20\d{2})년\s*(\d{1,2})월\s*(\d{1,2})일\s*이후에\s*최초계약")

#: ★★**「이전 대상계약」도 적용대상 표기다**(사용자 지적 2026-08-05).
#:
#:   삼성화재 무사고자 할인 특별약관은 이렇게 쓴다 —
#:     「다만, **2011년 9월 30일 이전 대상계약**에 대한 소급분에 한하여 적용합니다.」
#:     「무사고판정기간 예시(3년갱신 기준) **최초계약일('08.10.2)**」
#:
#:   내 패턴은 「…이전에 **최초계약**」만 봤기 때문에 이 둘을 통째로 놓쳤고,
#:   그래서 두 문서가 아무 근거 없이 `unknown` 으로 남아 있었다.
#:   ★사람이 눈으로 찾아 준 것을 규칙이 못 잡으면 **규칙을 고쳐야** 한다.
_TARGET_BEFORE = re.compile(r"(20\d{2})년\s*(\d{1,2})월\s*(\d{1,2})일\s*이전\s*(?:의\s*)?(?:대상)?계약")
#: 「최초계약일('08.10.2)」처럼 **두 자리 연도**로 적은 예시.
_TARGET_YY = re.compile(r"최초계약일\s*\(\s*'?(\d{2})\s*[.\-]\s*(\d{1,2})\s*[.\-]\s*(\d{1,2})")


#: ★★**업계는 세대를 이름으로 부른다** — 날짜보다 이쪽이 직접적이다.
#:
#:   실측 2026-08-05 — 날짜 구간(`…이전에 최초계약`)으로만 뽑으니 47건 중 2건만
#:   걸렸다. DB손해보험은 「2016년 4월 1일부터 … **갱신이 도래하는** 갱신형
#:   **표준화이전** 실손 의료비 보험계약」처럼 쓴다. **갱신 도래일**은 세대를
#:   정하는 기준(최초계약일)이 아니라서 날짜로는 잡으면 안 되는데,
#:   **「표준화이전」이라는 낱말 자체가 세대를 말한다.**
#:
#:     표준화이전  → 1세대 (2009-09-30 이전)
#:     표준화      → 2세대 (2009-10-01 ~ 2017-03-31)
#:     신 실손     → 3세대 (2017-04-01 ~ 2021-06-30)
#:
#:   ★순서가 중요하다 — 「표준화이전」이 「표준화」를 포함하므로 긴 것부터 본다.
_TIER_TERMS = (
    (re.compile(r"표준화\s*이전"), 1),
    (re.compile(r"표준화\s*실손"), 2),
    (re.compile(r"신\s*실손"), 3),
)


def _tier_generations(text: str) -> list[int]:
    """약관이 **이름으로** 부른 세대. `표준화이전`·`표준화`·`신 실손`."""
    out: set[int] = set()
    for pat, g in _TIER_TERMS:
        if pat.search(text):
            out.add(g)
    #: 「표준화이전」이 있으면 「표준화」도 매칭되지만 그건 낱말이 겹친 것뿐이다.
    #: 둘 다 진짜로 있는지 확인하려면 「표준화이전」을 지우고 다시 본다.
    if 1 in out:
        stripped = _TIER_TERMS[0][0].sub("", text)
        if not _TIER_TERMS[1][0].search(stripped):
            out.discard(2)
    return sorted(out)


def _target_generations(text: str) -> list[int]:
    """이 특약이 **어느 세대를 대상으로 삼는가**. 최초계약일 구간을 세대로 옮긴다."""
    gens: set[int] = set(_tier_generations(text))
    for m in _TARGET_RANGE.finditer(text):
        lo = f"{m.group(1)}{m.group(2).zfill(2)}{m.group(3).zfill(2)}"
        hi = f"{m.group(4)}{m.group(5).zfill(2)}{m.group(6).zfill(2)}"
        for g, glo, ghi in _RANGES:
            #: 구간이 겹치면 그 세대가 대상이다.
            if (glo or "00000000") <= hi and lo <= (ghi or "99999999"):
                gens.add(g)
    for m in _TARGET_OPEN.finditer(text):
        lo = f"{m.group(1)}{m.group(2).zfill(2)}{m.group(3).zfill(2)}"
        for g, glo, ghi in _RANGES:
            if lo <= (ghi or "99999999"):
                gens.add(g)
    #: 「YYYY년 M월 D일 이전 대상계약」 — 그 날짜까지의 모든 세대가 대상이다.
    for m in _TARGET_BEFORE.finditer(text):
        hi = f"{m.group(1)}{m.group(2).zfill(2)}{m.group(3).zfill(2)}"
        for g, glo, ghi in _RANGES:
            if (glo or "00000000") <= hi:
                gens.add(g)
    #: 「최초계약일('08.10.2)」 — 두 자리 연도 예시. 그 시점의 세대가 대상에 든다.
    for m in _TARGET_YY.finditer(text):
        y = 2000 + int(m.group(1))
        if not (2000 <= y <= 2027):
            continue
        ymd = f"{y}{m.group(2).zfill(2)}{m.group(3).zfill(2)}"
        g = gen_of(ymd)
        if g:
            gens.add(g)
    return sorted(gens)





def gen_of(ymd: str) -> int | None:
    for g, lo, hi in _RANGES:
        if (lo or "00000000") <= ymd <= (hi or "99999999"):
            return g
    return None


def scan_one(text: str, product_line: str = "") -> dict:
    """한 문서에서 세대 근거를 모은다. **판정하지 않고 근거를 낸다.**"""
    out: dict = {"sale_dates": [], "says_gen": [], "markers": {}, "spans_generations": False,
                 "sale_context": []}
    for pat in _SALE:
        for m in pat.finditer(text):
            y, mo = m.group(1), m.group(2).zfill(2)
            dd = (m.group(3) or "01").zfill(2)
            if dd == "00":
                dd = "01"
            out["sale_dates"].append(f"{y}{mo}{dd}")
            #: ★★**문맥을 함께 남긴다.** 정규식이 무엇을 먹었는지 눈으로 볼 수 없으면
            #:   오탐이 그대로 매니페스트까지 간다 — 실제로 두 번 그랬다
            #:   (KCD 고시 시행일 216건 · 쪽번호를 일자로 먹은 건들).
            ctx = re.sub(r"\s+", " ", text[max(0, m.start() - 40):m.end() + 30])
            out["sale_context"].append(f"{y}{mo}{dd} ← …{ctx}…")
    out["sale_dates"] = sorted(set(out["sale_dates"]))
    out["says_gen"] = sorted({int(m.group(1)) for m in _SAYS_GEN.finditer(text)})
    if product_line not in _MARKER_SKIP_LINES:
        for g, (pat, need) in _MARKERS.items():
            n = len(pat.findall(text))
            if n >= need:
                out["markers"][str(g)] = n
    out["spans_generations"] = bool(_SPANS.search(text))
    #: ★걸치는 세대를 **구체적으로** 뽑는다. 「해당 없음」으로 뭉개지 않는다.
    out["target_generations"] = _target_generations(text)
    return out


def decide(ev: dict) -> tuple[int | None, str, str]:
    """근거에서 세대를 정한다. → (세대, 확신도, 근거설명)

    ★순서가 곧 근거의 강도다. 제도 표지 > 직접 표기 > 날짜.
    """
    if ev["markers"]:
        g = max(int(k) for k in ev["markers"])
        n = ev["markers"][str(g)]
        #: ★★**「비중증이 있다 → 5세대다」로 **확정하지 않는다**(코덱스 지적 2026-08-05).
        #:
        #:   세대가 확정된 982건에 대 보니 검출력이 이랬다 —
        #:     5세대 84% · 4세대 1% · 3세대 이하 0%
        #:
        #:   판별력은 강하지만 **5세대인데 이 낱말이 없는 문서가 16%** 다.
        #:   특약은 본문이 짧아 안 나올 수 있고, OCR 이 표·괄호를 훼손하기도 한다.
        #:   반대로 비교표·타 세대 설명에 인용되어 나올 수도 있다.
        #:
        #:   ★그래서 `exact`(확정)가 아니라 `review`(검수 우선)로 낸다.
        #:     날짜 근거가 함께 있으면 그때 확정된다 — 아래 `sale_dates` 분기가 처리한다.
        if ev["sale_dates"]:
            gens = {gen_of(d) for d in ev["sale_dates"]}
            gens.discard(None)
            if gens == {g}:
                return g, "exact", (
                    f"본문 {g}세대 표지 {n}회 + 문서가 밝힌 판매개시 {ev['sale_dates']}")
        return g, "review", f"본문에 {g}세대 고유 표지 {n}회 — 날짜 근거가 없어 검수 필요"
    if len(ev["says_gen"]) == 1:
        g = ev["says_gen"][0]
        return g, "exact", f"문서가 「{g}세대실손」이라고 직접 밝힘"
    #: ★★**「대상 세대」로 그 문서의 세대를 정하지 않는다.**
    #:
    #:   처음엔 「적용대상 구간이 한 세대에만 걸치면 그 세대다」로 했는데
    #:   세대가 확정된 200건에 대 보니 **정확도 11%**(16/146)였다.
    #:
    #:   원인은 **계약전환용 약관**이다. 4세대 전환용 약관이 전환 **대상**인
    #:   「표준화이전」(1세대)을 설명하므로, 용어만 보면 1세대로 읽힌다.
    #:   실측: 흥국화재 계약전환용 여러 건이 매니페스트 4세대인데 문서 1세대로 판정됐다.
    #:
    #:   ★같은 자료로 「판매개시일」 근거는 **27/27 = 100%** 였다.
    #:     그래서 대상 세대는 **누구에게 적용되는가**를 적는 데만 쓰고,
    #:     **이 문서가 몇 세대인가**에는 쓰지 않는다. 둘은 다른 질문이다.
    tg = ev.get("target_generations") or []
    if ev["spans_generations"]:
        return None, "not_applicable", "여러 세대를 적용대상으로 삼는 특약(표준화·신실손 등)"
    if len(ev["says_gen"]) > 1:
        return None, "not_applicable", f"여러 세대를 함께 언급({ev['says_gen']})"
    if ev["sale_dates"]:
        gens = {gen_of(d) for d in ev["sale_dates"]}
        gens.discard(None)
        if len(gens) == 1:
            g = gens.pop()
            return g, "exact", f"문서가 밝힌 판매개시/시행일 {ev['sale_dates']}"
        if gens:
            return None, "ambiguous", f"문서 안 날짜가 여러 세대에 걸침 {sorted(gens)}"
    return None, "unknown", "문서에서 세대 근거를 찾지 못함"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--disagree", action="store_true", help="매니페스트와 어긋난 것만 출력")
    a = ap.parse_args(argv)

    from scripts.confirm.identify_documents import load_manifest_rows

    rows = [r for r in load_manifest_rows() if not (r.get("excluded_reason") or "").strip()]
    if a.limit:
        rows = rows[: a.limit]

    t0 = time.time()
    results, no_artifact = [], 0
    for i, r in enumerate(rows):
        sha12 = r["sha256"][:12]
        hits = list((_ROOT / "data" / "extracted").glob(f"*/s5_pymupdf-1.28.0/{sha12}.json"))
        if not hits:
            #: ★조용히 넘기지 않는다. 못 읽은 것을 세어 결과에 적는다.
            no_artifact += 1
            continue
        d = json.loads(hits[0].read_text(encoding="utf-8"))
        text = "\n".join((p.get("text") or "") for p in (d.get("pages") or []))
        #: ★찾기 전에 정규화한다 — 줄바꿈이 낱말을 자른 것을 「없다」로 읽지 않는다.
        ev = scan_one(normalize(text), r.get("product_line") or "")
        g, conf, why = decide(ev)
        results.append({
            "sha256": r["sha256"], "insurer": r["insurer"],
            "product_name": r.get("product_name", ""),
            "manifest_generation": r.get("generation"),
            "manifest_confidence": r.get("generation_confidence"),
            "manifest_sale_start": r.get("sale_start"),
            "doc_generation": g, "doc_confidence": conf, "evidence": why,
            "doc_sale_dates": ev["sale_dates"],
            "doc_sale_context": ev.get("sale_context") or [],
            #: ★어느 세대를 대상으로 삼는 특약인지. 비면 해당 없음.
            "target_generations": ev.get("target_generations") or [],
        })
        if (i + 1) % 200 == 0:
            print(f"  {i + 1}/{len(rows)} · {time.time() - t0:.0f}초", flush=True)

    disagree = [x for x in results
                if x["doc_generation"] is not None
                and x["manifest_generation"] is not None
                and x["doc_generation"] != x["manifest_generation"]]
    filled = [x for x in results
              if x["manifest_generation"] is None and x["doc_generation"] is not None]

    data = {
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "scanned": len(results), "no_artifact": no_artifact,
        "disagree_count": len(disagree), "filled_count": len(filled),
        "doc_confidence": dict(collections.Counter(x["doc_confidence"] for x in results)),
        "items": results,
    }
    _OUT.parent.mkdir(parents=True, exist_ok=True)
    _OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"\n스캔 {len(results):,} (산출물 없음 {no_artifact}) · {time.time() - t0:.0f}초")
    print(f"문서 근거 확신도: {data['doc_confidence']}")
    print(f"\n★매니페스트와 **세대가 어긋나는 것** {len(disagree)}")
    for x in (disagree if a.disagree else disagree[:15]):
        print(f"   {x['insurer']:<10} 매니페스트 {x['manifest_generation']}세대 → "
              f"문서 {x['doc_generation']}세대 | {x['product_name'][:34]}")
        print(f"      근거: {x['evidence']}")
    print(f"\n★매니페스트가 비었는데 **문서로 채울 수 있는 것** {len(filled)}")
    for x in filled[:10]:
        print(f"   {x['insurer']:<10} → {x['doc_generation']}세대 | {x['product_name'][:34]}")
        print(f"      근거: {x['evidence']}")
    print(f"\n→ {_OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
