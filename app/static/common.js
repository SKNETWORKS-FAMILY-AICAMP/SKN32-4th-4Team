// 공용 인증/요청 헬퍼 — 데모 프론트엔드 전 페이지가 공유한다.
// 토큰은 localStorage에 저장(로컬 데모 서버 전제, 프로덕션 보안모델 아님).

// --- 브랜드명 단일 소스(프론트엔드) ---
// 이 값 하나만 바꾸면 모든 페이지 타이틀/헤더에 반영된다.
// 백엔드(프롬프트·API 타이틀)는 app/core/config.py의 BRAND_NAME이 대응하는 단일 소스다.
//
// ★쓸 수 있는 이름은 둘뿐이다 — 프로젝트명 "올바른 보험비서", 팀명 "비서단".
const BRAND_NAME = "올바른 보험비서";

// 타이틀의 {{BRAND}} 토큰과 [data-brand] 요소에 브랜드명을 주입한다.
function applyBrand() {
  if (document.title.includes("{{BRAND}}")) {
    document.title = document.title.split("{{BRAND}}").join(BRAND_NAME);
  }
  document.querySelectorAll("[data-brand]").forEach((el) => {
    el.textContent = BRAND_NAME;
  });
}
document.addEventListener("DOMContentLoaded", applyBrand);

const AUTH_KEY = "mall_demo_token";
const USER_KEY = "mall_demo_username";

function getToken() { return localStorage.getItem(AUTH_KEY) || ""; }
function getUsername() { return localStorage.getItem(USER_KEY) || ""; }
function setAuth(token, username) {
  localStorage.setItem(AUTH_KEY, token);
  localStorage.setItem(USER_KEY, username);
}
function clearAuth() {
  localStorage.removeItem(AUTH_KEY);
  localStorage.removeItem(USER_KEY);
}

function authHeaders(extra) {
  const h = Object.assign({}, extra || {});
  const t = getToken();
  if (t) h["Authorization"] = "Bearer " + t;
  return h;
}

async function apiFetch(path, opts) {
  opts = opts || {};
  const resp = await fetch(path, opts);
  let body;
  try { body = await resp.json(); } catch { body = null; }
  return { status: resp.status, ok: resp.ok, body };
}

function renderResult(el, status, body) {
  const badgeClass = status < 300 ? "ok" : status < 500 ? "warn" : "err";
  el.innerHTML = "";
  const badge = document.createElement("span");
  badge.className = "badge " + badgeClass;
  badge.textContent = "HTTP " + status;
  el.appendChild(badge);
  const pre = document.createElement("pre");
  pre.className = "result";
  pre.textContent = JSON.stringify(body, null, 2);
  el.appendChild(pre);
}

function updateAuthStatusBar() {
  const bar = document.getElementById("authStatusBar");
  if (!bar) return;
  const u = getUsername();
  bar.innerHTML = u
    ? `로그인됨: <strong>${u}</strong> · <a href="#" id="logoutLink">로그아웃</a>`
    : `로그인 안 됨 (각 페이지의 로그인 폼 이용)`;
  const logout = document.getElementById("logoutLink");
  if (logout) {
    logout.addEventListener("click", (e) => {
      e.preventDefault();
      clearAuth();
      updateAuthStatusBar();
    });
  }
}

document.addEventListener("DOMContentLoaded", updateAuthStatusBar);

// --- 음성 STT/TTS 공용 헬퍼 (Phase 11) ---
// STT/TTS는 /api/voice/*를 그대로 호출한다 — 페이지마다 따로 구현하지 않고 여기서 공유.

function transcribeBlob(blob) {
  const form = new FormData();
  form.append("audio", blob, "recording.webm");
  return apiFetch("/api/voice/stt", { method: "POST", body: form });
}

// 현재 재생 중인 TTS 오디오(새 응답 전 정리용) — 페이지 전역 단일 재생.
let _currentTtsAudio = null;

