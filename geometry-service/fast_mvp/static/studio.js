/**
 * studio.js — MyJewl Studio Page
 *
 * Multi-step wizard with cinematic transitions + full workspace.
 * Integrates with ring3d.js for 3D preview and existing API endpoints.
 */

import { initRing3D, defaultPreview, clamp } from '/static/ring3d.js';

const studioParams = new URLSearchParams(window.location.search);
const WORKSPACE_ACCEPTED_STATUS = 'Design brief validated. Preview resolution is separate from manufacturing review.';
const OPERATIONAL_AID_LABEL = 'Procedural · operational design aid outside the production preview contract';

// ── State ──────────────────────────────────────────────────
const state = {
  currentStep: 1,
  path: null, // 'upload' | 'scratch' | 'fast'
  preview: { ...defaultPreview() },
  file: null,
  imageDataUrl: null,
  result: null,
  assets: null,
  needsAnalysis: true,
  stoneConfirmed: false,
  mountingEdited: false,
  spin: false,
  metalPurity: '14k',
  metalFinish: 'polished',
  catalogOptions: null,
  headStyleId: null,
  bandStyleId: null,
  manualReport: null,
  workspaceBound: false,
  previewManifest: null,
  previewApproval: null,
  // Goal 0025. The server's answer to "may this customer receive the geometry",
  // which is a different question from whether a preview renders. The UI never
  // decides it and never links to a file the server did not authorise.
  geometryDelivery: null,
  // Goal 0027. Whether this deployment reads photographs at all, straight from
  // the same perception gate /api/extract obeys. Seeded with the refusing answer
  // so the honest copy is what renders before -- and if -- the fetch lands.
  perception: null,
  delivery: {
    status: 'unavailable',
    displayed_status: 'unavailable',
    asset_status: 'not_applicable',
    renderer: 'none',
    reason_code: 'not_resolved',
    retryable: false,
    asset_id: null,
    asset_version: null,
    content_sha256: null,
  },
};

let wizardScene = null;
let workspaceScene = null;
let particleStep = null;

// ── Boot ───────────────────────────────────────────────────
boot();

async function boot() {
  // Check for fast-track query param
  if (studioParams.get('fast') === '1') {
    state.path = 'fast';
  }

  particleStep = initParticles();
  bindWizardEvents();
  // Before the first paint of step 1, so the upload card never advertises a
  // capability this deployment does not have. Awaited deliberately: it is one
  // local request, and a card that corrects itself after the customer has read
  // it is worse than one that was right to begin with.
  await refreshPerceptionState();

  // Start animation loop IMMEDIATELY — don't wait for 3D init
  requestAnimationFrame(animate);

  try {
    const wizardCanvas = document.getElementById('wizardCanvas');
    wizardScene = await initRing3D(wizardCanvas, {
      purpose: 'operational_aid',
      hero: true,
      bloom: true,
      spin: state.spin,
      forceFallback: studioParams.get('fallback') === '1',
      initialPreview: state.preview,
    });
    // Ring is already built inside initRing3D, no need to rebuild
  } catch (err) {
    console.warn('3D init failed:', err);
  }

  showStep(state.path === 'fast' ? 3 : 1);
}

function animate() {
  try { particleStep?.(); } catch (e) { /* skip frame */ }
  try { wizardScene?.render(); } catch (e) { /* skip frame */ }
  try { workspaceScene?.render(); } catch (e) { /* skip frame */ }
  requestAnimationFrame(animate);
}

// ── Wizard Navigation ──────────────────────────────────────
function showStep(stepNum) {
  state.currentStep = stepNum;
  const steps = document.querySelectorAll('.step');
  steps.forEach(s => s.classList.remove('active'));

  let target;
  if (stepNum === 'upload') {
    target = document.getElementById('stepUpload');
  } else {
    target = document.getElementById(`step${stepNum}`);
  }
  if (target) {
    // Force re-animation
    target.style.animation = 'none';
    target.offsetHeight; // trigger reflow
    target.style.animation = '';
    target.classList.add('active');
  }

  updateJourneyProgress(stageForScreen(stepNum));

  // Rebuild 3D on step change
  wizardScene?.rebuild(state.preview, 'rendered');
}

function stageForScreen(stepNum) {
  if (stepNum === 'upload' || Number(stepNum) === 1) return 0;
  if (Number(stepNum) <= 3) return 1;
  return 2;
}

function updateJourneyProgress(stage) {
  const labels = {
    0: 'Getting started',
    1: 'Stage 1 of 3: Choose your diamond',
    2: 'Stage 2 of 3: Build your setting',
    3: 'Stage 3 of 3: Complete your ring',
  };
  const indicator = document.getElementById('stepIndicator');
  if (indicator) indicator.textContent = labels[stage] || labels[0];
  document.querySelectorAll('[data-flow-step]').forEach(item => {
    const value = Number(item.dataset.flowStep);
    const current = value === stage;
    item.classList.toggle('active', current);
    item.classList.toggle('done', stage > 0 && value < stage);
    if (current) item.setAttribute('aria-current', 'step');
    else item.removeAttribute('aria-current');
  });
}

