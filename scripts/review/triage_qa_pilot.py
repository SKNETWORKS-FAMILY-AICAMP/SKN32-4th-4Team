# -*- coding: utf-8 -*-
"""검수 후보를 **규칙으로 먼저 훑어** 사람이 볼 것을 줄인다.

    python -m scripts.review.triage_qa_pilot
    python -m scripts.review.triage_qa_pilot --report      # 숫자만 본다

★★왜 만들었나 (2026-08-26)

    파트5 60건을 사람에게 넘겼더니 **메모가 18개 층 전부 층당 1종**으로 돌아왔다.
    항목별 판단이 아니라 층 단위 판단이다. 그런데 검수자를 탓할 수 없다 —
    같은 층 안에서 **초안이 글자까지 똑같기** 때문이다.

        A축 180건 → 고유 초안 25종   B축 60건 → 16종   C축 60건 → 4종
        전체 300건 → **고유 45종 (15%)**

    엔진 메시지가 `reason_code` 별 **고정 템플릿**이라 문서·질병기호가 달라도
    문장이 같다. 「3,000건 × 43.2초 = 36시간」이라는 05D 전제가 여기서 깨진다.

★★무엇을 하고 무엇을 하지 않나

    한다     결정론적 규칙으로 **초안의 결함을 짚는다**(내부용어·단정·모순·중복).
             결함이 있으면 **수정문을 제안**한다. 제안은 고정 틀에서 나온다.
             같은 문장이 여러 항목에 걸치면 **묶어서 한 번만 보게** 한다.

    안 한다  **자동 승인.** 05D §3-3 · 코덱스 결론 —
             기계 출력을 사람 확인 없이 학습에 넣지 않는다.
             모든 항목은 여전히 `decision: ""` 로 나가고 사람이 키를 누른다.
             LLM 도 쓰지 않는다 — 규칙만 쓴다. 그래야 **왜 그렇게 제안했는지** 감사된다.

★묶어도 되는 것과 안 되는 것을 가른다

    인용 0건   문장만 보면 된다 → **같은 문장은 한 번만**
    인용 있음  같은 문장이라도 **인용 조항이 다르면 답이 다르다** → 개별로 본다
"""

from __future__ import annotations

import argparse
import collections
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
IN_DEFAULT = ROOT / "data" / "finetune" / "qa_pilot" / "candidates.jsonl"
OUT_DEFAULT = ROOT / "data" / "finetune" / "qa_pilot" / "triage.jsonl"

#: 고객 문장에 나오면 안 되는 내부 표현. **필드명·상태값·파이프라인 용어.**
_JARGON = [
    "parse_status", "citation_eligible", "occurrence", "content_hash",
    "index_generation", "reason_code", "verdict", "sha256",
    "needs_expert", "needs_documents", "likely_covered",
    "suspect", "'suspect'", "구조화 상태",
    "근거 인용을 검증하지 못해", "인용을 검증",
]

#: 근거 없이 쓰면 안 되는 **단정 표현**. CLAUDE.md §0 — 잘못 말하면 사람이 손해를 본다.
_ASSERT = [
    "보장되지 않습니다", "보상되지 않습니다", "판매기간 밖입니다",
    "해당하지 않습니다", "보장됩니다", "지급됩니다", "면책입니다",
    "보상하지 않습니다",
]

_CLAUSE_NO = re.compile(r"제\s?\d+\s?조")
_CLAUSE_PATH = re.compile(r"[^\s,()]+/제\s?\d+\s?조")

