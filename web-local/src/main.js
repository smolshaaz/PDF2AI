import scribe from 'scribe.js-ocr';
import { marked } from 'marked';
import DOMPurify from 'dompurify';
import './style.css';

const $ = (id) => document.getElementById(id);
const queue = [];
const logs = [];
let selected = new Set();
let running = false;
let stopping = false;
let currentDoc = null;
let currentResult = null;

scribe.opt.langPath = new URL('./tessdata', window.location.href).href.replace(/\/$/, '');
scribe.opt.workerN = Math.min(4, Math.max(1, (navigator.hardwareConcurrency || 4) - 1));
scribe.opt.warningHandler = (message) => log(`Warning: ${formatMessage(message)}`);
scribe.opt.errorHandler = (message) => log(`OCR: ${formatMessage(message)}`);
scribe.ScribeDoc.defaults.reflow = true;
scribe.ScribeDoc.defaults.removeMargins = false;
scribe.ScribeDoc.defaults.enableLayout = true;
scribe.ScribeDoc.defaults.usePDFText = {
  native: { supp: true, main: true },
  ocr: { supp: false, main: false },
};

function formatMessage(value) {
  if (value instanceof Error) return `${value.name}: ${value.message}`;
  if (typeof value === 'string') return value;
  try { return JSON.stringify(value); } catch { return String(value); }
}

function log(message) {
  const stamp = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
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

async function assembleMarkdown(doc, fileName) {
  const pages = [];
  const emptyPages = [];
  for (let i = 0; i < doc.inputData.pageCount; i++) {
    const text = String(await doc.exportData('md', { pageArr: [i], enableLayout: true, reflow: true })).trimEnd();
    if (!text.trim()) emptyPages.push(i + 1);
    pages.push(`<!-- PAGE ${i + 1} -->\n\n${text}`);
  }
  const header = `<!-- PDF2AI\nSource: ${fileName}\nPages: ${doc.inputData.pageCount}\nGenerated locally in this browser by PDF2AI\n-->`;
  return { markdown: `${header}\n\n${pages.join('\n\n')}\n`, emptyPages };
}

function setProgress(item, page, total, stage, start) {
  const percent = total ? Math.max(1, Math.min(99, Math.round((page / total) * 100))) : 1;
  $('workTitle').textContent = `Processing ${item.file.name}`;
  $('workStage').textContent = stage;
  $('workPercent').textContent = `${percent}%`;
  $('progressBar').value = percent;
  $('timeLabel').textContent = `Elapsed ${elapsed(start)}`;
}

async function convertItem(item, index, totalFiles) {
  const start = performance.now();
  item.status = 'Processing';
  renderQueue();
  setProgress(item, 0, 1, `Opening file ${index + 1} of ${totalFiles}`, start);
  log(`Opening ${item.file.name}`);
  const doc = await scribe.openDocument([item.file]);
  currentDoc = doc;
  item.pages = doc.inputData.pageCount;
  renderQueue();
  if (!doc.inputData.pageCount) throw new Error('This PDF has no pages.');

  doc.progressHandler = (event) => {
    const page = Number.isInteger(event?.n) ? event.n + 1 : 0;
    const stages = { importPDF: 'Reading PDF', importImage: 'Reading page', recognize: 'Recognizing text', convert: 'Building page', export: 'Creating Markdown', render: 'Rendering page' };
    setProgress(item, page, doc.inputData.pageCount, `${stages[event?.type] || 'Processing'}${page ? ` — page ${page} of ${doc.inputData.pageCount}` : ''}`, start);
  };

  const type = doc.inputData.pdfType;
  if (type !== 'text') {
    setProgress(item, 0, doc.inputData.pageCount, type === 'ocr' ? 'Replacing existing scan text' : 'Recognizing scanned pages', start);
    await doc.recognize({ langs: ['eng', 'ara'], modeAdv: 'lstm', combineMode: 'none' });
  } else {
    log(`${item.file.name}: using its healthy native text layer.`);
  }
  if (stopping) throw new DOMException('Conversion stopped.', 'AbortError');

  setProgress(item, doc.inputData.pageCount, doc.inputData.pageCount, 'Creating Markdown', start);
  const { markdown, emptyPages } = await assembleMarkdown(doc, item.file.name);
  const blob = new Blob([markdown], { type: 'text/markdown;charset=utf-8' });
  item.result = { name: outputName(item.file.name), sourceName: item.file.name, markdown, blob, pages: doc.inputData.pageCount, emptyPages, seconds: Math.round((performance.now() - start) / 1000) };
  item.status = emptyPages.length ? 'Done — review suggested' : 'Done ✓';
  currentResult = item.result;
  log(`${item.file.name}: done in ${item.result.seconds}s${emptyPages.length ? `; little or no text on pages ${emptyPages.join(', ')}` : ''}.`);
  await doc.terminate();
  currentDoc = null;
}

async function convertAll() {
  if (running) return;
  const pending = queue.filter((item) => item.status === 'Ready' || item.status === 'Failed');
  if (!pending.length) { log('Add at least one PDF before converting.'); return; }
  running = true;
  stopping = false;
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
      try { await currentDoc?.terminate(); } catch { /* stopping */ }
      currentDoc = null;
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
  try { await currentDoc?.terminate(); } catch { /* worker is stopping */ }
});

$('copyDetails').addEventListener('click', async () => {
  await navigator.clipboard.writeText($('detailsLog').textContent);
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
