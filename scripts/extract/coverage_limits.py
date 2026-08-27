"""보장한도·자기부담금 표 → **판정이 집을 수 있는 레코드**.

★왜 필요한가 (계획서 L3)

    자기부담금 표는 2차원이 아니다. 실측한 축은 **다섯**이다 —
    `가입형 × 상해/질병 × 급여구분 × 입통원 × 기관종별`.
    지금 `tables_coords[].records` 는 `{"1": 좌, "2": 우}` 처럼 **평평한 열 짝**이라
    "상급종합병원 통원 자기부담금"을 집을 수 없다.

★★먼저 확인한 사실 — **자기부담금 표는 `tables_coords` 에 거의 없다**

    전량 1,367문서를 훑었다(2026-08-03).

      자기부담금 키워드(`자기부담금|본인부담금|상급종합병원|보상한도|보장한도`)를
      가진 `tables_coords` 표          13,263개
        그중 `선`(괘선 격자, 조항에 실리는 유일한 경로)      **3개**
        나머지 13,260개는 전부 `2열짝짓기` → **보류 중이라 조항에 안 실린다**

      게다가 그 `선` 3개(현대해상 `09db07e32346`·`4e39dccf59ce`·`b3b339438e49` p3)를
      열어 보니 자기부담금 표가 아니라 **임신·출산 보험금 계산 예시 표**였다.
      `본인부담금` 이라는 낱말 때문에 걸린 것이다.

    → **`tables_coords` 만 입력으로 삼으면 이 파서는 영원히 0건을 낸다.**

★그러면 표는 어디에 있나 — `pages[].tables` (PyMuPDF `find_tables`)

    같은 페이지 JSON 의 **다른 키**에 격자가 들어 있었다. 실측 —

      `상급종합병원` + `공제|자기부담` + 금액 토큰 4개 이상인 페이지   1,958쪽
        그중 `pages[].tables` 에 3열 이상·3행 이상 격자가 있는 쪽    **1,279쪽 (65.3%)**

      회사별  삼성화재 374/415 · 흥국화재 195/434 · 롯데손보 172/173 ·
              삼성생명 259/367 · 현대해상 122/248 · 나은생명 53/70 ·
              KB손보 51/74 · 동양생명 34/34 · DB손보 15/118 ·
              흥국생명 4/20 · 메리츠화재 0/2 · NH손보 0/3

    ★표본이 작은 회사(메리츠 2쪽·NH손보 3쪽)의 0/N 을 "실패율 100%"라고 읽지 않는다.
      후보 자체가 몇 쪽 없다.

    그래서 이 파서는 **두 모양을 모두 받는다** —
      (가) `tables_coords` 표 dict  — 계획서가 정한 계약. 실측상 지금은 거의 안 걸린다
      (나) `pages[].tables` 원소    — `list[list[str|None]]` 격자. **실제로 값이 여기 있다**

★내가 실제로 눈으로 확인한 표 (10개 · 9개사)

    흥국화재 `0e5af513f7d6` p10   4세대. 5열. 급여/비급여 × 입원/통원 × 의료기관①②③
    흥국화재 `0149a994930a` p32   3세대. 4열. 2종(선택형Ⅱ)
    DB손보   `2c2c4f679faa` p24   3세대. `<표1 항목별 공제금액>` 표준형·선택형Ⅱ
    삼성화재 `907806fc82d8` p7    3세대. 4열이 **페이지 단 넘김에서 셀이 쪼개짐**
    삼성화재 `01d17da13fe7` p15   요약서. 6열. 한 셀에 `- 의원 등 : 1만원` 여러 줄
    KB손보   `6060b8bda3fa` p10   3세대. 4열. **가장 깨끗하다**
    롯데손보 `1b922d9f78a4` p44   3세대. 4열. KB와 같은 조판
    현대해상 `c6230695c978` p29   3세대. 4열 × 격자 2개. 표준형/선택형Ⅱ/선택형
    삼성생명 `010d042bf502` p27   3세대. 4열이지만 **축 라벨이 여러 행으로 쪼개짐**
    동양생명 `002496fe7873` p32   3세대. **10열**(빈 스페이서 열 6개)

★★`담보종목` 열은 **격자 밖이 아니라 옆 격자에 있었다** (2026-08-03 실측)

    앞선 리포트는 "`find_tables` 격자가 담보종목 열을 대부분 포함하지 않는다"고 적었다.
    데이터를 직접 보니 **절반만 맞았다.** `find_tables` 는 같은 표를 **격자 두 개**로 낸다 —

      tables[0]  바깥 2열 표   [['담보종목','보상하는 사항'], ['(2)\n상해통원', '<안쪽 표 전체>']]
      tables[1]  안쪽 자기부담금 격자  [['구분','','항목','공제금액'], ['표\n준\n형', …]]

    파서는 tables[1] 만 보고 있었다. 담보종목은 tables[0] 왼쪽 칸에 멀쩡히 있었다.

    ★`tables_coords` 는 이 문제의 답이 아니다. ok 레코드가 나온 1,439쪽 중
      **1,329쪽(92.3%)은 `tables_coords` 가 아예 비어 있고**, 있는 110쪽에서도
      담보종목이 걸린 표는 **0개**였다. 좌표 표로 갈아타도 이 축은 안 채워진다.

    그래서 **바깥 격자에서 되찾는다**. 되찾아도 되는지는 **포함관계로 확인**한다 —
    안쪽 격자의 각 줄(공백 제거, 4자 이상)이 바깥 행의 나머지 칸 안에 부분문자열로
    들어 있는 비율. 실측 분포가 깨끗하게 갈린다(라벨 후보가 있는 격자 262개):

      비율 1.0  253개 · 0.9  6개 · 0.2  2개 · 0.0  1개

    즉 "바깥 행이 이 격자를 통째로 감싼다"가 **되거나 안 되거나** 둘 중 하나다.
    0.8 을 문턱으로 잡으면 259개가 걸리고 3개가 빠진다.

    ★**포함관계가 확인된 것만 사실이다.** 같은 쪽에 라벨이 하나뿐이라는 것만으로는
      추론이다(`inferred=true`). 그 쪽 아래쪽에서 새 담보종목 절이 시작하는데
      표는 앞 절 것일 수 있다. 자리가 가깝다는 것은 같은 표라는 증거가 아니다.

★★`한도금액`·`한도단위`·`단위` 는 **지웠다** — 이 표에 없는 필드였다 (RULE.md §3.3)

    전량 10,726레코드의 금액 셀을 전수 조사했다.

      `_CAP`(`…원 한도`)가 걸린 금액 셀            **0개**
      `한도` 라는 낱말이라도 든 금액 셀            77개 (**전부 `unparsed`** — 격자가
                                                  무너져 본문이 칸에 들어온 것)
      `_UNIT_RULES`(방문1회·처방전1건·1일·1회)가 걸린 셀  14개 (**전부 `unparsed`**)

    격자 셀 전체로 넓히면 `…원 한도` 는 194개 걸리는데 전부
    `연간 300만원 한도` · `보험가입금액 한도` 였다. 그건 **보장한도(연간 총액)**이지
    자기부담금 표의 값이 아니다. **다른 표의 필드**를 이 표 스키마에 넣어 뒀던 것이다.

    ★원문에 아예 없다는 뜻은 아니다. 흥국화재 4세대
      `비급여 병실료의 50%, 1일 평균금액 10만원 한도` 는 **쪽 본문에는 있는데
      `find_tables` 격자가 그 열을 통째로 잃는다**(아래 ★못 읽는 것 참조).
      즉 값이 없는 이유는 "원문에 없어서"가 아니라 "이 파서의 입력에 안 들어와서"다.
      되살리려면 4세대 금액 열 유실을 먼저 고쳐야 한다. 그때 필드를 **다시 만든다.**
      지금 비워 둔 채로 두면 판정이 "한도 없음"으로 읽는다 — 그게 더 위험하다.

★지어내지 않는다 (CLAUDE.md §1)

    - 금액·비율을 못 읽으면 `None` 이다. 0 으로 채우지 않는다.
    - 축 라벨이 병합셀 쪼개짐으로 안 붙으면 `None` 이다. 위 행에서 끌어오지 않는다.
    - `의료기관①` 처럼 **각주로 정의되는 축**은 원문만 남기고 `기관종별=None` 이다.
      각주(`주1) 의료기관① : …`)는 표 밖에 있어서 표 dict 만으로는 못 푼다.
    - 값을 못 읽은 행도 **레코드로 남긴다**(`parse_status="unparsed"`).
      조용히 버리면 분모가 줄어 커버리지가 실제보다 좋아 보인다(CLAUDE.md §3).
"""

