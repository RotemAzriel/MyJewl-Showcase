/**
 * Shared production-preview renderer for the bounded MyJewl ring contract.
 *
 * Browser geometry is visual-only. Rhino/Grasshopper remains the sole
 * manufacturing geometry authority and every manufacturing gate remains
 * pending/not_run/needs_calibration until its separate review path.
 */

import {
  FACET_PROFILES,
  FINISH_PRESETS,
  METAL_PRESETS,
  PURITY_PRESETS,
  PREVIEW_PROFILE_VERSION,
  QUALITY_PROFILES,
  STUDIO_ENVIRONMENT_VERSION,
  facetProfileFor,
  metalPresetFor,
  previewGeometrySignature,
  previewMaterialSignature,
} from './preview_profiles_v2.js';

const THREE_LOCAL = './vendor/three-r166';
const SETTLE_DELAY_MS = 900;
const AUTO_ROTATE_RADIANS_PER_SECOND = 0.34;
const ASSET_FETCH_TIMEOUT_MS = 15000;
const PURPOSE_OPERATIONAL_AID = 'operational_aid';
const PURPOSE_PRODUCTION_PREVIEW = 'production_preview';
const OPERATIONAL_V4_AID = Object.freeze({
  headStyleId: 'head_v4_mid_petal_4_prong',
  templateId: 'goal0011-realism-first-round-solitaire-master-v4',
});

let THREE = null;
let EffectComposer = null;
let RenderPass = null;
let UnrealBloomPass = null;
let BokehPass = null;
let depsPromise = null;
const verifiedAssetPayloadPromises = new Map();

async function loadThree() {
  if (!THREE) THREE = await import(`${THREE_LOCAL}/three.module.min.js`);
  return THREE;
}

async function loadDeps() {
  if (depsPromise) return depsPromise;
  const pending = (async () => {
    await loadThree();
    const [composer, render, bloom, bokeh] = await Promise.all([
      import(`${THREE_LOCAL}/postprocessing/EffectComposer.js`),
      import(`${THREE_LOCAL}/postprocessing/RenderPass.js`),
      import(`${THREE_LOCAL}/postprocessing/UnrealBloomPass.js`),
      import(`${THREE_LOCAL}/postprocessing/BokehPass.js`),
    ]);
    EffectComposer = composer.EffectComposer;
    RenderPass = render.RenderPass;
    UnrealBloomPass = bloom.UnrealBloomPass;
    BokehPass = bokeh.BokehPass;
  })();
  depsPromise = pending;
  try {
    return await pending;
  } catch (error) {
    if (depsPromise === pending) depsPromise = null;
    throw error;
  }
}

// ── Preview-only materials and environment ─────────────────

function metalMaterial(family, purity, envMap, finish = 'polished') {
  const preset = metalPresetFor(family, purity, finish);
  const mat = new THREE.MeshPhysicalMaterial({
    color: preset.color,
    metalness: 1,
    roughness: preset.finish.roughness,
    clearcoat: preset.finish.clearcoat,
    clearcoatRoughness: preset.finish.clearcoatRoughness,
    anisotropy: preset.finish.anisotropy,
    anisotropyRotation: preset.finish.anisotropyRotation,
    envMapIntensity: preset.envMapIntensity,
  });
  mat.envMap = envMap;
  mat.name = `${preset.metal.id}:${preset.purity.id}:${preset.finish.id}`;
  mat.userData.previewOnly = true;
  return mat;
}

function gemMaterial(envMap) {
  const mat = new THREE.MeshPhysicalMaterial({
    color: 0xe9f4ff,
    metalness: 0,
    roughness: 0.012,
    transmission: 0.96,
    thickness: 0.38,
    ior: 2.417,
    clearcoat: 0.68,
    clearcoatRoughness: 0.0,
    envMapIntensity: 3.4,
    specularIntensity: 1,
    specularColor: new THREE.Color(0xffffff),
    attenuationColor: new THREE.Color(0xffffff),
    attenuationDistance: Infinity,
    dispersion: 0.012,
    flatShading: true,
    side: THREE.DoubleSide,
    depthWrite: true,
  });
  mat.envMap = envMap;
  mat.name = 'diamond-preview-optics-v1';
  mat.userData = {
    previewOnly: true,
    iorBasis: 'diamond_visual_constant',
    opticalClaim: 'none',
  };
  return mat;
}

function wireframeMaterial() {
  return new THREE.MeshBasicMaterial({
    color: 0xc9a96e,
    wireframe: true,
    transparent: true,
    opacity: 0.7,
  });
}

// This branch is deliberately procedural and may be used only in a viewport
// whose purpose is explicitly operational_aid. It never selects a production
// preview asset; only a server-issued PreviewManifest can do that.
function isV4OperationalAid(preview = {}) {
  return (
    preview.head_style_id === OPERATIONAL_V4_AID.headStyleId
    && preview.reference_geometry_id === OPERATIONAL_V4_AID.templateId
  );
}

async function sha256Hex(buffer) {
  if (!globalThis.crypto?.subtle) {
    throw new PreviewDeliveryError('crypto_unavailable', 'SHA-256 verification is unavailable.');
  }
  const digest = await globalThis.crypto.subtle.digest('SHA-256', buffer);
  return Array.from(new Uint8Array(digest))
    .map(value => value.toString(16).padStart(2, '0'))
    .join('');
}

class PreviewDeliveryError extends Error {
  constructor(code, message) {
    super(message);
    this.name = 'PreviewDeliveryError';
    this.code = code;
  }
}

function isSha256(value, prefixed = false) {
  return new RegExp(prefixed ? '^sha256:[0-9a-f]{64}$' : '^[0-9a-f]{64}$').test(String(value || ''));
}

function sameOriginAssetUrl(value) {
  if (
    typeof value !== 'string'
    || !value.startsWith('/static/assets/')
    || value.includes('\\')
    || value.includes('?')
    || value.includes('#')
  ) {
    throw new PreviewDeliveryError('asset_url_rejected', 'Preview asset URL is outside the local asset root.');
  }
  const base = globalThis.location?.href || 'http://localhost/';
  const resolved = new URL(value, base);
  let decodedPath;
  try {
    decodedPath = decodeURIComponent(resolved.pathname);
  } catch (_error) {
    throw new PreviewDeliveryError('asset_url_rejected', 'Preview asset URL encoding is invalid.');
  }
  if (
    (globalThis.location?.origin && resolved.origin !== globalThis.location.origin)
    || !resolved.pathname.startsWith('/static/assets/')
    || !decodedPath.startsWith('/static/assets/')
    || decodedPath.split('/').includes('..')
    || resolved.search
    || resolved.hash
  ) {
    throw new PreviewDeliveryError('asset_url_rejected', 'Preview asset URL must be same-origin.');
  }
  return resolved.href;
}

function selectedAssetFor(previewManifest) {
  if (!previewManifest || previewManifest.schema !== 'myjewl.preview_manifest.v0_1') {
    throw new PreviewDeliveryError('manifest_invalid', 'PreviewManifest schema is missing or unsupported.');
  }
  if (
    previewManifest.manufacturing_authority !== false
    || previewManifest.manufacturing_state !== 'pending_not_run_needs_calibration'
    || !isSha256(previewManifest.manifest_id, true)
    || !isSha256(previewManifest.geometry?.signature, true)
  ) {
    throw new PreviewDeliveryError('manifest_invalid', 'PreviewManifest authority or identity is invalid.');
  }
  const status = previewManifest.preview_status;
  const representation = previewManifest.representation_kind;
  if (!['exact', 'estimated'].includes(status)) {
    throw new PreviewDeliveryError(
      status === 'unavailable' ? previewManifest.reason_code || 'preview_unavailable' : 'manifest_invalid',
      'No production preview asset was resolved.',
    );
  }
  if (!['full_ring_asset', 'approved_assembly'].includes(representation)) {
    throw new PreviewDeliveryError('representation_mismatch', 'Resolved preview representation is inconsistent.');
  }
  const asset = previewManifest.selected_asset;
  const deliveryManifest = asset?.delivery_manifest;
  const content = asset?.content;
  if (
    !asset
    || asset.classification !== status
    || asset.asset_type !== 'full_ring'
    || asset.renderer !== 'threejs-r166'
    || asset.format !== 'myjewl_meshbin_v1'
    || !asset.asset_id
    || !asset.asset_version
    || !isSha256(deliveryManifest?.sha256)
    || !Number.isInteger(deliveryManifest?.byte_size)
    || deliveryManifest.byte_size <= 0
    || !isSha256(content?.sha256)
    || !Number.isInteger(content?.byte_size)
    || content.byte_size <= 0
    || !isSha256(asset.source_receipt_sha256)
    || !Number.isInteger(asset.vertex_count)
    || !Number.isInteger(asset.index_count)
    || !Number.isInteger(asset.triangle_count)
    || !Number.isInteger(asset.part_count)
  ) {
    throw new PreviewDeliveryError('asset_metadata_invalid', 'Selected preview asset metadata is incomplete.');
  }
  sameOriginAssetUrl(deliveryManifest.url);
  sameOriginAssetUrl(content.url);
  return asset;
}

async function fetchBytes(fileReference, signal) {
  let response;
  try {
    response = await fetch(sameOriginAssetUrl(fileReference.url), {
      cache: 'no-store',
      credentials: 'same-origin',
      signal,
    });
  } catch (error) {
    const reason = error?.name === 'AbortError' ? 'asset_timeout' : 'asset_unavailable';
    throw new PreviewDeliveryError(reason, 'Preview asset could not be fetched.');
  }
  if (!response.ok) {
    throw new PreviewDeliveryError('asset_unavailable', `Preview asset request failed (${response.status}).`);
  }
  const bytes = await response.arrayBuffer();
  if (bytes.byteLength !== fileReference.byte_size) {
    throw new PreviewDeliveryError('asset_size_mismatch', 'Preview asset byte size does not match the registry.');
  }
  if (await sha256Hex(bytes) !== fileReference.sha256) {
    throw new PreviewDeliveryError('asset_hash_mismatch', 'Preview asset hash does not match the registry.');
  }
  return bytes;
}

function verifyDeliveryManifest(manifest, asset) {
  if (
    manifest?.schema !== 'myjewl.goal0011.receipt_backed_browser_mesh.v1'
    || manifest.authority !== 'receipt_backed_rhino_derived_browser_preview'
    || manifest.manufacturing_authority !== false
    || manifest.template_id !== asset.template_id
    || manifest.source_receipt_sha256 !== asset.source_receipt_sha256
    || manifest.vertex_count !== asset.vertex_count
    || manifest.index_count !== asset.index_count
    || manifest.triangle_count !== asset.triangle_count
    || manifest.binary?.sha256 !== asset.content.sha256
    || manifest.binary?.byte_size !== asset.content.byte_size
    || !Array.isArray(manifest.parts)
    || manifest.parts.length !== asset.part_count
    || !Number.isFinite(manifest.source_to_preview_transform?.rotation_x_radians)
    || !Number.isFinite(manifest.source_to_preview_transform?.scale)
    || manifest.source_to_preview_transform.scale <= 0
  ) {
    throw new PreviewDeliveryError('delivery_manifest_mismatch', 'Delivery manifest does not match the selected registry asset.');
  }
  let nextIndex = 0;
  manifest.parts.forEach((part) => {
    if (
      !part
      || !['metal', 'stone'].includes(part.role)
      || !Number.isInteger(part.index_start)
      || !Number.isInteger(part.index_count)
      || part.index_start !== nextIndex
      || part.index_count <= 0
      || part.index_count % 3 !== 0
      || part.triangles !== part.index_count / 3
    ) {
      throw new PreviewDeliveryError('delivery_manifest_mismatch', 'Delivery manifest contains invalid named-part ranges.');
    }
    nextIndex += part.index_count;
  });
  if (nextIndex !== asset.index_count) {
    throw new PreviewDeliveryError('delivery_manifest_mismatch', 'Delivery manifest named-part ranges are incomplete.');
  }
}

