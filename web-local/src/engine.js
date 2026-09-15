import './engine-workers.js';
import scribe from 'scribe.js-ocr';

const send = (message) => parent.postMessage(message, location.origin);
const progress = (stage, total, percent) => send({ type: 'progress', stage, total, percent });
scribe.opt.langPath = new URL('./tessdata', location.href).href.replace(/\/$/, '');
// Bound simultaneous large page images and bilingual recognition on office machines.
scribe.opt.workerN = Math.min(2, Math.max(1, (navigator.hardwareConcurrency || 2) - 1));
scribe.opt.warningHandler = () => progress('Engine reported a warning');
scribe.opt.errorHandler = () => send({ type: 'error', error: 'Local OCR engine failed. Retry or download session logs.' });
scribe.ScribeDoc.defaults.reflow = true;
scribe.ScribeDoc.defaults.removeMargins = false;
scribe.ScribeDoc.defaults.enableLayout = true;
scribe.ScribeDoc.defaults.usePDFText = { native: { supp: true, main: true }, ocr: { supp: false, main: false } };

window.addEventListener('message', async (event) => {
  if (event.source !== parent || event.origin !== location.origin || event.data.type !== 'convert') return;
  try {
    progress('Loading local PDF engine');
    await scribe.init({ font: true });
    const doc = new scribe.ScribeDoc();
    const completed = new Set();
    let phase = 'Reading PDF';
    doc.progressHandler = (event) => {
      const total = doc.inputData.pageCount;
      if (phase === 'Recognizing text' && event.type === 'convert' && Number.isInteger(event.n)) completed.add(event.n);
      const suffix = Number.isInteger(event.n) ? `; page ${event.n + 1}` : '';
      progress(`${phase}${suffix}; ${completed.size} OCR pages completed`, total,
        phase === 'Recognizing text' ? Math.floor(90 * completed.size / Math.max(1, total)) : undefined);
    };
    progress('Reading PDF');
    await doc.importFiles([event.data.file]);
    const total = doc.inputData.pageCount;
    if (!total) throw new Error('EmptyPDF');
    if (doc.inputData.pdfType !== 'text') {
      phase = 'Recognizing text';
      progress('Loading English and Arabic OCR', total, 0);
      await doc.recognize({ langs: ['eng', 'ara'], modeAdv: 'lstm', combineMode: 'none' });
    }
    phase = 'Creating Markdown';
    const parts = [`<!-- PDF2AI\nSource: ${event.data.file.name.replace(/-->/g, '-- >')}\nPages: ${total}\nGenerated locally in this browser by PDF2AI\n-->`];
    const emptyPages = [];
    for (let i = 0; i < total; i++) {
      progress(`Creating Markdown; page ${i + 1} of ${total}`, total, 90 + Math.floor(10 * i / total));
      const text = String(await doc.exportData('md', { pageArr: [i], enableLayout: true, reflow: true })).trimEnd();
      if (!text.trim()) emptyPages.push(i + 1);
      parts.push(`<!-- PAGE ${i + 1} -->\n\n${text}`);
    }
    send({ type: 'done', result: { markdown: parts.join('\n\n') + '\n', pages: total, emptyPages } });
  } catch (error) {
    send({ type: 'error', error: `Local processing failed (${error?.name || 'Error'}). Download session logs and retry.` });
  }
});
send({ type: 'ready' });