from __future__ import annotations

import re

#: ── 축 정규화 사전 ─────────────────────────────────────────────────────────
#:
#: ★모두 **실측한 셀 원문**에서 뽑았다. 사전에 없는 표기는 `None` 으로 둔다.


#: ★★**쪽 경계에서 잘린 행을 잡는다.**
#:
#:   실측(KB손보 `224bd44994c3` p5→p6):
#:     p5 끝  `…「의료법」 제3조 제2항 제3호에 따른 종` + `1만`
#:     p6 앞  `합병원, 병원, 치과병원, 한방병원, 요양병원` + `5천원과 …`
#:   원문은 `1만 5천원`(**15,000원**)인데 `parse_page` 가 한 쪽만 받으므로
#:   `5천원` → **5,000원** 으로 읽는다. 그리고 `parse_status="ok"` 로 나간다.
#:
#:   ★자기부담금이 1/3 로 줄면 **가입자에게 유리한 쪽으로 틀린다** —
#:     "덜 부담한다"고 답하고 실제 청구에서 뒤집힌다(CLAUDE.md §0).
#:
#:   ★**표준값으로 고치지 않는다.** 그건 지어내는 것이다(§1).
#:     잘린 흔적이 보이면 **의심 표시만** 하고 판정이 기권하게 둔다.
#:
#:   신호: 기관 이름이 **어절 중간에서 시작**한다(`합병원` ← `종합병원`).
#:   기관종별 사전의 표제어 중 어느 것의 **꼬리**로 시작하면 잘린 것이다.
#:   실측한 두 모양:
#:     `합병원, 병원, 치과병원, …`   ← `종합병원` 의 꼬리로 시작
#:     `원, 병원, 치과병원, …`       ← `종합병원` 의 **한 글자** 꼬리로 시작
#:
#:   그래서 **쉼표로 끊은 첫 토막이 온전한 기관명인가**를 본다.
#:   온전한 목록은 `종합병원,` · `의원,` 처럼 사전에 있는 말로 시작한다.
_ORG_WORDS = ("상급종합병원", "종합병원", "치과병원", "한방병원", "요양병원",
              "정신병원", "병원", "치과의원", "한의원", "보건소", "보건지소",
              "보건의료원", "보건진료소", "조산원", "의원", "약국")


def _looks_truncated(raw: str) -> bool:
    """기관 이름이 **잘린 채 시작**하는가.

    ★쉼표로 끊은 **첫 토막**을 본다. 그게 기관명 사전의 말이 아니고
      짧으면(4자 미만) 앞이 잘린 것이다 — `합병원` · `원`.
    ★사전에 있으면 통과시킨다. `병원` 하나로 시작하는 온전한 목록도 있다.
    """
    t = (raw or "").strip()
    if not t:
        return False
    #: 구두점으로 시작하면 앞이 확실히 잘렸다
    if t[0] in ",·、":
        return True
    head = t.split(",")[0].split(chr(10))[0].strip()
    if not head:
        return False
    if head in _ORG_WORDS:
        #: ★★사전에 있는 말이어도 **바로 뒤가 같은 말이면** 잘린 것이다.
        #:   실측(KB손보 `3b8f46b78174` p12): `병원, 병원, 치과병원, …`
        #:   원문은 `종합병원, 병원, 치과병원, …` 인데 `종합` 이 앞 쪽에 남았다.
        #:   같은 기관을 두 번 쓰는 약관은 없다 — 중복 자체가 절단의 지문이다.
        parts = [x.strip() for x in t.replace(chr(10), "").split(",") if x.strip()]
        if len(parts) >= 2 and parts[0] == parts[1]:
            return True
        return False
    #: 사전 말의 **꼬리**인가 — `합병원` 은 `종합병원` 의 꼬리다
    for w in _ORG_WORDS:
        if len(head) < len(w) and w.endswith(head):
            return True
    return False


#: 기관종별. ★순서가 규칙이다 — 상급 셀에도 `병원`, 병원급 셀에도 `종합병원` 이 있다.
#:   실측 셀 원문:
#:     의원급 「의료법」 제3조 제2항 제1호에 따른 의원, 치과의원, 한의원, … 보건진료소
#:     병원급 「의료법」 제3조 제2항 제3호에 따른 종합병원, 병원, 치과병원, 한방병원, 요양병원
#:     상급   「국민건강보험법」 제42조 제2항에 따른 종합전문요양기관 또는 … 상급종합병원
#:     약국   「국민건강보험법」 제42조 제1항 제2호에 따른 약국, … 한국희귀의약품센터
_INST_RULES = (
    ("상급종합병원", ("상급종합병원", "종합전문요양기관", "전문요양기관")),
    ("약국", ("약국", "한국희귀")),
    ("의원급", ("의원", "조산원", "보건진료소", "보건지소")),
    ("병원급", ("종합병원", "병원")),
)
#: ★**각주로 정의되는 축.** 흥국화재 4세대가 이 방식이다 —
#:   `주1) 의료기관① : 「의료법」 제3조 제2항에 의한 의료기관(종합병원은 제외)…`
#:   각주는 표 **밖**에 있다. 표 dict 만으로는 못 푼다 → 원문만 남긴다.
_INST_FOOTNOTE = re.compile(r"의료기관\s*[①-⑳]")

#: 가입형. ★`선택형Ⅱ` 를 `선택형` 보다 먼저 본다.
#:   실측 원문: `표\n준\n형` · `선\n택\n형\nⅡ` · `2종\n⁀\n선\n택\n형\nⅡ\n‿` · `기본형` · `특약형`
_PLAN_RULES = (
    ("선택형Ⅱ", ("선택형Ⅱ", "선택형II", "선택형2")),
    ("표준형", ("표준형",)),
    ("선택형", ("선택형",)),
    ("기본형", ("기본형",)),
    ("특약형", ("특약형",)),
)
#: 급여구분. `3대비급여` 를 `비급여` 보다 먼저.
_BENEFIT_RULES = (
    ("3대비급여", ("3대비급여",)),
    ("비급여", ("비급여",)),
    ("급여", ("급여",)),
)
#: 항목. 실측 원문에서 그대로.
_ITEM_RULES = (
    ("상급병실료차액", ("상급병실료",)),
    ("처방조제비", ("처방조제", "처방\n조제", "조제비")),
    ("외래", ("외래",)),
    ("입원의료비", ("입원실료", "입원제비용", "입원수술비")),
    ("도수치료·체외충격파·증식치료", ("도수치료",)),
    ("주사료", ("주사료", "주사치료")),
    ("자기공명영상진단", ("자기공명",)),
)

#: ── 값 파싱 ────────────────────────────────────────────────────────────────
#:
#: ★실측한 금액 셀 원문(공백·줄바꿈은 지운 뒤 매칭한다) —
#:     `1만원과보상대상의료비의20%중큰금액`
#:     `1만5천원과보상대상의료비의20%중큰금액`   `1만5천원` `1만 5천원` `1 만5 천원` 세 표기
#:     `8천원과보상대상의료비의20%중큰금액`
#:     `2만원과공제기준금액(보상대상의료비의급여10%해당액과비급여20%해당액의합산액)중큰금액`
#:     `1만원과공제기준금액주)중큰금액`          ← 비율이 **표 밖 각주**에 있다
#:     `1만원`                                  ← 비율 없는 정액(현대해상 선택형)
#:     `비급여병실료의50%,1일평균금액10만원한도`
#:     `1회당3만원과보장대상의료비의30%중큰금액`

