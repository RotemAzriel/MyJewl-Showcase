const state = {
  mode: "entry",
  file: null,
  imageDataUrl: null,
  result: null,
  assets: null,
  needsAnalysis: true,
  spin: true,
  preview: {
    stone_shape: "round",
    stone_dimensions_mm: { length: 6.5, width: 6.5, depth: 3.9 },
    metal_family: "yellow",
    ring_size_us: 6.5,
    band_width_mm: 2.0,
    prong_count: 4,
    center_setting: "prong",
    carat: 1.0,
  },
};

const els = {
  navUpload: document.querySelector("#navUpload"),
  navScratch: document.querySelector("#navScratch"),
  heroQuickStl: document.querySelector("#heroQuickStl"),
  startUpload: document.querySelector("#startUpload"),
  startScratch: document.querySelector("#startScratch"),
  heroShapeValue: document.querySelector("#heroShapeValue"),
  heroMetalValue: document.querySelector("#heroMetalValue"),
  photoInput: document.querySelector("#photoInput"),
  photoPreview: document.querySelector("#photoPreview"),
  dropzoneTitle: document.querySelector("#dropzoneTitle"),
  caratInput: document.querySelector("#caratInput"),
  sizeInput: document.querySelector("#sizeInput"),
  shapeInput: document.querySelector("#shapeInput"),
  settingInput: document.querySelector("#settingInput"),
  bandInput: document.querySelector("#bandInput"),
  bandOutput: document.querySelector("#bandOutput"),
  analyzeButton: document.querySelector("#analyzeButton"),
  generateButton: document.querySelector("#generateButton"),
  rhinoButton: document.querySelector("#rhinoButton"),
  resetView: document.querySelector("#resetView"),
  spinToggle: document.querySelector("#spinToggle"),
  confidenceRing: document.querySelector("#confidenceRing"),
  confidenceValue: document.querySelector("#confidenceValue"),
  shapeValue: document.querySelector("#shapeValue"),
  settingValue: document.querySelector("#settingValue"),
  prongValue: document.querySelector("#prongValue"),
  metalValue: document.querySelector("#metalValue"),
  stoneValue: document.querySelector("#stoneValue"),
  viewportShape: document.querySelector("#viewportShape"),
  viewportSpecs: document.querySelector("#viewportSpecs"),
  statusBox: document.querySelector("#statusBox"),
  downloadList: document.querySelector("#downloadList"),
  analysisStep: document.querySelector("#analysisStep"),
  assetStep: document.querySelector("#assetStep"),
  shapeChips: [...document.querySelectorAll("[data-shape]")],
  metalSwatches: [...document.querySelectorAll("[data-metal]")],
};

let THREE = null;
let heroScene = null;
let studioScene = null;

boot();

async function boot() {
  bindEvents();
  syncManualPreview({ preserveResult: true });
  setMode("entry", { scroll: false, quiet: true });
  try {
    THREE = await import("/static/vendor/three-r166/three.module.min.js");
    document.body.classList.add("three-ready");
    heroScene = createRingScene(document.querySelector("#heroScene"), { hero: true });
    studioScene = createRingScene(document.querySelector("#workspaceScene"), { hero: false });
    animate();
  } catch (error) {
    document.body.classList.add("three-failed");
    console.warn("Three.js could not load", error);
  }
}

