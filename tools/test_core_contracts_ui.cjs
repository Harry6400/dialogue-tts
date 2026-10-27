// Fictional DOM regressions; does not claim native or network acceptance.
const {JSDOM}=require('jsdom'),assert=require('assert'),fs=require('fs');
const html=fs.readFileSync(require('path').join(__dirname,'../web_dialogue/index.html'),'utf8');
const ids=['Xiaoxiao','Xiaoyi','Yunxi','Yunjian','Yunxia','Yunyang'].map(n=>'zh-CN-'+n+'Neural');
const dom=new JSDOM(html,{runScripts:'dangerously',url:'http://127.0.0.1:1234/',beforeParse(w){w.HTMLMediaElement.prototype.pause=()=>{};w.HTMLMediaElement.prototype.load=()=>{};w.fetch=async()=>({ok:true,json:async()=>({token:'t',voices:ids.map(id=>({id,label:id,locale:'zh-CN'}))})});}});
setTimeout(async()=>{try{
 const w=dom.window,d=w.document;
 for(const id of ['xmlunit','batchunit','swapxml','voice-search'])assert.equal(d.getElementById(id),null);
 w.eval(`xmlFiles=[{name:'user.xml',content:'x'},{name:'agent.xml',content:'x'}];displayPreview({speakers:['USER','AGENT'],sources:[{role:'USER'},{role:'AGENT'}],rows:[{speaker:'USER',text:'one'},{speaker:'USER',text:'two'},{speaker:'AGENT',text:'three'}],script:{lines:[{speaker:'USER',text:'one'}]},voice_choice:{user:'zh-CN-YunxiNeural',reason:'称呼：先生'}});`);
 assert.equal(d.querySelector('#voice-0').closest('.voicerow').textContent.includes('客服'),true);
 assert.equal(d.querySelector('#voice-1').value,ids[2]);assert.equal(w.eval('voiceMap.USER'),ids[1],'automatic effective voice must not overwrite global default');
 assert.deepEqual([...d.querySelectorAll('.xmlfile')].map(x=>x.dataset.role),['USER','AGENT']);
 assert.deepEqual([...d.querySelectorAll('.line')].map(x=>x.dataset.role),['USER','USER','AGENT']);
 w.eval(`$('result').hidden=false;$('download').href='/api/jobs/old/audio';generatedResourcePaths.set('/api/jobs/old/audio','old.mp3');invalidate()`);
 assert.equal(d.getElementById('result').hidden,false);assert.equal(d.getElementById('download').getAttribute('href'),'/api/jobs/old/audio');assert.equal(d.getElementById('resultcontext').hidden,false);assert.equal(w.dialogueCloseSafety().unsaved,true);
 w.dialogueShowUnsaved();assert.equal(d.querySelector('#unsavedlist a').getAttribute('href'),'/api/jobs/old/audio');
 let posts=0;w.eval(`setSourceMode('batch');parsed={};batchGroups=[{id:'g',status:'ready',preview:{script:{lines:[{}]}}}];$('consent').checked=true;api=async()=>{window.posts++;return {id:'never'}}`);w.posts=0;await w.eval('generate()');posts=w.posts;assert.equal(posts,0);assert.match(d.getElementById('joberror').textContent,/结果文件夹/);assert.equal(d.getElementById('result').hidden,false);
 w.eval("outputFolder={folder_id:'x'};folderCall=async()=>({ok:true,writable:false,free_bytes:99999999})");await w.eval('generate()');assert.equal(w.posts,0);assert.match(d.getElementById('joberror').textContent,/不可写/);
 console.log('PASS: effective/default voices, fixed roles, retained results, batch authorization, exit save ledger');
 }catch(e){console.error(e);process.exitCode=1;}finally{dom.window.close();}},30);