function verifyMeshBinary(buffer, asset) {
  if (buffer.byteLength < 16) {
    throw new PreviewDeliveryError('binary_header_invalid', 'Preview mesh header is incomplete.');
  }
  const header = new DataView(buffer, 0, 16);
  const magic = String.fromCharCode(...new Uint8Array(buffer, 0, 8));
  const vertexCount = header.getUint32(8, true);
  const indexCount = header.getUint32(12, true);
  const positionOffset = 16;
  const normalOffset = positionOffset + vertexCount * 3 * Float32Array.BYTES_PER_ELEMENT;
  const indexOffset = normalOffset + vertexCount * 3 * Float32Array.BYTES_PER_ELEMENT;
  const expectedBytes = indexOffset + indexCount * Uint32Array.BYTES_PER_ELEMENT;
  if (
    magic !== 'MYJV4B1\0'
    || vertexCount !== asset.vertex_count
    || indexCount !== asset.index_count
    || asset.triangle_count * 3 !== indexCount
    || expectedBytes !== buffer.byteLength
  ) {
    throw new PreviewDeliveryError('binary_contract_mismatch', 'Preview mesh binary contract is invalid.');
  }
  const positions = new Float32Array(buffer, positionOffset, vertexCount * 3);
  const normals = new Float32Array(buffer, normalOffset, vertexCount * 3);
  const indices = new Uint32Array(buffer, indexOffset, indexCount);
  if (
    positions.some(value => !Number.isFinite(value))
    || normals.some(value => !Number.isFinite(value))
    || indices.some(index => index >= vertexCount)
  ) {
    throw new PreviewDeliveryError('binary_contract_mismatch', 'Preview mesh binary data is out of range.');
  }
  return { positionOffset, normalOffset, indexOffset };
}

async function loadVerifiedAssetPayload(previewManifest, retry = false) {
  const asset = selectedAssetFor(previewManifest);
  const cacheKey = [asset.asset_id, asset.asset_version, asset.delivery_manifest.sha256, asset.content.sha256].join(':');
  if (retry) verifiedAssetPayloadPromises.delete(cacheKey);
  if (verifiedAssetPayloadPromises.has(cacheKey)) return verifiedAssetPayloadPromises.get(cacheKey);

  const promise = (async () => {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), ASSET_FETCH_TIMEOUT_MS);
    try {
      const deliveryBytes = await fetchBytes(asset.delivery_manifest, controller.signal);
      let deliveryManifest;
      try {
        deliveryManifest = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(deliveryBytes));
      } catch (_error) {
        throw new PreviewDeliveryError('delivery_manifest_malformed', 'Delivery manifest is not valid UTF-8 JSON.');
      }
      verifyDeliveryManifest(deliveryManifest, asset);
      const buffer = await fetchBytes(asset.content, controller.signal);
      const offsets = verifyMeshBinary(buffer, asset);
      return { manifest: deliveryManifest, asset, buffer, ...offsets };
    } finally {
      clearTimeout(timeout);
    }
  })();
  verifiedAssetPayloadPromises.set(cacheKey, promise);
  promise.catch(() => {
    if (verifiedAssetPayloadPromises.get(cacheKey) === promise) {
      verifiedAssetPayloadPromises.delete(cacheKey);
    }
  });
  return promise;
}

function buildVerifiedAssetGroup(preview, previewManifest, envMap, mode, payload) {
  const { manifest, buffer, positionOffset, normalOffset, indexOffset } = payload;
  const positions = new Float32Array(buffer, positionOffset, manifest.vertex_count * 3);
  const normals = new Float32Array(buffer, normalOffset, manifest.vertex_count * 3);
  const indices = new Uint32Array(buffer, indexOffset, manifest.index_count);
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
  geometry.setAttribute('normal', new THREE.BufferAttribute(normals, 3));
  geometry.setIndex(new THREE.BufferAttribute(indices, 1));
  geometry.clearGroups();
  manifest.parts.forEach(part => {
    geometry.addGroup(part.index_start, part.index_count, part.role === 'stone' ? 1 : 0);
  });
  geometry.computeBoundingBox();
  geometry.computeBoundingSphere();
  geometry.userData = {
    source: 'Azure Rhino.Compute named-part OBJ',
    sourceReceiptSha256: manifest.source_receipt_sha256,
    sourceObjSha256: manifest.source_obj_sha256,
    binarySha256: manifest.binary.sha256,
    namedParts: manifest.parts.map(part => ({ ...part })),
    manufacturingAuthority: false,
  };

  const materials = mode === 'wireframe'
    ? [wireframeMaterial(), wireframeMaterial()]
    : [
      metalMaterial(
        preview.metal_family,
        preview.metal_purity || preview.metal?.purity,
        envMap,
        preview.metal_finish,
      ),
      gemMaterial(envMap),
    ];
  const mesh = new THREE.Mesh(geometry, materials);
  mesh.name = `${payload.asset.asset_id}-verified-named-parts`;
  mesh.castShadow = true;
  mesh.receiveShadow = true;

  const sourceFrame = new THREE.Group();
  sourceFrame.name = 'goal0011-v4-rhino-z-up-source-frame';
  sourceFrame.rotation.x = manifest.source_to_preview_transform.rotation_x_radians;
  sourceFrame.scale.setScalar(manifest.source_to_preview_transform.scale);
  sourceFrame.add(mesh);

  const group = new THREE.Group();
  group.name = `${payload.asset.asset_id}-verified-production-preview`;
  group.add(sourceFrame);
  group.rotation.set(0.38, -0.36, 0);
  group.userData = {
    geometrySignature: previewManifest.geometry.signature,
    materialSignature: previewManifest.appearance_signature || previewMaterialSignature(preview),
    facetProfileVersion: PREVIEW_PROFILE_VERSION,
    facetProfileId: facetProfileFor(preview.stone_shape || 'round').id,
    stoneShape: previewManifest.geometry.projection?.stones
      ?.find(stone => stone?.role === 'center')?.shape || preview.stone_shape || 'round',
    stoneCounts: { center: 1, side: 0, accent: 0 },
    geometryAuthority: 'receipt_backed_rhino_derived_browser_preview',
    sourceReceiptSha256: manifest.source_receipt_sha256,
    sourceObjSha256: manifest.source_obj_sha256,
    referenceAssetSha256: manifest.binary.sha256,
    referencePartCount: manifest.parts.length,
    referenceTriangleCount: manifest.triangle_count,
    previewStatus: previewManifest.preview_status,
    representationKind: previewManifest.representation_kind,
    assetId: payload.asset.asset_id,
    assetVersion: payload.asset.asset_version,
    manufacturingAuthority: false,
  };
  return group;
}

function neutralStudioEnvironment(renderer) {
  const env = new THREE.Scene();
  env.name = STUDIO_ENVIRONMENT_VERSION;
  const box = new THREE.BoxGeometry(1, 1, 1);

  const roomMaterial = new THREE.MeshBasicMaterial({
    color: 0x3d444b,
    side: THREE.BackSide,
    toneMapped: false,
  });
  const room = new THREE.Mesh(box, roomMaterial);
  room.scale.set(24, 18, 24);
  env.add(room);

  const card = (color, intensity, position, scale, rotation = [0, 0, 0]) => {
    const material = new THREE.MeshBasicMaterial({ toneMapped: false });
    const c = new THREE.Color(color);
    material.color.setRGB(c.r * intensity, c.g * intensity, c.b * intensity);
    const mesh = new THREE.Mesh(box, material);
    mesh.position.set(...position);
    mesh.scale.set(...scale);
    mesh.rotation.set(...rotation);
    env.add(mesh);
  };

  // Neutral soft boxes and deliberate black flags shape long metal reflections.
  card(0xffffff, 8.0, [-7.5, 5.0, 5.5], [0.12, 5.2, 4.0], [0, -0.25, 0]);
  card(0xeaf1ff, 4.0, [7.0, 2.0, 3.0], [0.12, 3.8, 3.2], [0, 0.32, 0]);
  card(0xffffff, 5.5, [0.0, 8.0, -2.0], [4.5, 0.12, 4.5]);
  card(0xffffff, 2.2, [0.0, -4.0, 5.5], [3.0, 0.12, 2.2], [0.28, 0, 0]);
  card(0xf7fbff, 2.6, [4.8, 0.8, 8.2], [2.2, 4.5, 0.12]);
  card(0x000000, 1.0, [-1.1, 0.4, 8.0], [0.48, 4.4, 0.1]);
  card(0x000000, 1.0, [-3.0, 0.5, -7.0], [1.4, 5.0, 0.1], [0, -0.22, 0]);
  card(0x000000, 1.0, [3.6, 0.0, -6.5], [0.8, 4.0, 0.1], [0, 0.28, 0]);

  const pmrem = new THREE.PMREMGenerator(renderer);
  pmrem.compileCubemapShader();
  const target = pmrem.fromScene(env, 0.035);
  pmrem.dispose();
  disposeSceneResources(env);
  target.texture.name = STUDIO_ENVIRONMENT_VERSION;
  return target;
}

function backgroundTexture() {
  const surface = document.createElement('canvas');
  surface.width = 8;
  surface.height = 256;
  const ctx = surface.getContext('2d');
  const gradient = ctx.createLinearGradient(0, 0, 0, surface.height);
  gradient.addColorStop(0, '#293038');
  gradient.addColorStop(0.46, '#151b20');
  gradient.addColorStop(1, '#07090b');
  ctx.fillStyle = gradient;
  ctx.fillRect(0, 0, surface.width, surface.height);
  const texture = new THREE.CanvasTexture(surface);
  texture.colorSpace = THREE.SRGBColorSpace;
  texture.name = 'myjewl-neutral-background-v1';
  return texture;
}

// ── Closed gemstone geometry ───────────────────────────────

function ellipseOutline(length, width, segments, phase = 0) {
  const points = [];
  for (let i = 0; i < segments; i++) {
    const angle = phase + Math.PI * 2 * i / segments;
    points.push([
      Math.cos(angle) * length / 2,
      Math.sin(angle) * width / 2,
    ]);
  }
  return points;
}

function pearOutline(length, width, segments, phase = 0) {
  const points = [];
  for (let i = 0; i < segments; i++) {
    const angle = phase + Math.PI * 2 * i / segments;
    const x = Math.cos(angle);
    const lobe = 0.78 - 0.22 * x;
    points.push([
      x * length / 2,
      Math.sin(angle) * width / 2 * lobe,
    ]);
  }
  const maxY = Math.max(...points.map(([, y]) => Math.abs(y))) || 1;
  return points.map(([x, y]) => [x, y * (width / 2) / maxY]);
}

function superellipseOutline(length, width, segments, exponent = 4, phase = 0) {
  const points = [];
  for (let i = 0; i < segments; i++) {
    const angle = phase + Math.PI * 2 * i / segments;
    const x = Math.cos(angle);
    const y = Math.sin(angle);
    points.push([
      Math.sign(x) * Math.pow(Math.abs(x), 2 / exponent) * length / 2,
      Math.sign(y) * Math.pow(Math.abs(y), 2 / exponent) * width / 2,
    ]);
  }
  return points;
}

function clippedRectangleOutline(length, width, segments, cornerCutRatio) {
  const halfL = length / 2;
  const halfW = width / 2;
  const cutX = length * cornerCutRatio;
  const cutY = width * cornerCutRatio;
  const corners = [
    [-halfL + cutX, -halfW], [halfL - cutX, -halfW],
    [halfL, -halfW + cutY], [halfL, halfW - cutY],
    [halfL - cutX, halfW], [-halfL + cutX, halfW],
    [-halfL, halfW - cutY], [-halfL, -halfW + cutY],
  ];
  const points = [];
  const samplesPerEdge = Math.max(1, Math.round(segments / corners.length));
  corners.forEach((start, index) => {
    const end = corners[(index + 1) % corners.length];
    for (let sample = 0; sample < samplesPerEdge; sample++) {
      const t = sample / samplesPerEdge;
      points.push([
        start[0] + (end[0] - start[0]) * t,
        start[1] + (end[1] - start[1]) * t,
      ]);
    }
  });
  return points;
}

