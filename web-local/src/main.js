import { marked } from 'marked';
import DOMPurify from 'dompurify';
import './style.css';

const $ = (id) => document.getElementById(id);
const queue = [];
const logs = [];
let selected = new Set();
let running = false;
let stopping = false;
let activeTask = null;
let sessionId = crypto.randomUUID();
const sessionLogs = (() => {
  try { return JSON.parse(sessionStorage.getItem('pdf2ai-session-logs') || '[]'); }
  catch { return []; }
})();
let currentResult = null;

function formatMessage(value) {
  if (value instanceof Error) return `${value.name}: ${value.message}`;
  if (typeof value === 'string') return value;
  try { return JSON.stringify(value); } catch { return String(value); }
}

function log(message) {
  const stamp = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  const entry = `${new Date().toISOString()} [${sessionId}] ${message}`;
  sessionLogs.push(entry);
  if (sessionLogs.length > 2000) sessionLogs.shift();
  try { sessionStorage.setItem('pdf2ai-session-logs', JSON.stringify(sessionLogs)); } catch {}
  logs.push(`${stamp}  ${message}`);
  if (logs.length > 100) logs.shift();
  $('detailsLog').textContent = logs.join('\n');
}

function fileKey(file) { return `${file.name}:${file.size}:${file.lastModified}`; }
function outputName(name) { return name.replace(/\.pdf$/i, '') + '.ai.md'; }
function elapsed(start) { return `${Math.max(1, Math.round((performance.now() - start) / 1000))}s`; }

function addFiles(files) {
  let added = 0;
  for (const file of files) {
    if (!file.name.toLowerCase().endsWith('.pdf') && file.type !== 'application/pdf') {
      log(`Skipped non-PDF: ${file.name}`);
      continue;
    }
    if (queue.some((item) => item.key === fileKey(file))) {
      log(`Already queued: ${file.name}`);
      continue;
    }
    queue.push({ key: fileKey(file), file, pages: '—', status: 'Ready', result: null });
    selected.add(fileKey(file));
    added++;
  }
  if (added) log(`Added ${added} PDF${added === 1 ? '' : 's'}. Files remain in this browser.`);
  renderQueue();
}

function getSelectedOrLatestResult() {
  const selectedItems = queue.filter((item) => selected.has(item.key) && item.result);
  if (selectedItems.length > 0) return selectedItems[0].result;
  const doneItems = queue.filter((item) => item.result);
  if (doneItems.length > 0) return doneItems[doneItems.length - 1].result;
  return currentResult;
}

function renderQueue() {
  const body = $('queueBody');
  body.replaceChildren();

  for (const item of queue) {
    const row = document.createElement('tr');
    const isSelected = selected.has(item.key);
    if (isSelected) row.className = 'selected';

    const statusClass = item.status.startsWith('Done')
      ? 'done'
      : item.status === 'Failed'
      ? 'failed'
      : item.status.startsWith('Processing') || item.status.startsWith('Reading') || item.status.startsWith('Recognizing')
      ? 'working'
      : '';

    const actionHtml = item.result
      ? `<div class="row-actions">
           <button class="row-btn view" type="button" title="View output preview">👁️ View</button>
           <button class="row-btn download" type="button" title="Download .ai.md">⬇️ Save</button>
         </div>`
      : `<span style="color:#94a3b8">—</span>`;

    row.innerHTML = `
      <td style="text-align:center;">
        <input type="checkbox" aria-label="Select ${escapeHtml(item.file.name)}" ${isSelected ? 'checked' : ''}>
      </td>
      <td class="file-name" title="${escapeHtml(item.file.name)}">${escapeHtml(item.file.name)}</td>
      <td style="text-align:center;">${item.pages}</td>
      <td class="status ${statusClass}">${escapeHtml(item.status)}</td>
      <td>${actionHtml}</td>
    `;

    // Click anywhere on row toggles selection (except buttons/inputs)
    row.addEventListener('click', (event) => {
      if (event.target.closest('button') || event.target.closest('input')) return;
      if (selected.has(item.key)) {
        selected.delete(item.key);
      } else {
        selected.add(item.key);
      }
      renderQueue();
    });

    // Checkbox direct change
    const checkbox = row.querySelector('input[type="checkbox"]');
    checkbox.addEventListener('change', (event) => {
      event.target.checked ? selected.add(item.key) : selected.delete(item.key);
      renderQueue();
    });

    // Row action buttons
    row.querySelector('.row-btn.view')?.addEventListener('click', (event) => {
      event.stopPropagation();
      openViewer(item.result);
    });

    row.querySelector('.row-btn.download')?.addEventListener('click', (event) => {
      event.stopPropagation();
      downloadResult(item.result);
    });

    body.append(row);
  }

  // Sync select-all checkbox
  const selectAll = $('selectAllCheckbox');
  if (selectAll) {
    selectAll.checked = queue.length > 0 && queue.every((item) => selected.has(item.key));
    selectAll.indeterminate = queue.some((item) => selected.has(item.key)) && !selectAll.checked;
  }

  // Update empty state
  const emptyQueue = $('emptyQueue');
  if (emptyQueue) {
    emptyQueue.hidden = queue.length > 0;
  }

  // Summary & button states
  $('queueSummary').textContent = queue.length ? `${queue.length} PDF${queue.length === 1 ? '' : 's'} queued (${selected.size} selected)` : 'No PDFs queued';
  $('removeButton').disabled = running || selected.size === 0;
  $('clearButton').disabled = running || queue.length === 0;
  $('convertButton').disabled = running || !queue.some((item) => item.status === 'Ready' || item.status === 'Failed');

  // Separate "View Output" button
  const availableResult = getSelectedOrLatestResult();
  const viewOutputBtn = $('viewOutputButton');
  if (viewOutputBtn) {
    viewOutputBtn.disabled = !availableResult;
    if (availableResult) {
      viewOutputBtn.title = `View output for ${availableResult.sourceName}`;
    } else {
      viewOutputBtn.title = 'Convert a PDF first to view output';
    }
  }
}

