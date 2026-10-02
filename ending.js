/* ================================================================
   ending.js  —  OBS Browser Source Ending Scene
   Pure vanilla JS, no frameworks, no external dependencies.
   ================================================================ */

'use strict';

/* ────────────────────────────────────────────────────────────────
   CONFIGURATION
   ──────────────────────────────────────────────────────────────── */
const CFG = {
  API_ENDPOINT:        '/api/ending-state',
  MOCK_ENDPOINT:       'mock-ending-state.json',
  MOCK_SPARSE_ENDPOINT:'mock-ending-state-sparse.json',
  STOP_ENDPOINT:       '/api/obs/stop-stream',
  FALLBACK_DURATION:   180,   // seconds, used when audio cannot be loaded
  LOADING_MIN_MS:      480,   // минимум от старта страницы до скрытия лоадера (читаемость этапов)
  MIN_CREDITS_SPEED:   4,     // px/s minimum scroll speed
  CREDITS_TICKER_SPEED: 48,   // px/s for the bottom ticker
  CREDITS_MIN_DURATION: 34,   // seconds
  CREDITS_FILL_PADDING_PX: 160,
  MUSIC_BASE_TRIM:     0.38,  // global music trim so clip/dialogue stays readable
  CREDITS_ENTRY_OFFSET_PX: 80,
  OUTRO_DARKEN_FRAC:   0.30,  // fraction into outro when bg starts darkening
  OUTRO_MSG_FRAC:      0.38,  // fraction into outro when final message appears
  OUTRO_STOP_FRAC:     0.65,  // fraction into outro when stop-stream is sent (after_final_message)
  STATS_CYCLE_COUNT:   1,     // how many full rotations through all sections during main
};

const STATS_META = Object.freeze({
  visitors:       { label: 'След, который люди оставили сегодня', shortLabel: 'След' },
  chatters:       { label: 'Кто вёл эфир голосом', shortLabel: 'Чат' },
  firstChatters:  { label: 'Новые голоса сегодняшнего эфира', shortLabel: 'Новые' },
  follows:        { label: 'Кто решил остаться с каналом', shortLabel: 'Фоллоу' },
  streaks:        { label: 'Кто возвращался на эфиры', shortLabel: 'Верность' },
  subscriptions:  { label: 'Кто поддержал подпиской', shortLabel: 'Подписки' },
  bits:           { label: 'Кто поддержал bits', shortLabel: 'Bits' },
  raids:          { label: 'Кто привёл людей рейдом', shortLabel: 'Рейды' },
  redemptions:    { label: 'Кто запускал активности канала', shortLabel: 'Поинты' },
  donors:         { label: 'Кто поддержал эфир делом', shortLabel: 'Донаты' },
});

/* ────────────────────────────────────────────────────────────────
   PHASE STATE MACHINE
   ──────────────────────────────────────────────────────────────── */
const Phase = Object.freeze({
  IDLE:    'idle',
  LOADING: 'loading',
  READY:   'ready',
  INTRO:   'intro',
  MAIN:    'main',
  OUTRO:   'outro',
  FINAL:   'final',
  DONE:    'done',
});

/* ────────────────────────────────────────────────────────────────
   GLOBAL STATE
   ──────────────────────────────────────────────────────────────── */
let appState          = null;   // JSON from API / mock
let audioDuration     = 0;      // seconds
let audioFailed       = false;  // true if audio could not load
let sceneWallStart    = 0;      // Date.now() when show began (wall-clock fallback)
let currentPhase      = Phase.IDLE;
let stopStreamSent    = false;
let showStarted       = false;
let rafId             = null;

let isDebug = false;
let isDemo  = false;
let demoScenario = '';

let loadPageStartedAt = 0;
let loadingOverlayDismissed = false;
let localBrowserEmbedGestureMode = false;
let currentClipState = {
  index: 0,
  total: 0,
  startedAt: 0,
  duration: 1,
};
let activeStatsSections = [];
let clipDeck = [];
let clipDeckLastIndex = null;
let clipDeckLastKey = '';
let clipAudioFadeWindow = null;

/* ────────────────────────────────────────────────────────────────
   DOM REFERENCES  (populated once DOM is ready)
   ──────────────────────────────────────────────────────────────── */
const $ = id => document.getElementById(id);
const DOM = {};

function cacheDOM() {
  DOM.loadingOverlay  = $('loading-overlay');
  DOM.loadingTitle    = $('loading-title');
  DOM.loadingDetail   = $('loading-detail');
  DOM.loadingSteps    = $('loading-steps');
  DOM.startOverlay    = $('start-overlay');
  DOM.startOverlayTitle = $('start-overlay-title');
  DOM.startOverlaySub = $('start-overlay-sub');
  DOM.errorOverlay    = $('error-overlay');
  DOM.errorMsg        = $('error-message-text');
  DOM.scene           = $('scene');
  DOM.clipsPanel      = $('clips-panel');
  DOM.statsPanel      = $('stats-panel');
  DOM.clipStage       = $('clip-stage');
  DOM.clipSlotA       = $('clip-slot-a');
  DOM.clipSlotB       = $('clip-slot-b');
  DOM.clipVideoA      = $('clip-video-a');
  DOM.clipVideoB      = $('clip-video-b');
  DOM.clipEmbedA      = $('clip-embed-a');
  DOM.clipEmbedB      = $('clip-embed-b');
  DOM.clipEmpty       = $('clip-empty');
  DOM.clipTitleText   = $('clip-title-text');
  DOM.clipStageTitle  = $('clip-stage-title');
  DOM.clipStageCaption = document.querySelector('.clip-stage-caption');
  DOM.clipCounterBadge = $('clip-counter-badge');
  DOM.clipStageProgressBar = $('clip-stage-progress-bar');
  DOM.creditsOverlay  = $('credits-overlay');
  DOM.creditsInner    = $('credits-inner');
  DOM.creditsCount    = $('credits-count');
  DOM.finalOverlay    = $('final-overlay');
  DOM.finalCard       = $('final-card');
  DOM.finalText       = $('final-text');
  DOM.debugPanel      = $('debug-panel');
  DOM.dbgPhase        = $('dbg-phase');
  DOM.dbgTime         = $('dbg-time');
  DOM.dbgDur          = $('dbg-dur');
  DOM.dbgBar          = $('dbg-bar');
  DOM.dbgMarkers      = $('dbg-markers');
  DOM.dbgLog          = $('dbg-log');
  DOM.audioEl         = $('bg-audio');
  DOM.startBtn        = $('start-btn');
  DOM.sectionVisitors = $('section-visitors');
  DOM.sectionChatters = $('section-chatters');
  DOM.sectionFirstChatters = $('section-first-chatters');
  DOM.sectionFollows  = $('section-follows');
  DOM.sectionStreaks  = $('section-streaks');
  DOM.sectionSubscriptions = $('section-subscriptions');
  DOM.sectionBits     = $('section-bits');
  DOM.sectionRaids    = $('section-raids');
  DOM.sectionRedemptions = $('section-redemptions');
  DOM.sectionDonors   = $('section-donors');
  DOM.visitorsNumber  = $('visitors-number');
  DOM.visitorsLabel   = $('visitors-label');
  DOM.visitorsSecondaryNote = $('visitors-secondary-note');
  DOM.chattersList    = $('chatters-list');
  DOM.firstChattersList = $('first-chatters-list');
  DOM.followsList     = $('follows-list');
  DOM.chattersEmpty   = $('chatters-empty');
  DOM.streaksList     = $('streaks-list');
  DOM.streaksEmpty    = $('streaks-empty');
  DOM.subscriptionsList = $('subscriptions-list');
  DOM.bitsList        = $('bits-list');
  DOM.raidsList       = $('raids-list');
  DOM.redemptionsList = $('redemptions-list');
  DOM.donorsChips     = $('donors-chips');
  DOM.noDonorsMsg     = $('no-donors-msg');
  DOM.sessionTitle    = $('session-title-text');
  DOM.creditsLabel    = $('credits-label');
  DOM.statsCurrentLabel = $('stats-current-label');
  DOM.statsCurrentIndex = $('stats-current-index');
  DOM.statsNav        = $('stats-nav');
  DOM.statsNavItems   = [];
}

/* ────────────────────────────────────────────────────────────────
   TIMELINE SCHEDULER
   Events are fired when getSceneTime() crosses their `t` threshold.
   ──────────────────────────────────────────────────────────────── */
const timelineEvents = [];  // { t, fn, label, done }

function at(t, fn, label) {
  timelineEvents.push({ t, fn, label: label || '', done: false });
  timelineEvents.sort((a, b) => a.t - b.t);  // keep sorted
}

function checkTimeline(now) {
  for (const ev of timelineEvents) {
    if (!ev.done && now >= ev.t) {
      ev.done = true;
      debugFireEvent(ev.label, ev.t);
      try { ev.fn(); } catch (e) { console.error('[timeline]', ev.label, e); }
    }
  }
}

/* ────────────────────────────────────────────────────────────────
   TIME SOURCE
   Primary: audio.currentTime.  Fallback: wall-clock (if audio failed).
   ──────────────────────────────────────────────────────────────── */
