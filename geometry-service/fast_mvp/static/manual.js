import { defaultPreview, initRing3D } from '/static/ring3d.js';

const state = {
  source: 'manual', options: null, headId: null, bandId: null, report: null,
  previewManifest: null, previewApproval: null, scenePurpose: null,
  delivery: {
    status: 'unavailable', displayed_status: 'unavailable', asset_status: 'not_applicable',
    renderer: 'none', reason_code: 'not_resolved', retryable: false,
    asset_id: null, asset_version: null, content_sha256: null,
  },
};
let scene = null;
let sceneGeneration = 0;

boot();

async function boot() {
  bind();
  bindPreviewDelivery();
  await showOperationalAid();
  const animate = () => { scene?.render(); requestAnimationFrame(animate); };
  requestAnimationFrame(animate);
  updatePreview();
}

function bind() {
  document.querySelectorAll('[data-source]').forEach(button => button.addEventListener('click', () => {
    state.source = button.dataset.source;
    document.querySelectorAll('[data-source]').forEach(item => item.classList.toggle('active', item === button));
    document.getElementById('manual-fields').hidden = state.source !== 'manual';
    document.getElementById('certificate-fields').hidden = state.source !== 'certificate';
    clearOptions();
    updatePreview();
  }));
  document.querySelectorAll('input,select').forEach(input => input.addEventListener('input', () => {
    if (!['metal-family', 'metal-purity', 'metal-finish'].includes(input.id)) {
      clearOptions();
    } else {
      invalidateReport();
    }
    enforceMetalCombination();
    updatePreview();
  }));
  document.getElementById('find-styles').addEventListener('click', loadOptions);
  document.getElementById('config-form').addEventListener('submit', createConfig);
  document.getElementById('request-geometry').addEventListener('click', requestGeometry);
  document.getElementById('retry-preview').addEventListener('click', retryProductionPreview);
  document.getElementById('preview-decision').addEventListener('click', recordPreviewDecision);
}

function number(id, optional = false) {
  const value = document.getElementById(id).value.trim();
  return optional && value === '' ? null : Number(value);
}

function typedPayload() {
  const payload = {
    ring_size_us: number('ring-size'),
    metal: {
      color_family: value('metal-family'),
      purity: value('metal-purity'),
      finish: value('metal-finish'),
    },
  };
  if (state.source === 'certificate') {
    payload.certificate = {
      lab: value('cert-lab'), report_number: value('cert-number'),
      shape: value('cert-shape'), measurements: value('cert-measurements'),
      carat: number('cert-carat', true), reviewed: document.getElementById('cert-reviewed').checked,
    };
  } else {
    Object.assign(payload, {
      stone_shape: value('stone-shape'), stone_length_mm: number('stone-length'),
      stone_width_mm: number('stone-width'), stone_depth_mm: number('stone-depth'),
      stone_carat_eq: number('stone-carat', true),
    });
  }
  return payload;
}

async function post(path, payload) {
  const response = await fetch(path, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(payload) });
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || `Request failed (${response.status})`);
  return body;
}

async function loadOptions() {
  message('options-message', 'Checking the internal catalog…');
  try {
    state.options = await post('/api/manual-options', typedPayload());
    state.headId = null; state.bandId = null;
    renderHeads();
    document.getElementById('styles-panel').hidden = false;
    message('options-message', `${state.options.head_styles.length} compatible heads found.`);
  } catch (error) {
    clearOptions(); message('options-message', error.message, true);
  }
}

function renderHeads() {
  const root = document.getElementById('head-options'); root.replaceChildren();
  state.options.head_styles.forEach(head => root.append(optionCard(head.head_style_id, head.display_name, head.description, () => {
    state.headId = head.head_style_id; state.bandId = null; invalidateReport(); renderHeads(); renderBands(); updatePreview();
  }, state.headId === head.head_style_id)));
  renderBands();
}

