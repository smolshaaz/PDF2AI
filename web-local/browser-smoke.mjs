import puppeteer from 'puppeteer-core';
import { mkdtemp, readdir } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';

const browser = await puppeteer.launch({
  executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  headless: true,
  args: ['--no-sandbox'],
});

try {
  const page = await browser.newPage();
  const requests = [];
  const errors = [];
  page.on('request', (request) => requests.push({ method: request.method(), url: request.url() }));
  page.on('pageerror', (error) => errors.push(error.message));
  page.on('console', (message) => console.log(`browser:${message.type()}: ${message.text()}`));
  page.on('requestfailed', (request) => console.log(`requestfailed: ${request.url()} ${request.failure()?.errorText}`));
  await page.goto('http://127.0.0.1:4173/', { waitUntil: 'networkidle0' });
  const downloads = await mkdtemp(join(tmpdir(), 'pdf2ai-browser-smoke-'));
  await page._client().send('Page.setDownloadBehavior', { behavior: 'allow', downloadPath: downloads });
  const input = await page.$('#fileInput');
  const fixtures = process.argv.slice(2).length
    ? process.argv.slice(2).map((path) => resolve(path))
    : [resolve('../.build/packaged-smoke/digital.pdf'), resolve('../.build/packaged-smoke/scan.pdf')];
  await page.evaluate((count) => { window.__expectedCount = count; }, fixtures.length);
  await input.uploadFile(...fixtures);
  await page.click('#convertButton');
  try {
    await page.waitForFunction(() => {
      const states = [...document.querySelectorAll('.status')].map((node) => node.textContent);
      return states.length === window.__expectedCount && states.every((state) => state.startsWith('Done'));
    }, { timeout: 180_000 });
  } catch (error) {
    const state = await page.evaluate(() => ({
      statuses: [...document.querySelectorAll('.status')].map((node) => node.textContent),
      details: document.querySelector('#detailsLog')?.textContent,
      stage: document.querySelector('#workStage')?.textContent,
    }));
    throw new Error(`${error.message}\n${JSON.stringify({ state, errors, failed: requests.filter((request) => request.failed) }, null, 2)}`);
  }
  await page.waitForFunction(() => document.querySelector('#convertButton')?.textContent === 'Convert');
  await page.$eval('.row-open', (button) => button.click());
  await new Promise((resolvePromise) => setTimeout(resolvePromise, 500));
  const viewerOpen = await page.$eval('#viewer', (dialog) => dialog.open);
  if (!viewerOpen) throw new Error(`Preview did not open: ${JSON.stringify({ errors, details: await page.$eval('#detailsLog', (node) => node.textContent) })}`);
  const source = await page.$eval('#sourcePane', (node) => node.value);
  if (!source.includes('<!-- PAGE 1 -->')) throw new Error('Page marker missing from Markdown.');
  if (source.split('<!-- PAGE 1 -->')[1].trim().length < 5) throw new Error('The extracted page is empty.');
  await page.click('#downloadButton');
  await new Promise((resolvePromise) => setTimeout(resolvePromise, 1000));
  const downloaded = await readdir(downloads);
  if (!downloaded.some((name) => name.endsWith('.ai.md'))) throw new Error('Markdown download did not complete.');
  const uploads = requests.filter((request) => !['GET', 'HEAD'].includes(request.method));
  if (uploads.length) throw new Error(`Unexpected document request: ${JSON.stringify(uploads)}`);
  if (errors.length) throw new Error(`Browser errors: ${errors.join('; ')}`);
  console.log(JSON.stringify({ converted: fixtures.length, downloaded, documentUploads: uploads.length }));
} finally {
  await Promise.race([browser.close(), new Promise((resolvePromise) => setTimeout(resolvePromise, 2000))]);
  browser.process()?.kill('SIGKILL');
}