function getSceneTime() {
  if (!audioFailed && DOM.audioEl && !DOM.audioEl.paused && DOM.audioEl.currentTime > 0) {
    return DOM.audioEl.currentTime;
  }
  if (sceneWallStart > 0) {
    return (Date.now() - sceneWallStart) / 1000;
  }
  return 0;
}

/* ────────────────────────────────────────────────────────────────
   UTILITIES
   ──────────────────────────────────────────────────────────────── */
function escapeHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function clamp(v, min, max) { return Math.max(min, Math.min(max, v)); }

function lerp(a, b, t) { return a + (b - a) * clamp(t, 0, 1); }

function formatCompactNumber(value) {
  const n = Number(value || 0);
  if (!Number.isFinite(n) || n <= 0) return '0';
  return new Intl.NumberFormat('ru-RU', {
    notation: 'compact',
    maximumFractionDigits: 1,
  }).format(n);
}

function getConfiguredMusicVolume(audioConfig) {
  const config = audioConfig || {};
  const base = clamp(config.volume ?? 0.85, 0, 1);
  const trim = clamp(config.mix_gain ?? CFG.MUSIC_BASE_TRIM, 0, 1);
  return clamp(base * trim, 0, 1);
}

function buildStateUrl(demoMode) {
  const url = new URL(CFG.API_ENDPOINT, location.href);
  if (demoMode) url.searchParams.set('demo', '1');
  return url.toString();
}

function getMockEndpoint() {
  if (demoScenario === 'sparse') return CFG.MOCK_SPARSE_ENDPOINT;
  return CFG.MOCK_ENDPOINT;
}

async function loadMockState() {
  const endpoint = getMockEndpoint();
  const res = await fetch(endpoint, { cache: 'no-store' });
  if (!res.ok) throw new Error(`Failed to load mock: HTTP ${res.status}`);
  return res.json();
}

/* ────────────────────────────────────────────────────────────────
   ENTER PHASE
   ──────────────────────────────────────────────────────────────── */
function enterPhase(phase) {
  currentPhase = phase;
  debugLog(`→ phase: ${phase}`, 'log-fired');
  if (DOM.dbgPhase) DOM.dbgPhase.textContent = phase;
}

/* ────────────────────────────────────────────────────────────────
   ERROR & OVERLAYS
   ──────────────────────────────────────────────────────────────── */
function showError(msg) {
  hideLoadingOverlay();
  if (DOM.errorMsg) DOM.errorMsg.textContent = msg;
  DOM.errorOverlay.classList.remove('hidden');
  DOM.startOverlay.classList.add('hidden');
}

/* ── Loading overlay ───────────────────────────────────────────── */
function setLoaderPhase(activeKey) {
  if (!DOM.loadingSteps) return;
  const order = ['data', 'audio', 'ready'];
  const iActive = order.indexOf(activeKey);
  DOM.loadingSteps.querySelectorAll('[data-step]').forEach(li => {
    const key = li.getAttribute('data-step');
    const idx = order.indexOf(key);
    li.classList.remove('loading-step--pending', 'loading-step--active', 'loading-step--done');
    if (idx < 0) return;
    if (iActive < 0) {
      li.classList.add('loading-step--pending');
      return;
    }
    if (idx < iActive) li.classList.add('loading-step--done');
    else if (idx === iActive) li.classList.add('loading-step--active');
    else li.classList.add('loading-step--pending');
  });
}

function setLoaderTexts(title, detail) {
  if (DOM.loadingTitle) DOM.loadingTitle.textContent = title;
  if (DOM.loadingDetail) DOM.loadingDetail.textContent = detail || '';
}

function hideLoadingOverlay() {
  if (loadingOverlayDismissed) return;
  loadingOverlayDismissed = true;
  document.body.classList.remove('body-loading');
  if (!DOM.loadingOverlay) return;
  DOM.loadingOverlay.classList.add('load-exit');
  DOM.loadingOverlay.setAttribute('aria-busy', 'false');
  setTimeout(() => {
    DOM.loadingOverlay.classList.add('hidden');
  }, 620);
}

function setStartOverlayCopy(title, sub) {
  if (DOM.startOverlayTitle) DOM.startOverlayTitle.textContent = title;
  if (DOM.startOverlaySub) DOM.startOverlaySub.textContent = sub;
}

function finishLoadingAndStartAttempt() {
  const wait = Math.max(0, CFG.LOADING_MIN_MS - (Date.now() - loadPageStartedAt));
  setTimeout(() => {
    hideLoadingOverlay();
    attemptAutoplay();
  }, wait);
}

function showStartOverlay() {
  DOM.startOverlay.classList.remove('hidden');
}

function hideStartOverlay() {
  DOM.startOverlay.classList.add('hidden');
}

/* ────────────────────────────────────────────────────────────────
   THEME APPLICATION
   ──────────────────────────────────────────────────────────────── */
function applyTheme(theme) {
  if (!theme) return;
  const root = document.documentElement;
  if (theme.bg_color)        root.style.setProperty('--bg',       theme.bg_color);
  if (theme.panel_color)     root.style.setProperty('--panel',    theme.panel_color);
  if (theme.accent_color) {
    root.style.setProperty('--accent', theme.accent_color);
    // Derive glow from accent
    root.style.setProperty('--accent-glow', hexToRgba(theme.accent_color, 0.12));
  }
  if (theme.text_color)      root.style.setProperty('--text',    theme.text_color);
  if (theme.muted_text_color) root.style.setProperty('--muted',  theme.muted_text_color);
  if (theme.success_color)   root.style.setProperty('--success', theme.success_color);
  if (theme.danger_color)    root.style.setProperty('--danger',  theme.danger_color);
  document.body.style.background = theme.bg_color || '#0f0e0b';
}

function hexToRgba(hex, alpha) {
  const m = hex.replace('#', '').match(/.{1,2}/g);
  if (!m || m.length < 3) return `rgba(198,168,118,${alpha})`;
  const [r, g, b] = m.map(x => parseInt(x, 16));
  return `rgba(${r},${g},${b},${alpha})`;
}

/* ────────────────────────────────────────────────────────────────
   LOAD STATE
   ──────────────────────────────────────────────────────────────── */
async function loadState() {
  if (isDemo) {
    if (demoScenario) {
      debugLog(`demo scenario: ${demoScenario} — loading local mock`, 'log-warn');
      return loadMockState();
    }
    try {
      debugLog('demo mode: loading backend snapshot with auto clips', 'log-warn');
      const res = await fetch(buildStateUrl(true), { cache: 'no-store' });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return res.json();
    } catch (apiErr) {
      debugLog(`demo API unavailable (${apiErr.message}), falling back to mock file`, 'log-warn');
      return loadMockState();
    }
  }

  try {
    const res = await fetch(buildStateUrl(false), { cache: 'no-store' });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.json();
  } catch (apiErr) {
    debugLog(`API unavailable (${apiErr.message}), falling back to mock`, 'log-warn');
    return loadMockState();
  }
}

/* ────────────────────────────────────────────────────────────────
   SCENE POPULATION  (populates DOM with state data before show)
   ──────────────────────────────────────────────────────────────── */
function populateScene(state) {
  // Session title
  const title = state.session_title || '';
  if (title && DOM.sessionTitle) DOM.sessionTitle.textContent = title;

  // Final message
  if (DOM.finalText) DOM.finalText.textContent = state.final_message || 'Спасибо!';

  // Stats section 1: visitors
  const stats = state.stats || {};
  if (DOM.visitorsLabel && stats.total_seen_label) {
    DOM.visitorsLabel.textContent = stats.total_seen_label;
  }
  if (DOM.visitorsNumber) {
    DOM.visitorsNumber.textContent = String(stats.total_seen_users || 0);
  }

  // Stats section 2: top chatters
  buildTopList(DOM.chattersList, state.top_chatters, 'messages', v => pluralize(v, 'сообщение', 'сообщения', 'сообщений'));
  if (!state.top_chatters || !state.top_chatters.length) {
    DOM.chattersEmpty && DOM.chattersEmpty.classList.remove('hidden');
  }

  buildNameChips(DOM.firstChattersList, state.first_time_chatters || []);
  buildNameChips(DOM.followsList, state.follows || []);

  // Stats section 5: top streaks
  buildTopList(DOM.streaksList, state.top_streaks, 'streak', v => pluralize(v, 'стрим подряд', 'стрима подряд', 'стримов подряд'));
  if (!state.top_streaks || !state.top_streaks.length) {
    DOM.streaksEmpty && DOM.streaksEmpty.classList.remove('hidden');
  }

  buildEventList(DOM.subscriptionsList, state.subscription_events || []);
  buildTopList(DOM.bitsList, state.bits_leaders, 'bits', 'bits');
  buildEventList(DOM.raidsList, state.raids || []);
  buildEventList(DOM.redemptionsList, state.reward_redemptions || []);

  // Stats section 10: donors
  if (state.donors && state.donors.length > 0) {
    buildDonorChips(DOM.donorsChips, state.donors);
  } else {
    DOM.noDonorsMsg && DOM.noDonorsMsg.classList.remove('hidden');
  }

  // Credits
  buildCredits(state.credits || []);
  updateVisitorsSummary(state.credits || []);

  // Clips
  initClips(state.clips || []);

  activeStatsSections = getVisibleStatsSections(state);
  syncVisibleSections(activeStatsSections);
  renderStatsNav(activeStatsSections);
  transitionToSection(activeStatsSections[0] || 'visitors');
}

