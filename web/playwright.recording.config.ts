import { defineConfig } from '@playwright/test';
import base from './playwright.config';

export default defineConfig({
  ...base,
  testMatch: 'knowledge.spec.ts',
  outputDir: '../.data/qa/learning-recording-work',
  use: {
    ...base.use,
    video: { mode: 'on', size: { width: 1280, height: 720 } },
  },
});
