"""좌표로 표를 복원한다 — 테두리가 없어도, 페이지가 돌아가 있어도.

★왜 필요한가

    보장한도·자기부담금·질병분류표(KCD)가 **전부 표에 있다.**
    지금 `to_page_json.py` 는 `page.find_tables()` 기본값만 쓰는데, 그게 실패하면
    표의 글자가 조항 본문에 **평문으로** 섞여 들어간다. 글자는 있는데 행·열이 없다.
    "자기부담금 20%" 는 검색되지만 **그 20% 가 표준형 입원인지 선택형 통원인지 모른다.**
    KCD 표는 더 위험하다 — 질병명과 코드가 잘못 짝지어지면 **오판정**이 된다.
    결함 상세: `docs/reports/debugs/2026-08-02_표추출_구조파괴_결함.md`

★실측 (2026-08-02)

    | 케이스 | `find_tables()` | OpenDataLoader v2.0 | 이 모듈 |
    |---|---|---|---|
    | 흥국화재 `ff0521a99902` p109 KCD (선 있음) | **2×3 붕괴** | 27×3 | **42×3 · 단어배정 100%** |
    | DB `02aaee47b190` p53 용어정의표 (rot90·무테두리·2패널) | **0표** | **0표** | **용어 23개 · 점수 0.97/0.92** |

    ★도구를 갈아치우는 문제가 아니었다. **좌표가 답이다.**
      `find_tables()` 가 격자를 못 엮어도 **선은 `get_drawings()` 에 남아 있고**,
      선이 아예 없어도 좌측 정렬과 여백으로 열을 찾을 수 있다.

★핵심은 **순서**다

    행(baseline)부터 묶으면 안 된다. 용어정의표는 용어가 여러 줄짜리 정의 옆에
    **세로 가운데**로 놓여서, 행으로 묶으면 용어와 정의가 다른 행이 된다.
        열 경계 → 열별 **덩어리** → **y 겹침**으로 짝짓기
    코덱스 표현으로 "시각 줄 · 격자 row · 논리 레코드"는 **서로 다른 개념**이다.

★추정하지 않는다

    경계를 못 고르면 `None` 을 돌려준다. 점수(`score`)를 함께 내보내
    쓰는 쪽이 **믿을지 말지 스스로 판단**하게 한다. 보장한도·자기부담금·
    표준형/선택형·입원/통원·KCD 는 점수가 낮으면 **자동 판정을 멈춰야** 한다.

실행:
    python -m scripts.extract.table_coords <pdf> <0-based page>
"""

from __future__ import annotations

import sys
from collections import defaultdict

import fitz


def words_of(page) -> list[tuple]:
    """`(x0, y0, x1, y1, text)` — 회전을 편 좌표계로.

    ★수동 삼각함수로 돌리면 안 된다. `page.rect` 는 **회전이 반영된** 크기인데
      `get_text("words")` 좌표는 **회전 전** 공간이라 헷갈린다
      (실측 DB p53: rect 751×544 인데 단어 y 가 664 까지).
      `page.rotation_matrix` 를 쓰면 CropBox 원점이 0 이 아닌 문서도 맞는다.
    """
    m = page.rotation_matrix
    out = []
    for x0, y0, x1, y1, t, *_ in page.get_text("words"):
        r = fitz.Rect(x0, y0, x1, y1) * m
        out.append((r.x0, r.y0, r.x1, r.y1, t))
    return out


