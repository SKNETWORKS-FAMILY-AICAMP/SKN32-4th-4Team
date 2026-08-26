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

//: ★Django 전달 계층이 심는 쿠키. 없으면 빈 문자열이다.
//:   ★★**쿠키는 포트로 구분되지 않는다**(실측 2026-08-26) — Django(:8098)가 심은
//:     `csrftoken` 은 같은 호스트의 FastAPI(:8090) 직결에도 함께 실려 간다.
//:     그래도 문제가 없다: FastAPI 는 이 헤더를 **읽지 않는다.**
//:     즉 「direct 면 안 붙는다」가 아니라 **「붙어도 무해하다」**가 정확한 이유다.
//:     어느 쪽이든 모드를 분기하지 않고 같은 코드가 돈다.
function csrfToken() {
  const hit = document.cookie.split('; ').find((c) => c.startsWith('csrftoken='));
  return hit ? decodeURIComponent(hit.slice('csrftoken='.length)) : '';
}

async function api(path, opts) {
  //: ★★이게 없으면 Django 를 앞에 뒀을 때 **모든 POST 가 403** 이다(실측 2026-08-26).
  //:   Django 의 CSRF 보호는 계획서 D3 대로 전달 계층이 쥔다. 화면은 쿠키로 받은 토큰을
  //:   되돌려주기만 한다 — 여기서 무엇을 판단하지 않는다.
  //:   GET·HEAD 는 Django 가 검사하지 않으므로 건드리지 않는다.
  const method = (opts?.method || 'GET').toUpperCase();
  let next = opts;
  if (method !== 'GET' && method !== 'HEAD') {
    const token = csrfToken();
    if (token) {
      //: ★`opts.headers` 를 덮어쓰지 않는다 — `Idempotency-Key` 같은 도메인 헤더가 있다.
      next = { ...opts, headers: { ...(opts?.headers || {}), 'X-CSRFToken': token } };
    }
  }
  const res = await fetch(path, next);
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

//: ★PostgreSQL 저장 모드는 요청마다 `Idempotency-Key`를 요구한다(서버 최소 8자).
//:   재시도가 아니라 새 논리적 요청마다 새로 만든다 — 호출할 때마다 부른다.
function newIdempotencyKey() {
  const raw = globalThis.crypto?.randomUUID?.().replaceAll('-', '')
    || `${Date.now().toString(36)}${Math.random().toString(36).slice(2)}`;
  return raw.slice(0, 32).padEnd(8, '0');
}

//: ★가장 최근 판정 호출의 trace_id·idempotency key. `/observations` 제출이
//:   "그 판정과 이어진 사례"임을 서버에 밝히는 데 쓴다(사용자가 손으로 입력할
//:   값이 아니다 — 화면이 실제로 부른 요청을 그대로 되짚는다).
let lastPrecheckTraceId = '';
let lastPrecheckIdempotencyKey = '';

/* ★★상단 칩은 **「지금 어떤 보험을 기준으로 답하고 있나」**를 말하는 자리다.
 *   앞서 「미등록 · 미등록 · 미등록」만 떠 있어 무슨 칸인지 알 수 없었다.
 *   - 아무것도 없으면 → 한 문장으로 「아직 등록 안 함」
 *   - 보험사만 있으면 → 보험사
 *   - 상품까지 있으면 → 보험사 · 상품
 *   자세한 값(계약일·세션 ID)은 설명풍선에 넣는다. 세션 ID 는 문의할 때만 쓰는 값이라
 *   평소에 자리를 차지할 이유가 없다.
 */
//: ★`20200101` 을 그대로 보여 주면 읽기 어렵다. **형식만** 바꾸고 값은 그대로 둔다
//:   — 모르는 자리를 채우거나 고치지 않는다(CLAUDE.md §1).
function formatYmd(raw) {
  return /^\d{8}$/.test(raw) ? `${raw.slice(0, 4)}.${raw.slice(4, 6)}.${raw.slice(6)}` : raw;
}

/* ── 선택 목록(콤보박스) ────────────────────────────────────────────
 *
 * ★★브라우저 기본 `<datalist>` 드롭다운은 **위치를 우리가 정할 수 없다.**
 *   실측 2026-08-26: 입력칸이 아니라 화면 위쪽에 떴다. 모양도 못 맞춘다.
 *   그래서 입력칸 바로 아래에 우리가 그린다.
 *
 * ★`<datalist>` 는 **데이터 그릇으로 남겨 둔다.** 목록을 채우는 기존 코드
 *   (`loadProducts` · 보험사 목록)를 고치지 않아도 되고, `MutationObserver` 로
 *   그릇이 바뀌는 것만 지켜보면 된다. 그리는 쪽과 채우는 쪽이 서로를 모른다.
 *
 * ★비어 있어도 연다 — 무엇을 넣어야 할지 모르는 사람에게 목록이 곧 설명이다.
 */
function createCombo(input, source, { onPick, onOpen } = {}) {
  const wrap = input.closest('.combo');
  const list = document.createElement('ul');
  list.className = 'combo-list';
  list.id = input.getAttribute('aria-controls');
  list.setAttribute('role', 'listbox');
  list.hidden = true;
  wrap.appendChild(list);

  let active = -1;

  const options = () => [...source.querySelectorAll('option')]
    .map((o) => ({ value: o.value, label: o.label || '' }));

  function visible() {
    const q = input.value.trim().toLowerCase();
    const all = options();
    //: ★상품 목록은 **서버가 이미 걸러서** 내려 준다. 여기서 또 거르면
    //:   서버가 찾아 준 후보가 화면에서 사라진다. 보험사 목록만 화면에서 거른다.
    if (source.id === 'products' || !q) return all;
    return all.filter((o) => o.value.toLowerCase().includes(q));
  }

  function render() {
    const items = visible();
    list.replaceChildren();
    if (!items.length) {
      const li = document.createElement('li');
      li.className = 'combo-empty';
      li.textContent = input.disabled ? '보험사를 먼저 고르세요' : '맞는 후보가 없습니다';
      list.appendChild(li);
      active = -1;
      return;
    }
    items.forEach((o, i) => {
      const li = document.createElement('li');
      li.className = 'combo-opt' + (i === active ? ' is-active' : '');
      li.id = `${list.id}-opt-${i}`;
      li.setAttribute('role', 'option');
      li.setAttribute('aria-selected', String(i === active));
      li.textContent = o.value;
      if (o.label) {
        const s = document.createElement('small');
        s.textContent = o.label;
        li.appendChild(s);
      }
      //: `mousedown` 을 쓴다 — `click` 은 `blur` 뒤에 와서 목록이 이미 닫혀 있다.
      li.addEventListener('mousedown', (e) => { e.preventDefault(); pick(o.value); });
      list.appendChild(li);
    });
  }

  function open() {
    if (input.disabled) return;
    list.hidden = false;
    input.setAttribute('aria-expanded', 'true');
    render();
    if (onOpen) onOpen();
  }

  function close() {
    list.hidden = true;
    input.setAttribute('aria-expanded', 'false');
    input.removeAttribute('aria-activedescendant');
    active = -1;
  }

  function pick(value) {
    input.value = value;
    close();
    //: 값이 바뀌었으니 기존 배선(`input` 리스너)이 그대로 돌게 한다.
    input.dispatchEvent(new Event('input', { bubbles: true }));
    if (onPick) onPick(value);
  }

  function move(step) {
    const items = visible();
    if (!items.length) return;
    if (list.hidden) open();
    active = (active + step + items.length) % items.length;
    render();
    const el = list.children[active];
    if (el) {
      el.scrollIntoView({ block: 'nearest' });
      input.setAttribute('aria-activedescendant', el.id);
    }
  }

  input.addEventListener('focus', open);
  input.addEventListener('click', open);
  input.addEventListener('input', () => { active = -1; open(); });
  input.addEventListener('blur', () => setTimeout(close, 120));
  input.addEventListener('keydown', (e) => {
    if (e.key === 'ArrowDown') { e.preventDefault(); move(1); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); move(-1); }
    else if (e.key === 'Enter' && !list.hidden && active >= 0) {
      e.preventDefault();
      pick(visible()[active].value);
    } else if (e.key === 'Escape' && !list.hidden) { e.stopPropagation(); close(); }
  });

  //: 목록을 채우는 쪽이 바뀌면 그리는 쪽이 알아서 따라간다.
  new MutationObserver(() => { if (!list.hidden) render(); }).observe(source, { childList: true });

  return { open, close, render };
}

