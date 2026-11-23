/**
 * Goal 0010 visual-only preview profiles.
 *
 * Version 2 extends the published Goal 0008 round/oval/pear profile set with
 * bounded cushion, emerald, princess, and radiant silhouettes and typed-purity
 * appearance overlays. These values are browser-rendering approximations only:
 * overall L x W x D comes from the accepted typed contract, while Rhino /
 * Grasshopper remains the sole geometry authority.
 */

export const PREVIEW_PROFILE_VERSION = 'myjewl-preview-facets-v2';
export const STUDIO_ENVIRONMENT_VERSION = 'myjewl-neutral-studio-v1';

export const FACET_PROFILES = Object.freeze({
  round: Object.freeze({
    id: 'round-brilliant-preview-v2', outlineKind: 'ellipse', outlineSegments: 16,
    tableRatio: 0.52, starRatio: 0.78, crownHeightRatio: 0.16,
    girdleThicknessRatio: 0.04, pavilionDepthRatio: 0.80,
    pavilionBreakRatio: 0.44, culetRatio: 0.025,
  }),
  oval: Object.freeze({
    id: 'oval-brilliant-preview-v2', outlineKind: 'ellipse', outlineSegments: 20,
    tableRatio: 0.51, starRatio: 0.79, crownHeightRatio: 0.16,
    girdleThicknessRatio: 0.05, pavilionDepthRatio: 0.79,
    pavilionBreakRatio: 0.43, culetRatio: 0.028,
  }),
  cushion: Object.freeze({
    id: 'cushion-brilliant-preview-v2', outlineKind: 'cushion', outlineSegments: 24,
    outlineExponent: 4, tableRatio: 0.50, starRatio: 0.77,
    crownHeightRatio: 0.17, girdleThicknessRatio: 0.05,
    pavilionDepthRatio: 0.78, pavilionBreakRatio: 0.43, culetRatio: 0.026,
  }),
  pear: Object.freeze({
    id: 'pear-brilliant-preview-v2', outlineKind: 'pear', outlineSegments: 24,
    tableRatio: 0.50, starRatio: 0.78, crownHeightRatio: 0.15,
    girdleThicknessRatio: 0.05, pavilionDepthRatio: 0.80,
    pavilionBreakRatio: 0.42, culetRatio: 0.024,
  }),
  emerald: Object.freeze({
    id: 'emerald-step-preview-v2', outlineKind: 'clipped_rectangle', outlineSegments: 24,
    cornerCutRatio: 0.16, tableRatio: 0.62, starRatio: 0.86,
    crownHeightRatio: 0.12, girdleThicknessRatio: 0.05,
    pavilionDepthRatio: 0.83, pavilionBreakRatio: 0.48, culetRatio: 0.03,
  }),
  princess: Object.freeze({
    id: 'princess-brilliant-preview-v2', outlineKind: 'clipped_square', outlineSegments: 24,
    cornerCutRatio: 0.045, tableRatio: 0.64, starRatio: 0.88,
    crownHeightRatio: 0.13, girdleThicknessRatio: 0.05,
    pavilionDepthRatio: 0.82, pavilionBreakRatio: 0.47, culetRatio: 0.02,
  }),
  radiant: Object.freeze({
    id: 'radiant-mixed-preview-v2', outlineKind: 'clipped_rectangle', outlineSegments: 24,
    cornerCutRatio: 0.24, tableRatio: 0.58, starRatio: 0.84,
    crownHeightRatio: 0.14, girdleThicknessRatio: 0.05,
    pavilionDepthRatio: 0.81, pavilionBreakRatio: 0.46, culetRatio: 0.024,
  }),
});

export const METAL_PRESETS = Object.freeze({
  yellow: Object.freeze({
    id: 'myjewl-yellow-metal-v2', color: 0xe0b65b, envMapIntensity: 1.28,
  }),
  white: Object.freeze({
    id: 'myjewl-white-metal-v2', color: 0xe8ecef, envMapIntensity: 1.38,
  }),
  rose: Object.freeze({
    id: 'myjewl-rose-metal-v2', color: 0xd39180, envMapIntensity: 1.22,
  }),
});

export const PURITY_PRESETS = Object.freeze({
  '14k': Object.freeze({
    id: 'myjewl-14k-appearance-v1',
    colors: Object.freeze({ yellow: 0xd9b974, white: 0xe9ecee, rose: 0xd99c8e }),
    envMapFactor: 0.96,
  }),
  '18k': Object.freeze({
    id: 'myjewl-18k-appearance-v1',
    colors: Object.freeze({ yellow: 0xe0aa45, white: 0xe2e6e8, rose: 0xc98270 }),
    envMapFactor: 1.02,
  }),
  pt950: Object.freeze({
    id: 'myjewl-pt950-appearance-v1',
    colors: Object.freeze({ white: 0xdde5e9 }),
    envMapFactor: 1.06,
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
    id: 'deterministic-2d-v2', maxPixelRatio: 1.0, shadowMapSize: 0,
    postProcessing: false,
  }),
});

const GEOMETRY_FIELDS = Object.freeze([
  'architecture', 'stone_shape', 'stone_dimensions_mm', 'ring_size_us',
  'band_width_mm', 'band_thickness_mm', 'prong_count', 'center_setting',
  'head_family', 'head_geometry', 'gallery_structure', 'side_setting',
  'head_style_id', 'reference_geometry_id', 'side_stones', 'accent_stones',
  'band_profile', 'shoulder_style', 'pave',
]);

export function facetProfileFor(shape) {
  return FACET_PROFILES[shape] || FACET_PROFILES.round;
}

export function metalPresetFor(family, purity, finish) {
  const resolvedPurity = PURITY_PRESETS[purity] || PURITY_PRESETS['14k'];
  const resolvedFamily = purity === 'pt950' ? 'white' : family;
  const metal = METAL_PRESETS[resolvedFamily] || METAL_PRESETS.yellow;
  return {
    metal,
    purity: resolvedPurity,
    finish: FINISH_PRESETS[finish] || FINISH_PRESETS.polished,
    color: resolvedPurity.colors[resolvedFamily] || metal.color,
    envMapIntensity: metal.envMapIntensity * resolvedPurity.envMapFactor,
  };
}

// These projection/signature helpers fingerprint the procedural operational
// design aid only. They are not the server-authoritative production-preview
// geometry signature used by PreviewManifest resolution.
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

export const operationalAidGeometryProjection = previewGeometryProjection;
export const operationalAidGeometrySignature = previewGeometrySignature;

export function previewMaterialSignature(preview = {}) {
  const preset = metalPresetFor(
    preview.metal_family,
    preview.metal_purity || preview.metal?.purity,
    preview.metal_finish,
  );
  return `${preset.metal.id}:${preset.purity.id}:${preset.finish.id}`;
}
