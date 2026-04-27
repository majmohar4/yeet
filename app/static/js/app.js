'use strict';

// ── Session ───────────────────────────────────────────────────────────────────
const SESSION_KEY = 'yeet_session';
function getSession() {
  let s = localStorage.getItem(SESSION_KEY);
  if (!s) {
    s = crypto.randomUUID
      ? crypto.randomUUID()
      : ([1e7]+-1e3+-4e3+-8e3+-1e11).replace(/[018]/g, c =>
          (c ^ crypto.getRandomValues(new Uint8Array(1))[0] & 15 >> c / 4).toString(16));
    localStorage.setItem(SESSION_KEY, s);
  }
  return s;
}
getSession();

// ── State ─────────────────────────────────────────────────────────────────────
const _cfg         = window.YEET_CONFIG || {};
let   _allFiles    = [];
let   _accent      = localStorage.getItem('yeet_accent') || 'cyan';
let   _expiry      = parseInt(localStorage.getItem('yeet_expiry') || '24', 10);
let   _pendingDlId = null;
let   _resultUrl   = '';

// ── DOM refs ──────────────────────────────────────────────────────────────────
const dropZone       = document.getElementById('drop-zone');
const dzPrompt       = document.getElementById('dz-prompt');
const fileInput      = document.getElementById('file-input');
const dzPassword     = document.getElementById('dz-password');
const dzGenPw        = document.getElementById('dz-gen-pw');
const activeUploads  = document.getElementById('active-uploads');
const uploadResult   = document.getElementById('upload-result');
const resultUrlEl    = document.getElementById('upload-result-url');
const fileGrid       = document.getElementById('file-grid');
const fileCount      = document.getElementById('file-count');
const searchInput    = document.getElementById('search-input');
const sortSelect     = document.getElementById('sort-select');
const deletedSection = document.getElementById('deleted-section');
const deletedList    = document.getElementById('deleted-list');
const settingsBtn    = document.getElementById('settings-btn');
const settingsModal  = document.getElementById('settings-modal');
const settingsClose  = document.getElementById('settings-close');
const pwModal        = document.getElementById('pw-modal');
const pwModalClose   = document.getElementById('pw-modal-close');
const pwModalInput   = document.getElementById('pw-modal-input');
const pwSubmit       = document.getElementById('pw-submit');
const pwError        = document.getElementById('pw-error');
const toastContainer = document.getElementById('toast-container');
const fabBtn         = document.getElementById('fab-btn');

// ── Accent / settings init ────────────────────────────────────────────────────
applyAccent(_accent);
(function syncAccentButtons() {
  document.querySelectorAll('[data-setting="accent"]').forEach(b => {
    b.classList.remove('active', 'active-mono');
    if (b.dataset.value === _accent) {
      b.classList.add(_accent === 'mono' ? 'active-mono' : 'active');
    }
  });
})();

function applyAccent(val) {
  const map = {
    cyan:   ['#00D4FF', '#00AACC'],
    purple: ['#B537F2', '#8E1ED4'],
    mono:   ['#FFFFFF', '#CCCCCC'],
  };
  const [a, h] = map[val] || map.cyan;
  document.documentElement.style.setProperty('--accent', a);
  document.documentElement.style.setProperty('--accent-hover', h);
}

// ── Expiry chips ──────────────────────────────────────────────────────────────
(function initChips() {
  const maxH = _cfg.expiryHours || 24;

  // Cap stored preference at server max
  if (_expiry > maxH) { _expiry = maxH; localStorage.setItem('yeet_expiry', String(_expiry)); }

  let anyVisible = false;
  document.querySelectorAll('.chip[data-hours]').forEach(btn => {
    const h = parseInt(btn.dataset.hours, 10);
    if (h > maxH) { btn.style.display = 'none'; return; }
    anyVisible = true;
    if (h === _expiry) btn.classList.add('active');
    btn.addEventListener('click', e => {
      e.stopPropagation();
      document.querySelectorAll('.chip[data-hours]').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      _expiry = h;
      localStorage.setItem('yeet_expiry', String(_expiry));
    });
  });

  // Activate the largest visible chip if none match saved preference
  if (!document.querySelector('.chip.active')) {
    const chips = [...document.querySelectorAll('.chip[data-hours]')].filter(b => b.style.display !== 'none');
    const best  = chips.reduce((a, b) => parseInt(b.dataset.hours,10) > parseInt(a.dataset.hours,10) ? b : a, chips[0] || null);
    if (best) { best.classList.add('active'); _expiry = parseInt(best.dataset.hours, 10); }
  }
})();

