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
let   _allBundles  = [];
let   _clipItems   = [];
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
const folderInput    = document.getElementById('folder-input');
const photoInput     = document.getElementById('photo-input');
const clipboardPanel = document.getElementById('clipboard-panel');
const clipboardList  = document.getElementById('clipboard-list');
const clipboardCount = document.getElementById('clipboard-count');
const clipboardEmpty = document.getElementById('clipboard-empty');
const dzBurn         = document.getElementById('dz-burn');
const quickPaste     = document.getElementById('quick-paste');
const quickPhoto     = document.getElementById('quick-photo');
const quickFolder    = document.getElementById('quick-folder');
const pasteModal     = document.getElementById('paste-modal');
const pasteClose     = document.getElementById('paste-close');
const pasteTextarea  = document.getElementById('paste-textarea');
const pasteSave      = document.getElementById('paste-save');
const pasteClear     = document.getElementById('paste-clear');
const pastePickImg   = document.getElementById('paste-pick-img');

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

    const looseFiles = [];
    const folderEntries = [];
    if (e.dataTransfer.items) {
      for (const item of Array.from(e.dataTransfer.items)) {
        if (item.kind !== 'file') continue;
        const entry = typeof item.webkitGetAsEntry === 'function' ? item.webkitGetAsEntry() : null;
        if (entry && entry.isDirectory) { folderEntries.push(entry); continue; }
        const f = item.getAsFile();
        if (!f) continue;
        if (!(await _probeReadable(f))) continue;
        looseFiles.push(f);
      }
    } else {
      for (const f of Array.from(e.dataTransfer.files)) {
        if (!(await _probeReadable(f))) continue;
        looseFiles.push(f);
      }
    }

    // Folders → bundle uploads
    for (const entry of folderEntries) {
      const collected = await _walkDirectory(entry, entry.name);
      if (collected.length) await uploadBundle(collected, entry.name);
      else toast('Empty folder — nothing to upload', 'warn');
    }

    if (looseFiles.length) await uploadFiles(looseFiles);
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

  // Image → save as clipboard image (lives in the clipboard manager).
  // Hold Shift when pasting to upload as a regular file instead.
  const imgItem = items.find(i => i.type.startsWith('image/'));
  if (imgItem) {
    const blob = imgItem.getAsFile();
    const ext  = imgItem.type.split('/')[1] || 'png';
    if (e.shiftKey) {
      await uploadFiles([new File([blob], `pasted-image.${ext}`, { type: imgItem.type })]);
    } else {
      await saveClipboardImage(blob, imgItem.type);
    }
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

async function saveClipboardImage(blob, mime) {
  try {
    const dataUrl = await new Promise((res, rej) => {
      const fr = new FileReader();
      fr.onload  = () => res(fr.result);
      fr.onerror = () => rej(fr.error);
      fr.readAsDataURL(blob);
    });
    const expiryMinutes = _expiry * 60;
    const r = await fetch('/api/clipboard/paste', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        content_type: 'image',
        content: dataUrl,
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
      toast('Image saved — link copied! (Shift+V to upload as file)', 'success');
      if (clipboardPanel) clipboardPanel.open = true;
      await loadClipboardItems();
    } else {
      toast(data.error || 'Failed to save image', 'error');
    }
  } catch {
    toast('Failed to save clipboard image', 'error');
  }
}

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
      if (clipboardPanel) clipboardPanel.open = true;
      await loadClipboardItems();
    } else {
      toast(data.error || 'Failed to save text', 'error');
    }
  } catch {
    toast('Failed to save clipboard text', 'error');
  }
}

// ── Folder upload ─────────────────────────────────────────────────────────────
if (quickFolder && folderInput) {
  quickFolder.addEventListener('click', e => {
    e.stopPropagation();
    folderInput.click();
  });
}
if (folderInput) {
  folderInput.addEventListener('change', async () => {
    const files = Array.from(folderInput.files);
    folderInput.value = '';
    if (!files.length) return;
    // The browser provides webkitRelativePath ("root/sub/file.txt") for folder
    // selections — that becomes our bundle path.
    const collected = files.map(f => ({
      file: f,
      path: f.webkitRelativePath || f.name,
    }));
    const root = (collected[0].path.split('/')[0]) || 'folder';
    await uploadBundle(collected, root);
  });
}