function bindWizardEvents() {
  // Step 1: Path selection
  document.getElementById('pathUpload')?.addEventListener('click', () => {
    state.path = 'upload';
    showStep('upload');
  });
  document.getElementById('pathScratch')?.addEventListener('click', () => {
    state.path = 'scratch';
    showStep(2);
  });
  document.getElementById('pathFast')?.addEventListener('click', () => {
    state.path = 'fast';
    showStep(3);
  });
  document.getElementById('fastTrackBtn')?.addEventListener('click', () => {
    state.path = 'fast';
    showStep(3);
  });

  // Step 2: Shape selection
  document.querySelectorAll('.shape-option').forEach(btn => {
    btn.addEventListener('click', () => {
      const shape = btn.dataset.shape;
      state.preview.stone_shape = shape;
      state.preview.stone_dimensions_mm = baseDims(shape);
      syncManualStoneInputs();
      state.preview.prong_count = inferProngCount(shape, state.preview.carat, state.preview.center_setting);
      document.querySelectorAll('.shape-option').forEach(button => {
        const selected = button === btn;
        button.classList.toggle('active', selected);
        button.setAttribute('aria-pressed', String(selected));
      });
      wizardScene?.rebuild(state.preview, 'rendered');
      updateViewportBadge();
    });
  });
  document.getElementById('step2Back')?.addEventListener('click', () => showStep(1));
  document.getElementById('step2Next')?.addEventListener('click', () => showStep(3));

  // Step 3: Stone spec
  document.querySelectorAll('.spec-tab').forEach(tab => {
    tab.addEventListener('click', () => {
      document.querySelectorAll('.spec-tab').forEach(t => t.classList.remove('active'));
      tab.classList.add('active');
      const panel = tab.dataset.tab;
      document.getElementById('specManual').classList.toggle('hidden', panel !== 'manual');
      document.getElementById('specCert').classList.toggle('hidden', panel !== 'cert');
    });
  });

  const caratInput = document.getElementById('caratInput');
  const sizeInput = document.getElementById('sizeInput');
  const stoneLengthInput = document.getElementById('stoneLengthInput');
  const stoneWidthInput = document.getElementById('stoneWidthInput');
  const stoneDepthInput = document.getElementById('stoneDepthInput');
  [caratInput, sizeInput, stoneLengthInput, stoneWidthInput, stoneDepthInput].forEach(el => {
    el?.addEventListener('input', () => {
      state.preview.carat = readOptionalNum(caratInput);
      state.preview.ring_size_us = readNum(sizeInput, 6.5);
      state.preview.stone_dimensions_mm = {
        length: readNum(stoneLengthInput, 6.5),
        width: readNum(stoneWidthInput, 6.5),
        depth: readNum(stoneDepthInput, 3.9),
      };
      wizardScene?.rebuild(state.preview, 'rendered');
      updateViewportBadge();
    });
  });

  const certInput = document.getElementById('certInput');
  certInput?.addEventListener('change', () => {
    const file = certInput.files[0];
    if (file) {
      document.getElementById('certText').textContent = file.name;
    }
  });

  document.getElementById('step3Back')?.addEventListener('click', () => showStep(2));
  document.getElementById('step3Next')?.addEventListener('click', () => {
    const error = validateManualStoneInputs();
    if (error) {
      const errorElement = document.getElementById('manualStoneError');
      set('manualStoneError', error);
      errorElement?.focus();
      return;
    }
    set('manualStoneError', '');
    showStep(4);
  });

  // Step 4: Metal. Head and band geometry are selected only from the
  // compatibility-filtered internal catalog on the next two steps.
  document.querySelectorAll('.metal-option').forEach(btn => {
    btn.addEventListener('click', () => {
      if (state.metalPurity === 'pt950' && btn.dataset.metal !== 'white') return;
      state.preview.metal_family = btn.dataset.metal;
      state.manualReport = null;
      document.querySelectorAll('.metal-option').forEach(button => {
        const selected = button === btn;
        button.classList.toggle('active', selected);
        button.setAttribute('aria-pressed', String(selected));
      });
      wizardScene?.rebuild(state.preview, 'rendered');
      updateViewportBadge();
    });
  });

  document.getElementById('metalPurityInput')?.addEventListener('input', event => {
    state.metalPurity = event.target.value;
    state.preview.metal_purity = state.metalPurity;
    document.querySelectorAll('.metal-option').forEach(button => {
      const unavailable = state.metalPurity === 'pt950' && button.dataset.metal !== 'white';
      button.disabled = unavailable;
      button.setAttribute('aria-disabled', String(unavailable));
    });
    if (state.metalPurity === 'pt950') {
      state.preview.metal_family = 'white';
      document.querySelectorAll('.metal-option').forEach(button => {
        const selected = button.dataset.metal === 'white';
        button.classList.toggle('active', selected);
        button.setAttribute('aria-pressed', String(selected));
      });
    }
    state.manualReport = null;
    wizardScene?.rebuild(state.preview, 'rendered');
    updateViewportBadge();
  });

  document.getElementById('metalFinishInput')?.addEventListener('input', event => {
    state.metalFinish = event.target.value;
    state.preview.metal_finish = state.metalFinish;
    state.manualReport = null;
    wizardScene?.rebuild(state.preview, 'rendered');
  });

  document.getElementById('step4Back')?.addEventListener('click', () => showStep(3));
  document.getElementById('step4Next')?.addEventListener('click', loadCompatibleHeads);

  // Steps 5–6: compatible head, then compatible band.
  document.getElementById('step5Back')?.addEventListener('click', () => showStep(4));
  document.getElementById('step5Next')?.addEventListener('click', () => {
    renderBandOptions();
    showStep(6);
  });
  document.getElementById('step6Back')?.addEventListener('click', () => showStep(5));
  document.getElementById('step6Next')?.addEventListener('click', configureManualDesign);

  // Upload step
  const photoInput = document.getElementById('photoInput');
  const photoPreview = document.getElementById('photoPreview');
  photoInput?.addEventListener('change', (e) => ingestPhoto(e.target.files?.[0]));
  ['dragenter', 'dragover'].forEach(type => {
    photoPreview?.addEventListener(type, event => {
      event.preventDefault();
      photoPreview.classList.add('is-dragging');
    });
  });
  ['dragleave', 'drop'].forEach(type => {
    photoPreview?.addEventListener(type, event => {
      event.preventDefault();
      photoPreview.classList.remove('is-dragging');
    });
  });
  photoPreview?.addEventListener('drop', event => {
    ingestPhoto(event.dataTransfer?.files?.[0]);
  });

  document.getElementById('stepUploadBack')?.addEventListener('click', () => showStep(1));
  document.getElementById('stepUploadNext')?.addEventListener('click', openStoneDialog);
  bindStoneDialog();
}

function validateManualStoneInputs() {
  const required = [
    document.getElementById('sizeInput'),
    document.getElementById('stoneLengthInput'),
    document.getElementById('stoneWidthInput'),
    document.getElementById('stoneDepthInput'),
  ];
  const invalid = required.find(input => !input?.checkValidity());
  if (invalid) {
    invalid.reportValidity();
    return 'Check the highlighted stone details.';
  }
  return '';
}

function manualTypedPayload() {
  const dims = state.preview.stone_dimensions_mm || {};
  return {
    stone_shape: state.preview.stone_shape,
    stone_length_mm: Number(dims.length),
    stone_width_mm: Number(dims.width),
    stone_depth_mm: Number(dims.depth),
    stone_carat_eq: state.preview.carat,
    ring_size_us: Number(state.preview.ring_size_us),
    metal: {
      color_family: state.preview.metal_family,
      purity: state.metalPurity,
      finish: state.metalFinish,
    },
  };
}

async function loadCompatibleHeads() {
  const button = document.getElementById('step4Next');
  setBusy(button, true, 'Checking styles');
  set('headOptionsError', '');
  set('headOptionsMessage', 'Checking the internal compatibility catalog…');
  try {
    const options = await postJson('/api/manual-options', manualTypedPayload());
    const headStillCompatible = (options.head_styles || [])
      .some(head => head.head_style_id === state.headStyleId);
    if (!headStillCompatible) {
      state.headStyleId = null;
      state.bandStyleId = null;
    } else {
      const bandStillCompatible = (options.band_styles_by_head?.[state.headStyleId] || [])
        .some(band => band.band_style_id === state.bandStyleId);
      if (!bandStillCompatible) state.bandStyleId = null;
    }
    state.catalogOptions = options;
    state.manualReport = null;
    renderHeadOptions();
    document.getElementById('step5Next').disabled = !state.headStyleId;
    document.getElementById('step6Next').disabled = !state.bandStyleId;
    showStep(5);
  } catch (error) {
    const errorElement = document.getElementById('headOptionsError');
    set('headOptionsError', error.message);
    errorElement?.focus();
  } finally {
    setBusy(button, false, 'Continue →');
  }
}