#: 한국식 금액. `1만5천원` `10만원` `8천원` `100,000원` `1.5만원`
_KRW = re.compile(
    r"(?:(?P<억>\d+(?:\.\d+)?)억)?"
    r"(?:(?P<만>\d+(?:\.\d+)?)만)?"
    r"(?:(?P<천>\d+(?:\.\d+)?)천)?"
    r"(?:(?P<원>[\d,]+))?원"
)
_PCT = re.compile(r"(\d+(?:\.\d+)?)%")
#: 선택형Ⅱ 의 합산식. `급여10%해당액과비급여20%해당액`
_SPLIT_PCT = re.compile(r"급여(\d+(?:\.\d+)?)%.*?비급여(\d+(?:\.\d+)?)%")
#: `…중큰금액` → 정액과 정률 중 **큰 쪽**을 뺀다
_MAX_RULE = re.compile(r"중\s*큰\s*금액|중큰금액")

#: ★`한도` 가 든 줄은 **금액 읽기에서 뺀다.** 이건 죽은 코드가 아니라 살아 있는 가드다 —
#:   실측 194개 격자 셀의 `…원 한도` 는 전부 `연간 300만원 한도`·`보험가입금액 한도`,
#:   즉 **보장한도(연간 총액)**였다. 공제액으로 읽으면 `공제액 = 3,000,000원` 이 된다.
#:   (`한도금액` 필드 자체는 지웠다 — 모듈 첫머리 ★★ 참조.)
_CAP_LINE = "한도"


def _flat(s: str | None) -> str:
    """공백·줄바꿈을 지운다. 조판이 `1 만5 천원` `제3 조 제2 항` 처럼 띄운다(동양생명)."""
    return re.sub(r"\s+", "", s or "")


def _krw_lines(raw: str) -> int | None:
    """금액을 **줄 단위로** 읽는다. 여러 줄이면 첫 줄에서 읽힌 값.

    ★★왜 줄 단위인가 — **전체를 붙이면 워터마크가 금액이 된다.**

      실측 오진(삼성화재 `6934fffd4e8e` p11 · `442f08077616` p13 등 8건):
        금액 셀 원문 `'0 5 0 7 - 1 1 1\\n1만원과\\n보상대상\\n의료비의\\n20% 중\\n큰 금액'`
        공백을 다 지우면  `0507-11111만원과…`  →  **1111만원 = 11,110,000원**
        참값은 `1만원` 이다. 앞줄은 상품코드(`0507-111`)가 표 칸에 섞여 든 것이다.

      줄로 끊으면 `0507-111` 에는 `원` 이 없어 금액이 아니고, 다음 줄 `1만원과` 가 잡힌다.

    ★단위가 줄 끝에 걸린 경우(`1만` / 다음 줄 `5천원`)는 **이어 붙인 뒤** 읽는다.
      안 그러면 `1만` 만 읽고 10,000 으로 조용히 틀린다.
    """
    lines = [ln for ln in (raw or "").split("\n")]
    merged: list[str] = []
    for ln in lines:
        f = _flat(ln)
        if merged and _flat(merged[-1]).endswith(("만", "천", "억")):
            merged[-1] = merged[-1] + ln
        else:
            merged.append(ln)
    for ln in merged:
        v = _krw(_flat(ln))
        if v is not None:
            return v
    return None


def _krw(token: str) -> int | None:
    """`1만5천원` → 15000. 못 읽으면 `None`.

    ★★**단위 앞의 숫자가 날아간 셀은 읽지 않는다.**

      실측 오진(삼성화재 `849873098f4b` p4 · 전량 실행 2026-08-03에서 잡았다):
        PDF 원문은 `1만 5천원`(=15,000)인데 추출 텍스트는 `'만 5천원'` 이었다.
        `1` 이 열 경계에서 잘려 나갔다.
        `_KRW` 는 모든 그룹이 선택적이라 `5천원` 만 잡아 **5,000** 을 냈고,
        그 레코드가 `parse_status="ok"` 로 나갔다. 참값의 1/3이다.

      그래서 매치 **바로 왼쪽**이 맨 단위(`억`·`만`·`천`)면 `None` 이다.
      숫자가 있었는데 사라진 것이므로 **모른다**가 정답이다(CLAUDE.md §0).
    """
    m = _KRW.search(token)
    if not m or not any(m.group(g) for g in ("억", "만", "천", "원")):
        return None
    if m.start() > 0 and token[m.start() - 1] in "억만천":
        return None
    v = 0.0
    if m.group("억"):
        v += float(m.group("억")) * 100_000_000
    if m.group("만"):
        v += float(m.group("만")) * 10_000
    if m.group("천"):
        v += float(m.group("천")) * 1_000
    if m.group("원"):
        v += float(m.group("원").replace(",", ""))
    return int(v)


def parse_amount(raw: str | None) -> dict:
    """금액 셀 원문 → 구조화된 값. **못 읽은 것은 `None` 이다.**

    반환 키:
      `공제액`      정액 공제(원). `1만원과 … 중 큰 금액` 의 `1만원`
      `자기부담률`  단일 비율(0~1). 급여/비급여가 갈리면 `None` 이고 아래 둘에 담는다
      `자기부담률_급여` `자기부담률_비급여`
      `결합규칙`    `max`(둘 중 큰 금액) · `정액` · `정률` · `None`
      `통화`
      `미파싱_사유` 못 읽은 것의 목록

    ★`한도금액`·`한도단위`·`단위` 는 **없다.** 전량 전수 조사에서 이 표의 금액 셀이
      한 번도 그 값을 담지 않는다는 것을 확인하고 지웠다 — 모듈 첫머리 ★★ 참조.
    """
    out = {"공제액": None, "자기부담률": None, "자기부담률_급여": None,
           "자기부담률_비급여": None, "결합규칙": None, "통화": None,
           "미파싱_사유": []}
    flat = _flat(raw)
    if not flat:
        out["미파싱_사유"].append("금액 셀이 비어 있음")
        return out

    #: ── 정액 공제. ★**줄 단위로 읽는다**(`_krw_lines` 주석의 워터마크 오진 참조).
    #:   `한도` 가 적힌 줄은 뺀다 — `연간 300만원 한도` 를 공제액으로 읽지 않으려고.
    flat_wo_cap = flat
    raw_wo_cap = "\n".join(ln for ln in (raw or "").split("\n") if _CAP_LINE not in ln)
    amt = _krw_lines(raw_wo_cap)
    if amt is not None:
        out["공제액"] = amt
        out["통화"] = "KRW"

    #: ── 비율. 급여/비급여가 갈리는 합산식이 먼저다
    sp = _SPLIT_PCT.search(flat_wo_cap)
    if sp:
        out["자기부담률_급여"] = float(sp.group(1)) / 100
        out["자기부담률_비급여"] = float(sp.group(2)) / 100
        #: ★단일 비율로 뭉개지 않는다. 급여·비급여 비율이 다르면 하나의 수가 아니다.
    else:
        pcts = _PCT.findall(flat_wo_cap)
        if len(pcts) == 1:
            out["자기부담률"] = float(pcts[0]) / 100
        elif len(pcts) > 1:
            #: ★여러 비율이 한 셀에 있는데 무엇에 붙는지 모른다. 고르지 않는다.
            out["미파싱_사유"].append(f"비율이 {len(pcts)}개라 무엇에 붙는지 모름")

    #: ── 결합규칙
    if _MAX_RULE.search(flat_wo_cap):
        out["결합규칙"] = "max"
    elif out["공제액"] is not None and out["자기부담률"] is None \
            and out["자기부담률_급여"] is None:
        out["결합규칙"] = "정액"
    elif out["공제액"] is None and out["자기부담률"] is not None:
        out["결합규칙"] = "정률"

    #: ★★**정액과 정률이 같이 있는데 결합규칙을 못 읽으면 판정이 달라진다.**
    #:   `20%` 와 `max(2만원, 20%)` 는 다른 금액이다. 모르면 모른다고 적는다.
    #:   실측: 삼성화재 `907806fc82d8` p7 은 `2만원과 보상대상 의료비의 20%중` 에서
    #:   페이지 단이 넘어가 `큰 금액` 이 다음 덩어리로 잘렸다.
    if out["결합규칙"] is None and out["공제액"] is not None and (
            out["자기부담률"] is not None or out["자기부담률_급여"] is not None):
        out["미파싱_사유"].append("정액·정률이 함께 있는데 결합규칙(중 큰 금액 등)을 못 읽음")

    #: ★**각주로 미룬 비율.** `공제기준금액주)중큰금액` — 비율이 표 밖에 있다(현대해상).
    if "공제기준금액" in flat_wo_cap and out["자기부담률_급여"] is None:
        out["미파싱_사유"].append("공제기준금액이 각주로 미뤄져 비율이 표 밖에 있음")
    if out["공제액"] is None and out["자기부담률"] is None \
            and out["자기부담률_급여"] is None:
        out["미파싱_사유"].append("금액·비율을 하나도 못 읽음")
    return out