/* ── 날짜 기본값 ────────────────────────────────────────────────────
 *
 * ★★**이건 값을 채우는 일이다.** `CLAUDE.md` §1 은 모르는 값을 지어내지 말라고 한다.
 *   그래서 지키는 선을 정해 둔다 —
 *
 *     ① 예시(placeholder)로 먼저 **보여만 준다.** 저절로 들어가지 않는다.
 *     ② 비운 채로 등록하면 그때 **눈에 보이게 입력칸에 써 넣는다.**
 *        몰래 요청에만 싣지 않는다 — 사용자가 무엇으로 계산됐는지 보게 한다.
 *     ③ 무엇을 근거로 채웠는지 **화면에 남긴다**(`#status` 와 채팅 안내).
 *
 * ★계약일은 특히 조심한다. **가입일이 적용 판본을 가른다.** 판매개시일로 채우면
 *   "이 상품이 처음 팔린 날에 가입했다면" 이라는 가정이다. 그 가정을 말하지 않고
 *   판정하면 §0 을 어긴다.
 */

//: 상품명 → 판매개시일(YYYYMMDD). `loadProducts` 가 채운다.
let _productSaleStart = new Map();

function todayYmd() {
  const d = new Date();
  const p = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}`;
}

//: 지금 고른 상품의 판매개시일. 고른 상품이 목록에 없으면 빈 문자열이다(지어내지 않는다).
function selectedSaleStart() {
  return _productSaleStart.get($('productName').value.trim()) || '';
}

//: ★★예시 자리에는 **날짜만** 둔다. 설명까지 넣었더니 칸 폭을 넘어 잘렸다
//:   (실측 2026-08-26 — 「예) 20130401 — 비우면 이 상품 판…」에서 끊겼다).
//:   잘린 설명은 없는 것만 못하다. 설명은 풍선으로 옮긴다.
//: ★★예시 자리에는 **날짜만** 둔다. 설명까지 넣었더니 칸 폭을 넘어 잘렸다
//:   (실측 2026-08-26 — 「예) 20130401 — 비우면 이 상품 판…」에서 끊겼다).
//:   잘린 설명은 없는 것만 못하다. 설명은 풍선으로 옮긴다.
function syncEnrolledPlaceholder() {
  const start = selectedSaleStart();
  $('enrolled').placeholder = `예) ${start || '20260801'}`;
  $('enrolledTip').setAttribute('data-tip', start
    ? `비워 두면 이 상품의 판매개시일 ${formatYmd(start)} 로 계산합니다.\n실제 가입일과 다르면 적용 약관과 판정이 달라집니다.`
    : '실제 보험을 계약한 날짜입니다.\n상품을 고르면 그 상품의 판매개시일을 기본값으로 씁니다.');
}

function syncIncidentPlaceholder() {
  const today = todayYmd();
  $('incident').placeholder = `예) ${today}`;
  $('incidentTip').setAttribute('data-tip',
    `비워 두면 오늘 ${formatYmd(today)} 로 계산합니다.\n`
    + '진료비 세부내역서에 적힌 실제 진료일을 넣으면 더 정확합니다.');
}

/* 등록을 누른 순간, 비어 있는 날짜를 **보이게** 채운다.
 * 무엇을 채웠는지 문장으로 돌려준다 — 부른 쪽이 그대로 화면에 띄운다. */
function applyDateDefaults() {
  const filled = [];

  const enrolled = $('enrolled');
  if (!enrolled.value.trim()) {
    const start = selectedSaleStart();
    //: ★근거가 없으면 채우지 않는다. 상품을 안 골랐으면 판매개시일을 알 수 없다.
    if (start) {
      enrolled.value = start;
      filled.push(`계약일을 이 상품의 판매개시일 ${formatYmd(start)} 로 넣었습니다`);
    }
  }

  const incident = $('incident');
  if (!incident.value.trim()) {
    incident.value = todayYmd();
    filled.push(`진료(사고)일을 오늘 ${formatYmd(todayYmd())} 로 넣었습니다`);
  }

  if (filled.length) {
    updateRegisterState();
    updateSessionCard();
  }
  return filled;
}

/* ── 서비스 상태 배지 ───────────────────────────────────────────────
 *
 * ★★전에는 초록 점이 화면에 **그냥 박혀** 있었다. 아무것도 확인하지 않으면서
 *   「상담 서비스 이용 가능」이라고 말하는 것은 거짓 신호다 — 서버가 죽어 있어도
 *   초록이었다. 그래서 `GET /api/health/ready` 를 실제로 물어보고 그 답을 보인다.
 *
 * ★못 물어본 경우를 **정상으로 그리지 않는다.** 「확인 불가」는 「정상」과 다른 사실이다.
 */
async function loadServiceStatus() {
  const box = $('serviceStatus');
  const label = $('serviceStatusLabel');
  if (!box) return;

  const { status, body } = await api('/api/health/ready');
  if (status !== 200 || !body) {
    box.classList.remove('is-down');
    box.classList.add('is-unknown');
    label.textContent = '상태 확인 불가';
    box.setAttribute('data-tip',
      `서비스 상태를 확인하지 못했습니다 (HTTP ${status}).\n`
      + '정상이라는 뜻도, 고장이라는 뜻도 아닙니다.');
    return;
  }

  const ready = Boolean(body.ready);
  //: `null` 은 「해당 없음」이다 — 준비 안 됨으로 세지 않는다.
  const down = Object.entries(body.components || {})
    .filter(([, v]) => v === false).map(([k]) => k);

  box.classList.toggle('is-down', !ready);
  box.classList.remove('is-unknown');
  label.textContent = ready ? '상담 서비스 이용 가능' : '지금은 이용할 수 없습니다';
  box.setAttribute('data-tip', [
    ready ? '약관 색인과 저장소가 준비돼 있습니다.' : '준비되지 않은 구성요소가 있습니다.',
    ...(down.length ? [`준비 안 됨 — ${down.join(', ')}`] : []),
    '',
    '서비스가 도는지만 나타냅니다. 판정의 정확성을 보증하지 않습니다.',
  ].join('\n'));
}

function updateSessionCard() {
  const insurer = $('insurer').value.trim();
  const product = $('productName').value.trim();
  const enrolled = $('enrolled').value.trim();

  $('sessionId').textContent = sessionToken;
  $('sessionInsurer').textContent = insurer;
  $('sessionProduct').textContent = product;
  $('sessionDate').textContent = enrolled;

  const chip = $('sessionChip');
  const summary = [insurer, product].filter(Boolean).join(' · ');
  chip.classList.toggle('is-empty', !summary);
  $('sessionSummary').textContent = summary || '아직 등록 안 함';

  //: ★★풍선은 **사람이 읽는 문장**이어야 한다. 앞서 값을 가운뎃점으로 이어 붙이고
  //:   끝에 `세션 ses_b3bfcebe` 까지 달아 놨더니 무슨 말인지 알 수 없었다.
  //:   - 이름과 값을 **줄을 나눠** 적는다(CSS `white-space: pre-line`).
  //:   - 세션 ID 는 「문의번호」라고 부른다. 값 자체는 개발용이지만, 사용자가 문의할 때
  //:     불러 줄 번호이므로 그 쓰임새를 이름으로 밝힌다.
  //:   - 없는 값은 줄째 뺀다. 「미등록」을 채워 늘어놓지 않는다.
  //: `null` 인 줄만 빼고, 빈 문자열은 **문단 사이 빈 줄**로 그대로 남긴다.
  const lines = summary
    ? [
        `보험사 ${insurer}`,
        product ? `상품 ${product}` : null,
        enrolled ? `계약일 ${formatYmd(enrolled)}` : null,
        '',
        '이 약관을 기준으로 답해 드립니다.',
        `문의번호 ${sessionToken}`,
      ]
    : [
        '아직 보험을 등록하지 않았습니다.',
        '왼쪽 「보험정보」 버튼에서 보험사와 계약일을 넣어 주세요.',
        '그 약관을 기준으로 답해 드립니다.',
        '',
        `문의번호 ${sessionToken}`,
      ];
  chip.setAttribute('data-tip', lines.filter((l) => l !== null).join('\n'));
}

/* ── 패널·서랍 여닫기 ──────────────────────────────────────────────
 *
 * ★★여는 버튼(`#sideToggle`·`#detailOpen`)은 **여닫히는 요소 바깥**, 상단바에 있다.
 *   팀 `front` 브랜치는 여는 버튼을 패널 **안**에 뒀는데, 좁은 화면에서 패널이
 *   `width: 0` 으로 접히면 버튼까지 잘려 나가 휴대폰에서 보험정보를 입력할 방법이
 *   없었다(실측 375x812: `elementFromPoint` 가 늘 다른 요소를 잡았다).
 *
 * ★`aria-expanded` 를 함께 돌린다 — 화면만 열리고 보조기술에는 닫힌 채로 남으면
 *   "열렸다"고 말할 수 없다.
 */
