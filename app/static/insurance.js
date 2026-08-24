/* 올바른 보험비서 — 사람 화면.
 *
 * ★이 화면은 **판정하지 않는다.** 서버가 돌려준 것을 그대로 그린다.
 *   화면에서 조건을 하나라도 해석하면 규칙엔진과 두 벌이 되고 반드시 어긋난다.
 *
 * ★기권을 실패처럼 그리지 않는다.
 *   `verdict="needs_expert"` 는 정상 결과이고 HTTP 200 이다.
 *   빨간 오류 화면으로 그리면 사용자가 "고장났다"로 읽는다.
 *
 * ★경고를 접지 않는다. `warnings[]` 는 항상 펼쳐서 보인다.
 */
'use strict';

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

//: 서버 enum → 사람 말. ★네 단계를 셋으로 줄이지 않는다.
const VERDICT_KO = {
  likely_covered: ['보장 가능', 'ok'],
  needs_documents: ['조건부 확인 필요', 'warn'],
  unlikely: ['면책 가능성', 'danger'],
  needs_expert: ['전문가 확인 필요', 'warn'],
};

// 개인 진료기록의 KCD 상병코드 형식. 약관 분류 범위(C30~C39)는 입력값이 아니다.
// 소수점 세분류(S72.0, N39.3)는 정상 입력으로 받는다.
const SINGLE_KCD_CODE = /^[A-Z]\d{2}(?:\.\d{1,2})?$/i;

async function api(path, opts) {
  const res = await fetch(path, opts);
  let body = null;
  try { body = await res.json(); } catch { /* 본문 없음 */ }
  return { status: res.status, body };
}

/* ── 반응형 화면 흐름 · 세션 요약 ─────────────────────────────── */

const sessionToken = (() => {
  const raw = globalThis.crypto?.randomUUID?.().replaceAll('-', '')
    || Math.random().toString(36).slice(2);
  return `ses_${raw.slice(0, 8)}`;
})();

function updateSessionCard() {
  $('sessionId').textContent = sessionToken;
  $('sessionInsurer').textContent = $('insurer').value.trim() || '미등록';
  $('sessionProduct').textContent = $('productName').value.trim() || '미등록';
  $('sessionDate').textContent = $('enrolled').value.trim() || '미등록';
}

function showChat() {
  updateSessionCard();
  $('appShell').classList.add('show-chat');
  requestAnimationFrame(() => {
    $('chatIn').focus({ preventScroll: true });
    //: ★★**보일 때 다시 잰다.** 숨은 요소는 `scrollWidth`·`clientWidth` 가 0 이라
    //:   넘침 판정이 늘 false 가 된다 — 실측 2026-08-04, 칩 26개인데 페이드가 안 붙었다.
    //:
    //:   ★`requestAnimationFrame` 으로는 **모자랐다.** 이 창은 CSS 전환으로 나타나서
    //:     다음 프레임에도 폭이 아직 옛 값이다(실측: scrollWidth 2069 · clientWidth 582
    //:     인데 판정이 false). 그래서 몇 번 나눠 잰다 — 한 번이라도 제대로 잡히면 된다.
    //:     늦게 재는 것은 해롭지 않고, 안 재는 것이 해롭다.
    markQuickScrollable();
  });
}

function updateRegisterState() {
  const ready = $('consent').checked
    && $('insurer').value.trim()
    && /^\d{8}$/.test($('enrolled').value.trim())
    && $('codes').value.trim();
  $('go').disabled = !ready;
}

/* ── 상품명 자동완성 ───────────────────────────────────────────────
 *
 * ★★**「전체 상품 목록」이 아니다.** 확정된 약관 850건 / 판정대상 1,367건(62.2%)
 *   에서 **검색된 후보**다. 목록에 없다고 그 상품이 없는 것이 아니다 —
 *   아직 「이 파일이 무엇인가」를 확정하지 못한 약관이 있다.
 *
 * ★그래서 **보험사를 고르기 전에는 잠가 둔다.** 서버도 보험사를 필수로 받는다.
 *   전량을 내려주면 「목록에 없으면 미지원」으로 읽힌다.
 *
 * ★★**고른다고 판본이 정해지지 않는다.** 같은 상품명이 여러 판본으로 존재하는 것이
 *   실측 140종·421건(확정분의 절반)이다. 적용 판본은 **가입일**이 정한다.
 *   그래서 후보마다 판본 수를 함께 보이고, 안내 문구로도 말한다.
 */
let _productTimer = null;

function _setProductHint(text, warn) {
  const el = $('productHint');
  if (!el) return;
  el.textContent = text;
  el.style.color = warn ? 'var(--danger, #991b1b)' : '';
}