// ── Photo picker (camera or photo library) ────────────────────────────────────
if (quickPhoto && photoInput) {
  quickPhoto.addEventListener('click', e => {
    e.stopPropagation();
    photoInput.click();
  });
  photoInput.addEventListener('change', async () => {
    const files = Array.from(photoInput.files);
    photoInput.value = '';
    if (!files.length) return;
    // Single image → save as clipboard image so it lives in the manager.
    const f = files[0];
    if (f.type && f.type.startsWith('image/')) {
      await saveClipboardImage(f, f.type);
    } else {
      await uploadFiles(files);
    }
  });
}

// ── Paste modal (mobile/tablet helper) ────────────────────────────────────────
function openPasteModal() {
  if (!pasteModal) return;
  pasteModal.classList.remove('hidden');
  setTimeout(() => pasteTextarea?.focus(), 60);
  // Try the async clipboard API first — works on https origins after a user
  // gesture, even on Android Chrome and iOS 14+ Safari.
  tryPrefillFromClipboard();
}
function closePasteModal() {
  if (!pasteModal) return;
  pasteModal.classList.add('hidden');
  if (pasteTextarea) pasteTextarea.value = '';
}

async function tryPrefillFromClipboard() {
  if (!navigator.clipboard) return;
  // Prefer rich (image) clipboard when available
  if (navigator.clipboard.read) {
    try {
      const items = await navigator.clipboard.read();
      for (const it of items) {
        const imgType = it.types.find(t => t.startsWith('image/'));
        if (imgType) {
          const blob = await it.getType(imgType);
          closePasteModal();
          await saveClipboardImage(blob, imgType);
          return;
        }
      }
    } catch { /* fall through to text */ }
  }
  if (navigator.clipboard.readText) {
    try {
      const txt = await navigator.clipboard.readText();
      if (txt && pasteTextarea) {
        pasteTextarea.value = txt;
      }
    } catch { /* user denied / not allowed */ }
  }
}

if (quickPaste) {
  quickPaste.addEventListener('click', e => {
    e.stopPropagation();
    openPasteModal();
  });
}
if (pasteClose)  pasteClose.addEventListener('click', closePasteModal);
if (pasteModal)  pasteModal.addEventListener('click', e => { if (e.target === pasteModal) closePasteModal(); });
if (pasteClear)  pasteClear.addEventListener('click', () => { if (pasteTextarea) pasteTextarea.value = ''; pasteTextarea?.focus(); });
if (pastePickImg) pastePickImg.addEventListener('click', () => { closePasteModal(); photoInput?.click(); });
if (pasteSave) {
  pasteSave.addEventListener('click', async () => {
    const txt = (pasteTextarea?.value || '').trim();
    if (!txt) { toast('Nothing to save', 'warn'); pasteTextarea?.focus(); return; }
    pasteSave.disabled = true; pasteSave.textContent = 'Saving…';
    try { await saveClipboardSnippet(txt); }
    finally { pasteSave.disabled = false; pasteSave.textContent = 'Save text'; closePasteModal(); }
  });
}
// Pasting directly inside the textarea — intercept image paste to upload it
if (pasteTextarea) {
  pasteTextarea.addEventListener('paste', async e => {
    const items = e.clipboardData ? Array.from(e.clipboardData.items) : [];
    const img = items.find(i => i.type && i.type.startsWith('image/'));
    if (img) {
      e.preventDefault();
      const blob = img.getAsFile();
      if (blob) { closePasteModal(); await saveClipboardImage(blob, img.type); }
    }
    // text falls through to default behaviour
  });
}

