# -*- coding: utf-8 -*-
"""전처리 v5 산출물 시각화 HTML 생성."""
import os, json, datetime
import pandas as pd

TAG = os.environ.get('VIZ_TAG', 's5')

#: ★추출기 이름을 글자로 박아 두면 안 된다. s5·s6 은 pymupdf 였지만
#:   s7 은 `s7_hybrid-table-v1` 이다. 박아 둔 채 s7 을 만들었더니 머리말이
#:   「추출기 pymupdf/1.28.0」이라고 **거짓을 적었다**. 디렉터리에서 읽어 온다.
def _extractor_of(tag: str) -> str:
    import glob as _glob
    names = {os.path.basename(p.rstrip('/\\'))
             for p in _glob.glob(f'data/structured/*/{tag}_*/')}
    suffix = sorted({n.split('_', 1)[1] for n in names if '_' in n})
    return ' · '.join(suffix) if suffix else '알 수 없음'


EXTRACTOR = _extractor_of(TAG)
import plotly.express as px
import plotly.graph_objects as go

df = pd.read_parquet(f'data/exports/{TAG}_documents.parquet')
ln = pd.read_parquet(f'data/exports/{TAG}_clause_lengths.parquet')['char_length']
kcd = pd.read_parquet('data/exports/kcd_chapter_kind.parquet')
mx = pd.read_parquet('data/exports/ref_matrix_top.parquet')
meta = json.load(open('data/exports/ref_matrix_meta.json', encoding='utf-8'))

figs = []
C = {'ok': '#3d8bfd', 'suspect': '#e8a05a', 'no_clause_heads': '#d9534f'}

# A
fA = px.scatter(df, x='pages', y='clauses', color='parse_status', color_discrete_map=C,
                hover_data=['sha12', 'insurer', 'generation', 'numbering',
                            'clauses_per_page', 'max_clause_len', 'product'],
                labels={'pages': '본문 쪽수', 'clauses': '조항 수', 'parse_status': '파싱 상태'},
                opacity=.72)
for r, nm in ((0.3, '조항/쪽 0.3'), (2.0, '조항/쪽 2.0')):
    xm = df.pages.max()
    fA.add_scatter(x=[0, xm], y=[0, r * xm], mode='lines',
                   line=dict(dash='dot', width=1, color='#999'), name=nm, hoverinfo='skip')
fA.update_layout(height=540, title='A. 문서별 쪽수 × 조항 수 — 선 아래는 조항이 너무 적은 문서')
figs.append(fA)

# D1 / D2
piv = df.pivot_table(index='insurer', columns='generation', values='sha12', aggfunc='count').fillna(0)
f = px.imshow(piv, text_auto=True, aspect='auto', color_continuous_scale='Blues', labels=dict(color='문서 수'))
f.update_layout(height=420, title='D-1. 보험사 × 세대 — 문서 수 (코퍼스 편중)')
figs.append(f)

df['bad'] = (df.parse_status != 'ok').astype(int)
piv2 = (df.pivot_table(index='insurer', columns='generation', values='bad', aggfunc='mean') * 100).round(1)
f = px.imshow(piv2, text_auto=True, aspect='auto', color_continuous_scale='Oranges', labels=dict(color='% 불량'))
f.update_layout(height=420, title='D-2. 보험사 × 세대 — parse_status ≠ ok 비율(%) (파서 편향)')
figs.append(f)

# Pareto
df['burden'] = df.ambiguous + df.unresolved + df.bad * 50
top = df.nlargest(40, 'burden')
p = top[['sha12', 'ambiguous', 'unresolved']].copy()
p['cum'] = (top.burden.cumsum() / df.burden.sum() * 100).round(1).values
f = go.Figure()
f.add_bar(x=p.sha12, y=p.ambiguous, name='모호 조항', marker_color='#e8a05a')
f.add_bar(x=p.sha12, y=p.unresolved, name='미해결 항', marker_color='#d9534f')
f.add_scatter(x=p.sha12, y=p.cum, name='누적 비중(%)', yaxis='y2', mode='lines+markers',
              line=dict(color='#0b5fa5'))
f.update_layout(barmode='stack', height=500,
                yaxis2=dict(overlaying='y', side='right', range=[0, 100], title='누적 %'),
                xaxis_tickangle=-60,
                title='Pareto. 오류 부담 상위 40문서 = 전체의 %s%% — 평평하다(소수 문서 수정으로 끝나지 않는다)' % p.cum.iloc[-1])
figs.append(f)

