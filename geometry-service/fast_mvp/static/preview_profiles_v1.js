/**
 * Visual-only preview profiles for Goal 0008.
 *
 * These values shape browser rendering only. They are not canonical product
 * fields, certificate measurements, cut-grade claims, Rhino inputs, or
 * manufacturing authority. Overall L x W x D always comes from the accepted
 * preview contract.
 */

export const PREVIEW_PROFILE_VERSION = 'myjewl-preview-facets-v1';
export const STUDIO_ENVIRONMENT_VERSION = 'myjewl-neutral-studio-v1';

export const FACET_PROFILES = Object.freeze({
  round: Object.freeze({
    id: 'round-brilliant-preview-v1',
    outlineSegments: 16,
    tableRatio: 0.52,
    starRatio: 0.78,
    crownHeightRatio: 0.16,
    girdleThicknessRatio: 0.04,
    pavilionDepthRatio: 0.80,
    pavilionBreakRatio: 0.44,
    culetRatio: 0.025,
  }),
  oval: Object.freeze({
    id: 'oval-brilliant-preview-v1',
    outlineSegments: 20,
    tableRatio: 0.51,
    starRatio: 0.79,
    crownHeightRatio: 0.16,
    girdleThicknessRatio: 0.05,
    pavilionDepthRatio: 0.79,
    pavilionBreakRatio: 0.43,
    culetRatio: 0.028,
  }),
  pear: Object.freeze({
    id: 'pear-brilliant-preview-v1',
    outlineSegments: 20,
    tableRatio: 0.50,
    starRatio: 0.78,
    crownHeightRatio: 0.15,
    girdleThicknessRatio: 0.05,
    pavilionDepthRatio: 0.80,
    pavilionBreakRatio: 0.42,
    culetRatio: 0.024,
  }),
});

export const METAL_PRESETS = Object.freeze({
  yellow: Object.freeze({
    id: 'myjewl-yellow-metal-v1', color: 0xe1b95f, envMapIntensity: 1.28,
  }),
  white: Object.freeze({
    id: 'myjewl-white-metal-v1', color: 0xe8ecef, envMapIntensity: 1.38,
  }),
  rose: Object.freeze({
    id: 'myjewl-rose-metal-v1', color: 0xd39180, envMapIntensity: 1.22,
  }),
});

export const FINISH_PRESETS = Object.freeze({
  polished: Object.freeze({
    id: 'myjewl-polished-v1', roughness: 0.11, clearcoat: 0.12,
    clearcoatRoughness: 0.08, anisotropy: 0.0, anisotropyRotation: 0.0,
  }),
  matte: Object.freeze({
    id: 'myjewl-matte-v1', roughness: 0.48, clearcoat: 0.02,
    clearcoatRoughness: 0.42, anisotropy: 0.0, anisotropyRotation: 0.0,
  }),
  brushed: Object.freeze({
    id: 'myjewl-brushed-v1', roughness: 0.31, clearcoat: 0.04,
    clearcoatRoughness: 0.28, anisotropy: 0.72, anisotropyRotation: Math.PI / 2,
  }),
});

export const QUALITY_PROFILES = Object.freeze({
  interactive: Object.freeze({
    id: 'responsive-raster-v1', maxPixelRatio: 1.25, shadowMapSize: 512,
    postProcessing: false,
  }),
  settled: Object.freeze({
    id: 'settled-jewelry-v1', maxPixelRatio: 2.0, shadowMapSize: 1536,
    postProcessing: true,
  }),
  fallback: Object.freeze({
    id: 'deterministic-2d-v1', maxPixelRatio: 1.0, shadowMapSize: 0,
    postProcessing: false,
  }),
});

const GEOMETRY_FIELDS = Object.freeze([
  'architecture', 'stone_shape', 'stone_dimensions_mm', 'ring_size_us',
  'band_width_mm', 'band_thickness_mm', 'prong_count', 'center_setting',
  'head_family', 'head_geometry', 'gallery_structure', 'side_setting',
  'side_stones', 'accent_stones', 'band_profile', 'shoulder_style', 'pave',
]);

export function facetProfileFor(shape) {
  return FACET_PROFILES[shape] || FACET_PROFILES.round;
}

export function metalPresetFor(family, finish) {
  return {
    metal: METAL_PRESETS[family] || METAL_PRESETS.yellow,
    finish: FINISH_PRESETS[finish] || FINISH_PRESETS.polished,
  };
}

export function previewGeometryProjection(preview = {}) {
  const projection = {};
  GEOMETRY_FIELDS.forEach((field) => {
    if (preview[field] !== undefined) projection[field] = preview[field];
  });
  return projection;
}

function stable(value) {
  if (Array.isArray(value)) return value.map(stable);
  if (value && typeof value === 'object') {
    return Object.keys(value).sort().reduce((result, key) => {
      result[key] = stable(value[key]);
      return result;
    }, {});
  }
  return value;
}

export function previewGeometrySignature(preview = {}) {
  return JSON.stringify(stable(previewGeometryProjection(preview)));
}

export function previewMaterialSignature(preview = {}) {
  const { metal, finish } = metalPresetFor(preview.metal_family, preview.metal_finish);
  return `${metal.id}:${finish.id}`;
}
