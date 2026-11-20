'use strict';
/* yeet front end — no framework, no build step.
   Sections: utilities · appearance · modals · uploader · upload page · file list ·
   clipboard · share page (download landing) · folder page · clipboard page. */

const CFG = window.YEET_CONFIG || {};
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

// ── Utilities ─────────────────────────────────────────────────────────────────
function esc(s) {
  return String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}
function icon(name, cls = '') { return `<svg class="icon ${cls}"><use href="#i-${name}"/></svg>`; }

function fmtSize(b) {
  b = Number(b) || 0;
  const u = ['B', 'KB', 'MB', 'GB', 'TB'];
  let i = 0;
  while (b >= 1024 && i < u.length - 1) { b /= 1024; i++; }
  return (b >= 10 || i === 0 ? Math.round(b) : b.toFixed(1)) + ' ' + u[i];
}
function fmtDuration(s) {
  if (!isFinite(s) || s < 0) return '';
  if (s < 60) return Math.ceil(s) + 's';
  if (s < 3600) return Math.floor(s / 60) + 'm ' + String(Math.floor(s % 60)).padStart(2, '0') + 's';
  return Math.floor(s / 3600) + 'h ' + Math.floor((s % 3600) / 60) + 'm';
}
function fmtExpiry(iso) {
  const ms = new Date(iso).getTime() - Date.now();
  if (ms <= 0) return { label: 'expired', cls: 'exp-urgent' };
  const h = ms / 3_600_000;
  if (h >= 48) return { label: `${Math.floor(h / 24)}d left`, cls: '' };
  if (h >= 6) return { label: `${Math.floor(h)}h left`, cls: '' };
  if (h >= 1) return { label: `${Math.floor(h)}h left`, cls: 'exp-warn' };
  return { label: `${Math.max(1, Math.floor(ms / 60_000))}m left`, cls: 'exp-urgent' };
}
function timeAgo(iso) {
  // SQLite's datetime('now') has no zone; it is UTC.
  const t = new Date(/Z|[+-]\d\d:?\d\d$/.test(iso) ? iso : iso.replace(' ', 'T') + 'Z').getTime();
  const m = Math.floor((Date.now() - t) / 60_000);
  if (m < 1) return 'just now';
  if (m < 60) return m + 'm ago';
  const h = Math.floor(m / 60);
  return h < 24 ? h + 'h ago' : Math.floor(h / 24) + 'd ago';
}
const sleep = ms => new Promise(r => setTimeout(r, ms));
const safeJson = t => { try { return JSON.parse(t); } catch { return {}; } };
const store = {
  get(k, d = null) { try { const v = localStorage.getItem(k); return v === null ? d : v; } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* private mode */ } },
  del(k) { try { localStorage.removeItem(k); } catch { /* ignore */ } },
};

function getSession() {
  let s = store.get('yeet_session');
  if (!s) {
    const b = crypto.getRandomValues(new Uint8Array(16));
    s = Array.from(b, x => x.toString(16).padStart(2, '0')).join('');
    store.set('yeet_session', s);
  }
  return s;
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    // iOS / http / lost user-activation: fall back to a hidden textarea.
    const ta = document.createElement('textarea');
    ta.value = text; ta.setAttribute('readonly', '');
    ta.style.cssText = 'position:fixed;top:-1000px;opacity:0';
    document.body.appendChild(ta); ta.select();
    let ok = false;
    try { ok = document.execCommand('copy'); } catch { /* ignore */ }
    ta.remove();
    return ok;
  }
}
async function copyWithToast(text, label = 'Link copied') {
  if (await copyText(text)) toast(label, 'success');
  else toast('Copy failed — long-press the link to copy it', 'warn');
}
const canShare = () => typeof navigator.share === 'function' && matchMedia('(pointer: coarse)').matches;

// ── Toasts ────────────────────────────────────────────────────────────────────
const TOAST_ICON = { success: 'check', error: 'alert', warn: 'alert', info: 'info' };
function toast(msg, type = 'info', ms = 4500) {
  const box = $('#toasts');
  if (!box) return;
  const el = document.createElement('div');
  el.className = `toast ${type}`;
  el.innerHTML = `${icon(TOAST_ICON[type] || 'info')}<span>${esc(msg)}</span>`;
  const kill = () => { el.classList.add('out'); setTimeout(() => el.remove(), 220); };
  el.addEventListener('click', kill);
  box.appendChild(el);
  setTimeout(kill, type === 'error' ? Math.max(ms, 7000) : ms);
}

// ── Appearance ────────────────────────────────────────────────────────────────
const ACCENTS = { emerald: '#2f9e74', sky: '#3b82c4', amber: '#c98a2b', rose: '#c8566e', slate: '#64748b' };
const LEGACY_ACCENT = { cyan: 'sky', blue: 'sky', purple: 'sky', green: 'emerald', teal: 'emerald',
  pink: 'rose', red: 'rose', orange: 'amber', yellow: 'amber', mono: 'slate' };
let theme = store.get('yeet_theme', 'auto');
let accent = store.get('yeet_accent', 'emerald');
accent = LEGACY_ACCENT[accent] || (ACCENTS[accent] ? accent : 'emerald');
const lightMql = matchMedia('(prefers-color-scheme: light)');

function applyAppearance() {
  const eff = theme === 'auto' ? (lightMql.matches ? 'light' : 'dark') : theme;
  document.documentElement.setAttribute('data-theme', eff);
  document.documentElement.style.setProperty('--accent', ACCENTS[accent]);
  $$('[data-theme-opt]').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.themeOpt === theme)));
  $$('[data-accent]').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.accent === accent)));
}
lightMql.addEventListener?.('change', () => theme === 'auto' && applyAppearance());
$('#theme-row')?.addEventListener('click', e => {
  const b = e.target.closest('[data-theme-opt]');
  if (!b) return;
  theme = b.dataset.themeOpt; store.set('yeet_theme', theme); applyAppearance();
});
$('#accent-swatches')?.addEventListener('click', e => {
  const b = e.target.closest('[data-accent]');
  if (!b) return;
  accent = b.dataset.accent; store.set('yeet_accent', accent); applyAppearance();
});
applyAppearance();

const header = $('#site-header');
if (header) {
  const onScroll = () => header.classList.toggle('scrolled', scrollY > 4);
  addEventListener('scroll', onScroll, { passive: true }); onScroll();
}