// ── Password generator ────────────────────────────────────────────────────────
if (dzGenPw) {
  dzGenPw.addEventListener('click', e => {
    e.stopPropagation();
    const chars = 'abcdefghjkmnpqrstuvwxyzABCDEFGHJKMNPQRSTUVWXYZ23456789!@#$%&';
    const arr = crypto.getRandomValues(new Uint8Array(14));
    const pw = Array.from(arr, b => chars[b % chars.length]).join('');
    if (dzPassword) { dzPassword.value = pw; dzPassword.type = 'text'; setTimeout(() => { dzPassword.type = 'password'; }, 2500); }
    navigator.clipboard.writeText(pw).catch(() => {});
    toast('Password generated & copied', 'success');
  });
}

// ── Drop zone interactions ────────────────────────────────────────────────────
if (dropZone) {
  // Click to browse — ignore clicks on controls
  dropZone.addEventListener('click', e => {
    if (e.target.closest('#dz-controls')) return;
    fileInput && fileInput.click();
  });
  dropZone.addEventListener('keydown', e => {
    if ((e.key === 'Enter' || e.key === ' ') && e.target === dropZone) fileInput && fileInput.click();
  });

  // Drag
  dropZone.addEventListener('dragover', e => { e.preventDefault(); dropZone.classList.add('drag-over'); });
  dropZone.addEventListener('dragleave', e => { if (!dropZone.contains(e.relatedTarget)) dropZone.classList.remove('drag-over'); });
  dropZone.addEventListener('drop', async e => {
    e.preventDefault(); dropZone.classList.remove('drag-over');

    const files = [];
    let hadFolder = false;
    if (e.dataTransfer.items) {
      for (const item of Array.from(e.dataTransfer.items)) {
        if (item.kind !== 'file') continue;
        const entry = typeof item.webkitGetAsEntry === 'function' ? item.webkitGetAsEntry() : null;
        if (entry && entry.isDirectory) { hadFolder = true; continue; }
        const f = item.getAsFile();
        if (!f) continue;
        if (!(await _probeReadable(f))) { hadFolder = true; continue; }
        files.push(f);
      }
    } else {
      for (const f of Array.from(e.dataTransfer.files)) {
        if (!(await _probeReadable(f))) { hadFolder = true; continue; }
        files.push(f);
      }
    }
    if (hadFolder) toast('Folder upload is not supported — drop individual files', 'warn');
    if (files.length) await uploadFiles(files);
  });
}

if (fileInput) {
  fileInput.addEventListener('change', async () => {
    const files = Array.from(fileInput.files);
    fileInput.value = '';
    if (files.length) await uploadFiles(files);
  });
}

if (fabBtn) fabBtn.addEventListener('click', () => fileInput && fileInput.click());

// ── Global paste detection ────────────────────────────────────────────────────
document.addEventListener('paste', async e => {
  if (!dropZone) return;
  const active = document.activeElement;
  if (active && ['INPUT', 'TEXTAREA', 'SELECT'].includes(active.tagName)) return;

  e.preventDefault();
  const items = Array.from(e.clipboardData.items);

  // Image → upload as file
  const imgItem = items.find(i => i.type.startsWith('image/'));
  if (imgItem) {
    const blob = imgItem.getAsFile();
    const ext  = imgItem.type.split('/')[1] || 'png';
    await uploadFiles([new File([blob], `pasted-image.${ext}`, { type: imgItem.type })]);
    return;
  }

  // Text → save as clipboard snippet (readable at /c/{id})
  const stringItems = items.filter(i => i.kind === 'string');
  const txtItem = stringItems.find(i => i.type === 'text/plain');
  if (txtItem) {
    const text = await new Promise(r => txtItem.getAsString(r));
    if (!text.trim()) return;
    await saveClipboardSnippet(text);
  }
});

