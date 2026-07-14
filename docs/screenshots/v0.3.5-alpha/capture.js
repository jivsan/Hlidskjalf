// v0.3.4-alpha screenshot capture — runs against the backend on :8787
// (serving the built frontend), mock_pve on :18006, mock_switch on :18080.
const puppeteer = require('puppeteer-core');

const BASE = 'http://127.0.0.1:8787';
const OUT = process.env.OUT_DIR || __dirname;
const fs = require('fs');
fs.mkdirSync(OUT, { recursive: true });

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function login(page, user, pass) {
  await page.goto(`${BASE}/login`, { waitUntil: 'networkidle2' });
  await page.evaluate(() => {
    const u = document.querySelector('input#username');
    const p = document.querySelector('input#password');
    if (u) u.value = '';
    if (p) p.value = '';
  });
  await page.type('input#username', user);
  await page.type('input#password', pass);
  await Promise.all([
    page.click('button[type="submit"]'),
    page.waitForNavigation({ waitUntil: 'networkidle2' }).catch(() => {}),
  ]);
  await sleep(2000);
}

async function shot(page, path, name, settle = 2000) {
  await page.goto(`${BASE}${path}`, { waitUntil: 'networkidle2' });
  await sleep(settle);
  await page.screenshot({ path: `${OUT}/${name}.png` });
  console.log('saved', name);
}

(async () => {
  const browser = await puppeteer.launch({
    executablePath: '/usr/bin/chromium',
    headless: 'new',
    args: ['--no-sandbox', '--disable-setuid-sandbox', '--window-size=1400,900'],
  });
  const page = await browser.newPage();
  await page.setViewport({ width: 1400, height: 900 });

  // Login page itself (before authenticating)
  await page.goto(`${BASE}/login`, { waitUntil: 'networkidle2' });
  await sleep(800);
  await page.screenshot({ path: `${OUT}/login.png` });
  console.log('saved login');

  // ---- Admin views ----
  await login(page, 'admin', 'devpass');
  console.log('admin logged in, url:', page.url());

  await shot(page, '/', 'admin-fleet', 3000);
  await shot(page, '/users', 'admin-users');
  await shot(page, '/new', 'admin-provision');
  await shot(page, '/node', 'admin-node', 3000);
  await shot(page, '/switch', 'admin-switch', 3500);
  await shot(page, '/debug', 'admin-debug', 3000);
  await shot(page, '/vm/105', 'admin-vm-detail', 3000);

  // ---- User views ----
  // drop the admin session cookie entirely, then log in as the demo user
  const cookies = await page.cookies(BASE);
  if (cookies.length) await page.deleteCookie(...cookies);
  await login(page, 'demo', 'demopass123');
  console.log('demo logged in, url:', page.url());
  await sleep(2500);
  await page.screenshot({ path: `${OUT}/user-my-vm.png` });
  console.log('saved user-my-vm');
  await shot(page, '/switch', 'user-switch', 3500);

  await browser.close();
  console.log('done');
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