// ── Modals ────────────────────────────────────────────────────────────────────
let lastFocus = null;
function openModal(m) {
  if (!m) return;
  lastFocus = document.activeElement;
  m.classList.remove('hidden');
  document.body.style.overflow = 'hidden';
  setTimeout(() => (m.querySelector('[autofocus], input, textarea, button:not([data-close])') || m).focus?.(), 50);
}
function closeModal(m) {
  if (!m || m.classList.contains('hidden')) return;
  m.classList.add('hidden');
  if (m.dataset.temp) m.remove();
  if (!$$('.modal').some(x => !x.classList.contains('hidden'))) document.body.style.overflow = '';
  lastFocus?.focus?.();
}
document.addEventListener('click', e => {
  const close = e.target.closest('[data-close]');
  if (close) { closeModal(close.closest('.modal')); return; }
  if (e.target.classList?.contains('modal')) closeModal(e.target);
});
document.addEventListener('keydown', e => {
  const typing = ['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement?.tagName);
  if (e.key === 'Escape') $$('.modal').filter(m => !m.classList.contains('hidden')).forEach(closeModal);
  if (e.key === '?' && !typing && !e.metaKey && !e.ctrlKey) { e.preventDefault(); openModal($('#help-modal')); }
});
$('#settings-btn')?.addEventListener('click', () => openModal($('#settings-modal')));
$('#help-btn')?.addEventListener('click', () => openModal($('#help-modal')));
$$('[data-tab]').forEach(tab => tab.addEventListener('click', () => {
  $$('[data-tab]').forEach(t => t.setAttribute('aria-selected', String(t === tab)));
  $$('[data-pane]').forEach(p => p.classList.toggle('active', p.dataset.pane === tab.dataset.tab));
}));

function tempModal(title, bodyHtml, cls = '') {
  const m = document.createElement('div');
  m.className = 'modal'; m.dataset.temp = '1';
  m.setAttribute('role', 'dialog'); m.setAttribute('aria-modal', 'true');
  m.innerHTML = `<div class="modal-box ${cls}"><div class="modal-head">
      <span class="modal-title">${esc(title)}</span>${'<span data-slot="head"></span>'}
      <button class="icon-btn" data-close type="button" aria-label="Close">${icon('x')}</button>
    </div>${bodyHtml}</div>`;
  document.body.appendChild(m);
  openModal(m);
  return m;
}

// Format any [data-expires] timestamps in the viewer's local time.
$$('[data-expires]').forEach(el => {
  const d = new Date(el.dataset.expires);
  if (!isNaN(d)) el.textContent = d.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' });
});

// ── HTTP helpers ──────────────────────────────────────────────────────────────
class HttpError extends Error {
  constructor(status, body) {
    super(body?.error || httpMessage(status));
    this.status = status; this.body = body;
  }
}
function httpMessage(status) {
  if (status === 0) return 'Network error — check your connection.';
  if (status === 413) return 'Too large for the server or a proxy in front of it.';
  if (status === 429) return 'Rate limited — wait a bit and try again.';
  if (status === 502 || status === 504 || status === 524) return 'The server took too long to answer.';
  if (status === 507) return 'The server is out of space.';
  return `Server error (${status}).`;
}
async function api(method, url, body) {
  let r;
  try {
    r = await fetch(url, {
      method,
      headers: { 'Content-Type': 'application/json', 'X-Session-ID': getSession() },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new HttpError(0, {});
  }
  const data = safeJson(await r.text());
  if (!r.ok && r.status !== 202) throw new HttpError(r.status, data);
  return { status: r.status, data };
}

// ── Uploader (chunked, resumable) ─────────────────────────────────────────────
// Each file is split into server-chosen chunks (16 MB by default) and sent with
// PUT, up to PARALLEL at a time. Every chunk retries with backoff, waits out
// offline periods, and an interrupted upload can resume after a reload because
// the upload id is remembered per file fingerprint.
const PARALLEL_CHUNKS = 3;
const PARALLEL_FILES = 2;
const MAX_TRIES = 8;
const RESUME_KEY = 'yeet_resume';

function resumeMap() { return safeJson(store.get(RESUME_KEY, '{}')) || {}; }
function rememberUpload(fp, id) {
  const m = resumeMap();
  m[fp] = { id, t: Date.now() };
  for (const [k, v] of Object.entries(m)) if (Date.now() - v.t > 6 * 3600_000) delete m[k];
  store.set(RESUME_KEY, JSON.stringify(m));
}
function forgetUpload(fp) { const m = resumeMap(); delete m[fp]; store.set(RESUME_KEY, JSON.stringify(m)); }
const fingerprint = (f, extra = '') => [f.name, f.size, f.lastModified, extra].join('|');

function waitOnline() {
  if (navigator.onLine !== false) return Promise.resolve();
  return new Promise(res => addEventListener('online', res, { once: true }));
}

function putChunk(url, blob, onProgress, signal) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('PUT', url);
    xhr.setRequestHeader('Content-Type', 'application/octet-stream');
    xhr.upload.onprogress = e => onProgress(e.loaded);
    xhr.onload = () => xhr.status >= 200 && xhr.status < 300
      ? resolve() : reject(new HttpError(xhr.status, safeJson(xhr.responseText)));
    xhr.onerror = () => reject(new HttpError(0, {}));
    xhr.ontimeout = () => reject(new HttpError(0, {}));
    xhr.timeout = 10 * 60_000;
    if (signal) {
      if (signal.aborted) { reject(new DOMException('aborted', 'AbortError')); return; }
      signal.addEventListener('abort', () => { xhr.abort(); reject(new DOMException('aborted', 'AbortError')); });
    }
    xhr.send(blob);
  });
}

// Transient: network drop, proxy timeout, 5xx (but not 507 "disk full").
const TRANSIENT = s => s === 0 || s === 408 || (s >= 500 && s !== 507);
// A chunk PUT may also see 400 (body cut off mid-flight) or 429 — just resend.
const CHUNK_RETRY = s => TRANSIENT(s) || s === 400 || s === 429;

async function withRetry(fn, signal, onWait, retryable = TRANSIENT) {
  for (let attempt = 1; ; attempt++) {
    try {
      return await fn();
    } catch (e) {
      if (e.name === 'AbortError' || signal?.aborted) throw e;
      if (!(e instanceof HttpError) || !retryable(e.status) || attempt >= MAX_TRIES) throw e;
      onWait?.(attempt, e);
      await waitOnline();
      await sleep(Math.min(30_000, 1000 * 2 ** (attempt - 1)) * (0.75 + Math.random() / 2));
    }
  }
}

/** Upload one File. `opts` is the init payload; `hooks.progress(bytesDone)`. Returns server result. */
async function uploadOne(file, opts, hooks, signal) {
  const fp = fingerprint(file, opts.bundle_id || '');
  let up = null;
  let received = new Set();

  const saved = resumeMap()[fp];
  if (saved && !opts.bundle_id) {
    try {
      const { data } = await api('GET', `/api/uploads/${saved.id}`);
      if (data.status === 'uploading' && data.size === file.size) {
        up = data; received = new Set(data.received || []);
        hooks.note?.('Resuming earlier upload…');
      } else if (data.status === 'done' && data.result) {
        forgetUpload(fp); return data.result;
      }
    } catch { forgetUpload(fp); }
  }
  if (!up) {
    const { data } = await withRetry(() => api('POST', '/api/uploads', { name: file.name, size: file.size, ...opts }), signal);
    up = data;
    if (!opts.bundle_id && file.size > up.chunk_size) rememberUpload(fp, up.id);
  }

  const cs = up.chunk_size;
  const inflight = new Map();
  let doneBytes = 0;
  for (const i of received) doneBytes += Math.min(cs, file.size - i * cs);
  const report = () => hooks.progress(doneBytes + [...inflight.values()].reduce((a, b) => a + b, 0));
  report();

  const sendChunks = async indexes => {
    const queue = [...indexes];
    const worker = async () => {
      while (queue.length) {
        const i = queue.shift();
        const blob = file.slice(i * cs, Math.min(file.size, (i + 1) * cs));
        await withRetry(() => putChunk(`/api/uploads/${up.id}/chunks/${i}`, blob,
          loaded => { inflight.set(i, loaded); report(); }, signal),
          signal, (n, e) => { inflight.set(i, 0); hooks.note?.(`Connection hiccup — retrying (${n})…`, e); },
          CHUNK_RETRY);
        inflight.delete(i); doneBytes += blob.size; received.add(i); report();
        hooks.note?.('');
      }
    };
    await Promise.all(Array.from({ length: Math.min(PARALLEL_CHUNKS, queue.length) }, worker));
  };

  const all = Array.from({ length: up.chunk_count }, (_, i) => i);
  await sendChunks(all.filter(i => !received.has(i)));

  hooks.processing?.();
  for (let round = 0; round < 3; round++) {
    let res;
    try {
      res = await withRetry(() => api('POST', `/api/uploads/${up.id}/complete`), signal);
    } catch (e) {
      if (e.status === 409 && e.body?.missing) { await sendChunks(e.body.missing); continue; }
      if (e.status && e.status !== 404) forgetUpload(fp);
      throw e;
    }
    if (res.status === 202) {
      // Scan still running server-side — poll. Survives proxy timeouts, and
      // the server keeps going if this page is closed.
      const result = await pollUpload(up.id, hooks, signal);
      if (result === 'restart') continue;
      forgetUpload(fp);
      return result;
    }
    forgetUpload(fp);
    markSeen(up.id);
    return res.data;
  }
  throw new HttpError(409, { error: 'Upload could not be completed. Please try again.' });
}

/** Poll a finalizing upload until done. Returns the result, or 'restart' when
 *  the server lost the task (restart) and `complete` must be called again. */