function escapeHtml(value) {
  return String(value).replace(/[&<>'"]/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' })[char]);
}

// A separate browsing context owns the entire engine, including its worker pools.
// Removing it and terminating its workers does not depend on OCR promises resolving.
function runEngine(file, onProgress) {
  const frame = document.createElement('iframe');
  frame.hidden = true;
  frame.src = new URL('./engine.html', window.location.href).href;
  let settle;
  let finished = false;
  let lastProgress = performance.now();
  const start = lastProgress;
  let stage = 'Starting local engine';
  const cleanup = () => {
    clearInterval(timer);
    window.removeEventListener('message', receive);
    try { frame.contentWindow?.stopEngine?.(); } catch {}
    frame.remove();
  };
  const finish = (error, result) => {
    if (finished) return;
    finished = true;
    cleanup();
    error ? settle.reject(error) : settle.resolve(result);
  };
  const receive = (event) => {
    if (event.source !== frame.contentWindow || event.origin !== location.origin) return;
    const message = event.data;
    if (message.type === 'ready') {
      frame.contentWindow.postMessage({ type: 'convert', file }, location.origin);
    } else if (message.type === 'progress') {
      lastProgress = performance.now();
      stage = message.stage;
      log(stage);
      onProgress(message);
    } else if (message.type === 'done') finish(null, message.result);
    else if (message.type === 'error') finish(new Error(message.error));
  };
  const timer = setInterval(() => {
    const idle = Math.round((performance.now() - lastProgress) / 1000);
    $('timeLabel').textContent = `Elapsed ${elapsed(start)} · last activity ${idle}s ago`;
    if (idle >= 30 && idle % 15 === 0) log(`Still waiting: ${stage}; no progress for ${idle}s; tab ${document.visibilityState}`);
    if (idle >= 60) $('workStage').textContent = `${stage} — waiting ${idle}s. You can stop and retry.`;
    if (idle >= 180) finish(new Error(`Stopped after 3 minutes without progress during: ${stage}. Download session logs and retry.`));
  }, 1000);
  const promise = new Promise((resolve, reject) => { settle = { resolve, reject }; });
  window.addEventListener('message', receive);
  document.body.append(frame);
  return { promise, cancel: () => finish(new DOMException('Conversion stopped.', 'AbortError')) };
}

