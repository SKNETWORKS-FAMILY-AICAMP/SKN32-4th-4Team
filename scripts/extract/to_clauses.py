"""페이지 JSON → 조항 단위 구조화 (전처리 5·6·7·8·9단계).

★왜 이게 병목인가

    페이지 단위까지만 오면 판정 근거를 **"몇 쪽"** 으로만 인용할 수 있다.
    실제로 필요한 것은 **"제○조 몇 항"** 이다. 그리고 이게 없으면
    8(중복 제거) · 9(청킹) · 10(색인) · 11(평가)이 전부 막힌다.
    ERD 의 `assessment_clause_citation` 이 참조할 `policy_clause` 를 만드는 단계가 여기다.

이 스크립트가 하는 일 (단계 번호는 팀이 정리한 11단계 기준)

    5 문서 구조 복원   : 보통약관/특별약관/별표 같은 **부(部) 경계**를 찾는다
    6 조항 구조화      : `제○조(제목)` 로 잘라 조항 단위 레코드를 만든다
    7 메타데이터 부착   : 출처·페이지·부 경계·표를 각 조항에 붙인다
    8 중복 처리(준비)  : 조항마다 **내용 해시**를 계산한다
                        ★번호가 아니라 **내용**이 정체성이다 — 같은 번호라도 내용이
                          다르면 다른 조항이고, 번호가 바뀌어도 내용이 같을 수 있다
    9 계층형 청킹      : 부 → 조 → **항(①)** 으로 나누고, 항 안의 **호(1./가./1))**
                        위치를 표시한다. 인용은 `제4조(보상하지 않는 사항) 제1항` 형태

    ★9단계는 v5(2026-08-02)에 실제로 구현했다. v4 까지는 이 자리에
      "부 → 조 → (긴 조는 항 단위) 로 나눈다"고 적혀 있었지만 **하지 않았다** —
      `_PARA.split()` 결과의 개수만 세고 버렸다. `2026-07-31_전처리_파이프라인_현황.md`
      는 9단계를 `❌ 없음` 으로 정확히 기록하고 있었는데, 이 독스트링만 앞서 있었다.
      **문서가 코드보다 앞서 가면 아무도 그게 비어 있는 줄 모른다.**

이 스크립트가 **하지 않는 일**
    - 조항이 무엇을 뜻하는지 해석하지 않는다
    - 표가 어느 조항에 속하는지 **추정하지 않는다.** 같은 페이지에 있으면 그 사실만 기록한다
    - 경계를 못 찾으면 조용히 넘어가지 않고 그 사실을 남긴다
    - **읽지 못한 항 번호를 지어내지 않는다.** `paragraph_no=null` 로 두고 세어 남긴다
    - 목(目, `(1)`/`(가)`)은 나누지 않는다. 호까지만 한다

실행:
    python -m scripts.extract.to_clauses --sha 968e67f4d3b6
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from app.core.errors import InfraError, ValidationErr

_ROOT = Path(__file__).resolve().parents[2]
_IN = _ROOT / "data" / "extracted"
_OUT = _ROOT / "data" / "structured"

#: v6 — 부록(별표·붙임·분류표)을 `annexes[]` 로 분리 + 조항 단위 인용 게이트.
#:      ★`content_hash` 가 바뀐다(부록을 뺀 본문 + 제목 글리프 정리).
#:        그래서 s5 를 덮어쓰지 않고 **새 버전**으로 낸다.
#:        구·신 해시가 DB 에 함께 남으면 오염된 옛 근거가 계속 검색된다(코덱스).
#:
#: v5 — 목차 판정을 비율에서 **구조**로 교체(본문 25,252쪽 복구).
SCHEMA_VERSION = "6"


def _version_tag() -> str:
    """조항 산출물 경로에 넣을 버전 태그.

    ★페이지 JSON 의 태그(`to_page_json._version_tag()`)와 **따로 간다.**
      추출기는 그대로인데 조항 스키마만 올라가는 일이 있다(v5 가 그렇다).
      같이 묶으면 s4 를 덮어써서 **비교 대상이 사라진다** —
      v3 의 35% 손상을 찾을 수 있었던 건 s3 를 남겨 뒀기 때문이다.
    """
    from scripts.extract.to_page_json import EXTRACTOR

    return f"s{SCHEMA_VERSION}_{EXTRACTOR.replace('/', '-').replace(':', '')}"

#: 조항 머리. ★**줄 시작**에 있는 것만 인정한다.
#: 본문 속 상호참조("제4조에 따라…")를 머리로 오인하면 5~6자짜리 가짜 조항이 쏟아진다
#: (실측: 그렇게 391개 중 112개(28%)가 100자 미만이었다).
#: 조항 머리는 줄 첫머리에 오고, 상호참조는 문장 중간에 온다.
_ARTICLE = re.compile(
    r"^[ \t ]{0,6}제\s*(\d{1,3})\s*조(?:의\s*(\d{1,2}))?"
    #: 제목 괄호는 `()` 말고 `【】` `[]` 도 쓴다(NH 문서 실측).
    r"\s*(?:[（(\[【]\s*([^)）\]】\n]{1,60})\s*[)）\]】])?",
    re.MULTILINE,
)
#: ★특별약관은 `제N조` 대신 **`N. (제목)`** 을 쓴다.
#:
#:   자동차사고 변호사선임비용(...)(실손) 특별약관
#:   1. (특별약관의 적용범위 및 효력)
#:   2. (보험금의 지급사유)
#:
#: 이걸 몰라서 표본 10건 중 4건이 "조항 머리를 하나도 못 찾음"으로 실패했다.
#:
#: ★오탐이 무섭다. 본문에는 `1.` 로 시작하는 번호 목록이 널려 있다.
#:   그래서 **줄 전체가 `N. (제목)` 인 것만** 인정한다.
#:   실측(실패 문서 13쪽): 넓은 `^\d+\.` 후보는 258행인데
#:   줄 전체가 `N. (제목)` 인 것은 **6행**뿐이었다(코덱스 교차검증).
#:
#: `N-M.` 도 받는다(조항의 세분화. 실측 21/37 문서에서 발견).
#:
#: ★v5 — 제목 안의 **중첩 괄호**를 받는다. `[^)）\]】\n]` 로 닫는 괄호를 막았더니
#:   제목에 괄호가 들어간 조항을 통째로 놓쳤다. 하필 판정에 중요한 것들이다:
#:
#:     4. (보험료의 납입을 연체하여 해지된 계약의 부활(효력회복))
#:     30. (보험료의 납입이 연체되는 경우 납입최고(독촉)와 계약의 해지)
#:     26-1. (보험료 납입면제)(3종, 5종限)
#:
#:   ★그래도 **줄이 닫는 괄호로 끝나야 한다**는 제약은 유지한다. 이게 오탐을 막는
#:     핵심이다. 실측(DB손보 25문서)에서 이 제약 덕에 아래 상호참조가 전부 걸러진다:
#:       `11.(계약 전 알릴의무) 및 13.(알릴 의무 위반의 효과),`   ← 쉼표로 끝남
#:       `38.(해약환급금) ①에 따른 해약환급금을 …지급합니다.`      ← 마침표로 끝남
#:       `4-1.(보상하지 않는 사항)에 따라 보상합니다.`             ← 마침표로 끝남
#:     `_3rd_project_4` 는 줄 끝 제약이 없어 이것들을 전부 조항으로 센다.
_NUMBERED = re.compile(
    r"^[ \t ]{0,6}(\d{1,3})(?:-(\d{1,2}))?\s*[.．]\s*"
    r"[（(\[【]\s*([^\n]{2,80}?)[)）\]】][ \t]*$",
    re.MULTILINE,
)
#: `N. (제목)` 을 조항으로 인정하려면 **번호열이 형성**돼야 한다.
#: 단독으로 하나만 있으면 본문 인용일 수 있다.
NUMBERED_MIN_HEADS = 3

#: 조항 하나가 이보다 길면 경계를 못 찾은 것이다.
#: 실측: 정상 문서의 p95 는 4,216자였고 최대도 11,194자였다.
#: 반면 깨진 문서는 90,385 / 197,589 자가 나왔다.
CLAUSE_MAX_CHARS = 30_000

#: 목차 판정용(줄 위치 무관). 목차는 조 번호가 촘촘히 나열되므로 전체 검색이 맞다.
_ARTICLE_ANY = re.compile(r"제\s*\d{1,3}\s*조")
#: ★`제N조` 바로 뒤에 오면 **상호참조**라는 신호. 제목 없는 머리에만 적용한다.
#:   `제42조제1항…` 처럼 항·호가 이어지거나, `에 의한/에 따라/에서 정한` 조사가 붙는다.
_REF_TAIL = re.compile(
    r"^\s*(?:제\s*\d|의\s*\d|에\s*(?:따라|따른|의하여|의해|의한|정한|규정)"
    r"|에서\s*정한|의\s*규정|와\s|과\s|및\s|부터|까지|내지)"
)
#: ★항(項)과 호(號)는 **다른 층이다.** v4 의 `_PARA` 는 둘을 섞었다
#:   (`([①-⑳]|\d{1,2}\.)` — `①` 과 `1.` 을 같은 것으로 봤다).
#:   그래서 `paragraph_count` 가 항 수도 호 수도 아닌 값이었고,
#:   **"제N조 제M항"을 조립할 수 없었다.**
#:
#:   약관의 층은 이렇다:  조 > 항(①②③) > 호(1. / 가. / 1)) > 목((1) / (가) / 가))
#:   실측(표본 120문서): 항 52,046 · 호 78,162(숫자점 47,368 · 가나다 18,507 · 숫자괄호 12,287)
#:
#: ★줄머리에 있는 것만 인정한다. 조 머리와 같은 이유다 —
#:   본문 중간의 `①` 은 인용이거나 표 안의 기호다.
#:   (DB손보 일부 문서는 항 마커가 문장 중간에 인라인으로 온다. 그건 못 잡는다 —
#:    잡으려다 오탐을 만드느니 놓치고 세는 편이 낫다.)
_PARA_MARK = re.compile(r"^[ \t]*([①-⑳])[ \t]*")

#: ★번호를 **못 읽은** 항. 지우지 않고 센다.
#:
#:   `①~⑨` 는 보조 사용자영역(U+F02B1~F02B9)에서 복구했지만(`to_page_json`),
#:   `⑩` 부터는 두 글자가 겹쳐 나와 산술 매핑이 **틀린다**(v4 리포트 §3).
#:   그래서 복구하지 않았고, 그 자리엔 아직 PUA 문자가 남아 있다.
#:   실측(표본 120문서): 줄머리 미매핑 PUA **241개 / 12문서**
#:   (`U+F02BA` 52 · `U+F0289` 42 · `U+F02C3` 31 …).
#:
#:   이런 항은 `paragraph_no=null` 로 두고 `stats.unresolved_paragraphs` 에 센다.
#:   **번호를 지어내지 않는다** — 틀린 "제10항"은 없는 것만 못하다.
_PARA_UNKNOWN = re.compile(r"^[ \t]*([\U000F0000-\U000FFFFD])[ \t]*")

#: 호(號). `1.` `가.` `1)` 세 조판을 다 받는다.
#:
#: ★`re.MULTILINE` 이 없으면 `^` 가 **문자열 맨 앞에만** 걸려 호가 0개로 나온다
#:   (첫 구현에서 그렇게 나왔다. 항은 줄 단위로 돌려 무사했고 호만 비었다).
#:
#: ★★`[가-하]` 로 쓰면 안 된다. 정규식의 `-` 는 **유니코드 코드포인트 범위**라
#:   `가`(U+AC00)~`하`(U+D558) 사이 **10,585자**를 전부 잡는다 —
#:   `값.` `강.` `곱.` 처럼 **아무 한글 음절 + 마침표**가 호가 된다.
#:   문장이 한 글자로 끝나고 마침표가 오면 그게 전부 호로 잡혔다(코덱스가 잡았다).
#:   가나다 호 기호는 **14개뿐**이므로 그대로 나열한다.
_HO_KOR = "가나다라마바사아자차카타파하"
_ITEM_MARK = re.compile(
    r"^[ \t]*(\d{1,2}\.|[" + _HO_KOR + r"]\.|\d{1,2}\))[ \t]", re.MULTILINE
)

#: 원문자 → 숫자. `chr(0x2460)` 이 `①`.
_CIRCLED_NO = {chr(0x2460 + i): i + 1 for i in range(20)}
#: ★목차 신호 1 — **점선(dot leader)**.
#: 목차 줄은 `제1 조 【보장종목】 ......................... 1` 꼴이다.
#: 이 점선이 글자수를 부풀려 목차 판정을 무력화했다(실측: p25 비율 202 로
#: 임계 200 을 간발로 넘겨 본문으로 오판 → 점선 제거 후 33).
_DOTS = re.compile(r"[.·․‥…]{5,}")

#: ★목차 신호 2 — 페이지에 찍힌 **`목 차`** 표시. 비율 추정보다 확실하다.
#: 실측: 25,000자짜리 '조항'의 꼬리가 `251 / 401              목 차` 였다.
_TOC_MARK = re.compile(r"^\s*목\s*차\s*$", re.MULTILINE)

#: ★목차 신호 3 — 줄 끝의 페이지 번호. `… 121` 처럼 끝난다.
#: ★`\s` 를 쓰면 줄바꿈을 먹어 **다음 줄까지 넘어가 매칭된다.** `[ \t]` 로 못박는다.
#:   (아래 `_TOC_TITLE` 주석의 롯데손보 사고와 같은 원인)
_TOC_LINE = re.compile(r"[.·․‥…]{5,}[ \t]*\d{1,4}[ \t]*$", re.MULTILINE)

#: ★목차 신호 4 (v5 신설) — **목차 항목 줄**.
#:
#:   v4 는 "조 머리가 촘촘하고 머리당 글자가 짧으면 목차"라는 **비율**로 판정했다.
#:   그게 본문을 목차로 오판했다. 원인은 **법령 인용이 조밀한 본문**이다.
#:
#:     "「국민건강보험법」제5조, 제53조, 제54조에 따라 요양급여 또는
#:      「의료급여법」제4조, 제15조, 제17조에 따라 의료급여를 …"
#:
#:   실측(NH농협생명 176쪽 `42ce81976809`) — 같은 지표로 목차와 본문이 안 갈린다:
#:
#:     쪽  실제      제N조수  글자수/n   v4판정
#:     19  진짜목차     11      39.5     TOC  (정답)
#:     86  본문          7     202.3     BODY (정답)
#:     87  본문          8     170.1     TOC  ✗
#:     93  본문          7     181.0     TOC  ✗
#:     115 본문         13     100.3     TOC  ✗
#:
#:   전량 집계: 162,678쪽 중 29,221쪽(18.0%)을 목차로 뺐고,
#:   그중 25,736쪽(**전체의 15.8%**)이 **비율 규칙 단독**이었다.
#:   피해: 그 NH 문서의 「보상하지 않는 사항」이 목차상 3개인데 산출물엔 1개뿐이고,
#:   그 1개조차 p39·40·41·44 가 빠진 채 조립됐다 — **면책 조항이 없는 것처럼 보인다.**
#:
#: ★그래서 비율을 버리고 **구조**를 본다. 목차 줄에는 반드시 **쪽번호**가 붙는다.
#:   조판이 둘이라 양쪽을 다 받는다.
#:     (가) 같은 줄 끝에 번호   `제1 조 【보장종목】 ......... 28`
#:     (나) 다음 줄에 번호      `제1조【보장종목】` / 다음 줄 `28`   ← NH 실측
#:
#:   실측 대조(같은 문서): 목차 p19·20·23 은 쌍이 15·28·24 개,
#:   본문 p29·35·58·86·87·93·102·115·167 은 **전부 0** 이었다. 겹치지 않는다.
_TOC_ENTRY_HEAD = re.compile(r"^\s*제\s*\d{1,3}\s*(?:조(?:의\s*\d{1,2})?|관|절|장|편)")
_NUM_ONLY_LINE = re.compile(r"^\s*\d{1,4}\s*$")
_TRAILING_NUM = re.compile(r"\d{1,4}\s*$")


def _toc_entry_count(text: str) -> int:
    """`제N조…` + 쪽번호(같은 줄 끝 또는 다음 줄) 꼴의 목차 항목 줄 수."""
    lines = [ln for ln in text.split("\n") if ln.strip()]
    n = 0
    for i, ln in enumerate(lines):
        if not _TOC_ENTRY_HEAD.match(ln):
            continue
        if _TRAILING_NUM.search(ln) or (
            i + 1 < len(lines) and _NUM_ONLY_LINE.match(lines[i + 1])
        ):
            n += 1
    return n


#: ★목차 신호 1 (v5 재작성) — **줄이 `…목차` 로 끝나는 제목 줄**.
#:
#:   v4 는 `^\s*목\s*차\s*$` — `목 차` **단독 줄**만 봤다. 두 가지가 틀렸다.
#:
#:   (1) ★`\s` 가 **줄바꿈을 먹는다.** 단독 줄 제한이 사실은 없었다.
#:       롯데손보는 페이지 옆에 세로 탭으로 `목` / `차` 를 한 글자씩 찍는데,
#:       그게 `목\n차` 로 추출돼 매칭됐다. **실제 본문 103쪽(2문서)이 통째로 삭제**됐다
#:       (`1b922d9f78a4` 53쪽 · `1cdb018a5867` 50쪽 — p40 은 세로 내비 바로 뒤에
#:       보통약관 제1조 본문이 온다). 코덱스가 잡았다. `[ \t]` 로 못박는다.
#:
#:   (2) 제목이 `목 차` 단독인 문서가 오히려 소수다. 실제로는
#:       `상품 목차` `상황별 목차` `별표 목차` `보통약관(공통조항) 목차` 처럼 쓴다.
#:       이걸 못 잡아 **825쪽(399문서)** 의 목차가 본문으로 새고 있었다
#:       (삼성생명 602 · 현대해상 141 · 삼성화재 82 — 코덱스 실측).
#:
#:   그래서 "짧은 줄이 `목차`로 끝나면 목차 제목"으로 바꾼다.
#:   DB손보의 `☞ 목차로 돌아가기` 는 줄 끝이 `목차` 가 아니라 안 걸린다
#:   (그 문구는 28문서 2,791쪽에 있다 — 잘못 잡으면 피해가 크다).
_TOC_TITLE = re.compile(r"^[ \t]*[^\n]{0,80}목[ \t]*차(?:[ \t]+\d{1,4})?[ \t]*$", re.MULTILINE)

#: ★목차 신호 5 — **목차 연속 페이지**.
#:   목차 제목은 첫 장에만 찍힌다. 둘째 장부터는 제목도 점선도 없이 항목만 이어진다.
#:   현대해상 `dc72bc8035b4` p33 이 `보통약관(공통조항) 목차`, p34 가 그 연속인데
#:   p34 는 어느 신호에도 안 걸려 **가짜 조항 23개**를 만들었다.
#:   조건: 직전 쪽이 목차 제목 쪽 + 목차항목 3개 이상 + 조 머리 6개 이상.
#:   실측 24쪽(17문서) 추가 검출, **전부 목차 연속 페이지였다**(코덱스 확인).
TOC_CONT_MIN_ENTRY = 3
TOC_CONT_MIN_HEAD = 6

#: ★★폐기한 신호 — "조 머리 줄 밀집도"(`제N조…` 로 시작하는 줄의 비율).
#:
#:   신호 4(목차항목)가 **쪽번호에 의존**하는 게 약점이라 보고, 쪽번호 없이
#:   "조 머리가 빽빽하면 목차"라는 신호를 넣었다. 실측으로 갈리는 듯했다:
#:     목차 p19 15/40=0.38 · p20 30/59=0.51   /   본문 p87 1 · p93 1 · p115 1
#:
#: ★**코덱스가 반례를 찾았다 — 관계법령 부록.**
#:   약관 뒤에 붙는 인용 법령 페이지는 목차와 **구조가 같다.**
#:
#:     제250조(살인, 존속살해)          ← 조 머리 줄
#:     ① 사람을 살해한 자는 …           ← 본문 줄
#:     제252조(촉탁, 승낙에 의한 살인 등)
#:     ① 사람의 촉탁 또는 승낙을 받어 …
#:
#:   실측 밀집도: 흥국생명 `76e173e5747b` p131 = 15/45(0.33),
#:   NH손보 `ba396b382a73` p287 = 16/39(0.41) — **목차(0.38~0.51)와 겹친다.**
#:   그 외 동양생명 단체취급특약(조항이 한 줄씩 짧다)·삼성화재 본문도 걸렸다.
#:
#:   밀집도로는 목차와 법령 부록을 **가를 수 없다.** 그래서 신호를 뺀다.
#:   대가로 현대해상 구형 목차(`제1장 일반사항 1 / 제1조 (담보종목)` — 쪽번호가
#:   장에만 붙는 조판)를 놓친다. 그건 감수한다 —
#:   **본문을 지우는 것보다 가짜 조항이 섞이는 편이 낫다.** 가짜는 눈에 띄고,
#:   지운 것은 아무도 모른다(그게 v4 의 15.8% 였다).


def _toc_signals(text: str) -> dict[str, int]:
    """발동한 목차 신호 **전부**를 `{이름: 수치}` 로.

    ★첫 신호만 반환하지 않는다. 페이지를 통째로 버리는 판단이라
      나중에 "왜 이게 빠졌나"를 되짚을 수 있어야 한다(코덱스 지적).
    """
    sig: dict[str, int] = {}
    if _TOC_TITLE.search(text):
        sig["목차제목"] = 1
    n = len(_TOC_LINE.findall(text))
    if n >= TOC_MIN_HEADS:
        sig["점선줄"] = n
    n = _toc_entry_count(text)
    if n >= TOC_MIN_HEADS:
        sig["목차항목"] = n
    return sig


def _toc_verdict(text: str) -> str:
    """목차면 대표 신호 이름, 아니면 빈 문자열. (연속 페이지 판정은 `build()` 안에 있다)"""
    sig = _toc_signals(text)
    return next(iter(sig), "")

#: 부(部) 경계. ★**단독 줄로 나온 것만** 인정한다.
#: 초안은 페이지 앞 400자에서 아무 데나 매칭해 '용어의정의'가 266개로 잡혔다 —
#: 그건 부 제목이 아니라 **조항 제목**이었다. 실측으로 확인한 실제 부 제목은
#: p23 '보통약관', p63 '별표' 처럼 **한 줄에 그것만** 있다.
_SECTION_LINE = re.compile(r"^\s*(보통약관|특별약관|별\s*표\s*\d*|부\s*록|약관\s*요약서)\s*$")

#: ★★인용 법령 구간 — **약관 조항이 아니다.**
#:
#:   약관 뒤에는 본문이 인용한 법령 원문이 통째로 실린다(상법·의료법·개인정보보호법…).
#:   그게 그대로 조항으로 잡히면 판정이 이렇게 근거를 댄다:
#:
#:       "단체취급 특별약관 제651조(고지의무위반으로 인한 계약해지)에 따르면 …"
#:
#:   **그런 조항은 약관에 없다.** 제651조는 상법이다. 부(部) 이름이 직전 특별약관에서
#:   안 바뀌어 그대로 따라붙은 것이다.
#:   실측(표본 300문서): 조 번호 100 이상 3,086개 중 **604개가 약관 부 이름**을 달고 있었다
#:   (`별표` 1,362 · `부록` 1,120 은 구분이 됐다).
#:
#:   문서에 **명시적 경계가 있다.** 보험사별 표기를 실측(표본 250문서)했다:
#:     `[법규5] 상법`      삼성화재 1,494 · 현대해상 196 · DB 50 · NH손보 37 · KB 22
#:     `인용법·규정`        현대해상 163 · 롯데 5 · 메리츠 2
#:     `○ 개인정보보호법`   흥국생명 81 · 현대해상 11
#:
#:   추정이 아니라 **문서가 스스로 표시한 것**을 읽는다.
_STATUTE_HEAD = re.compile(r"^\s*[\[【]\s*법\s*규\s*\d{0,3}\s*[\]】]\s*(.{2,30}?)\s*$")
#: 구간 진입만 알리고 법령명은 안 주는 표기.
_STATUTE_ZONE = re.compile(
    r"^\s*(관\s*계\s*법\s*령|관\s*련\s*법\s*규|약관에서\s*인용된\s*법령"
    r"|인용\s*법[·․.]?\s*규정|약관\s*인용\s*법[·․.]?\s*규정)\s*$"
)
#: ★`○` 뒤에 **공백이 있어야** 한다. 없으면 `○보험금지급안내및심사절차조회방법` 같은
#:   안내 문구가 `…방법` 의 `법` 때문에 걸린다(실측).
_STATUTE_LAW = re.compile(r"^\s*○\s+(.{2,20}법(?:\s*시행령|\s*시행규칙)?)\s*$")
#: ★역순 판본 — 법 이름 줄 **다음 줄**에 `○` 만 있다(글리프 순서 뒤집힘).
#:   `○` 짝을 **반드시** 요구한다. 이름 줄만 보면 `…하는 방법` 류가 다 걸린다.
_STATUTE_LAW_REV = re.compile(r"^\s*(.{2,30}?(?:법|법률|시행령|시행규칙))\s*$")
_STATUTE_BULLET = re.compile(r"^\s*[○ㅇ●]\s*$")

#: ★부 경계를 넓힌다 — 위 규칙만으로는 403쪽 약관의 부가 2개뿐이었고,
#:   그래서 조 번호가 **52종 충돌**했다(제2조가 51회). 특별약관마다 조 번호가
#:   1부터 다시 시작하는데 구분이 안 돼 섞인 것이다.
#:
#:   코덱스 교차검증으로 얻은 목록. **제목이 `약관`/`특약`으로 끝나는 줄**이 경계다.
#:     `○○ 보통약관` `○○ 특별약관` `○○ 특약` `제도성특약`
#:     `단체취급특약` `지정대리청구서비스특약` `1-1. [건강] ○○ 특별약관`
#:
#:   ★`제N편` `제N장` `제N절` `제N관` 은 **경계가 아니다.**
#:     `특별약관 → 제1관 일반사항 → 제1조` 구조가 실재하므로,
#:     이걸 경계로 삼으면 **과분할**된다(코덱스 지적).
_SECTION_TITLE = re.compile(
    r"^[ \t]{0,8}(?:\d{1,2}(?:-\d{1,2})?\.?\s*)?"      # 앞의 번호 접두어(선택)
    r"(?:\[[^\]\n]{1,12}\]\s*)?"                        # `[건강]` 같은 분류(선택)
    r"([^\n]{2,40}?(?:특별약관|특약|보통약관|주계약\s*약관))\s*$",
    re.MULTILINE,
)

#: ★제목처럼 생겼지만 제목이 아닌 줄을 걸러 낸다.
#:
#:   처음엔 길이 제한만 뒀더니 이런 본문이 부 제목으로 잡혔다(실측).
#:     "② 해당계약에 단체취급특약이 부가되어 있는 경우에는 이 특약에 대하여도 단체취급특약"
#:     "‘특약 색인(索引)’을 활용하시면 본인이 실제 가입한 특약"
#:
#:   제목은 **문장이 아니다** — 항 번호로 시작하지 않고, 쉼표·마침표가 없고,
#:   조사로 끝나지 않는다.
_PARA_START = re.compile(r"^[ \t]*[①-⑳]")            # 항 번호로 시작하면 본문이다
_SENTENCE_MARK = re.compile(r"[,，.。;；]")            # 문장부호가 있으면 본문이다
_NOT_SECTION_TAIL = re.compile(
    r"(?:은|는|이|가|을|를|의|에|로|와|과|및|한다|합니다|따라|경우|하시면)$"
)


def _looks_like_section(line: str, title: str) -> bool:
    """부 경계 제목인가. 하나라도 걸리면 아니다."""
    if _PARA_START.match(line):
        return False
    if _SENTENCE_MARK.search(title):
        return False
    if _NOT_SECTION_TAIL.search(title):
        return False
    if _DOTS.search(line):          # 목차 줄
        return False
    #: 따옴표·괄호 인용으로 시작하면 본문 안의 언급이다.
    if title.lstrip()[:1] in "‘’“”\"'":
        return False
    return True

#: 목차로 보려면 그 신호가 몇 줄 이상 나와야 하는가.
#: 한두 줄은 본문에도 나온다(`제3조(의료기관) 참조 12` 같은 인용).
TOC_MIN_HEADS = 6

#: ★v4 의 비율 임계 `TOC_MAX_CHARS_PER_HEAD = 200` 은 **삭제했다.**
#:   본문(100~200)과 목차(≈40)가 이 축에서 겹쳐, 본문 25,736쪽을 목차로 버렸다.
#:   상세는 위 `_TOC_ENTRY_HEAD` 주석. 되살리지 말 것.
#:
#: v5 교체 효과(1,367문서 162,678쪽 전량 재계산):
#:   목차 제외      29,221쪽(18.0%) → 3,970쪽(2.4%)
#:   되찾은 본문    25,252쪽  — 12개사 층화 표본 39건 전수 확인, **오판 0건**
#:   새로 잡은 목차     1쪽  — 실제 목차였다(`목 차 21` + 조 제목·쪽번호).
#:                              v4 는 `목 차` 뒤에 쪽번호가 붙어 단독 줄이 아니라 놓쳤다


def _norm(text: str) -> str:
    """해시 계산용 정규화. 공백·줄바꿈 차이로 다른 조항이 되지 않게 한다."""
    return re.sub(r"\s+", " ", text).strip()


#: ★부록(별표·붙임·분류표) 시작 마커. **줄머리에 있는 것만** 인정한다.
#:
#:   문장 안 인용을 잡으면 조항 본문이 잘린다. 실측 반례(코덱스, 10건 확인):
#:     `[별표 2] …에서 정한 이율`  ·  `<붙임2>에서 정한 이율`
#:     `[별표1]의 보험금지급기준표에서 적용하는 …`
#:   그래서 **마커 뒤에 조사·서술어가 이어지면 참조**로 보고 거부한다.
#:
#:   표기는 보험사마다 다르다(코덱스 전수, s5 기준 발생 수):
#:     `[별표 N]` 2,171 · `[별표N]` 1,794 · `【별표N】` 1,121 · `<붙임N…>` 998
#:     `<붙임>` 604 · `[붙임N]` 480 · `[별첨 N]` 98 · `[별표-…]` 206(삼성화재)
#:     질병표 단독 줄: 특정질병분류표 430 · 특정신체부위분류표 245 · 재해분류표 106
#: ★★마커가 **줄바꿈으로 깨져 있다.** 글리프 중복 렌더링 때문이다.
#:   실측(`16b227ff95b8` p13) — `[붙임]` 하나가 이렇게 나온다:
#:       `[붙임`  /  `붙임`  /  `붙임`  /  `붙임] `
#:   닫는 괄호를 **같은 줄에서** 요구하면 진짜 경계를 통째로 놓친다(코덱스 지적).
#:   그래서 닫는 괄호까지 **줄바꿈을 건너뛰며** 찾되, 30자 안이어야 한다.
#:   (`[별표` 만 허용하면 과탐한다 — 닫힘은 여전히 요구한다.)
_ANNEX_HEAD = re.compile(
    r"^[ \t]{0,6}(?:"
    r"[\[［【]\s*(?:별\s*표|붙\s*임|별\s*첨)[^\]］】]{0,30}?[\]］】]"
    r"|[＜<]\s*(?:별\s*표|붙\s*임)[^＞>]{0,30}?[＞>]"
    r"|(?:특정질병|특정신체부위|특정부위|질병|재해|장해)\s*분류표"
    r")",
    re.MULTILINE,
)
#: 마커 **직후**에 이런 게 오면 부록 시작이 아니라 본문 안의 참조다.
_ANNEX_REF_TAIL = re.compile(
    r"^\s*(?:에서|에\s|의\s|중에서|과\s|와\s|을\s|를\s|이\s|가\s|참조|따라|같이|정한|규정)"
)


#: ★겹침 렌더링으로 같은 토큰이 여러 번 찍히는 아티팩트.
#:   `[붙임 / 붙임 / 붙임 / 붙임]` 처럼 줄바꿈으로 갈리기도 하고,
#:   제목이 `준용규정 준용규정 준용규정 준용규정` 으로 나오기도 한다.
#:   ★**라벨·제목에만** 쓴다. 본문에 쓰면 정당한 반복까지 지운다.
_WS_ANY = re.compile(r"\s+")
_GLYPH_DUP = re.compile(r"(\S{1,12})(?:\s+\1){2,}")


def _collapse_dup(s: str) -> str:
    """겹침 글리프를 1회로 줄인다. **3회 이상** 연속일 때만."""
    return _GLYPH_DUP.sub(r"\1", _WS_ANY.sub(" ", s)).strip()


def _annex_events(pages, toc_pages) -> list[tuple[int, int, str]]:
    """부록 시작 지점을 `(page, offset, 라벨)` 로 모은다.

    ★페이지 단위 `current` 가 아니라 **`(page, char_offset)` 이벤트**여야 한다.
      같은 페이지에서 조항 뒤에 별표가 시작하면 페이지 단위로는 표현할 수 없다(코덱스).
    """
    out = []
    for pg in pages:
        if pg["page"] in toc_pages:
            continue
        t = pg["text"]
        for m in _ANNEX_HEAD.finditer(t):
            tail = t[m.end():m.end() + 12]
            if _ANNEX_REF_TAIL.match(tail):
                continue          # 본문 안의 참조다
            out.append((pg["page"], m.start(), _collapse_dup(m.group(0))))
    return out


def _section_events(pages, toc_pages) -> list[tuple[int, int]]:
    """부(部)·특약이 바뀌는 지점을 `(page, offset)` 로 모은다.

    ★★조 본문과 부록을 **여기서도 끊는다.**

      안 끊으면 한 특약의 **마지막 조가 다음 특약 제목을 삼킨다.**
      실측(s6, 끊기 전): 조항 194,160개 중 **6,575개(3.39%)** 의 본문이
      다음 약관 제목으로 끝났다. 거의 전부 `준용규정` 이다 —

          `4.(준용규정) … 보통약관을 따릅니다. 4 특별조건부(표준하체 인수) 특별약관`

      두 가지가 망가진다.
        1. 인용에 **남의 특약 이름**이 붙는다.
        2. 같은 `준용규정` 이 문서마다 꼬리가 달라 `content_hash` 가 갈린다 —
           **중복 제거가 그만큼 덜 된다.** 이 조항은 문서마다 거의 똑같은데도.

    ★목차 페이지는 제외한다. 목차 안의 특약 이름은 경계가 아니다.
    """
    out = []
    for pg in pages:
        if pg["page"] in toc_pages:
            continue
        off = 0
        for line in pg["text"].splitlines(keepends=True):
            s = line.rstrip("\n")
            hit = bool(_SECTION_LINE.match(s))
            if not hit:
                m = _SECTION_TITLE.match(s)
                #: ★`_looks_like_section` 을 반드시 통과시킨다. 이걸 빼면
                #:   "…특별약관에서 정하지 않은 사항은" 같은 **문장**이 경계가 된다.
                hit = bool(m and _looks_like_section(s, m.group(1).strip()))
            if hit:
                out.append((pg["page"], off))
            off += len(line)
    return out


def _statute_events(pages, toc_pages) -> list[tuple[int, int]]:
    """인용 법령이 시작하는 지점을 `(page, offset)` 로 모은다.

    ★`statute_of_page` 는 **페이지 단위**라 부록 종료 판단에 쓸 수 없다(코덱스).
      한 페이지에서 별표가 먼저 나오고 그 아래 법령이 시작하면
      그 페이지는 이미 `True` 라 `if not statute_of_page[ap]` 에 걸려
      법령 경계 검사를 통째로 건너뛴다. 실측: `e87074365e0f` 의
      `장해분류표` 가 p79~136·114,724자로 호스피스 법률조문까지 삼켰는데
      페이지 단위 규칙이 **발동조차 하지 않았다.**
    """
    out = []
    for pg in pages:
        if pg["page"] in toc_pages:
            continue
        lines = pg["text"].splitlines(keepends=True)
        bare = [l.rstrip("\n") for l in lines]
        off = 0
        for i, line in enumerate(lines):
            s = bare[i]
            hit = bool(_STATUTE_HEAD.match(s) or _STATUTE_LAW.match(s) or _STATUTE_ZONE.match(s))
            #: ★글리프 순서가 뒤집힌 판본이 있다 — 법 이름 줄 **다음**에 `○` 줄이 온다.
            #:   실측: 정순 `○ 상법` 515건/106문서, 역순 283건/**9문서**.
            #:   역순 9문서는 정순 형태를 하나도 안 쓴다 — 못 잡으면 그 문서는
            #:   관계법령 경계가 **통째로 없다.** 실제로 `e87074365e0f` 의
            #:   `장해분류표` 가 법령 58쪽을 삼킨 원인이 이것이었다.
            if not hit and i + 1 < len(bare):
                hit = bool(_STATUTE_LAW_REV.match(s) and _STATUTE_BULLET.match(bare[i + 1]))
            if hit:
                out.append((pg["page"], off))
            off += len(line)
    return out


def _split_paragraphs(body: str, clause_cite: str) -> tuple[list[dict], int]:
    """조 본문 → 항(項) 목록. `(항 목록, 번호를 못 읽은 항 수)`.

    ★9단계(계층형 청킹). v4 는 이걸 **하는 일로 적어 놓고 하지 않았다** —
      `_PARA.split()` 결과의 **개수만** 세고 버렸다.

    반환하는 항 하나:
        paragraph_no  항 번호(정수). ★못 읽으면 `None` — 지어내지 않는다
        marker        원문 표기(`①`). 못 읽었으면 빈 문자열
        text          항 본문(마커 줄 포함)
        citation      `제4조(보상하지 않는 사항) 제1항`
        items         호 위치 `[{item_no, offset, length}]`
                      ★본문을 복사하지 않고 **항 텍스트 안의 위치**로 준다.
                        복사하면 산출물이 두 배가 되고, 두 벌이 어긋날 수 있다.

    ★첫 항 앞의 서술은 버리지 않는다. `paragraph_no=None` 인 **조 서두**로 남긴다
      (항 없이 한 문장으로 끝나는 조가 실제로 많다).
    """
    lines = body.split("\n")
    paras: list[dict] = []
    cur: dict | None = None
    unresolved = 0

    def flush() -> None:
        if cur is None:
            return
        text = "\n".join(cur["_lines"]).strip()
        if not text:
            return
        no = cur["paragraph_no"]
        cite = clause_cite + (f" 제{no}항" if no else "")
        items: list[dict] = []
        first_para = not paras
        for m in _ITEM_MARK.finditer(text):
            #: 줄머리만 인정 — `re.MULTILINE` 없이 줄 단위로 재확인한다.
            if m.start() and text[m.start() - 1] != "\n":
                continue
            #: ★조 머리 자신을 호로 세지 않는다.
            #:   특별약관은 조 머리가 `1. (보장종목)` 꼴이라 `_ITEM_MARK` 에 그대로 걸린다.
            #:   조 본문의 맨 앞은 언제나 그 조의 머리이므로 건너뛴다(코덱스 지적).
            if first_para and m.start() == 0:
                continue
            raw = m.group(1)
            style = ("숫자점" if raw.endswith(".") and raw[0].isdigit()
                     else "숫자괄호" if raw.endswith(")")
                     else "가나다")
            items.append({"item_no": raw.rstrip(".)"), "offset": m.start(),
                          "length": 0, "_style": style})
        #: ★한 항 안에 두 층이 섞이면 **위층만 호**다.
        #:
        #:   `가.` 는 호일 수도 목(目)일 수도 있다. 문맥 없이는 못 가른다.
        #:     1. 제1차 의료급여기관          ← 호
        #:        가. 「의료법」 …            ← 목 (호 아래)
        #:        나. 제1항제2호 …            ← 목
        #:   전부 호로 세면 `1.` 호의 범위가 `가.` 에서 잘린다(코덱스 지적).
        #:
        #:   약관 조판은 한 층에 한 표기를 쓴다. 그래서 **우세한 표기만 호로 인정**하고
        #:   나머지는 그 호의 본문 안에 그대로 둔다(목은 나누지 않는다 — 독스트링 참조).
        if items:
            order = ["숫자점", "숫자괄호", "가나다"]
            present = {it["_style"] for it in items}
            top = next(s for s in order if s in present)
            items = [it for it in items if it["_style"] == top]
        for it in items:
            del it["_style"]
        for i, it in enumerate(items):
            nxt = items[i + 1]["offset"] if i + 1 < len(items) else len(text)
            it["length"] = nxt - it["offset"]
        paras.append({
            "paragraph_no": no, "marker": cur["marker"], "text": text,
            "char_length": len(text), "citation": cite, "items": items,
        })

    for ln in lines:
        m = _PARA_MARK.match(ln)
        if m:
            flush()
            cur = {"paragraph_no": _CIRCLED_NO[m.group(1)], "marker": m.group(1),
                   "_lines": [ln]}
            continue
        mu = _PARA_UNKNOWN.match(ln)
        if mu:
            flush()
            unresolved += 1
            #: ★번호를 모른다. `None` 으로 두고 센다. 추정 금지.
            cur = {"paragraph_no": None, "marker": "", "_lines": [ln]}
            continue
        if cur is None:
            cur = {"paragraph_no": None, "marker": "", "_lines": []}
        cur["_lines"].append(ln)
    flush()
    return paras, unresolved


#: 조 번호 파서. ★`to_clauses` 와 `struct_audit` 이 **같은 것을 써야** 한다.
#:   두 곳이 어긋나면 산출물의 `structure_faults` 와 감사 결과가 다른 말을 한다.
_CLAUSE_NO = re.compile(r"^제(\d{1,3})조|^(\d{1,3})(?:-\d{1,2})?\.")


def _clause_num(clause_no: str) -> int | None:
    """`제12조` -> 12 · `4-1.` -> 4 · 그 밖 -> None."""
    m = _CLAUSE_NO.match(clause_no or "")
    if not m:
        return None
    g = m.group(1) or m.group(2)
    return int(g) if g else None


def _clause_hash(section: str, title: str, body: str) -> str:
    """★조항의 정체성. **번호를 넣지 않는다** — 번호가 바뀌어도 내용이 같으면 같은 조항이다."""
    return hashlib.sha256(f"{section}\x1f{title}\x1f{_norm(body)}".encode()).hexdigest()


def build(page_doc: dict) -> dict:
    pages = page_doc["pages"]
    if not pages:
        raise ValidationErr("페이지가 없습니다.")

    # ── 목차 페이지 식별 (6단계 정확도의 전제) ──
    #: ★v5 — 비율을 버리고 **구조**를 본다. 근거는 `_TOC_ENTRY_HEAD` 주석.
    #:   페이지를 통째로 버리는 판단이므로 **왜 버렸는지 반드시 남긴다.**
    toc_pages: set[int] = set()
    toc_reasons: dict[int, dict[str, int]] = {}
    prev_titled = False          # 직전 쪽이 `…목차` 제목 쪽이었나
    for pg in pages:
        text = pg["text"]
        sig = _toc_signals(text)
        #: ★목차 둘째 장 — 제목도 점선도 없이 항목만 이어진다.
        if not sig and prev_titled:
            e = _toc_entry_count(text)
            h = len(_ARTICLE.findall(text))
            if e >= TOC_CONT_MIN_ENTRY and h >= TOC_CONT_MIN_HEAD:
                sig = {"목차연속": h}
        if sig:
            toc_pages.add(pg["page"])
            toc_reasons[pg["page"]] = sig
        prev_titled = "목차제목" in sig or "목차연속" in sig

    # ── 5) 문서 구조 복원: 단독 줄로 나온 부 제목만 인정 ──
    section_of_page: dict[int, str] = {}
    statute_of_page: dict[int, bool] = {}
    current = "머리말"
    in_statute = False           # 인용 법령 구간 안인가
    for pg in pages:
        if pg["page"] not in toc_pages:  # ★목차 안의 부 제목은 경계가 아니다
            for line in pg["text"].splitlines():
                #: ★인용 법령 경계가 먼저다 — `[법규5] 상법` 은 부 제목이 아니라
                #:   **약관이 끝나고 법령이 시작된다**는 표시다.
                ms = _STATUTE_HEAD.match(line) or _STATUTE_LAW.match(line)
                if ms:
                    current = re.sub(r"\s+", " ", ms.group(1)).strip()
                    in_statute = True
                    break
                if _STATUTE_ZONE.match(line):
                    current = "관계법령"
                    in_statute = True
                    break
                #: 옛 규칙 — `보통약관` 처럼 그 단어만 있는 줄.
                m = _SECTION_LINE.match(line)
                if m:
                    current = re.sub(r"\s+", "", m.group(1))
                    #: 약관 부(部)로 되돌아오면 법령 구간이 끝난 것이다.
                    in_statute = current in ("부록", "별표")  and in_statute
                    break
                #: 넓힌 규칙 — `○○ 특별약관` 처럼 제목형 줄.
                m2 = _SECTION_TITLE.match(line)
                if m2:
                    title = re.sub(r"\s+", " ", m2.group(1)).strip()
                    if not _looks_like_section(line, title):
                        continue
                    current = title
                    in_statute = False
                    break
        section_of_page[pg["page"]] = current
        statute_of_page[pg["page"]] = in_statute

    # ── 6) 조항 경계 찾기 (목차 페이지 제외) ──
    #: (페이지, 페이지내 오프셋, 조번호, 가지번호, 제목)
    heads: list[tuple[int, int, str, str, str]] = []
    n_ref_head = 0              # 상호참조로 판정해 버린 가짜 머리 (조용히 버리지 않는다)
    for pg in pages:
        if pg["page"] in toc_pages:
            continue
        t = pg["text"]
        for m in _ARTICLE.finditer(t):
            #: ★★**제목 없는 `제N조` 뒤에 참조 꼬리가 오면 머리가 아니다.**
            #:
            #:   `_ARTICLE` 은 줄머리만 보지만 제목 괄호가 **선택**이라,
            #:   긴 문장이 줄바꿈되며 줄머리로 온 상호참조가 그대로 머리가 된다.
            #:   실측(표본 300문서): 머리 후보 10,924개 중 제목 없음 2,607(23.9%),
            #:   그중 참조 꼬리가 **1,056개(전체의 9.67%)** —
            #:     `제42조제1항제2호에 의한 약국, 동법 제42조제1항제3호에 의한`
            #:   이것들이 조항을 쪼개 100자 미만 조각을 만들고,
            #:   부록 종료 판단에도 걸려 별표를 **조기 절단**한다(코덱스 지적).
            #:
            #:   ★제목이 **있으면** 거르지 않는다. `제3조(용어의 정의)` 는 진짜 머리다.
            if not (m.group(3) or "").strip() and _REF_TAIL.match(t[m.end():m.end() + 12]):
                n_ref_head += 1
                continue
            heads.append(
                (pg["page"], m.start(), m.group(1), m.group(2) or "", (m.group(3) or "").strip())
            )

    #: ★번호 체계를 **점수로 고른다.** "제N조가 하나라도 있으면 그것" 은 틀렸다.
    #:
    #:   실측(DB손보 96쪽): 줄머리 `제N조` 8개는 **전부 법령 인용**이었다.
    #:     "「의료법」 제3조(의료기관)에서 규정한 …"
    #:   긴 문장이 줄바꿈되며 `제3조` 가 줄머리로 왔다. 진짜 조항은
    #:   `1. (보장종목)` 꼴로 **93개**였는데, `제N조` 가 0이 아니라서
    #:   `article` 로 판정했고 **조항 3개 / 최대 90,385자**가 나왔다.
    #:   그런 문서가 v3 산출물의 **35%(268/761)** 였다.
    #:
    #:   그래서 두 후보를 다 모아 **제목 있는 머리 수**로 비교한다.
    #:   제목 없는 `제N조` 는 인용일 가능성이 높다(코덱스 지적).
    numbered_cand: list[tuple[int, int, str, str, str]] = []
    for pg in pages:
        if pg["page"] in toc_pages:
            continue
        t2 = pg["text"]
        for m in _NUMBERED.finditer(t2):
            #: ★`제N조` 쪽과 **같은 함정**이다 — `31.(…부활(효력회복))에 따라` 처럼
            #:   줄머리로 온 상호참조가 머리가 된다. 실측 표본 300문서 23건(0.10%).
            #:   작지만 고치는 값이 같으므로 함께 막는다.
            if _REF_TAIL.match(t2[m.end():m.end() + 12]):
                n_ref_head += 1
                continue
            numbered_cand.append(
                (pg["page"], m.start(), m.group(1), m.group(2) or "", m.group(3).strip())
            )

    #: 제목이 붙은 것만 센다 — 조항 머리는 제목을 달고 나온다.
    titled_articles = sum(1 for h in heads if h[4])
    numbering = "article"
    ambiguous = False
    if len(numbered_cand) >= NUMBERED_MIN_HEADS and len(numbered_cand) > titled_articles:
        heads = numbered_cand
        numbering = "numbered"
        #: 점수 차가 작으면 확신할 수 없다 — 자동 판정에서 빼도록 표시한다.
        ambiguous = len(numbered_cand) < titled_articles * 2
    elif titled_articles and numbered_cand:
        ambiguous = titled_articles < len(numbered_cand) * 2

    if not heads:
        #: ★가짜 조항 1개를 만들지 않는다.
        #:
        #:   문서 전체를 "조항 1개"로 가장하면 파싱이 성공한 것처럼 보이고,
        #:   근거 조항·페이지 추적이 망가진다. 보장 판정에 그대로 쓰이면
        #:   "약관 어디에 근거했나"를 댈 수 없게 된다.
        #:
        #:   대신 **성격이 다른 산출물**임을 명시한 fallback 을 만든다.
        #:   RAG 검색에는 쓰되(문서를 버릴 이유는 없다), 자동 판정에는
        #:   `parse_status` 를 보고 걸러야 한다.
        return _fallback(page_doc, pages, toc_pages, toc_reasons, section_of_page)

    text_of = {pg["page"]: pg["text"] for pg in pages}
    tables_of = {pg["page"]: pg.get("tables", []) for pg in pages}
    #: ★★**좌표 복원 표를 조항·부록에 실어 나른다.**
    #:
    #:   여기까지 안 오면 판정은 표를 **평평해진 텍스트**로만 읽는다.
    #:   실측(정답셋 66레코드): 텍스트 순서로 읽은 질병명↔KCD 짝은 **0.455**,
    #:   좌표 복원 레코드는 **1.000**. 절반 이상이 틀린 코드와 짝지어진다.
    #:   KCD 오짝은 이 서비스에서 가장 위험한 실패다.
    #: ★★**검증된 복원만 조항에 싣는다.**
    #:
    #:   `table_coords` 는 두 경로로 표를 만든다 —
    #:     `선`         벡터 선으로 격자를 세운다. 정답셋 66레코드에서 **1.000**
    #:     `2열짝짓기`  선이 없을 때 x 투영으로 두 열을 짝짓는다. **미검증**
    #:
    #:   실측(s6 전량): 표 405,845개 중 `2열짝짓기` 가 **404,929개(99.8%)** 다.
    #:   표본을 열어 보니 대부분 **표가 아니라 본문 두 단 조판**이었다 —
    #:     `'6.' | '응급환자의 이송 등…'` 이 6·7·8행에 같은 값으로 반복
    #:     `'보험금 ④ 회사는' | '늦어지는 경우 보험수익자…'`
    #:   점수 문턱(0.6)을 통과하고도 그렇다. 이대로 판정에 실으면
    #:   **없는 표를 근거로 든다.** 정답셋 1.000 은 `선` 경로 0.2% 를 잰 값이지
    #:   전체가 아니다.
    #:
    #:   ★페이지 JSON 에는 **그대로 남긴다.** 지우면 나중에 검증할 재료가 없다.
    #:     여기서만 거르고, 거른 수를 `stats` 에 남긴다(CLAUDE.md §3).
    #: ★게이트 규칙
    #:   `선`        벡터 괘선으로 격자를 세운 것. 정답셋 1.000. **무조건 싣는다**
    #:   `2열짝짓기` 판별 신호(`is_table`)를 통과한 것만. 나머지는 보류
    #:
    #:   판별 신호와 임계값은 `scripts/extract/table_signals.py` 에 있고
    #:   **알려진 참/거짓으로 맞춘 값**이다(`scripts/eval/table_signal_fit.py`).
    #:   지어낸 문턱이 아니다.
    from scripts.extract.table_signals import attachment_verdict

    coord_of: dict[int, list] = {}
    n_table_withheld = 0
    for pg in pages:
        keep, drop = [], 0
        for t in pg.get("tables_coords", []) or []:
            #: ★★**2열은 다시 닫았다.** 신호 게이트를 열고 실측했더니
            #:   통과한 4,607개 중 **3,685개(80.0%)** 가 본문 지문을 갖고 있었다 —
            #:   항 표지(①②③) · 문장 2개 이상 · 조 머리(`제N조`, `17. (…)`).
            #:   표본:
            #:     `Ⅶ. 프로미라이프 지식백과 | (안내문)`
            #:     `17. (청약의 철회) ①계약자는 … 15일 다만, | 그 청약을 철회할 수 있습니다.`
            #:
            #:   ★임계값을 **참 4 · 거짓 3** 으로 맞춘 것이 화근이다. 그 표본에서
            #:     T1 통로가 깨끗이 갈려 보였지만(참 0.039 / 거짓 0.064) 전량에서는
            #:     경계가 0.041~0.051 구간에 본문이 잔뜩 있다.
            #:     **작은 표본으로 정한 문턱을 전량에 적용하지 않는다.**
            #:
            #:   신호는 계속 페이지 JSON 에 싣는다 — 정답셋을 층화 확장한 뒤(계획서 L1)
            #:   다시 맞춰서 열 것이다. 지금 여는 것은 **없는 표를 근거로 드는 일**이다.
            #: ★★`선` 경로도 **무조건 싣지 않는다.**
            #:   실측: 8·9열 표 19개를 전부 열어 보니 **진짜 표가 하나도 없었다** —
            #:   QR 인포그래픽 14 · 본문 과분할 4 · 혼합 1.
            #:   원인은 인포그래픽 상자 테두리도 세로선이라 열 경계로 잡히는데
            #:   그건 **자기 상자 높이만** 뻗는다는 것이다(`T8_rule_span`).
            #:
            #:   ★`is not False` 다. `== True` 로 쓰면 신호가 붙기 전에 만든
            #:     옛 산출물(`is_table` 이 `null`)의 표가 **통째로 사라진다.**
            #:     신호가 실린 뒤 재생성하면 그때부터 실제로 걸린다.
            #: ★★`선` 경로에도 **본문 모양** 검사를 건다(T9).
            #:   사람 라벨 59건 실측: `false` 55건 중 우리가 실은 22건 가운데
            #:   **18건이 선 경로**였다. T7·T8 은 다열 전용이라 2~6열을 아무도 안 봤다.
            #:
            #:   ★`records` 에서 **여기서 계산한다.** 페이지 JSON 에 신호가
            #:     실려 있든 아니든 걸리게 하려는 것이다 — 재생성 전에도 막힌다.
            #:   ★한쪽 문턱이라 참 손실은 0 이지만 **거짓의 45%만** 잡는다.
            #:     짧은 셀(코드 목록을 가로로 읽은 것)은 못 잡는다.
            attachable, _ = attachment_verdict(t)
            if attachable:
                keep.append(t)
            else:
                drop += 1
        coord_of[pg["page"]] = keep
        n_table_withheld += drop
    #: ★부록 시작 이벤트. 조항 본문을 여기서 끊고, 별도 배열로 뺀다.
    annex_ev = sorted(_annex_events(pages, toc_pages))
    #: ★부록 종료용. 페이지 단위 `statute_of_page` 로는 같은 페이지 안의 경계를 못 본다.
    statute_ev = sorted(_statute_events(pages, toc_pages))
    #: ★조 본문·부록을 **부(部) 경계**에서도 끊는다. 안 끊으면 특약 마지막 조가
    #:   다음 특약 제목을 삼킨다(실측 6,575조항 3.39%).
    section_ev = sorted(_section_events(pages, toc_pages))

    clauses: list[dict] = []
    doc_unresolved = 0          # 번호를 못 읽은 항 (문서 전체)
    #: ★수록(occurrence) 식별자. **내용 해시로는 대신할 수 없다.**
    #:
    #:   `content_hash` 는 **정체성**이다 — 정의상 문서 간에 공유되고(전량 중복 65.4%),
    #:   같은 문서 안에서도 3,578건 겹친다(544문서). "이 조항이 **어느 문서 몇 쪽**에
    #:   실렸나"를 못 답한다. `citation` 은 더 나쁘다 — 문서 내 충돌 46,540건(1,210문서).
    #:
    #:   ★uuid 를 쓰지 않는다. 재실행할 때마다 값이 바뀌면 **같은 입력이 다른 산출물**이
    #:     되고, s4↔s5 대조나 색인 계보 추적이 불가능해진다.
    #:     `(문서 sha12, ordinal)` 이면 결정적이면서 유일하다. DB PK 는 적재 시 만든다.
    ordinal = 0
    doc_ambiguous_cite = 0      # 한 조 안에서 항 번호가 되풀이되는 조항 수
    for i, (page, off, no, sub, title) in enumerate(heads):
        # 본문 = 이 머리부터 다음 머리 전까지 (페이지를 넘어갈 수 있다)
        if i + 1 < len(heads):
            end_page, end_off = heads[i + 1][0], heads[i + 1][1]
        else:
            end_page, end_off = pages[-1]["page"], len(text_of[pages[-1]["page"]])

        #: ★★부록이 먼저 오면 **거기서 끊는다.**
        #:
        #:   안 끊으면 마지막 조가 문서 끝까지 삼킨다. 실측 `16b227ff95b8`:
        #:   `제27조(준용규정)` 이 p12~16·4,162자로 `[붙임] 용어의 정의` 와
        #:   `[별표] 임신질환 분류표(O00~O08 … O80~O84)` 를 통째로 먹었다.
        #:   그러면 판정이 **"제27조(준용규정)에 따르면 분만은 O80~O84"** 라고 답한다 —
        #:   내용은 표에 있지만 **출처가 틀렸다.** KCD 오인용이다.
        #:
        #:   ★`_split_paragraphs` **보다 먼저** 끊어야 한다. 나중에 `text` 만
        #:     잘라내면 `paragraphs` 안에는 부록이 그대로 남는다(코덱스 지적).
        for ap, ao, _lab in annex_ev:
            if (ap, ao) <= (page, off):
                continue          # 이 조항 앞
            if (ap, ao) < (end_page, end_off):
                end_page, end_off = ap, ao
                break

        #: ★**부(部)가 바뀌면 거기서도 끊는다.** `준용규정` 이 다음 특약 이름을
        #:   삼키던 문제다. ★조 머리 **자신**이 section 줄일 수 있으므로
        #:   `<=` 가 아니라 **엄격히 뒤**(`>`)만 본다.
        for sp, so in section_ev:
            if (sp, so) <= (page, off):
                continue
            if (sp, so) < (end_page, end_off):
                end_page, end_off = sp, so
            break

        parts: list[str] = []
        for p in range(page, end_page + 1):
            #: ★목차 페이지는 본문에 넣지 않는다.
            #:   조항 **머리**는 목차에서 안 찾으면서 **본문**은 목차째로 담고 있었다.
            #:   그래서 조항 하나가 42,632자가 됐고 그 안에 조 머리 194개·점선줄 253개가
            #:   들어 있었다. 조 사이에 목차가 끼면 그 조가 목차를 통째로 삼킨다.
            if p in toc_pages:
                continue
            t = text_of.get(p, "")
            a = off if p == page else 0
            b = end_off if p == end_page else len(t)
            parts.append(t[a:b])
        body = "\n".join(parts)

        if numbering == "numbered":
            #: 특별약관 번호 형식. `제N조` 가 아니므로 그렇게 부르지 않는다.
            #: ★원문 표기를 그대로 쓴다. `4-1.` 을 `4.1` 로 적으면 **원문에 없는 번호**가 된다
            #:   (v5 첫 구현이 `f"{no}." + sub` 로 조립해 `4.1(보상하지 않는 사항)` 이 나왔다).
            label = (f"{no}-{sub}." if sub else f"{no}.")
        else:
            label = f"제{no}조" + (f"의{sub}" if sub else "")
        section_name = section_of_page.get(page, "미상")

        # ── 9) 계층형 청킹: 부 → 조 → **항** ──
        #: ★조 수준 인용 문자열. 판정 근거에 그대로 실린다.
        #: ★제목의 겹침 글리프를 정리한다 — `준용규정 준용규정 준용규정 준용규정`.
        #:   `content_hash` 입력이라 **해시가 바뀐다.** s6 이므로 괜찮다.
        title = _collapse_dup(title) if title else title
        clause_cite = f"{label}({title})" if title else label
        is_statute = statute_of_page.get(page, False)
        paragraphs, n_unresolved = _split_paragraphs(body, clause_cite)
        doc_unresolved += n_unresolved
        #: ★한 조 안에서 항 번호가 **되풀이**될 수 있다.
        #:
        #:   실측(NH `42ce81976809` 제4조): 조 하나가 보장종목 여럿을 덮고
        #:   `(1) 상해급여` `(2) 질병급여` 마다 `①②③` 이 다시 시작한다.
        #:   그러면 `제4조 제1항` 이 **한 조 안에서 두 곳을 가리킨다.**
        #:
        #:   나누지도 합치지도 않는다 — 어느 보장종목인지는 목(目) 층이고
        #:   우리는 거기까지 안 내려간다. 대신 **인용이 유일하지 않다는 사실을 표시**한다.
        #:   판정이 이 조항을 근거로 들 때 항 번호만으로 특정하면 안 된다.
        _nos = [p["paragraph_no"] for p in paragraphs if p["paragraph_no"]]
        cite_ambiguous = len(_nos) != len(set(_nos))
        if cite_ambiguous:
            doc_ambiguous_cite += 1
        ordinal += 1
        clauses.append(
            {
                #: 이 문서 안에서의 수록 순번. 재실행해도 같다.
                "ordinal": ordinal - 1,
                "clause_no": label,
                "title": title,
                # ★특별약관이 여러 개면 조 번호가 1부터 다시 시작한다.
                # 부 이름을 함께 들고 다녀야 유일해진다.
                "section": section_name,
                "qualified_no": f"{section_name}/{label}",
                #: ★True 면 **약관 조항이 아니라 인용된 법령 원문**이다.
                #:   판정 근거로 "약관 제651조" 라고 대면 안 된다 — 그건 상법이다.
                "statute": is_statute,
                # ── 7) 메타데이터: locator ──
                "locator": {"page_from": page, "page_to": end_page, "char_offset": off},
                #: ★조 단위 원문·해시는 **바꾸지 않는다.** 항 분해는 얹기만 한다
                #:   (기존 색인·해시가 그대로 유효해야 한다).
                "text": body,
                "char_length": len(body),
                "citation": clause_cite,
                # ── 9) 항 단위 ──
                "paragraphs": paragraphs,
                #: ★번호가 붙은 항만 센다. 조 서두(번호 없음)와
                #:   번호를 못 읽은 항은 여기 안 들어간다.
                "paragraph_count": sum(1 for p in paragraphs if p["paragraph_no"]),
                "unresolved_paragraphs": n_unresolved,
                #: ★True 면 `제N항` 만으로 이 조항 안의 위치를 특정할 수 없다.
                "paragraph_no_ambiguous": cite_ambiguous,
                # 같은 페이지에 있던 표. ★어느 조항 것인지 추정하지 않는다.
                "tables_on_pages": {
                    str(p): len(tables_of.get(p, [])) for p in range(page, end_page + 1)
                    if tables_of.get(p)
                },
                #: ★좌표 복원 표. `tables_on_pages`(개수)와 달리 **내용**이 온다.
                #:   페이지 단위라 조 경계보다 넓을 수 있다 — 그래서 `page` 를 붙인다.
                #:   조 안의 어디인지까지는 **모른다. 추정하지 않는다.**
                "tables": [
                    {"page": p, **t}
                    for p in range(page, end_page + 1)
                    for t in coord_of.get(p, [])
                ],
                # ── 8) 중복 처리 준비 ──
                "content_hash": _clause_hash(section_name, title, body),
            }
        )

    #: ── 부록을 **별도 배열**로 뺀다 ──
    #:
    #: ★`clauses` 사이에 끼워 넣으면 안 된다. 그러면 이후 모든 `ordinal` 이 밀려
    #:   `(sha, ordinal)` 결정적 식별자가 깨진다(코덱스 지적). 배열도 ordinal 도 따로 둔다.
    annexes: list[dict] = []
    for ai, (ap, ao, lab) in enumerate(annex_ev):
        #: 끝 = 다음 부록 시작, 없으면 문서 끝
        if ai + 1 < len(annex_ev):
            e_pg, e_off = annex_ev[ai + 1][0], annex_ev[ai + 1][1]
        else:
            e_pg, e_off = pages[-1]["page"], len(text_of[pages[-1]["page"]])

        #: ★★**다음 조 머리에서도 끊는다.** 이걸 빼면 문서 중간의 별표가
        #:   그 뒤 조항을 전부 삼킨다. 조항 본문은 따로 잘려 있으므로
        #:   같은 글이 조항과 부록에 **두 벌** 남는다.
        #:
        #:   실측(끊기 전, s6 1차): 부록 9,342개에 108,940,010자 —
        #:   본문 총량의 **0.72배**다. 한 문서(`3dea54cde8ad`)에 `【별표1】` 이
        #:   204,869자와 198,157자로 겹쳐 있었다(뒤 것이 앞 것에 포함).
        #:   이대로 색인하면 같은 문장이 조항으로도 부록으로도 검색되고,
        #:   부록 쪽 인용은 **출처가 틀린다.**
        for hp, ho, *_ in heads:
            if (hp, ho) <= (ap, ao):
                continue          # 이 부록 앞의 조 머리
            if (hp, ho) < (e_pg, e_off):
                e_pg, e_off = hp, ho
            break

        #: ★**인용 법령 시작에서도 끊는다.** 문서 끝의 부록은 뒤에 조 머리가 없어서
        #:   위 규칙에 안 걸리고 「관계법령」까지 삼킨다. 실측(끊기 전):
        #:   `e87074365e0f` 의 `장해분류표` 가 p79~136(58쪽)·114,724자였고
        #:   끝이 호스피스 **법률 조문**이었다. 그대로 두면 판정이
        #:   "장해분류표에 따르면 …" 하며 **법령을 약관 별표로 인용한다.**
        for sp, so in statute_ev:
            if (sp, so) <= (ap, ao):
                continue
            if (sp, so) < (e_pg, e_off):
                e_pg, e_off = sp, so
            break

        #: ★부(部) 경계에서도 끊는다(코덱스 권고). 조 머리 없는 후면물
        #:   (`약관 요약서` 등)이 부록에 섞이는 것을 막는다.
        for sp, so in section_ev:
            if (sp, so) <= (ap, ao):
                continue
            if (sp, so) < (e_pg, e_off):
                e_pg, e_off = sp, so
            break
        parts = []
        for pnum in range(ap, e_pg + 1):
            if pnum in toc_pages:
                continue
            t = text_of.get(pnum, "")
            parts.append(t[(ao if pnum == ap else 0):(e_off if pnum == e_pg else len(t))])
        body = chr(10).join(parts)
        if not body.strip():
            continue
        sec = section_of_page.get(ap, "미상")
        annexes.append({
            "ordinal": len(annexes),
            "label": lab,
            "section": sec,
            "locator": {"page_from": ap, "page_to": e_pg, "char_offset": ao},
            "text": body,
            "char_length": len(body),
            #: ★어느 조항 것인지 **추정하지 않는다.** 판정은 부록 자체를 인용한다.
            "owner_clause_ordinal": None,
            "tables_on_pages": {str(pnum): len(tables_of.get(pnum, []))
                                for pnum in range(ap, e_pg + 1) if tables_of.get(pnum)},
            #: ★★부록에 실리는 이게 **KCD 대조의 실제 근거**다.
            #:   질병분류표·장해분류표가 전부 여기 있다.
            "tables": [
                {"page": pnum, **t}
                for pnum in range(ap, e_pg + 1)
                for t in coord_of.get(pnum, [])
            ],
            "content_hash": _clause_hash(sec, lab, body),
            #: ★라벨은 정리하되 본문(`text`)은 **원문 그대로** 둔다.
        })

    dup: dict[str, int] = {}
    for c in clauses:
        dup[c["content_hash"]] = dup.get(c["content_hash"], 0) + 1

    #: ★사후 검증 — 결과가 말이 되는지 스스로 확인한다.
    #:
    #:   "조항을 만들었다"가 "제대로 만들었다"는 뜻이 아니다.
    #:   96쪽에서 조항 3개, 그중 하나가 90,385자인 결과를 **성공으로 내보냈다.**
    #:   그런 산출물이 판정 근거로 쓰이면 "제N조에 따르면" 이 통째로 무의미해진다.
    warnings: list[str] = []
    if clauses:
        longest = max(c["char_length"] for c in clauses)
        if longest > CLAUSE_MAX_CHARS:
            warnings.append(f"조항 하나가 {longest:,}자 — 경계를 못 찾은 것으로 보인다")
    n_pages = page_doc["stats"]["pages"]
    body_pages = n_pages - len(toc_pages)
    if body_pages >= 30 and len(clauses) < body_pages / 10:
        warnings.append(f"본문 {body_pages}쪽인데 조항 {len(clauses)}개 — 너무 적다")
    if ambiguous:
        warnings.append("번호 체계를 확신할 수 없다(두 형식의 머리 수가 비슷하다)")

    #: ★사후 검증 (v5 신설) — **놓친 목차가 만든 가짜 조항**을 잡는다.
    #:
    #:   위 두 검사는 "조항이 너무 길다"와 "너무 적다"만 본다. 목차 페이지를 놓치면
    #:   반대 방향으로 망가진다 — **짧은 가짜가 대량으로 늘어난다.**
    #:
    #:   실측(현대해상 `dc72bc8035b4` 104쪽): 목차 p33·p34 를 놓쳐 가짜 조항 51개
    #:   (15~92자)가 생겼다. 전체 157개의 **32.5%**. 그런데
    #:     · 최대 길이 3,515자 → 길이 검사 통과
    #:     · 가짜가 조항 수를 부풀려 → "너무 적다" 검사도 통과
    #:   결국 `parse_status="ok"` 로 나갔다. **두 검사가 서로의 사각을 못 메운다**(코덱스).
    #:
    #:   목차가 새면 **한 페이지에서 조 머리가 무더기로** 나오고 그 조항들이 다 짧다.
    #:   지우지는 않는다 — 세어서 `suspect` 로 보낸다.
    TINY_CLAUSE_CHARS = 100
    HEADS_PER_PAGE_ALARM = 20
    #: ★★별표에 실린 **법령 조문**은 여기 해당하지 않는다.
    #:
    #:   실측(s5 전량): 이 경고로 `suspect` 가 된 135문서 중 **85문서(63%)** 가
    #:   별표에 조세특례제한법 시행령·형법 조문을 그대로 실은 것이었다.
    #:
    #:       제298조(강제추행)
    #:       폭행 또는 협박으로 사람에 대하여 추행을 한 자는 10년 이하의 징역…
    #:       제299조(준강간, 준강제추행)
    #:       사람의 심신상실 또는 항거불능의 상태를 이용하여…
    #:
    #:   짧은 조항이 한 쪽에 촘촘한 것은 **법령 조문의 정상 모습**이다.
    #:   목차가 아니다. 이걸 `suspect` 로 내리면 **정상 문서 85건이
    #:   판정 대상에서 빠진다.**
    #:
    #:   ★s5 는 이미 조항마다 `statute` 를 붙여 법령 조문을 표시하고 있었는데
    #:     이 검사가 그걸 **보지 않았다**. 표시해 놓고 안 쓴 것이다.
    if clauses:
        per_page = Counter(c["locator"]["page_from"] for c in clauses)
        page, cnt = per_page.most_common(1)[0]
        if cnt >= HEADS_PER_PAGE_ALARM:
            same = [c for c in clauses if c["locator"]["page_from"] == page]
            n_statute = sum(1 for c in same if c.get("statute"))
            lens = sorted(c["char_length"] for c in same)
            med = lens[len(lens) // 2]
            #: 그 쪽의 절반 이상이 법령 조문이면 목차가 아니라 별표다.
            is_statute_page = n_statute >= len(same) / 2
            if med < TINY_CLAUSE_CHARS and not is_statute_page:
                warnings.append(
                    f"p{page} 한 쪽에서 조항 {cnt}개(중앙 {med}자) — "
                    f"목차를 본문으로 읽은 것으로 보인다"
                )

    #: ★★사후 검증 (v5 · 2026-08-02 추가) — **구조 모순**.
    #:
    #:   위 검사들은 "길이"와 "개수"만 본다. 그걸 다 통과하면서 **경계가 서로 모순**인
    #:   산출물이 나온다. 실측 반례 `16b227ff95b8`(삼성화재 임신질환 특약, 16쪽):
    #:
    #:       조 번호 순서   제1 제2 제3 제4 제5 **제4** 제6 …   ← 제5조 본문이 제4조로 오귀속
    #:       제27조         p12~16 · 4,162자 · 붙임·질병분류표를 삼킴
    #:       parse_status   **ok** · 경고 0개
    #:
    #:   ★그 문서는 `_3rd_project_4` 도 `coverage 100.0 / OK` 로 통과시킨다.
    #:     **두 파이프라인 공통 결함**이고, 지표가 못 잡는다는 증거다.
    #:
    #:   질병분류표가 `제27조(준용규정) 제2항 제7호` 안에 들어가면
    #:   **KCD 코드가 잘못된 조항에 인용된다.** 이 서비스에서 가장 위험한 실패다.
    #:
    #: ★확정 신호만 쓴다. `번호 비연속`·`항호 이상`은 원문 결번·표 숫자에도 걸려
    #:   저정밀이므로 **검수 우선순위 신호**로만 두고 여기 넣지 않는다(코덱스 지적).
    #: ★판정식은 `scripts/eval/struct_audit.py` 와 **같아야 한다.**
    #:   두 곳이 어긋나면 감사와 산출물이 다른 말을 한다.
    from scripts.eval.struct_audit import structure_faults

    #: ★alternation 에서 `group(1)` 만 읽으면 **`N.` 형이 통째로 `None`** 이 된다.
    #:   `제N조` 는 group(1), `N-M.` 은 group(2) 다. 실측: 그래서 numbered 형식의
    #:   A-B-A 재진입 **7건을 놓쳤다**(4,714 → 4,721). 코덱스가 잡았다.
    audit_blocks = [
        {"no": _clause_num(c["clause_no"]),
         "kind": numbering, "title": c["title"], "text": c["text"]}
        for c in clauses
    ]
    faults = structure_faults(audit_blocks)

    #: ★★구조 모순을 `parse_warnings` 에 **넣지 않는다.** 축이 다르기 때문이다.
    #:
    #:   처음엔 넣었다가 `parse_status` 의 `ok` 가 **1,108 → 161 로 떨어졌다.**
    #:   `precheck` 는 `parse_status == "ok"` 만 쓰므로 판정 가능 문서가 88% 사라진다.
    #:
    #:   두 값은 다른 질문에 답한다:
    #:     `parse_status`      파싱이 됐나 (길이·개수가 말이 되나)
    #:     `citation_eligible` **인용해도 되나** (경계가 서로 모순이 아닌가)
    #:   섞으면 둘 다 뜻을 잃는다. 판정은 **두 값을 다 봐야 한다.**
    #:
    #:   ★그리고 이 신호들은 **precision 이 검증되지 않았다.** 정답셋이 없다.
    #:     실제로 부록 흡수 신호는 만든 날 과탐이 드러나 11,478 → 4,789 로 줄었다
    #:     (문장 안 인용 `제9조([별표1] 비급여대상)에 의한…` 을 잡고 있었다).
    #:     검증 안 된 신호로 `parse_status` 를 끄면 **멀쩡한 문서를 버린다.**

    parse_status = "ok" if not warnings else "suspect"

    #: ★판정 근거로 인용해도 되는가. **`parse_status` 와 다른 축이다.**
    #:   길이·개수는 멀쩡한데 경계가 모순인 문서가 있다(위 반례).
    #:   `precheck` 는 `parse_status == "ok"` 만 보고 있다 — 이 값도 봐야 한다.
    #: ★★게이트를 **조항 단위**로 내린다.
    #:
    #:   처음엔 문서 단위로 껐다가 판정 가능 조항이 **897개(0.42%)** 로 줄었다.
    #:   그 161문서는 조항 중앙 3개·2쪽짜리 제도성 특약이고
    #:   **「보상하지 않는 사항」 조항이 0건** — 사실상 전면 기권이다.
    #:   결함 4개 때문에 그 문서의 조항 155개를 통째로 버린 셈이었다.
    #:   조항 단위로 내리면 **168,523개(93.95%)** 가 살고 면책 조항도 2,099개가 산다.
    gated = set(faults.get("gated_ordinals") or [])
    for c in clauses:
        #: `False` 면 이 조항을 **판정 근거로 인용하면 안 된다.**
        c["citation_eligible"] = c["ordinal"] not in gated
        #: ★옆 조항이 꺼졌다는 **문서 완전성 신호**(앞뒤 1개).
        #:   경고일 뿐 기권 사유가 아니다 — 다만 "면책 조항이 없다" 같은
        #:   **부재 기반 결론**은 이 값이 True 면 내리면 안 된다(코덱스 합의).
        c["has_gated_neighbors"] = bool(gated & {c["ordinal"] - 1, c["ordinal"] + 1})

    #: 문서 수준은 **요약**으로만 남긴다. 판정은 조항 값을 봐야 한다.
    citation_eligible = not gated

    return {
        "schema_version": SCHEMA_VERSION,
        #: ★이 문서가 어떻게 파싱됐는지. 판정에 쓸 수 있는지 여기서 갈린다.
        #:   `suspect` 는 "만들긴 했는데 믿지 말라"는 뜻이다.
        "parse_status": parse_status,
        "parse_warnings": warnings,
        #: ★판정 근거로 인용해도 되는가. `parse_status` 와 **다른 축**이다.
        #:   `precheck` 는 지금 `parse_status == "ok"` 만 본다 — 이 값도 봐야 한다.
        "citation_eligible": citation_eligible,
        #: ★조항 단위 요약. 판정은 `clauses[].citation_eligible` 을 봐야 한다.
        "gated_clause_count": len(gated),
        "structure_faults": {k: v for k, v in faults.items()
                             if k not in ("n_blocks", "gated_ordinals")},
        #: 어떤 번호 체계로 쪼갰나. `제N조` 인지 특별약관의 `N.` 인지.
        "numbering": numbering,
        "toc_pages": sorted(toc_pages),
        #: ★어느 신호로 뺐는지. 근거 없이 페이지를 버리지 않는다.
        "toc_reasons": {str(p): toc_reasons[p] for p in sorted(toc_pages)},
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": page_doc["source"],
        "identification": page_doc.get("identification", "unidentified"),
        "extractor": page_doc.get("extractor", ""),
        "stats": {
            "pages": page_doc["stats"]["pages"],
            "clauses": len(clauses),
            "sections": len(set(section_of_page.values())),
            "toc_pages_excluded": len(toc_pages),
            "unique_clause_hashes": len(dup),
            "duplicate_clauses": sum(v - 1 for v in dup.values() if v > 1),
            # ── 9단계 ──
            #: 번호가 붙은 항의 총수. `제N조 제M항` 인용의 분모다.
            "paragraphs": sum(c["paragraph_count"] for c in clauses),
            #: 항 아래 호(號)의 총수.
            "items": sum(len(p["items"]) for c in clauses for p in c["paragraphs"]),
            #: ★번호를 못 읽은 항. 지우지 않고 센다(미매핑 보조 PUA).
            #:   이 수가 크면 그 문서의 "제N항" 인용을 믿으면 안 된다.
            "unresolved_paragraphs": doc_unresolved,
            #: ★`제N항` 인용이 유일하지 않은 조항 수. 판정이 근거를 댈 때 걸린다.
            "ambiguous_paragraph_citations": doc_ambiguous_cite,
            #: 인용 법령 원문으로 잡힌 조항 수(약관 조항이 아니다).
            "statute_clauses": sum(1 for c in clauses if c["statute"]),
            "annexes": len(annexes),
            #: ★버린 것을 **센다**(CLAUDE.md §3). 이 값이 갑자기 크면 그 문서는
            #:   진짜 머리를 참조로 오인했을 수 있다 — 조용히 줄어들면 못 찾는다.
            "ref_heads_dropped": n_ref_head,
            #: ★미검증 복원이라 조항에 안 실은 표 수. 페이지 JSON 에는 남아 있다.
            "tables_withheld_unverified": n_table_withheld,
            "annex_chars": sum(a["char_length"] for a in annexes),
        },
        "sections": sorted(set(section_of_page.values())),
        "clauses": clauses,
        #: ★별표·붙임·분류표. **약관 조항이 아니다.** 별도 ordinal 을 쓴다.
        "annexes": annexes,
    }


def _fallback(page_doc: dict, pages: list[dict], toc_pages: set[int],
              toc_reasons: dict[int, str], section_of_page: dict[int, str]) -> dict:
    """조항 머리를 못 찾은 문서 — **페이지 단위 청크**로 남긴다.

    ★왜 실패로 버리지 않나

        문서를 버리면 검색에서 아예 사라진다. 약관 원문은 있는데
        "그런 문서 없다"고 답하게 되는 것이 더 나쁘다.

    ★왜 조항 1개로 만들지 않나

        파싱이 성공한 것처럼 보이기 때문이다. 그러면 통계에서 정상 문서와
        섞이고, 보장 판정이 "근거 조항"을 대야 할 때 문서 전체를 들이대게 된다.

        그래서 `parse_status="no_clause_heads"` / `chunk_type="page_fallback"`
        을 **명시**한다. 자동 판정은 이 값을 보고 걸러야 한다.
    """
    chunks: list[dict] = []
    for pg in pages:
        if pg["page"] in toc_pages:
            continue
        body = pg["text"]
        if not body.strip():
            continue
        section_name = section_of_page.get(pg["page"], "미상")
        chunks.append(
            {
                "clause_no": f"p{pg['page']}",
                "title": "",
                "section": section_name,
                "qualified_no": f"{section_name}/p{pg['page']}",
                #: ★조항이 아니다. 이름을 다르게 붙여 섞이지 않게 한다.
                "chunk_type": "page_fallback",
                "locator": {"page_from": pg["page"], "page_to": pg["page"], "char_offset": 0},
                "text": body,
                "char_length": len(body),
                #: ★조 머리를 못 찾은 문서다. 조가 없으니 **항도 세지 않는다.**
                #:   빈 배열을 두는 건 "찾아봤는데 없다"는 뜻이 아니라
                #:   "이 산출물엔 항이라는 개념이 없다"는 뜻이다.
                "paragraphs": [],
                "paragraph_count": 0,
                "unresolved_paragraphs": 0,
                "tables_on_pages": (
                    {str(pg["page"]): len(pg.get("tables", []))} if pg.get("tables") else {}
                ),
                "content_hash": _clause_hash(section_name, "", body),
            }
        )

    return {
        "schema_version": SCHEMA_VERSION,
        #: ★"성공"이 아니다. 판정에 쓰려면 이 값을 확인해야 한다.
        "parse_status": "no_clause_heads",
        "numbering": "none",
        "toc_pages": sorted(toc_pages),
        "toc_reasons": {str(p): toc_reasons[p] for p in sorted(toc_pages)},
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": page_doc["source"],
        "identification": page_doc.get("identification", "unidentified"),
        "extractor": page_doc.get("extractor", ""),
        "stats": {
            "pages": page_doc["stats"]["pages"],
            #: ★조항이 아니므로 `clauses` 를 0 으로 둔다. 청크 수는 따로 센다.
            "clauses": 0,
            "fallback_chunks": len(chunks),
            "sections": len(set(section_of_page.values())),
            "toc_pages_excluded": len(toc_pages),
            "unique_clause_hashes": len({c["content_hash"] for c in chunks}),
            "duplicate_clauses": 0,
        },
        "sections": sorted(set(section_of_page.values())),
        "clauses": chunks,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sha", required=True, help="sha256 접두사")
    args = ap.parse_args()

    hits = sorted(_IN.rglob(f"{args.sha}*.json"))
    if not hits:
        raise InfraError(f"페이지 JSON 을 찾지 못했습니다: {args.sha} (4단계를 먼저 실행하세요)")
    src = hits[0]
    doc = build(json.loads(src.read_text(encoding="utf-8")))

    #: ★입력 경로(`s4_…`)를 그대로 따라가면 **s4 산출물을 덮어쓴다.**
    #:   조항 스키마 버전으로 갈아 끼운다.
    rel = src.relative_to(_IN)
    out = _OUT / rel.parent.parent / _version_tag() / f"{args.sha[:12]}.clauses.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")

    s = doc["stats"]
    print(f"[OK] {src.relative_to(_ROOT)}")
    print(f"     {s['pages']}쪽 → 조항 {s['clauses']}개 / 부(部) {s['sections']}개")
    print(f"     고유 조항 해시 {s['unique_clause_hashes']}개 / 문서 내 중복 {s['duplicate_clauses']}개")
    print(f"     부 목록: {doc['sections']}")
    print(f"     → {out.relative_to(_ROOT)} ({out.stat().st_size:,}B)")


if __name__ == "__main__":
    import sys

    sys.path.insert(0, str(_ROOT))
    main()
