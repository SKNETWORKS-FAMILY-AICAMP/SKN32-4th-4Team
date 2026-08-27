"""표인가 본문인가 — **무참조 판별 신호**.

★왜 필요한가

    `find_two_col` 의 점수(왼쪽 덩어리가 오른쪽과 1:1 로 붙는 비율)는
    **표와 본문 두 단 조판을 못 가른다.** 본문도 줄마다 좌우가 붙기 때문이다.
    실측(s6 전량): 좌표 표 405,845개 중 `2열짝짓기` 가 404,929개(99.8%)인데
    표본이 대부분 본문이었다 —

        '6.' | '응급환자의 이송 등…'          ← 6·7·8행에 같은 값 반복
        '보험금 ④ 회사는' | '늦어지는 경우 보험수익자…'
        '실손의료비보험은 에 한정합니다)를' | '보험회사가 피보험자의 질병 또는…'

    마지막 것은 **한 문장을 두 열로 쪼개며 어절이 뒤섞인 것**이다.
    이대로 판정에 실으면 **없는 표를 근거로 든다.**

★`word_coverage` 로는 못 잡는다

    그건 "단어가 셀에 다 들어갔나"만 본다. 본문을 통째로 두 셀에 나눠 담아도 1.0 이다.
    실제로 오탐 표본 전부가 `word_coverage == 1.0` 이었다.

★신호 다섯 (웹리서치 수렴점: 열 경계 정렬 일관성 + 공백 통로)

    T1 통로무결성   경계를 가로지르는 단어 비율. 표는 0 에 가깝다
    T2 셀중복률     연속 동일 우셀 비율. rowspan 오연결의 지문
    T3 좌열변동     좌셀 길이 변동계수. 표(용어·번호)는 고르고 본문은 들쭉날쭉
    T4 문장종결     좌셀이 문장으로 끝나는 비율. 본문의 지문
    T5 어절파손     좌셀이 조사·어미로 **시작**하는 비율. 한 단어를 쪼갠 지문
    T6 본문지문     항 표지·문장·조 머리가 있나. 기하와 **다른 계열**

★★`선` 경로(격자)는 신호가 따로다 — `grid_signals`

    T7 열사용률     내용이 있는 열 / 선언한 열
    T8 괘선뻗음     열 경계선이 표 높이에서 뻗은 비율(중앙값)

    ★T1~T6 은 `(좌, 우)` 짝이 있어야 잰다. 격자에는 짝이 없다.

★임계값을 지어내지 않는다

    아래 기본값은 **알려진 참/거짓 집합으로 맞춘 것**이고,
    맞춘 근거는 `scripts/eval/table_signal_fit.py` 가 출력한다.
    표본이 늘면 다시 맞춘다.
"""

from __future__ import annotations

import re
import statistics

#: 좌셀이 이걸로 **시작**하면 앞 단어에서 잘려 나온 조각이다.
#:   `에 한정합니다)를` · `및 용어의 정의` · `는 경우는 개정된`
_FRAGMENT_HEAD = re.compile(
    r"^(?:이|가|은|는|을|를|에|의|와|과|로|으로|에서|에게|부터|까지|만|도|및|또는|그리고"
    r"|하여|하고|하는|한|할|함|합니다|입니다|였|었|며|면|나|거나)\b|^[)\]}）］」』]"
)
#: 문장 종결. 법률문은 `~습니다.` `~한다.` 로 끝난다.
_SENT_END = re.compile(r"(?:다|요|음|함)\s*[.。]\s*$|[.。]\s*$")
#: 문장 종결이 **어디에 있든** 센다(T6 용). `_SENT_END` 는 끝만 본다.
_SENT_ANY = re.compile(r"(?:다|요|음|함)\s*[.。]")
#: 항 표지. 본문 조판의 강한 지문이다.
_PARA_MARK = re.compile(r"[①-⑳]")
#: 조 머리. `제9조` · `17. (청약의 철회)` — 표 셀에 이런 게 있으면 본문이다.
_ARTICLE_HEAD = re.compile(r"제\s*\d{1,3}\s*조|\d{1,3}\s*\.\s*[（(]")


