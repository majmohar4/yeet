'use strict';

// ── DOM refs ──────────────────────────────────────────────────────────────────
const dropZone   = document.getElementById('dropZone');
const fileInput  = document.getElementById('fileInput');
const uploadForm = document.getElementById('uploadForm');
const fileInfo   = document.getElementById('fileInfo');
const submitBtn  = document.getElementById('submitBtn');
const result     = document.getElementById('result');
const resultUrl  = document.getElementById('resultUrl');
const resultMeta = document.getElementById('resultMeta');
const resultWarn = document.getElementById('resultWarning');
const errorBox   = document.getElementById('errorBox');
const passwordInput = document.getElementById('password');
const passwordWarn  = document.getElementById('passwordWarn');

let selectedFile = null;
const LS_KEY = 'yeet_file_ids';

// ── Drag-and-drop ─────────────────────────────────────────────────────────────
if (dropZone) {
  dropZone.addEventListener('dragover', e => { e.preventDefault(); dropZone.classList.add('drag-over'); });
  dropZone.addEventListener('dragleave', () => dropZone.classList.remove('drag-over'));
  dropZone.addEventListener('drop', e => {
    e.preventDefault(); dropZone.classList.remove('drag-over');
    if (e.dataTransfer.files[0]) selectFile(e.dataTransfer.files[0]);
  });
  dropZone.addEventListener('click', () => fileInput && fileInput.click());
}
if (fileInput) {
  fileInput.addEventListener('change', () => { if (fileInput.files[0]) selectFile(fileInput.files[0]); });
}

function selectFile(f) {
  selectedFile = f;
  fileInfo.textContent = `${f.name}  (${fmtSize(f.size)})`;
  dropZone && dropZone.classList.add('hidden');
  uploadForm.classList.remove('hidden');
  hideError();
}

// ── Password warning ──────────────────────────────────────────────────────────
if (passwordInput && passwordWarn) {
  passwordInput.addEventListener('input', () => {
    passwordWarn.classList.toggle('hidden', !passwordInput.value);
  });
}

// ── Upload ────────────────────────────────────────────────────────────────────
if (uploadForm) {
  uploadForm.addEventListener('submit', async e => {
    e.preventDefault();
    if (!selectedFile) return;

    const form = new FormData();
    form.append('file', selectedFile);
    form.append('password', document.getElementById('password').value || '');
    form.append('max_downloads', document.getElementById('maxDownloads').value || '');
    form.append('bypass_code', document.getElementById('bypassCode')?.value || '');

    setLoading(true);
    hideError();

    // Progress bar
    const pw = document.createElement('div'); pw.className = 'progress-wrap';
    const pb = document.createElement('div'); pb.className = 'progress-bar';
    pw.appendChild(pb); uploadForm.appendChild(pw);

    try {
      const data = await new Promise((resolve, reject) => {
        const xhr = new XMLHttpRequest();
        xhr.open('POST', '/upload');
        xhr.upload.onprogress = ev => { if (ev.lengthComputable) pb.style.width = (ev.loaded / ev.total * 100) + '%'; };
        xhr.onload = () => resolve({ status: xhr.status, body: xhr.responseText });
        xhr.onerror = () => reject(new Error('Network error'));
        xhr.send(form);
      });

      const json = JSON.parse(data.body);
      if (data.status === 201) {
        storeFileId(json.id);
        showResult(json);
        loadFileList();
      } else {
        showError(json.error || 'Upload failed.');
      }
    } catch (err) {
      showError('Upload failed: ' + err.message);
    } finally {
      setLoading(false); pw.remove();
    }
  });
}

function showResult(json) {
  resultUrl.value = json.url;
  const expiry = new Date(json.expires_at).toLocaleString();
  const scan = json.scan_status === 'clean' ? '✓ scanned' :
               json.scan_status === 'skipped' ? '⚠ scan skipped' : json.scan_status;
  const bypassed = json.bypassed ? '  · ⚡ bypass used' : '';
  resultMeta.textContent = `expires ${expiry}  ·  ${fmtSize(json.size)}  ·  ${scan}${bypassed}`;
  if (json.warning) {
    resultWarn.textContent = '⚠ ' + json.warning;
    resultWarn.classList.remove('hidden');
  } else {
    resultWarn.classList.add('hidden');
  }
  uploadForm.classList.add('hidden');
  result.classList.remove('hidden');
}

function resetForm() {
  selectedFile = null;
  if (fileInput) fileInput.value = '';
  document.getElementById('password').value = '';
  document.getElementById('maxDownloads').value = '';
  if (document.getElementById('bypassCode')) document.getElementById('bypassCode').value = '';
  if (passwordWarn) passwordWarn.classList.add('hidden');
  fileInfo.textContent = '';
  result.classList.add('hidden');
  errorBox.classList.add('hidden');
  uploadForm.classList.add('hidden');
  if (dropZone) dropZone.classList.remove('hidden');
}

function copyLink() {
  resultUrl.select();
  navigator.clipboard.writeText(resultUrl.value).then(() => {
    const btn = document.getElementById('copyBtn');
    btn.textContent = 'copied!';
    setTimeout(() => { btn.textContent = 'copy'; }, 2000);
  });
}

