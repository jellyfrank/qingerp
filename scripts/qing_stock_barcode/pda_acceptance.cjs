// Synthetic Android browser acceptance, not physical PDA certification.
// Run seed_browser.py in the disposable qing_barcode_test database first.
const { chromium, devices } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const URL = 'http://127.0.0.1:19996';
(async () => {
    fs.mkdirSync('/tmp/qing-pda-browser', { recursive: true });
    const browser = await chromium.launch({ headless: true, args: ['--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream'] });
    try {
        const context = await browser.newContext({ ...devices['Pixel 7'], permissions: ['camera'] });
        await context.addInitScript(() => {
            // The real native scanner/camera lifecycle runs; decoding is deterministic test input.
            window.BarcodeDetector = class {
                static async getSupportedFormats() { return ['qr_code']; }
                async detect(video) { return window.__testCameraResult ? [{ rawValue: 'UI-WIDGET', boundingBox: { x: video.videoWidth / 2, y: video.videoHeight / 2, width: 1, height: 1 } }] : []; }
            };
        });
        const auth = await context.request.post(`${URL}/web/session/authenticate`, { data: { jsonrpc: '2.0', method: 'call', params: {
            db: 'qing_barcode_test', login: 'barcode_admin', password: 'barcode-isolated-test',
        } } });
        assert.ok((await auth.json()).result?.uid);
        const page = await context.newPage();
        const errors = [];
        page.on('pageerror', (error) => errors.push(error.message));
        let writes = 0;
        page.on('request', (request) => { if (request.url().endsWith('/execute')) { writes++; } });
        await page.goto(`${URL}/odoo/action-qing_stock_barcode.action_scan_workbench?debug=assets`);
        assert.equal(await page.locator('.qing_scan_input_panel').count(), 0);
        await page.locator('.qing_scan_task_row').filter({ has: page.locator('.qing_scan_start') }).filter({ hasText: 'Browser acceptance' }).last().locator('.qing_scan_task_select').click();
        await page.locator('.qing_scan_task_row').filter({ has: page.locator('.qing_scan_start') }).filter({ hasText: 'Browser acceptance' }).last().locator('.qing_scan_start').click();
        await page.locator('.qing_scan_session_header').waitFor();
        await page.locator('.qing_scan_pda_toggle').check();
        const input = page.locator('#qing_barcode_input');
        const focus = async () => { try { await page.waitForFunction(() => document.activeElement?.id === 'qing_barcode_input'); } catch (error) { console.error('FOCUS', await page.evaluate(() => ({ active: document.activeElement?.tagName + '#' + document.activeElement?.id, body: document.body.className, input: document.querySelector('#qing_barcode_input')?.outerHTML }))); throw error; } };
        const inject = async (value) => input.evaluate((el, text) => {
            el.value = text;
            el.dispatchEvent(new InputEvent('input', { bubbles: true, data: text, inputType: 'insertText' }));
        }, value);
        const quantity = async (value) => { try { await page.waitForFunction((expected) => document.querySelector('.qing_scan_scanned')?.textContent === String(expected), value); } catch (error) { console.error('WORKBENCH', await page.locator('.o_qing_scan').innerText()); throw error; } };
        await focus();
        assert.equal(await input.getAttribute('inputmode'), 'none');
        assert.equal(await page.locator('.qing_scan_device_settings').count(), 0);
        await page.keyboard.type('UI-WIDGET', { delay: 15 });
        await page.keyboard.press('Enter');
        await quantity(1);
        await page.keyboard.type('UI-WIDGET', { delay: 15 });
        await page.keyboard.press('Tab');
        await quantity(2);
        await inject('UI-WIDGET\n'); await quantity(3);
        await inject('UI-WIDGET\t'); await quantity(4);
        await page.keyboard.press('Enter');
        assert.equal(writes, 4);
        const quantityInput = page.locator('.qing_scan_options input[type=number]');
        await quantityInput.fill('2');
        assert.equal(await quantityInput.evaluate(el => document.activeElement === el), true);
        await page.getByRole('button', { name: 'Return to scanning', exact: true }).click();
        await focus(); await inject('UI-WIDGET\n'); await quantity(6);
        await quantityInput.fill('1');
        await page.getByRole('button', { name: 'Cancel session', exact: true }).click();
        await page.locator('.modal').waitFor();
        await inject('UI-WIDGET\n');
        assert.equal(writes, 5);
        await page.locator('.modal-header [aria-label=Close]').click();
        await page.getByRole('button', { name: 'Return to scanning', exact: true }).click();
        await context.setOffline(true);
        await page.getByRole('status').filter({ hasText: 'Offline. Scanning' }).waitFor();
        await inject('UI-WIDGET\n');
        assert.equal(writes, 5);
        await context.setOffline(false);
        await focus();
        // Cancel camera without leaving scanning paused; then record exactly one camera result.
        await page.getByRole('button', { name: 'Camera', exact: true }).click();
        await page.locator('.modal video').waitFor();
        await page.locator('.modal-header [aria-label=Close]').click();
        await focus();
        await page.evaluate(() => { window.__testCameraResult = true; });
        await page.getByRole('button', { name: 'Camera', exact: true }).click();
        await quantity(7);
        await page.locator('.modal').waitFor({ state: 'detached' });
        assert.equal(writes, 6);
        // Native permission failure is shown in its dialog and remains recoverable.
        await page.evaluate(() => {
            navigator.mediaDevices.getUserMedia = async () => { throw new DOMException('Test denial', 'NotAllowedError'); };
        });
        await page.getByRole('button', { name: 'Camera', exact: true }).click();
        await page.locator('.modal').filter({ hasText: 'Odoo needs your authorization first.' }).waitFor();
        await page.locator('.modal-header [aria-label=Close]').click();
        await focus();
        let dropped = false;
        await page.route('**/web/dataset/call_kw/qing.stock.scan.session/execute', async route => {
            if (!dropped) { dropped = true; await route.fetch(); await route.abort('failed'); }
            else { await route.continue(); }
        });
        await inject('UI-WIDGET\n');
        await page.getByRole('button', { name: 'Check and retry', exact: true }).waitFor();
        await page.reload();
        await page.getByRole('button', { name: 'Check and retry', exact: true }).click();
        await quantity(8);
        assert.equal(await page.locator('.qing_scan_pda_toggle').isChecked(), true);
        await page.getByRole('button', { name: 'Keyboard', exact: true }).click();
        await page.waitForFunction(() => document.querySelector('#qing_barcode_input')?.getAttribute('inputmode') === 'text');
        await page.getByRole('button', { name: 'Keyboard', exact: true }).click();
        await page.waitForFunction(() => document.querySelector('#qing_barcode_input')?.getAttribute('inputmode') === 'none');
        for (const viewport of [{ width: 320, height: 640 }, { width: 360, height: 800 }, { width: 800, height: 360 }]) {
            await page.setViewportSize(viewport);
            assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
        }
        await page.setViewportSize({ width: 360, height: 800 });
        await page.screenshot({ path: '/tmp/qing-pda-browser/android.png', fullPage: true });
        assert.deepEqual(errors, []);
        console.log('PASS Android text injection, Enter/Tab, repeated scans, focus, modal/offline guards, camera cancel/result/denial, lost acknowledgement recovery, PDA mode persistence, 320/360/800px layout. Physical hardware not tested.');
    } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
