# -*- coding: utf-8 -*-
"""학습 데이터를 **문서·상품계열 단위로** 나누고 다섯 가지 검사를 돌린다 — 05D §3-4.

    python -m scripts.finetune.split_dataset
    python -m scripts.finetune.split_dataset --in data/finetune/qa_pilot/candidates.jsonl

★★왜 랜덤 분할을 쓰지 않나

    같은 약관 조항이 **최대 170개 문서에 그대로 실린다**(중복률 66.5%).
    행 단위로 랜덤 분할하면 **train 의 문장이 test 에 그대로 들어가** 성능이 부풀려진다.
    그래서 분할 키는 행이 아니라 `(document_sha256, product_line)` 이다.

★★이 스크립트가 왜 지금 필요한가 (2026-08-27)

    05D §12-1 이 이렇게 적어 뒀다 —
    「§3-4 분할 검사 ① 교집합 0 · 통과 — **단 편법으로.**
      합성 조항에 고유 문장을 넣어 0 을 만들었다. **실데이터에서는 통하지 않는다.**」

    즉 파인튜닝 착수 조건 2 는 **한 번도 실데이터로 확인된 적이 없다.**
    승인 QA 가 3,000건 모이길 기다렸다가 여기서 막히면 그때 되돌릴 수 없다.
    **먼저 확인한다.** 지금 있는 후보로 돌려서 게이트가 서는지 본다.

★★①이 0 이 아니면 **학습을 시작하지 않는다**(05D §3-4). 이 스크립트는 그때 0 이 아닌
  종료 코드를 낸다 — 파이프라인이 조용히 지나가지 않도록.
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import pathlib
import random
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
IN_DEFAULT = ROOT / "data" / "finetune" / "qa_pilot" / "candidates.jsonl"
OUT_DEFAULT = ROOT / "data" / "finetune" / "qa_pilot" / "split.json"

SEED = 42                       #: 05D §3-4 ⑤ — 고정한다
RATIO = (0.70, 0.15, 0.15)      #: train / valid / test

#: `A:8df70026e828:F32` · `B:823789501858` 처럼 item_id 에 sha12 가 박혀 있다.
_SHA12_IN_ID = re.compile(r"^[ABC]:([0-9a-f]{12})")
#: `8df70026e828/질병입원형/제4조#4ec64fa7` — `#` 뒤가 content_hash 앞 8자리.
_HASH_IN_CLAUSE = re.compile(r"#([0-9a-f]{6,})$")


def _manifest_lines():
    for p in sorted((ROOT / "data/raw/manifests").glob("*.jsonl")):
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.strip():
                yield json.loads(line)


def load_product_lines() -> dict[str, str]:
    """`sha12 → product_line`. 매니페스트에서 **실측으로** 만든다."""
    out: dict[str, str] = {}
    for r in _manifest_lines():
        sha = r.get("sha256")
        if sha:
            out[sha[:12]] = (r.get("product_line") or "").strip() or "(미상)"
    return out


def load_generations() -> dict[str, str]:
    """`sha12 → 세대`. 매니페스트 `generation` 을 그대로 쓴다.

    ★없는 문서는 **빈 값**으로 둔다. 「모르면 모른다」 — 4세대라고 찍지 않는다.
      실측(2026-08-27): 매니페스트 2,121행 중 `generation` 이 있는 것은 1,483행이다.
    """
    out: dict[str, str] = {}
    for r in _manifest_lines():
        sha = r.get("sha256")
        g = r.get("generation")
        if sha and g not in (None, ""):
            out[sha[:12]] = str(g)
    return out


def item_sha12(item: dict) -> str:
    ev = (item.get("evidence") or [None])[0]
    if ev and ev.get("sha12"):
        return ev["sha12"]
    m = _SHA12_IN_ID.match(item.get("item_id", ""))
    return m.group(1) if m else ""


def doc_key(item: dict, plines: dict[str, str]) -> tuple[str, str]:
    """분할 키 `(sha12, product_line)`.

    ★문서를 특정할 수 없는 항목(합성 기권 등)은 `("", stratum)` 으로 묶는다 —
      **문서 누수가 있을 수 없는 항목**이라 문서 단위로 가를 이유가 없고,
      층은 유지해야 ④ 비율 검사가 성립한다.
    """
    sha = ""
    ev = (item.get("evidence") or [None])[0]
    if ev and ev.get("sha12"):
        sha = ev["sha12"]
    else:
        m = _SHA12_IN_ID.match(item.get("item_id", ""))
        if m:
            sha = m.group(1)
    if not sha:
        return ("", item.get("stratum", ""))
    return (sha, plines.get(sha, "(미상)"))


def content_hashes(item: dict) -> set[str]:
    """이 항목이 **어느 조항 내용**을 담고 있나 — ① 교집합 검사의 재료."""
    out: set[str] = set()
    for ev in item.get("evidence") or []:
        if ev.get("content_hash"):
            out.add(ev["content_hash"])
            continue
        m = _HASH_IN_CLAUSE.search(ev.get("clause_id") or "")
        if m:
            #: ★전체 해시가 없으면 **앞자리로 비교한다.** 짧아서 우연히 겹칠 수 있으므로
            #:   교집합이 나오면 「누수 의심」으로 보고 사람이 확인한다 — 통과시키지 않는다.
            out.add("~" + m.group(1))
    return out


class _UF:
    """묶음 찾기 — 문서나 조항 내용을 공유하면 **같은 덩어리**다."""

    def __init__(self):
        self.p: dict = {}

    def find(self, x):
        self.p.setdefault(x, x)
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[rb] = ra


def components(items: list[dict], plines: dict[str, str]) -> dict[int, list[int]]:
    """분할의 **원자 단위**를 만든다.

    ★★2026-08-27 — 처음엔 `(document_sha256, product_line)` 만으로 나눴다(05D §3-4 문구 그대로).
      실데이터로 돌리니 **① 조항 내용 교집합이 6개** 나왔다. 문서를 갈라도
      **같은 조항이 여러 문서에 실려** 내용이 양쪽으로 샌다(중복률 66.5% · 최대 170문서).
      05D §12-1 이 「합성으로는 0 을 만들었지만 실데이터에서는 통하지 않는다」고
      적어 둔 것이 바로 이것이다.

    ★그래서 **문서와 조항 내용을 같은 그래프에 넣고 연결 성분을 뽑는다.**
      문서를 공유하거나 조항 내용을 공유하면 한 덩어리이고, 덩어리는 통째로 한 칸에 간다.
      ①을 정의상 0 으로 만드는 유일한 방법이다.

    ★문서도 조항도 없는 항목(합성 기권)은 **각자 한 덩어리**다 — 샐 것이 없으므로
      비율을 맞추는 데 자유롭게 쓴다.
    """
    uf = _UF()
    for i, it in enumerate(items):
        node = ("item", i)
        uf.find(node)
        k = doc_key(it, plines)
        if k[0]:
            uf.union(node, ("doc", k))
        for h in content_hashes(it):
            uf.union(node, ("hash", h))

    comp: dict = {}
    for i in range(len(items)):
        comp.setdefault(uf.find(("item", i)), []).append(i)
    return comp


def split(items: list[dict], plines: dict[str, str], seed: int = SEED):
    comp = components(items, plines)
    groups = {k: [items[i] for i in v] for k, v in comp.items()}

    total = len(items)
    want = {"train": total * RATIO[0], "valid": total * RATIO[1], "test": total * RATIO[2]}
    got = {"train": 0, "valid": 0, "test": 0}
    assign: dict = {}

    def insurers_of(v):
        s = set()
        for i in v:
            for ev in i.get("evidence") or []:
                if ev.get("insurer"):
                    s.add(ev["insurer"])
        return s

    all_ins = insurers_of(items)
    test_ins: set = set()
    c_count = {"train": 0, "test": 0, "valid": 0}

    gens = load_generations()

    def gens_of(v):
        return {gens[s] for s in (item_sha12(i) for i in v) if s in gens}

    all_gen = gens_of(items)
    test_gen: set = set()

    rnd = random.Random(seed)
    keys = sorted(groups, key=lambda k: (-len(groups[k]), str(k)))

    #: ★★① 은 성분 단위 배정만으로 **정의상** 지켜진다.
    #:   남은 것은 ②(보험사)·③(세대)·④(기권 비율)이고, 이건 **배정 순서**로 푼다.
    #:
    #:   ★처음엔 큰 것부터 넣으면서 「test 에 없는 보험사면 test 로」 로 했다.
    #:     그랬더니 test 가 24% 로 넘치고도 보험사 2곳이 빠졌다 — 넘친 뒤엔 못 당기고,
    #:     빠진 보험사의 성분은 작아서 **순서상 뒤에 있었기 때문**이다.
    #:   ★그래서 **커버리지를 먼저 채운다.** 보험사·세대마다 **가장 작은 성분**을
    #:     하나씩 test 에 넣고, 그 다음에 나머지를 비율로 채운다.
    #:     test 를 덜 먹으면서 커버리지를 확보하는 순서다.
    placed: set = set()
    for need_all, of in ((all_ins, insurers_of), (all_gen, gens_of)):
        for want_one in sorted(need_all):
            covered = of([i for k2 in placed for i in groups[k2]])
            if want_one in covered:
                continue
            cands = [k for k in keys if k not in placed and want_one in of(groups[k])]
            if not cands:
                continue
            k = min(cands, key=lambda k: len(groups[k]))
            assign[k] = "test"
            placed.add(k)
            got["test"] += len(groups[k])
            test_ins |= insurers_of(groups[k])
            test_gen |= gens_of(groups[k])
            c_count["test"] += sum(1 for i in groups[k] if i.get("axis") == "C")

    for k in keys:
        if k in placed:
            continue
        v = groups[k]
        room = {c: (want[c] - got[c]) / max(1.0, want[c]) for c in got}
        cell = max(room, key=lambda c: room[c])
        assign[k] = cell
        got[cell] += len(v)
        if cell == "test":
            test_ins |= insurers_of(v)
            test_gen |= gens_of(v)
        c_count[cell] += sum(1 for i in v if i.get("axis") == "C")

    #: ④ 기권 비율 — **문서가 없는 합성 기권 덩어리**만 옮겨서 맞춘다(샐 것이 없으므로 안전).
    movable = [k for k in groups
               if len(groups[k]) == 1 and not (groups[k][0].get("evidence") or [])
               and groups[k][0].get("axis") == "C"]
    rnd.shuffle(movable)
    for k in movable:
        rate = lambda c: (c_count[c] / got[c] * 100) if got[c] else 0.0
        if abs(rate("train") - rate("test")) <= 2.0:
            break
        src, dst = ("train", "test") if rate("train") > rate("test") else ("test", "train")
        if assign[k] != src or got[src] <= 1:
            continue
        assign[k] = dst
        got[src] -= 1
        got[dst] += 1
        c_count[src] -= 1
        c_count[dst] += 1

    out = {"train": [], "valid": [], "test": []}
    for k, v in groups.items():
        out[assign[k]].extend(v)
    return out, groups, assign


def checks(out: dict[str, list[dict]], items: list[dict]) -> list[tuple[str, bool, str]]:
    res = []

    #: ① content_hash 교집합 = 0
    hs = {c: set().union(*(content_hashes(i) for i in v)) if v else set()
          for c, v in out.items()}
    inter = (hs["train"] & hs["test"]) | (hs["train"] & hs["valid"]) | (hs["valid"] & hs["test"])
    res.append(("① 조항 내용 교집합 = 0", not inter,
                "없음" if not inter else f"{len(inter)}개 겹침 예: {sorted(inter)[:3]}"))

    #: ② 보험사가 test 에 모두
    def insurers(v):
        s = set()
        for i in v:
            for ev in i.get("evidence") or []:
                if ev.get("insurer"):
                    s.add(ev["insurer"])
        return s
    all_ins, test_ins = insurers(items), insurers(out["test"])
    res.append(("② 보험사가 test 에 모두 있나", not (all_ins - test_ins),
                f"전체 {len(all_ins)} · test {len(test_ins)}"
                + (f" · 빠짐 {sorted(all_ins - test_ins)}" if all_ins - test_ins else "")))

    #: ③ 세대 — 매니페스트에서 문서별 세대를 끌어와 본다.
    gens = load_generations()

    def gens_of(v):
        return {gens[s] for s in (item_sha12(i) for i in v) if s in gens}

    all_g, test_g = gens_of(items), gens_of(out["test"])
    #: ★세대를 못 붙인 항목이 얼마나 되는지 **함께 적는다.** 분모를 숨기지 않는다.
    unknown = sum(1 for i in items if item_sha12(i) not in gens)
    res.append(("③ 세대가 test 에 모두 있나", bool(all_g) and not (all_g - test_g),
                f"전체 {sorted(all_g)} · test {sorted(test_g)}"
                + (f" · 빠짐 {sorted(all_g - test_g)}" if all_g - test_g else "")
                + f" · 세대 미상 항목 {unknown}/{len(items)}"))

    #: ④ 기권(C) 비율이 train/test 에서 ±3%p 이내
    def crate(v):
        return (sum(1 for i in v if i.get("axis") == "C") / len(v)) if v else 0.0
    gap = abs(crate(out["train"]) - crate(out["test"])) * 100
    res.append(("④ 기권 비율 차이 ≤ 3%p", gap <= 3.0,
                f"train {crate(out['train'])*100:.1f}% · test {crate(out['test'])*100:.1f}%"
                f" · 차이 {gap:.1f}%p"))

    #: ⑤ seed 고정 · 결과 SHA-256
    ids = "|".join(sorted(f"{c}:{i['item_id']}" for c, v in out.items() for i in v))
    res.append(("⑤ 분할 결과 SHA-256", True,
                hashlib.sha256(ids.encode("utf-8")).hexdigest()[:16] + f" (seed {SEED})"))
    return res


def main() -> int:
    ap = argparse.ArgumentParser(description="05D §3-4 분할과 다섯 검사")
    ap.add_argument("--in", dest="src", default=str(IN_DEFAULT))
    ap.add_argument("--out", default=str(OUT_DEFAULT))
    args = ap.parse_args()

    items = [json.loads(l) for l in
             pathlib.Path(args.src).read_text(encoding="utf-8").splitlines() if l.strip()]
    plines = load_product_lines()
    out, groups, assign = split(items, plines)

    sizes = sorted((len(v) for v in groups.values()), reverse=True)
    print(f"입력 {len(items)}건 · 연결 성분 {len(groups)}개 "
          f"· 가장 큰 성분 {sizes[0]}건({sizes[0]/len(items)*100:.0f}%)")
    for c in ("train", "valid", "test"):
        print(f"  {c:6s} {len(out[c]):4d}건 ({len(out[c])/len(items)*100:4.1f}%)"
              f" · 키 {sum(1 for k, v in assign.items() if v == c)}개")
    print()
    print("★성분 크기 상위")
    for n in sizes[:6]:
        print(f"   {n:4d}건")
    print()

    res = checks(out, items)
    ok = True
    for name, passed, detail in res:
        mark = "통과" if passed else "★실패"
        print(f"  {mark:5s} {name:32s} {detail}")
        ok = ok and passed

    pathlib.Path(args.out).write_text(json.dumps({
        "seed": SEED, "ratio": list(RATIO), "source": str(pathlib.Path(args.src).name),
        "counts": {c: len(v) for c, v in out.items()},
        "checks": [{"name": n, "passed": p, "detail": d} for n, p, d in res],
        "assignment": {c: sorted(i["item_id"] for i in v) for c, v in out.items()},
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print()
    print(f"작성: {args.out}")

    if not ok:
        #: ★조용히 지나가지 않는다. ①이 0 이 아니면 학습을 시작하면 안 된다(05D §3-4).
        print()
        print("★검사에 실패한 항목이 있습니다 — 이 상태로 학습을 시작하지 않습니다.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