async function convertItem(item, index, totalFiles) {
  const start = performance.now();
  item.status = 'Processing';
  renderQueue();
  $('workTitle').textContent = `Processing ${item.file.name} · file ${index + 1} of ${totalFiles}`;
  $('workStage').textContent = 'Starting local engine';
  $('progressBar').removeAttribute('value');
  $('workPercent').textContent = 'Preparing';
  log(`Opening ${item.file.name}; bytes=${item.file.size}`);
  activeTask = runEngine(item.file, (message) => {
    $('workStage').textContent = message.stage;
    if (message.total) item.pages = message.total;
    if (message.percent == null) {
      $('progressBar').removeAttribute('value');
      $('workPercent').textContent = 'Preparing';
    } else {
      $('progressBar').value = message.percent;
      $('workPercent').textContent = `${message.percent}%`;
    }
  });
  try {
    const result = await activeTask.promise;
    const { markdown, emptyPages, pages } = result;
    item.pages = pages;
    item.result = { name: outputName(item.file.name), sourceName: item.file.name, markdown,
      blob: new Blob([markdown], { type: 'text/markdown;charset=utf-8' }), pages, emptyPages,
      seconds: Math.round((performance.now() - start) / 1000) };
    item.status = emptyPages.length ? 'Done — review suggested' : 'Done ✓';
    currentResult = item.result;
    log(`${item.file.name}: completed ${pages} pages in ${item.result.seconds}s; empty pages: ${emptyPages.join(', ') || 'none'}`);
  } finally { activeTask = null; }
}

async function convertAll() {
  if (running) return;
  const pending = queue.filter((item) => item.status === 'Ready' || item.status === 'Failed');
  if (!pending.length) { log('Add at least one PDF before converting.'); return; }
  running = true;
  stopping = false;
  sessionId = crypto.randomUUID();
  log(`Session started; build=stall-fix-1; files=${pending.length}; logical CPUs=${navigator.hardwareConcurrency || 'unknown'}; memory GB=${navigator.deviceMemory || 'unknown'}`);
  $('workPanel').hidden = false;
  $('resultPanel').hidden = true;
  $('convertButton').textContent = 'Converting…';
  renderQueue();
  let completed = 0;
  let failed = 0;
  let lastSuccessfulItem = null;

  for (let i = 0; i < pending.length; i++) {
    const item = pending[i];
    if (stopping) { item.status = 'Ready'; continue; }
    try {
      await convertItem(item, i, pending.length);
      completed++;
      lastSuccessfulItem = item;
    } catch (error) {
      if (error?.name === 'AbortError' || stopping) {
        item.status = 'Ready';
        log(`${item.file.name}: stopped.`);
      } else {
        item.status = 'Failed';
        failed++;
        log(`${item.file.name}: ${formatMessage(error)}`);
      }

    }
    renderQueue();
  }

  running = false;
  $('convertButton').textContent = 'Convert';
  $('workPanel').hidden = true;

  if (completed > 0 || failed > 0) {
    $('resultPanel').hidden = false;
    $('resultTitle').textContent = stopping
      ? 'Conversion stopped'
      : `${completed} PDF${completed === 1 ? '' : 's'} converted successfully!`;
    $('resultNote').textContent = failed
      ? `${failed} file${failed === 1 ? '' : 's'} failed. Open Details for error logs.`
      : 'Click "View Output" to inspect preview or "Download" to save .ai.md.';
  }

  if (lastSuccessfulItem && lastSuccessfulItem.result) {
    currentResult = lastSuccessfulItem.result;
    // If only one file was converted, open viewer directly for instant feedback
    if (completed === 1 && pending.length === 1 && !stopping) {
      openViewer(lastSuccessfulItem.result);
    }
  }

  renderQueue();
}

