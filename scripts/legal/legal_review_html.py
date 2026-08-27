# -*- coding: utf-8 -*-
"""판례·금감원 검토 큐를 한 파일짜리 오프라인 HTML로 만든다."""

from __future__ import annotations

import hashlib
import html
import json
import re
from html.parser import HTMLParser
from pathlib import Path


def _plain_text(value: object) -> str:
    text = str(value or "")
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text).replace("\r\n", "\n").replace("\r", "\n")
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _load_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _collect_court_records(value: object, out: dict[str, dict]) -> None:
    if not isinstance(value, dict):
        return
    if any(key in value for key in ("법원명", "판시사항", "판결요지", "판례내용_앞부분")):
        return
    for key, child in value.items():
        if isinstance(child, dict) and any(
            field in child
            for field in ("법원명", "판시사항", "판결요지", "판례내용_앞부분", "판례내용_이유부분")
        ):
            out[str(key)] = child
        elif isinstance(child, dict):
            _collect_court_records(child, out)


def load_court_sources(legal_dir: Path) -> tuple[dict[str, dict], dict[str, str]]:
    records: dict[str, dict] = {}
    location_parts: dict[str, list[str]] = {}
    for name in (
        "remaining115_bundle.json",
        "pilot20_bundle.json",
        "retry11_bundle.json",
        "prec_v3_bundle.json",
    ):
        path = legal_dir / name
        if not path.exists():
            continue
        found: dict[str, dict] = {}
        _collect_court_records(_load_json(path), found)
        for case_id, record in found.items():
            merged = records.setdefault(case_id, {})
            for key, value in record.items():
                if len(_plain_text(value)) > len(_plain_text(merged.get(key))):
                    merged[key] = value
            location = path.as_posix()
            if location not in location_parts.setdefault(case_id, []):
                location_parts[case_id].append(location)
    return records, {case_id: ", ".join(paths) for case_id, paths in location_parts.items()}


def load_fss_urls(legal_dir: Path) -> dict[str, str]:
    """일부 깨진 제목이 있어도 id/url 쌍은 복구한다."""
    path = legal_dir / "fss" / "index.json"
    if not path.exists():
        return {}
    raw = path.read_text(encoding="utf-8", errors="replace")
    return dict(
        re.findall(
            r'"id"\s*:\s*"([^"\\]+)"\s*,\s*"url"\s*:\s*"(https?://[^"\\]+)"',
            raw,
        )
    )


class _DbdataParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.depth = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        classes = dict(attrs).get("class") or ""
        if self.depth:
            if tag in {"br", "p", "li", "tr"}:
                self.parts.append("\n")
            if tag not in {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}:
                self.depth += 1
        elif tag == "div" and "dbdata" in classes.split():
            self.depth = 1

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.depth and tag == "br":
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if self.depth:
            if tag in {"p", "li", "tr", "div"}:
                self.parts.append("\n")
            self.depth -= 1

    def handle_data(self, data: str) -> None:
        if self.depth:
            self.parts.append(data)

    def text(self) -> str:
        value = html.unescape("".join(self.parts)).replace("\xa0", " ")
        value = re.sub(r"[ \t]+", " ", value)
        value = re.sub(r" *\n *", "\n", value)
        return re.sub(r"\n{3,}", "\n\n", value).strip()


def load_fss_texts(
    legal_dir: Path, wanted_case_ids: set[str] | None = None
) -> tuple[dict[str, str], dict[str, str]]:
    text_dir = legal_dir / "raw" / "fss" / "text"
    texts: dict[str, list[str]] = {}
    locations: dict[str, list[str]] = {}
    if text_dir.exists():
        for path in sorted(text_dir.glob("*.txt")):
            match = re.match(r"(.+)_([0-9]+)$", path.stem)
            case_id = match.group(1) if match else path.stem
            if wanted_case_ids is not None and case_id not in wanted_case_ids:
                continue
            body = path.read_text(encoding="utf-8", errors="replace").strip()
            if body:
                texts.setdefault(case_id, []).append(body)
                locations.setdefault(case_id, []).append(path.as_posix())

    raw_dir = legal_dir / "raw" / "fss"
    if raw_dir.exists():
        for path in sorted(raw_dir.glob("*.html")):
            if wanted_case_ids is not None and path.stem not in wanted_case_ids:
                continue
            if path.stem in texts:
                continue
            parser = _DbdataParser()
            parser.feed(path.read_text(encoding="utf-8", errors="replace"))
            body = parser.text()
            if body:
                texts[path.stem] = [body]
                locations[path.stem] = [path.as_posix()]
    return (
        {case_id: "\n\n".join(parts) for case_id, parts in texts.items()},
        {case_id: ", ".join(parts) for case_id, parts in locations.items()},
    )


