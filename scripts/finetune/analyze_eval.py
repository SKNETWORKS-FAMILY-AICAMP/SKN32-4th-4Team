# -*- coding: utf-8 -*-
"""평가 결과를 **구간으로** 말한다 — 05D §7-4.

    python -m scripts.finetune.analyze_eval

★★단일 숫자만 말하지 않는다. gold 221건은 작은 표본이라
  「0.199 → 0.541」 같은 평균 차이가 **우연일 수 있는지**를 함께 봐야 한다.
  같은 항목을 두 조건이 모두 답했으므로 **짝지어(paired) bootstrap** 이 맞다.

★★구간이 0 을 걸치면 **「차이를 확인하지 못했다」**이지 「같다」가 아니다.
  이 저장소가 임베딩 모델 비교에서 이미 못박아 둔 표현이다.

★못 재는 것을 잰 척하지 않는다 — `groundedness`(주장이 근거로 검증되는가)는
  여기서 나오지 않는다. 사람 검수로만 나온다(05D §7-2).
"""

from __future__ import annotations

import argparse
import collections
import difflib
import json
import pathlib
import random
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
RES = ROOT / "data" / "finetune" / "results"
BOOT = 10000
SEED = 20260827

JARGON = ["parse_status", "citation_eligible", "occurrence", "content_hash",
          "index_generation", "reason_code", "verdict", "sha256",
          "needs_expert", "needs_documents", "likely_covered", "suspect",
          "근거 인용을 검증하지 못해", "인용을 검증"]
ASSERT_WORDS = ["보장되지 않습니다", "보상되지 않습니다", "판매기간 밖입니다",
                "해당하지 않습니다", "보장됩니다", "지급됩니다", "면책입니다"]
CLAUSE_NO = re.compile(r"제\s?\d+\s?조")
CLAUSE_PATH = re.compile(r"[^\s,()]+/제\s?\d+\s?조")


def violations(text: str, has_citation: bool) -> list[str]:
    out = [f"내부용어:{j}" for j in JARGON if j in text]
    if not has_citation:
        out += [f"단정:{a}" for a in ASSERT_WORDS if a in text]
    if ("특정할 수 없" in text or "특정하지 못" in text) and CLAUSE_NO.search(text):
        out.append("모순")
    out += [f"중복조항:{p}" for p, n in collections.Counter(CLAUSE_PATH.findall(text)).items()
            if n > 1]
    return sorted(set(out))


def ci(diffs: list[float], rnd: random.Random, boot: int = BOOT):
    """짝지은 차이의 95% bootstrap 구간."""
    n = len(diffs)
    means = []
    for _ in range(boot):
        means.append(sum(diffs[rnd.randrange(n)] for _ in range(n)) / n)
    means.sort()
    return means[int(boot * 0.025)], means[int(boot * 0.975)]


def main() -> int:
    ap = argparse.ArgumentParser(description="평가 결과 구간 추정")
    ap.add_argument("--pred", default=str(RES / "predictions.jsonl"))
    ap.add_argument("--out", default=str(RES / "analysis.json"))
    args = ap.parse_args()

    rows = [json.loads(l) for l in
            pathlib.Path(args.pred).read_text(encoding="utf-8").splitlines() if l.strip()]
    #: 인용 유무 — gold 프롬프트가 아니라 예측 파일에 없으므로 축으로 근사한다.
    #: ★C축 합성 기권과 A축 인용 0건은 「단정」 규칙이 살아야 한다.
    print(f"예측 {len(rows)}건 · 축별 {dict(collections.Counter(r['axis'] for r in rows))}")

    sim_b, sim_a, vio_b, vio_a = [], [], [], []
    per_axis = collections.defaultdict(lambda: {"n": 0, "sb": [], "sa": [], "vb": 0, "va": 0})
    for r in rows:
        human = r["사람답"]
        #: D·C 축은 인용이 있을 수도 없을 수도 있다. 보수적으로 **인용 있음**으로 본다 —
        #: 그러면 「단정」을 안 세므로 개선을 **과대평가하지 않는다.**
        has_cite = True
        b = difflib.SequenceMatcher(None, human, r["baseline"]).ratio()
        a = difflib.SequenceMatcher(None, human, r["adapter"]).ratio()
        vb = 1 if violations(r["baseline"], has_cite) else 0
        va = 1 if violations(r["adapter"], has_cite) else 0
        sim_b.append(b); sim_a.append(a); vio_b.append(vb); vio_a.append(va)
        g = per_axis[r["axis"]]
        g["n"] += 1; g["sb"].append(b); g["sa"].append(a); g["vb"] += vb; g["va"] += va

    rnd = random.Random(SEED)
    n = len(rows)
    sim_d = [a - b for a, b in zip(sim_a, sim_b)]
    vio_d = [a - b for a, b in zip(vio_a, vio_b)]
    sim_lo, sim_hi = ci(sim_d, rnd)
    vio_lo, vio_hi = ci(vio_d, rnd)

    def say(lo, hi):
        #: ★구간이 0 을 걸치면 「차이를 확인하지 못했다」이지 「같다」가 아니다.
        return ("차이 확인" if (lo > 0 or hi < 0) else "차이를 확인하지 못함")

    res = {
        "표본": n, "bootstrap": BOOT, "seed": SEED,
        "사람답과 유사도": {
            "baseline": round(sum(sim_b) / n, 4),
            "adapter": round(sum(sim_a) / n, 4),
            "차이": round(sum(sim_d) / n, 4),
            "95% 구간": [round(sim_lo, 4), round(sim_hi, 4)],
            "판정": say(sim_lo, sim_hi),
        },
        "규칙 위반율": {
            "baseline": round(sum(vio_b) / n, 4),
            "adapter": round(sum(vio_a) / n, 4),
            "차이": round(sum(vio_d) / n, 4),
            "95% 구간": [round(vio_lo, 4), round(vio_hi, 4)],
            "판정": say(vio_lo, vio_hi),
        },
        "축별": {
            k: {"n": v["n"],
                "유사도": [round(sum(v["sb"]) / v["n"], 4), round(sum(v["sa"]) / v["n"], 4)],
                "위반": [v["vb"], v["va"]]}
            for k, v in sorted(per_axis.items())},
        "★못 잰 것": "groundedness — 주장이 근거로 검증되는지는 사람 검수로만 나온다(05D §7-2)",
        "★주의": "학습 라벨의 85%가 규칙 라벨(silver)이고, 평가 규칙과 학습 규칙이 같다. "
                "규칙 위반 감소는 부분적으로 자명하다 — 값은 baseline 이 얼마나 위반했나 쪽에 있다.",
    }
    pathlib.Path(args.out).write_text(
        json.dumps(res, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print()
    for k in ("사람답과 유사도", "규칙 위반율"):
        d = res[k]
        print(f"{k}: {d['baseline']} → {d['adapter']}  차이 {d['차이']:+.4f}"
              f"  95% [{d['95% 구간'][0]:+.4f}, {d['95% 구간'][1]:+.4f}]  → {d['판정']}")
    print()
    print(f"{'축':4s} {'n':>4s}  {'유사도 base→ft':22s} {'위반 base→ft'}")
    for k, v in res["축별"].items():
        print(f"{k:4s} {v['n']:4d}  {v['유사도'][0]:.4f} → {v['유사도'][1]:.4f}"
              f"        {v['위반'][0]:3d} → {v['위반'][1]:3d}")
    print()
    print(f"작성: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