function previewOutline(shape, length, width, segments, phase = 0) {
  const profile = facetProfileFor(shape);
  const count = segments || profile.outlineSegments;
  if (profile.outlineKind === 'pear') return pearOutline(length, width, count, phase);
  if (profile.outlineKind === 'cushion') {
    return superellipseOutline(length, width, count, profile.outlineExponent || 4, phase);
  }
  if (profile.outlineKind === 'clipped_rectangle' || profile.outlineKind === 'clipped_square') {
    return clippedRectangleOutline(length, width, count, profile.cornerCutRatio);
  }
  return ellipseOutline(length, width, count, phase);
}

function scaleRing(ring, scale) {
  return ring.map(([x, y]) => [x * scale, y * scale]);
}

function pushTriangle(vertices, a, b, c) {
  vertices.push(...a, ...b, ...c);
}

function capRing(vertices, ring, z, upward) {
  const center = [0, 0, z];
  for (let i = 0; i < ring.length; i++) {
    const next = (i + 1) % ring.length;
    const a = [...ring[i], z];
    const b = [...ring[next], z];
    if (upward) pushTriangle(vertices, center, a, b);
    else pushTriangle(vertices, center, b, a);
  }
}

function connectRings(vertices, upper, upperZ, lower, lowerZ) {
  for (let i = 0; i < upper.length; i++) {
    const next = (i + 1) % upper.length;
    const ua = [...upper[i], upperZ];
    const ub = [...upper[next], upperZ];
    const la = [...lower[i], lowerZ];
    const lb = [...lower[next], lowerZ];
    pushTriangle(vertices, ua, la, ub);
    pushTriangle(vertices, ub, la, lb);
  }
}

function buildFacetedGem(shape, length, width, depth) {
  const profile = facetProfileFor(shape);
  const n = profile.outlineSegments;
  const outline = previewOutline(shape, length, width, n);
  const table = scaleRing(outline, profile.tableRatio);
  const star = scaleRing(outline, profile.starRatio);
  const pavilionBreak = scaleRing(outline, profile.pavilionBreakRatio);
  const culet = scaleRing(outline, profile.culetRatio);
  const crownZ = depth * profile.crownHeightRatio;
  const starZ = crownZ * 0.47;
  const girdleTopZ = 0;
  const girdleBottomZ = -depth * profile.girdleThicknessRatio;
  const culetZ = -depth * (profile.girdleThicknessRatio + profile.pavilionDepthRatio);
  const pavilionBreakZ = girdleBottomZ + (culetZ - girdleBottomZ) * 0.61;
  const vertices = [];

  // Explicit table, crown, girdle, pavilion, and flat culet regions.
  capRing(vertices, table, crownZ, true);
  connectRings(vertices, table, crownZ, star, starZ);
  connectRings(vertices, star, starZ, outline, girdleTopZ);
  connectRings(vertices, outline, girdleTopZ, outline, girdleBottomZ);
  connectRings(vertices, outline, girdleBottomZ, pavilionBreak, pavilionBreakZ);
  connectRings(vertices, pavilionBreak, pavilionBreakZ, culet, culetZ);
  capRing(vertices, culet, culetZ, false);

  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.Float32BufferAttribute(vertices, 3));
  geometry.computeVertexNormals();
  geometry.computeBoundingBox();
  geometry.userData = {
    profileVersion: PREVIEW_PROFILE_VERSION,
    profileId: profile.id,
    shape,
    regions: ['table', 'crown', 'girdle', 'pavilion', 'culet'],
    closed: true,
    dimensionsSource: 'accepted_preview_contract',
  };
  return geometry;
}

function buildStone(shape, length, width, depth, material, role = 'center') {
  const group = new THREE.Group();
  group.name = `${role}-${shape}-stone`;
  group.userData = {
    previewRole: role,
    stoneShape: shape,
    profileVersion: PREVIEW_PROFILE_VERSION,
    profileId: facetProfileFor(shape).id,
  };
  const cut = new THREE.Mesh(buildFacetedGem(shape, length, width, depth), material);
  cut.name = `${shape}-faceted-cut`;
  cut.castShadow = true;
  cut.receiveShadow = false;
  group.add(cut);
  if (role !== 'accent' && material.isMeshPhysicalMaterial) {
    // A back-face return shell approximates one additional internal reflection
    // path in the raster renderer. It is camera/environment responsive and does
    // not assign fixed colours to individual facets.
    const returnMaterial = material.clone();
    returnMaterial.name = 'diamond-internal-return-v1';
    returnMaterial.color.set(0x8fa4b8);
    returnMaterial.transmission = 0.58;
    returnMaterial.roughness = 0.035;
    returnMaterial.clearcoat = 0;
    returnMaterial.transparent = true;
    returnMaterial.opacity = 0.22;
    returnMaterial.depthWrite = false;
    returnMaterial.side = THREE.BackSide;
    returnMaterial.envMapIntensity = 3.8;
    const returnShell = new THREE.Mesh(cut.geometry, returnMaterial);
    returnShell.name = 'internal-reflection-shell';
    returnShell.scale.setScalar(0.982);
    group.add(returnShell);
  }
  return group;
}

// ── Contract-backed metal geometry ─────────────────────────

function buildBand(innerDiameter, bandThickness, bandWidth, knifeEdge = false) {
  const innerR = innerDiameter / 2;
  const outerR = innerR + bandThickness;
  const halfW = bandWidth / 2;
  const bevel = Math.min(bandThickness * 0.3, halfW * 0.5, 0.35);
  const pts = [];

  if (knifeEdge) {
    pts.push(new THREE.Vector2(innerR, -halfW));
    pts.push(new THREE.Vector2(outerR - bevel, -halfW));
    pts.push(new THREE.Vector2(outerR + bandThickness * 0.16, 0));
    pts.push(new THREE.Vector2(outerR - bevel, halfW));
    pts.push(new THREE.Vector2(innerR, halfW));
    pts.push(new THREE.Vector2(innerR, -halfW));
    return new THREE.LatheGeometry(pts, 144);
  }

  const bulge = bandThickness * 0.06;
  const arc = 7;
  pts.push(new THREE.Vector2(innerR, -halfW));
  pts.push(new THREE.Vector2(outerR - bevel, -halfW));
  for (let i = 1; i <= arc; i++) {
    const a = Math.PI / 2 * i / arc;
    pts.push(new THREE.Vector2(
      outerR - bevel + Math.sin(a) * bevel,
      -halfW + (1 - Math.cos(a)) * bevel
    ));
  }
  for (let i = 1; i < arc * 2; i++) {
    const t = i / (arc * 2);
    pts.push(new THREE.Vector2(
      outerR + Math.sin(Math.PI * t) * bulge,
      -halfW + bevel + (bandWidth - 2 * bevel) * t
    ));
  }
  for (let i = 0; i <= arc; i++) {
    const a = Math.PI / 2 * i / arc;
    pts.push(new THREE.Vector2(
      outerR - (1 - Math.cos(a)) * bevel,
      halfW - bevel + Math.sin(a) * bevel
    ));
  }
  pts.push(new THREE.Vector2(innerR, halfW));
  pts.push(new THREE.Vector2(innerR, -halfW));
  return new THREE.LatheGeometry(pts, 144);
}

function addCylinderBetween(group, start, end, radiusStart, radiusEnd, material, name = 'support') {
  const direction = new THREE.Vector3().subVectors(end, start);
  const geometry = new THREE.CylinderGeometry(radiusEnd, radiusStart, direction.length(), 20);
  const cylinder = new THREE.Mesh(geometry, material);
  cylinder.name = name;
  cylinder.position.copy(start).add(end).multiplyScalar(0.5);
  cylinder.quaternion.setFromUnitVectors(
    new THREE.Vector3(0, 1, 0),
    direction.clone().normalize()
  );
  cylinder.castShadow = true;
  cylinder.receiveShadow = true;
  group.add(cylinder);
  return cylinder;
}

function addShoulder(group, points, radius, material, name = 'shoulder') {
  const curve = new THREE.CatmullRomCurve3(points);
  const geometry = new THREE.TubeGeometry(curve, 42, radius, 16, false);
  const shoulder = new THREE.Mesh(geometry, material);
  shoulder.name = name;
  shoulder.castShadow = true;
  shoulder.receiveShadow = true;
  group.add(shoulder);
}

function stoneOutline(shape, length, width) {
  return previewOutline(shape, length, width, facetProfileFor(shape).outlineSegments);
}

function buildBezelFrame(shape, length, width, material, scale) {
  const outline = stoneOutline(shape, length, width);
  const outerScale = 1.14;
  const innerScale = 0.99;
  const zTop = 0.12 * scale;
  const zBottom = -0.2 * scale;
  const verts = [];
  for (let i = 0; i < outline.length; i++) {
    const next = (i + 1) % outline.length;
    const [x0, y0] = outline[i];
    const [x1, y1] = outline[next];
    const ot0 = [x0 * outerScale * scale, y0 * outerScale * scale, zTop];
    const ot1 = [x1 * outerScale * scale, y1 * outerScale * scale, zTop];
    const it0 = [x0 * innerScale * scale, y0 * innerScale * scale, zTop];
    const it1 = [x1 * innerScale * scale, y1 * innerScale * scale, zTop];
    const ob0 = [ot0[0], ot0[1], zBottom];
    const ob1 = [ot1[0], ot1[1], zBottom];
    const ib0 = [it0[0], it0[1], zBottom];
    const ib1 = [it1[0], it1[1], zBottom];
    pushTriangle(verts, ot0, it0, ot1); pushTriangle(verts, ot1, it0, it1);
    pushTriangle(verts, ot0, ob0, ot1); pushTriangle(verts, ot1, ob0, ob1);
    pushTriangle(verts, it0, it1, ib0); pushTriangle(verts, it1, ib1, ib0);
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.Float32BufferAttribute(verts, 3));
  geo.computeVertexNormals();
  const bezel = new THREE.Mesh(geo, material);
  bezel.name = 'contract-bezel-frame';
  return bezel;
}

function addPave(group, preview, gem, metal, outerR, bandW, s, counts) {
  (preview.accent_stones || []).forEach((schedule) => {
    if (schedule.role !== 'accent' || schedule.placement?.region !== 'band') return;
    const dims = schedule.dimensions_mm || {};
    const count = Math.min(Number(schedule.quantity) || 0, 32);
    const coverage = preview.pave?.coverage;
    const span = coverage === 'full'
      ? Math.PI * 2
      : coverage === 'three_quarter' ? Math.PI * 1.5 : Math.PI * 0.72;
    for (let i = 0; i < count; i++) {
      const t = count === 1 ? 0.5 : i / (count - 1);
      const angle = -span / 2 + span * t;
      const accent = buildStone(
        schedule.shape,
        dims.length,
        dims.width,
        dims.depth,
        gem,
        'accent'
      );
      accent.scale.set(s, s, s);
      accent.position.set(
        Math.sin(angle) * outerR * s,
        Math.cos(angle) * outerR * s,
        bandW * 0.53 * s
      );
      accent.rotation.z = -angle;
      group.add(accent);

      if (preview.pave?.setting_style === 'bead_set') {
        [-1, 1].forEach((edge) => {
          const bead = new THREE.Mesh(
            new THREE.SphereGeometry(Math.max(0.017, dims.width * 0.018), 10, 8),
            metal
          );
          bead.name = 'pave-bead';
          bead.position.copy(accent.position);
          bead.position.z -= dims.depth * 0.018;
          bead.position.x += Math.cos(angle) * edge * dims.width * 0.052;
          bead.position.y -= Math.sin(angle) * edge * dims.width * 0.052;
          bead.castShadow = true;
          group.add(bead);
        });
      }
      counts.accent += 1;
    }
  });
}