def _cv(xs: list[int]) -> float:
    """변동계수. 값이 하나뿐이면 0."""
    if len(xs) < 2:
        return 0.0
    m = statistics.mean(xs)
    return (statistics.pstdev(xs) / m) if m else 0.0


def signals(pairs: list[tuple[str, str]], words, bound: float) -> dict:
    """`(좌, 우)` 짝 목록에서 판별 신호를 낸다.

    `words` · `bound` 는 통로 무결성용. 없으면 `T1` 은 `None` 이다 —
    **0 으로 채우지 않는다.** 못 잰 것과 0 은 다르다(CLAUDE.md §0).
    """
    n = len(pairs)
    if not n:
        return {}

    lefts = [a or "" for a, _ in pairs]
    rights = [b or "" for _, b in pairs]

    #: T1 — 경계를 가로지르는 단어 비율
    t1 = None
    if words:
        cross = sum(1 for w in words if w[0] < bound < w[2])
        t1 = cross / len(words)

    #: T2 — 연속 동일 우셀. `'6.'|X, '7.'|X, '8.'|X` 를 잡는다
    dup = sum(1 for i in range(1, n) if rights[i] and rights[i] == rights[i - 1])
    t2 = dup / max(n - 1, 1)

    #: T3 — 좌셀 길이 변동계수
    t3 = _cv([len(x) for x in lefts])

    #: T4 — 좌셀이 문장으로 끝나거나 항 표지를 품은 비율
    t4 = sum(1 for x in lefts if _SENT_END.search(x) or _PARA_MARK.search(x)) / n

    #: T5 — 좌셀이 조사·어미로 시작(= 앞 단어에서 잘림)
    t5 = sum(1 for x in lefts if _FRAGMENT_HEAD.search(x.strip())) / n

    #: T6 — **본문 지문.** 표 전체 글에 항 표지·문장 2개 이상·조 머리가 있나.
    #:
    #:   ★기하(T1~T5)와 **다른 계열**의 신호다. 열 경계가 아무리 깨끗해도
    #:     셀 안에 `제9조` 나 `①` 가 있으면 그건 표가 아니라 본문이다.
    #:   ★실측(2026-08-03): T1 만으로 문을 열었더니 통과한 2열 4,607개 중
    #:     **3,685개(80.0%)** 가 이 지문을 갖고 있었다. 기하만으로는 못 가른다.
    blob = " ".join(lefts) + " " + " ".join(rights)
    hits = 0
    if _PARA_MARK.search(blob):
        hits += 1
    if len(_SENT_ANY.findall(blob)) >= 2:
        hits += 1
    if _ARTICLE_HEAD.search(blob):
        hits += 1

    return {"T1_corridor": t1, "T2_dup_cells": round(t2, 3),
            "T3_left_cv": round(t3, 3), "T4_sentence": round(t4, 3),
            "T5_fragment": round(t5, 3), "T6_prose_marks": hits, "rows": n}


