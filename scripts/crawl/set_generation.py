"""실손의료보험 **세대**를 판매개시일로 판정해 매니페스트에 넣는다.

★경계는 코드에 박지 않는다 — `config/generation_profiles.json` 을 읽는다

    처음에 이 스크립트를 만들 때 설정 파일이 있는 줄 모르고 경계를 코드에 박았다.
    그래서 **틀렸다**(코덱스가 잡아냈다).

      · 5세대(2026-05-06~)를 아예 몰라서 139건을 4세대로 판정했다
      · 노후실손·유병력자실손을 일반 실손과 **같은 축**으로 판정했다.
        설정에는 `applies_to: ["standard"]` 로 **별도 라인**임이 명시돼 있다.

    같은 사실을 두 곳에 두면 반드시 갈라진다. 설정이 단일 출처다.

★상품 라인이 먼저다 — 세대는 일반 실손에만 있다

    노후실손(2014-08~)·유병력자실손(2018-04-02~)은 **별개 상품**이지
    일반 실손의 세대가 아니다. 여행 실손은 세대 구분 대상이 아니다.
    이들에게 "3세대"라고 붙이면 자기부담금을 잘못 안내하게 된다.

★`review_status` 를 존중한다

    설정에 이렇게 적혀 있다.

        "출처는 2차 자료(보도자료·보험사 뉴스룸)다. 금융위/협회 원문으로 재확인이 필요하다."
        "review_status 가 'unreviewed' 인 동안에는 어떤 문서도 CONFIRMED 로 확정할 수 없다."

    그래서 `unreviewed` 면 판정 결과에 `generation_review="unreviewed"` 를 박아
    **자동 판정에서 그대로 믿지 않도록** 표시한다.

★신뢰도

    exact     날짜를 일자까지 안다
    month     월까지만 안다(상품코드에서 유도). 경계달이 아니면 세대는 갈린다
    ambiguous 월까지만 아는데 **경계달에 걸린다** — 세대가 흔들릴 수 있다
    unknown   날짜를 모른다. 판정하지 않는다

실행:
    python -m scripts.crawl.set_generation --dry-run
    python -m scripts.crawl.set_generation
"""

from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path

from app.core.domain.policy_naming import is_rider
from app.core.errors import InfraError

_ROOT = Path(__file__).resolve().parents[2]
_MANIFESTS = _ROOT / "data" / "raw" / "manifests"
_PROFILES = _ROOT / "config" / "generation_profiles.json"


def _load_profiles() -> dict:
    if not _PROFILES.exists():
        raise InfraError(
            f"세대 설정이 없습니다: {_PROFILES}\n"
            "경계를 코드에 박지 않는다 — 설정이 단일 출처다."
        )
    return json.loads(_PROFILES.read_text(encoding="utf-8"))


def _ymd(s: str | None) -> str:
    """`2021-07-01` → `20210701`. None 이면 열린 구간."""
    return s.replace("-", "") if s else ""


def product_line(name: str, profiles: dict) -> str:
    """상품 라인(standard / senior / simplified_issue / travel).

    ★★**공백을 지우고 본다.**

        보험사가 「무배당수호천사온라인 **실손 의료비**보장보험」처럼 낱말 사이에
        공백을 넣는다. 표지가 `실손의료비` 라 그대로 대조하면 안 걸리고,
        그러면 세대 축이 없는 `unknown` 으로 빠진다.
        실측 2026-08-05 — 동양생명 3건이 이 공백 하나로 분류 실패했다.
    """
    import re as _re

    name = _re.sub(r"\s+", "", name or "")
    types = profiles["product_types"]
    #: ★일반 실손보다 **특수 라인을 먼저** 본다.
    #:   `노후실손의료비보험` 은 `실손의료비` 도 포함하므로 순서가 중요하다.
    for key in ("travel", "senior", "simplified_issue"):
        spec = types.get(key, {})
        if any(mk in name for mk in spec.get("name_markers", [])):
            return key
    spec = types.get("standard", {})
    if any(mk in name for mk in spec.get("name_markers", [])):
        if any(x in name for x in spec.get("exclude_markers", [])):
            return "unknown"
        return "standard"
    return "unknown"