async function saveClipboardSnippet(text) {
  try {
    const expiryMinutes = _expiry * 60;
    const r = await fetch('/api/clipboard/paste', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        content_type: 'text',
        content: text,
        expiry_minutes: expiryMinutes,
        session_id: getSession(),
      }),
    });
    const data = await r.json();
    if (r.status === 201) {
      _resultUrl = data.share_url;
      if (resultUrlEl) resultUrlEl.textContent = data.share_url;
      if (uploadResult) uploadResult.classList.remove('hidden');
      navigator.clipboard.writeText(data.share_url).catch(() => {});
      toast('Text saved — link copied!', 'success');
    } else {
      toast(data.error || 'Failed to save text', 'error');
    }
  } catch {
    toast('Failed to save clipboard text', 'error');
  }
}

// Returns false when the browser can't read the file (directories cause a NotReadableError)
function _probeReadable(file) {
  return new Promise(resolve => {
    const r = new FileReader();
    r.onload  = () => resolve(true);
    r.onerror = () => resolve(false);
    try {
      // slice(0,4) of a 0-byte File (dropped directory) returns an empty Blob
      // that fires onload instead of onerror — read the raw File in that case.
      r.readAsArrayBuffer(file.size > 0 ? file.slice(0, 4) : file);
    } catch {
      resolve(false);
    }
  });
}

// ── Upload ────────────────────────────────────────────────────────────────────
async function uploadFiles(files) {
  if (_cfg.storageBlocked) { toast('Storage full — uploads disabled', 'error'); return; }
  for (const f of files) await uploadSingle(f);
}

async function uploadSingle(file) {
  const uid = 'u' + Math.random().toString(36).slice(2, 8);
  const pw  = dzPassword ? dzPassword.value : '';

  // Progress element
  const progEl = mkProgressEl(uid, file.name, file.size);
  if (activeUploads) { activeUploads.appendChild(progEl); activeUploads.style.display = ''; }

  const fd = new FormData();
  fd.append('file', file);
  fd.append('password', pw);
  fd.append('expiry_hours', String(_expiry));
  fd.append('session_id', getSession());

  return new Promise(resolve => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', '/upload');
    xhr.upload.onprogress = ev => {
      if (ev.lengthComputable) setProgress(uid, Math.round(ev.loaded / ev.total * 100));
    };
    xhr.onload = async () => {
      const json = safeJson(xhr.responseText);
      if (xhr.status === 201) {
        doneProgress(uid);
        _resultUrl = json.url || '';
        if (resultUrlEl) resultUrlEl.textContent = json.url;
        if (uploadResult) uploadResult.classList.remove('hidden');
        navigator.clipboard.writeText(json.url).catch(() => {});
        toast('Uploaded — link copied!', 'success');
        if (json.warning) toast('⚠ ' + json.warning, 'warn');
        loadFileList();
      } else {
        failProgress(uid);
        toast(json.error || 'Upload failed', 'error');
      }
      setTimeout(() => removeProgress(uid), 3000);
      resolve(json);
    };
    xhr.onerror = () => { failProgress(uid); toast('Upload failed: network error', 'error'); resolve({}); };
    xhr.send(fd);
  });
}

function safeJson(text) { try { return JSON.parse(text); } catch { return {}; } }

