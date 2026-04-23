'use strict';

// ── Session ───────────────────────────────────────────────────────────────────
const SESSION_KEY = 'yeet_session';
function getSession() {
  let s = localStorage.getItem(SESSION_KEY);
  if (!s) {
    s = crypto.randomUUID ? crypto.randomUUID() :
      ([1e7]+-1e3+-4e3+-8e3+-1e11).replace(/[018]/g, c =>
        (c ^ crypto.getRandomValues(new Uint8Array(1))[0] & 15 >> c / 4).toString(16));
    localStorage.setItem(SESSION_KEY, s);
  }
  return s;
}
getSession();

// ── State ─────────────────────────────────────────────────────────────────────
let _allFiles = [];
let _compact = localStorage.getItem('yeet_compact') === '1';
let _accent = localStorage.getItem('yeet_accent') || 'blue';
let _pendingDlId = null;
let _resultUrl = '';

// ── DOM refs ──────────────────────────────────────────────────────────────────
const dropZone      = document.getElementById('drop-zone');
const fileInput     = document.getElementById('file-input');
const uploadForm    = document.getElementById('upload-form');
const uploadSubmit  = document.getElementById('upload-submit');
const progressWrap  = document.getElementById('progress-wrap');
const progressFill  = document.getElementById('progress-fill');
const progressText  = document.getElementById('progress-text');
const uploadFilename= document.getElementById('upload-filename');
const uploadResult  = document.getElementById('upload-result');
const resultUrlEl   = document.getElementById('upload-result-url');
const sortSelect    = document.getElementById('sort-select');
const searchInput   = document.getElementById('search-input');
const fileList      = document.getElementById('file-list');
const fileCount     = document.getElementById('file-count');
const deletedSection= document.getElementById('deleted-section');
const deletedList   = document.getElementById('deleted-list');
const settingsBtn   = document.getElementById('settings-btn');
const settingsModal = document.getElementById('settings-modal');
const settingsClose = document.getElementById('settings-close');
const compactToggle = document.getElementById('compact-toggle');
const pwModal       = document.getElementById('pw-modal');
const pwModalClose  = document.getElementById('pw-modal-close');
const pwModalInput  = document.getElementById('pw-modal-input');
const pwSubmit      = document.getElementById('pw-submit');
const pwError       = document.getElementById('pw-error');
const toastContainer= document.getElementById('toast-container');

// ── Init settings ─────────────────────────────────────────────────────────────
(function initSettings() {
  if (_compact && compactToggle) compactToggle.checked = true;
  if (_accent === 'mono') applyAccent('mono');
  document.querySelectorAll('[data-setting="accent"]').forEach(b => {
    b.classList.toggle('active', b.dataset.value === _accent && _accent === 'blue');
    b.classList.toggle('active-mono', b.dataset.value === _accent && _accent === 'mono');
  });
})();

function applyAccent(val) {
  if (val === 'mono') {
    document.documentElement.style.setProperty('--accent', '#fff');
    document.documentElement.style.setProperty('--accent-hover', '#ccc');
  } else {
    document.documentElement.style.setProperty('--accent', '#3b82f6');
    document.documentElement.style.setProperty('--accent-hover', '#2563eb');
  }
}

// ── Drag-and-drop + file select ───────────────────────────────────────────────
if (dropZone) {
  dropZone.addEventListener('click', () => fileInput && fileInput.click());
  dropZone.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') fileInput && fileInput.click(); });
  dropZone.addEventListener('dragover', e => { e.preventDefault(); dropZone.classList.add('drag-over'); });
  dropZone.addEventListener('dragleave', e => { if (!dropZone.contains(e.relatedTarget)) dropZone.classList.remove('drag-over'); });
  dropZone.addEventListener('drop', e => {
    e.preventDefault(); dropZone.classList.remove('drag-over');
    const f = e.dataTransfer.files[0];
    if (f) setSelectedFile(f);
  });
}
if (fileInput) {
  fileInput.addEventListener('change', () => {
    if (fileInput.files[0]) setSelectedFile(fileInput.files[0]);
    fileInput.value = '';
  });
}

let _selectedFile = null;
function setSelectedFile(f) {
  _selectedFile = f;
  if (uploadFilename) uploadFilename.textContent = `${f.name} (${fmtSize(f.size)})`;
}