async function pollUpload(id, hooks, signal) {
  for (;;) {
    const { data } = await withRetry(() => api('GET', `/api/uploads/${id}`), signal);
    if (data.status === 'done' || data.status === 'failed') {
      markSeen(id);
      if (data.code !== 201) throw new HttpError(data.code, data.result);
      return data.result;
    }
    if (data.status === 'uploading') return 'restart';
    hooks.scan?.(data.phase, data.progress);
    await sleep(1200);
  }
}

// Uploads whose outcome this browser has already shown, so a finished upload
// is announced once — not on every later visit.
const SEEN_KEY = 'yeet_seen_uploads';
function seenSet() { return new Set(safeJson(store.get(SEEN_KEY, '[]')) || []); }
function markSeen(id) {
  const ids = [...seenSet(), id].slice(-100);
  store.set(SEEN_KEY, JSON.stringify(ids));
}

const SCAN_PHASE = {
  queued: 'Waiting for the scanner…',
  checking: 'Checking the file…',
  starting: 'Starting the virus scanner — about a minute after a quiet period…',
  scanning: 'Scanning for viruses',
  verifying: 'Finishing the virus scan…',
  finishing: 'Saving…',
};

// ── Upload page ───────────────────────────────────────────────────────────────
const dropZone = $('#drop-zone');

