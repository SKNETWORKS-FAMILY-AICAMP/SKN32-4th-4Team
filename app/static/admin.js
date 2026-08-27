(() => {
  "use strict";

  const REFRESH_INTERVAL_MS = 30_000;

  const state = {
    agents: [],
    reviewQueue: [],
    cohort: null,
    events: [],
    knowledgeGaps: [],
    indexStatus: null,
    loading: false,
    refreshTimer: null,
    streamAbort: null,
    simulation: null,
    simTimer: null,
    mode: null,
    realQueue: null,
    users: null,
    llmProvider: null,
    clauseRerank: null,
    ipAllowlist: null
  };

  const elements = {};

  document.addEventListener("DOMContentLoaded", initialize);

  function initialize() {
    cacheElements();
    bindEvents();
    bindLoginEvents();
    bindSimulationEvents();
    bindModeEvents();
    bindLlmProviderEvents();
    bindClauseRerankEvents();
    bindIpAllowlistEvents();
    synchronizeAuthentication();

    if (hasToken()) {
      refreshDashboard();
      startAutoRefresh();
      connectStream();
    }
  }

  function cacheElements() {
    elements.refreshButton = document.getElementById("refreshButton");
    elements.reportButton = document.getElementById("reportButton");
    elements.printButton = document.getElementById("printButton");
    elements.lastUpdated = document.getElementById("lastUpdated");
    elements.authNotice = document.getElementById("authNotice");
    elements.errorBanner = document.getElementById("errorBanner");
    elements.errorMessage = document.getElementById("errorMessage");
    elements.dismissErrorButton =
      document.getElementById("dismissErrorButton");

    elements.readinessCard = document.getElementById("readinessCard");
    elements.readinessValue = document.getElementById("readinessValue");
    elements.readinessDescription =
      document.getElementById("readinessDescription");
    elements.componentStatuses =
      document.getElementById("componentStatuses");

    elements.activeAgentCount = document.getElementById("activeAgentCount");
    elements.totalAgentCount = document.getElementById("totalAgentCount");
    elements.agentSummaryCard = document.getElementById("agentSummaryCard");
    elements.pendingReviewCount = document.getElementById("pendingReviewCount");
    elements.queueSummaryCard = document.getElementById("queueSummaryCard");
    elements.unresolvedGapCount =
      document.getElementById("unresolvedGapCount");
    elements.gapSummaryCard =
      document.getElementById("gapSummaryCard");

    elements.cohortCode = document.getElementById("cohortCode");
    elements.cohortRealN = document.getElementById("cohortRealN");
    elements.cohortRealNote = document.getElementById("cohortRealNote");
    elements.cohortRealCell = document.getElementById("cohortRealCell");
    elements.cohortDemoN = document.getElementById("cohortDemoN");
    elements.cohortDemoNote = document.getElementById("cohortDemoNote");
    elements.cohortDemoCell = document.getElementById("cohortDemoCell");

    elements.agentStream = document.getElementById("agentStream");
    elements.streamState = document.getElementById("streamState");

    elements.signupBtn = document.getElementById("signupBtn");
    elements.usersTableBody = document.getElementById("usersTableBody");
    elements.usersPanelCount = document.getElementById("usersPanelCount");
    elements.usersNote = document.getElementById("usersNote");

    elements.modeBadge = document.getElementById("modeBadge");
    elements.modeAuto = document.getElementById("modeAuto");
    elements.modeDesc = document.getElementById("modeDesc");
    elements.modeUsable = document.getElementById("modeUsable");
    elements.modeConfirmed = document.getElementById("modeConfirmed");
    elements.modePending = document.getElementById("modePending");
    elements.modeCollected = document.getElementById("modeCollected");
    elements.modeNote = document.getElementById("modeNote");

    elements.llmProviderBadge = document.getElementById("llmProviderBadge");
    elements.llmProviderSelect = document.getElementById("llmProviderSelect");
    elements.llmProviderDesc = document.getElementById("llmProviderDesc");
    elements.llmProviderNote = document.getElementById("llmProviderNote");

    elements.ipAllowlistBadge = document.getElementById("ipAllowlistBadge");
    elements.ipAllowlistYourIp = document.getElementById("ipAllowlistYourIp");
    elements.ipAllowlistTextarea = document.getElementById("ipAllowlistTextarea");
    elements.ipAllowlistSaveBtn = document.getElementById("ipAllowlistSaveBtn");
    elements.ipAllowlistNote = document.getElementById("ipAllowlistNote");

    elements.clauseRerankBadge = document.getElementById("clauseRerankBadge");
    elements.clauseRerankSelect = document.getElementById("clauseRerankSelect");
    elements.clauseRerankDesc = document.getElementById("clauseRerankDesc");
    elements.clauseRerankNote = document.getElementById("clauseRerankNote");

    elements.realQueue = document.getElementById("realQueue");
    elements.realQueueCount = document.getElementById("realQueueCount");

    elements.simStateBadge = document.getElementById("simStateBadge");
    elements.simProgressWrap = document.getElementById("simProgressWrap");
    elements.simBar = document.getElementById("simBar");
    elements.simProgressText = document.getElementById("simProgressText");
    elements.simAgents = document.getElementById("simAgents");
    elements.simCases = document.getElementById("simCases");
    elements.simDelay = document.getElementById("simDelay");
    elements.simSeed = document.getElementById("simSeed");
    elements.simCodes = document.getElementById("simCodes");
    elements.simAutoVerify = document.getElementById("simAutoVerify");
    elements.simStartBtn = document.getElementById("simStartBtn");
    elements.simStopBtn = document.getElementById("simStopBtn");
    elements.simResetBtn = document.getElementById("simResetBtn");
    elements.simNote = document.getElementById("simNote");

    elements.reviewQueue = document.getElementById("reviewQueue");
    elements.queuePanelCount = document.getElementById("queuePanelCount");
    elements.agentsTableBody = document.getElementById("agentsTableBody");
    elements.agentsPanelCount = document.getElementById("agentsPanelCount");

    elements.eventsPanelCount =
      document.getElementById("eventsPanelCount");
    elements.eventTimeline =
      document.getElementById("eventTimeline");

    elements.gapStatusFilter =
      document.getElementById("gapStatusFilter");
    elements.gapSearchInput =
      document.getElementById("gapSearchInput");
    elements.gapsPanelCount =
      document.getElementById("gapsPanelCount");
    elements.knowledgeGapList =
      document.getElementById("knowledgeGapList");

    elements.adminUsername = document.getElementById("adminUsername");
    elements.adminPassword = document.getElementById("adminPassword");
    elements.adminLoginBtn = document.getElementById("adminLoginBtn");
    elements.logoutBtn = document.getElementById("logoutBtn");
    elements.loginStatus = document.getElementById("loginStatus");

    elements.loggedOutGate = document.getElementById("loggedOutGate");
    elements.dashboardHeader = document.getElementById("dashboardHeader");
    elements.dashboardMain = document.getElementById("dashboardMain");
  }

  /*
   * 로그인/로그아웃 — 이 프로젝트 공용 common.js(setAuth/clearAuth/apiFetch)를 그대로 쓴다.
   * 성공하면 auth:changed 이벤트를 쏴서 대시보드가 폴링을 기다리지 않고 바로 새로고침한다.
   */
  function bindLoginEvents() {
    if (elements.adminLoginBtn) {
      elements.adminLoginBtn.addEventListener("click", handleLogin);
    }
    if (elements.logoutBtn) {
      elements.logoutBtn.addEventListener("click", handleLogout);
    }
    if (elements.signupBtn) {
      elements.signupBtn.addEventListener("click", handleSignup);
    }
  }

  async function handleLogin() {
    const username = (elements.adminUsername.value || "").trim();
    const password = elements.adminPassword.value || "";
    elements.adminLoginBtn.disabled = true;

    try {
      // 공통 헬퍼: 얼굴 등록 계정이면 웹캠 2차 인증 오버레이까지 처리하고 최종 토큰을 준다.
      await submitLogin(username, password);
      elements.loginStatus.textContent = `로그인됨: ${username}`;
      elements.loginStatus.style.color = "var(--success)";
      document.dispatchEvent(new CustomEvent("auth:changed"));
      maybePromptFaceEnroll();
    } catch (err) {
      elements.loginStatus.textContent = err.message || "로그인 실패";
      elements.loginStatus.style.color = "var(--danger)";
    } finally {
      elements.adminLoginBtn.disabled = false;
    }
  }

  // 관리자 얼굴 등록 유도: 로그인 후 얼굴 미등록이면 마이페이지 등록을 안내(무강제).
  async function maybePromptFaceEnroll() {
    try {
      const { ok, body } = await apiFetch("/api/face/status", { headers: authHeaders() });
      if (ok && body && body.registered === false) {
        elements.loginStatus.innerHTML =
          `로그인됨 · <strong>얼굴 미등록</strong> — ` +
          `<a href="/static/mypage.html">마이페이지에서 얼굴을 등록</a>하면 다음 로그인부터 2차 인증이 적용됩니다.`;
        elements.loginStatus.style.color = "var(--warning)";
      }
    } catch { /* 상태 조회 실패는 무시(로그인 자체는 성공) */ }
  }

  function handleLogout() {
    clearAuth();
    elements.loginStatus.textContent = "로그아웃됨";
    elements.loginStatus.style.color = "var(--muted)";
    document.dispatchEvent(new CustomEvent("auth:changed"));
  }

  async function downloadReport() {
    elements.reportButton.disabled = true;
    try {
      const resp = await fetch("/api/admin/report", { headers: authHeaders() });
      if (!resp.ok) {
        handlePossibleAuthenticationError({ status: resp.status });
        showError(`보고서 생성 실패 (HTTP ${resp.status})`);
        return;
      }
      const blob = await resp.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "admin_report.pdf";
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (err) {
      showError("보고서 요청 실패: " + err.message);
    } finally {
      elements.reportButton.disabled = false;
    }
  }

  function bindEvents() {
    elements.refreshButton.addEventListener("click", () => {
      refreshDashboard();
    });
    elements.reportButton.addEventListener("click", downloadReport);
    elements.printButton.addEventListener("click", () => window.print());

    elements.dismissErrorButton.addEventListener("click", hideError);

    if (elements.cohortCode) {
      elements.cohortCode.addEventListener("change", () => refreshDashboard({ silent: true }));
    }

    elements.gapStatusFilter.addEventListener(
      "change",
      renderKnowledgeGaps
    );

    elements.gapSearchInput.addEventListener(
      "input",
      renderKnowledgeGaps
    );

    window.addEventListener("storage", handleAuthenticationChange);
    window.addEventListener("focus", handleAuthenticationChange);

    document.addEventListener(
      "auth:changed",
      handleAuthenticationChange
    );

    /*
     * 기존 로그인 폼이 페이지 안에 있는 경우, 기존 submit 핸들러를
     * 방해하지 않고 토큰 저장 이후 대시보드를 자동 갱신한다.
     */
    const loginForm = document.querySelector(
      "#loginForm, #login-form, [data-login-form]"
    );

    if (loginForm) {
      loginForm.addEventListener("submit", () => {
        window.setTimeout(handleAuthenticationChange, 500);
        window.setTimeout(handleAuthenticationChange, 1200);
      });
    }
  }

  function hasToken() {
    return typeof getToken === "function" && Boolean(getToken());
  }

  function synchronizeAuthentication() {
    const authenticated = hasToken();

    // 토큰이 없으면 대시보드 본문(헤더+패널) 자체를 렌더링하지 않는다 — 로그인 전
    // "확인 중" 같은 빈 카드가 마치 동작 중인 것처럼 보이는 걸 막는다(무폴백: 미인증 상태를
    // 스켈레톤 UI로 가리지 않고 명시적인 로그인 게이트로 보여준다).
    elements.loggedOutGate.hidden = authenticated;
    elements.dashboardHeader.hidden = !authenticated;
    elements.dashboardMain.hidden = !authenticated;

    elements.authNotice.hidden = authenticated;
    elements.refreshButton.disabled = !authenticated || state.loading;

    return authenticated;
  }

  function handleAuthenticationChange() {
    const authenticated = synchronizeAuthentication();

    if (authenticated) {
      refreshDashboard();
      startAutoRefresh();
      connectStream();
    } else {
      stopAutoRefresh();
      stopSimPolling();
      disconnectStream();
      showAuthenticationRequired();
    }
  }

  function startAutoRefresh() {
    stopAutoRefresh();

    state.refreshTimer = window.setInterval(() => {
      if (
        hasToken() &&
        !state.loading &&
        document.visibilityState === "visible"
      ) {
        refreshDashboard({ silent: true });
      }
    }, REFRESH_INTERVAL_MS);
  }

  function stopAutoRefresh() {
    if (state.refreshTimer !== null) {
      window.clearInterval(state.refreshTimer);
      state.refreshTimer = null;
    }
  }

  function formatLoadFailure(label, reason) {
    const message = reason && typeof reason.message === "string"
      ? reason.message.trim()
      : "";
    return message
      ? `${label}: ${message}`
      : `${label} 데이터를 불러오지 못했습니다.`;
  }

  async function refreshDashboard({ silent = false } = {}) {
    if (state.loading) {
      return;
    }

    if (!hasToken()) {
      showAuthenticationRequired();
      return;
    }

    state.loading = true;
    elements.refreshButton.disabled = true;
    elements.refreshButton.textContent = "갱신 중";

    if (!silent) {
      hideError();
    }

    const code = (elements.cohortCode && elements.cohortCode.value.trim()) || "S72.0";

    const requests = await Promise.allSettled([
      fetchApi("/api/admin/index"),
      fetchApi("/api/admin/agents"),
      fetchApi("/api/admin/events"),
      fetchApi("/api/admin/knowledge-gaps"),
      fetchApi("/api/admin/demo/queue"),
      fetchApi(`/api/admin/cohort-summary?code=${encodeURIComponent(code)}`),
      fetchApi("/api/admin/demo/simulation"),
      fetchApi("/api/admin/precheck-mode"),
      fetchApi("/api/admin/verifications/queue"),
      fetchApi("/api/admin/users"),
      fetchApi("/api/admin/llm-provider"),
      fetchApi("/api/admin/clause-rerank"),
      fetchApi("/api/admin/ip-allowlist")
    ]);

    const [
      indexResult,
      agentsResult,
      eventsResult,
      gapsResult,
      queueResult,
      cohortResult,
      simResult,
      modeResult,
      realQueueResult,
      usersResult,
      llmProviderResult,
      clauseRerankResult,
      ipAllowlistResult
    ] = requests;

    const failures = [];

    if (indexResult.status === "fulfilled") {
      state.indexStatus = normalizeObject(indexResult.value);
      renderReadiness();
    } else {
      state.indexStatus = null;
      renderReadinessError();
      failures.push(formatLoadFailure("준비 상태", indexResult.reason));
      handlePossibleAuthenticationError(indexResult.reason);
    }

    if (agentsResult.status === "fulfilled") {
      const payload = normalizeObject(agentsResult.value);
      state.agents = Array.isArray(payload.agents) ? payload.agents : [];
      renderAgents();
    } else {
      state.agents = [];
      renderAgentsError();
      failures.push(formatLoadFailure("에이전트", agentsResult.reason));
      handlePossibleAuthenticationError(agentsResult.reason);
    }

    if (queueResult.status === "fulfilled") {
      const payload = normalizeObject(queueResult.value);
      state.reviewQueue = Array.isArray(payload.pending) ? payload.pending : [];
      renderReviewQueue(payload.counts || {});
    } else {
      state.reviewQueue = [];
      renderReviewQueueError();
      failures.push(formatLoadFailure("검수 큐", queueResult.reason));
      handlePossibleAuthenticationError(queueResult.reason);
    }

    if (usersResult.status === "fulfilled") {
      state.users = normalizeObject(usersResult.value);
      renderUsers(state.users);
    } else {
      renderUsersError();
      failures.push(formatLoadFailure("사용자 목록", usersResult.reason));
      handlePossibleAuthenticationError(usersResult.reason);
    }

    if (modeResult.status === "fulfilled") {
      state.mode = normalizeObject(modeResult.value);
      renderMode(state.mode);
    } else {
      renderMode(null);
      failures.push(formatLoadFailure("판정 모드", modeResult.reason));
      handlePossibleAuthenticationError(modeResult.reason);
    }

    if (llmProviderResult.status === "fulfilled") {
      state.llmProvider = normalizeObject(llmProviderResult.value);
      renderLlmProvider(state.llmProvider);
    } else {
      renderLlmProvider(null);
      failures.push(formatLoadFailure("LLM 프로바이더", llmProviderResult.reason));
      handlePossibleAuthenticationError(llmProviderResult.reason);
    }

    if (clauseRerankResult.status === "fulfilled") {
      state.clauseRerank = normalizeObject(clauseRerankResult.value);
      renderClauseRerank(state.clauseRerank);
    } else {
      renderClauseRerank(null);
      failures.push(formatLoadFailure("조항 리랭커", clauseRerankResult.reason));
      handlePossibleAuthenticationError(clauseRerankResult.reason);
    }

    if (ipAllowlistResult.status === "fulfilled") {
      state.ipAllowlist = normalizeObject(ipAllowlistResult.value);
      renderIpAllowlist(state.ipAllowlist);
    } else {
      renderIpAllowlist(null);
      failures.push(formatLoadFailure("IP 허용목록", ipAllowlistResult.reason));
      handlePossibleAuthenticationError(ipAllowlistResult.reason);
    }

    if (realQueueResult.status === "fulfilled") {
      state.realQueue = normalizeObject(realQueueResult.value);
      renderRealQueue(state.realQueue);
    } else {
      renderRealQueue(null);
      failures.push(formatLoadFailure("실제 검수 큐", realQueueResult.reason));
      handlePossibleAuthenticationError(realQueueResult.reason);
    }

    if (simResult.status === "fulfilled") {
      state.simulation = normalizeObject(simResult.value);
      renderSimulation(state.simulation);
      // 다른 창(또는 CLI)에서 시작한 실행도 화면이 따라잡게 한다.
      if (state.simulation.running && !state.simTimer) {
        startSimPolling();
      }
    } else {
      renderSimulation(null);
      failures.push(formatLoadFailure("시뮬레이션 상태", simResult.reason));
      handlePossibleAuthenticationError(simResult.reason);
    }

    if (cohortResult.status === "fulfilled") {
      state.cohort = normalizeObject(cohortResult.value);
      renderCohort();
    } else {
      state.cohort = null;
      renderCohortError();
      failures.push(formatLoadFailure("코호트", cohortResult.reason));
      handlePossibleAuthenticationError(cohortResult.reason);
    }

    if (eventsResult.status === "fulfilled") {
      state.events = normalizeList(eventsResult.value, "events");
      renderEvents();
    } else {
      state.events = [];
      renderEventsError();
      failures.push(formatLoadFailure("이벤트", eventsResult.reason));
      handlePossibleAuthenticationError(eventsResult.reason);
    }

    if (gapsResult.status === "fulfilled") {
      state.knowledgeGaps = normalizeList(
        gapsResult.value,
        "knowledge_gaps",
        "knowledgeGaps",
        "gaps"
      );
      renderKnowledgeGaps();
      renderGapSummary();
    } else {
      state.knowledgeGaps = [];
      renderKnowledgeGapsError();
      renderGapSummaryError();
      failures.push(formatLoadFailure("지식갭", gapsResult.reason));
      handlePossibleAuthenticationError(gapsResult.reason);
    }

    state.loading = false;
    elements.refreshButton.disabled = !hasToken();
    elements.refreshButton.textContent = "새로고침";

    if (failures.length > 0) {
      showError(failures.join(" · "));
    }

    if (requests.some((result) => result.status === "fulfilled")) {
      elements.lastUpdated.textContent =
        `마지막 갱신 ${formatDateTime(new Date())}`;
    }
  }

  async function fetchApi(path) {
    if (typeof apiFetch !== "function") {
      throw new Error("common.js의 apiFetch를 찾을 수 없습니다.");
    }

    const headers =
      typeof authHeaders === "function"
        ? authHeaders()
        : {
            Authorization: `Bearer ${getToken()}`
          };

    // 이 프로젝트의 apiFetch(common.js)는 {status, ok, body}를 반환한다
    // (fetch Response도, .json()이 있는 객체도 아니다 — 항상 이 형태로 온다).
    const result = await apiFetch(path, {
      method: "GET",
      headers
    });

    if (!result || !result.ok) {
      const status = result ? result.status : 0;
      const body = result && result.body;
      const serverMessage = body && typeof body.message === "string"
        ? body.message.trim()
        : body && typeof body.detail === "string"
          ? body.detail.trim()
          : "";
      const error = new Error(serverMessage || `API 요청 실패: HTTP ${status}`);
      error.status = status;
      error.errorCode = body && body.error_code ? body.error_code : "";
      throw error;
    }

    return result.body;
  }

  function normalizeObject(payload) {
    if (!payload || typeof payload !== "object") {
      return {};
    }

    if (
      payload.data &&
      !Array.isArray(payload.data) &&
      typeof payload.data === "object"
    ) {
      return payload.data;
    }

    return payload;
  }

  function normalizeList(payload, ...candidateKeys) {
    if (Array.isArray(payload)) {
      return payload;
    }

    if (!payload || typeof payload !== "object") {
      return [];
    }

    if (Array.isArray(payload.data)) {
      return payload.data;
    }

    const root =
      payload.data &&
      typeof payload.data === "object" &&
      !Array.isArray(payload.data)
        ? payload.data
        : payload;

    for (const key of candidateKeys) {
      if (Array.isArray(root[key])) {
        return root[key];
      }
    }

    if (Array.isArray(root.items)) {
      return root.items;
    }

    if (Array.isArray(root.results)) {
      return root.results;
    }

    return [];
  }

  function renderReadiness() {
    const index = state.indexStatus || {};
    const ready = toBoolean(index.ready);
    const databaseReady = toBoolean(index.db_tables_ready);
    const clauseIndexReady = toBoolean(index.clause_index_ready);
    const missingTables = Array.isArray(index.missing_tables)
      ? index.missing_tables
      : [];

    clearCardState(elements.readinessCard);

    if (ready) {
      elements.readinessCard.classList.add("is-success");
      elements.readinessValue.textContent = "정상 운영";
      elements.readinessDescription.textContent =
        "데이터베이스와 보험 조항 인덱스가 모두 준비되었습니다.";
    } else {
      elements.readinessCard.classList.add("is-warning");
      elements.readinessValue.textContent = "점검 필요";

      elements.readinessDescription.textContent =
        missingTables.length > 0
          ? `누락 테이블: ${missingTables.join(", ")}`
          : "일부 백엔드 구성요소가 아직 준비되지 않았습니다.";
    }

    elements.componentStatuses.replaceChildren(
      createComponentChip("데이터베이스", databaseReady),
      createComponentChip("보험 조항 인덱스", clauseIndexReady)
    );
  }

  function renderReadinessError() {
    clearCardState(elements.readinessCard);
    elements.readinessCard.classList.add("is-danger");
    elements.readinessValue.textContent = "확인 실패";
    elements.readinessDescription.textContent =
      "인덱스 상태 API에 연결할 수 없습니다.";

    elements.componentStatuses.replaceChildren(
      createComponentChip("데이터베이스", null),
      createComponentChip("보험 조항 인덱스", null)
    );
  }

  function createComponentChip(label, ready) {
    const chip = document.createElement("span");
    chip.className = "component-chip";

    if (ready === true) {
      chip.classList.add("is-ready");
      chip.textContent = `${label} 준비`;
    } else if (ready === false) {
      chip.classList.add("is-not-ready");
      chip.textContent = `${label} 미준비`;
    } else {
      chip.textContent = `${label} 확인 불가`;
    }

    return chip;
  }

  function clearCardState(card) {
    card.classList.remove("is-success", "is-warning", "is-danger");
  }

  function createCell(value, className = "") {
    const cell = document.createElement("td");
    cell.textContent = String(value);

    if (className) {
      cell.className = className;
    }

    return cell;
  }

  function createTableMessage(message, colSpan = 4) {
    const row = document.createElement("tr");
    row.className = "loading-row";

    const cell = document.createElement("td");
    cell.colSpan = colSpan;
    cell.textContent = message;

    row.appendChild(cell);
    return row;
  }

  /* ── 계정 만들기 ─────────────────────────────────────────────────────
   *
   * ★**항상 일반 사용자(USER)로 만든다.** 화면에서 관리자를 만들 수 있으면
   *   가입한 누구나 관리자가 된다. 승격은 이미 관리자인 사람만 한다.
   */
  async function handleSignup() {
    const username = (elements.adminUsername.value || "").trim();
    const password = elements.adminPassword.value || "";
    if (!username || !password) {
      elements.loginStatus.textContent = "아이디와 비밀번호를 입력하세요.";
      elements.loginStatus.style.color = "var(--danger)";
      return;
    }

    elements.signupBtn.disabled = true;
    try {
      const { ok, status, body } = await apiFetch("/auth/signup", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, password })
      });
      if (!ok) {
        elements.loginStatus.textContent =
          (body && (body.message || body.detail)) || `계정 생성 실패 (HTTP ${status})`;
        elements.loginStatus.style.color = "var(--danger)";
        return;
      }
      // ★만들어졌다고 관리자가 된 것은 아니다. 그 사실을 바로 말해 준다.
      elements.loginStatus.innerHTML =
        `계정 <strong>${username}</strong> 생성됨(일반 사용자). ` +
        `관리자가 <strong>사용자 관리</strong>에서 승격하거나, 최초 1명이면 ` +
        `<code>python -m scripts.manage promote ${username}</code>`;
      elements.loginStatus.style.color = "var(--warning)";
    } catch (err) {
      elements.loginStatus.textContent = "계정 생성 실패: " + err.message;
      elements.loginStatus.style.color = "var(--danger)";
    } finally {
      elements.signupBtn.disabled = false;
    }
  }

  /* ── 사용자 관리 ─────────────────────────────────────────────────── */
  function renderUsers(payload) {
    if (!elements.usersTableBody) return;
    const users = (payload && payload.users) || [];
    const adminCount = (payload && payload.admin_count) || 0;

    elements.usersPanelCount.textContent =
      `${formatInteger(users.length)}명 · 관리자 ${formatInteger(adminCount)}명`;
    elements.usersTableBody.replaceChildren();

    if (users.length === 0) {
      elements.usersTableBody.appendChild(
        createTableMessage("계정이 없습니다.", 4)
      );
      return;
    }

    const fragment = document.createDocumentFragment();
    for (const u of users) {
      const row = document.createElement("tr");
      row.appendChild(createCell(valueOrFallback(u.username), "agent-ref"));

      const roleCell = document.createElement("td");
      const badge = document.createElement("span");
      const isAdmin = u.role === "ADMIN";
      badge.className = "status-badge " + (isAdmin ? "status-resolved" : "status-unknown");
      badge.textContent = isAdmin ? "관리자" : "일반";
      roleCell.appendChild(badge);
      row.appendChild(roleCell);

      row.appendChild(createCell(u.face_registered ? "등록됨" : "-"));

      const actionCell = document.createElement("td");
      actionCell.className = "num";
      const btn = document.createElement("button");
      btn.className = "verify-btn";
      btn.type = "button";
      btn.textContent = isAdmin ? "해제" : "관리자로";
      // ★마지막 관리자 해제는 **누르기 전에** 막는다. 서버도 거부하지만
      //   눌러 보고 실패하는 것보다 못 누르게 하는 편이 낫다.
      if (isAdmin && adminCount <= 1) {
        btn.disabled = true;
        btn.title = "마지막 관리자는 해제할 수 없습니다(잠금 방지).";
      }
      btn.addEventListener("click", () =>
        changeUserRole(u.username, isAdmin ? "USER" : "ADMIN", btn));
      actionCell.appendChild(btn);
      row.appendChild(actionCell);

      fragment.appendChild(row);
    }
    elements.usersTableBody.appendChild(fragment);
  }

  function renderUsersError() {
    elements.usersPanelCount.textContent = "불러오기 실패";
    elements.usersTableBody.replaceChildren(
      createTableMessage("사용자 목록을 불러오지 못했습니다.", 4)
    );
  }

  async function changeUserRole(username, role, button) {
    const what = role === "ADMIN" ? "관리자로 승격" : "관리자 권한 해제";
    if (!window.confirm(`${username} 계정을 ${what}합니다.\n\n계속할까요?`)) return;

    button.disabled = true;
    const result = await apiFetch(`/api/admin/users/${encodeURIComponent(username)}/role`, {
      method: "PUT",
      headers: { ...authHeaders(), "Content-Type": "application/json" },
      body: JSON.stringify({ role })
    });

    if (!result || !result.ok) {
      setUsersNote(simErrorText(result), "error");
      button.disabled = false;
      return;
    }
    setUsersNote((result.body && result.body.message) || "변경했습니다.", "ok");
    await refreshDashboard({ silent: true });
  }

  function setUsersNote(text, tone) {
    if (!elements.usersNote) return;
    elements.usersNote.textContent = text || "";
    elements.usersNote.className = "sim-note" + (tone ? ` is-${tone}` : "");
  }

  /* ── 판정 모드 ───────────────────────────────────────────────────────
   *
   * ★시뮬레이션 제어와 **의도적으로 다르게** 보이게 한다. 저쪽은 합성 데이터를
   *   만들 뿐이지만 이쪽은 고객이 받는 답을 바꾼다. 그래서 확인창을 띄운다.
   */
  function bindModeEvents() {
    if (!elements.modeAuto) return;
    elements.modeAuto.addEventListener("change", handleModeToggle);
  }

  async function handleModeToggle() {
    const want = elements.modeAuto.checked;
    const msg = want
      ? "자동승인을 켭니다.\n\n사람의 최종 승인을 거치지 않은 약관으로도 판정하게 됩니다.\n" +
        "판정 응답과 지원범위에 경고가 붙습니다.\n\n계속할까요?"
      : "엄격 모드로 바꿉니다.\n\n사람 승인이 끝난 약관만 사용하므로 판정 가능 약관이\n" +
        "크게 줄거나 0건이 될 수 있습니다.\n\n계속할까요?";

    if (!window.confirm(msg)) {
      elements.modeAuto.checked = !want;  // 되돌린다
      return;
    }

    elements.modeAuto.disabled = true;
    const result = await apiFetch("/api/admin/precheck-mode", {
      method: "PUT",
      headers: { ...authHeaders(), "Content-Type": "application/json" },
      body: JSON.stringify({ auto_approve: want })
    });
    elements.modeAuto.disabled = false;

    if (!result || !result.ok) {
      elements.modeAuto.checked = !want;
      setModeNote(simErrorText(result), "error");
      return;
    }
    state.mode = result.body;
    renderMode(state.mode);
    setModeNote(
      `모드를 바꿨습니다 — 판정 가능 약관 ${result.body.usable_now}건.`,
      result.body.usable_now > 0 ? "ok" : "error"
    );
  }

  function renderMode(mode) {
    if (!elements.modeBadge) return;
    if (!mode) {
      elements.modeBadge.className = "status-badge status-unknown";
      elements.modeBadge.textContent = "확인 실패";
      return;
    }

    const auto = Boolean(mode.auto_approve);
    elements.modeAuto.checked = auto;
    elements.modeBadge.className =
      "status-badge " + (auto ? "status-pending" : "status-resolved");
    elements.modeBadge.textContent = auto ? "자동승인" : "엄격";

    elements.modeDesc.className = "mode-desc" + (auto ? "" : " is-strict");
    elements.modeDesc.textContent = auto
      ? mode.warning || mode.label
      : mode.label;

    const st = mode.stats || {};
    elements.modeUsable.textContent = formatInteger(mode.usable_now ?? 0);
    elements.modeConfirmed.textContent = formatInteger(st.confirmed ?? 0);
    elements.modePending.textContent = formatInteger(st.human_signoff_pending ?? 0);
    elements.modeCollected.textContent = formatInteger(st.collected ?? 0);
  }

  function setModeNote(text, tone) {
    if (!elements.modeNote) return;
    elements.modeNote.textContent = text || "";
    elements.modeNote.className = "sim-note" + (tone ? ` is-${tone}` : "");
  }

  /* ── LLM 프로바이더 ───────────────────────────────────────────────────
   * `.env`의 LLM_PROVIDER 기본값을 런타임에 덮어쓴다. 판정 모드와 같은 이유로
   * 파일에 남겨 admin·customer 프로세스가 같은 값을 보게 한다.
   */
  function bindLlmProviderEvents() {
    if (!elements.llmProviderSelect) return;
    elements.llmProviderSelect.addEventListener("change", handleLlmProviderChange);
  }

  async function handleLlmProviderChange() {
    const want = elements.llmProviderSelect.value || null;  // "" → 기본값(null)

    elements.llmProviderSelect.disabled = true;
    const result = await apiFetch("/api/admin/llm-provider", {
      method: "PUT",
      headers: { ...authHeaders(), "Content-Type": "application/json" },
      body: JSON.stringify({ provider: want })
    });
    elements.llmProviderSelect.disabled = false;

    if (!result || !result.ok) {
      renderLlmProvider(state.llmProvider);  // 되돌린다
      setLlmProviderNote(simErrorText(result), "error");
      return;
    }
    state.llmProvider = result.body;
    renderLlmProvider(state.llmProvider);
    setLlmProviderNote(`LLM 프로바이더를 ${result.body.effective}(으)로 바꿨습니다.`, "ok");
  }

  function renderLlmProvider(info) {
    if (!elements.llmProviderBadge) return;
    if (!info) {
      elements.llmProviderBadge.className = "status-badge status-unknown";
      elements.llmProviderBadge.textContent = "확인 실패";
      return;
    }

    const overridden = Boolean(info.override);
    elements.llmProviderSelect.value = info.override || "";
    elements.llmProviderBadge.className =
      "status-badge " + (overridden ? "status-pending" : "status-resolved");
    elements.llmProviderBadge.textContent = info.effective || "-";

    elements.llmProviderDesc.textContent = overridden
      ? `.env 기본값(${info.default})을 덮어씀 — 현재 ${info.effective} 사용 중.`
      : `.env 기본값(${info.default}) 그대로 사용 중.`;
  }

  function setLlmProviderNote(text, tone) {
    if (!elements.llmProviderNote) return;
    elements.llmProviderNote.textContent = text || "";
    elements.llmProviderNote.className = "sim-note" + (tone ? ` is-${tone}` : "");
  }

  /* ── 관리자 IP 허용목록 ────────────────────────────────────────────────
   * 빈 목록 = 전체 허용. 저장할 때마다 서버가 알려주는 "현재 접속 IP"를 항상
   * 같이 보여줘서, 자기 IP를 빼먹고 저장해 스스로를 잠그는 실수를 줄인다.
   */
  function bindIpAllowlistEvents() {
    if (!elements.ipAllowlistSaveBtn) return;
    elements.ipAllowlistSaveBtn.addEventListener("click", handleIpAllowlistSave);
  }

  async function handleIpAllowlistSave() {
    const raw = (elements.ipAllowlistTextarea && elements.ipAllowlistTextarea.value) || "";
    const ips = raw.split("\n").map((s) => s.trim()).filter(Boolean);

    elements.ipAllowlistSaveBtn.disabled = true;
    const result = await apiFetch("/api/admin/ip-allowlist", {
      method: "PUT",
      headers: { ...authHeaders(), "Content-Type": "application/json" },
      body: JSON.stringify({ ips })
    });
    elements.ipAllowlistSaveBtn.disabled = false;

    if (!result || !result.ok) {
      setIpAllowlistNote(simErrorText(result), "error");
      return;
    }
    state.ipAllowlist = result.body;
    renderIpAllowlist(state.ipAllowlist);
    if (result.body.locks_out_self) {
      setIpAllowlistNote(
        "⚠ 저장했지만 지금 접속 중인 IP가 이 목록에 없습니다 — 다음 요청부터 " +
          "본인도 차단됩니다. 필요하면 지금 바로 본인 IP를 추가해 다시 저장하세요.",
        "error"
      );
    } else {
      setIpAllowlistNote(
        ips.length ? `허용 IP ${ips.length}건 저장했습니다.` : "허용목록을 비웠습니다 — 전체 허용.",
        "ok"
      );
    }
  }

  function renderIpAllowlist(info) {
    if (!elements.ipAllowlistBadge) return;
    if (!info) {
      elements.ipAllowlistBadge.className = "status-badge status-unknown";
      elements.ipAllowlistBadge.textContent = "확인 실패";
      return;
    }
    const ips = Array.isArray(info.ips) ? info.ips : [];
    elements.ipAllowlistBadge.className =
      "status-badge " + (info.enforced ? "status-pending" : "status-resolved");
    elements.ipAllowlistBadge.textContent = info.enforced
      ? `${ips.length}건 제한 중`
      : "전체 허용";
    elements.ipAllowlistYourIp.textContent = info.your_ip || "-";
    if (document.activeElement !== elements.ipAllowlistTextarea) {
      elements.ipAllowlistTextarea.value = ips.join("\n");
    }
  }

  function setIpAllowlistNote(text, tone) {
    if (!elements.ipAllowlistNote) return;
    elements.ipAllowlistNote.textContent = text || "";
    elements.ipAllowlistNote.className = "sim-note" + (tone ? ` is-${tone}` : "");
  }

  /* ── 조항 리랭커 ───────────────────────────────────────────────────────
   * `.env`의 INSURANCE_CLAUSE_RERANK_ENABLED 기본값을 런타임에 덮어쓴다.
   * LLM 프로바이더와 같은 이유로 파일에 남겨 admin·customer 프로세스가
   * 같은 값을 보게 한다. 대상은 /api/admin/clause-search 뿐 — 판정 경로는
   * 안 건드린다.
   */
  function bindClauseRerankEvents() {
    if (!elements.clauseRerankSelect) return;
    elements.clauseRerankSelect.addEventListener("change", handleClauseRerankChange);
  }

  async function handleClauseRerankChange() {
    const raw = elements.clauseRerankSelect.value;  // "" → 기본값(null)
    const want = raw === "" ? null : raw === "true";

    elements.clauseRerankSelect.disabled = true;
    const result = await apiFetch("/api/admin/clause-rerank", {
      method: "PUT",
      headers: { ...authHeaders(), "Content-Type": "application/json" },
      body: JSON.stringify({ enabled: want })
    });
    elements.clauseRerankSelect.disabled = false;

    if (!result || !result.ok) {
      renderClauseRerank(state.clauseRerank);  // 되돌린다
      setClauseRerankNote(simErrorText(result), "error");
      return;
    }
    state.clauseRerank = result.body;
    renderClauseRerank(state.clauseRerank);
    setClauseRerankNote(
      `조항 리랭커를 ${result.body.effective ? "켰습니다" : "껐습니다"}.`, "ok"
    );
  }

  function renderClauseRerank(info) {
    if (!elements.clauseRerankBadge) return;
    if (!info) {
      elements.clauseRerankBadge.className = "status-badge status-unknown";
      elements.clauseRerankBadge.textContent = "확인 실패";
      return;
    }

    const overridden = info.override !== null && info.override !== undefined;
    elements.clauseRerankSelect.value = overridden ? String(info.override) : "";
    elements.clauseRerankBadge.className =
      "status-badge " + (overridden ? "status-pending" : "status-resolved");
    elements.clauseRerankBadge.textContent = info.effective ? "켬" : "끔";

    elements.clauseRerankDesc.textContent = overridden
      ? `.env 기본값(${info.default ? "켬" : "끔"})을 덮어씀 — 현재 ${info.effective ? "켬" : "끔"}.`
      : `.env 기본값(${info.default ? "켬" : "끔"}) 그대로 사용 중.`;
  }

  function setClauseRerankNote(text, tone) {
    if (!elements.clauseRerankNote) return;
    elements.clauseRerankNote.textContent = text || "";
    elements.clauseRerankNote.className = "sim-note" + (tone ? ` is-${tone}` : "");
  }

  /* ── 실제 트랙 검수 큐 ────────────────────────────────────────────────
   *
   * ★합성 큐와 **다른 패널**이다. 한 목록에 섞어 놓고 배지로만 구분하면
   *   승인 버튼을 잘못 누른다 — 그 실수는 "실제 통계"를 오염시킨다.
   */
  function renderRealQueue(payload) {
    const pending = (payload && payload.pending) || [];
    const counts = (payload && payload.counts) || {};
    elements.realQueueCount.textContent =
      `대기 ${formatInteger(counts.pending ?? pending.length)}건 · ` +
      `승인 ${formatInteger(counts.attested ?? 0)}건`;

    elements.realQueue.replaceChildren();
    if (pending.length === 0) {
      elements.realQueue.appendChild(
        createEmptyState(
          "검수 대기 중인 실제 제보가 없습니다.",
          "고객·에이전트가 /v1/observations 로 보고하면 여기 쌓입니다."
        )
      );
      return;
    }

    const fragment = document.createDocumentFragment();
    for (const item of pending) {
      fragment.appendChild(createRealQueueItem(item));
    }
    elements.realQueue.appendChild(fragment);
  }

  function createRealQueueItem(item) {
    const li = document.createElement("li");
    li.className = "queue-item";

    const main = document.createElement("div");
    main.className = "queue-main";

    const title = document.createElement("p");
    title.className = "queue-title";
    title.textContent =
      `${valueOrFallback(item.client_ref)} · ${valueOrFallback(item.insurer)} · ` +
      `${(item.kcd_codes || []).join(", ") || "코드 없음"}`;

    const meta = document.createElement("p");
    meta.className = "queue-meta";
    meta.textContent =
      `결과 ${valueOrFallback(item.outcome)} · ${valueOrFallback(item.verification)} · ` +
      `id ${valueOrFallback(item.submission_id)}`;

    main.append(title, meta);

    const button = document.createElement("button");
    button.className = "verify-btn";
    button.type = "button";
    button.textContent = "교차검증 승인";
    button.addEventListener("click", () => attestSubmission(item.submission_id, button));

    li.append(main, button);
    return li;
  }

  //: 서버(`external_submission_store._MIN_BASIS_LEN`)와 **같은 값**이어야 한다.
  //: 다르면 화면이 통과시킨 것을 서버가 거절해 사용자가 이유를 모른다.
  const MIN_BASIS_LEN = 5;

  async function attestSubmission(submissionId, button) {
    // ★근거를 받는다. 빈 승인은 나중에 설명할 수 없다(서버도 짧으면 거절한다).
    //
    // ★★**빈 값으로 확인을 눌러도 그대로 보내고 있었다**(2026-08-04).
    //   서버가 422 로 막아 주긴 했지만 화면에는 pydantic 오류가 날것으로 찍혔다.
    //   막는 것과 **왜 막혔는지 알려 주는 것**은 다르다 — 여기서 먼저 확인한다.
    let basis = null;
    for (;;) {
      basis = window.prompt(
        "무엇을 보고 이 제보를 납득했습니까?\n" +
        `(${MIN_BASIS_LEN}자 이상 · 예: 지급통지서 사본 대조 · 통화 확인 · 영수증 금액 일치)\n\n` +
        "★이것은 발행처 확인이 아닙니다. admin_attested 등급으로 기록됩니다.",
        basis || ""
      );
      if (basis === null) return;              // 취소
      if (basis.trim().length >= MIN_BASIS_LEN) break;
      window.alert(
        `검수 근거를 ${MIN_BASIS_LEN}자 이상 적어 주세요.\n\n` +
        "이 문장은 나중에 \"이 숫자가 어떻게 생겼나\"에 답하는 유일한 기록입니다."
      );
    }
    basis = basis.trim();

    button.disabled = true;
    button.textContent = "승인 중";
    const result = await apiFetch("/api/admin/verifications", {
      method: "POST",
      headers: { ...authHeaders(), "Content-Type": "application/json" },
      body: JSON.stringify({ submission_id: submissionId, basis })
    });

    if (!result || !result.ok) {
      showError(`교차검증 승인 실패: ${simErrorText(result)}`);
      button.disabled = false;
      button.textContent = "교차검증 승인";
      return;
    }
    await refreshDashboard({ silent: true });
  }

  /* ── 시뮬레이션 제어 ─────────────────────────────────────────────────
   *
   * ★실행 중에는 **빠르게(1.5초)** 따로 폴링한다. 대시보드 전체 갱신(30초)에
   *   맡기면 진행률이 멈춘 것처럼 보인다. 멈추면 이 타이머도 끈다 —
   *   가만히 있는 화면이 서버를 계속 두드리지 않게.
   */
  const SIM_POLL_MS = 1_500;

  function bindSimulationEvents() {
    if (!elements.simStartBtn) return;
    elements.simStartBtn.addEventListener("click", startSimulation);
    elements.simStopBtn.addEventListener("click", stopSimulation);
    elements.simResetBtn.addEventListener("click", resetDemoTrack);
    bindKcdEvents();
  }

  //: ── 약관에 나오는 질병기호 ────────────────────────────────────────
  //:
  //: ★★**「질병기호 전체 표」가 아니다.** 우리는 KCD 코드→질병명 사전을
  //:   갖고 있지 않다(약 2만 항목). 그래서 화면도 그렇게 말하지 않는다 —
  //:   보여주는 것은 **확정 약관 본문에 실제로 등장한 표기**다.
  //:   그게 관리자에게 더 쓸모 있다: 「우리가 판정할 수 있는 코드가 무엇인가」.
  let kcdLoaded = false;

  function bindKcdEvents() {
    const open = document.getElementById("kcdOpen");
    const close = document.getElementById("kcdClose");
    const panel = document.getElementById("kcdPanel");
    if (!open || !panel) return;

    open.addEventListener("click", () => {
      panel.hidden = false;
      panel.scrollIntoView({ behavior: "smooth", block: "start" });
      if (!kcdLoaded) loadKcd();
    });
    if (close) close.addEventListener("click", () => { panel.hidden = true; });

    const q = document.getElementById("kcdQuery");
    const kind = document.getElementById("kcdKind");
    //: 필터는 **서버에서** 건다. 525개를 다 내려받아 클라이언트에서 거르면
    //: 「몇 개 중 몇 개」의 분모를 화면이 스스로 만들게 되어 서버와 어긋난다.
    if (q) q.addEventListener("change", loadKcd);
    if (kind) kind.addEventListener("change", loadKcd);
  }

  async function loadKcd() {
    const body = document.getElementById("kcdBody");
    const summary = document.getElementById("kcdSummary");
    if (!body) return;
    const q = (document.getElementById("kcdQuery") || {}).value || "";
    const kind = (document.getElementById("kcdKind") || {}).value || "";

    body.replaceChildren(createTableMessage("불러오는 중…", 5));
    const params = new URLSearchParams();
    if (q.trim()) params.set("q", q.trim());
    if (kind) params.set("kind", kind);
    const suffix = params.toString() ? `?${params}` : "";

    let data;
    try {
      data = await fetchApi(`/api/admin/kcd-codes${suffix}`);
    } catch (error) {
      //: ★조용히 빈 표로 만들지 않는다. 「등장하는 코드가 없다」로 읽힌다.
      body.replaceChildren(
        createTableMessage(`불러오지 못했습니다: ${error && error.message ? error.message : error}`, 5)
      );
      if (summary) summary.textContent = "목록을 불러오지 못했습니다.";
      handlePossibleAuthenticationError(error);
      return;
    }

    kcdLoaded = true;
    const items = Array.isArray(data.items) ? data.items : [];
    if (summary) {
      //: ★분모를 함께 적는다 — 거른 결과가 전량으로 보이면 안 된다.
      summary.textContent =
        `확정 약관 ${formatInteger(data.scanned_policies || 0)}건에서 찾은 표기 ` +
        `${formatInteger(data.total_ranges || 0)}종 중 ${formatInteger(data.matched || 0)}종 표시 · ` +
        `언급 ${formatInteger(data.total_mentions || 0)}회 · 기준 ${data.built_at || "-"}`;
    }

    body.replaceChildren();
    if (!items.length) {
      body.appendChild(createTableMessage("조건에 맞는 표기가 없습니다.", 5));
      return;
    }

    const KIND_KO = { exclude: "면책", exception: "면책의 예외", mention: "그 밖의 언급" };
    const fragment = document.createDocumentFragment();
    for (const item of items) {
      const row = document.createElement("tr");

      const codeCell = document.createElement("td");
      const code = document.createElement("code");
      code.textContent = item.range || "-";
      codeCell.appendChild(code);
      row.appendChild(codeCell);

      const kindCell = document.createElement("td");
      const badge = document.createElement("span");
      badge.className = `kcd-kind ${item.kind || "mention"}`;
      badge.textContent = KIND_KO[item.kind] || item.kind || "-";
      kindCell.appendChild(badge);
      row.appendChild(kindCell);

      row.appendChild(createCell(valueOrFallback(item.chapter)));
      row.appendChild(createCell(formatInteger(toFiniteNumber(item.documents, 0)), "num"));

      const ex = item.example || {};
      const where = [ex.insurer, ex.qualified_no, ex.title].filter(Boolean).join(" · ");
      const whereCell = createCell(where || "-");
      if (ex.context) whereCell.title = ex.context;
      row.appendChild(whereCell);

      fragment.appendChild(row);
    }
    body.appendChild(fragment);
  }

  function simParams() {
    const codes = (elements.simCodes.value || "")
      .split(",")
      .map((c) => c.trim())
      .filter(Boolean);

    return {
      agents: Number(elements.simAgents.value) || 1,
      cases: Number(elements.simCases.value) || 1,
      delay_ms: Number(elements.simDelay.value) || 0,
      seed: Number(elements.simSeed.value) || 0,
      auto_verify: elements.simAutoVerify.checked,
      codes
    };
  }

  async function startSimulation() {
    setSimNote("", null);
    elements.simStartBtn.disabled = true;
    const result = await apiFetch("/api/admin/demo/simulation", {
      method: "POST",
      headers: { ...authHeaders(), "Content-Type": "application/json" },
      body: JSON.stringify(simParams())
    });

    if (!result || !result.ok) {
      // 409(이미 실행 중)·422(상한 초과)를 구분해서 그대로 보여준다 — 조용히 삼키지 않는다.
      setSimNote(simErrorText(result), "error");
      renderSimulation(state.simulation);
      return;
    }
    state.simulation = result.body;
    renderSimulation(state.simulation);
    startSimPolling();
  }

  async function stopSimulation() {
    elements.simStopBtn.disabled = true;
    const result = await apiFetch("/api/admin/demo/simulation", {
      method: "DELETE",
      headers: authHeaders()
    });
    if (!result || !result.ok) {
      setSimNote(simErrorText(result), "error");
      return;
    }
    setSimNote("정지를 요청했습니다. 진행 중인 건을 마치고 멈춥니다.", "ok");
    state.simulation = result.body;
    renderSimulation(state.simulation);
  }

  async function resetDemoTrack() {
    // 되돌릴 수 없는 삭제다 — 누르자마자 지우지 않는다.
    if (!window.confirm(
      "합성 트랙(제출·승격 이력)을 모두 삭제합니다.\n" +
      "실제 트랙은 지워지지 않습니다.\n\n계속할까요?"
    )) {
      return;
    }
    elements.simResetBtn.disabled = true;
    const result = await apiFetch("/api/admin/demo/reset", {
      method: "POST",
      headers: authHeaders()
    });
    elements.simResetBtn.disabled = false;

    if (!result || !result.ok) {
      setSimNote(simErrorText(result), "error");
      return;
    }
    const removed = (result.body && result.body.removed) || [];
    setSimNote(
      removed.length
        ? `합성 트랙을 비웠습니다: ${removed.join(", ")}`
        : "지울 합성 데이터가 없었습니다.",
      "ok"
    );
    state.simulation = null;
    await refreshDashboard({ silent: true });
  }

  /*
   * ★서버 오류를 **사람이 읽을 문장**으로 바꾼다.
   *
   *   이 앱의 오류는 두 모양으로 온다 —
   *     ① AppError 계열: {ok:false, error_code, message}  ← 이미 한국어 문장
   *     ② FastAPI 입력 검증: {detail:[{type,loc,msg,ctx}, ...]}  ← **배열**
   *
   *   ②를 그대로 `JSON.stringify` 하면 화면에
   *   `[{"type":"string_too_short","loc":["body","basis"],...}]` 가 찍힌다.
   *   실제로 그렇게 나왔다(2026-08-04, 교차검증 승인). 오류 메시지가 사실을
   *   잘못 전하는 것보다 낫지도 않다 — 무엇을 고쳐야 하는지 알 수 없다.
   */
  const _VALIDATION_HINTS = {
    string_too_short: (e) => `${_fieldName(e)}이(가) 너무 짧습니다` +
      (e.ctx && e.ctx.min_length ? ` (${e.ctx.min_length}자 이상)` : ""),
    string_too_long: (e) => `${_fieldName(e)}이(가) 너무 깁니다`,
    missing: (e) => `${_fieldName(e)}이(가) 필요합니다`,
    int_parsing: (e) => `${_fieldName(e)}은(는) 숫자여야 합니다`,
    greater_than_equal: (e) => `${_fieldName(e)} 값이 너무 작습니다` +
      (e.ctx && e.ctx.ge !== undefined ? ` (${e.ctx.ge} 이상)` : ""),
    less_than_equal: (e) => `${_fieldName(e)} 값이 너무 큽니다` +
      (e.ctx && e.ctx.le !== undefined ? ` (${e.ctx.le} 이하)` : ""),
  };

  function _fieldName(e) {
    const loc = Array.isArray(e.loc) ? e.loc : [];
    return String(loc[loc.length - 1] ?? "입력값");
  }

  function simErrorText(result) {
    const status = result ? result.status : 0;
    const body = result && result.body;
    if (!body) return `HTTP ${status}`;

    if (typeof body.message === "string" && body.message) return body.message;

    const detail = body.detail;
    if (typeof detail === "string" && detail) return detail;

    if (Array.isArray(detail)) {
      const parts = detail.map((e) => {
        const hint = _VALIDATION_HINTS[e.type];
        return hint ? hint(e) : `${_fieldName(e)}: ${e.msg || e.type}`;
      });
      return parts.join(" · ");
    }
    return `HTTP ${status}`;
  }

  function renderSimulation(sim) {
    if (!elements.simStateBadge) return;

    if (!sim) {
      elements.simStateBadge.className = "status-badge status-unknown";
      elements.simStateBadge.textContent = "상태 불명";
      elements.simStartBtn.disabled = false;
      elements.simStopBtn.disabled = true;
      elements.simProgressWrap.hidden = true;
      return;
    }

    const running = Boolean(sim.running);
    elements.simStartBtn.disabled = running;
    elements.simStopBtn.disabled = !running;
    elements.simResetBtn.disabled = running;  // 쓰는 도중 삭제 금지

    elements.simStateBadge.className =
      "status-badge " +
      (running ? "status-running" : sim.stopped_by === "error" ? "status-error" : "status-stopped");
    elements.simStateBadge.textContent = running
      ? "실행 중"
      : sim.stopped_by === "error"
        ? "오류로 중단"
        : sim.stopped_by === "user"
          ? "사용자 정지"
          : sim.stopped_by === "completed"
            ? "완료"
            : "정지됨";

    const done = (sim.submitted || 0) + (sim.duplicated || 0) + (sim.failed || 0);
    const planned = sim.planned || 0;
    elements.simProgressWrap.hidden = planned === 0;

    if (planned > 0) {
      const pct = Math.min(100, Math.round((done / planned) * 100));
      elements.simBar.style.width = `${pct}%`;
      elements.simProgressText.textContent =
        `${done}/${planned} (${pct}%) · 제출 ${sim.submitted} · 승격 ${sim.promoted} · ` +
        `검증거절 ${sim.rejected || 0} · 중복 ${sim.duplicated} · 실패 ${sim.failed}`;
    }

    // ★실패를 숫자로만 두지 않는다. 마지막 사유를 화면에 올린다.
    if (sim.last_error) {
      setSimNote(`마지막 오류: ${sim.last_error}`, "error");
    }
  }

  function setSimNote(text, tone) {
    if (!elements.simNote) return;
    elements.simNote.textContent = text || "";
    elements.simNote.className = "sim-note" + (tone ? ` is-${tone}` : "");
  }

  function startSimPolling() {
    stopSimPolling();
    state.simTimer = window.setInterval(async () => {
      if (!hasToken()) {
        stopSimPolling();
        return;
      }
      const result = await apiFetch("/api/admin/demo/simulation", { headers: authHeaders() });
      if (!result || !result.ok) {
        stopSimPolling();
        return;
      }
      const wasRunning = state.simulation && state.simulation.running;
      state.simulation = result.body;
      renderSimulation(state.simulation);

      if (!result.body.running) {
        stopSimPolling();
        // 끝난 뒤 한 번 전체 갱신 — 코호트·검수 큐 숫자를 맞춘다.
        refreshDashboard({ silent: true });
      } else if (wasRunning) {
        // 실행 중에도 코호트·큐가 자라는 것을 보여준다(중복 요청은 refreshDashboard가 막는다).
        refreshDashboard({ silent: true });
      }
    }, SIM_POLL_MS);
  }

  function stopSimPolling() {
    if (state.simTimer !== null && state.simTimer !== undefined) {
      window.clearInterval(state.simTimer);
      state.simTimer = null;
    }
  }

  /* ── 코호트 두 트랙 ──────────────────────────────────────────────────
   *
   * ★두 값을 더하지 않는다. 화면에 합계 칸을 만들지 않는다 —
   *   합치는 순간 합성이 실제로 샌다(계획서 §5-1).
   */
  function renderCohort() {
    const tracks = (state.cohort && state.cohort.tracks) || {};
    paintTrack(
      tracks.verified_real,
      elements.cohortRealN, elements.cohortRealNote, elements.cohortRealCell,
      "검증된 실제 사례입니다. 0이면 0이라고 말합니다 — 지어내지 않습니다."
    );
    paintTrack(
      tracks.synthetic,
      elements.cohortDemoN, elements.cohortDemoNote, elements.cohortDemoCell,
      "시연용 생성 데이터입니다. 실제 지급 통계가 아닙니다."
    );
  }

  function paintTrack(track, nEl, noteEl, cellEl, suffix) {
    if (!track) {
      nEl.textContent = "-";
      noteEl.textContent = "불러오지 못했습니다.";
      return;
    }

    const previous = Number(nEl.dataset.n);
    nEl.textContent = formatInteger(track.n);
    nEl.dataset.n = String(track.n);

    // n이 실제로 늘어난 순간만 강조한다 — 매 폴링마다 깜빡이면 신호가 죽는다.
    if (Number.isFinite(previous) && track.n > previous) {
      cellEl.classList.remove("cohort-bump");
      void cellEl.offsetWidth;
      cellEl.classList.add("cohort-bump");
    }

    const gate = track.min_sample_met
      ? `최소표본 ${track.min_sample}건 충족`
      : `최소표본 ${track.min_sample}건 미달 → 비율 비공개`;

    const rate =
      track.approval_rate === null || track.approval_rate === undefined
        ? "비율 계산 안 함"
        : `관측 비율 ${(track.approval_rate * 100).toFixed(1)}%` +
          (Array.isArray(track.approval_ci)
            ? ` (95% CI ${(track.approval_ci[0] * 100).toFixed(0)}~${(track.approval_ci[1] * 100).toFixed(0)}%)`
            : "");

    const approved = Number(track.approved_n) || 0;
    const denied = Number(track.denied_n) || 0;
    const total = Number(track.n) || 0;
    const other = Math.max(0, total - approved - denied);
    const otherText = other > 0
      ? ` · 일부지급·처리중 ${formatInteger(other)}`
      : "";

    noteEl.textContent =
      `지급 ${formatInteger(approved)} · 부지급 ${formatInteger(denied)}` +
      `${otherText} · ${gate} · ${rate}\n${suffix}`;
  }

  function renderCohortError() {
    elements.cohortRealN.textContent = "-";
    elements.cohortDemoN.textContent = "-";
    elements.cohortRealNote.textContent = "코호트 집계를 불러오지 못했습니다.";
    elements.cohortDemoNote.textContent = "코호트 집계를 불러오지 못했습니다.";
  }

  /* ── 검수 큐 ─────────────────────────────────────────────────────── */
  function renderReviewQueue(counts) {
    const pending = state.reviewQueue;

    elements.queuePanelCount.textContent =
      `대기 ${formatInteger(counts.pending ?? pending.length)}건 · ` +
      `승격 ${formatInteger(counts.promoted ?? 0)}건`;
    elements.pendingReviewCount.textContent =
      formatInteger(counts.pending ?? pending.length);

    clearCardState(elements.queueSummaryCard);
    elements.queueSummaryCard.classList.add(
      (counts.pending ?? pending.length) > 0 ? "is-warning" : "is-success"
    );

    elements.reviewQueue.replaceChildren();

    if (pending.length === 0) {
      elements.reviewQueue.appendChild(
        createEmptyState(
          "검수 대기 중인 합성 제출이 없습니다.",
          "시뮬레이터를 실행하거나 /v1/demo/observations 로 제출하면 여기 쌓입니다."
        )
      );
      return;
    }

    const fragment = document.createDocumentFragment();
    for (const item of pending) {
      fragment.appendChild(createQueueItem(item));
    }
    elements.reviewQueue.appendChild(fragment);
  }

  function createQueueItem(item) {
    const li = document.createElement("li");
    li.className = "queue-item";

    const main = document.createElement("div");
    main.className = "queue-main";

    const title = document.createElement("p");
    title.className = "queue-title";
    title.textContent =
      `${valueOrFallback(item.client_ref)} · ${valueOrFallback(item.insurer)} · ` +
      `${(item.kcd_codes || []).join(", ") || "코드 없음"}`;

    const meta = document.createElement("p");
    meta.className = "queue-meta";
    meta.textContent =
      `결과 ${valueOrFallback(item.outcome)} · ${valueOrFallback(item.verification, "unverified")} · ` +
      `id ${valueOrFallback(item.submission_id)}`;

    main.append(title, meta);

    const button = document.createElement("button");
    button.className = "verify-btn";
    button.type = "button";
    button.textContent = "검수 승인";
    button.addEventListener("click", () => verifySubmission(item.submission_id, button));

    li.append(main, button);
    return li;
  }

  async function verifySubmission(submissionId, button) {
    button.disabled = true;
    button.textContent = "승격 중";
    try {
      const result = await apiFetch("/api/admin/demo/verifications", {
        method: "POST",
        headers: { ...authHeaders(), "Content-Type": "application/json" },
        body: JSON.stringify({ submission_id: submissionId })
      });
      if (!result || !result.ok) {
        const detail = (result && result.body && result.body.detail) || `HTTP ${result && result.status}`;
        showError(`승격 실패: ${detail}`);
        button.disabled = false;
        button.textContent = "검수 승인";
        return;
      }
      // 승격 직후 바로 갱신해 n이 움직이는 것을 눈으로 보게 한다.
      await refreshDashboard({ silent: true });
    } catch (err) {
      showError("승격 요청 실패: " + err.message);
      button.disabled = false;
      button.textContent = "검수 승인";
    }
  }

  function renderReviewQueueError() {
    elements.queuePanelCount.textContent = "불러오기 실패";
    elements.pendingReviewCount.textContent = "-";
    clearCardState(elements.queueSummaryCard);
    elements.queueSummaryCard.classList.add("is-danger");
    elements.reviewQueue.replaceChildren(
      createEmptyState("검수 큐를 불러오지 못했습니다.", "인증 상태와 API 연결을 확인해주세요.")
    );
  }

  /* ── 에이전트 목록 ───────────────────────────────────────────────── */
  function renderAgents() {
    const agents = state.agents;
    const active = agents.filter((a) => a.active).length;

    elements.activeAgentCount.textContent = formatInteger(active);
    elements.totalAgentCount.textContent = formatInteger(agents.length);
    elements.agentsPanelCount.textContent = `${formatInteger(agents.length)}대`;

    clearCardState(elements.agentSummaryCard);
    elements.agentSummaryCard.classList.add(active > 0 ? "is-success" : "is-warning");

    elements.agentsTableBody.replaceChildren();

    if (agents.length === 0) {
      elements.agentsTableBody.appendChild(
        createTableMessage("아직 요청한 에이전트가 없습니다.", 4)
      );
      return;
    }

    const fragment = document.createDocumentFragment();
    for (const agent of agents) {
      const row = document.createElement("tr");

      const refCell = document.createElement("td");
      refCell.className = "agent-ref";
      const dot = document.createElement("span");
      dot.className = `live-dot${agent.active ? " is-active" : ""}`;
      refCell.append(dot, document.createTextNode(valueOrFallback(agent.client_ref)));
      row.appendChild(refCell);

      const trackCell = document.createElement("td");
      trackCell.appendChild(createTrackBadge(agent.track));
      row.appendChild(trackCell);

      row.appendChild(createCell(formatInteger(agent.events), "num"));
      row.appendChild(
        createCell(agent.idle_s >= 0 ? `${agent.idle_s}s` : "-", "num")
      );

      fragment.appendChild(row);
    }
    elements.agentsTableBody.appendChild(fragment);
  }

  function renderAgentsError() {
    elements.activeAgentCount.textContent = "-";
    elements.totalAgentCount.textContent = "-";
    elements.agentsPanelCount.textContent = "불러오기 실패";
    clearCardState(elements.agentSummaryCard);
    elements.agentSummaryCard.classList.add("is-danger");
    elements.agentsTableBody.replaceChildren(
      createTableMessage("에이전트 목록을 불러오지 못했습니다.", 4)
    );
  }

  function createTrackBadge(track) {
    const badge = document.createElement("span");
    if (track === "synthetic") {
      badge.className = "track-badge track-synthetic";
      badge.textContent = "합성";
    } else if (track === "verified_real") {
      badge.className = "track-badge track-real";
      badge.textContent = "실제";
    } else {
      badge.className = "track-badge track-none";
      badge.textContent = "-";
    }
    return badge;
  }

  /* ── 실시간 스트림(SSE) ──────────────────────────────────────────────
   *
   * ★EventSource 는 헤더를 못 붙인다(토큰을 Authorization 으로 못 보낸다).
   *   그래서 fetch + ReadableStream 으로 직접 읽는다. 토큰을 쿼리스트링에
   *   실으면 서버 로그·리퍼러에 남는다 — 그렇게 하지 않는다.
   */
  function connectStream() {
    if (state.streamAbort) {
      return;
    }
    const controller = new AbortController();
    state.streamAbort = controller;
    setStreamState("연결 중…", false);

    fetch("/api/admin/agents/stream", {
      headers: authHeaders(),
      signal: controller.signal
    })
      .then(async (resp) => {
        if (!resp.ok || !resp.body) {
          setStreamState(`연결 실패 (HTTP ${resp.status})`, false);
          state.streamAbort = null;
          return;
        }
        setStreamState("● 실시간 연결됨", true);

        const reader = resp.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";

        for (;;) {
          const { value, done } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });

          // SSE 프레임 경계는 빈 줄이다.
          let split;
          while ((split = buffer.indexOf("\n\n")) !== -1) {
            const frame = buffer.slice(0, split);
            buffer = buffer.slice(split + 2);
            handleStreamFrame(frame);
          }
        }
        setStreamState("연결 끊김", false);
        state.streamAbort = null;
      })
      .catch((err) => {
        if (err.name !== "AbortError") {
          setStreamState("연결 끊김", false);
        }
        state.streamAbort = null;
      });
  }

  function disconnectStream() {
    if (state.streamAbort) {
      state.streamAbort.abort();
      state.streamAbort = null;
    }
    setStreamState("연결 안 됨", false);
  }

  function handleStreamFrame(frame) {
    const line = frame.split("\n").find((l) => l.startsWith("data:"));
    if (!line) return;  // ": keep-alive" 주석 프레임
    let payload;
    try {
      payload = JSON.parse(line.slice(5).trim());
    } catch {
      return;
    }

    if (payload.kind === "_snapshot") {
      elements.agentStream.replaceChildren();
      for (const item of (payload.items || []).slice().reverse()) {
        prependStreamItem(item, false);
      }
      if (!(payload.items || []).length) {
        elements.agentStream.appendChild(
          createEmptyState("아직 상호작용이 없습니다.", "에이전트가 요청하면 여기에 실시간으로 표시됩니다.")
        );
      }
      return;
    }

    const empty = elements.agentStream.querySelector(".empty-state");
    if (empty) empty.remove();
    prependStreamItem(payload, true);

    // 승격 이벤트가 오면 코호트 숫자가 바뀌었다는 뜻 → 즉시 갱신.
    if (payload.kind === "admin.verify") {
      refreshDashboard({ silent: true });
    }
  }

  function prependStreamItem(ev, isNew) {
    const li = document.createElement("li");
    li.className = `stream-item${isNew ? " is-new" : ""}`;

    const time = document.createElement("span");
    time.className = "stream-time";
    const d = parseDate(ev.at);
    time.textContent = d ? formatDateTime(d) : "-";

    const kind = document.createElement("span");
    kind.className = `stream-kind${ev.kind === "admin.verify" ? " is-verify" : ""}`;
    kind.textContent = valueOrFallback(ev.kind);

    const body = document.createElement("span");
    body.className = "stream-body";
    body.textContent =
      `${valueOrFallback(ev.client_ref)} ${ev.track ? `[${ev.track}]` : ""} ` +
      compactValue(ev.detail || {});

    li.append(time, kind, body);
    elements.agentStream.prepend(li);

    // 화면이 무한히 자라지 않게 자른다(스트림은 관측용이지 저장소가 아니다).
    while (elements.agentStream.children.length > 120) {
      elements.agentStream.lastElementChild.remove();
    }
  }

  function setStreamState(text, live) {
    elements.streamState.textContent = text;
    elements.streamState.classList.toggle("is-live", Boolean(live));
  }

  function renderEvents() {
    const events = [...state.events].sort(compareEventsNewestFirst);

    elements.eventsPanelCount.textContent =
      `${formatInteger(events.length)}건`;

    elements.eventTimeline.replaceChildren();

    if (events.length === 0) {
      elements.eventTimeline.appendChild(
        createEmptyState(
          "관측 이벤트가 없습니다.",
          "에이전트가 동작하면 trace 이벤트가 여기에 표시됩니다."
        )
      );
      return;
    }

    const fragment = document.createDocumentFragment();

    for (const event of events) {
      fragment.appendChild(createTimelineItem(event));
    }

    elements.eventTimeline.appendChild(fragment);
  }

  function renderEventsError() {
    elements.eventsPanelCount.textContent = "불러오기 실패";
    elements.eventTimeline.replaceChildren(
      createEmptyState(
        "이벤트를 불러오지 못했습니다.",
        "인증 상태와 API 연결을 확인해주세요."
      )
    );
  }

  function createTimelineItem(event) {
    const detail = parseEventDetail(event.detail);
    const item = document.createElement("li");
    item.className = "timeline-item";

    const dot = document.createElement("span");
    dot.className = `timeline-dot ${eventToneClass(event.kind)}`;
    dot.setAttribute("aria-hidden", "true");

    const heading = document.createElement("div");
    heading.className = "timeline-heading";

    const headingLeft = document.createElement("div");

    const kind = document.createElement("p");
    kind.className = "timeline-kind";
    kind.textContent = valueOrFallback(event.kind, "unknown");

    const trace = document.createElement("code");
    trace.className = "trace-id";
    trace.textContent =
      `trace ${valueOrFallback(event.trace_id, "-")}`;

    const time = document.createElement("time");
    time.className = "timeline-time";

    const eventDate = extractEventDate(event, detail.parsed);

    if (eventDate) {
      time.dateTime = eventDate.toISOString();
      time.textContent = formatDateTime(eventDate);
    } else {
      time.textContent =
        event.id !== undefined && event.id !== null
          ? `이벤트 #${event.id}`
          : "시각 정보 없음";
    }

    headingLeft.append(kind, trace);
    heading.append(headingLeft, time);

    const summary = document.createElement("p");
    summary.className = "event-summary";
    summary.textContent = summarizeEventDetail(detail.parsed);

    const details = document.createElement("details");
    details.className = "event-detail";

    const detailsSummary = document.createElement("summary");
    detailsSummary.textContent = "상세 데이터 보기";

    const raw = document.createElement("pre");
    raw.textContent = detail.pretty;

    details.append(detailsSummary, raw);
    item.append(dot, heading, summary, details);

    return item;
  }

  function parseEventDetail(rawDetail) {
    if (
      rawDetail &&
      typeof rawDetail === "object"
    ) {
      return {
        parsed: rawDetail,
        pretty: JSON.stringify(rawDetail, null, 2)
      };
    }

    if (typeof rawDetail !== "string") {
      return {
        parsed: {},
        pretty: valueOrFallback(rawDetail)
      };
    }

    try {
      const parsed = JSON.parse(rawDetail);

      return {
        parsed,
        pretty: JSON.stringify(parsed, null, 2)
      };
    } catch {
      return {
        parsed: { message: rawDetail },
        pretty: rawDetail
      };
    }
  }

  function summarizeEventDetail(detail) {
    if (detail === null || detail === undefined) {
      return "상세 정보가 없습니다.";
    }

    if (typeof detail !== "object") {
      return truncateText(String(detail), 180);
    }

    const preferredKeys = [
      "message",
      "summary",
      "action",
      "status",
      "question",
      "query",
      "tool",
      "model",
      "error"
    ];

    const parts = [];

    for (const key of preferredKeys) {
      if (
        detail[key] !== undefined &&
        detail[key] !== null &&
        detail[key] !== ""
      ) {
        parts.push(
          `${humanizeKey(key)}: ${compactValue(detail[key])}`
        );
      }

      if (parts.length >= 3) {
        break;
      }
    }

    if (parts.length === 0) {
      for (const [key, value] of Object.entries(detail).slice(0, 3)) {
        if (isTimestampKey(key)) {
          continue;
        }

        parts.push(`${humanizeKey(key)}: ${compactValue(value)}`);
      }
    }

    return parts.length > 0
      ? truncateText(parts.join(" · "), 240)
      : "상세 정보가 없습니다.";
  }

  function compareEventsNewestFirst(a, b) {
    const detailA = parseEventDetail(a.detail).parsed;
    const detailB = parseEventDetail(b.detail).parsed;

    const dateA = extractEventDate(a, detailA);
    const dateB = extractEventDate(b, detailB);

    if (dateA && dateB) {
      return dateB.getTime() - dateA.getTime();
    }

    if (dateA) {
      return -1;
    }

    if (dateB) {
      return 1;
    }

    return compareIdsDescending(a.id, b.id);
  }

  function extractEventDate(event, detail) {
    const candidates = [
      event.created_at,
      event.timestamp,
      event.occurred_at,
      event.time,
      detail && detail.created_at,
      detail && detail.timestamp,
      detail && detail.occurred_at,
      detail && detail.event_time,
      detail && detail.time
    ];

    for (const value of candidates) {
      const date = parseDate(value);

      if (date) {
        return date;
      }
    }

    return null;
  }

  function parseDate(value) {
    if (value === null || value === undefined || value === "") {
      return null;
    }

    let timestamp;

    if (typeof value === "number") {
      timestamp = value < 10_000_000_000 ? value * 1000 : value;
    } else {
      timestamp = Date.parse(String(value));
    }

    const date = new Date(timestamp);
    return Number.isNaN(date.getTime()) ? null : date;
  }

  function eventToneClass(kind) {
    const normalized = String(kind || "").toLowerCase();

    if (
      normalized.includes("error") ||
      normalized.includes("fail") ||
      normalized.includes("exception")
    ) {
      return "is-error";
    }

    if (
      normalized.includes("success") ||
      normalized.includes("complete") ||
      normalized.includes("finish")
    ) {
      return "is-success";
    }

    if (
      normalized.includes("warn") ||
      normalized.includes("retry") ||
      normalized.includes("gap")
    ) {
      return "is-warning";
    }

    return "";
  }

  function renderGapSummary() {
    const unresolvedCount = state.knowledgeGaps.filter(
      (gap) => !toBoolean(gap.resolved)
    ).length;

    elements.unresolvedGapCount.textContent =
      formatInteger(unresolvedCount);

    clearCardState(elements.gapSummaryCard);

    if (unresolvedCount === 0) {
      elements.gapSummaryCard.classList.add("is-success");
    } else {
      elements.gapSummaryCard.classList.add("is-warning");
    }
  }

  function renderGapSummaryError() {
    elements.unresolvedGapCount.textContent = "-";
    clearCardState(elements.gapSummaryCard);
    elements.gapSummaryCard.classList.add("is-danger");
  }

  function renderKnowledgeGaps() {
    const statusFilter = elements.gapStatusFilter.value;
    const query = elements.gapSearchInput.value
      .trim()
      .toLowerCase();

    const filteredGaps = state.knowledgeGaps
      .filter((gap) => {
        const resolved = toBoolean(gap.resolved);

        if (statusFilter === "resolved" && !resolved) {
          return false;
        }

        if (statusFilter === "unresolved" && resolved) {
          return false;
        }

        if (!query) {
          return true;
        }

        const searchableText = [
          gap.id,
          gap.question,
          gap.trace_id
        ]
          .map((value) => String(value ?? "").toLowerCase())
          .join(" ");

        return searchableText.includes(query);
      })
      .sort(compareKnowledgeGaps);

    elements.gapsPanelCount.textContent =
      `전체 ${formatInteger(state.knowledgeGaps.length)}건 · ` +
      `표시 ${formatInteger(filteredGaps.length)}건`;

    elements.knowledgeGapList.replaceChildren();

    if (filteredGaps.length === 0) {
      elements.knowledgeGapList.appendChild(
        createEmptyState(
          "조건에 맞는 지식갭이 없습니다.",
          "필터나 검색어를 변경해보세요."
        )
      );
      return;
    }

    const fragment = document.createDocumentFragment();

    for (const gap of filteredGaps) {
      fragment.appendChild(createKnowledgeGapItem(gap));
    }

    elements.knowledgeGapList.appendChild(fragment);
  }

  function renderKnowledgeGapsError() {
    elements.gapsPanelCount.textContent = "불러오기 실패";
    elements.knowledgeGapList.replaceChildren(
      createEmptyState(
        "지식갭을 불러오지 못했습니다.",
        "인증 상태와 API 연결을 확인해주세요."
      )
    );
  }

  function createKnowledgeGapItem(gap) {
    const resolved = toBoolean(gap.resolved);
    const item = document.createElement("li");
    item.className = "gap-item";

    const heading = document.createElement("div");
    heading.className = "gap-heading";

    const question = document.createElement("p");
    question.className = "gap-question";
    question.textContent =
      valueOrFallback(gap.question, "질문 내용 없음");

    const badge = document.createElement("span");
    badge.className = resolved
      ? "status-badge status-resolved"
      : "status-badge status-unresolved";
    badge.textContent = resolved ? "해결됨" : "미해결";

    heading.append(question, badge);

    const meta = document.createElement("div");
    meta.className = "gap-meta";

    const id = document.createElement("span");
    id.textContent = `ID ${valueOrFallback(gap.id)}`;

    const trace = document.createElement("code");
    trace.textContent =
      `trace ${valueOrFallback(gap.trace_id)}`;

    meta.append(id, trace);
    item.append(heading, meta);

    return item;
  }

  function compareKnowledgeGaps(a, b) {
    const resolvedDifference =
      Number(toBoolean(a.resolved)) -
      Number(toBoolean(b.resolved));

    if (resolvedDifference !== 0) {
      return resolvedDifference;
    }

    return compareIdsDescending(a.id, b.id);
  }

  function compareIdsDescending(a, b) {
    const numberA = Number(a);
    const numberB = Number(b);

    if (Number.isFinite(numberA) && Number.isFinite(numberB)) {
      return numberB - numberA;
    }

    return String(b ?? "").localeCompare(String(a ?? ""));
  }

  function createEmptyState(title, description = "") {
    const item = document.createElement("li");
    item.className = "empty-state";

    const strong = document.createElement("strong");
    strong.textContent = title;
    item.appendChild(strong);

    if (description) {
      const text = document.createElement("span");
      text.textContent = description;
      item.appendChild(text);
    }

    return item;
  }

  function showAuthenticationRequired() {
    synchronizeAuthentication();
    elements.authNotice.hidden = false;
    elements.lastUpdated.textContent = "로그인 필요";
    showError("관리자 인증 토큰이 없거나 만료되었습니다.");
  }

  function handlePossibleAuthenticationError(error) {
    const status = Number(error && error.status);

    if (status === 401) {
      showAuthenticationRequired();
      stopAutoRefresh();
    } else if (status === 403) {
      // 로그인은 됐지만 관리자가 아님 — "로그인하라"는 문구는 부정확하므로 구분한다.
      elements.authNotice.hidden = false;
      elements.authNotice.textContent =
        "관리자 권한이 없는 계정입니다. 관리자로 승격된 계정으로 다시 로그인해주세요.";
      elements.lastUpdated.textContent = "권한 없음";
      showError("관리자 권한이 없습니다(403). role은 매 요청 DB에서 조회되므로 강등되면 즉시 반영됩니다.");
      stopAutoRefresh();
    }
  }

  function showError(message) {
    elements.errorMessage.textContent = message;
    elements.errorBanner.hidden = false;
  }

  function hideError() {
    elements.errorBanner.hidden = true;
    elements.errorMessage.textContent = "";
  }

  function toBoolean(value) {
    if (typeof value === "boolean") {
      return value;
    }

    if (typeof value === "number") {
      return value !== 0;
    }

    return ["true", "1", "yes", "y", "resolved"].includes(
      String(value ?? "").trim().toLowerCase()
    );
  }

  function toFiniteNumber(value, fallback = 0) {
    const number = Number(value);
    return Number.isFinite(number) ? number : fallback;
  }

  function formatInteger(value) {
    return new Intl.NumberFormat("ko-KR", {
      maximumFractionDigits: 0
    }).format(toFiniteNumber(value));
  }

  function formatDateTime(date) {
    return new Intl.DateTimeFormat("ko-KR", {
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false
    }).format(date);
  }

  function valueOrFallback(value, fallback = "-") {
    if (value === null || value === undefined || value === "") {
      return fallback;
    }

    return String(value);
  }

  function compactValue(value) {
    if (typeof value === "string") {
      return value;
    }

    if (
      typeof value === "number" ||
      typeof value === "boolean"
    ) {
      return String(value);
    }

    try {
      return JSON.stringify(value);
    } catch {
      return String(value);
    }
  }

  function truncateText(value, maximumLength) {
    const text = String(value);

    if (text.length <= maximumLength) {
      return text;
    }

    return `${text.slice(0, maximumLength - 1)}…`;
  }

  function humanizeKey(key) {
    return String(key)
      .replace(/_/g, " ")
      .replace(/\b\w/g, (character) => character.toUpperCase());
  }

  function isTimestampKey(key) {
    return [
      "created_at",
      "timestamp",
      "occurred_at",
      "event_time",
      "time"
    ].includes(String(key).toLowerCase());
  }
})();
