// 마이페이지 — 얼굴 로그인 2차 인증 생애주기 (Phase 13)
// 로그인(2FA 분기) → [2FA 단계] → 로그인 완료 → 얼굴 등록/삭제.
// 웹캠 정지프레임 캡처(common.js captureFrameBlob) → multipart 업로드.
// 무폴백: 카메라 거부/인증 실패는 명시 메시지로 알리고 조용히 넘어가지 않는다.

const loginPanel = document.getElementById("loginPanel");
const twofaPanel = document.getElementById("twofaPanel");
const facePanel = document.getElementById("facePanel");

const loginUser = document.getElementById("loginUser");
const loginPass = document.getElementById("loginPass");
const loginBtn = document.getElementById("loginBtn");

const twofaVideo = document.getElementById("twofaVideo");
const twofaCaptureBtn = document.getElementById("twofaCaptureBtn");
const twofaCancelBtn = document.getElementById("twofaCancelBtn");
const twofaStatus = document.getElementById("twofaStatus");

const regVideo = document.getElementById("regVideo");
const regCameraBtn = document.getElementById("regCameraBtn");
const regCaptureBtn = document.getElementById("regCaptureBtn");
const regDeleteBtn = document.getElementById("regDeleteBtn");
const logoutBtn = document.getElementById("logoutBtn");
const faceStatusText = document.getElementById("faceStatusText");
const regStatus = document.getElementById("regStatus");

let challengeToken = null;
let pendingUser = null;
let twofaStream = null;
let regStream = null;

function stopStream(v, s) {
  if (s) s.getTracks().forEach((t) => t.stop());
  if (v) v.srcObject = null;
}

async function startStream(videoEl) {
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ video: true });
    videoEl.srcObject = stream;
    return stream;
  } catch (err) {
    // 무폴백: 사유별 안내
    let msg = "카메라를 열 수 없습니다: " + err.name;
    if (err.name === "NotAllowedError") msg = "카메라 권한이 거부되었습니다. 브라우저에서 허용해주세요.";
    else if (err.name === "NotFoundError") msg = "연결된 카메라를 찾을 수 없습니다.";
    else if (err.name === "NotReadableError") msg = "카메라가 다른 앱에서 사용 중입니다.";
    throw new Error(msg);
  }
}

// ---- 상태 전환 ----
function showLoggedOut() {
  loginPanel.hidden = false;
  twofaPanel.hidden = true;
  facePanel.hidden = true;
  stopStream(twofaVideo, twofaStream); twofaStream = null;
  stopStream(regVideo, regStream); regStream = null;
}

async function showLoggedIn() {
  loginPanel.hidden = true;
  twofaPanel.hidden = true;
  facePanel.hidden = false;
  stopStream(twofaVideo, twofaStream); twofaStream = null;
  await refreshFaceStatus();
}

async function showTwoFa(username, token) {
  pendingUser = username;
  challengeToken = token;
  loginPanel.hidden = true;
  facePanel.hidden = true;
  twofaPanel.hidden = false;
  twofaStatus.textContent = "카메라를 준비 중…";
  try {
    twofaStream = await startStream(twofaVideo);
    twofaStatus.textContent = "얼굴을 화면 중앙에 맞추고 촬영하세요.";
  } catch (err) {
    twofaStatus.textContent = "🎥 " + err.message;
  }
}

// ---- 로그인 ----
loginBtn.addEventListener("click", async () => {
  const username = loginUser.value.trim();
  const password = loginPass.value;
  if (!username || !password) return;
  loginBtn.disabled = true;
  try {
    const form = new URLSearchParams({ username, password });
    const { status, ok, body } = await apiFetch("/auth/login", { method: "POST", body: form });
    if (!ok) {
      regStatusToLogin(`로그인 실패 (HTTP ${status})`);
      return;
    }
    if (body.face_2fa_required) {
      await showTwoFa(username, body.challenge_token);
    } else {
      setAuth(body.access_token, username);
      updateAuthStatusBar();
      await showLoggedIn();
    }
  } catch (err) {
    regStatusToLogin("로그인 요청 실패: " + err.message);
  } finally {
    loginBtn.disabled = false;
  }
});

function regStatusToLogin(msg) {
  document.getElementById("loginHint").textContent = msg;
}

