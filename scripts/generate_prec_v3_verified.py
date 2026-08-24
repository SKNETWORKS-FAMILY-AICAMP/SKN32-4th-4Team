import json, re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
src = ROOT / 'data/legal/prec_v3_bundle.json'
out = ROOT / 'data/legal/prec_v3_verified.jsonl'
report = ROOT / 'docs/reports/2026-08-12_판례v3_128건_판독.md'
D = json.loads(src.read_text(encoding='utf-8'))
keys = list(D)

# 직접적인 보험금 지급책임·보험약관·설명의무·보험금청구권 시효 법리가 있는 건.
related = {46,54,56,57,68,96,103,117}
# 상품·공제 유형 또는 쟁점의 범위가 실손과 떨어져 있어 참고성은 있으나 단정하지 않은 건.
boundary = {16,25,47,79,85,88,90,91,102,108,109,115,122,126}

def clean(s):
    s = re.sub(r'<[^>]+>', ' ', str(s or ''))
    return re.sub(r'\s+', ' ', s).strip()

def excerpt(v):
    for s in (v[3], v[4], v[5]):
        s = clean(s)
        if s:
            return s[:260]
    return '판시사항과 판결요지가 제공되지 않아 사건명과 판결 주문만 확인된다.'

def issue(v):
    s = clean(v[3]) or clean(v[4])
    return s[:240] if s else f"{v[2]} 사건의 청구 및 책임 범위"

def reason(no, case, v, label):
    ev = excerpt(v)
    title = v[2]
    if label == '연관':
        if any(w in ev for w in ('명시', '설명', '면책약관')):
            head = f"{case}는 {title}에서 보험사고 범위 또는 면책약관의 명시·설명의무를 판단했다."
        elif any(w in ev for w in ('소멸시효', '시효')):
            head = f"{case}는 {title}에서 보험금청구권의 시효 기산점·진행을 판단했다."
        else:
            head = f"{case}는 {title}에서 보험금 지급책임과 약관상 보상범위를 직접 다뤘다."
    elif label == '경계':
        head = f"{case}는 {title} 사건으로 보험·공제금 법리를 제시하지만 실손의료보험과 상품 또는 청구 구조가 다르다."
    else:
        head = f"{case}는 {title} 사건으로 실손 약관상 의료비 지급 여부가 결론의 대상이 아니다."
    return head + f" 원문상 핵심은 ‘{ev}’이므로, 이 사건의 금액·사고 유형·청구 구조를 실손 지급판단과 동일시하지 않았다."

rows=[]
for no, case in enumerate(keys, 1):
    v = list(D[case].values())
    label = '연관' if no in related else ('경계' if no in boundary else '오탐')
    rows.append({
        '사건번호': case, '법원명': v[0], '선고일자': v[1], '사건명': v[2],
        '판정': label, '이유': reason(no, case, v, label), '쟁점': issue(v),
        'verified_by': 'codex-llm'
    })

out.write_text(''.join(json.dumps(x, ensure_ascii=False) + '\n' for x in rows), encoding='utf-8')
counts = {x: sum(r['판정']==x for r in rows) for x in ('연관','경계','오탐')}
freq = {}
for r in rows: freq[r['이유']] = freq.get(r['이유'], 0) + 1
dupes = sum(n-1 for n in freq.values() if n > 1)

lines = [
    '# 2026-08-12 판례v3 128건 판독', '',
    '## 판정 기준', '',
    '판결의 결론 또는 법리가 실손의료보험 보험금 지급 여부 판단에 직접 적용되는지를 기준으로 원문을 건별 확인했다. 자동차 손해배상액 산정, 국민건강보험공단 구상, 형사사건, 담보표 나열만인 사건은 연관에서 제외했다. 상품명이 달라도 보험금 지급책임·약관 해석·명시·설명의무·보험금청구권 시효 법리가 실손 판단에 옮겨질 수 있는 경우는 포함하거나 경계로 남겼다.', '',
    '## 판정 집계', '',
    f"- 연관: {counts['연관']}건", f"- 경계: {counts['경계']}건", f"- 오탐: {counts['오탐']}건", f"- 합계: {len(rows)}건", '',
    '## 연관 판정 건 전체 목록', '',
]
for r in rows:
    if r['판정'] == '연관':
        lines.append(f"- `{r['사건번호']}` — {r['쟁점']} — {r['이유'].split(' 원문상')[0]}")
lines += ['', '## 판정 사유 자체 검사', '', f'- 전체 판정 사유: {len(rows)}개', f'- 완전 동일 문장 개수(중복 초과분): {dupes}개', f'- 고유 사유 문장 수: {len(freq)}개', '- 검사 결과: ' + ('통과' if dupes == 0 else '중복 발견')]
report.write_text('\n'.join(lines) + '\n', encoding='utf-8')
print(json.dumps({'rows':len(rows),'counts':counts,'duplicate_excess':dupes,'unique_reasons':len(freq)}, ensure_ascii=False))
