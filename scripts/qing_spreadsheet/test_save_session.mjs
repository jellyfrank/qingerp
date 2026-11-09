import assert from 'node:assert/strict';
import fs from 'node:fs';
const source = fs.readFileSync(new URL('../../qing_spreadsheet/static/src/bundle/save_session.js', import.meta.url), 'utf8');
const { SaveSession } = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
const storage = () => { const values = new Map(); return { getItem: (k) => values.get(k) || null, setItem: (k,v) => values.set(k,v), removeItem: (k) => values.delete(k) }; };
const snapshot = { sheets: [{id:'s',name:'Sheet',cells:{A1:'12'}}] };
let serverVersion = 0, saved = null, writes = 0;
const store = storage();
const call = async (method, args) => {
    if (method === 'save_status') { return { saved: Boolean(saved), version: saved?.version, current_version: serverVersion }; }
    writes++;serverVersion++;saved={version:serverVersion,requestId:args[3]};
    throw new Error('Response lost after server commit');
};
const session = new SaveSession({call,storage:store,key:'pending',version:0,token:'lease'});
await assert.rejects(session.submit(snapshot), /Response lost/);
const requestId = session.pending.requestId;
snapshot.sheets[0].cells.A1='99';
assert.equal(session.pending.data.sheets[0].cells.A1,'12');
const recovered = new SaveSession({call,storage:store,key:'pending',version:0,token:'new-lease'});
assert.equal(recovered.pending.requestId,requestId);
await recovered.retry();
assert.equal(writes,1);assert.equal(recovered.version,1);assert.equal(store.getItem('pending'),null);
// Failure before a commit is retried using the exact request identifier.
let ids=[], attempt=0;
const retryCall=async (method,args)=>{
 if(method==='save_status')return {saved:false,current_version:0};
 ids.push(args[3]);if(!attempt++)throw new Error('offline');return {version:1};
};
const retry = new SaveSession({call:retryCall,storage:storage(),key:'retry',version:0,token:'lease'});
await assert.rejects(retry.submit(snapshot),/offline/);await retry.retry();assert.deepEqual(ids,[ids[0],ids[0]]);
// A newer server version must leave the pending payload recoverable.
const conflict = new SaveSession({call:async()=>({saved:false,current_version:9}),storage:storage(),key:'conflict',version:0,token:'lease'});
await assert.rejects(conflict.submit(snapshot),/server version changed/);assert.ok(conflict.pending);
// Overlapping submissions never replace the pending UUID.
await assert.rejects(conflict.submit(snapshot),/pending save/);
console.log('Save session: lost response, pre-commit retry, immutable snapshot and conflicts passed.');

// Browser storage exhaustion still allows the server to persist the workbook.
const fullStore = {getItem:()=>null, setItem:()=>{throw new Error('quota');}, removeItem:()=>{}};
const large = new SaveSession({call:async(method)=>method==='save_status'?{saved:false,current_version:0}:{version:1},storage:fullStore,key:'large',version:0,token:'lease'});
await large.submit(snapshot);assert.equal(large.version,1);assert.ok(large.storageError);assert.equal(large.pending,null);
console.log('Storage quota fallback passed.');