/* ★★덮개가 떠 있는 동안 **뒤쪽은 Tab 으로 잡히면 안 된다.**
 *   실측 2026-08-26: 상세 서랍이 열려 있는데 `#chatIn` 이 그대로 포커스를 받았다.
 *   키보드로 쓰면 보이지 않는 곳으로 커서가 사라진다.
 *   `inert` 는 그 요소와 자손을 **클릭·포커스·보조기술에서 통째로** 뺀다.
 * ★넓은 화면에서 왼쪽 패널은 채팅을 **밀어내는 한 칸**이라 덮개가 아니다.
 *   그때는 뒤쪽을 막지 않는다 — 막을 이유가 없는데 막으면 그게 더 불편하다.
 */
function syncInertLayers() {
  const shell = $('appShell');
  const drawerOpen = shell.classList.contains('detail-open');
  const panelOverlays = !shell.classList.contains('panel-closed') && isNarrow();

  //: 서랍이 위다 — 서랍이 열려 있으면 나머지 전부를 막는다.
  $('sidePanel').toggleAttribute('inert', drawerOpen);
  document.querySelector('.chat-main').toggleAttribute('inert', drawerOpen || panelOverlays);
  $('detailDrawer').toggleAttribute('inert', !drawerOpen);
}

function setPanelOpen(open, { focus = true } = {}) {
  $('appShell').classList.toggle('panel-closed', !open);
  $('sideToggle').setAttribute('aria-expanded', String(open));
  syncInertLayers();
  //: ★첫 로드에서는 포커스를 옮기지 않는다 — 휴대폰에서 키보드가 튀어 오른다.
  if (open && focus) requestAnimationFrame(() => $('insurer').focus({ preventScroll: true }));
}

