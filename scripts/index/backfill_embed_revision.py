"""색인 라벨에 **빠져 있던 revision 을 채운다.**

    python -m scripts.index.backfill_embed_revision --dry-run
    python -m scripts.index.backfill_embed_revision
    python -m scripts.index.backfill_embed_revision --revert

★왜 필요한가 (2026-08-05)
  `policy_clause_chunk.embed_model` 이 `…|-|d1024|…` 로 적재돼 있다.
  `-` 는 **revision 을 안 적었다**는 뜻이지 「revision 이 없다」가 아니다.
  적재 매니페스트(`data/work/s7_arctic_embed5/manifest.json`)는 그 벡터를
  `55ec6e93…` 로 만들었다고 적고 있다. 라벨이 **불완전**했을 뿐이다.

  승인 릴리스에 revision 을 채우자(`sync_embed_profile.py`) 게이트 키가
  `…|55ec6e93…|…` 로 바뀌었고, DB 라벨과 안 맞아 **검색이 전량 막혔다**(503).
  게이트는 옳게 동작한 것이다 — 프로필이 다르면 다른 이름이어야 한다.
  고칠 곳은 게이트가 아니라 **라벨**이다.

★★**벡터를 건드리지 않는다.** 바꾸는 것은 이름표뿐이고, 그 이름표가 가리키는
  사실은 매니페스트가 증언한다. 그래서 이 스크립트는 **증언이 맞을 때만** 쓴다 —
  DB 라벨이 예상과 한 글자라도 다르면 아무것도 하지 않는다.

★되돌릴 수 있다(`--revert`). 라벨 하나짜리 변경이라 원래 값이 유일하게 정해진다.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

MANIFEST = ROOT / "data" / "work" / "s7_arctic_embed5" / "manifest.json"
TABLES = ("policy_clause_chunk",)


class BackfillError(RuntimeError):
    pass


def _keys() -> tuple[str, str, str]:
    """(빠진 라벨, 채운 라벨, revision) 을 돌려준다."""
    from app.core import release

    prof = release.current().embed_profile
    if not prof.revision:
        raise BackfillError(
            "릴리스에 revision 이 없다. `python -m scripts.index.sync_embed_profile` 를 먼저 돌린다."
        )
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if manifest.get("revision") != prof.revision:
        raise BackfillError(
            f"매니페스트와 릴리스의 revision 이 다르다: "
            f"{manifest.get('revision')!r} ≠ {prof.revision!r}. 라벨을 고칠 근거가 없다."
        )
    if manifest.get("model") != prof.model:
        raise BackfillError("매니페스트와 릴리스의 model 이 다르다.")

    filled = prof.key
    #: 빠진 라벨은 revision 자리만 `-` 인 같은 문자열이다.
    missing = filled.replace(f"|{prof.revision}|", "|-|", 1)
    if missing == filled:
        raise BackfillError("키에서 revision 자리를 찾지 못했다.")
    return missing, filled, prof.revision


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--revert", action="store_true")
    a = ap.parse_args()

    import psycopg

    from app.core.config import get_settings

    missing, filled, rev = _keys()
    src, dst = (filled, missing) if a.revert else (missing, filled)
    print(f"  {'되돌리기' if a.revert else '채우기'}")
    print(f"    from  {src}")
    print(f"    to    {filled if not a.revert else missing}")

    with psycopg.connect(get_settings().PGVECTOR_DSN, connect_timeout=15) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT embed_model, count(*) FROM policy_clause_chunk GROUP BY 1")
            rows = dict(cur.fetchall())
            print("  현재 DB 라벨:")
            for m, n in sorted(rows.items(), key=lambda kv: -kv[1]):
                print(f"    {n:>8,}  {m}")

            #: ★예상 밖의 라벨이 하나라도 있으면 멈춘다. 섞인 색인을 한 이름으로
            #:   덮으면 **무엇으로 만든 벡터인지 영원히 알 수 없게 된다.**
            unexpected = set(rows) - {missing, filled}
            if unexpected:
                raise BackfillError(f"예상 밖 라벨이 있다: {sorted(unexpected)}")
            if src not in rows:
                print(f"\n  바꿀 행이 없다(이미 {'되돌려' if a.revert else '채워'}져 있다).")
                return 0

            n = rows[src]
            if a.dry_run:
                print(f"\n  (--dry-run) {n:,}행을 바꿀 것이다.")
                return 0

            for table in TABLES:
                cur.execute(
                    f"UPDATE {table} SET embed_model = %s WHERE embed_model = %s", (dst, src)
                )
                print(f"  {table}: {cur.rowcount:,}행 갱신")
            conn.commit()

            cur.execute("SELECT embed_model, count(*) FROM policy_clause_chunk GROUP BY 1")
            print("  갱신 후:")
            for m, c in cur.fetchall():
                print(f"    {c:>8,}  {m}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BackfillError as exc:
        print(f"★{exc}", file=sys.stderr)
        raise SystemExit(2) from None