// ── Upload ────────────────────────────────────────────────────────────────────
if (uploadForm) {
  uploadForm.addEventListener('submit', async e => {
    e.preventDefault();
    if (!_selectedFile) { toast('Select a file first', 'warn'); return; }
    if (window.YEET_CONFIG?.storageBlocked) { toast('Storage full — uploads disabled', 'error'); return; }

    const form = new FormData();
    form.append('file', _selectedFile);
    form.append('password', document.getElementById('pw-input')?.value || '');
    form.append('bypass_code', document.getElementById('bypass-input')?.value || '');
    form.append('session_id', getSession());

    if (progressWrap) progressWrap.style.display = 'flex';
    if (progressFill) progressFill.style.width = '0%';
    if (progressText) progressText.textContent = '0%';
    if (uploadSubmit) uploadSubmit.disabled = true;
    if (uploadResult) uploadResult.classList.add('hidden');

    try {
      const resp = await new Promise((resolve, reject) => {
        const xhr = new XMLHttpRequest();
        xhr.open('POST', '/upload');
        xhr.upload.onprogress = ev => {
          if (ev.lengthComputable) {
            const pct = Math.round(ev.loaded / ev.total * 100);
            if (progressFill) progressFill.style.width = pct + '%';
            if (progressText) progressText.textContent = pct + '%';
          }
        };
        xhr.onload = () => resolve({ status: xhr.status, body: xhr.responseText });
        xhr.onerror = () => reject(new Error('Network error'));
        xhr.send(form);
      });

      const json = JSON.parse(resp.body);
      if (resp.status === 201) {
        _selectedFile = null;
        if (uploadFilename) uploadFilename.textContent = '';
        if (document.getElementById('pw-input')) document.getElementById('pw-input').value = '';
        if (document.getElementById('bypass-input')) document.getElementById('bypass-input').value = '';

        _resultUrl = json.url;
        if (resultUrlEl) resultUrlEl.textContent = json.url;
        if (uploadResult) uploadResult.classList.remove('hidden');

        toast(`Uploaded — link ready`, 'success');
        if (json.warning) toast('⚠ ' + json.warning, 'warn');
        loadFileList();
      } else {
        toast(json.error || 'Upload failed', 'error');
      }
    } catch (err) {
      toast('Upload failed: ' + err.message, 'error');
    } finally {
      if (progressWrap) progressWrap.style.display = 'none';
      if (uploadSubmit) uploadSubmit.disabled = false;
    }
  });
}

function copyResult() {
  if (!_resultUrl) return;
  navigator.clipboard.writeText(_resultUrl).then(() => {
    const btn = document.getElementById('upload-result-copy');
    if (btn) { btn.textContent = 'copied!'; setTimeout(() => { btn.textContent = 'copy'; }, 2000); }
  });
}

function dismissResult() {
  if (uploadResult) uploadResult.classList.add('hidden');
}

// ── File list ─────────────────────────────────────────────────────────────────
const FILE_ICONS = {
  pdf:'📄', zip:'📦', gz:'📦', tar:'📦', rar:'📦', '7z':'📦', bz2:'📦', xz:'📦',
  png:'🖼️', jpg:'🖼️', jpeg:'🖼️', gif:'🖼️', webp:'🖼️', bmp:'🖼️', avif:'🖼️',
  mp4:'🎬', mov:'🎬', avi:'🎬', mkv:'🎬', webm:'🎬',
  mp3:'🎵', wav:'🎵', flac:'🎵', aac:'🎵', ogg:'🎵', m4a:'🎵',
  doc:'📝', docx:'📝', txt:'📝', md:'📝', rtf:'📝', odt:'📝',
  xls:'📊', xlsx:'📊', csv:'📊', ods:'📊',
  ppt:'📊', pptx:'📊',
  json:'⚙', xml:'⚙', yaml:'⚙', yml:'⚙', toml:'⚙',
  py:'⚡', js:'⚡', ts:'⚡', go:'⚡', rs:'⚡', java:'⚡', cpp:'⚡', c:'⚡',
};
function fileIcon(name) {
  const ext = (name.split('.').pop() || '').toLowerCase();
  return FILE_ICONS[ext] || '📄';
}

