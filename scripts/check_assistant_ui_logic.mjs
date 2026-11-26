// Minimal DOM/transport harness for the assistant controller. No browser required.
import assert from 'node:assert/strict';
import {initAssistant, defaultPaths} from '../frontend/assistant.js';
class Element extends EventTarget {
  constructor(tag='div'){ super(); this.tagName=tag; this.children=[]; this.dataset={}; this.value=''; this.disabled=false; this.hidden=false; this.className=''; this.textContent=''; }
  append(...items){ for(const item of items){item.parent=this;this.children.push(item);} }
  replaceChildren(){this.children=[];}
  remove(){if(this.parent)this.parent.children=this.parent.children.filter(x=>x!==this);}
  querySelector(sel){return this.children.find(x=>x.className.split(' ').includes(sel.slice(1)));}
  focus(){}
  requestSubmit(){this.dispatchEvent(new Event('submit',{cancelable:true}));}
  click(){if(!this.disabled)this.dispatchEvent(new Event('click'));}
}
const ids=['chat-messages','chat-input','chat-form','chat-send','chat-stop','chat-new','chat-use-form','chat-defaults','chat-continue','chat-retry','chat-status','chat-context','chat-files','chat-file-preview','chat-file-result'];
const nodes=Object.fromEntries(ids.map(id=>[id,new Element()]));
globalThis.document={getElementById:id=>nodes[id],createElement:tag=>new Element(tag)};
globalThis.window={location:{origin:'http://localhost'}};
const storage=new Map();globalThis.localStorage={getItem:k=>storage.get(k)||null,setItem:(k,v)=>storage.set(k,v)};
const requests=[]; let responder; let enabled=true; let begin=0,finish=0,applied=[],failed=[];
globalThis.fetch=async(url,options)=>{
  if(url.pathname.endsWith('/status'))return {ok:true,json:async()=>({configured:enabled,status:enabled?'configured':'disabled'})};
  requests.push(JSON.parse(options.body));
  return await responder(options);
};
const form=new Element('form');
const dashboard={form,requestBody:partial=>{assert.equal(partial,true);return {reservoir:{p_res:200}};},begin:()=>begin++,finish:()=>finish++,apply:r=>applied.push(r),fail:s=>failed.push(s)};
const result=(status='needs_input',session_id='test-session')=>({session_id,status,answer:'Ответ',parameters:{reservoir:{p_res:200}},validation:{missing:['well.p_wh'],errors:[],warnings:[],defaults_requiring_consent:{'well.roughness':0.0001}},tool_trace:[],provenance:{}});
const respond=data=>async()=>({ok:true,json:async()=>data});
assert.deepEqual(defaultPaths(result().validation), ['well.roughness']);
assert.deepEqual(defaultPaths({defaults_requiring_consent:{}}), []);
assert.deepEqual(defaultPaths({}), []);
const tick=()=>new Promise(resolve=>setImmediate(resolve));
const send=async text=>{nodes['chat-input'].value=text;nodes['chat-form'].requestSubmit();await tick();};
initAssistant(dashboard); await tick();
assert.equal(nodes['chat-send'].disabled,false);
responder=respond(result()); await send('Пластовое давление 200 атм');
assert.equal(requests[0].session_id,undefined);assert.equal(applied.length,1);assert.equal(nodes['chat-defaults'].hidden,false);
responder=respond(result('calculated'));await send('Устьевое давление 10 атм');assert.equal(requests[1].session_id,'test-session');
form.dispatchEvent(new Event('input'));assert.equal(nodes['chat-send'].disabled,false);assert.equal(nodes['chat-continue'].hidden,false);
responder=respond({...result('ok'),mode:'conversation'});await send('Что такое IPR?');assert.equal(requests.at(-1).conversation_only,true);assert.equal(applied.length,2);assert.equal(nodes['chat-continue'].hidden,false);
nodes['chat-continue'].click();assert.equal(nodes['chat-send'].disabled,false);
responder=respond(result('needs_input','new-session'));nodes['chat-use-form'].click();await tick();assert.equal(requests[3].session_id,undefined);assert.deepEqual(JSON.parse(requests[3].message),{reservoir:{p_res:200}});
responder=respond(result('needs_input','new-session'));nodes['chat-defaults'].click();await tick();assert.equal(requests[4].message,'Использовать значения по умолчанию');assert.equal(requests[4].session_id,'new-session');
let release;responder=()=>new Promise(resolve=>release=resolve);await send('долгий запрос');assert.equal(nodes['chat-stop'].hidden,false);const appliedBefore=applied.length;nodes['chat-stop'].click();assert.equal(nodes['chat-stop'].hidden,true);release({ok:true,json:async()=>result('calculated')});await tick();assert.equal(applied.length,appliedBefore);assert.equal(failed.length,1);
responder=respond(result());await send('новый запрос');assert.equal(requests.at(-1).session_id,undefined);
responder=async()=>({ok:false,status:404,json:async()=>({detail:'Диалог истёк'})});await send('уточнение');assert.equal(failed.length,2);
responder=respond(result());await send('повтор');assert.equal(requests.at(-1).session_id,undefined);
responder=respond({...result('calculated'),validation:{missing:[],errors:[],warnings:[],defaults_requiring_consent:{}}});await send('полный ответ');assert.equal(nodes['chat-defaults'].hidden,true);assert.equal(failed.length,2);
const apply=dashboard.apply; dashboard.apply=()=>{throw new TypeError('test render error')};await send('ошибка интерфейса');assert.match(failed.at(-1), /Backend ответил, но интерфейс/);dashboard.apply=apply;
responder=async()=>{throw new TypeError('Failed to fetch')};await send('ошибка сети');assert.match(failed.at(-1), /Не удалось связаться/);
enabled=false;nodes['chat-retry'].click();await tick();assert.equal(nodes['chat-send'].disabled,true);assert.equal(nodes['chat-use-form'].disabled,true);assert.equal(begin,finish);
assert.ok(JSON.parse(storage.get('well-ai-chat-v7')).records.length>1);
const persisted=JSON.parse(storage.get('well-ai-chat-v7'));
for(const id of ids)nodes[id]=new Element();
enabled=true;
let restoreApplied=[];
initAssistant({...dashboard,form:new Element('form'),apply:r=>restoreApplied.push(r)});await tick();
assert.equal(nodes['chat-messages'].children.length,persisted.records.length);
assert.equal(restoreApplied.length,1);
responder=respond(result('ok',persisted.sid));await send('после обновления');
assert.equal(requests.at(-1).session_id,persisted.sid);
nodes['chat-new'].click();assert.equal(JSON.parse(storage.get('well-ai-chat-v7')).records.length,1);
assert.equal(JSON.parse(storage.get('well-ai-chat-v7')).sid,null);