def generation_of(sale_start: str, line: str, profiles: dict) -> dict | None:
    """해당 라인·날짜의 세대 항목. 없으면 None."""
    for g in profiles["generations"]:
        if line not in g.get("applies_to", []):
            continue
        #: ★★**상품라인마다 세대 시작일이 다를 수 있다.**
        #:
        #:   유병력자(간편)실손은 **2018-04 출시**라 표준실손 3세대 시작(2017-04-01)을
        #:   그대로 쓰면 출시 전 구간이 열린다. 1·2세대는 아예 없다.
        #:   4·5세대 경계는 표준실손과 **같다**(2021-07-01 · 2026-05-06).
        #:
        #:   근거: https://myside.kr/insurance/1660 (유병자 실손 세대별 정리) ·
        #:   우리 데이터 173건 중 2018-04 이전 **0건**으로 출시일과 일치.
        #:
        #:   ★`senior`(노후실손)는 여기 넣지 않았다 — 2014-08 출시는 확인됐지만
        #:     「노후실손 3·4·5세대」라는 공식 구간을 못 찾았다. 모르면 안 붙인다(§0).
        by_line = g.get("effective_from_by_line") or {}
        lo = _ymd(by_line.get(line) or g.get("effective_from"))
        hi = _ymd(g.get("effective_to"))
        if lo and sale_start < lo:
            continue
        if hi and sale_start > hi:
            continue
        return g
    return None