function setDetailOpen(open) {
  const shell = $('appShell');
  //: ★닫을 때 **열기 전에 있던 자리로** 돌려보낸다. 그러지 않으면 포커스가
  //:   문서 맨 앞으로 튕겨 키보드 사용자가 처음부터 다시 훑어야 한다.
  if (open && !shell.classList.contains('detail-open')) {
    _focusBeforeDrawer = document.activeElement;
  }
  shell.classList.toggle('detail-open', open);
  $('detailOpen').setAttribute('aria-expanded', String(open));
  syncInertLayers();
  if (open) {
    requestAnimationFrame(() => $('result').focus({ preventScroll: true }));
  } else if (_focusBeforeDrawer && document.contains(_focusBeforeDrawer)) {
    _focusBeforeDrawer.focus({ preventScroll: true });
    _focusBeforeDrawer = null;
  }
}

let _focusBeforeDrawer = null;

function isPanelOpen() { return !$('appShell').classList.contains('panel-closed'); }

/* ★★좁은 화면에서 패널은 **채팅 위를 덮는 서랍**이다. 그래서 처음부터 열어 두면
 *   패널이 상단바를 가려 토글을 누를 수 없다(실측: `elementFromPoint` 가
 *   패널 안의 `.eyebrow` 를 잡았다 — front 브랜치와 증상만 다르고 결과는 같다).
 *   넓은 화면에서는 패널이 채팅을 밀어내는 한 칸이라 덮는 문제가 없으므로 열어 둔다.
 */
function isNarrow() { return window.matchMedia('(max-width: 860px)').matches; }

/* ── 스크롤바를 굴리는 동안만 보여 준다 ────────────────────────────
 *
 * ★CSS 만으로는 「지금 굴리고 있다」를 알 수 없다. `:hover` 는 마우스를 올렸을 때뿐이라
 *   휠·키보드·터치로 굴릴 때는 표시가 안 뜬다. 그래서 스크롤 이벤트로 클래스를 붙였다 뗀다.
 * ★`passive: true` — 이 처리는 스크롤을 막지 않으므로 브라우저에 그렇다고 알려 준다.
 */
