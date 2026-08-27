# -*- coding: utf-8 -*-
"""답변의 **검증 가능한 조각**이 인용 안에 실제로 있는지 대조한다.

    python -m scripts.finetune.machine_groundedness
    python -m scripts.finetune.machine_groundedness --pred data/finetune/results/predictions.jsonl

★★**이것은 `groundedness` 가 아니라 그 대용지표(proxy)다.**

    05D §7-2 의 `groundedness` 는 「답변의 각 **주장**이 제공된 근거로 검증되는가」다.
    주장을 알아보고 함의를 판단하는 것은 **기계가 못 한다.**

    대신 기계가 **확실히** 할 수 있는 것이 있다 — 답변에 나온
    **숫자 · 질병기호 · 조항번호**가 인용문 안에 **실제로 있는지** 대조하는 일이다.
    이건 글자 대조라 오류가 없고, 틀리면 **그건 확실히 근거 없는 말**이다.

    ★그래서 이 지표는 **한쪽으로만 확실하다.**
      「걸렸다」 → 근거에 없는 것을 말했다. **확실하다.**
      「안 걸렸다」 → 근거 없는 주장이 **없다는 뜻이 아니다.**
        숫자·기호를 안 쓰고 틀리게 말하는 것은 이 검사가 못 잡는다.

    ★리포트에 쓸 때 이 비대칭을 반드시 함께 적는다. 「사람 검수를 대신했다」가 아니다.

★검사 다섯

    ① 숫자        답변의 금액·비율이 인용(또는 질문·가입정보)에 있나
    ② 질병기호    답변이 댄 KCD 가 인용에 **직접 또는 범위로** 있나
    ③ 조항번호    답변이 댄 「제N조」가 인용된 조항과 같나
    ④ 빈근거단정  인용이 0건인데 결론을 단정하나
    ⑤ 어휘        답변의 내용 낱말 중 인용·질문 어디에도 없는 비율
"""

from __future__ import annotations

import argparse
import collections
import json
import pathlib
import random
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
PRED_DEFAULT = ROOT / "data" / "finetune" / "results_run2" / "predictions.jsonl"
GOLD_DEFAULT = ROOT / "data" / "finetune" / "sft" / "gold.jsonl"
OUT_DEFAULT = ROOT / "data" / "finetune" / "results_run2" / "machine_groundedness.json"
BOOT = 10000
SEED = 20260827

#: 약관은 질병기호를 범위로 적는다(`F04~F99`). 낱개만 찾으면 못 찾는다.
_RANGE = re.compile(r"([A-Z])\s*(\d{2})(?:\.\d+)?\s*[~∼\-–]\s*([A-Z])?\s*(\d{2})")
_CODE = re.compile(r"\b([A-Z]\d{2})(?:\.\d+)?\b")
_CLAUSE = re.compile(r"제\s?(\d+)\s?조")
#: 금액·비율. 「1만 5천원」·「20%」·「8천원」 같은 형태를 잡는다.
_NUM = re.compile(r"\d[\d,]*\s?(?:%|퍼센트|원|만원|천원|배|일|개월|년)")
_ASSERT = ["보장되지 않습니다", "보상되지 않습니다", "판매기간 밖입니다", "해당하지 않습니다",
           "보장됩니다", "지급됩니다", "면책입니다", "보상하지 않습니다"]
#: 내용어만 본다 — 조사·접속사는 근거 대조에 의미가 없다.
_STOP = set("그리고 그러나 다만 또는 이는 해당 관련 경우 대해 대한 통해 위해 따라 따른 있습니다 "
            "없습니다 합니다 입니다 됩니다 확인 필요 가능 여부 내용 사항 기준 조건 부분 이상 "
            "이하 등의 등에 등을 등과 것은 것을 것이 수도 우리 저희 고객 보험 약관 조항".split())


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


def code_in(code: str, text: str) -> bool:
    """질병기호가 인용에 **직접 또는 범위로** 있나."""
    flat = _norm(text)
    if code in flat:
        return True
    letter, num = code[0], int(code[1:3])
    for m in _RANGE.finditer(flat):
        a, an, b, bn = m.group(1), int(m.group(2)), m.group(3) or m.group(1), int(m.group(4))
        if a == letter == b and an <= num <= bn:
            return True
    return False


_YMD = re.compile(r"\b(\d{4})(\d{2})(\d{2})\b")


def _expand_dates(text: str) -> str:
    """`20250815` 를 사람이 읽는 형태로 함께 넣는다.

    ★★2026-08-27 실측 — 이 처리가 없어서 **6건을 전부 오탐**으로 잡았다.
      어댑터가 가입일 `20250815` 를 「2025년 8월 15일」로 풀어 쓴 것을
      「인용에 없는 숫자」라고 신고했다. 그 답변들은 **사람 답과 글자까지 같았다.**
      ★검사가 틀리면 좋은 답을 나쁘다고 말한다 — 지표가 거짓말하는 쪽이다.
    """
    out = [text]
    for m in _YMD.finditer(text):
        y, mo, d = m.group(1), int(m.group(2)), int(m.group(3))
        out.append(f"{y}년{mo}월{d}일 {y}년 {mo}월 {d}일 {mo}월{d}일 {d}일 {mo}월")
    return " ".join(out)