// ── Progress helpers ──────────────────────────────────────────────────────────
function mkProgressEl(uid, name, size) {
  const d = document.createElement('div');
  d.className = 'upload-progress';
  d.dataset.uid = uid;
  d.innerHTML = `<div class="upload-progress-info">
    <span class="upload-progress-icon">${fileIcon(name)}</span>
    <span class="upload-progress-name">${esc(name)}</span>
    <span class="upload-progress-pct" id="pct-${uid}">0%</span>
  </div>
  <div class="upload-progress-bar"><div class="upload-progress-fill" id="fill-${uid}" style="width:0"></div></div>`;
  return d;
}
function setProgress(uid, pct) {
  const fill = document.getElementById('fill-' + uid);
  const label = document.getElementById('pct-' + uid);
  if (fill)  fill.style.width  = pct + '%';
  if (label) label.textContent = pct + '%';
}
function doneProgress(uid) {
  const el = document.querySelector(`[data-uid="${uid}"]`);
  if (!el) return;
  el.classList.add('done');
  setProgress(uid, 100);
  const label = document.getElementById('pct-' + uid);
  if (label) label.textContent = '✓';
}
function failProgress(uid) {
  const el = document.querySelector(`[data-uid="${uid}"]`);
  if (el) el.classList.add('failed');
  const label = document.getElementById('pct-' + uid);
  if (label) label.textContent = '✗';
}
function removeProgress(uid) {
  const el = document.querySelector(`[data-uid="${uid}"]`);
  if (!el) return;
  el.style.opacity = '0';
  el.style.transition = 'opacity 300ms';
  setTimeout(() => {
    el.remove();
    if (activeUploads && !activeUploads.querySelector('.upload-progress')) activeUploads.style.display = 'none';
  }, 320);
}

function copyResult() {
  if (!_resultUrl) return;
  navigator.clipboard.writeText(_resultUrl).then(() => {
    const btn = document.getElementById('upload-result-copy');
    if (btn) { btn.textContent = 'copied!'; setTimeout(() => { btn.textContent = 'copy'; }, 2000); }
  });
}
function dismissResult() { if (uploadResult) uploadResult.classList.add('hidden'); }

// ── File list ─────────────────────────────────────────────────────────────────
async function loadFileList() {
  try {
    const r = await fetch('/api/files/all');
    const data = await r.json();
    _allFiles = data.files || [];
    renderFiles();
  } catch {
    if (fileGrid) fileGrid.innerHTML = '<div class="empty-state"><div class="empty-title">Failed to load files</div></div>';
  }
}