#: 결함이 있을 때 내놓을 **고정 수정문**. `reason_code` 마다 하나씩.
#: ★새 사실을 주장하지 않는다 — 「확인하지 못했다 · 확인이 필요하다」까지만 말한다.
_TEMPLATE = {
    "no_version_at_date":
        "가입일인 {enrolled}에 적용되는 약관을 확인하지 못해 현재 정보만으로는 "
        "보장 여부를 판단할 수 없습니다. 가입 당시 보험증권과 약관을 확인한 뒤 "
        "전문가 검토가 필요합니다.",
    "citation_unverified":
        "현재 확인된 자료만으로는 {code} 관련 보장 여부를 판단할 근거 조항을 "
        "확인하지 못했습니다. 가입 당시 약관과 구체적인 치료 내용을 확인한 뒤 "
        "전문가 검토가 필요합니다.",
    "no_evidence":
        "{code}에 대해 약관에서 근거가 되는 조항을 찾지 못해 보장 여부를 판단하지 "
        "않았습니다. 면책 목록에 없다는 사실만으로 보장된다고 단정하지 않습니다. "
        "가입하신 약관으로 확인해 주세요.",
    "document_not_reliable":
        "해당 약관 문서를 정확히 읽지 못해 {code}의 보장 여부를 판단하지 않았습니다. "
        "가입 당시 약관 원문으로 확인한 뒤 전문가 검토가 필요합니다.",
    "product_not_matched":
        "말씀하신 상품을 저희가 보유한 약관에서 특정하지 못해 보장 여부를 판단하지 "
        "않았습니다. 가입하신 상품명과 증권을 확인해 주세요.",
    "ambiguous_product_line":
        "일반 실손·노후실손·유병력자실손 중 어느 상품인지에 따라 자기부담금이 다릅니다. "
        "가입하신 상품을 알려주시면 다시 확인해 드리겠습니다.",
}

#: C축 합성 초안(`판정하지 않았습니다 — …`)에 쓸 고정 수정문.
_TEMPLATE_C = {
    "no_evidence":
        "약관에서 근거가 되는 조항을 찾지 못해 보장 여부를 판단하지 않았습니다. "
        "가입하신 약관으로 확인해 주세요.",
    "ambiguous_product":
        "같은 시점에 적용될 수 있는 상품이 여럿이라 하나로 좁히지 못했습니다. "
        "가입하신 상품명을 알려주시면 다시 확인해 드리겠습니다.",
    "document_not_reliable":
        "해당 약관 문서를 정확히 읽지 못해 보장 여부를 판단하지 않았습니다. "
        "가입 당시 약관 원문으로 확인해 주세요.",
    "citation_unverified":
        "찾은 조항을 근거로 인용할 수 없어 보장 여부를 판단하지 않았습니다. "
        "가입 당시 약관 원문으로 확인해 주세요.",
}


#: 약관은 질병기호를 **범위로** 적는다(`F04~F99`). 낱개만 찾으면 못 찾는다 —
#: 실제로 F32 28건을 「인용에 없다」고 잘못 셌다(2026-08-26).
_RANGE = re.compile(r"([A-Z])\s*(\d{2})(?:\.\d+)?\s*[~∼-]\s*([A-Z])?\s*(\d{2})")


def code_in_text(code: str, text: str) -> tuple[str, str] | None:
    """질병기호가 인용문 안에 **직접** 또는 **범위로** 들어 있나, 그리고 **어디서**.

    ★★2026-08-26 — 처음엔 「있다/없다」만 돌려줬다. 그랬더니
      `F04~F99` 가 인용문 어딘가에 있다는 이유로 **통과**시켰는데,
      실제로 인용된 문장은 「의사 지시를 따르지 않아 발생한 의료비」였다.
      코드가 **있는지**가 아니라 **어느 줄에 있는지**를 봐야 한다.
      그래서 맞은 자리의 앞뒤를 함께 돌려준다 — 사람이 한눈에 본다.
    """
    base = (code or "").split(".")[0]
    if not base or len(base) < 3:
        return None
    flat = re.sub(r"\s+", " ", text or "")
    pos = flat.find(base)
    if pos >= 0:
        return "직접", flat[max(0, pos - 90):pos + 90].strip()
    letter, num = base[0], int(base[1:3])
    for m in _RANGE.finditer(flat):
        a, an, b, bn = m.group(1), int(m.group(2)), m.group(3) or m.group(1), int(m.group(4))
        if a == letter == b and an <= num <= bn:
            s = m.start()
            return f"범위 {m.group(0)}", flat[max(0, s - 90):s + 90].strip()
    return None