const SCROLL_IDLE_MS = 900;

function markScrollingWhileScrolled(el) {
  if (!el) return;
  let timer = null;
  el.addEventListener('scroll', () => {
    el.classList.add('is-scrolling');
    if (timer) clearTimeout(timer);
    timer = setTimeout(() => el.classList.remove('is-scrolling'), SCROLL_IDLE_MS);
  }, { passive: true });
}
function isDetailOpen() { return $('appShell').classList.contains('detail-open'); }

//: ★빈 화면 안내는 **첫 말풍선이 생기면** 치운다. 남겨 두면 대화 위에 겹쳐 보인다.
function syncEmptyState() {
  const empty = $('emptyState');
  if (empty) empty.hidden = $('chatLog').childElementCount > 0;
}

function showChat() {
  updateSessionCard();
  setPanelOpen(false);
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
  //: ★★날짜는 **비워 둬도 누를 수 있다.** 등록하는 순간 기본값이 채워지기 때문이다
  //:   (`applyDateDefaults`). 단 계약일 기본값은 **상품을 골라야** 생기므로,
  //:   상품도 계약일도 없으면 여전히 막는다 — 없는 값을 지어내지 않기 위해서다.
  const ymd = (v) => /^\d{8}$/.test(v);
  const enrolled = $('enrolled').value.trim();
  const enrolledOk = ymd(enrolled) || Boolean(selectedSaleStart());
  const incidentRaw = $('incident').value.trim();
  const incidentOk = ymd(incidentRaw) || incidentRaw === '';
  const ready = $('consent').checked
    && $('insurer').value.trim()
    && enrolledOk
    && incidentOk
    && $('codes').value.trim();
  $('go').disabled = !ready;
}

/* ── 상품명 자동완성 ───────────────────────────────────────────────
 *
 * ★★**「전체 상품 목록」이 아니다.** **확정된 약관에서 검색된 후보**다.
 *   목록에 없다고 그 상품이 없는 것이 아니다 — 아직 「이 파일이 무엇인가」를
 *   확정하지 못한 약관이 있다.
 *
 * ★★**여기에 건수를 적지 않는다.** 예전에 「확정 850건 / 판정대상 1,367건(62.2%)」
 *   이라고 적어 뒀는데, 원장이 자라는 동안 주석만 그대로 남아 **틀린 말**이 됐다
 *   (2026-08-26 실측 1,353건 / 1,362건 = 99.3%). 숫자의 출처는 두 곳이다 —
 *   화면에 보이는 값은 `GET /v1/catalog/products` 의 `note`·`confirmed_for_insurer`,
 *   전체 범위는 `GET /v1/support-manifest` 다. 세고 싶으면 그쪽을 부른다.
 *
 * ★그래서 **보험사를 고르기 전에는 잠가 둔다.** 서버도 보험사를 필수로 받는다.
 *   전량을 내려주면 「목록에 없으면 미지원」으로 읽힌다.
 *
 * ★★**고른다고 판본이 정해지지 않는다.** 같은 상품명이 여러 판본으로 존재한다
 *   (2026-08-26 실측 201종·611건 — 이 수치도 원장이 바뀌면 낡는다.
 *   재려면 `config/confirmed_documents.jsonl` 을 (보험사, 상품명)으로 세면 된다).
 *   적용 판본은 **가입일**이 정한다. 그래서 후보마다 판본 수를 함께 보이고,
 *   안내 문구로도 말한다.
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
  box.placeholder = '눌러서 목록 보기 · 입력하면 좁혀집니다';

  //: ★★비어 있어도 부른다. 예전에는 두 글자를 넣어야 후보가 나왔는데,
  //:   무엇을 넣어야 할지 모르는 사람에게는 **목록이 곧 설명**이다.
  //:   서버는 빈 `q` 를 그대로 받는다(실측 2026-08-26: 삼성화재 10건 반환).
  const q = box.value.trim();
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

  //: ★★설명은 **서버가 준 문장을 그대로** 쓴다(`note`). 화면에 같은 말을 또 적어 두면
  //:   서버 쪽이 바뀔 때 둘이 어긋나고, 어느 쪽이 사실인지 알 수 없게 된다.
  $('productNameTip').setAttribute('data-tip',
    body.note || '확정된 약관에서 찾은 후보만 보여 줍니다.');

  const items = body.items || [];
  list.replaceChildren();
  for (const it of items) {
    const opt = document.createElement('option');
    opt.value = it.product_name;
    //: ★판본 수를 **함께** 보인다 — 「골랐으니 내 약관이 정해졌다」로 믿게 두지 않는다.
    opt.label = `판본 ${it.versions}개 · ${it.sale_start_range[0]}~${it.sale_start_range[1]}`;
    list.appendChild(opt);
  }
  //: ★★고른 상품의 **판매개시일**을 기억해 둔다. 계약일을 비워 두고 등록할 때
  //:   무엇을 기준으로 채웠는지 화면이 말할 수 있어야 한다(§`applyDateDefaults`).
  _productSaleStart = new Map(items.map((it) => [it.product_name, it.sale_start_range?.[0] || '']));
  syncEnrolledPlaceholder();

  if (!items.length) {
    _setProductHint(
      q
        ? `'${q}' 로 찾은 확정 약관이 없습니다. 확정되지 않은 약관은 여기 나오지 않으니, ` +
          '상품명을 비우고 진행하셔도 됩니다.'
        : `${insurer} 의 확정 약관 후보를 불러오지 못했습니다. 상품명은 비워 두어도 됩니다.`);
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
  /* ★★**넣을 수 있는 것을 먼저 보여 준다.**
   *   전에는 서버가 준 순서 그대로 섞어 놓아서, 맨 앞에 `O00~O99` 같은
   *   **누를 수 없는 범위**가 오는 일이 흔했다. 사용자는 그것부터 눌러 보고
   *   "판정 입력값이 아닙니다"라는 말을 듣는다 — 목록이 사람을 헛걸음시킨 것이다.
   *   그래서 ① 입력 가능한 개별 코드 ② 참고용 약관 범위 로 나누고,
   *   각 묶음이 무엇인지 **제목으로 밝힌다.**
   * ★범위도 지우지 않는다. 약관에 그렇게 적혀 있다는 것 자체가 정보다.
   */
  const usable = items.filter((it) => it.input_allowed);
  const ranges = items.filter((it) => !it.input_allowed);

  function chipFor(it) {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'chip';
    b.style.cursor = 'pointer';
    if (it.input_allowed) {
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
      b.style.opacity = '.62';
      b.addEventListener('click', () => {
        if (summary) {
          summary.textContent =
            `${it.code}는 약관의 분류 범위이므로 판정 입력값이 아닙니다. ` +
            '진료비 세부내역서나 진단서에 적힌 개별 상병코드(예: C34.1)를 입력하세요.';
        }
      });
    }
    return b;
  }

  function section(title, help, list) {
    if (!list.length) return null;
    const wrap = document.createElement('div');
    wrap.style.marginTop = '4px';
    const h = document.createElement('p');
    h.className = 'precheck-help';
    h.style.margin = '8px 0 4px';
    h.innerHTML = `<strong>${esc(title)} ${list.length}종</strong> — ${esc(help)}`;
    wrap.appendChild(h);
    const row = document.createElement('div');
    list.forEach((it) => row.appendChild(chipFor(it)));
    wrap.appendChild(row);
    return wrap;
  }

  const frag = document.createDocumentFragment();
  const a = section('입력할 수 있는 코드', '눌러서 넣을 수 있습니다.', usable);
  if (a) frag.appendChild(a);
  const c = section('약관에만 있는 범위', '개인 상병코드가 아니라 넣을 수 없습니다. 참고용입니다.', ranges);
  if (c) frag.appendChild(c);
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

  //: ★★보험사 수를 **세어서** 넣는다. 예전에 「12개 보험사」라고 박아 뒀는데,
  //:   그 숫자는 확정 원장이 바뀌면 조용히 틀린 말이 된다(출처는 이 응답이다).
  $('insurerTip').setAttribute('data-tip',
    `약관을 수집·확정해 둔 보험사 ${insurers.length}곳입니다.\n`
    + '여기 없는 보험사는 아직 판정할 수 없습니다.');

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

