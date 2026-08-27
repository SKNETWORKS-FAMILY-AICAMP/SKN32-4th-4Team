# -*- coding: utf-8 -*-
"""s6 재추출이 끝나기를 기다렸다가 전처리 시각화를 통째로 다시 만든다.

    python regen_viz.py [--tag s6] [--no-wait]

★기다리는 조건 (둘 다 만족해야 한다)
    1. `scripts.extract.run_all` 프로세스가 없다
    2. 산출물 개수가 60초 동안 늘지 않는다
  하나만 보면 안 된다 — 프로세스가 죽어도 미완일 수 있고,
  잠깐 멈춘 것을 완료로 오인할 수도 있다.

★Tad 캡처는 여기서 하지 않는다. GUI 자동화는 무인 실행에서 깨진다.
  데이터·HTML 까지 만들고, 캡처는 사람이 확인하며 따로 붙인다.
"""
from __future__ import annotations

import argparse
import collections
import glob
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

#: ★스크래치패드에 있던 것을 저장소로 옮겼다. 임시 폴더는 지워진다 —
#:   핸드오프 산출물의 생성기가 거기 있으면 재생성이 불가능해진다.
ROOT = str(Path(__file__).resolve().parents[2])
os.chdir(ROOT)
sys.path.insert(0, ROOT)

WS = re.compile(r"\s+")
HEAD = re.compile(
    r"^\s*(?:제\s*\d{1,3}\s*조(?:\s*의\s*\d{1,2})?|\d{1,3}(?:-\d{1,2})?\s*[.．])"
    r"\s*(?:[（(\[【][^)）\]】\n]{0,60}[)）\]】])?\s*"
)
MENTION = re.compile(r"제\s*(\d{1,3})\s*조(?:\s*의\s*(\d{1,2}))?")
NUM = re.compile(r"^\s*(?:제\s*(\d{1,3})\s*조|(\d{1,3})\s*[.．])")
PUA = re.compile("[\U000F0000-\U000FFFFD]")


def norm(s: str) -> str:
    return WS.sub(" ", s or "").strip()


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def files_for(tag: str) -> list[str]:
    return sorted(glob.glob(f"data/structured/*/{tag}_*/*.clauses.json"))


def wait_until_done(tag: str) -> None:
    """추출이 끝날 때까지 기다린다."""
    log(f"{tag} 완료 대기 시작")
    stable_since = None
    last = -1
    while True:
        running = "run_all" in subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" |"
             " Select-Object -ExpandProperty CommandLine) -join ' '"],
            capture_output=True, text=True, errors="replace").stdout
        n = len(files_for(tag))
        if n != last:
            log(f"  {n:,}건 (추출 {'진행 중' if running else '프로세스 없음'})")
            last, stable_since = n, None
        elif stable_since is None:
            stable_since = time.time()

        if not running and stable_since and time.time() - stable_since > 60:
            log(f"  → 완료 판정. {n:,}건")
            return
        time.sleep(20)


