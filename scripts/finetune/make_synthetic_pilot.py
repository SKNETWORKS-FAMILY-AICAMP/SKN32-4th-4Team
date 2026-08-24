"""파일럿용 **완전 합성** 학습 데이터 생성 — 05D §3 규격을 그대로 따른다.

★★이 데이터로 학습한 어댑터의 **정확도는 품질 근거가 아니다.**
   05D §1-3 의 착수 조건 1(승인 QA ≥ 3,000)이 **0건**이라 진짜 학습을 할 수 없다.
   05D §6 이 「**완전 합성** 데이터로 파이프라인 시험」을 명시적으로 허용하므로,
   이 스크립트는 **파이프라인·VRAM·처리량을 재기 위한 것**이다.
   측정에 의미가 있으려면 **토큰 길이 분포**가 실제와 비슷해야 하므로
   근거 건수(3~5)와 조항 길이를 실제 s6 조항 분포에 맞춰 만든다.

★약관 원문을 쓰지 않는다. 조항 본문은 전부 지어낸 문장이다 —
   그래서 이 파일은 커밋해도 05D §6·CLAUDE.md §2 의 저작물 제약에 걸리지 않는다.

사용:
    python -m scripts.finetune.make_synthetic_pilot --out data/finetune/pilot_synth
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
from collections import Counter
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]

#: 05D §3-4 — seed 고정. 분할 결과 SHA-256 을 manifest 에 기록한다.
SEED = 42

#: 실제 코퍼스와 같은 축(보험사 12곳 · 세대 1~5)을 쓴다. 층별 평가(§7-3)를 하려면
#: test 에 12개사·5세대가 **모두** 있어야 하기 때문이다.
INSURERS = [
    "삼성화재", "현대해상", "DB손해보험", "KB손해보험", "메리츠화재", "흥국화재",
    "한화손해보험", "롯데손해보험", "NH농협손해보험", "MG손해보험", "AXA손해보험", "캐롯손해보험",
]
GENERATIONS = {
    1: "1세대 (구실손)", 2: "2세대 (표준화실손)", 3: "3세대 (착한실손)",
    4: "4세대 (급여/비급여 분리)", 5: "5세대 (2026 개편)",
}
VERDICTS = ["covered", "not_covered", "needs_documents", "needs_expert"]

#: 05D §3-2 — C(기권 사례)를 **별도 축**으로 둔다. 목표 600/3000 = 20%.
#: 정답만 학습시키면 모델이 "항상 답하는 쪽"으로 기운다.
ABSTAIN_RATIO = 0.20

_KCD = ["K02.1", "M51.2", "J34.2", "H25.1", "S83.5", "N20.0", "L20.9", "E11.9", "I10", "G56.0"]
_TREAT = ["치료", "수술", "입원", "통원 치료", "재활 치료", "검사"]

#: 조항 제목·본문 골격. ★전부 지어낸 문장이다(원문 아님).
_TITLES = [
    "보상하지 않는 사항", "보상하는 손해", "보험금의 지급사유", "자기부담금",
    "보장종목별 보상한도", "면책기간", "특약의 적용범위", "보험금 청구 절차",
]
_BODY = [
    "회사는 피보험자가 제{n}항에서 정한 사유로 발생한 의료비에 대하여 보험금을 지급합니다.",
    "다음 각 호의 사유로 생긴 손해에 대하여는 보상하지 않습니다. 다만 예외에 해당하는 경우에는 그러하지 아니합니다.",
    "본인부담금은 보장대상 의료비의 {p}%로 하며, 연간 누적 한도는 {q}만원으로 합니다.",
    "이 특약은 계약일로부터 {m}개월이 경과한 후 발생한 질병에 대하여 적용합니다.",
    "피보험자가 국민건강보험법에 따른 요양급여에 해당하지 않는 진료를 받은 경우의 처리는 별표에서 정합니다.",
]


def _clause_text(rng: random.Random, nonce: str) -> str:
    """조항 한 건. ★길이 분포가 목적이다 — 문장을 반복해 200~700자를 만든다.

    ★`nonce` 로 **모든 조항 본문을 서로 다르게** 만든다.
      처음엔 템플릿 풀에서만 뽑았더니 문서 간에 같은 문장이 겹쳐
      05D §3-4 검사 ①(train↔test content_hash 교집합)이 **254** 로 실패했다.
      설계서는 "①이 0 이 아니면 학습을 시작하지 않는다"고 못박고 있다.

    ★★실제 코퍼스에서는 이 문제가 **훨씬 어렵다.** 조항 중복이 66.5%이고
      한 조항이 최대 170개 문서에 실린다(CLAUDE.md §1). 문서 단위로 갈라도
      같은 조항 본문이 양쪽에 남는다 — 진짜 학습에 들어갈 때는
      **content_hash 연결성분 단위**로 묶어 갈라야 ①을 통과할 수 있다.
      합성 데이터에서 nonce 로 피한 것을 실데이터에서 피했다고 말하면 안 된다.
    """
    n = rng.randint(2, 5)
    parts = [
        rng.choice(_BODY).format(n=rng.randint(1, 9), p=rng.choice([20, 30, 40]),
                                 q=rng.choice([200, 300, 500]), m=rng.randint(3, 24))
        for _ in range(n)
    ]
    parts.append(f"(조항식별 {nonce})")
    return " ".join(parts)


def _sha12(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:12]


def _make_example(rng: random.Random, idx: int, abstain: bool) -> dict:
    insurer = rng.choice(INSURERS)
    gen = rng.randint(1, 5)
    #: 분할 키가 될 값들(§3-4). 같은 문서가 train/test 에 갈라지지 않게 하려면
    #: 문서 단위 식별자가 **예제마다 새로 생기면 안 된다** — 문서 풀에서 고른다.
    doc_pool = f"{insurer}-{gen}-{idx % 120}"
    sha = _sha12(doc_pool)
    product = f"무배당 {insurer} 실손의료비보험({1900 + gen * 10 + idx % 9}.{idx % 9 + 1})"
    product_line = f"{insurer}/{gen}"

    kcd = rng.choice(_KCD)
    question = f"{kcd} {rng.choice(_TREAT)}가 보장되나요?"

    #: 05D §3-1 — 근거는 검색·리랭크·인용게이트를 통과한 것만. tier 는 POLICY_CLAUSE 고정.
    #: 기권 사례(C)는 **근거 0건**이 핵심이다.
    n_ev = 0 if abstain else rng.randint(3, 5)
    evidence = []
    for j in range(n_ev):
        no = f"보통약관/제{rng.randint(3, 40)}조"
        body = _clause_text(rng, nonce=f"{sha}-{idx}-{j}")
        evidence.append({
            "clause_id": f"{sha}/{no}#{_sha12(body)[:8]}",
            "qualified_no": no,
            "title": rng.choice(_TITLES),
            "page_from": rng.randint(10, 90),
            "page_to": rng.randint(90, 160),
            "tier": "POLICY_CLAUSE",
            "text": body,
        })

    prompt_obj = {
        "context": {
            "insurer": insurer,
            "product_name": product,
            "generation": gen,
            "generation_label": GENERATIONS[gen],
            "enrolled_on": f"20{rng.randint(10, 25):02d}{rng.randint(1,12):02d}{rng.randint(1,28):02d}",
            "date_confidence": "exact",
            "parse_status": "ok",
        },
        "question": question,
        "evidence": evidence,
        "output_schema": "PrecheckResult@v1",
    }

    if abstain:
        target = {
            "verdict": "needs_expert",
            "abstained": True,
            "reason_code": "no_evidence",
            "message": "근거 조항을 찾지 못했습니다. 판정하지 않습니다.",
            "citations": [],
            "abstain_reason": "no_evidence",
        }
    else:
        v = rng.choice(VERDICTS[:3])
        cited = [e["clause_id"] for e in rng.sample(evidence, k=min(2, len(evidence)))]
        target = {
            "verdict": v,
            "abstained": False,
            "reason_code": rng.choice(["exception_applies", "period_not_met", "covered_basic"]),
            "message": f"{GENERATIONS[gen]} 기준으로 해당 조항을 확인했습니다. 아래 근거를 확인하세요.",
            "citations": cited,
            "abstain_reason": None,
        }

    return {
        "id": f"synth-{idx:05d}",
        "document_sha256": sha,
        "product_line": product_line,
        "axis": "C" if abstain else ("B" if idx % 5 == 0 else "A"),
        "insurer": insurer,
        "generation": gen,
        "prompt": prompt_obj,
        "target": target,
        #: 누수 검사(§3-4 ①)용. 근거 본문의 content hash 집합.
        "content_hashes": sorted({_sha12(e["text"]) for e in evidence}),
    }


def build(n: int, out: Path) -> dict:
    rng = random.Random(SEED)
    rows = [_make_example(rng, i, abstain=(rng.random() < ABSTAIN_RATIO)) for i in range(n)]

    #: ★05D §3-4 — 랜덤 70/15/15 를 **쓰지 않는다**. 분할 키는 (document_sha256, product_line).
    keys = sorted({(r["document_sha256"], r["product_line"]) for r in rows})
    rng.shuffle(keys)
    n_tr, n_va = int(len(keys) * 0.70), int(len(keys) * 0.15)
    split_of = {}
    for i, k in enumerate(keys):
        split_of[k] = "train" if i < n_tr else ("valid" if i < n_tr + n_va else "test")

    parts: dict[str, list] = {"train": [], "valid": [], "test": []}
    for r in rows:
        parts[split_of[(r["document_sha256"], r["product_line"])]].append(r)

    out.mkdir(parents=True, exist_ok=True)
    digests = {}
    for name, part in parts.items():
        p = out / f"{name}.jsonl"
        text = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in part)
        p.write_text(text, encoding="utf-8")
        digests[name] = hashlib.sha256(text.encode("utf-8")).hexdigest()

    #: ★§3-4 의 검사 5종. ①이 0 이 아니면 **학습을 시작하지 않는다**.
    def hashes(part):
        s = set()
        for r in part:
            s.update(r["content_hashes"])
        return s

    tr_h, te_h = hashes(parts["train"]), hashes(parts["test"])
    checks = {
        "①_content_hash_교집합": len(tr_h & te_h),
        "②_test_보험사_수": len({r["insurer"] for r in parts["test"]}),
        "③_test_세대_수": len({r["generation"] for r in parts["test"]}),
        "④_기권비율_train": round(sum(r["axis"] == "C" for r in parts["train"]) / max(1, len(parts["train"])), 4),
        "④_기권비율_test": round(sum(r["axis"] == "C" for r in parts["test"]) / max(1, len(parts["test"])), 4),
        "⑤_seed": SEED,
    }
    checks["④_차이_pp"] = round(abs(checks["④_기권비율_train"] - checks["④_기권비율_test"]) * 100, 2)
    checks["①_통과"] = checks["①_content_hash_교집합"] == 0
    checks["②_통과"] = checks["②_test_보험사_수"] == len(INSURERS)
    checks["③_통과"] = checks["③_test_세대_수"] == 5
    checks["④_통과"] = checks["④_차이_pp"] <= 3.0

    manifest = {
        "★경고": "완전 합성 데이터다. 정확도 결과를 품질 근거로 쓰지 않는다(05D §1-3·§6).",
        "seed": SEED,
        "총건수": len(rows),
        "분할건수": {k: len(v) for k, v in parts.items()},
        "축별건수": dict(Counter(r["axis"] for r in rows)),
        "sha256": digests,
        "분할검사_05D_3_4": checks,
    }
    (out / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=3000, help="05D §3-2 의 착수 하한과 같은 규모")
    ap.add_argument("--out", default="data/finetune/pilot_synth")
    a = ap.parse_args()
    m = build(a.n, _ROOT / a.out)
    print(json.dumps(m, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
