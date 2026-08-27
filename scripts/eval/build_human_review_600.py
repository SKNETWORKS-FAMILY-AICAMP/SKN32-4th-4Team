"""Build five self-contained, time-balanced HTML files for the 600 human reviews.

The source candidate files stay immutable. Reviewers download one JSONL result
from each HTML file; those result files can be merged later after validation.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import html
import json
import math
import re
import zipfile
from collections import Counter
from functools import lru_cache
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = ROOT / "docs" / "handoff" / "review_600_5parts"
PACKAGE_PATH = ROOT / "docs" / "handoff" / "packages" / "사람검수_600건_5인_시간균형_20260825.zip"


LABELS: dict[str, list[tuple[str, str]]] = {
    "table_b": [
        ("table", "표 맞음"),
        ("prose", "본문을 표로 잘못 잡음"),
        ("broken", "표는 맞지만 칸 연결이 깨짐"),
        ("unsure", "판단 보류"),
    ],
    "table_a": [
        ("true", "표 맞음"),
        ("false", "표 아님"),
        ("unsure", "판단 보류"),
    ],
    "outside_clause": [
        ("missed_policy_content", "약관 내용인데 조항에서 빠짐"),
        ("external_reference", "외부 법령·참고자료"),
        ("front_or_index", "표지·목차·안내문"),
        ("blank_or_image", "빈쪽·이미지만 있는 쪽"),
        ("other_non_clause", "그 밖의 조항 아닌 내용"),
        ("unsure", "판단 보류"),
    ],
    "b8_disability": [
        ("approve", "모든 항목과 지급률 짝이 맞음"),
        ("reject", "여러 짝이 틀려 사용할 수 없음"),
        ("fix", "일부 짝만 수정하면 됨"),
        ("unsure", "판단 보류"),
    ],
    "f4_interest": [
        ("approve", "네 기간과 이율이 모두 맞음"),
        ("reject", "표 전체를 사용할 수 없음"),
        ("fix", "일부 기간·이율 수정 필요"),
        ("unsure", "판단 보류"),
    ],
}


def _jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _stable_key(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _plain_cell(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("text") or value.get("value") or "")
    return str(value or "")


def _preview_rows(rows: Any) -> list[list[str]]:
    output: list[list[str]] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        cells = row.get("cols") if isinstance(row.get("cols"), dict) else {
            key: value for key, value in row.items() if str(key).isdigit()
        }
        output.append([_plain_cell(value) for _, value in sorted(cells.items(), key=lambda pair: str(pair[0]))])
    return output


@lru_cache(maxsize=None)
def _extracted_document(sha12: str) -> dict[str, Any]:
    paths = list((ROOT / "data" / "extracted").glob(f"*/s5_pymupdf-1.28.0/{sha12}.json"))
    if not paths:
        return {}
    return json.loads(paths[0].read_text(encoding="utf-8"))


def _extracted_page(sha12: str, page: int) -> dict[str, Any]:
    payload = _extracted_document(sha12)
    return next((row for row in payload.get("pages") or [] if int(row.get("page") or 0) == page), {})


def _page_context(sha12: str, page: int) -> dict[str, str]:
    current = str(_extracted_page(sha12, page).get("text") or "").strip()
    previous = str(_extracted_page(sha12, page - 1).get("text") or "").strip() if page > 1 else ""
    following = str(_extracted_page(sha12, page + 1).get("text") or "").strip()
    return {
        "current": current,
        "previous": previous[-1600:],
        "next": following[:1600],
    }


def _page_image(sha12: str, page: int, dpi: int = 100) -> str:
    matches = list((ROOT / "data" / "raw" / "insurance_terms").glob(f"*/{sha12}*.pdf"))
    if not matches:
        return ""
    try:
        import fitz

        with fitz.open(str(matches[0])) as document:
            if page < 1 or page > document.page_count:
                return ""
            pixmap = document[page - 1].get_pixmap(matrix=fitz.Matrix(dpi / 72, dpi / 72), alpha=False)
            encoded = base64.b64encode(pixmap.tobytes("jpg", jpg_quality=62)).decode("ascii")
            return "data:image/jpeg;base64," + encoded
    except Exception:
        return ""


def _clean_disability_labels(page_text: str, count: int) -> list[str]:
    """Recover readable numbered labels from the clean extracted page text."""
    start = page_text.find("지급률(%)")
    segment = page_text[start + len("지급률(%)") :] if start >= 0 else page_text
    end_markers = ["\n나. 장해판정", "\n나. 장해의", "\n장해판정기준"]
    ends = [segment.find(marker) for marker in end_markers if segment.find(marker) >= 0]
    if ends:
        segment = segment[: min(ends)]
    matches = list(re.finditer(r"(?m)(?:^|\n)\s*(\d{1,2})\)\s*", segment))
    labels: list[str] = []
    for index, match in enumerate(matches):
        number = int(match.group(1))
        if number != len(labels) + 1:
            if labels:
                break
            continue
        stop = matches[index + 1].start() if index + 1 < len(matches) else len(segment)
        value = segment[match.end() : stop].strip()
        value = re.sub(r"(?:\n\s*\d+(?:\.\d+)?\s*)+$", "", value).strip()
        value = re.sub(r"\s+", " ", value)
        labels.append(value)
        if len(labels) == count:
            break
    return labels


def _base_item(source_type: str, review_id: str, minutes: float, **kwargs: Any) -> dict[str, Any]:
    return {
        "review_id": review_id,
        "source_type": source_type,
        "estimated_minutes": round(minutes, 2),
        **kwargs,
    }


def _table_b_items() -> list[dict[str, Any]]:
    prior = {
        (row.get("sha12"), int(row.get("page") or 0)): row
        for row in _jsonl(ROOT / "data" / "eval" / "human_table_labels_20260804.jsonl")
        if row.get("queue") == "B5-candidate65"
    }
    output = []
    for row in _jsonl(ROOT / "data" / "eval" / "table_labelset_candidates.jsonl"):
        sha12, page = str(row["sha12"]), int(row["page"])
        previous = prior.get((sha12, page), {})
        output.append(_base_item(
            "table_b",
            f"table_b:{sha12}:p{page}:{row.get('table_id')}",
            0.8,
            title=f"표 B 재확인 · {row.get('insurer') or '보험사 미상'} · {sha12} p.{page}",
            subtitle="기존 사람 판정을 참고하되, 미리보기를 직접 읽고 다시 선택하세요.",
            sha12=sha12,
            page=page,
            insurer=row.get("insurer") or "",
            method=row.get("method") or "",
            source_group="표 정답셋 B",
            previous_label=previous.get("label") or "",
            previous_note=previous.get("note") or "",
            preview=_preview_rows(row.get("preview")),
            instructions="세로로 읽어야 자연스러우면 표입니다. 가로로 이어 읽는 문장이면 본문입니다.",
        ))
    return output


def _table_a_items() -> list[dict[str, Any]]:
    payload = json.loads((ROOT / "data" / "eval" / "table_tf_candidates.json").read_text(encoding="utf-8"))
    output = []
    for group in ("line_method", "overdetect_suspect"):
        for row in payload[group]:
            sha12, page = str(row["sha12"]), int(row["page"])
            output.append(_base_item(
                "table_a",
                f"table_a:{group}:{sha12}:p{page}:{row.get('table_id')}",
                1.5,
                title=f"표 A 참/거짓 · {row.get('insurer') or '보험사 미상'} · {sha12} p.{page}",
                subtitle="컴퓨터가 잡은 영역이 실제 표인지 판단합니다.",
                sha12=sha12,
                page=page,
                insurer=row.get("insurer") or "",
                method=row.get("method") or "",
                source_group=f"표 정답셋 A · {group}",
                preview=_preview_rows(row.get("preview")),
                instructions="행과 열의 대응 관계가 있으면 ‘표 맞음’, 이어지는 문장이 잘못 잘렸으면 ‘표 아님’을 선택하세요.",
            ))
    return output


def _outside_minutes(row: dict[str, Any]) -> float:
    risk = str(row.get("risk_class") or "")
    return {
        "blank_or_image_only_proxy": 0.55,
        "short_text_proxy": 0.8,
        "business_signal": 1.2,
        "unclassified_narrative": 1.35,
    }.get(risk, 1.1)


def _outside_suggestion(row: dict[str, Any], page_text: str) -> tuple[str, str]:
    cause = str(row.get("cause_proxy") or "")
    risk = str(row.get("risk_class") or "")
    compact = re.sub(r"\s+", "", page_text[:1200])
    signals = row.get("signals") or {}
    if "statute_reference" in cause or "【법규" in page_text or re.search(r"(?:법률|시행령|시행규칙)제\d+조", compact):
        return "external_reference", "‘법규’ 표지 또는 법률·시행령 조문 형식이 확인됩니다. 약관 조항이 아니라 별도 법령 참고자료인지 확인하세요."
    if risk == "blank_or_image_only_proxy" or not compact:
        return "blank_or_image", "추출된 글이 거의 없습니다. 내장된 원문 이미지에 실제 글이나 표가 있는지 확인하세요."
    if "front_matter" in cause or "locator_only" in cause or any(word in compact[:300] for word in ("목차", "차례", "찾아보기")):
        return "front_or_index", "표지·목차·찾아보기 성격의 신호가 있습니다. 보장 조건이 직접 적힌 페이지인지 다시 확인하세요."
    guide_words = ("보험약관이란", "약관이용", "약관해설", "보험금지급절차", "QR코드", "가입자유의사항", "주요내용요약")
    if sum(word in compact[:1200] for word in guide_words) >= 2:
        return "front_or_index", "약관 이용법·QR코드·해설 영상·지급절차 같은 안내 문구가 여러 개 확인됩니다. 실제 보장 조항이 아니라 안내 페이지인지 확인하세요."
    if any(bool(signals.get(key)) for key in ("money", "rate", "kcd", "trusted_table")):
        return "missed_policy_content", "금액·비율·질병코드·표 신호가 있습니다. 실제 보장 조건이나 별표가 빠진 것인지 확인하세요."
    return "", "자동으로 확실히 분류하지 못했습니다. 현재 쪽과 바로 앞뒤 쪽을 함께 읽고 선택하세요."


def _outside_items(filename: str, prefix: str, source_group: str, *, embed_images: bool) -> list[dict[str, Any]]:
    output = []
    for sequence, row in enumerate(_jsonl(ROOT / "data" / "eval" / filename), 1):
        sha12, page = str(row["sha12"]), int(row["page"])
        context = _page_context(sha12, page)
        page_text = context["current"] or str(row.get("text_preview") or "")
        suggested_label, suggestion_reason = _outside_suggestion(row, page_text)
        needs_image = str(row.get("risk_class") or "") == "blank_or_image_only_proxy"
        output.append(_base_item(
            "outside_clause",
            f"{prefix}:{sha12}:p{page}:{sequence}",
            _outside_minutes(row),
            title=f"{source_group} · {row.get('insurer') or '보험사 미상'} · {sha12} p.{page}",
            subtitle="이 페이지가 약관 내용인데 누락된 것인지, 원래 조항이 아닌 페이지인지 구분합니다.",
            sha12=sha12,
            page=page,
            insurer=row.get("insurer") or "",
            product_name=row.get("product_name") or "",
            source_group=source_group,
            risk_class=row.get("risk_class") or "",
            cause_proxy=row.get("cause_proxy") or "",
            text_preview=row.get("text_preview") or "",
            page_text=page_text,
            previous_page_text=context["previous"],
            next_page_text=context["next"],
            page_image=_page_image(sha12, page, dpi=82) if embed_images and needs_image else "",
            suggested_label=suggested_label,
            suggestion_reason=suggestion_reason,
            gap_context=row.get("gap_context") or "",
            gap_start=row.get("gap_start"),
            gap_end=row.get("gap_end"),
            previous_covered_page=row.get("previous_covered_page"),
            next_covered_page=row.get("next_covered_page"),
            instructions="먼저 현재 쪽과 바로 앞뒤 쪽을 읽으세요. 보장 조건·면책·지급표·약관 별표면 ‘약관 내용인데 빠짐’, 법률 원문이면 ‘외부 법령·참고자료’를 선택하세요.",
        ))
    return output


def _b8_items(embed_images: bool) -> list[dict[str, Any]]:
    output = []
    for row in _jsonl(ROOT / "data" / "candidates" / "s7_disability_rates" / "pattern_review_shard00-of-01.jsonl"):
        representative = row.get("representative") or {}
        sha12, page = str(representative.get("source_sha12") or ""), int(representative.get("page") or 0)
        page_doc = _extracted_page(sha12, page)
        page_text = str(page_doc.get("text") or "")
        facts = row.get("facts") or []
        recovered = _clean_disability_labels(page_text, len(facts))
        readable_facts = []
        for index, fact in enumerate(facts):
            readable_facts.append({
                "ordinal": fact.get("ordinal") or index + 1,
                "classification": recovered[index] if index < len(recovered) else fact.get("classification") or "원문 이미지에서 확인",
                "payment_rate_percent": fact.get("payment_rate_percent"),
            })
        table_id = representative.get("table_id")
        source_table = next((table for table in page_doc.get("tables_coords") or [] if table.get("table_id") == table_id), {})
        output.append(_base_item(
            "b8_disability",
            f"b8:{row['pattern_id']}",
            1.5 + 0.4 * len(facts),
            title=f"중요 B8 · 장해 항목↔지급률 · {sha12} p.{page}",
            subtitle=f"이 한 번의 판단이 같은 형태 {int(row.get('occurrences') or 0):,}건에 적용될 수 있습니다.",
            sha12=sha12,
            page=page,
            insurer=representative.get("insurer") or "",
            product_name=representative.get("product_name") or "",
            source_group="중요 B8 장해지급률",
            pattern_id=row["pattern_id"],
            occurrences=int(row.get("occurrences") or 0),
            facts=readable_facts,
            preview=_preview_rows(source_table.get("records")),
            page_text=page_text,
            page_image=_page_image(sha12, page) if embed_images else "",
            instructions="왼쪽 장해 항목과 오른쪽 지급률이 순서대로 정확히 짝지어졌는지 전부 확인하세요. 하나라도 다르면 ‘일부 수정’ 또는 ‘사용 불가’를 선택하고 메모에 번호를 적으세요.",
        ))
    return output


def _f4_items(embed_images: bool) -> list[dict[str, Any]]:
    output = []
    for row in _jsonl(ROOT / "data" / "candidates" / "s7_delayed_payment_interest" / "candidates.jsonl"):
        sha12, page = str(row.get("source_sha12") or ""), int(row.get("page") or 0)
        page_doc = _extracted_page(sha12, page)
        facts = [
            {"period": "지급기일 다음날부터 30일 이내", "rate": "보험계약대출이율"},
            {"period": "31일 이후부터 60일 이내", "rate": "보험계약대출이율 + 4%"},
            {"period": "61일 이후부터 90일 이내", "rate": "보험계약대출이율 + 6%"},
            {"period": "91일 이후", "rate": "보험계약대출이율 + 8%"},
        ]
        output.append(_base_item(
            "f4_interest",
            f"f4:{row.get('source_sha256')}:{page}",
            3.0,
            title=f"중요 F4 · 지연이자 4구간 · {sha12} p.{page}",
            subtitle="원문 표에서 기간과 가산이율 네 쌍을 모두 확인합니다.",
            sha12=sha12,
            page=page,
            insurer="롯데손해보험",
            product_name=row.get("product_name") or "",
            source_group="중요 F4 지연이자",
            facts=facts,
            page_text=page_doc.get("text") or "",
            preview=_preview_rows((page_doc.get("tables_coords") or [{}])[0].get("records")) if page_doc.get("tables_coords") else [],
            page_image=_page_image(sha12, page) if embed_images else "",
            instructions="30일·60일·90일 경계와 가산이율 0%·4%·6%·8%가 정확한지 네 줄을 모두 대조하세요.",
        ))
    return output


def build_items(*, embed_images: bool) -> list[dict[str, Any]]:
    return (
        _table_b_items()
        + _table_a_items()
        + _outside_items("a1_gap_causes_s6_review240.jsonl", "a1", "A1 조항 밖 원인", embed_images=embed_images)
        + _outside_items("outside_clause_pages_s6_review200.jsonl", "outside2", "조항 밖 2차 표본", embed_images=embed_images)
        + _b8_items(embed_images)
        + _f4_items(embed_images)
    )


def assign_parts(items: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    normal = [row for row in items if row["source_type"] not in {"b8_disability", "f4_interest"}]
    important = [row for row in items if row["source_type"] in {"b8_disability", "f4_interest"}]
    bins: list[list[dict[str, Any]]] = [[] for _ in range(4)]
    totals = [0.0] * 4
    ordered = sorted(normal, key=lambda row: (-row["estimated_minutes"], _stable_key(row["review_id"])))
    for row in ordered:
        target = min(range(4), key=lambda index: (totals[index], len(bins[index]), index))
        bins[target].append(row)
        totals[target] += row["estimated_minutes"]
    for part in bins:
        part.sort(key=lambda row: (row["source_group"], row.get("insurer") or "", row["review_id"]))
    important.sort(key=lambda row: (0 if row["source_type"] == "b8_disability" else 1, -row.get("occurrences", 0), row["review_id"]))
    return bins + [important]


def _safe_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")


CSS = r"""
:root{--ink:#17211d;--muted:#59645f;--paper:#f3f5f1;--card:#fff;--line:#d7ded8;--accent:#16644f;--accent2:#e2f3ec;--warn:#9a4d05;--warnbg:#fff2d9;--danger:#9d2b2b;--done:#dff3e9}
*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font:15px/1.55 system-ui,"Malgun Gothic",sans-serif}.top{position:sticky;top:0;z-index:20;background:#fffffff2;backdrop-filter:blur(9px);border-bottom:1px solid var(--line);padding:12px 18px}.topline{display:flex;gap:10px;align-items:center;flex-wrap:wrap}.title{font-size:18px;font-weight:800}.badge{border-radius:999px;padding:4px 9px;background:var(--accent2);color:var(--accent);font-weight:700;font-size:12px}.important .badge{background:#ffe0d9;color:#982f20}.grow{flex:1}.progress{min-width:190px}.bar{height:7px;background:#e2e7e3;border-radius:9px;overflow:hidden}.bar i{display:block;height:100%;background:var(--accent);width:0}.controls{display:flex;gap:7px;flex-wrap:wrap;margin-top:9px}button,.file-label,input,select{font:inherit}button,.file-label{border:1px solid #9da9a2;background:#fff;border-radius:7px;padding:7px 10px;cursor:pointer}button.primary{background:var(--accent);border-color:var(--accent);color:#fff}button.active{background:var(--accent);color:#fff;border-color:var(--accent)}input.reviewer{width:170px;border:1px solid #aeb8b1;border-radius:7px;padding:7px 9px}.notice{max-width:1180px;margin:16px auto 0;padding:12px 15px;background:var(--warnbg);border-left:4px solid #d88925;border-radius:7px}.list{max-width:1180px;margin:14px auto 80px;padding:0 12px}.card{background:var(--card);border:1px solid var(--line);border-radius:11px;margin:13px 0;overflow:hidden;box-shadow:0 2px 9px #17211d0a}.card.done{border:2px solid #4e9a79}.card-head{padding:12px 15px;border-bottom:1px solid var(--line);display:flex;gap:10px;align-items:flex-start}.num{min-width:38px;height:28px;border-radius:7px;background:#edf0ed;text-align:center;padding-top:3px;font-weight:800}.headtext{flex:1}.headtext h2{font-size:16px;margin:0}.headtext p{margin:3px 0 0;color:var(--muted);font-size:13px}.time{font-weight:700;color:var(--accent);white-space:nowrap}.card-body{display:grid;grid-template-columns:minmax(0,1fr) minmax(320px,.8fr);gap:16px;padding:15px}.source,.decision{min-width:0}.instruction{background:#eef6f2;border-left:4px solid var(--accent);padding:9px 11px;margin:0 0 12px;border-radius:5px}.meta{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:10px}.meta span{background:#f0f2f0;border-radius:5px;padding:3px 7px;font-size:12px}.old{padding:9px 11px;border:1px dashed #c28a3e;background:#fff9e9;border-radius:7px;margin:8px 0}.preview{width:100%;border-collapse:collapse;table-layout:fixed;margin:8px 0 12px;font-size:13px}.preview td,.preview th{border:1px solid #bcc5bf;padding:6px;vertical-align:top;word-break:break-word}.preview th{background:#edf2ee}.page-image{width:100%;max-height:760px;object-fit:contain;background:#e5e8e5;border:1px solid var(--line);border-radius:6px}.textblock{white-space:pre-wrap;max-height:360px;overflow:auto;background:#f8faf8;border:1px solid var(--line);padding:10px;border-radius:6px;word-break:break-word}.facts{width:100%;border-collapse:collapse;margin:8px 0}.facts th,.facts td{border:1px solid #bfc8c1;padding:7px;text-align:left}.facts th{background:#eef2ef}.choices{display:grid;gap:7px}.choice{text-align:left;padding:10px 12px}.choice.active{background:var(--accent);color:#fff;border-color:var(--accent)}textarea{width:100%;min-height:105px;margin-top:10px;border:1px solid #aeb8b1;border-radius:7px;padding:9px;font:inherit}.required{font-size:12px;color:var(--muted)}details{margin-top:9px}summary{cursor:pointer;font-weight:700}.empty{padding:50px;text-align:center;color:var(--muted)}.guide-link{text-decoration:none;color:var(--ink)}@media(max-width:850px){.card-body{grid-template-columns:1fr}.top{position:relative}.page-image{max-height:none}}@media print{.top,.controls,.notice{display:none}.card{break-inside:avoid;box-shadow:none}.list{max-width:none}.card-body{grid-template-columns:1fr 1fr}}
.choice.active{background:var(--accent);color:#fff;border:3px solid #083f31;font-weight:800;box-shadow:0 0 0 3px #b7e4d1}.choice.active::before{content:"✓ 선택됨 · ";font-weight:900}.selected-status{margin:6px 0 10px;padding:9px 11px;border-radius:7px;background:var(--done);border:1px solid #4e9a79;color:#0b4938;font-weight:800}.selected-status.empty-selection{background:#f2f4f2;border-color:#c6cec8;color:var(--muted);font-weight:600}
"""


def _review_html(part_no: int, items: list[dict[str, Any]], summary: dict[str, Any]) -> str:
    choices = {kind: LABELS[kind] for kind in LABELS}
    important_class = " important" if part_no == 5 else ""
    payload = _safe_json(items)
    choice_payload = _safe_json(choices)
    total_minutes = summary["estimated_minutes"]
    return f'''<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>사람 검수 600건 · 파트 {part_no}</title><style>{CSS}</style></head>
<body class="{important_class.strip()}"><header class="top{important_class}"><div class="topline"><span class="title">사람 검수 600건 · 파트 {part_no}</span><span class="badge">{summary['items']}개 · 약 {summary['estimated_display']}</span><div class="grow"></div><label>검수자 <input id="reviewer" class="reviewer" placeholder="이름 입력"></label><div class="progress"><div id="progressText">0 / {summary['items']}</div><div class="bar"><i id="progressBar"></i></div></div></div>
<div class="controls"><a class="file-label guide-link" href="00_먼저읽기.html">쉬운 설명 다시 보기</a><a class="file-label guide-link" href="01_모범선택사례.html">모범 선택사례</a><button data-filter="all" class="active">전체</button><button data-filter="todo">미완료</button><button data-filter="done">완료</button><button data-filter="unsure">보류·수정</button><button id="nextTodo">다음 미완료</button><label class="file-label">이전 결과 불러오기<input id="importFile" type="file" accept=".jsonl,.json" hidden></label><button id="download" class="primary">결과 JSONL 내려받기</button></div></header>
<div class="notice"><b>{'중요 작업 전용 파트입니다. 모든 짝이 맞을 때만 승인하고, 하나라도 다르면 수정 또는 사용 불가를 선택하세요.' if part_no == 5 else '건수가 아니라 예상 검수시간으로 나눈 파트입니다. 화면에 보이는 컴퓨터 추정은 참고만 하고 직접 읽어 선택하세요.'}</b><br>선택 내용은 이 브라우저에 자동 임시저장됩니다. 잘못 눌렀으면 다른 선택지를 다시 누르면 됩니다. 다른 컴퓨터로 옮기기 전에는 반드시 결과 파일을 내려받으세요. 예상시간 {total_minutes:.1f}분은 배분을 위한 기준값입니다.</div>
<main id="list" class="list"></main>
<script>
const ITEMS={payload};const CHOICES={choice_payload};const PART={part_no};const KEY='human-review-600-v3-context-part-'+PART;
let state={{}},filter='all';try{{state=JSON.parse(localStorage.getItem(KEY)||'{{}}')}}catch(e){{state={{}}}}
const esc=s=>String(s??'');
function node(tag,cls,text){{const n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=text;return n}}
function previewTable(rows,heads){{if(!rows?.length)return null;const t=node('table','preview');if(heads){{const tr=node('tr');heads.forEach(h=>tr.append(node('th','',h)));t.append(tr)}}rows.forEach(r=>{{const tr=node('tr');r.forEach(c=>tr.append(node('td','',c)));t.append(tr)}});return t}}
function factsTable(item){{if(!item.facts?.length)return null;const isB=item.source_type==='b8_disability';const rows=item.facts.map(f=>isB?[f.ordinal,f.classification,f.payment_rate_percent+'%']:[f.period,f.rate]);return previewTable(rows,isB?['번호','장해 항목','후보 지급률']:['기간','후보 이율'])}}
function labelName(item,value){{return (CHOICES[item.source_type]||[]).find(x=>x[0]===value)?.[1]||value}}
function normalizeStoredState(){{let changed=false;const byId=Object.fromEntries(ITEMS.map(item=>[item.review_id,item]));Object.entries(state).forEach(([id,saved])=>{{const item=byId[id];if(!item||!saved?.label)return;const pair=(CHOICES[item.source_type]||[]).find(([value,text])=>saved.label===value||saved.label===text);if(pair&&saved.label!==pair[0]){{saved.label=pair[0];changed=true}}else if(!pair){{saved.invalid_label=saved.label;saved.label='';changed=true}}}});if(changed)localStorage.setItem(KEY,JSON.stringify(state))}}
function sourcePane(item){{const box=node('div','source');const meta=node('div','meta');[['자료',item.source_group],['보험사',item.insurer],['문서',item.sha12],['쪽',item.page],['예상',item.estimated_minutes+'분']].forEach(x=>{{if(x[1]!==''&&x[1]!=null)meta.append(node('span','',x[0]+' · '+x[1]))}});if(item.gap_start!=null)meta.append(node('span','','누락구간 · p.'+item.gap_start+'~'+item.gap_end));if(item.previous_covered_page!=null)meta.append(node('span','','앞 조항 끝 · p.'+item.previous_covered_page));if(item.next_covered_page!=null)meta.append(node('span','','뒤 조항 시작 · p.'+item.next_covered_page));box.append(meta);box.append(node('p','instruction',item.instructions));if(item.suggested_label||item.suggestion_reason){{const suggestion=node('div','old');suggestion.append(node('b','','컴퓨터 1차 제안: '+(item.suggested_label?labelName(item,item.suggested_label):'확실한 제안 없음')));suggestion.append(node('div','',item.suggestion_reason||''));suggestion.append(node('small','','제안은 정답이 아닙니다. 아래 원문을 확인한 뒤 직접 선택하세요.'));box.append(suggestion)}}if(item.previous_label){{const old=node('div','old','기존 사람 판정: '+item.previous_label+(item.previous_note?' · '+item.previous_note:''));box.append(old)}}if(item.page_image){{const img=node('img','page-image');img.src=item.page_image;img.alt=item.sha12+' '+item.page+'쪽 원문';img.loading='lazy';box.append(img)}}const ft=factsTable(item);if(ft)box.append(ft);const pt=previewTable(item.preview);if(pt){{const d=node('details');d.open=!item.page_image;d.append(node('summary','','추출된 표 미리보기'));d.append(pt);box.append(d)}}if(item.page_text||item.text_preview){{const d=node('details');d.open=item.source_type==='outside_clause'||(!item.preview?.length&&!item.page_image);d.append(node('summary','','현재 페이지 전체 글'));d.append(node('div','textblock',item.page_text||item.text_preview));box.append(d)}}if(item.previous_page_text){{const d=node('details');d.append(node('summary','','바로 앞 페이지 끝부분'));d.append(node('div','textblock',item.previous_page_text));box.append(d)}}if(item.next_page_text){{const d=node('details');d.append(node('summary','','바로 다음 페이지 시작부분'));d.append(node('div','textblock',item.next_page_text));box.append(d)}}return box}}
function decisionPane(item,card){{const box=node('div','decision');box.append(node('div','required','하나를 선택하세요. 보류·수정·사용 불가는 이유를 메모에 적어 주세요.'));const current=state[item.review_id]?.label||'';const invalid=state[item.review_id]?.invalid_label||'';box.append(node('div','selected-status'+(current?'':' empty-selection'),current?'✓ 현재 선택: '+labelName(item,current):(invalid?'⚠ 이전 선택값을 읽지 못했습니다. 다시 선택하세요.':'아직 선택하지 않았습니다.')));const choices=node('div','choices');(CHOICES[item.source_type]||[]).forEach(([value,label])=>{{const b=node('button','choice',label);b.type='button';b.dataset.label=value;b.setAttribute('aria-pressed','false');b.onclick=()=>{{const updated={{...(state[item.review_id]||{{}}),label:value,note:ta.value,updated_at:new Date().toISOString()}};delete updated.invalid_label;state[item.review_id]=updated;save();render()}};choices.append(b)}});box.append(choices);const ta=node('textarea','');ta.placeholder='판단 근거 또는 수정할 내용을 적어 주세요.';ta.value=state[item.review_id]?.note||'';ta.oninput=()=>{{state[item.review_id]={{...(state[item.review_id]||{{}}),note:ta.value,updated_at:new Date().toISOString()}};save(false)}};box.append(ta);return box}}
function isVisible(item){{const label=state[item.review_id]?.label||'';return filter==='all'||(filter==='todo'&&!label)||(filter==='done'&&!!label)||(filter==='unsure'&&['unsure','fix','reject'].includes(label))}}
function render(){{const list=document.getElementById('list');list.replaceChildren();let shown=0;ITEMS.forEach((item,index)=>{{if(!isVisible(item))return;shown++;const card=node('section','card'+(state[item.review_id]?.label?' done':''));card.dataset.id=item.review_id;const head=node('div','card-head');head.append(node('div','num',String(index+1)));const ht=node('div','headtext');ht.append(node('h2','',item.title));ht.append(node('p','',item.subtitle));head.append(ht);head.append(node('div','time','약 '+item.estimated_minutes+'분'));card.append(head);const body=node('div','card-body');body.append(sourcePane(item));const dp=decisionPane(item,card);dp.querySelectorAll('[data-label]').forEach(b=>{{const selected=b.dataset.label===state[item.review_id]?.label;b.classList.toggle('active',selected);b.setAttribute('aria-pressed',selected?'true':'false')}});body.append(dp);card.append(body);list.append(card)}});if(!shown)list.append(node('div','empty','이 조건에 해당하는 항목이 없습니다.'));updateProgress()}}
function save(doProgress=true){{localStorage.setItem(KEY,JSON.stringify(state));if(doProgress)updateProgress()}}
function updateProgress(){{const done=ITEMS.filter(i=>state[i.review_id]?.label).length;document.getElementById('progressText').textContent=done+' / '+ITEMS.length;document.getElementById('progressBar').style.width=(100*done/ITEMS.length)+'%'}}
document.querySelectorAll('[data-filter]').forEach(b=>b.onclick=()=>{{filter=b.dataset.filter;document.querySelectorAll('[data-filter]').forEach(x=>x.classList.toggle('active',x===b));render()}});
document.getElementById('reviewer').value=localStorage.getItem(KEY+'-reviewer')||'';document.getElementById('reviewer').oninput=e=>localStorage.setItem(KEY+'-reviewer',e.target.value);
document.getElementById('nextTodo').onclick=()=>{{filter='all';render();const item=ITEMS.find(i=>!state[i.review_id]?.label);if(item)document.querySelector('[data-id="'+CSS.escape(item.review_id)+'"]')?.scrollIntoView({{behavior:'smooth'}})}};
document.getElementById('download').onclick=()=>{{const reviewer=document.getElementById('reviewer').value.trim();const rows=ITEMS.map(item=>({{review_id:item.review_id,source_type:item.source_type,source_group:item.source_group,sha12:item.sha12,page:item.page,pattern_id:item.pattern_id||null,answer:state[item.review_id]?.label||'',note:state[item.review_id]?.note||'',reviewer,reviewed_at:state[item.review_id]?.updated_at||null,part:PART}}));const missing=rows.filter(r=>!r.answer).length;if(missing&&!confirm('미완료 '+missing+'개가 있습니다. 그래도 내려받을까요?'))return;const blob=new Blob([rows.map(r=>JSON.stringify(r)).join('\\n')+'\\n'],{{type:'application/x-ndjson'}});const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='human_review_600_part'+PART+'.jsonl';a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000)}};
document.getElementById('importFile').onchange=async e=>{{const text=await e.target.files[0].text();const rows=text.trim().startsWith('[')?JSON.parse(text):text.split(/\\r?\\n/).filter(Boolean).map(JSON.parse);rows.forEach(r=>{{if(r.review_id)state[r.review_id]={{label:r.answer||r.label||'',note:r.note||'',updated_at:r.reviewed_at||new Date().toISOString()}}}});normalizeStoredState();save();render()}};
normalizeStoredState();render();
</script></body></html>'''


def _display_minutes(minutes: float) -> str:
    rounded = int(round(minutes))
    hours, remainder = divmod(rounded, 60)
    return f"{hours}시간 {remainder}분" if hours else f"{remainder}분"


def _summaries(parts: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    output = []
    for number, rows in enumerate(parts, 1):
        minutes = round(sum(row["estimated_minutes"] for row in rows), 2)
        output.append({
            "part": number,
            "items": len(rows),
            "estimated_minutes": minutes,
            "estimated_display": _display_minutes(minutes),
            "source_counts": dict(sorted(Counter(row["source_group"] for row in rows).items())),
            "facts": sum(len(row.get("facts") or []) for row in rows),
            "important": number == 5,
        })
    return output


def _index_html(summaries: list[dict[str, Any]]) -> str:
    rows = []
    for row in summaries:
        sources = " · ".join(f"{key} {value}" for key, value in row["source_counts"].items())
        important = " ★ 중요 작업" if row["important"] else ""
        rows.append(f'''<tr><td><b>파트 {row['part']}{important}</b></td><td>{row['items']}</td><td>{html.escape(row['estimated_display'])}</td><td>{row['facts'] or '-'}</td><td>{html.escape(sources)}</td><td><a class="start" href="part{row['part']}.html">검수 시작</a></td></tr>''')
    return f'''<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>먼저 읽기 · 사람 검수 600건</title><style>{CSS}main{{max-width:1100px;margin:30px auto;padding:0 18px}}h1{{font-size:28px}}h2{{margin-top:34px}}h3{{margin-bottom:4px}}.lead{{font-size:17px;color:var(--muted)}}table{{width:100%;border-collapse:collapse;background:#fff;margin:10px 0 18px}}th,td{{border:1px solid var(--line);padding:11px;text-align:left;vertical-align:top}}th{{background:#e9efeb}}a.start{{display:inline-block;padding:7px 10px;background:var(--accent);color:#fff;text-decoration:none;border-radius:6px;white-space:nowrap}}.callout{{background:var(--warnbg);border-left:4px solid #d88925;padding:12px;margin:18px 0}}.ok{{background:#e8f5ef;border-left:4px solid var(--accent);padding:12px;margin:14px 0}}code{{background:#eef1ee;padding:2px 5px;border-radius:4px}}li{{margin:7px 0}}</style></head><body><main>
<h1>먼저 읽기 · 사람 검수 600건</h1>
<p class="lead">이 압축파일만 있으면 작업할 수 있습니다. 프로그램 설치나 원본 데이터 폴더가 필요하지 않습니다.</p>
<div class="ok"><b>하는 일은 간단합니다.</b> 화면에 나온 원문이나 표를 읽고, 가장 맞는 버튼 하나를 누른 뒤 마지막에 결과 파일을 내려받아 보내면 됩니다.</div>
<p><a class="start" href="01_모범선택사례.html">파트별 모범 선택사례 먼저 보기</a></p>

<h2>1. 다섯 명에게 나누는 방법</h2>
<p>팀원마다 서로 다른 파트 하나를 맡으세요. 같은 파트를 두 명이 작업하면 결과가 겹칩니다.</p>
<table><thead><tr><th>담당</th><th>화면 항목</th><th>예상시간</th><th>내부 사실</th><th>구성</th><th>시작</th></tr></thead><tbody>{''.join(rows)}</tbody></table>
<div class="callout"><b>파트 5는 가장 중요한 작업입니다.</b> 항목은 35개뿐이지만 장해지급률 192개 사실과 지연이자 36개 사실을 원문과 대조합니다. 후속 후보 1,297건의 사용 여부에 영향을 주므로 꼼꼼한 팀원이 맡는 것을 권장합니다.</div>

<h2>2. 작업 시작 방법</h2>
<ol><li>위 표에서 자기 파트의 <b>검수 시작</b>을 누릅니다.</li><li>화면 위쪽 <b>검수자</b> 칸에 자기 이름을 적습니다.</li><li>각 항목의 설명과 원문을 읽고 버튼 하나를 누릅니다.</li><li>보류·수정·사용 불가를 골랐다면 메모에 이유를 한 줄 적습니다.</li></ol>

<h2>3. 선택지는 이런 뜻입니다</h2>
<h3>표 A·B</h3><table><tr><th>선택</th><th>쉬운 뜻과 예시</th></tr><tr><td><b>표 맞음</b></td><td>행과 열이 있고, 같은 줄의 값끼리 관계가 있습니다. 예: 질병명 옆에 질병코드가 붙어 있음.</td></tr><tr><td><b>표 아님 / 본문을 표로 잘못 잡음</b></td><td>가로로 이어 읽으면 자연스러운 문장인데 컴퓨터가 칸으로 잘랐습니다.</td></tr><tr><td><b>표는 맞지만 칸 연결이 깨짐</b></td><td>실제 표이지만 제목과 값이 엉뚱하게 붙었거나 여러 행이 한 칸에 합쳐졌습니다.</td></tr><tr><td><b>판단 보류</b></td><td>화면만으로 확실히 결정할 수 없습니다. 왜 어려운지 메모합니다.</td></tr></table>
<h3>A1·조항 밖 페이지</h3><table><tr><th>선택</th><th>쉬운 뜻과 예시</th></tr><tr><td><b>약관 내용인데 조항에서 빠짐</b></td><td>보장 조건, 보상하지 않는 사항, 보험금 계산표, 장해표처럼 보험 약관 자체의 내용입니다.</td></tr><tr><td><b>외부 법령·참고자료</b></td><td><code>【법규47】</code>, 「감염병예방법」 제2조처럼 법률·시행령 원문을 참고용으로 붙인 페이지입니다. 약관 조항과는 따로 보관해야 합니다.</td></tr><tr><td><b>표지·목차·안내문</b></td><td>상품 표지, 목차, 찾아보기, 단순 이용 안내라서 보장 판단 본문이 아닙니다.</td></tr><tr><td><b>빈쪽·이미지만 있는 쪽</b></td><td>글이 없거나 로고·장식 그림만 있습니다. 이 선택은 내장된 원문 이미지를 보고 결정합니다.</td></tr><tr><td><b>그 밖의 조항 아닌 내용</b></td><td>위 네 종류에는 해당하지 않지만 보험 약관 조항도 아닙니다.</td></tr><tr><td><b>판단 보류</b></td><td>현재 쪽과 바로 앞뒤 쪽을 함께 봐도 결정할 수 없습니다.</td></tr></table>
<h3>중요 B8·F4</h3><table><tr><th>선택</th><th>언제 누르나요?</th></tr><tr><td><b>모두 맞음 / 승인</b></td><td>화면에 나온 모든 항목과 숫자의 짝이 원문과 전부 같습니다. 하나라도 틀리면 승인하면 안 됩니다.</td></tr><tr><td><b>일부 수정</b></td><td>대부분 맞지만 몇 개가 틀립니다. 메모 예: <code>3번 지급률 30%가 아니라 20%</code>.</td></tr><tr><td><b>사용 불가</b></td><td>여러 줄이 섞였거나 구조가 심하게 깨져 그대로 사용할 수 없습니다.</td></tr><tr><td><b>판단 보류</b></td><td>원문 이미지로도 확실히 읽을 수 없습니다. 어려운 부분을 메모합니다.</td></tr></table>

<h2>4. 저장과 재개</h2>
<ul><li>버튼을 누를 때마다 현재 브라우저에 자동 저장됩니다.</li><li>잘못 선택했다면 올바른 버튼을 다시 누르면 덮어씁니다.</li><li>다른 컴퓨터에서 이어 하려면 먼저 <b>결과 JSONL 내려받기</b>로 저장한 뒤, 새 컴퓨터에서 <b>이전 결과 불러오기</b>를 누릅니다.</li><li>브라우저 기록을 지우면 임시저장이 사라질 수 있으므로 20~30개마다 결과를 한 번 내려받는 것을 권장합니다.</li></ul>

<h2>5. 완료 후 보내는 파일</h2>
<ol><li>위쪽 진행 숫자가 전부 완료됐는지 확인합니다.</li><li><b>결과 JSONL 내려받기</b>를 누릅니다.</li><li>내려받은 <code>human_review_600_part1.jsonl</code> 같은 파일 <b>하나만</b> 취합 담당자에게 보냅니다.</li></ol>
<div class="callout"><b>보내면 안 되는 것:</b> 화면 캡처만 보내기, HTML 내용을 직접 고치기, 다른 파트 결과를 한 파일에 복사하기. 결과 JSONL 원본을 그대로 보내 주세요.</div>

<h2>6. 문제가 생겼을 때</h2>
<ul><li>화면이 비어 보이면 압축파일 안의 HTML을 다시 열어 보세요. 메신저 미리보기에서 열지 마세요.</li><li>사진이 작으면 브라우저 확대 기능을 사용하세요.</li><li>판단이 어려우면 억지로 승인하지 말고 <b>판단 보류</b>와 이유를 남기세요.</li><li>결과 파일을 두 번 내려받았다면 파일 이름 뒤 숫자가 붙을 수 있습니다. 가장 최근 파일 하나만 보내세요.</li></ul>
</main></body></html>'''


def _examples_html() -> str:
    return f'''<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>파트별 모범 선택사례</title><style>{CSS}
main{{max-width:1050px;margin:28px auto;padding:0 18px}}h1{{font-size:28px}}h2{{margin-top:38px;padding-top:14px;border-top:3px solid var(--accent)}}.lead{{font-size:17px;color:var(--muted)}}nav{{display:flex;gap:8px;flex-wrap:wrap;margin:18px 0}}nav a,.back{{padding:7px 11px;border-radius:7px;background:var(--accent);color:#fff;text-decoration:none}}.case{{background:#fff;border:1px solid var(--line);border-radius:10px;padding:15px;margin:13px 0}}.case h3{{margin:4px 0 8px}}.sample{{white-space:pre-wrap;background:#f5f7f5;border:1px solid var(--line);border-radius:7px;padding:11px;margin:9px 0}}.answer{{display:inline-block;background:#dff3e9;color:#125842;border-radius:6px;padding:4px 8px;font-weight:800}}.memo{{background:#fff4d8;border-radius:5px;padding:5px 8px}}.why{{border-left:4px solid var(--accent);padding-left:10px}}.warn{{background:var(--warnbg);border-left:4px solid #d88925;padding:12px;margin:16px 0}}code{{background:#eef1ee;padding:2px 5px;border-radius:4px}}
</style></head><body><main><a class="back" href="00_먼저읽기.html">← 먼저 읽기로 돌아가기</a><h1>파트별 모범 선택사례</h1><p class="lead">각 파트에서 만날 수 있는 대표 상황을 연습용으로 정리했습니다. 정답 버튼뿐 아니라 왜 그렇게 고르는지도 읽어 보세요.</p><div class="warn"><b>주의:</b> 이것은 판단 방법을 익히는 연습 예시입니다. 실제 검수에서는 문장이 비슷하다는 이유만으로 그대로 복사하지 말고, 현재 항목의 원문과 앞뒤 문맥을 확인하세요.</div><nav><a href="#part1">파트 1</a><a href="#part2">파트 2</a><a href="#part3">파트 3</a><a href="#part4">파트 4</a><a href="#part5">파트 5 · 중요</a></nav>

<section id="part1"><h2>파트 1 모범 사례</h2>
<article class="case"><h3>사례 1-1 · 법률 원문이 조항 밖에 있음</h3><div class="sample">【법규47】 감염병의 예방 및 관리에 관한 법률 제2조(정의)\n이 법에서 사용하는 용어의 뜻은 다음과 같다…</div><p>모범 선택: <span class="answer">외부 법령·참고자료</span></p><p class="why"><b>이유:</b> 보험회사가 정한 보장 조항이 아니라 법률 원문을 참고용으로 붙인 페이지입니다. 약관 조항 누락으로 처리하지 않습니다.</p><p>메모 예시: <span class="memo">법규47 표지와 감염병예방법 조문 확인</span></p></article>
<article class="case"><h3>사례 1-2 · 긴 문장이 두 칸처럼 잘림</h3><div class="sample">왼쪽: 회사는 보험금 청구서류를\n오른쪽: 접수한 날부터 3영업일 이내에 지급합니다.</div><p>모범 선택: <span class="answer">표 아님 / 본문을 표로 잘못 잡음</span></p><p class="why"><b>이유:</b> 왼쪽과 오른쪽을 이어 읽으면 하나의 자연스러운 문장입니다. 행·열 관계가 있는 표가 아닙니다.</p></article>
<article class="case"><h3>사례 1-3 · 지급률 별표가 조항에서 빠짐</h3><div class="sample">장해의 분류 | 지급률(%)\n두 눈이 멀었을 때 | 100\n한 눈이 멀었을 때 | 50</div><p>모범 선택: <span class="answer">약관 내용인데 조항에서 빠짐</span></p><p class="why"><b>이유:</b> 보험금 계산에 직접 쓰이는 약관 별표입니다. 법률 참고자료나 목차가 아닙니다.</p></article></section>

<section id="part2"><h2>파트 2 모범 사례</h2>
<article class="case"><h3>사례 2-1 · 질병명과 코드가 줄별로 대응함</h3><div class="sample">대상이 되는 질병 | 분류번호\n당뇨병 | E10~E14\n급성심근경색증 | I21~I23</div><p>모범 선택: <span class="answer">표 맞음</span></p><p class="why"><b>이유:</b> 같은 줄의 질병명과 코드가 한 쌍을 이루며, 열 제목도 분명합니다.</p></article>
<article class="case"><h3>사례 2-2 · 실제 표지만 값이 잘못 붙음</h3><div class="sample">질병명 열: 당뇨병 / 고혈압 / 뇌혈관질환\n코드 열: I10~I15 / E10~E14 / I60~I69</div><p>모범 선택: <span class="answer">표는 맞지만 칸 연결이 깨짐</span></p><p class="why"><b>이유:</b> 표 구조는 있지만 당뇨병과 고혈압 코드의 순서가 뒤바뀌었습니다.</p><p>메모 예시: <span class="memo">1·2행 코드가 서로 뒤바뀜</span></p></article>
<article class="case"><h3>사례 2-3 · 원문 이미지에도 로고만 있음</h3><div class="sample">추출 글 없음\n원문 이미지: 보험회사 로고와 장식선만 표시</div><p>모범 선택: <span class="answer">빈쪽·이미지만 있는 쪽</span></p><p class="why"><b>이유:</b> 스캔 글이 숨어 있는 페이지가 아니라 실제로 판단에 쓸 내용이 없는 장식 페이지입니다.</p></article></section>

<section id="part3"><h2>파트 3 모범 사례</h2>
<article class="case"><h3>사례 3-1 · 상품 목차 페이지</h3><div class="sample">목차\n제1관 목적 및 용어의 정의 ........ 4\n제2관 보험금의 지급 .............. 8</div><p>모범 선택: <span class="answer">표지·목차·안내문</span></p><p class="why"><b>이유:</b> 조항 위치를 알려 주는 페이지일 뿐 보장 조건 자체는 아닙니다.</p></article>
<article class="case"><h3>사례 3-2 · 시행령 조문을 통째로 수록</h3><div class="sample">의료법 시행령 제10조(업무)\n법 제○조에 따른 업무의 범위는 다음과 같다…</div><p>모범 선택: <span class="answer">외부 법령·참고자료</span></p><p class="why"><b>이유:</b> ‘시행령 제○조’ 형식의 외부 규정입니다. 약관 본문과 구분해 보존해야 합니다.</p></article>
<article class="case"><h3>사례 3-3 · 청약서 작성 안내</h3><div class="sample">고객님의 성명과 연락처를 정확히 기재해 주세요.\n서명란에는 반드시 자필로 서명해 주세요.</div><p>모범 선택: <span class="answer">그 밖의 조항 아닌 내용</span></p><p class="why"><b>이유:</b> 표지나 목차는 아니지만 보험금 보장·면책·계산에 쓰이는 약관 조항도 아닙니다.</p></article></section>

<section id="part4"><h2>파트 4 모범 사례</h2>
<article class="case"><h3>사례 4-1 · 보상하지 않는 사항이 이어짐</h3><div class="sample">회사는 다음 사유로 발생한 의료비를 보상하지 않습니다.\n1. 고의로 자신을 해친 경우\n2. 보험수익자가 고의로 피보험자를 해친 경우</div><p>모범 선택: <span class="answer">약관 내용인데 조항에서 빠짐</span></p><p class="why"><b>이유:</b> 면책 조건은 실제 보장 판정에 직접 사용되는 약관 내용입니다.</p></article>
<article class="case"><h3>사례 4-2 · 글이 중간에서 끊겨 종류를 알 수 없음</h3><div class="sample">…보험금 지급사유가 발생한 경우 회사는\n[이후 내용 없음, 앞뒤 페이지도 깨짐]</div><p>모범 선택: <span class="answer">판단 보류</span></p><p class="why"><b>이유:</b> 억지로 약관 누락이라고 확정할 근거가 없습니다. 앞뒤 원문도 읽을 수 없을 때만 보류합니다.</p><p>메모 예시: <span class="memo">현재·다음 페이지 글이 잘려 내용 종류 확인 불가</span></p></article>
<article class="case"><h3>사례 4-3 · 문장과 표를 구분</h3><div class="sample">항목 | 금액\n입원 | 5천만원 한도\n통원 | 회당 20만원 한도</div><p>모범 선택: <span class="answer">표 맞음</span></p><p class="why"><b>이유:</b> 각 행에서 치료 유형과 한도 금액이 일관되게 대응합니다.</p></article></section>

<section id="part5"><h2>파트 5 모범 사례 · 중요 작업</h2>
<article class="case"><h3>사례 5-1 · B8 모든 장해 항목과 지급률이 일치</h3><div class="sample">원문: 두 팔의 손목 이상을 잃었을 때 100% / 한 팔은 60%\n후보: 두 팔 100% / 한 팔 60%</div><p>모범 선택: <span class="answer">모든 항목과 지급률 짝이 맞음</span></p><p class="why"><b>이유:</b> 일부만 보지 말고 화면에 나온 모든 줄이 원문과 같은 것을 확인한 뒤 승인합니다.</p></article>
<article class="case"><h3>사례 5-2 · B8 한 줄만 지급률이 틀림</h3><div class="sample">원문 3번: 관절 기능을 완전히 잃었을 때 30%\n후보 3번: 관절 기능을 완전히 잃었을 때 20%</div><p>모범 선택: <span class="answer">일부 짝만 수정하면 됨</span></p><p class="why"><b>이유:</b> 표 전체를 버릴 필요는 없지만 그대로 승인할 수도 없습니다.</p><p>메모 예시: <span class="memo">3번 지급률 20% → 30%로 수정</span></p></article>
<article class="case"><h3>사례 5-3 · F4 네 기간이 모두 일치</h3><div class="sample">다음날~30일: 대출이율\n31~60일: 대출이율+4%\n61~90일: 대출이율+6%\n91일 이후: 대출이율+8%</div><p>모범 선택: <span class="answer">네 기간과 이율이 모두 맞음</span></p><p class="why"><b>이유:</b> 기간 경계와 가산이율 네 쌍을 전부 대조했습니다. 예를 들어 31~60일이 +5%로 적혀 있다면 ‘일부 수정’을 선택해야 합니다.</p></article>
<article class="case"><h3>사례 5-4 · 구조가 섞여 어느 숫자가 어느 항목인지 모름</h3><div class="sample">장해 항목 9개와 지급률 숫자 9개가 서로 다른 순서로 섞였고 원문 이미지도 잘림</div><p>모범 선택: <span class="answer">여러 짝이 틀려 사용할 수 없음</span></p><p class="why"><b>이유:</b> 몇 줄만 고치면 되는 상태가 아니라 짝 전체를 신뢰할 수 없습니다.</p><p>메모 예시: <span class="memo">항목·지급률 순서 전체 붕괴, 재추출 필요</span></p></article></section>

<div class="warn"><b>마지막 원칙:</b> 확실하지 않을 때 승인하지 마세요. 다만 원문과 앞뒤 문맥으로 충분히 판단할 수 있는데 습관적으로 보류하는 것도 피해야 합니다.</div><p><a class="back" href="00_먼저읽기.html">← 먼저 읽기로 돌아가기</a></p></main></body></html>'''


README_TEXT = """사람 검수 600건 · 5인 분담

1. 압축을 풉니다.
2. 00_먼저읽기.html을 더블클릭합니다.
3. 처음 작업한다면 01_모범선택사례.html을 읽습니다.
4. 팀원마다 파트 1~5 중 하나씩 맡습니다.
5. 자기 파트 화면 위에 이름을 쓰고 각 항목의 버튼 하나를 선택합니다.
6. 완료 후 '결과 JSONL 내려받기'를 누릅니다.
7. 내려받은 human_review_600_partN.jsonl 파일 하나만 취합 담당자에게 보냅니다.

주의
- 메신저 미리보기에서 작업하지 말고 압축을 푼 뒤 브라우저로 여세요.
- 보류·수정·사용 불가는 이유를 메모하세요.
- 화면 선택은 자동 임시저장되지만 브라우저 기록을 지우면 사라질 수 있습니다.
- 자세한 선택 기준과 예시는 00_먼저읽기.html에 있습니다.
"""


RETURN_CHECKLIST = """검수 결과 반환 체크리스트

[ ] 내 파트 번호가 다른 팀원과 겹치지 않는다.
[ ] 화면 위쪽에 검수자 이름을 입력했다.
[ ] 진행 숫자가 전체 개수와 같다.
[ ] 보류·수정·사용 불가 항목에 이유를 적었다.
[ ] '결과 JSONL 내려받기' 버튼으로 파일을 받았다.
[ ] 가장 최근 human_review_600_partN.jsonl 파일 하나만 보낸다.

HTML 파일이나 화면 캡처가 아니라 JSONL 결과 파일을 보내야 합니다.
"""


def _validate(parts: list[list[dict[str, Any]]]) -> None:
    flat = [row for part in parts for row in part]
    ids = [row["review_id"] for row in flat]
    if len(flat) != 600:
        raise ValueError(f"expected 600 items, got {len(flat)}")
    if len(ids) != len(set(ids)):
        duplicates = [item for item, count in Counter(ids).items() if count > 1]
        raise ValueError(f"duplicate review ids: {duplicates[:5]}")
    if any(row["source_type"] in {"b8_disability", "f4_interest"} for part in parts[:4] for row in part):
        raise ValueError("important work leaked outside part 5")
    if any(row["source_type"] not in {"b8_disability", "f4_interest"} for row in parts[4]):
        raise ValueError("part 5 contains non-important work")
    counts = Counter(row["source_type"] for row in flat)
    expected = {"table_b": 65, "table_a": 60, "outside_clause": 440, "b8_disability": 26, "f4_interest": 9}
    if dict(counts) != expected:
        raise ValueError(f"unexpected source counts: {dict(counts)}")


def build(output_dir: Path, *, embed_images: bool, make_zip: bool) -> dict[str, Any]:
    items = build_items(embed_images=embed_images)
    parts = assign_parts(items)
    _validate(parts)
    summaries = _summaries(parts)
    output_dir.mkdir(parents=True, exist_ok=True)
    for number, rows in enumerate(parts, 1):
        (output_dir / f"part{number}.html").write_text(_review_html(number, rows, summaries[number - 1]), encoding="utf-8")
    guide = _index_html(summaries)
    (output_dir / "index.html").write_text(guide, encoding="utf-8")
    (output_dir / "00_먼저읽기.html").write_text(guide, encoding="utf-8")
    (output_dir / "01_모범선택사례.html").write_text(_examples_html(), encoding="utf-8")
    (output_dir / "README_먼저읽기.txt").write_text(README_TEXT, encoding="utf-8")
    (output_dir / "검수결과_반환체크리스트.txt").write_text(RETURN_CHECKLIST, encoding="utf-8")
    manifest = {
        "schema_version": "human-review-600-five-parts-v4-selection-visible",
        "total_items": 600,
        "assignment_basis": "estimated_review_minutes",
        "part5_policy": "B8 and F4 important work only",
        "parts": summaries,
        "review_ids_sha256": hashlib.sha256("\n".join(sorted(row["review_id"] for row in items)).encode("utf-8")).hexdigest(),
    }
    (output_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if make_zip:
        PACKAGE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(PACKAGE_PATH, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for path in sorted(output_dir.iterdir()):
                if path.is_file():
                    archive.write(path, arcname=f"사람검수_600건_5인/{path.name}")
    return {"output_dir": str(output_dir), "package": str(PACKAGE_PATH) if make_zip else None, **manifest}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--no-images", action="store_true", help="skip embedded source-page images (test/debug only)")
    parser.add_argument("--no-zip", action="store_true")
    args = parser.parse_args()
    result = build(args.output_dir, embed_images=not args.no_images, make_zip=not args.no_zip)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
