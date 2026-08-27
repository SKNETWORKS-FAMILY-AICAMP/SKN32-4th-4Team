"""문서 종류를 판별해 표시하고, 판정 대상이 아닌 것은 **따로 옮긴다.**

★왜 필요한가 — 약관이 아닌 것이 12% 섞여 있었다

    처음부터 **약관만** 받기로 했는데 실측하니 203건(12%)이 사업방법서·상품요약서였다.

        노후실손의료비보험 중지 및 재개 특별약관
        【사업방법서 별지】                       ← 2쪽에 이렇게 적혀 있다
        1. 보험의 종류 : 장기손해보험 / 제도성 특별약관

    ★1쪽만 봐서는 못 찾는다. 1쪽은 제목만 있고 표시는 **2쪽**에 있다.
      처음에 1쪽만 훑어 21건으로 봤는데, 3쪽까지 보니 203건이었다.

    이게 왜 위험한가 — 사업방법서는 **회사 내부 업무 기준**이지 계약 내용이 아니다.
    이걸 근거로 "약관 제○조에 따르면" 이라고 답하면 **틀린 근거**를 대는 것이다.

★여행 실손도 대상이 아니다

    다른 어댑터들은 이미 여행 실손을 뺀다(`_TRAVEL_HINTS`).
    "여행 실손은 세대 구분 대상이 아니고 우리 코호트와 성격이 다르다."
    그런데 **메리츠·삼성생명은 브라우저 수집이라 그 필터가 안 걸렸다.**

★지우지 않는다 — 옮긴다

    삭제는 되돌릴 수 없고, 판별이 오탐일 수 있다.
    `data/raw/excluded/{사유}/` 로 옮기고 매니페스트에 `excluded_reason` 을 남긴다.
    나중에 오탐으로 밝혀지면 되돌릴 수 있다.

실행:
    python -m scripts.crawl.classify_documents --dry-run
    python -m scripts.crawl.classify_documents
"""

from __future__ import annotations

import argparse
import collections
import json
import re
import shutil
from pathlib import Path

import fitz

from app.core.errors import InfraError

_ROOT = Path(__file__).resolve().parents[2]
_MANIFESTS = _ROOT / "data" / "raw" / "manifests"
_RAW = _ROOT / "data" / "raw" / "insurance_terms"
_EXCLUDED = _ROOT / "data" / "raw" / "excluded"

#: ★앞 3쪽을 본다. 1쪽은 제목만 있는 문서가 많다(실측).
_HEAD_PAGES = 3
_HEAD_CHARS = 1500

#: ★약관이 아님을 스스로 밝히는 **표제**만 인정한다.
#:
#:   처음엔 `사업방법서|상품요약서` 를 아무 데서나 찾았더니 **오탐이 쏟아졌다**(실측).
#:
#:     · 205쪽·138쪽짜리 **정상 약관**의 목차에 항목으로 들어 있었다
#:         "Ⅰ. 무배당현대닥터코리아의료보험…상품요약서∙∙∙4"
#:         "상품공시실에서도 보험약관과 상품요약서를 조회하실 수 있습니다"
#:     · 본문에서 **인용**만 한 것도 있었다
#:         "할인율은 사업방법서 별지에 따릅니다"
#:
#:   그래서 **줄 전체가 표제인 것**만 본다. 표제는 한 줄에 그것만 있다.
#:
#:       【사업방법서 별지】       ← 이건 표제
#:       …사업방법서 별지에 따릅니다  ← 이건 인용
_NOT_TERMS = [
    (
        "사업방법서",
        re.compile(r"^\s*[【\[]?\s*사업방법서\s*(?:별지)?\s*[】\]]?\s*$", re.MULTILINE),
    ),
    (
        "상품요약서",
        re.compile(r"^\s*[【\[]?\s*상품\s*요약서\s*[】\]]?\s*$", re.MULTILINE),
    ),
]

#: 여행 실손 — 다른 어댑터의 `_TRAVEL_HINTS` 와 같은 기준을 쓴다.
_TRAVEL = re.compile(r"해외여행|국내여행|여행자|해외장기체류|글로벌케어|여행카드|유학생")

