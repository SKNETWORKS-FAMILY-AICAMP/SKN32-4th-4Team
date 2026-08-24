"""**약관에 정의된 용어** 목록을 만든다 — 챗봇 입력 도우미용.

★왜 필요한가

    챗봇은 「약관에 적힌 용어의 뜻」을 원문으로 찾아 준다. 그런데 화면에는
    예시 4개(`도수치료가 뭐야`·`통원 뜻`·`본인부담금`·`도수치료 보장되나요?`)만
    **하드코딩**돼 있었다. 사용자는 무엇을 물어볼 수 있는지 알 길이 없다.

★어떻게 뽑나 — **답이 실제로 나오는 것만** 남긴다

    용어집은 `data/glossary/passages.jsonl` 의 구절 2,650개뿐이고
    「용어→뜻」 표가 따로 없다. 정의표가 PDF 안에서 테두리 없는 표라
    본문에서 칸이 무너져 있다(`용 어  정  의  계약 보험계약 …`).

    그래서 **후보를 뽑고 → 실제 검색으로 검증**한다.
      1. 정의 구절에서 한글 낱말 후보를 뽑는다
      2. `glossary.find()` 로 실제로 걸리는지 확인한다
      3. **몇 개 약관에 나오는지**로 정렬한다 — 널리 쓰이는 용어가 위로

    ★검증을 안 하면 「목록에 있는데 물어보면 못 찾는」 용어가 생긴다.
      그건 입력 도우미로서 최악이다.

★이건 사전이 아니다

    뜻을 여기 담지 않는다. 뜻은 챗봇이 **약관 원문 인용으로** 답한다.
    여기 담는 것은 「이 낱말은 약관에 정의가 있다」는 사실뿐이다.

쓰는 법:
    python -m scripts.eval.glossary_terms
    python -m scripts.eval.glossary_terms --min-policies 5 --limit 300
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib
import re
import sys
import time

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_OUT = _ROOT / "data" / "exports" / "glossary_terms.json"

#: ★★용어는 **짧은 줄**로 서 있고 바로 아래에 **긴 정의 줄**이 온다(실측).
#:
#:     도수치료
#:     치료자가 손(정형용 교정장치 …)을 이용해서 환자의 근골격계통 …
#:
#:   처음엔 본문에서 한글 낱말을 그냥 긁었는데 `에서`·`료비`·`료기관` 같은
#:   **깨진 조각**이 5,968개 나왔다. 정의표가 PDF 안에서 테두리 없는 표라
#:   줄바꿈이 낱말 가운데를 자르기 때문이다. 빈도만 보면 조각도 통과한다.
#:   → **줄 구조**를 본다. 그래야 「용어」와 「조각」이 갈린다.
_TERM_LINE = re.compile(r"^[가-힣A-Za-z0-9·\(\)]{2,18}$")
_DEF_MIN = 18  # 정의 줄로 볼 최소 길이

#: 너무 흔해서 도움이 안 되는 것을 거른다 — 거의 모든 약관에 있는 낱말은
#: 「무엇을 물어볼 수 있나」에 답하지 못한다.
_MAX_POLICY_RATIO = 0.98

#: ★용어가 아니라 **표 머리글·조사·연결어**. 넣으면 「계약 뜻」 같은 항목이 생긴다.
_STOP = frozenset({
    "용어", "정의", "용 어", "정 의", "다음", "경우", "이때", "그리고", "또는", "다만",
    "포함", "제외", "합니다", "봅니다", "말합니다", "같습니다", "여기서", "붙임", "별표",
    "약관", "특별약관", "보통약관", "회사", "이하", "이상", "미만", "초과", "각각", "모두",
    "해당", "관련", "기준", "방법", "내용", "사항", "여부", "이내", "이후", "이전",
    "특", "별", "관", "제1조", "제2조", "제3조", "제4조", "제5조",
})


#: ★★**진짜 쓰레기만** 거른다. 「다른 용어의 부분문자열」로 거르면 안 된다 —
#:   `의료비`·`계약`·`급여`·`입원` 은 복합어의 부분이지만 **그 자체가 용어**다.
#:   실측 2026-08-04: 그 방식으로 311개 중 35개를 걸렀는데 대부분 정상 용어였다.
#:
#:   진짜 쓰레기의 모양은 따로 있다 —
#:     `료기관`  줄바꿈이 낱말 가운데를 잘라 앞이 날아간 것
#:     `함)`·`등)`  괄호 짝이 안 맞는 것
#:     `따른`·`상해로`·`기준에`  조사로 끝나는 것 = 용어가 아니라 구절 조각
#:     `13`·`42`  쪽번호
_JUNK_TAIL = re.compile(r"(으로|로|에서|에게|에|의|를|을|은|는|와|과|이|가|도|만|부터|까지)$")
_ONLY_DIGIT = re.compile(r"^[0-9]+$")
#: 조문 참조(`제42조제1항…`)와 서술어 꼬리(`말함`·`이며`·`동등하다고`)는 용어가 아니다.
_ARTICLE_REF = re.compile(r"제\s*\d+\s*조")
_PREDICATE = re.compile(r"(하고|하며|이며|이고|한다|합니다|였|했|함|말함|하는|한$)")
#: ★**관형형 어미**로 끝나는 짧은 낱말은 용어가 아니라 구절 조각이다
#:   (`따른`·`정한`·`같은`). 조사 목록으로는 안 잡힌다 — 어미는 다른 부류다.
#:   실측 2026-08-04: `따른` 이 이 구멍으로 통과해 목록에 들어갔다.
_ADNOMINAL = re.compile(r"(른|던|을|ㄹ)$")


def _is_junk(term: str, freq: dict) -> bool:
    """용어가 아니라 **조각**인가."""
    if _ONLY_DIGIT.match(term):
        return True
    if term.count("(") != term.count(")"):
        return True
    if _JUNK_TAIL.search(term) and len(term) <= 5:
        return True
    if _ARTICLE_REF.search(term):
        return True
    if _PREDICATE.search(term):
        return True
    if len(term) <= 4 and _ADNOMINAL.search(term):
        return True
    #: 가운뎃점이 든 것은 표 칸이 통째로 붙은 것이다(`원·병원·치과병원·…`).
    if term.count("·") >= 2:
        return True
    #: ★잘린 앞부분 — 더 긴 용어의 **꼬리**이고 등장 수가 **정확히 같다**.
    #:   같다는 것은 늘 그 긴 낱말 안에서만 나왔다는 뜻이다.
    n = freq.get(term, 0)
    for other, m in freq.items():
        if other != term and other.endswith(term) and m == n:
            return True
    return False


def _terms_in(text: str) -> set[str]:
    """한 구절에서 **정의된 용어**만 뽑는다. 줄 구조로 가른다."""
    lines = [ln.strip() for ln in (text or "").splitlines()]
    out: set[str] = set()
    for i, ln in enumerate(lines[:-1]):
        if not ln or ln in _STOP or not _TERM_LINE.match(ln):
            continue
        #: 바로 다음의 **비어 있지 않은** 줄이 충분히 길면 그건 정의다.
        nxt = next((x for x in lines[i + 1:i + 3] if x), "")
        if len(nxt) >= _DEF_MIN and nxt not in _STOP:
            out.add(ln)
    return out


def build(min_policies: int, limit: int) -> dict:
    from app.adapters import file_glossary_source as src

    passages = src._load()
    total_policies = len({p.sha256 for p in passages})
    print(f"정의 구절 {len(passages):,} · 약관 {total_policies:,}", flush=True)

    #: ── 1. 후보 뽑기 ──
    freq: collections.Counter = collections.Counter()
    for p in passages:
        for w in _terms_in(p.text):
            freq[w] += 1
    cand = [w for w, n in freq.most_common() if n >= min_policies]
    print(f"줄 구조로 뽑은 용어 후보 {len(freq):,} → 구절 {min_policies}개 이상 {len(cand):,}",
          flush=True)

    #: ── 2. 실제 검색으로 검증 ──
    #: ★여기서 거르지 않으면 「목록에 있는데 못 찾는」 용어가 생긴다.
    t0 = time.time()
    items: list[dict] = []
    pol_by_term: dict[str, int] = {}
    for i, w in enumerate(cand):
        hits = src.find(w, limit=0)
        if not hits:
            continue
        policies = len({h.sha256 for h in hits})
        if policies < min_policies:
            continue
        if policies / total_policies > _MAX_POLICY_RATIO:
            #: 거의 모든 약관에 있는 낱말 — 「무엇을 물어볼 수 있나」에 답이 안 된다.
            continue
        items.append({"term": w, "policies": policies, "passages": len(hits)})
        pol_by_term[w] = policies
        if (i + 1) % 500 == 0:
            print(f"  검증 {i + 1}/{len(cand)} · 통과 {len(items)} · "
                  f"{time.time() - t0:.0f}초", flush=True)

    #: ★거른 것을 **센다.** 조용히 빼면 「검증했다」가 실제보다 좋아 보인다.
    before = len(items)
    items = [x for x in items if not _is_junk(x["term"], pol_by_term)]
    print(f"조각 제외 {before - len(items)} → 남은 용어 {len(items)}", flush=True)

    items.sort(key=lambda x: (-x["policies"], -x["passages"], x["term"]))
    kept = items[:limit] if limit else items

    return {
        "schema_version": "v1",
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "source": "data/glossary/passages.jsonl (약관의 용어 정의 구절)",
        "scanned_passages": len(passages),
        "scanned_policies": total_policies,
        #: ★분모를 함께 — 「300개」만 내보내면 그게 전부인 줄 안다.
        "verified_terms": len(items),
        "junk_removed": before - len(items),
        "min_policies": min_policies,
        "★한계": [
            "이건 용어 사전이 아니다 — 뜻은 담지 않는다. 뜻은 챗봇이 약관 원문으로 답한다.",
            "여기 있는 것은 **약관 정의 구절에 실제로 나오고 검색으로 확인된** 낱말뿐이다.",
            "목록에 없는 낱말도 물어볼 수 있다 — 못 찾으면 못 찾았다고 답한다.",
            "부분 문자열 검색이라 낱말이 다른 낱말 안에 들어 있어도 걸린다.",
        ],
        "items": kept,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-policies", type=int, default=3,
                    help="이만큼의 약관에 나와야 목록에 넣는다")
    ap.add_argument("--limit", type=int, default=400, help="저장할 상한(0=전량)")
    a = ap.parse_args(argv)

    data = build(a.min_policies, a.limit)
    _OUT.parent.mkdir(parents=True, exist_ok=True)
    _OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n검증된 용어 {data['verified_terms']:,} · 저장 {len(data['items']):,}")
    print(f"→ {_OUT}")
    print("상위:", [x["term"] for x in data["items"][:12]])
    return 0


if __name__ == "__main__":
    sys.exit(main())
