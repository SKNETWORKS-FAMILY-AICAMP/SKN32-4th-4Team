"""팀원이 손으로 찍은 표 라벨을 들여온다.

    python -m scripts.eval.import_table_labels

★출처

    `_3rd_project_4/ai-1/table_true_false.json` — 다른 팀원이 원문을 보고 찍은 것.
    **우리가 막혀 있던 L1(정답셋 확장)의 씨앗**이다. 우리 후보 65개는 라벨이 없다.

★원본이 JSON 이 아니다

    최상위 객체 없이 `"true": [...]` 가 반복되고, 일부 줄은 `]` 가 빠졌다.
    `json.loads` 가 `Extra data: line 1 column 7` 로 죽는다.
    그래서 **줄 단위로 복구**한다. 복구 실패한 줄은 **세어서 남긴다**(§3).

★원본을 고치지 않는다

    남의 저장소 파일이다. 여기서 읽어 우리 형식으로 **베껴 온다.**
    원본이 바뀌면 다시 돌리면 된다 — `source_sha256` 으로 어느 판을 읽었는지 남긴다.

★`check` 는 판정이 아니다

    팀원이 "원문 확인 필요"라고 남긴 것이다. `true` 로 반올림하지 않는다.
    임계값 적합에서 **제외**하고, 확인 대기 목록으로 둔다.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_SRC = _ROOT.parent / "_3rd_project_4" / "ai-1" / "table_true_false.json"
_OUT = _ROOT / "data" / "eval" / "table_labels.jsonl"

_LINE = re.compile(r'"(true|check|false)"\s*:\s*\[(.*?)\]?\s*$')
_OBJ = re.compile(r"\{.*?\}")


def main() -> int:
    if not _SRC.exists():
        raise SystemExit(f"원본이 없습니다: {_SRC}")
    raw = _SRC.read_text(encoding="utf-8")
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()

    rows: list[dict] = []
    seen: set[tuple[str, int]] = set()
    n_bad = n_dup = 0
    for lineno, line in enumerate(raw.split("\n"), 1):
        t = line.strip()
        if not t:
            continue
        m = _LINE.match(t)
        if not m:
            n_bad += 1
            continue
        label = m.group(1)
        for om in _OBJ.finditer(m.group(2).rstrip("]")):
            try:
                d = json.loads(om.group(0))
            except json.JSONDecodeError:
                n_bad += 1
                continue
            key = (d.get("sha12", ""), int(d.get("page", 0)))
            if key in seen:
                #: ★중복을 조용히 덮지 않는다. 세어서 보고한다.
                n_dup += 1
                continue
            seen.add(key)
            rows.append({
                "label": label,
                "sha12": key[0],
                "page": key[1],
                "why": d.get("why", ""),
                "source_line": lineno,
            })

    #: 우리 코퍼스에 있는지 확인 — 없으면 라벨이 있어도 못 쓴다
    have = {p.name.split(".")[0]
            for p in (_ROOT / "data" / "extracted").rglob("s5_pymupdf-1.28.0/*.json")}
    n_missing = 0
    for r in rows:
        r["in_corpus"] = r["sha12"] in have
        n_missing += not r["in_corpus"]

    _OUT.parent.mkdir(parents=True, exist_ok=True)
    with _OUT.open("w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps({
            "_meta": True,
            "source": str(_SRC.relative_to(_ROOT.parent)),
            "source_sha256": digest,
            "imported_at": "2026-08-03",
            "labeled_by": "팀원(_3rd_project_4/ai-1) · 원문 육안 확인",
            "★주의": [
                "원본이 유효한 JSON 이 아니라 줄 단위로 복구해 들여왔다.",
                "`check` 는 '원문 확인 필요' 다 — true 로 반올림하지 말 것.",
                "라벨 단위는 **(문서, 쪽)** 이다. 한 쪽에 표가 여럿이면 구분되지 않는다.",
            ],
            "counts": dict(Counter(r["label"] for r in rows)),
            "parse_failed_lines": n_bad,
            "duplicates_dropped": n_dup,
            "not_in_corpus": n_missing,
        }, ensure_ascii=False) + "\n")
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    c = Counter(r["label"] for r in rows)
    print(f"들여옴 {len(rows)}건 → {_OUT.relative_to(_ROOT)}")
    print(f"  라벨 {dict(c)}")
    print(f"  복구 실패 줄 {n_bad} · 중복 제거 {n_dup} · 코퍼스에 없음 {n_missing}")
    print(f"  원본 지문 {digest[:16]}…")
    print("\n★라벨 단위가 **(문서, 쪽)** 이다. 한 쪽에 표가 여럿이면 어느 표인지 모른다 —")
    print("  그 쪽의 표를 전부 같은 라벨로 보게 된다. 임계값을 적합할 때 이 한계를 적어라.")
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(_ROOT))
    raise SystemExit(main())