function renderHeadOptions() {
  const root = document.getElementById('headOptions');
  root?.replaceChildren();
  const heads = state.catalogOptions?.head_styles || [];
  set('headOptionsMessage', `${heads.length} compatible head styles for this stone.`);
  heads.forEach(head => root?.append(styleOption(
    head.head_style_id,
    head.display_name,
    head.description,
    state.headStyleId === head.head_style_id,
    head.visual_reference,
    () => {
      const headChanged = state.headStyleId !== head.head_style_id;
      state.headStyleId = head.head_style_id;
      if (headChanged) state.bandStyleId = null;
      state.manualReport = null;
      document.getElementById('step6Next').disabled = !state.bandStyleId;
      const mapping = head.canonical_mapping || {};
      state.preview.architecture = mapping['job.archetype'] || 'solitaire';
      state.preview.center_setting = mapping['setting.center_setting'] || 'prong';
      state.preview.prong_count = mapping['setting.prong_count'] ?? 4;
      state.preview.head_family = mapping['setting.head_family'];
      state.preview.head_geometry = mapping['setting.head_geometry'];
      state.preview.gallery_structure = mapping['setting.gallery_structure'];
      state.preview.side_setting = mapping['setting.side_setting'];
      state.preview.side_stones = head.side_stone_schedules || [];
      state.preview.head_style_id = head.head_style_id;
      state.preview.visual_reference = head.visual_reference || null;
      state.preview.reference_geometry_id = head.visual_reference?.template_id || null;
      renderHeadOptions();
      wizardScene?.rebuild(state.preview, 'rendered');
      updateViewportBadge();
      document.getElementById('step5Next').disabled = false;
    },
    head.preview_availability
  )));
}

function renderBandOptions() {
  const root = document.getElementById('bandOptions');
  root?.replaceChildren();
  const bands = state.catalogOptions?.band_styles_by_head?.[state.headStyleId] || [];
  set('bandOptionsMessage', `${bands.length} band styles compatible with your chosen head.`);
  bands.forEach(band => root?.append(styleOption(
    band.band_style_id,
    band.display_name,
    band.description,
    state.bandStyleId === band.band_style_id,
    null,
    () => {
      state.bandStyleId = band.band_style_id;
      state.manualReport = null;
      const mapping = band.canonical_mapping || {};
      state.preview.band_width_mm = mapping['ring.band_width_mm'] || 2;
      state.preview.band_thickness_mm = mapping['ring.band_thickness_mm'] || 1.6;
      state.preview.band_profile = mapping['ring.band_profile'];
      state.preview.shoulder_style = mapping['ring.shoulder_style'];
      state.preview.pave = mapping['ring.pave'];
      state.preview.band_style_id = band.band_style_id;
      state.preview.accent_stones = band.pave?.accent_stone_schedule
        ? [band.pave.accent_stone_schedule]
        : [];
      renderBandOptions();
      wizardScene?.rebuild(state.preview, 'rendered');
      updateViewportBadge();
      document.getElementById('step6Next').disabled = false;
    }
  )));
}

function styleOption(id, name, description, selected, visualReference, onSelect, availability) {
  const button = document.createElement('button');
  button.type = 'button';
  button.className = `setting-option${selected ? ' active' : ''}`;
  button.dataset.styleId = id;
  button.setAttribute('aria-pressed', String(selected));
  const icon = document.createElement('span');
  icon.className = `style-thumb style-thumb-${id.replace(/[^a-z0-9]+/gi, '-')}`;
  icon.setAttribute('aria-hidden', 'true');
  const heading = document.createElement('h3');
  heading.textContent = name;
  const copy = document.createElement('p');
  copy.textContent = description;
  button.append(icon);
  if (visualReference?.status === 'approved') {
    const badge = document.createElement('span');
    badge.className = 'reference-badge';
    badge.textContent = 'GOAL 0011 SOURCE MASTER · RESOLVED AFTER SAVE';
    button.append(badge);
  }
  // Goal 0016: say up front whether this exact configuration can be previewed.
  // "Made to order" is an honest statement about missing evidence, not a defect:
  // the master can build it, but nothing has been solved for it yet, so no
  // preview will appear and no neighbouring size will be shown instead.
  if (availability?.status === 'made_to_order') {
    const badge = document.createElement('span');
    badge.className = 'reference-badge availability-made-to-order';
    badge.dataset.availability = 'made_to_order';
    badge.textContent = 'MADE TO ORDER · NO PREVIEW FOR THIS EXACT SIZE';
    button.append(badge);
  } else if (availability?.status === 'preview_available' && availability?.configuration_id) {
    const badge = document.createElement('span');
    badge.className = 'reference-badge availability-preview-available';
    badge.dataset.availability = 'preview_available';
    badge.textContent = 'RECEIPT-BACKED PREVIEW FOR THIS EXACT SIZE';
    button.append(badge);
  }
  button.append(heading, copy);
  button.addEventListener('click', onSelect);
  return button;
}

async function configureManualDesign() {
  if (!state.headStyleId || !state.bandStyleId) return;
  const button = document.getElementById('step6Next');
  setBusy(button, true, 'Preparing review');
  set('manualConfigureError', '');
  try {
    const report = await postJson('/api/manual-configure', {
      ...manualTypedPayload(),
      head_style_id: state.headStyleId,
      band_style_id: state.bandStyleId,
    });
    if (report.status !== 'accepted') {
      throw new Error('The canonical live gate did not accept this selection.');
    }
    state.manualReport = report;
    state.result = report;
    state.needsAnalysis = false;
    state.preview = { ...state.preview, ...(report.preview || {}) };
    state.previewManifest = previewManifestFrom(report);
    state.previewApproval = null;
    await enterWorkspace();
  } catch (error) {
    set('manualConfigureError', error.message);
  } finally {
    setBusy(button, false, 'Review completed ring →');
  }
}

function selectedHead() {
  return state.catalogOptions?.head_styles?.find(head => head.head_style_id === state.headStyleId);
}

function selectedBand() {
  return (state.catalogOptions?.band_styles_by_head?.[state.headStyleId] || [])
    .find(band => band.band_style_id === state.bandStyleId);
}

function ingestPhoto(file) {
  if (!file) return;
  if (!file.type.startsWith('image/')) {
    set('uploadText', 'Please choose a PNG, JPG, or other image file.');
    return;
  }
  if (file.size > 10 * 1024 * 1024) {
    set('uploadText', 'Image must be 10 MB or smaller.');
    return;
  }

  state.file = file;
  state.result = null;
  state.assets = null;
  state.previewManifest = null;
  state.previewApproval = null;
  state.needsAnalysis = true;
  state.stoneConfirmed = false;
  state.mountingEdited = false;
  const reader = new FileReader();
  reader.onload = () => {
    state.imageDataUrl = reader.result;
    const preview = document.getElementById('photoPreview');
    if (preview) {
      preview.style.backgroundImage = `url("${state.imageDataUrl}")`;
      preview.classList.add('has-image');
    }
    set('uploadText', file.name);
    const next = document.getElementById('stepUploadNext');
    if (next) next.disabled = false;
    openStoneDialog();
  };
  reader.onerror = () => set('uploadText', 'The selected image could not be read.');
  reader.readAsDataURL(file);
}

function bindStoneDialog() {
  const dialog = document.getElementById('stoneDetailsDialog');
  const form = document.getElementById('stoneDetailsForm');
  const close = () => {
    set('stoneDialogError', '');
    dialog?.close();
  };
  document.getElementById('stoneDialogClose')?.addEventListener('click', close);
  document.getElementById('stoneDialogCancel')?.addEventListener('click', close);
  form?.addEventListener('submit', async event => {
    event.preventDefault();
    const error = validateAndApplyStoneDialog();
    if (error) {
      set('stoneDialogError', error);
      return;
    }

    const btn = document.getElementById('stoneDialogConfirm');
    set('stoneDialogError', '');
    setBusy(btn, true, perceptionDisclosure().reads_your_photo
      ? 'Analyzing mounting' : 'Building your ring');
    try {
      await analyzeDesign();
      dialog?.close();
      await enterWorkspace();
    } catch (err) {
      set('stoneDialogError', err.message || 'Extraction failed.');
    } finally {
      setBusy(btn, false, perceptionDisclosure().reads_your_photo
        ? 'Confirm & Analyze →' : 'Confirm & Build →');
    }
  });
}

