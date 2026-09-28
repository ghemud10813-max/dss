// The 3D stage: holographic face, gaze rays, virtual monitor, cinematic camera.

import * as THREE from "three";
import { EffectComposer } from "three/addons/postprocessing/EffectComposer.js";
import { RenderPass } from "three/addons/postprocessing/RenderPass.js";
import { UnrealBloomPass } from "three/addons/postprocessing/UnrealBloomPass.js";
import { OutputPass } from "three/addons/postprocessing/OutputPass.js";

const EYES = [33, 7, 163, 144, 145, 153, 154, 155, 133, 173, 157, 158, 159, 160, 161, 246,
  362, 382, 381, 380, 374, 373, 390, 249, 263, 466, 388, 387, 386, 385, 384, 398];
const LIPS = [61, 146, 91, 181, 84, 17, 314, 405, 321, 375, 291, 409, 270, 269, 267, 0, 37, 39, 40, 185,
  78, 95, 88, 178, 87, 14, 317, 402, 318, 324, 308, 415, 310, 311, 312, 13, 82, 81, 80, 191];
const BROWS = [70, 63, 105, 66, 107, 55, 65, 52, 53, 46, 300, 293, 334, 296, 336, 285, 295, 282, 283, 276];
const IRIS_L = [468, 469, 470, 471, 472];
const IRIS_R = [473, 474, 475, 476, 477];

const SCREEN_W = 3.2;
const SCREEN_H = SCREEN_W * 9 / 16;

// Camera shots per view. The virtual monitor floats at z≈2.6 between the
// viewer and the face, so the default shot looks "through the pilot's glass".
export const VIEWS = {
  deck:      { pos: [2.5, 1.2, 10.4], look: [-0.3, 0.1, 1.3], fov: 32 },
  calibrate: { pos: [-5.2, 0.9, 5.6], look: [0.1, 0.05, 1.2], fov: 34 },
  gestures:  { pos: [-1.25, -0.3, 2.35], look: [0.05, -0.28, 0], fov: 40 },
  pointer:   { pos: [7.2, 1.8, 3.4], look: [0, 0.1, 1.3], fov: 36 },
  zones:     { pos: [0.35, 0.55, -3.4], look: [0, 0.15, 2.6], fov: 40 },
  trainer:   { pos: [-3.4, 0.7, 7.6], look: [0, 0.1, 1.6], fov: 34 },
  insights:  { pos: [-0.1, 0.3, -1.0], look: [0, 0.15, 2.6], fov: 46 },
  voice:     { pos: [-6.0, 1.8, 6.5], look: [0, 0.1, 1.0], fov: 34 },
  system:    { pos: [8.0, 4.5, 12.0], look: [0, 0.2, 1.3], fov: 32 },
};

const pointVS = /* glsl */`
  attribute float aKind;
  attribute float aRand;
  uniform float uTime, uScan, uDissolve, uSize, uPR;
  varying float vKind, vGlow, vAlpha;
  void main() {
    vec3 p = position;
    vec3 dir = normalize(p + vec3(0.0001, 0.0002, 0.0003));
    float d = uDissolve * (0.4 + aRand * 1.8);
    p += dir * d * 1.6 + vec3(sin(uTime * 2.0 + aRand * 40.0), aRand * 2.0, cos(uTime * 1.7 + aRand * 30.0)) * d * 0.35;
    vec4 mv = modelViewMatrix * vec4(p, 1.0);
    float scan = exp(-pow((position.y - uScan) * 5.0, 2.0));
    vGlow = scan;
    vKind = aKind;
    vAlpha = 1.0 - uDissolve * 0.8;
    float base = aKind > 1.5 && aKind < 2.5 ? 2.6 : (aKind > 0.5 ? 1.35 : 1.0);
    float tw = 0.85 + 0.15 * sin(uTime * 3.0 + aRand * 60.0);
    gl_PointSize = uSize * base * tw * (1.0 + scan * 1.6) * uPR / -mv.z;
    gl_Position = projectionMatrix * mv;
  }`;
const pointFS = /* glsl */`
  uniform vec3 uFace, uEye, uIris, uLip;
  varying float vKind, vGlow, vAlpha;
  void main() {
    vec2 c = gl_PointCoord - 0.5;
    float d = length(c);
    if (d > 0.5) discard;
    float a = pow(smoothstep(0.5, 0.0, d), 1.6);
    vec3 col = uFace;
    if (vKind > 0.5 && vKind < 1.5) col = uEye;
    else if (vKind > 1.5 && vKind < 2.5) col = uIris;
    else if (vKind > 2.5) col = uLip;
    col += vec3(0.55, 1.0, 0.95) * vGlow;
    gl_FragColor = vec4(col * a * (0.62 + vGlow), a * vAlpha);
  }`;
const lineVS = /* glsl */`
  uniform float uScan, uDissolve;
  varying float vGlow;
  void main() {
    vGlow = exp(-pow((position.y - uScan) * 4.0, 2.0));
    vec3 p = position * (1.0 + uDissolve * 0.6);
    gl_Position = projectionMatrix * modelViewMatrix * vec4(p, 1.0);
  }`;
