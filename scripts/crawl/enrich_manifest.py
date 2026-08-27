"""매니페스트 보강 — 복구본에 빠진 판매기간·상품명을 카탈로그로 채운다.

★왜 필요한가

    기록이 두 번 유실됐고(디스크 풀 / 배치 중간 사망) 그때마다 파일명으로 복구했다.
    파일명에서는 **보험사·sha·상품명**만 나온다. **판매기간이 없다.**

    판매기간이 없으면 **버전 매칭을 할 수 없다.**
    사용자의 계약 시점에 어느 약관이 적용되는지 정하는 것이 실손 판정의 전제인데,
    그 열쇠가 판매개시일·종료일이다.

    실측: 1,835행 중 **612행(33%)** 이 판매기간 없는 복구본이었다.

★어떻게 채우나

    `data/catalog/*.jsonl` 에 어댑터가 저장해 둔 카탈로그가 있다.
    파일명(원본 파일명 부분)으로 조인하면 판매기간이 복원된다.

      삼성화재  `{sha12}_ZPB293050_0_20250101_file1.pdf` → 카탈로그 `pdf_paths`
      DB손보    `{sha12}_약관_30982(14)_….pdf`           → 카탈로그 `files`
      KB손보    `{sha12}_20200101_10108_1_….pdf`         → 파일명 자체에 날짜가 있다

★지어내지 않는다

    카탈로그에서 못 찾으면 **비워 둔다.** 추정으로 날짜를 넣으면
    "이 계약에는 이 약관" 이라는 판정이 조용히 틀리게 된다.
    채운 행에는 `enriched=true` 를 남겨 원래 기록과 구분한다.

실행:
    python -m scripts.crawl.enrich_manifest --dry-run
    python -m scripts.crawl.enrich_manifest
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from app.core.errors import InfraError

_ROOT = Path(__file__).resolve().parents[2]
_MANIFESTS = _ROOT / "data" / "raw" / "manifests"
_CATALOG = _ROOT / "data" / "catalog"


def _latest(pattern: str) -> Path | None:
    hits = sorted(_CATALOG.glob(pattern))
    return hits[-1] if hits else None


def _rows(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _index_samsungfire() -> dict[str, dict]:
    p = _latest("*_samsungfire_products.jsonl")
    if not p:
        return {}
    idx: dict[str, dict] = {}
    for r in _rows(p):
        for path in r.get("pdf_paths", []):
            idx[path.rsplit("/", 1)[-1]] = {
                "product_name": r.get("product_name", ""),
                "product_code": r.get("product_code", ""),
                "sale_start": r.get("sale_start", ""),
                "sale_end": r.get("sale_end", ""),
            }
    return idx


def _index_dbins() -> dict[str, dict]:
    p = _latest("*_dbins_products.jsonl")
    if not p:
        return {}
    idx: dict[str, dict] = {}
    for r in _rows(p):
        for _kind, fn in r.get("files", []):
            idx[fn] = {
                "product_name": r.get("product_name", ""),
                "product_code": r.get("product_code", ""),
                "sale_start": r.get("sale_start", ""),
                "sale_end": r.get("sale_end", ""),
            }
    return idx


def _index_myangel() -> dict[str, dict]:
    p = _latest("*_myangel_products.jsonl")
    if not p:
        return {}
    #: 동양생명은 파일명에 상품명이 들어간다. 정규화해서 맞춘다.
    return {
        _norm(r.get("product_name", "")): {
            "product_name": r.get("product_name", ""),
            "product_code": r.get("product_code", ""),
            "sale_start": r.get("sale_start", ""),
            "sale_end": r.get("sale_end", ""),
        }
        for r in _rows(p)
    }


def _index_hyundaimarine() -> dict[str, dict]:
    p = _latest("*_hyundaimarine_products.jsonl")
    if not p:
        return {}
    return {
        _norm(r.get("product_name", "")): {
            "product_name": r.get("product_name", ""),
            "product_code": r.get("terms_file_id", ""),
            "sale_start": r.get("sale_start", ""),
            "sale_end": r.get("sale_end", ""),
        }
        for r in _rows(p)
    }


def _norm(s: str) -> str:
    return re.sub(r"[\s·∙・()]+", "", s or "")


#: 파일명 안에 `YYYYMMDD` 가 있으면 그것이 판매개시일인 어댑터들.
_DATE_IN_NAME = {"kbinsure", "lotteins", "heungkukfire", "heungkuklife"}
_DATE = re.compile(r"_(\d{8})_")

INDEXERS = {
    "samsungfire": _index_samsungfire,
    "dbins": _index_dbins,
    "myangel": _index_myangel,
    "hyundaimarine": _index_hyundaimarine,
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not _MANIFESTS.exists():
        raise InfraError(f"매니페스트 폴더가 없습니다: {_MANIFESTS}")

    total = filled = still = 0
    for m in sorted(_MANIFESTS.glob("*.jsonl")):
        slug = m.stem
        rows = _rows(m)
        need = [r for r in rows if not (r.get("sale_start") or "").strip()]
        if not need:
            continue
        idx = INDEXERS.get(slug, lambda: {})()
        n_fill = 0
        for r in need:
            fname = Path(r.get("saved_as", "")).name
            orig = fname.split("_", 1)[-1] if fname else ""
            info = idx.get(orig)
            if not info and idx:
                #: 상품명 정규화 매칭(동양생명·현대해상)
                info = idx.get(_norm(Path(orig).stem))
            if not info and slug in _DATE_IN_NAME:
                mm = _DATE.search(fname)
                if mm:
                    r["sale_start"] = mm.group(1)
                    r["enriched"] = True
                    n_fill += 1
                    continue
            if not info:
                continue  # ★못 찾으면 비워 둔다. 지어내지 않는다.
            for k in ("product_name", "product_code", "sale_start", "sale_end"):
                if info.get(k) and not (r.get(k) or "").strip():
                    r[k] = info[k]
            r["enriched"] = True
            n_fill += 1

        total += len(need)
        filled += n_fill
        still += len(need) - n_fill
        print(f"  {slug:<15} 판매기간 없음 {len(need):>4} → 채움 {n_fill:>4} / 남음 {len(need) - n_fill:>4}")

        if not args.dry_run and n_fill:
            tmp = m.with_suffix(".jsonl.tmp")
            with tmp.open("w", encoding="utf-8") as f:
                for r in rows:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
            tmp.replace(m)

    print(f"\n합계 {total}행 중 {filled}행 채움 / {still}행 여전히 없음")
    if args.dry_run:
        print("(dry-run: 아무것도 쓰지 않았습니다.)")
    if still:
        print("★남은 것은 카탈로그에 없어 **비워 두었다**. 추정으로 날짜를 넣지 않는다.")


if __name__ == "__main__":
    import sys

    sys.path.insert(0, str(_ROOT))
    main()
