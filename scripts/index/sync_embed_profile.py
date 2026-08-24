"""승인 릴리스의 `embed_profile` 을 **적재 매니페스트에서 검증해 채운다.**

    python -m scripts.index.sync_embed_profile --dry-run
    python -m scripts.index.sync_embed_profile

★왜 필요한가 (2026-08-04 실측)
  색인은 질의에 `query: ` 접두사를 붙여 인코딩하도록 만들어졌다
  (`data/work/s7_arctic_embed5/manifest.json` · `query_prefix`).
  그런데 **승인 릴리스(`config/accepted_extraction.json`)에는 그 값이 없다.**
  서비스가 접두사 없이 질의를 인코딩하면 색인과 다른 공간에서 찾게 되고,
  **틀린 조항이 조용히 올라온다** — 오류도 안 난다. 그게 제일 나쁘다.

★손으로 베끼지 않는다. 베끼면 다음에 재임베딩할 때 또 어긋난다.
  이 도구는 **겹치는 항목이 실제로 같은지 먼저 확인하고**, 다른 게 하나라도 있으면
  아무것도 쓰지 않고 멈춘다. 릴리스가 진실의 원천이되, 그 값은 매니페스트에서 온다.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
RELEASE = ROOT / "config" / "accepted_extraction.json"
MANIFEST = ROOT / "data" / "work" / "s7_arctic_embed5" / "manifest.json"

#: 릴리스와 매니페스트가 **반드시 같아야** 하는 항목. 다르면 서로 다른 색인이다.
MUST_MATCH = ("model", "dim", "max_seq_length", "chunk_budget", "overlap")
#: 매니페스트에서 릴리스로 **가져올** 항목.
COPY = ("revision", "query_prefix", "doc_prefix", "normalized")


class ProfileError(RuntimeError):
    pass


def check(release: dict, manifest: dict) -> list[str]:
    prof = release.get("embed_profile")
    if not isinstance(prof, dict):
        raise ProfileError("릴리스에 embed_profile 이 없다")

    diffs = []
    for key in MUST_MATCH:
        want, got = manifest.get(key), prof.get(key)
        if want is None:
            raise ProfileError(f"매니페스트에 {key} 가 없다 — 어느 색인인지 확정할 수 없다")
        #: ★빈 값은 «같다»로 보지 않는다. 비어 있으면 채워야 할 대상이지 일치가 아니다.
        if got in (None, "") or got != want:
            diffs.append(f"{key}: 릴리스={got!r} ≠ 매니페스트={want!r}")
    return diffs


def plan(release: dict, manifest: dict) -> dict[str, object]:
    prof = release["embed_profile"]
    out: dict[str, object] = {}
    for key in COPY:
        if key not in manifest:
            raise ProfileError(
                f"매니페스트에 {key} 가 없다. 접두사를 모르는 채로 질의를 인코딩하면 "
                f"색인과 다른 공간에서 찾게 된다 — 기본값으로 때우지 않는다."
            )
        if prof.get(key) != manifest[key]:
            out[key] = manifest[key]
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--release", type=pathlib.Path, default=RELEASE)
    ap.add_argument("--manifest", type=pathlib.Path, default=MANIFEST)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    release = json.loads(a.release.read_text(encoding="utf-8"))
    manifest = json.loads(a.manifest.read_text(encoding="utf-8"))

    print(f"릴리스  {a.release.relative_to(ROOT)}  ({release.get('release_id')})")
    print(f"매니페스트 {a.manifest.relative_to(ROOT)}  ({manifest.get('schema_version')})")

    diffs = check(release, manifest)
    if diffs:
        print("\n★겹치는 항목이 다르다 — 서로 다른 색인일 수 있다. 아무것도 쓰지 않는다.")
        for d in diffs:
            print(f"   {d}")
        return 1
    print("  겹치는 항목 일치:", ", ".join(MUST_MATCH))

    changes = plan(release, manifest)
    if not changes:
        print("\n  채울 것이 없다 — 이미 맞춰져 있다.")
        return 0

    print("\n  채울 항목:")
    for k, v in changes.items():
        print(f"   {k:14} {release['embed_profile'].get(k)!r} → {v!r}")

    if a.dry_run:
        print("\n  (--dry-run — 쓰지 않았다)")
        return 0

    release["embed_profile"].update(changes)
    release["embed_profile"]["_출처"] = (
        f"{a.manifest.relative_to(ROOT).as_posix()} 에서 "
        f"scripts/index/sync_embed_profile.py 로 검증해 채웠다. 손으로 고치지 않는다."
    )
    a.release.write_text(
        json.dumps(release, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    print(f"\n  기록 완료 → {a.release.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ProfileError as exc:
        print(f"★{exc}", file=sys.stderr)
        raise SystemExit(2) from None
