"""URL 경로에 박힌 판매개시일을 매니페스트에 채운다.

★어떻게 찾았나

    삼성생명 약관 URL 이 이렇게 생겼다.

        https://pcms.samsunglife.com/uploadDir/doc/2021/0401/LP0525006/301/….pdf
                                              ↑↑↑↑ ↑↑↑↑
                                              연     월일

    사이트 목록에서 판매기간을 직접 읽어 확정한 **40건과 대조하니 40/40 일치**했다.
    즉 이 경로 날짜가 곧 판매개시일이다.

★왜 이 방법이 필요했나

    삼성생명 목록의 약관 링크는 `href="javascript:void(0)"` 라 URL 이 없다.
    상품명으로 맞추려 했더니 **같은 상품명이 여러 기간으로 나와**(개정판) 177건이
    모호해 채울 수 없었다. URL 은 파일마다 유일하므로 그 문제가 없다.

    PDF 안에서 찾는 길도 막혀 있었다.
      - 1페이지가 **비어 있다**(표지가 이미지)
      - 본문 날짜는 `2007.6.28` 같은 **법령 인용일**이다 — 쓰면 지어내는 것이다
      - 파일명에도 없다

★지어내지 않는다

    채운 행에 근거를 남긴다: `date_source="url_path"`, `date_confidence="exact"`.
    **`exact` 로 두는 근거는 위의 40/40 대조**다. 검증 없이 이 등급을 주지 않는다.

실행:
    python -m scripts.crawl.fill_dates_from_url --dry-run
    python -m scripts.crawl.fill_dates_from_url
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from app.core.errors import InfraError

_ROOT = Path(__file__).resolve().parents[2]
_MANIFESTS = _ROOT / "data" / "raw" / "manifests"

#: 사이트별 URL 날짜 패턴. **검증한 것만 넣는다.**
#: (slug, 정규식, 설명, 검증 근거)
URL_DATE_PATTERNS: dict[str, tuple[re.Pattern[str], str]] = {
    #: 40건을 사이트 목록 판매기간과 대조해 40/40 일치 확인(2026-08-01).
    "samsunglife": (re.compile(r"/doc/(20\d{2})/(\d{2})(\d{2})/"), "/doc/YYYY/MMDD/"),
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", help="한 곳만. 생략하면 패턴이 있는 전부")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    slugs = [args.site] if args.site else sorted(URL_DATE_PATTERNS)
    total = 0
    for slug in slugs:
        entry = URL_DATE_PATTERNS.get(slug)
        if entry is None:
            raise InfraError(
                f"{slug} 의 URL 날짜 패턴이 없습니다. "
                "패턴을 넣기 전에 반드시 확정된 날짜와 대조해 검증하세요."
            )
        pat, desc = entry
        m = _MANIFESTS / f"{slug}.jsonl"
        if not m.exists():
            raise InfraError(f"매니페스트가 없습니다: {m}")
        rows = [
            json.loads(line)
            for line in m.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

        filled = conflict = nourl = 0
        for r in rows:
            url = (r.get("url") or "").strip()
            hit = pat.search(url) if url else None
            if hit is None:
                if not (r.get("sale_start") or "").strip():
                    nourl += 1
                continue
            guess = f"{hit.group(1)}{hit.group(2)}{hit.group(3)}"
            cur = (r.get("sale_start") or "").strip()
            if cur:
                #: ★이미 값이 있는데 다르면 **덮지 않는다.** 사이트가 더 믿을 만하다.
                if cur != guess:
                    conflict += 1
                continue
            r["sale_start"] = guess
            r["date_source"] = "url_path"
            r["date_confidence"] = "exact"
            filled += 1

        total += filled
        print(f"  {slug:<14} {desc:<18} 채움 {filled:>4} / 기존값과 불일치 {conflict:>3} / "
              f"URL 없어 못함 {nourl:>3}")
        if not args.dry_run and filled:
            tmp = m.with_suffix(".jsonl.tmp")
            tmp.write_text(
                "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
                encoding="utf-8",
            )
            tmp.replace(m)

    print(f"\n합계 {total}행 채움")
    if args.dry_run:
        print("(dry-run: 아무것도 쓰지 않았습니다.)")


if __name__ == "__main__":
    import sys

    sys.path.insert(0, str(_ROOT))
    main()