function pluralize(n, form1, form2, form5) {
  let v = Math.abs(n) % 100;
  let v10 = v % 10;
  if (v > 10 && v < 20) return form5;
  if (v10 === 1) return form1;
  if (v10 > 1 && v10 < 5) return form2;
  return form5;
}

function buildTopList(container, items, valueKey, valueSuffixOrFn) {
  if (!container) return;
  container.innerHTML = '';
  if (!items || !items.length) return;

  const rankClasses = ['top-rank--gold', 'top-rank--silver', 'top-rank--bronze'];
  const rankLabels  = ['#1', '#2', '#3'];

  items.forEach((item, i) => {
    const el = document.createElement('div');
    el.className = `top-item top-item--${valueKey}`;
    el.setAttribute('role', 'listitem');
    const rankClass = rankClasses[i] || '';
    const rankLabel = rankLabels[i] || `#${i + 1}`;
    const val = item[valueKey] || 0;
    const suffix = typeof valueSuffixOrFn === 'function' ? valueSuffixOrFn(val) : valueSuffixOrFn;

    el.innerHTML = `
      <span class="top-rank ${rankClass}">${escapeHtml(rankLabel)}</span>
      <span class="top-name">${escapeHtml(item.name || '—')}</span>
      <span class="top-value">${escapeHtml(String(val))}<span class="top-suffix"> ${escapeHtml(suffix)}</span></span>
    `;
    container.appendChild(el);
  });
}

function buildDonorChips(container, donors) {
  if (!container) return;
  container.innerHTML = '';

  donors.forEach(donor => {
    const chip = document.createElement('div');
    chip.className = 'donor-chip';
    chip.setAttribute('role', 'listitem');
    const contribution = donor.contribution_label || '';
    const message = String(donor.message || '').trim();
    chip.innerHTML = `
      <span class="donor-name">${escapeHtml(donor.name || '—')}</span>
      <span class="donor-amount">${escapeHtml(donor.formatted_amount || String(donor.amount || ''))}</span>
      ${contribution ? `<span class="donor-meta">${escapeHtml(contribution)}</span>` : ''}
      ${message ? `<span class="donor-message">«${escapeHtml(message)}»</span>` : ''}
    `;
    container.appendChild(chip);
  });
}

function buildNameChips(container, items) {
  if (!container) return;
  container.innerHTML = '';
  if (!items || !items.length) return;

  const isCompact = items.length > 12;
  items.forEach(item => {
    const chip = document.createElement('div');
    const name = String(item.name || '—');
    const lengthClass = name.length > 18 ? ' name-chip--very-long' : name.length > 12 ? ' name-chip--long' : '';
    chip.className = `name-chip${isCompact ? ' name-chip--compact' : ''}${lengthClass}`;
    chip.setAttribute('role', 'listitem');
    chip.textContent = name;
    chip.title = name;
    container.appendChild(chip);
  });
}

function buildEventList(container, items) {
  if (!container) return;
  container.innerHTML = '';
  if (!items || !items.length) return;

  const isCompact = items.length > 6;
  items.forEach(item => {
    const row = document.createElement('div');
    row.className = `event-item${isCompact ? ' event-item--compact' : ''}`;
    row.setAttribute('role', 'listitem');
    row.title = [item.name, item.detail].filter(Boolean).join(' — ');
    row.innerHTML = `
      <span class="event-name">${escapeHtml(item.name || '—')}</span>
      <span class="event-detail">${escapeHtml(item.detail || '')}</span>
    `;
    container.appendChild(row);
  });
}

function buildCredits(names) {
  if (!DOM.creditsInner || !DOM.creditsOverlay) return;
  const seen = new Set();
  const safeNames = [];
  (Array.isArray(names) ? names : []).forEach(rawName => {
    const name = String(rawName || '').trim();
    const key = name.toLocaleLowerCase();
    if (!name || seen.has(key)) return;
    seen.add(key);
    safeNames.push(name);
  });

  if (!safeNames.length) {
    DOM.creditsInner.innerHTML = '';
    DOM.creditsOverlay.classList.add('hidden');
    return;
  }

  const laneCount = safeNames.length >= 10 ? 2 : 1;
  const lanes = Array.from({ length: laneCount }, () => []);
  safeNames.forEach((name, index) => {
    lanes[index % laneCount].push(name);
  });

  const renderNameSet = lane => lane
    .map(name => `<span class="credits-name">${escapeHtml(name)}</span><span class="credits-sep" aria-hidden="true">•</span>`)
    .join('');

  const rows = lanes.map((lane, index) => {
    const content = renderNameSet(lane);
    return `
      <div class="credits-row credits-row--ticker" style="--credits-duration:58s; --credits-delay:-${index * 14}s">
        <div class="credits-track">
          <div class="credits-set">${content}</div>
          <div class="credits-set" aria-hidden="true">${content}</div>
        </div>
      </div>
    `;
  });

  DOM.creditsInner.innerHTML = rows.join('');
  DOM.creditsOverlay.classList.remove('hidden', 'credits-active', 'credits-visible', 'credits-hiding');
  requestCreditsTickerLayout();
  if (DOM.creditsLabel) {
    DOM.creditsLabel.textContent = 'ЛЮДИ, КОТОРЫЕ БЫЛИ НА СТРИМЕ';
  }
  if (DOM.creditsCount) {
    const count = safeNames.length;
    DOM.creditsCount.textContent = count ? `${formatCompactNumber(count)} ${pluralize(count, 'человек', 'человека', 'человек')}` : '';
  }
}

const creditsTickerBaseHtml = new WeakMap();
let creditsTickerLayoutRaf = 0;

function requestCreditsTickerLayout() {
  if (creditsTickerLayoutRaf) {
    cancelAnimationFrame(creditsTickerLayoutRaf);
  }
  creditsTickerLayoutRaf = requestAnimationFrame(() => {
    creditsTickerLayoutRaf = 0;
    configureCreditTickerRows();
  });
}

function configureCreditTickerRows() {
  if (!DOM.creditsInner || !DOM.creditsOverlay) return;

  const rows = DOM.creditsInner.querySelectorAll('.credits-row--ticker');
  if (!rows.length) return;

  rows.forEach(row => {
    const track = row.querySelector('.credits-track');
    const firstSet = track && track.querySelector('.credits-set:first-child');
    const secondSet = track && track.querySelector('.credits-set:nth-child(2)');
    if (!track || !firstSet || !secondSet) return;

    const rowWidth = row.getBoundingClientRect().width;
    if (!rowWidth) return;

    const baseHtml = creditsTickerBaseHtml.get(firstSet) || firstSet.innerHTML;
    creditsTickerBaseHtml.set(firstSet, baseHtml);
    firstSet.innerHTML = baseHtml;

    const targetWidth = rowWidth + CFG.CREDITS_FILL_PADDING_PX;
    let guard = 0;
    while (firstSet.getBoundingClientRect().width < targetWidth && guard < 12) {
      firstSet.insertAdjacentHTML('beforeend', baseHtml);
      guard += 1;
    }

    secondSet.innerHTML = firstSet.innerHTML;

    const loopWidth = Math.max(firstSet.getBoundingClientRect().width, rowWidth);
    const duration = Math.max(CFG.CREDITS_MIN_DURATION, loopWidth / CFG.CREDITS_TICKER_SPEED);
    track.style.setProperty('--credits-shift', `${loopWidth.toFixed(1)}px`);
    row.style.setProperty('--credits-duration', `${duration.toFixed(1)}s`);
  });
}

function updateStatsMeta(id) {
  const meta = STATS_META[id] || STATS_META.visitors;
  if (DOM.statsCurrentLabel) DOM.statsCurrentLabel.textContent = meta.label;
  const currentIndex = Math.max(activeStatsSections.indexOf(id), 0) + 1;
  if (DOM.statsCurrentIndex) DOM.statsCurrentIndex.textContent = String(currentIndex).padStart(2, '0');
  if (DOM.statsPanel) DOM.statsPanel.dataset.section = id || 'visitors';
  if (DOM.statsNavItems) {
    DOM.statsNavItems.forEach(item => {
      item.classList.toggle('is-active', item.getAttribute('data-stats-nav') === id);
    });
  }
}

