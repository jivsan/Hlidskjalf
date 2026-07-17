// v0.5.0-alpha cyberpunk screenshot sweep — runs against the backend on :8787
// (serving the built SPA), mock_pve on :18006, mock_switch on :18080.
// A second, FRESH panel instance on :8790 provides the setup-wizard shot.
//
//   OUT_DIR=docs/screenshots/v0.5.0-alpha node capture.js
//
// Covers: login, fleet, VM detail (overview/graphs/console), node, provision,
// switch, users, settings, debug, setup step 1 — plus a mobile viewport pass
// and a prefers-reduced-motion pass (the not-cringe invariants).
const puppeteer = require('puppeteer-core');

const BASE = 'http://127.0.0.1:8787';
const FRESH = 'http://127.0.0.1:8790';
const OUT = process.env.OUT_DIR || __dirname;
const fs = require('fs');
fs.mkdirSync(OUT, { recursive: true });

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function login(page, user, pass) {
  await page.goto(`${BASE}/login`, { waitUntil: 'networkidle2' });
  await page.type('input#username', user);
  await page.type('input#password', pass);
  await Promise.all([
    page.click('button[type="submit"]'),
    page.waitForNavigation({ waitUntil: 'networkidle2' }).catch(() => {}),
  ]);
  await sleep(2000);
}

async function shot(page, path, name, settle = 2500) {
  if (path) await page.goto(`${BASE}${path}`, { waitUntil: 'networkidle2' });
  await sleep(settle);
  await page.screenshot({ path: `${OUT}/${name}.png` });
  console.log('saved', name);
}

(async () => {
  const browser = await puppeteer.launch({
    executablePath: '/nix/store/d2naq8q4hyfg7m2fmrimfbd7hzclqbqf-chromium-150.0.7871.124/bin/chromium',
    headless: 'new',
    args: ['--no-sandbox', '--disable-setuid-sandbox', '--force-device-scale-factor=1'],
  });
  const page = await browser.newPage();
  await page.setViewport({ width: 1400, height: 900 });

  // ---- Login (the hero) — let the boot line finish + cursor retire ----
  await page.goto(`${BASE}/login`, { waitUntil: 'networkidle2' });
  await sleep(4500);
  await page.screenshot({ path: `${OUT}/login.png` });
  console.log('saved login');

  // ---- Setup wizard step 1 (fresh instance, no users yet) ----
  try {
    await page.goto(`${FRESH}/`, { waitUntil: 'networkidle2', timeout: 8000 });
    await sleep(1500);
    await page.screenshot({ path: `${OUT}/setup-1-connect.png` });
    console.log('saved setup-1-connect');
  } catch {
    console.log('setup shot skipped (no fresh instance on :8790)');
  }

  // ---- Admin views ----
  await login(page, 'admin', 'devpass123');
  console.log('admin logged in');
  await shot(page, '/', 'admin-fleet', 3500);
  await shot(page, '/vm/105', 'admin-vm-detail', 3500);
  await shot(page, '/vm/105?tab=graphs', 'admin-vm-graphs', 3500);
  await shot(page, '/vm/105?tab=console', 'admin-vm-console', 4000);
  await shot(page, '/vm/130?tab=console', 'admin-ct-terminal', 4000);
  await shot(page, '/node', 'admin-node', 3500);
  await shot(page, '/new', 'admin-provision');
  await shot(page, '/users', 'admin-users');
  await shot(page, '/settings', 'admin-settings', 3000);
  await shot(page, '/debug', 'admin-debug', 3000);
  await shot(page, '/switch', 'admin-switch', 4000);

  // ---- Tenant view (scoped to one VM) ----
  const cookies = await page.cookies(BASE);
  if (cookies.length) await page.deleteCookie(...cookies);
  await login(page, 'demo', 'demopass123');
  await page.screenshot({ path: `${OUT}/user-my-vm.png` });
  console.log('saved user-my-vm');

  // ---- Mobile pass (390x844) ----
  await page.setViewport({ width: 390, height: 844 });
  const mcookies = await page.cookies(BASE);
  if (mcookies.length) await page.deleteCookie(...mcookies);
  await page.goto(`${BASE}/login`, { waitUntil: 'networkidle2' });
  await sleep(4500);
  await page.screenshot({ path: `${OUT}/mobile-login.png` });
  console.log('saved mobile-login');
  await login(page, 'admin', 'devpass123');
  await shot(page, '/', 'mobile-fleet', 3000);
  await shot(page, '/vm/105', 'mobile-vm-detail', 3000);

  // ---- Reduced-motion pass (every animation must be dead; scene may stay) ----
  await page.setViewport({ width: 1400, height: 900 });
  await page.emulateMediaFeatures([
    { name: 'prefers-reduced-motion', value: 'reduce' },
  ]);
  const rcookies = await page.cookies(BASE);
  if (rcookies.length) await page.deleteCookie(...rcookies);
  await page.goto(`${BASE}/login`, { waitUntil: 'networkidle2' });
  await sleep(1200); // the boot line must render COMPLETE, immediately
  await page.screenshot({ path: `${OUT}/reduced-motion-login.png` });
  console.log('saved reduced-motion-login');
  await login(page, 'admin', 'devpass123');
  await shot(page, '/', 'reduced-motion-fleet', 3000);

  await browser.close();
  console.log('done');
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