function renderBands() {
  const root = document.getElementById('band-options'); root.replaceChildren();
  const bands = state.headId ? state.options.band_styles_by_head[state.headId] || [] : [];
  if (!bands.length) { const p = document.createElement('p'); p.className = 'hint'; p.textContent = 'Choose a head first.'; root.append(p); }
  bands.forEach(band => root.append(optionCard(band.band_style_id, band.display_name, band.description, () => {
    state.bandId = band.band_style_id; invalidateReport(); renderBands(); updatePreview();
  }, state.bandId === band.band_style_id)));
  document.getElementById('create-config').disabled = !(state.headId && state.bandId);
}

function optionCard(id, name, description, select, selected) {
  const button = document.createElement('button'); button.type = 'button';
  button.className = `option-card${selected ? ' selected' : ''}`; button.dataset.id = id;
  const strong = document.createElement('strong'); strong.textContent = name;
  const detail = document.createElement('span'); detail.textContent = description;
  button.append(strong, detail); button.addEventListener('click', select); return button;
}

// Goal 0023. The server owns the decision; this only reports it, and always names
// the volume the weight came from — the measured union, never the parts sum.
function quoteLine(quote) {
  if (!quote || quote.status !== 'indicative' || quote.metal_weight_g === null) {
    return `Quote: pending geometry — ${quote?.reason || 'no receipt-backed ring volume; no weight or price was invented.'}`
      + ` ${quote ? priceNote(quote) : ''}`.trimEnd();
  }
  const cost = quote.metal_content_cost;
  const ev = quote.volume_evidence;
  let line = `Quote: ${Number(quote.metal_weight_g).toFixed(3)} g metal`
    + (cost === null || cost === undefined
      ? ' (unpriced)'
      : ` · ${quote.currency ? `${quote.currency} ${cost}` : `${cost} (currency not stated)`} metal content`);
  if (ev) {
    const over = (Number(ev.parts_sum_overcount_fraction) * 100).toFixed(2);
    line += `. Basis ${ev.union_mm3} mm³ measured union (${ev.union_engine}) of ${ev.metal_part_count}`
      + ` parts from ${ev.asset_id}; the naive parts sum of ${ev.parts_sum_mm3} mm³ over-counts by ${over}%`
      + ` and is not the basis.`;
  }
  return `${line} ${alloyWeightsLine(quote)} ${priceNote(quote)}`
    + ` Excludes ${(quote.excludes || []).join(', ')}.`
    + ` Measurement only — not a manufacturing certification.`;
}

// One measured volume, every alloy — the product's actual deliverable. The server
// computes all seven; this only reads them.
function alloyWeightsLine(quote) {
  const rows = quote.metal_weight_by_alloy;
  if (!rows || !rows.length) return '';
  const parts = rows.map((r) =>
    `${r.display_name} ${Number(r.metal_weight_g).toFixed(2)} g / ${Number(r.metal_weight_dwt).toFixed(2)} dwt`
    + ` (SPG ${Number(r.spg).toFixed(2)})${r.selected ? ' — selected' : ''}`);
  return `By alloy — ${parts.join('; ')}. Same geometry throughout; only the SPG changes.`;
}

// Goal 0024. Where the price came from, or which condition stopped it being
// priced. A cost never appears without its currency and its exclusions.
function priceNote(quote) {
  const ref = quote.price_refusal;
  if (ref) return `Metal content unpriced: ${ref.detail} ${ref.no_fallback_note || ''}`.trim();
  const pe = quote.price_evidence;
  if (!pe) return '';
  if (!pe.sheet_id) {
    return `Priced at a caller-supplied spot rate of ${pe.price_per_gram}/g — no citation, no as-of date, no expiry.`;
  }
  return `Priced from sheet ${pe.sheet_id}@${pe.sheet_version} at ${pe.currency} ${pe.price_per_gram}/g`
    + ` (${pe.price_basis}), as of ${pe.as_of}, source ${pe.source}, expires ${pe.expires_on}.`
    + ` Metal content only — not the price of a finished ring.`;
}

