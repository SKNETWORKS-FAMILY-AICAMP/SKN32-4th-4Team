# -*- coding: utf-8 -*-
"""판례 본문 1,096건 쟁점 분류 (실손 판정 연관성 기준).

- 네트워크 미사용. 로컬 파일만 읽는다.
- 산출물에 판례 원문 텍스트를 넣지 않는다(메타데이터 + 분류결과만).
- 출력: data/legal/prec_classified.jsonl
"""
import json, glob, os, re, collections

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BODIES = os.path.join(ROOT, 'data', 'legal', 'raw', 'bodies', '*.json')
DEST = os.path.join(ROOT, 'data', 'legal', 'prec_classified.jsonl')


def strip_html(s):
    s = s or ''
    s = re.sub(r'<[^>]+>', ' ', s)
    s = s.replace('&nbsp;', ' ').replace('&amp;', '&')
    return re.sub(r'\s+', ' ', s).strip()


# ---------------------------------------------------------------- 쟁점 렉시콘
# '실손해'(actual damage)는 '실손'과 다르다 -> (?!해)
# 맨 '실비'는 '실비변상'/'실비 상당'이 압도적이라 단독 신호로 쓰지 않는다.
TAG_PATTERNS = [
    ('본인부담상한액_초과환급', r'본인부담상한'),
    ('비급여_보상범위',        r'비급여'),
    ('입원_필요성',            r'입원(?:치료)?(?:의|이|가)?\s*필요(?:성|하|했|한|였)'),
    ('기왕증_기여도',          r'기왕증'),
    ('고지의무_위반',          r'고지의무|계약전\s*알릴|계약\s*후\s*알릴'),
    ('다초점_백내장',          r'백내장|다초점|인공수정체'),
    ('한방·도수·증식치료',      r'도수치료|증식치료|체외충격파|신장분사|한방병원|한방치료|한의원|한방의료'),
    ('보험사기',              r'보험사기|보험금을?\s*(?:부정)?\s*(?:편취|취득할\s*목적)|부정취득'),
    ('약관_설명의무',          r'설명의무|명시·?\s*설명|약관의\s*규제에\s*관한\s*법률'),
    ('구상금_보험자대위',      r'구상금|구상권|보험자대위|손해배상채권을?\s*대위'),
    ('실손형_의료비담보',      r'실손(?!해)|의료실비|실비보험|질병입원의료비|상해입원의료비|'
                              r'질병통원의료비|상해통원의료비|입원의료비|통원의료비'),
    ('자기부담금_공제',        r'자기부담금'),
    ('요양급여_기준',          r'요양급여(?:기준|의\s*기준)|임의비급여|요양급여비용|선별급여'),
    ('사회보험_부과처분',      r'산업재해보상보험|산재보험료|고용보험료|건강보험료|보험료부과|보수월액|평균임금'),
]

# ---------------------------------------------- 실손 "강신호" (기계 1차 선별)
STRONG = {
    'A_실손보험명칭': r'실손(?!해)의료보험|실손(?!해)의료비|실손(?!해)보험|의료실비보험|실비보험|실손형',
    'B_실손담보항목': r'질병입원의료비|상해입원의료비|질병통원의료비|상해통원의료비|입원의료비|통원의료비',
    'C_본인부담상한': r'본인부담상한',
    'D_백내장류':     r'다초점|인공수정체|백내장',
    'E_도수증식':     r'도수치료|증식치료|체외충격파|신장분사',
    #: ★★2026-08-11 추가 — **「실손」이라는 낱말 없이도 실손 판정 쟁점인 축들.**
    #:
    #:   그 전 렉시콘은 A~E 가 전부 「실손」·「의료비 담보명」·특정 처치명이라,
    #:   본문에 그 낱말이 있어야만 잡혔다. 그래서 코퍼스를 2,565 → 4,183 으로 늘려도
    #:   연관이 53건에서 **하나도 안 늘었다**(실측).
    #:
    #:   금감원 실손 96건의 제목을 세어 보니 최대 쟁점군은 백내장이 아니라
    #:   **「제3자에게서 받은 돈을 공제하는가」(12건 이상)** 였고,
    #:   그 판결문들은 「실손」이 아니라 **「피보험자가 실제로 부담한」** 이라고 쓴다.
    #:   대법원 2023다283913·2024다223949 가 공유하는 법리가 정확히 그것이다.
    'H_실제부담액':   r'실제로 부담한|실제 부담한|최종적으로 부담|사후환급|위험분담제|공단부담금',
    'I_제3자수령':    r'자동차보험(에서|으로)\s*(보상|지급)|산재보험.{0,6}(급여|보험금)|국가유공자.{0,6}의료비'
                     r'|의료비.{0,4}(감면|지원)|진료비.{0,4}할인',
    'J_입원수술인정': r'입원의?\s*필요성|입원치료의?\s*필요성|당일\s*입원|수술에\s*해당|수술의\s*정의',
}
WEAK_COUNT = {'F_비급여': (r'비급여', 2), 'G_실손': (r'실손(?!해)', 2)}

