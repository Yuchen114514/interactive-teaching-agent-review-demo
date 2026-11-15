import * as THREE from "three";
import {
  AGENT_MASCOT_CODES,
  AGENT_MASCOT_POSES,
  AGENT_MASCOT_TONES,
  isAgentMascotErrorState,
  normalizeAgentMascotSkin,
  type AgentMascotPixelPattern,
  type AgentMascotErrorReason,
  type AgentMascotPose,
  type AgentMascotSkinId,
  type AgentMascotState,
} from "./agentMascot";

import { resolveMascotAppearance } from "./agentMascotAppearance";
import type { ColorScheme } from "../../theme/appearance";

export interface AgentMascotSceneOptions {
  theme?: ColorScheme;
  state: AgentMascotState;
  skin?: AgentMascotSkinId;
  errorReason?: AgentMascotErrorReason;
  reducedMotion?: boolean;
  paused?: boolean;
  onUnavailable?: () => void;
}

export interface AgentMascotSceneController {
  setTheme: (theme: ColorScheme) => void;
  setPaused: (paused: boolean) => void;
  setState: (state: AgentMascotState, errorReason?: AgentMascotErrorReason) => void;
  setSkin: (skin: AgentMascotSkinId) => void;
  setPointer: (x: number, y: number) => void;
  resetPointer: () => void;
  resize: () => void;
  dispose: () => void;
}

interface PixelPool {
  group: THREE.Group;
  meshes: THREE.Mesh[];
}

const MODEL_VIEW_SIZE = 6.8;
const PIXEL_SIZE = 0.2;
const PIXEL_GAP = 0.04;
const FACE_DEPTH = 1.56;
const KEYCAP_FACE_Z = 1.84;
const KEYCAP_FACE_Y_OFFSET = -0.32;
const KEYCAP_TOP_WIDTH = 3.05;
const KEYCAP_BASE_WIDTH = 4.1;
const BODY_WIDTH = 3.65;
const BODY_DEPTH = 3;
const BODY_HEIGHT = 2.3;
const FACE_BASE_Y = 0.05;
const SETTLE_FACTOR = 0.18;
const SETTLE_EPSILON = 0.001;
const ERROR_NOISE_MS = 320;
const ERROR_CODE_MS = 2000;
const EXPRESSION_TICK_MS = 120;
const PIXEL_STEP = PIXEL_SIZE + PIXEL_GAP;
const ERROR_NOISE_COLUMNS = 14;
const ERROR_NOISE_LEFT = -((ERROR_NOISE_COLUMNS - 1) * PIXEL_STEP) / 2;
const LED_DROP_DISTANCE = PIXEL_SIZE * 0.45;
const LED_DROP_PERIOD = 9;
const LED_POOL_PHASES = [0, 2, 5] as const;
const ACCEPTED_ANIMATION_MS = 1500;
const ACCEPTED_JUMP_HEIGHT = 0.72;
const IMPACT_PARTICLE_COUNT = 24;
const IMPACT_PARTICLE_MS = 700;
const IMPACT_PARTICLE_SPREAD = 1.8;
const IMPACT_PARTICLE_GRAVITY = 1.9;
const IMPACT_EDGE_INSET = 0.18;
const IMPACT_EDGE_SPAN_X = BODY_WIDTH - IMPACT_EDGE_INSET * 2;
const IMPACT_EDGE_SPAN_Z = BODY_DEPTH - IMPACT_EDGE_INSET * 2;

const PIXEL_GLYPHS: Readonly<Record<string, AgentMascotPixelPattern>> = {
  A: [".##.", "#..#", "####", "#..#", "#..#"],
  C: ["####", "#...", "#...", "#...", "####"],
  E: ["####", "#...", "###.", "#...", "####"],
  F: ["####", "#...", "###.", "#...", "#..."],
  L: ["#...", "#...", "#...", "#...", "####"],
  M: ["#...#", "##.##", "#.#.#", "#...#", "#...#"],
  O: [".##.", "#..#", "#..#", "#..#", ".##."],
  R: ["###.", "#..#", "###.", "#.#.", "#..#"],
  T: ["####", ".##.", ".##.", ".##.", ".##."],
  U: ["#..#", "#..#", "#..#", "#..#", ".##."],
  W: ["#...#", "#...#", "#.#.#", "##.##", "#...#"],
};

function clampPointer(value: number) {
  return Math.max(-1, Math.min(1, value));
}

function degrees(value: number) {
  return THREE.MathUtils.degToRad(value);
}

const KEYCAP_FACE_TILT = degrees(-14);

function easeInOutCubic(value: number) {
  const progress = Math.max(0, Math.min(1, value));
  return progress < 0.5
    ? 4 * progress ** 3
    : 1 - Math.pow(-2 * progress + 2, 3) / 2;
}

function setMaterialColor(material: THREE.Material, color: string) {
  if (
    material instanceof THREE.MeshLambertMaterial
    || material instanceof THREE.MeshStandardMaterial
    || material instanceof THREE.MeshBasicMaterial
  ) {
    material.color.set(color);
  }
}