async function loadFileList() {
  try {
    const r = await fetch('/api/files/all');
    const data = await r.json();
    _allFiles = data.files || [];
    renderFiles();
  } catch {
    if (fileList) fileList.innerHTML = '<div class="empty-state"><div class="empty-title">Failed to load files</div></div>';
  }
}

function renderFiles() {
  if (!fileList) return;
  const q = (searchInput?.value || '').toLowerCase();
  const sort = sortSelect?.value || 'recent';
  const mySession = getSession();

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
    fileList.innerHTML = `<div class="empty-state">
      <div class="empty-title">${q ? `No results for "${esc(q)}"` : 'No files yet'}</div>
      <div class="empty-sub">${q ? 'Try a different search term' : 'Upload a file above to get started'}</div>
    </div>`;
    return;
  }

  const rowH = _compact ? 36 : 48;
  const canPreview = f => /\.(jpg|jpeg|png|gif|webp|avif|pdf)$/i.test(f.orig_name);

  fileList.innerHTML = list.map(f => {
    const exp = fmtExpiry(f.expires_at);
    const isOwn = f.uploader_session && f.uploader_session === mySession;
    const icon = f.has_password ? '🔒' : fileIcon(f.orig_name);
    const pwBadge = f.has_password ? `<span class="pw-badge">pw</span>` : '';
    const previewBtn = canPreview(f) ? `<button class="icon-btn" data-action="preview" data-id="${esc(f.id)}" title="Preview">👁</button>` : '';
    const dlBtn = `<button class="icon-btn" data-action="download" data-id="${esc(f.id)}" data-pw="${f.has_password ? '1' : '0'}" title="Download">⬇</button>`;
    const delBtn = isOwn ? `<button class="icon-btn danger" data-action="delete" data-id="${esc(f.id)}" title="Delete">🗑</button>` : '';
    return `<div class="file-row${_compact ? ' compact' : ''}" style="height:${rowH}px" id="row-${f.id}">
      <div class="file-icon">${icon}</div>
      <div class="file-name-cell">
        <span class="file-name">${esc(f.orig_name)}</span>
        ${pwBadge}
      </div>
      <div class="file-size">${fmtSize(f.file_size)}</div>
      <div class="file-expiry ${exp.cls}">${exp.label}</div>
      <div class="row-actions">${previewBtn}${dlBtn}${delBtn}</div>
    </div>`;
  }).join('');
}

// ── File list events ──────────────────────────────────────────────────────────
if (fileList) {
  fileList.addEventListener('click', async e => {
    const btn = e.target.closest('[data-action]');
    if (!btn) return;
    const { action, id, pw } = btn.dataset;

    if (action === 'preview') {
      window.open(`/preview/${id}`, '_blank');
    }
    if (action === 'download') {
      if (pw === '1') {
        _pendingDlId = id;
        if (pwModalInput) pwModalInput.value = '';
        if (pwError) pwError.textContent = '';
        if (pwModal) pwModal.classList.remove('hidden');
        setTimeout(() => pwModalInput?.focus(), 60);
      } else {
        window.location.href = `/f/${id}`;
      }
    }
    if (action === 'delete') {
      await deleteFile(id);
    }
  });
}

async function deleteFile(fileId) {
  if (!confirm('Delete this file permanently?')) return;
  try {
    const r = await fetch(`/api/files/${fileId}`, {
      method: 'DELETE',
      headers: { 'X-Session-ID': getSession() },
    });
    if (r.ok) {
      _allFiles = _allFiles.filter(f => f.id !== fileId);
      const row = document.getElementById(`row-${fileId}`);
      if (row) row.remove();
      toast('File deleted', 'warn');
    } else {
      const j = await r.json().catch(() => ({}));
      toast(j.error || 'Delete failed', 'error');
    }
  } catch {
    toast('Delete failed: network error', 'error');
  }
}

// ── Search + sort ─────────────────────────────────────────────────────────────
if (searchInput) searchInput.addEventListener('input', renderFiles);
if (sortSelect) sortSelect.addEventListener('change', renderFiles);

// ── Password modal ────────────────────────────────────────────────────────────
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
    pwSubmit.disabled = true;
    pwSubmit.textContent = 'Checking…';

    try {
      const r = await fetch('/api/verify-password', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ file_id: _pendingDlId, password: pw }),
      });
      const data = await r.json();
      if (data.success) {
        closePwModal();
        window.location.href = data.download_url;
      } else {
        if (pwError) pwError.textContent = data.error || 'Incorrect password.';
      }
    } catch {
      if (pwError) pwError.textContent = 'Network error. Try again.';
    } finally {
      pwSubmit.disabled = false;
      pwSubmit.textContent = 'Unlock & download';
    }
  });
}

