// Synthetic qing_barcode_test only; run with MOBILE_ENTRY_FIXTURES pointing to the seed JSON.
const { chromium, devices } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const fixtures = JSON.parse(fs.readFileSync(process.env.MOBILE_ENTRY_FIXTURES, 'utf8'));
const output = process.env.MOBILE_ENTRY_OUTPUT || '/tmp/qing-mobile-entry-ui';
fs.mkdirSync(output, { recursive: true });
const URL = 'http://127.0.0.1:19996';
(async () => {
 const browser = await chromium.launch({ headless: true });
 try {
  const context = await browser.newContext({ ...devices['Pixel 7'] });
  const auth = await context.request.post(`${URL}/web/session/authenticate`, { data: { jsonrpc: '2.0', method: 'call', params: { db: 'qing_barcode_test', login: 'scan_ui_zh', password: 'scan-ui-isolated-test' } } });
  assert.ok((await auth.json()).result.uid);
  const page = await context.newPage(); const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  await page.goto(`${URL}/odoo/action-stock.stock_picking_type_action`);
  await page.locator('.o_menu_toggle').click();
  const menu = page.locator('.o_app_menu_sidebar [data-menu-xmlid="qing_stock_barcode.menu_scan_workbench"]');
  await menu.waitFor({ state: 'visible' });
  assert.equal((await menu.innerText()).trim(), '扫码');
  await page.screenshot({ path: `${output}/mobile-menu.png` });
  await menu.click();
  await page.locator('.o_qing_scan').waitFor();
  await page.waitForFunction(() => { const sidebar = document.querySelector('.o_app_menu_sidebar'); return !sidebar || sidebar.getBoundingClientRect().right <= 0; });
  for (const picking of fixtures.pickings) {
   await page.goto(`${URL}/odoo/action-${fixtures.action}`);
   const card = page.locator('.o_kanban_record').filter({ hasText: picking.name });
   const scan = card.locator('.qing_scan_kanban_button');
   await scan.waitFor({ state: 'visible' });
   assert.equal((await scan.textContent()).trim(), picking.state === 'done' ? '查看' : '扫码');
   await card.screenshot({ path: `${output}/${picking.id}-kanban.png` });
   await scan.click();
   await page.locator('.qing_scan_session_header').waitFor();
   assert.equal(await page.locator('.qing_scan_session_header h2').innerText(), picking.name);
   const labels = { incoming: '收货', outgoing: '发货', internal: '调拨' };
   assert.equal(await page.getByRole('button', { name: labels[picking.picking_type_code], exact: true }).getAttribute('aria-pressed'), 'true');
   if (picking.state === 'done') {
    assert.equal(await page.locator('.qing_scan_input_panel').count(), 0);
    await page.locator('.qing_scan_return').click();
   } else {
    assert.equal(await page.locator('.qing_scan_input_panel').count(), 1);
    // Leave the saved session in place: the next card must still open its own picking.
    await page.screenshot({ path: `${output}/${picking.id}-workbench.png` });
    continue;
   }
   assert.equal(await page.getByRole('button', { name: labels[picking.picking_type_code], exact: true }).getAttribute('aria-pressed'), 'true');
  }
  // Resize in the same running app to ensure the mobile menu is not cached for desktop.
  await page.goto(`${URL}/odoo/action-stock.stock_picking_type_action`);
  await page.setViewportSize({ width: 1366, height: 900 });
  await page.locator('.o_qing_sidebar').waitFor({ state: 'visible' });
  assert.equal(await page.locator('.o_qing_sidebar [data-menu-xmlid="qing_stock_barcode.menu_scan_workbench"]').count(), 0);
  assert.equal(await page.locator('.o_qing_top_apps').getByText('仓库扫码', { exact: true }).count(), 0);
  await page.goto(`${URL}/odoo/action-${fixtures.action}`);
  await page.locator('.o_kanban_record').first().waitFor();
  assert.ok(await page.locator('.qing_scan_kanban_button').count());
  assert.equal(await page.locator('.qing_scan_kanban_button').first().isVisible(), false);
  await page.screenshot({ path: `${output}/desktop-kanban.png` });
  assert.deepEqual(errors, []);
  const restricted = await browser.newContext({ ...devices['Pixel 7'] });
  await restricted.request.post(`${URL}/web/session/authenticate`, { data: { jsonrpc: '2.0', method: 'call', params: { db: 'qing_barcode_test', login: 'stock_no_scan', password: 'stock-no-scan-test' } } });
  const other = await restricted.newPage();
  await other.goto(`${URL}/odoo/action-${fixtures.action}`);
  await other.locator('.o_kanban_record').first().waitFor();
  assert.equal(await other.locator('.qing_scan_kanban_button').count(), 0);
  await other.locator('.o_menu_toggle').click();
  assert.equal(await other.locator('[data-menu-xmlid="qing_stock_barcode.menu_scan_workbench"]').count(), 0);
  console.log('PASS mobile Inventory scan menu, six native kanban entries for three operations/open and completed, saved-session override, correct return list, desktop resize/menu/icon hidden, stock user without scan permission has no entries, no JS errors');
 } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
