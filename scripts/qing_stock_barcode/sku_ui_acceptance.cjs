// Run seed_browser.py + seed_sku_ui.py in the disposable database first.
const { chromium, devices } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const output = '/tmp/qing-sku-ui-browser';
fs.mkdirSync(output, { recursive: true });
(async () => {
    const browser = await chromium.launch({ headless: true });
    try {
        const context = await browser.newContext({ ...devices['Pixel 7'], reducedMotion: 'reduce' });
        const page = await context.newPage();
        const errors = [];
        page.on('pageerror', error => errors.push(error.message));
        await page.request.post('http://127.0.0.1:19996/web/session/authenticate', { data: {
            jsonrpc: '2.0', method: 'call', params: { db: 'qing_barcode_test', login: 'scan_ui_zh', password: 'scan-ui-isolated-test' },
        } });
        // Production/minified assets as well as the Qing theme must support the new layout.
        await page.goto('http://127.0.0.1:19996/odoo/action-qing_stock_barcode.action_scan_workbench');
        assert.equal(await page.locator('.qing_scan_eyebrow').count(), 0);
        assert.equal((await page.locator('.qing_scan_header').innerText()).includes('QINGERP'), false);
        assert.deepEqual(await page.locator('.qing_scan_nav button span').allTextContents(), ['收货', '发货', '调拨', '盘点', '查库存']);
        assert.equal(await page.locator('.qing_scan_nav button svg path').count(), 5);
        assert.deepEqual(await page.locator('.qing_scan_task_header span').allTextContents(), ['单号', '库位', '负责人', '操作']);
        assert.equal(await page.locator('.qing_scan_input_panel').count(), 0);
        const task = page.locator('.qing_scan_task_row').filter({ hasText: 'SKU 界面验收' });
        assert.ok((await task.innerText()).includes('仓库负责人'));
        await task.locator('.qing_scan_task_select').click();
        await page.locator('.qing_scan_task_row.qing_scan_selected').waitFor();
        assert.equal(await task.locator('.qing_scan_task_select').getAttribute('aria-pressed'), 'true');
        assert.equal(await task.evaluate(el => getComputedStyle(el).backgroundColor), 'rgb(231, 241, 255)');
        assert.equal(await page.locator('.qing_scan_session_header').count(), 0);
        for (const width of [320, 390, 1366]) {
            await page.setViewportSize({ width, height: 844 });
            assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
            await page.locator('.qing_scan_task_list').screenshot({ path: `${output}/task-list-${width}.png` });
        }
        await page.setViewportSize({ width: 390, height: 844 });
        await task.locator('.qing_scan_start').click();
        await page.locator('.qing_scan_pda_toggle').check();
        const input = page.locator('#qing_barcode_input');
        await page.locator('.qing_scan_mode_button').click();
        await page.locator('.qing_scan_mode_choice').first().waitFor();
        assert.equal(await page.locator('.qing_scan_mode_choice').count(), 6);
        assert.equal(await page.locator('.qing_scan_mode_choice').evaluateAll(items => items.every(el => parseFloat(getComputedStyle(el).fontSize) >= 18 && el.getBoundingClientRect().height >= 52)), true);
        await page.locator('.modal-content').screenshot({ path: `${output}/mode-dialog.png` });
        await page.locator('.qing_scan_mode_choice').filter({ hasText: '产品' }).click();
        await page.locator('.modal').waitFor({ state: 'detached' });
        assert.equal(await page.locator('.qing_scan_mode_button').innerText(), '产品');
        await page.waitForFunction(() => document.activeElement?.id === 'qing_barcode_input');

        assert.equal(await input.getAttribute('placeholder'), '条码 / SKU');
        const geometry = await input.evaluate(el => {
            const s = getComputedStyle(el);
            return { height: el.getBoundingClientRect().height, top: parseFloat(s.paddingTop), bottom: parseFloat(s.paddingBottom), line: parseFloat(s.lineHeight), borders: parseFloat(s.borderTopWidth) + parseFloat(s.borderBottomWidth) };
        });
        assert.equal(geometry.top, geometry.bottom);
        assert.ok(Math.abs(geometry.height - geometry.top - geometry.bottom - geometry.line - geometry.borders) < 1);
        const card = page.locator('.qing_scan_progress');
        async function checkProgress(percent) {
            await page.waitForFunction(value => document.querySelector('.qing_scan_progress_track')?.getAttribute('aria-valuenow') === String(value), percent);
            const fill = await card.evaluate(el => {
                const fill = el.querySelector('.qing_scan_progress_fill');
                const style = getComputedStyle(fill);
                return { width: parseFloat(style.width), total: el.querySelector('.qing_scan_progress_track').clientWidth, gradient: style.backgroundImage };
            });
            assert.equal(await page.locator('.qing_scan_document_progress').getAttribute('aria-valuenow'), String(percent));
            const header = await page.locator('.qing_scan_session_header').evaluate(el => ({ fill: parseFloat(getComputedStyle(el, '::before').width), width: el.clientWidth, gradient: getComputedStyle(el, '::before').backgroundImage }));
            assert.ok(header.gradient.includes('linear-gradient'));
            assert.ok(Math.abs(header.fill / header.width * 100 - percent) < .2);
            assert.ok(Math.abs(fill.width / fill.total * 100 - percent) < .2);
            assert.ok(fill.gradient.includes('linear-gradient'));
            await page.locator('.qing_scan_session_header').screenshot({ path: `${output}/header-${percent}.png` });
            await card.screenshot({ path: `${output}/progress-${percent}.png` });
        }
        await checkProgress(0);
        assert.equal(await page.locator('.qing_scan_responsible').innerText(), '仓库负责人');
        assert.equal(await page.locator('.qing_scan_session_actions svg').count(), 2);
        assert.equal(await page.locator('.qing_scan_operation_details').count(), 0);
        await page.locator('.qing_scan_progress_toggle').click();
        await page.locator('.qing_scan_operation_details').waitFor();
        await page.locator('.qing_scan_progress').screenshot({ path: `${output}/details-expanded.png` });
        await page.locator('.qing_scan_progress_toggle').click();
        await page.locator('.qing_scan_operation_details').waitFor({ state: 'detached' });
        assert.equal(await page.locator('.qing_scan_operation_details').count(), 0);
        assert.equal(await page.locator('.qing_scan_scanned').evaluate(el => getComputedStyle(el).color), 'rgb(25, 135, 84)');
        await page.locator('.o_qing_scan').evaluate(el => { el.scrollTop = 0; });
        await page.screenshot({ path: `${output}/mobile-top.png` });
        await page.locator('.qing_scan_options input[type=number]').fill('5');
        assert.equal(await page.locator('.qing_scan_confirm').isDisabled(), true);
        await input.fill('FURN_7777');
        await page.locator('.qing_scan_confirm:not([disabled])').waitFor();
        await page.locator('.qing_scan_confirm').click();
        await checkProgress(50);
        const feedbackTop = await page.locator('.qing_scan_feedback').evaluate(el => el.getBoundingClientRect().top);
        const panelTop = await page.locator('.qing_scan_input_panel').evaluate(el => el.getBoundingClientRect().top);
        assert.ok(feedbackTop < panelTop);
        const progressSession = await page.evaluate(() => Object.keys(localStorage).filter(key => key.endsWith(':session')).map(key => localStorage.getItem(key)));
        await page.locator('.qing_scan_back').click();
        const active = page.locator('.qing_scan_task_row').filter({ hasText: 'SKU 界面验收' });
        await page.waitForFunction(() => document.querySelector('.qing_scan_task_active')?.style.getPropertyValue('--qing-progress') === '50%');
        assert.equal((await page.locator('.o_qing_scan').innerText()).includes('继续已有作业'), false);
        assert.equal((await active.innerText()).includes('50%'), false);
        const rowFill = await active.evaluate(el => ({ width: parseFloat(getComputedStyle(el, '::before').width), total: el.clientWidth, image: getComputedStyle(el, '::before').backgroundImage }));
        assert.ok(rowFill.image.includes('linear-gradient'));
        assert.ok(Math.abs(rowFill.width / rowFill.total * 100 - 50) < .2);
        await active.locator('.qing_scan_task_select').click();
        await active.screenshot({ path: `${output}/active-row-50.png` });
        await page.screenshot({ path: `${output}/active-list.png` });
        await active.locator('.qing_scan_start').click();
        await page.locator('.qing_scan_session_header').waitFor();
        assert.deepEqual(await page.evaluate(() => Object.keys(localStorage).filter(key => key.endsWith(':session')).map(key => localStorage.getItem(key))), progressSession);
        await checkProgress(50);
        assert.equal(await page.locator('.qing_scan_confirm').isDisabled(), true);
        assert.equal(await page.locator('.qing_scan_entry button').count(), 2);
        assert.equal(await page.locator('.qing_scan_refocus').getAttribute('aria-label'), '返回扫码');
        assert.equal(await page.locator('.qing_scan_keyboard').getAttribute('aria-label'), '软键盘');
        for (const [width, height] of [[320, 800], [390, 844], [800, 360], [1366, 900]]) {
            await page.setViewportSize({ width, height });
            await page.locator('.o_qing_scan').evaluate(el => { el.scrollTop = 0; });
            assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
            const overflow = await page.locator('.qing_scan_nav button').evaluateAll(buttons => buttons.some(el => el.scrollWidth > el.clientWidth));
            assert.equal(overflow, false);
            assert.equal(await page.locator('.qing_scan_nav_icon').evaluateAll(icons => icons.every(el => el.getBoundingClientRect().height >= 24)), true);
            await page.screenshot({ path: `${output}/layout-${width}.png` });
        }
        await page.setViewportSize({ width: 390, height: 844 });
        await input.fill('FURN_7777'); await input.press('Tab');
        await checkProgress(100);
        await page.getByRole('button', { name: '验证', exact: true }).click();
        await page.locator('.qing_scan_session_header .badge').filter({ hasText: '已完成' }).waitFor();
        await checkProgress(100);
        await page.locator('.qing_scan_back').click();
        await page.locator('.qing_scan_view').first().waitFor();
        const completed = page.locator('.qing_scan_task_row').filter({ hasText: 'SKU 界面验收' });
        await completed.locator('.qing_scan_view').waitFor();
        assert.equal(await completed.locator('.qing_scan_start').count(), 0);
        assert.equal(await completed.locator('.qing_scan_view').getAttribute('aria-label'), '查看已完成明细');
        assert.equal(await page.locator('.qing_scan_input_panel').count(), 0);
        assert.equal(await page.locator('.qing_scan_feedback').count(), 0);
        await page.locator('.o_qing_scan').evaluate(el => { el.scrollTop = 0; });
        await page.screenshot({ path: `${output}/completed-list.png` });
        await page.getByRole('button', { name: '查库存', exact: true }).click();
        await input.fill('FURN_7777'); await input.press('Enter');
        await page.locator('.qing_scan_stock_table tbody tr').filter({ hasText: 'Office Chair' }).waitFor();
        assert.ok((await page.locator('.qing_scan_stock_table tbody tr').filter({ hasText: 'Office Chair' }).innerText()).includes('10'));
        assert.deepEqual(errors, []);
        console.log('PASS task list columns/responsible/selection, readable mode dialog, green scanned quantity, Chinese SVG navigation, no QINGERP, centered input, inline camera/check controls, empty-input disabled, translated icon labels, top feedback, SKU-only product via check/Tab/Enter, 0/50/100 gradient, completion, SKU lookup, 320/390/800/1366px');
    } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
