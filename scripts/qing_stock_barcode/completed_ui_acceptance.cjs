// Run seed_browser.py, seed_sku_ui.py and seed_completed_ui.py in the disposable database.
const { chromium, devices } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const output = '/tmp/qing-completed-ui';
fs.mkdirSync(output, { recursive: true });
(async () => {
    const browser = await chromium.launch({ headless: true });
    try {
        const context = await browser.newContext({ ...devices['Pixel 7'] });
        const page = await context.newPage();
        const errors = [];
        const writes = [];
        page.on('pageerror', error => errors.push(error.message));
        page.on('request', request => {
            if (/\/(start_picking|execute|create|write)$/.test(request.url())) { writes.push(request.url()); }
        });
        const auth = await context.request.post('http://127.0.0.1:19996/web/session/authenticate', { data: {
            jsonrpc: '2.0', method: 'call', params: { db: 'qing_barcode_test', login: 'scan_ui_zh', password: 'scan-ui-isolated-test' },
        } });
        assert.ok((await auth.json()).result.uid);
        await page.goto('http://127.0.0.1:19996/odoo/action-qing_stock_barcode.action_scan_workbench');
        await page.locator('.qing_scan_completed_filter').waitFor();
        assert.ok((await page.locator('.qing_scan_completed_filter').innerText()).includes('显示已完成'));
        for (const [operation, label] of [['incoming', '收货'], ['outgoing', '发货'], ['internal', '调拨']]) {
            await page.getByRole('button', { name: label, exact: true }).click();
            // Python fixture uses capitalized booleans in the origin.
            const completed = page.locator('.qing_scan_task_row').filter({ hasText: `Completed UI ${operation} True` });
            await completed.locator('.qing_scan_view').waitFor();
            assert.equal(await completed.locator('.qing_scan_start').count(), 0);
            const name = await completed.locator('strong').innerText();
            await completed.locator('.qing_scan_task_select').click();
            assert.equal(await completed.evaluate(el => getComputedStyle(el).getPropertyValue('--qing-progress').trim()), '100%');
            assert.equal(await completed.evaluate(el => getComputedStyle(el, '::before').width === `${el.clientWidth}px`), true);
            await page.locator('.qing_scan_completed_filter input').uncheck();
            await completed.waitFor({ state: 'detached' });
            assert.equal(await page.locator('.qing_scan_task_completed').count(), 0);
            assert.ok(await page.locator('.qing_scan_task_row').filter({ hasText: `Completed UI ${operation} False` }).count());
            await page.locator('.qing_scan_completed_filter input').check();
            await completed.locator('.qing_scan_view').click();
            await page.locator('.qing_scan_return').waitFor();
            assert.equal(await page.locator('.qing_scan_session_header h2').innerText(), name);
            assert.equal(await page.locator('.qing_scan_input_panel').count(), 0);
            assert.equal(await page.locator('.qing_scan_validate').count(), 0);
            assert.equal(await page.locator('.qing_scan_completed_filter').count(), 0);
            assert.ok((await page.locator('.qing_scan_progress_summary').innerText()).includes('已完成数量'));
            await page.locator('.qing_scan_progress_toggle').click();
            await page.locator('.qing_scan_operation_details').waitFor();
            assert.ok((await page.locator('.qing_scan_operation_details').innerText()).includes('Completed View Widget'));
            assert.equal(await page.locator('.qing_scan_operation_details .qing_scan_amount').innerText(), '3');
            await page.locator('.qing_scan_refresh').click();
            await page.waitForFunction(() => !document.querySelector('.qing_scan_return')?.disabled);
            await page.screenshot({ path: `${output}/${operation}-details.png`, fullPage: true });
            await page.locator('.qing_scan_return').click();
            await completed.locator('.qing_scan_view').waitFor();
            assert.equal(await page.getByRole('button', { name: label, exact: true }).getAttribute('aria-pressed'), 'true');
            for (const width of [320, 390, 1366]) {
                await page.setViewportSize({ width, height: 844 });
                assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
                await page.screenshot({ path: `${output}/${operation}-list-${width}.png`, fullPage: true });
            }
            await page.setViewportSize({ width: 390, height: 844 });
        }
        for (const label of ['盘点', '查库存']) {
            await page.getByRole('button', { name: label, exact: true }).click();
            await page.locator('.qing_scan_completed_filter').waitFor({ state: 'detached' });
        }
        assert.deepEqual(errors, []);
        assert.deepEqual(writes, []);
        console.log('PASS native completed detail view without stock/session writes; full green rows, show/hide completed for receipts/deliveries/internal, refreshed details and return to correct list, no filter for count/lookup, Chinese labels, 320/390/1366px');
    } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