#: ★★임계값은 **알려진 참/거짓으로 맞춘 값**이다. 지어낸 값이 아니다.
#:   `python -m scripts.eval.table_signal_fit` 이 분리도를 출력한다.
#:
#:   ★1차 측정(참 4 · 거짓 4) — `pair_two_col` 이 **오른쪽 덩어리를 재사용**하던 때
#:     T1 통로 +0.034 갈림 · T4 문장 +0.333 갈림 · T2 중복셀 겹침
#:
#:   ★★재사용 버그를 고친 뒤(참 4 · 거짓 3) — **T2·T4 가 참·거짓 모두 0 이 됐다.**
#:     그 둘은 표의 성질이 아니라 **버그의 지문**이었다. 원인이 사라지니 신호도 사라졌다.
#:     남은 판별자는 T1 하나뿐이고 간격도 좁다 —
#:       T1 통로   참 [0.000~0.039]  거짓 [0.064~0.088]  간격 +0.025  → 임계값 0.052
#:       T2·T3·T4·T5  전부 겹치거나 분리 0
#:
#:   ★그래서 이 게이트는 **얇다.** 표본이 참 4 · 거짓 3 이고 판별자가 하나다.
#:     "정확하다"고 말하지 않는다 — **명백한 오탐을 거르는 하한선**이다.
#:     표본 확장(계획서 L1) 전에는 이 문턱을 근거로 품질을 주장하지 않는다.
THRESHOLDS = {
    "T1_corridor": 0.052,
    #: ★**본문 지문이 하나라도 있으면 표가 아니다.** 기하 신호와 계열이 달라
    #:   T1 이 놓치는 것을 잡는다. 실측에서 오탐의 80% 가 여기 걸린다.
    "T6_prose_marks": 0,
}
RECORDED_ONLY = ("T2_dup_cells", "T3_left_cv", "T4_sentence", "T5_fragment")


def verdict(sig: dict) -> tuple[bool, list[str]]:
    """표로 인정할 것인가. `(인정, 걸린 이유들)`.

    ★**걸린 이유를 돌려준다.** 왜 버렸는지 못 대면 다음 사람이 되풀이한다.
    ★`T1` 이 `None`(못 쟀음)이면 **통과시키지 않는다.** 모르면 안 쓴다(§0).
    """
    why = []
    t1 = sig.get("T1_corridor")
    if t1 is None:
        #: ★못 잰 것과 0 은 다르다(§0). 모르면 통과시키지 않는다.
        why.append("T1 통로를 재지 못함")
    elif t1 > THRESHOLDS["T1_corridor"]:
        why.append(f"T1 통로 가로지름 {t1:.3f}")
    #: ★T4 는 재사용 버그를 고친 뒤 **갈리지 않는다.** 게이트에서 뺐다.
    #:   기록은 계속 남긴다 — 표본이 늘면 다시 갈릴 수 있다.
    t6 = sig.get("T6_prose_marks")
    if t6 is None:
        why.append("T6 본문 지문을 재지 못함")
    elif t6 > THRESHOLDS["T6_prose_marks"]:
        why.append(f"T6 본문 지문 {t6}개")
    return (not why), why


# ─────────────────────────────────────────────────────────────────────────
# `선` 경로(격자) 신호 — 다열 표가 진짜 표인가
# ─────────────────────────────────────────────────────────────────────────

def grid_signals(grid: list[list[str]], ncol: int,
                 rule_spans: list[float] | None = None) -> dict:
    """격자에서 판별 신호를 낸다. `선` 경로 전용.

    `rule_spans` 는 **열 경계선마다** `그 선이 표 높이에서 뻗은 비율`(0~1).
    없으면 `T8` 은 `None` 이다 — **0 으로 채우지 않는다**(CLAUDE.md §0).

    ★★`word_coverage` 로는 못 잡는다. 실측(2026-08-03 · `선` 경로 1,207표):
      **1,207개 전부 `word_coverage == 1.000`** 이다. 모든 단어를 최근접 열에
      강제로 배정하니 열을 9개로 쪼개도 100% 가 나온다. 열을 몇 개 선언했든
      그 지표는 움직이지 않는다.
    """
    if not grid or ncol <= 0:
        return {}
    rows = [r for r in grid if any(c.strip() for c in r)]

    #: T7 — **내용이 있는 열 / 선언한 열.**
    #:
    #:   실측(2026-08-03 · `선` 경로 1,207표) 열 수별 중앙값 —
    #:     2열 1.000 · 3열 1.000 · 4열 0.750 · 5열 1.000 · 6열 0.667
    #:     7열 0.857 · 8열 0.625 · 9열 0.556 · 10열 0.700 · 11열 0.636
    #:
    #:   ★그런데 **게이트로 못 쓴다.** 라벨 37개(참 19·거짓 18)에서
    #:     참 [0.500~1.000] · 거짓 [0.333~1.000] 으로 **통째로 겹친다.**
    #:     특정질병분류표는 표 바깥 테두리가 열로 잡혀 T7 0.750 이고(진짜 표),
    #:     본문을 8열로 쪼갠 것은 T7 1.000 이다(표 아님). 기록만 한다.
    filled = sum(1 for i in range(ncol) if any(r[i].strip() for r in grid))
    t7 = filled / ncol

    #: T8 — **열 경계선이 표 높이에서 얼마나 뻗었나**(중앙값).
    #:
    #:   ★진짜 표의 세로 괘선은 표를 위아래로 가로지른다. 인포그래픽 상자
    #:     테두리는 **자기 상자 높이만** 뻗는다. 상자가 여러 개 쌓여 있으면
    #:     그 테두리들이 전부 "열 경계"로 잡혀 8~9열 격자가 선다.
    #:     그렇게 세운 셀에는 서로 다른 상자의 글이 뒤섞인다 —
    #:       `{'1': 'QR코드를 통한 QR(Quic', '3': '편리한 정보이용 Respons'}`
    t8 = None
    if rule_spans:
        t8 = statistics.median(rule_spans)

    return {"T7_column_use": round(t7, 3),
            "T8_rule_span": None if t8 is None else round(t8, 3),
            "cols": ncol, "filled_cols": filled, "rows": len(rows)}