function getVisibleStatsSections(state) {
  const items = [
    'visitors',
    state.top_chatters && state.top_chatters.length > 0 ? 'chatters' : null,
    state.first_time_chatters && state.first_time_chatters.length > 0 ? 'firstChatters' : null,
    state.follows && state.follows.length > 0 ? 'follows' : null,
    state.top_streaks && state.top_streaks.length > 0 ? 'streaks' : null,
    state.subscription_events && state.subscription_events.length > 0 ? 'subscriptions' : null,
    state.bits_leaders && state.bits_leaders.length > 0 ? 'bits' : null,
    state.raids && state.raids.length > 0 ? 'raids' : null,
    state.reward_redemptions && state.reward_redemptions.length > 0 ? 'redemptions' : null,
    state.donors && state.donors.length > 0 ? 'donors' : null,
  ].filter(Boolean);
  return items.length ? items : ['visitors'];
}

function getSectionElementById(id) {
  const mapping = {
    visitors: DOM.sectionVisitors,
    chatters: DOM.sectionChatters,
    firstChatters: DOM.sectionFirstChatters,
    follows: DOM.sectionFollows,
    streaks: DOM.sectionStreaks,
    subscriptions: DOM.sectionSubscriptions,
    bits: DOM.sectionBits,
    raids: DOM.sectionRaids,
    redemptions: DOM.sectionRedemptions,
    donors: DOM.sectionDonors,
  };
  return mapping[id] || null;
}

function syncVisibleSections(sectionIds) {
  Object.keys(STATS_META).forEach(id => {
    const el = getSectionElementById(id);
    if (!el) return;
    el.classList.toggle('hidden', !sectionIds.includes(id));
    el.classList.remove('section-active', 'section-leaving');
  });
}

function renderStatsNav(sectionIds) {
  if (!DOM.statsNav) return;
  DOM.statsNav.innerHTML = sectionIds.map((id, index) => {
    const meta = STATS_META[id] || STATS_META.visitors;
    const isActive = index === 0 ? ' is-active' : '';
    return `<span class="stats-nav-item${isActive}" data-stats-nav="${escapeHtml(id)}">${escapeHtml(meta.shortLabel || meta.label)}</span>`;
  }).join('');
  DOM.statsNavItems = Array.from(DOM.statsNav.querySelectorAll('[data-stats-nav]'));
}

function updateClipUi(index, clip, startedAt, sequencePosition = null) {
  const total = (appState && Array.isArray(appState.clips)) ? appState.clips.length : 0;
  const safeClip = clip || {};
  currentClipState = {
    index,
    total,
    startedAt,
    duration: getClipScheduleDuration(safeClip),
  };

  const label = safeClip.title || 'Лучший момент эфира';
  setClipTitle(label);
  if (DOM.clipStageTitle) DOM.clipStageTitle.textContent = label;
  if (DOM.clipStageCaption) {
    DOM.clipStageCaption.textContent = 'Лучшие моменты эфира';
  }
  if (DOM.clipCounterBadge) {
    const rawShownIndex = sequencePosition != null ? sequencePosition : (index || 0);
    const shownIndex = total > 0 ? (rawShownIndex % total) : 0;
    const num = String(shownIndex + 1).padStart(2, '0');
    const den = String(Math.max(total, 1)).padStart(2, '0');
    DOM.clipCounterBadge.textContent = `${num} / ${den}`;
  }
}

function updateClipProgress(now) {
  if (!DOM.clipStageProgressBar) return;

  let progress = 0;
  if (currentPhase === Phase.MAIN && currentClipState.duration > 0) {
    progress = clamp((now - currentClipState.startedAt) / currentClipState.duration, 0, 1);
  } else if (currentPhase === Phase.OUTRO || currentPhase === Phase.FINAL || currentPhase === Phase.DONE) {
    progress = 1;
  }

  DOM.clipStageProgressBar.style.transform = `scaleX(${progress.toFixed(4)})`;
}

function updateVisitorsSummary(names) {
  if (!DOM.visitorsSecondaryNote) return;
  const count = Array.isArray(names) ? names.length : 0;
  if (!count) {
    DOM.visitorsSecondaryNote.classList.add('hidden');
    DOM.visitorsSecondaryNote.textContent = '';
    return;
  }
  DOM.visitorsSecondaryNote.textContent = 'Ниже пойдут все участники, которых мы реально увидели во время эфира';
  DOM.visitorsSecondaryNote.classList.remove('hidden');
}

function shuffleArray(items) {
  const copy = items.slice();
  for (let i = copy.length - 1; i > 0; i -= 1) {
    const j = Math.floor(Math.random() * (i + 1));
    [copy[i], copy[j]] = [copy[j], copy[i]];
  }
  return copy;
}

function getClipRepeatKey(index) {
  const clips = appState && Array.isArray(appState.clips) ? appState.clips : [];
  const clip = clips[index] || {};
  const url = String(
    clip.url ||
    clip.fallback_video_url ||
    clip.fallback_local_url ||
    clip.fallback_embed_url ||
    clip.embed_url ||
    ''
  ).trim().toLowerCase();
  if (url) return `url:${url}`;

  const title = String(clip.title || '').trim().toLowerCase();
  const duration = String(clip.duration_sec || clip.preferred_duration_sec || '').trim();
  if (title || duration) return `meta:${title}:${duration}`;

  return `index:${index}`;
}

function refillClipDeck(total) {
  if (total <= 1) {
    clipDeck = [0];
    return;
  }
  const order = shuffleArray(Array.from({ length: total }, (_, i) => i));
  if (clipDeckLastKey && getClipRepeatKey(order[0]) === clipDeckLastKey) {
    const swapIdx = order.findIndex(idx => getClipRepeatKey(idx) !== clipDeckLastKey);
    if (swapIdx > 0) {
      [order[0], order[swapIdx]] = [order[swapIdx], order[0]];
    }
  }
  clipDeck = order;
}

function resetClipDeck(total) {
  clipDeck = [];
  clipDeckLastIndex = null;
  clipDeckLastKey = '';
  refillClipDeck(total);
}

function drawNextClipIndex(total) {
  if (total <= 1) return 0;
  if (!clipDeck.length) refillClipDeck(total);
  if (clipDeckLastKey && getClipRepeatKey(clipDeck[0]) === clipDeckLastKey) {
    const swapIdx = clipDeck.findIndex(idx => getClipRepeatKey(idx) !== clipDeckLastKey);
    if (swapIdx > 0) {
      [clipDeck[0], clipDeck[swapIdx]] = [clipDeck[swapIdx], clipDeck[0]];
    }
  }
  const next = clipDeck.shift();
  clipDeckLastIndex = next;
  clipDeckLastKey = getClipRepeatKey(next);
  return next;
}

function getClipBaseVolume(clip) {
  const show = appState && appState.show ? appState.show : {};
  return clamp(
    clip && clip.clip_volume != null ? clip.clip_volume : (show.clip_video_volume ?? 1),
    0,
    1
  );
}

function setClipAudioFadeWindow(startT, endT) {
  if (!Number.isFinite(startT) || !Number.isFinite(endT) || endT <= startT) {
    clipAudioFadeWindow = null;
    return;
  }
  clipAudioFadeWindow = { startT, endT };
}

function getClipAudioFadeFactor(now) {
  if (!clipAudioFadeWindow) return 1;
  if (now <= clipAudioFadeWindow.startT) return 1;
  if (now >= clipAudioFadeWindow.endT) return 0;
  return 1 - clamp(
    (now - clipAudioFadeWindow.startT) / (clipAudioFadeWindow.endT - clipAudioFadeWindow.startT),
    0,
    1
  );
}

function getClipScheduleDuration(clip) {
  const realDuration = Number(clip && clip.duration_sec);
  if (Number.isFinite(realDuration) && realDuration > 0.2) {
    return realDuration;
  }
  const preferred = Number(clip && clip.preferred_duration_sec);
  if (Number.isFinite(preferred) && preferred > 0.2) {
    return preferred;
  }
  return 15;
}

function isObsBrowserEnvironment() {
  return typeof window.obsstudio !== 'undefined' || /obs/i.test(navigator.userAgent || '');
}

function hasEmbedClips() {
  return Boolean(appState && Array.isArray(appState.clips) && appState.clips.some(clip => {
    const normalized = normalizeClipDescriptor(clip);
    return normalized && normalized.type === 'embed';
  }));
}

function shouldUseLocalEmbedGestureMode() {
  return hasEmbedClips() && !isObsBrowserEnvironment();
}

/* ────────────────────────────────────────────────────────────────
   AUDIO INIT
   ──────────────────────────────────────────────────────────────── */
function initAudio(audioConfig) {
  const audio = DOM.audioEl;
  audio.volume = getConfiguredMusicVolume(audioConfig);
  audio.src    = audioConfig.url || '';
  audio.preload = 'auto';

  audio.addEventListener('loadedmetadata', () => {
    const dur = audio.duration;
    audioDuration = (isFinite(dur) && dur > 5) ? dur : CFG.FALLBACK_DURATION;
    debugLog(`audio ready: ${audioDuration.toFixed(1)}s`, 'log-fired');
    onAudioReady();
  });

  audio.addEventListener('error', () => {
    debugLog(`audio error — using fallback ${CFG.FALLBACK_DURATION}s`, 'log-warn');
    audioFailed    = true;
    audioDuration  = CFG.FALLBACK_DURATION;
    onAudioReady();
  });

  // Drive the timeline via timeupdate for efficiency
  audio.addEventListener('timeupdate', () => {
    if (showStarted) checkTimeline(audio.currentTime);
  });

  audio.addEventListener('ended', () => {
    enterPhase(Phase.DONE);
    stopAllClipMedia();
    if (appState.show.auto_stop_stream && appState.show.stop_stream_at === 'audio_ended') {
      const stopDelayMs = clamp(Number(appState.show.stop_stream_delay_seconds ?? 10), 0, 60) * 1000;
      setTimeout(() => stopStreamOnce(), stopDelayMs);
    }
  });

  audio.load();
}