async function loadProducts() {
  const box = $('productName');
  const list = $('products');
  if (!box || !list) return;
  const insurer = $('insurer').value.trim();

  if (!insurer) {
    box.disabled = true;
    box.placeholder = '보험사를 먼저 고르세요';
    list.replaceChildren();
    _setProductHint('보험사를 고르면 확정된 약관에서 상품명을 찾아 드립니다.');
    return;
  }
  box.disabled = false;
  box.placeholder = '두 글자 이상 입력하면 후보를 찾습니다';

  const q = box.value.trim();
  if (q.length < 2) {
    list.replaceChildren();
    _setProductHint('두 글자 이상 입력하면 후보를 찾습니다. 비워 두어도 판정은 됩니다.');
    return;
  }

  const params = new URLSearchParams({ insurer, q, limit: '10' });
  const enrolled = $('enrolled').value.trim();
  if (/^\d{8}$/.test(enrolled)) params.set('enrolled_on', enrolled);

  const { status, body } = await api(`/v1/catalog/products?${params}`);
  if (status !== 200 || !body) {
    //: ★못 불러온 것을 "그런 상품 없음"으로 그리지 않는다. 다른 사실이다.
    list.replaceChildren();
    _setProductHint(`상품 후보를 불러오지 못했습니다 (HTTP ${status}). 상품명은 비워 두어도 됩니다.`, true);
    return;
  }

  const items = body.items || [];
  list.replaceChildren();
  for (const it of items) {
    const opt = document.createElement('option');
    opt.value = it.product_name;
    //: ★판본 수를 **함께** 보인다 — 「골랐으니 내 약관이 정해졌다」로 믿게 두지 않는다.
    opt.label = `판본 ${it.versions}개 · ${it.sale_start_range[0]}~${it.sale_start_range[1]}`;
    list.appendChild(opt);
  }
  if (!items.length) {
    _setProductHint(
      `'${q}' 로 찾은 확정 약관이 없습니다. 확정되지 않은 약관은 여기 나오지 않으니, ` +
      '상품명을 비우고 진행하셔도 됩니다.');
    return;
  }
  _setProductHint(
    `후보 ${body.shown}개 표시 (검색 결과 ${body.matched}개 · ${insurer} 확정 약관 ` +
    `${body.confirmed_for_insurer}건). 전체 상품 목록이 아니며, 고르셔도 적용 약관은 가입일이 정합니다.`);
}

function scheduleProductSearch() {
  //: 입력할 때마다 서버를 두드리지 않는다.
  if (_productTimer) clearTimeout(_productTimer);
  _productTimer = setTimeout(loadProducts, 250);
}

/* ── 약관에 등장한 질병코드 (입력 도우미) ─────────────────────────
 *
 * ★★**「입력 가능한 코드 목록」이 아니다.** 아무 KCD 코드나 넣을 수 있고,
 *   여기 없는 코드도 판정이 정상 처리한다.
 *
 *   실측 2026-08-04 — 흔한 청구 코드가 이 목록에 **없다**
 *   (골절 S72.0 · 위염 K29.7 · 백내장 H25.9). 목록에 든 것은 약관이 콕 집어
 *   말한 코드(정신질환·임신출산·치과·비만·요실금)뿐이다.
 *   그래서 **자기 코드를 못 찾은 사용자가 입력을 포기하는 것**이 이 기능의 주된 위험이고,
 *   패널이 그 사실을 **항상 위에** 적어 둔다.
 *
 * ★면책·예외 라벨은 **보이지 않는다.** 「F04~F99 면책」만 읽으면 F32 가 예외라는
 *   것을 놓쳐 정당한 청구를 포기할 수 있다. 보장 여부는 판정이 근거와 함께 답한다.
 */
let _codeListLoaded = false;
let _codeListQueryTimer = null;

