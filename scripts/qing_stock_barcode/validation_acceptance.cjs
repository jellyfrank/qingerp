// Run seed_validation.py once in the disposable database before this script.
const { chromium, devices } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const base = 'http://127.0.0.1:19996';
const output = '/tmp/qing-validation-browser';
fs.mkdirSync(output, { recursive: true });
(async () => {
    const browser = await chromium.launch({ headless: true });
    try {
        for (const name of ['ask-create', 'ask-cancel', 'always', 'never', 'full']) {
            const context = await browser.newContext({ ...devices['Pixel 7'] });
            const page = await context.newPage();
            const errors = [];
            page.on('pageerror', error => errors.push(error.message));
            async function rpc(model, method, args, kwargs = {}) {
                const response = await page.request.post(`${base}/web/dataset/call_kw/${model}/${method}`, {
                    data: { jsonrpc: '2.0', method: 'call', params: { model, method, args, kwargs } },
                });
                const result = await response.json();
                assert.ok(!result.error, JSON.stringify(result.error));
                return result.result;
            }
            await page.request.post(`${base}/web/session/authenticate`, { data: { jsonrpc: '2.0', method: 'call',
                params: { db: 'qing_barcode_test', login: 'validation_operator', password: 'validation-isolated-test' } } });
            await page.goto(`${base}/odoo/action-qing_stock_barcode.action_scan_workbench?debug=assets`);
            await page.locator('.qing_scan_task_row').filter({ has: page.locator('.qing_scan_start') }).filter({ hasText: `Validation ${name}` }).last().locator('.qing_scan_task_select').click();
            await page.locator('.qing_scan_task_row').filter({ has: page.locator('.qing_scan_start') }).filter({ hasText: `Validation ${name}` }).last().locator('.qing_scan_start').click();
            const pickingName = await page.locator('.qing_scan_session_header h2').innerText();
            const validate = page.getByRole('button', { name: 'Validate', exact: true });
            await validate.waitFor();
            assert.equal(await validate.isDisabled(), true);
            await page.waitForFunction(() => getComputedStyle(document.querySelector('.qing_scan_validate')).backgroundColor === 'rgb(108, 117, 125)');
            await page.locator('#qing_barcode_input').fill('VALIDATE-ITEM');
            await page.locator('#qing_barcode_input').press('Enter');
            await page.waitForFunction(() => !document.querySelector('.qing_scan_validate')?.disabled);
            await page.waitForFunction(() => getComputedStyle(document.querySelector('.qing_scan_validate')).backgroundColor === 'rgb(25, 135, 84)');
            if (name === 'ask-create') {
                await validate.scrollIntoViewIfNeeded();
                await page.screenshot({ path: `${output}/green-validate.png` });
            }
            await validate.click();
            if (name.startsWith('ask-')) {
                await page.getByRole('button', { name: 'Create Backorder', exact: true }).waitFor();
                if (name === 'ask-create') {
                    await page.screenshot({ path: `${output}/native-backorder.png` });
                    await page.locator('.modal-header [aria-label=Close]').click();
                    await page.waitForFunction(() => !document.querySelector('.qing_scan_validate')?.disabled);
                    await validate.click();
                }
                await page.getByRole('button', { name: name === 'ask-create' ? 'Create Backorder' : 'No Backorder', exact: true }).click();
            }
            await page.locator('.qing_scan_session_header .badge').filter({ hasText: 'Done' }).waitFor();
            assert.equal(await validate.isDisabled(), true);
            await page.waitForFunction(() => getComputedStyle(document.querySelector('.qing_scan_validate')).backgroundColor === 'rgb(108, 117, 125)');
            const [picking] = await rpc('stock.picking', 'search_read', [[['name', '=', pickingName], ['backorder_id', '=', false]]],
                { fields: ['id', 'state', 'move_line_ids', 'backorder_ids'] });
            assert.equal(picking.state, 'done');
            const lines = await rpc('stock.move.line', 'read', [picking.move_line_ids], { fields: ['quantity'] });
            assert.equal(lines.reduce((sum, line) => sum + line.quantity, 0), 1);
            const expected = ['ask-create', 'always'].includes(name);
            assert.equal(picking.backorder_ids.length, Number(expected));
            if (expected) {
                const remaining = await rpc('stock.move', 'search_read', [[['picking_id', 'in', picking.backorder_ids]]], { fields: ['product_uom_qty'] });
                assert.equal(remaining.reduce((sum, line) => sum + line.product_uom_qty, 0), 1);
            }
            assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
            assert.deepEqual(errors, []);
            console.log(`PASS ${name}: native policy, quantity, backorder, button color, mobile layout`);
            await context.close();
        }
    } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