function onAudioReady() {
  enterPhase(Phase.READY);
  setLoaderPhase('ready');
  if (audioFailed) {
    setLoaderTexts(
      'Готовим финальный кадр',
      'Музыка не ответила вовремя, сцена продолжит по резервному таймеру'
    );
  } else {
    setLoaderTexts(
      'Финальная сцена готова',
      'Ещё мгновение, и начнём прощаться красиво'
    );
  }
  finishLoadingAndStartAttempt();
}

/* ────────────────────────────────────────────────────────────────
   AUTOPLAY
   ──────────────────────────────────────────────────────────────── */
async function attemptAutoplay() {
  if (shouldUseLocalEmbedGestureMode()) {
    setStartOverlayCopy(
      'Для Twitch-клипов нужен клик',
      'В обычном браузере Twitch часто блокирует автозапуск iframe со звуком. В OBS эта сцена обычно стартует сама.'
    );
    debugLog('embed clips detected in regular browser — waiting for user gesture', 'log-warn');
    showStartOverlay();
    return;
  }

  if (audioFailed) {
    // No audio — start show with wall-clock timing
    startShow();
    return;
  }

  try {
    await DOM.audioEl.play();
    startShow();
  } catch {
    setStartOverlayCopy(
      'Финальная сцена готова',
      'Нажмите, чтобы начать'
    );
    debugLog('autoplay blocked — showing start overlay', 'log-warn');
    showStartOverlay();
  }
}

// Called by the start button (onclick attribute in HTML)
function handleManualStart() {    // eslint-disable-line no-unused-vars
  localBrowserEmbedGestureMode = shouldUseLocalEmbedGestureMode();
  if (audioFailed) {
    hideStartOverlay();
    startShow();
    return;
  }

  const playPromise = DOM.audioEl.play();
  hideStartOverlay();
  startShow();
  playPromise.catch(e => {
    console.error('[ending] manual play failed:', e);
    debugLog(`manual audio play failed: ${e.message}`, 'log-warn');
  });
}

/* ────────────────────────────────────────────────────────────────
   BUILD TIMELINE
   Computes all phase timestamps from audioDuration + show config.
   ──────────────────────────────────────────────────────────────── */
function buildTimeline() {
  const show = appState.show;
  const T    = audioDuration;

  const introS   = localBrowserEmbedGestureMode ? 0 : (show.intro_seconds || 6);
  const outroS   = show.outro_seconds         || 12;
  const early    = show.outro_early_seconds   ?? 4;
  const padS     = show.main_padding_seconds  || 1;
  const fadeS    = show.music_fade_out_seconds|| 2;

  const mainStart  = introS;
  const outroStart = Math.max(mainStart + 4, T - outroS - early);
  const outroSpan  = T - outroStart;
  const usable     = outroStart - mainStart;

  const finalLeadS = clamp(Number(show.final_message_lead_seconds ?? 14), 6, Math.max(6, T - mainStart - 2));
  const finalPrepS = clamp(Number(show.final_message_prep_seconds ?? 2.5), 0.5, 6);
  const stopDelayS = clamp(Number(show.stop_stream_delay_seconds ?? 10), 0, 60);
  const outroMsgAt = Math.max(outroStart + 1, T - finalLeadS);
  const outroDarkenAt = Math.max(outroStart, outroMsgAt - finalPrepS);
  const outroStopAt = Math.max(outroMsgAt + 5, T + stopDelayS);
  const clipFadeLeadS = Math.min(2.6, Math.max(1.2, outroMsgAt - outroStart));
  const clipFadeStartAt = Math.max(outroStart, outroMsgAt - clipFadeLeadS);
  const statsStart   = mainStart + padS;
  const statsEnd     = outroStart - padS;
  const creditsWindow = clamp(Math.min(8, outroMsgAt - outroStart - 0.8), 0, 8);
  const creditsStart = creditsWindow > 0 ? Math.max(outroStart + 0.35, outroMsgAt - creditsWindow) : -1;
  const creditsEnd   = creditsWindow > 0 ? Math.max(creditsStart + 1.5, outroMsgAt - 0.35) : -1;
  const musicFadeAt  = T - fadeS;
  setClipAudioFadeWindow(clipFadeStartAt, outroMsgAt);

  debugLog(`T=${T.toFixed(1)} intro=${introS} outroStart=${outroStart.toFixed(1)} usable=${usable.toFixed(1)}`);

  // Debug markers
  if (isDebug) {
    addDebugMarker(mainStart,  'main',   T);
    addDebugMarker(outroStart, 'outro',  T);
    addDebugMarker(musicFadeAt,'fade',   T);
    addDebugMarker(outroMsgAt, 'msg',    T);
  }

  /* ── Phase transitions ──── */
  at(0,          () => enterPhase(Phase.INTRO),  'phase:intro');
  at(mainStart,  () => enterPhase(Phase.MAIN),   'phase:main');
  at(outroStart, () => enterPhase(Phase.OUTRO),  'phase:outro');
  at(outroMsgAt, () => enterPhase(Phase.FINAL),  'phase:final');

  /* ── Intro: reveal panels ── */
  at(0.2, () => {
    DOM.scene.classList.add('scene-intro');
  }, 'intro:scene-reveal');

  at(0.8, () => {
    DOM.clipsPanel.classList.add('panel-enter');
    DOM.clipsPanel.style.animationDelay = '0s';
  }, 'intro:clips-enter');

  at(1.1, () => {
    DOM.statsPanel.classList.add('panel-enter');
    DOM.statsPanel.style.animationDelay = '0s';
  }, 'intro:stats-enter');

  /* ── Stats schedule ──── */
  scheduleStats(statsStart, statsEnd);

  /* ── Clips schedule ──── */
  scheduleClips(mainStart, outroStart);

  /* ── Credits ──── */
  scheduleCredits(creditsStart, creditsEnd);

  /* ── Music fade-out ── */
  // Handled in RAF loop; just mark the time
  at(musicFadeAt, () => {
    debugLog(`music fade-out started`, 'log-fired');
  }, 'audio:fade-start');

  /* ── Outro sequence ── */
  at(outroStart, () => startOutro(), 'outro:start');
  at(outroDarkenAt, () => {
    DOM.finalOverlay.classList.remove('hidden');
    requestAnimationFrame(() => DOM.finalOverlay.classList.add('final-darken'));
  }, 'outro:darken');
  at(outroMsgAt, () => playFinalMessage(), 'outro:final-msg');

  /* ── Stop-stream signal ── */
  if (show.auto_stop_stream) {
    if (show.stop_stream_at === 'after_final_message') {
      at(outroStopAt, () => stopStreamOnce(), 'stream:stop');
    }
    // 'audio_ended' case is handled in the 'ended' audio event
  }

  return { mainStart, outroStart, musicFadeAt, usable };
}

/* ────────────────────────────────────────────────────────────────
   SCHEDULE STATS
   Divides usable time equally among visible sections.
   ──────────────────────────────────────────────────────────────── */
function scheduleStats(statsStart, statsEnd) {
  const sections  = activeStatsSections && activeStatsSections.length ? activeStatsSections : ['visitors'];
  const totalTime = Math.max(1, statsEnd - statsStart);
  const sectionDur = totalTime / sections.length;
  const transS     = (appState.show.stats_change_transition_ms || 650) / 1000;

  sections.forEach((id, i) => {
    const enterAt = statsStart + i * sectionDur;
    const leaveAt = enterAt + sectionDur - transS;

    at(enterAt, () => transitionToSection(id), `stats:enter:${id}`);
    // Schedule exit only if there is a next section
    if (i < sections.length - 1) {
      at(leaveAt, () => exitSection(id), `stats:leave:${id}`);
    }
  });
}

let currentSectionId = null;
let sectionScrollRaf = null;

function startSectionAutoScroll(id, el) {
  stopSectionAutoScroll();

  const inner = el.querySelector('.stats-inner');
  if (!inner) return;

  // Reset any previous state
  inner.style.transform = 'translateY(0)';
  el.style.alignItems = 'center';

  const containerH = el.offsetHeight;
  const contentH   = inner.scrollHeight;

  if (contentH <= containerH + 10) return; // Allow small buffer

  // If content is taller, we need to scroll.
  // Switch to flex-start so we start from the top.
  el.style.alignItems = 'flex-start';

  const scrollDist = contentH - containerH + 40; // 40px padding at bottom
  const sectionDur = getSectionDisplayDuration(id);
  
  // Timing: wait 1.5s, scroll, stay 1s at end
  const waitStart = 1.5;
  const waitEnd = 1.0;
  const scrollTime = Math.max(0.5, sectionDur - waitStart - waitEnd);
  const startTime = getSceneTime();

  function tick() {
    const elapsed = getSceneTime() - startTime;
    let offset = 0;

    if (elapsed < waitStart) {
      offset = 0;
    } else if (elapsed > waitStart + scrollTime) {
      offset = scrollDist;
    } else {
      const t = (elapsed - waitStart) / scrollTime;
      // Smooth scroll
      const ease = t * t * (3 - 2 * t);
      offset = ease * scrollDist;
    }

    inner.style.transform = `translateY(${-offset.toFixed(2)}px)`;
    sectionScrollRaf = requestAnimationFrame(tick);
  }

  sectionScrollRaf = requestAnimationFrame(tick);
}

