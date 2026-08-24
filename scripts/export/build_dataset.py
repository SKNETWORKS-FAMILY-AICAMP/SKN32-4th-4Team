"""모델팀에 넘길 **단일 데이터셋**을 만든다.

    python -m scripts.export.build_dataset
    python -m scripts.export.build_dataset --clause-tag s6_pymupdf-1.28.0   # shadow 세대

★왜 하나로 합치나

    지금 산출물은 문서별 JSON **1,367개**다. 모델팀이 임베딩하려면
    그걸 다 열어 중복을 스스로 제거해야 한다. 그런데 중복이 **66.5%** 다 —
    같은 조항이 최대 170개 문서에 실린다. 모르고 등장마다 임베딩하면
    같은 계산을 3배 한다.

★정체성과 발생을 나눈다 (CLAUDE.md §1)

    `clauses.jsonl`      내용 한 벌. `content_hash` 가 식별자다. **임베딩 대상**
    `occurrences.jsonl`  그 내용이 **어느 문서 어디에** 실렸는가. 근거를 댈 때 쓴다
    `manifest.json`      무엇으로 만들었는지 — 릴리스·세대·규칙 버전·집계·**입력 해시**

    합쳐서 한 파일로 만들지 않는다. 합치면 본문이 중복만큼 늘어난다.

★**거른 것을 센다**(CLAUDE.md §3)

    `eligible` 필드를 넣되 **거르지 않고 다 내보낸다.** 모델팀이 임베딩은 전부 하고
    인용은 `eligible` 만 쓰는 선택을 할 수 있어야 한다.
    다만 **왜 부적격인지**(`ineligible_reason`)를 함께 넣는다 —
    "몇 개가 왜 빠졌나"를 우리 쪽에 물어보지 않아도 되게.

★**부록을 빠뜨리지 않는다**

    별표·붙임·장해분류표가 `annexes[]` 에 따로 있다. **KCD 대조의 실제 근거**가
    거기 있으므로 같은 파일에 `source_kind="annex"` 로 함께 낸다.
    조항으로 오해하지 않게 종류를 박아 둔다.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from collections import Counter
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_STRUCT = _ROOT / "data" / "structured"
_OUT = _ROOT / "data" / "exports" / "dataset"


def _digest(paths: list[Path]) -> str:
    """입력 산출물의 지문. **무엇으로 만든 데이터셋인지** 나중에 대조할 수 있어야 한다.

    ★★**내용을 해시한다.** 처음엔 파일 이름+크기만 해시했는데(코덱스 지적),
      그러면 **크기가 같은 다른 내용**을 구분하지 못한다. 조항 하나가 바뀌어도
      전체 크기는 그대로일 수 있다 — 지문이라면서 지문 구실을 못 한다.
    """
    h = hashlib.sha256()
    for p in sorted(paths):
        h.update(p.name.encode())
        with p.open("rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
    return h.hexdigest()


def main() -> int:
    from app.core import release
    from app.core.domain import eligibility

    ap = argparse.ArgumentParser(description="모델팀 인계용 단일 데이터셋")
    ap.add_argument("--clause-tag", default="", help="★shadow 세대. 기본은 승인 릴리스")
    ap.add_argument("--out", default=str(_OUT))
    a = ap.parse_args()

    rel = release.load()
    tag = a.clause_tag or rel.clause_tag
    #: ★★**승인 릴리스가 아닌 세대를 뽑으면 릴리스 이름도 바꾼다.**
    #:
    #:   처음엔 `rel.release_id` 를 그대로 썼다. 그러면 `--clause-tag s6…` 로 뽑은
    #:   발생행이 **s5 릴리스 식별자**를 달고 나간다 — 세대 혼입을 막으려고 만든
    #:   `occurrence_id` 가 오히려 두 세대를 같은 이름으로 묶는다.
    if a.clause_tag and a.clause_tag != rel.clause_tag:
        release_id = f"shadow-{tag}"
        note = f"  ★승인 릴리스가 아니다 — release_id 를 `{release_id}` 로 쓴다"
    else:
        release_id = rel.release_id
        note = ""
    print(f"[세대] {tag}{note}", flush=True)

    files = sorted(_STRUCT.glob(f"*/{tag}/*.clauses.json"))
    if not files:
        raise SystemExit(f"조항 산출물이 없습니다: {tag}")

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    t0 = time.time()

    #: 내용 한 벌. `content_hash` → 본문
    contents: dict[str, dict] = {}
    n_occ = 0
    status_cnt: Counter = Counter()
    kind_cnt: Counter = Counter()
    inelig_cnt: Counter = Counter()
    n_docs = 0
    n_candidates = 0
    candidate_ids: set[str] = set()
    candidate_status: Counter = Counter()
    candidate_reasons: Counter = Counter()

    with (
        (out / "occurrences.jsonl").open("w", encoding="utf-8", newline="\n") as f_occ,
        (out / "candidate_facts.jsonl").open("w", encoding="utf-8", newline="\n") as f_candidate,
    ):
        for p in files:
            doc = json.loads(p.read_text(encoding="utf-8"))
            n_docs += 1
            status = doc.get("parse_status") or "unknown"
            status_cnt[status] += 1
            src = doc.get("source") or {}
            sha = src.get("sha256") or ""
            insurer = src.get("insurer") or ""

            for fact in doc.get("candidate_facts") or []:
                candidate_id = str(fact.get("candidate_id") or "")
                if not candidate_id or candidate_id in candidate_ids:
                    raise SystemExit(f"candidate fact ID 누락/중복: {p} {candidate_id!r}")
                if (
                    fact.get("approval") != "candidate"
                    or fact.get("serving_eligible") is not False
                    or fact.get("citation_eligible") is not False
                ):
                    raise SystemExit(f"candidate 격리 플래그 위반: {p} {candidate_id}")
                candidate_ids.add(candidate_id)
                validation = fact.get("validation") or {}
                candidate_status[str(validation.get("status") or "missing")] += 1
                candidate_reasons.update(
                    str(reason) for reason in validation.get("reasons") or []
                )
                f_candidate.write(
                    json.dumps(
                        {
                            "release_id": release_id,
                            "clause_tag": tag,
                            **fact,
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
                n_candidates += 1

            for kind, key in (("clause", "clauses"), ("annex", "annexes")):
                for x in doc.get(key) or []:
                    h = x.get("content_hash") or ""
                    text = x.get("text") or ""
                    if not h or not text.strip():
                        continue
                    kind_cnt[kind] += 1

                    if kind == "clause":
                        v = eligibility.check(x, parse_status=status)
                    else:
                        #: ★부록에는 `citation_eligible` 이 없다. **조항 규칙을 그대로
                        #:   들이대면 전부 부적격**이 된다 — 부록은 조가 아니라
                        #:   부록 자신(`label`)으로 인용하므로 조 번호를 요구하지 않는다.
                        v = eligibility.EligibilityResult(
                            status == "ok",
                            "" if status == "ok" else f"문서 파싱 상태가 '{status}'",
                        )
                    if not v.usable:
                        inelig_cnt[v.reason] += 1

                    if h not in contents:
                        contents[h] = {
                            "content_hash": h,
                            "source_kind": kind,
                            "text": text,
                            "char_length": len(text),
                            "n_occurrences": 0,
                            #: ★★**적격 등장이 몇 개인가.** 이게 없으면 모델팀이
                            #:   `occurrences.jsonl` 을 조인해야 안전 대상을 안다(코덱스 지적).
                            #:   같은 내용이 어떤 문서에선 적격, 다른 문서에선 부적격일 수 있다 —
                            #:   `parse_status` 가 문서마다 다르기 때문이다.
                            "n_eligible_occurrences": 0,
                        }
                    contents[h]["n_occurrences"] += 1
                    if v.usable:
                        contents[h]["n_eligible_occurrences"] += 1

                    loc = x.get("locator") or {}
                    ordinal = x.get("ordinal")
                    f_occ.write(json.dumps({
                        "content_hash": h,
                        "sha256": sha,
                        "insurer": insurer,
                        "source_kind": kind,
                        "ordinal": ordinal,
                        #: ★인용 식별자. 릴리스·문서·종류·순번이 다 들어간다.
                        "occurrence_id": (
                            f"{release_id}:{sha}:{kind}:{ordinal}"
                            if (sha and ordinal is not None) else ""
                        ),
                        #: 조항은 조 번호, 부록은 라벨
                        "label": x.get("qualified_no") if kind == "clause" else x.get("label"),
                        "section": x.get("section") or "",
                        "title": x.get("title") or "",
                        "page_from": loc.get("page_from"),
                        "page_to": loc.get("page_to"),
                        "eligible": v.usable,
                        "ineligible_reason": v.reason,
                        #: ★표 레코드를 함께 낸다. KCD 대조가 여기 달려 있다.
                        "tables": x.get("tables") or [],
                    }, ensure_ascii=False) + "\n")
                    n_occ += 1

    n_has_elig = n_mixed = 0
    with (out / "clauses.jsonl").open("w", encoding="utf-8", newline="\n") as f:
        for c in contents.values():
            e = c["n_eligible_occurrences"]
            #: ★적격 등장이 하나라도 있나. **임베딩 안전 대상**은 이것이다.
            #:   전체 고유 수를 안전 대상으로 읽으면 부적격만 있는 내용까지 인용된다.
            c["has_eligible"] = e > 0
            #: ★적격·부적격이 **섞인** 내용. 같은 본문이 어떤 문서에선 적격,
            #:   다른 문서에선 부적격일 수 있다(`parse_status` 가 문서마다 다르다).
            #:   인용할 때 반드시 **적격 등장**을 골라야 한다.
            c["mixed_eligibility"] = 0 < e < c["n_occurrences"]
            n_has_elig += c["has_eligible"]
            n_mixed += c["mixed_eligibility"]
            f.write(json.dumps(c, ensure_ascii=False) + "\n")

    manifest = {
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "release_id": release_id,
        "approved_release_id": rel.release_id,
        "is_shadow": release_id != rel.release_id,
        "clause_tag": tag,
        "index_generation": tag.split("_")[0],
        #: ★승인 설정의 `page_tag` 를 그대로 믿지 않는다 — 실제 디스크를 본다.
        #:   설정이 `s4` 인데 산출물은 `s5` 인 상태를 실제로 겪었다.
        "page_tag_declared": rel.page_tag,
        "page_tag_on_disk": sorted(
            {d.name for d in (_ROOT / "data" / "extracted").glob("*/s*_*") if d.is_dir()}
        ),
        "eligibility_rules_version": eligibility.RULES_VERSION,
        "input_digest": _digest(files),
        "documents": n_docs,
        "parse_status": dict(status_cnt),
        "occurrences": n_occ,
        "unique_contents": len(contents),
        #: ★★모델팀이 실제로 임베딩할 대상. 전체가 아니다.
        "unique_contents_with_eligible": n_has_elig,
        "unique_contents_mixed_eligibility": n_mixed,
        "dedup_ratio": round(1 - len(contents) / max(n_occ, 1), 4),
        "by_source_kind": dict(kind_cnt),
        "ineligible_by_reason": dict(inelig_cnt.most_common()),
        "candidate_facts": n_candidates,
        "candidate_fact_ids_unique": len(candidate_ids),
        "candidate_validation_status": dict(candidate_status),
        "candidate_validation_reasons": dict(candidate_reasons.most_common()),
        "candidate_serving_eligible": 0,
        "candidate_citation_eligible": 0,
        "★주의": [
            "`clauses.jsonl` 이 임베딩 대상이다 — 중복을 이미 뺐다.",
            "★안전 대상은 전체가 아니라 `has_eligible=true` 인 것이다.",
            "★`mixed_eligibility=true` 는 적격·부적격 등장이 섞였다 — 인용은 적격 등장만 고른다.",
            "`occurrences.jsonl` 이 근거(어느 문서 몇 쪽)다. 인용은 여기서 만든다.",
            "`eligible=false` 를 지우지 않고 이유와 함께 남겼다. 필터는 쓰는 쪽이 정한다.",
            "★부록(`source_kind=annex`)은 조가 아니다. 조 번호로 인용하면 출처가 틀린다.",
            "★`candidate_facts.jsonl`은 사람 검수 입력이다. clauses 임베딩·서빙·인용에 넣지 않는다.",
            "★임베딩 모델은 아직 미확정이다(2026-08-03). 이 데이터셋에 벡터는 없다.",
        ],
    }
    (out / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"[완료] {time.time() - t0:.1f}초 → {out.resolve().relative_to(_ROOT.resolve())}")
    print(f"  문서 {n_docs:,} · 등장 {n_occ:,} → 고유 {len(contents):,} "
          f"(중복 {manifest['dedup_ratio']:.1%})")
    print(f"  종류 {dict(kind_cnt)}")
    print(f"  부적격 사유 상위: {list(inelig_cnt.most_common(3))}")
    for name in ("clauses.jsonl", "occurrences.jsonl", "candidate_facts.jsonl", "manifest.json"):
        print(f"  {name:20s} {(out / name).stat().st_size / 1e6:8.1f} MB")
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(_ROOT))
    raise SystemExit(main())