def _evidence_excerpt(row: dict) -> str:
    parts: list[str] = []
    for fact in row.get("facts") or []:
        text = str(fact.get("fact") or "").strip()
        ref = fact.get("evidence_ref") or {}
        if text:
            parts.append(f"[사실 · {ref.get('source_part') or '근거 위치 미상'}]\n{text}")
    for holding in row.get("holdings") or []:
        ref = holding.get("evidence_ref") or {}
        locator = str(ref.get("locator") or "").strip()
        if locator:
            parts.append(
                f"[결론 근거 위치 · {ref.get('source_part') or '위치 미상'}]\n{locator}"
            )
    return "\n\n".join(parts)


def build_review_items(queue: list[dict], ledger: list[dict], legal_dir: Path) -> list[dict]:
    by_id = {(row.get("case") or {}).get("id", ""): row for row in ledger}
    court_sources, court_locations = load_court_sources(legal_dir)
    wanted_fss_ids = {
        str(item.get("case_id") or "") for item in queue if item.get("source") == "fss"
    }
    fss_texts, fss_locations = load_fss_texts(legal_dir, wanted_fss_ids)
    fss_urls = load_fss_urls(legal_dir)
    items: list[dict] = []
    source_positions = {"court": 0, "fss": 0}
    source_offsets = {"court": 0, "fss": 4}

    for position, queued in enumerate(queue, start=1):
        case_id = str(queued.get("case_id") or "")
        source = str(queued.get("source") or "")
        source_index = source_positions.get(source, 0)
        review_part = ((source_index + source_offsets.get(source, 0)) % 5) + 1
        source_positions[source] = source_index + 1
        row = by_id.get(case_id, {})
        case = row.get("case") or {}
        raw_text = ""
        source_location = ""
        source_url = ""
        source_level = "발췌 근거"
        title = ((queued.get("issues") or [{}])[0].get("쟁점문구") or case_id)

        if source == "court":
            record = court_sources.get(case_id) or {}
            sections = []
            for label, key in (
                ("판시사항", "판시사항"),
                ("판결요지", "판결요지"),
                ("판결문 앞부분 발췌", "판례내용_앞부분"),
                ("판결 이유 발췌", "판례내용_이유부분"),
            ):
                body = _plain_text(record.get(key))
                if body:
                    sections.append(f"[{label}]\n{body}")
            raw_text = "\n\n".join(sections)
            source_location = court_locations.get(case_id, "")
            title = str(record.get("사건명") or title)
            if raw_text:
                source_level = "법원 원문 발췌"
        elif source == "fss":
            raw_text = fss_texts.get(case_id, "")
            source_location = fss_locations.get(case_id, "")
            source_url = fss_urls.get(case_id, "")
            if len(raw_text) >= 120:
                source_level = "금감원 원문 텍스트"
            elif raw_text:
                evidence = _evidence_excerpt(row)
                if evidence:
                    raw_text = f"[수집 원문의 짧은 본문]\n{raw_text}\n\n{evidence}"
                    source_level = "발췌 근거"
                else:
                    source_level = "원문 재확인 필요"

        if not raw_text:
            raw_text = _evidence_excerpt(row)
        if not raw_text:
            raw_text = "비교할 원문이 로컬 자료에 없습니다. 이 항목은 출처를 다시 찾아 확인해야 합니다."
            source_level = "원문 재확인 필요"

        items.append(
            {
                "position": position,
                "case_id": case_id,
                "source": source,
                "review_part": review_part,
                "source_label": "법원 판례" if source == "court" else "금융감독원",
                "source_board": case.get("source_board", ""),
                "source_level": source_level,
                "source_location": source_location,
                "source_url": source_url,
                "title": title,
                "authority_grade": queued.get("authority_grade", ""),
                "date": queued.get("date", ""),
                "finality": queued.get("finality", ""),
                "source_completeness": row.get("source_completeness", ""),
                "generated_by": row.get("generated_by", ""),
                "issues": queued.get("issues") or [],
                "facts": row.get("facts") or [],
                "holdings": queued.get("holdings") or [],
                "raw_text": raw_text,
                "verdict": queued.get("verdict", ""),
                "note": queued.get("note", ""),
            }
        )
    return items