# B
f = px.histogram(ln[ln > 0], log_x=True, nbins=90,
                 labels={'value': '조항 길이(자, 로그)'}, color_discrete_sequence=['#3d8bfd'])
f.add_vline(x=30000, line_dash='dash', line_color='#d9534f', annotation_text='suspect 임계 30,000자')
q = ln.quantile([.5, .95, .99]).astype(int)
for v, c in ((q[.5], '중앙'), (q[.95], 'p95'), (q[.99], 'p99')):
    f.add_vline(x=v, line_dash='dot', line_color='#888', annotation_text='%s %s' % (c, format(v, ',')))
f.update_layout(height=450, showlegend=False,
                title='B. 조항 길이 분포 (n=%s) — 30,000자 초과 %s건 (%.2f%%)'
                      % (format(len(ln), ','), format(int((ln > 30000).sum()), ','), (ln > 30000).mean() * 100))
figs.append(f)

# E
kp = kcd.pivot_table(index='chapter', columns='kind', values='n', aggfunc='sum').fillna(0)
kp = kp.reindex(columns=[c for c in ['exclude', 'exception', 'mention'] if c in kp.columns])
kp = kp.loc[kp.sum(1).sort_values().index]
f = px.imshow(kp, text_auto='.0f', aspect='auto', color_continuous_scale='Reds', labels=dict(color='언급 수'))
f.update_layout(height=620,
                title='E. 약관이 직접 쓴 KCD 범위 — 질병 장(章) × 성격 (총 %s건)' % format(int(kcd.n.sum()), ','))
figs.append(f)

# F
f = px.scatter(mx, x='target_no', y='section', size='refs', color='dup',
               color_continuous_scale='Turbo', size_max=26,
               labels={'target_no': '참조된 조 번호', 'section': '참조를 한 부(部)',
                       'dup': '문서 내 같은 번호 개수', 'refs': '참조 횟수'},
               hover_data=['refs', 'dup'])
f.update_layout(height=560,
                title='F. 준용 모호 최다 문서 <b>%s</b>(%s) — 참조 %s건 중 %s건(%.0f%%)이 모호'
                      '<br><sub>색 = 그 조 번호를 가진 조항이 문서 안에 몇 개인가. '
                      '1이면 특정 가능, 2 이상이면 특정 불가, 0이면 문서 밖</sub>'
                      % (meta['sha12'], meta['insurer'], format(meta['tot'], ','),
                         format(meta['amb'], ','), meta['amb'] / meta['tot'] * 100))
figs.append(f)

parts = [fg.to_html(full_html=False,
                    include_plotlyjs='inline' if i == 0 else False,
                    config={'displaylogo': False}) for i, fg in enumerate(figs)]

built = datetime.datetime.now().strftime('%Y-%m-%d %H:%M')
STYLE = """<style>body{margin:0;padding:1.5rem 1.2rem 4rem;font:15px/1.7 -apple-system,"Segoe UI","Malgun Gothic",sans-serif;background:#fff;color:#1a1a1a}
main{max-width:1180px;margin:0 auto} h1{font-size:1.6rem;margin:0 0 .3rem}
h2{font-size:1.2rem;margin:2.5rem 0 .6rem;border-bottom:2px solid #e2e2e2;padding-bottom:.3rem}
.sub{color:#666;margin:0 0 1.2rem} table{border-collapse:collapse;width:100%;font-size:.9rem;margin:.6rem 0}
th,td{border:1px solid #e2e2e2;padding:.45rem .6rem;text-align:left} th{background:#fafafa}
td.n,th.n{text-align:right;font-variant-numeric:tabular-nums}
.box{border:1px solid #e2e2e2;border-left:4px solid #0b5fa5;background:#fafafa;padding:.7rem 1rem;margin:1rem 0;font-size:.92rem;border-radius:0 6px 6px 0}
.box.w{border-left-color:#a54b0b} .box b{display:block;margin-bottom:.25rem}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:.6rem;margin:1rem 0}
.stat{border:1px solid #e2e2e2;border-radius:6px;padding:.6rem .8rem;background:#fafafa}
.stat .v{font-size:1.4rem;font-weight:600} .stat .l{color:#666;font-size:.78rem}
code{background:#f0f0f0;padding:.1rem .3rem;border-radius:3px;font:12.5px ui-monospace,Consolas,monospace}</style>"""

