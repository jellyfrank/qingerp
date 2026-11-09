/* Run against the disposable fixture only. PLAYWRIGHT_MODULE points to the installed package. */
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const base = process.env.TEST_URL || 'http://127.0.0.1:18998';
const out = process.env.TEST_OUTPUT || '/tmp/qing-spreadsheet-browser';
fs.mkdirSync(out, {recursive:true});
(async () => {
 const browser=await chromium.launch({headless:true});
 const context=await browser.newContext({viewport:{width:1440,height:1000}});
 const page=await context.newPage();
 const errors=[],failedRequests=[];
 page.on('pageerror',e=>errors.push(String(e)));
 page.on('response',r=>{if(r.status()>=400)failedRequests.push([r.status(),r.url()])});
 try {
  for (let attempt=0;attempt<30;attempt++) {
   try { const response=await page.request.get(base+'/web/login?db=qing_spreadsheet_test');if(response.ok())break; } catch {}
   await new Promise(resolve=>setTimeout(resolve,1000));
  }
  await page.goto(base+'/web/login?db=qing_spreadsheet_test');
  await page.locator('input[name=login]').fill('sheet_admin');
  await page.locator('input[name=password]').fill('sheet-test-local-only');
  await page.locator('button[type=submit]').click();
  await page.waitForURL(/odoo/,{timeout:30000});
  await page.goto(base+'/odoo?debug='+(process.env.ASSET_MODE||'assets'));
  await page.waitForFunction(()=>Boolean(odoo.__WOWL_DEBUG__?.root?.env?.services?.action),{timeout:30000});
  await page.evaluate(async()=>{
   const env=odoo.__WOWL_DEBUG__.root.env;
   await odoo.loader.modules.get('@web/core/assets').loadBundle('spreadsheet.o_spreadsheet');
   const {WorkbookEditor}=odoo.loader.modules.get('@qing_spreadsheet/bundle/editor');
   const original=WorkbookEditor.prototype.setup;
   WorkbookEditor.prototype.setup=function(){original.call(this);window.sheetEditor=this;};
   await env.services.action.doAction('qing_spreadsheet.action_workbooks');
  });
  await page.getByText('New',{exact:true}).click();
  await page.locator('.o_field_widget[name=name] input').fill('Acceptance '+Date.now());
  await page.getByRole('button',{name:'Open Spreadsheet',exact:true}).click();
  await page.locator('.o_qing_spreadsheet').waitFor();
  const bookId=await page.evaluate(()=>sheetEditor.id);
  assert.equal(await page.evaluate(()=>sheetEditor.state.editable),true);
  // Actual keyboard entry through the native editor.
  const clickCell=async(col,row=0)=>{ const box=await page.locator('.o-grid').first().boundingBox();await page.mouse.click(box.x+48+col*96+48,box.y+26+row*23+11); };
  await clickCell(0);await page.keyboard.type('12');await page.keyboard.press('Enter');
  await page.waitForTimeout(300);
  await page.evaluate(()=>{
   const model=sheetEditor.model,sheetId=model.getters.getActiveSheetId();
   model.dispatch('UPDATE_CELL',{sheetId,col:1,row:0,content:'=SUM(A1,3)'});
   for(let row=1;row<=6;row++)model.dispatch('UPDATE_CELL',{sheetId,col:0,row,content:String(row)});
   model.dispatch('SET_FORMATTING',{sheetId,target:[{left:0,right:1,top:0,bottom:0}],style:{bold:true}});
  });
  await page.getByRole('button',{name:'Save',exact:true}).click();
  await page.waitForFunction(()=>!sheetEditor.state.busy);
  assert.equal(await page.evaluate(()=>sheetEditor.state.error),'');
  assert.equal(await page.evaluate(()=>sheetEditor.state.version),1);
  const stored=await page.evaluate(()=>sheetEditor.call('open_workbook'));
  assert.equal(stored.data.sheets[0].cells.A1,'12');
  assert.equal(await page.evaluate(()=>sheetEditor.model.getters.getEvaluatedCell({sheetId:sheetEditor.model.getters.getActiveSheetId(),col:1,row:0}).value),15);
  // Data source dialog and native list rendering.
  await clickCell(3);
  assert.equal(await page.evaluate(()=>sheetEditor.model.getters.getActivePosition().col),3);
  await page.getByRole('button',{name:'Insert Odoo data',exact:true}).click();
  await page.getByRole('dialog').waitFor();
  await page.getByRole('button',{name:'Insert',exact:true}).click();
  await page.waitForTimeout(1200);
  assert.equal(await page.getByRole('dialog').count(),0);
  await page.getByRole('button',{name:'Save',exact:true}).click();
  await page.waitForFunction(()=>!sheetEditor.state.busy);
  assert.equal(await page.evaluate(()=>sheetEditor.state.error),'');
  assert.equal(await page.evaluate(()=>sheetEditor.model.getters.getListIds().length),1);
  // Update an existing source and add a server-aggregated pivot.
  await page.getByRole('button',{name:'Data sources',exact:true}).click();
  await page.getByRole('dialog').getByRole('button',{name:'Edit',exact:true}).click();
  await page.getByRole('dialog').getByRole('button',{name:'Update',exact:true}).click();
  await page.waitForTimeout(300);
  await clickCell(8);
  assert.equal(await page.evaluate(()=>sheetEditor.model.getters.getActivePosition().col),8);
  await page.getByRole('button',{name:'Insert Odoo data',exact:true}).click();
  await page.getByRole('dialog').getByLabel('Insert as').selectOption('pivot');
  await page.getByRole('dialog').getByLabel('Group by').selectOption('is_company');
  await page.getByRole('button',{name:'Insert',exact:true}).click();
  await page.evaluate(()=>odoo.loader.modules.get('@spreadsheet/helpers/model').waitForDataLoaded(sheetEditor.model));
  await page.getByRole('button',{name:'Save',exact:true}).click();
  await page.waitForFunction(()=>!sheetEditor.state.busy);
  assert.equal(await page.evaluate(()=>sheetEditor.state.error),'');
  await page.waitForFunction(()=>sheetEditor.model.getters.getEvaluatedCell({sheetId:sheetEditor.model.getters.getActiveSheetId(),col:8,row:0}).type !== 'error', {timeout:10000});
  console.log('PIVOT',await page.evaluate(()=>{
    const m=sheetEditor.model,id=m.getters.getPivotIds()[0];
    return {id, definition:m.getters.getPivotCoreDefinition(id),cell:m.getters.getEvaluatedCell({sheetId:m.getters.getActiveSheetId(),col:8,row:0})};
  }));
  assert.equal(await page.evaluate(()=>sheetEditor.model.getters.getEvaluatedCell({sheetId:sheetEditor.model.getters.getActiveSheetId(),col:8,row:0}).type),'text');
  const chartResult=await page.evaluate(()=>{
   const m=sheetEditor.model;
   return m.dispatch('CREATE_CHART',{sheetId:m.getters.getActiveSheetId(),chartId:'acceptance-chart',figureId:'acceptance-figure',col:0,row:12,offset:{x:0,y:0},definition:{
     type:'bar',title:{text:'Acceptance chart'},dataSource:{type:'range',dataSets:[{dataRange:'A1:A7'}]},background:'#fff',verticalAxisPosition:'left',legendPosition:'top',stackedBar:false,dataSetStyles:{}
   }}).isSuccessful;
  });
  assert.equal(chartResult,true);
  await page.getByRole('button',{name:'Save',exact:true}).click();await page.waitForFunction(()=>!sheetEditor.state.busy);
  assert.equal(await page.evaluate(()=>sheetEditor.state.error),'');
  await page.screenshot({path:out+'/desktop.png'});
  // XLSX export route, and import creates a second independent workbook.
  await page.getByRole('button',{name:'Workbook',exact:true}).click();
  const downloadPromise=page.waitForEvent('download');
  await page.getByRole('button',{name:'Export XLSX',exact:true}).click();
  const file=await downloadPromise;await file.saveAs(out+'/roundtrip.xlsx');
  assert.ok(fs.statSync(out+'/roundtrip.xlsx').size>1000);
  await page.waitForFunction(()=>!sheetEditor.state.busy);
  console.log('XLSX exported',fs.statSync(out+'/roundtrip.xlsx').size);
  await page.locator('input[type=file]').setInputFiles(out+'/roundtrip.xlsx');
  await page.waitForFunction(id=>sheetEditor.id!==id,bookId,{timeout:30000});
  await page.waitForTimeout(500);
  assert.equal(await page.evaluate(()=>sheetEditor.model.getters.getEvaluatedCell({sheetId:sheetEditor.model.getters.getActiveSheetId(),col:1,row:0}).value),15);
  console.log('Imported workbook',await page.evaluate(()=>sheetEditor.id));
  assert.equal(await page.evaluate(()=>sheetEditor.model.exportData().sheets[0].figures.length),1);
  await page.locator('.o_notification_close').click();
  // Restore a saved version through both confirmation dialogs.
  await page.evaluate(()=>sheetEditor.model.dispatch('UPDATE_CELL',{sheetId:sheetEditor.model.getters.getActiveSheetId(),col:0,row:0,content:'999'}));
  await page.getByRole('button',{name:'Save',exact:true}).click();await page.waitForFunction(()=>!sheetEditor.state.busy);
  await page.getByRole('button',{name:'History',exact:true}).click();
  await page.getByRole('dialog').locator('tbody tr').filter({has:page.locator('td:first-child',{hasText:/^1$/})}).getByRole('button',{name:'Restore',exact:true}).click();
  await page.getByRole('dialog').last().getByRole('button',{name:'Ok',exact:true}).click();
  await page.waitForFunction(()=>!sheetEditor.state.busy && sheetEditor.state.version===3);
  assert.equal(await page.evaluate(()=>sheetEditor.model.getters.getEvaluatedCell({sheetId:sheetEditor.model.getters.getActiveSheetId(),col:0,row:0}).value),12);
  // The business editor uses the same component and applies only after review.
  const recordId=await page.evaluate(async()=>{
   const env=odoo.__WOWL_DEBUG__.root.env;
   const id=(await env.services.orm.create('qing.spreadsheet.fixture',[{name:'Browser record adapter',value:12}]))[0];
   const action=await env.services.orm.call('qing.spreadsheet.fixture','action_open_qing_spreadsheet',[[id]]);
   await env.services.action.doAction(action);return id;
  });
  await page.getByRole('button',{name:'Review and apply to record',exact:true}).waitFor();
  await clickCell(1);await page.keyboard.type('27');await page.keyboard.press('Enter');
  await page.getByRole('button',{name:'Review and apply to record',exact:true}).click();
  await page.getByRole('dialog').getByRole('button',{name:'Apply to record',exact:true}).click();
  await page.waitForFunction(()=>!sheetEditor.state.busy && sheetEditor.state.version===1);
  const applied=await page.evaluate(id=>odoo.__WOWL_DEBUG__.root.env.services.orm.read('qing.spreadsheet.fixture',[id],['value','apply_count']),recordId);
  assert.equal(applied[0].value,27);assert.equal(applied[0].apply_count,1);
  // Compact viewport is deliberately read-only and remains usable.
  await page.setViewportSize({width:390,height:844});
  await page.evaluate(()=>sheetEditor.load({recover:false}));
  assert.equal(await page.evaluate(()=>sheetEditor.state.editable),false);
  await page.screenshot({path:out+'/mobile.png'});
  assert.deepEqual(errors,[]);
  console.log(JSON.stringify({bookId,errors,failedRequests,out},null,2));
 } catch(error) {
  const details=page.getByText('See technical details');if(await details.count())await details.click();
  console.error('BODY',await page.locator('body').innerText());
  console.error('ERRORS',errors,'REQUESTS',failedRequests);
  await page.screenshot({path:out+'/failure.png'});
  throw error;
 } finally {await browser.close();}
})();