def render_assignment_markdown(items: list[dict]) -> str:
    lines = [
        "# 판례·금감원 사람 검토 5인 분담표",
        "",
        "- 전체: 114건",
        "- 분담 기준: 총 건수와 법원·금감원 출처를 최대한 고르게 배분",
        "- 사용 방법: 검토 HTML의 `담당 파트` 필터에서 자기 파트만 선택",
        "- 주의: `case_145`는 원문 부족 건이므로 담당자가 원문 재확인 후 제외 여부를 판단",
        "",
        "## 한눈에 보는 분담",
        "",
        "| 담당 | 전체 | 법원 | 금감원 |",
        "|---|---:|---:|---:|",
    ]
    for part in range(1, 6):
        assigned = [item for item in items if item["review_part"] == part]
        court = sum(item["source"] == "court" for item in assigned)
        fss = sum(item["source"] == "fss" for item in assigned)
        lines.append(f"| {part}파트 | {len(assigned)} | {court} | {fss} |")

    for part in range(1, 6):
        lines.extend(
            [
                "",
                f"## {part}파트",
                "",
                "| 번호 | 출처 | 사건 ID | 검토 제목 |",
                "|---:|---|---|---|",
            ]
        )
        for item in (x for x in items if x["review_part"] == part):
            title = str(item["title"]).replace("|", "\\|").replace("\n", " ")
            lines.append(
                f"| {item['position']} | {item['source_label']} | `{item['case_id']}` | {title} |"
            )
    return "\n".join(lines) + "\n"


def render_review_html(items: list[dict], queue: list[dict]) -> str:
    canonical = json.dumps(queue, ensure_ascii=False, sort_keys=True)
    fingerprint = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]
    payload = json.dumps(
        {"version": 1, "fingerprint": fingerprint, "items": items, "queue": queue},
        ensure_ascii=False,
    ).replace("</", "<\\/")
    return _HTML.replace("__REVIEW_DATA__", payload)


