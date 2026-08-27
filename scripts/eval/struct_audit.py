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

#: ★S3(파묻힌 머리) 판정이 `to_clauses.py` 의 머리 판정과 어긋나면 안 된다
#:   (아래 `structure_faults` 안 §9-1 주석 참조) — 정규식을 다시 베끼지 않고
#:   정답 소스를 그대로 가져온다. 순환 없음: `to_clauses.py` 모듈 최상단은
#:   `struct_audit` 를 쓰지 않고, `build()` 함수 **안에서만** 지연 import 한다.
from scripts.extract.to_clauses import (  # noqa: E402
    _ARTICLE, _REF_TAIL, _ANNEX_HEAD, _ANNEX_REF_TAIL,
)

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
#:
#: ★★2026-08-26 population A 사람 표본검수(44건) 중 발견 — 위 자체 규칙은
#:   "줄머리"만 보고 **닫는 괄호·조사 참조를 안 본다.** 실측(전량 스캔 기준
#:   S4 게이트 모집단 **4건 전부**, samsungfire "제7조(보험금의 지급절차)"):
#:
#:     ④ … 그 다음날부터 지급일까지의 기간에 대하여
#:     <붙임2>에서 정한 이율로 계산한 금액을 보험금에 더하여 지급합니다.
#:
#:   `<붙임2>에서 정한`은 **정상 문장 중간의 인용**인데(줄바꿈으로 `<붙임2>`가
#:   줄머리에 온 것뿐), 이 정규식은 `<붙임`까지만 보고 뒤의 `에서 정한`을
#:   확인 안 해서 진짜 부록 시작으로 오판했다(4/4, 표본 전수 오탐).
#:   ★`to_clauses.py`의 `_ANNEX_HEAD`/`_ANNEX_REF_TAIL`은 **이미 이 정확한
#:     사례를 예시로 들며 고쳐져 있다**(주석에 "<붙임2>에서 정한 이율" 그대로
#:     나온다) — 닫는 괄호까지 확인하고 참조 꼬리를 거른다. S3(파묻힌 머리)가
#:     `_ARTICLE`/`_REF_TAIL`을 재사용하는 것과 같은 이유로, 여기서도 별도
#:     느슨한 규칙을 두지 않고 **정답 소스를 그대로 가져온다**(순환 없음,
#:     위 import 주석 참조). Codex 설계검토(2026-08-26) 교차확인 완료 —
#:     단, 표본이 4건(사실상 같은 조항 1종)뿐이라 "이 신호가 앞으로도
#:     오탐 없다"는 보장은 아니다. 전량 재측정으로 확인한다.
#: 마커 뒤에 이만큼 이상 남아 있어야 "삼켰다"고 본다. 제목 한 줄만 있는 건 경계 표시다.
ANNEX_MIN_TAIL = 300

#: ★★2026-08-26 S3 잔여 26건 Codex 전수분석(디버그 리포트 참조) — "정한→정하는"
#:   외에 새 오탐 2종을 발견했다. `_ARTICLE`/`_REF_TAIL`(=heads 판정, 조항
#:   경계 자체)은 **안 건드린다** — 이 두 패턴은 S3 가 최종 블록을 다시 훑을
#:   때만 생기는 **감사 전용 오탐**이라, S3 게이트 정밀도만 좁게 고친다
#:   (Codex 설계, 코드는 그대로 실행 안 하고 검토만 받은 뒤 이 세션에서 구현).
#:
#:   ① 법령 나열 항목("가."~"하.") 안에서 "「형법」... 및\n제281조(제목)
#:      (한정문)의 죄" 처럼 조 번호 뒤에 **괄호 한정문 + "의 죄"로 그 줄이
#:      끝나는** 형법 조문 열거(실측 dbins 8건).
#:   ② "약관요약서" 부(section) 안에서 "숫자\n항목명\n제N조(제목),"처럼
#:      **표 행 다음에 조 인용이 쉼표로 끝나고 그 뒤 본문이 아예 없는** 참조
#:      (실측 meritzfire 9건).
#:
#:   ★`[가-하]` 같은 유니코드 범위는 안 쓴다 — 그 사이 수많은 무관한 한글
#:     음절을 다 받는다. 명시적으로 나열한다.
_LAW_CRIME_RIDER_EOL = re.compile(
    r"\A[ \t]*"
    r"(?:\([^()\n]{2,80}\)|（[^（）\n]{2,80}）)"
    r"[ \t]*의[ \t]*죄[ \t]*"
    r"(?:\r?\n|\Z)"
)
_LIST_ITEM_HEAD = re.compile(
    r"^[ \t]*[가나다라마바사아자차카타파하]\.[ \t]*",
    re.MULTILINE,
)