def median_h(words) -> float:
    """글자 높이 중앙값. ★임계를 여기서 유도한다 — 문서마다 손으로 주지 않는다."""
    hs = sorted(w[3] - w[1] for w in words)
    return hs[len(hs) // 2] if hs else 10.0


def detect_clip(words):
    """본문 영역만 남긴다.

    ★머리말·쪽번호가 **하나만** 있어도 열 추정이 무너진다 —
      그 한 줄이 좌우를 이어 버려 빈 구간이 사라진다(코덱스 지적).
    """
    if not words:
        return None
    h = median_h(words)
    rows = defaultdict(list)
    for w in words:
        rows[round(w[1] / h)].append(w)
    keys = sorted(rows)
    while len(keys) > 2 and len(rows[keys[0]]) <= 3:
        keys.pop(0)
    while len(keys) > 2 and len(rows[keys[-1]]) <= 3:
        keys.pop()
    keep = [w for k in keys for w in rows[k]]
    if not keep:
        return None
    return (min(w[0] for w in keep), min(w[1] for w in keep),
            max(w[2] for w in keep), max(w[3] for w in keep))


def panels(words) -> list[float]:
    """다단(좌우 패널) 분리 — **가장 적게 걸치는** 세로 띠.

    ★"완전히 빈 띠"를 요구하면 못 찾는다. 실측(DB p53) x 히스토그램에서
      360~460 구간이 단어 5~15개로 파여 있는데 **0 은 아니다.**
      두 패널에 걸친 넓은 셀이 있기 때문이다.
    """
    xs = [w[0] for w in words] + [w[2] for w in words]
    lo, hi = min(xs), max(xs)
    step = max(2.0, (hi - lo) / 200)
    best, best_cross = None, None
    x = lo + (hi - lo) * 0.25
    while x < lo + (hi - lo) * 0.75:
        cross = sum(1 for w in words if w[0] < x < w[2])
        if best_cross is None or cross < best_cross:
            best, best_cross = x, cross
        x += step
    if best is None:
        return []
    left = sum(1 for w in words if w[2] <= best) / len(words)
    if best_cross / len(words) < 0.02 and 0.2 < left < 0.8:
        return [best]
    return []


def _rules(page, m):
    """페이지의 **괘선 조각**을 `(x0, y0, x1, y1)` 로 모은다.

    ★★`"l"`(선분)만 보면 안 된다. 조판기에 따라 표 괘선을 **얇은 사각형(`"re"`)**
      으로 그린다. 실측(KB손보 `1d87934060bb` p8): `l` 27개 · `re` 2개인데
      **열 경계가 하나도 안 나왔다.** 이 판본을 쓰는 4개사(kbinsure·meritzfire·
      myangel·nhlife)는 `선` 경로 표가 **전부 0개**였다.

    ★`re` 는 얇을 때만 괘선으로 본다. 두꺼우면 음영 상자다 — 그걸 선으로 세면
      표가 아닌 배경 박스에서 가짜 열 경계가 나온다.
    """
    THIN = 2.0
    out = []
    for d in page.get_drawings():
        for it in d["items"]:
            if it[0] == "l":
                a, b = fitz.Point(it[1]) * m, fitz.Point(it[2]) * m
                out.append((min(a.x, b.x), min(a.y, b.y), max(a.x, b.x), max(a.y, b.y)))
            elif it[0] == "re":
                r = fitz.Rect(it[1]) * m
                if min(r.width, r.height) <= THIN:
                    out.append((r.x0, r.y0, r.x1, r.y1))
    return out


def line_cols(page, clip, min_frac: float = 0.3, min_abs: float = 24.0) -> list[tuple]:
    """★세로선에서 열 경계를 얻는다 — 있으면 이게 정답이다. `(x, y0, y1)`.

    `find_tables()` 는 선을 **격자로 엮지 못하면** 표를 통째로 놓치지만
    (흥국화재 KCD 22행 → 2×3), **선 자체는 `get_drawings()` 에 그대로 있다.**
    실측: 경계 `66.13 | 86.49 | 165.44 | 445.88` 가 여기서 나오고,
    그걸 쓰면 격자 조립 없이 42×3 이 복원된다.

    ★★길이 조건이 **페이지 높이의 30%** 였다. 그러면 쪽 일부만 차지하는
      작은 표는 **원리적으로 못 잡는다.** 그래서 `min_abs`(절대 길이) 를 함께 두고
      **둘 중 작은 쪽**을 문턱으로 쓴다. 24pt 는 본문 3줄 남짓이다 —
      그보다 짧은 세로선은 밑줄·구분자이지 표 괘선이 아니다.

    ★★**x 만 돌려주고 y 를 버렸던 것이 화근이었다.** 인포그래픽 상자 테두리도
      세로선이라 열 경계로 잡히는데, 그건 **자기 상자 높이만** 뻗는다.
      상자가 여럿 쌓여 있으면 그 테두리가 전부 열 경계가 되어 8~9열 격자가 서고,
      한 셀에 서로 다른 상자의 글이 섞인다. 실측(2026-08-03): 8열 이상 격자
      **19개가 전부 표가 아니었다.** 어디까지 뻗었는지를 함께 돌려줘야
      `table_signals.grid_signals` 가 그걸 잰다(`T8_rule_span`).
    """
    if not clip:
        return []
    x0, y0, x1, y1 = clip
    need = min((y1 - y0) * min_frac, min_abs)
    m = page.rotation_matrix
    segs = []
    for a0, b0, a1, b1 in _rules(page, m):
        if abs(a1 - a0) > 1.5 or abs(b1 - b0) < need:
            continue          # 세로가 아니거나 너무 짧다
        segs.append(((a0 + a1) / 2, b0, b1))
    segs = sorted(s for s in segs if x0 - 2 <= s[0] <= x1 + 2)
    out: list[list[float]] = []
    for x, ya, yb in segs:
        #: ★같은 자리에 겹쳐 그린 조각은 **y 를 합쳐** 한 경계로 본다.
        #:   토막난 괘선을 따로 세면 뻗음이 실제보다 짧게 나온다.
        if out and x - out[-1][0] < 3:
            out[-1][1] = min(out[-1][1], ya)
            out[-1][2] = max(out[-1][2], yb)
            continue
        out.append([x, ya, yb])
    #: 양끝은 표 테두리라 열 경계가 아니다.
    return [tuple(c) for c in out[1:-1]] if len(out) >= 3 else []


def drop_vertical_runs(words, h, min_len: int = 3) -> tuple[list, list]:
    """★세로로 세운 글자(사이드바 탭)를 걷어낸다. `(남길 것, 걷어낸 것)`.

    약관은 쪽 옆에 `보`/`통`/`약`/`관` 을 **한 글자씩 세로로** 찍는다.
    회전을 편 좌표계에서는 그게 정의 열 안으로 들어가 본문을 오염시킨다
    (실측 DB `02aaee47b190` p53: 정의 열에 `보 통 약 관` 이 섞였다).

    시그니처: **한 글자 단어가 같은 x 에 3개 이상, y 가 글자높이 간격으로 규칙적.**
        `보`(720.5, 42.6) `통`(720.5, 54.4) `약`(720.5, 66.1) `관`(720.5, 78.0)

    ★걷어낸 것도 **돌려준다.** 조용히 버리지 않는다(RULE §3).
    """
    #: ★★숫자는 제외한다. 표의 **행 번호**(`5` `6` `7`…)가 세로로 쌓여 있어
    #:   그대로 걸면 번호가 사라진다. 실제로 흥국 KCD 표에서 번호 5~9 가 지워져
    #:   `고혈압` 이 결핵·담석증까지 흡수하는 **KCD 오짝**이 났다.
    #: ★그리고 **본문 영역 안쪽은 건드리지 않는다.** 사이드바는 쪽 가장자리에 있다.
    xs_all = [w[0] for w in words] + [w[2] for w in words]
    lo, hi = min(xs_all), max(xs_all)
    edge = (hi - lo) * 0.08
    by_x = defaultdict(list)
    for w in words:
        if len(w[4]) != 1 or w[4].isdigit():
            continue
        if lo + edge < w[0] < hi - edge:      # 가장자리가 아니면 사이드바가 아니다
            continue
        by_x[round(w[0])].append(w)
    drop = set()
    for xs in by_x.values():
        if len(xs) < min_len:
            continue
        xs.sort(key=lambda w: w[1])
        run = [xs[0]]
        for a, b in zip(xs, xs[1:]):
            #: 세로로 잇달아 있는가 — 간격이 글자높이의 2배 이내
            if b[1] - a[1] < h * 2.0:
                run.append(b)
            else:
                if len(run) >= min_len:
                    drop |= {id(z) for z in run}
                run = [b]
        if len(run) >= min_len:
            drop |= {id(z) for z in run}
    keep = [w for w in words if id(w) not in drop]
    return keep, [w for w in words if id(w) in drop]


def logical_records(cells, anchor_col: int = 0):
    """★rowspan 복원 — **y 겹침**으로. 행 순서로 묶으면 틀린다.

    ★처음엔 "번호 행부터 다음 번호 행까지"로 묶었다가 **KCD 오짝을 만들었다**:
        1 심장질환   -> I20~I25, I26~I28  I30~I52, I97  **I60~I69**  ← 뇌혈관질환 것
        2 뇌혈관질환 -> G45, G46, Q28  **E10~E14**                    ← 당뇨병 것
        4 고혈압     -> 결핵·담석증·요로결석증까지 흡수(rowspan 8)
      번호·질병명이 **세로 가운데**에 놓여 코드 줄이 한 칸 위에서 시작하기 때문이다.
      질병명↔코드 오짝은 **이 서비스에서 가장 위험한 실패**다(CLAUDE.md 0장).

    그래서 anchor 셀의 **y 범위와 겹치는 셀만** 가져온다. 용어정의표와 같은 원리.
    ★anchor 가 없는 구간은 **묶지 않고 남긴다**(`no=None`). 추정하지 않는다.

    cells: `[{col, y0, y1, text}]`
    """
    anchors = sorted((c for c in cells
                      if c["col"] == anchor_col and c["text"].strip()
                      and c["text"].strip().isascii() and c["text"].strip().isdigit()),
                     key=lambda c: c["y0"])
    if len(anchors) < 3:
        return []
    recs = []
    for i, a in enumerate(anchors):
        #: ★경계는 **이웃 anchor 의 중간**. anchor 자신의 높이만 쓰면 코드 줄을 놓친다.
        top = (anchors[i - 1]["y1"] + a["y0"]) / 2 if i else a["y0"] - (a["y1"] - a["y0"])
        bot = (a["y1"] + anchors[i + 1]["y0"]) / 2 if i + 1 < len(anchors) else a["y1"] + (a["y1"] - a["y0"])
        by_col = defaultdict(list)
        for c in cells:
            if c["col"] == anchor_col or not c["text"].strip():
                continue
            cy = (c["y0"] + c["y1"]) / 2
            if top <= cy < bot:
                by_col[c["col"]].append(c)
        rec = {"no": int(a["text"].strip()), "y0": top, "y1": bot, "cols": {}}
        for col, cs in by_col.items():
            rec["cols"][col] = " ".join(c["text"] for c in sorted(cs, key=lambda c: c["y0"]))
        recs.append(rec)
    return recs


def rows_of(words, tol):
    """y 대역으로 행 묶기. ★대역을 **고정**한다 — 팽창시키면 연쇄 병합된다."""
    rows = []
    for w in sorted(words, key=lambda x: (x[1], x[0])):
        cy = (w[1] + w[3]) / 2
        hit = next((r for r in rows if r["c"] - tol <= cy <= r["c"] + tol), None)
        if hit:
            hit["ws"].append(w)
        else:
            rows.append({"c": cy, "ws": [w]})
    return rows


def join_words(ws, tol) -> str:
    """셀 안의 단어를 **읽는 순서**로 잇는다.

    ★x 로만 정렬하면 여러 줄짜리 정의가 뒤섞인다 —
      실제로 `계약을계약 체결하기 위하여…` 가 나왔다.
      **줄(y)로 먼저 묶고 줄 안에서 x** 로 정렬해야 한다(코덱스 지적).
    ★붙임: x 간격이 1pt 미만이면 줄바꿈으로 갈린 한 낱말이다
      (`계약자에 게` · `의료기 관` 이 그렇게 생겼다).
    """
    parts = []
    for r in sorted(rows_of(ws, tol), key=lambda r: r["c"]):
        line, prev = [], None
        for w in sorted(r["ws"], key=lambda x: x[0]):
            if prev is not None and w[0] - prev < 1.0:
                line[-1] += w[4]
            else:
                line.append(w[4])
            prev = w[2]
        parts.append(" ".join(line))
    return " ".join(parts)


def blocks_of(words, gap):
    """y 로 이어지는 **덩어리**. ★행이 아니다.

    용어정의표는 용어가 여러 줄짜리 정의 옆에 **세로 가운데**로 놓인다.
    행(baseline)으로 묶으면 용어와 정의가 다른 행이 되어 짝을 못 짓는다.
    """
    out = []
    for w in sorted(words, key=lambda x: x[1]):
        if out and w[1] - max(z[3] for z in out[-1]) < gap:
            out[-1].append(w)
        else:
            out.append([w])
    return out


def pair_two_col(words, h, bound, tol):
    """경계 하나로 2열 표를 짝짓고 **점수**를 낸다.

    ★★**오른쪽 덩어리를 재사용하지 않는다.**

      재사용하면 한 덩어리가 여러 왼쪽 덩어리에 붙어 이런 산출물이 나온다 —

          '6.' | '응급환자의 이송 등…'
          '7.' | '응급환자의 이송 등…'   ← 같은 값
          '8.' | '응급환자의 이송 등…'   ← 같은 값

      본문 두 단 조판에서 특히 심하다. 오른쪽 문단 하나가 왼쪽 여러 줄과
      y 가 겹치기 때문이다. 그리고 **점수도 부풀린다** — 붙기만 하면
      `matched` 가 오르므로 본문이 표처럼 높은 점수를 받는다(코덱스 지적).

      그래서 **겹침이 가장 큰 왼쪽 덩어리에만** 배정하고, 이미 쓴 것은 뺀다.
    """
    left = [w for w in words if w[2] <= bound]
    right = [w for w in words if w[0] >= bound]
    if len(left) < 3 or len(right) < 3:
        return None
    lb, rb = blocks_of(left, h * 0.8), blocks_of(right, h * 0.8)
    if len(lb) < 3:
        return None

    def _span(b):
        return min(w[1] for w in b), max(w[3] for w in b)

    #: 왼쪽 덩어리를 y 순으로 두고, 오른쪽 덩어리를 **한 번씩만** 배정한다.
    lb = sorted(lb, key=lambda b: _span(b)[0])
    taken = set()
    pairs, matched = [], 0
    for t in lb:
        ty0, ty1 = _span(t)
        best, best_ov = None, 0.0
        for i, b in enumerate(rb):
            if i in taken:
                continue
            by0, by1 = _span(b)
            ov = min(ty1, by1) - max(ty0, by0)
            if ov > best_ov:
                best, best_ov = i, ov
        if best is not None and best_ov > 0:
            taken.add(best)
            matched += 1
            pairs.append((join_words(t, tol), join_words(rb[best], tol)))
        else:
            pairs.append((join_words(t, tol), ""))
    #: ★검증 — 왼쪽 덩어리가 오른쪽과 **1:1 로** 붙는 비율.
    #:   단어 커버리지만 보면 안 된다. **전부 한 셀에 넣어도 커버리지는 100%** 다.
    return {"bound": bound, "pairs": pairs, "score": matched / len(lb),
            "n_left": len(lb), "n_right": len(rb),
            #: ★배정 못 한 오른쪽 덩어리 수. 남는 게 많으면 표가 아니다.
            "n_right_unused": len(rb) - len(taken)}


def corridors(words, h, min_width_ratio: float = 0.8,
              max_cross_ratio: float = 0.05) -> list[float]:
    """x 축에서 **글자가 거의 없는 통로**를 넓은 순으로 돌려준다.

    ★★**합집합으로 찾으면 안 된다.** 처음에 단어 구간을 합쳐 빈 곳을 찾았더니
      통로가 **1개**만 나왔다. 전폭 제목 한 줄이 통로를 통째로 지우기 때문이다.
      원본 `find_two_col` 이 이미 겪은 함정이다 —
      *"가로지르는 단어를 금지하지 않는다. 제목 한 줄이 전폭이면 후보가 다 죽는다."*

    ★그래서 **가로지르는 단어 비율**로 본다. 몇 줄이 가로질러도 통로는 통로다.
      `max_cross_ratio` 를 넘으면 그때 통로가 아니다.

    ★넓은 순으로 돌려준다. 진짜 열 경계가 어절 사이 공백보다 넓다.
    """
    if not words:
        return []
    lo = min(w[0] for w in words)
    hi = max(w[2] for w in words)
    if hi - lo < h:
        return []
    step = max(1.0, h / 4)
    n = int((hi - lo) / step) + 1
    cover = [0] * n
    for w in words:
        a = max(0, int((w[0] - lo) / step))
        b = min(n - 1, int((w[2] - lo) / step))
        for i in range(a, b + 1):
            cover[i] += 1
    limit = max(1, int(len(words) * max_cross_ratio))
    need = max(2, int(h * min_width_ratio / step))

    runs, start = [], None
    for i, c in enumerate(cover):
        if c <= limit:
            start = i if start is None else start
        else:
            if start is not None and i - start >= need:
                runs.append((i - start, lo + (start + i) / 2 * step))
            start = None
    if start is not None and n - start >= need:
        runs.append((n - start, lo + (start + n) / 2 * step))

    #: ★양 끝 여백은 열 경계가 아니다. 안쪽 것만 쓴다.
    runs = [r for r in runs if lo + h < r[1] < hi - h]
    runs.sort(reverse=True)          # 넓은 통로 먼저
    return [x for _, x in runs]


def _grid_quality(g: dict) -> float:
    """격자가 표다운가. **행마다 비슷한 열이 채워지는가**로 본다.

    ★열을 많이 쪼갤수록 `word_coverage` 는 그대로라 그걸로는 k 를 못 고른다.
      표는 행마다 같은 열들이 차고, 본문은 행마다 들쭉날쭉하다.
    """
    rows = [r for r in g["grid"] if any(c.strip() for c in r)]
    if len(rows) < 3:
        return 0.0
    ncol = g["cols"]
    filled = [sum(1 for c in r if c.strip()) for r in rows]
    #: 채움 개수의 최빈값이 얼마나 지배적인가
    mode = max(set(filled), key=filled.count)
    consistency = filled.count(mode) / len(rows)
    #: 한 열만 차는 격자는 표가 아니다(본문 한 덩어리)
    if mode <= 1:
        return 0.0
    return consistency * (mode / ncol)


def find_multi_col(words, h, tol, kmax: int = 5, min_quality: float = 0.6):
    """열 수 **k 를 골라서** 격자를 세운다. 못 고르면 `None`.

    ★k 를 1..kmax 로 다 세워 보고 **행별 채움이 가장 고른 것**을 택한다(웹리서치
      수렴점: 반복·정렬된 x 위치가 열 구조를 드러낸다).
    ★동점이면 **열이 적은 쪽**을 택한다. 과분할이 과소분할보다 위험하다 —
      한 셀을 쪼개면 질병명과 코드가 갈라진다.
    """
    cands = corridors(words, h)
    if not cands:
        return None
    best = None
    for k in range(1, min(kmax, len(cands)) + 1):
        #: 통로가 많으면 **넓은 것부터** 고른다 — 넓은 통로가 진짜 열 경계다.
        #: `build_grid` 는 경계가 x 순으로 정렬돼 있어야 한다.
        for combo in (sorted(cands[:k]),):
            g = build_grid(words, combo, tol)
            q = _grid_quality(g)
            if q < min_quality:
                continue
            if best is None or (q, -g["cols"]) > (best[0], -best[1]["cols"]):
                best = (q, g, combo)
    if best is None:
        return None
    q, g, bounds = best
    g = dict(g)
    g["quality"] = round(q, 3)
    g["bounds"] = bounds
    return g


def find_two_col(words, h, tol, min_score: float = 0.6):
    """★경계를 **검증 점수로 고른다.** 후보를 다 만들어 보고 제일 잘 붙는 것을 쓴다."""
    xs = sorted(set(round(w[2]) for w in words))
    if not xs:
        return None
    lo, hi = xs[0], xs[-1]
    best = None
    x = lo + (hi - lo) * 0.05
    while x < lo + (hi - lo) * 0.6:
        #: ★가로지르는 단어를 **금지하지 않는다.** 제목 한 줄이 전폭이면 후보가 다 죽는다.
        #:   대신 벌점으로 주고 점수로 고른다.
        cross = sum(1 for w in words if w[0] < x < w[2])
        r = pair_two_col(words, h, x, tol)
        if r:
            r["score"] -= cross / len(words)
            if best is None or (r["score"], r["n_left"]) > (best["score"], best["n_left"]):
                best = r
        x += h * 0.5
    return best if best and best["score"] >= min_score else None


def build_grid(words, bounds, tol):
    """경계를 받아 격자를 만든다. 열 배정은 **겹침 폭이 최대인 열**로."""
    ncol = len(bounds) + 1
    edges = [-1e9] + list(bounds) + [1e9]
    grid, assigned, flat = [], 0, []
    for r in sorted(rows_of(words, tol), key=lambda r: r["c"]):
        cells = defaultdict(list)
        for w in r["ws"]:
            best, bi = -1.0, 0
            for i in range(ncol):
                ov = min(w[2], edges[i + 1]) - max(w[0], edges[i])
                if ov > best:
                    best, bi = ov, i
            cells[bi].append(w)
            assigned += 1
        row = []
        for i in range(ncol):
            ws2 = cells.get(i, [])
            row.append(join_words(ws2, tol))
            if ws2:
                flat.append({"col": i, "text": row[-1],
                             "y0": min(w[1] for w in ws2), "y1": max(w[3] for w in ws2)})
        grid.append(row)
    #: ★첫 열이 `1…22` 처럼 단조 증가하면 표일 가능성이 높다(약한 신호).
    firsts = [r[0].strip() for r in grid if r[0].strip()]
    nums = [int(x) for x in firsts if x.isascii() and x.isdigit()]
    return {"cols": ncol, "rows": len(grid), "grid": grid, "cells": flat,
            "word_coverage": round(assigned / len(words), 3) if words else 0.0,
            "first_col_monotonic": len(nums) >= 3 and all(a < b for a, b in zip(nums, nums[1:]))}


def extract(page) -> list[dict]:
    """페이지에서 표 후보를 만든다. 못 만들면 빈 목록."""
    #: ★모듈 최상단이 아니라 여기서 들여온다 — `python scripts/extract/table_coords.py`
    #:   로 직접 돌릴 때 `sys.path` 가 `__main__` 에서야 잡히기 때문이다.
    from scripts.extract.table_signals import (grid_signals, grid_verdict,
                                               signals, verdict)

    ws = words_of(page)
    if not ws:
        return []
    clip = detect_clip(ws)
    if clip:
        ws = [w for w in ws if clip[0] - 1 <= w[0] and w[2] <= clip[2] + 1
              and clip[1] - 1 <= w[1] and w[3] <= clip[3] + 1]
    if len(ws) < 5:
        return []
    h = median_h(ws)
    #: ★세로 사이드바를 먼저 걷어낸다. 안 하면 정의 열이 오염된다.
    ws, dropped = drop_vertical_runs(ws, h)
    if len(ws) < 5:
        return []
    tol = h * 0.3
    lines = line_cols(page, clip)
    out = []
    edges = [-1e9] + panels(ws) + [1e9]
    for i in range(len(edges) - 1):
        #: ★패널마다 **자기 y 축**으로. 전역 y 로 맞추면 왼쪽 14행·오른쪽 10행이 깨진다.
        sub = [w for w in ws if edges[i] <= w[0] < edges[i + 1]]
        if len(sub) < 5:
            continue
        mine = [c for c in lines if edges[i] < c[0] < edges[i + 1]]
        if mine:
            r = build_grid(sub, [c[0] for c in mine], tol)
            #: ★★**`선` 경로에도 게이트를 건다.** 그동안 이 경로는 신호 없이
            #:   무조건 실렸다. 정답셋(3표·전부 3열)에서 1.000 이었기 때문인데,
            #:   그 정답셋에 **다열 표가 하나도 없었다.** 실측(2026-08-03)
            #:   8열 이상 격자 19개는 전부 인포그래픽 상자거나 본문이었다.
            #:   `word_coverage` 는 1,207표 **전부 1.000** 이라 아무것도 못 가른다.
            top = min(w[1] for w in sub)
            bot = max(w[3] for w in sub)
            span = max(bot - top, 1.0)
            #: 경계선이 이 표 높이에서 뻗은 비율. 1.0 을 넘지 않게 자른다.
            spans = [max(0.0, min(1.0, (min(yb, bot) - max(ya, top)) / span))
                     for _, ya, yb in mine]
            sig = grid_signals(r["grid"], r["cols"], spans)
            ok, why = grid_verdict(sig)
            r.update(method="선", panel=i + 1,
                     dropped_vertical=len(dropped),
                     records=logical_records(r.pop("cells")),
                     signals=sig, is_table=ok, reject_why=why)
            out.append(r)
            continue
        two = find_two_col(sub, h, tol)
        if two:
            #: ★2열도 `records` 를 만든다. 안 만들면 하류(페이지 JSON)가
            #:   "표가 아니다"로 보고 통째로 버린다 — 용어정의표가 그렇게 사라졌다.
            #:   ★번호 anchor 가 없으므로 `no` 는 **행 순서**다. 원문 번호가 아니다.
            #:   그래서 `no_source` 로 어디서 온 번호인지 밝힌다 — 지어낸 값처럼
            #:   보이면 안 된다(CLAUDE.md §1).
            sig = signals(two["pairs"], sub, two["bound"])
            ok, why = verdict(sig)
            recs = [{"no": k, "no_source": "row_order",
                     "cols": {"1": a, "2": b}}
                    for k, (a, b) in enumerate(two["pairs"], 1)]
            out.append({"method": "2열짝짓기", "panel": i + 1, "cols": 2,
                        "rows": len(two["pairs"]), "grid": [list(p) for p in two["pairs"]],
                        "score": round(two["score"], 3), "word_coverage": None,
                        "first_col_monotonic": False,
                        "dropped_vertical": len(dropped), "records": recs,
                        #: ★**판별 신호를 함께 싣는다.** 왜 통과/탈락인지 못 대면
                        #:   다음 사람이 임계값을 다시 지어낸다.
                        "signals": sig, "is_table": ok, "reject_why": why})
    return out


def main() -> None:
    src, pno = sys.argv[1], int(sys.argv[2])
    doc = fitz.open(src)
    page = doc[pno]
    print(f"rotation={page.rotation} · find_tables()={len(page.find_tables().tables)}표")
    for t in extract(page):
        head = {k: v for k, v in t.items() if k != "grid"}
        print(f"\n-- {head}")
        for row in t["grid"][:12]:
            print("   ", [c[:30] for c in row])


if __name__ == "__main__":
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    main()