async function loadCodeList() {
  const body = $('codeListBody');
  const summary = $('codeListSummary');
  if (!body) return;
  const q = ($('codeListQuery') || {}).value || '';
  const params = new URLSearchParams({ limit: '60' });
  if (q.trim()) params.set('q', q.trim());

  const { status, body: data } = await api(`/v1/catalog/codes?${params}`);
  if (status !== 200 || !data) {
    //: ★못 불러온 것을 "코드가 없다"로 그리지 않는다. 코드는 직접 입력하면 된다.
    body.replaceChildren();
    if (summary) {
      summary.textContent =
        `목록을 불러오지 못했습니다 (HTTP ${status}). 코드는 직접 입력하시면 됩니다.`;
    }
    return;
  }
  _codeListLoaded = true;
  const items = data.items || [];
  if (summary) {
    //: ★분모를 함께 — 거른 결과가 전량으로 보이면 안 된다.
    summary.textContent =
      `확정 약관 ${data.scanned_policies}건에서 찾은 코드 ${data.total_codes}종 중 ` +
      `${data.shown}종 표시. 목록에 없는 코드도 입력하실 수 있습니다.`;
  }
  body.replaceChildren();
  if (!items.length) {
    const p = document.createElement('p');
    p.className = 'precheck-help';
    p.textContent = '조건에 맞는 코드가 없습니다. 그래도 코드를 직접 입력하시면 판정됩니다.';
    body.appendChild(p);
    return;
  }
  const frag = document.createDocumentFragment();
  for (const it of items) {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'chip';
    b.style.cursor = 'pointer';
    if (it.input_allowed) {
      // 단일 코드만 입력창에 넣는다.
      b.textContent = `${it.code} · ${it.chapter}`;
      b.title = `약관 ${it.policies}건에 등장 · 눌러서 입력`;
      b.addEventListener('click', () => {
        const box = $('codes');
        const cur = box.value.trim();
        box.value = cur ? `${cur}, ${it.code}` : it.code;
        box.dispatchEvent(new Event('input', { bubbles: true }));
      });
    } else {
      // C30~C39는 약관이 묶어서 표기한 범위다. 이를 개인 상병코드로 보내지 않는다.
      b.textContent = `${it.code} · ${it.chapter} · 약관 범위`;
      b.title = '범위는 선택할 수 없습니다. 진단서의 개별 상병코드를 입력하세요.';
      b.setAttribute('aria-disabled', 'true');
      b.addEventListener('click', () => {
        if (summary) {
          summary.textContent =
            `${it.code}는 약관의 분류 범위이므로 판정 입력값이 아닙니다. ` +
            '진료비 세부내역서나 진단서에 적힌 개별 상병코드(예: C34.1)를 입력하세요.';
        }
      });
    }
    frag.appendChild(b);
  }
  body.appendChild(frag);
}

/* ── 챗봇 용어 도우미 ─────────────────────────────────────────────
 *
 * ★★**용어 사전이 아니다.** 뜻은 여기 담지 않고 챗봇이 약관 원문으로 답한다.
 *   여기 쓰는 것은 「이 낱말은 약관에 정의가 있다」는 사실뿐이다.
 *
 * ★칩을 **하드코딩하지 않는다.** 4개가 박혀 있었는데, 약관이 바뀌면 눌렀을 때
 *   못 찾는 칩이 생긴다. 서버 목록은 `scripts/eval/glossary_terms.py` 가
 *   **실제 검색으로 검증한 것만** 담는다.
 *
 * ★목록에 **없는 낱말도 물어볼 수 있다.** 자동완성은 거들 뿐 막지 않는다 —
 *   막으면 사용자가 질문 자체를 포기한다.
 */
async function loadChatTerms() {
  const list = $('chatTerms');
  const chips = $('quickPrompts');
  const wrap = $('quickWrap');
  if (!list || !chips) return;

  const { status, body } = await api('/v1/chat/terms?limit=120');
  if (status !== 200 || !body) {
    //: ★못 불러온 것을 "용어가 없다"로 그리지 않는다. 직접 물어보면 된다.
    markQuickScrollable();
    return;
  }
  const items = body.items || [];

  //: 입력창 자동완성 — 낱말만 넣는다. 문장을 넣으면 그대로 전송돼 의도가 흐려진다.
  list.replaceChildren();
  for (const it of items) {
    const opt = document.createElement('option');
    opt.value = it.term;
    opt.label = `약관 ${it.policies}건에 정의`;
    list.appendChild(opt);
  }

  //: 칩 — 서버가 실제 검색으로 검증한 용어만 널리 쓰이는 순서로 채운다.
  const seen = new Set([...chips.querySelectorAll('button')].map((b) => b.dataset.q));
  const frag = document.createDocumentFragment();
  for (const it of items.slice(0, 24)) {
    const q = `${it.term} 뜻`;
    if (seen.has(q) || seen.has(it.term)) continue;
    seen.add(q);
    const b = document.createElement('button');
    b.className = 'chip-btn';
    b.type = 'button';
    b.dataset.q = q;
    b.textContent = q;
    b.title = `약관 ${it.policies}건에 정의가 있습니다`;
    b.addEventListener('click', () => sendChat(q));
    frag.appendChild(b);
  }
  chips.appendChild(frag);
  //: ★붙인 직후엔 아직 레이아웃 전일 수 있다. 지금과 다음 프레임 둘 다 잰다.
  markQuickScrollable();
  if (wrap) {
    wrap.title =
      `약관에 정의가 있는 용어 ${body.total_terms}종에서 골랐습니다. ` +
      '목록에 없는 낱말도 물어보실 수 있습니다.';
  }
}

