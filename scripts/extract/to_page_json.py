"""약관 PDF → 페이지별 JSON 변환.

★왜 이 형식인가

    RAG 에 쓸 것은 텍스트지만, 실손 약관은 **보장한도·자기부담금·갱신주기가 표에 있다**.
    평문만 뽑으면 **셀 경계가 사라져** 어느 값이 어느 담보에 속하는지가 구조로 보장되지 않는다.
    ※단, `find_tables()` 결과가 원 표를 **정확히 복원한다는 뜻은 아니다** — 병합 헤더 분해,
      빈 행 삽입 등이 실측에서 확인됐다. "평문보다 낫다"까지가 검증된 범위다.

    그래서 페이지 단위로 **텍스트 + 표(셀 배열) + 페이지 번호**를 함께 저장한다.
      - 표가 셀 배열로 남는다(원 구조의 완전 복원은 아님 — 위 주석 참조)
      - `locator`(몇 쪽)를 유지해 판정 근거로 인용할 수 있다
      - 실측(무작위 20건): PDF 대비 **합계 11.7% / 중앙값 14.9%**, 파일별 4~29%
        (초기에 보고한 "3%"는 큰 파일만 본 편향 표본이었다 — 정정)

    ★표별 CSV 를 따로 저장하지 않는다
        표는 이미 이 JSON 안에 셀 배열로 들어 있다. CSV 로 또 쓰면 **같은 데이터가 두 벌**이 되어
        어긋났을 때 무엇이 맞는지 판단할 근거가 없어지고, 831건 x 표 수십 개 = 수만 개 파일이 된다.
        사람 검수는 파일이 아니라 **검수 화면에서 표를 렌더링**해 한다.
        엑셀이 필요하면 그때 JSON 에서 뽑으면 된다(파생물은 재생성 가능해야 한다).

★PDF 는 캐시가 아니라 **불변 원본 아카이브**다
    추출 로직은 반드시 바뀐다. 그때 원본이 없으면 다시 받아야 하는데 판매중지 상품 URL 은
    언제 내려갈지 모른다. **재취득 가능성은 가정이고, 가정 위에 비가역적 삭제를 세울 수 없다.**
    이 스크립트는 원본을 읽기만 하고 절대 삭제·이동하지 않는다.

★산출물은 덮어쓰지 않는다
    추출기 버전이 바뀌면 결과가 달라진다. 같은 경로에 덮어쓰면 이전 결과와 그에 기반한
    검수 근거가 사라진다. 그래서 **추출기·스키마 버전을 경로에 넣는다.

실행:
    python -m scripts.extract.to_page_json --sha 968e67f4d3b6        # 한 건
    python -m scripts.extract.to_page_json --insurer samsungfire --limit 5
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import fitz

from app.core.errors import InfraError

_ROOT = Path(__file__).resolve().parents[2]
_RAW = _ROOT / "data" / "raw" / "insurance_terms"
_MANIFEST = _ROOT / "data" / "raw" / "fetch_manifest.jsonl"
_OUT = _ROOT / "data" / "extracted"

#: v2 — 텍스트 정규화(제어문자·사용자영역 글리프 제거). §_clean_text
#: v3 — 서로게이트 제거 + 원자적 쓰기 + 목차·부 경계 수정
#: v4 — 보조 PUA 원문자 복구(①~⑨) + 번호체계 점수 선택 + 사후 검증
SCHEMA_VERSION = "5"
#: 이 값이 바뀌면 같은 PDF 라도 결과가 달라진다. 산출물에 함께 기록한다.
EXTRACTOR = f"pymupdf/{fitz.__doc__.split()[1] if fitz.__doc__ else 'unknown'}"


def _version_tag() -> str:
    """산출물 경로에 넣을 버전 태그. 추출기가 바뀌면 경로가 바뀐다."""
    return f"s{SCHEMA_VERSION}_{EXTRACTOR.replace('/', '-').replace(':', '')}"


def _load_manifest() -> list[dict]:
    """★매니페스트는 보험사별로 나뉘어 있다. `glob` 으로 전부 읽어 빠뜨리지 않는다."""
    from scripts.crawl.split_manifest import load_all

    records = load_all()
    if not records:
        raise InfraError("수집 기록이 없습니다(data/raw/manifests/*.jsonl).")
    return records


#: 지워야 할 것 1 — C0 제어문자(줄바꿈·탭은 남긴다).
#: 지워야 할 것 2 — 사용자 정의 영역(PUA) 글리프.
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_PUA = re.compile(r"[-]")


#: 지워야 할 것 3 — **짝 없는 서로게이트**.
#: ★현대해상 1건이 이것 때문에 `UnicodeEncodeError` 로 죽었다.
#:   PDF 폰트 매핑이 깨져 유니코드로 옮기지 못한 자리다. JSON 으로 쓸 수 없으므로 지운다.
#:   ★더 나쁜 건 그 실패가 **0바이트 파일을 남겨** 다음 단계(조항 구조화)까지
#:     `JSONDecodeError` 로 무너뜨린 것이다. 그래서 쓰기를 원자적으로 바꿨다.
#:     (이 수정을 하던 중 내 편집 스크립트도 같은 방식으로 이 파일을 날렸다. git 으로 복구했다.)
_SURROGATE = re.compile("[\ud800-\udfff]")

#: ★항 번호 ①②③ 이 **보조 사용자영역**(U+F0000~) 문자로 들어와 있다.
#:
#:   실측: `U+F02B1` 221회 · `U+F02B2` 104회 … `U+F02B9` 까지 순서대로.
#:   표본 250문서 중 **144개(58%)** 에 있다.
#:   BMP 사용자영역(U+E000~U+F8FF)만 지우던 규칙에 안 걸려 본문에 깨진 채 남았다.
#:   그래서 항 번호 정규식 `[①-⑳]` 이 매칭되지 않아 "제N항"을 특정할 수 없었다.
#:
#: ★지우지 않고 되살린다. 다만 **검증된 범위만.**
#:
#:   코덱스가 해당 PDF 를 직접 렌더링해 대조했다.
#:     · `U+F02B1~U+F02B9` 는 화면상 `①~⑨` 로 표시된다
#:     · 같은 문서에 정상 `①~⑨` 도 섞여 있다
#:     · ★`⑩` 은 `U+F02BA` + `U+F02C3` **두 문자가 같은 자리에 겹쳐** 나온다
#:       → `U+F02B0+n` 산술식을 10 이상으로 늘리면 **틀린다**
#:
#:   그래서 1~9 만 매핑하고, 나머지 보조 PUA 는 **지우지 않고 세어 남긴다**
#:   (`stats.unmapped_glyphs`). 지우면 뭘 잃었는지 알 수 없게 된다.
_PUA_CIRCLED = {chr(0xF02B0 + n): chr(0x245F + n) for n in range(1, 10)}
_PUA_SUP = re.compile(r"[\U000F0000-\U000FFFFD\U00100000-\U0010FFFD]")


def _clean_text(s: str) -> tuple[str, int, int]:
    """텍스트 정규화. `(정리된 문자열, 지운 제어문자 수, 지운 PUA 수)`.

    ★왜 필요한가 — **검색이 깨진다**

        추출본을 열어 보니 이랬다(실측).

            '회사는\\x01 다음\\x01 중\\x01 어느\\x01 한\\x01 가지의\\x01 경우에...'

        `\\x01` 이 낱말마다 끼어 있다. 이 상태로는
        `"보험금을 지급하지 않습니다"` 로 검색해도 **안 걸린다.**
        보장 판정에서 면책 조항을 못 찾는다는 뜻이다.

        표본 64건 중 **27건(42%)** 이 오염돼 있었고 `\\x01` 이 279,623회 나왔다.

    ★지워도 되는지 확인했다

        `\\x01` 은 **항상 공백 바로 앞**에 온다(상위 조합 전부 `앞=글자 뒤=' '`).
        즉 공백의 잉여 마커이고, 지우면 원문이 그대로 남는다.
        `\\uf000` 은 줄머리에 오는 **장식 글리프**다(`앞='\\n' 뒤='회'`).

        ★①②③ 같은 원문자는 **PUA 가 아니다**(U+2460~). 지워지지 않는다.

    ★조용히 지우지 않는다

        몇 자를 지웠는지 산출물 `stats.normalized` 에 남긴다.
        나중에 "이 문서 왜 이러지" 할 때 근거가 된다.
    """
    #: 서로게이트를 먼저 지운다 — 남겨 두면 JSON 직렬화 자체가 실패한다.
    if _SURROGATE.search(s):
        s = _SURROGATE.sub("", s)
    #: 검증된 원문자만 되살린다. 나머지 보조 PUA 는 남겨 두고 세기만 한다.
    for src, dst in _PUA_CIRCLED.items():
        if src in s:
            s = s.replace(src, dst)
    n_ctl = len(_CONTROL.findall(s))
    n_pua = len(_PUA.findall(s))
    if n_ctl:
        s = _CONTROL.sub("", s)
    if n_pua:
        s = _PUA.sub("", s)
    return s, n_ctl, n_pua


def _coord_tables(page):
    """좌표 기반 표 복원. **여기서만** import 한다 — 실패해도 나머지가 살게.

    ★`table_coords` 는 `fitz` 의 `page.rotation_matrix`·`get_drawings()` 에 기댄다.
      PyMuPDF 판이 바뀌면 여기가 먼저 깨진다. 그때 페이지 텍스트까지 못 만들면
      전처리가 통째로 멈춘다 — 그래서 호출부에서 잡는다.
    """
    from scripts.extract.table_coords import extract as _tc

    return _tc(page)


def extract(pdf: Path, meta: dict) -> dict:
    """한 건을 페이지별 JSON 구조로 만든다."""
    doc = fitz.open(str(pdf))
    pages: list[dict] = []
    total_tables = 0
    total_coord = 0
    #: 좌표 복원이 실패한 페이지 → 사유. **세어서 남긴다.**
    coord_failed: dict[int, str] = {}
    n_ctl_all = n_pua_all = 0
    #: 되살리지 못한 보조 PUA — **지우지 않고 세어 남긴다.** 뭘 잃었는지 알아야 한다.
    unmapped: dict[str, int] = {}
    for i, page in enumerate(doc):
        text, n_ctl, n_pua = _clean_text(page.get_text())
        for ch in _PUA_SUP.findall(text):
            key = f"U+{ord(ch):05X}"
            unmapped[key] = unmapped.get(key, 0) + 1
        n_ctl_all += n_ctl
        n_pua_all += n_pua
        tables: list[list[list[str]]] = []
        try:
            for t in page.find_tables().tables:
                #: ★표 안의 글자도 같은 오염을 겪는다. 같이 정리한다.
                cells = [
                    [_clean_text("" if c is None else str(c))[0].strip() for c in row]
                    for row in t.extract()
                ]
                if cells:
                    tables.append(cells)
        except Exception:  # noqa: BLE001
            # 표 인식 실패는 페이지 전체 실패가 아니다. 다만 조용히 '표 없음'으로
            # 만들지 않도록 아래 table_extraction_failed 로 표시한다.
            tables = []
            pages.append(
                {
                    "page": i + 1,
                    "text": text,
                    "tables": [],
                    "tables_coords": [],
                    "table_extraction_failed": True,
                }
            )
            continue
        total_tables += len(tables)
        #: ★★**좌표 기반 표 복원을 함께 싣는다.**
        #:
        #:   `find_tables()` 는 병합 헤더를 못 풀고 rowspan 을 행 순서로 붙인다.
        #:   실측(흥국화재 p109 특정질병 분류표): `find_tables()` 는 **2행 3열**로
        #:   붕괴시킨다 — 실제 22행이다. 그 상태로 텍스트만 읽으면
        #:   질병명↔KCD 짝 정확도가 **0.455** 다(정답셋 66레코드).
        #:   좌표 복원은 같은 정답셋에서 **1.000** 이다.
        #:
        #:   ★그래도 `tables`(find_tables)를 **지우지 않는다.** 두 벌을 나란히 두고
        #:     비교할 수 있어야 한다(CLAUDE.md §1). 좌표 복원이 실패한 페이지가 있고,
        #:     그때 무엇을 잃었는지 알려면 옛 결과가 필요하다.
        #:
        #:   ★`grid` 는 안 싣는다 — `records` 에서 복원되고, 전량이면 수 GB 가 된다.
        #:     대신 실패를 감추지 않도록 `method`·`word_coverage` 를 남긴다.
        coord: list[dict] = []
        try:
            for t in _coord_tables(page):
                if not t.get("records"):
                    continue
                coord.append({
                    #: ★★**표에 신원을 준다.** 같은 표가 조항과 부록에 **두 번**
                    #:   붙을 수 있는데, 신원이 없으면 하류가 그걸 서로 다른 표로 센다.
                    #:   실측: 정답 22레코드에 "실린 레코드 44" 가 그래서 나왔다(코덱스 지적).
                    #:   `(쪽, 패널, 방식)` 이면 한 문서 안에서 유일하고 **결정적**이다.
                    "table_id": f"p{i + 1}-{t.get('panel')}-{t.get('method')}",
                    "method": t.get("method"),
                    "panel": t.get("panel"),
                    "cols": t.get("cols"),
                    "rows": t.get("rows"),
                    "word_coverage": t.get("word_coverage"),
                    #: ★★**판별 신호를 반드시 함께 싣는다.**
                    #:   처음에 이 세 키를 빠뜨려서, `table_signals` 를 만들어 놓고도
                    #:   조항 층에서는 `is_table` 이 항상 `None` 이었다 —
                    #:   신호가 **아무 일도 하지 않고 있었다.** 만든 것과 실린 것은 다르다.
                    "signals": t.get("signals"),
                    "is_table": t.get("is_table"),
                    "reject_why": t.get("reject_why"),
                    "records": t["records"],
                })
        except Exception as exc:  # noqa: BLE001
            #: ★조용히 '표 없음'으로 만들지 않는다(CLAUDE.md §3).
            coord = []
            coord_failed[i + 1] = f"{type(exc).__name__}: {exc}"[:120]
        total_coord += len(coord)
        pages.append({"page": i + 1, "text": text,
                      "tables": tables, "tables_coords": coord})
    n = doc.page_count
    doc.close()

    text_len = sum(len(p["text"]) for p in pages)
    return {
        "schema_version": SCHEMA_VERSION,
        "extractor": EXTRACTOR,
        "extracted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": {
            "insurer": meta.get("insurer", ""),
            "product_name": meta.get("product_name", ""),
            "product_code": meta.get("product_code", ""),
            "sale_start": meta.get("sale_start", ""),
            "sale_end": meta.get("sale_end", ""),
            "url": meta.get("url", ""),
            "sha256": meta.get("sha256", ""),
            "bytes": meta.get("bytes", 0),
        },
        #: ★받았다는 이유로 무엇인지 안다고 하지 않는다. 식별은 별도 단계다.
        "identification": meta.get("identification", "unidentified"),
        "stats": {
            "pages": n,
            "text_length": text_len,
            "tables": total_tables,
            #: ★좌표 복원 표와 그 실패. 합계만 보면 실패가 안 보인다.
            "tables_coords": total_coord,
            "tables_coords_failed_pages": coord_failed,
            #: ★무엇을 지웠는지 남긴다. 조용한 변환은 나중에 원인을 못 찾게 한다.
            "normalized": {
                "control_removed": n_ctl_all,
                "pua_removed": n_pua_all,
                #: 되살린 원문자는 본문에 이미 반영됐다. 못 되살린 것만 여기 남는다.
                #: ★비어 있지 않으면 그 문서는 항 번호가 온전하지 않다는 뜻이다.
                "unmapped_glyphs": dict(sorted(unmapped.items())),
            },
        },
        "pages": pages,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sha", help="sha256 접두사(12자 이상)로 한 건만 변환")
    ap.add_argument("--insurer", help="보험사 슬러그 폴더 (예: samsungfire)")
    ap.add_argument("--limit", type=int, default=1)
    args = ap.parse_args()

    records = _load_manifest()
    by_sha: dict[str, dict] = {}
    for r in records:
        by_sha.setdefault(r["sha256"], r)

    targets: list[dict] = []
    if args.sha:
        hits = [r for s, r in by_sha.items() if s.startswith(args.sha)]
        if not hits:
            raise InfraError(f"매니페스트에서 sha 접두사 '{args.sha}' 를 찾지 못했습니다.")
        targets = hits[:1]
    else:
        slug = args.insurer or "samsungfire"
        targets = [
            r for r in by_sha.values() if f"/{slug}/" in r["saved_as"]
        ][: args.limit]
        if not targets:
            raise InfraError(f"'{slug}' 폴더의 수집 기록을 찾지 못했습니다.")

    _OUT.mkdir(parents=True, exist_ok=True)
    for meta in targets:
        pdf = _ROOT / meta["saved_as"]
        if not pdf.exists():
            print(f"[SKIP] 파일 없음: {meta['saved_as']}")
            continue
        doc = extract(pdf, meta)
        slug = Path(meta["saved_as"]).parent.name
        out_dir = _OUT / slug
        out_dir.mkdir(parents=True, exist_ok=True)
        # ★버전별 경로. 같은 PDF 를 새 추출기로 다시 뽑아도 이전 결과가 남는다.
        ver_dir = out_dir / _version_tag()
        ver_dir.mkdir(parents=True, exist_ok=True)
        out = ver_dir / f"{meta['sha256'][:12]}.json"
        if out.exists():
            print(f"[SKIP] 이미 있음(덮어쓰지 않음): {out.relative_to(_ROOT)}")
            continue
        out.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")

        s = doc["stats"]
        ratio = out.stat().st_size / max(meta.get("bytes", 1), 1) * 100
        print(
            f"[OK] {(meta.get('product_name') or '(상품명 없음)')[:30]}\n"
            f"     {s['pages']}쪽 / 텍스트 {s['text_length']:,}자 / 표 {s['tables']}개\n"
            f"     PDF {meta.get('bytes', 0):,}B → JSON {out.stat().st_size:,}B ({ratio:.1f}%)"
            + f"\n     {out.relative_to(_ROOT)}"
        )


if __name__ == "__main__":
    import sys

    sys.path.insert(0, str(_ROOT))
    main()