function stopCurrentTts() {
  if (_currentTtsAudio) {
    try { _currentTtsAudio.pause(); } catch { /* noop */ }
    _currentTtsAudio = null;
  }
}

// hooks: { onSpeakStart(), onSpeakEnd() } — 아바타 애니메이션 동기화용(선택).
// 애니메이션은 실제 'playing' 이벤트에서 시작하고 ended/pause/error/abort에서 모두 해제한다.
async function synthesizeAndPlay(text, hooks) {
  hooks = hooks || {};
  stopCurrentTts(); // 새 발화 전 이전 발화 중단(중복 재생 방지)

  const resp = await fetch("/api/voice/tts", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text }),
  });
  if (!resp.ok) {
    let body;
    try { body = await resp.json(); } catch { body = null; }
    const err = new Error((body && body.message) || `TTS 요청 실패: HTTP ${resp.status}`);
    err.status = resp.status;
    throw err;
  }
  const blob = await resp.blob();
  const url = URL.createObjectURL(blob);
  const audio = new Audio(url);
  _currentTtsAudio = audio;

  let ended = false;
  const release = () => {
    if (ended) return;
    ended = true;
    URL.revokeObjectURL(url);
    if (_currentTtsAudio === audio) _currentTtsAudio = null;
    if (typeof hooks.onSpeakEnd === "function") hooks.onSpeakEnd();
  };
  audio.addEventListener("playing", () => {
    if (typeof hooks.onSpeakStart === "function") hooks.onSpeakStart();
  });
  audio.addEventListener("ended", release);
  audio.addEventListener("pause", release);
  audio.addEventListener("error", release);
  audio.addEventListener("abort", release);

  await audio.play();
  return audio;
}

// 마이크 버튼 하나로 녹음 시작/종료를 토글하는 헬퍼.
// onResult(text)/onError(err)/onStateChange("idle"|"recording"|"transcribing")를 받는다.
// 마이크 권한 거부는 조용히 넘어가지 않고 onError로 명시 전달한다(무폴백).
function createVoiceRecorder({ onResult, onError, onStateChange }) {
  let mediaRecorder = null;
  let chunks = [];
  let recording = false;

  function setState(state) {
    if (onStateChange) onStateChange(state);
  }

  async function start() {
    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (err) {
      onError(new Error("마이크 권한이 거부되었거나 사용할 수 없습니다: " + err.message));
      return;
    }
    chunks = [];
    mediaRecorder = new MediaRecorder(stream);
    mediaRecorder.ondataavailable = (e) => { if (e.data.size > 0) chunks.push(e.data); };
    mediaRecorder.onstop = async () => {
      stream.getTracks().forEach((t) => t.stop());
      setState("transcribing");
      try {
        const blob = new Blob(chunks, { type: mediaRecorder.mimeType || "audio/webm" });
        const { status, ok, body } = await transcribeBlob(blob);
        if (!ok) {
          throw new Error((body && body.message) || `STT 요청 실패: HTTP ${status}`);
        }
        onResult(body.text);
      } catch (err) {
        onError(err);
      } finally {
        setState("idle");
      }
    };
    mediaRecorder.start();
    recording = true;
    setState("recording");
  }

  function stop() {
    if (mediaRecorder && recording) {
      mediaRecorder.stop();
      recording = false;
    }
  }

  return {
    toggle() {
      if (recording) stop(); else start();
    },
    isRecording() { return recording; },
  };
}

// --- 웹캠 정지 프레임 캡처(Phase 13 얼굴 등록/인증 공용) ---
// videoEl의 현재 프레임을 JPEG Blob으로 캡처한다. 스트림이 없으면 null.
function captureFrameBlob(videoEl) {
  if (!videoEl || !videoEl.videoWidth) return Promise.resolve(null);
  const canvas = document.createElement("canvas");
  canvas.width = videoEl.videoWidth;
  canvas.height = videoEl.videoHeight;
  canvas.getContext("2d").drawImage(videoEl, 0, 0);
  return new Promise((resolve) => canvas.toBlob((b) => resolve(b), "image/jpeg", 0.92));
}

