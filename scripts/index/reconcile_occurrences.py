"""발생행을 **현행 산출물과 맞춘다** — 고아의 3분의 2를 만드는 원인을 정리한다.

    python -m scripts.index.reconcile_occurrences                 # 조회만(기본)
    python -m scripts.index.reconcile_occurrences --apply         # 실제로 지운다
    python -m scripts.index.reconcile_occurrences --generation s5-mixed

★왜 (2026-08-25 실측 · `docs/reports/debugs/2026-08-25_2300_...` )

    적재는 `upsert` 만 한다 — 산출물에서 **없어진** 조항의 발생행을 안 지운다.
    추출기가 바뀌면 조항 경계가 달라져 해시가 바뀌는데, 청크는 현행 산출물로
    다시 만들어지므로 옛 해시의 발생은 가리킬 청크가 없다. 그게 고아다.
    무작위 15문서 표본에서 고아 270행 중 **176행(65.2%)** 이 이 무리였다.

★★**증거 없이 지우지 않는다.** 산출물 파일을 못 읽은 문서는 **건너뛴다.**
  「못 읽었다」와 「거기 없다」를 뭉개면 읽기 실패를 이유로 멀쩡한 행을 지운다.
  건너뛴 수를 반드시 찍는다.

★기본은 조회만이다. 지우려면 `--apply` 를 **명시**해야 한다.
  지우기 전에 백업을 확인한다 — 되돌릴 수 없다.
"""

from __future__ import annotations

