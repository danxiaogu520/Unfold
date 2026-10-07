// Trusted driver. Candidate JavaScript runs in Chromium, never in this Node process.
import { chromium } from '../node_modules/@playwright/test/index.mjs';
import http from 'node:http';
import readline from 'node:readline';
import { createHash } from 'node:crypto';

const viewport = { width: 800, height: 540 };
let browser;
let context;
let page;
let source = '';
const logs = [];
const blocked = [];
const record = (list, value) => { if (list.length < 30) list.push(String(value).slice(0, 500)); };
const proxy = http.createServer((request, response) => {
  record(blocked, 'proxy-http'); response.writeHead(403); response.end('network-denied');
});
proxy.on('connect', (request, socket) => { record(blocked, 'proxy-connect'); socket.end('HTTP/1.1 403 Forbidden\r\n\r\n'); });
await new Promise(resolve => proxy.listen(0, '127.0.0.1', resolve));

async function reset() {
  await context?.close();
  context = await browser.newContext({ viewport, serviceWorkers: 'block', acceptDownloads: false });
  await context.route('**/*', route => { record(blocked, route.request().resourceType()); return route.abort('blockedbyclient'); });
  await context.routeWebSocket('**/*', socket => { record(blocked, 'websocket'); socket.close(); });
  page = await context.newPage();
  page.setDefaultTimeout(1800);
  page.on('console', message => record(logs, message.text()));
  page.on('pageerror', error => record(logs, 'page-error:' + error.message));
  await page.setContent(source, { waitUntil: 'domcontentloaded', timeout: 1800 });
}

const policy = "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; connect-src 'none'; worker-src 'none'; frame-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'";
function framed(html) {
  return `<!doctype html><meta http-equiv="Content-Security-Policy" content="${policy}">${html}`;
}

async function handle(command) {
  switch (command.op) {
    case 'open':
      browser = await chromium.launch({ headless: true, chromiumSandbox: true,
        proxy: { server: `http://127.0.0.1:${proxy.address().port}` },
        args: ['--proxy-bypass-list=<-loopback>', '--force-webrtc-ip-handling-policy=disable_non_proxied_udp',
          '--disable-background-networking', '--dns-prefetch-disable', '--disable-extensions'] });
      source = framed(command.html); await reset();
      return { origin: await page.evaluate(() => location.origin), viewport, browser_sandbox: true,
        model_key_in_driver: Boolean(process.env.DEEPSEEK_API_KEY), network_policy: 'deny-proxy-and-routing.v1' };
    case 'snapshot':
      return { png: (await page.screenshot({ timeout: 1800 })).toString('base64'), viewport, logs: [...logs], blocked: [...blocked] };
    case 'click':
      await page.mouse.click(command.x, command.y); return { accepted: true };
    case 'key':
      await page.keyboard.press(command.key); return { accepted: true };
    case 'text':
      await page.keyboard.insertText(command.text); return { accepted: true };
    case 'reset':
      logs.length = 0; await reset(); return { reset: true };
    case 'inspect':
      return { body: (await page.locator('body').innerText()).slice(0, 4000),
        buttons: await page.getByRole('button').allTextContents(),
        sliders: await page.locator('input[type=range]').count(), logs: [...logs], blocked: [...blocked] };
    case 'selector_click':
      await page.locator(command.selector).click(); return { accepted: true };
    case 'set_value':
      await page.locator(command.selector).evaluate((node, value) => {
        node.value = value; node.dispatchEvent(new Event('input', { bubbles: true }));
        node.dispatchEvent(new Event('change', { bubbles: true }));
      }, command.value); return { accepted: true };
    case 'read_value':
      return { text: await page.locator(command.selector).innerText() };
    case 'plot_fingerprint': {
      const plots = page.locator('canvas,svg');
      const count = await plots.count();
      if (!count) return { count: 0, visible: false, sha256: null };
      const plot = plots.first();
      const box = await plot.boundingBox();
      const visible = await plot.isVisible();
      const png = visible ? await plot.screenshot({ timeout: 1800 }) : null;
      return { count, visible: Boolean(visible && box && box.width >= 100 && box.height >= 100),
        sha256: png ? createHash('sha256').update(png).digest('hex') : null };
    }
    case 'probe': {
      const result = await page.evaluate(async () => {
        let storage;
        try { storage = localStorage.length; } catch { storage = 'unavailable'; }
        let network;
        try { await fetch('http://127.0.0.1:8000/api/v1/spaces'); network = 'unexpected'; }
        catch { network = 'blocked'; }
        return { storage, network, parentIsSelf: parent === window, nodeProcess: typeof process };
      });
      return { ...result, logs: [...logs], blocked: [...blocked] };
    }
    case 'stop':
      await browser?.close(); return { stopped: true };
    default: throw new Error('unknown trusted driver operation');
  }
}

const input = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
for await (const line of input) {
  let command;
  try {
    command = JSON.parse(line);
    const result = await handle(command);
    process.stdout.write(JSON.stringify({ id: command.id, ok: true, result }) + '\n');
    if (command.op === 'stop') break;
  } catch (error) {
    process.stdout.write(JSON.stringify({ id: command?.id, ok: false, errorCode: error.name === 'TimeoutError' ? 'timeout' : 'domain_validation', error: String(error.message).slice(0, 800) }) + '\n');
  }
}
await browser?.close();
proxy.close();