//: ★★**레이아웃을 재지 않고 「칩 개수」로 판단한다.**
//:
//:   원래 `scrollWidth > clientWidth` 로 넘침을 쟀다. 그런데 이 창은 CSS 전환으로
//:   나타나서 **언제 재도 폭이 옛 값이거나 0** 이었다 — `requestAnimationFrame`,
//:   `ResizeObserver`, 60/200/500ms 타이머를 다 붙여도 판정이 안 걸렸다
//:   (실측 2026-08-04: `scrollWidth 2069 · clientWidth 582` 인데 계속 false).
//:
//:   ★재는 시점을 더 찾아 헤매는 대신 **확실히 아는 것**을 쓴다.
//:     칩이 8개를 넘으면 어떤 현실적 폭에서도 한 줄에 안 들어간다.
//:     추측이 아니라 우리가 만든 개수다. 틀릴 여지가 없고 설명도 쉽다.
//:
//:   ★페이드는 「더 있다」는 신호일 뿐이라, 몇 개에서 켜지느냐가 정확할 필요는 없다.
//:     정확해야 하는 것은 **없는데 있다고 말하지 않는 것**이고 그건 지켜진다.
const _QUICK_SCROLL_MIN_CHIPS = 8;

function markQuickScrollable() {
  const wrap = $('quickWrap');
  const chips = $('quickPrompts');
  if (!wrap || !chips) return;
  const n = chips.querySelectorAll('.chip-btn').length;
  wrap.classList.toggle('is-scrollable', n > _QUICK_SCROLL_MIN_CHIPS);
}


/* ── 컷① 지원범위 ─────────────────────────────────────────────── */

async function loadScope() {
  const { status, body } = await api('/v1/support-manifest');
  const el = $('scopeBody');
  if (status !== 200 || !body) {
    //: ★못 불러온 것을 "지원 안 함"으로 그리지 않는다. 다른 사실이다.
    el.innerHTML = `<div class="banner danger">지원 범위를 불러오지 못했습니다 (HTTP ${status}).
      화면 문제이지 보장 여부와는 무관합니다.</div>`;
    return;
  }

  const insurers = Object.keys(body.insurers || {});
  $('insurers').innerHTML = insurers.map((n) => `<option value="${esc(n)}">`).join('');

  //: ★판정 가능 약관이 0건이면 그것을 **첫 화면에** 쓴다.
  const total = body.total_policy_versions || 0;
  const notes = (body.notes || []).map((n) => {
    const warn = n.startsWith('⚠');
    return `<div class="banner ${warn ? 'warn' : ''}" ${warn ? '' : 'style="background:transparent;border:0;padding:2px 0"'}>${esc(n)}</div>`;
  }).join('');

  el.innerHTML = `
    <div class="${total === 0 ? 'banner danger' : ''}">
      <strong>판정 가능 약관 ${total.toLocaleString()}건</strong>
      · 보험사 ${insurers.length}곳
      ${total === 0 ? '<br>수집은 끝났지만 사람이 문서를 확정하는 절차가 남아 지금은 판정할 수 없습니다.' : ''}
    </div>
    ${insurers.map((n) => `<span class="chip">${esc(n)} ${body.insurers[n].versions}</span>`).join('')}
    <div style="margin-top:10px">${notes}</div>
    <div class="small muted" style="margin-top:8px">
      규칙엔진 ${esc(body.rule_engine_version || '')} ·
      확정 문서만 사용: ${body.require_confirmed_documents ? '예' : '<strong>아니오(시연 모드)</strong>'}
    </div>`;
}

/* ── 컷②~⑦ 판정 ──────────────────────────────────────────────── */

function renderCitations(cites) {
  if (!cites || !cites.length) return '';
  return `<h2 style="margin-top:18px">근거 조항</h2>` + cites.map((c) => `
    <div class="cite">
      <div><strong>${esc(c.title || c.qualified_no)}${c.scope ? ` · ${esc(c.scope)}` : ''}</strong></div>
      <div class="quote">${esc(c.quote || '')}</div>
      <div class="loc">${esc(c.clause_id)} · ${esc(c.section || '')} p${c.page_from}${c.page_to && c.page_to !== c.page_from ? '–' + c.page_to : ''}</div>
    </div>`).join('');
}

