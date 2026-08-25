/* 일렁이는 파티클 배경 — React Three Fiber 원본(maketemp/skal-ventures-template)의
 * GLSL 노이즈·포인트 셰이더를 순수 WebGL2로 옮긴 것. Three.js/React 의존성이 없다.
 *
 * 원본은 프레임마다 위치를 텍스처(FBO)에 렌더링해서 다음 프레임이 읽는 구조였지만,
 * 그 텍스처는 "이전 프레임 결과"가 아니라 매번 "원래 격자 위치 + 그 순간의 노이즈"만
 * 계산하는 무상태(stateless) 연산이다 — 그래서 정점 셰이더 안에서 그 자리에 바로
 * 계산해도 결과가 같고, FBO 왕복을 통째로 없앨 수 있다.
 */
'use strict';

const NOISE_GLSL = `
float periodicNoise(vec3 p, float time) {
  float noise = 0.0;
  noise += sin(p.x * 2.0 + time) * cos(p.z * 1.5 + time);
  noise += sin(p.x * 3.2 + time * 2.0) * cos(p.z * 2.1 + time) * 0.6;
  noise += sin(p.x * 1.7 + time) * cos(p.z * 2.8 + time * 3.0) * 0.4;
  noise += sin(p.x * p.z * 0.5 + time * 2.0) * 0.3;
  return noise * 0.3;
}
`;

const VERTEX_SRC = `#version 300 es
in vec3 aPos;
uniform mat4 uProjection;
uniform mat4 uView;
uniform float uTime;
uniform float uNoiseScale;
uniform float uNoiseIntensity;
uniform float uTimeScale;
uniform float uLoopPeriod;
uniform float uFocus;
uniform float uBlur;
uniform float uPointSize;
out float vDistance;
out float vPosY;
out vec3 vWorldPosition;
out vec3 vInitialPosition;

${NOISE_GLSL}

void main() {
  vec3 originalPos = aPos;
  float continuousTime = uTime * uTimeScale * (6.28318530718 / uLoopPeriod);
  vec3 noiseInput = originalPos * uNoiseScale;
  float dx = periodicNoise(noiseInput, continuousTime);
  float dy = periodicNoise(noiseInput + vec3(50.0, 0.0, 0.0), continuousTime + 2.094);
  float dz = periodicNoise(noiseInput + vec3(0.0, 50.0, 0.0), continuousTime + 4.188);
  vec3 pos = originalPos + vec3(dx, dy, dz) * uNoiseIntensity;

  vec4 mvPosition = uView * vec4(pos, 1.0);
  gl_Position = uProjection * mvPosition;
  vDistance = abs(uFocus - (-mvPosition.z));
  vPosY = pos.y;
  vWorldPosition = pos;
  vInitialPosition = originalPos;
  gl_PointSize = max(vDistance * uBlur * uPointSize, 3.0);
}
`;

const FRAGMENT_SRC = `#version 300 es
precision highp float;
in float vDistance;
in float vPosY;
in vec3 vWorldPosition;
in vec3 vInitialPosition;
uniform float uOpacity;
uniform float uRevealFactor;
uniform float uRevealProgress;
uniform float uTime;
out vec4 fragColor;

${NOISE_GLSL}

float sparkleNoise(vec3 seed, float time) {
  float hash = fract(sin(seed.x * 127.1 + seed.y * 311.7 + seed.z * 74.7) * 43758.5453);
  float slowTime = time;
  float sparkle = 0.0;
  sparkle += sin(slowTime + hash * 6.28318) * 0.5;
  sparkle += sin(slowTime * 1.7 + hash * 12.56636) * 0.3;
  sparkle += sin(slowTime * 0.8 + hash * 18.84954) * 0.2;

  float hash2 = fract(sin(seed.x * 113.5 + seed.y * 271.9 + seed.z * 97.3) * 37849.3241);
  float sparkleMask = sin(hash2 * 6.28318) * 0.7 + sin(hash2 * 12.56636) * 0.3;
  if (sparkleMask < 0.3) sparkle *= 0.05;

  float normalizedSparkle = (sparkle + 1.0) * 0.5;
  float smoothCurve = pow(normalizedSparkle, 4.0);
  float blendFactor = normalizedSparkle * normalizedSparkle;
  float finalBrightness = mix(normalizedSparkle, smoothCurve, blendFactor);
  return 0.7 + finalBrightness * 1.3;
}

void main() {
  vec2 cxy = 2.0 * gl_PointCoord - 1.0;
  float sdf = length(cxy) - 0.5;
  if (sdf > 0.0) discard;

  float distanceFromCenter = length(vWorldPosition.xz);
  float noiseValue = periodicNoise(vInitialPosition * 4.0, 0.0);
  float revealThreshold = uRevealFactor + noiseValue * 0.3;
  float revealMask = 1.0 - smoothstep(revealThreshold - 0.2, revealThreshold + 0.1, distanceFromCenter);

  float sparkleBrightness = sparkleNoise(vInitialPosition, uTime);

  float alpha = (1.04 - clamp(vDistance, 0.0, 1.0))
    * clamp(smoothstep(-0.5, 0.25, vPosY), 0.0, 1.0)
    * uOpacity * revealMask * uRevealProgress * sparkleBrightness;

  fragColor = vec4(vec3(1.0), alpha);
}
`;