function openStoneDialog() {
  if (!state.file || !state.imageDataUrl) return;
  const dialog = document.getElementById('stoneDetailsDialog');
  if (!dialog) return;
  syncStoneDialog();
  set('stoneDialogError', '');
  if (!dialog.open) dialog.showModal();
}

function syncStoneDialog() {
  const dims = state.preview.stone_dimensions_mm || baseDims(state.preview.stone_shape);
  setValue('confirmStoneShape', state.preview.stone_shape || 'round');
  setValue('confirmRingSize', state.preview.ring_size_us || 6.5);
  setValue('confirmStoneLength', Number(dims.length).toFixed(2));
  setValue('confirmStoneWidth', Number(dims.width).toFixed(2));
  setValue('confirmStoneDepth', Number(dims.depth).toFixed(2));
  setValue('confirmStoneCarat', state.preview.carat ?? '');
}

function validateAndApplyStoneDialog() {
  const shape = document.getElementById('confirmStoneShape')?.value;
  const size = readBoundedInput('confirmRingSize', 3, 13, 'Ring size');
  const length = readBoundedInput('confirmStoneLength', 0.5, 20, 'Stone length');
  const width = readBoundedInput('confirmStoneWidth', 0.5, 20, 'Stone width');
  const depth = readBoundedInput('confirmStoneDepth', 0.2, 15, 'Stone depth');
  const caratInput = document.getElementById('confirmStoneCarat');
  const carat = readOptionalNum(caratInput);
  if (!shape) return 'Choose a stone shape.';
  for (const value of [size, length, width, depth]) {
    if (typeof value === 'string') return value;
  }
  if (carat !== null && (carat < 0.1 || carat > 10)) {
    return 'Carat must be between 0.1 and 10, or left blank.';
  }

  state.preview = {
    ...state.preview,
    stone_shape: shape,
    stone_dimensions_mm: { length, width, depth },
    ring_size_us: size,
    carat,
  };
  state.stoneConfirmed = true;
  state.mountingEdited = false;
  return '';
}

// ── Enter Workspace ────────────────────────────────────────
async function enterWorkspace() {
  set('manualStoneError', '');
  setStatus(WORKSPACE_ACCEPTED_STATUS, 'ok');

  // Hide wizard, show workspace
  const wizard = document.getElementById('wizard');
  const workspace = document.getElementById('workspace');
  if (wizard) wizard.style.display = 'none';
  if (workspace) workspace.style.display = 'grid';

  updateJourneyProgress(3);

  // Wait for layout to settle before creating the 3D scene
  // (canvas needs a measured size for the renderer)
  await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));

  // Sync workspace controls with state
  syncWorkspaceControls();
  bindWorkspaceEvents();
  updateWorkspaceUI();
  refreshGeometryDelivery();
  await initializeWorkspacePreview();
}

// Goal 0025. MyJewl is Geometry-as-a-Service, so the workspace has to answer
// "can I have the geometry?" — including, and especially, when the answer is no.
// The server decides; this only reports the decision and never constructs a
// download URL of its own.
async function refreshGeometryDelivery() {
  const jobId = persistedJobId();
  if (!jobId) {
    state.geometryDelivery = null;
    updateGeometryDeliveryUI();
    return;
  }
  try {
    state.geometryDelivery = await postJson('/api/deliver-geometry', { job_id: jobId });
  } catch (error) {
    state.geometryDelivery = {
      deliverable: false,
      reason: 'delivery_check_failed',
      detail: error.message,
      warnings: [],
      findings: [],
    };
  }
  updateGeometryDeliveryUI();
}

function updateGeometryDeliveryUI() {
  const panel = document.getElementById('wsDownloads');
  if (!panel) return;
  const decision = state.geometryDelivery;
  panel.replaceChildren();
  if (!decision) return;

  const heading = document.createElement('h4');
  heading.textContent = 'Manufacturing geometry';
  panel.append(heading);

  if (decision.deliverable) {
    const link = document.createElement('a');
    // The server owns the URL. A hand-built path here could point at the
    // procedural draft, which is exactly the file that must never be delivered.
    link.href = decision.archive_url;
    link.setAttribute('download', decision.archive_name || '');
    const mb = (Number(decision.byte_size || 0) / 1048576).toFixed(1);
    link.textContent = `Download ${decision.file_count} receipt-verified STL files (${mb} MB)`;
    panel.append(link);
  } else {
    const state_ = document.createElement('p');
    state_.className = 'ws-delivery-refused';
    state_.textContent = 'Not available for download';
    panel.append(state_);
  }

  const why = document.createElement('p');
  why.className = 'ws-note';
  why.textContent = decision.detail || '';
  panel.append(why);

  // Findings and warnings travel with the answer either way: a shop that
  // accepts this geometry is entitled to know what it accepted, and a customer
  // refused it is entitled to know precisely what is wrong.
  const notes = [
    ...(decision.findings || []).map(finding => finding.summary),
    ...(decision.warnings || []),
  ];
  if (notes.length) {
    const list = document.createElement('ul');
    list.className = 'ws-delivery-notes';
    for (const note of notes) {
      const item = document.createElement('li');
      item.textContent = note;
      list.append(item);
    }
    panel.append(list);
  }

  const scope = document.createElement('p');
  scope.className = 'ws-note';
  scope.textContent = decision.scope_note
    || 'Geometry only. Not a manufacturing certification.';
  panel.append(scope);
}

async function initializeWorkspacePreview({ retry = false } = {}) {
  const workspaceCanvas = document.getElementById('wsCanvas');
  if (!workspaceCanvas) return;
  bindPreviewDeliveryStatus(workspaceCanvas);
  if (retry && workspaceScene?.getDiagnostics?.().rendererPath !== 'none') {
    await workspaceScene.retry();
    return;
  }
  workspaceScene?.dispose();
  workspaceScene = null;
  state.delivery = {
    status: state.previewManifest?.preview_status === 'unavailable' ? 'unavailable' : 'loading',
    displayed_status: 'unavailable',
    asset_status: state.previewManifest?.preview_status === 'unavailable' ? 'not_applicable' : 'loading',
    renderer: state.previewManifest?.selected_asset?.renderer || 'none',
    reason_code: state.previewManifest?.reason_code || 'preview_manifest_missing',
    retryable: false,
    asset_id: state.previewManifest?.selected_asset?.asset_id || null,
    asset_version: state.previewManifest?.selected_asset?.asset_version || null,
    content_sha256: state.previewManifest?.selected_asset?.content?.sha256 || null,
  };
  updatePreviewDeliveryUI();
  try {
    workspaceScene = await initRing3D(workspaceCanvas, {
      purpose: 'production_preview',
      hero: false,
      bloom: true,
      spin: state.spin,
      forceFallback: studioParams.get('fallback') === '1',
      initialPreview: state.preview,
      previewManifest: state.previewManifest,
    });
  } catch (error) {
    console.warn('Production preview initialization failed:', error);
    state.delivery = {
      ...state.delivery,
      status: state.previewManifest?.selected_asset ? 'failure' : 'unavailable',
      displayed_status: 'unavailable',
      asset_status: 'error',
      reason_code: 'renderer_initialization_failed',
      retryable: true,
    };
    updatePreviewDeliveryUI();
  }
}