console.log('Assistant UI controller: sessions, explicit form import, divergence, consent, stop/stale reply, expired session, disabled LLM — OK');

// File preview is read-only; confirm sends only the server-side token.
const fileBytes=new TextEncoder().encode('p_res: 200 atm');
nodes['chat-files'].files=[{name:'well.txt',size:fileBytes.length,arrayBuffer:async()=>fileBytes.buffer}];
const beforeImport=restoreApplied.length;
responder=respond({session_id:'file-session',preview_id:'preview-token',can_import:true,parameters:{reservoir:{p_res:200}},provenance:{'reservoir.p_res':{value:200,unit:'атм',source:'well.txt · строка 1'}},validation:{missing:['well.p_wh'],errors:[],warnings:[],defaults_requiring_consent:{}}});
nodes['chat-file-preview'].click();await tick();
assert.equal(restoreApplied.length,beforeImport);assert.equal(requests.at(-1).files[0].name,'well.txt');
const confirm=nodes['chat-file-result'].children.find(x=>x.tagName==='button');assert.ok(confirm);
responder=respond(result('needs_input','file-session'));confirm.click();await tick();
assert.deepEqual(requests.at(-1),{session_id:'file-session',preview_id:'preview-token'});
assert.equal(restoreApplied.length,beforeImport+1);
responder=respond({...result('ok','file-session'),mode:'conversation',sources:[{id:'A',title:'ТЗ',location:'стр. 7',url:'/knowledge/specification.pdf#page=7',text:'Выдержка',cited:true}]});
await send('Что такое IPR?');
assert.ok(nodes['chat-messages'].children.at(-1).children.some(x=>x.className.includes('chat-sources')));
console.log('Stage 5 UI: file preview, explicit confirmation, source rendering — OK');