function addSideStones(headGroup, preview, gem, metal, stoneL, stoneD, s, galleryTube, counts) {
  (preview.side_stones || []).forEach((schedule) => {
    if (schedule.role !== 'side' || schedule.placement?.pattern !== 'symmetric_pair') return;
    const dims = schedule.dimensions_mm || {};
    [-1, 1].slice(0, Number(schedule.quantity) || 0).forEach((side) => {
      const centerX = side * (stoneL / 2 + dims.length * 0.34) * s;
      const sideStone = buildStone(schedule.shape, dims.length, dims.width, dims.depth, gem, 'side');
      sideStone.scale.set(s, s, s);
      sideStone.position.set(centerX, 0, -stoneD * 0.08 * s);
      if (schedule.placement?.orientation === 'points_outward') {
        sideStone.rotation.z = side < 0 ? Math.PI : 0;
      }
      headGroup.add(sideStone);

      if (preview.side_setting === 'prong') {
        const seat = new THREE.Mesh(
          new THREE.TorusGeometry(dims.length * 0.38 * s, galleryTube * 0.62, 10, 48),
          metal
        );
        seat.name = 'side-stone-seat';
        seat.scale.y = dims.width / dims.length;
        seat.position.set(centerX, 0, -dims.depth * 0.28 * s);
        headGroup.add(seat);
        [-0.56, 0.56].forEach((yFactor) => {
          const base = new THREE.Vector3(
            centerX + side * dims.length * 0.25 * s,
            yFactor * dims.width * 0.42 * s,
            -dims.depth * 0.34 * s
          );
          const tip = new THREE.Vector3(
            centerX + side * dims.length * 0.31 * s,
            yFactor * dims.width * 0.38 * s,
            dims.depth * 0.06 * s
          );
          addCylinderBetween(headGroup, base, tip, galleryTube * 0.55, galleryTube * 0.38, metal, 'side-prong');
          const bead = new THREE.Mesh(new THREE.SphereGeometry(galleryTube * 0.48, 12, 9), metal);
          bead.name = 'side-prong-tip';
          bead.position.copy(tip);
          headGroup.add(bead);
        });
      }
      counts.side += 1;
    });
  });
}

function buildRing(preview, envMap, mode) {
  const group = new THREE.Group();
  const counts = { center: 1, side: 0, accent: 0 };
  const metal = mode === 'wireframe'
    ? wireframeMaterial()
    : metalMaterial(
      preview.metal_family,
      preview.metal_purity || preview.metal?.purity,
      envMap,
      preview.metal_finish,
    );
  const gem = mode === 'wireframe' ? wireframeMaterial() : gemMaterial(envMap);
  const innerDiam = 11.63 + 0.8128 * (preview.ring_size_us || 6.5);
  const bandThick = Math.max(1.2, preview.band_thickness_mm || 1.6);
  const bandW = Math.max(1.4, preview.band_width_mm || 2.0);
  const outerR = innerDiam / 2 + bandThick;
  const s = 0.1;
  const approvedV4Reference = isV4OperationalAid(preview);

  group.name = 'myjewl-contract-preview';
  group.userData = {
    geometrySignature: previewGeometrySignature(preview),
    materialSignature: previewMaterialSignature(preview),
    facetProfileVersion: PREVIEW_PROFILE_VERSION,
    facetProfileId: facetProfileFor(preview.stone_shape || 'round').id,
    stoneShape: preview.stone_shape || 'round',
    geometryAuthority: 'operational_out_of_preview_design_aid',
  };

  const band = new THREE.Mesh(
    buildBand(innerDiam, bandThick, bandW, preview.band_profile === 'knife_edge'),
    metal
  );
  band.name = preview.band_profile === 'knife_edge' ? 'knife-edge-band' : 'comfort-fit-band';
  band.rotation.x = Math.PI / 2;
  band.scale.set(s, s, s);
  band.castShadow = true;
  band.receiveShadow = true;
  group.add(band);

  addPave(group, preview, gem, metal, outerR, bandW, s, counts);

  const dims = preview.stone_dimensions_mm || { length: 6.5, width: 6.5, depth: 3.9 };
  const stoneL = Number(dims.length) || 6.5;
  const stoneW = Number(dims.width) || 6.5;
  const stoneD = Number(dims.depth) || stoneW * 0.6;
  const girdleY = approvedV4Reference
    ? 12.10 * s
    : (outerR + stoneD * 0.34) * s;
  const headGroup = new THREE.Group();
  headGroup.name = 'contract-head-assembly';
  headGroup.position.set(0, girdleY, 0);
  headGroup.rotation.x = -Math.PI / 2;

  const centerStone = buildStone(
    preview.stone_shape || 'round', stoneL, stoneW, stoneD, gem, 'center'
  );
  centerStone.scale.set(s, s, s);
  headGroup.add(centerStone);

  const setting = preview.center_setting || 'prong';
  const galleryRadius = stoneL * 0.43 * s;
  const galleryTube = Math.max(0.16, stoneL * 0.026) * s;
  addSideStones(headGroup, preview, gem, metal, stoneL, stoneD, s, galleryTube, counts);

  const gallerySpecs = approvedV4Reference
    ? [
      { radius: 1.32 * s, z: (9.70 - 12.10) * s, name: 'v4-basket-lower-rail' },
      { radius: 2.42 * s, z: (10.90 - 12.10) * s, name: 'v4-stone-seat-rail' },
    ]
    : (setting === 'bezel' ? [-0.2] : [-0.32, -0.13]).map((level, index) => ({
      radius: galleryRadius,
      z: stoneD * level * s,
      name: `gallery-rail-${index + 1}`,
    }));
  gallerySpecs.forEach((spec, index) => {
    const gallery = new THREE.Mesh(
      new THREE.TorusGeometry(spec.radius, galleryTube, 14, 80),
      metal
    );
    gallery.name = spec.name || `gallery-rail-${index + 1}`;
    gallery.scale.y = approvedV4Reference ? 1 : stoneW / stoneL;
    gallery.position.z = spec.z;
    gallery.castShadow = true;
    headGroup.add(gallery);
  });

  if (approvedV4Reference) {
    const petalRadii = [1.32, 1.50, 1.85, 2.24];
    const petalZ = [9.70, 9.94, 10.38, 10.90];
    const tangentOffsets = [0, 0.08, 0, 0];
    for (let i = 0; i < 4; i++) {
      const angle = Math.PI / 4 + i * Math.PI / 2;
      const radial = new THREE.Vector2(Math.cos(angle), Math.sin(angle));
      const tangent = new THREE.Vector2(-radial.y, radial.x);
      const points = petalRadii.map((radius, index) => new THREE.Vector3(
        (radial.x * radius + tangent.x * tangentOffsets[index]) * s,
        (radial.y * radius + tangent.y * tangentOffsets[index]) * s,
        (petalZ[index] - 12.10) * s,
      ));
      addShoulder(headGroup, points, 0.021 * s / 0.1, metal, 'v4-petal-gallery-support');
    }
  } else if (preview.head_geometry === 'petal_prong') {
    for (let i = 0; i < 4; i++) {
      const angle = Math.PI / 4 + i * Math.PI / 2;
      addShoulder(headGroup, [
        new THREE.Vector3(
          Math.cos(angle) * galleryRadius * 0.35,
          Math.sin(angle) * galleryRadius * 0.35,
          -stoneD * 0.48 * s
        ),
        new THREE.Vector3(
          Math.cos(angle) * galleryRadius * 0.86,
          Math.sin(angle) * galleryRadius * 0.86,
          -stoneD * 0.36 * s
        ),
        new THREE.Vector3(
          Math.cos(angle) * stoneL * 0.48 * s,
          Math.sin(angle) * stoneW * 0.48 * s,
          -stoneD * 0.24 * s
        ),
      ], galleryTube * 0.92, metal, 'petal-gallery-support');
    }
  }

  const prongCount = preview.prong_count || 0;
  if (approvedV4Reference) {
    const prongRadii = [1.38, 1.66, 2.24, 3.06, 2.86, 2.68];
    const prongZ = [9.72, 10.02, 10.82, 11.92, 12.28, 12.54];
    const wireRadii = [0.30, 0.29, 0.27, 0.235, 0.20, 0.17];
    for (let i = 0; i < 4; i++) {
      const angle = Math.PI / 4 + i * Math.PI / 2;
      const points = prongRadii.map((radius, index) => new THREE.Vector3(
        Math.cos(angle) * radius * s,
        Math.sin(angle) * radius * s,
        (prongZ[index] - 12.10) * s,
      ));
      for (let segment = 0; segment < points.length - 1; segment++) {
        addCylinderBetween(
          headGroup,
          points[segment],
          points[segment + 1],
          wireRadii[segment] * s,
          wireRadii[segment + 1] * s,
          metal,
          'v4-center-prong',
        );
      }
      const bead = new THREE.Mesh(new THREE.SphereGeometry(0.17 * 1.08 * s, 18, 14), metal);
      bead.name = 'v4-rounded-prong-tip';
      bead.position.copy(points.at(-1));
      bead.scale.z = 0.86;
      bead.castShadow = true;
      headGroup.add(bead);
    }
  } else if ((setting === 'prong' && prongCount > 0) || setting === 'basket') {
    const visualCount = setting === 'basket' ? 4 : prongCount;
    const prongR = Math.max(0.28, Math.min(0.42, Math.max(stoneL, stoneW) * 0.045));
    for (let i = 0; i < visualCount; i++) {
      const offset = visualCount === 4 ? Math.PI / 4 : 0;
      const angle = Math.PI * 2 / visualCount * i + offset;
      const edgeX = Math.cos(angle) * stoneL / 2;
      const edgeY = Math.sin(angle) * stoneW / 2;
      const base = new THREE.Vector3(
        edgeX * 1.07 * s,
        edgeY * 1.07 * s,
        -stoneD * (setting === 'basket' ? 0.4 : 0.34) * s
      );
      const tip = new THREE.Vector3(
        edgeX * 0.91 * s,
        edgeY * 0.91 * s,
        stoneD * (setting === 'basket' ? 0.1 : 0.14) * s
      );
      const baseR = prongR * 1.08 * s;
      const tipR = prongR * 0.7 * s;
      addCylinderBetween(headGroup, base, tip, baseR, tipR, metal, 'center-prong');
      const bead = new THREE.Mesh(new THREE.SphereGeometry(tipR * 1.12, 18, 14), metal);
      bead.name = 'rounded-prong-tip';
      bead.position.copy(tip);
      bead.scale.z = 0.86;
      bead.castShadow = true;
      headGroup.add(bead);
    }
  } else if (setting === 'bezel') {
    const bezel = buildBezelFrame(preview.stone_shape || 'round', stoneL, stoneW, metal, s);
    bezel.position.z = stoneD * 0.045 * s;
    bezel.castShadow = true;
    headGroup.add(bezel);
  }
  group.add(headGroup);

  if (approvedV4Reference) {
    const shoulderPath = [
      [5.35, 0.25, 7.42],
      [4.82, 0.28, 7.72],
      [4.35, 0.42, 8.30],
      [3.55, 0.66, 9.05],
      [2.45, 0.98, 9.78],
      [1.30815, 1.30815, 10.38],
    ];
    [-1, 1].forEach((side) => {
      [-1, 1].forEach((branch) => {
        addShoulder(group, shoulderPath.map(([x, split, z]) => new THREE.Vector3(
          side * x * s,
          z * s,
          branch * split * s,
        )), 0.035, metal, 'v4-mid-petal-split-shoulder');
      });
    });
  } else {
    const shoulderStartX = Math.max(1.05, bandW * 0.58) * s;
    const shoulderStartY = Math.sqrt(
      Math.max(0, outerR * outerR - Math.pow(shoulderStartX / s, 2))
    ) * s;
    const shoulderEndX = Math.min(stoneL * 0.34, 2.5) * s;
    const shoulderEndY = girdleY - stoneD * 0.34 * s;
    const shoulderRadius = Math.max(0.05, Math.min(0.075, bandW * 0.027));
    const cathedral = preview.shoulder_style === 'tapered_cathedral';
    [-1, 1].forEach((side) => {
      addShoulder(group, [
        new THREE.Vector3(side * shoulderStartX, shoulderStartY, 0),
        new THREE.Vector3(
          side * shoulderEndX * (cathedral ? 1.08 : 0.78),
          (shoulderStartY + shoulderEndY) * (cathedral ? 0.56 : 0.5),
          cathedral ? side * 0.04 : 0
        ),
        new THREE.Vector3(side * shoulderEndX, shoulderEndY, 0),
      ], cathedral ? shoulderRadius * 1.18 : shoulderRadius, metal,
      cathedral ? 'tapered-cathedral-shoulder' : 'straight-shoulder');
    });
  }

  group.rotation.set(0.38, -0.36, 0);
  group.userData.stoneCounts = counts;
  return group;
}