function renderRelated(cites, searchState) {
  //: ★★**근거와 눈으로 구분돼야 한다.** 안 그러면 백엔드에서 목록을 나눈 것이
  //:   화면에서 도로 합쳐진다 — 사용자에게는 화면이 곧 계약이다.
  //:   그래서 ① 제목을 「참고 조항」으로 다르게 달고 ② 「판정 근거가 아닙니다」를
  //:   **매번** 붙이고 ③ 테두리 색을 달리한다(.cite.related).
  if (searchState && searchState.indexOf('failed:') === 0) {
    //: ★실패를 조용히 빈 화면으로 만들지 않는다 — 「관련 조항 없음」과 다른 말이다.
    return `<div class="banner warn" style="margin-top:16px">
      참고 조항을 함께 보여 드리지 못했습니다(검색 오류).
      <span class="small">보장 판정 자체는 약관 조항 대조로 이뤄졌으며 영향받지 않았습니다.</span></div>`;
  }
  if (!cites || !cites.length) return '';
  return `<h2 style="margin-top:18px">참고 조항 <span class="small muted">— 판정 근거가 아닙니다</span></h2>
    <div class="small muted" style="margin-bottom:4px">
      질병기호가 적힌 조항이 아니라, <strong>의미가 가까워 읽어 볼 만한</strong> 조항입니다.
      비슷하다는 것은 근거가 아니므로 위 판정에는 쓰이지 않았습니다.</div>` +
    cites.map((c) => `
    <div class="cite related">
      <div><strong>${esc(c.title || c.qualified_no)}${c.scope ? ` · ${esc(c.scope)}` : ''}</strong></div>
      <div class="quote">${esc(c.quote || '')}</div>
      <div class="loc">${esc(c.clause_id)} · ${esc(c.section || '')} p${c.page_from}${c.page_to && c.page_to !== c.page_from ? '–' + c.page_to : ''}</div>
    </div>`).join('');
}

