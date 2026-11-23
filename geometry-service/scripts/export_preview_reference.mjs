#!/usr/bin/env node
/**
 * Export the exact Three.js preview triangles for non-certifying Cycles evidence.
 *
 * This is evidence tooling only. It does not create canonical or manufacturing
 * geometry and it never calls the geometry API or paid compute.
 */

import { mkdir, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import {
  PREVIEW_PROFILE_VERSION,
  buildPreviewReference,
  defaultPreview,
} from '../fast_mvp/static/ring3d.js';

const FIXED_POSE = Object.freeze({ x: 0.38, y: -0.36, z: 0 });

function trilogyPreview() {
  return {
    ...defaultPreview(),
    architecture: 'trilogy',
    head_family: 'petal',
    head_geometry: 'petal_prong',
    gallery_structure: 'petal_open',
    side_setting: 'prong',
    side_stones: [{
      stone_id: 'side_pear_pair', role: 'side', shape: 'pear', quantity: 2,
      dimensions_mm: { length: 3.8, width: 2.4, depth: 1.5 },
      placement: {
        region: 'head_sides', pattern: 'symmetric_pair', orientation: 'points_outward',
      },
    }],
    band_width_mm: 2.4,
    band_thickness_mm: 1.8,
    band_profile: 'knife_edge',
    shoulder_style: 'tapered_cathedral',
    pave: {
      coverage: 'half', setting_style: 'bead_set',
      accent_stone_ids: ['band_round_accents_14'],
    },
    accent_stones: [{
      stone_id: 'band_round_accents_14', role: 'accent', shape: 'round', quantity: 14,
      dimensions_mm: { length: 1.15, width: 1.15, depth: 0.72 },
      placement: { region: 'band', pattern: 'mirrored_row', orientation: 'radial' },
    }],
    metal_family: 'rose',
    metal_finish: 'brushed',
  };
}

function safeName(value) {
  return String(value || 'mesh').replace(/[^a-zA-Z0-9_.-]+/g, '_');
}

function sceneToObj(group) {
  group.rotation.set(FIXED_POSE.x, FIXED_POSE.y, FIXED_POSE.z);
  group.updateMatrixWorld(true);
  const lines = [
    '# MyJewl preview-only geometry; not Rhino or manufacturing authority',
    `# facet-profile ${PREVIEW_PROFILE_VERSION}`,
  ];
  let vertexOffset = 1;
  let objectCount = 0;
  let triangleCount = 0;
  const duplicateNames = new Map();

  group.traverse((object) => {
    if (!object.isMesh || !object.geometry?.getAttribute('position')) return;
    const count = duplicateNames.get(object.name) || 0;
    duplicateNames.set(object.name, count + 1);
    const name = `${safeName(object.name)}_${String(count + 1).padStart(2, '0')}`;
    const geometry = object.geometry.clone();
    geometry.applyMatrix4(object.matrixWorld);
    const position = geometry.getAttribute('position');
    lines.push(`o ${name}`);
    for (let index = 0; index < position.count; index += 1) {
      lines.push(
        `v ${position.getX(index).toFixed(7)} ${position.getY(index).toFixed(7)} ${position.getZ(index).toFixed(7)}`
      );
    }
    const indices = geometry.index?.array;
    if (indices) {
      for (let index = 0; index < indices.length; index += 3) {
        lines.push(
          `f ${indices[index] + vertexOffset} ${indices[index + 1] + vertexOffset} ${indices[index + 2] + vertexOffset}`
        );
        triangleCount += 1;
      }
    } else {
      for (let index = 0; index < position.count; index += 3) {
        lines.push(`f ${index + vertexOffset} ${index + vertexOffset + 1} ${index + vertexOffset + 2}`);
        triangleCount += 1;
      }
    }
    vertexOffset += position.count;
    objectCount += 1;
    geometry.dispose();
  });
  return { source: `${lines.join('\n')}\n`, objectCount, triangleCount };
}

async function exportCase(outputDir, id, preview) {
  const group = await buildPreviewReference(preview);
  const exported = sceneToObj(group);
  const fileName = `${id}.obj`;
  await writeFile(resolve(outputDir, fileName), exported.source, 'utf8');
  return {
    id,
    obj: fileName,
    preview,
    fixed_pose_radians: FIXED_POSE,
    facet_profile_version: PREVIEW_PROFILE_VERSION,
    geometry_signature: group.userData.geometrySignature,
    material_signature: group.userData.materialSignature,
    stone_counts: group.userData.stoneCounts,
    object_count: exported.objectCount,
    triangle_count: exported.triangleCount,
    authority: 'preview_only_not_rhino',
  };
}

const outputDir = resolve(process.argv[2] || 'output/goal_0008_preview_20260728/cycles_reference');
await mkdir(outputDir, { recursive: true });
const cases = [
  await exportCase(outputDir, 'solitaire_yellow_polished', defaultPreview()),
  await exportCase(outputDir, 'trilogy_rose_brushed', trilogyPreview()),
];
const manifest = {
  schema: 'myjewl.preview_cycles_reference.v1',
  generated_by: 'scripts/export_preview_reference.mjs',
  comparison_only: true,
  manufacturing_authority: false,
  cases,
};
await writeFile(
  resolve(outputDir, 'preview_reference_manifest.json'),
  `${JSON.stringify(manifest, null, 2)}\n`,
  'utf8'
);
console.log(JSON.stringify(manifest, null, 2));