def build_parquets(tag: str):
    import pandas as pd

    log("매니페스트에서 세대 결합")
    gen = {}
    for f in glob.glob("data/raw/manifests/*.jsonl"):
        for line in open(f, encoding="utf-8"):
            if not line.strip():
                continue
            r = json.loads(line)
            s = r.get("sha256", "")
            if s and s not in gen:
                lb = (r.get("generation_label") or "").strip()
                m = re.match(r"(\d)\s*세대", lb)
                gen[s] = (
                    f"{m.group(1)}세대" if m
                    else (f"{r['generation']}세대" if str(r.get("generation", "")).isdigit() else "unknown")
                )

    docs, lens, clauses = [], [], []
    files = files_for(tag)
    log(f"{tag} 문서 {len(files):,}건 읽는 중")
    for i, f in enumerate(files, 1):
        if i % 300 == 0:
            log(f"  {i:,}/{len(files):,}")
        d = json.load(open(f, encoding="utf-8"))
        s, st = d.get("source", {}), d.get("stats", {})
        sha = s.get("sha256", "")
        base = dict(
            sha12=sha[:12], insurer=s.get("insurer", "?"), generation=gen.get(sha, "unknown"),
            product=(s.get("product_name") or "")[:80],
            parse_status=d.get("parse_status"), numbering=d.get("numbering"),
        )
        cs = d.get("clauses", [])
        docs.append({**base,
                     "pages": st.get("pages", 0), "clauses": st.get("clauses", 0),
                     "paragraphs": st.get("paragraphs", 0), "items": st.get("items", 0),
                     "statute": st.get("statute_clauses", 0),
                     "ambiguous": st.get("ambiguous_paragraph_citations", 0),
                     "unresolved": st.get("unresolved_paragraphs", 0),
                     "toc_excluded": st.get("toc_pages_excluded", 0),
                     "max_clause_len": max((c.get("char_length", 0) for c in cs), default=0)})
        for c in cs:
            lens.append(c.get("char_length", 0))
            loc = c.get("locator") or {}
            clauses.append({**base,
                            "ordinal": c.get("ordinal"), "section": c.get("section", ""),
                            "clause_no": c.get("clause_no", ""), "title": c.get("title", ""),
                            "citation": c.get("citation", ""), "statute": bool(c.get("statute")),
                            "chunk_type": c.get("chunk_type", "clause"),
                            "para_n": c.get("paragraph_count", 0),
                            "para_ambiguous": bool(c.get("paragraph_no_ambiguous")),
                            "unresolved": c.get("unresolved_paragraphs", 0),
                            "page_from": loc.get("page_from"), "page_to": loc.get("page_to"),
                            "char_length": c.get("char_length", 0),
                            "content_hash": (c.get("content_hash") or "")[:16],
                            "text": c.get("text", "")})

    os.makedirs("data/exports", exist_ok=True)
    dfd = pd.DataFrame(docs)
    dfd["clauses_per_page"] = (dfd.clauses / dfd.pages.replace(0, pd.NA)).round(2)
    dfd.to_parquet(f"data/exports/{tag}_documents.parquet", index=False)
    pd.DataFrame({"char_length": lens}).to_parquet(f"data/exports/{tag}_clause_lengths.parquet", index=False)

    dfc = pd.DataFrame(clauses)
    dfc = dfc.join(dfc.groupby("content_hash")["sha12"].nunique().rename("reuse_docs"), on="content_hash")
    dfc.to_parquet(f"data/exports/{tag}_clauses.parquet", index=False, compression="zstd")
    log(f"parquet 저장 — 문서 {len(dfd):,} · 조항 {len(dfc):,}")
    return dfd, dfc


def build_kcd(dfc, tag):
    import pandas as pd
    from app.core.domain.kcd_ranges import scan_clause

    CH = [("A00", "B99", "1 감염성·기생충"), ("C00", "D48", "2 신생물"), ("D50", "D89", "3 혈액·면역"),
          ("E00", "E90", "4 내분비·대사"), ("F00", "F99", "5 정신·행동"), ("G00", "G99", "6 신경"),
          ("H00", "H59", "7 눈"), ("H60", "H95", "8 귀"), ("I00", "I99", "9 순환"), ("J00", "J99", "10 호흡"),
          ("K00", "K93", "11 소화"), ("L00", "L99", "12 피부"), ("M00", "M99", "13 근골격"),
          ("N00", "N99", "14 비뇨생식"), ("O00", "O99", "15 임신·출산"), ("P00", "P96", "16 출생전후기"),
          ("Q00", "Q99", "17 선천기형"), ("R00", "R99", "18 증상·징후"), ("S00", "T98", "19 손상·중독"),
          ("V01", "Y98", "20 외인"), ("Z00", "Z99", "21 보건서비스"), ("U00", "U99", "22 특수목적")]
    CHK = [((a[0], int(a[1:3])), (b[0], int(b[1:3])), n) for a, b, n in CH]

    def chapter(code):
        k = (code.letter, code.number)
        for lo, hi, n in CHK:
            if lo <= k <= hi:
                return n
        return "기타"

    ya = dfc[(~dfc.statute) & (dfc.chunk_type != "page_fallback")].drop_duplicates("content_hash")
    cand = ya[ya.text.str.contains(re.compile(r"[A-Z]\d{2}"), regex=True, na=False)]
    log(f"KCD 스캔 — 고유 {len(ya):,} 중 사전필터 통과 {len(cand):,}")
    cnt = collections.Counter()
    for t in cand.text:
        for m in scan_clause(t):
            cnt[(chapter(m.range.lo), m.kind)] += 1
    pd.DataFrame([{"chapter": c, "kind": k, "n": v} for (c, k), v in cnt.items()]) \
        .to_parquet(f"data/exports/kcd_chapter_kind.parquet", index=False)
    log(f"KCD 범위 언급 {sum(cnt.values()):,}")


