/* Run after measure_isolated.py has created the synthetic 10k source records. */
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const base = process.env.TEST_URL || 'http://127.0.0.1:18998';
(async () => {
    const browser = await chromium.launch({ headless: true });
    try {
        const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
        await page.goto(base + '/web/login?db=qing_spreadsheet_test');
        await page.locator('input[name=login]').fill('sheet_admin');
        await page.locator('input[name=password]').fill('sheet-test-local-only');
        await page.locator('button[type=submit]').click();
        await page.waitForURL(/odoo/);
        await page.goto(base + '/odoo?debug=1');
        await page.waitForFunction(() => Boolean(odoo.__WOWL_DEBUG__?.root?.env?.services?.action));
        await page.evaluate(async () => {
            await odoo.loader.modules.get('@web/core/assets').loadBundle('spreadsheet.o_spreadsheet');
            const { WorkbookEditor } = odoo.loader.modules.get('@qing_spreadsheet/bundle/editor');
            const setup = WorkbookEditor.prototype.setup;
            WorkbookEditor.prototype.setup = function () { setup.call(this); window.sheetEditor = this; };
        });
        const cdp = await page.context().newCDPSession(page);
        await cdp.send('Performance.enable');
        const results = [];
        for (const count of [1000, 10000]) {
            await page.evaluate(async (count) => {
                const env = odoo.__WOWL_DEBUG__.root.env;
                const id = (await env.services.orm.create('qing.spreadsheet', [{ name: `Browser performance ${count}` }]))[0];
                await env.services.action.doAction({ type: 'ir.actions.client', tag: 'qing_spreadsheet.editor', params: { workbook_id: id } });
                const m = sheetEditor.model, pivotId = crypto.randomUUID();
                const domain = [['name', 'ilike', 'Qing Sheet Perf ']];
                if (count === 1000) domain.push(['name', '<=', 'Qing Sheet Perf 00999']);
                m.dispatch('ADD_PIVOT', { pivotId, pivot: { name: 'Performance', type: 'ODOO', model: 'res.partner', domain,
                    context: { allowed_company_ids: [sheetEditor.book.company_id] }, columns: [], rows: [{ fieldName: 'city' }],
                    measures: [{ id: '__count', fieldName: '__count', aggregator: 'count' }], orderBy: [] } });
                m.dispatch('UPDATE_CELL', { sheetId: m.getters.getActiveSheetId(), col: 0, row: 0, content: `=PIVOT(${m.getters.getPivotFormulaId(pivotId)})` });
                await odoo.loader.modules.get('@spreadsheet/helpers/model').waitForDataLoaded(m);
                await sheetEditor.save();
            }, count);
            const timings = await page.evaluate(async () => {
                const settle = async () => {
                    await odoo.loader.modules.get('@spreadsheet/helpers/model').waitForDataLoaded(sheetEditor.model);
                    await new Promise(requestAnimationFrame); await new Promise(requestAnimationFrame);
                };
                let start = performance.now();
                await sheetEditor.load({ recover: false }); await settle();
                const reloadMs = performance.now() - start;
                start = performance.now(); sheetEditor.refresh(); await settle();
                const refreshMs = performance.now() - start;
                const m = sheetEditor.model;
                const values = Array.from({ length: 8 }, (_, row) => m.getters.getEvaluatedCell({ sheetId: m.getters.getActiveSheetId(), col: 1, row }).value);
                return { reloadMs, refreshMs, values };
            });
            assert.ok(timings.values.includes(count), JSON.stringify(timings));
            assert.ok(timings.values.includes(count / 5) && timings.values.includes(count * 4 / 5), JSON.stringify(timings));
            await cdp.send('HeapProfiler.collectGarbage');
            const metrics = (await cdp.send('Performance.getMetrics')).metrics;
            const heap = metrics.find((item) => item.name === 'JSHeapUsedSize').value;
            results.push({ records: count, reload_ms: +timings.reloadMs.toFixed(2), refresh_ms: +timings.refreshMs.toFixed(2),
                page_js_heap_mb: +(heap / 1024 / 1024).toFixed(2), totals_verified: true });
        }
        const output = JSON.stringify({ note: 'Warm assets; entire page JS heap after GC; includes SSH tunnel latency; not an SLO.', results }, null, 2);
        const filename = process.env.TEST_OUTPUT || '/tmp/qing-spreadsheet-browser/performance.json';
        fs.mkdirSync(path.dirname(filename), { recursive: true });
        fs.writeFileSync(filename, output);
        console.log(output);
    } finally { await browser.close(); }
})();
