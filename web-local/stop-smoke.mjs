import puppeteer from 'puppeteer-core';
import { resolve } from 'node:path';
const browser = await puppeteer.launch({ executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', headless: true });
try {
  const page = await browser.newPage();
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  await page.goto('http://127.0.0.1:8766/');
  await (await page.$('#fileInput')).uploadFile(resolve('../.build/packaged-smoke/digital.pdf'));
  await page.click('#convertButton');
  await page.waitForSelector('iframe', { timeout: 5000 });
  const start = Date.now();
  await page.click('#stopButton');
  await page.waitForFunction(() => !document.querySelector('iframe') && !document.querySelector('#convertButton').disabled, { timeout: 3000 });
  console.log(`Stop during startup: ${Date.now()-start}ms; engine removed; retry enabled`);
  await page.click('#convertButton');
  await page.waitForFunction(() => document.querySelector('.status')?.textContent.startsWith('Done'), { timeout: 45000 });
  console.log('Retry digital conversion: passed');
  if (errors.length) throw new Error(errors.join('\n'));
  console.log('Browser errors: none; logs button:', await page.$eval('#downloadLogs', x => x.textContent));
} finally { await browser.close(); }