/**
 * Build the bounded procedural operational aid without starting a renderer.
 * This exists only for out-of-preview design/comparison tooling. It is never
 * a production PreviewManifest representation.
 */
async function buildPreviewReference(preview, mode = 'solid') {
  await loadThree();
  return buildRing(preview || defaultPreview(), null, mode);
}

// ── Deterministic 2D fallback ──────────────────────────────

function shadeHex(color, adjustment) {
  const channels = [16, 8, 0].map((shift) => (
    clamp(((color >> shift) & 0xff) + adjustment, 0, 255)
  ));
  return `#${channels.map((channel) => Math.round(channel).toString(16).padStart(2, '0')).join('')}`;
}

function fallbackMetalGradient(ctx, family, purity, finish, x0, y0, x1, y1) {
  const preset = metalPresetFor(family, purity, finish);
  const colors = [shadeHex(preset.color, 58), shadeHex(preset.color, 0), shadeHex(preset.color, -76)];
  const gradient = ctx.createLinearGradient(x0, y0, x1, y1);
  gradient.addColorStop(0, colors[0]);
  gradient.addColorStop(finish === 'matte' ? 0.7 : 0.46, colors[1]);
  gradient.addColorStop(1, colors[2]);
  return gradient;
}

function fallbackStonePath(ctx, shape, x, y, length, width) {
  const points = previewOutline(shape, length, width, facetProfileFor(shape).outlineSegments);
  ctx.beginPath();
  points.forEach(([pointX, pointY], index) => {
    if (index === 0) ctx.moveTo(x + pointX, y + pointY);
    else ctx.lineTo(x + pointX, y + pointY);
  });
  ctx.closePath();
  return points;
}

function drawFallback(canvas, preview) {
  const rect = canvas.getBoundingClientRect();
  const dpr = Math.min(window.devicePixelRatio || 1, QUALITY_PROFILES.fallback.maxPixelRatio);
  const width = Math.max(1, Math.floor(rect.width));
  const height = Math.max(1, Math.floor(rect.height));
  canvas.width = Math.floor(width * dpr);
  canvas.height = Math.floor(height * dpr);
  const ctx = canvas.getContext('2d');
  if (!ctx) return;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  const bg = ctx.createLinearGradient(0, 0, 0, height);
  bg.addColorStop(0, '#20252a'); bg.addColorStop(0.48, '#11161a'); bg.addColorStop(1, '#07090b');
  ctx.fillStyle = bg; ctx.fillRect(0, 0, width, height);
  const cx = width * 0.52; const cy = height * 0.58;
  const ringW = Math.min(width, height) * 0.4; const ringH = ringW * 0.78;
  ctx.save(); ctx.translate(cx, cy); ctx.rotate(-0.32);
  ctx.filter = 'blur(8px)'; ctx.strokeStyle = 'rgba(0,0,0,.48)'; ctx.lineWidth = ringW * 0.11;
  ctx.beginPath(); ctx.ellipse(8, 16, ringW * 0.42, ringH * 0.5, 0, 0, Math.PI * 2); ctx.stroke();
  ctx.filter = 'none'; ctx.lineCap = 'round';
  ctx.strokeStyle = fallbackMetalGradient(
    ctx,
    preview.metal_family,
    preview.metal_purity || preview.metal?.purity,
    preview.metal_finish,
    -ringW / 2,
    0,
    ringW / 2,
    0,
  );
  ctx.lineWidth = ringW * (preview.band_profile === 'knife_edge' ? 0.075 : 0.092);
  ctx.beginPath(); ctx.ellipse(0, 0, ringW * 0.42, ringH * 0.5, 0, 0, Math.PI * 2); ctx.stroke();

  if (preview.reference_geometry_id === 'goal0011-realism-first-round-solitaire-master-v4') {
    ctx.lineWidth = Math.max(1.4, ringW * 0.012);
    [-1, 1].forEach((side) => {
      [-1, 1].forEach((branch) => {
        ctx.beginPath();
        ctx.moveTo(side * ringW * 0.25, -ringH * 0.27 + branch * 2.5);
        ctx.quadraticCurveTo(
          side * ringW * 0.15,
          -ringH * 0.44 + branch * 5,
          side * ringW * 0.075,
          -ringH * 0.49 + branch * 8,
        );
        ctx.stroke();
      });
    });
  }

  const stoneY = -ringH * 0.48;
  const dimensions = preview.stone_dimensions_mm || { length: 6.5, width: 6.5 };
  const typedLength = Math.max(0.1, Number(dimensions.length) || 6.5);
  const typedWidth = Math.max(0.1, Number(dimensions.width) || typedLength);
  const displayMaximum = ringW * 0.31;
  const dimensionMaximum = Math.max(typedLength, typedWidth);
  const stoneL = displayMaximum * typedLength / dimensionMaximum;
  const stoneW = displayMaximum * typedWidth / dimensionMaximum;
  const centerOutline = fallbackStonePath(ctx, preview.stone_shape, 0, stoneY, stoneL, stoneW);
  const gem = ctx.createRadialGradient(-stoneL * 0.12, stoneY - stoneW * 0.14, 1, 0, stoneY, stoneL * 0.55);
  gem.addColorStop(0, '#ffffff'); gem.addColorStop(0.28, '#d9efff'); gem.addColorStop(0.56, '#627482'); gem.addColorStop(0.75, '#f6fdff'); gem.addColorStop(1, '#43505b');
  ctx.fillStyle = gem; ctx.fill(); ctx.strokeStyle = 'rgba(255,255,255,.88)'; ctx.lineWidth = 1.2; ctx.stroke();
  const facetStride = Math.max(1, Math.floor(centerOutline.length / 12));
  for (let i = 0; i < centerOutline.length; i += facetStride) {
    const [facetX, facetY] = centerOutline[i];
    ctx.beginPath(); ctx.moveTo(0, stoneY); ctx.lineTo(facetX, stoneY + facetY); ctx.stroke();
  }
  if (preview.architecture === 'trilogy') {
    [-1, 1].forEach((side) => {
      fallbackStonePath(ctx, 'pear', side * stoneL * 0.73, stoneY + 4, stoneL * 0.5, stoneW * 0.46);
      ctx.fillStyle = gem; ctx.fill(); ctx.stroke();
    });
  }
  ctx.restore();
}

function stoneCounts(preview) {
  return {
    center: 1,
    side: (preview.side_stones || []).reduce((sum, item) => sum + (Number(item.quantity) || 0), 0),
    accent: (preview.accent_stones || []).reduce((sum, item) => sum + (Number(item.quantity) || 0), 0),
  };
}

function createFallbackApi(canvas, options, reason) {
  let preview = options.initialPreview || defaultPreview();
  let fallbackDirty = true;
  let fallbackWidth = 0;
  let fallbackHeight = 0;
  const createdAt = performance.now();
  const diagnostics = {
    purpose: PURPOSE_OPERATIONAL_AID,
    rendererPath: 'fallback-2d',
    capabilityTier: 'fallback',
    fallbackReason: reason,
    quality: 'fallback',
    profileVersion: PREVIEW_PROFILE_VERSION,
    environmentVersion: null,
    geometrySignature: previewGeometrySignature(preview),
    materialSignature: previewMaterialSignature(preview),
    stoneShape: preview.stone_shape || 'round',
    facetProfileId: facetProfileFor(preview.stone_shape || 'round').id,
    stoneCounts: stoneCounts(preview),
    loadMs: 0,
    settledMs: 0,
    interactiveFps: null,
    memory: { geometries: 0, textures: 0 },
    autoRotate: false,
    rotationRadians: { x: 0, y: 0 },
    framing: { fit: 'deterministic_2d' },
    geometryAuthority: 'operational_out_of_preview_design_aid',
    resolvedPreviewStatus: null,
    displayedPreviewStatus: 'operational_aid',
    assetLoadStatus: 'not_applicable',
    representationKind: 'none',
    reasonCode: reason,
  };
  canvas.dataset.previewQuality = 'fallback';
  canvas.dataset.previewPath = 'fallback-2d';
  canvas.dataset.previewProfile = PREVIEW_PROFILE_VERSION;
  drawFallback(canvas, preview);
  diagnostics.loadMs = Math.round(performance.now() - createdAt);
  registerDiagnostics(canvas, diagnostics);
  return {
    rebuild(nextPreview) {
      preview = nextPreview || defaultPreview();
      diagnostics.geometrySignature = previewGeometrySignature(preview);
      diagnostics.materialSignature = previewMaterialSignature(preview);
      diagnostics.stoneShape = preview.stone_shape || 'round';
      diagnostics.facetProfileId = facetProfileFor(preview.stone_shape || 'round').id;
      diagnostics.stoneCounts = stoneCounts(preview);
      fallbackDirty = true;
      drawFallback(canvas, preview);
      publishDiagnostics(canvas, diagnostics);
    },
    render() {
      const rect = canvas.getBoundingClientRect();
      if (rect.width !== fallbackWidth || rect.height !== fallbackHeight) fallbackDirty = true;
      if (!fallbackDirty) return;
      fallbackWidth = rect.width;
      fallbackHeight = rect.height;
      drawFallback(canvas, preview);
      fallbackDirty = false;
    },
    reset() {},
    ringGroup: null,
    getDiagnostics() { return JSON.parse(JSON.stringify(diagnostics)); },
    setMode() {},
    dispose() {},
    get spin() { return false; }, set spin(_value) {},
    get targetX() { return 0; }, set targetX(_value) {},
    get targetY() { return 0; }, set targetY(_value) {},
  };
}

function dispatchDeliveryState(canvas, diagnostics) {
  const hasSelectedAsset = Boolean(
    diagnostics.assetId || diagnostics.assetVersion || diagnostics.referenceAssetSha256,
  );
  const deliveryStatus = diagnostics.assetLoadStatus === 'ready'
    ? 'ready'
    : diagnostics.assetLoadStatus === 'loading'
      ? 'loading'
      : hasSelectedAsset
        ? 'failure'
        : 'unavailable';
  canvas.dispatchEvent(new CustomEvent('myjewl-preview-delivery', {
    detail: {
      purpose: diagnostics.purpose,
      status: deliveryStatus,
      resolved_status: diagnostics.resolvedPreviewStatus,
      displayed_status: diagnostics.displayedPreviewStatus,
      asset_status: diagnostics.assetLoadStatus,
      representation_kind: diagnostics.representationKind,
      renderer: hasSelectedAsset ? diagnostics.deliveryRenderer || 'none' : 'none',
      reason_code: diagnostics.reasonCode,
      retryable: diagnostics.retryable === true,
      manifest_id: diagnostics.manifestId || null,
      asset_id: diagnostics.assetId || null,
      asset_version: diagnostics.assetVersion || null,
      content_sha256: diagnostics.referenceAssetSha256 || null,
    },
  }));
}

function clearCanvas(canvas) {
  // Resetting dimensions clears either a prior 2D or WebGL drawing buffer
  // without drawing substitute geometry into a production viewport.
  canvas.width = Math.max(1, canvas.width || 1);
  canvas.height = Math.max(1, canvas.height || 1);
}

