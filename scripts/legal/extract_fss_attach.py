"""금감원 첨부(.hwp/.pdf)에서 텍스트를 뽑는다.

★분쟁조정결정례는 **본문이 "첨부파일 참조" 한 줄뿐**이라 첨부를 못 읽으면 자료가 없는 것과 같다.
   실측(2026-08-11): 실손 54건 중 `dcsn` 16건이 그 상태였다.

포맷이 섞여 있다(매직바이트 실측):
    OLE(HWP5)  46 · PDF 4 · ZIP(HWPX)  1 · HWP3  1

★**조용한 스킵을 만들지 않는다.** 포맷별로 실패를 세어 보고한다(RULE.md §3 · CLAUDE.md §3).
   "48건 중 40건 추출" 이 아니라 "HWP5 46 중 40 성공 · HWP3 1 실패(포맷 미지원)" 로 적는다.

사용:
    python -m scripts.legal.extract_fss_attach
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
SRC = _ROOT / "data" / "legal" / "raw" / "fss" / "attach"
OUT = _ROOT / "data" / "legal" / "raw" / "fss" / "text"


def _magic(p: Path) -> str:
    h = p.read_bytes()[:64]
    if h[:4] == b"\xd0\xcf\x11\xe0":
        return "OLE"          # HWP5 또는 DOC
    if h[:4] == b"%PDF":
        return "PDF"
    if h[:4] == b"PK\x03\x04":
        return "ZIP"          # HWPX 또는 DOCX
    if b"HWP" in h:
        return "HWP3"
    return "UNKNOWN"


def _pdf(p: Path) -> str | None:
    try:
        import fitz
        with fitz.open(p) as doc:
            return "\n".join(pg.get_text() for pg in doc)
    except Exception:
        return None


def _hwp5(p: Path) -> str | None:
    """`hwp5txt` CLI. pyhwp 가 설치돼 있어야 한다."""
    try:
        r = subprocess.run([sys.executable, "-m", "hwp5.hwp5txt", str(p)],
                           capture_output=True, timeout=90)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.decode("utf-8", "replace")
    except Exception:
        pass
    for exe in ("hwp5txt", "hwp5txt.exe"):
        try:
            r = subprocess.run([exe, str(p)], capture_output=True, timeout=90)
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout.decode("utf-8", "replace")
        except Exception:
            continue
    return None


def _hwpx(p: Path) -> str | None:
    """HWPX 는 ZIP 안에 XML 이다."""
    try:
        import zipfile
        parts = []
        with zipfile.ZipFile(p) as z:
            for n in z.namelist():
                if n.endswith(".xml") and "section" in n.lower():
                    x = z.read(n).decode("utf-8", "replace")
                    parts.append(re.sub(r"<[^>]+>", " ", x))
        t = re.sub(r"\s+", " ", " ".join(parts)).strip()
        return t or None
    except Exception:
        return None


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    files = [p for p in sorted(SRC.glob("*")) if p.suffix.lower() in (".hwp", ".pdf", ".hwpx")]
    fmt_tot: Counter = Counter()
    fmt_ok: Counter = Counter()
    rows = []
    for p in files:
        fmt = _magic(p)
        fmt_tot[fmt] += 1
        text = {"PDF": _pdf, "OLE": _hwp5, "ZIP": _hwpx}.get(fmt, lambda _: None)(p)
        if text and len(text.strip()) >= 50:
            fmt_ok[fmt] += 1
            (OUT / f"{p.stem}.txt").write_text(text, encoding="utf-8")
            rows.append({"file": p.name, "format": fmt, "ok": True, "chars": len(text.strip())})
        else:
            #: ★실패를 세어 남긴다. 왜 실패했는지 포맷으로 구분된다.
            rows.append({"file": p.name, "format": fmt, "ok": False,
                         "why": "추출기 없음/실패" if text is None else "본문 50자 미만"})
    (OUT / "_extract_report.json").write_text(
        json.dumps({"총": len(files), "포맷별_전체": dict(fmt_tot),
                    "포맷별_성공": dict(fmt_ok), "건별": rows},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"첨부 {len(files)}건")
    for f in fmt_tot:
        print(f"  {f:8s} {fmt_ok[f]:3d}/{fmt_tot[f]:3d} 성공")
    ok = sum(fmt_ok.values())
    print(f"합계 {ok}/{len(files)} 성공 · 실패 {len(files)-ok}")


if __name__ == "__main__":
    main()
