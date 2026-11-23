// Aureon 3D viewers behind one small interface, so the Reveal can swap renderers.
//
//   const viewer = await createViewer(container, { prefer: "ijewel" });
//   await viewer.show(glbUrl);   // resolves once the ring is on screen
//   viewer.name;                 // "ijewel" | "model-viewer"
//   viewer.dispose();
//
// "ijewel": iJewel3D mini-viewer on the WebGi v0 engine. Renders OUR local GLB (nothing
// is uploaded) and turns meshes named "Gem *" into its ray-traced diamond material.
// Telemetry is switched off; the SDK still sends its domain licence check (hostname +
// version only). Unlicensed for production: see docs/aureon_viewer_decision.md.
// "model-viewer": Google model-viewer with a synthetic studio HDRI and a tuned
// transmissive diamond. Used when iJewel can't load (offline, blocked) or by ?viewer=.

const SOURCES = {
  webgi: "https://releases.ijewel3d.com/libs/webgi-v0/bundle-0.22.0.js",
  miniViewer: "https://releases.ijewel3d.com/libs/mini-viewer/0.6.27/bundle.nowebgi.iife.js",
  modelViewer: "https://cdn.jsdelivr.net/npm/@google/model-viewer@3.5.0/dist/model-viewer.min.js",
};
const STUDIO_HDR = "/static/env/studio_softbox.hdr";
const SCENE_TIMEOUT_MS = 30000;

const scripts = new Map();
function loadScript(src, { module = false, timeout = 25000 } = {}) {
  if (scripts.has(src)) return scripts.get(src);
  const promise = new Promise((resolve, reject) => {
    const el = document.createElement("script");
    el.src = src;
    if (module) el.type = "module";
    const timer = setTimeout(() => reject(new Error(`timed out loading ${src}`)), timeout);
    el.onload = () => { clearTimeout(timer); resolve(); };
    el.onerror = () => { clearTimeout(timer); reject(new Error(`could not load ${src}`)); };
    document.head.appendChild(el);
  });
  promise.catch(() => scripts.delete(src));
  scripts.set(src, promise);
  return promise;
}

async function loadIJewel() {
  await loadScript(SOURCES.webgi);
  await loadScript(SOURCES.miniViewer);
  if (!window.ijewelViewer || !window.ijewelViewer.Viewer) throw new Error("iJewel viewer missing");
}

function once(target, type, timeout) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => { target.removeEventListener(type, on); reject(new Error(`${type} timed out`)); }, timeout);
    function on(ev) { clearTimeout(timer); target.removeEventListener(type, on); resolve(ev); }
    target.addEventListener(type, on);
  });
}

class IJewelViewer {
  constructor(container, { autoRotate = true } = {}) {
    this.name = "ijewel";
    this.label = "iJewel3D · WebGi";
    this.container = container;
    this.autoRotate = autoRotate;
    this.instance = null;
    this.app = null;
    this.onReady = (ev) => { this.app = ev.detail && ev.detail.viewer; };
  }

  async mount() {
    await loadIJewel();
    window.addEventListener("ijewel-viewer-ready", this.onReady);
  }

  async show(url) {
    const project = { modelUrl: new URL(url, location.href).href, name: "Aureon ring" };
    const sceneReady = once(window, "ijewel-scene-ready", SCENE_TIMEOUT_MS);
    if (!this.instance) {
      this.instance = new window.ijewelViewer.Viewer(this.container, project, {
        showLogo: true, // attribution stays on: no licence yet
        showCard: false, showShareButton: false, showConfigurator: false, showSwitchNode: false,
        hideTryOn: true, hideQuality: true, hideCameraViews: true, hideFitScene: true,
        runRotateCamera: this.autoRotate,
        trackingDisabled: true, // no usage analytics to iJewel
      });
    } else {
      this.instance.render(project);
    }
    await sceneReady;
    this.applyDiamonds();
    this.heroView();
    this.settleDiamonds();
  }

  // On a second model in the same viewer, "ijewel-scene-ready" can fire before the new
  // meshes are in the scene, and a stone left on its glTF material renders black. Keep
  // converting for a few seconds (applyDiamonds skips stones that are already diamonds).
  settleDiamonds() {
    const token = (this.settleToken = (this.settleToken || 0) + 1);
    let ticks = 0;
    const tick = () => {
      if (token !== this.settleToken || !this.instance) return;
      this.applyDiamonds();
      if (++ticks < 16) setTimeout(tick, 350);
    };
    setTimeout(tick, 250);
  }

  // Named camera views. iJewel normalises every model to the same size on load, so
  // one set of coordinates frames every ring. "hero": whole ring, three-quarter view
  // from above; "detail": the head and shoulders (style thumbnails).
  heroView() { this.setView("hero"); }

  setView(name) {
    const views = { hero: [[2.9, 3.6, 7.9], [0, 0.2, 0]], detail: [[2.0, 2.9, 5.0], [0, 0.8, 0]] };
    const app = this.app || (this.instance && this.instance.viewerApp);
    const cam = app && app.scene && app.scene.activeCamera;
    if (!cam || !cam.positionTargetUpdated || !views[name]) return;
    cam.position.set(...views[name][0]);
    cam.target.set(...views[name][1]);
    cam.positionTargetUpdated(true);
    app.setDirty && app.setDirty();
  }