function renderFiles() {
  if (!fileGrid) return;
  const q    = (searchInput?.value || '').toLowerCase();
  const sort = sortSelect?.value || 'recent';
  const me   = getSession();

  let list = _allFiles.filter(f => !q || f.orig_name.toLowerCase().includes(q));
  list.sort((a, b) => {
    if (sort === 'recent') return new Date(b.created_at) - new Date(a.created_at);
    if (sort === 'expiry') return new Date(a.expires_at) - new Date(b.expires_at);
    if (sort === 'size')   return b.file_size - a.file_size;
    if (sort === 'name')   return a.orig_name.localeCompare(b.orig_name);
    return 0;
  });

  if (fileCount) fileCount.textContent = list.length;

  if (!list.length) {
    fileGrid.innerHTML = `<div class="empty-state">
      <div class="empty-icon">📂</div>
      <div class="empty-title">${q ? `No results for "${esc(q)}"` : 'No files yet'}</div>
      <div class="empty-sub">${q ? 'Try a different search term' : 'Drop files above or paste Ctrl+V to get started'}</div>
    </div>`;
    return;
  }

  const isOwn     = f => f.uploader_session && f.uploader_session === me;
  const isImg     = f => f.mime_type && f.mime_type.startsWith('image/');
  const isText    = f => (f.mime_type || '').startsWith('text/') || /\.(txt|md)$/i.test(f.orig_name);
  const canInline = f => /\.(jpg|jpeg|png|gif|webp|avif|pdf|mp4|webm|mov|mp3|wav|ogg|m4a|txt|md|json|py|js|ts|go|rs)$/i.test(f.orig_name);

  fileGrid.innerHTML = list.map(f => {
    const exp      = fmtExpiry(f.expires_at);
    const type     = cardType(f);
    const thumb    = isImg(f)
      ? `<img src="/raw/${esc(f.id)}" alt="${esc(f.orig_name)}" loading="lazy">`
      : isText(f)
        ? `<div class="card-thumb-text" data-text-id="${esc(f.id)}" style="overflow:hidden;padding:8px 10px;font-size:10px;line-height:1.5;white-space:pre-wrap;word-break:break-word;color:var(--text-2,rgba(255,255,255,.55));text-align:left;font-family:monospace;cursor:pointer">…</div>`
        : `<div class="card-thumb-icon">${fileIcon(f.orig_name)}</div>`;
    const own      = isOwn(f);
    const pwAttr   = f.has_password ? '1' : '0';
    const preBtn   = canInline(f) ? `<button class="icon-btn" data-action="preview" data-id="${esc(f.id)}" data-pw="${pwAttr}" title="Preview">👁</button>` : '';
    const dlBtn    = `<button class="icon-btn" data-action="download" data-id="${esc(f.id)}" data-pw="${pwAttr}" title="Download">⬇</button>`;
    const linkBtn  = `<button class="icon-btn" data-action="copylink" data-id="${esc(f.id)}" title="Copy link">🔗</button>`;
    const delBtn   = own ? `<button class="icon-btn danger" data-action="delete" data-id="${esc(f.id)}" title="Delete">🗑</button>` : '';
    const scanBadge = f.scan_status === 'pending' ? `<div><span class="scan-badge">scanning</span></div>` : '';

    return `<div class="file-card${f.has_password ? ' protected' : ''}" id="card-${esc(f.id)}">
      <div class="card-thumb type-${type}">${thumb}</div>
      <div class="card-body">
        <div class="card-name" title="${esc(f.orig_name)}">${esc(f.orig_name)}</div>
        <div class="card-meta">
          <span class="card-size">${fmtSize(f.file_size)}</span>
          <span class="card-expiry ${exp.cls}">${exp.label}</span>
        </div>
        ${scanBadge}
      </div>
      <div class="card-actions">${preBtn}${dlBtn}${linkBtn}${delBtn}</div>
    </div>`;
  }).join('');

  // Async-populate text-snippet thumbnails so the content is visible on the card
  fileGrid.querySelectorAll('[data-text-id]').forEach(el => {
    fetch(`/raw/${el.dataset.textId}`)
      .then(r => r.ok ? r.text() : Promise.reject())
      .then(t => { el.textContent = t.trim().slice(0, 400) || '(empty)'; })
      .catch(() => { el.innerHTML = `<div class="card-thumb-icon">📝</div>`; });
  });
}

function cardType(f) {
  const m = f.mime_type || '';
  const n = (f.orig_name || '').toLowerCase();
  if (m.startsWith('image/')) return 'image';
  if (m.startsWith('video/')) return 'video';
  if (m.startsWith('audio/')) return 'audio';
  if (m === 'application/pdf' || /\.(doc|docx|pdf|txt|md|odt|rtf)$/.test(n)) return 'doc';
  if (/\.(zip|tar|gz|rar|7z|bz2|xz)$/.test(n)) return 'archive';
  if (/\.(py|js|ts|go|rs|java|cpp|c|css|sql|rb|php|swift|kt)$/.test(n)) return 'code';
  if (/\.(json|xml|yaml|yml|toml|csv|xls|xlsx|ods)$/.test(n)) return 'data';
  return 'other';
}

// ── File grid events ──────────────────────────────────────────────────────────
if (fileGrid) {
  fileGrid.addEventListener('click', async e => {
    const btn    = e.target.closest('[data-action]');
    const action = btn?.dataset?.action;

    if (action === 'preview') {
      const { id, pw } = btn.dataset;
      if (pw === '1') { openPwModal(id); return; }
      const f = _allFiles.find(x => x.id === id);
      if (f) showPreviewModal(f);
      return;
    }
    if (action === 'download') {
      const { id, pw } = btn.dataset;
      if (pw === '1') { openPwModal(id); return; }
      window.open(`/f/${id}`, '_blank', 'noopener');
      return;
    }
    if (action === 'copylink') {
      await navigator.clipboard.writeText(`${location.origin}/f/${btn.dataset.id}`);
      toast('Link copied', 'success');
      return;
    }
    if (action === 'delete') {
      await deleteFile(btn.dataset.id);
      return;
    }

    // No btn or unrecognized action → treat as card-body click
    const card = e.target.closest('.file-card');
    if (!card) return;
    const id = card.id.replace(/^card-/, '');
    const f  = _allFiles.find(x => x.id === id);
    if (!f) return;
    if (f.has_password) { openPwModal(id); return; }
    const canInline = /\.(jpg|jpeg|png|gif|webp|avif|pdf|mp4|webm|mov|mp3|wav|ogg|m4a|txt|md|json|py|js|ts|go|rs)$/i.test(f.orig_name);
    if (canInline) { showPreviewModal(f); return; }
    window.open(`/f/${id}`, '_blank', 'noopener');
  });
}