function bindEvents() {
  els.navUpload.addEventListener("click", () => selectUploadPath());
  els.navScratch.addEventListener("click", () => selectScratchPath());
  els.startUpload.addEventListener("click", () => selectUploadPath());
  els.startScratch.addEventListener("click", () => selectScratchPath());
  els.heroQuickStl.addEventListener("click", () => {
    selectScratchPath({ quiet: true });
    setTimeout(() => generateAsset("/api/generate-stl"), 380);
  });

  els.photoInput.addEventListener("change", handlePhoto);
  els.bandInput.addEventListener("input", () => syncManualPreview());
  [els.caratInput, els.sizeInput, els.shapeInput, els.settingInput].forEach((el) => {
    el.addEventListener("input", () => syncManualPreview());
  });

  els.shapeChips.forEach((button) => {
    button.addEventListener("click", () => {
      els.shapeInput.value = button.dataset.shape;
      selectScratchPath({ quiet: true });
      syncManualPreview();
    });
  });

  els.metalSwatches.forEach((button) => {
    button.addEventListener("click", () => {
      state.preview.metal_family = button.dataset.metal;
      selectScratchPath({ quiet: true });
      markDirty();
      studioScene?.rebuild();
      heroScene?.rebuild();
      updateUi();
    });
  });

  els.analyzeButton.addEventListener("click", () => analyzeDesign());
  els.generateButton.addEventListener("click", () => generateAsset("/api/generate-stl"));
  els.rhinoButton.addEventListener("click", () => generateAsset("/api/rhino-handoff"));
  els.resetView.addEventListener("click", () => studioScene?.reset());
  els.spinToggle.addEventListener("click", () => {
    state.spin = !state.spin;
    els.spinToggle.textContent = state.spin ? "Spin" : "Still";
    els.spinToggle.classList.toggle("is-active", state.spin);
  });
}

function selectUploadPath(options = {}) {
  setMode("upload", options);
  if (!options.quiet) setStatus("Upload a ring photo or tune the known values before extraction.", "");
}

function selectScratchPath(options = {}) {
  setMode("scratch", options);
  if (!options.quiet) setStatus("Brief is live. Create a draft STL whenever the preview feels right.", "ok");
}

function setMode(mode, options = {}) {
  state.mode = mode;
  document.body.dataset.mode = mode;
  if (!options.quiet) {
    document.body.classList.add("is-transitioning");
    setTimeout(() => document.body.classList.remove("is-transitioning"), 640);
  }
  updateAssetControls();
  updateProgress();
  if (options.scroll !== false && mode !== "entry") {
    document.querySelector("#studio").scrollIntoView({ behavior: "smooth", block: "start" });
  }
}

function handlePhoto(event) {
  const [file] = event.target.files || [];
  if (!file) return;
  state.file = file;
  const reader = new FileReader();
  reader.onload = () => {
    state.imageDataUrl = String(reader.result);
    state.result = null;
    state.assets = null;
    state.needsAnalysis = true;
    els.photoPreview.style.backgroundImage = `url("${state.imageDataUrl}")`;
    els.photoPreview.classList.add("has-image");
    els.dropzoneTitle.textContent = file.name;
    setMode("upload", { quiet: true });
    setStatus("Photo loaded. Extraction is ready.", "ok");
    updateAssetControls();
    updateProgress();
  };
  reader.readAsDataURL(file);
}

function syncManualPreview(options = {}) {
  const selectedShape = els.shapeInput.value === "auto" ? state.preview.stone_shape : els.shapeInput.value;
  const carat = readNumber(els.caratInput, 1);
  const scale = Math.cbrt(Math.max(0.1, carat));
  const dims = baseDims(selectedShape);
  const setting = els.settingInput.value;
  state.preview = {
    ...state.preview,
    stone_shape: selectedShape,
    stone_dimensions_mm: {
      length: dims[0] * scale,
      width: dims[1] * scale,
      depth: dims[2] * scale,
    },
    ring_size_us: readNumber(els.sizeInput, 6.5),
    band_width_mm: readNumber(els.bandInput, 2),
    prong_count: inferProngCount(selectedShape, carat, setting),
    center_setting: setting,
    carat,
  };
  if (!options.preserveResult) markDirty();
  studioScene?.rebuild();
  heroScene?.rebuild();
  updateUi();
}