head = ('<meta charset="utf-8"><title>전처리 v5 산출물 시각화</title>' + STYLE +
        '<main><h1>전처리 v5 산출물 시각화</h1>'
        '<p class="sub">생성 %s · 스키마 <code>s5</code> · 추출기 <code>pymupdf/1.28.0</code> · 문서 %s · 조항 %s</p>'
        '<div class="grid">'
        '<div class="stat"><div class="v">%s</div><div class="l">parse ok</div></div>'
        '<div class="stat"><div class="v">%s</div><div class="l">suspect</div></div>'
        '<div class="stat"><div class="v">%s</div><div class="l">인용 모호 조항</div></div>'
        '<div class="stat"><div class="v">%s</div><div class="l">번호 못읽은 항</div></div>'
        '<div class="stat"><div class="v">%s</div><div class="l">법령 원문 조항</div></div>'
        '<div class="stat"><div class="v">%s</div><div class="l">KCD 범위 언급</div></div>'
        '</div>'
        '<div class="box w"><b>★수치 스냅샷 주의</b>'
        '<b>2026-08-02 15:50~15:52 재생성 s5</b> 기준이다. <code>CLAUDE.md</code>·일부 리포트에는 '
        '이전 값(<code>ok 1,108 / suspect 250</code>)이 남아 있다. 시점이 다른 숫자를 섞지 말 것.</div>'
        '<div class="box"><b>무엇을 넣고 무엇을 뺐나</b>'
        '코덱스와 순위를 합의해 <b>보면 뭔가를 고치게 되는 것</b>만 넣었다. '
        '<b>2D 임베딩 투영(t-SNE·PCA)은 일부러 뺐다</b> — 왜곡이 크고 중복 조항 65%%에 그림이 지배돼 '
        '검색 실패 원인을 설명하지 못한다. 대신 k-NN 이웃 혼합률·Recall@k 실패 슬라이스를 봐야 한다.</div>'
        % (built, format(len(df), ','), format(len(ln), ','),
           format(int((df.parse_status == 'ok').sum()), ','),
           format(int((df.parse_status == 'suspect').sum()), ','),
           format(int(df.ambiguous.sum()), ','), format(int(df.unresolved.sum()), ','),
           format(int(df.statute.sum()), ','), format(int(kcd.n.sum()), ',')))


# ★스키마 태그를 완성된 문자열에서 바꾼다. 포맷 문자열을 건드리면 % 이스케이프가 깨진다.
if TAG != 's5':
    head = (head
            .replace('전처리 v5 산출물', f'전처리 {TAG} 산출물')
            .replace('<code>s5</code>', f'<code>{TAG}</code>')
            .replace('<b>2026-08-02 15:50~15:52 재생성 s5</b> 기준이다. '
                     '<code>CLAUDE.md</code>·일부 리포트에는 '
                     '이전 값(<code>ok 1,108 / suspect 250</code>)이 남아 있다. '
                     '시점이 다른 숫자를 섞지 말 것.',
                     f'<b>{TAG} 재추출 완료본</b> 기준이다({built}). '
                     f'<code>CLAUDE.md</code>·기존 리포트·이전 판 시각화에는 '
                     f'<b>이전 스키마(s5·s6) 기준 수치</b>가 남아 있다. 시점이 다른 숫자를 섞지 말 것.'))

#: ★머리말의 추출기 표기는 스키마를 따라간다(위 `_extractor_of`).
head = head.replace('<code>pymupdf/1.28.0</code>', f'<code>{EXTRACTOR}</code>')