function renderResult(status, b) {
  const out = $('result');

  if (status === 422) {
    out.innerHTML = `<div class="card"><div class="banner warn">입력을 확인해 주세요 —
      ${esc(b?.message || b?.detail || '형식이 올바르지 않습니다.')}</div></div>`;
    return;
  }
  if (status === 503) {
    //: ★이때만 "우리 잘못"이다. 기권과 섞으면 안 된다.
    out.innerHTML = `<div class="card"><div class="banner danger">
      일시적으로 조회할 수 없습니다 — ${esc(b?.message || b?.detail || '')}<br>
      <span class="small">보장 여부를 판단한 것이 아닙니다. 잠시 후 다시 시도해 주세요.</span></div></div>`;
    return;
  }
  if (status !== 200 || !b) {
    out.innerHTML = `<div class="card"><div class="banner danger">예상하지 못한 응답입니다 (HTTP ${status}).</div></div>`;
    return;
  }

  const [label, tone] = VERDICT_KO[b.verdict] || [b.verdict, 'warn'];
  const p = b.applied_policy;

  out.innerHTML = `
    <section class="card">
      <div class="verdict">${esc(label)}</div>
      <div class="banner ${tone}">${esc(b.message || '')}</div>

      ${b.abstained ? `<p class="small muted">
        ★근거 조항을 대지 못해 판정하지 않았습니다. <strong>오류가 아닙니다</strong> —
        추측으로 "보장됩니다"라고 말하지 않기 위한 정상 동작입니다.</p>` : ''}

      ${(b.warnings || []).length ? `<div class="banner warn">
        ${b.warnings.map((w) => `<div>⚠ ${esc(w)}</div>`).join('')}</div>` : ''}

      ${p ? `<h2 style="margin-top:16px">어느 약관으로 봤나</h2>
      <dl class="kv">
        <dt>보험사</dt><dd>${esc(p.insurer)}</dd>
        <dt>상품</dt><dd>${esc(p.product_name)}</dd>
        <dt>판매기간</dt><dd>${esc(p.sale_start)} ~ ${esc(p.sale_end || '')}</dd>
        <dt>세대</dt><dd>${esc(p.generation_label || p.generation || '미확정')}</dd>
        <dt>날짜 신뢰도</dt><dd>${esc(p.date_confidence)}</dd>
        <dt>추출 상태</dt><dd>${esc(p.parse_status)}</dd>
      </dl>` : ''}

      ${(b.candidates || []).length ? `<h2 style="margin-top:16px">후보 약관</h2>
        <div class="small muted">어느 것인지 특정하지 못했습니다.
          ★<strong>고르지 않으면 판정하지 않습니다</strong> — 아무거나 골라 답하면
          다른 약관의 조항을 근거로 대게 됩니다.</div>
        <div style="margin-top:8px">
        ${b.candidates.map((c, i) => `<button class="chip-btn cand" data-i="${i}"
            data-name="${esc(c.product_name)}">${esc(c.product_name)} ·
            ${esc(c.sale_start)}${c.generation_label ? ' · ' + esc(c.generation_label) : ''}</button>`).join('')}
        </div>` : ''}

      ${(b.per_code || []).length ? `<h2 style="margin-top:16px">질병기호별</h2>
        ${b.per_code.map((a) => {
          const [l] = VERDICT_KO[a.verdict] || [a.verdict];
          return `<div class="cite"><strong>${esc(a.code)}</strong> — ${esc(l)}
            <div class="small muted">${esc(a.note || a.reason_code || '')}</div></div>`;
        }).join('')}` : ''}

      ${renderCitations(b.citations)}

      <div class="small muted" style="margin-top:16px">
        ${/*
          ★빈 값이면 라벨째 뺀다. 앞서 `추출 ` 만 덩그러니 찍혔다(실측 2026-08-04).
            `extractor` 는 PG 색인 경로에서 **원래 비어 있다** — PG 에 그 값이 없어
            `pg_clause_store.stats()` 가 안 돌려준다. 없는 것을 채우지 않는 것은 맞고
            (CLAUDE.md §1), 화면이 라벨만 보여 주는 것이 틀렸다.
        */''}
        ${[
          b.rule_engine_version ? `규칙엔진 ${esc(b.rule_engine_version)}` : '',
          b.extractor ? `추출 ${esc(b.extractor)}` : '',
          b.trace_id ? `추적 ${esc(b.trace_id)}` : '',
        ].filter(Boolean).join(' · ')}
      </div>
    </section>`;
}

/* ── 컷③ 되묻기 ──────────────────────────────────────────────────
 * ★후보를 고르면 **그 상품명을 실어 다시 판정한다.**
 *   화면이 후보 중 하나를 임의로 고르지 않는다 — 고르는 것은 사용자다.
 *   임의로 고르면 다른 약관의 조항을 근거로 대게 된다.
 */