async function analyzeDesign(options = {}) {
  if (!options.quietButton) setBusy(els.analyzeButton, true, "Extracting");
  setStatus(options.forAsset ? "Locking canonical spec before output..." : "Extracting design...", "");
  try {
    const shapeHint = els.shapeInput.value === "auto" ? state.preview.stone_shape : els.shapeInput.value;
    const result = await postJson("/api/extract", {
      image_data_url: state.imageDataUrl,
      filename: state.file?.name || syntheticFilename(),
      carat: readNumber(els.caratInput, 1),
      ring_size_us: readNumber(els.sizeInput, 6.5),
      shape_hint: shapeHint,
      setting_hint: els.settingInput.value,
      band_width_mm: readNumber(els.bandInput, 2),
    });

    state.result = result;
    state.preview = {
      ...result.preview,
      center_setting: result.extraction?.components?.center_setting?.value || els.settingInput.value,
      carat: readNumber(els.caratInput, 1),
    };
    state.assets = null;
    state.needsAnalysis = false;
    els.downloadList.innerHTML = "";
    studioScene?.rebuild();
    heroScene?.rebuild();
    updateUi();

    const validation = result.validation;
    if (validation.constraint_accepted && validation.schema_accepted) {
      setStatus("Canonical spec accepted. STL and Rhino output are ready.", "ok");
    } else {
      setStatus(validation.hard_violations?.[0]?.message || "Spec needs adjustment.", "error");
    }
    return result;
  } catch (error) {
    setStatus(error.message, "error");
    throw error;
  } finally {
    if (!options.quietButton) setBusy(els.analyzeButton, false, "Extract Design");
    updateAssetControls();
    updateProgress();
  }
}

async function generateAsset(endpoint) {
  const button = endpoint.includes("rhino") ? els.rhinoButton : els.generateButton;
  const defaultLabel = endpoint.includes("rhino") ? "Rhino Handoff" : "Create Draft STL";
  setBusy(button, true, state.result && !state.needsAnalysis ? (endpoint.includes("rhino") ? "Preparing" : "Creating") : "Extracting");
  try {
    if (!state.result || state.needsAnalysis || !state.result.rhino?.ready_for_handoff) {
      await analyzeDesign({ forAsset: true, quietButton: true });
    }
    if (!state.result?.canonical_spec || !state.result?.rhino?.ready_for_handoff) {
      setStatus("Canonical spec is not ready for output yet.", "error");
      return;
    }

    setBusy(button, true, endpoint.includes("rhino") ? "Preparing" : "Creating");
    setStatus(endpoint.includes("rhino") ? "Preparing Rhino handoff..." : "Creating draft STL...", "");
    const assets = await postJson(endpoint, { canonical_spec: state.result.canonical_spec });
    state.assets = assets;
    state.needsAnalysis = false;
    renderDownloads(assets);
    const rhino = assets.rhino_compute;
    if (rhino?.status === "handoff_ready") {
      setStatus("Rhino handoff is ready. Live POST waits for endpoint env vars.", "ok");
    } else if (rhino?.status) {
      setStatus(`Rhino status: ${rhino.status}`, rhino.status === "completed" ? "ok" : "");
    } else {
      setStatus("Draft STL created.", "ok");
    }
  } catch (error) {
    setStatus(error.message, "error");
  } finally {
    setBusy(button, false, defaultLabel);
    updateAssetControls();
    updateProgress();
  }
}

function renderDownloads(assets) {
  const links = [
    ["Draft STL", assets.urls?.draft_stl],
    ["Rhino JSON", assets.urls?.rhino_handoff],
    ["Canonical Spec", assets.urls?.canonical_spec],
  ].filter(([, href]) => href);
  els.downloadList.innerHTML = links
    .map(([label, href]) => `<a href="${href}"><span>${label}</span><span>Download</span></a>`)
    .join("");
}

async function postJson(url, payload) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(payload),
  });
  const body = await response.json();
  if (!response.ok) {
    throw new Error(body.error || `Request failed: ${response.status}`);
  }
  return body;
}