def _db_section() -> str:
    """★G 절을 **실측으로 만든다.**

    2026-08-04 실측: 여기 있던 표는 손으로 적은 **s5 시절 적재 결과**였다
    (`조항 156,946 / 고유내용 2,221 / 청크 6,208` · *"고유 내용은 3.5%뿐"*).
    그 사이 S7.1 이 적재돼 실제로는 `s6` 발생 210,733 · 청크 122,772 인데,
    문서를 여는 사람은 **「DB 가 3.5% 적재됨」으로 읽는다.** 제출물에 그대로 나갈 뻔했다.

    ★그래서 숫자를 **글자로 두지 않고 매번 조회한다.** 못 붙으면 못 붙었다고 적는다 —
      조용히 옛 숫자를 남기는 것이 가장 나쁘다.
    """
    head_ = ('<h2>G. 전처리 → DB 적재 정합성</h2>\n'
             '<div class="box w"><b>분모가 다른 것을 한 퍼널로 합치지 않는다</b>\n'
             '조항(수록)·고유 내용·청크는 서로 다른 단위다. '
             '하나로 이으면 "몇 % 적재됨"이 잘못 계산된다.</div>\n')
    try:
        #: ★`python scripts/viz/build_preprocess_viz.py` 로 돌리면 저장소 루트가
        #:   `sys.path` 에 없어 `app` 을 못 찾는다(실제로 여기서 한 번 실패했다).
        #:   `-m` 으로만 돌게 강제하지 않고, 어느 쪽으로 불러도 되게 여기서 붙인다.
        import sys
        _root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
        if _root not in sys.path:
            sys.path.insert(0, _root)
        import psycopg
        from app.core.config import get_settings
        with psycopg.connect(get_settings().PGVECTOR_DSN, connect_timeout=10) as conn:
            with conn.cursor() as cur:
                def one(sql, *a):
                    cur.execute(sql, a)
                    return cur.fetchone()[0]

                def rows(sql, *a):
                    cur.execute(sql, a)
                    return cur.fetchall()

                occ_gen = rows("SELECT index_generation, count(*) FROM policy_clause_occurrence"
                               " GROUP BY 1 ORDER BY 2 DESC")
                occ_kind = rows("SELECT source_kind, count(*) FROM policy_clause_occurrence"
                                " WHERE index_generation=%s GROUP BY 1 ORDER BY 2 DESC", TAG)
                chunk_model = rows("SELECT embed_model, count(*) FROM policy_clause_chunk"
                                   " GROUP BY 1 ORDER BY 2 DESC")
                occ_cur = one("SELECT count(*) FROM policy_clause_occurrence"
                              " WHERE index_generation=%s", TAG)
                uniq_cur = one("SELECT count(DISTINCT content_hash) FROM policy_clause_occurrence"
                               " WHERE index_generation=%s", TAG)
                content_n = one("SELECT count(*) FROM policy_clause_content")
                chunk_n = one("SELECT count(*) FROM policy_clause_chunk")
                eligible = one("SELECT count(*) FROM policy_clause_occurrence"
                               " WHERE index_generation=%s AND citation_eligible", TAG)
                leaked = one("SELECT count(*) FROM policy_clause_occurrence"
                             " WHERE index_generation<>%s AND citation_eligible IS NOT NULL", TAG)
                #: ★「TAG 아닌 세대에 게이트 값이 있으면 누수」는 **TAG 가 곧 활성 세대일 때만**
                #:   맞는 말이다. s7 문서를 만들면서 그대로 뒀더니, 실제로 검색에 쓰이는 s6 를
                #:   「누수 190,155건」·「막혀 있다」로 적었다. 활성 세대를 DB 에 직접 묻는다.
                act = rows("SELECT index_generation, count(*) FROM policy_clause_occurrence"
                           " WHERE citation_eligible IS NOT NULL GROUP BY 1 ORDER BY 2 DESC LIMIT 1")
                active_gen = act[0][0] if act else None
                with_vec = one(
                    "SELECT count(*) FROM policy_clause_occurrence o"
                    " WHERE o.index_generation=%s AND EXISTS ("
                    "   SELECT 1 FROM policy_clause_chunk k WHERE k.content_hash=o.content_hash)",
                    TAG)
    except Exception as exc:                      # noqa: BLE001 — 원인을 화면에 그대로 싣는다
        return (head_ + '<div class="box w"><b>DB 에 붙지 못해 적재 수치를 싣지 못했다</b><br>'
                f'<code>{type(exc).__name__}: {str(exc)[:200]}</code><br>'
                '★옛 숫자를 대신 채우지 않는다. '
                '<code>python -m scripts.pg status</code> 로 기동을 확인한 뒤 다시 만든다.</div>')

    n = lambda v: format(int(v), ',')             # noqa: E731
    def _why(g: str) -> str:
        if g == active_gen:
            return ('★<b>지금 검색에 쓰이는 세대</b> — 게이트 값이 채워져 있다'
                    + ('' if g == TAG else f' (이 문서가 다루는 <code>{TAG}</code> 가 아니다)'))
        return '게이트 값이 비어 있어 검색에서 막힌다'

    gen_rows = ''.join(
        f'<tr><td><code>{g}</code></td><td class="n">{n(c)}</td><td>{_why(g)}</td></tr>'
        for g, c in occ_gen)

    #: 이 스키마가 아직 DB 에 없을 수 있다. 그때 0 을 「적재 실패」로 읽히게 두지 않는다.
    not_loaded = (
        '' if occ_cur else
        f'<div class="box w"><b><code>{TAG}</code> 는 아직 DB 에 적재되지 않았다</b>'
        f'전처리 산출물({n(len(ln))}조항)은 디스크에 다 있지만 <code>policy_clause_occurrence</code> 에는 '
        f'<code>{TAG}</code> 행이 <b>0건</b>이다. 실패가 아니라 <b>아직 적재 안 한 상태</b>다. '
        f'지금 검색은 <code>{active_gen}</code> 로 돌아간다 — 아래 표의 0 을 '
        f'「적재 실패」로 읽지 말 것.</div>')
    kind_rows = ''.join(f'<tr><td><code>{k}</code></td><td class="n">{n(c)}</td></tr>'
                        for k, c in occ_kind)
    model_rows = ''.join(f'<tr><td><code>{m}</code></td><td class="n">{n(c)}</td></tr>'
                         for m, c in chunk_model)
    return head_ + not_loaded + (
        f'<table><tr><th>단위</th><th class="n">전처리 산출({TAG})</th>'
        f'<th class="n">DB 적재({TAG})</th><th>비고</th></tr>'
        f'<tr><td>조항(수록) <code>policy_clause_occurrence</code></td>'
        f'<td class="n">{n(len(ln))}</td><td class="n">{n(occ_cur)}</td>'
        f'<td>그중 벡터가 있는 발생 <b>{n(with_vec)}</b> · 없는 발생 {n(occ_cur - with_vec)}</td></tr>'
        f'<tr><td>고유 내용 <code>policy_clause_content</code></td>'
        f'<td class="n">{n(uniq_cur)}</td><td class="n">{n(content_n)}</td>'
        f'<td>왼쪽은 {TAG} 발생의 고유 <code>content_hash</code>, 오른쪽은 테이블 전체 행</td></tr>'
        f'<tr><td>청크 <code>policy_clause_chunk</code></td>'
        f'<td class="n">—</td><td class="n">{n(chunk_n)}</td>'
        f'<td>임베딩 모델별 내역은 아래</td></tr></table>'
        f'<table><tr><th>index_generation</th><th class="n">발생</th><th>뜻</th></tr>{gen_rows}</table>'
        f'<table><tr><th>{TAG} source_kind</th><th class="n">발생</th></tr>{kind_rows}</table>'
        f'<table><tr><th>embed_model</th><th class="n">청크</th></tr>{model_rows}</table>'
        f'<div class="box"><b>읽는 법</b><br>'
        f'· <code>{TAG}</code> 발생 중 <b>인용 가능</b>(<code>citation_eligible</code>)은 <b>{n(eligible)}</b>건이다.<br>'
        f'· 지금 <b>검색에 실제로 쓰이는 세대</b>는 <code>{active_gen}</code> 다. '
        + (f'이 문서가 다루는 <code>{TAG}</code> 와 <b>다르다</b> — '
           f'그러므로 아래 그림·표(전처리 산출)와 DB 적재는 <b>다른 판을 보고 있다.</b><br>'
           if active_gen != TAG else
           f'이 문서가 다루는 판과 같다.<br>')
        + f'· 게이트 값이 채워진 행은 <code>{active_gen}</code> 계열 <b>{n(leaked if active_gen != TAG else 0)}</b>건이다 — '
        f'활성 세대이므로 <b>정상</b>이다. 「0 이어야 정상」은 '
        f'<code>{TAG}</code> 가 활성 세대일 때만 성립한다.<br>'
        f'· ★<b>이 표는 문서를 만들 때 DB 를 직접 조회한 값이다.</b> '
        f'전처리 산출과 DB 적재는 <b>단위가 다르므로</b> 차이를 "누락"으로 읽지 않는다.</div>')


