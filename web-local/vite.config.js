import { defineConfig } from 'vite';
import { cpSync, existsSync, mkdirSync } from 'node:fs';
import { resolve } from 'node:path';

function copyLanguageData() {
  return {
    name: 'copy-language-data',
    closeBundle() {
      const output = resolve('dist/tessdata');
      mkdirSync(output, { recursive: true });
      for (const lang of ['eng', 'ara']) {
        const source = resolve(`node_modules/@tesseract.js-data/${lang}/4.0.0_best_int/${lang}.traineddata.gz`);
        if (!existsSync(source)) throw new Error(`Missing OCR language file: ${source}`);
        cpSync(source, resolve(output, `${lang}.traineddata.gz`));
      }
    },
  };
}

export default defineConfig({
  base: './',
  plugins: [copyLanguageData()],
  define: { process: 'undefined' },
  worker: { format: 'es' },
  build: { rollupOptions: { input: { main: resolve('index.html'), engine: resolve('engine.html') } }, target: 'es2022', sourcemap: false },
});