def build_refmatrix(dfc):
    import pandas as pd

    d = dfc[(~dfc.statute) & (dfc.chunk_type != "page_fallback")]
    best = None
    for sha, g in d.groupby("sha12"):
        nums = collections.Counter()
        for cn in g.clause_no:
            m = NUM.match(str(cn))
            n = (m.group(1) or m.group(2)) if m else None
            if n:
                nums[n] += 1
        amb = tot = 0
        for t in g.text:
            h = HEAD.match(t)
            body = t[h.end():] if h else t
            for m in MENTION.finditer(body):
                tot += 1
                if nums.get(m.group(1), 0) >= 2:
                    amb += 1
        if tot and (best is None or amb > best[1]):
            best = (sha, amb, tot, g)
    sha, amb, tot, g = best
    nums = collections.Counter()
    for cn in g.clause_no:
        m = NUM.match(str(cn))
        n = (m.group(1) or m.group(2)) if m else None
        if n:
            nums[n] += 1
    cells = collections.Counter()
    for sec, t in zip(g.section, g.text):
        h = HEAD.match(t)
        body = t[h.end():] if h else t
        for m in MENTION.finditer(body):
            cells[(sec, int(m.group(1)))] += 1
    pd.DataFrame([{"section": s, "target_no": n, "refs": v, "dup": nums.get(str(n), 0)}
                  for (s, n), v in cells.items()]).to_parquet("data/exports/ref_matrix_top.parquet", index=False)
    json.dump({"sha12": sha, "insurer": g.insurer.iloc[0], "amb": amb, "tot": tot, "clauses": len(g)},
              open("data/exports/ref_matrix_meta.json", "w", encoding="utf-8"), ensure_ascii=False)
    log(f"준용 인접행렬 — {sha} 참조 {tot:,} 중 모호 {amb:,}")


def build_views(dfc, tag):
    import pandas as pd

    out = "data/exports/views"
    os.makedirs(out, exist_ok=True)
    df = dfc.copy()
    df["text_head240"] = df.text.fillna("").map(lambda s: norm(s)[:240])

    def pua_cols(t):
        m = PUA.search(t or "")
        if not m:
            return pd.Series({"pua_codepoint": "", "pua_snippet": ""})
        i = m.start()
        return pd.Series({"pua_codepoint": "U+%05X" % ord(t[i]),
                          "pua_snippet": norm(t[max(0, i - 60):i + 100])})

    V = {}
    V["v1_clause_boundary"] = df[(~df.statute) & (df.chunk_type == "clause") & (df.char_length > 30000)] \
        .sort_values(["char_length", "sha12", "ordinal"], ascending=[False, True, True]) \
        [["sha12", "insurer", "title", "page_from", "page_to", "char_length", "text_head240"]]
    V["v2_page_fallback"] = df[df.chunk_type == "page_fallback"].sort_values(["sha12", "page_from"]) \
        [["insurer", "product", "sha12", "page_from", "parse_status", "clause_no", "text_head240"]]
    V["v3_reuse"] = df[(~df.statute) & (df.chunk_type == "clause") & (df.reuse_docs >= 100)] \
        .sort_values(["reuse_docs", "content_hash"], ascending=[False, True]) \
        [["insurer", "generation", "product", "content_hash", "reuse_docs", "title", "text_head240"]]
    V["v4_para_marker"] = df[(df.para_ambiguous) & (df.unresolved == 0) &
                             (df.char_length.between(150, 1200))].sort_values(["char_length", "content_hash"]) \
        [["sha12", "ordinal", "clause_no", "title", "para_n", "text_head240"]]
    v5 = df[df.unresolved > 0].copy()
    if len(v5):
        v5 = v5.join(v5.text.apply(pua_cols))
        V["v5_pua"] = v5.sort_values(["unresolved", "char_length"], ascending=[False, False]) \
            [["sha12", "ordinal", "clause_no", "unresolved", "pua_codepoint", "pua_snippet"]]
    for k, v in V.items():
        v.reset_index(drop=True).to_parquet(f"{out}/{k}.parquet", index=False, compression="zstd")
        log(f"  뷰 {k:22} {len(v):,}행")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="s6")
    ap.add_argument("--no-wait", action="store_true")
    a = ap.parse_args()

    if not a.no_wait:
        wait_until_done(a.tag)

    n = len(files_for(a.tag))
    if n < 100:
        log(f"★{a.tag} 산출물이 {n}건뿐이다. 중단한다.")
        return 1

    dfd, dfc = build_parquets(a.tag)
    build_kcd(dfc, a.tag)
    build_refmatrix(dfc)
    build_views(dfc, a.tag)

    log("HTML 생성")
    env = dict(os.environ, VIZ_TAG=a.tag)
    r = subprocess.run([sys.executable,
                        os.path.join(ROOT, "scripts", "viz", "build_preprocess_viz.py")],
                       env=env, capture_output=True, text=True, errors="replace")
    print(r.stdout, r.stderr)
    log("완료 — Tad 캡처는 따로 붙인다")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
