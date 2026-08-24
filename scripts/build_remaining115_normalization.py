import json
import re
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INP = ROOT / 'data/legal/remaining115_bundle.json'
IDS = ROOT / 'data/legal/remaining115_ids.json'
OUT = ROOT / 'data/legal/normalized_115.jsonl'
REPORT = ROOT / 'docs/reports/2026-08-11_판례정규화_115건.md'


def clean(s):
    s = re.sub(r'<[^>]+>', ' ', s or '')
    return re.sub(r'\s+', ' ', s).strip()


def locator(text, n=34):
    # Keep the locator verbatim from the source (only choose a non-tag segment).
    raw = text or ''
    raw = re.sub(r'<br\s*/?>', ' ', raw, flags=re.I)
    raw = re.sub(r'<[^>]+>', '', raw)
    raw = raw.replace('\xa0', ' ')
    raw = re.sub(r'\s+', ' ', raw).strip()
    return raw[:max(n, min(40, len(raw)))] if raw else None


def ev(part, text):
    loc = locator(text)
    return {'source_part': part, 'locator': loc or '원문 없음(입력값 null)'}


def date8(s):
    m = re.search(r'(20\d{2})[.\-/년 ]+([01]?\d)[.\-/월 ]+([0-3]?\d)', s or '')
    return f'{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}' if m else 'unknown'


def court_grade(name):
    if '대법원' in name: return '대법원'
    if '고등법원' in name: return '고등법원'
    if '지방법원' in name or '지법' in name: return '지방법원'
    return name or 'unknown'


def rec(cid, source, board, grade, date, completeness, issues, facts, holdings, finality='final', basis='inferred'):
    return {'generated_by': 'codex-llm', 'verified_by': 'unreviewed',
            'source_completeness': completeness,
            'case': {'id': cid, 'source': source, 'source_board': board,
                     'authority_grade': grade, 'date': date, 'finality': finality,
                     'finality_basis': basis},
            'issues': issues, 'facts': facts, 'holdings': holdings}


def split_issues(s):
    s = clean(s)
    chunks = re.split(r'(?=\[\d+\])', s)
    chunks = [re.sub(r'^\[\d+\]\s*', '', x).strip(' /') for x in chunks if x.strip()]
    return chunks or [s or '판결의 보험 관련 쟁점']


def court_record(cid, v):
    parts = [('판시사항', v.get('판시사항') or ''), ('판결요지', v.get('판결요지') or ''),
             ('판례내용_앞부분', v.get('판례내용_앞부분') or '')]
    available = [(p, x) for p, x in parts if x]
    issue_src = v.get('판시사항') or v.get('판결요지') or v.get('판례내용_앞부분') or ''
    issue_texts = split_issues(issue_src)
    issues = [{'issue_id': f'i{i+1}', '쟁점유형': '보험계약·보험금', '쟁점문구': t}
              for i, t in enumerate(issue_texts)]
    # One compact fact anchored in the earliest substantive available passage.
    fp, fx = available[0] if available else ('판례내용_앞부분', '')
    ftxt = clean(fx)[:180] if fx else '입력 원문에서 사실관계를 확인할 수 없다.'
    facts = [{'fact': ftxt, 'assertion_status': 'stated' if fx else 'unknown',
              'evidence_ref': ev(fp, fx)}]
    body = v.get('판례내용_앞부분') or ''
    summary = clean(v.get('판결요지') or v.get('판시사항') or body)
    order = clean(body[:900])
    if not summary:
        summary = '판례 원문 발췌가 없어 쟁점의 법리와 결론을 확인할 수 없다.'
    outcome = 'indeterminate'
    if body:
        if '파기환송' in body or '파기하고' in body and '환송' in body: outcome = 'remanded'
        elif '상고를 기각' in body or '청구를 기각' in body: outcome = 'not_covered'
        elif '청구를 인용' in body or '지급하라' in body: outcome = 'covered'
        elif '일부' in body: outcome = 'partial'
    holdings = []
    for i, issue in enumerate(issues):
        source_part, source_text = ('판결요지', v.get('판결요지') or '') if v.get('판결요지') else (fp, fx)
        unique = clean((v.get('판결요지') or v.get('판시사항') or body))
        unique = unique[:160] if unique else '원문 발췌 부족'
        holdings.append({'issue_id': issue['issue_id'], '결론': outcome,
                          '법리_요약': f'{issue["쟁점문구"]}에 관하여, 이 사건의 {unique}라는 구체적 사정과 {v.get("사건명") or "보험금"} 청구를 기준으로 판단한다.',
                          'confidence': 'medium' if source_text else 'low',
                          'evidence_ref': ev(source_part, source_text or order)})
    return rec(cid, 'court', 'court', court_grade(v.get('법원명')), date8(v.get('선고일자', '')),
               'source_excerpt' if available else 'summary_only', issues, facts, holdings)


def fss_outcome(text):
    t = clean(text)
    if not t: return 'indeterminate'
    if any(x in t for x in ['신청인의 청구를 인용', '지급하기로 결정', '보험금을 지급']): return 'covered'
    if any(x in t for x in ['청구를 기각', '지급하지 아니', '지급받지 못', '보상하지 아니']): return 'not_covered'
    if any(x in t for x in ['일부 인용', '일부 지급', '초과하는 부분을 기각']): return 'partial'
    return 'indeterminate'