def _b_pairing(item: dict) -> dict | None:
    """B축 — 질문의 **가입유형·의료서비스**가 인용 표 사실과 같은 줄인가.

    ★검수자가 눈으로 하던 대조다. 표 사실 본문이 `가입유형: X` / `의료서비스: Y` 를
      그대로 담고 있으므로 **기계가 대조할 수 있다.** 사람은 결과만 본다.
    """
    ev = (item.get("evidence") or [None])[0]
    if not ev:
        return None
    body, q = ev.get("text") or "", item.get("question") or ""
    plan = re.search(r"가입유형:\s*(.+)", body)
    svc = re.search(r"의료서비스:\s*(.+)", body)
    if not plan or not svc:
        return None
    plan_v = plan.group(1).strip()
    svc_list = [s.strip() for s in svc.group(1).split(",") if s.strip()]
    plan_ok = plan_v.replace(" ", "") in q.replace(" ", "")
    svc_ok = any(s.replace(" ", "") in q.replace(" ", "") for s in svc_list)
    return {"plan": plan_v, "plan_ok": plan_ok,
            "service": ", ".join(svc_list), "service_ok": svc_ok,
            "ok": plan_ok and svc_ok}


#: 조사 앞 공백 — 보험사명·따옴표 뒤에서만 본다(일반 문장의 우연한 일치를 피한다).
_JOSA_GAP = re.compile(r"(?:[가-힣]{2,}(?:화재|해상|생명|보험)|['’\)]) (?:에서|의|과\(와\)|과|와|을|를)")

#: 표 금액이 붙어 나온 자리.
_RUNON = re.compile(r"원과[가-힣]|[가-힣]의\d+%|%중|\)중|\d만\d")

#: 재띄어쓰기 — **좁은 패턴에만** 손댄다. 새 낱말을 만들지 않는다.
_RESPACE = [
    (re.compile(r"(\d)만(\d)"), r"\1만 \2"),
    (re.compile(r"(원과)([가-힣])"), r"\1 \2"),
    (re.compile(r"([가-힣])의(\d+)%"), r"\1의 \2%"),
    (re.compile(r"%중"), "% 중"),
    (re.compile(r"\)중"), ") 중"),
    (re.compile(r"금액 입니다"), "금액입니다"),
    (re.compile(r"([가-힣]{2,}(?:화재|해상|생명|보험)) (에서|의|과|와|을|를)\b"), r"\1\2"),
    (re.compile(r"' (과\(와\)|과|와|의|을|를)\b"), r"'\1"),
    (re.compile(r"\s{2,}"), " "),
]


def respace(text: str) -> str:
    """붙어 나온 문장을 **정해진 자리에서만** 띄운다.

    ★★2026-08-26 — 이 표를 셸 heredoc 으로 써 넣었더니 그룹 참조와 \\b 가
      **먹혀서** 치환문이 빈 문자열이 됐다. 그 결과
      `보상대상의료비의20%` → `상대상의료의 %` 로 **글자와 숫자가 사라졌다.**
      그대로 나갔으면 틀린 금액을 학습시킬 뻔했다.
      ★그래서 아래 `_assert_respace_is_lossless()` 가 **import 시점에** 막는다.
    """
    out = text or ""
    for rx, rep in _RESPACE:
        out = rx.sub(rep, out)
    return out.strip()


def _assert_respace_is_lossless() -> None:
    """재띄어쓰기가 **글자·숫자를 잃지 않는지** 켤 때마다 확인한다.

    띄어쓰기만 고치는 함수이므로 **공백을 뺀 나머지는 그대로여야 한다.**
    한 글자라도 달라지면 그건 띄어쓰기가 아니라 내용 변경이다.
    """
    samples = [
        "1만5천원과보상대상의료비의20%중 큰 금액 입니다.",
        "8천원과보상대상의료비의20% 중 큰 금액 입니다.",
        "1만 5천원과공제기준금액주)중 큰 금액 입니다.",
        "'무배당 실손의료비 특별약관(1501)' 과(와) 이름이 맞는 약관을 흥국화재 에서 찾지 못했습니다.",
    ]
    for s in samples:
        got = respace(s)
        if re.sub(r"\s+", "", got) != re.sub(r"\s+", "", s):
            raise RuntimeError(
                "재띄어쓰기가 글자를 바꿨습니다 — 치환문이 깨졌습니다." + chr(10)
                + f"  원본: {s}" + chr(10) + f"  결과: {got}")