if (pwModalInput) {
  pwModalInput.addEventListener('keydown', e => { if (e.key === 'Enter') pwSubmit?.click(); });
}

// ── Settings modal ────────────────────────────────────────────────────────────
if (settingsBtn) settingsBtn.addEventListener('click', () => settingsModal?.classList.remove('hidden'));
if (settingsClose) settingsClose.addEventListener('click', () => settingsModal?.classList.add('hidden'));
if (settingsModal) settingsModal.addEventListener('click', e => { if (e.target === settingsModal) settingsModal.classList.add('hidden'); });

document.querySelectorAll('[data-setting]').forEach(btn => {
  btn.addEventListener('click', () => {
    const { setting, value } = btn.dataset;
    if (setting === 'accent') {
      _accent = value;
      localStorage.setItem('yeet_accent', value);
      applyAccent(value);
      document.querySelectorAll('[data-setting="accent"]').forEach(b => {
        b.classList.remove('active', 'active-mono');
      });
      btn.classList.add(value === 'mono' ? 'active-mono' : 'active');
    }
  });
});

if (compactToggle) {
  compactToggle.addEventListener('change', e => {
    _compact = e.target.checked;
    localStorage.setItem('yeet_compact', _compact ? '1' : '0');
    renderFiles();
  });
}

// ── Deleted / virus log ───────────────────────────────────────────────────────
if (deletedSection) {
  deletedSection.addEventListener('toggle', () => {
    if (deletedSection.open && deletedList && !deletedList.children.length) {
      loadDeletedFiles();
    }
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
    deletedList.innerHTML = entries.map(e => `
      <div class="deleted-item">
        <span class="d-name">${esc(e.filename)}</span>
        <span class="d-reason">virus detected</span>
        <span class="d-time">${timeAgo(e.ts)}</span>
      </div>`).join('');
  } catch {
    deletedList.innerHTML = '<div class="deleted-item"><span class="d-name">Failed to load.</span></div>';
  }
}

// ── Utilities ─────────────────────────────────────────────────────────────────
function fmtSize(b) {
  if (b < 1024) return b + ' B';
  if (b < 1_048_576) return (b / 1024).toFixed(1) + ' KB';
  if (b < 1_073_741_824) return (b / 1_048_576).toFixed(1) + ' MB';
  return (b / 1_073_741_824).toFixed(2) + ' GB';
}

function fmtExpiry(isoStr) {
  const ms = new Date(isoStr).getTime() - Date.now();
  if (ms <= 0) return { label: 'expired', cls: 'exp-urgent' };
  const h = ms / 3_600_000;
  if (h >= 48) return { label: Math.floor(h / 24) + 'd', cls: 'exp-ok' };
  if (h >= 6)  return { label: Math.floor(h) + 'h', cls: 'exp-ok' };
  if (h >= 1)  return { label: Math.floor(h) + 'h', cls: 'exp-warn' };
  return { label: Math.floor(ms / 60_000) + 'm', cls: 'exp-urgent' };
}

function timeAgo(isoStr) {
  const diff = Date.now() - new Date(isoStr).getTime();
  const m = Math.floor(diff / 60_000);
  if (m < 1) return 'just now';
  if (m < 60) return m + 'm ago';
  const h = Math.floor(m / 60);
  if (h < 24) return h + 'h ago';
  return Math.floor(h / 24) + 'd ago';
}

function esc(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

// ── Toast ─────────────────────────────────────────────────────────────────────
const TOAST_COLORS = { success: '#22c55e', error: '#ef4444', info: '#3b82f6', warn: '#f59e0b' };
function toast(message, type = 'info') {
  if (!toastContainer) return;
  const id = 't' + Math.random().toString(36).slice(2);
  const el = document.createElement('div');
  el.className = 'toast'; el.id = id;
  el.innerHTML = `<div class="toast-dot" style="background:${TOAST_COLORS[type] || TOAST_COLORS.info}"></div><span>${esc(message)}</span>`;
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
