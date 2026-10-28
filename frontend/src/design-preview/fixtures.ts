import type {Voice, WorkspaceSummary} from '../types/contracts';
export const voices: Voice[] = [
  {id:'zh-CN-XiaoxiaoNeural',label:'晓晓 · 温暖自然',gender:'Female',locale:'zh-CN'},
  {id:'zh-CN-XiaoyiNeural',label:'晓伊 · 清晰亲切',gender:'Female',locale:'zh-CN'},
  {id:'zh-CN-YunxiNeural',label:'云希 · 自然沉稳',gender:'Male',locale:'zh-CN'},
  {id:'zh-CN-YunjianNeural',label:'云健 · 浑厚有力',gender:'Male',locale:'zh-CN'},
  {id:'zh-CN-YunxiaNeural',label:'云夏 · 明亮轻快',gender:'Male',locale:'zh-CN'},
  {id:'zh-CN-YunyangNeural',label:'云扬 · 清晰专业',gender:'Male',locale:'zh-CN'},
];
export const calls = Array.from({length:12},(_,i)=>({id:i===11?'00000000000000000000202610280012':`20261028${String(i+1).padStart(4,'0')}`,segments:[18,24,16,32,20,14,28,22,16,30,12,26][i],status:i===3||i===8?'error':i===5||i===10?'saved':'ready',voice:i%3===0?'云希':'晓伊',source:i%3===0?'称呼匹配':'批次设置',error:i===3?'已有同名文件，尚未核实来源。':'缺少客户声道。'}));
export const scanWorkspace: WorkspaceSummary = {id:'fictional-preview',status:'scanning',phase:'reading',revision:1,scanned:8200,discovered:8200,total:0,ready:0,error:0,saved:0,selected:0,ready_selected:0,processed:0,save_pending:0,issues:{total:0,pairing:0,input:0,changed:0,conflict:0,synthesis:0,save:0,other:0},selection_query:{q:'',filter:'all',issue:''},input_folder:{folder_id:'fictional',path:'虚构批次'},settings:{voices:{AGENT:'zh-CN-XiaoxiaoNeural',USER:'zh-CN-XiaoyiNeural'},rate:1,pause_ms:250,auto_voice:true,manual_user_voice:false}};