function initUploadPage() {
  const fileInput = $('#file-input');
  const photoInput = $('#photo-input');
  const folderInput = $('#folder-input');
  const pwInput = $('#dz-password');
  const burnInput = $('#dz-burn');
  const queueWrap = $('#queue-wrap');
  const queueEl = $('#queue');

  // Expiry
  let expiry = parseInt(store.get('yeet_expiry', String(CFG.expiryHours || 24)), 10);
  const chips = $$('#expiry-chips [data-hours]');
  if (!chips.some(c => +c.dataset.hours === expiry)) expiry = +(chips.find(c => +c.dataset.hours === (CFG.expiryHours || 24)) || chips[1]).dataset.hours;
  const syncChips = () => chips.forEach(c => c.setAttribute('aria-pressed', String(+c.dataset.hours === expiry)));
  chips.forEach(c => c.addEventListener('click', () => { expiry = +c.dataset.hours; store.set('yeet_expiry', String(expiry)); syncChips(); }));
  syncChips();

  // Password helpers
  $('#dz-show-pw')?.addEventListener('click', () => { pwInput.type = pwInput.type === 'password' ? 'text' : 'password'; });
  $('#dz-gen-pw')?.addEventListener('click', async () => {
    const chars = 'abcdefghjkmnpqrstuvwxyzABCDEFGHJKMNPQRSTUVWXYZ23456789';
    pwInput.value = Array.from(crypto.getRandomValues(new Uint8Array(12)), b => chars[b % chars.length]).join('');
    pwInput.type = 'text';
    if (await copyText(pwInput.value)) toast('Password generated and copied — send it separately from the link', 'success');
  });

  const options = () => ({
    password: pwInput?.value || '',
    expiry_hours: expiry,
    burn: burnInput?.checked ? 1 : 0,
    session_id: getSession(),
  });

  // Spotlight that follows the cursor across the drop zone.
  let spotFrame = 0;
  dropZone.addEventListener('pointermove', e => {
    if (e.pointerType !== 'mouse' || spotFrame) return;
    spotFrame = requestAnimationFrame(() => {
      const r = dropZone.getBoundingClientRect();
      dropZone.style.setProperty('--mx', `${e.clientX - r.left}px`);
      dropZone.style.setProperty('--my', `${e.clientY - r.top}px`);
      spotFrame = 0;
    });
  });

  // Pickers
  const pick = input => input?.click();
  dropZone.addEventListener('click', e => { if (!e.target.closest('.dz-action')) pick(fileInput); });
  dropZone.addEventListener('keydown', e => {
    if ((e.key === 'Enter' || e.key === ' ') && e.target === dropZone) { e.preventDefault(); pick(fileInput); }
  });
  $('#quick-files')?.addEventListener('click', () => pick(fileInput));
  $('#quick-photo')?.addEventListener('click', () => pick(photoInput));
  $('#quick-folder')?.addEventListener('click', () => pick(folderInput));
  $('#quick-paste')?.addEventListener('click', openPasteModal);

  for (const input of [fileInput, photoInput]) {
    input?.addEventListener('change', () => {
      const files = Array.from(input.files || []);
      input.value = '';
      enqueueFiles(files);
    });
  }
  folderInput?.addEventListener('change', () => {
    const files = Array.from(folderInput.files || []);
    folderInput.value = '';
    if (!files.length) return;
    const items = files.map(f => ({ file: f, path: f.webkitRelativePath || f.name }));
    enqueueFolder(items, items[0].path.split('/')[0] || 'Folder');
  });

  // Drag & drop (whole page accepts drops so near-misses don't open the file in the tab)
  let dragDepth = 0;
  addEventListener('dragenter', e => { if (hasFiles(e)) { dragDepth++; dropZone.classList.add('drag-over'); } });
  addEventListener('dragleave', () => { if (--dragDepth <= 0) { dragDepth = 0; dropZone.classList.remove('drag-over'); } });
  addEventListener('dragover', e => { if (hasFiles(e)) e.preventDefault(); });
  addEventListener('drop', async e => {
    if (!hasFiles(e)) return;
    e.preventDefault(); dragDepth = 0; dropZone.classList.remove('drag-over');
    const loose = [];
    const folders = [];
    for (const item of Array.from(e.dataTransfer.items || [])) {
      if (item.kind !== 'file') continue;
      const entry = item.webkitGetAsEntry?.();
      if (entry?.isDirectory) folders.push(entry);
      else { const f = item.getAsFile(); if (f) loose.push(f); }
    }
    if (!e.dataTransfer.items) loose.push(...Array.from(e.dataTransfer.files));
    for (const entry of folders) {
      const items = await walkDirectory(entry);
      if (items.length) enqueueFolder(items, entry.name);
      else toast(`"${entry.name}" is empty`, 'warn');
    }
    enqueueFiles(loose);
  });
  function hasFiles(e) { return Array.from(e.dataTransfer?.types || []).includes('Files'); }

  // Paste anywhere
  document.addEventListener('paste', async e => {
    const a = document.activeElement;
    if (a && ['INPUT', 'TEXTAREA'].includes(a.tagName)) return;
    const items = Array.from(e.clipboardData?.items || []);
    const files = items.filter(i => i.kind === 'file').map(i => i.getAsFile()).filter(Boolean);
    if (files.length) {
      e.preventDefault();
      const img = files.length === 1 && files[0].type.startsWith('image/') ? files[0] : null;
      if (img && !e.shiftKey && CLIP_IMAGE_TYPES.includes(img.type) && img.size <= CLIP_IMAGE_MAX) {
        await saveClipboardImage(img);
      } else {
        enqueueFiles(files.map((f, i) => f.name && f.name !== 'image.png' ? f
          : new File([f], `pasted-${new Date().toISOString().slice(0, 19).replace(/[T:]/g, '-')}${i ? '-' + i : ''}.${(f.type.split('/')[1] || 'bin').replace('jpeg', 'jpg')}`, { type: f.type })));
      }
      return;
    }
    const txt = items.find(i => i.kind === 'string' && i.type === 'text/plain');
    if (txt) {
      e.preventDefault();
      const text = await new Promise(r => txt.getAsString(r));
      if (text.trim()) await saveClipboardText(text);
    }
  });

  // ── Queue ──────────────────────────────────────────────────────────────────
  const jobs = [];
  let running = 0;
  let wakeLock = null;

  function enqueueFiles(files) {
    if (!files.length) return;
    if (CFG.storageBlocked) { toast('Uploads are paused — the server is out of space.', 'error'); return; }
    const opts = options();
    for (const f of files) {
      if (CFG.maxFileBytes && f.size > CFG.maxFileBytes) {
        addJob({ name: f.name, size: f.size, kind: 'file' }).fail(`Too large — the limit is ${fmtSize(CFG.maxFileBytes)} per file.`);
        continue;
      }
      for (const j of jobs.filter(j => j.state === 'paused' && j.name === f.name && j.size === f.size)) {
        j.state = 'cancelled'; j.el.remove();
        // Resumable only if this browser remembered the upload id; otherwise
        // the new upload replaces it, so drop the stale server-side one.
        const resumable = Object.values(resumeMap()).some(v => v.id === j.serverId);
        if (!resumable && j.serverId) api('DELETE', `/api/uploads/${j.serverId}`).catch(() => {});
      }
      const job = addJob({ name: f.name, size: f.size, kind: 'file', file: f, opts });
      jobs.push(job);
    }
    pump();
  }

  function enqueueFolder(items, name) {
    if (CFG.storageBlocked) { toast('Uploads are paused — the server is out of space.', 'error'); return; }
    const total = items.reduce((s, it) => s + it.file.size, 0);
    const tooBig = items.filter(it => CFG.maxFileBytes && it.file.size > CFG.maxFileBytes);
    if (tooBig.length) toast(`${tooBig.length} file(s) over ${fmtSize(CFG.maxFileBytes)} will be skipped`, 'warn');
    const opts = options();
    if (opts.burn) toast('Single download does not apply to folders — uploading normally', 'info');
    const job = addJob({ name, size: total, kind: 'folder', items: items.filter(it => !tooBig.includes(it)), opts });
    jobs.push(job);
    pump();
  }

  function pump() {
    while (running < PARALLEL_FILES) {
      const next = jobs.find(j => j.state === 'queued');
      if (!next) break;
      running++;
      next.state = 'running';
      lockScreen();
      next.run().finally(() => { running--; pump(); if (!running) releaseScreen(); });
    }
  }

  async function lockScreen() {
    try { if (!wakeLock && navigator.wakeLock) wakeLock = await navigator.wakeLock.request('screen'); } catch { /* not allowed */ }
  }
  function releaseScreen() { wakeLock?.release?.().catch(() => {}); wakeLock = null; }
  document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible' && running) lockScreen(); });
  // Only warn while bytes are still leaving this device. Once an upload is
  // being scanned it finishes on the server whether or not the page is open.
  addEventListener('beforeunload', e => {
    if (jobs.some(j => j.sending)) { e.preventDefault(); e.returnValue = ''; }
  });

  function addJob(spec, quiet = false) {
    queueWrap.classList.remove('hidden');
    // On phones the queue sits below the fold — bring it into view so the
    // upload visibly starts.
    if (!quiet) requestAnimationFrame(() => {
      const r = queueWrap.getBoundingClientRect();
      if (r.top > innerHeight - 80 || r.bottom < 0) queueWrap.scrollIntoView({ behavior: 'smooth', block: 'start' });
    });
    const el = document.createElement('div');
    el.className = 'job';
    el.innerHTML = `<span class="job-icon">${icon(spec.kind === 'folder' ? 'folder' : kindIcon(spec.name))}</span>
      <div class="job-main">
        <div class="job-top"><span class="job-name">${esc(spec.name)}</span><span class="job-stat">${fmtSize(spec.size)}</span></div>
        <div class="bar"><i></i></div>
        <div class="job-msg">Waiting…</div>
      </div>
      <div class="job-actions"><button class="icon-btn" type="button" data-cancel aria-label="Cancel">${icon('x')}</button></div>`;
    queueEl.prepend(el);
    const fill = $('.bar > i', el), stat = $('.job-stat', el), msg = $('.job-msg', el), actions = $('.job-actions', el);
    const ctrl = new AbortController();
    let lastT = performance.now(), lastB = 0, speed = 0;

    const job = {
      ...spec, el, state: 'queued', result: null,
      progress(bytes) {
        job.sending = true;
        const pct = spec.size ? Math.min(100, bytes / spec.size * 100) : 100;
        fill.style.width = pct.toFixed(1) + '%';
        const now = performance.now();
        if (now - lastT > 700) {
          const inst = (bytes - lastB) / ((now - lastT) / 1000);
          speed = speed ? speed * 0.7 + inst * 0.3 : inst;
          lastT = now; lastB = bytes;
        }
        const eta = speed > 0 ? (spec.size - bytes) / speed : NaN;
        stat.textContent = `${Math.floor(pct)}%` + (speed > 1024 ? ` · ${fmtSize(speed)}/s` : '') + (eta > 1 ? ` · ${fmtDuration(eta)}` : '');
        if (!msg.dataset.sticky) msg.textContent = `${fmtSize(bytes)} of ${fmtSize(spec.size)}`;
      },
      note(text) {
        if (text) { msg.textContent = text; msg.dataset.sticky = '1'; } else delete msg.dataset.sticky;
      },
      processing() {
        job.sending = false;
        el.classList.add('processing', 'indeterminate');
        stat.textContent = fmtSize(spec.size);
        msg.innerHTML = `${esc(SCAN_PHASE.queued)} <span class="muted">You can leave this page — it finishes on the server.</span>`;
        msg.dataset.sticky = '1';
      },
      scan(phase, fraction) {
        if (!el.classList.contains('processing')) job.processing();
        const pct = phase === 'scanning' ? Math.floor((fraction || 0) * 100) : null;
        el.classList.toggle('indeterminate', pct === null);
        if (pct !== null) fill.style.width = pct + '%';
        stat.textContent = pct !== null ? `${pct}% scanned` : fmtSize(spec.size);
        msg.innerHTML = `${esc(SCAN_PHASE[phase] || SCAN_PHASE.queued)} <span class="muted">You can leave this page — it finishes on the server.</span>`;
      },
      paused(pct) {
        job.state = 'paused';
        fill.style.width = pct + '%';
        stat.textContent = `${pct}%`;
        msg.textContent = 'Interrupted — choose the same file again to continue where it stopped.';
        msg.dataset.sticky = '1';
        actions.innerHTML = `<button class="btn btn-quiet" type="button" data-repick>${icon('files')}<span class="only-pointer">Choose file</span></button>
          <button class="icon-btn" type="button" data-dismiss aria-label="Discard">${icon('x')}</button>`;
      },
      fail(text) {
        job.state = 'failed'; job.sending = false;
        el.classList.remove('processing', 'indeterminate'); el.classList.add('failed');
        msg.textContent = text; delete msg.dataset.sticky;
        stat.textContent = fmtSize(spec.size);
        actions.innerHTML = (spec.file || spec.items)
          ? `<button class="icon-btn" type="button" data-retry aria-label="Retry">${icon('refresh')}</button>
             <button class="icon-btn" type="button" data-dismiss aria-label="Dismiss">${icon('x')}</button>`
          : `<button class="icon-btn" type="button" data-dismiss aria-label="Dismiss">${icon('x')}</button>`;
        updateQueueTools();
        return job;
      },
      succeed(res, label) {
        job.state = 'done'; job.result = res; job.sending = false;
        el.classList.remove('processing', 'indeterminate'); el.classList.add('done');
        fill.style.width = '100%';
        stat.textContent = fmtSize(res.size ?? res.total_size ?? spec.size);
        delete msg.dataset.sticky;
        msg.innerHTML = `<a class="job-link" href="${esc(res.url)}" target="_blank" rel="noopener">${icon('check')}<span>${esc(res.url.replace(/^https?:\/\//, ''))}</span></a>`;
        actions.innerHTML = `
          ${canShare() ? `<button class="icon-btn" type="button" data-share aria-label="Share">${icon('share')}</button>` : ''}
          <button class="icon-btn" type="button" data-copy aria-label="Copy link">${icon('copy')}</button>`;
        if (label) toast(label, 'success');
        if (res.warning) toast(res.warning, 'warn', 8000);
        if (res.advisory) toast(res.advisory, 'info', 8000);
        updateQueueTools();
        loadFiles();
      },
      async run() {
        el.classList.remove('failed', 'done');
        try {
          if (spec.restoredId) {
            const res = await pollUpload(spec.restoredId, job, ctrl.signal);
            if (res === 'restart') {
              await api('POST', `/api/uploads/${spec.restoredId}/complete`).catch(() => {});
              job.state = 'queued'; return;
            }
            job.succeed(res, 'Finished — ' + spec.name);
          } else if (spec.kind === 'folder') await runFolder(job, ctrl.signal);
          else {
            const res = await uploadOne(spec.file, spec.opts, job, ctrl.signal);
            const copied = await copyText(res.url);
            job.succeed(res, copied ? 'Uploaded — link copied' : 'Uploaded');
          }
        } catch (e) {
          if (e.name === 'AbortError') return;
          job.sending = false;
          job.fail(e.message || 'Upload failed');
          if (e.status === 507 || e.status === 429) toast(e.message, 'error');
        }
      },
    };

    el.addEventListener('click', async e => {
      if (e.target.closest('[data-cancel]')) {
        ctrl.abort(); job.state = 'cancelled'; job.sending = false; el.remove(); updateQueueTools();
      } else if (e.target.closest('[data-dismiss]')) {
        if (spec.serverId) api('DELETE', `/api/uploads/${spec.serverId}`).catch(() => {});
        el.remove(); job.state = 'cancelled'; updateQueueTools();
      } else if (e.target.closest('[data-repick]')) {
        fileInput.click();
      } else if (e.target.closest('[data-retry]')) {
        actions.innerHTML = `<button class="icon-btn" type="button" data-cancel aria-label="Cancel">${icon('x')}</button>`;
        msg.textContent = 'Retrying…'; job.state = 'queued'; pump();
      } else if (e.target.closest('[data-copy]')) {
        copyWithToast(job.result.url);
      } else if (e.target.closest('[data-share]')) {
        navigator.share({ title: spec.name, url: job.result.url }).catch(() => {});
      }
    });
    return job;
  }

  async function runFolder(job, signal) {
    const { items, opts } = job;
    if (!items.length) throw new Error('Nothing to upload.');
    const { data: bundle } = await withRetry(() => api('POST', '/api/bundles', {
      name: job.name, file_count: items.length,
      total_size: items.reduce((s, it) => s + it.file.size, 0),
      password: opts.password, expiry_hours: opts.expiry_hours, session_id: opts.session_id,
    }), signal);

    let base = 0;
    const skipped = [];
    for (const [n, it] of items.entries()) {
      job.note(`File ${n + 1} of ${items.length}: ${it.path}`);
      try {
        await uploadOne(it.file, { bundle_id: bundle.id, bundle_token: bundle.token, path: it.path },
          { progress: b => job.progress(base + b) }, signal);
      } catch (e) {
        if (e.name === 'AbortError') throw e;
        if (e.status === 507 || e.status === 429 || e.status === 0) throw e;
        skipped.push(`${it.path}: ${e.message}`);
      }
      base += it.file.size;
    }
    job.processing();
    const { data } = await withRetry(() => api('POST', `/api/bundles/${bundle.id}/complete`, { bundle_token: bundle.token }), signal);
    const copied = await copyText(data.url);
    job.succeed(data, `Folder uploaded (${data.file_count} files)` + (copied ? ' — link copied' : ''));
    if (skipped.length) toast(`${skipped.length} file(s) skipped — ${skipped[0]}`, 'warn', 10000);
  }

  function updateQueueTools() {
    const done = $$('.job.done', queueEl).length;
    $('#copy-all').hidden = done < 2;
    if (!queueEl.children.length) queueWrap.classList.add('hidden');
  }
  $('#clear-done')?.addEventListener('click', () => {
    $$('.job.done, .job.failed', queueEl).forEach(el => el.remove());
    updateQueueTools();
  });
  $('#copy-all')?.addEventListener('click', () => {
    const urls = jobs.filter(j => j.state === 'done' && j.el.isConnected).map(j => j.result.url);
    copyWithToast(urls.join('\n'), `${urls.length} links copied`);
  });

  // ── Pick up this browser's uploads after a reload or a later visit ────────
  async function restoreUploads() {
    let list;
    try { list = (await api('GET', '/api/uploads')).data.uploads || []; } catch { return; }
    const seen = seenSet();
    for (const u of list) {
      if (jobs.some(j => j.restoredId === u.id || j.serverId === u.id)) continue;
      if (u.status === 'processing') {
        const job = addJob({ name: u.name, size: u.size, kind: 'file', restoredId: u.id, serverId: u.id }, true);
        jobs.push(job);
        job.scan(u.phase, u.progress);
        pump();
      } else if (u.status === 'uploading') {
        const job = addJob({ name: u.name, size: u.size, kind: 'file', serverId: u.id }, true);
        const pct = u.chunk_count ? Math.floor((u.received_count || 0) / u.chunk_count * 100) : 0;
        jobs.push(job);
        job.paused(pct);
      } else if (!seen.has(u.id)) {
        const job = addJob({ name: u.name, size: u.size, kind: 'file', serverId: u.id }, true);
        jobs.push(job);
        if (u.code === 201) job.succeed(u.result, null);
        else job.fail((u.result && u.result.error) || 'Upload failed');
        markSeen(u.id);
      }
    }
  }

  initFileList();
  initClipboard();
  initScannerLog();
  initUnlock();
  restoreUploads();
}