_assert_respace_is_lossless()


def _fmt_date(yyyymmdd: str | None) -> str:
    if not yyyymmdd or len(yyyymmdd) != 8:
        return "가입일"
    return f"{yyyymmdd[:4]}년 {int(yyyymmdd[4:6])}월 {int(yyyymmdd[6:8])}일"


def scan(text: str) -> list[dict]:
    """초안에서 **확인 가능한 결함**만 짚는다. 의견이 아니라 사실이다."""
    t = text or ""
    found: list[dict] = []
    for j in _JARGON:
        if j in t:
            found.append({"rule": "내부용어", "hit": j,
                          "why": "고객이 읽을 문장에 내부 필드명·상태값이 있다"})
    for a in _ASSERT:
        if a in t:
            found.append({"rule": "단정", "hit": a,
                          "why": "근거 없이 결론을 단정한다"})
    if ("특정할 수 없" in t or "특정하지 못" in t) and _CLAUSE_NO.search(t):
        found.append({"rule": "모순", "hit": _CLAUSE_NO.search(t).group(0),
                      "why": "「특정할 수 없다」면서 조항 번호를 대고 있다"})
    paths = _CLAUSE_PATH.findall(t)
    for path, n in collections.Counter(paths).items():
        if n > 1:
            found.append({"rule": "중복조항", "hit": path,
                          "why": f"같은 조항 경로가 {n}번 나온다"})
    #: ★인용이 0건인데 「아래 근거·후보」라고 가리키면 가리킬 것이 없다.
    #:   처음엔 「근거」만 봐서 `product_not_matched` 의 「아래 후보 중에서 골라 주세요」
    #:   12건을 놓쳤다(2026-08-26).
    for word in ("근거", "후보", "세부"):
        if "아래" in t and word in t:
            found.append({"rule": "빈참조", "hit": f"아래…{word}",
                          "why": "인용이 0건이면 가리킬 것이 없다 (인용 0건일 때만 결함)"})
            break
    #: ★조사 앞에 공백이 들어갔다 — 문자열을 그대로 이어 붙여 만든 문장의 흔적이다.
    #:   「흥국화재 에서」 「'…' 과(와)」 처럼 사람이 안 쓰는 형태다.
    m = _JOSA_GAP.search(t)
    if m:
        found.append({"rule": "조사공백", "hit": m.group(0).strip(),
                      "why": "조사 앞에 공백이 있다 — 사람이 쓴 문장으로 안 읽힌다"})
    #: ★표에서 읽은 금액이 **붙어쓰기로** 나온다. 그대로 승인하면 그 형태를 학습한다.
    if _RUNON.search(t):
        found.append({"rule": "붙어쓰기", "hit": _RUNON.search(t).group(0),
                      "why": "표 원문을 그대로 이어 붙여 띄어쓰기가 깨졌다"})
    #: 같은 규칙·같은 지점이 여러 번 잡히는 것은 한 번만 센다.
    seen, uniq = set(), []
    for f in found:
        k = (f["rule"], f["hit"])
        if k in seen:
            continue
        seen.add(k)
        uniq.append(f)
    return uniq


