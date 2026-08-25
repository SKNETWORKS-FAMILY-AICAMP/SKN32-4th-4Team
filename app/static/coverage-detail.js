/* 보장 확인 상세 — 독립 대시보드 페이지.
 *
 * ★이 화면도 판정하지 않는다. `insurance.js`가 sessionStorage에 남긴 마지막
 *   판정 입력·결과(`lastPrecheckHandoff`)를 그대로 그린다. 후보를 다시 고르는
 *   경우에만 같은 입력값으로 서버에 다시 물어본다(화면이 임의로 고르지 않는다).
 */
'use strict';

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

const VERDICT_KO = {
  likely_covered: ['보장 가능', 'ok'],
  needs_documents: ['조건부 확인 필요', 'warn'],
  unlikely: ['면책 가능성', 'danger'],
  needs_expert: ['전문가 확인 필요', 'warn'],
};

const HANDOFF_KEY = 'lastPrecheckHandoff';

async function api(path, opts) {
  const res = await fetch(path, opts);
  let body = null;
  try { body = await res.json(); } catch { /* 본문 없음 */ }
  return { status: res.status, body };
}

function loadHandoff() {
  try {
    const raw = sessionStorage.getItem(HANDOFF_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

function saveHandoff(handoff) {
  try { sessionStorage.setItem(HANDOFF_KEY, JSON.stringify(handoff)); } catch { /* 무시 */ }
}

/* ── 판정 결과 상세 ───────────────────────────────────────────── */

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
        근거 조항을 대지 못해 판정하지 않았습니다. <strong>오류가 아닙니다</strong> —
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
          <strong>고르지 않으면 판정하지 않습니다</strong> — 아무거나 골라 답하면
          다른 약관의 조항을 근거로 대게 됩니다.</div>
        <div style="margin-top:8px">
        ${b.candidates.map((c, i) => `<button class="chip cand" data-i="${i}"
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
        ${[
          b.rule_engine_version ? `규칙엔진 ${esc(b.rule_engine_version)}` : '',
          b.extractor ? `추출 ${esc(b.extractor)}` : '',
          b.trace_id ? `추적 ${esc(b.trace_id)}` : '',
        ].filter(Boolean).join(' · ')}
      </div>
    </section>`;
}

function bindCandidates(handoff) {
  document.querySelectorAll('.cand').forEach((btn) =>
    btn.addEventListener('click', () => rerunPrecheck(handoff, btn.dataset.name)));
}

/* ★후보를 고르면 그 상품명을 실어 **같은 입력값으로** 다시 판정한다.
 *   이 페이지는 채팅 화면과 분리돼 있어 원래 입력 폼에 접근할 수 없으므로,
 *   handoff에 저장해 둔 보험사·가입일·질병코드를 그대로 재사용한다.
 */
async function rerunPrecheck(handoff, productName) {
  const { status, body } = await api('/v1/prechecks', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      insurer: handoff.insurer,
      enrolled_on: handoff.enrolledOn,
      kcd_codes: handoff.codes,
      ...(productName ? { product_name: productName } : {}),
    }),
  });
  const nextHandoff = { ...handoff, status, body, savedAt: Date.now() };
  saveHandoff(nextHandoff);
  renderResult(status, body);
  bindCandidates(nextHandoff);
  if (handoff.codes?.length) loadCohorts(handoff.codes[0]);
}

/* ── 코호트 — 실제와 합성을 각각 제 구역에만 그린다 ────────────── */

async function loadCohort(path, elId, isDemo) {
  const el = $(elId);
  const { status, body } = await api(path);
  if (status !== 200 || !body) {
    el.innerHTML += `<div class="banner danger small">조회하지 못했습니다 (HTTP ${status}).</div>`;
    return;
  }
  el.innerHTML = `
    <h2>${isDemo ? 'DEMO · 합성 데이터' : '실제 검증 데이터'}</h2>
    <div class="banner ${isDemo ? 'warn' : (body.n ? 'ok' : '')}">
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
  $('cohortWrap').hidden = false;
  loadCohort('/v1/cohorts' + q, 'cohortReal', false);
  loadCohort('/v1/demo/cohorts' + q, 'cohortDemo', true);
}

/* ── 실제 청구 결과 알려주기 ──────────────────────────────────── */

async function submitObservation(handoff) {
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
      enrolled_on: handoff?.enrolledOn || '',
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

async function loadInsurerList() {
  const { status, body } = await api('/v1/support-manifest');
  if (status !== 200 || !body) return;
  const insurers = Object.keys(body.insurers || {});
  $('insurers').innerHTML = insurers.map((n) => `<option value="${esc(n)}">`).join('');
}

/* ── 시작 ─────────────────────────────────────────────────────── */

const handoff = loadHandoff();

if (!handoff) {
  $('emptyHint').hidden = false;
  $('resultWrap').hidden = true;
} else {
  renderResult(handoff.status, handoff.body);
  bindCandidates(handoff);
  if (handoff.codes?.length) loadCohorts(handoff.codes[0]);
  if (handoff.insurer) $('obInsurer').value = handoff.insurer;
  if (handoff.codes?.length) $('obCodes').value = handoff.codes.join(', ');
}

$('obGo').addEventListener('click', () => submitObservation(handoff));
loadInsurerList();
