import { expect, test } from '@playwright/test';

test('expired SSE falls back to a saved snapshot without another POST', async ({ page }) => {
  let generationPosts = 0;
  let snapshots = 0;
  page.on('request', request => {
    if (request.method() === 'POST' && request.url().endsWith('/api/v1/runs')) generationPosts++;
    if (request.method() === 'GET' && /\/api\/v1\/runs\/[^/?]+$/.test(request.url())) snapshots++;
  });
  // Inject the transport boundary only; the snapshot, worker and database are real.
  await page.route('**/api/v1/runs/*/events?*', route => route.fulfill({ status: 410, body: '{}' }));
  await page.goto('/');
  await page.getByLabel('你想学习什么？').fill('事件恢复验收');
  await page.getByLabel('你希望用它做什么？').fill('验证断线后继续读取任务。');
  await page.getByRole('button', { name: '开始探索' }).click();
  await expect(page.getByRole('heading', { name: '把学习方向再确定一点' })).toBeVisible();
  expect(snapshots).toBeGreaterThan(0);
  expect(generationPosts).toBe(1);
  await page.screenshot({ path: '../.data/qa/m3-reconnect.png', fullPage: true });
});