function stopSectionAutoScroll() {
  if (sectionScrollRaf) {
    cancelAnimationFrame(sectionScrollRaf);
    sectionScrollRaf = null;
  }
}

function getSectionDisplayDuration(id) {
  // Find duration from timeline events
  const enterEv = timelineEvents.find(e => e.label === `stats:enter:${id}`);
  if (!enterEv) return 8; // Fallback

  const nextIdx = timelineEvents.indexOf(enterEv) + 1;
  for (let i = nextIdx; i < timelineEvents.length; i++) {
    const e = timelineEvents[i];
    if (e.label.startsWith('stats:enter:') || e.label === 'outro:start') {
      return e.t - enterEv.t;
    }
  }
  return 8;
}

function transitionToSection(id) {
  // Exit current
  if (currentSectionId) exitSection(currentSectionId);

  currentSectionId = id;
  updateStatsMeta(id);
  const el = getSectionElementById(id);
  if (!el) return;

  // Force reflow so transition plays
  el.classList.remove('section-leaving');
  el.getBoundingClientRect();
  el.classList.add('section-active');

  // Start auto-scroll after a short delay for the entry animation
  setTimeout(() => {
    if (currentSectionId === id) {
      startSectionAutoScroll(id, el);
    }
  }, 400);
}

function exitSection(id) {
  const el = getSectionElementById(id);
  if (!el) return;
  el.classList.add('section-leaving');
  el.classList.remove('section-active');
  
  stopSectionAutoScroll();

  const ms = (appState.show.stats_change_transition_ms || 650) + 150;
  setTimeout(() => {
    el.classList.remove('section-leaving');
    const inner = el.querySelector('.stats-inner');
    if (inner) inner.style.transform = 'translateY(0)';
  }, ms);

  if (currentSectionId === id) currentSectionId = null;
}

/* ────────────────────────────────────────────────────────────────
   CLIPS MANAGEMENT
   ──────────────────────────────────────────────────────────────── */
let clipActiveSlot  = 'a';
let clipCurrentIdx  = 0;
let clipLoadTokenSeq = 0;

function initClips(clips) {
  if (!clips || clips.length === 0) {
    DOM.clipEmpty.classList.remove('hidden');
    DOM.clipTitleText.textContent = '';
    if (DOM.clipStageTitle) DOM.clipStageTitle.textContent = 'Клипы недоступны';
    if (DOM.clipCounterBadge) DOM.clipCounterBadge.textContent = '00 / 00';
    return;
  }
  resetClipDeck(clips.length);
  clipCurrentIdx = drawNextClipIndex(clips.length);
  const firstClip = normalizeClipDescriptor(clips[clipCurrentIdx]);
  // Preload only native video. Twitch embeds are loaded at the moment they appear.
  if (firstClip && firstClip.type === 'video') {
    loadClipIntoSlot('a', firstClip);
  }
  updateClipUi(clipCurrentIdx, clips[clipCurrentIdx], 0, 0);
}

function getEmbedParentHost() {
  const host = location.hostname || 'localhost';
  if (host === '::1' || host === '[::1]') return 'localhost';
  return host;
}

function ensureTwitchParent(url) {
  try {
    const parsed = new URL(url, location.href);
    const isTwitchHost =
      parsed.hostname === 'clips.twitch.tv' ||
      parsed.hostname === 'player.twitch.tv' ||
      parsed.hostname.endsWith('.twitch.tv');

    if (!isTwitchHost) return parsed.toString();

    const parent = getEmbedParentHost();
    const parents = parsed.searchParams.getAll('parent');
    if (!parents.includes(parent)) {
      parsed.searchParams.append('parent', parent);
    }
    if (!parsed.searchParams.has('autoplay')) {
      parsed.searchParams.set('autoplay', 'true');
    }
    if (parsed.hostname === 'player.twitch.tv' && !parsed.searchParams.has('muted')) {
      parsed.searchParams.set('muted', 'false');
    }
    return parsed.toString();
  } catch {
    return url;
  }
}

function extractTwitchClipSlug(url) {
  try {
    const parsed = new URL(url, location.href);

    if (parsed.hostname === 'clips.twitch.tv') {
      const fromQuery = parsed.searchParams.get('clip');
      if (fromQuery) return fromQuery;

      const path = parsed.pathname.replace(/^\/+|\/+$/g, '');
      if (path && path !== 'embed') return path;
    }

    const match = parsed.pathname.match(/\/clip\/([^/?]+)/i);
    if (match) return match[1];
  } catch {
    return '';
  }
  return '';
}

function extractTwitchVideoId(url) {
  try {
    const parsed = new URL(url, location.href);
    const fromQuery = parsed.searchParams.get('video');
    if (fromQuery) return fromQuery;

    const match = parsed.pathname.match(/\/videos\/(\d+)/i);
    if (match) return `v${match[1]}`;
  } catch {
    return '';
  }
  return '';
}

function normalizeClipDescriptor(clip) {
  if (!clip) return null;

  const rawUrl = clip.url || '';
  if (clip.type === 'video' && !rawUrl && clip.fallback_embed_url) {
    return {
      ...clip,
      type: 'embed',
      url: ensureTwitchParent(clip.fallback_embed_url),
    };
  }
  const clipSlug = extractTwitchClipSlug(rawUrl);
  if (clipSlug) {
    return {
      ...clip,
      type: 'embed',
      url: ensureTwitchParent(
        `https://clips.twitch.tv/embed?clip=${encodeURIComponent(clipSlug)}`
      ),
    };
  }

  const videoId = extractTwitchVideoId(rawUrl);
  if (videoId) {
    return {
      ...clip,
      type: 'embed',
      url: ensureTwitchParent(
        `https://player.twitch.tv/?video=${encodeURIComponent(videoId)}&autoplay=true&muted=false`
      ),
    };
  }

  if (clip.type === 'embed') {
    return {
      ...clip,
      url: ensureTwitchParent(rawUrl),
    };
  }

  if (clip.type === 'video' && clip.fallback_embed_url) {
    return {
      ...clip,
      fallback_embed_url: ensureTwitchParent(clip.fallback_embed_url),
    };
  }

  return clip;
}

function sameClipUrl(a, b) {
  if (!a || !b) return false;
  try {
    return new URL(a, location.href).toString() === new URL(b, location.href).toString();
  } catch {
    return String(a) === String(b);
  }
}

function isSlotActive(slot) {
  const slotEl = slot === 'a' ? DOM.clipSlotA : DOM.clipSlotB;
  return Boolean(slotEl && slotEl.classList.contains('slot-active'));
}

function getVideoFallback(video) {
  if (!video || video.dataset.fallbackTried === '1') return null;
  const currentUrl = video.currentSrc || video.src || '';
  const localUrl = video.dataset.fallbackLocalUrl || video.dataset.fallbackVideoUrl || '';
  if (localUrl && !sameClipUrl(currentUrl, localUrl)) {
    return { type: 'video', url: localUrl };
  }

  const embedUrl = video.dataset.fallbackEmbedUrl || '';
  if (embedUrl) {
    return { type: 'embed', url: ensureTwitchParent(embedUrl) };
  }

  return null;
}

function switchVideoToFallback(slot, reason) {
  const video = slot === 'a' ? DOM.clipVideoA : DOM.clipVideoB;
  const embed = slot === 'a' ? DOM.clipEmbedA : DOM.clipEmbedB;
  const fallback = getVideoFallback(video);
  if (!fallback) {
    debugLog(`clip ${slot}: video ${reason}, no fallback`, 'log-warn');
    return false;
  }

  video.dataset.fallbackTried = '1';
  debugLog(`clip ${slot}: video ${reason}, switching to ${fallback.type} fallback`, 'log-warn');

  if (fallback.type === 'video') {
    const v = clamp(Number(video.dataset.baseVolume || 1), 0, 1);
    video.pause();
    video.src = fallback.url;
    video.muted = false;
    video.volume = v;
    video.classList.add('media-visible');
    video.load();
    if (isSlotActive(slot)) {
      video.play().catch(() => {});
    }
    return true;
  }

  video.pause();
  video.classList.remove('media-visible');
  video.removeAttribute('src');
  video.load();
  embed.src = fallback.url;
  embed.classList.add('media-visible');
  return true;
}