function createRingScene(canvas, options) {
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = options.hero ? 1.14 : 1.05;
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(options.hero ? 31 : 34, 1, 0.1, 120);
  camera.position.set(options.hero ? 5.3 : 4.9, options.hero ? 2.5 : 2.9, options.hero ? 8.9 : 6.2);
  camera.lookAt(options.hero ? 1.18 : 0, 0, 0.18);

  const group = new THREE.Group();
  group.position.set(options.hero ? 2.08 : 0, options.hero ? -0.16 : 0, options.hero ? 0.08 : 0);
  scene.add(group);

  const fill = new THREE.HemisphereLight(0xfff2cf, 0x0a0a0a, options.hero ? 1.8 : 1.55);
  scene.add(fill);
  const key = new THREE.DirectionalLight(0xffe2ad, options.hero ? 4.8 : 4.2);
  key.position.set(3.2, 5.4, 4.8);
  key.castShadow = true;
  key.shadow.mapSize.set(1024, 1024);
  scene.add(key);
  const rim = new THREE.PointLight(0xc9a96e, options.hero ? 28 : 18, 14);
  rim.position.set(-3.8, 1.8, 3.6);
  scene.add(rim);
  const goldKick = new THREE.PointLight(0xffd676, options.hero ? 18 : 11, 12);
  goldKick.position.set(2.6, -1.9, 2.8);
  scene.add(goldKick);

  const floor = new THREE.Mesh(
    new THREE.CircleGeometry(options.hero ? 3.0 : 2.7, 80),
    new THREE.MeshBasicMaterial({ color: 0x000000, transparent: true, opacity: options.hero ? 0.22 : 0.34 })
  );
  floor.rotation.x = -Math.PI / 2;
  floor.position.y = -1.18;
  floor.position.z = 0.22;
  scene.add(floor);

  const api = {
    group,
    targetX: options.hero ? -0.46 : -0.34,
    targetY: options.hero ? -0.72 : -0.18,
    reset() {
      api.targetX = -0.34;
      api.targetY = -0.18;
    },
    rebuild() {
      rebuildRing(group, options.hero);
    },
    render() {
      resizeRenderer(renderer, camera, canvas);
      if (state.spin || options.hero) api.targetY += options.hero ? 0.002 : 0.0032;
      group.rotation.x += (api.targetX - group.rotation.x) * 0.06;
      group.rotation.y += (api.targetY - group.rotation.y) * 0.06;
      renderer.render(scene, camera);
    },
  };

  addDrag(canvas, api);
  api.rebuild();
  return api;
}

function rebuildRing(group, hero) {
  disposeGroup(group);

  const preview = hero
    ? {
        ...state.preview,
        stone_shape: state.preview.stone_shape || "oval",
        prong_count: Math.max(state.preview.prong_count || 4, 4),
      }
    : state.preview;
  const sizeScale = hero ? 0.96 : 1;
  const bandRadius = 1.78 * sizeScale;
  const tube = clamp(preview.band_width_mm / 17.5, 0.085, 0.24) * sizeScale;
  const dims = preview.stone_dimensions_mm || { length: 6.5, width: 6.5, depth: 3.9 };
  const aspect = clamp(dims.length / Math.max(dims.width, 0.1), 0.62, 1.75);
  const setting = preview.center_setting || els.settingInput.value;

  const metal = new THREE.MeshPhysicalMaterial({
    color: metalColor(preview.metal_family),
    metalness: 0.96,
    roughness: 0.18,
    clearcoat: 0.72,
    clearcoatRoughness: 0.16,
  });
  const darkerMetal = new THREE.MeshPhysicalMaterial({
    color: metalColor(preview.metal_family),
    metalness: 0.98,
    roughness: 0.32,
    clearcoat: 0.4,
  });
  const stone = new THREE.MeshPhysicalMaterial({
    color: 0xf8fbff,
    metalness: 0,
    roughness: 0.015,
    transmission: 0.5,
    thickness: 0.62,
    ior: 2.35,
    transparent: true,
    opacity: 0.82,
    clearcoat: 1,
    clearcoatRoughness: 0.02,
  });

  const band = mesh(new THREE.TorusGeometry(bandRadius, tube, 34, 192), metal);
  band.rotation.x = Math.PI / 2;
  group.add(band);

  addShoulders(group, bandRadius, tube, aspect, metal);
  addStone(group, preview, aspect, bandRadius, sizeScale, stone);
  addSetting(group, setting, preview, aspect, bandRadius, tube, metal, darkerMetal);
}

