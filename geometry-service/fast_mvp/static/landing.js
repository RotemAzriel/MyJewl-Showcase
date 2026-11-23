/**
 * landing.js — MyJewl Landing Page
 *
 * Features:
 *  - Ambient floating particle background
 *  - Hero 3D ring with a locally generated neutral studio environment
 *  - Scroll-driven storytelling with 3 phases:
 *    wireframe → transition → rendered ring
 *  - Progress bar synced with scroll
 */

import { initRing3D, defaultPreview, clamp } from '/static/ring3d.js';

// ── State ──────────────────────────────────────────────────
let heroScene = null;
let storyScene = null;
let currentPhase = -1;
let particleStep = null;

// ── Boot ───────────────────────────────────────────────────
boot();

async function boot() {
  particleStep = initParticles();
  initScrollObserver();
  // Goal 0027. The "Photo to Geometry" card used to promise that MyJewl's AI
  // matches the mounting from a photograph. The perception lane has been paused
  // since 2026-08-11 and reads nothing, so the promise was false on every
  // deployment this repository ships. The copy is now driven by the same gate
  // /api/extract obeys, rather than by a sentence someone wrote once.
  refreshPhotoLaneCopy();

  // Init hero 3D from locally served renderer assets.
  try {
    heroScene = await initRing3D(document.getElementById('heroCanvas'), {
      hero: true,
      bloom: true,
      spin: true,
      initialPreview: defaultPreview(),
    });
    // Animation loop starts here — covers both scenes
    requestAnimationFrame(animate);
  } catch (err) {
    console.warn('Hero 3D init failed:', err);
    // Start animation loop anyway for story scene
    requestAnimationFrame(animate);
  }

  // Init story 3D separately — don't block on hero
  try {
    storyScene = await initRing3D(document.getElementById('storyCanvas'), {
      hero: false,
      bloom: false,  // simpler rendering for story canvas
      spin: true,
      mode: 'wireframe',
      initialPreview: defaultPreview(),
    });
  } catch (err) {
    console.warn('Story 3D init failed:', err);
  }
}

// ── Animation Loop ─────────────────────────────────────────
function animate() {
  try { particleStep?.(); } catch (e) { /* ignore frame errors */ }
  try { heroScene?.render(); } catch (e) { /* ignore frame errors */ }
  try { storyScene?.render(); } catch (e) { /* ignore frame errors */ }
  requestAnimationFrame(animate);
}

// ── Scroll-driven Storytelling ─────────────────────────────
function initScrollObserver() {
  const story = document.querySelector('.story');
  if (!story) return;

  window.addEventListener('scroll', () => {
    const rect = story.getBoundingClientRect();
    const totalScroll = story.offsetHeight - window.innerHeight;
    if (totalScroll <= 0) return;

    const scrolled = -rect.top;
    const storyProgress = clamp(scrolled / totalScroll, 0, 1);

    // Update progress bar
    const fill = document.getElementById('progressFill');
    if (fill) fill.style.width = `${storyProgress * 100}%`;

    // Determine phase (3 equal zones)
    let phase = 0;
    if (storyProgress > 0.66) phase = 2;
    else if (storyProgress > 0.33) phase = 1;

    if (phase !== currentPhase) {
      currentPhase = phase;
      updatePhase(phase);
    }

    // Update progress labels
    const labels = document.querySelectorAll('.progress-labels span');
    labels.forEach((label, i) => {
      label.classList.toggle('active', i === phase);
    });

    // Update story scene mode based on phase
    if (storyScene) {
      if (phase === 0) {
        storyScene.rebuild(defaultPreview(), 'wireframe');
        storyScene.spin = true;
      } else if (phase === 1) {
        // Transition phase: keep wireframe but spin faster
        storyScene.spin = true;
        storyScene.targetY += 0.008;
      } else {
        storyScene.rebuild(defaultPreview(), 'rendered');
        storyScene.spin = true;
      }
    }
  }, { passive: true });
}