#: ★기관 셀이 **뒤섞였다**는 지문. 금액 조각이 기관 칸에 들어와 있으면 격자가 무너진 것이다.
#:   실측 오진(롯데손보 `fffbe9e8c890` p17):
#:     기관원문 `'20% 중 큰 금액 종합병원에서의 외래 및 처방･조제 20% 중 큰 금액'`
#:     금액원문 `'보건진료소에서의 센터에서의 : 1만원과 전문요양기관, 약국, : 2만원과'`
#:   그대로 두면 **병원급 1만원** 이라는 그럴듯한 거짓 레코드가 나온다.
_INST_SCRAMBLED = re.compile(r"%|중\s*큰\s*금액")


def norm_institution(raw: str | None) -> tuple[str | None, list[str], str | None]:
    """기관종별 정규화. `(정규값, 걸린 후보들, 미해결사유)`.

    ★`의료기관①` 처럼 **각주로 정의되는 축**은 정규값을 만들지 않는다.
      각주가 표 밖에 있어 표 dict 만으로는 못 푼다. 원문은 레코드에 남는다.

    ★★**한 칸이 여러 종별을 가리키는 경우가 39%다**(실측 5,630레코드 중 2,193).
      두 유형이 있고 **다르게 다룬다** —

      (가) 상위 등급 + 그에 딸린 약국 → **상위 등급으로 읽는다**
           `「국민건강보험법」 제42조 제2항에 따른 종합전문요양기관 또는 「의료법」
            제3조의4에 따른 상급종합병원 … 및 그에 따른 … 약국 … 처방·조제`
           약국은 종속절이다. 등급 자체는 상급종합병원으로 명확하다.

      (나) 의원·병원 + 약국이 **대등하게 묶인 통원 등급** → **읽지 않는다(`None`)**
           `「의료법」 제3조 제2항에 의한 의료기관(종합병원은 제외) … 보건소 …
            에서의 외래 및 … 약국 … 에서의 처방·조제`  (흥국화재 4세대 `의료기관①`)
           이건 "약국"도 "의원급"도 아니고 **묶음 등급**이다.
           ★실측 오진: 이걸 `약국` 으로 읽어 **`약국 → 10,000원` 434건**을 만들고 있었다.
             참값은 8,000원(약국)이 아니라 묶음 등급의 1만원이다. 등급 이름을 틀리면
             판정이 다른 칸을 집는다.
    """
    flat = _flat(raw)
    if not flat:
        return None, [], "기관 셀이 비어 있음"
    if _INST_SCRAMBLED.search(flat):
        return None, [], "기관 셀에 금액 조각이 섞임 — 격자가 무너진 표"
    if _INST_FOOTNOTE.search(flat):
        return None, [], "각주로 정의된 기관(의료기관①②③) — 표 밖에 정의가 있음"
    hits = [name for name, keys in _INST_RULES if any(k in flat for k in keys)]
    if not hits:
        return None, [], "기관종별 사전에 없는 표기"
    if "약국" in hits and "상급종합병원" not in hits and len(hits) > 1:
        return None, hits, "기관 셀이 여러 종별을 묶음(의원·병원+약국) — 단일 등급이 아님"
    return hits[0], hits, None


#: ★축 라벨의 **괄호류**. 세로쓰기 병합셀이 이런 문자를 섞어 넣는다.
#:   실측 원문: `선\n택\n형\n(Ⅱ)`(삼성화재) · `2종\n⁀\n선\n택\n형\nⅡ\n‿`(흥국화재)
#:   ★오진: 괄호를 안 지워서 `선택형(Ⅱ)` 가 **`선택형` 으로 읽혔다.**
#:     선택형과 선택형Ⅱ는 자기부담 계산식이 다른 **별개 상품**이다. 라벨을 틀리면
#:     엉뚱한 상품의 금액을 답한다.
_LABEL_BRACKETS = str.maketrans("", "", "()（）[]〔〕{}⁀‿「」『』<>＜＞")


def _flat_label(s: str | None) -> str:
    """축 라벨 비교용 정규화 — 공백 제거 + 괄호류 제거."""
    return _flat(s).translate(_LABEL_BRACKETS)


def _match_rules(text: str, rules) -> str | None:
    flat = _flat_label(text)
    for name, keys in rules:
        if any(_flat_label(k) in flat for k in keys):
            return name
    return None


def _match_all(text: str, rules) -> list[str]:
    """규칙에 걸린 것을 **다 돌려준다.**

    ★왜 필요한가 — 흥국화재 4세대의 항목 셀은 `외래제비용/외래수술비/처방조제비`
      **한 칸에 세 항목**이다. 앞에서 걸린 하나만 고르면 `처방조제비` 로 단정하게 되고,
      그건 **없는 사실을 만드는 것**이다(CLAUDE.md §0).
    """
    flat = _flat_label(text)
    return [name for name, keys in rules if any(_flat_label(k) in flat for k in keys)]


#: ── 담보종목(입통원·상해질병) 복원 ──────────────────────────────────────────
#:
#: 표 맨 왼쪽 `담보종목` 열의 값. `(2) 상해통원` · `(4) 질병통원` 처럼 적힌다.
#: ★`종합입원`·`종합통원` 표기도 실측에 2건 있다 — 입통원만 읽고 상해질병은 `None` 이다.
_DAMBO = re.compile(r"(상해|질병|종합)\s*(입원|통원)")

#: 라벨 셀 길이 상한. 이보다 길면 "담보종목 칸"이 아니라 본문 문장에 낱말이 섞인 것이다.
#:   실측한 라벨 원문: `(2)\n상해통원`(8자) · `(4)\n질병통원` · `상해통원`
_DAMBO_LABEL_MAX = 40

#: 포함 문턱. ★지어내지 않았다 — 라벨 후보가 있는 격자 262개에서 잰 분포가
#:   `1.0` 253 · `0.9` 6 · `0.2` 2 · `0.0` 1 로 **깨끗하게 갈린다.**
#:   0.8 은 이 골짜기 안이고, 259개를 사실로 인정하고 3개를 추론으로 내린다.
#:
#: ★오진 기록 — 처음에는 포함비를 **줄 집합의 교집합**으로 쟀다. 143개밖에 안 걸렸고
#:   분포도 0.0~1.0 에 고르게 퍼져 문턱을 정할 자리가 없었다.
#:   원인: 바깥 칸은 두 열을 **한 줄에 붙여** 내는 조판이 있다 —
#:     안쪽 셀 줄 `1만 5천원과`
#:     바깥 칸 줄 `합병원, 병원, 치과병원, 한방병원, 요양병원 1만 5천원과`
#:   줄이 정확히 같지 않으니 교집합에서 빠진다. **부분문자열**로 바꾸니 위 분포가 됐다.
_DAMBO_CONTAIN_MIN = 0.8


def _dambo_of(text: str | None) -> tuple[str | None, str | None]:
    """`(상해질병, 입통원)`. 못 읽으면 `(None, None)`."""
    m = _DAMBO.search(_flat(text))
    if not m:
        return None, None
    return (m.group(1) if m.group(1) in ("상해", "질병") else None), m.group(2)


def _grid_lines(grid: list[list[str]]) -> list[str]:
    """격자의 모든 줄을 공백 없이. 4자 미만은 흔해서 포함 판정에 못 쓴다."""
    out = []
    for row in grid:
        for cell in row:
            for ln in (cell or "").split("\n"):
                f = _flat(ln)
                if len(f) >= 4:
                    out.append(f)
    return out


def _dambo_rows(tables) -> list[tuple[int, str, str]]:
    """`pages[].tables` 전체에서 담보종목 라벨 행을 모은다.

    돌려주는 것: `(표번호, 라벨셀_원문, 같은행_나머지칸_전체를_공백없이_이은_문자열)`.
    마지막 것이 **포함 판정의 대상**이다 — 바깥 행이 안쪽 격자를 감싸는지 본다.
    """
    out: list[tuple[int, str, str]] = []
    for ti, t in enumerate(tables or []):
        for row in to_grid(t):
            for ci, cell in enumerate(row):
                if len(_flat(cell)) <= _DAMBO_LABEL_MAX and _DAMBO.search(_flat(cell)):
                    sib = "".join(_flat(row[k]) for k in range(len(row)) if k != ci)
                    out.append((ti, cell, sib))
    return out


