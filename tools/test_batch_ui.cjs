// Run: NODE_PATH=/path/to/node_modules node tools/test_batch_ui.cjs (jsdom required).
const {JSDOM}=require('jsdom');
const fs=require('fs');const assert=require('assert');const html=fs.readFileSync(require('path').join(__dirname,'../web_dialogue/index.html'),'utf8');
const dom=new JSDOM(html,{runScripts:'dangerously',url:'http://127.0.0.1:1234/',beforeParse(w){w.HTMLMediaElement.prototype.pause=()=>{};w.HTMLMediaElement.prototype.load=()=>{};w.fetch=async()=>({ok:true,json:async()=>({token:'x',voices:[{id:'zh-CN-XiaoxiaoNeural',label:'a'},{id:'zh-CN-XiaoyiNeural',label:'b'}]})});}});
setTimeout(()=>{
const w=dom.window,d=w.document;
w.eval(`batchFiles=[{name:'a.json',content:'[]'},{name:'b.json',content:'[]'}];setSourceMode('batch');renderBatchFiles();displayBatchPreview({groups:[{id:'a',name:'a',status:'ready',preview:{speakers:['A','B'],rows:[{speaker:'A',text:'你好'}],script:{lines:[{speaker:'A',text:'你好'}]}}},{id:'b',name:'b',status:'error',error:'字段缺失'}]});`);
assert.equal(d.querySelectorAll('#preview .batchrow').length,2);assert.equal(d.querySelectorAll('#voices select').length,2);assert.equal(w.eval('lineCount()'),1);
d.getElementById('consent').checked=true;w.eval('updateGenerate()');assert.equal(d.getElementById('generate').disabled,false);
let cb=d.querySelector('#preview input');cb.checked=false;cb.dispatchEvent(new w.Event('change'));assert.equal(d.getElementById('generate').disabled,true);assert.deepEqual(JSON.parse(w.eval('JSON.stringify(sourcePayload().excluded)')),['a','b']);
w.eval(`renderBatchStatus({id:'a',status:'done',completed:1,total:2,items:[{name:'../secret',status:'done',download_url:'/api/jobs/abc/audio'},{name:'bad',status:'error',error:'no'}],zip_url:'/api/batches/abc/zip'});`);
assert.equal(d.getElementById('batchzip').hidden,false);assert.equal(d.getElementById('retrybatch').hidden,false);assert.equal(d.querySelectorAll('#batchresults a').length,1);
assert.equal(w.eval("safeBatchDownload('/api/batches/abc/audio/001')"),'/api/batches/abc/audio/001');assert.equal(w.eval("safeBatchDownload('https://evil/a')"),null);assert.equal(w.eval("safeBatchDownload('/api/jobs/../audio')"),null);
w.eval(`batchFiles=[{name:'a.xml',content:''},{name:'b.xml',content:''},{name:'c.xml',content:''}];renderBatchFiles();`);assert.equal(d.querySelectorAll('#pairfirst option').length,3);
w.eval(`setSourceMode('json');displayPreview({speakers:['A'],rows:[{speaker:'A',text:'hello'}],script:{lines:[{speaker:'A',text:'hello'}]}});`);assert.equal(d.getElementById('source').hidden,false);assert.equal(d.getElementById('batchpanel').hidden,true);assert.equal(d.querySelectorAll('#preview .line').length,1);
w.eval(`displayPreview({speakers:['USER','AGENT'],rows:[],script:{lines:[]}})`);assert.equal(w.eval('voiceMap.USER'),'zh-CN-XiaoyiNeural');assert.equal(w.eval('voiceMap.AGENT'),'zh-CN-XiaoxiaoNeural');w.eval(`voiceMap.USER='zh-CN-XiaoxiaoNeural';displayPreview({speakers:['USER','AGENT'],rows:[],script:{lines:[]}})`);assert.equal(w.eval('voiceMap.USER'),'zh-CN-XiaoxiaoNeural');
console.log('PASS: batch preview, error groups, shared voices, exclusion, download URLs, manual pairing selectors, single mode regression');dom.window.close();
},30);