function bindClipVideoFallbacks() {
  [
    ['a', DOM.clipVideoA],
    ['b', DOM.clipVideoB],
  ].forEach(([slot, video]) => {
    if (!video) return;
    video.addEventListener('error', () => {
      if (video.classList.contains('media-visible')) {
        switchVideoToFallback(slot, 'error');
      }
    });
  });
}

function loadClipIntoSlot(slot, clip) {
  const video = slot === 'a' ? DOM.clipVideoA : DOM.clipVideoB;
  const embed = slot === 'a' ? DOM.clipEmbedA : DOM.clipEmbedB;
  const normalizedClip = normalizeClipDescriptor(clip);
  const loadToken = String(++clipLoadTokenSeq);

  // Clear previous content
  video.classList.remove('media-visible');
  embed.classList.remove('media-visible');
  video.dataset.loadToken = loadToken;
  video.dataset.fallbackTried = '0';
  video.dataset.fallbackLocalUrl = '';
  video.dataset.fallbackVideoUrl = '';
  video.dataset.fallbackEmbedUrl = '';
  video.pause();
  video.removeAttribute('src');
  video.removeAttribute('poster');
  video.load();
  embed.src = 'about:blank';

  if (!normalizedClip) return;

  if (normalizedClip.type === 'video') {
    video.src = normalizedClip.url || '';
    if (normalizedClip.poster) video.poster = normalizedClip.poster;
    video.muted = false;
    const v = getClipBaseVolume(normalizedClip);
    video.dataset.baseVolume = String(v);
    video.dataset.fallbackLocalUrl = normalizedClip.fallback_local_url || '';
    video.dataset.fallbackVideoUrl = normalizedClip.fallback_video_url || '';
    video.dataset.fallbackEmbedUrl = normalizedClip.fallback_embed_url || '';
    video.volume = v;
    video.classList.add('media-visible');
    setTimeout(() => {
      if (
        video.dataset.loadToken === loadToken &&
        video.classList.contains('media-visible') &&
        video.readyState < HTMLMediaElement.HAVE_CURRENT_DATA
      ) {
        switchVideoToFallback(slot, 'timeout');
      }
    }, 6500);
  } else if (normalizedClip.type === 'embed') {
    embed.src = normalizedClip.url || '';
    embed.classList.add('media-visible');
  }
}

function playClipInSlot(slot) {
  const video = slot === 'a' ? DOM.clipVideoA : DOM.clipVideoB;
  if (video.src && video.classList.contains('media-visible')) {
    video.muted = false;
    video.play().catch(() => {});
  }
}

function pauseClipInSlot(slot) {
  const video = slot === 'a' ? DOM.clipVideoA : DOM.clipVideoB;
  video.pause();
}

function clearEmbedInSlot(slot) {
  const embed = slot === 'a' ? DOM.clipEmbedA : DOM.clipEmbedB;
  if (embed) embed.src = 'about:blank';
}

function stopAllClipMedia() {
  pauseClipInSlot('a');
  pauseClipInSlot('b');
  clearEmbedInSlot('a');
  clearEmbedInSlot('b');
}

function trimClipMediaForOutro() {
  const activeSlot = clipActiveSlot;
  const inactiveSlot = activeSlot === 'a' ? 'b' : 'a';

  pauseClipInSlot(inactiveSlot);
  clearEmbedInSlot(inactiveSlot);

  const activeEmbed = activeSlot === 'a' ? DOM.clipEmbedA : DOM.clipEmbedB;
  if (activeEmbed && activeEmbed.classList.contains('media-visible')) {
    clearEmbedInSlot(activeSlot);
  }
}

function forceClipAudioSilentForFinal() {
  const muteVideo = (video) => {
    if (!video) return;
    video.volume = 0;
    video.muted = true;
  };

  muteVideo(DOM.clipVideoA);
  muteVideo(DOM.clipVideoB);

  // Twitch embeds can't be smoothly volume-automated from the page,
  // so by the final words we remove them to guarantee silence.
  clearEmbedInSlot('a');
  clearEmbedInSlot('b');
}

function setClipTitle(title) {
  if (DOM.clipTitleText) {
    DOM.clipTitleText.style.opacity = '0';
    setTimeout(() => {
      DOM.clipTitleText.textContent = title || '';
      DOM.clipTitleText.style.opacity = '1';
    }, 200);
  }
}

function crossfadeToSlot(nextSlot) {
  const crossMs = appState.show.clip_crossfade_ms || 500;
  const curSlotEl  = nextSlot === 'a' ? DOM.clipSlotB : DOM.clipSlotA;
  const nextSlotEl = nextSlot === 'a' ? DOM.clipSlotA : DOM.clipSlotB;
  const oldSlot    = nextSlot === 'a' ? 'b' : 'a';

  pauseClipInSlot(oldSlot);

  curSlotEl.style.transition  = `opacity ${crossMs}ms ease`;
  nextSlotEl.style.transition = `opacity ${crossMs}ms ease`;

  nextSlotEl.classList.add('slot-active');
  playClipInSlot(nextSlot);

  setTimeout(() => {
    curSlotEl.classList.remove('slot-active');
    clearEmbedInSlot(oldSlot);
  }, crossMs * 0.5);

  clipActiveSlot = nextSlot;
}

function scheduleClips(mainStart, outroStart) {
  const clips = appState.clips;
  if (!clips || clips.length === 0) return;
  const firstIdx = Number.isInteger(clipCurrentIdx) ? clipCurrentIdx : 0;

  // Start playing the first clip when main phase begins
  at(mainStart, () => {
    const firstClip = normalizeClipDescriptor(clips[firstIdx]);
    if (firstClip && firstClip.type === 'embed') {
      loadClipIntoSlot('a', firstClip);
    }
    updateClipUi(firstIdx, clips[firstIdx], mainStart, 0);
    DOM.clipSlotA.classList.add('slot-active');
    clipActiveSlot = 'a';
    playClipInSlot('a');
  }, 'clip:start-first');

  if (clips.length <= 1) return;

  // Schedule crossfades
  const crossS = (appState.show.clip_crossfade_ms || 500) / 1000;
  let t        = mainStart;
  let idx      = firstIdx;
  let switchCount = 0;

  while (true) {
    const clip = clips[idx];
    const dur  = getClipScheduleDuration(clip);
    const switchAt = t + dur;

    if (switchAt + crossS > outroStart) break;

    const nextIdx    = drawNextClipIndex(clips.length);
    const nextClip   = clips[nextIdx];
    const normalizedNextClip = normalizeClipDescriptor(nextClip);
    const targetSlot = switchCount % 2 === 0 ? 'b' : 'a';

    if (normalizedNextClip && normalizedNextClip.type === 'video') {
      const preloadAt = Math.max(mainStart, switchAt - 3);
      const preloadSlot = targetSlot;
      const capturedClip = nextClip;
      const capturedSlot = preloadSlot;

      at(preloadAt, () => {
        loadClipIntoSlot(capturedSlot, capturedClip);
      }, `clip:preload-${nextIdx}`);
    }

    at(switchAt, () => {
      if (!normalizedNextClip || normalizedNextClip.type === 'embed') {
        loadClipIntoSlot(targetSlot, nextClip);
      }
      updateClipUi(nextIdx, nextClip, switchAt, switchCount + 1);
      crossfadeToSlot(targetSlot);
    }, `clip:switch-to-${nextIdx}`);

    t   = switchAt;
    idx = nextIdx;
    switchCount += 1;
  }
}

/* ────────────────────────────────────────────────────────────────
   CREDITS CRAWL
   RAF-driven scroll using audioEl.currentTime as source of truth.
   ──────────────────────────────────────────────────────────────── */
let creditsCancelled = false;

function scheduleCredits(creditsStart, creditsEnd) {
  void creditsStart;
  void creditsEnd;
}

function startCredits(startT, endT) {
  const overlay    = DOM.creditsOverlay;
  const inner      = DOM.creditsInner;
  if (!overlay || !inner) return;

  creditsCancelled = false;
  overlay.classList.add('credits-active');
  overlay.classList.remove('credits-hiding');
  overlay.classList.remove('hidden');

  // Force layout so we can measure
  overlay.getBoundingClientRect();

  const containerH = overlay.querySelector('.credits-mask').offsetHeight;
  const contentH   = inner.scrollHeight;
  const totalDist  = containerH + contentH;
  const available  = endT - startT;
  const naturalSpd = totalDist / available;
  const speed      = Math.max(naturalSpd, CFG.MIN_CREDITS_SPEED);  // px/s
  const startPos   = 0;

  // Fade in
  requestAnimationFrame(() => overlay.classList.add('credits-visible'));

  // RAF scroll loop
  function tick() {
    if (creditsCancelled) return;

    const elapsed = getSceneTime() - startT;
    if (elapsed < 0) { requestAnimationFrame(tick); return; }

    const pos = startPos - speed * elapsed;
    inner.style.transform = `translateY(${pos.toFixed(2)}px)`;

    if (pos > -contentH) {
      requestAnimationFrame(tick);
    }
  }
  requestAnimationFrame(tick);
}