def propose(item: dict, defects: list[dict]) -> tuple[str, str, str]:
    """제안 결정·제안 문장·근거를 돌려준다. 못 정하면 `("", "", 사유)`."""
    if not defects:
        return "", "", "규칙에 걸리는 결함이 없다 — 사람이 본다"

    axis = item.get("axis")
    req = item.get("request") or {}
    code = (req.get("kcd_codes") or [""])[0] or "해당 질병기호"
    if axis == "C":
        kind = (item.get("stratum") or "").split(":")[-1]
        tpl = _TEMPLATE_C.get(kind)
        if not tpl:
            return "", "", f"C축 {kind} 용 고정 수정문이 없다"
        return "E", tpl, "초안에 내부 표현이 있어 고객 문장으로 바꾼다"

    #: ★결함이 **문장 형태뿐**이면 내용을 건드리지 않고 띄어쓰기만 고친다.
    #:   내용까지 바꾸면 표에서 읽은 금액을 사람이 다시 확인할 수 없게 된다.
    kinds = {d["rule"] for d in defects}
    if kinds <= {"붙어쓰기", "조사공백"}:
        fixed = respace(item.get("draft_answer") or "")
        if fixed and fixed != (item.get("draft_answer") or "").strip():
            return "E", fixed, "띄어쓰기만 고쳤다 — 숫자·내용은 그대로다"
        return "", "", "형태 결함인데 자동으로 고칠 자리를 못 찾았다"

    rc = (item.get("engine") or {}).get("reason_code")
    tpl = _TEMPLATE.get(rc)
    if not tpl:
        return "", "", f"`{rc}` 용 고정 수정문이 없다 — 사람이 쓴다"
    return ("E",
            tpl.format(enrolled=_fmt_date(req.get("enrolled_on")), code=code),
            "규칙이 짚은 결함을 고정 틀로 고친다")