  applyDiamonds() {
    const app = this.app || (this.instance && this.instance.viewerApp);
    const diamond = app && app.plugins && app.plugins.Diamond;
    if (!app || !diamond) return 0;
    const gems = [];
    app.scene.traverse((o) => { if (o.isMesh && /^gem/i.test(o.name || "")) gems.push(o); });
    // Each gem mesh needs its OWN material (glb_materials.style_glb writes one per gem
    // mesh): makeDiamond bakes the geometry of the first mesh using a material, so a
    // shared one gave the center stone the melee's map and it rendered black.
    for (const gem of gems) {
      if (!gem.material || gem.material.isDiamondMaterial) continue;
      const key = (gem.geometry && gem.geometry.uuid) || gem.uuid;
      diamond.makeDiamond(gem.material, { cacheKey: `aureon_${key}`, normalMapRes: 512 },
        { isDiamond: true, refractiveIndex: 2.42 });
    }
    app.setDirty && app.setDirty();
    return gems.length;
  }

  dispose() {
    window.removeEventListener("ijewel-viewer-ready", this.onReady);
    try { this.instance && this.instance.root && this.instance.root.unmount(); } catch (e) { /* ignore */ }
    this.instance = null;
    this.container.innerHTML = "";
  }
}

class ModelViewerFallback {
  constructor(container, { autoRotate = true } = {}) {
    this.name = "model-viewer";
    this.label = "model-viewer · studio light";
    this.container = container;
    this.autoRotate = autoRotate;
    this.el = null;
  }

  async mount() {
    await loadScript(SOURCES.modelViewer, { module: true });
    await customElements.whenDefined("model-viewer");
    const el = document.createElement("model-viewer");
    const attrs = {
      "camera-controls": "", "auto-rotate": "", "auto-rotate-delay": "600", "rotation-per-second": "16deg",
      "camera-orbit": "22deg 66deg 105%", "min-camera-orbit": "auto auto 60%", "field-of-view": "28deg",
      "environment-image": STUDIO_HDR, "exposure": "1.15", "tone-mapping": "neutral",
      "shadow-intensity": "0.6", "shadow-softness": "0.9", "interaction-prompt": "none",
      "loading": "eager", "alt": "3D view of your ring",
    };
    if (!this.autoRotate) delete attrs["auto-rotate"];
    for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
    el.style.cssText = "position:absolute;inset:0;width:100%;height:100%;background:transparent;--poster-color:transparent";
    this.container.appendChild(el);
    this.el = el;
  }

  tuneDiamond() {
    const model = this.el.model;
    if (!model) return;
    for (const material of model.materials) {
      if (!/diamond|gem/i.test(material.name)) continue;
      material.pbrMetallicRoughness.setRoughnessFactor(0);
      material.pbrMetallicRoughness.setMetallicFactor(0);
      if (material.setEmissiveStrength) material.setEmissiveStrength(0);
      if (material.ior && material.ior.setIor) material.ior.setIor(2.42);
      if (material.transmission && material.transmission.setTransmissionFactor) {
        material.transmission.setTransmissionFactor(1);
      }
    }
  }

  async load(url) {
    const loaded = once(this.el, "load", SCENE_TIMEOUT_MS);
    this.el.src = url;
    await loaded;
  }

  async show(url) {
    const bust = (n) => `${url}${url.includes("?") ? "&" : "?"}v=${Date.now()}${n}`;
    await this.load(bust("a"));
    // Workaround kept from v1 (not root-caused): model-viewer 3.5 sometimes leaves the
    // canvas blank on a build's first load; loading the same file again always draws it.
    await this.load(bust("b"));
    this.tuneDiamond();
  }

  dispose() {
    if (this.el) this.el.remove();
    this.el = null;
  }
}

const KINDS = { ijewel: IJewelViewer, "model-viewer": ModelViewerFallback };

/** Warm the network for the preferred viewer (call when the user reaches Review). */
export function preloadViewer(prefer = "ijewel") {
  const task = prefer === "ijewel" ? loadIJewel() : loadScript(SOURCES.modelViewer, { module: true });
  task.catch(() => {});
}

/** Mount the preferred viewer; fall back to model-viewer if it can't load. */
export async function createViewer(container, { prefer = "ijewel", autoRotate = true } = {}) {
  const order = prefer === "model-viewer" ? ["model-viewer"] : [prefer, "model-viewer"];
  let lastError = null;
  for (const kind of order) {
    const Viewer = KINDS[kind];
    if (!Viewer) continue;
    const viewer = new Viewer(container, { autoRotate });
    try {
      await viewer.mount();
      return viewer;
    } catch (err) {
      lastError = err;
      console.warn(`[aureon] ${kind} viewer unavailable:`, err);
      viewer.dispose();
    }
  }
  throw lastError || new Error("no 3D viewer available");
}