const lineFS = /* glsl */`
  uniform vec3 uColor;
  uniform float uOpacity, uDissolve;
  varying float vGlow;
  void main() {
    gl_FragColor = vec4(uColor + vGlow * vec3(0.4, 0.9, 0.85), (uOpacity + vGlow * 0.35) * (1.0 - uDissolve));
  }`;

function glowTexture(inner = "rgba(255,255,255,1)", outer = "rgba(255,255,255,0)") {
  const c = document.createElement("canvas");
  c.width = c.height = 128;
  const g = c.getContext("2d");
  const gr = g.createRadialGradient(64, 64, 0, 64, 64, 64);
  gr.addColorStop(0, inner);
  gr.addColorStop(0.25, inner.replace(/[\d.]+\)$/, "0.55)"));
  gr.addColorStop(1, outer);
  g.fillStyle = gr;
  g.fillRect(0, 0, 128, 128);
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  return t;
}

function reticleTexture() {
  const c = document.createElement("canvas");
  c.width = c.height = 256;
  const g = c.getContext("2d");
  g.translate(128, 128);
  g.strokeStyle = "#8ff8ee";
  g.shadowColor = "#3ee8d8";
  g.shadowBlur = 12;
  g.lineWidth = 7;
  for (let i = 0; i < 4; i++) {
    g.beginPath();
    g.arc(0, 0, 88, i * Math.PI / 2 + 0.25, (i + 1) * Math.PI / 2 - 0.25);
    g.stroke();
  }
  g.lineWidth = 6;
  for (let i = 0; i < 4; i++) {
    g.save();
    g.rotate(i * Math.PI / 2);
    g.beginPath();
    g.moveTo(0, -120);
    g.lineTo(0, -100);
    g.stroke();
    g.restore();
  }
  g.fillStyle = "#e9fffd";
  g.beginPath();
  g.arc(0, 0, 9, 0, Math.PI * 2);
  g.fill();
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  return t;
}

const ease = (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);