def _is_law_list_rider_reference(text: str, m: re.Match) -> bool:
    """이 `_ARTICLE` 매치가 "가.~하." 법령 나열 항목 안의 형법 조문 열거인가."""
    prefix = text[:m.start()]
    starts = list(_LIST_ITEM_HEAD.finditer(prefix))
    if not starts:
        return False
    current_item = prefix[starts[-1].start():]
    return (
        "「형법」" in current_item
        and re.search(r"및[ \t]*\r?\n?[ \t]*\Z", current_item) is not None
        and _LAW_CRIME_RIDER_EOL.match(text[m.end():]) is not None
    )


#: 약관요약서 표 행: "숫자\n항목명\n" 다음에 이 후보가 온다.
_SUMMARY_ROW_PREFIX = re.compile(
    r"(?:\A|\r?\n)"
    r"[ \t]*\d{1,2}[ \t]*\r?\n"
    r"[ \t]*[^\r\n,，、]{2,30}[ \t]*\r?\n"
    r"[ \t]*\Z"
)
#: ★12자 슬라이스가 아니라 **남은 텍스트 전체**를 본다 — 12자로 자르면
#:   "뒤에 본문이 있는데 우연히 12자 안에서 끝난 것"과 "진짜 블록 끝"을
#:   못 가른다(Codex 지적).
_SUMMARY_REF_AT_BLOCK_END = re.compile(r"\A[ \t]*[,，、][ \t\r\n]*\Z")