function createProductionUnavailableApi(canvas, options, reason) {
  let previewManifest = options.previewManifest || null;
  const resolvedStatus = previewManifest?.preview_status || 'unavailable';
  const resolverUnavailable = resolvedStatus === 'unavailable';
  const diagnostics = {
    purpose: PURPOSE_PRODUCTION_PREVIEW,
    rendererPath: 'none',
    capabilityTier: 'unavailable',
    fallbackReason: null,
    quality: 'unavailable',
    profileVersion: PREVIEW_PROFILE_VERSION,
    environmentVersion: null,
    geometrySignature: previewManifest?.geometry?.signature || '',
    materialSignature: previewManifest?.appearance_signature || '',
    stoneShape: previewManifest?.geometry?.projection?.stones
      ?.find(stone => stone?.role === 'center')?.shape || '',
    facetProfileId: '',
    stoneCounts: { center: 0, side: 0, accent: 0 },
    loadMs: 0,
    settledMs: 0,
    interactiveFps: null,
    memory: { geometries: 0, textures: 0 },
    autoRotate: false,
    rotationRadians: { x: 0, y: 0 },
    framing: null,
    geometryAuthority: 'server_resolved_preview_not_displayed',
    resolvedPreviewStatus: resolvedStatus,
    displayedPreviewStatus: 'unavailable',
    assetLoadStatus: resolverUnavailable ? 'not_applicable' : 'error',
    representationKind: previewManifest?.representation_kind || 'none',
    reasonCode: reason || previewManifest?.reason_code || 'preview_unavailable',
    retryable: resolvedStatus !== 'unavailable',
    manifestId: previewManifest?.manifest_id || null,
    assetId: previewManifest?.selected_asset?.asset_id || null,
    assetVersion: previewManifest?.selected_asset?.asset_version || null,
    deliveryRenderer: previewManifest?.selected_asset?.renderer || 'none',
    referenceAssetSha256: previewManifest?.selected_asset?.content?.sha256 || null,
    sourceReceiptSha256: previewManifest?.selected_asset?.source_receipt_sha256 || null,
    referencePartCount: previewManifest?.selected_asset?.part_count ?? null,
    referenceTriangleCount: previewManifest?.selected_asset?.triangle_count ?? null,
  };
  clearCanvas(canvas);
  registerDiagnostics(canvas, diagnostics);
  queueMicrotask(() => dispatchDeliveryState(canvas, diagnostics));
  return {
    rebuild(_preview, _mode, nextManifest) {
      previewManifest = nextManifest || previewManifest;
      diagnostics.resolvedPreviewStatus = previewManifest?.preview_status || 'unavailable';
      diagnostics.displayedPreviewStatus = 'unavailable';
      diagnostics.reasonCode = previewManifest?.reason_code || reason || 'preview_unavailable';
      diagnostics.manifestId = previewManifest?.manifest_id || null;
      diagnostics.assetId = previewManifest?.selected_asset?.asset_id || null;
      diagnostics.assetVersion = previewManifest?.selected_asset?.asset_version || null;
      diagnostics.deliveryRenderer = previewManifest?.selected_asset?.renderer || 'none';
      diagnostics.referenceAssetSha256 = previewManifest?.selected_asset?.content?.sha256 || null;
      diagnostics.sourceReceiptSha256 = previewManifest?.selected_asset?.source_receipt_sha256 || null;
      diagnostics.referencePartCount = previewManifest?.selected_asset?.part_count ?? null;
      diagnostics.referenceTriangleCount = previewManifest?.selected_asset?.triangle_count ?? null;
      diagnostics.assetLoadStatus = diagnostics.resolvedPreviewStatus === 'unavailable'
        ? 'not_applicable'
        : 'error';
      diagnostics.retryable = diagnostics.resolvedPreviewStatus !== 'unavailable';
      clearCanvas(canvas);
      publishDiagnostics(canvas, diagnostics);
      dispatchDeliveryState(canvas, diagnostics);
    },
    render() {},
    reset() {},
    retry() { dispatchDeliveryState(canvas, diagnostics); return false; },
    ringGroup: null,
    getDiagnostics() { return JSON.parse(JSON.stringify(diagnostics)); },
    setMode() {},
    dispose() {},
    get spin() { return false; }, set spin(_value) {},
    get targetX() { return 0; }, set targetX(_value) {},
    get targetY() { return 0; }, set targetY(_value) {},
  };
}

// ── Hybrid renderer ────────────────────────────────────────