async function deleteFile(id) {
  if (!confirm('Delete this file permanently?')) return;
  try {
    const r = await fetch(`/api/files/${id}`, { method: 'DELETE', headers: { 'X-Session-ID': getSession() } });
    if (r.ok) {
      _allFiles = _allFiles.filter(f => f.id !== id);
      const card = document.getElementById(`card-${id}`);
      if (card) { card.style.transition = 'opacity 240ms, transform 240ms'; card.style.opacity = '0'; card.style.transform = 'scale(0.92)'; setTimeout(() => { card.remove(); if (fileCount) fileCount.textContent = _allFiles.filter(f => !document.getElementById('search-input')?.value || f.orig_name.toLowerCase().includes(document.getElementById('search-input').value.toLowerCase())).length; }, 260); }
      toast('File deleted', 'warn');
    } else {
      const j = await r.json().catch(() => ({}));
      toast(j.error || 'Delete failed', 'error');
    }
  } catch { toast('Delete failed: network error', 'error'); }
}

// ── Preview modal ─────────────────────────────────────────────────────────────
function showPreviewModal(f) {
  const mime = f.mime_type || '';
  const name = f.orig_name || '';
  const id   = f.id;
  const isText = mime.startsWith('text/') || /\.(txt|md|json|yaml|yml|toml|csv|py|js|ts|go|rs|java|cpp|c|css|sql|rb|php|swift|kt)$/i.test(name);

  // PDFs can't be embedded — server sends frame-ancestors: none which blocks <embed>/<iframe>
  if (mime === 'application/pdf') {
    window.open(`/raw/${id}`, '_blank', 'noopener');
    return;
  }

  let bodyHtml;
  if (mime.startsWith('image/'))   bodyHtml = `<img src="/raw/${esc(id)}" alt="${esc(name)}" class="preview-media-img">`;
  else if (mime.startsWith('video/')) bodyHtml = `<video src="/raw/${esc(id)}" controls class="preview-media-video"></video>`;
  else if (mime.startsWith('audio/')) bodyHtml = `<div class="preview-audio-wrap"><audio src="/raw/${esc(id)}" controls class="preview-media-audio"></audio></div>`;
  else if (isText) bodyHtml = `<div class="preview-loading" id="preview-text-body">Loading…</div>`;
  else bodyHtml = `<div class="preview-unavailable"><div style="font-size:38px;margin-bottom:12px">${fileIcon(name)}</div><p>No preview available</p><p style="font-size:12px;color:var(--text-3);margin-top:6px">Use the download button</p></div>`;

  const overlay = document.createElement('div');
  overlay.className = 'modal-overlay';
  overlay.innerHTML = `<div class="modal-box preview-modal-box">
    <div class="modal-header">
      <span class="modal-title" style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(name)}</span>
      <div style="display:flex;gap:8px;align-items:center;flex-shrink:0">
        <a href="/f/${esc(id)}" class="btn-ghost" style="font-size:12px;height:28px;padding:0 10px">⬇ Download</a>
        <button class="modal-close" id="_pv-close">✕</button>
      </div>
    </div>
    <div class="preview-modal-body">${bodyHtml}</div>
  </div>`;
  document.body.appendChild(overlay);
  overlay.querySelector('#_pv-close').onclick = () => overlay.remove();
  overlay.addEventListener('click', e => { if (e.target === overlay) overlay.remove(); });

  if (isText) {
    fetch(`/raw/${id}`)
      .then(r => { if (!r.ok) throw new Error(r.status); return r.text(); })
      .then(txt => {
        const el = overlay.querySelector('#preview-text-body');
        if (el) { el.outerHTML = `<pre class="preview-text-content">${esc(txt.slice(0, 100_000))}</pre>`; }
      })
      .catch(() => {
        const el = overlay.querySelector('#preview-text-body');
        if (el) el.innerHTML = '<div class="preview-unavailable"><p>Could not load preview</p><p style="font-size:12px;color:var(--text-3);margin-top:6px">The file may have expired or be unavailable</p></div>';
      });
  }
}

