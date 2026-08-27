"""정규화 결과의 `evidence_ref.locator` 가 실제 원문에 있는지 대조한다.

★파일럿 20건 검증에서 배운 것 — 처음엔 이 검증 없이 코덱스 자기보고만 믿을 뻔했다.
  25건 "경계" 판정이 전부 같은 템플릿 문장이었던 사고가 있었고(2026-08-11),
  이번엔 자기보고와 별개로 **원문을 직접 대조**해야 한다는 게 핵심이었다.

★주의할 함정 둘 (파일럿에서 실제로 걸렸다) —
  1. 금감원 HTML 은 한글 자간에 공백이 섞여 렌더링된다(`KCD ( 제 5 차 )`).
     공백을 제거하고 비교해야 한다.
  2. HTML 엔티티(`&rsquo;`, `&middot;` 등)를 안 풀면 실제로 있는 문장도 "없음"으로 나온다.
     `html.unescape()` 를 반드시 거친다.
  이 둘을 안 하면 "지어냈다"는 오판이 나온다 — case_110 이 그 예였다(사실은 정확했다).

사용:
    python -m scripts.legal.verify_normalized --file data/legal/normalized_115.jsonl
"""

from __future__ import annotations

import argparse
import html
import json
import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
RAW = _ROOT / "data" / "legal" / "raw"


def _norm(s: str | None) -> str:
    return re.sub(r"\s+", "", html.unescape(s or ""))


def _prec_text_index(case_ids: set[str]) -> dict[str, str]:
    """법원 사건번호별 원문을 한 번의 bodies 순회로 만든다.

    같은 사건번호가 여러 파일에 있으면 기존 순차 조회와 같게 처음 만난 본문을 쓴다.
    요청한 사건을 모두 찾으면 이후 파일은 읽지 않는다.
    """
    pending = set(case_ids)
    index: dict[str, str] = {}
    if not pending:
        return index

    for p in (RAW / "bodies").glob("*.json"):
        d = json.loads(p.read_text(encoding="utf-8"))
        if "Law" in d:
            continue
        b = d.get("PrecService", d)
        case_id = b.get("사건번호")
        if case_id in pending:
            index[case_id] = re.sub(
                r"<[^>]+>", " ",
                " ".join(str(b.get(k) or "") for k in ("판시사항", "판결요지", "판례내용")),
            )
            pending.remove(case_id)
            if not pending:
                break
    return index


def _full_fss_text(fid: str) -> str:
    text = ""
    h = RAW / "fss" / f"{fid}.html"
    if h.exists():
        raw = h.read_text(encoding="utf-8", errors="replace")
        raw = re.sub(r"<script.*?</script>|<style.*?</style>", "", raw, flags=re.S)
        text += re.sub(r"<[^>]+>", " ", raw)
    td = RAW / "fss" / "text"
    if td.is_dir():
        for p in td.glob(f"{fid}*.txt"):
            text += " " + p.read_text(encoding="utf-8")
            break
    return text


def verify(path: Path) -> dict:
    rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    # 아래 cache의 기존 의미를 보존한다. 같은 id가 출처를 달리해 반복되면 첫 행의
    # 출처만 실제 조회되므로, 인덱스도 첫 행이 court인 id만 준비한다.
    first_source_by_id: dict[str, str] = {}
    for r in rows:
        first_source_by_id.setdefault(r["case"]["id"], r["case"]["source"])
    court_ids = {
        cid for cid, source in first_source_by_id.items() if source == "court"
    }
    court_texts = _prec_text_index(court_ids)
    cache: dict[str, str] = {}
    total = ok = 0
    unresolved: list[tuple[str, str]] = []
    missing_source: list[str] = []

    for r in rows:
        cid = r["case"]["id"]
        if cid not in cache:
            src = (court_texts.get(cid) if r["case"]["source"] == "court"
                   else _full_fss_text(cid))
            if src is None:
                missing_source.append(cid)
                src = ""
            cache[cid] = _norm(src)
        text_n = cache[cid]

        for group in (r.get("facts", []), r.get("holdings", [])):
            for item in group:
                loc = item.get("evidence_ref", {}).get("locator", "")
                if not loc:
                    continue
                total += 1
                # "..."/"…" 이전까지, 공백 제거 첫 20자만 본다 — 요약형 locator 허용
                key = _norm(loc.split("...")[0].split("…")[0])[:20]
                if key and key in text_n:
                    ok += 1
                else:
                    unresolved.append((cid, loc[:60]))

    return {
        "총_레코드": len(rows),
        "검사_항목": total,
        "원문에서_확인됨": ok,
        "미확인": len(unresolved),
        "미확인_목록": unresolved,
        "원문_자체를_못_찾은_사건": missing_source,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", required=True)
    a = ap.parse_args()
    result = verify(_ROOT / a.file)
    slim = {k: v for k, v in result.items() if k not in ("미확인_목록",)}
    print(json.dumps(slim, ensure_ascii=False, indent=2))
    if result["미확인"]:
        print(f"\n★미확인 {result['미확인']}건 — 자간공백/엔티티 오탐일 수 있으니 직접 원문을 다시 봐라:")
        for cid, loc in result["미확인_목록"]:
            print(f"  [{cid}] '{loc}'")


if __name__ == "__main__":
    main()