function syncWorkspaceControls() {
  const dims = state.preview.stone_dimensions_mm || baseDims(state.preview.stone_shape);
  setValue('wsCaratInput', state.preview.carat == null ? 'Not provided' : Number(state.preview.carat).toFixed(2));
  setValue('wsSizeInput', Number(state.preview.ring_size_us || 6.5).toFixed(2));
  setValue('wsShapeInput', title(state.preview.stone_shape));
  setValue('wsHeadStyle', selectedHead()?.display_name || title(state.preview.center_setting));
  setValue('wsArchitecture', title(state.preview.architecture || 'solitaire'));
  setValue('wsGallery', title(state.preview.gallery_structure || 'open_rail'));
  setValue('wsBandStyle', selectedBand()?.display_name || `${Number(state.preview.band_width_mm || 2).toFixed(1)} mm band`);
  setValue('wsShoulders', title(state.preview.shoulder_style || 'straight'));
  setValue('wsPave', paveLabel(state.preview));
  setValue('wsSideStones', sideStoneLabel(state.preview));
  setValue('wsStoneLengthInput', Number(dims.length).toFixed(2));
  setValue('wsStoneWidthInput', Number(dims.width).toFixed(2));
  setValue('wsStoneDepthInput', Number(dims.depth).toFixed(2));
}

function bindWorkspaceEvents() {
  if (state.workspaceBound) return;
  state.workspaceBound = true;
  document.getElementById('wsResetView')?.addEventListener('click', () => workspaceScene?.reset());
  document.getElementById('wsSpinToggle')?.addEventListener('click', () => {
    state.spin = !state.spin;
    if (workspaceScene) workspaceScene.spin = state.spin;
    const btn = document.getElementById('wsSpinToggle');
    if (btn) {
      btn.textContent = 'Auto-rotate';
      btn.classList.toggle('active', state.spin);
      btn.setAttribute('aria-pressed', String(state.spin));
    }
  });

  document.getElementById('wsRetryPreview')?.addEventListener('click', async () => {
    const button = document.getElementById('wsRetryPreview');
    setBusy(button, true, 'Retrying preview');
    try {
      await initializeWorkspacePreview({ retry: true });
    } finally {
      setBusy(button, false, 'Retry Preview');
      updatePreviewDeliveryUI();
    }
  });
  document.getElementById('wsPreviewDecision')?.addEventListener('click', recordPreviewDecision);
  document.getElementById('wsRequestGeometry')?.addEventListener('click', requestManualGeometry);
  document.getElementById('wsEditChoices')?.addEventListener('click', () => {
    workspaceScene?.dispose();
    workspaceScene = null;
    document.getElementById('workspace').style.display = 'none';
    document.getElementById('wizard').style.display = '';
    showStep(5);
  });
}

// Goal 0023. The server decides whether a volume exists for this exact
// configuration; the UI only reports what it decided. It never computes a weight,
// never rounds a pending quote up into a number, and always shows which volume the
// weight came from — the measured union, with the over-counting parts sum named as
// what it is not. Metal content only, and never a manufacturing claim.
function updateQuoteUI() {
  const quote = (state.manualReport || state.result || {}).quote;
  const weight = document.getElementById('wsSpecWeight');
  const basis = document.getElementById('wsQuoteBasis');
  if (!weight || !basis) return;

  if (!quote || quote.status !== 'indicative' || quote.metal_weight_g === null) {
    weight.textContent = 'Pending geometry';
    const why = quote?.reason
      || 'Metal weight needs a receipt-backed ring volume. Nothing is priced from an unmeasured configuration.';
    // Goal 0024: the price state is reported even with no weight yet, so the
    // shipped "no sheet declared" state is visible on every quote, not only the
    // four that already have a measured volume.
    basis.textContent = `${why} ${quote ? priceNote(quote) : ''}`.trim();
    return;
  }

  const cost = quote.metal_content_cost;
  const money = formatCost(quote);
  weight.textContent = `${Number(quote.metal_weight_g).toFixed(3)} g`
    + (cost === null || cost === undefined ? '' : ` · ${money} metal content`);

  const ev = quote.volume_evidence;
  if (!ev) {
    basis.textContent = `Indicative metal content from a ${quote.volume_basis} volume. `
      + `Excludes ${(quote.excludes || []).join(', ')}. ${priceNote(quote)}`;
    return;
  }
  const over = (Number(ev.parts_sum_overcount_fraction) * 100).toFixed(2);
  basis.textContent =
    `Indicative metal content only. Basis: ${ev.union_mm3} mm³ measured union of `
    + `${ev.metal_part_count} metal parts (${ev.union_engine}), from the receipt-backed `
    + `export for ${ev.asset_id}, measured ${ev.measured_on}. `
    + `Not the basis: the naive parts sum of ${ev.parts_sum_mm3} mm³, which over-counts `
    + `overlapping metal by ${over}%. Excludes ${(quote.excludes || []).join(', ')}. `
    + `${priceNote(quote)} `
    + `Measurement only — not a manufacturing certification.`;
}

// The product deliverable: how much metal this ring takes, in every alloy MyJewl
// can build. The server computes all seven from the one measured volume; the UI
// only renders them and never derives a weight of its own.
function updateAlloyWeightsUI() {
  const quote = (state.manualReport || state.result || {}).quote;
  const panel = document.getElementById('wsAlloyWeights');
  const body = document.getElementById('wsAlloyWeightsBody');
  if (!panel || !body) return;

  const rows = quote && quote.metal_weight_by_alloy;
  if (!rows || !rows.length) {
    panel.hidden = true;
    body.replaceChildren();
    return;
  }
  panel.hidden = false;
  body.replaceChildren(...rows.map((row) => {
    const tr = document.createElement('tr');
    if (row.selected) tr.className = 'selected';
    // Columns mirror Matrix's METAL WEIGHTS panel — Metal / Grams / DWT / SPG —
    // because a bench reads this table too, and it quotes in pennyweight.
    const name = document.createElement('th');
    name.scope = 'row';
    name.textContent = row.display_name + (row.selected ? ' · selected' : '');
    const grams = document.createElement('td');
    grams.textContent = Number(row.metal_weight_g).toFixed(2);
    const dwt = document.createElement('td');
    dwt.textContent = Number(row.metal_weight_dwt).toFixed(2);
    const spg = document.createElement('td');
    spg.textContent = Number(row.spg).toFixed(2);
    tr.append(name, grams, dwt, spg);
    return tr;
  }));
}

// Goal 0024. A cost is only ever shown with its currency; a bare number invites a
// reader to supply their own. The server owns the currency, so the UI never
// assumes one.
function formatCost(quote) {
  const cost = quote.metal_content_cost;
  if (cost === null || cost === undefined) return '';
  return quote.currency ? `${quote.currency} ${cost}` : `${cost} (currency not stated)`;
}