dbtab = _db_section()

tail = dbtab + """<div class="box"><b>원자료를 직접 보려면</b>
집계는 이 문서로, 행 단위 탐색은 <b>Parquet + Tad</b>(MIT · DuckDB 기반)로 나눴다.
수십만 행을 단일 HTML에 넣으면 파일 크기와 브라우저 메모리가 문제가 된다.
<code>data/exports/s5_clauses.parquet</code> (211,131행 · 83MB) ·
<code>s5_documents.parquet</code> · <code>s5_clause_lengths.parquet</code></div></main>"""

#: ★꼬리말도 스키마를 따라가야 한다. 예전엔 여기만 `s5` 로 굳어 있어
#:   s6 페이지가 **s5 parquet 을 가리켰다.**
#:   (DB 표는 이제 `_db_section()` 이 실측으로 만든다 — 여기서 문자열 치환하지 않는다.)
if TAG != 's5':
    tail = (tail
            .replace('data/exports/s5_clauses.parquet',
                     'data/exports/%s_clauses.parquet' % TAG)
            .replace('<code>s5_documents.parquet</code>',
                     '<code>%s_documents.parquet</code>' % TAG)
            .replace('<code>s5_clause_lengths.parquet</code>',
                     '<code>%s_clause_lengths.parquet</code>' % TAG)
            .replace('(211,131행 · 83MB)', '(%s행)' % format(len(ln), ',')))

