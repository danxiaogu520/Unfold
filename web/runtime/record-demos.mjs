// Record trusted evidence UI only; model HTML is never inserted or executed.
import { chromium } from '@playwright/test';
import { mkdir, rename, writeFile, readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { createHash } from 'node:crypto';

const input = resolve(process.argv[2]), directory = resolve(process.argv[3]);
await mkdir(directory, { recursive: true });
const browser = await chromium.launch({ chromiumSandbox: true });
const records = [];
try {
  for (const name of ['jobs', 'agent', 'context']) {
    const context = await browser.newContext({
      viewport: { width: 1200, height: 900 }, recordVideo: { dir: directory, size: { width: 1200, height: 900 } },
    });
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(pathToFileURL(input).href);
    await page.locator(`[data-tab="${name}"]`).click();
    await page.waitForTimeout(1800);
    let steps=1;
    while (!(await page.getByRole('button', { name: '下一步', exact: true }).isDisabled())) {
      if (steps >= 12) throw new Error('Unexpected evidence step count');
      await page.getByRole('button', { name: '下一步', exact: true }).click();
      await page.waitForTimeout(1800);
      steps++;
    }
    await page.screenshot({ path: resolve(directory, name+'.png'), fullPage: true });
    const video = page.video();
    await context.close();
    const target = resolve(directory, name+'.webm');
    await rename(await video.path(), target);
    if (errors.length) throw new Error('Evidence UI failed');
    records.push({ name, steps, video: name+'.webm', bytes: (await readFile(target)).length,
      new_model_calls: 0, execution: 'recorded_evidence_replay' });
  }
} finally { await browser.close(); }
await writeFile(resolve(directory, 'recording.json'), JSON.stringify({
  version: 'm7-recordings.v1', viewer_sha256: createHash('sha256').update(await readFile(input)).digest('hex'),
  browser: 'Chromium with browser sandbox', records,
}, null, 2)+'\n');