# ------------------------------------------------- 육안검증 결과 (전건 확인함)
# 1차 선별 27건을 전부 열어 확인했다. 아래 4건은 오탐이므로 연관에서 제외한다.
FALSE_POSITIVES = {
    '2004다52033':  '백내장·인공수정체는 상해보험 후유장해 산정의 기왕증 사실관계일 뿐, 실손 보상 쟁점 아님',
    '2013가합9197(본소), 2013가합520496(반소)': '휴대전화 단말기분실보험 정산 사건. "자기부담금"이 의료와 무관',
    '2016가합550030': '생명보험 사망보험금 사건. 실손 어휘가 부수적으로만 등장',
    '2020가단5040903': '쟁점은 계약 후 알릴의무(직업변경). 실손 담보는 보험증권 담보표 기재뿐',
    '2012나7924': '청구는 후유장해보험금 6.8억. 입원의료비 담보는 보험상품 구성 나열에만 등장',
}
# 동일 분쟁의 심급 중복(별건 아님)
INSTANCE_CHAINS = [
    ['2022가단5260084', '2023나29897', '2024다223949'],   # 위험분담제 환급금
    ['2023노878', '2024도11951'],                          # 실손 보험사기
]
# 강신호는 잡혔으나 "개별 청구의 지급 여부" 쟁점이 아닌 경계 사례
BORDERLINE = {
    '2019나11404': '실손 담보를 포함한 보험계약의 존속(존재확인) 다툼. 개별 청구 지급판정 쟁점은 아님',
}

CASE_TYPE_LABELS = ['민사_보험금지급', '민사_구상금', '민사_기타',
                    '행정_공보험처분', '형사_보험사기', '형사_기타']


def case_name_core(name):
    """대법원 사건명 뒤에 붙는 부기설명 [..] 을 떼어낸다."""
    return re.sub(r'\[[^\]]*\]', ' ', name)


def case_type(kind, name_core, summ):
    if kind == '형사':
        if re.search(r'보험사기|사기', name_core) or re.search(r'보험사기', summ):
            return '형사_보험사기'
        return '형사_기타'
    if kind == '일반행정':
        return '행정_공보험처분'
    if '구상금' in name_core:
        return '민사_구상금'
    if re.search(r'보험금|공제금|치료비|보험급여|채무부존재', name_core):
        return '민사_보험금지급'
    return '민사_기타'