async function createConfig(event) {
  event.preventDefault(); message('config-message', 'Building and validating the canonical spec…');
  try {
    state.report = await post('/api/manual-configure', { ...typedPayload(), head_style_id: state.headId, band_style_id: state.bandId });
    if (state.report.status !== 'accepted') throw new Error('The live canonical gate rejected this selection.');
    message('config-message', `Accepted as ${state.report.run.job_id}.`);
    document.getElementById('accepted-result').hidden = false;
    document.getElementById('geometry-identity').textContent = state.report.geometry_identity;
    document.getElementById('quote-status').textContent = quoteLine(state.report.quote);
    state.previewManifest = previewManifestFrom(state.report);
    state.previewApproval = null;
    updatePreviewSummary(state.report.preview);
    await showProductionPreview();
  } catch (error) { message('config-message', error.message, true); }
}

async function requestGeometry() {
  if (!state.report?.run?.job_id) return;
  const button = document.getElementById('request-geometry'); button.disabled = true;
  try {
    const request = await post('/api/request-geometry', { job_id: state.report.run.job_id });
    button.textContent = `Geometry ${request.status}`;
  } catch (error) { button.disabled = false; button.textContent = error.message; }
}

function selectedHead() { return state.options?.head_styles.find(item => item.head_style_id === state.headId); }
function selectedBand() { return (state.options?.band_styles_by_head[state.headId] || []).find(item => item.band_style_id === state.bandId); }

function currentPreview() {
  const fallback = defaultPreview();
  const certDims = parseMeasurements(value('cert-measurements'));
  const dims = state.source === 'certificate' ? certDims : { length:number('stone-length'), width:number('stone-width'), depth:number('stone-depth') };
  const head = selectedHead(); const band = selectedBand();
  return {
    ...fallback, stone_shape: state.source === 'certificate' ? value('cert-shape') : value('stone-shape'),
    stone_dimensions_mm: dims || fallback.stone_dimensions_mm,
    carat: state.source === 'certificate' ? number('cert-carat', true) : number('stone-carat', true),
    ring_size_us: number('ring-size'), metal_family: value('metal-family'),
    center_setting: head?.canonical_mapping['setting.center_setting'] || 'prong',
    prong_count: head?.canonical_mapping['setting.prong_count'] ?? 4,
    architecture: head?.canonical_mapping['job.archetype'] || 'solitaire',
    head_family: head?.canonical_mapping['setting.head_family'] || 'classic',
    head_geometry: head?.canonical_mapping['setting.head_geometry'] || 'open_prong',
    gallery_structure: head?.canonical_mapping['setting.gallery_structure'] || 'open_rail',
    head_style_id: head?.head_style_id || null,
    reference_geometry_id: head?.visual_reference?.template_id || null,
    visual_reference: head?.visual_reference || null,
    side_setting: head?.canonical_mapping['setting.side_setting'] || 'none',
    side_stones: head?.side_stone_schedules || [],
    band_width_mm: band?.canonical_mapping['ring.band_width_mm'] || 2.0,
    band_thickness_mm: band?.canonical_mapping['ring.band_thickness_mm'] || 1.6,
    band_profile: band?.canonical_mapping['ring.band_profile'] || 'comfort_fit',
    shoulder_style: band?.canonical_mapping['ring.shoulder_style'] || 'straight',
    pave: band?.canonical_mapping['ring.pave'] || { coverage:'none', setting_style:'none', accent_stone_ids:[] },
    accent_stones: band?.pave?.accent_stone_schedule ? [band.pave.accent_stone_schedule] : [],
    band_style_id: band?.band_style_id || null,
    metal_finish: value('metal-finish'),
  };
}

function updatePreview(reportPreview = null) {
  const preview = reportPreview || currentPreview();
  if (state.scenePurpose === 'operational_aid') scene?.rebuild(preview, 'rendered');
  updatePreviewSummary(preview);
}