def check(answer: str, evidence: str, question: str, request_line: str) -> dict:
    """답변에서 **확인 가능한 조각**만 뽑아 인용과 대조한다."""
    hay = _norm(_expand_dates(evidence + question + request_line))
    hits: list[str] = []

    #: ① 숫자
    nums = [_norm(x) for x in _NUM.findall(answer) or []]
    nums = [_norm(m.group(0)) for m in _NUM.finditer(answer)]
    bad_num = [n for n in dict.fromkeys(nums) if n not in hay]

    #: ② 질병기호 — 답변이 댄 코드가 인용에 있나
    codes = [m.group(1) for m in _CODE.finditer(answer)]
    bad_code = [c for c in dict.fromkeys(codes)
                if not code_in(c, evidence) and c not in _norm(question + request_line)]

    #: ③ 조항번호
    cls = [m.group(1) for m in _CLAUSE.finditer(answer)]
    ev_cls = {m.group(1) for m in _CLAUSE.finditer(evidence)}
    bad_clause = [c for c in dict.fromkeys(cls) if c not in ev_cls] if evidence.strip() else cls

    #: ④ 인용이 없는데 단정
    bad_assert = [] if evidence.strip() else [a for a in _ASSERT if a in answer]

    #: ⑤ 어휘 — 두 글자 이상 한글 낱말 중 어디에도 없는 것
    words = [w for w in re.findall(r"[가-힣]{2,}", answer) if w not in _STOP]
    uniq = list(dict.fromkeys(words))
    unseen = [w for w in uniq if w not in hay]
    oov = round(len(unseen) / len(uniq), 4) if uniq else 0.0

    if bad_num:
        hits.append(f"숫자:{','.join(bad_num[:3])}")
    if bad_code:
        hits.append(f"질병기호:{','.join(bad_code[:3])}")
    if bad_clause:
        hits.append(f"조항번호:제{','.join(bad_clause[:3])}조")
    if bad_assert:
        hits.append(f"빈근거단정:{bad_assert[0]}")
    return {"hits": hits, "oov": oov, "n_word": len(uniq),
            "has_ev": bool(evidence.strip()),
            "bad_num": len(bad_num), "bad_code": len(bad_code),
            "bad_clause": len(bad_clause), "bad_assert": len(bad_assert)}


def ci(diffs, rnd, boot=BOOT):
    n = len(diffs)
    means = sorted(sum(diffs[rnd.randrange(n)] for _ in range(n)) / n for _ in range(boot))
    return means[int(boot * 0.025)], means[int(boot * 0.975)]