function stopCredits() {
  creditsCancelled = true;
  const overlay = DOM.creditsOverlay;
  if (!overlay) return;
  overlay.classList.add('credits-hiding');
  overlay.classList.remove('credits-visible');
  overlay.classList.remove('credits-active');
  setTimeout(() => overlay.classList.add('hidden'), 1300);
}

/* ────────────────────────────────────────────────────────────────
   OUTRO
   ──────────────────────────────────────────────────────────────── */
function startOutro() {
  trimClipMediaForOutro();
  DOM.scene.classList.add('scene-outro');
  if (currentSectionId) exitSection(currentSectionId);
}

/* ────────────────────────────────────────────────────────────────
   FINAL MESSAGE
   ──────────────────────────────────────────────────────────────── */
function playFinalMessage() {
  forceClipAudioSilentForFinal();
  stopCredits();

  // Ensure overlay is visible and dark
  DOM.finalOverlay.classList.remove('hidden');
  DOM.finalOverlay.classList.add('final-darken');

  // Show the card with animation
  requestAnimationFrame(() => {
    requestAnimationFrame(() => {
      DOM.finalCard.classList.add('final-show');
    });
  });

  // Start glow pulse after the card is fully visible
  setTimeout(() => {
    DOM.finalText && DOM.finalText.classList.add('final-glow-pulse');
  }, 1800);
}

/* ────────────────────────────────────────────────────────────────
   STOP STREAM
   ──────────────────────────────────────────────────────────────── */
async function stopStreamOnce() {
  if (stopStreamSent) return;
  if (!appState || !appState.show.auto_stop_stream) return;

  stopStreamSent = true;
  debugLog('→ POST /api/obs/stop-stream', 'log-fired');

  try {
    const res = await fetch(CFG.STOP_ENDPOINT, {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ session_title: appState.session_title }),
    });
    if (!res.ok) {
      debugLog(`stop-stream: HTTP ${res.status}`, 'log-warn');
    } else {
      debugLog('stop-stream: OK', 'log-fired');
    }
  } catch (err) {
    debugLog(`stop-stream error: ${err.message}`, 'log-warn');
  }
}

/* ────────────────────────────────────────────────────────────────
   Фоновая музыка: fade-out в конце + ducking пока играет видеоклип со звуком
   ──────────────────────────────────────────────────────────────── */
function getBaseMusicVolume() {
  return getConfiguredMusicVolume(appState.audio || {});
}

function activeClipVideoIsAudible() {
  if (currentPhase !== Phase.MAIN || !appState) return false;
  const show = appState.show;
  if (show.duck_music_during_video_clips === false) return false;

  const audible = v =>
    v &&
    v.classList.contains('media-visible') &&
    Boolean(v.src) &&
    !v.paused &&
    !v.muted &&
    v.volume > 0.005;

  const activeEmbedVisible = (slotEl, embedEl) =>
    slotEl &&
    embedEl &&
    slotEl.classList.contains('slot-active') &&
    embedEl.classList.contains('media-visible') &&
    embedEl.src !== 'about:blank';

  if (DOM.clipSlotA.classList.contains('slot-active') && audible(DOM.clipVideoA)) return true;
  if (DOM.clipSlotB.classList.contains('slot-active') && audible(DOM.clipVideoB)) return true;
  if (activeEmbedVisible(DOM.clipSlotA, DOM.clipEmbedA)) return true;
  if (activeEmbedVisible(DOM.clipSlotB, DOM.clipEmbedB)) return true;
  return false;
}

function updateClipAudioVolumes(now) {
  if (!appState) return;

  const apply = (video, slotEl) => {
    if (!video || !slotEl) return;
    const base = clamp(Number(video.dataset.baseVolume || 0), 0, 1);
    const isVisible = video.classList.contains('media-visible') && Boolean(video.src);
    const isActive = slotEl.classList.contains('slot-active');

    if (!isVisible || !isActive) {
      video.volume = 0;
      return;
    }

    let factor = 1;
    if (currentPhase === Phase.OUTRO || currentPhase === Phase.FINAL || currentPhase === Phase.DONE) {
      factor = getClipAudioFadeFactor(now);
    }
    video.volume = base * factor;
  };

  apply(DOM.clipVideoA, DOM.clipSlotA);
  apply(DOM.clipVideoB, DOM.clipSlotB);
}

function updateBgMusicVolume(now) {
  if (audioFailed || !DOM.audioEl || !audioDuration || !appState) return;

  const show  = appState.show;
  const fadeS = show.music_fade_out_seconds || 2;
  const fadeAt = audioDuration - fadeS;
  const base   = getBaseMusicVolume();

  let factor = 1;
  if (now >= fadeAt) {
    factor *= 1 - clamp((now - fadeAt) / fadeS, 0, 1);
  } else if (activeClipVideoIsAudible()) {
    factor *= clamp(show.music_duck_level ?? 0.22, 0, 1);
  }

  DOM.audioEl.volume = base * factor;
}

/* ────────────────────────────────────────────────────────────────
   RAF ANIMATION LOOP
   Runs continuously after show starts.
   Drives: music fade, debug UI, wall-clock timeline (fallback).
   ──────────────────────────────────────────────────────────────── */
function startAnimationLoop() {
  function tick() {
    const now = getSceneTime();

    // Keep timeline alive after audio ends so delayed OBS stop can fire.
    if (showStarted) checkTimeline(now);

    if (showStarted && appState) updateBgMusicVolume(now);
    if (showStarted && appState) updateClipAudioVolumes(now);
    updateClipProgress(now);

    // Debug UI
    if (isDebug && audioDuration > 0) {
      DOM.dbgTime.textContent = now.toFixed(1) + 's';
      DOM.dbgDur.textContent  = `/ ${audioDuration.toFixed(1)}s`;
      const pct = clamp((now / audioDuration) * 100, 0, 100);
      DOM.dbgBar.style.width  = pct + '%';
    }

    rafId = requestAnimationFrame(tick);
  }
  rafId = requestAnimationFrame(tick);
}

/* ────────────────────────────────────────────────────────────────
   START SHOW
   Called once: either after successful autoplay or manual start.
   ──────────────────────────────────────────────────────────────── */
function startShow() {
  if (showStarted) return;
  showStarted    = true;
  sceneWallStart = Date.now();

  buildTimeline();
  checkTimeline(0);
  updateClipProgress(0);
  startAnimationLoop();

  debugLog('show started', 'log-fired');
}

/* ────────────────────────────────────────────────────────────────
   DEBUG HELPERS
   ──────────────────────────────────────────────────────────────── */
function debugLog(msg, cls) {
  if (!isDebug) return;
  console.log('[ending]', msg);
  if (!DOM.dbgLog) return;
  const el = document.createElement('div');
  el.className = `debug-log-entry ${cls || ''}`;
  el.textContent = msg;
  DOM.dbgLog.appendChild(el);
  DOM.dbgLog.scrollTop = DOM.dbgLog.scrollHeight;
}

function debugFireEvent(label, t) {
  if (!isDebug || !label) return;
  debugLog(`✓ ${label} @ ${t.toFixed(2)}s`, 'log-fired');
}

function addDebugMarker(t, label, total) {
  if (!DOM.dbgMarkers || !total) return;
  const pct = clamp((t / total) * 100, 0, 100);
  const marker = document.createElement('div');
  marker.className = 'debug-marker';
  marker.style.left = pct + '%';
  marker.title = `${label} @ ${t.toFixed(1)}s`;
  DOM.dbgMarkers.appendChild(marker);
}

function initDebug() {
  if (!isDebug) return;
  DOM.debugPanel.classList.remove('hidden');
  DOM.dbgPhase.textContent = Phase.LOADING;
}

/* ────────────────────────────────────────────────────────────────
   INIT  —  Entry point
   ──────────────────────────────────────────────────────────────── */
async function init() {
  const params = new URLSearchParams(location.search);
  isDebug = params.has('debug');
  isDemo  = params.has('demo');
  demoScenario = (params.get('example') || '').trim().toLowerCase();

  localBrowserEmbedGestureMode = false;
  loadPageStartedAt = Date.now();
  cacheDOM();
  bindClipVideoFallbacks();
  initDebug();
  window.addEventListener('resize', requestCreditsTickerLayout);

  if (DOM.startBtn) {
    DOM.startBtn.addEventListener('click', handleManualStart);
  }

  setLoaderPhase('data');
  setLoaderTexts(
    'Собираем финальные моменты',
    'Подтягиваем имена, события эфира и лучшие фрагменты'
  );

  enterPhase(Phase.LOADING);

  try {
    appState = await loadState();
  } catch (err) {
    showError(`Не удалось загрузить данные: ${err.message}`);
    return;
  }

  applyTheme(appState.theme);

  const audioUrl = (appState.audio && appState.audio.url) || '';
  setLoaderPhase('audio');
  setLoaderTexts('Настраиваем финальный ритм', 'Готовим музыку, переходы и последние кадры');

  populateScene(appState);

  initAudio(appState.audio || { url: '', volume: 0.85 });
}

// Wire manual start button
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init);
} else {
  init();
}

// Expose handleManualStart globally for the onclick attribute
window.handleManualStart = handleManualStart;