#: ── 격자 정규화 ────────────────────────────────────────────────────────────

def to_grid(table) -> list[list[str]]:
    """입력을 `list[list[str]]` 격자로 맞춘다.

    받는 모양 둘 —
      (가) `tables_coords` 표 dict  `{"cols": N, "records": [{"cols": {"1": …}}]}`
      (나) `pages[].tables` 원소     `list[list[str|None]]`
    """
    if isinstance(table, dict):
        recs = table.get("records") or []
        ncol = table.get("cols") or 0
        if not ncol and recs:
            ncol = max((max((int(k) for k in (r.get("cols") or {})), default=0)
                        for r in recs), default=0)
        return [[(r.get("cols") or {}).get(str(i)) or "" for i in range(1, ncol + 1)]
                for r in recs]
    return [[(c or "") for c in row] for row in (table or [])]


#: 헤더 낱말. 이 행은 데이터가 아니다. ★실측한 헤더 셀에서 그대로 모았다.
_HEADER_WORDS = ("구분", "항목", "공제금액", "자기부담금차감금액", "보상한도",
                 "보장종목", "보상하는사항", "지급사유", "지급금액", "보상금액",
                 "보장금액", "지급한도", "보험금", "비고")


def _is_header_cell(s: str) -> bool:
    f = _flat(s)
    return bool(f) and f in _HEADER_WORDS


#: ★**기관 셀 길이 상한.** 이 넘어가면 "표의 한 칸"이 아니라 **페이지 본문 덩어리**다.
#:   PyMuPDF `find_tables` 는 표가 아닌 두 단 조판을 1×2 격자로 만들어 놓기도 한다
#:   (`보장종목 | 보상하는 사항` 안에 페이지 전체가 들어간다).
#:
#:   ★문턱을 지어내지 않았다. 표본 196문서 2,159레코드에서 잰 값 —
#:     `ok`       n=753  p50 104 · p95 219 · **max 240**
#:     `partial`  n=101  p50  49 · p95 264 · **max 307**
#:     `unparsed` n=1,305 p50 88 · p90 679 · max 2,031
#:   320 은 관측된 `ok`·`partial` 을 **하나도 자르지 않는** 가장 낮은 자리다.
#:   즉 이 컷이 지우는 것은 전부 이미 값을 못 읽던 행이다.
#:   ★그래도 이건 **거친 컷**이다. 표본이 커지면 다시 잰다(계획서 L1).
_INST_CELL_MAX = 320


def _find_roles(grid: list[list[str]]) -> tuple[int | None, int | None]:
    """`(기관열, 금액열)`. 못 찾으면 `None`.

    ★**열 번호로 고정하지 않는다.** 동양생명은 빈 스페이서 열이 6개 섞인 10열이고
      회사마다 열 수가 3~10으로 다르다(실측). 그래서 **내용으로** 찾는다.
    """
    ncol = max((len(r) for r in grid), default=0)
    inst_score = [0] * ncol
    amt_score = [0] * ncol
    for row in grid:
        for c, cell in enumerate(row):
            if _is_header_cell(cell) or len(cell) > _INST_CELL_MAX:
                #: ★본문 덩어리를 점수에 넣으면 **본문 열이 기관열로 뽑힌다.**
                continue
            flat = _flat(cell)
            if not flat:
                continue
            #: ★역할 찾기에서는 **후보가 걸리기만 해도** 기관열 점수를 준다.
            #:   묶음 등급(`None` 이 되는 셀)도 기관열이라는 사실은 바뀌지 않는다.
            if _INST_FOOTNOTE.search(flat) or norm_institution(cell)[1]:
                inst_score[c] += 1
            if _KRW.search(flat) or _PCT.search(flat):
                amt_score[c] += 1
    inst = max(range(ncol), key=lambda c: inst_score[c], default=None) if ncol else None
    if inst is None or inst_score[inst] == 0:
        return None, None
    #: ★금액열은 기관열이 아닌 열 중 점수가 가장 높은 곳. 동점이면 **오른쪽**.
    #:   실측한 모든 조판에서 금액은 기관 오른쪽에 있다.
    cands = [c for c in range(ncol) if c != inst and amt_score[c] > 0]
    if not cands:
        return inst, None
    best = max(amt_score[c] for c in cands)
    amt = max(c for c in cands if amt_score[c] == best)
    return inst, amt


def _fill_down(grid: list[list[str]], col: int) -> list[str | None]:
    """병합셀 보정 — 위에서 아래로 마지막 값 끌기.

    ★**위 행에 값이 없으면 `None` 이다.** 삼성생명 `010d042bf502` p27 처럼
      병합셀 글자가 여러 행에 쪼개져 첫 값 행보다 **아래**에 놓이는 조판이 있다.
      그때 축은 `None` 이고, 그게 정직하다. 아래 값을 위로 끌어오지 않는다.
    """
    out: list[str | None] = []
    last: str | None = None
    for row in grid:
        cell = row[col] if col < len(row) else ""
        if _flat(cell) and not _is_header_cell(cell):
            last = cell
        out.append(last)
    return out