def triage(rows: list[dict]) -> list[dict]:
    #: **묶을 수 있는 단위** = 글자까지 같은 초안. 층이 아니다(층 단위 승인은 05D 가 폐기).
    groups: dict[str, list[int]] = collections.defaultdict(list)
    for i, r in enumerate(rows):
        groups[r["draft_answer"].strip()].append(i)

    out = []
    for i, r in enumerate(rows):
        has_cite = bool(r.get("evidence"))
        defects = scan(r["draft_answer"])
        if has_cite:
            #: ★★인용이 있으면 **두 규칙이 결함이 아니다.**
            #:   「빈참조」 — 실제로 가리킬 것이 있다.
            #:   「단정」 — 「…는 보상하지 않습니다」가 **약관 조항을 옮긴 것**이면
            #:     단정이 아니라 인용이다. 근거 없이 결론 내는 것만 문제다.
            #:   ★2026-08-26 실측 — 이 구분 없이 셌더니 팀원이 고친 문장 37건 중
            #:     23건을 「단정」으로 잘못 잡았다. 규칙이 사람을 틀렸다고 한 것이다.
            defects = [d for d in defects if d["rule"] not in ("빈참조", "단정")]
        dec, ans, why = propose(r, defects)

        #: ★사람이 눈으로 하던 **대조를 기계가 미리 끝내 둔다.** 판단이 아니라 사실이다.
        checks: list[dict] = []
        if has_cite:
            ev0 = r["evidence"][0]
            if r["axis"] == "A":
                req = r.get("request") or {}
                code = (req.get("kcd_codes") or [""])[0]
                got = code_in_text(code, ev0.get("text") or "")
                how, ctx = got if got else (None, "")
                checks.append({
                    "check": "인용에 질병기호가 있나", "code": code,
                    "result": how or "없음", "ok": how is not None,
                    #: ★맞은 자리의 앞뒤를 그대로 보여 준다. 「있다」만으론 안 된다 —
                    #:   범위가 딴 문단에 있고 인용된 줄은 상관없는 내용일 수 있다.
                    "context": ctx,
                    "note": ("인용 창이 300자로 잘려 밖에 있을 수 있다"
                             if how is None
                             else "★이 자리가 질문과 상관있는 내용인지 직접 보세요"),
                })
            elif r["axis"] == "B":
                pair = _b_pairing(r)
                if pair:
                    checks.append({
                        "check": "가입유형·의료서비스가 질문과 같은 줄인가",
                        "result": f"가입유형 {pair['plan']}"
                                  f"{'✓' if pair['plan_ok'] else '✗'}"
                                  f" · 의료서비스 {pair['service']}"
                                  f"{'✓' if pair['service_ok'] else '✗'}",
                        "ok": pair["ok"], "note": "",
                    })

        #: ★★규칙도 코덱스도 **묻지 않아서** 아무도 안 본 자리가 있었다
        #:   (60건 중 23건, 2026-08-26). 물어야 답이 나온다 — 명시적으로 묻는다.
        extra_ask = ""
        rc_ = ((r.get("engine") or {}).get("reason_code") or "")
        if rc_ in ("excluded_by_clause", "exception_applies") and has_cite:
            _code = ((r.get("request") or {}).get("kcd_codes") or [""])[0]
            extra_ask = (f"이 인용이 **{_code} 에 대한** 판정을 실제로 받치나요? "
                         f"기호가 인용문 어딘가에 있다는 것만으로는 부족합니다 — "
                         f"**그 기호가 나온 줄이 무엇을 말하는지** 보세요.")

        siblings = groups[r["draft_answer"].strip()]
        out.append({
            "item_id": r["item_id"],
            "axis": r["axis"],
            "stratum": r["stratum"],
            "has_citation": has_cite,
            #: ★인용 0건이면 볼 것이 문장뿐이라 **같은 문장은 한 번만** 보면 된다.
            #:   인용이 있으면 문장이 같아도 근거가 달라 **개별로** 봐야 한다.
            "group_key": r["draft_answer"].strip()[:80] if not has_cite else r["item_id"],
            "group_size": len(siblings) if not has_cite else 1,
            "defects": defects,
            "checks": checks,
            "extra_ask": extra_ask,
            "proposed_decision": dec,
            "proposed_answer": ans,
            "proposed_why": why,
            #: 사람이 반드시 봐야 하는가 — 제안이 없거나 인용을 대조해야 하면 그렇다.
            #: 제안이 없거나, 인용을 대조해야 하거나, 위 별도 질문이 붙은 자리.
            "needs_human": (not dec) or has_cite or bool(extra_ask),
        })
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="검수 후보 규칙 사전분류")
    ap.add_argument("--in", dest="src", default=str(IN_DEFAULT))
    ap.add_argument("--out", default=str(OUT_DEFAULT))
    ap.add_argument("--report", action="store_true", help="파일을 쓰지 않고 숫자만 낸다")
    args = ap.parse_args()

    rows = [json.loads(l) for l in
            pathlib.Path(args.src).read_text(encoding="utf-8").splitlines() if l.strip()]
    tri = triage(rows)

    if not args.report:
        out = pathlib.Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w", encoding="utf-8", newline="\n") as f:
            for t in tri:
                f.write(json.dumps(t, ensure_ascii=False) + "\n")
        print(f"작성: {out}  {len(tri)}건")

    print()
    print(f"전체 {len(tri)}건")
    print(f"  규칙이 결함을 짚은 것       {sum(1 for t in tri if t['defects']):4d}")
    print(f"  수정문까지 제안한 것        {sum(1 for t in tri if t['proposed_decision']):4d}")
    print(f"  인용이 있어 개별로 봐야 함  {sum(1 for t in tri if t['has_citation']):4d}")
    print()
    #: ★사람이 **실제로 눌러야 하는 횟수** — 묶을 수 있는 것은 묶어서 센다.
    solo = [t for t in tri if t["has_citation"]]
    grouped = {t["group_key"] for t in tri if not t["has_citation"]}
    print(f"사람이 실제로 눌러야 하는 횟수:  {len(solo)} (개별) + {len(grouped)} (문장 묶음)"
          f" = **{len(solo) + len(grouped)}**   ← 300 이 아니다")
    print()
    print("기계가 미리 끝낸 대조 (사람은 결과만 본다):")
    for name in ("인용에 질병기호가 있나", "가입유형·의료서비스가 질문과 같은 줄인가"):
        got = [c for t_ in tri for c in t_["checks"] if c["check"] == name]
        if got:
            ok = sum(1 for c in got if c["ok"])
            print(f"  {name:34s} 통과 {ok:3d} / {len(got):3d}"
                  f"  → 어긋난 {len(got)-ok}건만 자세히 본다")
    print()
    print("규칙별 적발:")
    for k, n in collections.Counter(
            d["rule"] for t in tri for d in t["defects"]).most_common():
        print(f"  {k:10s} {n:4d}")
    print()
    print("제안이 안 나온 것(사람이 문장을 써야 함):")
    for k, n in collections.Counter(
            t["proposed_why"] for t in tri if not t["proposed_decision"]).most_common():
        print(f"  {n:4d}  {k}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
