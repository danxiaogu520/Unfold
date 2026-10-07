import { expect, test } from '@playwright/test';

test('real HTTP and PostgreSQL: intake, save and refresh without another generation', async ({ page }) => {
  const failures: string[] = [];
  page.on('pageerror', error => failures.push(error.message));
  await page.goto('/');
  await expect(page.getByText('开发替身 · 不调用模型')).toBeVisible();
  await page.screenshot({ path: '../.data/qa/m1-home.png', fullPage: true });
  await page.getByLabel('你想学习什么？').fill('二分查找 · 浏览器验收');
  await page.getByLabel('你希望用它做什么？').fill('准备校招，希望理解循环边界。');
  await page.getByRole('button', { name: '开始探索' }).click();
  await expect(page.getByRole('heading', { name: '把学习方向再确定一点' })).toBeVisible();
  await page.getByRole('checkbox', { name: '暂不确定', exact: true }).check();
  await page.getByRole('button', { name: '整理学习设定' }).click();
  await expect(page.getByRole('heading', { name: '你的学习设定' })).toBeVisible();
  await expect(page.getByText('尚未确定当前基础')).toBeVisible();
  await expect(page.getByText('待确认假设').first()).toBeVisible();
  await expect(page.getByRole('status')).toContainText('已保存');
  let posts = 0;
  page.on('request', request => { if (request.method() === 'POST') posts++; });
  await page.reload();
  await expect(page.getByRole('heading', { name: '你的学习设定' })).toBeVisible();
  await expect(page.getByText('尚未确定当前基础')).toBeVisible();
  expect(posts).toBe(0);
  expect(failures).toEqual([]);
  await page.screenshot({ path: '../.data/qa/m1-profile.png', fullPage: true });
});

test('mobile start screen is usable and empty answers are rejected', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/');
  await page.getByLabel('你想学习什么？').fill('后端开发 · 移动端验收');
  await page.getByLabel('你希望用它做什么？').fill('准备校招。');
  await page.getByRole('button', { name: '开始探索' }).click();
  await expect(page.getByRole('heading', { name: '把学习方向再确定一点' })).toBeVisible();
  await page.getByRole('button', { name: '整理学习设定' }).click();
  await expect(page.getByRole('alert')).toContainText('请回答每一题');
  const width = await page.evaluate(() => document.documentElement.scrollWidth);
  expect(width).toBeLessThanOrEqual(390);
  await page.screenshot({ path: '../.data/qa/m1-mobile.png', fullPage: true });
});

test('two intake rounds preserve answers and remove further followup', async ({ page }) => {
  await page.goto('/');
  await page.getByLabel('你想学习什么？').fill('两轮问答 · 浏览器验收');
  await page.getByLabel('你希望用它做什么？').fill('想结合用途学习，基础暂不确定。');
  await page.getByRole('button', { name: '开始探索' }).click();
  await expect(page.getByRole('checkbox', { name: '暂不确定', exact: true })).toBeVisible();
  await page.getByRole('checkbox', { name: '暂不确定', exact: true }).check();
  await page.getByRole('button', { name: '还有影响范围的信息，补充一轮问题' }).click();
  await expect(page.getByRole('status')).toContainText('已保存');
  await expect(page.getByRole('button', { name: '还有影响范围的信息，补充一轮问题' })).toHaveCount(0);
  await page.getByRole('checkbox', { name: '暂不确定', exact: true }).check();
  await page.getByRole('button', { name: '整理学习设定' }).click();
  await expect(page.getByRole('heading', { name: '你的学习设定' })).toBeVisible();
  await page.reload();
  await expect(page.getByText('尚未确定当前基础')).toBeVisible();
});
