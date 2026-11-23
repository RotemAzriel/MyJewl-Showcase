#!/usr/bin/env node

import fs from 'node:fs/promises';
import path from 'node:path';

const playwrightModule = process.env.PLAYWRIGHT_MODULE || 'playwright';
const { chromium } = await import(playwrightModule);
const baseUrl = process.env.MYJEWL_BASE_URL || 'http://127.0.0.1:8765';
const outputDir = path.resolve(process.argv[2] || 'fast_mvp/catalog');
const shapes = ['round', 'oval', 'cushion', 'pear', 'emerald', 'radiant', 'princess'];
const variants = [
  { id: 'prong-yellow-classic', setting: 'prong', prongs: 4, metal: 'yellow', bandWidth: 2.0, bandThickness: 1.6 },
  { id: 'prong-white-six', setting: 'prong', prongs: 6, metal: 'white', bandWidth: 2.5, bandThickness: 1.8 },
  { id: 'prong-rose-slim', setting: 'prong', prongs: 4, metal: 'rose', bandWidth: 1.8, bandThickness: 1.5 },
  { id: 'bezel-yellow', setting: 'bezel', prongs: 0, metal: 'yellow', bandWidth: 2.4, bandThickness: 1.7 },
  { id: 'bezel-rose-wide', setting: 'bezel', prongs: 0, metal: 'rose', bandWidth: 3.0, bandThickness: 1.8 },
  { id: 'basket-white', setting: 'basket', prongs: 0, metal: 'white', bandWidth: 2.2, bandThickness: 1.6 },
];
const baseWidths = {
  round: 6.5, oval: 6.0, cushion: 6.5, pear: 5.5,
  emerald: 5.0, radiant: 5.5, princess: 5.5,
};

await fs.mkdir(path.join(outputDir, 'images'), { recursive: true });
const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 512, height: 512 }, deviceScaleFactor: 1 });
const entries = [];

for (const shape of shapes) {
  for (const variant of variants) {
    const prongs = shape === 'pear' && variant.setting === 'prong'
      ? 5
      : variant.prongs;
    const query = new URLSearchParams({
      shape,
      setting: variant.setting,
      prongs: String(prongs),
      metal: variant.metal,
      bandWidth: String(variant.bandWidth),
      bandThickness: String(variant.bandThickness),
    });
    await page.goto(`${baseUrl}/static/catalog_fixture.html?${query}`, { waitUntil: 'networkidle' });
    await page.waitForFunction(() => document.body.dataset.ready === 'true');
    const filename = `${shape}-${variant.id}.png`;
    const dataUrl = await page.locator('#fixtureData').textContent();
    await fs.writeFile(
      path.join(outputDir, 'images', filename),
      Buffer.from(dataUrl.split(',')[1], 'base64'),
    );
    entries.push({
      reference_id: `${shape}-${variant.id}`,
      image_path: `images/${filename}`,
      stone_shape: shape,
      center_setting: variant.setting,
      prong_count: prongs,
      metal_color_family: variant.metal,
      band_width_ratio: Number((variant.bandWidth / baseWidths[shape]).toFixed(6)),
      band_thickness_ratio: Number((variant.bandThickness / baseWidths[shape]).toFixed(6)),
    });
  }
}

await browser.close();
await fs.writeFile(
  path.join(outputDir, 'manifest.json'),
  `${JSON.stringify({
    schema_version: 1,
    purpose: 'development_fixture_only',
    renderer: 'fast_mvp/static/ring3d.js',
    background: 'black',
    entries,
  }, null, 2)}\n`,
);
console.log(`Generated ${entries.length} fixture references in ${outputDir}`);
