import { expect, test } from '@playwright/test';

test('comprehensive selection, native saved question and local render failure', async ({ page }) => {
  await page.goto('/');
  await page.getByLabel('你想学习什么？').fill('M7 综合练习与局部回退');
  await page.getByLabel('你希望用它做什么？').fill('联系已保存内容，不改历史答案。');
  await page.getByRole('button', { name: '开始探索' }).click();
  await page.getByRole('checkbox', { name: '暂不确定', exact: true }).check();
  await page.getByRole('button', { name: '整理学习设定' }).click();
  await expect(page.getByRole('heading', { name: '下一层知识' })).toBeVisible();
  await page.getByText('加入其他已保存内容，生成综合练习').click();
  await page.getByRole('checkbox', { name: /核心概念 · 内容/ }).check();
  const sent = page.waitForRequest(request => request.method() === 'POST' && request.url().endsWith('/actions'));
  await page.getByRole('button', { name: '生成练习', exact: true }).click();
  expect((await sent).postDataJSON().exercise_content_ids).toHaveLength(1);
  await expect(page.locator('.practice-question')).toHaveCount(3);
  await page.getByLabel('补充要求或反馈（选填）').fill('M7 行内练习验收');
  await page.getByRole('button', { name: '阅读详细讲解' }).click();
  await expect(page.getByText('引用已保存题目；答案、原始内容版本与历史作答保持原样。')).toBeVisible();
  await expect(page.locator('.practice-question')).toHaveCount(1);
  const question = page.locator('.practice-question');
  // The offered question ID order is stable but not tied to single/multiple mode.
  const options = question.locator('input');
  await options.nth(0).check();
  if (await question.getByRole('checkbox').count()) await options.nth(2).check();
  await question.getByRole('button', { name: '提交作答' }).click();
  await expect(question.locator('.answer-result')).toBeVisible();
  const id = await page.evaluate(() => localStorage.getItem('unfold.space'));
  // Corrupt one HTTP block to exercise the UI boundary, not the valid server contract.
  await page.route('**/api/v1/spaces/'+id, async route => {
    const response = await route.fetch();
    const body = await response.json();
    body.nodes.find((node: { id: string }) => node.id === body.root_id).contents[0].blocks.unshift({
      type: 'markdown', text: { invalid_fixture: true },
    });
    await route.fulfill({ response, json: body });
  });
  let posts = 0;
  page.on('request', request => { if (request.method() === 'POST') posts++; });
  await page.reload();
  await expect(page.getByText('这段内容暂时无法渲染')).toBeVisible();
  await expect(page.locator('.practice-question .answer-result')).toBeVisible();
  await expect(page.getByRole('button', { name: '生成新的讲解' })).toBeVisible();
  expect(posts).toBe(0);
  await page.screenshot({ path: '../.data/qa/m7-native-practice.png', fullPage: true });
});