def _boundary_months(profiles: dict) -> set[str]:
    """경계가 걸린 달 — 월까지만 아는 날짜로는 세대가 흔들린다."""
    out: set[str] = set()
    for g in profiles["generations"]:
        for k in ("effective_from", "effective_to"):
            v = _ymd(g.get(k))
            if v:
                out.add(v[:6])
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    profiles = _load_profiles()
    review = profiles.get("review_status", "unreviewed")
    boundary = _boundary_months(profiles)
    print(f"설정 {_PROFILES.name} (작성 {profiles.get('compiled_at')}, 검수 {review})")
    print(f"  세대 구간: {[(g['generation'], g.get('effective_from'), g.get('effective_to')) for g in profiles['generations']]}")
    if review != "reviewed":
        print("  ★검수 전이다 — 판정 결과에 generation_review 를 남긴다(그대로 믿지 않는다).")

    dist = collections.Counter()
    lines = collections.Counter()
    conf = collections.Counter()
    for m in sorted(_MANIFESTS.glob("*.jsonl")):
        rows = [
            json.loads(line)
            for line in m.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        for r in rows:
            #: ★격리된 문서(사업방법서·여행실손·비의료실손)는 판정 대상이 아니다.
            #:   세지도 않는다 — 세면 분모가 부풀어 통계가 거짓말을 한다.
            if (r.get("excluded_reason") or "").strip():
                continue
            name = r.get("product_name") or r.get("original_name") or ""
            line = product_line(name, profiles)
            r["product_line"] = line
            lines[line] += 1

            #: 이전 판정을 지우고 다시 넣는다(잘못 박힌 값이 남지 않게).
            for k in ("generation", "generation_label", "generation_note"):
                r.pop(k, None)

            #: ★★**문서 근거로 정정한 세대는 지우지 않는다.**
            #:
            #:   판매일이 「판매월 2026.05」처럼 **월까지만** 알려진 문서가 있다.
            #:   5세대 시행일이 2026-05-06 이라 1일인지 6일 이후인지로 세대가 갈리는데,
            #:   그 날짜를 지어내면 안 된다(§1 지어내지 않는다).
            #:
            #:   대신 **약관 본문이 세대를 말해 준다** — 5세대는 비급여를 중증/비중증으로
            #:   나눈다. 실측 2026-08-05: NH농협생명·NH농협손해보험 6건이 본문에
            #:   「비중증」을 19~22회 담고 있는데 매니페스트는 4세대였다.
            #:
            #:   ★그래서 `generation_override` 를 두고 **근거를 함께** 적는다.
            #:     날짜는 모르는 채로 두고, 세대만 근거를 대고 고친다.
            ov = r.get("generation_override")
            if ov:
                r["generation"] = ov
                #: ★정정분도 라인을 밝힌다 — 아래 일반 경로와 같은 규칙을 쓴다.
                ko = profiles["product_types"].get(line, {}).get("label_ko") or line
                r["generation_label"] = f"{ov}세대" if line == "standard" else f"{ov}세대 {ko}"
                r["generation_confidence"] = "exact"
                #: ★★**정정분도 태그를 단다.** 안 달면 「분류가 비어 있는」 행이 생기고,
                #:   그러면 세는 쪽이 그걸 어디에 넣을지 몰라 또 뭉뚱그린다.
                #:   실측 2026-08-05 — 이 분기가 `continue` 로 빠져 8건이 무태그였다.
                r["generation_semantics"] = (
                    "official_generation" if line == "standard" else "shared_reform_epoch")
                dist[f"{ov}세대(문서근거 정정)"] += 1
                continue

            start = (r.get("sale_start") or "").strip()
            if not start or len(start) < 8 or start == "00000000":
                r["generation_confidence"] = "unknown"
                #: ★「세대 축이 없다」가 아니라 **「판매일을 몰라 못 정했다」** 이다.
                r["generation_semantics"] = "date_unknown"
                dist["날짜모름"] += 1
                continue
            #: ★★**특약 판정이 상품라인 판정보다 먼저다.**
            #:
            #:   전에는 뒤에 있었다. 그랬더니 「무배당 임신출산질환실손입원의료비
            #:   …보장 **특별약관**」처럼 **상품라인을 못 정한 특약**이
            #:   `line_unclassified` 로 빠졌다 — 실측 2026-08-05, 9건 중 6건이 그랬다.
            #:   특약인지는 **상품라인과 무관하게** 정해진다.
            #:
            #: ★★**특약은 자기 세대를 갖지 않는다** (코덱스 교차검증 2026-08-05).
            #:
            #:   금융위 5세대 보도자료 https://www.fsc.go.kr/no010101/86831 —
            #:     「선택형 할인 특약은 기존 **1·2세대 계약을 유지한 상태에서**
            #:      보험료를 할인하는 특약」
            #:     「무사고 할인과 비급여 보험료차등제를 **5세대 특약에서도** 적용」
            #:
            #:   즉 특약은 **본계약의 세대에 붙는다.** 「2세대 계약에 붙는 할인특약」이지
            #:   「2세대 특약」이 아니다.
            #:
            #:   ★그런데 전에는 특약도 자기 판매일로 세대를 계산해 붙였다(212건 중 186건).
            #:     `generation=2` 가 「2세대 계약에 붙는다」가 아니라
            #:     「2015년에 발행됐다」는 뜻이 되어 **의미가 충돌**한다.
            #:     화면은 `generation_label` 을 그대로 보여 주므로 사용자는
            #:     그 특약 자체가 2세대 상품인 줄 안다.
            #:
            #:   → `generation` 은 **비운다.** 대신 —
            #:       applicable_generations  이 특약이 붙는 **본계약 세대**(문서 근거)
            #:       sale_epoch              판매 시점이 어느 세대 시기였나(참고용)
            #:
            #:   ★`generation` 을 남기고 `generation_semantics` 로만 구분하는 안은
            #:     코덱스가 반대했다 — **세대값을 읽는 코드가 이미 여럿**이고
            #:     그들이 semantics 를 정확히 검사한다는 보장이 없다.
            if is_rider(name):
                epoch = generation_of(start, line, profiles)
                r["generation_confidence"] = "not_applicable"
                r["generation_semantics"] = "rider_of_base_contract"
                r["generation_label"] = "특약(본계약 세대에 붙음)"
                if epoch:
                    #: ★세대가 아니라 **발행 시기**다. 이름으로 그 뜻을 못박는다.
                    r["sale_epoch"] = epoch["generation"]
                    r["sale_epoch_note"] = (
                        f"판매 시점({start})이 표준실손 {epoch['generation']}세대 시기였다는 뜻일 뿐, "
                        "이 특약이 그 세대 상품이라는 뜻이 아니다")
                dist["특약(본계약 세대에 붙음)"] += 1
                continue

            #: ★★**어느 라인에 세대 축이 있는지는 프로필이 정한다.**
            #:
            #:   전에는 `line != "standard"` 를 코드에 박아 두어 유병력자실손 173건이
            #:   통째로 `not_applicable` 이었다. 그런데 **유병자실손에도 세대가 있다** —
            #:   3세대(2018-04~) · 4세대(2021-07~) · 5세대(2026-05-06~)이고
            #:   4·5세대 경계는 표준실손과 같다.
            #:   근거: https://myside.kr/insurance/1660 · 우리 데이터 173건 중
            #:   2018-04 이전 0건(출시일과 일치).
            #:
            #:   ★`senior`(노후실손)는 여전히 축이 없다 — 공식 구간을 못 찾았다.
            #:     프로필의 `applies_to` 에 안 넣었으므로 여기서 자동으로 걸린다.
            #:     코드에 라인 이름을 박지 않으니, 근거가 생기면 **프로필만** 고치면 된다.
            if not any(line in g.get("applies_to", []) for g in profiles["generations"]):
                r["generation_confidence"] = "not_applicable"
                #: ★★**`not_applicable` 하나로 뭉치면 서로 다른 것이 섞인다.**
                #:
                #:   실측 2026-08-05 — `not_applicable` 163건 안에
                #:   **노후실손 154 + 상품라인 미확인 9** 가 함께 있었다.
                #:   앞의 것은 「세대 축이 없는 상품군」이고 뒤의 것은 「분류를 못 했다」다.
                #:   신뢰도만 보는 코드는 이 둘을 구분할 수 없다.
                #:
                #:   ★금감원 「2024년 실손의료보험 사업실적」도 1~4세대와
                #:     **그 외(유병력자·노후실손)** 를 나눠 집계한다. 그 축을 그대로 쓴다.
                r["generation_semantics"] = (
                    "separate_product_line" if line != "unknown" else "line_unclassified")
                dist[f"{line}(세대축 아님)"] += 1
                continue

            g = generation_of(start, line, profiles)
            if g is None:
                r["generation_confidence"] = "unknown"
                dist["구간없음"] += 1
                continue

            r["generation"] = g["generation"]
            #: ★★**라벨에 상품라인을 밝힌다.**
            #:
            #:   실측 2026-08-05 — 유병력자실손 50건이 「3세대 (착한실손)」 라벨을 달고
            #:   있었다. **「착한실손」은 표준실손 3세대의 별칭**이다. 유병력자실손에
            #:   그 이름을 붙이면 자기부담 구조까지 같은 줄 안다.
            #:
            #:   ★그리고 금감원 공식 통계는 **유병력자·노후실손을 1~4세대에서 뺀다** —
            #:     「그 외 유병력자 및 노후실손이 77만건(2.1%)」으로 따로 센다
            #:     (보험연구원 KIRI 정리, 2024년 실손의료보험 사업실적).
            #:     즉 「4세대」라는 이름은 **표준실손의 것**이다.
            #:
            #:   → 유병력자실손은 「4세대 유병력자실손」처럼 **라인을 붙여** 부른다.
            #:     세대 시기는 공유하되(개편을 함께 받았다) 이름을 섞지 않는다.
            #: ★★**숫자의 뜻을 함께 적는다**(코덱스 교차검증 2026-08-05).
            #:
            #:   금감원 「2024년 실손의료보험 사업실적」은 1~4세대와
            #:   **그 외(유병력자·노후실손)** 를 나눠 집계한다 — 「N세대」라는 이름은
            #:   **표준실손의 것**이다. 그런데 우리는 유병력자실손에도 3~5를 붙인다.
            #:
            #:   둘은 **양립한다** — 다만 뜻이 다르다.
            #:     official_generation   표준실손의 공식 세대
            #:     shared_reform_epoch   표준실손 개편 시기에 **대응하는** 구간
            #:
            #:   ★이 값을 자기부담률 자동 적용에 쓰면 안 된다. 유병력자실손은
            #:     급여/비급여 분리를 하지 않고(실측: 전 시기 0%) 보장종목이
            #:     상해/질병 × 입원/통원 4개로 유지된다 — 표준실손 4세대와 구조가 다르다.
            r["generation_semantics"] = (
                "official_generation" if line == "standard" else "shared_reform_epoch")
            label = g["label"]
            if line != "standard":
                base = label.split("(")[0].strip()
                ko = profiles["product_types"].get(line, {}).get("label_ko") or line
                label = f"{base} {ko}"
            r["generation_label"] = label
            #: ★★**근거를 값 옆에 남긴다**(CLAUDE.md §1 — 채운다면 무엇을 근거로 채웠는지).
            #:
            #:   `generation_confidence` 는 **날짜 정확도**만 말한다. 그것만으로는
            #:   「이 상품라인에 이 세대 축을 적용해도 되는가」라는 **다른 판단**이 안 보인다.
            #:   실측 2026-08-05 — 유병력자실손 173건에 세대를 붙였는데
            #:   `generation_note` 가 0건이었다. 무엇을 근거로 붙였는지 어디에도 없었다.
            basis = [f"{line} 라인 · sale_start={start}({r.get('date_source') or '출처미상'})"]
            by_line = g.get("effective_from_by_line") or {}
            if line in by_line:
                #: ★라인별 시작일은 **표준 구간을 그대로 쓴 것이 아니다.** 그 사실을 적는다.
                basis.append(f"이 라인의 {g['generation']}세대 시작 {by_line[line]}")
            note = g.get(f"note_{line}")
            if note:
                basis.append(note)
            r["generation_basis"] = " | ".join(basis)
            date_conf = r.get("date_confidence", "exact")
            if date_conf == "month" and start[:6] in boundary:
                gc = "ambiguous"
            elif date_conf == "month":
                gc = "month"
            else:
                gc = "exact"
            r["generation_confidence"] = gc
            r["generation_review"] = review
            dist[f"{g['generation']}세대"] += 1
            conf[gc] += 1

        if not args.dry_run:
            tmp = m.with_suffix(".jsonl.tmp")
            tmp.write_text(
                "".join(json.dumps(x, ensure_ascii=False) + "\n" for x in rows),
                encoding="utf-8",
            )
            tmp.replace(m)

    print("\n상품 라인(행 기준):")
    for k, v in lines.most_common():
        print(f"  {k:<20}{v:>5}")
    print("\n판정 결과:")
    for k in sorted(dist):
        print(f"  {k:<22}{dist[k]:>5}")
    print(f"\n세대 판정 신뢰도: {dict(conf)}")
    if args.dry_run:
        print("(dry-run: 아무것도 쓰지 않았습니다.)")


if __name__ == "__main__":
    import sys

    sys.path.insert(0, str(_ROOT))
    main()