// ── Search + sort ─────────────────────────────────────────────────────────────
if (searchInput) searchInput.addEventListener('input', renderFiles);
if (sortSelect)  sortSelect.addEventListener('change', renderFiles);

// ── Password modal (for protected file download) ──────────────────────────────
function openPwModal(id) {
  _pendingDlId = id;
  if (pwModalInput) pwModalInput.value = '';
  if (pwError)      pwError.textContent = '';
  if (pwModal)      pwModal.classList.remove('hidden');
  setTimeout(() => pwModalInput?.focus(), 60);
}
function closePwModal() {
  if (pwModal) pwModal.classList.add('hidden');
  _pendingDlId = null;
}
if (pwModalClose) pwModalClose.addEventListener('click', closePwModal);
if (pwModal) pwModal.addEventListener('click', e => { if (e.target === pwModal) closePwModal(); });
if (pwSubmit) {
  pwSubmit.addEventListener('click', async () => {
    const pw = pwModalInput?.value?.trim();
    if (!pw) { if (pwError) pwError.textContent = 'Enter a password.'; return; }
    if (pwError) pwError.textContent = '';
    pwSubmit.disabled = true; pwSubmit.textContent = 'Checking…';
    try {
      const r = await fetch('/api/verify-password', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ file_id: _pendingDlId, password: pw }),
      });
      const d = await r.json();
      if (d.success) { closePwModal(); window.open(d.download_url, '_blank', 'noopener'); }
      else { if (pwError) pwError.textContent = d.error || 'Incorrect password.'; }
    } catch { if (pwError) pwError.textContent = 'Network error. Try again.'; }
    finally { pwSubmit.disabled = false; pwSubmit.textContent = 'Unlock & download'; }
  });
}
if (pwModalInput) pwModalInput.addEventListener('keydown', e => { if (e.key === 'Enter') pwSubmit?.click(); });

// ── Settings modal ────────────────────────────────────────────────────────────
if (settingsBtn)   settingsBtn.addEventListener('click', () => settingsModal?.classList.remove('hidden'));
if (settingsClose) settingsClose.addEventListener('click', () => settingsModal?.classList.add('hidden'));
if (settingsModal) settingsModal.addEventListener('click', e => { if (e.target === settingsModal) settingsModal.classList.add('hidden'); });

document.querySelectorAll('[data-setting="accent"]').forEach(btn => {
  btn.addEventListener('click', () => {
    const val = btn.dataset.value;
    _accent = val;
    localStorage.setItem('yeet_accent', val);
    applyAccent(val);
    document.querySelectorAll('[data-setting="accent"]').forEach(b => b.classList.remove('active', 'active-mono'));
    btn.classList.add(val === 'mono' ? 'active-mono' : 'active');
  });
});

// ── Deleted / virus log ───────────────────────────────────────────────────────
if (deletedSection) {
  deletedSection.addEventListener('toggle', () => {
    if (deletedSection.open && deletedList && !deletedList.children.length) loadDeletedFiles();
  });
}
async function loadDeletedFiles() {
  if (!deletedList) return;
  try {
    const r = await fetch('/api/virus-log');
    const data = await r.json();
    const entries = data.deletions || [];
    if (!entries.length) {
      deletedList.innerHTML = '<div class="deleted-item"><span class="d-name">No recent removals by scanner.</span></div>';
      return;
    }
    deletedList.innerHTML = entries.map(e => `<div class="deleted-item">
      <span class="d-name">${esc(e.filename)}</span>
      <span class="d-reason">virus detected</span>
      <span class="d-time">${timeAgo(e.ts)}</span>
    </div>`).join('');
  } catch {
    deletedList.innerHTML = '<div class="deleted-item"><span class="d-name">Failed to load.</span></div>';
  }
}

