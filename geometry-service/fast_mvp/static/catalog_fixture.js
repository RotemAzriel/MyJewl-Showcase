import { initRing3D, defaultPreview } from '/static/ring3d.js';

const params = new URLSearchParams(window.location.search);
const preview = defaultPreview();
preview.stone_shape = params.get('shape') || preview.stone_shape;
preview.center_setting = params.get('setting') || preview.center_setting;
preview.prong_count = Number(params.get('prongs') || preview.prong_count);
preview.metal_family = params.get('metal') || preview.metal_family;
preview.metal_finish = params.get('finish') || preview.metal_finish;
preview.band_width_mm = Number(params.get('bandWidth') || preview.band_width_mm);
preview.band_thickness_mm = Number(params.get('bandThickness') || preview.band_thickness_mm);

// A single fixture-only flag selects Goal 0007's exact bounded reference. It
// is not a product control and cannot compose unsupported catalog choices.
if (params.get('reference') === 'trilogy') {
  Object.assign(preview, {
    architecture: 'trilogy',
    stone_shape: 'round',
    stone_dimensions_mm: { length: 6.5, width: 6.5, depth: 3.9 },
    center_setting: 'prong',
    prong_count: 4,
    head_family: 'petal',
    head_geometry: 'petal_prong',
    gallery_structure: 'petal_open',
    side_setting: 'prong',
    side_stones: [{
      stone_id: 'side_pear_pair', role: 'side', shape: 'pear', quantity: 2,
      dimensions_mm: { length: 3.8, width: 2.4, depth: 1.5 },
      placement: { region: 'head_sides', pattern: 'symmetric_pair', orientation: 'points_outward' },
    }],
    band_width_mm: 2.4,
    band_thickness_mm: 1.8,
    band_profile: 'knife_edge',
    shoulder_style: 'tapered_cathedral',
    pave: { coverage: 'half', setting_style: 'bead_set', accent_stone_ids: ['band_round_accents_14'] },
    accent_stones: [{
      stone_id: 'band_round_accents_14', role: 'accent', shape: 'round', quantity: 14,
      dimensions_mm: { length: 1.15, width: 1.15, depth: 0.72 },
      placement: { region: 'band', pattern: 'mirrored_row', orientation: 'radial' },
    }],
  });
}

const scene = await initRing3D(document.getElementById('fixtureCanvas'), {
  hero: false,
  bloom: false,
  spin: false,
  forceFallback: params.get('fallback') === '1',
  initialPreview: preview,
});
scene.ringGroup?.rotation.set(0.58, -0.52, 0.04);
scene.targetX = 0.58;
scene.targetY = -0.52;
scene.render();
await new Promise(resolve => setTimeout(resolve, 1200));
scene.render();
const fixtureDataUrl = document.getElementById('fixtureCanvas').toDataURL('image/png');
window.__fixtureDataUrl = fixtureDataUrl;
document.getElementById('fixtureData').textContent = fixtureDataUrl;
document.body.dataset.ready = 'true';