#: ★`실손` 이 붙었지만 **실손의료보험이 아닌 것**.
#:
#:   세대 판정에서 `product_line=unknown` 184건이 나와 열어 보니 전부 이것이었다.
#:
#:       실손·비례보상 주택 풍수해·지진재해보험(Ⅲ)      ← 주택 재해보험
#:       실손보상 온실 풍수해보험(Ⅴ)                   ← 농업시설 보험
#:       KB간편가입아파트화재보험(실손형)                ← 화재보험
#:       자동차사고 변호사선임비용…(실손) 특별약관         ← 자동차 특약
#:
#:   여기서 `실손` 은 **"실제 손해를 보상한다"는 방식**을 뜻하지 의료비 실손이 아니다.
#:   수집 필터가 `실손` 키워드만 봐서 딸려 들어왔다.
#:   이걸 판정 근거에 두면 의료비 질문에 풍수해보험 조항을 들이대게 된다.
_NOT_MEDICAL = re.compile(
    r"풍수해|지진재해|온실|농작물|가축|화재보험|운전자|변호사선임|골프|레저|자동차사고"
)


def classify(pdf: Path, name: str) -> tuple[str, str]:
    """`(doc_type, excluded_reason)`. 정상이면 `("약관", "")`."""
    #: ★이름만으로 판정한다 — 본문에는 '해외여행'·'화재' 가 인용될 수 있다.
    if _NOT_MEDICAL.search(name):
        return "약관", "비의료실손"
    if _TRAVEL.search(name):
        return "약관", "여행실손"
    try:
        doc = fitz.open(str(pdf))
        try:
            head = "".join(
                doc[i].get_text() for i in range(min(_HEAD_PAGES, doc.page_count))
            )[:_HEAD_CHARS]
        finally:
            doc.close()
    except Exception:  # noqa: BLE001
        return "약관", ""
    for label, pat in _NOT_TERMS:
        if pat.search(head):
            return label, label
    return "약관", ""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not _MANIFESTS.exists():
        raise InfraError(f"매니페스트 폴더가 없습니다: {_MANIFESTS}")

    stats = collections.Counter()
    moved = 0
    #: 같은 파일(sha)을 여러 번 열지 않는다.
    cache: dict[str, tuple[str, str]] = {}

    for m in sorted(_MANIFESTS.glob("*.jsonl")):
        rows = [
            json.loads(line)
            for line in m.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        changed = False
        for r in rows:
            sha = r.get("sha256", "")
            saved = r.get("saved_as", "")
            pdf = _ROOT / saved
            if sha not in cache:
                if not pdf.exists():
                    cache[sha] = ("약관", "")
                else:
                    cache[sha] = classify(
                        pdf, r.get("product_name") or r.get("original_name") or Path(saved).name
                    )
            doc_type, reason = cache[sha]
            if r.get("doc_type") == doc_type and r.get("excluded_reason", "") == reason:
                continue
            r["doc_type"] = doc_type
            r["excluded_reason"] = reason
            changed = True
            stats[reason or "약관"] += 1

            #: 판정 대상이 아니면 파일을 옮긴다.
            if reason and pdf.exists():
                dest_dir = _EXCLUDED / reason / m.stem
                dest = dest_dir / pdf.name
                if not args.dry_run:
                    dest_dir.mkdir(parents=True, exist_ok=True)
                    if not dest.exists():
                        shutil.move(str(pdf), str(dest))
                    moved += 1
                r["saved_as"] = str(dest.relative_to(_ROOT))

        if changed and not args.dry_run:
            tmp = m.with_suffix(".jsonl.tmp")
            tmp.write_text(
                "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
                encoding="utf-8",
            )
            tmp.replace(m)

    print("문서 종류 판정(행 기준):")
    for k, v in stats.most_common():
        print(f"  {k:<12}{v:>5}행")
    print(f"\n옮긴 파일 {moved}개 → data/raw/excluded/<사유>/<보험사>/")
    if args.dry_run:
        print("(dry-run: 아무것도 옮기거나 쓰지 않았습니다.)")


if __name__ == "__main__":
    import sys

    sys.path.insert(0, str(_ROOT))
    main()