export async function initRing3D(canvas, options = {}) {
  if (!canvas) throw new Error('A preview canvas is required.');
  const purpose = options.purpose || PURPOSE_OPERATIONAL_AID;
  const productionPreview = purpose === PURPOSE_PRODUCTION_PREVIEW;
  if (![PURPOSE_OPERATIONAL_AID, PURPOSE_PRODUCTION_PREVIEW].includes(purpose)) {
    throw new Error(`Unsupported preview purpose: ${purpose}`);
  }
  if (productionPreview) {
    // A completed preview starts from a blank framebuffer. This also removes
    // any operational-aid frame left by a renderer that was just disposed.
    clearCanvas(canvas);
    const manifest = options.previewManifest;
    if (!manifest || manifest.preview_status === 'unavailable') {
      return createProductionUnavailableApi(
        canvas,
        options,
        manifest?.reason_code || 'preview_manifest_unavailable',
      );
    }
    try {
      selectedAssetFor(manifest);
    } catch (error) {
      return createProductionUnavailableApi(canvas, options, error.code || 'manifest_invalid');
    }
  }
  if (options.forceFallback) {
    return productionPreview
      ? createProductionUnavailableApi(canvas, options, 'forced_renderer_unavailable')
      : createFallbackApi(canvas, options, 'forced_for_verification');
  }
  const probe = document.createElement('canvas');
  const webgl = probe.getContext('webgl2') || probe.getContext('webgl');
  if (!webgl) {
    return productionPreview
      ? createProductionUnavailableApi(canvas, options, 'webgl_unavailable')
      : createFallbackApi(canvas, options, 'webgl_unavailable');
  }

  try {
    await loadDeps();
  } catch (_error) {
    return productionPreview
      ? createProductionUnavailableApi(canvas, options, 'threejs_unavailable')
      : createFallbackApi(canvas, options, 'local_renderer_asset_unavailable');
  }

  const createdAt = performance.now();
  let renderer;
  try {
    renderer = new THREE.WebGLRenderer({
      canvas,
      antialias: true,
      alpha: false,
      powerPreference: 'high-performance',
    });
  } catch (_error) {
    return productionPreview
      ? createProductionUnavailableApi(canvas, options, 'webgl_initialization_failed')
      : createFallbackApi(canvas, options, 'webgl_initialization_failed');
  }

  const isHero = options.hero || false;
  const prefersReducedMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches || false;
  const deviceMemory = navigator.deviceMemory || 8;
  const hardwareConcurrency = navigator.hardwareConcurrency || 8;
  const reducedCapability = !renderer.capabilities.isWebGL2 || deviceMemory <= 2 || hardwareConcurrency <= 2;
  const capabilityTier = reducedCapability ? 'reduced-webgl' : 'full-webgl';
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = isHero ? 1.16 : 1.12;
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;
  renderer.setClearColor(0x0b0e10, 1);

  const scene = new THREE.Scene();
  const background = backgroundTexture();
  scene.background = background;
  const envTarget = neutralStudioEnvironment(renderer);
  scene.environment = envTarget.texture;
  scene.environmentIntensity = 1.0;

  const camera = new THREE.PerspectiveCamera(33, 1, 0.01, 200);
  const viewDirection = new THREE.Vector3(
    isHero ? 2.35 : 2.55,
    isHero ? 1.72 : 1.82,
    isHero ? 4.95 : 5.58,
  ).normalize();
  const cameraTarget = new THREE.Vector3(0, 0.08, 0);
  camera.position.copy(cameraTarget).addScaledVector(viewDirection, 5.8);
  camera.lookAt(cameraTarget);

  const key = new THREE.RectAreaLight(0xf8fbff, isHero ? 5.2 : 4.1, 4.6, 5.4);
  key.position.set(-3.8, 4.8, 5.4); key.lookAt(0, 0.2, 0); scene.add(key);
  const fill = new THREE.RectAreaLight(0xdfe9f7, isHero ? 2.5 : 1.85, 3.2, 4.2);
  fill.position.set(4.2, 1.3, 3.4); fill.lookAt(0, 0.15, 0); scene.add(fill);
  const rim = new THREE.RectAreaLight(0xffffff, isHero ? 3.2 : 2.4, 3.0, 2.4);
  rim.position.set(0, 3.2, -4.4); rim.lookAt(0, 0.2, 0); scene.add(rim);
  const shadowKey = new THREE.DirectionalLight(0xffffff, isHero ? 1.65 : 1.25);
  shadowKey.position.set(-2.5, 5.5, 4.5);
  shadowKey.castShadow = true;
  shadowKey.shadow.camera.near = 0.5; shadowKey.shadow.camera.far = 18;
  shadowKey.shadow.bias = -0.00035; scene.add(shadowKey);
  scene.add(new THREE.HemisphereLight(0xf1f5fa, 0x1d2228, isHero ? 0.48 : 0.38));

  const floor = new THREE.Mesh(
    new THREE.CircleGeometry(5.8, 96),
    new THREE.ShadowMaterial({ color: 0x000000, opacity: isHero ? 0.25 : 0.34 })
  );
  floor.name = 'contact-shadow-catcher';
  floor.rotation.x = -Math.PI / 2;
  floor.position.y = -1.17;
  floor.receiveShadow = true;
  scene.add(floor);

  let composer = null;
  if (options.bloom !== false && !reducedCapability) {
    composer = new EffectComposer(renderer);
    composer.addPass(new RenderPass(scene, camera));
    composer.addPass(new BokehPass(scene, camera, {
      focus: camera.position.length(),
      aperture: 0.000018,
      maxblur: 0.0025,
    }));
    composer.addPass(new UnrealBloomPass(
      new THREE.Vector2(512, 512),
      isHero ? 0.07 : 0.045,
      0.16,
      1.18
    ));
  }

  let ringGroup = null;
  let currentMode = options.mode || 'rendered';
  let currentPreview = options.initialPreview || defaultPreview();
  let currentPreviewManifest = options.previewManifest || null;
  let spin = options.spin === true && !prefersReducedMotion;
  let targetX = 0.38;
  let targetY = -0.36;
  let lastInteractionAt = createdAt;
  let previousFrameAt = createdAt;
  let quality = 'interactive';
  let dirty = true;
  let firstFrameAt = null;
  let settledAt = null;
  let settleTimer = null;
  let webglContextAvailable = true;
  const interactiveFrames = [];
  const diagnostics = {
    purpose,
    rendererPath: composer ? 'hybrid-webgl' : 'direct-webgl',
    capabilityTier,
    fallbackReason: null,
    quality,
    profileVersion: PREVIEW_PROFILE_VERSION,
    environmentVersion: STUDIO_ENVIRONMENT_VERSION,
    geometrySignature: previewGeometrySignature(currentPreview),
    materialSignature: previewMaterialSignature(currentPreview),
    stoneShape: currentPreview.stone_shape || 'round',
    facetProfileId: facetProfileFor(currentPreview.stone_shape || 'round').id,
    stoneCounts: stoneCounts(currentPreview),
    loadMs: null,
    settledMs: null,
    interactiveFps: null,
    memory: { geometries: 0, textures: 0 },
    framing: null,
    rotationRadians: { x: targetX, y: targetY },
    autoRotate: spin,
    renderer: renderer.capabilities.isWebGL2 ? 'webgl2' : 'webgl1',
    deviceMemory,
    hardwareConcurrency,
    geometryAuthority: productionPreview
      ? 'server_resolved_preview_not_displayed'
      : 'operational_out_of_preview_design_aid',
    resolvedPreviewStatus: productionPreview
      ? currentPreviewManifest?.preview_status || 'unavailable'
      : null,
    displayedPreviewStatus: productionPreview ? 'unavailable' : 'operational_aid',
    assetLoadStatus: productionPreview ? 'loading' : 'not_applicable',
    representationKind: productionPreview
      ? currentPreviewManifest?.representation_kind || 'none'
      : 'none',
    reasonCode: productionPreview ? 'asset_loading' : 'operational_out_of_preview',
    retryable: false,
    manifestId: currentPreviewManifest?.manifest_id || null,
    assetId: currentPreviewManifest?.selected_asset?.asset_id || null,
    assetVersion: currentPreviewManifest?.selected_asset?.asset_version || null,
    deliveryRenderer: currentPreviewManifest?.selected_asset?.renderer || 'none',
    referenceAssetStatus: productionPreview ? 'loading' : 'not_applicable',
    referenceAssetSha256: productionPreview
      ? currentPreviewManifest?.selected_asset?.content?.sha256 || null
      : null,
    sourceReceiptSha256: productionPreview
      ? currentPreviewManifest?.selected_asset?.source_receipt_sha256 || null
      : null,
    referencePartCount: productionPreview
      ? currentPreviewManifest?.selected_asset?.part_count ?? null
      : null,
    referenceTriangleCount: productionPreview
      ? currentPreviewManifest?.selected_asset?.triangle_count ?? null
      : null,
  };

  function fitCameraToRing() {
    if (!ringGroup) return;
    const sphere = new THREE.Box3().setFromObject(ringGroup)
      .getBoundingSphere(new THREE.Sphere());
    if (!Number.isFinite(sphere.radius) || sphere.radius <= 0) return;
    const verticalFov = THREE.MathUtils.degToRad(camera.fov);
    const horizontalFov = 2 * Math.atan(Math.tan(verticalFov / 2) * Math.max(camera.aspect, 0.1));
    const limitingFov = Math.min(verticalFov, horizontalFov);
    const margin = isHero ? 1.18 : 1.24;
    const distance = sphere.radius / Math.sin(limitingFov / 2) * margin;
    cameraTarget.copy(sphere.center);
    camera.position.copy(cameraTarget).addScaledVector(viewDirection, distance);
    camera.near = Math.max(0.01, distance - sphere.radius * 1.8);
    camera.far = distance + sphere.radius * 3.2;
    camera.lookAt(cameraTarget);
    camera.updateProjectionMatrix();
    diagnostics.framing = {
      radius: Number(sphere.radius.toFixed(4)),
      distance: Number(distance.toFixed(4)),
      margin,
      fit: 'bounding_sphere',
    };
  }

  function applyQuality(nextQuality) {
    if (quality === nextQuality && canvas.dataset.previewQuality) return;
    quality = nextQuality;
    const profile = QUALITY_PROFILES[nextQuality];
    const deviceDpr = window.devicePixelRatio || 1;
    const mobileCap = window.innerWidth <= 540 ? 1.5 : profile.maxPixelRatio;
    const dpr = Math.min(deviceDpr, profile.maxPixelRatio, mobileCap);
    renderer.setPixelRatio(dpr);
    composer?.setPixelRatio(dpr);
    shadowKey.shadow.mapSize.set(profile.shadowMapSize, profile.shadowMapSize);
    if (shadowKey.shadow.map) {
      shadowKey.shadow.map.dispose();
      shadowKey.shadow.map = null;
    }
    canvas.dataset.previewQuality = nextQuality;
    canvas.dataset.previewPath = diagnostics.rendererPath;
    canvas.dataset.previewProfile = PREVIEW_PROFILE_VERSION;
    diagnostics.quality = nextQuality;
    dirty = true;
    canvas.dispatchEvent(new CustomEvent('myjewl-preview-quality', {
      detail: { quality: nextQuality, profile: profile.id },
    }));
  }

  function markInteraction() {
    lastInteractionAt = performance.now();
    settledAt = null;
    applyQuality('interactive');
    dirty = true;
    if (settleTimer) clearTimeout(settleTimer);
    settleTimer = setTimeout(() => {
      if (performance.now() - lastInteractionAt < SETTLE_DELAY_MS - 8) return;
      if (quality !== 'settled') {
        applyQuality('settled');
        settledAt = performance.now();
        diagnostics.settledMs = Math.round(settledAt - lastInteractionAt);
        render(settledAt);
      }
    }, SETTLE_DELAY_MS);
  }

  let rebuildToken = 0;
  let awaitingFirstProductionFrame = false;

  function clearRingGroup() {
    if (!ringGroup) return;
    scene.remove(ringGroup);
    disposeGroup(ringGroup);
    ringGroup = null;
    diagnostics.framing = null;
    dirty = true;
  }

  function installRingGroup(nextGroup) {
    clearRingGroup();
    ringGroup = nextGroup;
    scene.add(ringGroup);
    resizeRenderer(renderer, camera, canvas, composer);
    fitCameraToRing();
    diagnostics.geometrySignature = ringGroup.userData.geometrySignature;
    diagnostics.materialSignature = ringGroup.userData.materialSignature;
    diagnostics.stoneShape = ringGroup.userData.stoneShape;
    diagnostics.facetProfileId = ringGroup.userData.facetProfileId;
    diagnostics.stoneCounts = ringGroup.userData.stoneCounts;
    diagnostics.geometryAuthority = ringGroup.userData.geometryAuthority;
  }

  function publishProductionState() {
    publishDiagnostics(canvas, diagnostics);
    dispatchDeliveryState(canvas, diagnostics);
  }

  function rebuildProduction(retry = false) {
    const token = ++rebuildToken;
    const preview = currentPreview;
    const previewManifest = currentPreviewManifest;
    awaitingFirstProductionFrame = false;
    clearRingGroup();
    diagnostics.geometrySignature = previewManifest?.geometry?.signature || '';
    diagnostics.materialSignature = previewManifest?.appearance_signature || previewMaterialSignature(preview);
    diagnostics.stoneShape = previewManifest?.geometry?.projection?.stones
      ?.find(stone => stone?.role === 'center')?.shape || preview.stone_shape || '';
    diagnostics.stoneCounts = { center: 0, side: 0, accent: 0 };
    diagnostics.geometryAuthority = 'server_resolved_preview_not_displayed';
    diagnostics.resolvedPreviewStatus = previewManifest?.preview_status || 'unavailable';
    diagnostics.displayedPreviewStatus = 'unavailable';
    diagnostics.assetLoadStatus = 'loading';
    diagnostics.representationKind = previewManifest?.representation_kind || 'none';
    diagnostics.reasonCode = 'asset_loading';
    diagnostics.retryable = false;
    diagnostics.manifestId = previewManifest?.manifest_id || null;
    diagnostics.assetId = previewManifest?.selected_asset?.asset_id || null;
    diagnostics.assetVersion = previewManifest?.selected_asset?.asset_version || null;
    diagnostics.deliveryRenderer = previewManifest?.selected_asset?.renderer || 'none';
    diagnostics.referenceAssetStatus = 'loading';
    diagnostics.referenceAssetSha256 = previewManifest?.selected_asset?.content?.sha256 || null;
    diagnostics.sourceReceiptSha256 = previewManifest?.selected_asset?.source_receipt_sha256 || null;
    diagnostics.referencePartCount = previewManifest?.selected_asset?.part_count ?? null;
    diagnostics.referenceTriangleCount = previewManifest?.selected_asset?.triangle_count ?? null;
    if (!webglContextAvailable) {
      diagnostics.assetLoadStatus = 'error';
      diagnostics.referenceAssetStatus = 'unavailable';
      diagnostics.reasonCode = 'webgl_context_lost';
      diagnostics.retryable = false;
      publishProductionState();
      return Promise.resolve(false);
    }
    if (!previewManifest || previewManifest.preview_status === 'unavailable') {
      diagnostics.assetLoadStatus = 'not_applicable';
      diagnostics.referenceAssetStatus = 'unavailable';
      diagnostics.reasonCode = previewManifest?.reason_code || 'preview_manifest_unavailable';
      diagnostics.retryable = false;
      publishProductionState();
      return Promise.resolve(false);
    }
    try {
      selectedAssetFor(previewManifest);
    } catch (error) {
      diagnostics.assetLoadStatus = 'error';
      diagnostics.referenceAssetStatus = 'unavailable';
      diagnostics.reasonCode = error.code || 'manifest_invalid';
      diagnostics.retryable = false;
      publishProductionState();
      return Promise.resolve(false);
    }

    publishProductionState();
    return loadVerifiedAssetPayload(previewManifest, retry).then(payload => {
      if (token !== rebuildToken) return;
      installRingGroup(buildVerifiedAssetGroup(
        preview,
        previewManifest,
        envTarget.texture,
        currentMode,
        payload,
      ));
      diagnostics.resolvedPreviewStatus = previewManifest.preview_status;
      diagnostics.displayedPreviewStatus = 'unavailable';
      diagnostics.assetLoadStatus = 'loading';
      diagnostics.reasonCode = 'asset_loading';
      diagnostics.retryable = false;
      diagnostics.referenceAssetStatus = 'verified_awaiting_first_frame';
      diagnostics.referenceAssetSha256 = ringGroup.userData.referenceAssetSha256;
      diagnostics.sourceReceiptSha256 = ringGroup.userData.sourceReceiptSha256;
      diagnostics.referencePartCount = ringGroup.userData.referencePartCount;
      diagnostics.referenceTriangleCount = ringGroup.userData.referenceTriangleCount;
      awaitingFirstProductionFrame = true;
      markInteraction();
      publishProductionState();
      return true;
    }).catch(error => {
      if (token !== rebuildToken) return;
      awaitingFirstProductionFrame = false;
      clearRingGroup();
      diagnostics.geometryAuthority = 'server_resolved_preview_not_displayed';
      diagnostics.displayedPreviewStatus = 'unavailable';
      diagnostics.assetLoadStatus = 'error';
      diagnostics.referenceAssetStatus = 'unavailable';
      diagnostics.reasonCode = error?.code || 'asset_delivery_failed';
      diagnostics.retryable = true;
      publishProductionState();
      return false;
    });
  }

  function rebuild(preview, mode, previewManifest) {
    currentMode = mode || currentMode;
    currentPreview = preview || defaultPreview();
    if (productionPreview) {
      currentPreviewManifest = previewManifest || currentPreview?.preview_manifest || currentPreviewManifest;
      return rebuildProduction(false);
    }
    ++rebuildToken;
    diagnostics.resolvedPreviewStatus = null;
    diagnostics.displayedPreviewStatus = 'operational_aid';
    diagnostics.assetLoadStatus = 'not_applicable';
    diagnostics.reasonCode = 'operational_out_of_preview';
    diagnostics.referenceAssetStatus = 'not_applicable';
    installRingGroup(buildRing(currentPreview, envTarget.texture, currentMode));
    markInteraction();
    publishDiagnostics(canvas, diagnostics);
    return Promise.resolve(true);
  }

  function render(now = performance.now()) {
    if (productionPreview) {
      let contextLost = !webglContextAvailable;
      try {
        contextLost ||= renderer.getContext?.().isContextLost?.() === true;
      } catch (_error) {
        contextLost = true;
      }
      if (contextLost) {
        if (webglContextAvailable) {
          webglContextAvailable = false;
          handleProductionRenderFailure('webgl_context_lost');
        }
        return;
      }
    }
    const deltaSeconds = Math.min(0.064, Math.max(0, now - previousFrameAt) / 1000);
    previousFrameAt = now;
    if (spin) {
      targetY += AUTO_ROTATE_RADIANS_PER_SECOND * deltaSeconds;
      lastInteractionAt = now;
      applyQuality('interactive');
      dirty = true;
    }
    const interactionActive = spin || now - lastInteractionAt < SETTLE_DELAY_MS;
    if (ringGroup) {
      const dx = targetX - ringGroup.rotation.x;
      const dy = targetY - ringGroup.rotation.y;
      if (Math.abs(dx) > 0.0002 || Math.abs(dy) > 0.0002) {
        ringGroup.rotation.x += dx * 0.16;
        ringGroup.rotation.y += dy * 0.16;
        dirty = true;
      }
    }

    const shouldSettle = !interactionActive;
    if (shouldSettle && quality !== 'settled') {
      applyQuality('settled');
      settledAt = now;
      diagnostics.settledMs = Math.round(settledAt - lastInteractionAt);
    } else if (!shouldSettle && quality !== 'interactive') {
      applyQuality('interactive');
    }
    if (!dirty && quality === 'settled') return;

    const resized = resizeRenderer(renderer, camera, canvas, composer);
    if (resized) fitCameraToRing();
    try {
      if (quality === 'settled' && composer) composer.render();
      else renderer.render(scene, camera);
    } catch (error) {
      if (!productionPreview) throw error;
      handleProductionRenderFailure('webgl_render_failed');
      return;
    }
    dirty = false;

    if (productionPreview && awaitingFirstProductionFrame && ringGroup) {
      awaitingFirstProductionFrame = false;
      diagnostics.displayedPreviewStatus = currentPreviewManifest.preview_status;
      diagnostics.assetLoadStatus = 'ready';
      diagnostics.reasonCode = currentPreviewManifest.reason_code;
      diagnostics.retryable = false;
      diagnostics.referenceAssetStatus = 'ready';
      publishProductionState();
    }

    if (!firstFrameAt) {
      firstFrameAt = now;
      diagnostics.loadMs = Math.round(firstFrameAt - createdAt);
    }
    if (quality === 'interactive') {
      interactiveFrames.push(now);
      while (interactiveFrames.length > 120) interactiveFrames.shift();
      while (interactiveFrames.length > 2 && now - interactiveFrames[0] > 1500) interactiveFrames.shift();
      if (interactiveFrames.length > 2) {
        const elapsed = interactiveFrames[interactiveFrames.length - 1] - interactiveFrames[0];
        diagnostics.interactiveFps = Math.round((interactiveFrames.length - 1) * 1000 / elapsed);
      }
    }
    diagnostics.memory = { ...renderer.info.memory };
    diagnostics.rotationRadians = {
      x: Number((ringGroup?.rotation.x || 0).toFixed(4)),
      y: Number((ringGroup?.rotation.y || 0).toFixed(4)),
    };
    canvas.dataset.previewFps = diagnostics.interactiveFps == null ? '' : String(diagnostics.interactiveFps);
    canvas.dataset.previewSettledMs = diagnostics.settledMs == null ? '' : String(diagnostics.settledMs);
    publishDiagnostics(canvas, diagnostics);
  }

  function reset() {
    targetX = 0.38;
    targetY = -0.36;
    markInteraction();
  }

  function handleProductionRenderFailure(reasonCode) {
    if (!productionPreview) return;
    rebuildToken += 1;
    awaitingFirstProductionFrame = false;
    clearRingGroup();
    diagnostics.geometryAuthority = 'server_resolved_preview_not_displayed';
    diagnostics.displayedPreviewStatus = 'unavailable';
    diagnostics.assetLoadStatus = 'error';
    diagnostics.referenceAssetStatus = 'unavailable';
    diagnostics.reasonCode = reasonCode;
    diagnostics.retryable = reasonCode !== 'webgl_context_lost';
    publishProductionState();
  }

  function onWebGlContextLost(event) {
    event.preventDefault();
    webglContextAvailable = false;
    handleProductionRenderFailure('webgl_context_lost');
  }

  function onWebGlContextRestored() {
    webglContextAvailable = true;
    dirty = true;
    if (productionPreview && diagnostics.reasonCode === 'webgl_context_lost') {
      diagnostics.retryable = true;
      publishProductionState();
    }
  }

  canvas.addEventListener('webglcontextlost', onWebGlContextLost);
  canvas.addEventListener('webglcontextrestored', onWebGlContextRestored);

  const removeDragHandlers = addDrag(canvas, {
    get targetX() { return targetX; }, set targetX(value) { targetX = value; },
    get targetY() { return targetY; }, set targetY(value) { targetY = value; },
  }, markInteraction);

  applyQuality('interactive');
  rebuild(currentPreview, currentMode);
  registerDiagnostics(canvas, diagnostics);

  return {
    rebuild,
    render,
    reset,
    scene,
    camera,
    renderer,
    get spin() { return spin; },
    set spin(value) {
      spin = Boolean(value) && !prefersReducedMotion;
      diagnostics.autoRotate = spin;
      markInteraction();
    },
    get targetX() { return targetX; },
    set targetX(value) { targetX = value; markInteraction(); },
    get targetY() { return targetY; },
    set targetY(value) { targetY = value; markInteraction(); },
    get ringGroup() { return ringGroup; },
    setMode(mode) { currentMode = mode; rebuild(currentPreview, mode, currentPreviewManifest); },
    retry() {
      return productionPreview ? rebuildProduction(true) : Promise.resolve(false);
    },
    getDiagnostics() { return JSON.parse(JSON.stringify(diagnostics)); },
    dispose() {
      rebuildToken += 1;
      awaitingFirstProductionFrame = false;
      clearRingGroup();
      removeDragHandlers();
      canvas.removeEventListener('webglcontextlost', onWebGlContextLost);
      canvas.removeEventListener('webglcontextrestored', onWebGlContextRestored);
      composer?.dispose?.();
      envTarget.dispose();
      background.dispose();
      if (settleTimer) clearTimeout(settleTimer);
      renderer.dispose();
    },
  };
}