// --- 공통 로그인 + 얼굴 2차 인증(Phase 13) ---
// 모든 페이지의 로그인 폼이 재사용한다. 얼굴 등록 계정이면 웹캠 오버레이로 2차 인증까지 마치고
// 최종 토큰을 돌려준다. 무폴백: 2차 인증 실패 시 토큰을 발급하지 않고 reject.
//
// submitLogin(username, password) -> {token, username}  (실패 시 throw)

function _face2faOverlay(challengeToken) {
  return new Promise((resolve, reject) => {
    const ov = document.createElement("div");
    ov.className = "twofa-overlay";
    ov.innerHTML =
      '<div class="twofa-box">' +
      '<h3>🙂 얼굴 2차 인증</h3>' +
      '<p class="twofa-hint">등록한 얼굴로 본인 확인을 완료해야 로그인이 끝납니다.</p>' +
      '<div class="twofa-cam"><video autoplay playsinline muted></video></div>' +
      '<p class="twofa-status"></p>' +
      '<div class="twofa-actions">' +
      '<button class="twofa-shot" type="button">📸 촬영해서 인증</button>' +
      '<button class="twofa-cancel secondary" type="button">취소</button>' +
      "</div></div>";
    document.body.appendChild(ov);
    const video = ov.querySelector("video");
    const statusEl = ov.querySelector(".twofa-status");
    const shotBtn = ov.querySelector(".twofa-shot");
    let stream = null;

    function cleanup() {
      if (stream) stream.getTracks().forEach((t) => t.stop());
      ov.remove();
    }

    navigator.mediaDevices.getUserMedia({ video: true })
      .then((s) => { stream = s; video.srcObject = s; statusEl.textContent = "얼굴을 중앙에 맞추고 촬영하세요."; })
      .catch((err) => {
        let m = "카메라를 열 수 없습니다: " + err.name;
        if (err.name === "NotAllowedError") m = "카메라 권한이 거부되었습니다.";
        statusEl.textContent = m;
      });

    shotBtn.addEventListener("click", async () => {
      shotBtn.disabled = true;
      statusEl.textContent = "얼굴 분석 중…";
      try {
        const blob = await captureFrameBlob(video);
        if (!blob) { statusEl.textContent = "프레임 캡처 실패"; shotBtn.disabled = false; return; }
        const fd = new FormData();
        fd.append("image", blob, "face.jpg");
        const resp = await fetch("/auth/login/face", {
          method: "POST", headers: { Authorization: "Bearer " + challengeToken }, body: fd,
        });
        const body = await resp.json().catch(() => null);
        if (resp.ok && body && body.access_token) {
          cleanup();
          resolve(body.access_token);
        } else {
          statusEl.textContent = (body && body.message) || `인증 실패 (HTTP ${resp.status})`;
          shotBtn.disabled = false;
        }
      } catch (err) {
        statusEl.textContent = "요청 실패: " + err.message;
        shotBtn.disabled = false;
      }
    });
    ov.querySelector(".twofa-cancel").addEventListener("click", () => {
      cleanup();
      reject(new Error("얼굴 2차 인증이 취소되었습니다."));
    });
  });
}

async function submitLogin(username, password) {
  const form = new URLSearchParams({ username, password });
  const { status, ok, body } = await apiFetch("/auth/login", { method: "POST", body: form });
  if (!ok) {
    const e = new Error((body && body.message) || `로그인 실패 (HTTP ${status})`);
    e.status = status;
    throw e;
  }
  if (body.face_2fa_required) {
    const token = await _face2faOverlay(body.challenge_token); // 실패/취소 시 throw
    setAuth(token, username);
    return { token, username };
  }
  setAuth(body.access_token, username);
  return { token: body.access_token, username };
}