import argparse
import glob
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def artifact_hashes(generation: str) -> tuple[dict[str, set[str]], int]:
    """``{sha256: {content_hash…}}`` 와 **읽다 실패한 파일 수**.

    ★조항과 부록을 **둘 다** 담는다. 부록을 빼면 부록 발생이 전부
      「산출물에 없음」으로 보여 통째로 지워진다.
    ★파일이 깨져 못 읽으면 그 문서는 **아예 넣지 않는다** — 그러면
      `reconcile_occurrences` 가 건너뛴다(빈 집합을 주면 전량 삭제가 된다).
    """
    out: dict[str, set[str]] = {}
    broken = 0
    for f in glob.glob(str(ROOT / "data" / "structured" / "*" / f"{generation}_*" / "*.clauses.json")):
        stem = pathlib.Path(f).name.split(".")[0]
        try:
            d = json.loads(pathlib.Path(f).read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            broken += 1
            print(f"  ★산출물을 못 읽었다(건너뛴다): {pathlib.Path(f).name} — {type(exc).__name__}: {exc}")
            continue
        hs = {x.get("content_hash") for key in ("clauses", "annexes")
              for x in (d.get(key) or []) if x.get("content_hash")}
        if hs:
            out[stem] = hs
    return out, broken


def _expand(conn, short: dict[str, set[str]]) -> dict[str, set[str]]:
    """파일명은 `sha12` 인데 DB 는 전체 `sha256` 이다. DB 값으로 펼친다.

    ★★**sha12 가 겹치면 그 문서는 뺀다.** 두 문서가 같은 앞 12자를 쓰면
      한쪽 산출물로 다른 쪽 발생을 심판하게 된다 — 멀쩡한 행이 지워진다.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT DISTINCT sha256 FROM policy_clause_occurrence")
        full = [r[0] for r in cur.fetchall()]
    by12: dict[str, list[str]] = {}
    for s in full:
        by12.setdefault(s[:12], []).append(s)
    out, collided = {}, 0
    for s12, hs in short.items():
        cands = by12.get(s12, [])
        if len(cands) == 1:
            out[cands[0]] = hs
        elif len(cands) > 1:
            collided += 1
    if collided:
        print(f"  ★sha12 가 겹쳐 대조에서 뺀 문서 {collided}건 — 한쪽 산출물로 다른 쪽을 심판할 수 없다")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--generation", default=None, help="기본: 승인 릴리스의 세대")
    ap.add_argument("--apply", action="store_true", help="실제로 지운다(기본은 조회만)")
    ap.add_argument("--backup-table", default=None,
                    help="지운 행을 복사해 둘 테이블 이름. --apply 에 필수. "
                         "되돌리기: INSERT INTO policy_clause_occurrence SELECT * FROM <이 테이블>;")
    #: ★★전량 재생성 뒤에는 「낡은 행 ⇒ 청크 없음」 전제가 깨진다 —
    #:   내용이 문서 사이에 공유되기 때문이다(실측 중복 65%, 한 조항 최대 170문서).
    #:   그래서 낡은 행이 청크도 갖고 인용 가능이기도 하다. 안전장치가 전량을 막는다.
    #:   ★끄는 것을 **말로 적게** 한다. 이유 없이 끄면 도구가 거절한다.
    ap.add_argument("--allow-usable", action="store_true",
                    help="청크·인용가능 안전장치를 끈다. 전량 재생성 뒤 정리 전용. "
                         "--reason 필수. source_kinds 보호는 이 갈래로 꺼지지 않는다")
    ap.add_argument("--reason", default="",
                    help="--allow-usable / --prune-missing-artifact 을 켠 이유. 결과와 함께 남는다")
    #: ★★「산출물이 없다」를 「제외됐다」로 읽는 갈래. **기본은 꺼져 있다.**
    #:   「읽기에 실패했다」와 구분이 안 되면 멀쩡한 문서를 통째로 날린다.
    #:   켜기 전에 **전처리 대상 목록과 대조**한다:
    #:       python -m scripts.extract.run_all --stage clauses --dry-run
    #:   그 대상에 없으면 제외된 것이다.
    ap.add_argument("--prune-missing-artifact", action="store_true",
                    help="산출물이 아예 없는 문서의 발생행을 지운다(격리·제외 반영). "
                         "--reason 필수. 켜기 전에 run_all --dry-run 대상과 대조할 것")
    a = ap.parse_args()

    from db.postgres import pgvector_clause_index as ix
    from db.postgres.pgvector_index import get_conn

    gen = a.generation or ix.current_generation()
    short, broken = artifact_hashes(gen)
    print(f"[산출물] 세대 {gen} · 문서 {len(short):,}개에서 해시를 읽었다"
          + (f" · ★못 읽은 파일 {broken}개" if broken else ""))
    if a.apply and not a.backup_table:
        #: ★되돌릴 수 없는 삭제를 «깜빡해서» 하게 두지 않는다.
        print("★--apply 에는 --backup-table 이 필요하다. 되돌릴 수 없는 삭제는 하지 않는다.")
        print("  예: --backup-table pco_removed_20260826")
        return 2
    if not short:
        print("★산출물을 하나도 못 읽었다. 아무것도 하지 않는다 —"
              " 빈 목록으로 대조하면 전량이 '산출물에 없음'이 된다.")
        return 1

    with get_conn() as conn:
        full = _expand(conn, short)
        print(f"[대조] DB 의 sha256 과 이은 문서 {len(full):,}개")
        r = ix.reconcile_occurrences(conn, generation=gen, artifact_hashes=full,
                                     apply=a.apply, backup_table=a.backup_table,
                                     protect_usable=not a.allow_usable,
                                     reason=a.reason,
                                     prune_missing_artifact=a.prune_missing_artifact)

    print(f"[결과] 대조 {r['documents_checked']:,}문서 · "
          f"★산출물 없어 건너뜀 {r['documents_skipped']:,}문서")
    print(f"       대조한 출처: {', '.join(r['source_kinds'])}"
          f"  (그 밖 출처는 심판하지 않는다 — 예: approved_ocr_table_fact)")
    print(f"       산출물에 없는 발생행 {r['stale_rows']:,}")
    if r["prune_missing_artifact"]:
        #: ★건너뛰던 것을 지우기로 **바꿨다는 사실**을 결과에 크게 남긴다.
        print(f"       ★★산출물이 아예 없는 문서 {r['documents_skipped']:,}건도 **지우기로 했다** "
              f"— 대상 {r['missing_artifact_rows']:,}행 · 지움 {r['missing_artifact_deleted']:,}행")
        print(f"          이유: {r['reason']}")
    if not r["protect_usable"]:
        #: ★껐다는 사실과 이유를 **결과에 같이 찍는다.** 로그만 보고도 알 수 있어야 한다.
        print(f"       ★★청크·인용가능 안전장치를 **껐다** — 이유: {r['reason']}")
        print(f"          (source_kinds 보호는 그대로다: {', '.join(r['source_kinds'])})")
    elif r["protected"]:
        #: ★0 이 아니면 뭔가 잘못된 것이다. 크게 말한다.
        print(f"       ★★안전장치가 지킨 행 {r['protected']:,} "
              f"— 청크가 있거나 인용 가능한 행이 삭제 후보에 들어왔다. 원인을 봐야 한다.")
    print(f"       고아 발생 {r['orphans_before']:,} → {r['orphans_after']:,}")
    if a.apply:
        print(f"       ★지웠다: {r['deleted']:,}행 "
              f"(백업 {r['backed_up']:,}행 → {r['backup_table']})")
        print(f"       되돌리려면:")
        print(f"         INSERT INTO policy_clause_occurrence SELECT * FROM {r['backup_table']};")
    else:
        print("       (조회만 했다. 지우려면 --apply)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