// ── Utilities ──────────────────────────────────────────────

function registerDiagnostics(canvas, diagnostics) {
  const registry = window.__MYJEWL_PREVIEW_DIAGNOSTICS__ || [];
  const existing = registry.find((entry) => entry.canvasId === canvas.id);
  if (existing) existing.diagnostics = diagnostics;
  else registry.push({ canvasId: canvas.id || null, diagnostics });
  window.__MYJEWL_PREVIEW_DIAGNOSTICS__ = registry;
  publishDiagnostics(canvas, diagnostics);
}

function publishDiagnostics(canvas, diagnostics) {
  canvas.dataset.previewPurpose = diagnostics.purpose || '';
  canvas.dataset.previewQuality = diagnostics.quality;
  canvas.dataset.previewPath = diagnostics.rendererPath;
  canvas.dataset.previewProfile = diagnostics.profileVersion;
  canvas.dataset.previewLoadMs = diagnostics.loadMs == null ? '' : String(diagnostics.loadMs);
  canvas.dataset.previewSettledMs = diagnostics.settledMs == null ? '' : String(diagnostics.settledMs);
  canvas.dataset.previewFps = diagnostics.interactiveFps == null ? '' : String(diagnostics.interactiveFps);
  canvas.dataset.previewGeometryKey = numericHash(diagnostics.geometrySignature || '');
  canvas.dataset.previewGeometrySignature = diagnostics.geometrySignature || '';
  canvas.dataset.previewMaterial = diagnostics.materialSignature || '';
  canvas.dataset.previewShape = diagnostics.stoneShape || '';
  canvas.dataset.previewFacetProfile = diagnostics.facetProfileId || '';
  canvas.dataset.previewGeometryAuthority = diagnostics.geometryAuthority || '';
  canvas.dataset.previewResolvedStatus = diagnostics.resolvedPreviewStatus || '';
  canvas.dataset.previewDisplayedStatus = diagnostics.displayedPreviewStatus || '';
  canvas.dataset.previewAssetStatus = diagnostics.assetLoadStatus || '';
  canvas.dataset.previewRepresentation = diagnostics.representationKind || '';
  canvas.dataset.previewReason = diagnostics.reasonCode || '';
  canvas.dataset.previewManifestId = diagnostics.manifestId || '';
  canvas.dataset.previewAssetId = diagnostics.assetId || '';
  canvas.dataset.previewAssetVersion = diagnostics.assetVersion || '';
  canvas.dataset.previewReferenceAssetStatus = diagnostics.referenceAssetStatus || '';
  canvas.dataset.previewReferenceAsset = diagnostics.referenceAssetSha256 || '';
  canvas.dataset.previewSourceReceipt = diagnostics.sourceReceiptSha256 || '';
  canvas.dataset.previewReferenceParts = diagnostics.referencePartCount == null
    ? '' : String(diagnostics.referencePartCount);
  canvas.dataset.previewReferenceTriangles = diagnostics.referenceTriangleCount == null
    ? '' : String(diagnostics.referenceTriangleCount);
  canvas.dataset.previewAutoRotate = diagnostics.autoRotate ? 'true' : 'false';
  if (diagnostics.rotationRadians) {
    canvas.dataset.previewRotation = `${diagnostics.rotationRadians.x}/${diagnostics.rotationRadians.y}`;
  }
  canvas.dataset.previewFraming = diagnostics.framing?.fit || '';
  const counts = diagnostics.stoneCounts || { center: 0, side: 0, accent: 0 };
  canvas.dataset.previewStoneCounts = `${counts.center}/${counts.side}/${counts.accent}`;
  const memory = diagnostics.memory || { geometries: 0, textures: 0 };
  canvas.dataset.previewMemory = `${memory.geometries || 0}/${memory.textures || 0}`;
}

function numericHash(value) {
  let hash = 2166136261;
  for (let i = 0; i < value.length; i++) {
    hash ^= value.charCodeAt(i);
    hash = Math.imul(hash, 16777619);
  }
  return (hash >>> 0).toString(16).padStart(8, '0');
}

function disposeSceneResources(scene) {
  const geometries = new Set();
  const materials = new Set();
  scene.traverse((object) => {
    if (object.geometry) geometries.add(object.geometry);
    if (Array.isArray(object.material)) object.material.forEach((material) => materials.add(material));
    else if (object.material) materials.add(object.material);
  });
  geometries.forEach((geometry) => geometry.dispose());
  materials.forEach((material) => material.dispose());
}

function disposeGroup(group) {
  disposeSceneResources(group);
  group.clear();
}

function addDrag(canvas, api, onInteraction) {
  let dragging = false;
  let lastX = 0;
  let lastY = 0;
  canvas.style.touchAction = 'none';
  const pointerDown = (event) => {
    dragging = true;
    lastX = event.clientX;
    lastY = event.clientY;
    canvas.setPointerCapture?.(event.pointerId);
    onInteraction();
  };
  const pointerMove = (event) => {
    if (!dragging) return;
    api.targetY += (event.clientX - lastX) * 0.01;
    api.targetX = clamp(api.targetX + (event.clientY - lastY) * 0.01, -0.95, 0.62);
    lastX = event.clientX;
    lastY = event.clientY;
    onInteraction();
  };
  const release = () => { dragging = false; onInteraction(); };
  const keyDown = (event) => {
    const step = event.shiftKey ? 0.32 : 0.14;
    if (event.key === 'ArrowLeft') api.targetY -= step;
    else if (event.key === 'ArrowRight') api.targetY += step;
    else if (event.key === 'ArrowUp') api.targetX = clamp(api.targetX - step, -0.95, 0.62);
    else if (event.key === 'ArrowDown') api.targetX = clamp(api.targetX + step, -0.95, 0.62);
    else return;
    event.preventDefault();
    onInteraction();
  };
  canvas.addEventListener('pointerdown', pointerDown);
  canvas.addEventListener('pointermove', pointerMove);
  canvas.addEventListener('pointerup', release);
  canvas.addEventListener('pointercancel', release);
  canvas.addEventListener('keydown', keyDown);
  return () => {
    dragging = false;
    canvas.removeEventListener('pointerdown', pointerDown);
    canvas.removeEventListener('pointermove', pointerMove);
    canvas.removeEventListener('pointerup', release);
    canvas.removeEventListener('pointercancel', release);
    canvas.removeEventListener('keydown', keyDown);
  };
}

function resizeRenderer(renderer, camera, canvas, composer) {
  const rect = canvas.getBoundingClientRect();
  const width = Math.max(1, Math.floor(rect.width));
  const height = Math.max(1, Math.floor(rect.height));
  if (canvas.dataset.rw !== String(width) || canvas.dataset.rh !== String(height)) {
    renderer.setSize(width, height, false);
    camera.aspect = width / height;
    camera.updateProjectionMatrix();
    composer?.setSize(width, height);
    canvas.dataset.rw = String(width);
    canvas.dataset.rh = String(height);
    return true;
  }
  return false;
}

function defaultPreview() {
  return {
    architecture: 'solitaire',
    stone_shape: 'round',
    stone_dimensions_mm: { length: 6.5, width: 6.5, depth: 3.9 },
    metal_family: 'yellow',
    metal_purity: '14k',
    ring_size_us: 6.5,
    band_width_mm: 2.0,
    band_thickness_mm: 1.6,
    prong_count: 4,
    center_setting: 'prong',
    head_family: 'classic',
    head_geometry: 'open_prong',
    gallery_structure: 'open_rail',
    side_setting: 'none',
    side_stones: [],
    accent_stones: [],
    band_profile: 'comfort_fit',
    shoulder_style: 'straight',
    pave: { coverage: 'none', setting_style: 'none', accent_stone_ids: [] },
    metal_finish: 'polished',
    carat: 1.0,
  };
}

function clamp(value, min, max) { return Math.max(min, Math.min(max, value)); }

export {
  FACET_PROFILES,
  FINISH_PRESETS,
  METAL_PRESETS,
  PURITY_PRESETS,
  PREVIEW_PROFILE_VERSION,
  PURPOSE_OPERATIONAL_AID,
  PURPOSE_PRODUCTION_PREVIEW,
  QUALITY_PROFILES,
  STUDIO_ENVIRONMENT_VERSION,
  THREE,
  buildPreviewReference,
  clamp,
  defaultPreview,
  loadDeps,
  loadVerifiedAssetPayload,
  previewOutline,
};