function bindCandidates() {
  document.querySelectorAll('.cand').forEach((b) =>
    b.addEventListener('click', () => runPrecheck(b.dataset.name)));
}

function renderPrecheckChat(body) {
  const [label] = VERDICT_KO[body.verdict] || [body.verdict];
  const assessments = (body.per_code || []).map((a) => `
    <div class="precheck-chat-line"><strong>${esc(a.code)}</strong> ·
      ${esc((VERDICT_KO[a.verdict] || [a.verdict])[0])}
      ${a.note ? `<br><span class="small">${esc(a.note)}</span>` : ''}
    </div>`).join('');
  const warnings = (body.warnings || []).map((w) =>
    `<div class="small precheck-chat-warning">⚠ ${esc(w)}</div>`).join('');
  const citationCount = (body.citations || []).length;

  return `<strong>${esc(label)}</strong><br>
    <div style="margin-top:6px">${esc(body.message || '판정 결과를 확인했습니다.')}</div>
    ${assessments ? `<div class="precheck-chat-details"><strong>질병기호별 판단</strong>${assessments}</div>` : ''}
    ${warnings}
    <div class="small muted" style="margin-top:8px">
      약관 원문 근거 ${citationCount}건을 아래 상세 결과에 표시했습니다.
    </div>
    <button class="chat-detail-link" type="button" data-scroll-result>상세 근거 보기 ↓</button>`;
}

function bindResultLink(message) {
  const button = message?.querySelector('[data-scroll-result]');
  if (!button) return;
  button.addEventListener('click', () => {
    $('detailTitle').scrollIntoView({ behavior: 'smooth', block: 'start' });
    $('result').focus({ preventScroll: true });
  });
}

function productLineFromChat(text) {
  const normalized = String(text || '').toLowerCase().replace(/[^0-9가-힣a-z]/g, '');
  if (normalized.includes('유병력자실손')) return 'simplified_issue';
  if (normalized.includes('노후실손')) return 'senior';
  if (normalized.includes('일반실손')) return 'standard';
  return null;
}

function sameProductLine(candidate, line) {
  const value = String(candidate?.product_line || candidate?.product_name || '')
    .toLowerCase().replace(/[^0-9가-힣a-z]/g, '');
  return value.includes(line);
}

async function runPrecheck(productName, options = {}) {
  const codes = $('codes').value.split(',').map((s) => s.trim().toUpperCase()).filter(Boolean);
  const selectedProduct = productName === undefined
    ? $('productName').value.trim()
    : productName;

  // 입력 도우미뿐 아니라 붙여넣기·직접 입력 경로도 막는다. 범위를 임의로
  // 여러 코드로 펼치면 실제 환자의 진단코드를 추측하게 되므로 자동 확장하지 않는다.
  const invalidCode = codes.find((code) => !SINGLE_KCD_CODE.test(code));
  if (invalidCode) {
    const detail = /[~∼～-]/.test(invalidCode)
      ? `${invalidCode}는 개별 질병기호가 아니라 약관의 코드 범위입니다. ` +
        '진료비 세부내역서나 진단서에 적힌 단일 코드(예: C34.1)를 입력하세요.'
      : `${invalidCode}의 형식이 올바르지 않습니다. 단일 질병기호(예: F32, S72.0)를 입력하세요.`;
    $('status').textContent = '';
    updateRegisterState();
    renderResult(422, { detail });
    if (!options.silentChat) bubble('bot', detail);
    showChat();
    return { status: 422, body: { detail } };
  }
  $('status').textContent = '판정 중…';
  $('go').disabled = true;

  const { status, body } = await api('/v1/prechecks', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      insurer: $('insurer').value.trim(),
      enrolled_on: $('enrolled').value.trim(),
      kcd_codes: codes,
      //: ★사용자가 후보를 고른 경우에만 실린다. 화면이 지어내지 않는다.
      ...(selectedProduct ? { product_name: selectedProduct } : {}),
    }),
  });

  $('status').textContent = '';
  updateRegisterState();
  renderResult(status, body);
  bindCandidates();
  if (codes.length) loadCohorts(codes[0]);

  if (status === 200 && body && !options.silentChat) {
    const message = bubble('bot', renderPrecheckChat(body));
    bindResultLink(message);
  } else if (!options.silentChat) {
    bubble('bot', '입력하신 보험정보를 확인하지 못했습니다. 입력값을 다시 확인해주세요.');
  }
  showChat();
  return { status, body };
}