// ── Utilities ─────────────────────────────────────────────────────────────────
const FILE_ICONS = {
  pdf:'📄', zip:'📦', gz:'📦', tar:'📦', rar:'📦', '7z':'📦', bz2:'📦', xz:'📦',
  png:'🖼', jpg:'🖼', jpeg:'🖼', gif:'🖼', webp:'🖼', bmp:'🖼', avif:'🖼',
  mp4:'🎬', mov:'🎬', avi:'🎬', mkv:'🎬', webm:'🎬',
  mp3:'🎵', wav:'🎵', flac:'🎵', aac:'🎵', ogg:'🎵', m4a:'🎵',
  doc:'📝', docx:'📝', txt:'📝', md:'📝', rtf:'📝', odt:'📝',
  xls:'📊', xlsx:'📊', csv:'📊', ods:'📊',
  json:'⚙', xml:'⚙', yaml:'⚙', yml:'⚙', toml:'⚙',
  py:'⚡', js:'⚡', ts:'⚡', go:'⚡', rs:'⚡', java:'⚡', cpp:'⚡', c:'⚡', css:'⚡',
};
function fileIcon(name) {
  const ext = (name.split('.').pop() || '').toLowerCase();
  return FILE_ICONS[ext] || '📄';
}

function fmtSize(b) {
  if (b < 1024)        return b + ' B';
  if (b < 1_048_576)   return (b / 1024).toFixed(1) + ' KB';
  if (b < 1_073_741_824) return (b / 1_048_576).toFixed(1) + ' MB';
  return (b / 1_073_741_824).toFixed(2) + ' GB';
}

function fmtExpiry(iso) {
  const ms = new Date(iso).getTime() - Date.now();
  if (ms <= 0) return { label: 'expired', cls: 'exp-urgent' };
  const h = ms / 3_600_000;
  if (h >= 48) return { label: Math.floor(h / 24) + 'd',  cls: 'exp-ok' };
  if (h >= 6)  return { label: Math.floor(h) + 'h',       cls: 'exp-ok' };
  if (h >= 1)  return { label: Math.floor(h) + 'h',       cls: 'exp-warn' };
  return             { label: Math.floor(ms / 60_000) + 'm', cls: 'exp-urgent' };
}

function timeAgo(iso) {
  const diff = Date.now() - new Date(iso).getTime();
  const m = Math.floor(diff / 60_000);
  if (m < 1)  return 'just now';
  if (m < 60) return m + 'm ago';
  const h = Math.floor(m / 60);
  if (h < 24) return h + 'h ago';
  return Math.floor(h / 24) + 'd ago';
}

function esc(s) {
  return String(s)
    .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

// ── Toast ─────────────────────────────────────────────────────────────────────
const TOAST_COLORS = { success: '#00F5A0', error: '#FF3366', info: '#00D4FF', warn: '#FF6B35' };
function toast(msg, type = 'info') {
  if (!toastContainer) return;
  const id = 't' + Math.random().toString(36).slice(2);
  const el = document.createElement('div');
  el.className = 'toast'; el.id = id;
  el.innerHTML = `<div class="toast-dot" style="background:${TOAST_COLORS[type]||TOAST_COLORS.info}"></div><span>${esc(msg)}</span>`;
  toastContainer.appendChild(el);
  el.addEventListener('click', () => dismissToast(id));
  setTimeout(() => dismissToast(id), 4000);
}
function dismissToast(id) {
  const el = document.getElementById(id);
  if (!el) return;
  el.classList.add('out');
  setTimeout(() => el.remove(), 200);
}

// ── Init ──────────────────────────────────────────────────────────────────────
loadFileList();
setInterval(loadFileList, 30_000);
