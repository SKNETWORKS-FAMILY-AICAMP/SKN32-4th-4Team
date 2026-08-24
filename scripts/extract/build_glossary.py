"""용어 정의 구절 색인 만들기 — 약관 원문에서만.

★용어 사전을 **따로 만들지 않는다.**

    용어의 뜻은 약관 안에 있다(「제2조 용어의 정의」·「붙임1_용어의 정의」).
    바깥에서 정의를 가져와 두 벌이 되면, 어긋났을 때 무엇이 맞는지 판단할 근거가 없다.
    여기서 만드는 것은 **사전이 아니라 색인**이다 — 원문 구절이 어디 있는지만 모은다.

★용어→뜻 쌍으로 파싱하지 않는다. **구절을 통째로 남긴다.**

    실측(2026-08-02): 용어 정의표는 PDF 안에서 **테두리 없는 표**라
    pymupdf 의 표 추출이 잡지 못한다(문서 하나에 표 67개를 잡았는데 그중에 없었다).
    본문 텍스트에서는 칸이 무너져 이렇게 나온다.

        용 어  정  의  계약 보험계약 진단계약 계약을 체결하기 위하여 …

    여기서 "계약 = 보험계약" 이라고 **끊어 읽는 것은 추측이다.**
    끊는 규칙을 만들면 그럴듯하게 틀린 정의가 대량으로 만들어진다.
    그래서 끊지 않고, 용어가 나온 자리 주변을 **원문 그대로** 인용한다.
    사람이 보고 판단할 수 있게 하는 것이 지금 할 수 있는 정직한 최선이다.

★두 갈래로 모은다

    clause    조항 제목이 「용어의 정의」이고 본문이 있는 것
    appendix  본문이 "붙임1을 보라"고만 하는 것 — 정의표는 페이지 텍스트에 있다

    실측: 「용어의 정의」 조항 3,052개 중 **1,431개(47%)가 참조뿐**이다.
    조항만 색인하면 절반을 잃는다.

산출물
    data/glossary/passages.jsonl
"""

from __future__ import annotations

import json
import pathlib
import re
import sys

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_STRUCT = _ROOT / "data" / "structured"
_EXTRACT = _ROOT / "data" / "extracted"
_OUT = _ROOT / "data" / "glossary" / "passages.jsonl"

#: 「용어의 정의」·「용어의 뜻」
_TITLE = re.compile(r"용어의?\s*(정의|뜻)")
#: 본문이 다른 곳을 가리키기만 하는가
_POINTER = re.compile(r"붙임|별표|별첨")
#: 페이지 텍스트에서 정의표가 시작하는 자리.
#:
#: ★처음에 `붙임1_용어의 정의` 하나만 보고 규칙을 만들었더니 **763문서를 놓쳤다.**
#:   실제로는 `[붙임1] 용어의 정의` 266건 · `<붙임> 용어의 정의` 228건처럼
#:   괄호가 끼어 있다. 표본 하나로 규칙을 정하지 않는다(CLAUDE.md §4).
_APPENDIX_HEAD = re.compile(
    r"[\[<(【]?\s*(붙임|별표|별첨)\s*\d*\s*[\]>)】]?\s*[_\-]?\s*용어의?\s*(정의|뜻)"
)

#: 참조뿐인지 가르는 길이. 이보다 짧으면서 붙임을 가리키면 본문이 없다고 본다.
_POINTER_MAX = 300
#: 붙임 정의표를 페이지 텍스트에서 얼마나 떠올 것인가.
#: 실측 중앙값이 2천자대라 넉넉히 잡되, 다음 관/조 제목이 나오면 거기서 끊는다.
_APPENDIX_MAX = 12000
_APPENDIX_STOP = re.compile(r"\n제\s*\d+\s*관\s|\n부\s*칙\s*\n")
#: 머리말 뒤를 판별할 때 지울 것들(공백·괄호·밑줄).
_PROBE_STRIP = re.compile(r"[\s>\]\)】_\-]")


def _clause_passages(path: pathlib.Path) -> list[dict]:
    doc = json.loads(path.read_text(encoding="utf-8"))
    src = doc.get("source") or {}
    sha = src.get("sha256") or doc.get("sha256") or ""
    insurer = src.get("insurer") or ""
    out: list[dict] = []
    n_pointer = 0
    for c in doc.get("clauses") or []:
        if not _TITLE.search(c.get("title") or ""):
            continue
        text = c.get("text") or ""
        if len(text) < _POINTER_MAX and _POINTER.search(text):
            n_pointer += 1
            continue
        out.append(
            {
                "kind": "clause",
                "sha256": sha,
                "insurer": insurer,
                "qualified_no": c.get("qualified_no") or "",
                "section": c.get("section") or "",
                "title": c.get("title") or "",
                "page_from": c.get("locator", {}).get("page_from") or c.get("page_from") or 0,
                "page_to": c.get("locator", {}).get("page_to") or c.get("page_to") or 0,
                "content_hash": c.get("content_hash") or "",
                "text": text,
            }
        )
    #: ★참조뿐인 조항 수를 **센다.** 조용히 버리면 커버리지가 실제보다 좋아 보인다.
    for o in out:
        o["_pointer_siblings"] = n_pointer
    return out or ([{"_only_pointer": n_pointer, "sha256": sha, "insurer": insurer}] if n_pointer else [])