function compileShader(gl, type, src) {
  const sh = gl.createShader(type);
  gl.shaderSource(sh, src);
  gl.compileShader(sh);
  if (!gl.getShaderParameter(sh, gl.COMPILE_STATUS)) {
    const log = gl.getShaderInfoLog(sh);
    gl.deleteShader(sh);
    throw new Error('셰이더 컴파일 실패: ' + log);
  }
  return sh;
}

function createProgram(gl, vsSrc, fsSrc) {
  const vs = compileShader(gl, gl.VERTEX_SHADER, vsSrc);
  const fs = compileShader(gl, gl.FRAGMENT_SHADER, fsSrc);
  const prog = gl.createProgram();
  gl.attachShader(prog, vs);
  gl.attachShader(prog, fs);
  gl.linkProgram(prog);
  if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) {
    const log = gl.getProgramInfoLog(prog);
    gl.deleteProgram(prog);
    throw new Error('셰이더 프로그램 링크 실패: ' + log);
  }
  return prog;
}

// 열 우선(column-major) mat4 — WebGL 관례 그대로.
function perspective(fovyDeg, aspect, near, far) {
  const f = 1.0 / Math.tan((fovyDeg * Math.PI) / 360);
  const out = new Float32Array(16);
  out[0] = f / aspect;
  out[5] = f;
  out[10] = (far + near) / (near - far);
  out[11] = -1;
  out[14] = (2 * far * near) / (near - far);
  return out;
}

function lookAt(eye, center, up) {
  const [ex, ey, ez] = eye;
  let zx = ex - center[0], zy = ey - center[1], zz = ez - center[2];
  let zl = Math.hypot(zx, zy, zz) || 1; zx /= zl; zy /= zl; zz /= zl;

  let xx = up[1] * zz - up[2] * zy;
  let xy = up[2] * zx - up[0] * zz;
  let xz = up[0] * zy - up[1] * zx;
  let xl = Math.hypot(xx, xy, xz) || 1; xx /= xl; xy /= xl; xz /= xl;

  const yx = zy * xz - zz * xy;
  const yy = zz * xx - zx * xz;
  const yz = zx * xy - zy * xx;

  const out = new Float32Array(16);
  out[0] = xx; out[1] = yx; out[2] = zx; out[3] = 0;
  out[4] = xy; out[5] = yy; out[6] = zy; out[7] = 0;
  out[8] = xz; out[9] = yz; out[10] = zz; out[11] = 0;
  out[12] = -(xx * ex + xy * ey + xz * ez);
  out[13] = -(yx * ex + yy * ey + yz * ez);
  out[14] = -(zx * ex + zy * ey + zz * ez);
  out[15] = 1;
  return out;
}

function buildGrid(size, scale) {
  const positions = new Float32Array(size * size * 3);
  for (let i = 0; i < size; i++) {
    for (let j = 0; j < size; j++) {
      const idx = (i * size + j) * 3;
      const x = (j / (size - 1) - 0.5) * 2 * scale;
      const z = (i / (size - 1) - 0.5) * 2 * scale;
      positions[idx + 0] = x;
      positions[idx + 1] = 0;
      positions[idx + 2] = z;
    }
  }
  return positions;
}