function buildPixelText(value: string): AgentMascotPixelPattern {
  const glyphs = Array.from(value).map((character) => PIXEL_GLYPHS[character] ?? PIXEL_GLYPHS.C);
  return Array.from({ length: 5 }, (_, rowIndex) => glyphs.map((glyph) => glyph[rowIndex]).join("."));
}

export function createAgentMascotScene(
  host: HTMLElement,
  options: AgentMascotSceneOptions,
): AgentMascotSceneController {
  const geometries = new Set<THREE.BufferGeometry>();
  const materials = new Set<THREE.Material>();
  const scene = new THREE.Scene();
  const camera = new THREE.OrthographicCamera(
    -MODEL_VIEW_SIZE / 2,
    MODEL_VIEW_SIZE / 2,
    MODEL_VIEW_SIZE / 2,
    -MODEL_VIEW_SIZE / 2,
    0.1,
    100,
  );
  camera.position.set(6.2, 5.1, 8.4);
  camera.lookAt(0, 0, 0);

  let renderer: THREE.WebGLRenderer;
  try {
    renderer = new THREE.WebGLRenderer({
      alpha: true,
      antialias: true,
      powerPreference: "low-power",
    });
  } catch (error) {
    options.onUnavailable?.();
    throw error;
  }

  renderer.setClearColor(0x000000, 0);
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.domElement.className = "agent-mascot-canvas";
  renderer.domElement.setAttribute("aria-hidden", "true");
  host.appendChild(renderer.domElement);

  const unitBox = new THREE.BoxGeometry(1, 1, 1);
  geometries.add(unitBox);

  function makeMaterial(color: string) {
    const material = new THREE.MeshLambertMaterial({ color, flatShading: true });
    materials.add(material);
    return material;
  }

  function makeBasicMaterial(color: string, opacity = 1) {
    const material = new THREE.MeshBasicMaterial({ color, transparent: opacity < 1, opacity });
    materials.add(material);
    return material;
  }

  const poseRoot = new THREE.Group();
  poseRoot.position.y = FACE_BASE_Y;
  scene.add(poseRoot);
  const mascotMotionRoot = new THREE.Group();
  poseRoot.add(mascotMotionRoot);

  const skinDecorRoot = new THREE.Group();
  mascotMotionRoot.add(skinDecorRoot);
  const skinAccentMaterial = makeMaterial("#8facc3");
  const skinPanelMaterial = makeMaterial("#647f96");
  const skinTrimMaterial = makeMaterial("#c1d6e6");
  const keycapBodyMaterial = makeMaterial("#d8d2c3");
  const keycapTopMaterial = makeMaterial("#f0e9db");
  const keycapBaseMaterial = makeMaterial("#8e918b");
  const keycapStemMaterial = makeMaterial("#596260");
  const terminalBodyMaterial = makeMaterial("#244d45");
  const terminalScreenMaterial = makeBasicMaterial("#102c28");
  const terminalBezelMaterial = makeMaterial("#173a35");
  const terminalHoodMaterial = makeMaterial("#38675b");
  const terminalKnobMaterial = makeMaterial("#78a888");
  const terminalScanlineMaterial = makeBasicMaterial("#b8d6a6", 0.16);
  const serverIndicatorMaterials = [
    makeBasicMaterial("#79a88b"),
    makeBasicMaterial("#87979a"),
    makeBasicMaterial("#87979a"),
  ];

  const keycapGeometryRoot = new THREE.Group();
  const keycapFrustumGeometry = new THREE.CylinderGeometry(
    KEYCAP_TOP_WIDTH / Math.SQRT2,
    KEYCAP_BASE_WIDTH / Math.SQRT2,
    2.1,
    4,
    1,
    false,
  );
  keycapFrustumGeometry.rotateY(Math.PI / 4);
  geometries.add(keycapFrustumGeometry);
  const keycapFrustum = new THREE.Mesh(
    keycapFrustumGeometry,
    [keycapBodyMaterial, keycapTopMaterial, keycapBaseMaterial],
  );
  keycapFrustum.position.y = 0.02;
  keycapGeometryRoot.add(keycapFrustum);

  const keycapUnderplate = new THREE.Mesh(unitBox, keycapBaseMaterial);
  keycapUnderplate.scale.set(1.42, 0.14, 1.08);
  keycapUnderplate.position.y = -1.09;
  keycapGeometryRoot.add(keycapUnderplate);

  const keycapCouplerBase = new THREE.Mesh(unitBox, keycapStemMaterial);
  keycapCouplerBase.scale.set(0.92, 0.22, 0.68);
  keycapCouplerBase.position.y = -1.27;
  keycapGeometryRoot.add(keycapCouplerBase);

  const keycapCrossStem = new THREE.Group();
  const keycapCrossStemVertical = new THREE.Mesh(unitBox, keycapStemMaterial);
  keycapCrossStemVertical.scale.set(0.15, 0.14, 0.56);
  keycapCrossStemVertical.position.y = -1.46;
  const keycapCrossStemHorizontal = new THREE.Mesh(unitBox, keycapStemMaterial);
  keycapCrossStemHorizontal.scale.set(0.56, 0.14, 0.15);
  keycapCrossStemHorizontal.position.y = -1.46;
  keycapCrossStem.add(keycapCrossStemVertical, keycapCrossStemHorizontal);
  keycapGeometryRoot.add(keycapCrossStem);
  keycapGeometryRoot.visible = false;
  mascotMotionRoot.add(keycapGeometryRoot);

  const terminalGeometryRoot = new THREE.Group();
  const terminalBody = new THREE.Mesh(unitBox, terminalBodyMaterial);
  terminalBody.scale.set(3.8, 2.35, 2.8);
  terminalGeometryRoot.add(terminalBody);

  const terminalScreen = new THREE.Mesh(unitBox, terminalScreenMaterial);
  terminalScreen.scale.set(3.15, 1.5, 0.06);
  terminalScreen.position.set(0, 0.02, 1.43);
  terminalGeometryRoot.add(terminalScreen);

  const terminalBezel = new THREE.Group();
  const terminalBezelTop = new THREE.Mesh(unitBox, terminalBezelMaterial);
  terminalBezelTop.scale.set(3.48, 0.15, 0.12);
  terminalBezelTop.position.set(0, 0.88, 1.5);
  const terminalBezelBottom = new THREE.Mesh(unitBox, terminalBezelMaterial);
  terminalBezelBottom.scale.set(3.48, 0.15, 0.12);
  terminalBezelBottom.position.set(0, -0.84, 1.5);
  const terminalBezelLeft = new THREE.Mesh(unitBox, terminalBezelMaterial);
  terminalBezelLeft.scale.set(0.15, 1.58, 0.12);
  terminalBezelLeft.position.set(-1.66, 0.02, 1.5);
  const terminalBezelRight = new THREE.Mesh(unitBox, terminalBezelMaterial);
  terminalBezelRight.scale.set(0.15, 1.58, 0.12);
  terminalBezelRight.position.set(1.66, 0.02, 1.5);
  terminalBezel.add(terminalBezelTop, terminalBezelBottom, terminalBezelLeft, terminalBezelRight);
  terminalGeometryRoot.add(terminalBezel);

  const terminalHood = new THREE.Mesh(unitBox, terminalHoodMaterial);
  terminalHood.scale.set(3.92, 0.22, 2.96);
  terminalHood.position.set(0, 1.24, 0.1);
  terminalGeometryRoot.add(terminalHood);

  const terminalKnob = new THREE.Mesh(
    new THREE.CylinderGeometry(0.17, 0.17, 0.12, 8),
    terminalKnobMaterial,
  );
  geometries.add(terminalKnob.geometry);
  terminalKnob.rotation.x = Math.PI / 2;
  terminalKnob.position.set(1.32, -0.94, 1.52);
  terminalGeometryRoot.add(terminalKnob);

  const terminalVentSlats = Array.from({ length: 5 }, (_, index) => {
    const slat = new THREE.Mesh(unitBox, terminalBezelMaterial);
    slat.scale.set(0.42, 0.035, 0.06);
    slat.position.set(-1.28, -0.56 - index * 0.12, 1.51);
    terminalGeometryRoot.add(slat);
    return slat;
  });
  terminalGeometryRoot.visible = false;
  mascotMotionRoot.add(terminalGeometryRoot);

  const terminalScanlines = Array.from({ length: 7 }, (_, index) => {
    const scanline = new THREE.Mesh(new THREE.PlaneGeometry(1, 1), terminalScanlineMaterial);
    geometries.add(scanline.geometry);
    scanline.scale.set(3.05, 0.018, 1);
    scanline.position.set(0, 0.62 - index * 0.18, FACE_DEPTH - 0.025);
    scanline.visible = false;
    skinDecorRoot.add(scanline);
    return scanline;
  });

  const keycapLabel = new THREE.Group();
  keycapLabel.position.z = 0;
  [0.54, 0, -0.54].forEach((x, index) => {
    const bar = new THREE.Mesh(unitBox, keycapStemMaterial);
    bar.scale.set(index === 1 ? 0.38 : 0.18, 0.045, 0.035);
    bar.position.set(x, -0.88, KEYCAP_FACE_Z + 0.05);
    keycapLabel.add(bar);
  });
  keycapLabel.visible = false;
  skinDecorRoot.add(keycapLabel);

  const serverPanel = new THREE.Mesh(unitBox, skinPanelMaterial);
  serverPanel.scale.set(BODY_WIDTH * 0.86, BODY_HEIGHT * 0.76, 0.035);
  serverPanel.position.set(0, 0, FACE_DEPTH - 0.02);
  serverPanel.visible = false;
  skinDecorRoot.add(serverPanel);

  const serverVents = Array.from({ length: 6 }, (_, index) => {
    const vent = new THREE.Mesh(unitBox, skinAccentMaterial);
    vent.scale.set(0.62, 0.035, 0.04);
    vent.position.set(0.86, -0.54 - index * 0.085, FACE_DEPTH - 0.045);
    vent.visible = false;
    skinDecorRoot.add(vent);
    return vent;
  });

  const serverIndicatorMeshes = serverIndicatorMaterials.map((material, index) => {
    const indicator = new THREE.Mesh(unitBox, material);
    indicator.scale.set(0.09, 0.09, 0.045);
    indicator.position.set(-1.22 + index * 0.25, 0.82, FACE_DEPTH - 0.045);
    indicator.visible = false;
    skinDecorRoot.add(indicator);
    return indicator;
  });

  const bodyFrontMaterial = makeMaterial("#9ebbd2");
  const bodyTopMaterial = makeMaterial("#c1d6e6");
  const bodySideMaterial = makeMaterial("#748fa6");
  const bodyBackMaterial = makeMaterial("#647f96");
  const bodyMaterials = [
    bodySideMaterial,
    bodyBackMaterial,
    bodyTopMaterial,
    bodySideMaterial,
    bodyFrontMaterial,
    bodyBackMaterial,
  ];
  const body: THREE.Mesh<THREE.BoxGeometry, THREE.Material | THREE.Material[]> = new THREE.Mesh(
    unitBox,
    bodyMaterials,
  );
  body.scale.set(BODY_WIDTH, BODY_HEIGHT, BODY_DEPTH);
  body.position.y = 0;
  mascotMotionRoot.add(body);

  const armMaterial = makeMaterial("#8facc3");
  const leftArmPivot = new THREE.Group();
  const rightArmPivot = new THREE.Group();
  leftArmPivot.position.set(-1.82, -0.15, 0.05);
  rightArmPivot.position.set(1.82, -0.15, 0.05);
  mascotMotionRoot.add(leftArmPivot, rightArmPivot);

  const leftArm = new THREE.Mesh(unitBox, armMaterial);
  leftArm.scale.set(1.05, 0.62, 0.94);
  leftArm.position.x = -0.52;
  leftArmPivot.add(leftArm);

  const rightArm = new THREE.Mesh(unitBox, armMaterial);
  rightArm.scale.set(1.05, 0.62, 0.94);
  rightArm.position.x = 0.52;
  rightArmPivot.add(rightArm);

  const faceRoot = new THREE.Group();
  faceRoot.position.y = 0;
  mascotMotionRoot.add(faceRoot);
  const pixelMaterial = new THREE.MeshBasicMaterial({ color: "#252a2d" });
  materials.add(pixelMaterial);
  const impactMaterial = new THREE.MeshLambertMaterial({
    color: "#9ebbd2",
    flatShading: true,
    transparent: true,
    opacity: 0,
  });
  materials.add(impactMaterial);
  const pixelGeometry = new THREE.PlaneGeometry(1, 1);
  geometries.add(pixelGeometry);

  function createPixelPool(capacity: number): PixelPool {
    const group = new THREE.Group();
    const meshes = Array.from({ length: capacity }, () => {
      const pixel = new THREE.Mesh(pixelGeometry, pixelMaterial);
      pixel.scale.set(PIXEL_SIZE, PIXEL_SIZE, 1);
      pixel.position.z = FACE_DEPTH;
      pixel.visible = false;
      group.add(pixel);
      return pixel;
    });
    faceRoot.add(group);
    return { group, meshes };
  }

  const leftBrow = createPixelPool(12);
  const rightBrow = createPixelPool(12);
  const leftEye = createPixelPool(18);
  const rightEye = createPixelPool(18);
  const mouth = createPixelPool(32);
  const errorScreen = createPixelPool(96);
  const expressionPools = [leftBrow, rightBrow, leftEye, rightEye, mouth, errorScreen] as const;
  const impactParticles = Array.from({ length: IMPACT_PARTICLE_COUNT }, (_, index) => {
    const particle = new THREE.Mesh(unitBox, impactMaterial);
    particle.scale.set(0.16, 0.16, 0.16);
    particle.visible = false;
    poseRoot.add(particle);
    return {
      mesh: particle,
      edgeIndex: Math.floor(index / (IMPACT_PARTICLE_COUNT / 4)),
      edgeProgress: (index % (IMPACT_PARTICLE_COUNT / 4)) / (IMPACT_PARTICLE_COUNT / 4 - 1),
      speed: 0.78 + (index % 4) * 0.16,
      lift: 0.68 + (index % 3) * 0.12,
    };
  });

  const ambientLight = new THREE.AmbientLight(0xffffff, 2.15);
  const directionalLight = new THREE.DirectionalLight(0xffffff, 2.55);
  directionalLight.position.set(-4.5, 7, 8);
  scene.add(ambientLight, directionalLight);

  let currentTheme: ColorScheme = options.theme ?? "light";
  let currentState = options.state;
  let currentPose: AgentMascotPose = AGENT_MASCOT_POSES[currentState];
  let currentSkin: AgentMascotSkinId = normalizeAgentMascotSkin(options.skin);
  let currentErrorReason: AgentMascotErrorReason = options.errorReason ?? "output-mismatch";
  let pointerX = 0;
  let pointerY = 0;
  let currentGazeX = 0;
  let currentGazeY = 0;
  let faceDepthOffset = 0;
  let faceVerticalOffset = 0;
  let animationFrame: number | null = null;
  let expressionMotionFrame: number | null = null;
  let celebrationFrame: number | null = null;
  let celebrationStartedAt = 0;
  let expressionTick = -1;
  let errorNoiseTimer: number | null = null;
  let errorTransitionTimer: number | null = null;
  let disposed = false;
  let paused = Boolean(options.paused);
  const reducedMotion = Boolean(options.reducedMotion);

  function desiredRotation() {
    const [poseX, poseY, poseZ] = currentPose.bodyRotation;
    return {
      x: degrees(poseX - pointerY * 4.5),
      y: degrees(poseY + pointerX * 8),
      z: degrees(poseZ),
    };
  }

  function desiredGaze() {
    return {
      x: currentPose.expression.gaze[0] + pointerX * 0.1,
      y: currentPose.expression.gaze[1] - pointerY * 0.07,
    };
  }

  function renderFrame() {
    if (disposed || paused) return;
    renderer.render(scene, camera);
  }

  function applySkin(skinId: AgentMascotSkinId) {
    currentSkin = normalizeAgentMascotSkin(skinId);
    const skin = resolveMascotAppearance(currentSkin, currentTheme, currentState);
    const palette = skin.palette;
    setMaterialColor(bodyFrontMaterial, palette.front);
    setMaterialColor(bodyTopMaterial, palette.top);
    setMaterialColor(bodySideMaterial, palette.side);
    setMaterialColor(bodyBackMaterial, palette.back);
    setMaterialColor(armMaterial, palette.arm);
    setMaterialColor(pixelMaterial, palette.pixel);
    setMaterialColor(skinAccentMaterial, skin.palette.accent);
    setMaterialColor(skinPanelMaterial, skin.palette.back);
    setMaterialColor(skinTrimMaterial, skin.palette.top);
    setMaterialColor(keycapBodyMaterial, skin.palette.front);
    setMaterialColor(keycapTopMaterial, skin.palette.top);
    setMaterialColor(keycapBaseMaterial, skin.palette.back);
    setMaterialColor(keycapStemMaterial, skin.palette.side);
    setMaterialColor(terminalBodyMaterial, skin.palette.front);
    setMaterialColor(terminalBezelMaterial, skin.palette.side);
    setMaterialColor(terminalHoodMaterial, skin.palette.top);
    setMaterialColor(terminalKnobMaterial, skin.palette.accent);
    setMaterialColor(terminalScreenMaterial, skin.palette.back);
    setMaterialColor(impactMaterial, currentSkin === "base" ? palette.front : skin.palette.accent);
    const coreColor = skin.indicator[AGENT_MASCOT_TONES[currentState]];
    setMaterialColor(terminalScanlineMaterial, skin.palette.pixel);
    terminalScanlineMaterial.opacity = skin.features.scanlines ? 0.16 : 0;
    terminalScanlines.forEach((mesh) => { mesh.visible = skin.features.scanlines; });
    body.visible = currentSkin !== "keycap" && currentSkin !== "terminal";
    body.material = bodyMaterials;
    body.renderOrder = 0;
    leftArmPivot.visible = true;
    rightArmPivot.visible = true;
    keycapGeometryRoot.visible = currentSkin === "keycap";
    terminalGeometryRoot.visible = currentSkin === "terminal";
    terminalVentSlats.forEach((mesh) => { mesh.visible = currentSkin === "terminal"; });
    keycapLabel.visible = skin.features.keycapLabel;
    faceDepthOffset = currentSkin === "keycap" ? KEYCAP_FACE_Z - FACE_DEPTH : currentSkin === "terminal" ? 0.04 : 0;
    faceVerticalOffset = currentSkin === "keycap" ? KEYCAP_FACE_Y_OFFSET : 0;
    faceRoot.position.z = faceDepthOffset;
    faceRoot.rotation.x = currentSkin === "keycap" ? KEYCAP_FACE_TILT : 0;
    skinDecorRoot.rotation.x = currentSkin === "keycap" ? KEYCAP_FACE_TILT : 0;
    serverPanel.visible = skin.features.serverPanel;
    serverVents.forEach((mesh) => { mesh.visible = skin.features.serverVents; });
    const indicatorColor = skin.indicator[AGENT_MASCOT_TONES[currentState]];
    serverIndicatorMeshes.forEach((mesh, index) => {
      mesh.visible = skin.features.serverPanel;
      setMaterialColor(serverIndicatorMaterials[index], index === 0 ? indicatorColor : skin.palette.accent);
    });
  }

  function resetCelebration() {
    mascotMotionRoot.position.set(0, 0, 0);
    mascotMotionRoot.rotation.set(0, 0, 0);
    mascotMotionRoot.scale.set(1, 1, 1);
    impactMaterial.opacity = 0;
    impactMaterial.userData.impactStartedAt = undefined;
    impactParticles.forEach(({ mesh }) => {
      mesh.visible = false;
    });
  }

  function startImpact(time: number) {
    impactMaterial.opacity = 0.95;
    impactParticles.forEach(({ mesh, edgeIndex, edgeProgress, speed, lift }) => {
      const edgeX = -IMPACT_EDGE_SPAN_X / 2 + edgeProgress * IMPACT_EDGE_SPAN_X;
      const edgeZ = -IMPACT_EDGE_SPAN_Z / 2 + edgeProgress * IMPACT_EDGE_SPAN_Z;
      const edgeRatio = Math.abs(edgeIndex % 2 === 0 ? edgeX : edgeZ)
        / (edgeIndex % 2 === 0 ? BODY_WIDTH / 2 : BODY_DEPTH / 2);
      const outwardX = edgeIndex === 1 ? 1 : edgeIndex === 3 ? -1 : 0;
      const outwardZ = edgeIndex === 0 ? -1 : edgeIndex === 2 ? 1 : 0;
      const startX = edgeIndex % 2 === 0 ? edgeX : outwardX * (BODY_WIDTH / 2 - IMPACT_EDGE_INSET);
      const startZ = edgeIndex % 2 === 0 ? outwardZ * (BODY_DEPTH / 2 - IMPACT_EDGE_INSET) : edgeZ;
      mesh.position.set(startX, -BODY_HEIGHT / 2 - 0.08, startZ);
      mesh.userData.impactStartX = mesh.position.x;
      mesh.userData.impactStartY = mesh.position.y;
      mesh.userData.impactStartZ = mesh.position.z;
      mesh.userData.impactVelocityX = outwardX * speed * (0.75 + edgeRatio * 0.45) * IMPACT_PARTICLE_SPREAD;
      mesh.userData.impactVelocityY = lift + (1 - edgeRatio) * 0.68;
      mesh.userData.impactVelocityZ = outwardZ * speed * (0.75 + edgeRatio * 0.45) * IMPACT_PARTICLE_SPREAD;
      mesh.visible = true;
    });
    impactMaterial.userData.impactStartedAt = time;
  }

  function animateAccepted(time: number) {
    celebrationFrame = null;
    if (disposed || paused) return;
    const elapsed = time - celebrationStartedAt;
    const progress = Math.min(1, elapsed / ACCEPTED_ANIMATION_MS);
    const jumpProgress = Math.min(1, progress / 0.62);
    const jumpHeight = Math.sin(jumpProgress * Math.PI) * ACCEPTED_JUMP_HEIGHT;
    const spin = easeInOutCubic(Math.min(1, progress / 0.58)) * Math.PI * 2;
    const impactProgress = Math.min(1, Math.max(0, (progress - 0.62) / 0.3));
    const impactPulse = Math.sin(Math.PI * impactProgress);

    mascotMotionRoot.position.y = jumpHeight + impactPulse * 0.04;
    mascotMotionRoot.rotation.x = spin;
    mascotMotionRoot.scale.set(
      1 + impactPulse * 0.035,
      1 - impactPulse * 0.07,
      1 + impactPulse * 0.035,
    );

    if (progress >= 0.62 && impactMaterial.userData.impactStartedAt === undefined) {
      startImpact(time);
    }
    if (impactMaterial.userData.impactStartedAt !== undefined) {
      const particleProgress = Math.min(
        1,
        (time - Number(impactMaterial.userData.impactStartedAt)) / IMPACT_PARTICLE_MS,
      );
      impactMaterial.opacity = 0.95 * (1 - particleProgress);
      impactParticles.forEach(({ mesh }) => {
        const startX = Number(mesh.userData.impactStartX);
        const startY = Number(mesh.userData.impactStartY);
        const startZ = Number(mesh.userData.impactStartZ);
        const velocityX = Number(mesh.userData.impactVelocityX);
        const velocityY = Number(mesh.userData.impactVelocityY);
        const velocityZ = Number(mesh.userData.impactVelocityZ);
        mesh.position.x = startX + velocityX * particleProgress;
        mesh.position.y = startY
          + velocityY * particleProgress
          - IMPACT_PARTICLE_GRAVITY * particleProgress ** 2;
        mesh.position.z = startZ + velocityZ * particleProgress;
        mesh.visible = particleProgress < 1;
      });
    }

    renderFrame();
    if (progress < 1) {
      celebrationFrame = window.requestAnimationFrame(animateAccepted);
    } else {
      resetCelebration();
      renderFrame();
    }
  }

  function startAcceptedAnimation() {
    resetCelebration();
    if (reducedMotion || paused) return;
    celebrationStartedAt = performance.now();
    celebrationFrame = window.requestAnimationFrame(animateAccepted);
  }

  function settleFrame() {
    animationFrame = null;
    if (disposed || paused) return;
    const rotation = desiredRotation();
    const gaze = desiredGaze();

    if (reducedMotion) {
      poseRoot.rotation.set(rotation.x, rotation.y, rotation.z);
      currentGazeX = gaze.x;
      currentGazeY = gaze.y;
    } else {
      poseRoot.rotation.x += (rotation.x - poseRoot.rotation.x) * SETTLE_FACTOR;
      poseRoot.rotation.y += (rotation.y - poseRoot.rotation.y) * SETTLE_FACTOR;
      poseRoot.rotation.z += (rotation.z - poseRoot.rotation.z) * SETTLE_FACTOR;
      currentGazeX += (gaze.x - currentGazeX) * SETTLE_FACTOR;
      currentGazeY += (gaze.y - currentGazeY) * SETTLE_FACTOR;
    }
    faceRoot.position.set(currentGazeX, currentGazeY + faceVerticalOffset, faceDepthOffset);
    renderFrame();

    const unsettled = Math.abs(rotation.x - poseRoot.rotation.x)
      + Math.abs(rotation.y - poseRoot.rotation.y)
      + Math.abs(rotation.z - poseRoot.rotation.z)
      + Math.abs(gaze.x - currentGazeX)
      + Math.abs(gaze.y - currentGazeY);
    if (!reducedMotion && unsettled > SETTLE_EPSILON) {
      animationFrame = window.requestAnimationFrame(settleFrame);
    }
  }

  function requestRender() {
    if (disposed || paused || animationFrame !== null) return;
    animationFrame = window.requestAnimationFrame(settleFrame);
  }

  function animateExpressionMotion(time: number) {
    expressionMotionFrame = null;
    if (disposed || paused) return;
    const tick = Math.floor(time / EXPRESSION_TICK_MS);
    if (tick !== expressionTick) {
      expressionTick = tick;
      [leftEye, rightEye, mouth].forEach((pool, poolIndex) => {
        pool.meshes.forEach((pixel, pixelIndex) => {
          if (!pixel.visible) return;
          const baseX = Number(pixel.userData.mascotBaseX ?? pixel.position.x);
          const baseY = Number(pixel.userData.mascotBaseY ?? pixel.position.y);
          const phase = LED_POOL_PHASES[poolIndex] + pixelIndex;
          const dropsThisTick = (tick + phase) % LED_DROP_PERIOD === 0;
          pixel.position.x = baseX;
          pixel.position.y = baseY - (dropsThisTick ? LED_DROP_DISTANCE : 0);
        });
      });
      renderFrame();
    }
    if (!reducedMotion) {
      expressionMotionFrame = window.requestAnimationFrame(animateExpressionMotion);
    }
  }

  function setExpressionVisibility(visible: boolean) {
    [leftBrow, rightBrow, leftEye, rightEye, mouth].forEach((pool) => {
      pool.meshes.forEach((pixel) => {
        pixel.visible = visible;
      });
    });
  }

  function applyPixelPattern(
    pool: PixelPool,
    pattern: AgentMascotPixelPattern,
    centerX: number,
    centerY: number,
  ) {
    const rows = pattern.length;
    const columns = Math.max(0, ...pattern.map((row) => row.length));
    const step = PIXEL_STEP;
    let meshIndex = 0;

    pattern.forEach((row, rowIndex) => {
      Array.from(row).forEach((cell, columnIndex) => {
        if (cell !== "#" || meshIndex >= pool.meshes.length) return;
        const pixel = pool.meshes[meshIndex];
        pixel.position.set(
          centerX + (columnIndex - (columns - 1) / 2) * step,
          centerY + ((rows - 1) / 2 - rowIndex) * step,
          FACE_DEPTH,
        );
        pixel.userData.mascotBaseX = pixel.position.x;
        pixel.userData.mascotBaseY = pixel.position.y;
        pixel.visible = true;
        meshIndex += 1;
      });
    });

    pool.meshes.forEach((pixel, index) => {
      if (index >= meshIndex) pixel.visible = false;
    });
  }

  function applyExpression(pose: AgentMascotPose) {
    setExpressionVisibility(false);
    applyPixelPattern(leftBrow, pose.expression.leftBrow, -0.86, 0.64);
    applyPixelPattern(rightBrow, pose.expression.rightBrow, 0.86, 0.64);
    applyPixelPattern(leftEye, pose.expression.leftEye, -0.86, 0.22);
    applyPixelPattern(rightEye, pose.expression.rightEye, 0.86, 0.22);
    applyPixelPattern(mouth, pose.expression.mouth, 0, -0.26);
    errorScreen.meshes.forEach((pixel) => {
      pixel.visible = false;
    });
  }

  function applyNoisePattern() {
    setExpressionVisibility(false);
    errorScreen.meshes.forEach((pixel, index) => {
      const visible = index < 28 + Math.floor(Math.random() * 36);
      pixel.visible = visible;
      if (!visible) return;
      pixel.position.set(
        ERROR_NOISE_LEFT + Math.floor(Math.random() * ERROR_NOISE_COLUMNS) * PIXEL_STEP,
        0.46 - Math.floor(Math.random() * 5) * PIXEL_STEP,
        FACE_DEPTH,
      );
      pixel.userData.mascotBaseX = pixel.position.x;
      pixel.userData.mascotBaseY = pixel.position.y;
    });
  }

  function clearErrorTransition() {
    if (errorNoiseTimer !== null) window.clearInterval(errorNoiseTimer);
    if (errorTransitionTimer !== null) window.clearTimeout(errorTransitionTimer);
    errorNoiseTimer = null;
    errorTransitionTimer = null;
  }

  function applyErrorTransition(code: string) {
    clearErrorTransition();
    if (reducedMotion || paused) {
      applyExpression(currentPose);
      requestRender();
      return;
    }

    applyNoisePattern();
    requestRender();
    errorNoiseTimer = window.setInterval(() => {
      applyNoisePattern();
      requestRender();
    }, 55);
    errorTransitionTimer = window.setTimeout(() => {
      if (errorNoiseTimer !== null) window.clearInterval(errorNoiseTimer);
      errorNoiseTimer = null;
      errorScreen.meshes.forEach((pixel) => { pixel.visible = false; });
      applyPixelPattern(errorScreen, buildPixelText(code), 0, 0.02);
      requestRender();
      errorTransitionTimer = window.setTimeout(() => {
        errorScreen.meshes.forEach((pixel) => { pixel.visible = false; });
        applyExpression(currentPose);
        requestRender();
        errorTransitionTimer = null;
      }, ERROR_CODE_MS);
    }, ERROR_NOISE_MS);
  }

  function applyState(state: AgentMascotState, transitionError = false) {
    clearErrorTransition();
    if (celebrationFrame !== null) window.cancelAnimationFrame(celebrationFrame);
    celebrationFrame = null;
    resetCelebration();
    currentState = state;
    currentPose = AGENT_MASCOT_POSES[currentState];
    const { expression, armRotation } = currentPose;
    applySkin(currentSkin);

    applyExpression(currentPose);

    leftArmPivot.rotation.z = degrees(armRotation[0]);
    rightArmPivot.rotation.z = degrees(armRotation[1]);
    if (transitionError && isAgentMascotErrorState(state)) {
      applyErrorTransition(AGENT_MASCOT_CODES[state]);
    }
    requestRender();
  }

  function resize() {
    if (disposed) return;
    const width = Math.max(1, Math.round(host.clientWidth));
    const height = Math.max(1, Math.round(host.clientHeight));
    const aspect = width / height;
    if (aspect >= 1) {
      camera.left = -MODEL_VIEW_SIZE * aspect / 2;
      camera.right = MODEL_VIEW_SIZE * aspect / 2;
      camera.top = MODEL_VIEW_SIZE / 2;
      camera.bottom = -MODEL_VIEW_SIZE / 2;
    } else {
      camera.left = -MODEL_VIEW_SIZE / 2;
      camera.right = MODEL_VIEW_SIZE / 2;
      camera.top = MODEL_VIEW_SIZE / aspect / 2;
      camera.bottom = -MODEL_VIEW_SIZE / aspect / 2;
    }
    camera.updateProjectionMatrix();
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.75));
    renderer.setSize(width, height, false);
    requestRender();
  }

  const resizeObserver = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(resize);
  resizeObserver?.observe(host);

  function handleContextLost(event: Event) {
    event.preventDefault();
    options.onUnavailable?.();
  }
  renderer.domElement.addEventListener("webglcontextlost", handleContextLost);

  resize();
  applySkin(currentSkin);
  applyState(options.state, isAgentMascotErrorState(options.state));
  if (!reducedMotion && !paused) {
    expressionMotionFrame = window.requestAnimationFrame(animateExpressionMotion);
  }

  return {
    setPaused(nextPaused: boolean) {
      if (disposed || paused === nextPaused) return;
      paused = nextPaused;
      if (paused) {
        if (animationFrame !== null) window.cancelAnimationFrame(animationFrame);
        if (expressionMotionFrame !== null) window.cancelAnimationFrame(expressionMotionFrame);
        if (celebrationFrame !== null) window.cancelAnimationFrame(celebrationFrame);
        animationFrame = null;
        expressionMotionFrame = null;
        celebrationFrame = null;
        clearErrorTransition();
        resetCelebration();
        return;
      }
      applyState(currentState);
      expressionTick = -1;
      if (!reducedMotion) expressionMotionFrame = window.requestAnimationFrame(animateExpressionMotion);
      requestRender();
    },
    setState(state, errorReason = "output-mismatch") {
      const stateChanged = state !== currentState;
      const errorReasonChanged = errorReason !== currentErrorReason;
      currentErrorReason = errorReason;
      if (stateChanged || (state === "wrong-answer" && errorReasonChanged)) {
        applyState(state, isAgentMascotErrorState(state));
        if (stateChanged && state === "accepted") startAcceptedAnimation();
      }
    },
    setTheme(theme: ColorScheme) {
      currentTheme = theme;
      applySkin(currentSkin);
      requestRender();
    },
    setSkin(skin: AgentMascotSkinId) {
      applySkin(skin);
      requestRender();
    },
    setPointer(x, y) {
      pointerX = clampPointer(x);
      pointerY = clampPointer(y);
      requestRender();
    },
    resetPointer() {
      pointerX = 0;
      pointerY = 0;
      requestRender();
    },
    resize,
    dispose() {
      if (disposed) return;
      disposed = true;
      if (animationFrame !== null) window.cancelAnimationFrame(animationFrame);
      if (expressionMotionFrame !== null) window.cancelAnimationFrame(expressionMotionFrame);
      if (celebrationFrame !== null) window.cancelAnimationFrame(celebrationFrame);
      clearErrorTransition();
      resizeObserver?.disconnect();
      renderer.domElement.removeEventListener("webglcontextlost", handleContextLost);
      geometries.forEach((geometry) => geometry.dispose());
      materials.forEach((material) => material.dispose());
      renderer.renderLists.dispose();
      renderer.dispose();
      renderer.domElement.remove();
      animationFrame = null;
      expressionMotionFrame = null;
    },
  };
}