function updatePreviewSummary(preview) {
  const dims = preview.stone_dimensions_mm; const head = selectedHead(); const band = selectedBand();
  document.getElementById('preview-title').textContent = `${title(preview.stone_shape)} ${title(preview.architecture || 'solitaire')}`;
  const rows = document.querySelectorAll('#preview-summary dd');
  rows[0].textContent = `${Number(dims.length).toFixed(2)} × ${Number(dims.width).toFixed(2)} × ${Number(dims.depth).toFixed(2)} mm`;
  rows[1].textContent = head?.display_name || 'Choose a compatible style';
  rows[2].textContent = band?.display_name || 'Choose a compatible style';
  rows[3].textContent = (value('metal-purity') === 'pt950' ? 'Platinum 950' : `${value('metal-purity')} ${value('metal-family')} gold`) + ` · ${value('metal-finish')}`;
}

function previewManifestFrom(report) {
  const manifest = report?.preview_manifest || report?.preview?.preview_manifest;
  if (manifest && typeof manifest === 'object') return manifest;
  return {
    schema: 'myjewl.preview_manifest.v0_1', manifest_id: null,
    preview_status: 'unavailable', reason_code: 'legacy_preview_manifest_missing',
    representation_kind: 'none', selected_asset: null,
    geometry: { signature: '', projection: {} },
    manufacturing_authority: false,
    manufacturing_state: 'pending_not_run_needs_calibration',
  };
}

function bindPreviewDelivery() {
  const canvas = document.getElementById('preview-canvas');
  if (!canvas || canvas.dataset.deliveryBound === 'true') return;
  canvas.dataset.deliveryBound = 'true';
  canvas.addEventListener('myjewl-preview-delivery', event => {
    const detail = event.detail || {};
    state.delivery = {
      status: detail.status || state.previewManifest?.preview_status || 'unavailable',
      displayed_status: detail.displayed_status || 'unavailable',
      asset_status: detail.asset_status || 'error',
      renderer: detail.renderer || 'none',
      reason_code: detail.reason_code || 'asset_delivery_failed',
      retryable: detail.retryable === true,
      asset_id: detail.asset_id || null,
      asset_version: detail.asset_version || null,
      content_sha256: detail.content_sha256 || null,
    };
    updateDeliveryUI();
  });
}

async function showOperationalAid() {
  const generation = ++sceneGeneration;
  scene?.dispose();
  scene = null;
  state.scenePurpose = 'operational_aid';
  const canvas = document.getElementById('preview-canvas');
  setOperationalLabels(canvas);
  try {
    const nextScene = await initRing3D(canvas, {
      purpose: 'operational_aid', hero: false, bloom: true, spin: true,
      initialPreview: currentPreview(),
    });
    if (generation !== sceneGeneration) { nextScene.dispose(); return; }
    scene = nextScene;
  } catch (error) {
    console.warn('Operational design aid unavailable:', error);
  }
}

function setOperationalLabels(canvas) {
  document.getElementById('preview-delivery').hidden = true;
  document.getElementById('preview-purpose-label').textContent = 'Operational design aid';
  document.getElementById('preview-status-badge').textContent = 'out of preview';
  canvas?.setAttribute('aria-label', 'Procedural operational design aid outside the production preview contract. Use the arrow keys to rotate.');
  canvas?.setAttribute('tabindex', '0');
  message('preview-decision-message', '');
  document.getElementById('retry-preview').hidden = true;
  document.getElementById('preview-decision').hidden = true;
}

async function showProductionPreview({ retry = false } = {}) {
  if (retry && scene?.getDiagnostics?.().rendererPath !== 'none') {
    await scene.retry();
    return;
  }
  const generation = ++sceneGeneration;
  scene?.dispose();
  scene = null;
  state.scenePurpose = 'production_preview';
  state.delivery = {
    status: state.previewManifest?.preview_status === 'unavailable' ? 'unavailable' : 'loading',
    displayed_status: 'unavailable',
    asset_status: state.previewManifest?.preview_status === 'unavailable' ? 'not_applicable' : 'loading',
    renderer: state.previewManifest?.selected_asset?.renderer || 'none',
    reason_code: state.previewManifest?.reason_code || 'preview_manifest_missing',
    retryable: false, asset_id: state.previewManifest?.selected_asset?.asset_id || null,
    asset_version: state.previewManifest?.selected_asset?.asset_version || null,
    content_sha256: state.previewManifest?.selected_asset?.content?.sha256 || null,
  };
  updateDeliveryUI();
  const canvas = document.getElementById('preview-canvas');
  try {
    const nextScene = await initRing3D(canvas, {
      purpose: 'production_preview', hero: false, bloom: true, spin: true,
      initialPreview: state.report?.preview || currentPreview(),
      previewManifest: state.previewManifest,
    });
    if (generation !== sceneGeneration) { nextScene.dispose(); return; }
    scene = nextScene;
  } catch (error) {
    console.warn('Production preview unavailable:', error);
    state.delivery = {
      ...state.delivery,
      status: state.previewManifest?.selected_asset ? 'failure' : 'unavailable',
      asset_status: 'error', displayed_status: 'unavailable',
      reason_code: 'renderer_initialization_failed', retryable: true,
    };
    updateDeliveryUI();
  }
}