_HTML = r'''<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>판례·금감원 사람 검토</title>
  <style>
    :root { --bg:#f4f6f8; --paper:#fff; --ink:#17202a; --muted:#5f6b76; --line:#d9e0e6;
      --navy:#173b57; --blue:#1769aa; --green:#247a52; --amber:#9a6500; --red:#a33838; --focus:#f6b73c; }
    * { box-sizing:border-box; }
    body { margin:0; color:var(--ink); background:var(--bg); font:15px/1.62 system-ui,"Malgun Gothic",sans-serif; }
    button,input,select,textarea { font:inherit; }
    button { cursor:pointer; }
    button:focus-visible,input:focus-visible,select:focus-visible,textarea:focus-visible,a:focus-visible {
      outline:3px solid var(--focus); outline-offset:2px; }
    .top { position:sticky; top:0; z-index:20; padding:14px 20px; color:#fff; background:var(--navy);
      box-shadow:0 2px 8px #0003; }
    .topline,.actions,.filters,.nav,.verdicts,.meta { display:flex; align-items:center; gap:9px; flex-wrap:wrap; }
    h1 { margin:0; font-size:21px; } .top .help { color:#dce9f3; }
    .progress { margin-left:auto; min-width:230px; }
    .bar { height:9px; border-radius:8px; overflow:hidden; background:#ffffff30; }
    .bar > span { display:block; height:100%; width:0; background:#7de2ac; transition:width .2s; }
    .top button,.top label.import { color:#fff; border:1px solid #ffffff70; background:#ffffff12; border-radius:7px;
      padding:7px 10px; }
    .top label.import { cursor:pointer; } .top input[type=file] { display:none; }
    .filters { max-width:1500px; margin:16px auto 0; padding:0 18px; }
    .filters input[type=search] { flex:1; min-width:230px; }
    input,select,textarea { color:var(--ink); background:#fff; border:1px solid #aeb9c3; border-radius:7px; padding:9px 10px; }
    .layout { display:grid; grid-template-columns:330px minmax(0,1fr); gap:16px; max-width:1500px; margin:12px auto 30px; padding:0 18px; }
    .list { height:calc(100vh - 190px); overflow:auto; background:var(--paper); border:1px solid var(--line); border-radius:10px; }
    .case-button { display:block; width:100%; text-align:left; padding:12px; border:0; border-bottom:1px solid var(--line); background:#fff; }
    .case-button:hover { background:#f4f9fc; } .case-button.on { background:#e8f3fa; box-shadow:inset 4px 0 var(--blue); }
    .case-button .line { display:flex; justify-content:space-between; gap:8px; }
    .case-button small { color:var(--muted); } .case-button .title { margin-top:5px; line-height:1.35; }
    .badge { display:inline-block; padding:2px 7px; border-radius:999px; font-size:12px; font-weight:700; background:#e9eef2; }
    .badge.court { color:#174b70; background:#e3f1fb; } .badge.fss { color:#725000; background:#fff0c7; }
    .badge.part { color:#4f357a; background:#eee6fb; }
    .badge.confirmed { color:#175b3b; background:#dff4e8; } .badge.corrected { color:#775000; background:#ffefbf; }
    .badge.rejected { color:#872f2f; background:#f9dddd; } .badge.incomplete { color:#7d4b00; background:#ffe6c2; }
    main { min-width:0; } .empty,.panel { background:var(--paper); border:1px solid var(--line); border-radius:10px; }
    .empty { padding:50px; text-align:center; color:var(--muted); }
    .case-head { padding:18px 20px; border-bottom:1px solid var(--line); }
    .case-head h2 { margin:8px 0 4px; font-size:22px; line-height:1.35; }
    .meta { color:var(--muted); font-size:13px; }
    .warning { margin-top:10px; padding:9px 11px; border-left:4px solid var(--amber); background:#fff8e7; color:#674900; }
    .compare { display:grid; grid-template-columns:minmax(0,1.05fr) minmax(0,.95fr); }
    .column { min-width:0; padding:18px 20px; } .column + .column { border-left:1px solid var(--line); }
    h3 { margin:0 0 10px; font-size:17px; } h4 { margin:16px 0 6px; font-size:15px; }
    .source-text { max-height:58vh; overflow:auto; white-space:pre-wrap; padding:14px; border:1px solid var(--line);
      border-radius:8px; background:#fbfcfd; word-break:break-word; }
    .source-link { display:inline-block; margin:0 0 10px; color:var(--blue); }
    .item-block { padding:11px 12px; margin:8px 0; border:1px solid var(--line); border-radius:8px; background:#fbfcfd; }
    .holding { border-left:4px solid var(--blue); } .conclusion { font-weight:800; color:var(--navy); }
    .decision { margin-top:16px; padding:18px 20px; border-top:1px solid var(--line); background:#f9fbfc; }
    .verdicts button { min-height:44px; padding:8px 15px; border:1px solid #9eaab4; border-radius:8px; background:#fff; font-weight:700; }
    .verdicts button[data-verdict=confirmed].on { color:#fff; background:var(--green); border-color:var(--green); }
    .verdicts button[data-verdict=corrected].on { color:#fff; background:var(--amber); border-color:var(--amber); }
    .verdicts button[data-verdict=rejected].on { color:#fff; background:var(--red); border-color:var(--red); }
    .verdicts button[data-verdict=""].on { color:#fff; background:#63717d; }
    textarea { display:block; width:100%; min-height:100px; resize:vertical; margin-top:9px; }
    .need-note { color:var(--red); font-weight:700; } .save-state { color:var(--green); margin-left:auto; }
    .nav { justify-content:space-between; margin-top:12px; }
    .nav button { padding:9px 14px; border:1px solid #aeb9c3; background:#fff; border-radius:7px; }
    .guide { max-width:1500px; margin:0 auto 14px; padding:0 18px; color:var(--muted); }
    .guide details { background:#fff; border:1px solid var(--line); border-radius:9px; padding:10px 14px; }
    .guide code { user-select:all; color:#263f52; }
    @media (max-width:900px) {
      .progress { margin-left:0; width:100%; } .layout { grid-template-columns:1fr; }
      .list { height:240px; } .compare { grid-template-columns:1fr; } .column + .column { border-left:0; border-top:1px solid var(--line); }
      .source-text { max-height:48vh; }
    }
    @media print { .top,.filters,.list,.nav,.guide { display:none!important; } .layout { display:block; margin:0; padding:0; }
      .panel { border:0; } .source-text { max-height:none; overflow:visible; } .decision { break-inside:avoid; } }
  </style>
</head>
<body>
  <header class="top">
    <div class="topline">
      <h1>판례·금감원 사람 검토</h1>
      <span class="help">원문과 AI 정리를 비교해 한 건씩 판단하세요.</span>
      <div class="progress" aria-live="polite"><div id="progressText">0 / 0 완료</div><div class="bar"><span id="progressBar"></span></div></div>
    </div>
    <div class="actions">
      <input id="reviewer" aria-label="검토자 이름" placeholder="검토자 이름">
      <button id="nextPending" type="button">다음 미검토</button>
      <label class="import">팀원 JSON 합치기<input id="importFile" type="file" accept="application/json,.json" multiple></label>
      <button id="exportBtn" type="button">현재까지 JSON 내려받기</button>
      <button id="copyCommand" type="button">반영 명령 복사</button>
      <button id="resetBtn" type="button">내 판단 초기화</button>
    </div>
  </header>
  <section class="guide">
    <details><summary><b>처음 보는 분을 위한 판단 기준</b></summary>
      <p><b>맞음</b>: AI가 적은 쟁점과 결론이 원문 뜻과 같습니다. <b>수정 필요</b>: 자료는 쓸 수 있지만 AI 결론이나 설명이 틀렸습니다. 메모에 올바른 내용을 적으세요. <b>제외</b>: 원문이 부족하거나 우리 보험 판단 자료로 쓰기 어렵습니다. 이유를 적으세요.</p>
      <p>한 사건에 결론이 여러 개면 전부 확인합니다. 하나라도 틀리면 “수정 필요”를 선택하고 어느 항목이 틀렸는지 메모에 적습니다. 법원 자료의 `판결문 앞부분 발췌`와 `판결 이유 발췌`는 전문이 아니므로 중간에서 끝날 수 있습니다. 그때는 위쪽의 판시사항과 판결요지가 완전한지 먼저 확인합니다.</p>
      <p>이 화면은 서버로 전송하지 않으며 이 브라우저에 임시 저장됩니다.</p>
      <p>모두 검토한 뒤 JSON을 내려받아 <code>data/legal/human_review_queue.json</code>에 덮어쓰고, 표시되는 반영 명령을 실행하면 됩니다.</p>
    </details>
  </section>
  <section class="filters">
    <input id="search" type="search" placeholder="사건번호·쟁점·결론 검색" aria-label="검토 항목 검색">
    <select id="sourceFilter" aria-label="출처 필터"><option value="all">전체 출처</option><option value="court">법원 판례</option><option value="fss">금융감독원</option></select>
    <select id="partFilter" aria-label="담당 파트 필터"><option value="all">전체 담당 파트</option><option value="1">1파트</option><option value="2">2파트</option><option value="3">3파트</option><option value="4">4파트</option><option value="5">5파트</option></select>
    <select id="statusFilter" aria-label="검토 상태 필터"><option value="all">전체 상태</option><option value="pending">미검토</option><option value="confirmed">맞음</option><option value="corrected">수정 필요</option><option value="rejected">제외</option><option value="incomplete">메모 미완료</option></select>
    <span id="visibleCount" aria-live="polite"></span>
  </section>
  <div class="layout">
    <aside id="caseList" class="list" aria-label="검토 대상 목록"></aside>
    <main id="detail"><div class="empty">왼쪽 목록에서 검토할 사건을 선택하세요.</div></main>
  </div>
  <script id="reviewData" type="application/json">__REVIEW_DATA__</script>
  <script>
  (() => {
    'use strict';
    const DATA = JSON.parse(document.getElementById('reviewData').textContent);
    const KEY = 'legal-case-review-' + DATA.fingerprint;
    const META_KEY = KEY + '-meta';
    const labels = {confirmed:'맞음', corrected:'수정 필요', rejected:'제외', pending:'미검토', incomplete:'메모 미완료'};
    let saved = readJSON(KEY, {}), current = '', visible = [];
    const $ = id => document.getElementById(id);
    const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    function readJSON(key, fallback) { try { return JSON.parse(localStorage.getItem(key)) || fallback; } catch (_) { return fallback; } }
    function initialDecision(item) { return {verdict:item.verdict || '', note:item.note || '', at:''}; }
    function decision(item) { return saved[item.case_id] || initialDecision(item); }
    function status(item) { const d=decision(item); return ((d.verdict==='corrected'||d.verdict==='rejected')&&!d.note.trim()) ? 'incomplete' : (d.verdict || 'pending'); }
    function isComplete(item) { return ['confirmed','corrected','rejected'].includes(status(item)); }
    function persist() { localStorage.setItem(KEY, JSON.stringify(saved)); updateProgress(); renderList(); }
    function updateProgress() { const done=DATA.items.filter(isComplete).length, total=DATA.items.length;
      $('progressText').textContent=`${done} / ${total} 완료`; $('progressBar').style.width=(total ? done/total*100 : 0)+'%'; }
    function matches(item) { const q=$('search').value.trim().toLowerCase(); const source=$('sourceFilter').value, part=$('partFilter').value, state=$('statusFilter').value;
      const hay=[item.case_id,item.title,item.raw_text,...item.issues.map(x=>x['쟁점문구']),...item.holdings.map(x=>x['법리_요약'])].join(' ').toLowerCase();
      return (!q||hay.includes(q)) && (source==='all'||item.source===source) && (part==='all'||String(item.review_part)===part) && (state==='all'||status(item)===state); }
    function renderList() { visible=DATA.items.filter(matches); $('visibleCount').textContent=`${visible.length}건 표시`;
      $('caseList').innerHTML=visible.map(item=>{ const st=status(item); return `<button type="button" class="case-button ${item.case_id===current?'on':''}" data-id="${esc(item.case_id)}"><span class="line"><b>${item.position}. ${esc(item.case_id)}</b><span class="badge ${st}">${labels[st]}</span></span><small><span class="badge ${item.source}">${esc(item.source_label)}</span> <span class="badge part">${item.review_part}파트</span> · ${esc(item.date||'날짜 미상')} · ${esc(item.source_level)}</small><div class="title">${esc(item.title)}</div></button>`; }).join('') || '<div class="empty">조건에 맞는 항목이 없습니다.</div>';
      $('caseList').querySelectorAll('[data-id]').forEach(b=>b.addEventListener('click',()=>select(b.dataset.id))); }
    function block(title, body, cls='') { return `<div class="item-block ${cls}"><b>${esc(title)}</b><div>${esc(body||'내용 없음')}</div></div>`; }
    function select(id) { const item=DATA.items.find(x=>x.case_id===id); if(!item) return; current=id; renderList();
      const d=decision(item), incomplete=status(item)==='incomplete';
      const issues=item.issues.map(x=>block(`쟁점 ${x.issue_id||''}`,x['쟁점문구'])).join('');
      const facts=item.facts.map((x,i)=>block(`사실 ${i+1}`,x.fact)).join('');
      const holdings=item.holdings.map(x=>`<div class="item-block holding"><b>AI 결론 · ${esc(x.issue_id||'')}</b><div class="conclusion">${esc(x['결론']||'결론 없음')} · 신뢰도 ${esc(x.confidence||'미기재')}</div><div>${esc(x['법리_요약']||'설명 없음')}</div></div>`).join('');
      const sourceLink=item.source_url?`<a class="source-link" href="${esc(item.source_url)}" target="_blank" rel="noopener noreferrer">금감원 원문 페이지 새 창으로 열기 ↗</a>`:'';
      $('detail').innerHTML=`<article class="panel"><header class="case-head"><div><span class="badge ${item.source}">${esc(item.source_label)}</span> <span class="badge part">${item.review_part}파트</span> <span class="badge">${esc(item.source_level)}</span></div><h2>${esc(item.case_id)} · ${esc(item.title)}</h2><div class="meta"><span>${esc(item.authority_grade)}</span><span>${esc(item.date||'날짜 미상')}</span><span>확정성 ${esc(item.finality||'미상')}</span><span>AI 생성 ${esc(item.generated_by||'미상')}</span></div>${item.source_level==='발췌 근거'||item.source_level==='원문 재확인 필요'?'<div class="warning">전문 원문이 아니라 장부에 남은 발췌 근거입니다. “맞음”을 누르기 전에 출처를 다시 찾아보는 것이 안전합니다.</div>':''}</header><div class="compare"><section class="column"><h3>① 사람이 읽을 원문·근거</h3>${sourceLink}<div class="source-text">${esc(item.raw_text)}</div><div class="meta">로컬 위치: ${esc(item.source_location||'별도 원문 파일 없음')}</div></section><section class="column"><h3>② AI가 정리한 내용</h3><h4>쟁점</h4>${issues||'<p>쟁점 없음</p>'}<h4>사실</h4>${facts||'<p>사실 요약 없음</p>'}<h4>결론과 설명</h4>${holdings||'<p>결론 없음</p>'}</section></div><section class="decision"><h3>③ 사람의 최종 판단</h3><div class="verdicts"><button type="button" data-verdict="confirmed" class="${d.verdict==='confirmed'?'on':''}">✓ 맞음</button><button type="button" data-verdict="corrected" class="${d.verdict==='corrected'?'on':''}">✎ 수정 필요</button><button type="button" data-verdict="rejected" class="${d.verdict==='rejected'?'on':''}">× 제외</button><button type="button" data-verdict="" class="${!d.verdict?'on':''}">판단 지우기</button><span class="save-state">${d.at?'자동 저장됨':''}</span></div><label for="reviewNote"><b>검토 메모</b> <span id="noteHelp" class="${incomplete?'need-note':''}">${d.verdict==='corrected'||d.verdict==='rejected'?'(이유를 적어야 완료됩니다)':'(선택)'}</span></label><textarea id="reviewNote" placeholder="수정할 결론과 근거, 또는 제외 이유를 적으세요.">${esc(d.note)}</textarea></section></article><div class="nav"><button id="prevBtn" type="button">← 이전 항목</button><span>${item.position} / ${DATA.items.length}</span><button id="nextBtn" type="button">다음 항목 →</button></div>`;
      $('detail').querySelectorAll('[data-verdict]').forEach(b=>b.addEventListener('click',()=>saveDecision(item,b.dataset.verdict,$('reviewNote').value)));
      $('reviewNote').addEventListener('input',e=>saveDecision(item,decision(item).verdict,e.target.value,false));
      $('prevBtn').addEventListener('click',()=>move(-1)); $('nextBtn').addEventListener('click',()=>move(1));
      document.querySelector('.layout').scrollIntoView({block:'start'}); }
    function saveDecision(item, verdict, note, rerender=true) { if(!verdict&&!note) delete saved[item.case_id]; else saved[item.case_id]={verdict,note,at:new Date().toISOString()}; persist(); if(rerender) select(item.case_id); else { const help=$('noteHelp'); if(help) { const needed=(verdict==='corrected'||verdict==='rejected')&&!note.trim(); help.classList.toggle('need-note',needed); } updateProgress(); } }
    function move(delta) { const i=DATA.items.findIndex(x=>x.case_id===current); select(DATA.items[Math.max(0,Math.min(DATA.items.length-1,i+delta))].case_id); }
    function nextPending() { const start=Math.max(0,DATA.items.findIndex(x=>x.case_id===current)+1); const found=DATA.items.slice(start).concat(DATA.items.slice(0,start)).find(x=>!isComplete(x)); if(found) select(found.case_id); else alert('114건 검토가 모두 완료되었습니다.'); }
    function exportQueue() { const incomplete=DATA.items.filter(x=>!isComplete(x)).length; if(incomplete&&!confirm(`${incomplete}건이 아직 완료되지 않았습니다. 현재까지의 결과만 내려받을까요?`)) return;
      const out=DATA.queue.map(item=>{ const d=decision({case_id:item.case_id,verdict:item.verdict,note:item.note}); return {...item,verdict:d.verdict||'',note:d.note||''}; });
      const url=URL.createObjectURL(new Blob([JSON.stringify(out,null,2)+'\n'],{type:'application/json'})); const a=document.createElement('a'); a.href=url; a.download='human_review_queue.json'; a.click(); setTimeout(()=>URL.revokeObjectURL(url),1000); }
    async function importQueues(files) { try { const known=new Set(DATA.items.map(x=>x.case_id)); let count=0; const incoming={};
      for(const file of files) { const rows=JSON.parse(await file.text()); if(!Array.isArray(rows)) throw new Error(`${file.name}: JSON 최상위가 목록이 아닙니다.`);
        for(const row of rows) { if(!known.has(row.case_id)) continue; const verdict=String(row.verdict||''), note=String(row.note||''); if(verdict&&!['confirmed','corrected','rejected'].includes(verdict)) throw new Error(`${file.name}: ${row.case_id}의 verdict 값이 잘못되었습니다.`); if(!verdict&&!note) continue;
          const next={verdict,note}; const old=incoming[row.case_id]; if(old&&(old.verdict!==next.verdict||old.note!==next.note)) throw new Error(`${row.case_id}가 파일마다 다르게 검토되었습니다. 담당자를 확인하세요.`); incoming[row.case_id]=next; }
      }
      for(const [caseId,d] of Object.entries(incoming)) { const old=saved[caseId]; if(old&&(old.verdict!==d.verdict||old.note!==d.note)&&!confirm(`${caseId}에 이미 다른 판단이 있습니다. 새 파일 내용으로 바꿀까요?`)) continue; saved[caseId]={...d,at:new Date().toISOString()}; count++; }
      persist(); if(current) select(current); alert(`${files.length}개 파일에서 ${count}건의 검토 내용을 합쳤습니다.`); } catch(e) { alert('불러오지 못했습니다: '+e.message); } }
    $('search').addEventListener('input',renderList); $('sourceFilter').addEventListener('change',renderList); $('partFilter').addEventListener('change',renderList); $('statusFilter').addEventListener('change',renderList);
    $('nextPending').addEventListener('click',nextPending); $('exportBtn').addEventListener('click',exportQueue);
    $('importFile').addEventListener('change',e=>{ if(e.target.files.length) importQueues([...e.target.files]); e.target.value=''; });
    $('reviewer').value=readJSON(META_KEY,{}).reviewer||''; $('reviewer').addEventListener('input',e=>localStorage.setItem(META_KEY,JSON.stringify({reviewer:e.target.value})));
    $('copyCommand').addEventListener('click',async()=>{ const name=$('reviewer').value.trim(); if(!name) { alert('먼저 검토자 이름을 적어주세요.'); return; } const command=`python -m scripts.legal.review_legal_cases --apply --reviewed-by "${name.replaceAll('"','')}"`; try { await navigator.clipboard.writeText(command); alert('반영 명령을 복사했습니다.'); } catch(_) { prompt('아래 명령을 복사하세요.',command); } });
    $('resetBtn').addEventListener('click',()=>{ if(confirm('이 브라우저에 저장된 검토 판단을 모두 지울까요? 내려받은 JSON 파일은 지워지지 않습니다.')) { saved={}; localStorage.removeItem(KEY); renderList(); updateProgress(); if(current) select(current); } });
    renderList(); updateProgress(); if(DATA.items.length) select(DATA.items[0].case_id);
  })();
  </script>
</body>
</html>'''