function downloadResult(result) {
  if (!result) return;
  const url = URL.createObjectURL(result.blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = result.name;
  anchor.click();
  setTimeout(() => URL.revokeObjectURL(url), 1500);
  log(`Downloaded ${result.name} to this device.`);
}

function renderPreview(markdown) {
  const pieces = markdown.split(/<!-- PAGE (\d+) -->/g);
  const pages = [];
  for (let i = 1; i < pieces.length; i += 2) {
    const number = pieces[i];
    const html = DOMPurify.sanitize(marked.parse(pieces[i + 1] || '', { breaks: true }));
    pages.push(`<article class="document-page" data-page="${number}"><div class="page-label">Page ${number}</div>${html || '<p><em>No extractable text on this page.</em></p>'}</article>`);
  }
  return pages.join('');
}

function openViewer(result) {
  if (!result) {
    result = getSelectedOrLatestResult();
  }
  if (!result) {
    log('No converted document available to view.');
    return;
  }
  currentResult = result;
  $('viewerName').textContent = result.name;
  $('viewerMeta').textContent = `${result.pages} pages · ${result.seconds}s${result.emptyPages?.length ? ` · Review pages ${result.emptyPages.join(', ')}` : ''}`;
  $('previewPane').innerHTML = renderPreview(result.markdown);
  $('sourcePane').value = result.markdown;
  selectViewerTab('preview');

  const dialog = $('viewer');
  if (typeof dialog.showModal === 'function') {
    try {
      dialog.showModal();
    } catch {
      dialog.setAttribute('open', '');
    }
  } else {
    dialog.setAttribute('open', '');
  }
}

function closeViewer() {
  const dialog = $('viewer');
  if (typeof dialog.close === 'function') {
    try { dialog.close(); } catch {}
  }
  dialog.removeAttribute('open');
}

function selectViewerTab(tab) {
  const preview = tab === 'preview';
  $('previewPane').hidden = !preview;
  $('sourcePane').hidden = preview;
  $('previewTab').classList.toggle('active', preview);
  $('sourceTab').classList.toggle('active', !preview);
}

// Event Listeners
$('chooseButton').addEventListener('click', (event) => { event.stopPropagation(); $('fileInput').click(); });
$('dropZone').addEventListener('click', () => $('fileInput').click());
$('dropZone').addEventListener('keydown', (event) => { if (event.key === 'Enter' || event.key === ' ') $('fileInput').click(); });
$('fileInput').addEventListener('change', (event) => { addFiles(event.target.files); event.target.value = ''; });

for (const name of ['dragenter', 'dragover']) {
  $('dropZone').addEventListener(name, (event) => { event.preventDefault(); $('dropZone').classList.add('dragging'); });
}
for (const name of ['dragleave', 'drop']) {
  $('dropZone').addEventListener(name, (event) => { event.preventDefault(); $('dropZone').classList.remove('dragging'); });
}
$('dropZone').addEventListener('drop', (event) => addFiles(event.dataTransfer.files));

$('selectAllCheckbox')?.addEventListener('change', (event) => {
  if (event.target.checked) {
    queue.forEach((item) => selected.add(item.key));
  } else {
    selected.clear();
  }
  renderQueue();
});

$('removeButton').addEventListener('click', () => {
  for (let i = queue.length - 1; i >= 0; i--) {
    if (selected.has(queue[i].key)) queue.splice(i, 1);
  }
  selected.clear();
  renderQueue();
});

$('clearButton').addEventListener('click', () => {
  queue.length = 0;
  selected.clear();
  currentResult = null;
  renderQueue();
});

$('convertButton').addEventListener('click', convertAll);

// Dedicated "View Output" Button in main action bar
$('viewOutputButton')?.addEventListener('click', () => {
  const res = getSelectedOrLatestResult();
  if (res) openViewer(res);
});

// Result panel buttons
$('resultViewButton')?.addEventListener('click', () => {
  const res = getSelectedOrLatestResult();
  if (res) openViewer(res);
});

$('resultDownloadButton')?.addEventListener('click', () => {
  const res = getSelectedOrLatestResult();
  if (res) downloadResult(res);
});

$('stopButton').addEventListener('click', async () => {
  stopping = true;
  $('workStage').textContent = 'Stopping safely…';
  log('Stop requested; terminating engine and worker pools.');
  activeTask?.cancel();
});

$('downloadLogs').addEventListener('click', () => {
  const url = URL.createObjectURL(new Blob([sessionLogs.join('\n')], { type: 'text/plain;charset=utf-8' }));
  const anchor = document.createElement('a');
  anchor.href = url; anchor.download = `PDF2AI-session-${sessionId}.log`; anchor.click();
  setTimeout(() => URL.revokeObjectURL(url), 1500);
});

$('copyDetails').addEventListener('click', async () => {
  await navigator.clipboard.writeText(sessionLogs.join('\n'));
  $('copyDetails').textContent = 'Copied';
  setTimeout(() => $('copyDetails').textContent = 'Copy details', 1200);
});

$('closeViewer').addEventListener('click', closeViewer);
$('previewTab').addEventListener('click', () => selectViewerTab('preview'));
$('sourceTab').addEventListener('click', () => selectViewerTab('source'));
$('downloadButton').addEventListener('click', () => currentResult && downloadResult(currentResult));
$('printButton').addEventListener('click', () => window.print());
$('viewer').addEventListener('click', (event) => { if (event.target === $('viewer')) closeViewer(); });

window.addEventListener('beforeunload', (event) => {
  if (running) { event.preventDefault(); event.returnValue = ''; }
});

log('Ready. All PDF processing runs locally on this device.');
renderQueue();