function updatePhase(phase) {
  const phases = document.querySelectorAll('.story-phase');
  phases.forEach((el) => {
    const p = parseInt(el.dataset.phase, 10);
    el.classList.toggle('active', p === phase);
  });
}

// ── Ambient Particle System ────────────────────────────────
function initParticles() {
  const canvas = document.getElementById('particleBg');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  if (!ctx) return;

  const particles = [];
  const count = 60;

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
      this.size = Math.random() * 2 + 0.5;
      this.speedY = -(Math.random() * 0.3 + 0.1);
      this.speedX = (Math.random() - 0.5) * 0.2;
      this.opacity = Math.random() * 0.4 + 0.1;
      this.hue = 38 + Math.random() * 14; // champagne gold range
      this.life = 0;
      this.maxLife = 300 + Math.random() * 400;
    }
    update() {
      this.y += this.speedY;
      this.x += this.speedX + Math.sin(this.life * 0.01) * 0.15;
      this.life++;
      const fadeIn = Math.min(this.life / 60, 1);
      const fadeOut = Math.max(1 - (this.life - this.maxLife + 60) / 60, 0);
      this.currentOpacity = this.opacity * fadeIn * (this.life > this.maxLife - 60 ? fadeOut : 1);
      if (this.life > this.maxLife || this.y < -20) this.reset(false);
    }
    draw() {
      ctx.beginPath();
      ctx.arc(this.x, this.y, this.size, 0, Math.PI * 2);
      ctx.fillStyle = `hsla(${this.hue}, 80%, 70%, ${this.currentOpacity})`;
      ctx.fill();
      // Glow halo
      ctx.beginPath();
      ctx.arc(this.x, this.y, this.size * 3, 0, Math.PI * 2);
      ctx.fillStyle = `hsla(${this.hue}, 80%, 70%, ${this.currentOpacity * 0.15})`;
      ctx.fill();
    }
  }

  for (let i = 0; i < count; i++) particles.push(new Particle());

  // Returns a per-frame step; the shared animate() loop drives it (no separate RAF).
  return function drawParticlesFrame() {
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    particles.forEach(p => { p.update(); p.draw(); });

    // Connecting lines between nearby particles
    for (let i = 0; i < particles.length; i++) {
      for (let j = i + 1; j < particles.length; j++) {
        const dx = particles[i].x - particles[j].x;
        const dy = particles[i].y - particles[j].y;
        const dist = Math.sqrt(dx * dx + dy * dy);
        if (dist < 120) {
          const alpha = (1 - dist / 120) * 0.06 * particles[i].currentOpacity * particles[j].currentOpacity;
          ctx.beginPath();
          ctx.moveTo(particles[i].x, particles[i].y);
          ctx.lineTo(particles[j].x, particles[j].y);
          ctx.strokeStyle = `rgba(201, 169, 110, ${alpha})`;
          ctx.lineWidth = 0.5;
          ctx.stroke();
        }
      }
    }

  };
}

// ── Perception lane copy (Goal 0027) ───────────────────────
// Fails closed to the refusing text: the markup already ships it, so a failed or
// slow fetch simply leaves the honest version in place. The only thing this can
// do is *upgrade* the claim, and only when the server says the lane answers.
async function refreshPhotoLaneCopy() {
  let disclosure = null;
  try {
    const res = await fetch('/api/health', { headers: { accept: 'application/json' } });
    if (res.ok) disclosure = (await res.json())?.perception?.customer_disclosure || null;
  } catch (err) {
    console.warn('perception state unavailable; leaving the paused copy in place:', err);
  }
  if (disclosure?.reads_your_photo !== true) return;

  const title = document.getElementById('photoLaneTitle');
  const copy = document.getElementById('photoLaneCopy');
  if (title) title.textContent = 'Photo to Geometry';
  if (copy) {
    copy.textContent =
      'Upload any ring photo and confirm the stone details, ring size, and metal ' +
      '— those are always typed, never read from pixels. MyJewl matches the ' +
      'mounting — setting, prongs, and band proportions — against our own ' +
      'validated catalog. The match is diagnostic and has not passed an accuracy ' +
      'gate.';
  }
}