def _appendix_passage(sha12: str) -> dict | None:
    """붙임 정의표 본문. **가리키는 말과 표 자체를 구분한다.**

    ★처음엔 구분하지 않아 **가리키는 말을 정의표로 잡았다.**

        본문에도 머리말과 똑같은 글자가 나온다.

            <붙임1_용어의 정의>와 같습니다.   ← 가리키는 말. 뒤에 보상내용이 이어진다
            <붙임1_용어의 정의>
            용 어  정  의  계약 보험계약 …    ← 진짜 표

        앞엣것을 잡으면 "상해"를 물었을 때 「보장종목별 보상내용」이 정의랍시고 나온다.
        실측(표본 120문서): 머리말 235회 중 **106회가 가리키는 말**이었다.

    그래서 머리말 **뒤를 본다** — 공백·괄호를 지우고 `용어정의` 로 시작하면 표,
    `와같습니다` 로 시작하면 가리키는 말이다.
    """
    hits = list(_EXTRACT.glob(f"*/s4_*/{sha12}.json"))
    if not hits:
        return None
    doc = json.loads(hits[0].read_text(encoding="utf-8"))
    src = doc.get("source") or {}
    pages = doc.get("pages") or []

    for i, pg in enumerate(pages):
        text = pg.get("text") or ""
        for m in _APPENDIX_HEAD.finditer(text):
            #: 표는 페이지를 넘어가고, 머리말이 페이지 끝에 걸리기도 한다.
            #: 판별에도 이어붙인 것을 쓴다.
            buf = text[m.start():]
            page_to = pg.get("page") or 0
            for nxt in pages[i + 1 : i + 6]:
                if len(buf) >= _APPENDIX_MAX:
                    break
                buf += "\n" + (nxt.get("text") or "")
                page_to = nxt.get("page") or page_to

            probe = _PROBE_STRIP.sub("", buf[m.end() - m.start() :])[:24]
            if not probe.startswith("용어정") and "용어정의" not in probe[:12]:
                #: 가리키는 말이거나 판별 불가 — **정의표라고 하지 않는다.**
                continue

            stop = _APPENDIX_STOP.search(buf, 200)
            if stop:
                buf = buf[: stop.start()]
            return {
                "kind": "appendix",
                "sha256": src.get("sha256") or "",
                "insurer": src.get("insurer") or "",
                "qualified_no": "",
                "section": "",
                "title": m.group(0).strip(),
                "page_from": pg.get("page") or 0,
                "page_to": page_to,
                "content_hash": "",
                "text": buf[:_APPENDIX_MAX],
            }
    return None


def main() -> int:
    files = sorted(_STRUCT.glob("*/s5_*/*.clauses.json"))
    if not files:
        print("s5 조항 산출물이 없습니다. 먼저 전처리를 돌리세요.", file=sys.stderr)
        return 2

    rows: list[dict] = []
    n_clause = n_appendix = n_pointer_only = n_lost = 0
    for p in files:
        got = _clause_passages(p)
        real = [g for g in got if g.get("kind")]

        #: ★참조뿐인 조항이 있는지 **먼저** 본다.
        #:   앞서 표시 필드를 지운 뒤에 검사해 항상 거짓이었고,
        #:   붙임 탐색이 대부분 돌지 않았다.
        pointer_here = any(g.get("_only_pointer") for g in got) or any(
            g.get("_pointer_siblings") for g in got
        )
        for g in real:
            g.pop("_pointer_siblings", None)
        rows.extend(real)
        n_clause += len(real)

        if pointer_here:
            n_pointer_only += 1
            ap = _appendix_passage(p.name.split(".")[0])
            if ap and ap["text"].strip():
                rows.append(ap)
                n_appendix += 1
            else:
                #: ★못 찾은 것을 **센다.** 이것이 용어 설명이 답하지 못하는 범위다.
                n_lost += 1

    _OUT.parent.mkdir(parents=True, exist_ok=True)
    with _OUT.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    meta = {
        "built_from": "s5",
        "documents": len(files),
        "clause_passages": n_clause,
        "appendix_passages": n_appendix,
        "documents_with_pointer_only": n_pointer_only,
        "appendix_not_found": n_lost,
        "note": (
            "용어→뜻 쌍으로 파싱하지 않는다. 정의표가 테두리 없는 표라 "
            "칸을 복원할 수 없고, 끊어 읽으면 그럴듯하게 틀린 정의가 만들어진다. "
            "구절을 원문 그대로 두고 사람이 판단하게 한다."
        ),
    }
    (_OUT.parent / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(meta, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