// ── Unlock password (bigger limits for this browser) ──────────────────────────
function initUnlock() {
  const box = $('#tier-box');
  if (!box) return;
  const modal = $('#unlock-modal'), form = $('#unlock-form'), input = $('#unlock-input');
  const err = $('#unlock-error'), submit = $('#unlock-submit');

  const showLimit = bytes => {
    CFG.maxFileBytes = bytes;
    for (const id of ['limit-file', 'intro-limit']) { const el = $('#' + id); if (el) el.textContent = fmtSize(bytes); }
  };
  const renderUnlocked = tier => {
    box.dataset.tier = tier;
    const note = tier === 'admin' ? ' Admin: virus scan skipped.' : '';
    box.innerHTML = `<span class="tier-badge">${icon('key')}Unlocked</span>
      <span class="tier-text">Bigger uploads are on for this browser.${note}</span>
      <button class="btn btn-quiet btn-sm" type="button" id="lock-btn">Lock</button>`;
  };

  box.addEventListener('click', async e => {
    if (e.target.closest('#unlock-open')) {
      input.value = ''; err.textContent = '';
      openModal(modal);
    } else if (e.target.closest('#lock-btn')) {
      try { await api('POST', '/api/lock'); } catch { /* cookie may already be gone */ }
      toast('Locked — standard limits again', 'info');
      setTimeout(() => location.reload(), 600);
    }
  });

  form?.addEventListener('submit', async e => {
    e.preventDefault();
    err.textContent = '';
    submit.disabled = true;
    try {
      const { data } = await api('POST', '/api/unlock', { password: input.value });
      closeModal(modal);
      renderUnlocked(data.tier);
      showLimit(data.max_file_bytes);
      box.classList.add('celebrate');
      toast(`Unlocked — up to ${fmtSize(data.max_file_bytes)} per file`, 'success');
    } catch (ex) {
      err.textContent = ex.message || 'Could not unlock.';
      input.select();
    } finally {
      submit.disabled = false;
    }
  });
}

async function walkDirectory(root) {
  const out = [];
  const readAll = reader => new Promise(res => {
    const all = [];
    const step = () => reader.readEntries(batch => { if (!batch.length) res(all); else { all.push(...batch); step(); } }, () => res(all));
    step();
  });
  async function walk(entry, path) {
    if (entry.isFile) {
      const f = await new Promise(res => entry.file(res, () => res(null)));
      if (f) out.push({ file: f, path });
    } else if (entry.isDirectory) {
      for (const child of await readAll(entry.createReader())) await walk(child, `${path}/${child.name}`);
    }
  }
  await walk(root, root.name);
  return out;
}

// ── File list ─────────────────────────────────────────────────────────────────
let allFiles = [];
let allBundles = [];
let scope = store.get('yeet_scope', 'mine');
let loadFiles = () => {};