function renderResult(status, b) {
  const out = $('result');
  //: ★★판정 결과는 **눈에 보이는 곳에** 그린다. 서랍에만 써 두고 열지 않으면
  //:   화면상 아무 일도 안 일어난 것처럼 보인다(실패 응답도 마찬가지다).
  setDetailOpen(true);

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
      ${renderRelated(b.related_clauses, b.related_search)}

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
      약관 원문 근거 ${citationCount}건을 「보장 확인 상세」에 표시했습니다.
    </div>
    <button class="chat-detail-link" type="button" data-scroll-result
            data-tip-align="start"
            data-tip="약관 원문 근거와 비슷한 사례를 오른쪽에서 보여 줍니다."
    >상세 근거 보기 →</button>`;
}

/* ★★상세 결과가 **서랍으로 옮겨 간 뒤로 이 버튼이 아무 일도 하지 않았다.**
 *   예전에는 상세가 페이지 아래에 있어서 `scrollIntoView` 로 닿았는데,
 *   지금은 `transform: translateX(100%)` 로 화면 밖에 있어 스크롤로는 닿지 않는다.
 *   화면 구조를 바꿨으면 그리로 가는 길도 같이 고쳐야 했다(내 회귀다).
 */
function bindResultLink(message) {
  const button = message?.querySelector('[data-scroll-result]');
  if (!button) return;
  button.addEventListener('click', () => setDetailOpen(true));
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
  //: ★★비어 있는 날짜를 **여기서** 채운다. 검증(`updateRegisterState`)이 아니라
  //:   실제로 보낼 값을 만드는 자리다 — 채운 값이 입력칸에 그대로 보인다.
  const assumed = applyDateDefaults();
  if (assumed.length) {
    const note = `입력하지 않은 날짜를 채웠습니다 — ${assumed.join(' · ')}. ` +
      '실제 날짜와 다르면 적용 약관과 판정이 달라집니다.';
    $('status').textContent = note;
    if (!options.silentChat) bubble('bot', `<div class="banner warn">${esc(note)}</div>`);
  } else {
    $('status').textContent = '판정 중…';
  }
  $('go').disabled = true;

  const precheckKey = newIdempotencyKey();
  const { status, body } = await api('/v1/prechecks', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      //: ★PostgreSQL 저장 모드 필수 헤더(app/routers/precheck.py:428-431).
      //:   빠지면 이 화면이 보내는 요청이 전부 422로 거부된다(코덱스 리뷰 지적).
      'Idempotency-Key': precheckKey,
    },
    body: JSON.stringify({
      insurer: $('insurer').value.trim(),
      enrolled_on: $('enrolled').value.trim(),
      incident_on: $('incident').value.trim(),
      kcd_codes: codes,
      //: ★사용자가 후보를 고른 경우에만 실린다. 화면이 지어내지 않는다.
      ...(selectedProduct ? { product_name: selectedProduct } : {}),
    }),
  });

  $('status').textContent = '';
  updateRegisterState();
  if (status === 200 && body?.trace_id) {
    lastPrecheckTraceId = body.trace_id;
    lastPrecheckIdempotencyKey = precheckKey;
  }
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

/* ★대화 초기화 — **화면의 기록만** 지운다.
 *   판정 원장이나 서버 세션을 지우는 것이 아니다. 지운다고 말하면 안 되는
 *   것을 지운 것처럼 읽히므로, 하는 일을 그대로 부른다(`resetChat`).
 */
/* ★★초기화 뒤에 **먼저 보낸 답이 뒤늦게 떨어지는** 일을 막는다.
 *   실측 2026-08-26: 답을 기다리는 동안 초기화를 누르면 화면은 비었다가
 *   잠시 뒤 지운 대화의 답이 혼자 나타났다. 사용자에게는 지워지지 않은 것으로 보인다.
 *   보낸 시점의 회차를 들고 있다가, 돌아왔을 때 회차가 다르면 **버린다.**
 */
let _chatEpoch = 0;

function resetChat() {
  _chatEpoch += 1;
  $('chatLog').innerHTML = '';
  $('chatIn').value = '';
  syncEmptyState();
  $('chatIn').focus({ preventScroll: true });
}

function bubble(cls, html) {
  const d = document.createElement('div');
  d.className = 'msg ' + cls;
  d.innerHTML = html;
  const log = $('chatLog');
  log.appendChild(d);
  syncEmptyState();
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

  const epoch = _chatEpoch;
  const thinking = bubble('bot muted', '약관에서 찾는 중…');

  const { status, body } = await api('/v1/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message: msg }),
  });
  thinking.remove();

  //: ★기다리는 동안 초기화됐으면 이 답은 **지운 대화의 것**이다. 그리지 않는다.
  if (epoch !== _chatEpoch) return;

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
  //: ★★패널이 닫혀 있으면 입력칸이 화면 밖이라 `scrollIntoView`·`focus` 가
  //:   아무 일도 하지 않는다(상세 서랍과 같은 종류의 실수다). **먼저 연다.**
  if (body.next_action === 'precheck_form') {
    setPanelOpen(true);
    requestAnimationFrame(() => {
      const box = $('insurer');
      box.scrollIntoView({ behavior: 'smooth', block: 'center' });
      box.focus();
    });
  }
}

/* ── 컷⑨ 증빙 제출 ────────────────────────────────────────────── */

/* ★제출 결과를 "반영되었습니다"로 그리지 않는다.
 *   서버는 `verification="unverified"` 로 고정해 저장하고, 검증 전까지
 *   통계에 넣지 않는다. 화면이 그보다 강하게 말하면 거짓말이 된다.
 */
//: ★증거 파일은 판정 근거가 아니라 감사용 원본이다 — 서버가 내용을 파싱·신뢰하지
//:   않는다(app/routers/precheck.py:upload_observation_evidence). 실패해도 제출
//:   자체를 막지 않는다(파일은 선택 항목이므로) — 다만 실패 사실은 사용자에게 보인다.
async function uploadObservationEvidence() {
  const input = $('obEvidence');
  const file = input?.files?.[0];
  if (!file) return { ok: true, sha256: '', storedRef: '' };

  const form = new FormData();
  form.append('file', file);
  const { status, body } = await api('/v1/observations/evidence', { method: 'POST', body: form });
  if (status !== 200 || !body?.evidence_sha256) {
    return { ok: false, sha256: '', storedRef: '', detail: body?.detail || `HTTP ${status}` };
  }
  return { ok: true, sha256: body.evidence_sha256, storedRef: body.evidence_stored_ref };
}

async function submitObservation() {
  const out = $('obOut');
  const insurer = $('obInsurer').value.trim();
  if (!insurer) {
    out.innerHTML = '<div class="banner warn small">보험사를 적어 주세요.</div>';
    return;
  }
  $('obGo').disabled = true;

  const evidence = await uploadObservationEvidence();
  if (!evidence.ok) {
    $('obGo').disabled = false;
    out.innerHTML = `<div class="banner danger small">증거 파일을 저장하지 못했습니다 — ${esc(evidence.detail)}</div>`;
    return;
  }

  const { status, body } = await api('/v1/observations', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      //: ★PostgreSQL 저장 모드 필수 헤더(app/routers/precheck.py:658-668).
      'Idempotency-Key': newIdempotencyKey(),
    },
    body: JSON.stringify({
      client_ref: 'web-ui',
      insurer,
      enrolled_on: $('enrolled').value.trim(),
      kcd_codes: $('obCodes').value.split(',').map((s) => s.trim()).filter(Boolean),
      outcome: $('obOutcome').value,
      outcome_reason: $('obReason').value.trim(),
      //: ★빈 값은 키 자체를 뺀다 — 여러 필드가 패턴 제약(`^[0-9]{8}$` 등)이라
      //:   빈 문자열을 보내면 "선택 항목이라 안 채웠다"가 아니라 **형식 오류**로
      //:   읽혀 기본(file) 모드에서도 422가 났다(코덱스 리뷰 지적).
      ...(lastPrecheckTraceId ? { precheck_trace_id: lastPrecheckTraceId } : {}),
      ...(lastPrecheckIdempotencyKey
        ? { precheck_idempotency_key: lastPrecheckIdempotencyKey } : {}),
      ...($('obClaimed').value.trim() ? { claimed_on: $('obClaimed').value.trim() } : {}),
      ...($('obDecided').value.trim() ? { decided_on: $('obDecided').value.trim() } : {}),
      ...($('obDocType').value ? { evidence_doc_type: $('obDocType').value } : {}),
      ...(evidence.sha256 ? { evidence_sha256: evidence.sha256 } : {}),
      ...(evidence.storedRef ? { evidence_stored_ref: evidence.storedRef } : {}),
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
$('sideToggle').addEventListener('click', () => setPanelOpen(!isPanelOpen()));
$('sideClose').addEventListener('click', () => setPanelOpen(false));
$('detailOpen').addEventListener('click', () => setDetailOpen(!isDetailOpen()));
$('detailClose').addEventListener('click', () => setDetailOpen(false));
//: 어두운 막을 누르면 **열려 있는 쪽**을 닫는다. 서랍이 패널보다 위에 있으므로 먼저 본다.
$('scrim').addEventListener('click', () => {
  if (isDetailOpen()) setDetailOpen(false);
  else setPanelOpen(false);
});
//: ★Esc 로도 닫힌다 — 스크림을 못 누르는 키보드 사용자를 위해서다.
document.addEventListener('keydown', (e) => {
  if (e.key !== 'Escape') return;
  if (isDetailOpen()) setDetailOpen(false);
  else if (isPanelOpen()) setPanelOpen(false);
});
$('resetChatBtn').addEventListener('click', resetChat);
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
//: ★폭이 바뀌면 「덮개인가 한 칸인가」가 달라진다 — 막는 범위도 따라 바꾼다.
window.addEventListener('resize', syncInertLayers);

document.querySelectorAll('.scroll-soft').forEach(markScrollingWhileScrolled);
//: 기본 드롭다운 대신 입력칸 바로 아래에 우리 목록을 붙인다.
createCombo($('insurer'), $('insurers'));
createCombo($('chatIn'), $('chatTerms'));
createCombo($('obInsurer'), $('insurers'));
createCombo($('productName'), $('products'), {
  //: 빈 칸으로 열었을 때도 후보가 보이도록 그때 한 번 불러온다.
  onOpen: () => { if (!$('products').children.length) loadProducts(); },
  onPick: syncEnrolledPlaceholder,
});
syncIncidentPlaceholder();
syncEnrolledPlaceholder();
syncInertLayers();
loadServiceStatus();
setPanelOpen(!isNarrow(), { focus: false });
//: ★첫 배치가 **그려진 뒤에** 전환을 켠다. 같은 프레임에서 떼면 초기 상태가
//:   애니메이션으로 보인다 — 프레임을 두 번 넘긴다.
requestAnimationFrame(() => requestAnimationFrame(
  () => $('appShell').classList.remove('no-anim')));
updateSessionCard();
updateRegisterState();
syncEmptyState();
loadProducts();
loadChatTerms();
loadScope();