async function runPrecheckForChatProductLine(line) {
  // 먼저 상품명을 비워 후보 목록을 받아 product_line으로 정확히 고른다.
  const initial = await runPrecheck('', { fromChat: true, silentChat: true });
  const candidate = (initial.body?.candidates || []).find((item) =>
    sameProductLine(item, line));

  if (candidate?.product_name) {
    return runPrecheck(candidate.product_name, { fromChat: true });
  }
  return initial;
}

/* ── 컷⑧ 코호트 — ★실제와 합성을 각각 제 구역에만 그린다 ────────── */

async function loadCohort(path, elId, isDemo) {
  const el = $(elId);
  const { status, body } = await api(path);
  if (status !== 200 || !body) {
    el.innerHTML += `<div class="banner danger small">조회하지 못했습니다 (HTTP ${status}).</div>`;
    return;
  }
  el.innerHTML = `
    <h2>${isDemo ? 'DEMO · 합성 데이터' : '실제 검증 데이터'}</h2>
    <div class="banner ${isDemo ? 'warn' : (body.n ? 'ok' : '')}"
         ${!isDemo && !body.n ? 'style="background:transparent;border:1px solid var(--line)"' : ''}>
      ${esc(body.headline || '')}
    </div>
    <dl class="kv">
      <dt>사례 수</dt><dd>${body.n}</dd>
      <dt>지급</dt><dd>${body.approved_n}</dd>
      <dt>부지급</dt><dd>${body.denied_n}</dd>
      <dt>최소 표본</dt><dd>${body.min_sample} ${body.min_sample_met ? '충족' : '미달'}</dd>
    </dl>
    ${body.approval_rate != null ? `<p class="small">관측 비율 ${(body.approval_rate * 100).toFixed(1)}%
      <strong>(95% 구간 ${(body.approval_ci[0] * 100).toFixed(0)}~${(body.approval_ci[1] * 100).toFixed(0)}%)</strong></p>`
      : `<p class="small muted">표본이 적어 비율을 계산하지 않았습니다.</p>`}
    ${(body.warnings || []).map((w) => `<div class="small muted">⚠ ${esc(w)}</div>`).join('')}
    <div class="small muted" style="margin-top:8px">data_source: <code>${esc(body.data_source)}</code></div>`;
}

function loadCohorts(code) {
  const q = `?code=${encodeURIComponent(code)}`;
  //: ★엔드포인트가 다르다. 한 응답을 나눠 그리지 않는다.
  loadCohort('/v1/cohorts' + q, 'cohortReal', false);
  loadCohort('/v1/demo/cohorts' + q, 'cohortDemo', true);
}

/* ── 용어 챗봇 ────────────────────────────────────────────────── */

/* ★대화창이 판정하지 않는다.
 *   서버가 `intent="precheck"` 를 주면 **답을 만들지 않고** 판정 양식으로 올려보낸다.
 *   화면에서 "아마 보장될 거예요" 같은 말을 한 마디라도 만들면
 *   약관버전 확정·인용검증·4단 판정을 통째로 우회한 답이 된다.
 */

function bubble(cls, html) {
  const d = document.createElement('div');
  d.className = 'msg ' + cls;
  d.innerHTML = html;
  const log = $('chatLog');
  log.appendChild(d);
  const scroll = $('chatScroll');
  scroll.scrollTop = scroll.scrollHeight;
  return d;
}

async function sendChat(text) {
  const msg = (text ?? $('chatIn').value).trim();
  if (!msg) return;
  $('chatIn').value = '';
  bubble('me', esc(msg));

  const productLine = productLineFromChat(msg);
  if (productLine) {
    bubble('bot', `${esc(msg)}을(를) 상품 유형으로 인식했습니다. 해당 후보를 확인하는 중입니다…`);
    await runPrecheckForChatProductLine(productLine);
    return;
  }

  const thinking = bubble('bot muted', '약관에서 찾는 중…');

  const { status, body } = await api('/v1/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message: msg }),
  });
  thinking.remove();

  if (status === 502 || status === 503) {
    const reason = body?.message || body?.detail || '모델 또는 용어 색인이 준비되지 않았습니다.';
    bubble('bot', `<span style="color:var(--danger)">AI 설명 서비스를 사용할 수 없습니다 — ${esc(reason)}</span>`);
    return;
  }
  if (status !== 200 || !body) {
    bubble('bot', `<span style="color:var(--danger)">응답을 받지 못했습니다 (HTTP ${status}).</span>`);
    return;
  }

  let html = esc(body.message).replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');

  if (body.llm?.used) {
    html += `<div class="small muted" style="margin-top:8px">AI 설명 · ${esc(body.llm.provider)} · ${esc(body.llm.model)}</div>`;
  }

  if (body.found && body.quotes.length) {
    html += `<div class="small muted" style="margin-top:8px">정의 구절 ${body.total_passages}개 · 보험사 ${body.insurers.length}곳</div>`;
    html += body.quotes.map((q) => `
      <div class="cite">
        <div class="small muted">${esc(q.insurer)} · ${esc(q.title)}${q.kind === 'appendix' ? ' (붙임 정의표)' : ''}</div>
        <div class="quote">${esc(q.quote)}</div>
        <div class="loc">${esc(q.locator)}</div>
      </div>`).join('');
  }

  //: ★경고를 접지 않는다. 특히 "보장 여부는 판정하지 않습니다".
  if (body.warnings.length) {
    html += body.warnings.map((w) => `<div class="small muted" style="margin-top:6px">⚠ ${esc(w)}</div>`).join('');
  }
  bubble('bot', html);

  //: ★보장 질문이면 판정 양식으로 **올려보낸다.** 여기서 답하지 않는다.
  if (body.next_action === 'precheck_form') {
    document.getElementById('insurer').scrollIntoView({ behavior: 'smooth', block: 'center' });
    document.getElementById('insurer').focus();
  }
}

