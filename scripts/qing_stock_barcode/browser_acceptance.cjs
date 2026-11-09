// Disposable database only. Run seed_browser.py before each execution.
// Requires Playwright; screenshots are written to /tmp/qing-barcode-browser.
const { chromium } = require('playwright');
const assert=require('node:assert/strict');
require('node:fs').mkdirSync('/tmp/qing-barcode-browser', { recursive: true });
(async()=>{
 const browser=await chromium.launch({headless:true});
 const context=await browser.newContext({viewport:{width:1366,height:900}});
 const page=await context.newPage();
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.request.post('http://127.0.0.1:19996/web/session/authenticate',{data:{jsonrpc:'2.0',method:'call',params:{db:'qing_barcode_test',login:'barcode_admin',password:'barcode-isolated-test'},id:1}});
 await page.goto('http://127.0.0.1:19996/odoo/action-qing_stock_barcode.action_scan_workbench?debug=assets');
 await page.locator('.qing_scan_task_row').filter({ has: page.locator('.qing_scan_start') }).filter({hasText:'Browser acceptance'}).last().locator('.qing_scan_task_select').click();
 await page.locator('.qing_scan_task_row').filter({ has: page.locator('.qing_scan_start') }).filter({hasText:'Browser acceptance'}).last().locator('.qing_scan_start').click();
 const amount=page.locator('.qing_scan_scanned').first();
 await page.locator('.qing_scan_session_header h2').click();
 await page.keyboard.type('UI-WIDGET',{delay:15});await page.keyboard.press('Enter');
 await page.waitForFunction(()=>document.querySelector('.qing_scan_scanned')?.textContent==='1');
 await page.keyboard.type('UI-WIDGET',{delay:15});await page.keyboard.press('Enter');
 await page.waitForFunction(()=>document.querySelector('.qing_scan_scanned')?.textContent==='2');
 await page.locator('#qing_barcode_input').fill('UI-WIDGET');await page.locator('#qing_barcode_input').press('Enter');
 await page.waitForFunction(()=>document.querySelector('.qing_scan_scanned')?.textContent==='3');
 await page.locator('#qing_barcode_input').fill('WRONG-SKU');await page.locator('#qing_barcode_input').press('Enter');
 await page.locator('.qing_scan_feedback').filter({hasText:'Unknown barcode'}).waitFor();
 assert.equal(await amount.innerText(),'3');
 // Simulate a server commit followed by loss of its HTTP response.
 let dropped=false;
 await page.route('**/web/dataset/call_kw/qing.stock.scan.session/execute',async route=>{
   if(!dropped){dropped=true;await route.fetch();await route.abort('failed');}else await route.continue();
 });
 await page.locator('#qing_barcode_input').fill('UI-WIDGET');await page.locator('#qing_barcode_input').press('Enter');
 await page.getByRole('button',{name:'Check and retry',exact:true}).waitFor();
 await page.reload();
 await page.getByRole('button',{name:'Check and retry',exact:true}).click();
 await page.waitForFunction(()=>document.querySelector('.qing_scan_scanned')?.textContent==='4');
 await page.getByRole('button',{name:'Check and retry',exact:true}).waitFor({state:'hidden'});
 assert.equal(await page.getByRole('button',{name:'Check and retry',exact:true}).count(),0);
 await page.setViewportSize({width:390,height:844});
 await page.screenshot({path:'/tmp/qing-barcode-browser/mobile.png',fullPage:true});
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
 await page.setViewportSize({width:1366,height:900});
 await page.getByRole('button',{name:'Validate',exact:true}).click();
 await page.getByRole('button',{name:'Create Backorder',exact:true}).waitFor();
 await page.getByRole('button',{name:'Create Backorder',exact:true}).click();
 await page.locator('.qing_scan_session_header .badge').filter({hasText:'Done'}).waitFor();
 await page.locator('.o_qing_scan').evaluate(el=>{el.scrollTop=0;});
 await page.screenshot({path:'/tmp/qing-barcode-browser/completed.png',fullPage:true});
 console.log('PASS keyboard repeated scans, focused input Enter, wrong code, lost response + reload, mobile width, native backorder completion');
 assert.deepEqual(errors, []);
 console.log('PAGEERRORS',JSON.stringify(errors));
 console.log('BODY',(await page.locator('.o_qing_scan').innerText()).slice(0,5000));
 await browser.close();
})();
