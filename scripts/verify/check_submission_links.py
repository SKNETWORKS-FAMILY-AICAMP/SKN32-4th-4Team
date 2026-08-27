"""제출 문서(`docs/submission/`)의 상대 링크가 전부 실존하는지 검사한다.

★왜 필요한가

    제출물은 **링크 허브**다(`05` 가 `05A~05D` 를, `00` 이 전부를 가리킨다).
    링크 하나가 죽으면 심사자는 그 항목을 **제출하지 않은 것으로 본다.**
    문서를 나눈 대가이므로 기계가 검사한다.

★함께 막는 것 — 커머스 화면 재유입

    `docs/screen_walkthrough.html` 은 이 저장소가 쇼핑몰 실습이던 시절 산출물이다
    (주문·장바구니·얼굴인식·화상상담·facebench 18장).
    제출 문서가 이걸 "전체 앱 워크스루"로 링크해 뒀던 적이 있다 — 열면 쇼핑몰이 나온다.
    링크를 뗐지만 **다음 사람이 되풀이하지 않도록** 검사에 넣는다.

사용:
    python -m scripts.verify.check_submission_links          # 실패하면 exit 1
    python -m scripts.verify.check_submission_links --json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parents[2]
SUBMISSION = ROOT / "docs" / "submission"

#: `[텍스트](경로)` — 이미지(`![]()`)도 같은 규칙으로 검사한다.
_LINK = re.compile(r"!?\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")

#: ★제출물에서 링크하면 안 되는 것. 값은 "왜 안 되나".
BANNED: dict[str, str] = {
    "docs/screen_walkthrough.html": (
        "커머스 시절 화면 18장(주문·장바구니·얼굴인식·facebench). "
        "제출 정본은 docs/delivery/storyboard.html 이다."
    ),
}


def _iter_links(md: Path):
    """(줄번호, 원본 링크) 를 흘려보낸다. 코드펜스 안은 건너뛴다."""
    in_fence = False
    for lineno, line in enumerate(md.read_text(encoding="utf-8").splitlines(), 1):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        #: ★취소선으로 감싼 것은 "쓰지 말라"는 표시라 링크가 아니다.
        for raw in _LINK.findall(line):
            yield lineno, raw


def _is_external(target: str) -> bool:
    return bool(urlparse(target).scheme) or target.startswith("//")


def check() -> tuple[list[dict], dict]:
    if not SUBMISSION.is_dir():
        raise SystemExit(f"제출 폴더가 없습니다: {SUBMISSION}")

    problems: list[dict] = []
    checked = external = 0

    for md in sorted(SUBMISSION.glob("*.md")):
        for lineno, raw in _iter_links(md):
            target = raw.split("#", 1)[0]
            if not target:
                continue                      # 같은 문서 안의 앵커
            if _is_external(target):
                external += 1
                continue
            checked += 1
            resolved = (md.parent / unquote(target)).resolve()
            where = f"{md.relative_to(ROOT).as_posix()}:{lineno}"

            if not resolved.exists():
                problems.append({
                    "kind": "missing",
                    "where": where,
                    "target": target,
                    "detail": "파일이 없습니다",
                })
                continue

            try:
                rel = resolved.relative_to(ROOT).as_posix()
            except ValueError:
                problems.append({
                    "kind": "outside_repo",
                    "where": where,
                    "target": target,
                    "detail": "저장소 밖을 가리킵니다",
                })
                continue

            if rel in BANNED:
                problems.append({
                    "kind": "banned",
                    "where": where,
                    "target": target,
                    "detail": BANNED[rel],
                })

    return problems, {
        "documents": len(list(SUBMISSION.glob("*.md"))),
        "relative_links_checked": checked,
        "external_links_skipped": external,
        "problems": len(problems),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="제출 문서 링크 검사")
    ap.add_argument("--json", action="store_true", help="결과를 JSON 으로 출력")
    args = ap.parse_args()

    problems, summary = check()

    if args.json:
        print(json.dumps({"summary": summary, "problems": problems},
                         ensure_ascii=False, indent=1))
        return 1 if problems else 0

    print(f"문서 {summary['documents']}개 · 상대 링크 {summary['relative_links_checked']}개 검사 "
          f"(외부 {summary['external_links_skipped']}개 제외)")
    if not problems:
        print("문제 없음")
        return 0

    #: ★몇 건인지 먼저 말한다. 목록만 쏟으면 규모가 안 보인다.
    print(f"\n★문제 {len(problems)}건\n")
    for p in problems:
        print(f"  [{p['kind']}] {p['where']}")
        print(f"      → {p['target']}")
        print(f"        {p['detail']}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