async function retryProductionPreview() {
  const button = document.getElementById('retry-preview');
  button.disabled = true;
  button.textContent = 'Retrying…';
  try { await showProductionPreview({ retry: true }); }
  finally { button.disabled = false; button.textContent = 'Retry preview'; updateDeliveryUI(); }
}

function reasonLabel(code) {
  const labels = {
    approved_exact_asset: 'The server resolved the registered full-ring V4 asset.',
    approved_estimated_asset: 'The server resolved an approved bounded estimate.',
    no_approved_preview_asset: 'No approved production preview represents this completed configuration.',
    no_registry_match: 'No registered production preview represents this completed configuration.',
    invalid_canonical_spec: 'The completed configuration could not be normalized for preview resolution.',
    registry_invalid: 'The production preview registry did not pass validation.',
    unsupported_geometry_parameters: 'Required style identifiers are unavailable for preview resolution.',
    ambiguous_preview_asset: 'Preview resolution was ambiguous and was rejected.',
    legacy_preview_manifest_missing: 'This legacy result has no server-authoritative preview resolution.',
    asset_unavailable: 'The registered asset could not be downloaded.',
    asset_timeout: 'The registered asset request timed out.',
    asset_hash_mismatch: 'The registered asset failed integrity verification.',
    delivery_manifest_malformed: 'The registered delivery manifest was malformed.',
    delivery_manifest_mismatch: 'The delivery manifest did not match the registry.',
    binary_contract_mismatch: 'The registered mesh failed binary contract checks.',
    webgl_render_failed: 'The production renderer failed before a product frame was displayed.',
    webgl_context_lost: 'The production rendering context was lost.',
    webgl_unavailable: 'WebGL is unavailable in this browser.',
    threejs_unavailable: 'The local production renderer could not be loaded.',
  };
  return labels[code] || String(code || 'preview_unavailable').replaceAll('_', ' ');
}

