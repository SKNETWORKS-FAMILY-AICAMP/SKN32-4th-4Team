# -*- coding: utf-8 -*-
"""파인튜닝 산출 문서를 **하나의 압축파일**로 묶는다.

    python -m scripts.finetune.pack_deliverables

★★**약관 원문이 들어간 것은 넣지 않는다.**
  `predictions.jsonl`·`gold.jsonl`·검수 화면 HTML 은 인용 조항 원문을 담고 있다.
  문서 묶음은 **팀·심사에 돌리는 것**이므로 그런 파일을 함께 넣으면
  저작물이 의도치 않게 퍼진다(CLAUDE.md §2).
  대신 **무엇이 어디에 있는지**를 `00_읽어보기.md` 에 적어 둔다.

★들어가는 것 — 계획 · 리포트 · 테스트 · 검증(수치) · 결론.
  수치 파일(`analysis.json` 등)은 **집계값만** 들어 있어 안전하다.
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import pathlib
import sys
import zipfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
OUT_DEFAULT = ROOT / "dist" / "파인튜닝_산출물_20260827.zip"

#: (묶음 안 경로, 원본 경로) — 없는 파일은 **세어서 보고**하고 조용히 넘기지 않는다.
PLAN = [
    ("01_계획/파인튜닝_실행과_평가_계획.md",
     "docs/plans/2026-08-27_파인튜닝_실행과_평가_계획.md"),
    ("01_계획/05D_파인튜닝_모델_설계.md",
     "docs/submission/05D_파인튜닝_모델_설계.md"),

    ("02_리포트/파인튜닝_실행_2500건_기계라벨.md",
     "docs/reports/2026-08-27_파인튜닝_실행_2500건_기계라벨.md"),
    ("02_리포트/파인튜닝_실행결과_평가.md",
     "docs/reports/2026-08-27_파인튜닝_실행결과_평가.md"),
    ("02_리포트/QA파일럿_검수결과_통합과_재배포.md",
     "docs/reports/2026-08-27_QA파일럿_검수결과_통합과_재배포.md"),
    ("02_리포트/승인QA_파일럿300_후보생성과_검수화면.md",
     "docs/reports/2026-08-26_승인QA_파일럿300_후보생성과_검수화면.md"),

    ("03_코드/build_qa_pilot.py", "scripts/finetune/build_qa_pilot.py"),
    ("03_코드/restore_reviewed_items.py", "scripts/finetune/restore_reviewed_items.py"),
    ("03_코드/build_sft_dataset.py", "scripts/finetune/build_sft_dataset.py"),
    ("03_코드/split_dataset.py", "scripts/finetune/split_dataset.py"),
    ("03_코드/remote_train_qlora.py", "scripts/finetune/remote_train_qlora.py"),
    ("03_코드/remote_eval_gold.py", "scripts/finetune/remote_eval_gold.py"),
    ("03_코드/remote_measure_serving.py", "scripts/finetune/remote_measure_serving.py"),
    ("03_코드/remote_check_repetition.py", "scripts/finetune/remote_check_repetition.py"),
    ("03_코드/analyze_eval.py", "scripts/finetune/analyze_eval.py"),
    ("03_코드/triage_qa_pilot.py", "scripts/review/triage_qa_pilot.py"),
    ("03_코드/consolidate_qa_pilot.py", "scripts/review/consolidate_qa_pilot.py"),
    ("03_코드/build_model_output_review.py", "scripts/review/build_model_output_review.py"),

    ("04_테스트/test_split_gate.py", "tests/test_split_gate.py"),
    ("04_테스트/test_settings_binding_guard.py", "tests/test_settings_binding_guard.py"),

    ("05_검증수치/run1_run_summary.json", "data/finetune/results/run_summary.json"),
    ("05_검증수치/run1_eval_gold.json", "data/finetune/results/eval_gold.json"),
    ("05_검증수치/run1_analysis.json", "data/finetune/results/analysis.json"),
    ("05_검증수치/run1_serving_measure.json", "data/finetune/results/serving_measure.json"),
    ("05_검증수치/run2_run_summary.json", "data/finetune/results_run2/run_summary.json"),
    ("05_검증수치/run2_eval_gold.json", "data/finetune/results_run2/eval_gold.json"),
    ("05_검증수치/run2_analysis.json", "data/finetune/results_run2/analysis.json"),
    ("05_검증수치/run2_serving_measure.json", "data/finetune/results_run2/serving_measure.json"),
    ("05_검증수치/run2_repetition_check.json", "data/finetune/results_run2/repetition_check.json"),
    ("05_검증수치/sft_manifest.json", "data/finetune/sft/manifest.json"),
    ("05_검증수치/split.json", "data/finetune/qa_pilot/split.json"),

    ("04_테스트/테스트_리포트.md",
     "docs/reports/2026-08-27_파인튜닝_테스트_리포트.md"),
    ("04_테스트/기계검수_근거성.md",
     "docs/reports/2026-08-27_파인튜닝_기계검수_근거성.md"),
    ("03_코드/machine_groundedness.py", "scripts/finetune/machine_groundedness.py"),
    ("03_코드/pack_deliverables.py", "scripts/finetune/pack_deliverables.py"),
    ("05_검증수치/run2_machine_groundedness.json",
     "data/finetune/results_run2/machine_groundedness.json"),
    ("05_검증수치/run1_machine_groundedness.json",
     "data/finetune/results/machine_groundedness.json"),
    ("05_검증수치/run3_run_summary.json", "data/finetune/results_run3/run_summary.json"),
    ("05_검증수치/run3_eval_gold.json", "data/finetune/results_run3/eval_gold.json"),
    ("05_검증수치/run3_analysis.json", "data/finetune/results_run3/analysis.json"),
    ("05_검증수치/run3_machine_groundedness.json",
     "data/finetune/results_run3/machine_groundedness.json"),

    ("06_결론/run1_run2_비교와_결론.md",
     "docs/reports/2026-08-27_파인튜닝_run1_run2_비교와_결론.md"),
    ("06_결론/검수화면_안내.md",
     "docs/reports/2026-08-27_모델출력_검수화면.md"),
]

#: ★넣지 않는 것 — 이유를 적어 둔다. 「빠뜨렸나」와 「일부러 뺐나」는 다르다.
EXCLUDED = [
    ("data/finetune/sft/{train,valid,gold}.jsonl",
     "약관 조항 원문이 들어 있다(CLAUDE.md §2). 내부 저장소에만 둔다"),
    ("data/finetune/results*/predictions.jsonl",
     "모델 답변과 함께 인용 조항 원문이 붙어 있다"),
    ("docs/review/**/*.html",
     "검수 화면은 약관 원문을 페이지에 그대로 박아 만든다"),
    ("어댑터 가중치(out/adapter)",
     "의료·약관 문서로 학습한 산출물이다. 05D §6"),
]


def main() -> int:
    ap = argparse.ArgumentParser(description="파인튜닝 산출 문서 묶기")
    ap.add_argument("--out", default=str(OUT_DEFAULT))
    args = ap.parse_args()

    have, missing = [], []
    for arc, rel in PLAN:
        p = ROOT / rel
        (have if p.exists() else missing).append((arc, rel, p))

    lines = [
        "# 파인튜닝 산출물 — 읽는 순서", "",
        "1. `01_계획/` — 무엇을 어떤 순서로 재고 **무엇은 재지 않는지**",
        "2. `02_리포트/` — 실행 경과와 측정 결과",
        "3. `03_코드/` — 후보 생성 → 분할 → 학습 → 평가 → 분석 전 과정",
        "4. `04_테스트/` — 분할 게이트가 **실제로 무는지** 확인하는 시험",
        "5. `05_검증수치/` — 원본 측정값(JSON). 리포트의 모든 숫자가 여기서 나온다",
        "6. `06_결론/` — run1 vs run2 비교와 채택 판정", "",
        "## ★이 묶음에 **없는** 것과 그 이유", "",
        "| 무엇 | 왜 뺐나 |", "|---|---|",
    ]
    lines += [f"| `{a}` | {b} |" for a, b in EXCLUDED]
    lines += [
        "", "약관 원문은 저작물이라 `data/raw/` 에만 두고 배포하지 않는다(CLAUDE.md §2).",
        "위 파일들은 내부 저장소에서 경로 그대로 볼 수 있다.", "",
        "## ★결과를 말할 때", "",
        "학습 라벨의 **85%는 사람이 안 본 규칙 라벨**이다.",
        "사람이 확정한 221건(gold)은 **평가 전용**으로 학습에서 뺐다.",
        "그래서 정확한 표현은 「파인튜닝했다」가 아니라 **「기계 라벨로 파인튜닝했다」**이고,",
        "05D §7-2 의 `groundedness` 는 **재지 않았다** — 사람 검수로만 나온다.", "",
        "## 묶은 파일", "",
    ]
    lines += [f"- `{a}`" for a, _r, _p in have]
    if missing:
        lines += ["", "## ★넣으려 했으나 없던 파일", ""]
        lines += [f"- `{a}` ← `{r}`" for a, r, _p in missing]

    out = pathlib.Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        z.writestr("00_읽어보기.md", chr(10).join(lines) + chr(10))
        for arc, _rel, p in have:
            z.write(p, arc)
    with zipfile.ZipFile(out) as z:
        if z.testzip() is not None:
            raise SystemExit(f"압축이 손상됐습니다: {out}")

    kinds = collections.Counter(a.split("/", 1)[0] for a, _r, _p in have)
    print(f"작성: {out}  {out.stat().st_size // 1024} KB · 파일 {len(have) + 1}개")
    print("  sha256:", hashlib.sha256(out.read_bytes()).hexdigest()[:16])
    for k, v in sorted(kinds.items()):
        print(f"  {k:12s} {v:2d}개")
    if missing:
        #: ★빠진 것을 조용히 넘기지 않는다.
        print(chr(10) + "★넣으려 했으나 없던 파일:")
        for a, r, _p in missing:
            print(f"   {a:44s} ← {r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