function kindOf(name, mime = '') {
  const n = (name || '').toLowerCase();
  if (mime.startsWith('image/') || /\.(jpe?g|png|gif|webp|avif|heic|heif|bmp|tiff?)$/.test(n)) return 'image';
  if (mime.startsWith('video/') || /\.(mp4|mov|m4v|mkv|webm|avi)$/.test(n)) return 'video';
  if (mime.startsWith('audio/') || /\.(mp3|m4a|wav|flac|ogg|opus|aac)$/.test(n)) return 'audio';
  if (/\.(zip|7z|rar|tar|gz|tgz|xz|zst|bz2)$/.test(n)) return 'archive';
  if (/\.(py|js|ts|go|rs|java|c|cpp|h|rb|php|swift|kt|sh|css|html|json|ya?ml|toml|sql)$/.test(n)) return 'code';
  if (mime === 'application/pdf' || mime.startsWith('text/') || /\.(pdf|docx?|odt|rtf|txt|md|pages|xlsx?|csv|pptx?|key|epub)$/.test(n)) return 'doc';
  return 'file';
}
const kindIcon = (name, mime) => ({ image: 'image', video: 'video', audio: 'audio', archive: 'archive', code: 'code', doc: 'doc' }[kindOf(name, mime)] || 'file');
const INLINE_IMG = /\.(jpe?g|png|gif|webp|avif|bmp)$/i;

function initFileList() {
  const list = $('#file-list');
  const search = $('#search-input');
  const sort = $('#sort-select');
  const scopeBtns = $$('#scope [data-scope]');
  const syncScope = () => scopeBtns.forEach(b => b.setAttribute('aria-pressed', String(b.dataset.scope === scope)));
  scopeBtns.forEach(b => b.addEventListener('click', () => { scope = b.dataset.scope; store.set('yeet_scope', scope); syncScope(); render(); }));
  syncScope();
  search?.addEventListener('input', render);
  sort?.addEventListener('change', render);

  let loaded = false;
  loadFiles = async () => {
    try {
      const r = await fetch('/api/files/all', { headers: { 'X-Session-ID': getSession() } });
      if (!r.ok) throw new Error();
      const data = await r.json();
      allFiles = data.files || []; allBundles = data.bundles || [];
      loaded = true;
      render();
    } catch {
      if (!loaded) list.innerHTML = `<div class="empty">${icon('alert', 'icon-xl')}<strong>Couldn't load files</strong><p>Check your connection — the list refreshes automatically.</p></div>`;
    }
  };

  function render() {
    const q = (search?.value || '').trim().toLowerCase();
    const items = [
      ...allBundles.map(b => ({ ...b, kind: 'folder', label: b.name, size: b.total_size })),
      ...allFiles.map(f => ({ ...f, kind: 'file', label: f.orig_name, size: f.file_size })),
    ].filter(it => (scope === 'all' || it.is_yours) && (!q || it.label.toLowerCase().includes(q)));
    const by = sort?.value || 'recent';
    items.sort((a, b) => by === 'expiry' ? new Date(a.expires_at) - new Date(b.expires_at)
      : by === 'size' ? b.size - a.size
      : by === 'name' ? a.label.localeCompare(b.label)
      : String(b.created_at).localeCompare(String(a.created_at)));

    $('#file-count').textContent = items.length;
    if (!items.length) {
      const othersExist = scope === 'mine' && (allFiles.length + allBundles.length) > 0;
      list.innerHTML = `<div class="empty">${icon(q ? 'search' : 'upload', 'icon-xl')}
        <strong>${q ? `Nothing matches "${esc(q)}"` : scope === 'mine' ? 'You haven’t shared anything yet' : 'No files right now'}</strong>
        <p>${q ? 'Try a different name.' : 'Files you upload from this browser show up here until they expire.'}</p>
        ${othersExist && !q ? '<button class="btn" type="button" data-scope-all>Show everyone’s files</button>' : ''}</div>`;
      $('[data-scope-all]', list)?.addEventListener('click', () => { scope = 'all'; store.set('yeet_scope', scope); syncScope(); render(); });
      return;
    }
    list.innerHTML = `<div class="rows">${items.map((it, i) => row(it, i)).join('')}</div>`;
  }

  function row(it, i) {
    const exp = fmtExpiry(it.expires_at);
    const yours = it.is_yours ? `<span class="tag you">you</span>` : '';
    if (it.kind === 'folder') {
      return `<div class="row" style="--i:${Math.min(i, 12)}" data-bundle="${esc(it.id)}">
        <a class="thumb t-folder" href="/b/${esc(it.id)}" target="_blank" rel="noopener" aria-label="Open folder">${icon('folder', 'icon-lg')}</a>
        <div class="row-main">
          <a class="row-name" href="/b/${esc(it.id)}" target="_blank" rel="noopener">${esc(it.name)}</a>
          <div class="row-meta">${yours}<span>${it.file_count} files</span><span class="mono">${fmtSize(it.size)}</span><span class="${exp.cls}">${exp.label}</span>
            ${it.has_password ? `<span class="tag">${icon('lock')}password</span>` : ''}</div>
        </div>
        <div class="row-actions">
          <button class="icon-btn" type="button" data-act="copy" data-url="/b/${esc(it.id)}" aria-label="Copy link">${icon('link')}</button>
          ${it.has_password ? '' : `<a class="icon-btn hide-sm" href="/b/${esc(it.id)}/zip" download aria-label="Download zip">${icon('download')}</a>`}
          ${it.is_yours ? `<button class="icon-btn danger" type="button" data-act="del-bundle" data-id="${esc(it.id)}" aria-label="Delete">${icon('trash')}</button>` : ''}
        </div></div>`;
    }
    const k = kindOf(it.orig_name, it.mime_type);
    const showImg = k === 'image' && INLINE_IMG.test(it.orig_name) && !it.has_password && !it.burn_after_read;
    const thumb = showImg
      ? `<img src="/raw/${esc(it.id)}" alt="" loading="lazy" decoding="async">`
      : icon(kindIcon(it.orig_name, it.mime_type), 'icon-lg');
    const previewable = !it.burn_after_read && !it.dangerous && ['image', 'video', 'audio', 'code', 'doc'].includes(k);
    const left = it.max_downloads ? `<span>${Math.max(0, it.max_downloads - it.download_count)} download${it.max_downloads - it.download_count === 1 ? '' : 's'} left</span>` : '';
    return `<div class="row" style="--i:${Math.min(i, 12)}" data-file="${esc(it.id)}">
      <button class="thumb t-${k}" type="button" data-act="${previewable ? 'preview' : 'open'}" data-id="${esc(it.id)}" aria-label="Preview">${thumb}</button>
      <div class="row-main">
        <a class="row-name" href="/f/${esc(it.id)}" target="_blank" rel="noopener" title="${esc(it.orig_name)}">${esc(it.orig_name)}</a>
        <div class="row-meta">${yours}<span class="mono">${fmtSize(it.size)}</span><span class="${exp.cls}">${exp.label}</span>${left}
          ${it.has_password ? `<span class="tag">${icon('lock')}password</span>` : ''}
          ${it.burn_after_read ? `<span class="tag burn">${icon('flame')}single download</span>` : ''}
          ${it.dangerous ? `<span class="tag danger">${icon('alert')}executable</span>` : ''}</div>
      </div>
      <div class="row-actions">
        ${previewable ? `<button class="icon-btn hide-sm" type="button" data-act="preview" data-id="${esc(it.id)}" aria-label="Preview">${icon('eye')}</button>` : ''}
        <button class="icon-btn" type="button" data-act="copy" data-url="/f/${esc(it.id)}" aria-label="Copy link">${icon('link')}</button>
        ${it.burn_after_read ? '' : `<button class="icon-btn hide-sm" type="button" data-act="download" data-id="${esc(it.id)}" aria-label="Download">${icon('download')}</button>`}
        ${it.is_yours ? `<button class="icon-btn danger" type="button" data-act="del" data-id="${esc(it.id)}" aria-label="Delete">${icon('trash')}</button>` : ''}
      </div></div>`;
  }

  // Broken thumbnails (expired, unsupported format) fall back to the type icon.
  list.addEventListener('error', e => {
    if (e.target.tagName === 'IMG') e.target.outerHTML = icon('image', 'icon-lg');
  }, true);

  list.addEventListener('click', async e => {
    const b = e.target.closest('[data-act]');
    if (!b) return;
    const f = allFiles.find(x => x.id === b.dataset.id);
    switch (b.dataset.act) {
      case 'copy': copyWithToast(location.origin + b.dataset.url); break;
      case 'open': open(`/f/${b.dataset.id}`, '_blank', 'noopener'); break;
      case 'preview': if (f) withUnlock(f, token => showPreview(f, token)); break;
      case 'download': if (f) withUnlock(f, token => { location.href = `/f/${f.id}?dl=1${token ? '&token=' + encodeURIComponent(token) : ''}`; }); break;
      case 'del': await removeItem(`/api/files/${b.dataset.id}`, b.closest('.row'), 'File deleted'); break;
      case 'del-bundle': await removeItem(`/api/bundles/${b.dataset.id}`, b.closest('.row'), 'Folder deleted'); break;
    }
  });

  async function removeItem(url, rowEl, msg) {
    if (!confirm('Delete this for everyone? The link stops working immediately.')) return;
    try {
      await api('DELETE', url);
      rowEl.style.transition = 'opacity .2s, transform .2s'; rowEl.style.opacity = '0'; rowEl.style.transform = 'translateX(8px)';
      setTimeout(loadFiles, 200);
      toast(msg, 'success');
    } catch (e) { toast(e.message || 'Delete failed', 'error'); }
  }

  loadFiles();
  setInterval(() => document.visibilityState === 'visible' && loadFiles(), 30_000);
}