function updateDeliveryUI() {
  if (state.scenePurpose !== 'production_preview') return;
  const delivery = state.delivery;
  const overlay = document.getElementById('preview-delivery');
  const canvas = document.getElementById('preview-canvas');
  const retry = document.getElementById('retry-preview');
  const decision = document.getElementById('preview-decision');
  const ready = delivery.asset_status === 'ready'
    && ['exact', 'estimated'].includes(delivery.displayed_status);
  overlay.classList.toggle('is-unavailable', !ready && delivery.asset_status !== 'loading');
  if (ready) {
    overlay.hidden = true;
    document.getElementById('preview-purpose-label').textContent = delivery.displayed_status === 'exact'
      ? 'Exact production preview' : 'Estimated production preview';
    document.getElementById('preview-status-badge').textContent = delivery.displayed_status;
    canvas.setAttribute('aria-label', `${delivery.displayed_status} production preview. Drag or use the arrow keys to rotate.`);
    canvas.setAttribute('tabindex', '0');
    message('preview-decision-message', `${delivery.displayed_status} production preview displayed after integrity verification. Manufacturing review remains separate.`);
  } else {
    overlay.hidden = false;
    const loading = delivery.asset_status === 'loading';
    document.getElementById('preview-delivery-title').textContent = loading
      ? 'Verifying production preview…' : 'Production preview unavailable';
    document.getElementById('preview-delivery-detail').textContent = loading
      ? 'The viewport stays empty until the registered asset passes integrity checks.'
      : `${reasonLabel(delivery.reason_code)} No procedural or 2D substitute is shown.`;
    document.getElementById('preview-purpose-label').textContent = loading
      ? 'Production preview loading' : 'Production preview unavailable';
    document.getElementById('preview-status-badge').textContent = loading ? 'loading' : 'unavailable';
    canvas.setAttribute('aria-label', loading ? 'Production preview loading.' : 'Production preview unavailable.');
    canvas.setAttribute('tabindex', '-1');
    message(
      'preview-decision-message',
      loading
        ? 'Verifying the registered production preview. The viewport remains empty until verification and rendering succeed.'
        : `Production preview unavailable: ${reasonLabel(delivery.reason_code)} No procedural or 2D substitute is shown.`,
    );
  }
  retry.hidden = !(delivery.retryable && !ready);
  const canRecord = Boolean(state.report?.run?.job_id && state.previewManifest?.manifest_id && !state.previewApproval);
  if (canRecord && ready) {
    decision.hidden = false;
    decision.dataset.decision = 'approved';
    decision.textContent = `Approve ${delivery.displayed_status} preview`;
  } else if (canRecord && delivery.asset_status !== 'loading' && delivery.displayed_status === 'unavailable') {
    decision.hidden = false;
    decision.dataset.decision = 'continued_without_preview';
    decision.textContent = 'Continue without preview';
  } else {
    decision.hidden = true;
    decision.dataset.decision = '';
  }
}

async function recordPreviewDecision() {
  const button = document.getElementById('preview-decision');
  const decision = button.dataset.decision;
  if (!decision || !state.report?.run?.job_id || !state.previewManifest?.manifest_id) return;
  button.disabled = true;
  try {
    const response = await post('/api/preview-approval', {
      job_id: state.report.run.job_id,
      manifest_id: state.previewManifest.manifest_id,
      decision,
      delivery: {
        status: state.delivery.status,
        displayed_status: state.delivery.displayed_status,
        renderer: state.delivery.renderer,
        reason_code: state.delivery.reason_code,
        asset_id: state.delivery.asset_id,
        asset_version: state.delivery.asset_version,
        content_sha256: state.delivery.content_sha256,
      },
    });
    state.previewApproval = response.preview_approval || response.approval || { decision };
    button.hidden = true;
    message('preview-decision-message', decision === 'approved'
      ? 'Preview approval recorded append-only; manufacturing review remains separate.'
      : 'Decision to continue without preview recorded append-only.');
  } catch (error) {
    button.disabled = false;
    message('preview-decision-message', error.message, true);
  }
}

function enforceMetalCombination() {
  const purity = value('metal-purity'); const family = document.getElementById('metal-family');
  [...family.options].forEach(option => option.disabled = purity === 'pt950' && option.value !== 'white');
  if (purity === 'pt950') family.value = 'white';
}

function parseMeasurements(text) {
  const values = (text.match(/[\d.]+/g) || []).map(Number);
  if (values.length === 3) return { length:Math.max(values[0], values[1]), width:Math.min(values[0], values[1]), depth:values[2] };
  return null;
}

function clearOptions() {
  state.options = null; state.headId = null; state.bandId = null; state.report = null;
  state.previewManifest = null; state.previewApproval = null;
  document.getElementById('styles-panel').hidden = true; document.getElementById('accepted-result').hidden = true;
  if (state.scenePurpose === 'production_preview') showOperationalAid();
}
function invalidateReport() {
  state.report = null;
  state.previewManifest = null; state.previewApproval = null;
  document.getElementById('accepted-result').hidden = true;
  if (state.scenePurpose === 'production_preview') showOperationalAid();
}
function value(id) { return document.getElementById(id).value; }
function title(text) { return text ? text[0].toUpperCase() + text.slice(1) : ''; }
function message(id, text, error = false) { const node = document.getElementById(id); node.textContent = text; node.classList.toggle('error', error); }