def main():
    files = sorted(glob.glob(BODIES))
    recs, api_fail, parse_err = [], [], []
    for f in files:
        sid = os.path.basename(f)[:-5]
        try:
            d = json.load(open(f, encoding='utf-8'))
        except Exception as e:
            parse_err.append((sid, repr(e)))
            continue
        if 'PrecService' not in d:
            api_fail.append(sid)
            continue
        p = dict(d['PrecService'])
        p['_sid'] = sid
        recs.append(p)

    out = []
    for p in recs:
        name = strip_html(p.get('사건명', ''))
        core = case_name_core(name)
        summ = ' '.join(strip_html(p.get(k, '')) for k in
                        ('사건명', '판시사항', '판결요지', '참조조문'))
        body = strip_html(p.get('판례내용', ''))
        allt = summ + ' ' + body

        tags, kws, scopes = [], [], []
        for tag, pat in TAG_PATTERNS:
            m_s, m_b = re.search(pat, summ), re.search(pat, body)
            if not (m_s or m_b):
                continue
            if tag == '자기부담금_공제' and not re.search(
                    r'실손(?!해)|의료비|치료비|입원|통원|병원|의료기관|수술|진료', allt):
                continue
            tags.append(tag)
            kws.append((m_s or m_b).group(0)[:20])
            scopes.append('요지' if m_s else '전문')

        ctype = case_type(p.get('사건종류명', ''), core, summ)

        # 실손 강신호 1차 선별
        sig = [k for k, pat in STRONG.items() if re.search(pat, allt)]
        sig += [k for k, (pat, n) in WEAK_COUNT.items()
                if len(re.findall(pat, allt)) >= n]
        cno = p.get('사건번호', '')
        if not sig:
            verdict, related = '실손신호_없음', False
        elif cno in FALSE_POSITIVES:
            verdict, related = '육안검증_오탐', False
        elif cno in BORDERLINE:
            verdict, related = '육안검증_경계', False
        elif ctype in ('형사_보험사기', '형사_기타'):
            verdict, related = '실손_형사(무관)', False
        elif ctype == '행정_공보험처분':
            verdict, related = '실손_행정(무관)', False
        elif ctype == '민사_구상금':
            verdict, related = '실손_구상금(쟁점상이)', False
        else:
            verdict, related = '육안검증_연관', True

        out.append({
            '판례일련번호': p['_sid'],
            '사건번호': cno,
            '법원명': p.get('법원명', ''),
            '선고일자': str(p.get('선고일자', '')),
            '사건명': name,
            '사건종류명': p.get('사건종류명', ''),
            '사건유형': ctype,
            '쟁점태그': tags if tags else ['기타·무관'],
            '우리판정_연관': related,
            '근거키워드': sorted(set(kws)),
            '실손강신호': sorted(sig),
            '판정근거': verdict,
        })

    with open(DEST, 'w', encoding='utf-8') as fh:
        for r in out:
            fh.write(json.dumps(r, ensure_ascii=False) + '\n')

    # ------------------------------------------------------------- 통계 출력
    print(f'본문파일 {len(files)} / 정상 {len(recs)} / API실패응답 {len(api_fail)} / 파싱오류 {len(parse_err)}')
    n = len(recs)
    print('\n[사건유형]')
    ct = collections.Counter(r['사건유형'] for r in out)
    for k in CASE_TYPE_LABELS:
        print(f'  {k}\t{ct[k]}\t{ct[k]/n*100:.1f}%')
    print('\n[쟁점태그]')
    tg = collections.Counter(t for r in out for t in r['쟁점태그'])
    for k, v in tg.most_common():
        print(f'  {k}\t{v}\t{v/n*100:.1f}%')
    print('\n[실손 강신호 1차선별]', sum(1 for r in out if r['실손강신호']))
    print('[판정근거]')
    for k, v in collections.Counter(r['판정근거'] for r in out).most_common():
        print(f'  {k}\t{v}')
    print('\n[우리판정_연관 = true]', sum(1 for r in out if r['우리판정_연관']))

    # 구상금 상세
    bodytext = {}
    for f in files:
        d = json.load(open(f, encoding='utf-8'))
        if 'PrecService' in d:
            bodytext[d['PrecService']['사건번호']] = strip_html(d['PrecService']['판례내용'])
    gu = [r for r in out if r['사건유형'] == '민사_구상금']
    nhis = [r for r in gu if '국민건강보험공단' in bodytext.get(r['사건번호'], '')]
    print(f'\n[민사_구상금 {len(gu)}건] 그중 국민건강보험공단 당사자: {len(nhis)}')
    for r in gu:
        mark = '★공단' if r in nhis else '     '
        print('   ', mark, r['사건번호'], r['법원명'], r['선고일자'], r['사건명'][:55])

    # 연도/법원 분포
    print('\n[연도대 분포]')
    for k, v in sorted(collections.Counter(r['선고일자'][:3] + '0년대' for r in out).items()):
        print(f'  {k}\t{v}')
    print('\n[법원 상위]')
    for k, v in collections.Counter(r['법원명'] for r in out).most_common(10):
        print(f'  {k}\t{v}')

    print('\n[우리판정_연관 목록]')
    for r in sorted((r for r in out if r['우리판정_연관']), key=lambda x: x['선고일자']):
        print('  ', r['사건번호'], '|', r['법원명'], r['선고일자'], '|', r['사건명'][:45],
              '|', ','.join(r['실손강신호']))


if __name__ == '__main__':
    main()