// ---- 2FA 촬영 인증 ----
twofaCaptureBtn.addEventListener("click", async () => {
  twofaCaptureBtn.disabled = true;
  twofaStatus.textContent = "얼굴 분석 중…";
  try {
    const blob = await captureFrameBlob(twofaVideo);
    if (!blob) { twofaStatus.textContent = "카메라 프레임을 캡처하지 못했습니다."; return; }
    const fd = new FormData();
    fd.append("image", blob, "face.jpg");
    const resp = await fetch("/auth/login/face", {
      method: "POST",
      headers: { Authorization: "Bearer " + challengeToken },
      body: fd,
    });
    const body = await resp.json().catch(() => null);
    if (resp.ok && body && body.access_token) {
      setAuth(body.access_token, pendingUser);
      updateAuthStatusBar();
      challengeToken = null;
      await showLoggedIn();
      regStatus.textContent = "얼굴 2차 인증 완료 — 로그인되었습니다.";
    } else {
      twofaStatus.textContent = (body && body.message) || `인증 실패 (HTTP ${resp.status})`;
    }
  } catch (err) {
    twofaStatus.textContent = "인증 요청 실패: " + err.message;
  } finally {
    twofaCaptureBtn.disabled = false;
  }
});

twofaCancelBtn.addEventListener("click", () => {
  challengeToken = null; pendingUser = null;
  showLoggedOut();
});

// ---- 얼굴 등록 상태/관리 ----
async function refreshFaceStatus() {
  const { ok, body } = await apiFetch("/api/face/status", { headers: authHeaders() });
  if (!ok) { faceStatusText.textContent = "상태 조회 실패"; return; }
  if (body.registered) {
    faceStatusText.textContent = "등록됨 ✅ (다음 로그인부터 얼굴 2차 인증 요구)";
    regDeleteBtn.hidden = false;
  } else {
    faceStatusText.textContent = "미등록 — 아래에서 얼굴을 등록할 수 있습니다.";
    regDeleteBtn.hidden = true;
  }
}

regCameraBtn.addEventListener("click", async () => {
  regStatus.textContent = "카메라 준비 중…";
  try {
    regStream = await startStream(regVideo);
    regCaptureBtn.hidden = false;
    regStatus.textContent = "얼굴을 화면 중앙에 맞추고 촬영해 등록하세요.";
  } catch (err) {
    regStatus.textContent = "🎥 " + err.message;
  }
});

const ENROLL_SHOTS = 3;  // 여러 샷을 품질 게이팅 후 임베딩 평균(견고성↑) — 백엔드와 맞춤

regCaptureBtn.addEventListener("click", async () => {
  regCaptureBtn.disabled = true;
  try {
    // 짧은 간격으로 여러 프레임 촬영(미세한 자세/표정 변화로 견고한 기준 임베딩).
    const fd = new FormData();
    for (let i = 0; i < ENROLL_SHOTS; i++) {
      regStatus.textContent = `촬영 중… (${i + 1}/${ENROLL_SHOTS}) 정면을 유지하세요.`;
      const blob = await captureFrameBlob(regVideo);
      if (blob) fd.append("images", blob, `face${i}.jpg`);
      await new Promise((r) => setTimeout(r, 500));
    }
    if (!fd.has("images")) { regStatus.textContent = "카메라 프레임을 캡처하지 못했습니다."; return; }
    regStatus.textContent = "얼굴 분석·등록 중…";
    const resp = await fetch("/api/face/register", { method: "POST", headers: authHeaders(), body: fd });
    const body = await resp.json().catch(() => null);
    if (resp.ok && body && body.registered) {
      regStatus.textContent = `등록 완료 (품질 통과 ${body.shots_used}/${body.shots_submitted}장 평균).`;
      await refreshFaceStatus();
    } else {
      regStatus.textContent = (body && body.message) || `등록 실패 (HTTP ${resp.status})`;
    }
  } catch (err) {
    regStatus.textContent = "등록 요청 실패: " + err.message;
  } finally {
    regCaptureBtn.disabled = false;
  }
});

regDeleteBtn.addEventListener("click", async () => {
  const { ok } = await apiFetch("/api/face/register", { method: "DELETE", headers: authHeaders() });
  if (ok) { regStatus.textContent = "얼굴 등록을 삭제했습니다."; await refreshFaceStatus(); }
});

logoutBtn.addEventListener("click", () => {
  clearAuth();
  updateAuthStatusBar();
  showLoggedOut();
});

window.addEventListener("pagehide", () => {
  stopStream(twofaVideo, twofaStream);
  stopStream(regVideo, regStream);
});

// 초기 상태: 토큰 있으면 로그인 상태로.
if (getToken()) {
  showLoggedIn();
} else {
  showLoggedOut();
}