def parse_coverage_limits(table, *, page: int | None = None,
                          table_id: str | None = None,
                          stats: dict | None = None,
                          담보종목: str | None = None,
                          담보종목_추론: str | None = None) -> list[dict]:
    """보장한도·자기부담금 표 하나 → `coverage_limit` 레코드 목록.

    `담보종목`      바깥 격자에서 **포함관계를 확인하고** 되찾은 라벨 원문(`(2) 상해통원`).
                    이 표의 맨 왼쪽 열이라는 것이 확인된 것이므로 **사실 칸**에 넣는다.
    `담보종목_추론`  같은 쪽에 라벨이 하나뿐이라 갖다 붙인 것. **추론 칸**에만 넣는다.

    ★둘 다 표 **자기 격자 안**의 라벨보다 뒤다. 격자 안에 적혀 있으면 그게 우선이다.

    `stats` 를 주면 세어 넣는다(CLAUDE.md §3 — 조용한 스킵을 만들지 않는다):
      `표_역할못찾음` `행_기관없음` `레코드_ok` `레코드_partial` `레코드_unparsed`
      `담보종목_사실` `담보종목_추론` `담보종목_충돌`
    """
    st = stats if stats is not None else {}
    st["표_본것"] = st.get("표_본것", 0) + 1
    grid = to_grid(table)
    if not grid:
        st["표_역할못찾음"] = st.get("표_역할못찾음", 0) + 1
        return []

    inst_col, amt_col = _find_roles(grid)
    if inst_col is None:
        #: ★기관 축이 없으면 이건 자기부담금 표가 아니다. 억지로 만들지 않는다.
        st["표_역할못찾음"] = st.get("표_역할못찾음", 0) + 1
        return []

    #: 축 열 = 기관열 **왼쪽**의 열들. 실측한 모든 조판에서 축은 왼쪽에 있다.
    axis_cols = [c for c in range(inst_col)]
    filled = {c: _fill_down(grid, c) for c in axis_cols}

    out: list[dict] = []
    for r, row in enumerate(grid):
        inst_raw = row[inst_col] if inst_col < len(row) else ""
        if not _flat(inst_raw) or _is_header_cell(inst_raw):
            st["행_기관없음"] = st.get("행_기관없음", 0) + 1
            continue
        if len(inst_raw) > _INST_CELL_MAX:
            #: ★표의 칸이 아니라 본문 덩어리다. 레코드로 만들지 않고 **센다**.
            st["행_본문덩어리"] = st.get("행_본문덩어리", 0) + 1
            continue

        labels = [filled[c][r] for c in axis_cols if filled[c][r]]
        blob = " ".join(labels)
        inst, inst_cands, inst_why = norm_institution(inst_raw)
        amt_raw = row[amt_col] if (amt_col is not None and amt_col < len(row)) else ""
        val = parse_amount(amt_raw)

        #: ── 입통원·상해질병. **셀에 적혀 있을 때만 사실이다.**
        io = None
        if re.search(r"(?<!퇴)입원", blob):
            io = "입원"
        if "통원" in blob:
            io = "통원"
        sd = ("상해" if "상해" in _flat(blob)
              else "질병" if "질병" in _flat(blob) else None)
        #: ★항목은 **한 칸에 여럿**일 수 있다(흥국화재 4세대). 하나로 단정하지 않는다.
        items = _match_all(blob, _ITEM_RULES)
        item = items[0] if len(items) == 1 else None
        inferred = []
        io_guess = sd_guess = None
        dambo_src = None
        dambo_conflict: list[str] = []
        axis_raw = list(labels)

        #: ── ★되찾은 `담보종목` 열. 격자 안에 없을 때만 쓴다.
        if 담보종목:
            d_sd, d_io = _dambo_of(담보종목)
            if (io is not None and d_io is not None and io != d_io) or \
                    (sd is not None and d_sd is not None and sd != d_sd):
                #: ★격자 안 라벨과 되찾은 라벨이 다르다. **격자 안이 이긴다** —
                #:   되찾은 쪽은 바깥 행이라 더 넓은 범위를 가리킬 수 있다.
                #:   조용히 넘기지 않고 **센다**(CLAUDE.md §3).
                st["담보종목_충돌"] = st.get("담보종목_충돌", 0) + 1
                dambo_conflict.append(
                    f"격자 안 담보종목({sd or ''}{io or ''})과 "
                    f"되찾은 담보종목({_flat(담보종목)})이 다름")
            if io is None and d_io:
                io, dambo_src = d_io, "바깥격자"
            if sd is None and d_sd:
                sd, dambo_src = d_sd, "바깥격자"
            if dambo_src:
                #: 되찾은 것도 **표의 축 원문**이다. 어디서 왔는지는 locator 에 남는다.
                axis_raw = [담보종목.strip()] + axis_raw
                st["담보종목_사실"] = st.get("담보종목_사실", 0) + 1
        if 담보종목_추론 and io is None and sd is None:
            g_sd, g_io = _dambo_of(담보종목_추론)
            if g_io:
                io_guess = g_io
                inferred.append(f"입통원←같은쪽 담보종목(유일):{_flat(담보종목_추론)}")
            if g_sd:
                sd_guess = g_sd
                inferred.append(f"상해질병←같은쪽 담보종목(유일):{_flat(담보종목_추론)}")
            if g_io or g_sd:
                dambo_src = "같은쪽유일"
                st["담보종목_추론"] = st.get("담보종목_추론", 0) + 1

        if io is None and io_guess is None and item in ("외래", "처방조제비"):
            #: ★추론이다. 사실 칸에 넣지 않는다(CLAUDE.md §0 — 추론과 사실을 나눈다).
            #:   표준 약관에서 통원의료비 = 외래제비용 + 외래수술비 + 처방조제비 지만,
            #:   **그 셀에 `통원` 이라고 적혀 있지는 않다.**
            io_guess = "통원"
            inferred.append(f"입통원←항목:{item}")

        why = list(val["미파싱_사유"]) + dambo_conflict
        if inst_why:
            why.append(inst_why)
        if len(items) > 1:
            why.append(f"항목이 한 칸에 {len(items)}개({'·'.join(items)})라 하나로 못 정함")
        if amt_col is None:
            why.append("표에 금액 열이 없음")

        rec = {
            "가입형": _match_rules(blob, _PLAN_RULES),
            "상해질병": sd,
            "상해질병_추론": sd_guess,
            "급여구분": _match_rules(blob, _BENEFIT_RULES),
            "입통원": io,
            "입통원_추론": io_guess,
            "항목": item,
            "항목_후보": items,
            "기관종별": inst,
            "기관종별_후보": inst_cands,
            "기관종별_원문": (inst_raw or "").strip(),
            "자기부담률": val["자기부담률"],
            "자기부담률_급여": val["자기부담률_급여"],
            "자기부담률_비급여": val["자기부담률_비급여"],
            "공제액": val["공제액"],
            "결합규칙": val["결합규칙"],
            "통화": val["통화"],
            "금액_원문": (amt_raw or "").strip(),
            "축_원문": axis_raw,
            "inferred": bool(inferred),
            "추론근거": inferred,
            "근거_locator": {"page_from": page, "page_to": page,
                            "table_id": table_id, "row": r,
                            "담보종목_출처": dambo_src},
            "미파싱_사유": why,
        }
        #: ── 판정 가능 여부. ★**금액이 없으면 판정에 못 쓴다.**
        usable_value = (rec["공제액"] is not None or rec["자기부담률"] is not None
                        or rec["자기부담률_급여"] is not None)
        #: ★쪽 경계 절단이 의심되면 **`ok` 로 내보내지 않는다.**
        #:   값이 있어 보여도 앞 토막이 다른 쪽에 있으면 틀린 값이다.
        truncated = _looks_truncated(rec.get("기관종별_원문") or "")
        if truncated:
            why = list(why) + ["기관명이 어절 중간에서 시작 — 쪽 경계 절단 의심"]
            rec["미파싱_사유"] = why
            rec["쪽경계_절단의심"] = True
        if not usable_value:
            rec["parse_status"] = "unparsed"
        elif rec["기관종별"] is None or why:
            rec["parse_status"] = "partial"
        else:
            rec["parse_status"] = "ok"
        st[f"레코드_{rec['parse_status']}"] = st.get(f"레코드_{rec['parse_status']}", 0) + 1
        out.append(rec)
    if out:
        st["표_레코드냄"] = st.get("표_레코드냄", 0) + 1
        if any(r["parse_status"] != "unparsed" for r in out):
            st["표_값있는레코드냄"] = st.get("표_값있는레코드냄", 0) + 1
    return out


def parse_page(page_doc: dict, *, stats: dict | None = None) -> list[dict]:
    """페이지 JSON 한 쪽의 표를 **둘 다** 훑는다 — `tables` 와 `tables_coords`.

    ★계약(`tables_coords`)만 보면 0건이라는 것을 위 실측에서 확인했다.
      그래서 두 키를 다 본다. 어느 쪽에서 나왔는지 `근거_locator.source` 에 남긴다.

    ★★여기서 **담보종목 열을 되찾는다.** `find_tables` 가 같은 표를 바깥/안쪽 격자
      둘로 쪼개 놓기 때문에, 안쪽 격자만 보던 파서는 맨 왼쪽 담보종목 열을 못 봤다.
      쪽 단위로 봐야 두 격자를 맞붙일 수 있어서 이 함수가 그 일을 한다.
    """
    st = stats if stats is not None else {}
    st["쪽_본것"] = st.get("쪽_본것", 0) + 1
    p = page_doc.get("page")
    tables = page_doc.get("tables") or []
    rows = _dambo_rows(tables)

    #: 쪽 전체에서 본 담보종목 라벨. 표 셀과 쪽 본문을 **둘 다** 본다.
    #:   ★하나뿐일 때만 쓴다. 둘 이상이면 어느 표 것인지 모르므로 안 쓴다.
    seen = {_DAMBO.search(_flat(raw)).group(0) for (_, raw, _) in rows}
    seen |= {m.group(0) for m in _DAMBO.finditer(_flat(page_doc.get("text")))}
    page_guess = None
    if len(seen) == 1:
        page_guess = next(iter(seen))

    out: list[dict] = []
    for gi, g in enumerate(tables):
        #: ── 포함 판정. 바깥 행이 이 격자를 감싸는가.
        lines = _grid_lines(to_grid(g))
        fact = None
        if lines:
            hits = {raw for (ti, raw, sib) in rows if ti != gi
                    and sum(1 for ln in lines if ln in sib) / len(lines)
                    >= _DAMBO_CONTAIN_MIN}
            labs = {_DAMBO.search(_flat(x)).group(0) for x in hits}
            if len(labs) == 1:
                fact = sorted(hits)[0]
            elif len(labs) > 1:
                #: ★감싸는 바깥 행이 여럿인데 라벨이 서로 다르다. 못 고른다.
                st["담보종목_바깥라벨여럿"] = st.get("담보종목_바깥라벨여럿", 0) + 1
        for rec in parse_coverage_limits(g, page=p, table_id=f"p{p}-tables-{gi}",
                                         stats=st, 담보종목=fact,
                                         담보종목_추론=page_guess):
            rec["근거_locator"]["source"] = "pages[].tables"
            out.append(rec)
    for t in page_doc.get("tables_coords") or []:
        #: ★`tables_coords` 표는 `pages[].tables` 격자와 좌표를 맞댈 수 없다
        #:   (한쪽은 좌표, 한쪽은 문자열 격자다). 그래서 **사실 경로가 없다.**
        for rec in parse_coverage_limits(t, page=p, table_id=t.get("table_id"),
                                         stats=st, 담보종목_추론=page_guess):
            rec["근거_locator"]["source"] = "tables_coords"
            out.append(rec)
    return out


