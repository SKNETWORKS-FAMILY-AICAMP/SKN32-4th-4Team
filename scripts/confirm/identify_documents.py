"""**이 파일이 무엇인가**를 확정한다 — 수집과 식별은 별개다.

참고 — 왜 필요한가

    `usable_for_judgment` 는 `identification == "confirmed"` 를 요구한다.
    지금 매니페스트 2,121행이 **전부 `unidentified`** 라 판정 가능 약관이 0건이고,
    `POST /v1/prechecks` 가 전량 `documents_not_confirmed` 로 기권한다.

    이건 고장이 아니라 **설계**다 — 확인 안 된 약관으로 "보장됩니다"라고 하면
    2019년 가입자에게 2024년 조항이 근거로 붙는다. 그래서 사람이 확정한다.

참고 — 무엇을 근거로 확정하나 — **문서 자신에게 물어본다**

    매니페스트는 *수집기가 사이트에서 읽은 것*이다. 그게 맞는지는
    **약관 본문**과 대조해야 안다. 네 가지를 본다.

        ① 참고 — **전체 상품명이 문서에 한 덩어리로** 나오는가 (강한 식별자)
        ② 문서가 스스로 밝힌 판매일이 `sale_start` 보다 **앞서지 않는가**
           (뒤면 개정이다 — 상품 판매개시와 판본 효력일은 **다른 것**이다)
        ③ 세대는 **값이 있을 때만** 규칙과 맞는지 본다 (NULL 은 정상 · 핸드오프 계약)
        ④ 참고 — 같은 문서에서 **다른 본약관**도 확인되면 자동 확정하지 않는다 (`ambiguous`)

    `parse_status` 는 **식별 조건이 아니다.** 「이 파일이 무엇인가」와
    「조항을 근거로 댈 수 있는가」는 다른 층이라 `extraction_blocked` 로 표시만 한다.

참고 — 이 도구가 실제로 잡은 것들 (2026-08-04)

    · 메리츠화재 `sale_start=20260501` vs 표지 「판매개시 2026. 7. 13」
      → 처음엔 탈락시켰는데 **틀렸다.** 상품 판매개시와 판본 효력일은 다른 필드다.
    · 반대로 내가 만든 **없는 규칙** 셋이 대량 오탈락을 냈다 —
      `date_confidence` 라벨 부재(+677) · 세대 NULL(+301) · `parse_status`(+36).
      참고 — **계약보다 엄격한 규칙을 임의로 만드는 것**이 이 작업의 주된 실패 모드였다.
    · 반대로 **너무 무른 기준**도 있었다 — 토큰 커버리지만 보다가
      확정 1,115건 중 **798건(71.6%)이 다른 상품명과도 일치**하고 있었다.
      강한 식별자로 바꾸니 850건이 됐다(제거 337). **줄어든 것이 맞는 방향이다.**

참고 — 확정은 **매니페스트에 쓰지 않는다.** 별도 원장에 쌓는다.

    매니페스트는 「우리가 무엇을 받았나」(수집 기록)이고,
    확정은 「이게 무엇인지 사람이 정했다」(결정)이다. 섞으면
    **크롤러를 다시 돌리는 순간 사람의 결정이 덮인다.**
    원장은 `config/confirmed_documents.jsonl` 이고 저장소에 커밋된다.

쓰는 법:
    python -m scripts.confirm.identify_documents --report
    python -m scripts.confirm.identify_documents --report --limit 40
    python -m scripts.confirm.identify_documents --apply --confirmed-by "홍길동" --scope demo
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
import unicodedata

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_MANIFESTS = _ROOT / "data" / "raw" / "manifests"
_LEDGER = _ROOT / "config" / "confirmed_documents.jsonl"

#: 상품명 토큰이 몇 %나 문서에 나와야 「이름이 맞다」고 볼 것인가.
#: 참고 — 1.0 이다. 0.8 로 두면 `[1종단체전환용]` 같은 **종별 표기 하나**가 빠져도
#:   통과하는데, 종이 다르면 자기부담금이 다른 **다른 상품**이다.
_NAME_MATCH_MIN = 1.0

#: 문서 앞 몇 쪽까지 훑을 것인가.
#:
#: 핵심 — **15 였다가 전체로 바꿨다(2026-08-04).** 표지가 「감사의 글」·「가이드 북」인
#:   회사가 있어 3 → 15 로 올렸는데 그래도 모자랐다 — 삼성생명은 **17쪽**에
#:   「삼성생명 인터넷실손의료비보장보험4.0(기본형,갱신형,무배당)」 이라고 정확히 밝힌다.
#:   실측: 상품명 탈락 206건 중 **117건이 40쪽 안에** 전체 이름을 갖고 있었다.
#:   즉 그 117건은 문서 문제가 아니라 **내가 만든 인위적 실패**였다(코덱스 지적).
#:
#: 참고 — `None` 이면 전문을 본다. 범위를 넓히는 대신 **아래 강한 식별자**로 정밀도를 지킨다.
_SCAN_PAGES: int | None = None

#: 파일명에서 온 날짜 접두어(`2016-01-01_무배당…`). 상품명이 아니라 **수집기가 붙인 것**이라
#: 문서 본문에 있을 리 없다. 이름 대조에서 뺀다 — 안 빼면 흥국화재가 통째로 탈락한다.
_FILENAME_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

_DATE_PATTERNS = (
    (re.compile(r"판매개시[:\s]*(\d{4})[.\-년](\d{1,2})"), "판매개시"),
    (re.compile(r"판매[월일][:\s]*(\d{4})[.\-년](\d{1,2})"), "판매월"),
    (re.compile(r"(\d{4})년(\d{1,2})월\d{0,2}일?시행"), "시행"),
)

#: 참고 — 「신계약 적용대상」 조항의 **판매기간** 진술. `_DATE_PATTERNS` 와 합치지 않는다.
#:
#:   실측 2026-08-11(현대해상) — 판본 없는 이름의 리더(특약)가 분기마다 재발행되는데,
#:   표지는 매번 똑같은 문구다. 대신 본문 2쪽이 스스로 적용기간을 밝힌다 —
#:
#:     sha 00eb260bb1c1 (sale_start 20260701): 「2026년 7월 1일부터 …까지 판매하는」
#:     sha 1a6a51c47465 (sale_start 20260401): 「2026년 4월 1일부터 …까지 판매하는」
#:
#:   위험 — `_DATE_PATTERNS` 에 합치지 않는 이유는 그쪽의 「개정 판정」 로직이
#:   **찾은 것 중 가장 이른 날짜**를 쓰기 때문이다. 같은 페이지의 무관한 법령
#:   인용일(「…규정…2020년1월1일부터시행」 따위)이 섞여 들어가면 그 최솟값이
#:   앞당겨져 이미 통과하던 문서를 「판매시점이 앞선다」로 되돌릴 수 있다.
#:   여기는 **가족 안 유일 식별**에만 쓰는 별도 신호다.
#:
#:   위험 — 처음엔 `…부터[\s\S]{0,60}?판매` 로 썼는데 시험이 거짓임을 잡았다.
#:   마침표를 건너뛰어 「2020년1월1일부터**시행합니다.** 2026년7월1일부터…판매」
#:   에서 **앞쪽 무관한 날짜**가 걸렸다. 「까지…판매」 형태로 좁히고 마침표를
#:   건너뛰지 못하게 했다 — 실제 문구는 항상 「…부터 …까지 판매하는」이다.
_APPLICABLE_PERIOD = re.compile(r"(\d{4})년(\d{1,2})월\d{1,2}일부터[^.。]{0,40}?까지[^.。]{0,10}?판매")


def _applicable_period_months(text: str) -> set[str]:
    """이 글이 스스로 밝힌 **판매기간**(YYYYMM). 「…부터 …까지 판매하는」 조항만 본다."""
    squeezed = (text or "").replace(" ", "")
    return {f"{m.group(1)}{int(m.group(2)):02d}" for m in _APPLICABLE_PERIOD.finditer(squeezed)}


#: 원천 사이트가 파일 자체에 붙인 날짜/판본 — 파일명 **끝**에서만 찾는다.
#:
#:   실측 2026-08-12(DB손해보험·현대해상) — 상품명 문자열의 판본은 낡았는데
#:   원천 URL은 최신 판본으로 갱신돼 있었다 —
#:
#:     상품명: 실손의료비보장 안정화 할인 특별약관2507
#:     URL   : …ACU(00)_20251013.pdf                    ← 파일명 자체가 2510(10월)
#:     sale_start: 20251013                              ← 매니페스트 필드는 이미 맞다
#:
#:   위험 — 상품코드·게시물 번호 같은 **긴 숫자열**을 판본으로 오인하면 안 된다
#:   (`data/gongsi/goods/150277466...` 류). 그래서 **파일명 끝, `.pdf` 바로 앞**만 본다.
#:
#:   위험 — 코덱스 교차검증(2026-08-12)이 반례를 냈다. 자리 형태만 보고 **월(MM)이
#:   유효한지 안 봐서** `_20251399.pdf`(13월)나 `(AB1234)`(상품 내부 코드) 같은
#:   것도 판본으로 읽었다. 월을 `01`~`12` 로 못박아 정규식 단계에서 거른다.
_URL_TAIL_DATE = re.compile(r"_(\d{4})(0[1-9]|1[0-2])\d{2}\.pdf(?:[?#]|$)", re.I)
_URL_TAIL_CODE = re.compile(r"\(([A-Za-z]{1,3})(\d{2})(0[1-9]|1[0-2])\)[^/\\]*\.pdf(?:[?#]|$)", re.I)


def url_tail_ym(row: dict) -> str | None:
    """이 문서의 `url`/`saved_as` **파일명 끝**에서 읽은 YYMM. 못 찾으면 `None`.

    위험 — 두 문자열을 공백으로 이어 붙여 하나로 검사하면 안 된다. `$` 는
      **글 전체의 끝**에서만 서므로, 뒤에 빈 `saved_as` 를 이어 붙이는 것만으로도
      `.pdf` 뒤에 공백이 남아 `$` 고정이 깨진다(실측 — 시험이 잡았다). 따로 본다.

    위험 — `.pdf` 뒤에 `?download=1` 같은 쿼리스트링이 붙으면 예전엔 놓쳤다
      (코덱스 지적). `[?#]` 도 끝으로 허용한다.
    """
    import urllib.parse

    for raw in (row.get("url") or "", row.get("saved_as") or ""):
        text = urllib.parse.unquote(raw)
        m = _URL_TAIL_DATE.search(text)
        if m:
            return f"{m.group(1)[2:]}{m.group(2)}"
        m = _URL_TAIL_CODE.search(text)
        if m:
            return f"{m.group(2)}{m.group(3)}"
    return None


def url_confirmed_qualifiers(row: dict, product_name: str) -> frozenset[str]:
    """매니페스트 이름의 정체성 부기 중 **원천 파일명에도 실제로 있는** 것.

    표지가 생략했어도, 사이트가 다운로드 파일 **이름 자체**에 그 낱말을 박아
    뒀다면 짐작이 아니라 원천이 직접 밝힌 것이다.

    위험 — 코덱스 지적(2026-08-12) — 처음엔 URL **전체**(경로·쿼리스트링 포함)를
      뒤졌다. 그러면 이 파일과 무관한 디렉터리 이름(예: `/InsProduct/계약전환용목록/`)
      에 그 낱말이 있어도 「확인됨」으로 잘못 읽는다. **파일명만** 본다 — `/` 뒤,
      `?`·`#` 앞.
    """
    import urllib.parse

    names = []
    for raw in (row.get("url") or "", row.get("saved_as") or ""):
        decoded = urllib.parse.unquote(raw)
        tail = re.split(r"[?#]", decoded)[0]
        names.append(tail.rsplit("/", 1)[-1].rsplit("\\", 1)[-1])
    tn = _norm(" ".join(names))
    return frozenset(q for q in _IDENTITY_QUALIFIERS if q in (product_name or "") and _norm(q) in tn)

#: `config/generation_profiles.json` 의 시행 구간. 코드에 박지 않고 읽어 온다.
def _generation_ranges() -> list[tuple[int, str | None, str | None]]:
    prof = json.loads((_ROOT / "config" / "generation_profiles.json").read_text(encoding="utf-8"))
    out = []
    for g in prof["generations"]:
        def _d(v):
            return v.replace("-", "") if v else None
        out.append((g["generation"], _d(g.get("effective_from")), _d(g.get("effective_to"))))
    return out


#: 참고 — **같은 말인데 표기가 다른 것**만 여기 둔다. 게이트를 무르게 하려는 목록이 아니다.
#:
#:   `무배당` 과 `(무)` 는 한국 보험 표기의 같은 낱말이다(배당 없음). 상품명에는
#:   `무배당 메리츠 …` 로, 약관 표지에는 `(무) 메리츠 …` 로 적힌다.
#:
#:   참고 — 크기 때문이 아니라 **맞기 때문에** 넣는다. 실측 2026-08-04 —
#:   `무배당` 누락 78건 중 문서에 `(무)` 가 실제로 있는 것은 **메리츠화재 9건뿐**이다.
#:   삼성생명 64건은 `무배당` 도 `(무)` 도 없다 — 그건 표기 차이가 아니라
#:   **문서가 상품명을 안 밝힌 것**이라 여전히 막혀야 한다.
#:
#:   참고 — 여기에 `기본형`·`갱신형` 같은 **상품 옵션 표기를 넣지 말 것.**
#:   그건 같은 말이 아니라 **다른 상품**을 가리킬 수 있다(자기부담금이 다르다).
_NAME_ALIASES: dict[str, tuple[str, ...]] = {
    "무배당": ("무",),
    "無배당": ("무", "무배당"),
}


def _gen_for(yyyymmdd: str) -> int | None:
    for gen, a, b in _generation_ranges():
        if (a is None or yyyymmdd >= a) and (b is None or yyyymmdd <= b):
            return gen
    return None


def _gen_splits_month(ss: str) -> bool:
    """그 달 **안에서** 세대가 갈리는가 — 갈리면 월 정밀도 날짜로는 못 가린다.

    참고 — 5세대 경계가 `2026-05-06` 이다. 경계가 달 가운데 있으면 「1일」로 채운 값이
      경계 앞뒤 어느 쪽에도 놓일 수 있다. 그 값으로 모순을 선언할 수 없다.

    위험 — **말일을 `31` 로 고정하면 안 된다.** 처음 그렇게 썼더니 2009-09 가 갈린다고
      나왔다 — 1세대 경계는 `2009-09-30` 이라 9월은 갈리지 않는데, 있지도 않은
      `20090931` 이 문자열 비교로 경계를 넘었다. **없는 날짜를 채워 결론을 만든 것**이
      이 함수가 막으려던 바로 그 잘못이다.
    """
    import calendar

    y, m = int(ss[:4]), int(ss[4:6])
    last = calendar.monthrange(y, m)[1]
    return _gen_for(f"{y:04d}{m:02d}01") != _gen_for(f"{y:04d}{m:02d}{last:02d}")


def _norm(s: str) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]", "", unicodedata.normalize("NFKC", s or "")).lower()


#: 수집기가 파일명에서 붙인 날짜 접두어. 상품명이 아니다.
_NAME_DATE_PREFIX = re.compile(r"^\d{4}-\d{2}-\d{2}_")


#: 상품명 **끝**에 붙은 수집·공시 표기. 상품명이 아니다.
#:
#:   실측 2026-08-11 —
#:     「…(단체전환용_25.07)_20250901」        수집일
#:     「…(계약전환용_26.05)(갱신형)_20260701이후」  참고 — 「그 날 이후 판매분」이라는 **공시 구분**
#:
#:   표지에는 당연히 없다. 이걸 이름의 일부로 요구하면 흥국화재가 통째로 막힌다.
_COLLECT_DATE_SUFFIX = re.compile(r"_\d{8}\s*(?:이후|이전|부터)?$")


def full_name_key(product_name: str) -> str:
    """상품명 **전체**를 기호·공백 없이 한 덩어리로. 제목이 줄바꿈으로 갈려도 이어 붙는다.

    참고 — 수집일 접미사는 뗀다. **원본 필드는 건드리지 않는다** — 비교용 열쇠에서만 뺀다.
    """
    name = _COLLECT_DATE_SUFFIX.sub("", _NAME_DATE_PREFIX.sub("", product_name or ""))
    return _norm(name)


#: 판본 토큰 — `2404` · `26.05` · `Hi2404` 를 모두 `2404`·`2605` 로 읽는다.
#: 참고 — YY 는 두 자리, MM 은 01~12 만. 그래야 「1111」·「2222」 같은 예시 숫자를 안 먹는다.
_VERSION = re.compile(r"(?<!\d)(?:[A-Za-z]{1,3})?(\d{2})[.\-]?(0[1-9]|1[0-2])(?!\d)")


def version_tokens(text: str) -> set[str]:
    """이 글에 적힌 **판본**들. `26.05` 와 `2605` 를 같은 것으로 본다.

    핵심 — 이것이 이 도구의 **불변식**이다. `2507` 과 `2510` 을 가르는 유일한 선이고,
      다른 표기 차이(낱말 삽입·괄호 부기·순서)를 아무리 허용해도 이것만은 안 흔든다.

      실측 2026-08-11 — 「실손의료비보장 안정화 할인 특별약관2507」 행에 붙은 문서의
      표지가 「…특별약관2510」이었다. 이름은 글자 하나까지 같고 판본만 다르다.
      **2507 가입자에게 2510 약관으로 답하게 된다.**
    """
    return {f"{m.group(1)}{m.group(2)}"
            for m in _VERSION.finditer(unicodedata.normalize("NFKC", text or ""))}


#: 표지가 **생략하곤 하는** 부기 — 판매채널·감독 분류어이지 상품 정체성이 아니다.
#:
#:   `CM`·`TM`  판매채널(사이버·텔레)
#:
#: 위험 — 여기에 **종별(`1~2종`)이나 형태(`기본형`)를 넣지 말 것.** 그건 자기부담금이
#:   달라지는 **다른 상품**이다.
_OPTIONAL_QUALIFIERS = frozenset({"cm", "tm"})

#: 참고 — 표지가 안 쓰는 **감독 분류어**. 낱말 경계 없이 붙어 나오므로 부분 문자열로 뗀다.
#:
#:   「제도성 특별약관」은 제도(무사고할인·중지재개 등)에 따른 특약이라는 **분류**이지
#:   상품을 가르는 말이 아니다. 실측 — 표지 「요양실손 보장한도 변경 특별약관」 ↔
#:   공시 「…제도성 특별약관」. 빠진 토큰 집계에서 제도성 계열이 12건이었다.
_OPTIONAL_WORDS = re.compile(r"제도성\s*")

#: 위험 — 표지에 **반드시 있어야 하는** 부기 — 이걸 빼면 다른 상품이 된다.
#:   코덱스 지적(2026-08-11) — 「(계약전환용)2404」와 「2404」는 가입 대상이 다르다.
#:   내 실측으로는 반증이 0건이었지만, 그건 통계지 **정체성 논증이 아니다.**
_IDENTITY_QUALIFIERS = ("계약전환용", "전환계약용", "단체전환용", "개인재개용",
                        "개인단체연계용", "만기재가입", "유병력자용")

#: 표지가 **더 쓰는** 연결 낱말 — 공시 목록 이름에는 없는 일이 있다.
#:   실측: 「…실손의료비(기본납입형)1304」 ↔ 표지 「…실손의료비**보험**(기본납입형)1304」
_INSERTIONS = ("보험", "보장", "특별약관", "비")


#: 「배지형」 조각 줄로 볼 최대 길이 · 최대 이어붙일 줄 수.
#:
#:   실측 2026-08-11(흥국화재) — 표지가 제목과 부기를 **낱말 하나짜리 배지**로
#:   쪼개 여러 줄에 흩어 놓는다 —
#:
#:       무배당흥국화재실손의료보험 | 계약전환용 | 갱신형 | ( | _1904)( | ) | 약관
#:
#:   인접 두 줄로는 못 묶는다. 위험·핵심 — 그렇다고 창을 무작정 넓히면 안 된다 — 본문
#:   문장은 한 줄이 훨씬 길다. **짧은 줄만** 이어붙이면 본문에는 안 걸리고
#:   이런 배지형 조판에만 걸린다.
_FRAGMENT_MAX = 12
_FRAGMENT_RUN_MAX = 8


#: 표지 후보를 몇 쪽까지 볼 것인가.
#:
#:   위험·핵심 — **2 였다가 40 으로 넓혔다(2026-08-12).** 이 파일은 예전에 같은 실수를
#:   한 번 했다 — 전체 텍스트 대조(`flat`)의 탐색 범위(`_SCAN_PAGES`)를 3→15로
#:   올렸다가 그래도 모자라 결국 전체(`None`)로 바꿨다(「삼성생명은 17쪽에」 주석 참조).
#:   `cover_windows` 는 그때 만든 게 아니라서 이 교훈을 안 물려받고 **또 2로 시작했다.**
#:
#:   실측 2026-08-12(롯데손해보험) — 「무배당 롯데 실손의료보험」이 2~122쪽 전체의
#:   **반복 머리말**로 박혀 있는데(판본 없이), 판본 「(1501)」은 **29쪽에 단 한 번**
#:   나온다. 1~28쪽은 회사 안내·목차이고 29쪽이 **진짜 약관 표지**다 —
#:
#:       '무배당'
#:       '롯데 실손의료보험 (1501)'
#:       '약   관'
#:
#:   인접 두 줄 메커니즘은 이미 이걸 잡을 수 있다. 앞 2쪽만 본 게 문제였다.
#:   가장 큰 잔여 무리(53건, 롯데손해보험)가 전부 이 모양이었다.
#:
#:   위험 — 그렇다고 전체(`None`)로 열지는 않는다. 한 줄·인접 두 줄 창은 **선착순**이라
#:   (`next()` 가 첫 일치를 쓴다) 위험이 적지만, 100쪽 넘는 문서를 전부 훑으면
#:   느려지고 「적용대상」 목록에 우연히 걸릴 가능성도 늘어난다. 이전 사례(17쪽)와
#:   이번 사례(29쪽)를 모두 덮는 **40**으로 못박는다 — 근거 없이 더 늘리지 않는다.
_COVER_SCAN_PAGES = 40


def cover_windows(page_texts: list[str], pages: int = _COVER_SCAN_PAGES) -> list[tuple[int, str, bool]]:
    """표지 후보 — **한 줄, 인접 두 줄, 그리고 짧은 조각이 이어지는 구간.**

    반환: `(쪽 번호, 창 글, 순서를 지키는가)`

    참고 — 왜 「한 줄」인가. 문서 전체에서 토큰이 다 나오는 것으로는 부족하다는 것을
      이미 재 봤다 — 확정 1,115건 중 **798건(71.6%)** 이 다른 상품명과도 일치했다.
      약관집에는 적용대상 목록·비교표가 실린다. 제목은 한 줄에 있다.

    참고 — 왜 「인접 두 줄」까지인가. 표지가 긴 상품명을 두 줄로 접는다. 세 줄까지 넓히면
      제목과 그 아래 안내문이 붙어 버린다 — **긴 줄**을 무작정 묶지는 않는다.

    참고 — 왜 앞 40쪽까지인가. `_COVER_SCAN_PAGES` 주석 참조 — 진짜 표지가 목차·안내문
      뒤에 있는 문서가 있다(실측 29쪽).

    참고 — 위험 — 「짧은 조각 구간」은 별개다. 배지형 조판(위 주석)에서는 제목 뒤로 **짧은
      줄만** 이어진다. 길이 상한을 두므로 본문 문장은 안 걸린다.

      위험 — 그리고 짧은 조각 구간은 **거짓 닻**에 걸리기 쉽다. 실측(농협생명) —
      「[제도성특약]…」에서 「제도성」을 뗀 잔여물 「특약」이 뒤쪽의 다른 낱말
      「안정화**할인특약**」 안에 우연히 걸린다. 순서를 지키며 찾으면 그 우연한
      자리에서 커서가 멈춰 진짜 제목을 놓친다. 그래서 짧은 조각 구간만
      `ordered=False` 로 표시해 순서를 풀고 다시 찾게 한다(실측 6건 해소) —
      한 줄·인접 두 줄은 실제 문장이므로 순서를 그대로 요구한다.

      위험 — 숫자+한글이 붙은 식별 토큰(`1~2종`)은 이걸로도 못 잡는다. 표지가
      「종」을 숫자보다 먼저 적어 토큰 자체가 쪼개져야 하는데, 억지로 쪼개면
      「12」 같은 약한 두 자리 숫자로 대조하게 되어 「1~3종」(자기부담금이 다른
      상품)과 헷갈릴 위험이 있다. 그래서 이건 풀지 않는다 — 사람 검토로 남긴다.
    """
    out: list[tuple[int, str, bool]] = []
    for page_no, text in enumerate(page_texts[:pages], start=1):
        lines = [x.strip() for x in (text or "").splitlines() if x.strip()]
        for i, line in enumerate(lines):
            out.append((page_no, line, True))
            if i + 1 < len(lines):
                out.append((page_no, f"{line} {lines[i + 1]}", True))
            #: 이 줄부터 뒤로 **짧은 줄**이 이어지면 하나의 창으로 묶는다.
            j = i + 1
            while j < len(lines) and j - i <= _FRAGMENT_RUN_MAX and len(_norm(lines[j])) <= _FRAGMENT_MAX:
                j += 1
            if j > i + 1:
                out.append((page_no, " ".join(lines[i:j]), False))
    return out


#: 표지 줄에서 확인해야 하는 **최소 길이**. 접두를 쪽 단위로 봐주는 대신,
#: 이름의 **알맹이**는 반드시 한 줄 안에 있어야 한다.
_COVER_CORE_MIN = 8


def family_key(product_name: str) -> str:
    """판본·채널·수집 표기를 **뺀** 이름 — 「같은 상품군」의 열쇠.

    참고 — 판본이 없는 이름을 어떻게 확정하나(실측 55건)에 대한 답이다. 판본 불변식을
      그냥 면제하면 안 되고, **가릴 것이 애초에 없을 때**만 면제한다 —
      같은 보험사에 같은 상품군이 **하나뿐**이면 판본으로 가릴 대상이 없다.
    """
    v = unicodedata.normalize("NFKC", product_name or "")
    v = _COLLECT_DATE_SUFFIX.sub("", v)
    v = _VERSION.sub(" ", v)
    v = re.sub(r"\(\s*(?:CM|TM)\s*\)", " ", v, flags=re.I)
    return _norm(v)


def cover_match(product_name: str, window: str, *, page: str = "",
                lone_in_family: bool = False, ordered: bool = True,
                expected_version: set[str] | None = None,
                identity_confirmed: frozenset[str] = frozenset()) -> bool:
    """이 표지 줄이 **이 상품**을 가리키는가.

    공시 목록 이름과 표지 이름이 다르다는 것을 전수로 확인했다(2026-08-11) —

        매니페스트: 무배당 프로미라이프 실손의료비(기본납입형)1304
        표지      : 무배당 프로미라이프 실손의료비**보험**(기본납입형)1304

    286건 중 **147건이 괄호 부기 차이**, 79건이 낱말 하나, 38건이 글자 하나였다.
    같은 상품을 두 곳이 다르게 적는 것이지 다른 상품이 아니다.

    참고 — 그래서 흔들어도 되는 것과 안 되는 것을 가른다 —

        흔들어도 됨   `(CM)`·`(TM)` 생략 · 「보험/보장」 삽입 · 공백
        위험 — 안 됨        판본 토큰 · 정체성 부기(계약전환용·단체전환용…)

      코덱스가 정체성 부기를 못박았다(2026-08-11): 「(계약전환용)2404」와 「2404」는
      **가입 대상이 다른 상품**이다. 내 실측으로는 반증이 0건이었지만 통계로
      정체성을 정할 수는 없다.

    `ordered=False` 는 **배지형 조판**(`cover_windows` 의 짧은 조각 구간) 전용이다.
    배지는 태그 묶음이지 문장이 아니라서 조판 순서가 이름 순서와 다를 수 있다
    (실측: 「범용」·「종」·판본이 이름과 다른 순서로 추출됨). 한 줄·인접 두 줄은
    실제 문장이므로 기본값(`True`)으로 순서를 그대로 요구한다.

    `expected_version` · `identity_confirmed` 는 **매니페스트 이름이 낡았다는 게
    독립적으로 증명됐을 때만** 쓴다(`verify()` 의 URL 대조 참조). 실측 2026-08-12 —
    DB손해보험 5건·현대해상 1건이 상품명엔 옛 판본이 박혀 있는데, 원천 사이트가
    파일에 붙인 URL 날짜와 매니페스트 `sale_start` 가 **서로 독립적으로** 새 판본에
    일치했다. 표지 하나만 보고 판단하는 게 아니라 **두 출처가 맞아떨어질 때만** 쓴다.
    """
    mine = expected_version if expected_version is not None else version_tokens(product_name)
    #: 참고 — 판본을 모르면 자동 확정하지 않는다. 「모른다」를 「맞다」로 바꾸지 않는다.
    #:
    #: 핵심 — 단 하나의 예외 — **가릴 것이 애초에 없을 때**(`lone_in_family`).
    #:   같은 보험사에 같은 상품군이 하나뿐이면 판본으로 구별할 대상이 없다.
    #:   판본 불변식은 「2507 과 2510 을 가르기」 위한 것이지 그 자체가 목적이 아니다.
    #:   위험 — 호출하는 쪽이 상품군 크기와 날짜 정합을 **먼저 확인**해야 한다(`verify()`).
    if not mine and not lone_in_family:
        return False
    #: 위험 — 불변식 — **내 판본이 이 줄에 적혀 있어야 한다.**(판본이 있을 때)
    #:
    #:   참고 — 집합이 **같아야** 한다고 두었더니 시험이 잡았다. 표지에는 계좌번호 예시
    #:     「1111-2222」 같은 숫자가 흔한데 `1111` 은 YY=11·MM=11 로 판본과 **모양이 같다.**
    #:     어휘로는 못 가린다. 그래서 같음이 아니라 **포함**을 요구한다 —
    #:     그래도 2507 ⊄ {2510} 이라 판본 충돌은 그대로 막힌다.
    if mine and not mine <= version_tokens(window):
        return False

    win = _norm(window)
    #: 정체성 부기는 **표지에 있어야** 한다. 단 `identity_confirmed` 에 있으면
    #: (원천 URL이 직접 밝힌 것만) 표지에 없어도 넘어간다 — 통계·짐작이 아니다.
    for q in _IDENTITY_QUALIFIERS:
        if q in (product_name or "") and q not in identity_confirmed and _norm(q) not in win:
            return False

    #: 남은 토큰이 **순서를 지키며** 이 줄 안에 이어져야 한다.
    #:
    #: 위험·핵심 — 판본을 **먼저 떼고** 자른다. 토큰에 판본이 붙어 있다고 그 토큰을 통째로
    #:   건너뛰면(처음 그렇게 썼다) 「어떤실손의료보험2404」가 한 덩어리일 때
    #:   **이름 전체가 검사에서 빠진다.** 시험이 잡았다 — 「비슷하면 통과」의 문이다.
    #: 참고 — 수집·공시 접미사를 먼저 뗀다. `full_name_key()` 에서만 떼면 여기가 새어
    #:   「_20260701이후」를 표지에서 찾다가 실패한다(실측 84건).
    stem = _COLLECT_DATE_SUFFIX.sub("", unicodedata.normalize("NFKC", product_name or ""))
    stem = _OPTIONAL_WORDS.sub("", _VERSION.sub(" ", stem))
    pos = 0
    core = 0        #: 이 **줄 안에서** 확인한 글자 수 — 알맹이가 줄에 있어야 한다
    started = False  #: 줄 대조가 시작됐나. 시작 전이면 접두로 본다
    identity_confirmed_norm = {_norm(q) for q in identity_confirmed}
    for tok in re.split(r"[\s()\[\],_/]+", stem):
        key = _norm(tok)
        if len(key) < 2 or _FILENAME_DATE.match(tok):
            continue
        if key.lower() in _OPTIONAL_QUALIFIERS:
            continue
        #: 위험 — 정체성 부기를 원천이 확인해 줬으면, 이름 대조 루프에서도 건너뛴다.
        #:   위의 「표지에 있어야 한다」 관문만 봐주고 여기서 다시 찾으면 어차피 막힌다
        #:   (표지에 정말 없으니까) — 시험이 잡았다.
        if key in identity_confirmed_norm:
            started, core = True, core + len(key)
            continue
        #: 위험 — 배지형(`ordered=False`)은 시작점을 안 옮긴다. 태그 순서가 이름
        #:   순서와 다를 수 있어 「직전 토큰 뒤에서부터」 찾으면 못 찾는다 — 창 전체에서
        #:   매번 다시 찾는다. 대신 **모든 토큰이 이 창 안에 있어야** 하는 건 그대로다.
        hit = win.find(key, pos if ordered else 0)
        if hit >= 0:
            started, core = True, core + len(key)
            if ordered:
                pos = hit + len(key)
            continue
        #: 표지가 연결 낱말을 더 쓴 경우만 봐준다 — 토큰 **가운데**에 끼어든 것.
        for ins in _INSERTIONS:
            for cut in range(1, len(key)):
                spliced = key[:cut] + _norm(ins) + key[cut:]
                hit = win.find(spliced, pos if ordered else 0)
                if hit >= 0:
                    started, core = True, core + len(key)
                    if ordered:
                        pos = hit + len(spliced)
                    break
            else:
                continue
            break
        else:
            #: 핵심 — **아직 줄 대조가 시작되기 전**이면 접두로 본다 — 같은 쪽에 있으면 된다.
            #:
            #:   표지는 회사명·브랜드를 **로고 자리**로 뺀다. 그건 언제나 이름 **앞부분**이다.
            #:   브랜드 목록을 코드에 박는 대신 **위치**로 규정한다 —
            #:   「프로미라이프」가 DB손해보험 브랜드라는 것을 코드가 알 필요가 없다.
            #:
            #:   위험 — 대신 **알맹이는 줄에 있어야 한다**(`_COVER_CORE_MIN`). 안 그러면
            #:     「앞부분이 다 쪽 어딘가에 있다」로 이름 전체가 새어 나간다.
            if not started and key in _norm(page):
                continue
            return False
    #: 위험 — 줄에서 확인한 글자가 너무 적으면 그건 이름을 맞춘 것이 아니다.
    return core >= _COVER_CORE_MIN


def _legacy_same_family_rivals(product_name: str, rivals: list[str]) -> list[str]:
    """상대 중 **같은 상품군의, 판본이 나보다 전부 낮은** 것 — 구판 잔존 문구 후보.

    참고 — 자동 확정에 쓰지 않는다. 코덱스 교차검증(2026-08-12)이 반대했다 —

        「내 판본이 더 높다」는 신판의 증거가 아니라 **부재 증거 추론**이다.
        매니페스트 이름 자체가 오표기일 수 있고(2507/2510 실측), 판본 체계가
        다를 수 있고(상품판·특약판·지점 번호), 상대가 잔존 문구가 아니라
        **구약관 전체가 부록으로 포함된 것**일 수도 있다 — `_rivals()` 는
        문맥(개정연혁·구약관 표제) 정보 없이 `flat_doc` 하나만 받아 그 구분을
        할 수 없다.

    참고 — 그래서 이건 **관찰용 표시**다. 사람이 93건을 검토할 때 「내 판본이
      더 높은 것」부터 먼저 보라는 순서 힌트일 뿐, 확정 여부를 바꾸지 않는다.
      실측 2026-08-12 — 93건 중 25건이 같은 상품군+판본 비교 가능이고,
      그중 19건이 이 조건에 해당했다(현대해상 Hi2108 사례로 스팟체크).
    """
    my_ver = version_tokens(product_name)
    if not my_ver:
        return []
    my_fam = family_key(product_name)
    out = []
    for rival in rivals:
        if family_key(rival) != my_fam:
            continue
        riv_ver = version_tokens(rival)
        #: 위험 — 상대 판본이 없으면(범용 이름) 비교 자체가 안 된다 — 넣지 않는다.
        if not riv_ver:
            continue
        if all(rv < mv for rv in riv_ver for mv in my_ver):
            out.append(rival)
    return out


#: 참고 — "개인실손의료보험"은 상품명이기도 하지만 **정의 문구로도 쓰인다**
#:   (실측 2026-08-14 — 현대해상 상품요약서를 직접 크롤링해 확인. 사이트가
#:   약관 PDF 말고 별도로 내주는 안내문서였다 — "그 정보를 크롤링해서 체크하면
#:   되잖아?"라는 지시로 찾았다). 상품요약서 원문:
#:     "* 개인실손의료보험 : 노후·유병력자·여행 실손의료보험 및 단체실손의료보험을
#:      제외한 실손의료보험" — 이건 **범주를 가리키는 용어 정의**지 특정 상품을
#:   가리키는 게 아니다. 문서 안에 "이하 …개인실손…이라 함" 같은 정의절이 있으면
#:   그 문서가 "개인실손의료보험"을 언급해도 경쟁 상품이 실린 게 아니다.
#:   모호 93건 중 "개인실손의료보험"이 상대로 잡힌 42건을 전수 재측정 —
#:   42건 전부(100%) 이 정의절과 함께 나왔다. 예외 없는 신호라 배제해도 안전하다.
_DEFINES_TERM = re.compile(r"이하.{0,3}개인실손")
_TERM_DEFINED_RIVAL = full_name_key("개인실손의료보험")


def _rivals(row: dict, flat_doc: str, siblings: list[dict], *, full_flat: str = "") -> tuple[list[str], list[str]]:
    """이 문서 안에서 **같은 보험사의 다른 상품명**도 통째로 확인되는가.

    반환: `(독립적으로 확인된 본약관, 내 이름을 삼키는 더 긴 상품명)`

    핵심 — **토큰 커버리지는 유일성을 보장하지 않는다**(코덱스 지적 · 2026-08-04 실측으로 확인).

        약관집 PDF 하나에 본약관·특약·비교표가 같이 실린다. 그래서 상품명 토큰이
        전부 나온다고 그 문서가 **그 상품**이라는 뜻이 아니다.

        확정 원장 1,115건을 재측정한 결과 —
          토큰 부분문자열 대조   다른 상품명도 일치 **798건(71.6%)**
          낱말 경계 대조         504건(60.1%)
          참고 — 전체 이름 연속 대조   297건(31.4%)  ← 지금 쓰는 것

    핵심 — 그런데 그 「경쟁」의 3분의 2는 **경쟁이 아니었다**(2026-08-11 전수 재측정).

        모호 240건의 실제 성격 —
          참고 — 상대명이 **내 이름 안에** 있음     161 (67.1%)  ← 매처가 자기 그림자를 밟았다
          내가 특약 · 상대는 내 적용대상        23 ( 9.6%)
          상대명이 「적용대상」 문맥에 있음      24 (10.0%)
          위험 — 내 이름이 상대 이름의 부분            0 ( 0.0%)
          진짜 모호                           32 (13.3%)

        실제 —
          나  : 무배당 흥국화재 다이렉트 실손의료보험(25.07)  →  …실손의료보험2507
          상대: 무배당 흥국화재 다이렉트 실손의료보험        →  …실손의료보험

        `_norm()` 이 괄호·점을 지우므로 **상대 키가 내 키의 접두사**가 된다.
        그러면 `k in flat_doc` 은 내 이름이 문서에 있다는 사실만으로 **반드시 참**이다.
        정보량이 0인 것을 증거로 센 것이다 — 이건 기준의 문제가 아니라 **결함**이다.

    핵심 — 방향이 반대면 정반대로 위험하다.

          나  : …실손의료보험          (짧다)
          상대: …실손의료보험(25.07)   (내 이름을 **포함**한다)

        이때는 `me in flat` 이 상대 이름 때문에 참이 된다 — **내 이름이 확인된 게 아니다.**
        문서는 상대 것일 수 있으므로 **자동 확정을 막아야 한다.**

        위험 — 실측 0건이다. 그래도 남긴다 — 없다는 것을 **재고 나서** 하는 말이라야 하고,
          매니페스트에 상품명이 하나 추가되면 언제든 생길 수 있다.

    참고 — 남은 것은 **조용히 통과시키지 않는다.** 독립적으로 확인된 본약관 상대가 있으면
      자동 확정하지 않는다(`ambiguous`). 특약만 상대인 경우는 정상이다 —
      `resolve()` 가 본약관을 우선하므로 섞이지 않는다.
    """
    from app.core.domain.policy_naming import looks_like_rider

    me = full_name_key(row.get("product_name", ""))
    out: list[str] = []
    shadowed: list[str] = []
    #: 참고 — 정의절은 표지 한 줄이 아니라 본문 약관에 있다. `flat_doc` 이 표지
    #:   줄로 좁혀진 경우를 대비해 문서 전체 텍스트를 따로 받는다.
    term_defined = bool(_DEFINES_TERM.search(full_flat or flat_doc))
    for o in siblings:
        if o.get("sha256") == row.get("sha256"):
            continue
        k = full_name_key(o.get("product_name", ""))
        if not k or k == me or k not in flat_doc:
            continue
        if k == _TERM_DEFINED_RIVAL and term_defined:
            #: 참고 — "개인실손의료보험"이 문서 안에서 정의 문구로 쓰였다 —
            #:   경쟁 상품이 아니라 범주 용어다(위 실측 42/42). 증거로 세지 않는다.
            continue
        if k in me:
            #: 참고 — 내 이름 안에 든 짧은 이름. 내 이름이 확인된 순간 **반드시** 걸린다.
            #:   증거가 아니므로 세지 않는다. 이건 완화가 아니라 잘못된 증거 제거다.
            continue
        if me in k:
            #: 위험 — 거꾸로다. 내 이름이 이 긴 이름 때문에 걸렸을 수 있다 — **막는다.**
            #:   참고 — `continue` 로 빼면 안 된다. 그러면 오히려 통과한다(코덱스 초안의 결함).
            shadowed.append(o.get("product_name") or "")
            continue
        if not looks_like_rider(o.get("product_name") or ""):
            out.append(o.get("product_name") or "")
    return out, shadowed


def _token_in(tok: str, flat: str) -> bool:
    """토큰이 문서에 있나. **별칭까지 본다.**"""
    if _norm(tok) in flat:
        return True
    return any(_norm(a) in flat for a in _NAME_ALIASES.get(tok.strip(), ()))


def load_manifest_rows() -> list[dict]:
    rows, seen = [], set()
    for m in sorted(_MANIFESTS.glob("*.jsonl")):
        for line in m.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            sha = r.get("sha256", "")
            if not sha or sha in seen:
                continue
            seen.add(sha)
            rows.append(r)
    return rows


def _artifact_text(sha12: str, page_tag: str) -> tuple[str, int, list[str]]:
    """페이지 산출물 앞부분 본문. 없으면 `("", 0, [])`.

    핵심 — **페이지 경계를 잃지 않는다.** 예전에는 전부 이어 붙여 돌려주었는데,
      그러면 「1쪽 표지의 이름」과 「4쪽 본문의 이름」을 구분할 수 없다(코덱스 지적).
      표지 대조를 하려면 쪽 단위가 남아 있어야 한다.
    """
    hits = list((_ROOT / "data" / "extracted").glob(f"*/{page_tag}/{sha12}.json"))
    if not hits:
        return "", 0, []
    d = json.loads(hits[0].read_text(encoding="utf-8"))
    pages = d.get("pages") or []
    keep = pages if _SCAN_PAGES is None else pages[:_SCAN_PAGES]
    texts = [(p.get("text") or "") for p in keep]

    #: 핵심 — 글꼴 매핑이 깨진 문서는 **되살려서** 본다.
    #:
    #:   실측 2026-08-11 — 표지가 「⯝G G G 㵜」로 나오는 문서가 5건 있었다.
    #:   처음엔 「추출 실패라 자동으로 못 푼다」고 적었는데 **게으른 결론**이었다.
    #:   `ToUnicode` 없는 CID 글꼴이라 글리프 번호가 일정한 거리만큼 밀린 것뿐이고,
    #:   계산으로 되돌아온다(`app/core/domain/glyph_recovery.py`). 5건 모두 복원됐다.
    #:
    #:   위험 — **되살린 글은 식별에만 쓴다.** 조항 산출물(`s6`)은 여전히 깨진 채이므로
    #:     `extraction_blocked` 는 그대로 둔다 — 식별과 인용은 다른 층이다.
    from app.core.domain.glyph_recovery import looks_broken, recover

    joined = "\n".join(texts)
    if looks_broken(joined):
        rec = recover(joined)
        if rec.recovered:
            #: 쪽 경계를 지키며 되살린다 — 표지 대조가 쪽 단위를 쓴다.
            texts = [recover(t, offsets=[rec.offset]).text if looks_broken(t, min_chars=40)
                     else t for t in texts]
            joined = "\n".join(texts)
    return joined, len(pages), texts


def _parse_status(sha12: str, clause_tag: str) -> str | None:
    hits = list((_ROOT / "data" / "structured").glob(f"*/{clause_tag}/{sha12}.clauses.json"))
    if not hits:
        return None
    return json.loads(hits[0].read_text(encoding="utf-8")).get("parse_status")


def verify(row: dict, *, page_tag: str, clause_tag: str,
           siblings: list[dict] | None = None) -> dict:
    """한 문서의 식별 근거를 모은다. **판정하지 않고 근거를 낸다.**

    참고 — `ok` 가 아닌 이유를 반드시 담는다. 조용히 빼면 「검토했다」가 거짓이 된다.

    Args:
        siblings: 같은 보험사의 다른 매니페스트 행. 주면 **경쟁 상품명 검사**를 한다.
    """
    sha = row.get("sha256", "")
    sha12 = sha[:12]
    out: dict = {"sha256": sha, "insurer": row.get("insurer", ""),
                 "product_name": row.get("product_name", ""),
                 "sale_start": row.get("sale_start", ""),
                 "generation": row.get("generation"),
                 "reasons": []}

    if (row.get("excluded_reason") or "").strip():
        out["reasons"].append(f"판정 제외 문서: {row['excluded_reason']}")
    if (row.get("date_confidence") or "") not in ("exact", "month"):
        #: 참고 — 키가 없는 것도 여기 걸린다. 「모른다」는 「정확하다」가 아니다.
        out["reasons"].append(f"판매시점 신뢰도가 없다: {row.get('date_confidence')!r}")
    ss = row.get("sale_start") or ""
    if not ss or ss == "00000000":
        out["reasons"].append("sale_start 가 비었거나 자리표시자다")

    #: 핵심 — **`parse_status` 를 식별 실패로 세지 않는다**(2026-08-04 · 코덱스 지적).
    #:
    #:   「이 파일이 무엇인가」(식별)와 「조항을 근거로 댈 수 있는가」(인용 가능성)는
    #:   **다른 층**이다. ERD 도 확정 문서와 추출 승인을 나눠 두었고,
    #:   런타임은 판본을 고른 **뒤에** `parse_status` 를 다시 본다
    #:   (`app/core/usecases/precheck.py`). 여기서 또 막으면 층을 섞는 것이다.
    #:
    #:   실측 — `suspect` 52건 중 **51건이 인용 가능 조항 10개 이상**이다.
    #:   식별은 되는데 식별 단계에서 떨어뜨리고 있었다(36건이 이 이유만으로 막혔다).
    #:
    #: 핵심 — 그런데 **그냥 통과시키면 안 된다.** `eligibility.check()` 가
    #:   `parse_status != "ok"` 인 문서의 조항을 **전부** 거절한다. 그래서 확정하면
    #:   판본은 잡히고 인용은 0건이 되어 `no_evidence`(「질병기호로 적힌 조항을
    #:   찾지 못했습니다」)로 답한다 — **실제 이유는 구조화 품질인데 다른 사유를 말한다.**
    #:   그래서 원장에 `extraction_blocked` 로 **표시**해 두고, 판정 쪽이
    #:   그 사유를 쓸 수 있게 남긴다.
    ps = _parse_status(sha12, clause_tag)
    out["parse_status"] = ps
    out["extraction_blocked"] = ps != "ok"

    text, n_pages, page_texts = _artifact_text(sha12, page_tag)
    out["pages"] = n_pages
    if not text:
        out["reasons"].append(f"페이지 산출물이 없다({page_tag})")
        out["ok"] = False
        return out

    #: ── ① 이름 대조 — **강한 식별자 하나**를 요구한다 ──
    #:
    #: 핵심 — 토큰이 문서 여기저기 흩어져 다 나오는 것으로는 **부족하다.**
    #:   전체 상품명이 **한 덩어리로** 있어야 한다. 근거는 `_rivals()` 주석의 실측 표.
    flat = _norm(text)
    me = full_name_key(out["product_name"])
    raw_toks = re.split(r"[\s()\[\],_/]+", unicodedata.normalize("NFKC", out["product_name"]))
    toks = [t for t in raw_toks if len(_norm(t)) >= 2 and not _FILENAME_DATE.match(t)]
    miss = [t for t in toks if not _token_in(t, flat)]
    out["name_match"] = f"{len(toks) - len(miss)}/{len(toks)}"
    out["name_missing"] = miss
    #: ── ①-b 표지 대조 — **공시 이름과 표지 이름은 다르다** ──
    #:
    #: 핵심 — 전수 실측 2026-08-11 — 「한 덩어리로 안 나온다」 286건의 정체는
    #:   조판도 추출 실패도 아니었다. **이름 출처가 둘**이다.
    #:     매니페스트 = 보험사 **공시 목록** 표기
    #:     문서       = 약관 **표지** 표기
    #:
    #:     147건 괄호 부기 차이 · 79건 낱말 하나 · 38건 글자 하나 · 22건 판본 관련
    #:
    #: 참고 — 그래서 표지 한 줄(또는 인접 두 줄)에서 **승인된 변형만** 허용해 다시 본다.
    #:   위험 — 판본 토큰과 정체성 부기는 안 흔든다 — `cover_match()` 주석 참조.
    windows = cover_windows(page_texts)

    #: 참고 — 판본이 없는 이름(실측 55건)은 **가릴 것이 없을 때만** 확정한다.
    #:
    #:   코덱스 설계(2026-08-11)를 그대로 지킨다 — 같은 보험사에 같은 상품군이
    #:   하나뿐이고, 그 하나가 **나 자신**이며, 판매시점을 알 때에 한한다.
    #:   위험 — 하나라도 어긋나면 예외를 주지 않는다. 「판본이 없으니 봐주자」가 아니라
    #:     「판본으로 구별할 대상이 애초에 없다」라야 한다.
    fam = family_key(out["product_name"])
    kin = [o for o in (siblings or [])
           if family_key(o.get("product_name") or "") == fam
           and o.get("sha256") != row.get("sha256")]
    lone = (not version_tokens(out["product_name"])
            and not kin
            and (row.get("date_confidence") or "") in ("exact", "month"))

    #: 참고 — 형제가 있어도 **이 문서 자신이 자기 판매기간을 밝히고**, 그 기간이
    #:   매니페스트의 `sale_start~sale_end` **범위 안**이면 유일하게 식별된 것이다.
    #:   실측 2026-08-11 — 판본 없음+형제 있음 37건 중 9건이 이걸로 풀렸다
    #:   (현대해상 「실손의료비보장 보험료 안정화 할인 특별약관」 분기별 재발행).
    #:
    #:   참고 — 실측 2026-08-14 — 처음엔 `sale_start` **시작월과 정확히 같을 때만**
    #:   인정했다. `sale_end` 를 그때까지 한 번도 안 썼다 — 매니페스트에 이미
    #:   734건에 채워져 있었는데도. 범위로 넓히니 4건이 추가로, **충돌 없이** 풀렸다.
    #:   `sale_end` 가 없으면 시작월 하나로 좁혀져 기존 동작과 같다.
    #:   위험 — `all()` 이다. **문서가 찾은 기간 중 하나라도** 범위 밖이면 막는다 —
    #:   여러 기간이 나오는 문서는 여러 판을 담고 있을 수 있어, `any()` 로 느슨하게
    #:   두면 약한 증거 하나로 통과시키게 된다(검증 스크립트도 `all()` 로 재서 충돌 0건이었다).
    se = row.get("sale_end") or ""
    se6 = se[:6] if se else ss[:6]
    found_periods = _applicable_period_months(text)
    date_anchored = (not version_tokens(out["product_name"])
                     and (row.get("date_confidence") or "") in ("exact", "month")
                     and bool(found_periods)
                     and all(ss[:6] <= ym <= se6 for ym in found_periods))

    #: 참고 — 원천 URL이 상품명의 판본보다 **더 정확할 때가 있다.** 실측 2026-08-12 —
    #:   상품명은 낡은 판본을 달고 있는데, 원천 사이트가 파일에 붙인 URL 날짜와
    #:   매니페스트 `sale_start` 가 서로 독립적으로 일치했다(6건). 표지 하나만 보고
    #:   판단하지 않는다 — **두 출처가 맞아떨어질 때만** 판본을 정정한다.
    my_name_ver = version_tokens(out["product_name"])
    url_ym = url_tail_ym(row)
    #: 위험 — `url_ym` 은 YY+MM(4자리) 다. `ss[:6]` 은 YYYY+MM(6자리) — 자릿수가
    #:   달라 **항상 거짓**이었다(오늘 세 번째 같은 실수 — `_gen_splits_month`·
    #:   `date_anchor_gain.py` 에서도 겪었다). `ss[2:6]` 이라야 같은 자릿수로 맞는다.
    #:
    #:   위험 — 코덱스 지적(2026-08-12) — `ss` 가 정말 `YYYYMMDD` 8자리인지 그때까지
    #:   확인 안 했다. `"2202"` 처럼 짧거나 숫자가 아닌 값이 들어오면 `[2:6]` 이
    #:   조용히 엉뚱한 조각을 잘라 우연히 일치할 수 있다. **형태부터 확인한다.**
    ss_is_ymd = bool(re.fullmatch(r"\d{8}", ss))
    url_corrects_version = bool(
        ss_is_ymd and my_name_ver and url_ym and url_ym not in my_name_ver and url_ym == ss[2:6])
    identity_confirmed = url_confirmed_qualifiers(row, out["product_name"])

    #: 위험 — 먼저 **URL 보정 없이** 시도한다. 「URL이 확인해 줬다」와 「URL이 없어도
    #:   이미 통과했다」를 못 가르면, 이미 통과하던 수백 건까지 잘못 「url_anchored」로
    #:   찍는다(실측 211건 — 실제로 필요했던 건 8건뿐이었다). 기본 경로가 실패할 때만
    #:   보정을 쓴다.
    hit = next(((pg, w) for pg, w, ordered in windows
                if cover_match(out["product_name"], w, page=page_texts[pg - 1],
                               lone_in_family=(lone or date_anchored), ordered=ordered)), None)
    if not hit and (url_corrects_version or identity_confirmed):
        hit = next(((pg, w) for pg, w, ordered in windows
                    if cover_match(out["product_name"], w, page=page_texts[pg - 1],
                                   lone_in_family=(lone or date_anchored), ordered=ordered,
                                   expected_version=({url_ym} if url_corrects_version else None),
                                   identity_confirmed=identity_confirmed)), None)
        if hit:
            out["url_anchored"] = {
                "정정판본": url_ym if url_corrects_version else None,
                "확인된부기": sorted(identity_confirmed) or None,
                "url": row.get("url"),
            }
    if hit and lone:
        out["lone_in_family"] = True
    if hit and date_anchored and kin:
        out["date_anchored"] = True
    out["cover_page"] = hit[0] if hit else None
    out["cover_name"] = hit[1][:120] if hit else None

    #: 위험 — 판본 충돌 — 이름은 같은데 판본만 다르면 **문서가 잘못 붙은 것**이다.
    #:   실측: 「…안정화 할인 특별약관2507」 행에 표지 「…특별약관2510」 문서.
    #:   이름 대조가 아무리 좋아도 통과시키면 안 된다.
    my_ver = version_tokens(out["product_name"])
    if my_ver and not hit:
        #: 이름에서 판본만 뺀 줄기가 표지에 그대로 있는데 판본이 다르면, 같은 상품의 **다른 판본**이다.
        #:
        #: 위험·핵심 — 처음엔 `\(?\b\d{2}[.\-]?\d{2}\b\)?` 로 판본을 뗐는데 **아무것도 떼지 못했다.**
        #:   「특별약관2507」에서 `관` 과 `2` 는 **둘 다 낱말 문자**라 `\b` 가 성립하지 않는다.
        #:   그래서 줄기에 판본이 남았고, 충돌 검사가 조용히 0건을 내놓았다.
        #:   참고 — 이미 만들어 둔 `_VERSION` 을 쓴다 — 같은 개념을 두 번 쓰지 않는다.
        stem = _norm(_VERSION.sub(" ", out["product_name"]))
        for _, w, _ordered in windows:
            wv = version_tokens(w)
            if not wv or wv == my_ver or len(stem) < 8 or stem not in _norm(w):
                continue
            #: 참고 — 원천까지 추적해 둔다. 실측(2026-08-11, DB손해보험) — 매니페스트가 아니라
            #:   **원천 공시 사이트 자체**가 `product_code` 가 다른 두 파일에 같은 이름을
            #:   붙여 놓았다(9184 vs 10002, 판매일 2025-07 vs 2025-10). 「사람이 열어서
            #:   고른다」가 아니라 **어느 근거가 무엇을 가리키는지**까지 적어 둔다.
            out["version_conflict"] = {
                "매니페스트_이름": sorted(my_ver),
                "표지_판본": sorted(wv),
                "product_code": row.get("product_code"),
                "sale_start": row.get("sale_start"),
                "date_source": row.get("date_source"),
            }
            out["reasons"].append(
                f"판본 충돌: 매니페스트 이름은 {sorted(my_ver)} 인데 표지는 {sorted(wv)} 다"
                f" — product_code={row.get('product_code')} · sale_start={row.get('sale_start')}."
                f" 같은 이름을 가진 다른 product_code 가 있는지 확인할 것"
                f"(원천 목록 자체가 잘못 붙였을 수 있다)")
            break

    if not me:
        out["reasons"].append("상품명이 비어 있어 대조할 수 없다")
    elif me not in flat and not hit:
        detail = f", 빠진 토큰 {miss[:3]}" if miss else " — 토큰은 다 있으나 흩어져 있다"
        out["reasons"].append(
            f"전체 상품명이 문서에 한 덩어리로 나오지 않는다(토큰 {out['name_match']}{detail})")

    #: ── ② 문서가 스스로 밝힌 판매일 ──
    squeezed = text.replace(" ", "")
    found = sorted({(lab, f"{m.group(1)}{int(m.group(2)):02d}")
                    for pat, lab in _DATE_PATTERNS for m in pat.finditer(squeezed)})
    out["doc_dates"] = [f"{lab}:{ym}" for lab, ym in found]
    if found:
        #: 핵심 — **두 날짜는 같은 것이 아니다.** 처음엔 다르면 탈락시켰는데 틀렸다.
        #:
        #:     `sale_start`   **상품**이 팔리기 시작한 때
        #:     표지 판매개시   **이 판본 문서**가 효력을 가진 때
        #:
        #:   실측 2026-08-04 — 메리츠화재는 `sale_start=20260501`(상품명 "2605" 에서
        #:   추론)인데 표지에 「판매개시 2026. 7. 13 · 판매버전 3.0」 이라 적혀 있다.
        #:   **모순이 아니라 개정이다.** 같은 것으로 보고 9건을 통째로 떨어뜨렸다.
        #:
        #:   참고 — 그렇다고 표지 날짜를 `sale_start` 로 삼아도 안 된다 —
        #:   그러면 2026년 6월 가입자가 「해당 시점 약관 없음」 이 된다. 약관은 있는데.
        #:
        #:   그래서 **뒤면 통과, 앞서면 막는다.** 문서가 상품보다 먼저 존재할 수는 없다.
        earliest = min(ym for _, ym in found)
        if any(ym == ss[:6] for _, ym in found):
            pass  # 월까지 같다 — 가장 강한 일치
        elif earliest >= ss[:6]:
            #: 개정판. 근거 등급을 낮춰 남긴다 — **통과시키되 같은 급으로 치지 않는다.**
            out["doc_dates"] = [*out["doc_dates"], f"개정추정(sale_start {ss[:6]} 이후)"]
        else:
            out["reasons"].append(
                f"문서가 밝힌 판매시점 {earliest} 이 상품 판매개시 {ss[:6]} 보다 **앞선다** "
                f"— 문서가 상품보다 먼저 있을 수는 없다(전체 {[y for _, y in found]})")
    else:
        #: 참고 — 근거가 **없는 것**과 **어긋나는 것**은 다르다. 없다고 탈락시키지 않되 적어 둔다.
        out["doc_dates"] = []

    #: ── ③ 세대 ──
    g = row.get("generation")
    exp = None
    for gen, a, b in _generation_ranges():
        if (a is None or ss >= a) and (b is None or ss <= b):
            exp = gen
            break
    out["generation_expected"] = exp
    #: 핵심 — **세대가 비었다고 탈락시키지 않는다.** 처음엔 그렇게 했는데 **계약 위반**이었다.
    #:
    #:   `docs/handoff/02_ERD_및_스키마.md` 가 못박아 둔 것 —
    #:     「`generation` 을 NULL 허용으로 두는 것이 이 표에서 가장 중요하다.
    #:      모르는 세대를 숫자로 채우면 그 오류가 판정까지 간다.
    #:      판정에서 참조할 수 있는 것은 `date_confidence <> 'unknown'` 인 버전뿐이다.」
    #:
    #:   즉 게이트는 **판매시점**이지 세대가 아니다. `usable_for_judgment` 도
    #:   세대를 요구하지 않는다(`app/core/ports/precheck.py`). 이 도구만 더 엄격했다.
    #:
    #:   실측 2026-08-04 — 그 규칙 하나로 **363건**이 떨어졌다. 그중 287건은
    #:   `generation_confidence="not_applicable"` 이다. 세대 축이 **적용되지 않는**
    #:   상품이라 비어 있는 게 정상인데 결함으로 셌다.
    #:
    #:   참고 — **값이 있는데 규칙과 다른 것**은 여전히 막는다. 그건 모름이 아니라 모순이다.
    #:
    #: 핵심 — 단, **모순이라고 말하려면 규칙 쪽이 확정적이어야 한다.**
    #:
    #:   실측 2026-08-11 — 3건이 이 규칙으로 떨어졌다.
    #:     (무)헤아림다이렉트실손의료비보험(전환계약용)2605  매니페스트 5 · 규칙 4
    #:
    #:   판매일은 `20260501` 인데 `date_confidence="month"` 다. 출처는 **상품명 코드**
    #:   (`2605` = 2026년 5월)라 **일(日)을 모른다.** 5세대 경계는 `2026-05-06` 으로
    #:   **그 달 가운데**에 있다. 즉 모르는 일자를 1일로 채운 뒤 그 값으로
    #:   「규칙은 4세대」라고 선언한 것이다 — 채운 값이 결론을 만들었다.
    #:
    #:   참고 — 그래서 **그 달 안에서 세대가 갈리면 날짜는 아무것도 말해 주지 않는다.**
    #:     모순이 아니라 **판정 불가**다. 판정 불가를 모순으로 부르면 안 된다.
    if g is not None and exp is not None and g != exp:
        if (row.get("date_confidence") or "") == "month" and _gen_splits_month(ss):
            #: 날짜로는 가릴 수 없다. 막지 않되 **적어 둔다** — 조용히 넘기지 않는다.
            out["generation_expected"] = f"{exp}?(월 안에서 갈림)"
        else:
            out["reasons"].append(f"세대가 규칙과 다르다: 매니페스트 {g} · 규칙 {exp}")

    #: ── ④ 경쟁 상품명 — **유일하게 식별되는가** ──
    #:
    #: 참고 — 이름이 맞는 것과 **그 이름만 맞는 것**은 다르다. 약관집에 본약관과 특약이
    #:   같이 실리므로, 같은 보험사의 다른 **본약관**까지 통째로 나오면 자동 확정하지 않는다.
    #:   (특약만 상대인 경우는 정상 — `resolve()` 가 본약관을 우선한다.)
    #:
    #: 핵심 — **근거의 범위를 맞춘다**(코덱스 지적 2026-08-11). 이름을 표지에서 맞췄는데
    #:   경쟁 검사만 문서 전체로 하면 「내 근거는 한 줄, 상대 근거는 100쪽」이 된다.
    #:   그 비대칭은 표지로 맞춘 건을 거의 다 모호로 떨어뜨린다 — 기준이 아니라 **자의**다.
    if me and me in flat:
        #: 문서 전체에서 내 이름이 통째로 확인된 경우 — 상대도 문서 전체에서 본다.
        out["rivals"], out["shadowed_by"] = _rivals(row, flat, siblings or [], full_flat=flat)
    elif hit:
        #: 표지 한 줄로 맞춘 경우 — 상대도 **같은 표지 줄들** 안에서만 본다.
        #: 참고 — `full_flat=flat` 은 예외다. 정의절 탐지만은 문서 전체를 본다
        #:   (표지엔 정의절이 없다) — 상품 일치 판단 자체는 여전히 표지로 좁힌다.
        out["rivals"], out["shadowed_by"] = _rivals(
            row, _norm(" ".join(w for _, w, _ordered in windows)), siblings or [], full_flat=flat)
    else:
        out["rivals"], out["shadowed_by"] = [], []
    if out["shadowed_by"]:
        #: 위험 — 이름 대조 자체가 성립하지 않는다. 확정률보다 **먼저** 막는다.
        out["reasons"].append(
            f"내 상품명이 더 긴 상품명 {len(out['shadowed_by'])}건에 삼켜져 있다 — "
            f"이 문서가 그쪽 것일 수 있다: {out['shadowed_by'][:2]}")
    if out["rivals"]:
        out["reasons"].append(
            f"같은 문서에서 다른 본약관 {len(out['rivals'])}건도 확인된다 — 사람이 골라야 한다"
            f": {out['rivals'][:2]}")
        #: 참고 — 관찰용 표시일 뿐 확정 여부는 안 바꾼다. `_legacy_same_family_rivals()`
        #: 주석 참조 — 코덱스가 자동 확정에는 반대했다. 검토 순서 힌트만 남긴다.
        legacy = _legacy_same_family_rivals(out["product_name"], out["rivals"])
        if legacy:
            out["legacy_rivals"] = legacy

    out["ok"] = not out["reasons"]
    #: 참고 — 근거 등급. 문서 안에서 판매일까지 확인되면 한 단 강하다.
    if not out["ok"]:
        #: 위험 — 「삼켜짐」은 모호와 다르다 — 모호는 둘 중 고르는 것이고 이건 대조가 안 된 것이다.
        out["evidence"] = ("version_conflict" if out.get("version_conflict")
                           else "shadowed" if out["shadowed_by"]
                           else "ambiguous" if out["rivals"] else "-")
    elif out["extraction_blocked"]:
        #: 식별은 됐지만 **인용은 못 한다.** 판정 쪽이 이 사실을 말할 수 있어야 한다.
        out["evidence"] = "extraction_blocked"
    elif out.get("date_anchored"):
        #: 참고 — 판매기간 진술로 유일하게 식별된 것도 **그렇다고 적는다.**
        out["evidence"] = "date_anchored"
    elif out.get("url_anchored"):
        #: 참고 — 원천 URL로 상품명의 낡은 판본을 정정했거나 정체성 부기를 확인한 것도
        #:   **그렇다고 적는다** — 표지 문구만으로 맞춘 것과 같은 등급이 아니다.
        out["evidence"] = "url_anchored"
    elif me not in flat:
        #: 참고 — 표지 이름으로 맞춘 것은 **그렇다고 적는다.** 공시 이름으로 맞춘 것과
        #:   같은 등급으로 뭉치면, 나중에 이 규칙이 틀렸을 때 어느 건이 영향받는지 모른다.
        out["evidence"] = "cover_name"
    else:
        out["evidence"] = "name+date" if out["doc_dates"] else "name_only"
    return out


def _release() -> tuple[str, str]:
    sys.path.insert(0, str(_ROOT))
    from app.core import release

    r = release.current()
    return r.page_tag, r.clause_tag


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", action="store_true", help="근거만 낸다(쓰지 않음)")
    ap.add_argument("--apply", action="store_true", help="통과한 것을 원장에 쓴다")
    ap.add_argument("--confirmed-by", default="", help="참고 — 누가 확정했나. `--apply` 에 필수")
    ap.add_argument("--scope", default="demo", help="확정 범위 표시(demo/full)")
    ap.add_argument("--limit", type=int, default=0, help="후보 상한(0=전량)")
    ap.add_argument("--insurer", default="", help="보험사로 좁힌다")
    ap.add_argument("--rebuild", action="store_true",
                    help="참고 — 원장을 **다시 심사**한다. 기준이 바뀌면 기존 항목도 다시 봐야 한다 — "
                         "덧붙이기만 하면 통과율이 「기준 완화 이력의 합」이 된다. "
                         "사람이 승인한 항목은 지우지 않고 보고만 한다")
    ap.add_argument("--spread", action="store_true",
                    help="참고 — 통과분에서 **보험사×세대마다 1건**만 남긴다(데모용 — 전 경로를 훑기 위함). "
                         "근거가 강한 것(name+date)을 먼저 고른다")
    a = ap.parse_args(argv)

    if a.apply and not a.confirmed_by.strip():
        raise SystemExit("참고 — `--confirmed-by` 가 필요합니다. 누가 확정했는지 안 남기면 확정이 아닙니다.")
    if not (a.report or a.apply):
        raise SystemExit("참고 — `--report` 또는 `--apply` 중 하나를 고르세요.")

    page_tag, clause_tag = _release()
    rows = load_manifest_rows()
    if a.insurer:
        rows = [r for r in rows if r.get("insurer") == a.insurer]

    #: 경쟁 상품명 검사용 — 같은 보험사 행을 미리 묶는다.
    import collections as _c
    by_insurer = _c.defaultdict(list)
    for _r in rows:
        by_insurer[_r.get('insurer', '')].append(_r)

    results, skipped = [], 0
    for r in rows:
        #: 참고 — 싸게 거를 수 있는 것을 먼저 걸러 산출물 읽기를 아낀다.
        #:   단 **센다** — 조용히 빼면 분모가 줄어 통과율이 좋아 보인다(CLAUDE.md §3).
        if (r.get("excluded_reason") or "").strip() or \
           (r.get("date_confidence") or "") not in ("exact", "month"):
            skipped += 1
            continue
        results.append(verify(r, page_tag=page_tag, clause_tag=clause_tag,
                              siblings=by_insurer.get(r.get('insurer', ''), [])))
        if a.limit and len(results) >= a.limit:
            break

    ok = [x for x in results if x["ok"]]
    print(f"매니페스트 {len(rows):,} · 사전탈락 {skipped:,}(제외사유·판매시점 미상) · "
          f"검사 {len(results):,} · 통과 {len(ok):,}", flush=True)

    if a.spread:
        #: 참고 — 근거 강한 것 먼저, 그 다음 판매시점이 이른 것. **결정적으로** 고른다 —
        #:   같은 명령을 두 번 돌려 다른 문서가 확정되면 재현이 안 된다.
        best: dict[tuple, dict] = {}
        for x in sorted(ok, key=lambda x: (x["evidence"] != "name+date",
                                           x["sale_start"], x["sha256"])):
            best.setdefault((x["insurer"], x["generation"]), x)
        dropped = len(ok) - len(best)
        ok = sorted(best.values(), key=lambda x: (x["insurer"], x["generation"] or 0))
        #: 참고 — 얼마나 뺐는지 **말한다.** 조용히 줄이면 "이만큼 확정했다"가 실제보다 커 보인다.
        print(f"--spread: 보험사×세대 {len(ok)}건만 남김(통과분에서 {dropped:,}건 보류)")
    by_ev: dict[str, int] = {}
    for x in ok:
        by_ev[x["evidence"]] = by_ev.get(x["evidence"], 0) + 1
    print(f"근거 등급: {by_ev}  (name+date = 문서가 판매시점까지 밝힘)")

    if a.report:
        #: 핵심 — `--spread` 를 켜면 **머리말은 10건인데 본문은 132건**을 찍고 있었다(2026-08-04).
        #:   보고서가 자기 요약과 어긋나면 읽는 사람이 어느 쪽을 믿을지 모른다.
        #:   좁혔으면 좁힌 것을 보여준다. 탈락분은 좁히기 전에만 의미가 있다.
        shown = ok if a.spread else results
        print()
        for x in shown:
            mark = "OK " if x["ok"] else "NG "
            print(f"{mark}{x['insurer']:<10} {x['sha256'][:12]} {x['generation']}세대 "
                  f"{x['sale_start']} 이름{x.get('name_match','-')} "
                  f"{x.get('evidence','-')} {x.get('doc_dates') or ''}")
            for why in x["reasons"]:
                print(f"      · {why}")
            print(f"      {x['product_name'][:70]}")

    if a.apply:
        from datetime import date

        _LEDGER.parent.mkdir(parents=True, exist_ok=True)
        existing = {}
        if _LEDGER.exists():
            for line in _LEDGER.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    e = json.loads(line)
                    existing[e["sha256"]] = e
        dropped = 0
        if a.rebuild:
            #: 핵심 — 기준이 바뀌면 **옛 기준으로 들어온 것을 반드시 다시 심사한다.**
            #:   덧붙이기만 하면 통과율이 「기준을 느슨하게 했던 이력의 합」이 된다.
            #:   실측 2026-08-04 — 강한 식별자를 넣기 전 1,115건이 들어와 있었는데
            #:   새 기준으로는 850건만 통과한다. 265건은 **빼야 한다.**
            keep = {x["sha256"] for x in ok}
            for sha, e in list(existing.items()):
                if sha in keep:
                    continue
                if "대기" not in (e.get("confirmed_by") or ""):
                    #: 참고 — 사람이 승인한 것은 **지우지 않는다.** 기계 기준이 사람 결정을 덮으면 안 된다.
                    print(f"  참고 — 사람 승인 항목이 새 기준에서 탈락 — 남겨 둡니다: "
                          f"{sha[:12]} {(e.get('product_name') or '')[:34]}")
                    continue
                del existing[sha]
                dropped += 1
        added = refreshed = 0
        for x in ok:
            prev = existing.get(x["sha256"])
            if prev is not None:
                #: 핵심 — **살아남았다고 그냥 두면 옛 기준의 근거가 남는다.**
                #:   `--rebuild` 가 탈락만 지우고 통과분을 갱신하지 않아,
                #:   새로 생긴 `extraction_blocked` 필드가 기존 행에 안 붙었다
                #:   (회귀 시험이 잡았다 · 2026-08-04). 기준이 바뀌면 **근거도 다시 쓴다.**
                #:   참고 — 사람이 승인한 것은 손대지 않는다.
                if not a.rebuild or "대기" not in (prev.get("confirmed_by") or ""):
                    continue
                refreshed += 1
            else:
                added += 1
            existing[x["sha256"]] = {
                "sha256": x["sha256"],
                "insurer": x["insurer"],
                "product_name": x["product_name"],
                "identification": "confirmed",
                #: 참고 — `reviewed` 가 아니라 `partial` 이다 —
                #:   `generation_profiles.json` 자신이 `review_status: "partial"` 이라고 적고 있다.
                #:   규칙셋이 부분 검토인데 그걸 근거로 한 판정을 완전 검토라 할 수 없다.
                "generation_review": "partial",
                "confirmed_at": date.today().isoformat(),
                "confirmed_by": a.confirmed_by.strip(),
                "scope": a.scope,
                "evidence": x["evidence"],
                #: 참고 — 인용 가능성은 식별과 **다른 층**이다. 확정했다고 근거를 댈 수 있는 건 아니다.
                "extraction_blocked": x.get("extraction_blocked", False),
                "basis": {
                    "name_match": x.get("name_match"),
                    "doc_dates": x.get("doc_dates"),
                    "generation_expected": x.get("generation_expected"),
                    "parse_status": x.get("parse_status"),
                    "page_tag": page_tag,
                    "clause_tag": clause_tag,
                },
            }
        with _LEDGER.open("w", encoding="utf-8") as f:
            for sha in sorted(existing):
                f.write(json.dumps(existing[sha], ensure_ascii=False) + "\n")
        print(f"\n원장 추가 {added:,} · 제거 {dropped:,} · 총 {len(existing):,}건 → {_LEDGER}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