async function _walkDirectory(entry, rootPath = '') {
  const out = [];
  async function walk(e, prefix) {
    if (e.isFile) {
      const f = await new Promise(res => e.file(res, () => res(null)));
      if (f) out.push({ file: f, path: prefix });
    } else if (e.isDirectory) {
      const reader = e.createReader();
      const entries = await new Promise(res => {
        const all = [];
        const read = () => reader.readEntries(batch => {
          if (!batch.length) return res(all);
          all.push(...batch);
          read();
        }, () => res(all));
        read();
      });
      for (const child of entries) {
        await walk(child, prefix + '/' + child.name);
      }
    }
  }
  await walk(entry, rootPath);
  return out;
}

async function uploadBundle(items, displayName) {
  if (_cfg.storageBlocked) { toast('Storage full — uploads disabled', 'error'); return; }
  if (!items.length) return;

  const uid = 'b' + Math.random().toString(36).slice(2, 8);
  const totalSize = items.reduce((s, it) => s + (it.file.size || 0), 0);
  const progEl = mkProgressEl(uid, `📁 ${displayName} (${items.length} files)`, totalSize);
  if (activeUploads) { activeUploads.appendChild(progEl); activeUploads.style.display = ''; }

  const fd = new FormData();
  for (const it of items) fd.append('files', it.file, it.file.name);
  fd.append('paths', items.map(it => it.path).join('\n'));
  fd.append('name', displayName || '');
  fd.append('password', dzPassword ? dzPassword.value : '');
  fd.append('expiry_hours', String(_expiry));
  fd.append('session_id', getSession());

  return new Promise(resolve => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', '/upload-bundle');
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
        toast(`Folder uploaded (${json.file_count} files) — link copied!`, 'success');
        loadFileList();
      } else {
        failProgress(uid);
        toast(json.error || 'Folder upload failed', 'error');
      }
      setTimeout(() => removeProgress(uid), 3500);
      resolve(json);
    };
    xhr.onerror = () => { failProgress(uid); toast('Upload failed: network error', 'error'); resolve({}); };
    xhr.send(fd);
  });
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
  if (dzBurn && dzBurn.checked) fd.append('burn', '1');

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
        if (json.warning)  toast('⚠ ' + json.warning,  'warn');
        if (json.advisory) toast('⚠ ' + json.advisory, 'warn');
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
    _allFiles   = data.files   || [];
    _allBundles = data.bundles || [];
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

  // Unified list: bundles + loose files, both annotated with .__kind so the
  // renderer can branch.
  const fileItems   = _allFiles.map(f => ({ ...f, __kind: 'file', __sortName: f.orig_name }));
  const bundleItems = _allBundles.map(b => ({ ...b, __kind: 'bundle', __sortName: b.name, file_size: b.total_size, orig_name: b.name }));
  let list = [...bundleItems, ...fileItems].filter(it => !q || it.__sortName.toLowerCase().includes(q));

  list.sort((a, b) => {
    if (sort === 'recent') return new Date(b.created_at) - new Date(a.created_at);
    if (sort === 'expiry') return new Date(a.expires_at) - new Date(b.expires_at);
    if (sort === 'size')   return (b.file_size || 0) - (a.file_size || 0);
    if (sort === 'name')   return a.__sortName.localeCompare(b.__sortName);
    return 0;
  });

  if (fileCount) fileCount.textContent = list.length;

  if (!list.length) {
    fileGrid.innerHTML = `<div class="empty-state">
      <div class="empty-icon">📂</div>
      <div class="empty-title">${q ? `No results for "${esc(q)}"` : 'No files yet'}</div>
      <div class="empty-sub">${q ? 'Try a different search term' : 'Drop files or a folder above, or paste Ctrl+V'}</div>
    </div>`;
    return;
  }

  const isOwn     = f => f.uploader_session && f.uploader_session === me;
  const isImg     = f => f.mime_type && f.mime_type.startsWith('image/');
  const isText    = f => (f.mime_type || '').startsWith('text/') || /\.(txt|md)$/i.test(f.orig_name);
  const canInline = f => /\.(jpg|jpeg|png|gif|webp|avif|pdf|mp4|webm|mov|mp3|wav|ogg|m4a|txt|md|json|py|js|ts|go|rs)$/i.test(f.orig_name);

  fileGrid.innerHTML = list.map(f => {
    if (f.__kind === 'bundle') {
      const exp     = fmtExpiry(f.expires_at);
      const own     = isOwn(f);
      const dlBtn   = `<a class="icon-btn" href="/b/${esc(f.id)}/zip" title="Download zip">⬇</a>`;
      const linkBtn = `<button class="icon-btn" data-action="copybundlelink" data-id="${esc(f.id)}" title="Copy link">🔗</button>`;
      const delBtn  = own ? `<button class="icon-btn danger" data-action="deletebundle" data-id="${esc(f.id)}" title="Delete folder">🗑</button>` : '';
      return `<div class="file-card bundle-card${f.has_password ? ' protected' : ''}" id="bcard-${esc(f.id)}" data-bundle-id="${esc(f.id)}">
        <a class="card-thumb type-bundle" href="/b/${esc(f.id)}" style="text-decoration:none">
          <div class="card-thumb-icon" style="font-size:38px">📁</div>
          <div class="bundle-card-count">${f.file_count} files</div>
        </a>
        <div class="card-body">
          <div class="card-name" title="${esc(f.name)}">${esc(f.name)}</div>
          <div class="card-meta">
            <span class="card-size">${fmtSize(f.total_size || 0)}</span>
            <span class="card-expiry ${exp.cls}">${exp.label}</span>
          </div>
        </div>
        <div class="card-actions">${dlBtn}${linkBtn}${delBtn}</div>
      </div>`;
    }
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
    const burnBadge = f.burn_after_read ? `<span class="burn-badge" title="Burn after read — deletes after first download">🔥 burn</span>` : '';
    const dangerBadge = f.dangerous ? `<span class="danger-badge" title="Executable or script — only run files from sources you trust">⚠ executable</span>` : '';
    const webBadge = (!f.dangerous && f.web_renderable) ? `<span class="web-badge" title="Browser-renderable type — yeet shows source, never executes">📄 source</span>` : '';

    return `<div class="file-card${f.has_password ? ' protected' : ''}${f.burn_after_read ? ' burn' : ''}${f.dangerous ? ' danger' : ''}" id="card-${esc(f.id)}">
      <div class="card-thumb type-${type}">${thumb}</div>
      <div class="card-body">
        <div class="card-name" title="${esc(f.orig_name)}">${esc(f.orig_name)}${burnBadge}${dangerBadge}${webBadge}</div>
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
    if (action === 'copybundlelink') {
      await navigator.clipboard.writeText(`${location.origin}/b/${btn.dataset.id}`);
      toast('Folder link copied', 'success');
      return;
    }
    if (action === 'delete') {
      await deleteFile(btn.dataset.id);
      return;
    }
    if (action === 'deletebundle') {
      await deleteBundle(btn.dataset.id);
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

async function deleteBundle(id) {
  if (!confirm('Delete this folder and all its files?')) return;
  try {
    const r = await fetch(`/api/bundles/${id}`, { method: 'DELETE', headers: { 'X-Session-ID': getSession() } });
    if (r.ok) {
      _allBundles = _allBundles.filter(b => b.id !== id);
      const card = document.getElementById(`bcard-${id}`);
      if (card) { card.style.transition = 'opacity 240ms, transform 240ms'; card.style.opacity = '0'; card.style.transform = 'scale(0.92)'; setTimeout(() => card.remove(), 260); }
      toast('Folder deleted', 'warn');
      renderFiles();
    } else {
      const j = await r.json().catch(() => ({}));
      toast(j.error || 'Delete failed', 'error');
    }
  } catch { toast('Delete failed: network error', 'error'); }
}

// ── Clipboard manager ─────────────────────────────────────────────────────────
async function loadClipboardItems() {
  if (!clipboardList) return;
  try {
    const r = await fetch('/api/clipboard/recent?limit=50', {
      headers: { 'X-Session-ID': getSession() },
    });
    const data = await r.json();
    _clipItems = data.items || [];
    renderClipboard();
  } catch {
    /* swallow */
  }
}

function renderClipboard() {
  if (!clipboardList) return;
  if (clipboardCount) clipboardCount.textContent = _clipItems.length;
  if (clipboardEmpty) clipboardEmpty.classList.toggle('hidden', _clipItems.length > 0);

  clipboardList.innerHTML = _clipItems.map(it => {
    const isImg = it.type === 'image';
    const preview = isImg
      ? `<img class="clip-thumb-img" src="/api/clipboard/image/${esc(it.id)}" alt="">`
      : `<pre class="clip-thumb-text">${esc((it.preview || '').slice(0, 240))}</pre>`;
    const pinned = it.pinned ? '📌' : '☆';
    const t = timeAgo(it.created_at);
    const exp = it.pinned ? 'pinned' : fmtExpiry(it.expires_at).label;
    const yours = it.is_yours;
    const youBadge = yours ? `<span class="clip-you" title="You posted this">you</span>` : '';
    const ownerActions = yours
      ? `<button class="icon-btn" data-clip-action="pin" title="${it.pinned ? 'Unpin' : 'Pin'}">${pinned}</button>
         <button class="icon-btn danger" data-clip-action="delete" title="Delete">🗑</button>`
      : '';
    return `<div class="clip-item${it.pinned ? ' pinned' : ''}${yours ? ' yours' : ''}" data-clip-id="${esc(it.id)}">
      <div class="clip-thumb">${preview}</div>
      <div class="clip-body">
        <div class="clip-meta">
          <span class="clip-type">${isImg ? '🖼 image' : '📝 text'}</span>
          ${youBadge}
          <span class="clip-time">${t}</span>
          <span class="clip-exp">${exp}</span>
        </div>
      </div>
      <div class="clip-actions">
        <button class="icon-btn" data-clip-action="open" title="Open">↗</button>
        <button class="icon-btn" data-clip-action="copy" title="Copy ${isImg ? 'link' : 'text'}">⎘</button>
        ${ownerActions}
      </div>
    </div>`;
  }).join('');
}

if (clipboardList) {
  clipboardList.addEventListener('click', async e => {
    const btn = e.target.closest('[data-clip-action]');
    if (!btn) return;
    const row = btn.closest('[data-clip-id]');
    const id = row?.dataset.clipId;
    if (!id) return;
    const action = btn.dataset.clipAction;
    const item = _clipItems.find(x => x.id === id);
    const url = `${location.origin}/c/${id}`;

    if (action === 'open') { window.open(url, '_blank', 'noopener'); return; }
    if (action === 'copy') {
      if (item && item.type === 'text') {
        try {
          const r = await fetch(`/c/${id}/raw`);
          const t = await r.text();
          await navigator.clipboard.writeText(t);
          toast('Text copied', 'success');
        } catch { toast('Copy failed', 'error'); }
      } else {
        await navigator.clipboard.writeText(url);
        toast('Link copied', 'success');
      }
      return;
    }
    if (action === 'pin') {
      const r = await fetch(`/api/clipboard/pin/${id}`, {
        method: 'POST',
        headers: { 'X-Session-ID': getSession() },
      });
      if (r.ok) { await loadClipboardItems(); }
      else { toast('Pin failed', 'error'); }
      return;
    }
    if (action === 'delete') {
      if (!confirm('Delete this clipboard item?')) return;
      const r = await fetch(`/api/clipboard/item/${id}`, {
        method: 'DELETE',
        headers: { 'X-Session-ID': getSession() },
      });
      if (r.ok) {
        _clipItems = _clipItems.filter(x => x.id !== id);
        renderClipboard();
        toast('Deleted', 'warn');
      } else {
        toast('Delete failed', 'error');
      }
      return;
    }
  });
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
loadClipboardItems();
setInterval(loadFileList, 30_000);
setInterval(loadClipboardItems, 60_000);