function setLoading(on) {
  submitBtn.disabled = on;
  submitBtn.querySelector('.btn-text').classList.toggle('hidden', on);
  submitBtn.querySelector('.btn-loading').classList.toggle('hidden', !on);
}

function showError(msg) { errorBox.textContent = msg; errorBox.classList.remove('hidden'); }
function hideError() { errorBox.classList.add('hidden'); }

// ── File ID storage (localStorage) ───────────────────────────────────────────
function storeFileId(id) {
  const ids = getStoredIds();
  if (!ids.includes(id)) ids.unshift(id);
  localStorage.setItem(LS_KEY, JSON.stringify(ids.slice(0, 50)));
}

function getStoredIds() {
  try { return JSON.parse(localStorage.getItem(LS_KEY) || '[]'); }
  catch { return []; }
}

// ── File list ──────────────────────────────────────────────────────────────────
let _allFiles = [];

async function loadFileList() {
  const ids = getStoredIds();
  if (!ids.length) { renderFiles([]); return; }

  try {
    const r = await fetch('/api/files?ids=' + ids.join(','));
    const data = await r.json();
    _allFiles = data.files || [];
    filterFiles();
  } catch { renderFiles([]); }
}

function filterFiles() {
  const q = (document.getElementById('searchInput')?.value || '').toLowerCase();
  const sizeFilter = document.getElementById('filterSize')?.value || '';
  const statusFilter = document.getElementById('filterStatus')?.value || '';

  let files = _allFiles.filter(f => {
    if (q && !f.orig_name.toLowerCase().includes(q)) return false;
    if (sizeFilter === 'small'  && f.file_size >= 1_048_576) return false;
    if (sizeFilter === 'medium' && (f.file_size < 1_048_576 || f.file_size > 10_485_760)) return false;
    if (sizeFilter === 'large'  && f.file_size <= 10_485_760) return false;
    if (statusFilter === 'active'  && (f.expired || f.archived)) return false;
    if (statusFilter === 'expired' && !f.expired && !f.archived) return false;
    return true;
  });

  renderFiles(files);
}

function renderFiles(files) {
  const el = document.getElementById('fileList');
  if (!el) return;
  if (!files.length) {
    el.innerHTML = '<p class="file-list-empty">No uploads yet — drop a file above to get started.</p>';
    return;
  }

  el.innerHTML = files.map(f => {
    const archived = f.archived;
    const expired  = f.expired && !archived;
    const url      = `/f/${f.id}`;
    const lock     = f.has_password ? `<span class="fcr-badge badge-lock">🔒</span>` : '';
    const status   = archived ? `<span class="fcr-badge badge-warn">archived</span>` :
                     expired  ? `<span class="fcr-badge badge-warn">expired</span>` :
                                `<span class="fcr-badge badge-ok">active</span>`;
    const dl = archived
      ? `<span class="fcr-meta">⚠ archived — contact <a href="mailto:info@majmohar.eu">info@majmohar.eu</a></span>`
      : `<a href="${url}" class="fcr-link" target="_blank">open</a>
         <a href="${url}" class="fcr-link" download>⬇</a>`;
    const dlCount = f.max_downloads
      ? ` · ${f.download_count}/${f.max_downloads} downloads`
      : ` · ${f.download_count} downloads`;
    return `
      <div class="file-card-row${archived ? ' file-card-row--archived' : ''}">
        <div class="fcr-info">
          <div class="fcr-name">${esc(f.orig_name)}${lock}${status}</div>
          <div class="fcr-meta">${fmtSize(f.file_size)} · exp ${f.expires_at.slice(0,10)}${dlCount}</div>
        </div>
        <div class="fcr-actions">${dl}</div>
      </div>`;
  }).join('');
}

// ── Virus / deleted files ─────────────────────────────────────────────────────
const deletedSection = document.getElementById('deletedSection');
const deletedList    = document.getElementById('deletedList');

if (deletedSection) {
  deletedSection.addEventListener('toggle', () => {
    if (deletedSection.open && deletedList.querySelector('.file-list-empty')) {
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
      deletedList.innerHTML = '<p class="file-list-empty">No files deleted by scanner.</p>';
      return;
    }
    deletedList.innerHTML = entries.map(e =>
      `<div class="deleted-entry">
        <span class="deleted-ts">${e.ts.slice(0,16)}</span>
        <span class="deleted-name">${esc(e.filename)}</span>
        <span class="deleted-threat">${esc(e.threat)}</span>
       </div>`
    ).join('');
  } catch {
    deletedList.innerHTML = '<p class="file-list-empty">Failed to load.</p>';
  }
}

// ── Utilities ─────────────────────────────────────────────────────────────────
function fmtSize(bytes) {
  if (bytes < 1024) return bytes + ' B';
  if (bytes < 1_048_576) return (bytes / 1024).toFixed(1) + ' KB';
  return (bytes / 1_048_576).toFixed(2) + ' MB';
}

function esc(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

// ── Init ──────────────────────────────────────────────────────────────────────
loadFileList();