// Goal 0024. Where the price came from, or — the state this repository ships in —
// exactly which condition stopped it being priced. Never silently blank.
function priceNote(quote) {
  const ref = quote.price_refusal;
  if (ref) {
    return `Metal content is unpriced: ${ref.detail} No last-known price is reused `
      + `and no other alloy's price is substituted.`;
  }
  const pe = quote.price_evidence;
  if (!pe) return '';
  if (!pe.sheet_id) {
    return `Priced at a caller-supplied spot rate of ${pe.price_per_gram}/g — no citation, `
      + `no as-of date, no currency, no expiry. Metal content only.`;
  }
  return `Priced from alloy price sheet ${pe.sheet_id}@${pe.sheet_version} at `
    + `${pe.currency} ${pe.price_per_gram}/g (${pe.price_basis}), as of ${pe.as_of}, `
    + `source ${pe.source}, expires ${pe.expires_on}. This is the metal content only — `
    + `not the price of a finished ring.`;
}

function updateWorkspaceUI() {
  const p = state.preview;
  const setting = p.center_setting || 'prong';
  const dims = p.stone_dimensions_mm || { length: 6.5, width: 6.5 };

  set('wsSpecShape', title(p.stone_shape));
  set('wsSpecArchitecture', title(p.architecture || 'solitaire'));
  set('wsSpecSetting', title(setting));
  set('wsSpecProngs', String(p.prong_count || 0));
  set('wsSpecMetal', state.metalPurity === 'pt950'
    ? 'Platinum 950'
    : `${state.metalPurity} ${metalLabel(p.metal_family)}`);
  set('wsSpecFinish', title(state.metalFinish));
  set('wsSpecGallery', title(p.gallery_structure || 'open_rail'));
  set('wsSpecShoulders', title(p.shoulder_style || 'straight'));
  set('wsSpecPave', paveLabel(p));
  set('wsSpecSideStones', sideStoneLabel(p));
  set('wsSpecStone', `${Number(dims.length).toFixed(2)} × ${Number(dims.width).toFixed(2)} mm`);
  set('wsViewShape', `${title(p.stone_shape)} ${title(setting)}`);
  set('wsViewSpecs', `${Number(p.carat || 1).toFixed(2)} ct | ${Number(p.band_width_mm || 2).toFixed(2)} mm band`);

  set('wsConfidenceVal', state.manualReport ? 'BRIEF READY' : 'REVIEW');
  updateQuoteUI();
  updateAlloyWeightsUI();
  updateMountingSourceUI();
  updatePreviewDeliveryUI();

  // Progress
  const analyzed = Boolean(state.manualReport) || (state.result && !state.needsAnalysis);
  const exported = Boolean(state.assets);
  toggleClass('wsExtractStep', 'done', analyzed);
  toggleClass('wsExtractStep', 'active', !analyzed);
  toggleClass('wsOutputStep', 'done', exported);
  toggleClass('wsOutputStep', 'active', analyzed && !exported);
}

async function requestManualGeometry() {
  const jobId = persistedJobId();
  if (!jobId) {
    setStatus('This design has not been persisted to the review queue.', 'error');
    return;
  }
  const button = document.getElementById('wsRequestGeometry');
  setBusy(button, true, 'Requesting review');
  try {
    const request = await postJson('/api/request-geometry', { job_id: jobId });
    state.assets = request;
    setStatus(`Geometry request ${request.status}. No manufacturing result is implied.`, 'ok');
    button.textContent = `Geometry ${request.status}`;
  } catch (error) {
    setStatus(error.message, 'error');
    setBusy(button, false, 'Request Geometry Review');
  }
  updateWorkspaceUI();
}

function updateViewportBadge() {
  const p = state.preview;
  set('viewportShape', `${title(p.stone_shape)} ${title(p.architecture || 'solitaire')}`);
  set('viewportMetal', metalLabel(p.metal_family));
  set('wizardPreviewTitle', 'Operational design aid');
  set('wizardPreviewAuthority', OPERATIONAL_AID_LABEL);
}

function previewManifestFrom(report) {
  const manifest = report?.preview_manifest || report?.preview?.preview_manifest;
  if (manifest && typeof manifest === 'object') return manifest;
  return {
    schema: 'myjewl.preview_manifest.v0_1',
    manifest_id: null,
    preview_status: 'unavailable',
    reason_code: 'legacy_preview_manifest_missing',
    representation_kind: 'none',
    selected_asset: null,
    geometry: { signature: '', projection: {} },
    warnings: ['This legacy result has no server-authoritative PreviewManifest.'],
    manufacturing_authority: false,
    manufacturing_state: 'pending_not_run_needs_calibration',
  };
}

function bindPreviewDeliveryStatus(canvas) {
  if (!canvas || canvas.dataset.previewDeliveryBound === 'true') return;
  canvas.dataset.previewDeliveryBound = 'true';
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
    updatePreviewDeliveryUI();
  });
}

function previewReasonLabel(code) {
  const labels = {
    approved_exact_asset: 'The server resolved the registered full-ring V4 asset.',
    approved_estimated_asset: 'The server resolved an approved bounded estimate.',
    exact_registry_match: 'The server resolved the registered full-ring V4 asset.',
    no_approved_preview_asset: 'No approved production preview represents this completed configuration.',
    no_registry_match: 'No registered production preview represents this completed configuration.',
    invalid_canonical_spec: 'The completed configuration could not be normalized for preview resolution.',
    registry_invalid: 'The production preview registry did not pass validation.',
    unsupported_geometry_parameters: 'Required style identifiers are unavailable for preview resolution.',
    ambiguous_preview_asset: 'Preview resolution was ambiguous and was rejected.',
    legacy_preview_manifest_missing: 'This legacy result has no server-authoritative preview resolution.',
    asset_loading: 'The registered asset is being verified before display.',
    asset_unavailable: 'The registered asset could not be downloaded.',
    asset_timeout: 'The registered asset request timed out.',
    asset_hash_mismatch: 'The registered asset failed integrity verification.',
    asset_size_mismatch: 'The registered asset size did not match the registry.',
    delivery_manifest_malformed: 'The delivery manifest was malformed.',
    delivery_manifest_mismatch: 'The delivery manifest did not match the registry.',
    binary_contract_mismatch: 'The registered mesh failed its binary contract checks.',
    webgl_unavailable: 'WebGL is unavailable in this browser.',
    webgl_initialization_failed: 'The production renderer could not initialize.',
    threejs_unavailable: 'The local production renderer could not be loaded.',
    forced_renderer_unavailable: 'Production rendering was disabled for this verification session.',
    webgl_render_failed: 'The production renderer failed before a product frame was displayed.',
    webgl_context_lost: 'The production rendering context was lost.',
  };
  return labels[code] || String(code || 'preview_unavailable').replaceAll('_', ' ');
}