def _tad_banner() -> str:
    """★캡처와 **현재 행 수를 나란히** 둔다.

    Tad 캡처는 GUI 로만 만들 수 있어 재생성에서 살아남지 못한다.
    그래서 캡처는 **s5 시절 화면**인데 본문은 s6 라고 적혀 있었다
    (예: 캡처 `133행` ↔ 현재 `v1_clause_boundary` 7행).
    캡처를 다시 못 찍으니, **지금 값을 옆에 적어 오해를 막는다.**
    """
    import glob as _glob
    try:
        import pandas as _pd
    except Exception:                              # noqa: BLE001
        return ''
    rows_ = []
    for p in sorted(_glob.glob(os.path.join('data', 'exports', 'views', 'v*.parquet'))):
        try:
            rows_.append((os.path.basename(p).replace('.parquet', ''), len(_pd.read_parquet(p))))
        except Exception:                          # noqa: BLE001
            rows_.append((os.path.basename(p), None))
    if not rows_:
        return ''
    body = ''.join(f'<tr><td><code>{k}</code></td>'
                   f'<td class="n">{format(v, ",") if v is not None else "읽지 못함"}</td></tr>'
                   for k, v in rows_)
    #: ★자산 파일에도 이미 낡음 경고가 있다(「이 캡처는 … 판이다」). 그 문장을 되풀이하지 않고
    #:   **거기 없는 것 — 지금 행 수 전량**만 덧붙인다.
    return (f'<div class="box"><b>지금({TAG}) 실제 행 수</b> — 아래 캡처의 행 수와 다르면 캡처가 옛것이다.'
            f'<table><tr><th>뷰</th><th class="n">현재 행</th></tr>{body}</table>'
            f'원본은 <code>data/exports/views/*.parquet</code> — Tad 로 열면 같은 화면이 나온다.</div>')


#: ★H 절(Tad 행 단위 캡처)은 GUI 로만 만들 수 있어 재생성에서 살아남지 못했다.
#:   자산 파일로 떼어 두고 여기서 이어 붙인다 — 없으면 없다고 적는다(조용히 빠지지 않게).
_tad = os.path.join('docs', 'handoff', '_viz_tad_section.html')
if os.path.exists(_tad):
    _asset = open(_tad, encoding='utf-8').read()
    #: ★자산은 s6 판에 쓰였고 「집계 그림·표는 재생성된 s6 기준」이라고 못박고 있다.
    #:   s7 문서에 그대로 실었더니 바로 위 배너는 「지금(s7) 실제 행 수」인데
    #:   아래 문장은 「집계는 s6」이라 **한 문서 안에서 서로 다른 판을 말했다.**
    #:   자산은 공용이므로 파일을 고치지 않고 **싣는 쪽에서** 스키마를 맞춘다.
    _asset = _asset.replace('<b>재생성된 s6</b>(11:45 완료)', f'<b>재생성된 {TAG}</b>')
    #: 캡처 바로 앞에 「지금 값」 표를 끼운다. 첫 h2 뒤가 그 자리다.
    _b = _tad_banner()
    if _b:
        _i = _asset.find('</h2>')
        _asset = (_asset[:_i + 5] + _b + _asset[_i + 5:]) if _i >= 0 else (_b + _asset)
    tail = tail.replace('</main>', _asset + '</main>')
else:
    tail = tail.replace('</main>',
        '<h2>H. 행 단위 캡처</h2><div class="box w">'
        '<b>캡처 자산이 없다</b> — <code>docs/handoff/_viz_tad_section.html</code> 이 없어 '
        '이 절을 싣지 못했다. 빠진 것을 조용히 넘기지 않으려고 이 문단을 남긴다.</div></main>')

out = 'docs/handoff/preprocess_viz.html'
open(out, 'w', encoding='utf-8').write(head + '\n'.join(parts) + tail)
print('작성 완료: %s  %d KB · 그림 %d장 + DB표' % (out, os.path.getsize(out) // 1024, len(figs)))
