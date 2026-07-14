const puppeteer = require('puppeteer-core');
const BASE = 'http://127.0.0.1:8790';
const OUT = process.env.OUT_DIR || __dirname;
require('fs').mkdirSync(OUT, { recursive: true });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function clickText(page, needle) {
  const btns = await page.$$('button');
  for (const b of btns) {
    const t = (await page.evaluate((el) => el.textContent, b)) || '';
    const dis = await page.evaluate((el) => el.disabled, b);
    if (t.toLowerCase().includes(needle) && !dis) {
      await b.click();
      return true;
    }
  }
  return false;
}

const set = async (page, sel, val) => {
  await page.click(sel, { clickCount: 3 });
  await page.type(sel, String(val));
};

(async () => {
  const browser = await puppeteer.launch({
    executablePath: '/usr/bin/chromium',
    headless: 'new',
    args: ['--no-sandbox', '--disable-setuid-sandbox'],
  });
  const page = await browser.newPage();
  // Tall viewport so the whole step fits without scrolling.
  await page.setViewport({ width: 1200, height: 1150 });

  await page.goto(BASE, { waitUntil: 'networkidle2' });
  await sleep(1200);
  await page.screenshot({ path: `${OUT}/setup-1-connect.png` });
  console.log('saved setup-1-connect');

  // Step 1 — point it at the mock Proxmox and test.
  await set(page, '#s-host', '127.0.0.1');
  await set(page, '#s-port', '18006');
  await set(page, '#s-node', 'pve');
  await page.select('#s-scheme', 'http');
  await set(page, '#s-token-secret', 'mock-secret');
  await sleep(200);
  await clickText(page, 'test connection');
  await sleep(2500);
  await page.screenshot({ path: `${OUT}/setup-2-tested.png` });
  console.log('saved setup-2-tested');

  // -> Step 2, admin
  await clickText(page, 'continue');
  await sleep(700);
  await set(page, '#s-admin-user', 'admin');
  await set(page, '#s-admin-pw', 'a-strong-password');
  const pw2 = await page.$$('input[type="password"]');
  if (pw2.length > 1) { await pw2[pw2.length - 1].click(); await page.keyboard.type('a-strong-password'); }
  await sleep(300);
  await page.screenshot({ path: `${OUT}/setup-3-admin.png` });
  console.log('saved setup-3-admin');

  // -> Step 3, first user (opt in, then use the VM picker)
  await clickText(page, 'continue');
  await sleep(700);
  const box = await page.$('input[type="checkbox"]');
  if (box) await box.click();
  await sleep(500);
  await set(page, '#s-user-name', 'customer');
  await page.select('#s-user-vmid', '105').catch(() => {});
  await set(page, '#s-user-pw', 'customer-password');
  const pws = await page.$$('input[type="password"]');
  if (pws.length > 1) { await pws[pws.length - 1].click(); await page.keyboard.type('customer-password'); }
  await sleep(400);
  await page.screenshot({ path: `${OUT}/setup-4-first-user.png` });
  console.log('saved setup-4-first-user');

  // -> Step 4, review
  await clickText(page, 'continue');
  await sleep(800);
  await page.screenshot({ path: `${OUT}/setup-5-review.png` });
  console.log('saved setup-5-review');

  await browser.close();
  console.log('done');
})().catch((e) => { console.error(e); process.exit(1); });