_BOUNDARY_AMOUNT_PREFIX = re.compile(r"(\d+(?:\.\d+)?(?:억|만|천))$")
_BOUNDARY_AMOUNT_SUFFIX = re.compile(r"^(\d+(?:\.\d+)?(?:만|천)?원)")
_BOUNDARY_UNIT_ORDER = {"억": 3, "만": 2, "천": 1, "원": 0}
_BOUNDARY_AXIS_FIELDS = ("가입형", "상해질병", "급여구분", "입통원", "항목")


def _boundary_amount(prev_raw: str, current_raw: str) -> tuple[int, str] | None:
    """Join complementary adjacent-page currency fragments, or refuse."""
    prev_flat = _flat(prev_raw)
    current_flat = _flat(current_raw)
    prefix = _BOUNDARY_AMOUNT_PREFIX.search(prev_flat)
    suffix = _BOUNDARY_AMOUNT_SUFFIX.match(current_flat)
    if not prefix or not suffix:
        return None
    prefix_unit = prefix.group(1)[-1]
    suffix_token = suffix.group(1)
    suffix_unit = "만" if "만" in suffix_token else "천" if "천" in suffix_token else "원"
    if _BOUNDARY_UNIT_ORDER[prefix_unit] <= _BOUNDARY_UNIT_ORDER[suffix_unit]:
        return None
    joined = prefix.group(1) + current_raw
    parsed = parse_amount(joined)
    amount = parsed["공제액"]
    current_amount = parse_amount(current_raw)["공제액"]
    if amount is None or (current_amount is not None and amount <= current_amount):
        return None
    return amount, joined


def _recover_boundary_record(prev_records: list[dict], record: dict) -> bool:
    """Recover one page-start record only when one previous row fits exactly."""
    if not record.get("쪽경계_절단의심"):
        return False
    candidates: list[tuple[dict, int, str, str, list[str], str | None]] = []
    for prev in prev_records:
        joined_amount = _boundary_amount(prev.get("금액_원문") or "", record.get("금액_원문") or "")
        if joined_amount is None:
            continue
        conflicts = [
            field for field in _BOUNDARY_AXIS_FIELDS
            if prev.get(field) is not None and record.get(field) is not None
            and prev.get(field) != record.get(field)
        ]
        if conflicts:
            continue
        joined_institution = (prev.get("기관종별_원문") or "") + (record.get("기관종별_원문") or "")
        institution, institution_candidates, institution_reason = norm_institution(joined_institution)
        if institution is None:
            continue
        amount, joined_raw = joined_amount
        candidates.append(
            (prev, amount, joined_raw, joined_institution, institution_candidates, institution_reason)
        )
    if len(candidates) != 1:
        return False

    prev, amount, joined_raw, institution_raw, institution_candidates, institution_reason = candidates[0]
    old_amount = record.get("공제액")
    record["공제액"] = amount
    record["통화"] = "KRW"
    record["금액_원문"] = joined_raw
    record["기관종별"], _, _ = norm_institution(institution_raw)
    record["기관종별_후보"] = institution_candidates
    record["기관종별_원문"] = institution_raw
    for field in _BOUNDARY_AXIS_FIELDS:
        if record.get(field) is None and prev.get(field) is not None:
            record[field] = prev[field]
    reasons = [
        reason for reason in record.get("미파싱_사유") or []
        if "쪽 경계 절단 의심" not in reason and "기관명이 어절 중간" not in reason
    ]
    if institution_reason:
        reasons.append(institution_reason)
    record["미파싱_사유"] = reasons
    record["쪽경계_절단의심"] = False
    record["쪽경계_복구"] = {
        "method": "adjacent_table_row_exact",
        "previous_page": (prev.get("근거_locator") or {}).get("page_from"),
        "current_page": (record.get("근거_locator") or {}).get("page_to"),
        "original_amount": old_amount,
        "recovered_amount": amount,
        "value_invention": False,
    }
    locator = record.get("근거_locator") or {}
    locator["page_from"] = (prev.get("근거_locator") or {}).get("page_from")
    record["근거_locator"] = locator
    usable_value = (
        record.get("공제액") is not None
        or record.get("자기부담률") is not None
        or record.get("자기부담률_급여") is not None
    )
    if not usable_value:
        record["parse_status"] = "unparsed"
    elif record.get("기관종별") is None or reasons:
        record["parse_status"] = "partial"
    else:
        record["parse_status"] = "ok"
    return True


def parse_pages(page_docs: list[dict], *, stats: dict | None = None) -> list[list[dict]]:
    """Parse adjacent pages and recover only uniquely matching split table rows."""
    parsed: list[list[dict]] = []
    for index, page_doc in enumerate(page_docs):
        records = parse_page(page_doc, stats=stats)
        if index and page_doc.get("page") == page_docs[index - 1].get("page", 0) + 1:
            for record in records:
                if _recover_boundary_record(parsed[-1], record) and stats is not None:
                    stats["쪽경계_복구"] = stats.get("쪽경계_복구", 0) + 1
        parsed.append(records)
    return parsed


def parse_page_with_previous(
    previous_page: dict | None,
    page_doc: dict,
    *,
    stats: dict | None = None,
) -> list[dict]:
    """Production entrypoint for a selected page plus its adjacent context."""
    records = parse_page(page_doc, stats=stats)
    if (
        previous_page is not None
        and page_doc.get("page") == previous_page.get("page", 0) + 1
    ):
        previous_records = parse_page(previous_page)
        for record in records:
            if _recover_boundary_record(previous_records, record) and stats is not None:
                stats["쪽경계_복구"] = stats.get("쪽경계_복구", 0) + 1
    return records