// Password-protected files: ask once, keep the token for this page view.
const tokens = {};
function withUnlock(f, then) {
  if (!f.has_password) return then('');
  if (tokens[f.id]) return then(tokens[f.id]);
  const m = $('#pw-modal');
  const input = $('#pw-modal-input'), err = $('#pw-error'), form = $('#pw-form'), btn = $('#pw-submit');
  input.value = ''; err.textContent = '';
  form.onsubmit = async ev => {
    ev.preventDefault();
    if (!input.value) { err.textContent = 'Enter the password.'; return; }
    btn.disabled = true;
    try {
      const r = await fetch('/api/verify-password', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ file_id: f.id, password: input.value }) });
      const d = await r.json();
      if (!d.success) { err.textContent = d.error || 'Incorrect password.'; input.select(); return; }
      tokens[f.id] = d.token; closeModal(m); then(d.token);
    } catch { err.textContent = 'Network error — try again.'; } finally { btn.disabled = false; }
  };
  openModal(m);
}

function showPreview(f, token = '') {
  const k = kindOf(f.orig_name, f.mime_type);
  const src = `/raw/${f.id}${token ? '?token=' + encodeURIComponent(token) : ''}`;
  if ((f.mime_type || '') === 'application/pdf') { open(src, '_blank', 'noopener'); return; }
  let stage;
  if (k === 'image') stage = `<img src="${src}" alt="${esc(f.orig_name)}">`;
  else if (k === 'video') stage = `<video src="${src}" controls playsinline autoplay preload="metadata"></video>`;
  else if (k === 'audio') stage = `<audio src="${src}" controls autoplay></audio>`;
  else stage = `<pre data-text>Loading…</pre>`;
  const m = tempModal(f.orig_name, `<div class="preview-stage">${stage}</div>`, 'media');
  $('[data-slot="head"]', m).outerHTML = `<a class="btn btn-quiet" href="/f/${f.id}?dl=1${token ? '&token=' + encodeURIComponent(token) : ''}" download>${icon('download')}<span class="only-pointer">Download</span></a>`;
  const pre = $('[data-text]', m);
  if (pre) {
    fetch(src).then(r => r.ok ? r.text() : Promise.reject(r.status))
      .then(t => { pre.textContent = t.length > 200_000 ? t.slice(0, 200_000) + '\n\n… (truncated — download for the full file)' : t; })
      .catch(() => { pre.outerHTML = `<div class="preview-note">${icon('file', 'icon-xl')}<span>No preview for this file.</span></div>`; });
  }
  const media = $('video, img', m);
  media?.addEventListener('error', () => {
    if (k === 'video') media.outerHTML = `<div class="preview-note">${icon('video', 'icon-xl')}<span>Your browser can't play this format. Download it instead.</span></div>`;
  });
}

// ── Clipboard ─────────────────────────────────────────────────────────────────
const CLIP_IMAGE_TYPES = ['image/png', 'image/jpeg', 'image/gif', 'image/webp'];
const CLIP_IMAGE_MAX = 10 * 1_048_576;
let loadClips = () => {};

async function saveClipboardText(text) {
  try {
    const { data } = await api('POST', '/api/clipboard/paste', {
      content_type: 'text', content: text, expiry_minutes: (+store.get('yeet_expiry', '24') || 24) * 60, session_id: getSession(),
    });
    const copied = await copyText(data.share_url);
    toast(copied ? 'Saved to the public clipboard — link copied' : 'Saved to the public clipboard', 'success');
    $('#clipboard-panel')?.setAttribute('open', '');
    loadClips();
  } catch (e) { toast(e.message || 'Could not save text', 'error'); }
}

async function saveClipboardImage(blob) {
  if (!CLIP_IMAGE_TYPES.includes(blob.type) || blob.size > CLIP_IMAGE_MAX) {
    toast('Clipboard images must be PNG, JPEG, GIF or WebP under 10 MB — use a normal upload instead', 'warn', 7000);
    return;
  }
  const dataUrl = await new Promise((res, rej) => { const fr = new FileReader(); fr.onload = () => res(fr.result); fr.onerror = rej; fr.readAsDataURL(blob); });
  try {
    const { data } = await api('POST', '/api/clipboard/paste', {
      content_type: 'image', content: dataUrl, expiry_minutes: (+store.get('yeet_expiry', '24') || 24) * 60, session_id: getSession(),
    });
    const copied = await copyText(data.share_url);
    toast(copied ? 'Image saved to the public clipboard — link copied' : 'Image saved to the public clipboard', 'success');
    $('#clipboard-panel')?.setAttribute('open', '');
    loadClips();
  } catch (e) { toast(e.message || 'Could not save image', 'error'); }
}

function openPasteModal() {
  const m = $('#paste-modal');
  const ta = $('#paste-textarea');
  ta.value = '';
  openModal(m);
  navigator.clipboard?.readText?.().then(t => { if (t && !ta.value) ta.value = t; }).catch(() => {});
}

function initClipboard() {
  const list = $('#clipboard-list'), empty = $('#clipboard-empty'), count = $('#clipboard-count'), panel = $('#clipboard-panel');
  if (!list) return;
  if (store.get('yeet_clip_open') === '1') panel.open = true;
  panel.addEventListener('toggle', () => store.set('yeet_clip_open', panel.open ? '1' : '0'));
  let items = [];

  loadClips = async () => {
    try {
      const r = await fetch('/api/clipboard/recent?limit=50', { headers: { 'X-Session-ID': getSession() } });
      items = (await r.json()).items || [];
    } catch { return; }
    count.textContent = items.length;
    empty.classList.toggle('hidden', items.length > 0);
    list.innerHTML = items.map(it => {
      const img = it.type === 'image';
      const exp = it.pinned ? 'pinned' : fmtExpiry(it.expires_at).label;
      return `<article class="clip${it.is_yours ? ' yours' : ''}" data-id="${esc(it.id)}">
        <a class="clip-preview" href="/c/${esc(it.id)}" target="_blank" rel="noopener">${img
          ? `<img src="/api/clipboard/image/${esc(it.id)}" alt="" loading="lazy">`
          : `<pre>${esc((it.preview || '').slice(0, 300))}</pre>`}</a>
        <div class="clip-foot">${it.is_yours ? '<span class="tag you">you</span>' : ''}<span>${timeAgo(it.created_at)}</span><span class="spacer"></span>
          <span title="${it.pinned ? 'Pinned' : 'Expires'}">${esc(exp)}</span>
          <button class="icon-btn" type="button" data-clip="copy" aria-label="Copy">${icon('copy')}</button>
          ${it.is_yours ? `<button class="icon-btn" type="button" data-clip="pin" aria-label="${it.pinned ? 'Unpin' : 'Pin'}">${icon('pin')}</button>
          <button class="icon-btn danger" type="button" data-clip="del" aria-label="Delete">${icon('trash')}</button>` : ''}
        </div></article>`;
    }).join('');
  };

  list.addEventListener('click', async e => {
    const b = e.target.closest('[data-clip]');
    if (!b) return;
    const id = b.closest('[data-id]').dataset.id;
    const it = items.find(x => x.id === id);
    if (b.dataset.clip === 'copy') {
      if (it?.type === 'text') {
        try { const t = await (await fetch(`/c/${id}/raw`)).text(); copyWithToast(t, 'Text copied'); } catch { toast('Copy failed', 'error'); }
      } else copyWithToast(`${location.origin}/c/${id}`);
    } else if (b.dataset.clip === 'pin') {
      try { await api('POST', `/api/clipboard/pin/${id}`); loadClips(); } catch (err) { toast(err.message, 'error'); }
    } else if (b.dataset.clip === 'del') {
      if (!confirm('Delete this clipboard item?')) return;
      try { await api('DELETE', `/api/clipboard/item/${id}`); loadClips(); toast('Deleted', 'success'); } catch (err) { toast(err.message, 'error'); }
    }
  });

  $('#paste-save')?.addEventListener('click', async () => {
    const ta = $('#paste-textarea');
    const txt = ta.value.trim();
    if (!txt) { toast('Nothing to save', 'warn'); ta.focus(); return; }
    closeModal($('#paste-modal'));
    await saveClipboardText(txt);
  });
  $('#paste-textarea')?.addEventListener('paste', async e => {
    const f = Array.from(e.clipboardData?.items || []).find(i => i.type.startsWith('image/'))?.getAsFile();
    if (f) { e.preventDefault(); closeModal($('#paste-modal')); await saveClipboardImage(f); }
  });
  const clipImg = $('#clip-image-input');
  $('#paste-pick-img')?.addEventListener('click', () => { closeModal($('#paste-modal')); clipImg.click(); });
  clipImg?.addEventListener('change', async () => {
    const f = clipImg.files?.[0]; clipImg.value = '';
    if (f) await saveClipboardImage(f);
  });

  loadClips();
  setInterval(() => document.visibilityState === 'visible' && panel.open && loadClips(), 60_000);
}

