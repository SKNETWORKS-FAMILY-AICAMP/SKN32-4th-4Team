# -*- coding: utf-8 -*-
"""전처리 산출물의 **불변 manifest** 를 만든다 — D2.

    python -m scripts.extract.build_manifest --schema s6
    python -m scripts.extract.build_manifest --schema s6 --verify     # 대조만, 안 씀

★왜 필요한가

    s7 이 나오면 s6 와 비교해야 하는데, **무엇과 무엇을 비교하는지**를 적어 두지 않으면
    비교가 성립하지 않는다. 실제로 이번에 s5 로 잰 커버리지를 s6 수치인 양 인용해
    한 번 헛짚었다(치과 154 → 4,181. 전처리가 좋아진 게 아니라 측정 패턴이 달랐다).

    그래서 **입력·산출물·코드·설정·환경**의 해시를 한 자리에 박는다.
    manifest 한 줄에서 어느 파일이든 역추적된다.

★불변이다

    같은 경로에 **내용이 다른** manifest 를 쓰려 하면 거부한다(`--force` 로만 덮어쓴다).
    manifest 가 조용히 바뀌면 그건 기준선이 아니다.

★"돌렸다"가 완료가 아니다

    `--verify` 는 기록된 해시를 **실제 파일과 다시 대조**한다.
    하나라도 어긋나면 0 이 아닌 코드로 끝낸다.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_RAW = _ROOT / "data" / "raw" / "insurance_terms"
_EXTRACTED = _ROOT / "data" / "extracted"
_STRUCTURED = _ROOT / "data" / "structured"
_OUT_DIR = _ROOT / "data" / "manifests" / "preprocess"
_CONFIG = _ROOT / "config"

#: 전처리 계보에 포함하기로 한 설정만 명시한다.
#:
#: ★예전에는 ``config/*.json`` 전부를 넣었다. 그 결과 문서 식별·세대 판정용
#:   ``generation_profiles.json``이나 serving 포인터가 바뀌어도 이미 생성된 페이지/
#:   조항 산출물이 변조된 것처럼 D2가 실패했다. 새 운영 설정이 추가될 때마다 전처리
#:   입력으로 자동 승격되는 것도 같은 결함이다. 입력은 glob이 아니라 allowlist다.
PREPROCESS_PROVENANCE_CONFIGS = frozenset({
    "ocr_shadow48_bench.json",
    "ocr_sota5_bench.json",
})

#: manifest 자체의 형식 버전. 필드가 바뀌면 올린다.
MANIFEST_VERSION = "1"

#: 고정할 의존성. 여기 없는 것이 결과를 바꿨다면 그건 이 목록의 결함이다.
_DEPS = ("pymupdf", "fitz", "regex")


def sha256_file(p: Path, _buf: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        while chunk := f.read(_buf):
            h.update(chunk)
    return h.hexdigest()


def _git(*args: str) -> str:
    try:
        return subprocess.run(["git", *args], cwd=_ROOT, capture_output=True,
                              text=True, encoding="utf-8", timeout=60).stdout.strip()
    except Exception:
        return ""


def code_state() -> dict:
    """커밋 + **작업트리가 더러우면 그 내용의 해시까지.**

    ★커밋만 적으면 안 된다 — 커밋 안 한 수정으로 돌린 결과가
      "그 커밋의 결과"로 기록되어 재현이 안 된다.
    """
    head = _git("rev-parse", "HEAD")
    status = _git("status", "--porcelain")
    diff = _git("diff", "HEAD")
    dirty = bool(status.strip())
    return {
        "git_commit": head,
        "dirty": dirty,
        #: 더러운 작업트리의 지문. 같은 값이면 같은 코드로 돌린 것이다.
        "dirty_sha256": hashlib.sha256((status + "\n" + diff).encode("utf-8")).hexdigest()
        if dirty else "",
        "dirty_files": sorted(l[3:] for l in status.splitlines() if l[3:])[:200] if dirty else [],
    }


def env_state() -> dict:
    deps = {}
    for name in _DEPS:
        try:
            mod = __import__(name)
            deps[name] = getattr(mod, "__version__", None) or getattr(
                mod, "VersionBind", None) or "(버전 미상)"
        except Exception:
            deps[name] = None            # ★없으면 None. 지어내지 않는다
    return {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "deps": deps,
    }


def config_state() -> dict:
    out = {}
    for p in sorted(_CONFIG.glob("*.json")):
        if p.name not in PREPROCESS_PROVENANCE_CONFIGS:
            continue
        out[p.name] = sha256_file(p)
    return out


def _raw_paths(insurer: str, sha12: str) -> list[str]:
    """같은 내용이 다른 이름으로 여러 벌 있을 수 있다. 전부 남긴다."""
    d = _RAW / insurer
    if not d.is_dir():
        return []
    return sorted(str(p.relative_to(_ROOT)).replace("\\", "/")
                  for p in d.glob(f"{sha12}_*.pdf"))


def collect(schema: str, also: tuple[str, ...], verify_inputs: bool) -> dict:
    rows, problems = [], []
    targets = sorted(_STRUCTURED.glob(f"*/{schema}_*/*.clauses.json"))
    if not targets:
        raise SystemExit(f"FAIL  {schema} 산출물이 없다 — data/structured/*/{schema}_*/")

    for p in targets:
        sha12 = p.name.split(".")[0]
        insurer = p.parent.parent.name
        doc = json.loads(p.read_text(encoding="utf-8"))
        src = doc.get("source") or {}
        row = {
            "sha12": sha12,
            "insurer": insurer,
            "input_sha256": src.get("sha256") or "",
            "input_bytes": src.get("bytes"),
            "raw_paths": _raw_paths(insurer, sha12),
            "schema_version": doc.get("schema_version"),
            "extractor": doc.get("extractor"),
            "built_at": doc.get("built_at"),
            "parse_status": doc.get("parse_status"),
            "outputs": {},
        }
        #: 산출물 해시 — 이 스키마와, 비교 대상으로 지정한 스키마들
        for s in (schema, *also):
            hits = list(_STRUCTURED.glob(f"{insurer}/{s}_*/{sha12}.clauses.json"))
            if hits:
                row["outputs"][f"structured/{s}"] = {
                    "path": str(hits[0].relative_to(_ROOT)).replace("\\", "/"),
                    "sha256": sha256_file(hits[0]),
                }
            hits = list(_EXTRACTED.glob(f"{insurer}/{s}_*/{sha12}.json"))
            if hits:
                row["outputs"][f"extracted/{s}"] = {
                    "path": str(hits[0].relative_to(_ROOT)).replace("\\", "/"),
                    "sha256": sha256_file(hits[0]),
                }

        #: ★입력 원본을 실제로 다시 해시해 산출물이 적어둔 값과 맞춰 본다.
        #:   어긋나면 그 산출물은 **다른 파일에서 나온 것**이다.
        if verify_inputs:
            got = None
            for rp in row["raw_paths"]:
                got = sha256_file(_ROOT / rp)
                if got == row["input_sha256"]:
                    break
            row["input_verified"] = (got == row["input_sha256"]) if row["raw_paths"] else False
            if not row["input_verified"]:
                problems.append(
                    f"{sha12}: 원본 해시 불일치 또는 원본 없음 "
                    f"(raw {len(row['raw_paths'])}개)")
        rows.append(row)

    return {"rows": rows, "problems": problems}


def build(schema: str, also: tuple[str, ...], verify_inputs: bool) -> dict:
    got = collect(schema, also, verify_inputs)
    rows = got["rows"]
    man = {
        "manifest_version": MANIFEST_VERSION,
        "schema": schema,
        "compared_with": list(also),
        #: ★built_at 을 여기서 새로 찍지 않는다. 산출물이 가진 시각의 범위를 적는다 —
        #:   manifest 를 다시 만들어도 값이 안 바뀌어야 불변이다.
        "built_at_range": {
            "min": min((r["built_at"] for r in rows if r["built_at"]), default=None),
            "max": max((r["built_at"] for r in rows if r["built_at"]), default=None),
        },
        "command": "python -m scripts.extract.run_all",
        "code": code_state(),
        "env": env_state(),
        "config": config_state(),
        "counts": {
            "documents": len(rows),
            "parse_status": _tally(r["parse_status"] for r in rows),
            "extractor": _tally(r["extractor"] for r in rows),
        },
        "documents": rows,
    }
    man["problems"] = got["problems"]
    return man


def _tally(it) -> dict:
    out: dict = {}
    for v in it:
        out[str(v)] = out.get(str(v), 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def _canon(man: dict) -> str:
    return json.dumps(man, ensure_ascii=False, indent=1, sort_keys=True)


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def manifest_sidecar_path(out: Path) -> Path:
    """manifest의 바이트 지문 경로.

    `Path.with_suffix(".sha256")` 규약을 한 곳에 둔다. 생성과 검증이 다른 이름을
    계산하면 sidecar가 있어도 못 찾는 조용한 결함이 생긴다.
    """
    return out.with_suffix(".sha256")


def verify_manifest_sidecar(out: Path) -> str | None:
    """실제 manifest 파일 바이트와 sidecar를 대조한다. 정상이면 ``None``.

    ★논리 문자열을 다시 JSON 직렬화해 해시하지 않는다. 줄바꿈·인코딩까지 포함한
      **실제 파일**이 전달·변조 검사의 대상이다.
    """
    sidecar = manifest_sidecar_path(out)
    if not sidecar.is_file():
        return f"{sidecar.name} 없음"
    try:
        recorded = sidecar.read_text(encoding="ascii").strip().lower()
    except (OSError, UnicodeError) as exc:
        return f"{sidecar.name} 읽기 실패: {exc}"
    if not _SHA256_RE.fullmatch(recorded):
        return f"{sidecar.name} 형식 오류 — SHA-256 64자리 hex가 아님"
    actual = sha256_file(out)
    if recorded != actual:
        return f"{sidecar.name} 불일치 — 기록 {recorded}, 실제 {actual}"
    return None


def write_manifest_with_sidecar(out: Path, body: str) -> None:
    """manifest를 쓴 **뒤 실제 파일**을 해시해 sidecar를 만든다.

    Windows 기본 text 쓰기는 ``\n``을 CRLF로 바꿀 수 있다. 예전 코드는 쓰기 전
    ``body.encode()``를 해시해 실제 파일과 다른 지문을 만들었다. newline을 LF로
    고정하고도, 안전을 위해 반드시 디스크 파일을 다시 해시한다.
    """
    out.write_text(body, encoding="utf-8", newline="\n")
    manifest_sidecar_path(out).write_text(
        sha256_file(out) + "\n", encoding="ascii", newline="\n"
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--schema", default="s6")
    ap.add_argument("--also", default="s5", help="함께 해시를 남길 비교 대상 스키마(콤마)")
    ap.add_argument("--verify", action="store_true",
                    help="기록된 manifest 를 실제 파일과 다시 대조한다. 쓰지 않는다")
    ap.add_argument("--no-verify-inputs", action="store_true",
                    help="원본 PDF 재해시를 건너뛴다(빠르지만 계보를 확인 안 한 것이다)")
    ap.add_argument("--force", action="store_true", help="내용이 다른 기존 manifest 를 덮어쓴다")
    a = ap.parse_args()

    also = tuple(s for s in (a.also or "").split(",") if s)
    _OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = _OUT_DIR / f"manifest_{a.schema}.json"

    # ── 대조 모드 ────────────────────────────────────────────────
    if a.verify:
        if not out.exists():
            print(f"FAIL  {out.relative_to(_ROOT)} 이 없다 — 먼저 만들어라")
            return 1
        rec = json.loads(out.read_text(encoding="utf-8"))
        bad = []
        sidecar_problem = verify_manifest_sidecar(out)
        if sidecar_problem:
            bad.append(f"manifest sidecar: {sidecar_problem}")
        for r in rec["documents"]:
            for key, o in r["outputs"].items():
                p = _ROOT / o["path"]
                if not p.exists():
                    bad.append(f"{r['sha12']} {key}: 파일 없음")
                elif sha256_file(p) != o["sha256"]:
                    bad.append(f"{r['sha12']} {key}: ★해시 불일치 — 산출물이 바뀌었다")
        for name, h in rec["config"].items():
            if name not in PREPROCESS_PROVENANCE_CONFIGS:
                # 과거 manifest가 모든 config JSON을 무차별로 품었다. 새 manifest에는
                # 실제 전처리 계보 설정만 넣고, 과거 판에서도 비입력 변경을 산출물
                # 변조로 오인하지 않는다. manifest 파일 자체는 덮어쓰지 않는다.
                print(f"  NOTE  config/{name} 은 전처리 입력이 아니라 검증에서 제외한다")
                continue
            p = _CONFIG / name
            if not p.exists() or sha256_file(p) != h:
                bad.append(f"config/{name}: 바뀌었거나 없다")
        now = code_state()
        if now["git_commit"] != rec["code"]["git_commit"]:
            print(f"  NOTE  커밋이 다르다 {rec['code']['git_commit'][:8]} → {now['git_commit'][:8]}")
        if now["dirty_sha256"] != rec["code"]["dirty_sha256"]:
            print("  NOTE  작업트리 상태가 다르다")
        print(f"문서 {len(rec['documents']):,} 대조 · 어긋남 {len(bad)}")
        for b in bad[:20]:
            print(f"  FAIL  {b}")
        return 1 if bad else 0

    # ── 생성 ─────────────────────────────────────────────────────
    man = build(a.schema, also, verify_inputs=not a.no_verify_inputs)
    body = _canon(man)

    if out.exists():
        old = out.read_text(encoding="utf-8")
        if old == body:
            sidecar_problem = verify_manifest_sidecar(out)
            if sidecar_problem:
                print(f"FAIL  내용은 같지만 manifest 지문이 유효하지 않다: {sidecar_problem}\n"
                      "      실제 파일을 확인한 뒤 --force 로 manifest와 sidecar를 다시 쓰라.")
                return 1
            print(f"이미 같은 내용이다 — {out.relative_to(_ROOT)} (그대로 둔다)")
            return 0
        if not a.force:
            print(f"FAIL  {out.relative_to(_ROOT)} 이 이미 있고 내용이 다르다.\n"
                  f"      manifest 는 불변이다. 정말 갈아야 하면 --force.")
            return 1

    #: manifest 자신의 지문도 옆에 남긴다. ★쓰기 전 문자열이 아니라 실제 파일을 해시한다.
    write_manifest_with_sidecar(out, body)

    c = man["counts"]
    print(f"{out.relative_to(_ROOT)}  ({len(body)/1024:.0f} KB)")
    print(f"  문서 {c['documents']:,} · parse_status {c['parse_status']}")
    print(f"  기간 {man['built_at_range']['min']} ~ {man['built_at_range']['max']}")
    print(f"  코드 {man['code']['git_commit'][:8]}"
          + (f" +dirty:{man['code']['dirty_sha256'][:8]}" if man['code']['dirty'] else ""))
    print(f"  env  python {man['env']['python']} · {man['env']['deps']}")
    if man["problems"]:
        print(f"  ★문제 {len(man['problems'])}건")
        for p in man["problems"][:10]:
            print(f"    {p}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