#: ── 손으로 읽은 정답 10표 ───────────────────────────────────────────────────
#:
#: ★★**이건 held-out 정답셋이 아니다.** 파서를 만들면서 본 바로 그 표들이다.
#:   그래서 여기 1.000 이 나오는 것은 "일반화된 정확도"가 아니라
#:   **"내가 읽은 것을 코드가 그대로 재현한다"** 는 회귀 방지선이다.
#:   진짜 정확도는 층화 표본을 따로 라벨링해야 안다(계획서 L1).
#:
#: 정답은 `pages[].text` 원문을 **사람이 읽어** 적었다. 파서 출력에서 만들지 않았다.
#: 튜플 = (기관종별, 공제액, 자기부담률, 급여률, 비급여률, 결합규칙)
_E = "data/extracted/"
HANDCHECK = [
    ("KB손보 3세대 <표1> 표준형4+선택형Ⅱ3",
     _E + "kbinsure/s5_pymupdf-1.28.0/6060b8bda3fa.json", 10, [
         ("의원급", 10000, 0.2, None, None, "max"),
         ("병원급", 15000, 0.2, None, None, "max"),
         ("상급종합병원", 20000, 0.2, None, None, "max"),
         ("약국", 8000, 0.2, None, None, "max"),
         ("의원급", 10000, None, 0.1, 0.2, "max"),
         ("병원급", 15000, None, 0.1, 0.2, "max"),
         ("상급종합병원", 20000, None, 0.1, 0.2, "max"),
     ]),
    ("롯데손보 3세대 <표1> 표준형4+선택형Ⅱ3",
     _E + "lotteins/s5_pymupdf-1.28.0/1b922d9f78a4.json", 44, [
         ("의원급", 10000, 0.2, None, None, "max"),
         ("병원급", 15000, 0.2, None, None, "max"),
         ("상급종합병원", 20000, 0.2, None, None, "max"),
         ("약국", 8000, 0.2, None, None, "max"),
         ("의원급", 10000, None, 0.1, 0.2, "max"),
         ("병원급", 15000, None, 0.1, 0.2, "max"),
         ("상급종합병원", 20000, None, 0.1, 0.2, "max"),
     ]),
    ("현대해상 표준형4+선택형Ⅱ4(비율이 각주)+선택형4(정액)",
     _E + "hyundaimarine/s5_pymupdf-1.28.0/c6230695c978.json", 29, [
         ("의원급", 10000, 0.2, None, None, "max"),
         ("병원급", 15000, 0.2, None, None, "max"),
         ("상급종합병원", 20000, 0.2, None, None, "max"),
         ("약국", 8000, 0.2, None, None, "max"),
         ("의원급", 10000, None, None, None, "max"),
         ("병원급", 15000, None, None, None, "max"),
         ("상급종합병원", 20000, None, None, None, "max"),
         ("약국", 8000, None, None, None, "max"),
         ("의원급", 10000, None, None, None, "정액"),
         ("병원급", 15000, None, None, None, "정액"),
         ("상급종합병원", 20000, None, None, None, "정액"),
         ("약국", 8000, None, None, None, "정액"),
     ]),
    ("동양생명 10열(빈 스페이서 6개) 표준형4",
     _E + "myangel/s5_pymupdf-1.28.0/002496fe7873.json", 32, [
         ("의원급", 10000, 0.2, None, None, "max"),
         ("병원급", 15000, 0.2, None, None, "max"),
         ("상급종합병원", 20000, 0.2, None, None, "max"),
         ("약국", 8000, 0.2, None, None, "max"),
     ]),
    ("삼성생명 축 라벨이 여러 행으로 쪼개진 조판 표준형4",
     _E + "samsunglife/s5_pymupdf-1.28.0/010d042bf502.json", 27, [
         ("의원급", 10000, 0.2, None, None, "max"),
         ("병원급", 15000, 0.2, None, None, "max"),
         ("상급종합병원", 20000, 0.2, None, None, "max"),
         ("약국", 8000, 0.2, None, None, "max"),
     ]),
    ("흥국화재 3세대 — 앞 페이지에서 잘려 온 행 + 2종(선택형Ⅱ)",
     _E + "heungkukfire/s5_pymupdf-1.28.0/0149a994930a.json", 32, [
         ("병원급", 15000, 0.2, None, None, "max"),
         ("상급종합병원", 20000, 0.2, None, None, "max"),
         ("약국", 8000, 0.2, None, None, "max"),
         ("의원급", 10000, None, 0.1, 0.2, "max"),
         ("병원급", 15000, None, 0.1, 0.2, "max"),
         ("상급종합병원", 20000, None, 0.1, 0.2, "max"),
     ]),
    ("삼성화재 페이지 단 넘김 표준형4+선택형Ⅱ4",
     _E + "samsungfire/s5_pymupdf-1.28.0/907806fc82d8.json", 7, [
         ("의원급", 10000, 0.2, None, None, "max"),
         ("병원급", 15000, 0.2, None, None, "max"),
         #: ★`…20%중` 에서 단이 넘어가 `큰 금액` 이 잘렸다 → 결합규칙 `None` 이 참값이다
         ("상급종합병원", 20000, 0.2, None, None, None),
         ("약국", 8000, 0.2, None, None, "max"),
         ("의원급", 10000, None, 0.1, 0.2, "max"),
         ("병원급", 15000, None, 0.1, 0.2, "max"),
         ("상급종합병원", 20000, None, 0.1, 0.2, "max"),
         ("약국", 8000, None, 0.1, 0.2, "max"),
     ]),
    #: ★아래 셋은 **못 읽는 것이 정답**이다. 값이 나오면 그게 거짓 레코드다.
    ("흥국화재 4세대 — 격자가 금액 열을 통째로 잃음(기대 0)",
     _E + "heungkukfire/s5_pymupdf-1.28.0/0e5af513f7d6.json", 10, []),
    ("DB손보 — find_tables 가 표를 [['구  분','항목']] 로만 냄(기대 0 · 참값 8행)",
     _E + "dbins/s5_pymupdf-1.28.0/2c2c4f679faa.json", 24, []),
    ("삼성화재 요약서 — 한 칸에 `- 의원 등 : 1만원` 이 여러 줄(기대 0)",
     _E + "samsungfire/s5_pymupdf-1.28.0/01d17da13fe7.json", 15, []),
]


def run_handcheck() -> int:
    """손으로 읽은 정답과 대조하고 **센다**. 거짓 레코드가 있으면 1 을 돌려준다."""
    import collections
    import json

    exp_n = got_n = hit_n = false_n = 0
    for desc, f, page, exp in HANDCHECK:
        d = json.load(open(f, encoding="utf-8"))
        pg = next(p for p in d["pages"] if p["page"] == page)
        recs = [r for r in parse_page(pg) if r["parse_status"] != "unparsed"]
        got = [(r["기관종별"], r["공제액"], r["자기부담률"], r["자기부담률_급여"],
                r["자기부담률_비급여"], r["결합규칙"]) for r in recs]
        ce, cg = collections.Counter(exp), collections.Counter(got)
        hit = sum((ce & cg).values())
        false = sum((cg - ce).values())
        miss = sum((ce - cg).values())
        exp_n += len(exp); got_n += len(got); hit_n += hit; false_n += false
        ok = hit == len(exp) and false == 0
        print(f'{"OK " if ok else "!! "}{desc}')
        print(f"    기대 {len(exp):2d}  산출 {len(got):2d}  일치 {hit:2d}  "
              f"놓침 {miss:2d}  거짓 {false:2d}")
        for t in (cg - ce).elements():
            print("      거짓:", t)
        for t in (ce - cg).elements():
            print("      놓침:", t)
    print(f"\n합계  기대 {exp_n}  산출 {got_n}  일치 {hit_n}  거짓 {false_n}")
    return 1 if false_n or hit_n != exp_n else 0


#: ── 실측용 CLI ─────────────────────────────────────────────────────────────
#:
#:   python -m scripts.extract.coverage_limits              # 전량 요약
#:   python -m scripts.extract.coverage_limits --dump 20    # 레코드 20개 눈으로 확인
#:   python -m scripts.extract.coverage_limits --handcheck  # 손으로 읽은 10표와 대조

if __name__ == "__main__":  # pragma: no cover
    import argparse
    import collections
    import glob
    import json
    import os
    import sys

    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="data/extracted")
    ap.add_argument("--schema", default="s5_pymupdf-1.28.0")
    ap.add_argument("--dump", type=int, default=0)
    ap.add_argument("--insurer", default="")
    ap.add_argument("--handcheck", action="store_true")
    a = ap.parse_args()

    if a.handcheck:
        sys.exit(run_handcheck())

    pat = os.path.join(a.root, a.insurer or "*", a.schema, "*.json")
    files = sorted(glob.glob(pat))
    st: dict = {}
    per_ins = collections.Counter()
    per_status = collections.Counter()
    inst_cnt = collections.Counter()
    dumped = 0
    n_doc_hit = 0
    for f in files:
        ins = f.replace(os.sep, "/").split("/")[-3]
        d = json.load(open(f, encoding="utf-8"))
        hit = False
        for page_index, pg in enumerate(d["pages"]):
            txt = pg.get("text") or ""
            #: ★자기부담금 표가 있을 법한 쪽만 본다. 전량 파싱은 본문까지 긁는다.
            if "상급종합병원" not in txt or ("공제" not in txt and "자기부담" not in txt):
                continue
            previous_page = d["pages"][page_index - 1] if page_index else None
            recs = parse_page_with_previous(previous_page, pg, stats=st)
            for rec in recs:
                per_ins[ins] += 1
                per_status[rec["parse_status"]] += 1
                inst_cnt[rec["기관종별"]] += 1
                hit = True
                if dumped < a.dump:
                    dumped += 1
                    print(json.dumps({"보험사": ins, "sha12": os.path.basename(f)[:12],
                                      **rec}, ensure_ascii=False))
        if hit:
            n_doc_hit += 1
    print("문서", len(files), "레코드 나온 문서", n_doc_hit)
    print("레코드", sum(per_ins.values()), dict(per_status))
    print("회사별", per_ins.most_common())
    print("기관종별", inst_cnt.most_common())
    print("stats", st)