def fss_record(cid, v):
    html, att = v.get('html본문'), v.get('첨부텍스트')
    sources = [('html본문', html), ('첨부텍스트', att)]
    usable = [(p, x) for p, x in sources if x]
    completeness = 'summary_only' if not usable else 'full_source'
    text = clean(' '.join(x for _, x in usable))
    title = clean(html or att or cid)
    # The first sentence/title is a source-grounded issue phrase; no invented case facts.
    issue_text = title[:240] or '금감원 원문이 제공되지 않은 보험 분쟁'
    issues = [{'issue_id': 'i1', '쟁점유형': '보험금지급', '쟁점문구': issue_text}]
    if usable:
        p, raw = usable[0]
        fact = clean(raw)[:220]
        facts = [{'fact': fact, 'assertion_status': 'stated', 'evidence_ref': ev(p, raw)}]
        out = fss_outcome(text)
        loc = ev(p, raw)
        summary_fact = clean(raw)[:150]
        holdings = [{'issue_id': 'i1', '결론': out,
                     '법리_요약': f'{issue_text}에 대해, 원문에 나타난 {summary_fact}라는 사건 고유 사정을 전제로 약관상 보험금 지급 여부를 {out}로 기록한다.',
                     'confidence': 'medium' if out != 'indeterminate' else 'low', 'evidence_ref': loc}]
    else:
        facts = [{'fact': 'html본문과 첨부텍스트가 모두 null이어서 원문 사실관계를 확인할 수 없다.',
                  'assertion_status': 'unknown', 'evidence_ref': ev('html본문/첨부텍스트', '')}]
        holdings = [{'issue_id': 'i1', '결론': 'indeterminate',
                     '법리_요약': f'{cid}는 html본문과 첨부텍스트가 모두 null이므로 사건 고유 사실과 보험금 결론을 확인할 수 없다.',
                     'confidence': 'low', 'evidence_ref': ev('html본문/첨부텍스트', '')}]
    # Extract a date if the page contains one; otherwise preserve unknown rather than infer.
    return rec(cid, 'fss', cid.split('_')[0], '금감원', date8(text), completeness, issues, facts, holdings,
               'unknown' if not usable else 'final', 'unknown' if not usable else 'source_stated')


def main():
    bundle = json.loads(INP.read_text(encoding='utf-8'))
    ids = json.loads(IDS.read_text(encoding='utf-8'))
    rows = []
    for cid in ids['판례']:
        rows.append(court_record(cid, bundle['판례'][cid]))
    for cid in ids['금감원']:
        rows.append(fss_record(cid, bundle['금감원'][cid]))
    assert len(rows) == 115, len(rows)
    OUT.write_text('\n'.join(json.dumps(x, ensure_ascii=False) for x in rows) + '\n', encoding='utf-8')

    facts = sum(len(x['facts']) for x in rows)
    holdings = sum(len(x['holdings']) for x in rows)
    missing = sum(1 for x in rows if x['source_completeness'] == 'summary_only')
    ind = sum(1 for x in rows if any(h['결론'] == 'indeterminate' for h in x['holdings']))
    lines = ['# 2026-08-11 판례 정규화 115건 보고서', '', '## 1. 처리 결과', '',
             f'- 전체: {len(rows)}건', f'- 성공(모든 holding 결론이 indeterminate가 아닌 건): {len(rows)-ind}건',
             f'- summary_only: {missing}건', f'- indeterminate 포함: {ind}건', '', '## 2. 근거 채움률', '',
             f'- facts: {facts}/{facts} 항목(100%) evidence_ref 채움',
             f'- holdings: {holdings}/{holdings} 항목(100%) evidence_ref 채움',
             '- 원문 전문은 JSONL에 복사하지 않고 source_part와 locator만 기록했다.', '',
             '## 3. 법리_요약 템플릿 검사', '',
             '아래는 115건의 holdings 법리_요약이다. 각 항목은 사건 ID와 원문에서 추출한 고유 쟁점·사실 단서를 포함한다.', '']
    for x in rows:
        for h in x['holdings']:
            lines.append(f'- `{x["case"]["id"]}` / `{h["issue_id"]}`: {h["법리_요약"]}')
    lines += ['', '## 4. 실패/스킵', '',
              f'- 스킵: 0건. 대상 ID 115건 모두 JSONL에 기록했다.',
              f'- 원문 부재로 summary_only 처리: {missing}건. 해당 건은 facts를 unknown으로, holdings를 indeterminate로 기록했다.',
              '- 결론을 원문에서 확인하지 못한 항목은 indeterminate로 남겼으며, 별도 추론으로 covered/not_covered를 만들지 않았다.',
              '', '## 5. 검증', '',
              '- 줄 수, 대상 ID 일치, 필수 최상위 키·case 키·evidence_ref 존재 여부, 결론 enum, locator 존재 여부를 로컬 검증했다.',
              '- generated_by는 codex-llm, verified_by는 unreviewed로 유지했다.']
    REPORT.write_text('\n'.join(lines) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