/* ── 컷⑨ 증빙 제출 ────────────────────────────────────────────── */

/* ★제출 결과를 "반영되었습니다"로 그리지 않는다.
 *   서버는 `verification="unverified"` 로 고정해 저장하고, 검증 전까지
 *   통계에 넣지 않는다. 화면이 그보다 강하게 말하면 거짓말이 된다.
 */
async function submitObservation() {
  const out = $('obOut');
  const insurer = $('obInsurer').value.trim();
  if (!insurer) {
    out.innerHTML = '<div class="banner warn small">보험사를 적어 주세요.</div>';
    return;
  }
  $('obGo').disabled = true;
  const { status, body } = await api('/v1/observations', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      client_ref: 'web-ui',
      insurer,
      enrolled_on: $('enrolled').value.trim(),
      kcd_codes: $('obCodes').value.split(',').map((s) => s.trim()).filter(Boolean),
      outcome: $('obOutcome').value,
      outcome_reason: $('obReason').value.trim(),
    }),
  });
  $('obGo').disabled = false;

  if (status === 503) {
    out.innerHTML = `<div class="banner danger small">저장하지 못했습니다 — ${esc(body?.message || body?.detail || '')}</div>`;
    return;
  }
  if (status !== 202 || !body) {
    out.innerHTML = `<div class="banner danger small">제출하지 못했습니다 (HTTP ${status}).</div>`;
    return;
  }
  out.innerHTML = `
    <div class="banner ok small">${esc(body.note || '')}</div>
    <div class="small muted">
      검증 상태 <code>${esc(body.verification)}</code>
      ${body.duplicate ? ' · 이미 접수된 보고입니다(중복으로 쌓지 않았습니다)' : ''}
    </div>`;
}

/* ── 시작 ─────────────────────────────────────────────────────── */

$('insuranceForm').addEventListener('submit', (e) => {
  e.preventDefault();
  if (!e.currentTarget.reportValidity()) return;
  runPrecheck();
});
['insurer', 'productName', 'enrolled', 'codes'].forEach((id) => {
  $(id).addEventListener('input', () => {
    updateRegisterState();
    updateSessionCard();
    //: 보험사·가입일이 바뀌면 후보도 바뀐다(가입일이 판본을 가른다).
    if (id === 'insurer' || id === 'productName' || id === 'enrolled') scheduleProductSearch();
  });
});
$('consent').addEventListener('change', updateRegisterState);
$('skipBtn').addEventListener('click', showChat);
$('mobileBack').addEventListener('click', () => $('appShell').classList.remove('show-chat'));
$('obGo').addEventListener('click', submitObservation);
$('chatGo').addEventListener('click', () => sendChat());
$('chatIn').addEventListener('keydown', (e) => { if (e.key === 'Enter') sendChat(); });
document.querySelectorAll('.chip-btn').forEach((b) =>
  b.addEventListener('click', () => sendChat(b.dataset.q)));
$('codeListOpen').addEventListener('click', () => {
  const panel = $('codeListPanel');
  panel.hidden = !panel.hidden;
  if (!panel.hidden && !_codeListLoaded) loadCodeList();
});
$('codeListClose').addEventListener('click', () => { $('codeListPanel').hidden = true; });
$('codeListQuery').addEventListener('input', () => {
  if (_codeListQueryTimer) clearTimeout(_codeListQueryTimer);
  _codeListQueryTimer = setTimeout(loadCodeList, 250);
});

window.addEventListener('resize', markQuickScrollable);

updateSessionCard();
updateRegisterState();
loadProducts();
loadChatTerms();
loadScope();