function addShoulders(group, bandRadius, tube, aspect, metal) {
  const zTop = bandRadius * 0.86;
  const spread = 0.42 + aspect * 0.13;
  [
    [new THREE.Vector3(-0.82, 0, zTop - 0.18), new THREE.Vector3(-spread, 0, zTop + 0.42)],
    [new THREE.Vector3(0.82, 0, zTop - 0.18), new THREE.Vector3(spread, 0, zTop + 0.42)],
    [new THREE.Vector3(-0.58, 0, zTop - 0.34), new THREE.Vector3(-spread * 0.76, 0, zTop + 0.2)],
    [new THREE.Vector3(0.58, 0, zTop - 0.34), new THREE.Vector3(spread * 0.76, 0, zTop + 0.2)],
  ].forEach(([start, end]) => group.add(cylinderBetween(start, end, tube * 0.34, metal, 18)));
}

function addStone(group, preview, aspect, bandRadius, sizeScale, material) {
  const segments = gemSegmentCount(preview.stone_shape);
  const gemGroup = new THREE.Group();
  const crownZ = bandRadius * 1.08;
  const stoneScale = clamp((preview.carat || 1) ** (1 / 3), 0.62, 1.72) * sizeScale;
  gemGroup.scale.set(stoneScale, stoneScale, stoneScale);
  gemGroup.position.z = crownZ;

  const shape = preview.stone_shape;
  if (shape === "emerald" || shape === "princess" || shape === "radiant") {
    const rectAspect = shape === "princess" ? 1 : aspect;
    const top = mesh(new THREE.BoxGeometry(0.92 * rectAspect, 0.82, 0.18), material);
    top.position.z = 0.18;
    gemGroup.add(top);
    const pavilion = mesh(new THREE.ConeGeometry(0.56, 0.74, shape === "emerald" ? 4 : 8), material);
    pavilion.rotation.x = Math.PI / 2;
    pavilion.rotation.z = Math.PI / 4;
    pavilion.scale.x = rectAspect;
    pavilion.position.z = -0.2;
    gemGroup.add(pavilion);
  } else if (shape === "pear") {
    const pear = createPearGeometry();
    const top = mesh(pear, material);
    top.scale.set(0.92, 1.08, 1);
    top.position.z = 0.08;
    gemGroup.add(top);
    const pavilion = mesh(new THREE.ConeGeometry(0.48, 0.72, 32), material);
    pavilion.rotation.x = Math.PI / 2;
    pavilion.scale.set(0.78, 1.15, 1);
    pavilion.position.z = -0.22;
    gemGroup.add(pavilion);
  } else {
    const top = mesh(new THREE.CylinderGeometry(0.48, 0.56, 0.2, segments), material);
    top.rotation.x = Math.PI / 2;
    top.scale.x = aspect;
    top.position.z = 0.16;
    gemGroup.add(top);
    const pavilion = mesh(new THREE.ConeGeometry(0.56, 0.78, segments), material);
    pavilion.rotation.x = Math.PI / 2;
    pavilion.scale.x = aspect;
    pavilion.position.z = -0.26;
    gemGroup.add(pavilion);
  }

  const table = mesh(new THREE.CylinderGeometry(0.32, 0.34, 0.035, segments), material);
  table.rotation.x = Math.PI / 2;
  table.scale.x = aspect;
  table.position.z = 0.32;
  gemGroup.add(table);
  group.add(gemGroup);
}

function addSetting(group, setting, preview, aspect, bandRadius, tube, metal, darkerMetal) {
  const baseZ = bandRadius * 1.08;
  const stoneScale = clamp((preview.carat || 1) ** (1 / 3), 0.62, 1.72);
  const radiusX = (0.54 * aspect + tube * 1.15) * stoneScale;
  const radiusY = (0.56 + tube * 1.05) * stoneScale;

  const basket = mesh(new THREE.TorusGeometry(0.5, tube * 0.17, 14, 72), darkerMetal);
  basket.scale.x = aspect * stoneScale;
  basket.scale.y = stoneScale;
  basket.position.z = baseZ - 0.28 * stoneScale;
  group.add(basket);

  if (setting === "bezel") {
    const bezel = mesh(new THREE.TorusGeometry(0.58, tube * 0.24, 18, 96), metal);
    bezel.scale.x = aspect * stoneScale;
    bezel.scale.y = stoneScale;
    bezel.position.z = baseZ + 0.09 * stoneScale;
    group.add(bezel);
    return;
  }

  const count = preview.prong_count || (setting === "basket" ? 4 : 0);
  for (let i = 0; i < count; i += 1) {
    const angle = (Math.PI * 2 * i) / count + Math.PI / count;
    const x = Math.cos(angle) * radiusX;
    const y = Math.sin(angle) * radiusY;
    const prong = mesh(new THREE.CylinderGeometry(tube * 0.23, tube * 0.19, 0.78 * stoneScale, 16), metal);
    prong.rotation.x = Math.PI / 2;
    prong.rotation.z = -angle * 0.08;
    prong.position.set(x, y, baseZ + 0.02 * stoneScale);
    group.add(prong);

    const bead = mesh(new THREE.SphereGeometry(tube * 0.33, 16, 12), metal);
    bead.position.set(x, y, baseZ + 0.42 * stoneScale);
    group.add(bead);
  }
}