def _is_summary_terminal_reference(text: str, m: re.Match, section: str | None) -> bool:
    """이 `_ARTICLE` 매치가 "약관요약서" 표의, 쉼표로 끝나고 본문이 없는 참조인가."""
    if re.sub(r"\s+", "", section or "") != "약관요약서":
        return False
    if not (m.group(3) or "").strip():
        return False
    return (
        _SUMMARY_ROW_PREFIX.search(text[:m.start()]) is not None
        and _SUMMARY_REF_AT_BLOCK_END.match(text[m.end():]) is not None
    )


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

    blocks 원소: {no: int|None, kind: str, title: str, text: str, section: str?}
      kind — 번호 체계(`article` / `numbered`). ★섞어서 비교하면 안 된다.
             `제5조` 다음 `4-1.` 을 역행으로 세면 거짓 경고가 쏟아진다(코덱스 지적).
      section — 부(部)/특약 이름. **있으면** kind 와 함께 시퀀스를 가른다(아래 §S1 참조).
                없는 호출부(과거 v5 등)는 생략 가능 — 전부 같은 부로 보고 예전과 같이 동작한다.
    """
    n = len(blocks)
    if not n:
        return {}
    aba = gap = embedded = annex = 0
    #: ★어느 **조항**이 걸렸는지 기록한다. 문서 단위로만 세면
    #:   결함 4개 때문에 문서의 조항 155개를 통째로 버리게 된다
    #:   (실측: 문서 게이트 897조항 0.42% → 조항 게이트 168,523조항 93.95%).
    gated: set[int] = set()
    #: ★★번호 체계뿐 아니라 **부(部)도 같이 갈라야 한다**(S1/S2 원인규명 리포트 §9-1).
    #:
    #:   고치기 전: `by_kind`가 `kind` 만으로 시퀀스를 묶어서, 새 특약이 시작해
    #:   번호가 `제1조`로 재시작하면 **직전 특약의 "제N조"와 부딪혀 A-B-A로 오판**했다.
    #:   실측(S1 리포트 §3): A-B-A 4,613건 중 **87.2%가 셋 다 진짜 헤더** — 즉
    #:   `제4→제5→제4`가 아니라 `(특약X)제4→제5→(특약Y)제4`처럼 **부가 바뀐 자연스러운
    #:   재시작**을 재진입으로 잘못 센 것이었다.
    #:   `section` 이 없는 옛 호출부(`load_v5`)는 전부 `None`이라 한 그룹으로 뭉쳐
    #:   **예전과 똑같이** 동작한다 — 이 변경으로 새로 깨지는 호출부가 없다.
    by_kind: dict[tuple[str, str | None], list[tuple[int, int]]] = collections.defaultdict(list)
    for i, b in enumerate(blocks):
        if isinstance(b.get("no"), int):
            by_kind[(b.get("kind", "article"), b.get("section"))].append((i, b["no"]))

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
        #: ★★자기참조·법령인용은 "파묻힌 머리"가 아니다(S1/S2 원인규명 §9-1 재조사
        #:   중 발견). `제3조(전환후계약의 보장개시일) 제2항에도 불구하고 …` 처럼
        #:   자기 조를 도로 인용하는 문장은 `to_clauses._ARTICLE` 의 머리 탐지에서
        #:   `_REF_TAIL` 로 이미 걸러진다(제목이 있어도, §9-1 수정). 그런데 이 S3
        #:   검사가 **옛 단순 정규식**(제목 캡처·참조꼬리 판단 없이 "제N조(" 만
        #:   보는 것)을 그대로 쓰고 있어서, `heads` 에서 뺀 그 문장이 부모 조 본문
        #:   안에 그대로 남으면 "파묻힌 머리"로 다시 걸렸다. 실측(전량 재빌드):
        #:   `S3_embedded_header` 가 117 → 6,650 으로 튀었는데, 표본 4건 전부
        #:   법령 인용·자기참조였다(진짜 파묻힌 조항 0건) — `to_clauses.py` 의
        #:   `heads` 판정과 **어긋나 있었다**(모듈 상단 주석 "두 곳이 어긋나면
        #:   감사와 산출물이 다른 말을 한다"가 정확히 이 상황).
        #:   ★그래서 같은 판정(`_ARTICLE` + `_REF_TAIL` + 중첩괄호 예외)을 그대로
        #:     가져와 쓴다 — 정규식을 다시 베끼지 않고 **정답 소스를 그대로 import**
        #:     한다(모듈 순환 없음: `to_clauses.py` 모듈 최상단은 `struct_audit`
        #:     를 안 쓰고, `build()` 안에서만 지연 import 한다).
        n_emb = 0
        for m in _ARTICLE.finditer(tail):
            title = m.group(3) or ""
            title_truncated = "(" in title or "（" in title
            if not title_truncated and _REF_TAIL.match(tail[m.end():m.end() + 12]):
                continue  # 자기참조·법령인용 — 파묻힌 머리가 아니다
            if not title_truncated and _is_law_list_rider_reference(tail, m):
                continue  # 법령 나열 항목 안의 형법 조문 열거 — S3 잔여26건 §1
            if not title_truncated and _is_summary_terminal_reference(tail, m, b.get("section")):
                continue  # 약관요약서 표의 쉼표종결 참조 — S3 잔여26건 §2
            n_emb += 1
        if n_emb:
            embedded += n_emb
            #: ★삼킨 조항(carrier)은 끈다. 삼켜진 조항은 **복구되지 않는다** —
            #:   별도 ordinal 로 존재하지 않기 때문이다(코덱스). 아래 목록에 남긴다.
            gated.add(bi)
        #: 부록 흡수 — 붙임·별표·분류표가 **줄머리에서** 시작하고
        #:   그 뒤로 본문이 이어지면 그 조가 부록을 삼킨 것이다.
        #:   ★★위 `_ANNEX_HEAD`/`_ANNEX_REF_TAIL` import 주석 참조 —
        #:     참조 꼬리(`에서 정한` 등)면 부록 시작이 아니다.
        for m in _ANNEX_HEAD.finditer(body):
            tail = body[m.end():m.end() + 12]
            if _ANNEX_REF_TAIL.match(tail):
                continue
            if len(body) - m.start() >= ANNEX_MIN_TAIL:
                annex += 1
                gated.add(bi)
            break

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