#: ★★임계값은 **사람이 격자를 직접 읽어 붙인 라벨 37개**(참 19 · 거짓 18)에서
#:   맞춘 값이다. 지어낸 값이 아니다. 측정은
#:   `docs/reports/2026-08-03_다열표_차단_게이트.md` 에 표로 남겼다.
#:
#:   ★전체 라벨에서는 **어떤 신호도 갈리지 않았다** — T7·T8·행채움일관성·
#:     T6·평균채움 전부 겹쳤다. 4열 이하에는 괘선이 짧은 진짜 표가 있기 때문이다
#:     (표가 쪽의 일부만 차지하면 괘선도 짧다. 그건 표가 아니라는 증거가 아니다).
#:
#:   ★**5열 이상에서만 갈렸다**(참 5 · 거짓 14) —
#:       T8 괘선뻗음  참 [0.705~0.847]  거짓 [0.129~0.519]  간격 +0.186  → 0.612
#:       T7 열사용률  참 [0.857~0.857]  거짓 [0.556~1.000]  **겹침** → 안 쓴다
#:     4열까지 범위를 넓히면 T8 도 겹친다(참 0.097 · 거짓 0.519).
#:     그래서 **적용 범위 자체가 실측으로 정해진 값**이다.
#:
#:   ★표본이 작다. 5열 이상 참이 **5개**뿐이고 그중 4개가 같은 조판
#:     (좌우 2패널 특정질병분류표)이다. "정확하다"고 말하지 않는다 —
#:     **명백한 오탐을 거르는 하한선**이다.
GRID_THRESHOLDS = {"T8_rule_span": 0.612}
#: ★이 열 수 **미만은 판정하지 않는다.** 4열 이하에서는 갈리는 신호가 없었다.
GRID_MIN_COLS = 5
GRID_RECORDED_ONLY = ("T7_column_use",)


def grid_verdict(sig: dict) -> tuple[bool, list[str]]:
    """다열 격자를 표로 인정할 것인가. `(인정, 걸린 이유들)`.

    ★**`True` 는 "이 게이트에 걸리지 않았다"는 뜻이다.** "표임을 확인했다"가
      아니다. 4열 이하는 이 게이트가 아예 판정하지 않는다(위 `GRID_MIN_COLS`).
    ★`T8` 이 `None`(못 쟀음)이면 **통과시키지 않는다.** 모르면 안 쓴다(§0).
    """
    why: list[str] = []
    ncol = sig.get("cols")
    if not ncol:
        return False, ["열 수를 모름"]
    if ncol < GRID_MIN_COLS:
        return True, []
    t8 = sig.get("T8_rule_span")
    if t8 is None:
        why.append("T8 괘선 뻗음을 재지 못함")
    elif t8 < GRID_THRESHOLDS["T8_rule_span"]:
        why.append(f"T8 괘선이 표 높이의 {t8:.3f} 만 뻗음 "
                   f"({ncol}열 격자를 세운 선이 표 괘선이 아니다)")
    return (not why), why