function updatePreviewDeliveryUI() {
  const manifest = state.previewManifest;
  const delivery = state.delivery;
  const resolved = manifest?.preview_status || delivery.status || 'unavailable';
  const ready = delivery.asset_status === 'ready'
    && ['exact', 'estimated'].includes(delivery.displayed_status);
  const overlay = document.getElementById('workspacePreviewState');
  const canvas = document.getElementById('wsCanvas');
  const retry = document.getElementById('wsRetryPreview');
  const decision = document.getElementById('wsPreviewDecision');
  overlay?.classList.remove('is-exact', 'is-estimated', 'is-unavailable');

  if (ready) {
    if (overlay) overlay.hidden = true;
    overlay?.classList.add(delivery.displayed_status === 'estimated' ? 'is-estimated' : 'is-exact');
    const statusLabel = delivery.displayed_status === 'exact' ? 'Exact production preview' : 'Estimated production preview';
    set('workspacePreviewTitle', statusLabel);
    set('workspacePreviewGuidanceTitle', statusLabel);
    set('workspacePreviewAuthority', delivery.displayed_status === 'exact'
      ? 'Verified registered full-ring asset · Manufacturing pending'
      : 'Verified approved estimate · Review warnings · Manufacturing pending');
    canvas?.setAttribute('aria-label', `${statusLabel}. Drag or use the arrow keys to rotate.`);
    canvas?.setAttribute('tabindex', '0');
    setStatus(`${statusLabel} displayed after integrity verification. Manufacturing review remains pending.`, 'ok');
  } else {
    if (overlay) overlay.hidden = false;
    overlay?.classList.add('is-unavailable');
    const loading = delivery.asset_status === 'loading';
    set('workspacePreviewStateTitle', loading ? `${title(resolved)} preview loading…` : 'Production preview unavailable');
    // Goal 0016: when the master can build this configuration but nobody has
    // solved it, say so instead of leaving the customer with a bare reason code.
    const madeToOrder = state.result?.fulfillment?.availability === 'made_to_order'
      ? state.result.fulfillment.detail
      : null;
    set('workspacePreviewStateDetail', loading
      ? 'The viewport stays empty until the registered asset passes integrity checks.'
      : (madeToOrder || previewReasonLabel(delivery.reason_code || manifest?.reason_code)));
    set('workspacePreviewTitle', loading ? 'Verifying production preview' : 'Preview unavailable');
    set('workspacePreviewGuidanceTitle', loading ? 'Production preview loading' : 'No production preview');
    set('workspacePreviewAuthority', loading
      ? 'Server-resolved asset · verification in progress'
      : 'No procedural or 2D substitute is shown');
    canvas?.setAttribute('aria-label', loading ? 'Production preview loading.' : 'Production preview unavailable.');
    canvas?.setAttribute('tabindex', '-1');
    if (!loading) {
      setStatus(`Design brief validated; production preview unavailable (${previewReasonLabel(delivery.reason_code || manifest?.reason_code)}).`, 'ok');
    }
  }

  if (retry) retry.hidden = !(delivery.retryable && !ready);
  if (decision) {
    const canRecord = Boolean(persistedJobId() && manifest?.manifest_id && !state.previewApproval);
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
}

function persistedJobId() {
  return state.manualReport?.run?.job_id || state.result?.run?.job_id || null;
}

async function recordPreviewDecision() {
  const button = document.getElementById('wsPreviewDecision');
  const decision = button?.dataset.decision;
  const jobId = persistedJobId();
  if (!button || !jobId || !state.previewManifest?.manifest_id || !decision) return;
  setBusy(button, true, 'Recording decision');
  try {
    const response = await postJson('/api/preview-approval', {
      job_id: jobId,
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
    setStatus(
      decision === 'approved'
        ? 'Preview approval recorded append-only. This is not manufacturing approval.'
        : 'Decision to continue without a preview recorded append-only. Manufacturing review remains separate.',
      'ok',
    );
  } catch (error) {
    setStatus(error.message, 'error');
    setBusy(button, false, decision === 'approved' ? 'Approve preview' : 'Continue without preview');
  }
}

// ── Perception lane (Goal 0027) ────────────────────────────
// MyJewl's perception lane is paused (Goal 0026), so on the shipped deployment a
// customer's photograph is not read: the mounting comes from `_heuristic_candidate`,
// a prior over the stone they typed. The API has said so since Goal 0026; this is
// what makes the page say it too.
//
// The copy is driven by the gate rather than hard-coded, because the lane can be
// switched on by an operator acknowledgement -- a hard-coded "we never read your
// photo" would become the next false claim the day someone declares one.
async function refreshPerceptionState() {
  try {
    const res = await fetch('/api/health', { headers: { accept: 'application/json' } });
    if (!res.ok) throw new Error(`health ${res.status}`);
    const body = await res.json();
    state.perception = body.perception || null;
  } catch (err) {
    // Fail closed, and closed here means the REFUSING text. The shipped state is
    // that photos are not read; claiming we looked at a picture we ignored is the
    // harm this exists to stop, and this direction understates rather than
    // overstates what MyJewl did.
    console.warn('perception state unavailable; assuming the lane is refusing:', err);
    state.perception = null;
  }
  applyPerceptionCopy();
}

function perceptionDisclosure() {
  const declared = state.perception?.customer_disclosure;
  if (declared && typeof declared.reads_your_photo === 'boolean') return declared;
  return {
    reads_your_photo: false,
    headline: 'Your photo is not being read',
    statement:
      'MyJewl is not reading photographs right now. Your picture is not used to ' +
      'choose the setting, the prongs or the band, and it is not kept with your ' +
      'design. Everything you see is built from the stone size, ring size and ' +
      'metal you typed.',
  };
}

function applyPerceptionCopy() {
  const disclosure = perceptionDisclosure();
  const reads = disclosure.reads_your_photo === true;

  set('pathUploadCopy', reads
    ? 'Have a photo of a ring you like? Upload it and MyJewl will suggest a mounting to match. The suggestion is diagnostic and unvalidated — check it.'
    : 'Start from a photo you like. MyJewl is not reading photographs right now, so your picture is not used to choose the mounting — the ring is built from details you type next.');
  set('pathUploadBadge', reads ? 'Photo read · unvalidated' : 'Photo not read');
  toggleClass('pathUploadBadge', 'is-refused', !reads);

  set('uploadLaneHeadline', disclosure.headline);
  set('uploadLaneStatement', disclosure.statement);
  // The operator-facing reason, shown to the customer as a plain aside rather
  // than hidden: "why not" is a fair question and the answer is not embarrassing.
  const status = state.perception?.status;
  set('uploadLaneWhy', reads || !status ? '' : `Reason: ${status.replace(/_/g, ' ')}.`);
  const notice = document.getElementById('uploadLaneNotice');
  if (notice) notice.dataset.readsPhoto = reads ? 'true' : 'false';

  set('stoneDialogLaneCopy', reads
    ? 'Stone geometry and ring size come from these confirmed values, never from the photo. Only the mounting is suggested from your picture.'
    : 'Stone geometry and ring size come from these confirmed values. Your photo is not read — the mounting comes from what you type here too.');
  set('stoneDialogConfirm', reads ? 'Confirm & Analyze →' : 'Confirm & Build →');
}

// Where THIS request's mounting actually came from, as the server named it.
// Rendered on every path, not just the upload one: "you chose this" is as much
// the answer as "we did not read your photo", and a panel that only appears when
// something is wrong teaches customers to ignore it.
function updateMountingSourceUI() {
  const source = state.result?.extraction?.mounting_source;
  const box = document.getElementById('wsMountingSource');
  if (!box) return;
  if (!source) {
    box.hidden = true;
    return;
  }
  box.hidden = false;
  box.dataset.readsPhoto = source.read_your_photo === true ? 'true' : 'false';
  set('wsMountingSourceHeadline', source.headline || '');
  set('wsMountingSourceStatement', source.statement || '');
}

// ── API Calls ──────────────────────────────────────────────
async function analyzeDesign() {
  const dims = state.preview.stone_dimensions_mm || {};
  const payload = {
    image_data_url: state.imageDataUrl,
    filename: state.file?.name || syntheticFilename(),
    stone_shape: state.preview.stone_shape,
    stone_length_mm: dims.length,
    stone_width_mm: dims.width,
    stone_depth_mm: dims.depth,
    stone_carat_eq: state.preview.carat,
    ring_size_us: state.preview.ring_size_us || 6.5,
    // Metal is a typed input on every path: the customer picked it, so it is sent
    // as typed and never read off the photo.
    metal_color_family: state.preview.metal_family,
    metal_karat: state.metalPurity,
    metal_finish: state.metalFinish,
  };
  if (state.path !== 'upload' || state.mountingEdited) {
    payload.mounting_overrides = {
      center_setting: state.preview.center_setting,
      prong_count: state.preview.prong_count,
      band_width_mm: state.preview.band_width_mm || 2,
      band_thickness_mm: state.preview.band_thickness_mm || 1.6,
    };
  }
  const result = await postJson('/api/extract', payload);

  state.result = result;
  if (result.preview) {
    state.preview = {
      ...state.preview,
      ...result.preview,
      center_setting: result.extraction?.components?.center_setting?.value || state.preview.center_setting,
      // The customer's typed metal always wins over anything the response echoes.
      metal_family: state.preview.metal_family,
      metal_karat: state.preview.metal_karat || '14k',
    };
  }
  state.previewManifest = previewManifestFrom(result);
  state.previewApproval = null;
  state.needsAnalysis = false;
  state.stoneConfirmed = true;
  return result;
}

// ── Helpers ────────────────────────────────────────────────
async function postJson(url, payload) {
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(payload),
  });
  const body = await res.json();
  if (!res.ok) throw new Error(body.error || `Request failed: ${res.status}`);
  return body;
}

function setStatus(msg, tone) {
  const el = document.getElementById('wsStatus');
  if (!el) return;
  el.textContent = msg || 'Design brief complete. Ready to generate.';
  el.classList.toggle('is-error', tone === 'error');
  el.classList.toggle('is-ok', tone === 'ok');
}

function setBusy(btn, busy, label) {
  if (!btn) return;
  btn.disabled = busy;
  btn.textContent = busy ? `${label}...` : label;
}

function set(id, text) {
  const el = document.getElementById(id);
  if (el) el.textContent = text;
}

function setValue(id, value) {
  const el = document.getElementById(id);
  if (el) el.value = value;
}

function toggleClass(id, cls, on) {
  const el = document.getElementById(id);
  if (el) el.classList.toggle(cls, on);
}

function readNum(el, fallback) {
  if (!el) return fallback;
  const v = Number(el.value);
  return Number.isFinite(v) ? v : fallback;
}

function readOptionalNum(el) {
  if (!el || el.value.trim() === '') return null;
  const value = Number(el.value);
  return Number.isFinite(value) ? value : null;
}

function readBoundedInput(id, minimum, maximum, label) {
  const el = document.getElementById(id);
  const value = readOptionalNum(el);
  if (value === null) return `${label} is required.`;
  if (value < minimum || value > maximum) {
    return `${label} must be between ${minimum} and ${maximum}.`;
  }
  return value;
}

function title(v) {
  return String(v || '').replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
}

function metalLabel(family) {
  if (family === 'white') return 'White Gold';
  if (family === 'rose') return 'Rose Gold';
  return 'Yellow Gold';
}

function paveLabel(preview) {
  const pave = preview.pave || {};
  if (!pave.coverage || pave.coverage === 'none') return 'None';
  const count = (preview.accent_stones || [])[0]?.quantity;
  return `${title(pave.coverage)} ${title(pave.setting_style)}${count ? ` · ${count} round accents` : ''}`;
}

function sideStoneLabel(preview) {
  const schedule = (preview.side_stones || [])[0];
  if (!schedule) return 'None';
  return `${schedule.quantity} × ${title(schedule.shape)} · ${title(schedule.placement?.orientation)}`;
}

function syntheticFilename() {
  return `${state.preview.metal_family}-${state.preview.stone_shape}-scratch-ring`;
}

function inferProngCount(shape, carat, setting) {
  if (setting !== 'prong') return 0;
  if (shape === 'pear') return 5;
  if (shape === 'round' && carat >= 1.35) return 6;
  return 4;
}

function syncManualStoneInputs() {
  const dims = state.preview.stone_dimensions_mm || baseDims(state.preview.stone_shape);
  setValue('stoneLengthInput', Number(dims.length).toFixed(2));
  setValue('stoneWidthInput', Number(dims.width).toFixed(2));
  setValue('stoneDepthInput', Number(dims.depth).toFixed(2));
}

function baseDims(shape) {
  const table = {
    round:    { length: 6.5, width: 6.5, depth: 3.9 },
    oval:     { length: 8.0, width: 6.0, depth: 3.8 },
    cushion:  { length: 6.5, width: 6.5, depth: 4.2 },
    pear:     { length: 8.5, width: 5.5, depth: 3.7 },
    emerald:  { length: 7.0, width: 5.0, depth: 3.5 },
    radiant:  { length: 6.5, width: 5.5, depth: 3.8 },
    princess: { length: 5.5, width: 5.5, depth: 4.0 },
  };
  return table[shape] || table.round;
}

// ── Ambient Particles ──────────────────────────────────────
function initParticles() {
  const canvas = document.getElementById('particleBg');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  if (!ctx) return;

  const particles = [];
  const count = 50;

  function resize() {
    canvas.width = window.innerWidth;
    canvas.height = window.innerHeight;
  }
  resize();
  window.addEventListener('resize', resize);

  class Particle {
    constructor() { this.reset(true); }
    reset(initial) {
      this.x = Math.random() * canvas.width;
      this.y = initial ? Math.random() * canvas.height : canvas.height + 10;
      this.size = Math.random() * 1.5 + 0.3;
      this.speedY = -(Math.random() * 0.2 + 0.05);
      this.speedX = (Math.random() - 0.5) * 0.15;
      this.opacity = Math.random() * 0.35 + 0.05;
      this.hue = 38 + Math.random() * 14; // champagne gold range
      this.life = 0;
      this.maxLife = 400 + Math.random() * 500;
    }
    update() {
      this.y += this.speedY;
      this.x += this.speedX + Math.sin(this.life * 0.008) * 0.1;
      this.life++;
      const fadeIn = Math.min(this.life / 80, 1);
      const fadeOut = Math.max(1 - (this.life - this.maxLife + 80) / 80, 0);
      this.currentOpacity = this.opacity * fadeIn * (this.life > this.maxLife - 80 ? fadeOut : 1);
      if (this.life > this.maxLife || this.y < -20) this.reset(false);
    }
    draw() {
      ctx.beginPath();
      ctx.arc(this.x, this.y, this.size, 0, Math.PI * 2);
      ctx.fillStyle = `hsla(${this.hue}, 75%, 65%, ${this.currentOpacity})`;
      ctx.fill();
    }
  }

  for (let i = 0; i < count; i++) particles.push(new Particle());

  // Returns a per-frame step; the shared animate() loop drives it (no separate RAF).
  return function drawParticlesFrame() {
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    particles.forEach(p => { p.update(); p.draw(); });
  };
}