function cylinderBetween(start, end, radius, material, segments = 16) {
  const direction = new THREE.Vector3().subVectors(end, start);
  const length = direction.length();
  const geometry = new THREE.CylinderGeometry(radius, radius, length, segments);
  const object = mesh(geometry, material);
  object.position.copy(start).add(end).multiplyScalar(0.5);
  object.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), direction.normalize());
  return object;
}

function createPearGeometry() {
  const shape = new THREE.Shape();
  shape.moveTo(0, 0.64);
  shape.bezierCurveTo(0.48, 0.38, 0.52, -0.18, 0, -0.68);
  shape.bezierCurveTo(-0.52, -0.18, -0.48, 0.38, 0, 0.64);
  return new THREE.ExtrudeGeometry(shape, {
    depth: 0.18,
    bevelEnabled: true,
    bevelSize: 0.025,
    bevelThickness: 0.025,
    bevelSegments: 1,
  }).center();
}

function mesh(geometry, material) {
  const object = new THREE.Mesh(geometry, material);
  object.castShadow = true;
  object.receiveShadow = true;
  return object;
}

function disposeGroup(group) {
  while (group.children.length) {
    const child = group.children[0];
    group.remove(child);
    child.traverse?.((object) => {
      object.geometry?.dispose?.();
      if (Array.isArray(object.material)) {
        object.material.forEach((material) => material.dispose?.());
      } else {
        object.material?.dispose?.();
      }
    });
  }
}

function addDrag(canvas, api) {
  let dragging = false;
  let lastX = 0;
  let lastY = 0;
  canvas.addEventListener("pointerdown", (event) => {
    dragging = true;
    lastX = event.clientX;
    lastY = event.clientY;
    canvas.setPointerCapture(event.pointerId);
  });
  canvas.addEventListener("pointermove", (event) => {
    if (!dragging) return;
    api.targetY += (event.clientX - lastX) * 0.01;
    api.targetX += (event.clientY - lastY) * 0.01;
    api.targetX = clamp(api.targetX, -0.95, 0.62);
    lastX = event.clientX;
    lastY = event.clientY;
  });
  canvas.addEventListener("pointerup", () => {
    dragging = false;
  });
  canvas.addEventListener("pointercancel", () => {
    dragging = false;
  });
}

function animate() {
  heroScene?.render();
  studioScene?.render();
  requestAnimationFrame(animate);
}

function resizeRenderer(renderer, camera, canvas) {
  const rect = canvas.getBoundingClientRect();
  const width = Math.max(1, Math.floor(rect.width));
  const height = Math.max(1, Math.floor(rect.height));
  if (canvas.dataset.renderWidth !== String(width) || canvas.dataset.renderHeight !== String(height)) {
    renderer.setSize(width, height, false);
    camera.aspect = width / height;
    camera.updateProjectionMatrix();
    canvas.dataset.renderWidth = String(width);
    canvas.dataset.renderHeight = String(height);
  }
}

function updateUi() {
  updateBandOutput();
  updateSpecPanel();
  updateActiveControls();
  updateAssetControls();
  updateProgress();
}

function updateBandOutput() {
  els.bandOutput.value = `${Number(els.bandInput.value).toFixed(1)} mm`;
}