export class Stage {
  constructor(canvas) {
    this.canvas = canvas;
    this.renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: false, powerPreference: "high-performance" });
    this.renderer.setClearColor(0x03060a, 1);
    this.quality = "high";
    this.bloomStrength = 1.0;
    this.reducedMotion = false;
    this.scene = new THREE.Scene();
    this.scene.fog = new THREE.FogExp2(0x03060a, 0.035);
    this.camera = new THREE.PerspectiveCamera(42, 1, 0.05, 200);
    this.clock = new THREE.Clock();
    this.time = 0;
    this.mouse = new THREE.Vector2();
    this.cam = { pos: new THREE.Vector3(...VIEWS.deck.pos), look: new THREE.Vector3(...VIEWS.deck.look), fov: 42 };
    this.camFrom = null;
    this.camTo = null;
    this.camT = 1;
    this.frame = null;
    this.targetMesh = null;
    this.dissolve = 1;
    this.dissolveTarget = 0.0;
    this.heatOpacity = 0.15;
    this.screenFocus = 0;
    this.ripples = [];
    this.gazeTrail = [];
    this.anchors = {};
    this.paused = false;
    this.autoLow = false;
    this.noAuto = new URLSearchParams(location.search).has("hq");
    this._slow = 0;
    this._frames = 0;
    this._buildWorld();
    this._buildScreen();
    this._buildPost();
    this.resize();
    window.addEventListener("resize", () => this.resize());
    window.addEventListener("pointermove", (e) => {
      this.mouse.set(e.clientX / window.innerWidth - 0.5, e.clientY / window.innerHeight - 0.5);
    });
    this.camera.position.copy(this.cam.pos);
    this.camera.lookAt(this.cam.look);
  }

  // ---------------------------------------------------------------- build

  _buildWorld() {
    const s = this.scene;
    // starfield
    const N = 2600;
    const pos = new Float32Array(N * 3);
    const rnd = new Float32Array(N);
    for (let i = 0; i < N; i++) {
      const r = 40 + Math.random() * 60;
      const th = Math.random() * Math.PI * 2;
      const ph = Math.acos(2 * Math.random() - 1);
      pos.set([r * Math.sin(ph) * Math.cos(th), r * Math.cos(ph) * 0.6, r * Math.sin(ph) * Math.sin(th)], i * 3);
      rnd[i] = Math.random();
    }
    const sg = new THREE.BufferGeometry();
    sg.setAttribute("position", new THREE.BufferAttribute(pos, 3));
    sg.setAttribute("aRand", new THREE.BufferAttribute(rnd, 1));
    this.starMat = new THREE.ShaderMaterial({
      uniforms: { uTime: { value: 0 }, uPR: { value: 1 } },
      vertexShader: `attribute float aRand; uniform float uTime, uPR; varying float vA;
        void main(){ vec4 mv = modelViewMatrix * vec4(position,1.0); vA = 0.35 + 0.65*abs(sin(uTime*0.6 + aRand*50.0));
        gl_PointSize = (1.0 + aRand*2.2) * uPR; gl_Position = projectionMatrix * mv; }`,
      fragmentShader: `varying float vA; void main(){ float d = length(gl_PointCoord-0.5); if(d>0.5) discard;
        gl_FragColor = vec4(vec3(0.55,0.85,0.95)*vA, vA*smoothstep(0.5,0.0,d)); }`,
      transparent: true, depthWrite: false, blending: THREE.AdditiveBlending, fog: false,
    });
    this.stars = new THREE.Points(sg, this.starMat);
    s.add(this.stars);

    // floor grid
    const gmat = new THREE.ShaderMaterial({
      uniforms: { uTime: { value: 0 }, uColor: { value: new THREE.Color(0x3ee8d8) } },
      vertexShader: `varying vec3 vW; void main(){ vec4 w = modelMatrix*vec4(position,1.0); vW = w.xyz; gl_Position = projectionMatrix*viewMatrix*w; }`,
      fragmentShader: `uniform float uTime; uniform vec3 uColor; varying vec3 vW;
        float line(float x, float w){ float f = abs(fract(x)-0.5); return smoothstep(w, 0.0, 0.5 - f); }
        void main(){
          vec2 p = vW.xz * 0.5; p.y += uTime*0.15;
          float g = max(line(p.x, 0.03), line(p.y, 0.03));
          float g2 = max(line(p.x*0.2, 0.012), line(p.y*0.2, 0.012));
          float fade = smoothstep(26.0, 2.0, length(vW.xz - vec2(0.0, 2.0)));
          float a = (g*0.35 + g2*0.6) * fade;
          gl_FragColor = vec4(uColor*a, a);
        }`,
      transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
    });
    this.gridMat = gmat;
    const floor = new THREE.Mesh(new THREE.PlaneGeometry(80, 80), gmat);
    floor.rotation.x = -Math.PI / 2;
    floor.position.y = -2.6;
    s.add(floor);

    // dust
    const D = 500;
    const dp = new Float32Array(D * 3);
    for (let i = 0; i < D; i++) dp.set([(Math.random() - 0.5) * 16, (Math.random() - 0.5) * 8, (Math.random() - 0.3) * 14], i * 3);
    const dg = new THREE.BufferGeometry();
    dg.setAttribute("position", new THREE.BufferAttribute(dp, 3));
    this.dust = new THREE.Points(dg, new THREE.PointsMaterial({ color: 0x7ff5ea, size: 0.022, transparent: true, opacity: 0.45,
      depthWrite: false, blending: THREE.AdditiveBlending }));
    s.add(this.dust);

    // face
    this.face = new THREE.Group();
    s.add(this.face);
    const P = 478;
    const fpos = new Float32Array(P * 3);
    const kind = new Float32Array(P);
    const rr = new Float32Array(P);
    for (let i = 0; i < P; i++) rr[i] = Math.random();
    EYES.forEach((i) => (kind[i] = 1));
    BROWS.forEach((i) => (kind[i] = 1));
    [...IRIS_L, ...IRIS_R].forEach((i) => (kind[i] = 2));
    LIPS.forEach((i) => (kind[i] = 3));
    this.meshPos = fpos;
    this.fgeo = new THREE.BufferGeometry();
    this.fgeo.setAttribute("position", new THREE.BufferAttribute(fpos, 3));
    this.fgeo.setAttribute("aKind", new THREE.BufferAttribute(kind, 1));
    this.fgeo.setAttribute("aRand", new THREE.BufferAttribute(rr, 1));
    this.faceUniforms = {
      uTime: { value: 0 }, uScan: { value: 0 }, uDissolve: { value: 1 }, uSize: { value: 16 }, uPR: { value: 1 },
      uFace: { value: new THREE.Color(0x2fb9b0) }, uEye: { value: new THREE.Color(0x7ff5ea) },
      uIris: { value: new THREE.Color(0xd9ccff) }, uLip: { value: new THREE.Color(0x9b7cff) },
    };
    this.facePoints = new THREE.Points(this.fgeo, new THREE.ShaderMaterial({
      uniforms: this.faceUniforms, vertexShader: pointVS, fragmentShader: pointFS,
      transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
    }));
    this.face.add(this.facePoints);
    this.lineUniforms = { uScan: this.faceUniforms.uScan, uDissolve: this.faceUniforms.uDissolve,
      uColor: { value: new THREE.Color(0x1d6f8a) }, uOpacity: { value: 0.12 } };
    this.faceLines = null;

    // iris glows
    const glow = glowTexture("rgba(200,190,255,1)");
    this.irisGlow = [0, 1].map(() => {
      const sp = new THREE.Sprite(new THREE.SpriteMaterial({ map: glow, color: 0xcbb8ff, transparent: true,
        blending: THREE.AdditiveBlending, depthWrite: false }));
      sp.scale.set(0.34, 0.34, 1);
      this.face.add(sp);
      return sp;
    });

    // gimbal rings around the head
    this.gimbal = new THREE.Group();
    const ringMat = (c, o) => new THREE.MeshBasicMaterial({ color: c, transparent: true, opacity: o, blending: THREE.AdditiveBlending,
      depthWrite: false, side: THREE.DoubleSide });
    const mk = (r, t, c, o) => new THREE.Mesh(new THREE.TorusGeometry(r, t, 6, 160), ringMat(c, o));
    this.ringYaw = mk(1.55, 0.005, 0x3ee8d8, 0.35);
    this.ringYaw.rotation.x = Math.PI / 2;
    this.ringPitch = mk(1.66, 0.004, 0x9b7cff, 0.28);
    this.ringPitch.rotation.y = Math.PI / 2;
    this.ringRoll = mk(1.77, 0.003, 0x3ee8d8, 0.18);
    // tick marks on the yaw ring
    const ticks = new THREE.Group();
    for (let i = 0; i < 72; i++) {
      const a = (i / 72) * Math.PI * 2;
      const len = i % 6 === 0 ? 0.12 : 0.05;
      const g = new THREE.BufferGeometry().setFromPoints([
        new THREE.Vector3(Math.cos(a) * 1.55, 0, Math.sin(a) * 1.55),
        new THREE.Vector3(Math.cos(a) * (1.55 + len), 0, Math.sin(a) * (1.55 + len))]);
      ticks.add(new THREE.Line(g, new THREE.LineBasicMaterial({ color: 0x3ee8d8, transparent: true, opacity: 0.35 })));
    }
    this.yawGroup = new THREE.Group();
    this.yawGroup.add(this.ringYaw, ticks);
    this.gimbal.add(this.yawGroup, this.ringPitch, this.ringRoll);
    this.gimbal.position.set(0, 0.05, -0.35);
    s.add(this.gimbal);

    // gaze rays: flowing particles + faint lines
    const R = 36;
    this.rayN = R;
    this.rays = [0, 1].map(() => {
      const g = new THREE.BufferGeometry();
      g.setAttribute("position", new THREE.BufferAttribute(new Float32Array(R * 3), 3));
      const pts = new THREE.Points(g, new THREE.PointsMaterial({ color: 0xb9a8ff, size: 0.05, map: glow, transparent: true,
        depthWrite: false, blending: THREE.AdditiveBlending }));
      const lg = new THREE.BufferGeometry();
      lg.setAttribute("position", new THREE.BufferAttribute(new Float32Array(6), 3));
      const line = new THREE.Line(lg, new THREE.LineBasicMaterial({ color: 0x9b7cff, transparent: true, opacity: 0.35,
        blending: THREE.AdditiveBlending, depthWrite: false }));
      s.add(pts, line);
      return { pts, line };
    });
  }

  _buildScreen() {
    const g = new THREE.Group();
    g.position.set(0, 0.12, 2.6);
    this.screen = g;
    this.scene.add(g);
    const W = SCREEN_W, H = SCREEN_H;
    const glass = new THREE.Mesh(new THREE.PlaneGeometry(W, H),
      new THREE.MeshBasicMaterial({ color: 0x0a2a36, transparent: true, opacity: 0.12, side: THREE.DoubleSide, depthWrite: false }));
    g.add(glass);
    // frame + brackets
    const frame = new THREE.LineLoop(new THREE.BufferGeometry().setFromPoints([
      new THREE.Vector3(-W / 2, -H / 2, 0), new THREE.Vector3(W / 2, -H / 2, 0),
      new THREE.Vector3(W / 2, H / 2, 0), new THREE.Vector3(-W / 2, H / 2, 0)]),
    new THREE.LineBasicMaterial({ color: 0x3ee8d8, transparent: true, opacity: 0.55 }));
    g.add(frame);
    const bm = new THREE.LineBasicMaterial({ color: 0x8ff8ee });
    const b = 0.22;
    for (const [sx, sy] of [[-1, -1], [1, -1], [1, 1], [-1, 1]]) {
      const x = sx * (W / 2 + 0.06), y = sy * (H / 2 + 0.06);
      g.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints([
        new THREE.Vector3(x - sx * b, y, 0.01), new THREE.Vector3(x, y, 0.01), new THREE.Vector3(x, y - sy * b, 0.01)]), bm));
    }
    // grid
    const gl = [];
    for (let i = 1; i < 16; i++) { const x = -W / 2 + (W * i) / 16; gl.push(new THREE.Vector3(x, -H / 2, 0), new THREE.Vector3(x, H / 2, 0)); }
    for (let i = 1; i < 9; i++) { const y = -H / 2 + (H * i) / 9; gl.push(new THREE.Vector3(-W / 2, y, 0), new THREE.Vector3(W / 2, y, 0)); }
    g.add(new THREE.LineSegments(new THREE.BufferGeometry().setFromPoints(gl),
      new THREE.LineBasicMaterial({ color: 0x3ee8d8, transparent: true, opacity: 0.06 })));
    // heatmap
    this.heatCanvas = document.createElement("canvas");
    this.heatCanvas.width = 48;
    this.heatCanvas.height = 27;
    this.heatTex = new THREE.CanvasTexture(this.heatCanvas);
    this.heatTex.colorSpace = THREE.SRGBColorSpace;
    this.heatTex.magFilter = THREE.LinearFilter;
    this.heatMat = new THREE.MeshBasicMaterial({ map: this.heatTex, transparent: true, opacity: 0.15, blending: THREE.AdditiveBlending,
      depthWrite: false, side: THREE.DoubleSide });
    const heat = new THREE.Mesh(new THREE.PlaneGeometry(W, H), this.heatMat);
    heat.position.z = 0.005;
    g.add(heat);
    // zones
    this.zoneMeshes = {};
    const zt = 0.07;
    const zdefs = {
      top: [0, H / 2 + zt / 2 + 0.03, W * 0.6, zt], bottom: [0, -H / 2 - zt / 2 - 0.03, W * 0.6, zt],
      left: [-W / 2 - zt / 2 - 0.03, 0, zt, H * 0.6], right: [W / 2 + zt / 2 + 0.03, 0, zt, H * 0.6],
      top_left: [-W / 2 - 0.04, H / 2 + 0.04, 0.2, 0.2], top_right: [W / 2 + 0.04, H / 2 + 0.04, 0.2, 0.2],
      bottom_left: [-W / 2 - 0.04, -H / 2 - 0.04, 0.2, 0.2], bottom_right: [W / 2 + 0.04, -H / 2 - 0.04, 0.2, 0.2],
    };
    for (const [k, [x, y, w, h]] of Object.entries(zdefs)) {
      const m = new THREE.Mesh(new THREE.PlaneGeometry(w, h), new THREE.MeshBasicMaterial({ color: 0x3ee8d8, transparent: true,
        opacity: 0, blending: THREE.AdditiveBlending, depthWrite: false, side: THREE.DoubleSide }));
      m.position.set(x, y, 0.02);
      g.add(m);
      this.zoneMeshes[k] = m;
    }
    // gaze glow + trail
    const glow = glowTexture("rgba(190,170,255,1)");
    this.gazeSprite = new THREE.Sprite(new THREE.SpriteMaterial({ map: glow, color: 0xc4b3ff, transparent: true,
      blending: THREE.AdditiveBlending, depthWrite: false }));
    this.gazeSprite.scale.set(0.5, 0.5, 1);
    g.add(this.gazeSprite);
    this.trailN = 40;
    const tg = new THREE.BufferGeometry();
    tg.setAttribute("position", new THREE.BufferAttribute(new Float32Array(this.trailN * 3), 3));
    this.trail = new THREE.Line(tg, new THREE.LineBasicMaterial({ color: 0x9b7cff, transparent: true, opacity: 0.5,
      blending: THREE.AdditiveBlending, depthWrite: false }));
    g.add(this.trail);
    // pointer reticle
    this.reticle = new THREE.Mesh(new THREE.PlaneGeometry(0.4, 0.4), new THREE.MeshBasicMaterial({ map: reticleTexture(),
      transparent: true, blending: THREE.AdditiveBlending, depthWrite: false, side: THREE.DoubleSide }));
    this.reticle.position.z = 0.03;
    g.add(this.reticle);
    this.dwellMesh = new THREE.Mesh(new THREE.RingGeometry(0.23, 0.26, 64, 1, 0, 0.001),
      new THREE.MeshBasicMaterial({ color: 0xffffff, transparent: true, opacity: 0.95, side: THREE.DoubleSide,
        blending: THREE.AdditiveBlending, depthWrite: false }));
    this.dwellMesh.position.z = 0.04;
    this.dwellShown = 0;
    g.add(this.dwellMesh);
    this.rippleGeo = new THREE.RingGeometry(0.13, 0.16, 64);
    // magnetic target lock: a bright rectangle on the virtual monitor
    this.lockRect = new THREE.LineLoop(new THREE.BufferGeometry().setFromPoints([
      new THREE.Vector3(-0.5, -0.5, 0), new THREE.Vector3(0.5, -0.5, 0), new THREE.Vector3(0.5, 0.5, 0), new THREE.Vector3(-0.5, 0.5, 0)]),
    new THREE.LineBasicMaterial({ color: 0x8ff8ee, transparent: true, opacity: 0.95, blending: THREE.AdditiveBlending, depthWrite: false }));
    this.lockRect.position.z = 0.045;
    this.lockRect.visible = false;
    g.add(this.lockRect);
  }

  _buildPost() {
    this.composer = new EffectComposer(this.renderer);
    this.composer.addPass(new RenderPass(this.scene, this.camera));
    this.bloom = new UnrealBloomPass(new THREE.Vector2(512, 512), 0.8, 0.5, 0.18);
    this.composer.addPass(this.bloom);
    this.composer.addPass(new OutputPass());
  }

  setMeshTopology(data) {
    const pts = data.points;
    for (let i = 0; i < pts.length && i < 478; i++) this.meshPos.set(pts[i], i * 3);
    this.fgeo.attributes.position.needsUpdate = true;
    this.canonical = Float32Array.from(this.meshPos);
    const idx = [];
    for (const [a, b] of data.edges) idx.push(a, b);
    const lg = new THREE.BufferGeometry();
    lg.setAttribute("position", this.fgeo.attributes.position);
    lg.setIndex(idx);
    this.faceLines = new THREE.LineSegments(lg, new THREE.ShaderMaterial({
      uniforms: this.lineUniforms, vertexShader: lineVS, fragmentShader: lineFS,
      transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
    }));
    this.face.add(this.faceLines);
  }

  // ---------------------------------------------------------------- inputs

  setQuality({ quality, bloom, reducedMotion }) {
    if (quality) this.quality = quality;
    if (bloom != null) this.bloomStrength = bloom;
    if (reducedMotion != null) this.reducedMotion = reducedMotion;
    this.resize();
  }

  setView(name, instant = false) {
    const v = VIEWS[name] || VIEWS.deck;
    this.view = name;
    this.camFrom = { pos: this.cam.pos.clone(), look: this.cam.look.clone(), fov: this.cam.fov };
    this.camTo = { pos: new THREE.Vector3(...v.pos), look: new THREE.Vector3(...v.look), fov: v.fov };
    this.camT = instant || this.reducedMotion ? 1 : 0;
    if (this.camT === 1) Object.assign(this.cam, { pos: this.camTo.pos.clone(), look: this.camTo.look.clone(), fov: this.camTo.fov });
    this.heatTarget = name === "insights" ? 0.85 : name === "zones" ? 0.35 : 0.15;
  }

  setFrame(f) {
    this.frame = f;
    if (f.mesh && f.mesh.length === 478 * 3) this.targetMesh = f.mesh;
    this.dissolveTarget = f.face ? 0 : 1;
    if (f.gaze) {
      this.gazeTrail.push(this._screenLocal(f.gaze));
      if (this.gazeTrail.length > this.trailN) this.gazeTrail.shift();
    }
    for (const ev of f.events || []) {
      if (ev.k === "click" && ev.p) this._ripple(ev.p, 0x3ee8d8);
      if (ev.k === "zone") this._zoneFlash = { zone: ev.zone, t: this.time };
    }
  }

  setHeat(heat) {
    if (!heat) return;
    const { w, h, data } = heat;
    const c = this.heatCanvas;
    if (c.width !== w) { c.width = w; c.height = h; }
    const g = c.getContext("2d");
    const img = g.createImageData(w, h);
    for (let i = 0; i < w * h; i++) {
      const v = data[i] / 255;
      const [r, gg, b] = v < 0.5 ? [62 + (155 - 62) * v * 2, 232 - (232 - 124) * v * 2, 216 + (255 - 216) * v * 2]
        : [155 + (255 - 155) * (v - 0.5) * 2, 124 + (181 - 124) * (v - 0.5) * 2, 255 - (255 - 71) * (v - 0.5) * 2];
      img.data.set([r, gg, b, Math.min(255, v * 340)], i * 4);
    }
    g.putImageData(img, 0, 0);
    this.heatTex.needsUpdate = true;
  }

  _screenLocal(p) {
    return new THREE.Vector3((p[0] - 0.5) * SCREEN_W, (0.5 - p[1]) * SCREEN_H, 0.02);
  }

  _ripple(p, color) {
    const m = new THREE.Mesh(this.rippleGeo, new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 1,
      blending: THREE.AdditiveBlending, depthWrite: false, side: THREE.DoubleSide }));
    m.position.copy(this._screenLocal(p));
    m.position.z = 0.05;
    this.screen.add(m);
    this.ripples.push({ m, t0: this.time });
  }

  // ---------------------------------------------------------------- frame

  get lowQuality() { return this.quality === "low" || this.autoLow; }

  _adapt(dt) {
    // Weak GPU (or software GL)? Drop bloom and pixel ratio once, automatically.
    if (this.autoLow || this.lowQuality || this.noAuto) return;
    this._frames++;
    if (this._frames < 90) return;
    this._slow = this._slow * 0.97 + (dt > 0.045 ? 1 : 0) * 0.03;
    if (this._slow > 0.6) {
      this.autoLow = true;
      this.resize();
      window.dispatchEvent(new CustomEvent("gazer-toast", { detail: { text: "Performance mode — bloom off for smoother tracking", kind: "warn" } }));
    }
  }

  resize() {
    const w = window.innerWidth, h = window.innerHeight;
    const pr = this.lowQuality ? 1 : Math.min(window.devicePixelRatio || 1, 2);
    this.renderer.setPixelRatio(pr);
    this.renderer.setSize(w, h, false);
    this.composer.setPixelRatio(pr);
    this.composer.setSize(w, h);
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
    this.faceUniforms.uPR.value = pr * (h / 900);
    this.starMat.uniforms.uPR.value = pr;
  }

  project(v) {
    const p = v.clone().project(this.camera);
    return { x: (p.x * 0.5 + 0.5) * window.innerWidth, y: (-p.y * 0.5 + 0.5) * window.innerHeight, visible: p.z < 1 };
  }

  render() {
    const rawDt = this.clock.getDelta();
    if (this.paused) return;
    this._adapt(rawDt);
    const dt = Math.min(rawDt, 0.1);
    this.time += dt;
    const t = this.time;
    const f = this.frame;

    // camera flight
    if (this.camT < 1 && this.camTo) {
      this.camT = Math.min(1, this.camT + dt / 1.7);
      const k = ease(this.camT);
      this.cam.pos.lerpVectors(this.camFrom.pos, this.camTo.pos, k);
      // arc: lift the camera mid-flight
      this.cam.pos.y += Math.sin(k * Math.PI) * 1.2;
      this.cam.look.lerpVectors(this.camFrom.look, this.camTo.look, k);
      this.cam.fov = this.camFrom.fov + (this.camTo.fov - this.camFrom.fov) * k;
    }
    const sway = this.reducedMotion ? 0 : 1;
    let base = this.cam.pos;
    if (this.view === "deck" && this.camT >= 1 && sway) {
      // slow cinematic orbit around the pilot: reveals depth and the gaze rays
      const ang = Math.sin(t * 0.06) * 0.42;
      const off = this.cam.pos.clone().sub(this.cam.look);
      off.applyAxisAngle(new THREE.Vector3(0, 1, 0), ang);
      base = this.cam.look.clone().add(off);
    }
    const cp = base.clone().add(new THREE.Vector3(
      Math.sin(t * 0.13) * 0.25 * sway + this.mouse.x * 0.6 * sway,
      Math.sin(t * 0.17) * 0.12 * sway - this.mouse.y * 0.35 * sway, 0));
    this.camera.position.copy(cp);
    this.camera.lookAt(this.cam.look);
    if (Math.abs(this.camera.fov - this.cam.fov) > 0.01) {
      this.camera.fov = this.cam.fov;
      this.camera.updateProjectionMatrix();
    }

    // face mesh: ease toward the latest landmarks (30 Hz data → 60 fps motion)
    const pos = this.meshPos;
    const src = this.targetMesh || (this.canonical && this._idleMesh(t));
    if (src) {
      const a = 1 - Math.exp(-dt * 22);
      for (let i = 0; i < pos.length; i++) pos[i] += (src[i] - pos[i]) * a;
      this.fgeo.attributes.position.needsUpdate = true;
    }
    this.dissolve += (this.dissolveTarget - this.dissolve) * (1 - Math.exp(-dt * (this.dissolveTarget > this.dissolve ? 3 : 5)));
    this.faceUniforms.uDissolve.value = this.dissolve;
    this.faceUniforms.uTime.value = t;
    this.faceUniforms.uScan.value = Math.sin(t * 0.9) * 1.5;
    this.starMat.uniforms.uTime.value = t;
    this.gridMat.uniforms.uTime.value = this.reducedMotion ? 0 : t;
    this.stars.rotation.y = t * 0.004;
    this.dust.rotation.y = t * 0.01;
    this.dust.position.y = Math.sin(t * 0.2) * 0.2;

    const irisL = new THREE.Vector3(pos[468 * 3], pos[468 * 3 + 1], pos[468 * 3 + 2]);
    const irisR = new THREE.Vector3(pos[473 * 3], pos[473 * 3 + 1], pos[473 * 3 + 2]);
    this.irisGlow[0].position.copy(irisL);
    this.irisGlow[1].position.copy(irisR);
    const closure = f?.closure || [0, 0];
    this.irisGlow[0].material.opacity = (1 - closure[0]) * (1 - this.dissolve);
    this.irisGlow[1].material.opacity = (1 - closure[1]) * (1 - this.dissolve);

    // gimbal follows head pose
    const hd = f?.head || [0, 0, 0];
    const rad = Math.PI / 180;
    this.yawGroup.rotation.y += ((-hd[0] * rad * 2) - this.yawGroup.rotation.y) * 0.15;
    this.ringPitch.rotation.x += ((hd[1] * rad * 2) - this.ringPitch.rotation.x) * 0.15;
    this.ringRoll.rotation.z += ((hd[2] * rad * 2) - this.ringRoll.rotation.z) * 0.15;
    this.gimbal.rotation.y = t * 0.05;

    // screen: gaze, trail, pointer, dwell, zones, heat
    this.screen.updateMatrixWorld();
    const gazeOn = !!(f && f.gaze && f.face);
    this.gazeSprite.visible = gazeOn;
    if (gazeOn) {
      const gl = this._screenLocal(f.gaze);
      this.gazeSprite.position.lerp(gl, 0.5);
      this.gazeSprite.material.opacity = 0.85 + Math.sin(t * 8) * 0.15;
    }
    const tp = this.trail.geometry.attributes.position;
    const tr = this.gazeTrail;
    for (let i = 0; i < this.trailN; i++) {
      const v = tr[Math.max(0, tr.length - this.trailN + i)] || this.gazeSprite.position;
      tp.setXYZ(i, v.x, v.y, 0.015);
    }
    tp.needsUpdate = true;
    this.trail.visible = gazeOn;

    const ptrOn = !!(f && f.pointer);
    this.reticle.visible = ptrOn;
    if (ptrOn) {
      const pl = this._screenLocal(f.pointer);
      pl.z = 0.03;
      this.reticle.position.lerp(pl, 0.45);
      this.reticle.rotation.z = t * (f.control ? 1.2 : 0.3);
      const col = f.paused ? 0xffb547 : f.control ? 0x8ff8ee : 0x4b6070;
      this.reticle.material.color.setHex(col);
      this.reticle.scale.setScalar(f.precision ? 0.6 : 1);
    }
    const tg = f && f.target;
    this.lockRect.visible = !!tg;
    if (tg) {
      const [x, y, w, hh] = tg.rect;
      const c = this._screenLocal([x + w / 2, y + hh / 2]);
      const pad = 1.15 + Math.sin(t * 6) * 0.05;
      this.lockRect.position.set(c.x, c.y, 0.045);
      this.lockRect.scale.set(Math.max(w * SCREEN_W * pad, 0.05), Math.max(hh * SCREEN_H * pad, 0.05), 1);
    }
    const dw = f && f.dwell_on && f.control ? f.dwell : 0;
    if (Math.abs(dw - this.dwellShown) > 0.015 || (dw === 0 && this.dwellShown !== 0)) {
      this.dwellShown = dw;
      this.dwellMesh.geometry.dispose();
      this.dwellMesh.geometry = new THREE.RingGeometry(0.23, 0.26, 64, 1, Math.PI / 2, -Math.max(0.001, dw * Math.PI * 2));
    }
    this.dwellMesh.position.x = this.reticle.position.x;
    this.dwellMesh.position.y = this.reticle.position.y;
    this.dwellMesh.visible = dw > 0.02;

    for (const [k, m] of Object.entries(this.zoneMeshes)) {
      let o = f && f.zones_on ? 0.08 : 0.0;
      if (f && f.zone && f.zone[0] === k) o = 0.25 + f.zone[1] * 0.6;
      if (this._zoneFlash && this._zoneFlash.zone === k) {
        const age = t - this._zoneFlash.t;
        if (age < 0.6) o = Math.max(o, 1 - age / 0.6);
      }
      m.material.opacity += (o - m.material.opacity) * 0.3;
    }
    this.heatMat.opacity += ((this.heatTarget ?? 0.15) - this.heatMat.opacity) * 0.05;

    for (const r of this.ripples) {
      const k = (t - r.t0) / 0.7;
      r.m.scale.setScalar(1 + k * 5);
      r.m.material.opacity = Math.max(0, 1 - k);
    }
    this.ripples = this.ripples.filter((r) => {
      if (t - r.t0 > 0.7) { this.screen.remove(r.m); r.m.material.dispose(); return false; }
      return true;
    });

    // rays from the irises to the gaze point
    const target = gazeOn ? this.gazeSprite.position.clone().applyMatrix4(this.screen.matrixWorld) : null;
    [irisL, irisR].forEach((eye, i) => {
      const ray = this.rays[i];
      const on = !!target && closure[i] < 0.5 && this.dissolve < 0.5;
      ray.pts.visible = ray.line.visible = on;
      if (!on) return;
      const lp = ray.line.geometry.attributes.position;
      lp.setXYZ(0, eye.x, eye.y, eye.z);
      lp.setXYZ(1, target.x, target.y, target.z);
      lp.needsUpdate = true;
      const pp = ray.pts.geometry.attributes.position;
      for (let j = 0; j < this.rayN; j++) {
        const k = ((j / this.rayN) + t * 0.55) % 1;
        pp.setXYZ(j, eye.x + (target.x - eye.x) * k, eye.y + (target.y - eye.y) * k, eye.z + (target.z - eye.z) * k);
      }
      pp.needsUpdate = true;
    });

    // callout anchors (screen space) for the DOM layer
    const head = new THREE.Vector3(-0.7, 1.05, 0);
    this.anchors = {
      head: this.project(head),
      eye: this.project(irisR),
      gaze: target ? this.project(target) : null,
      screen: this.project(new THREE.Vector3(SCREEN_W / 2, -SCREEN_H / 2, 0).applyMatrix4(this.screen.matrixWorld)),
    };

    this.bloom.strength = this.bloomStrength * 0.8;
    if (this.bloomStrength <= 0.01 || this.lowQuality) this.renderer.render(this.scene, this.camera);
    else this.composer.render();
  }

  _idleMesh(t) {
    // No live data yet: slowly turn the canonical face.
    if (!this._idle) this._idle = new Float32Array(this.canonical.length);
    const c = this.canonical, o = this._idle;
    const yaw = Math.sin(t * 0.4) * 0.35, pitch = Math.sin(t * 0.27) * 0.12;
    const cy = Math.cos(yaw), sy = Math.sin(yaw), cp = Math.cos(pitch), sp = Math.sin(pitch);
    for (let i = 0; i < c.length; i += 3) {
      let x = c[i], y = c[i + 1], z = c[i + 2];
      const y2 = y * cp - z * sp, z2 = y * sp + z * cp;
      o[i] = x * cy + z2 * sy;
      o[i + 1] = y2;
      o[i + 2] = -x * sy + z2 * cy;
    }
    return o;
  }
}
