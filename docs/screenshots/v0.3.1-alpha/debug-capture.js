const puppeteer = require('/tmp/node_modules/puppeteer');

(async () => {
  const browser = await puppeteer.launch({ executablePath: '/usr/bin/chromium', headless: 'new', args: ['--no-sandbox','--disable-setuid-sandbox'] });
  const page = await browser.newPage();
  page.on('console', msg => console.log('PAGE LOG:', msg.text()));
  page.on('pageerror', err => console.log('PAGE ERR:', err.message));

  await page.goto('http://127.0.0.1:5173/login', {waitUntil:'networkidle2'});
  await page.type('input#username', 'admin');
  await page.type('input#password', 'devpass');
  await page.click('button[type="submit"]');
  await new Promise(r=>setTimeout(r,2500));

  await page.goto('http://127.0.0.1:5173/switch', {waitUntil:'networkidle2'});
  await new Promise(r=>setTimeout(r,3000));

  const htmlInfo = await page.evaluate(() => {
    const chassis = document.querySelector('.arista-chassis');
    const ports = document.querySelectorAll('.rj45-port').length;
    const qs = document.querySelectorAll('.qsfp-port').length;
    const wrapper = document.querySelector('.faceplate-wrapper');
    const err = document.querySelector('.text-red')?.textContent || null;
    return { hasChassis: !!chassis, rj45Count: ports, qsfpCount: qs, hasWrapper: !!wrapper, errText: err, bodyLen: document.body.innerHTML.length };
  });
  console.log('DEBUG INFO:', JSON.stringify(htmlInfo));

  await page.screenshot({path: '/tmp/v031-switch-debug.png'});
  console.log('debug shot saved, size', require('fs').statSync('/tmp/v031-switch-debug.png').size);

  await browser.close();
})();