def main() -> int:
    ap = argparse.ArgumentParser(description="기계 근거 대조(대용지표)")
    ap.add_argument("--pred", default=str(PRED_DEFAULT))
    ap.add_argument("--gold", default=str(GOLD_DEFAULT))
    ap.add_argument("--out", default=str(OUT_DEFAULT))
    args = ap.parse_args()

    L = lambda p: [json.loads(l) for l in pathlib.Path(p).read_text(encoding="utf-8").splitlines()
                   if l.strip()]
    preds = L(args.pred)
    gold = {r["item_id"]: r for r in L(args.gold)}

    rows, skipped = [], 0
    for p in preds:
        g = gold.get(p["item_id"])
        if not g:
            skipped += 1
            continue
        user = g["messages"][1]["content"]
        q = user.split("[질문]" + chr(10), 1)[-1].split(chr(10) + chr(10), 1)[0]
        req = ""
        if "[가입정보]" in user:
            req = user.split("[가입정보]" + chr(10), 1)[1].split(chr(10), 1)[0]
        ev = ""
        if "[인용 조항]" in user:
            tail = user.split("[인용 조항]", 1)[1]
            ev = tail.split(chr(10), 1)[1] if chr(10) in tail else ""
            if ev.strip() == "없음":
                ev = ""
        rows.append((p, q, req, ev))
    if skipped:
        print(f"[경고] gold 에 없어 건너뛴 예측 {skipped}건")

    res, per = {}, {}
    for col in ("baseline", "adapter"):
        flags, oovs = [], []
        #: ★★인용이 0건인 항목은 **대조할 것이 없다.** 거기서 나온 미출현율은
        #:   「근거를 안 썼다」가 아니라 「근거가 없었다」다. 섞으면 지표가 거짓말한다.
        oov_ev = []
        kinds = collections.Counter()
        by_axis = collections.defaultdict(lambda: {"n": 0, "bad": 0, "oov": [], "ev": 0})
        for p, q, req, ev in rows:
            c = check(p[col], ev, q, req)
            f = 1 if c["hits"] else 0
            flags.append(f)
            oovs.append(c["oov"])
            if c["has_ev"]:
                oov_ev.append(c["oov"])
            for h in c["hits"]:
                kinds[h.split(":")[0]] += 1
            a = by_axis[p["axis"]]
            a["n"] += 1
            a["bad"] += f
            if c["has_ev"]:
                a["oov"].append(c["oov"])
                a["ev"] += 1
        n = len(rows)
        res[col] = {
            "근거밖 조각이 있는 답변": sum(flags),
            "비율": round(sum(flags) / n, 4),
            "종류별": dict(kinds),
            "어휘 미출현율(인용 있는 항목만)": (round(sum(oov_ev) / len(oov_ev), 4)
                                       if oov_ev else None),
            "인용 있는 항목": len(oov_ev),
            "축별": {k: {"n": v["n"], "근거밖": v["bad"], "인용있음": v["ev"],
                        "어휘미출현": (round(sum(v["oov"]) / len(v["oov"]), 4)
                                  if v["oov"] else None)}
                    for k, v in sorted(by_axis.items())},
        }
        per[col] = (flags, oov_ev)

    rnd = random.Random(SEED)
    d_flag = [a - b for a, b in zip(per["adapter"][0], per["baseline"][0])]
    d_oov = [a - b for a, b in zip(per["adapter"][1], per["baseline"][1])]
    lo1, hi1 = ci(d_flag, rnd)
    lo2, hi2 = ci(d_oov, rnd)
    say = lambda lo, hi: "차이 확인" if (lo > 0 or hi < 0) else "차이를 확인하지 못함"

    out = {
        "★이것은 무엇인가": (
            "`groundedness` 가 아니라 **대용지표**다. 답변의 숫자·질병기호·조항번호가 "
            "인용 안에 실제로 있는지 글자로 대조한다."),
        "★한쪽으로만 확실하다": (
            "걸리면 근거에 없는 말을 한 것이 **확실하다**. "
            "안 걸린다고 근거 없는 주장이 **없다는 뜻은 아니다** — "
            "숫자·기호를 안 쓰고 틀리게 말하는 것은 못 잡는다. 사람 검수를 대신하지 않는다."),
        "표본": len(rows), "bootstrap": BOOT, "seed": SEED,
        "baseline": res["baseline"], "adapter": res["adapter"],
        "차이": {
            "근거밖 비율": {
                "baseline": res["baseline"]["비율"], "adapter": res["adapter"]["비율"],
                "차이": round(sum(d_flag) / len(d_flag), 4),
                "95% 구간": [round(lo1, 4), round(hi1, 4)], "판정": say(lo1, hi1)},
            "어휘 미출현율(인용 있는 항목만)": {
                "baseline": res["baseline"]["어휘 미출현율(인용 있는 항목만)"],
                "adapter": res["adapter"]["어휘 미출현율(인용 있는 항목만)"],
                "차이": round(sum(d_oov) / len(d_oov), 4),
                "95% 구간": [round(lo2, 4), round(hi2, 4)], "판정": say(lo2, hi2)},
        },
    }
    pathlib.Path(args.out).write_text(
        json.dumps(out, ensure_ascii=False, indent=2) + chr(10), encoding="utf-8", newline="\n")

    print(f"표본 {len(rows)}건")
    print()
    for col in ("baseline", "adapter"):
        r = res[col]
        print(f"{col:9s} 근거밖 조각 {r['근거밖 조각이 있는 답변']:3d}/{len(rows)}"
              f" ({r['비율']*100:5.1f}%) · 어휘 미출현(인용 있는 {r['인용 있는 항목']}건)"
              f" {r['어휘 미출현율(인용 있는 항목만)']*100:5.1f}% · {r['종류별']}")
    print()
    for k, v in out["차이"].items():
        print(f"{k}: {v['baseline']} → {v['adapter']}  차이 {v['차이']:+.4f}"
              f"  95% [{v['95% 구간'][0]:+.4f}, {v['95% 구간'][1]:+.4f}] → {v['판정']}")
    print()
    print(f"{'축':4s} {'n':>4s}  {'근거밖 base→ft':16s} {'어휘미출현 base→ft'}")
    for ax in sorted(res["baseline"]["축별"]):
        b, a = res["baseline"]["축별"][ax], res["adapter"]["축별"][ax]
        ob = "—" if b["어휘미출현"] is None else f"{b['어휘미출현']:.3f}"
        oa = "—" if a["어휘미출현"] is None else f"{a['어휘미출현']:.3f}"
        print(f"{ax:4s} {b['n']:4d}(인용{b['인용있음']:3d})  {b['근거밖']:3d} → {a['근거밖']:3d}"
              f"     {ob} → {oa}")
    print()
    print(f"작성: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