#: ── T9: 셀이 **본문처럼 생겼나** (선 경로용) ────────────────────────
#:
#: ★★사람 라벨 59건으로 재보니 `선` 경로가 **오탐의 주범**이었다.
#:   `false` 55건 중 우리가 실은 22건 가운데 **18건이 선 경로**다.
#:   T1~T6 은 2열 전용이고 T7·T8 은 다열 전용이라 **선 경로 2~6열을 아무도 안 봤다.**
#:
#: ★T7·T8 은 사람 라벨에서 **겹쳤다**(T8: 참 med 0.710 / 거짓 med 0.722).
#:   자체 라벨 37건으로 맞춘 문턱이 남의 라벨에서 무너졌다.
#:
#: 그래서 **한쪽 문턱**만 쓴다 — 참 5건의 최댓값 바깥. 참을 하나도 안 버린다.
#:   실측: 거짓 20건 중 **9건(45%)** 을 잡고 참 손실 **0**.
#:
#: ★★**문턱이 약하다.** 참 표본이 **5건**뿐이라 여섯 번째 참이 이 선을 넘을 수 있다.
#:   그래서 이 신호는 **명백한 본문만** 거른다. 나머지 11건(짧은 셀 · 코드 목록을
#:   가로로 읽은 것)은 **못 잡는다** — 텍스트 통계로는 표와 구분되지 않는다.
_T9_MAX_CELL_LEN = 84        # 참 최대 84
_T9_SENT_RATIO = 0.30        # 참 최대 0.30
_T9_LONG_CELL_RATIO = 0.25   # 참 최대 0.25


def prose_shape(records: list) -> dict:
    """셀 모양이 본문에 가까운가. `선` 경로가 쓴다.

    반환에 `is_prose` 가 있다. `True` 면 **표가 아니라 본문**으로 본다.
    """
    cells = [str(v) for r in (records or [])
             for v in ((r or {}).get("cols") or {}).values() if str(v).strip()]
    if not cells:
        return {}
    blob = " ".join(cells)
    lens = [len(c) for c in cells]
    max_len = max(lens)
    sent = len(_SENT_ANY.findall(blob)) / len(cells)
    long_ratio = sum(1 for x in lens if x > 40) / len(lens)
    why = []
    if max_len > _T9_MAX_CELL_LEN:
        why.append(f"셀 최대 길이 {max_len}")
    if sent > _T9_SENT_RATIO:
        why.append(f"문장부호 비율 {sent:.2f}")
    if long_ratio > _T9_LONG_CELL_RATIO:
        why.append(f"긴 셀 비율 {long_ratio:.2f}")
    return {"T9_max_cell_len": max_len,
            "T9_sent_ratio": round(sent, 3),
            "T9_long_cell_ratio": round(long_ratio, 3),
            "is_prose": bool(why),
            "prose_why": why}


def attachment_verdict(table: dict) -> tuple[bool, list[str]]:
    """Whether a coordinate table may be attached to a clause/annex.

    This is the single contract shared by ``to_clauses`` and the S5↔S6
    consistency checker.  Keeping separate copies previously made an intended
    T9 rejection look like 138 artifact-integrity failures.

    ``is_table is None`` remains backward compatible for old line-table page
    artifacts, but the independent prose-shape veto always applies.
    """
    why: list[str] = []
    if table.get("method") != "선":
        why.append(f"미검증 방식 {table.get('method')!r}")
    if table.get("is_table") is False:
        why.extend(table.get("reject_why") or ["페이지 표 게이트 탈락"])
    prose = prose_shape(table.get("records") or [])
    if prose.get("is_prose"):
        why.extend(prose.get("prose_why") or ["T9 본문 모양"])
    return not why, why