function initScannerLog() {
  const sec = $('#deleted-section'), out = $('#deleted-list');
  sec?.addEventListener('toggle', async () => {
    if (!sec.open || out.dataset.loaded) return;
    out.dataset.loaded = '1';
    try {
      const entries = (await (await fetch('/api/virus-log')).json()).deletions || [];
      out.innerHTML = entries.length
        ? entries.map(e => `<div class="log-row"><span>${esc(e.filename)}</span><span class="muted">${timeAgo(e.ts)}</span></div>`).join('')
        : '<p class="muted" style="margin:0;font-size:13.5px">Nothing rejected recently.</p>';
    } catch { out.textContent = 'Could not load.'; }
  });
}

// ── Share page (/f/<id> landing) ──────────────────────────────────────────────
const share = $('#share');

function initSharePage() {
  const id = share.dataset.id;
  const size = +share.dataset.size;
  const name = share.dataset.name;
  const burn = share.dataset.burn === '1';
  let token = '';

  const loadText = pre => {
    if (!pre) return;
    fetch(pre.dataset.src + (token ? `?token=${encodeURIComponent(token)}` : ''))
      .then(r => r.ok ? r.text() : Promise.reject())
      .then(t => { pre.textContent = t.length > 200_000 ? t.slice(0, 200_000) + '\n\n… (truncated)' : t; })
      .catch(() => { pre.textContent = 'Preview unavailable.'; });
  };
  loadText($('#text-preview'));
  $('#stage video')?.addEventListener('error', e => {
    e.target.outerHTML = `<div class="preview-note">${icon('video', 'icon-xl')}<span>Your browser can't play this format — download it instead.</span></div>`;
  });

  // Unlock without a page reload, so the preview can appear too.
  const form = $('#unlock-form');
  form?.addEventListener('submit', async e => {
    e.preventDefault();
    const pw = $('#dl-password').value;
    const err = $('#unlock-error');
    err.textContent = '';
    try {
      const r = await fetch('/api/verify-password', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ file_id: id, password: pw }) });
      const d = await r.json();
      if (!d.success) { err.textContent = d.error || 'Incorrect password.'; return; }
      token = d.token;
      form.remove();
      $('#dl-actions').classList.remove('hidden');
      $('#dl-btn').href = d.download_url;
      const stage = $('#stage'), kind = share.dataset.kind, src = d.preview_url;
      if (stage && kind) {
        stage.innerHTML = kind === 'image' ? `<img src="${src}" alt="">`
          : kind === 'video' ? `<video src="${src}" controls playsinline preload="metadata"></video>`
          : kind === 'audio' ? `<audio src="${src}" controls></audio>`
          : kind === 'pdf' ? `<div class="preview-note">${icon('doc', 'icon-xl')}<a class="btn" href="${src}" target="_blank" rel="noopener">${icon('external')}Open PDF in a new tab</a></div>`
          : `<pre data-src="/raw/${id}">Loading…</pre>`;
        loadText($('pre[data-src]', stage));
      }
    } catch { err.textContent = 'Network error — try again.'; }
  });

  // Big files: fetch in 64 MB ranges straight to disk where the browser allows
  // it (Chromium's file picker). Each piece retries on its own, so a flaky
  // connection or proxy reset never restarts the whole download.
  const btn = $('#dl-btn');
  btn.addEventListener('click', async e => {
    if (burn || size < 100 * 1_048_576 || typeof window.showSaveFilePicker !== 'function') return;
    e.preventDefault();
    let handle;
    try { handle = await window.showSaveFilePicker({ suggestedName: name }); } catch { return; }
    const writable = await handle.createWritable();
    const prog = $('#dl-progress'), fill = $('#dl-fill'), stat = $('#dl-stat');
    prog.classList.remove('hidden'); btn.classList.add('hidden');
    const url = btn.getAttribute('href');
    const PIECE = 64 * 1_048_576;
    const t0 = performance.now();
    try {
      for (let off = 0; off < size; off += PIECE) {
        const end = Math.min(size, off + PIECE) - 1;
        const buf = await withRetry(async () => {
          let r;
          try { r = await fetch(url, { headers: { Range: `bytes=${off}-${end}` } }); } catch { throw new HttpError(0, {}); }
          if (r.status !== 206 && !(r.status === 200 && off === 0 && end === size - 1)) throw new HttpError(r.status, safeJson(await r.text()));
          return r.arrayBuffer();
        });
        await writable.write({ type: 'write', position: off, data: buf });
        const done = end + 1;
        fill.style.width = (done / size * 100).toFixed(1) + '%';
        const rate = done / ((performance.now() - t0) / 1000);
        stat.textContent = `${fmtSize(done)} of ${fmtSize(size)} · ${fmtSize(rate)}/s · ${fmtDuration((size - done) / rate)}`;
      }
      await writable.close();
      stat.textContent = `Saved ${fmtSize(size)}`;
      toast('Download complete', 'success');
    } catch (err) {
      await writable.abort?.().catch(() => {});
      stat.textContent = '';
      prog.classList.add('hidden'); btn.classList.remove('hidden');
      toast(`Download failed: ${err.message}. Try again — or right-click Download and save normally.`, 'error');
    }
  });

  $('#share-link')?.addEventListener('click', () => {
    const url = location.origin + location.pathname;
    if (canShare()) navigator.share({ title: name, url }).catch(() => {});
    else copyWithToast(url);
  });
}

// ── Folder + clipboard pages ──────────────────────────────────────────────────
if (!share || !share.dataset.id) {
  $('#share-link')?.addEventListener('click', () => {
    const url = location.origin + location.pathname;
    if (canShare()) navigator.share({ url }).catch(() => {});
    else copyWithToast(url);
  });
}
$('[data-copy-text]')?.addEventListener('click', () => copyWithToast($('#cb-content').textContent, 'Text copied'));
$('[data-copy-image]')?.addEventListener('click', async e => {
  try {
    const blob = await (await fetch(e.currentTarget.dataset.copyImage)).blob();
    await navigator.clipboard.write([new ClipboardItem({ [blob.type]: blob })]);
    toast('Image copied', 'success');
  } catch { toast('Your browser cannot copy images — use Download instead', 'warn'); }
});

// ── Boot ──────────────────────────────────────────────────────────────────────
// Last, so every top-level let/const above is initialised first.
if (dropZone) initUploadPage();
if (share && share.dataset.id && $('#dl-btn')) initSharePage();