/**
 * @param {HTMLCanvasElement} canvas
 * @param {object} [opts]
 */
export function initParticleBackground(canvas, opts = {}) {
  const gl = canvas.getContext('webgl2', { antialias: true, alpha: false });
  if (!gl) return null; // WebGL2 미지원 — 호출부가 정적 배경으로 폴백한다.

  const config = {
    gridSize: 220,
    planeScale: 10.0,
    noiseScale: 0.6,
    noiseIntensity: 0.52,
    timeScale: 1.0,
    focus: 3.8,
    aperture: 1.79,
    pointSize: 10.0,
    opacity: 0.8,
    loopPeriod: 24.0,
    cameraPos: [1.2629783123314589, 2.664606471394044, -1.8178993743288914],
    revealDuration: 3.5,
    ...opts,
  };

  const program = createProgram(gl, VERTEX_SRC, FRAGMENT_SRC);
  const positions = buildGrid(config.gridSize, config.planeScale);

  const vao = gl.createVertexArray();
  gl.bindVertexArray(vao);
  const buf = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, buf);
  gl.bufferData(gl.ARRAY_BUFFER, positions, gl.STATIC_DRAW);
  const aPos = gl.getAttribLocation(program, 'aPos');
  gl.enableVertexAttribArray(aPos);
  gl.vertexAttribPointer(aPos, 3, gl.FLOAT, false, 0, 0);
  gl.bindVertexArray(null);

  const u = {};
  ['uProjection', 'uView', 'uTime', 'uNoiseScale', 'uNoiseIntensity', 'uTimeScale',
    'uLoopPeriod', 'uFocus', 'uBlur', 'uPointSize', 'uOpacity', 'uRevealFactor',
    'uRevealProgress',
  ].forEach((name) => { u[name] = gl.getUniformLocation(program, name); });

  const view = lookAt(config.cameraPos, [0, 0, 0], [0, 1, 0]);

  let raf = null;
  let startTime = null;
  let disposed = false;

  function resize() {
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const w = Math.max(1, Math.round(canvas.clientWidth * dpr));
    const h = Math.max(1, Math.round(canvas.clientHeight * dpr));
    if (canvas.width !== w || canvas.height !== h) {
      canvas.width = w;
      canvas.height = h;
    }
    gl.viewport(0, 0, canvas.width, canvas.height);
  }

  gl.enable(gl.BLEND);
  gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
  gl.depthMask(false);
  gl.clearColor(0, 0, 0, 1);

  function frame(now) {
    if (disposed) return;
    if (startTime === null) startTime = now;
    const t = (now - startTime) / 1000;

    resize();
    gl.clear(gl.COLOR_BUFFER_BIT);

    const revealProgress = Math.min(t / config.revealDuration, 1.0);
    const eased = 1 - Math.pow(1 - revealProgress, 3);
    const revealFactor = eased * 4.0;

    const projection = perspective(50, canvas.width / canvas.height, 0.01, 300);

    gl.useProgram(program);
    gl.uniformMatrix4fv(u.uProjection, false, projection);
    gl.uniformMatrix4fv(u.uView, false, view);
    gl.uniform1f(u.uTime, t);
    gl.uniform1f(u.uNoiseScale, config.noiseScale);
    gl.uniform1f(u.uNoiseIntensity, config.noiseIntensity);
    gl.uniform1f(u.uTimeScale, config.timeScale);
    gl.uniform1f(u.uLoopPeriod, config.loopPeriod);
    gl.uniform1f(u.uFocus, config.focus);
    gl.uniform1f(u.uBlur, config.aperture);
    gl.uniform1f(u.uPointSize, config.pointSize);
    gl.uniform1f(u.uOpacity, config.opacity);
    gl.uniform1f(u.uRevealFactor, revealFactor);
    gl.uniform1f(u.uRevealProgress, eased);

    gl.bindVertexArray(vao);
    gl.drawArrays(gl.POINTS, 0, config.gridSize * config.gridSize);
    gl.bindVertexArray(null);

    raf = requestAnimationFrame(frame);
  }

  raf = requestAnimationFrame(frame);

  return {
    dispose() {
      disposed = true;
      if (raf) cancelAnimationFrame(raf);
      gl.deleteBuffer(buf);
      gl.deleteVertexArray(vao);
      gl.deleteProgram(program);
    },
  };
}
