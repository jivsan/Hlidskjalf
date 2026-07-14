const puppeteer = require('/tmp/node_modules/puppeteer');
const fs = require('fs');

(async () => {
  const browser = await puppeteer.launch({
    executablePath: '/usr/bin/chromium',
    headless: 'new',
    args: ['--no-sandbox', '--disable-setuid-sandbox', '--window-size=1400,900']
  });
  const page = await browser.newPage();
  await page.setViewport({ width: 1400, height: 900 });

  console.log('Logging in as admin...');
  await page.goto('http://127.0.0.1:5173/login', { waitUntil: 'networkidle2' });
  await page.type('input#username', 'admin');
  await page.type('input#password', 'devpass');
  await page.click('button[type="submit"]');
  await new Promise(r => setTimeout(r, 2500));

  // Admin Fleet
  await page.goto('http://127.0.0.1:5173/', { waitUntil: 'networkidle2' });
  await new Promise(r => setTimeout(r, 2000));
  await page.screenshot({ path: '/tmp/v032-admin-fleet.png', fullPage: false });
  console.log('Saved admin-fleet');

  // Admin Users page (new feature)
  await page.goto('http://127.0.0.1:5173/users', { waitUntil: 'networkidle2' });
  await new Promise(r => setTimeout(r, 1500));
  await page.screenshot({ path: '/tmp/v032-admin-users.png' });
  console.log('Saved admin-users');

  // Admin Provision
  await page.goto('http://127.0.0.1:5173/new', { waitUntil: 'networkidle2' });
  await new Promise(r => setTimeout(r, 1500));
  await page.screenshot({ path: '/tmp/v032-admin-provision.png' });
  console.log('Saved admin-provision');

  // Switch as admin
  await page.goto('http://127.0.0.1:5173/switch', { waitUntil: 'networkidle2' });
  await new Promise(r => setTimeout(r, 2000));
  await page.screenshot({ path: '/tmp/v032-admin-switch.png' });
  console.log('Saved admin-switch');

  // Now create a demo user via API so we can login as user (or assume exists)
  // For demo, we will logout and login as a user if one exists, else use admin for illustration.

  // Logout
  await page.click('button:has-text("logout")').catch(() => {});
  await new Promise(r => setTimeout(r, 1000));

  // Try to login as demo user (created in previous test); fallback to showing admin view
  console.log('Attempting user login...');
  await page.goto('http://127.0.0.1:5173/login', { waitUntil: 'networkidle2' });
  await page.type('input#username', 'demo');
  await page.type('input#password', 'demopass');
  await page.click('button[type="submit"]');
  await new Promise(r => setTimeout(r, 2500));

  const currentUrl = page.url();
  console.log('After demo login, url:', currentUrl);

  if (currentUrl.includes('/vm/')) {
    await page.screenshot({ path: '/tmp/v032-user-my-vm.png' });
    console.log('Saved user-my-vm');
  } else {
    // fallback capture of fleet or whatever
    await page.screenshot({ path: '/tmp/v032-user-view.png' });
  }

  // Switch as user
  await page.goto('http://127.0.0.1:5173/switch', { waitUntil: 'networkidle2' });
  await new Promise(r => setTimeout(r, 2000));
  await page.screenshot({ path: '/tmp/v032-user-switch.png' });
  console.log('Saved user-switch');

  await browser.close();
  console.log('All captures done to /tmp/v032-*.png');
})();