function updateSpecPanel() {
  const preview = state.preview;
  const result = state.result;
  const confidence = result?.extraction?.confidence ?? (state.mode === "entry" ? 0.52 : 0.68);
  const setting = preview.center_setting || els.settingInput.value;
  els.confidenceRing.style.setProperty("--score", confidence);
  els.confidenceValue.textContent = `${Math.round(confidence * 100)}%`;
  els.shapeValue.textContent = title(preview.stone_shape);
  els.settingValue.textContent = title(setting);
  els.prongValue.textContent = String(preview.prong_count || 0);
  els.metalValue.textContent = title(metalLabel(preview.metal_family));
  const dims = preview.stone_dimensions_mm;
  els.stoneValue.textContent = `${Number(dims.length).toFixed(2)} x ${Number(dims.width).toFixed(2)} mm`;
  els.heroShapeValue.textContent = `${title(preview.stone_shape)} solitaire`;
  els.heroMetalValue.textContent = metalLabel(preview.metal_family);
  els.viewportShape.textContent = `${title(preview.stone_shape)} ${title(setting)}`;
  els.viewportSpecs.textContent = `${Number(preview.carat || readNumber(els.caratInput, 1)).toFixed(2)} ct | ${Number(preview.band_width_mm).toFixed(1)} mm band`;
}

function updateActiveControls() {
  const shape = state.preview.stone_shape;
  els.shapeChips.forEach((button) => button.classList.toggle("is-active", button.dataset.shape === shape));
  els.metalSwatches.forEach((button) => button.classList.toggle("is-active", button.dataset.metal === state.preview.metal_family));
}

function updateAssetControls() {
  const canRequest = state.mode === "scratch" || state.mode === "workspace" || Boolean(state.file) || Boolean(state.result?.rhino?.ready_for_handoff);
  els.generateButton.disabled = !canRequest;
  els.rhinoButton.disabled = !canRequest;
}

function updateProgress() {
  const analyzed = Boolean(state.result && !state.needsAnalysis);
  const exported = Boolean(state.assets);
  els.analysisStep.classList.toggle("is-done", analyzed);
  els.analysisStep.classList.toggle("is-active", state.mode !== "entry" && !analyzed);
  els.assetStep.classList.toggle("is-done", exported);
  els.assetStep.classList.toggle("is-active", analyzed && !exported);
}

function setStatus(message, tone) {
  els.statusBox.textContent = message;
  els.statusBox.classList.toggle("is-error", tone === "error");
  els.statusBox.classList.toggle("is-ok", tone === "ok");
}

function setBusy(button, busy, label) {
  button.disabled = busy;
  button.textContent = busy ? `${label}...` : label;
}

function markDirty() {
  state.result = null;
  state.assets = null;
  state.needsAnalysis = true;
  els.downloadList.innerHTML = "";
}

function syntheticFilename() {
  return `${state.preview.metal_family}-${state.preview.stone_shape}-scratch-ring`;
}

function readNumber(input, fallback) {
  const value = Number(input.value);
  return Number.isFinite(value) ? value : fallback;
}

function title(value) {
  return String(value || "").replace(/_/g, " ").replace(/\b\w/g, (char) => char.toUpperCase());
}

function metalLabel(family) {
  if (family === "white") return "Platinum ice";
  if (family === "rose") return "Rose gold";
  return "Champagne gold";
}

function metalColor(family) {
  if (family === "white") return 0xeef0ee;
  if (family === "rose") return 0xd49a8f;
  return 0xd4af6a;
}

function gemSegmentCount(shape) {
  if (shape === "emerald" || shape === "princess") return 4;
  if (shape === "radiant") return 8;
  if (shape === "cushion") return 24;
  return 72;
}

function inferProngCount(shape, carat, setting) {
  if (setting !== "prong") return 0;
  if (shape === "pear") return 5;
  if (shape === "round" && carat >= 1.35) return 6;
  return 4;
}

function baseDims(shape) {
  const table = {
    round: [6.5, 6.5, 3.9],
    oval: [8.0, 6.0, 3.8],
    cushion: [6.5, 6.5, 4.2],
    pear: [8.5, 5.5, 3.7],
    emerald: [7.0, 5.0, 3.5],
    radiant: [6.5, 5.5, 3.8],
    princess: [5.5, 5.5, 4.0],
  };
  return table[shape] || table.round;
}

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}
