import {serviceUrl} from './service-url.js';

const labels = {
  'reservoir.p_res':'Пластовое давление', 'reservoir.pi':'Продуктивность', 'reservoir.pb':'Давление насыщения IPR',
  'well.p_wh':'Устьевое давление', 'well.h_res':'Глубина пласта', 'well.tubing.h_mes':'Глубина спуска НКТ',
  'well.casing.d':'Диаметр ЭК', 'well.tubing.d':'Диаметр НКТ', 'well.geo_grad':'Геотермический градиент',
  'well.roughness':'Шероховатость', 'well.inclinometry.MD':'MD', 'well.inclinometry.TVD':'TVD',
  'pvt.wct':'Обводнённость', 'pvt.rp':'Газовый фактор', 'pvt.t_res':'Пластовая температура',
  'pvt.gamma_oil':'Относительная плотность нефти', 'pvt.gamma_gas':'Относительная плотность газа', 'pvt.gamma_wat':'Относительная плотность воды',
  'sampling.q_max':'Максимальный дебит', 'sampling.ipr_points':'Точки IPR', 'sampling.vlp_points':'Точки VLP'
};
const tools = {get_current_parameters:'Получение параметров',update_parameters:'Обновление параметров',validate_parameters:'Валидация',calculate_well:'Расчёт IPR → VLP → Nodal',run_scenarios:'Сравнение режимов',analyze_measurement:'Сопоставление с замером',export_report:'Подготовка отчёта',unknown:'Неизвестный инструмент'};
const display = path => labels[path] || path;
// API returns a mapping of field paths to proposed default values, not an array.
export function defaultPaths(validation) {
  const defaults = validation?.defaults_requiring_consent;
  return Array.isArray(defaults) ? defaults : defaults && typeof defaults === 'object' ? Object.keys(defaults) : [];
}
const el = (tag, cls, text) => { const node = document.createElement(tag); if (cls) node.className=cls; if (text !== undefined) node.textContent=text; return node; };
const issueText = e => `${display(e.path)}${e.row ? ` · строка ${e.row}` : ''}: ${e.message}`;

export function initAssistant(dashboard) {
  const $ = id => document.getElementById(id);
  const log=$('chat-messages'), input=$('chat-input'), form=$('chat-form'), send=$('chat-send'), stop=$('chat-stop'), newChat=$('chat-new'), useForm=$('chat-use-form'), defaults=$('chat-defaults'), continueChat=$('chat-continue'), retry=$('chat-retry'), status=$('chat-status'), context=$('chat-context');
  let sid=null, active=null, configured=null, diverged=false, canDefault=false, hasChatData=false, statusVersion=0;
  const storageKey='well-ai-chat-v7';
  let records=[], lastResponse=null, restored=false, storageFailed=false;
  const fileInput=$('chat-files'), filePreview=$('chat-file-preview'), fileResult=$('chat-file-result');
  let previewData=null, uploading=false;
  function clearPreview(){previewData=null;if(fileResult){fileResult.replaceChildren();fileResult.hidden=true;}if(fileInput)fileInput.value='';}
  function save(){
    // Full response needed only for the last calculation and newest scenario. Keep diagnostics for older messages.
    const latestScenario=records.findLastIndex(r=>r.response?.scenario);
    const latestAnalysis=records.findLastIndex(r=>r.response?.analysis);
    const latestReport=records.findLastIndex(r=>r.response?.report);
    const compact=records.map((r,i)=>{if(!r.response)return r;const {calculation,parameters,scenario,analysis,report,...diagnostics}=r.response;return {...r,response:{...diagnostics,...(i===latestScenario?{scenario}:{}),...(i===latestAnalysis?{analysis}:{}),...(i===latestReport?{report}:{})}};});
    for(const limit of [100,50,20,5]){try{localStorage.setItem(storageKey,JSON.stringify({sid,diverged,canDefault,hasChatData,records:compact.slice(-limit),lastResponse}));storageFailed=false;return;}catch{}}
    storageFailed=true;
  }
  function remember(role,text,tone='',response=null){ const block=add(role,text,tone); records.push({role,text,tone,response}); records=records.slice(-100); save(); return block; }
  const greeting='Здравствуйте! Я Well AI. Могу объяснить узловой анализ, IPR, VLP, PVT и параметры скважины, помочь с ошибками или рассчитать рабочую точку. Задайте вопрос либо передайте данные текстом, JSON или кнопкой «Рассчитать по форме».';
  function scroll() { log.scrollTop=log.scrollHeight; }
  function add(role, text, tone='') {
    const block=el('div',`chat-message ${role} ${tone}`);
    const clean=String(text).trim().replace(/\n[ \t]*\n(?:[ \t]*\n)+/g,'\n\n');
    const bubble=el('div','chat-bubble');
    if(role==='user' && clean.length>250 && (clean.startsWith('{')||clean.startsWith('Новый набор данных из формы.'))){
      const section=el('details','chat-json');section.append(el('summary','',clean.startsWith('{')?'Данные скважины (JSON)':'Рассчитать по форме · исходные данные'),el('pre','',clean));bubble.append(section);
    } else if(role==='assistant') {
      // A small safe subset of Markdown, no HTML injection or arbitrary markup.
      for(const piece of clean.split(/(\*\*[^*\n]+\*\*)/g)){if(piece)bubble.append(el(piece.startsWith('**')&&piece.endsWith('**')?'strong':'span','',piece.startsWith('**')&&piece.endsWith('**')?piece.slice(2,-2):piece));}
    } else bubble.textContent=clean;
    block.append(el('span','chat-author',role==='user'?'Вы':'Well AI'),bubble);
    log.append(block); scroll(); return block;
  }
  function details(block, title, items, tone='', open=false) {
    if (!items.length) return;
    const section=el('details',`chat-details ${tone}`); section.open=open;
    section.append(el('summary','',`${title} · ${items.length}`));
    const list=el('ul'); for(const item of items) list.append(el('li','',item));
    section.append(list); block.append(section);
  }
  function update() {
    const busy=!!active;
    send.disabled=busy || configured!==true;
    input.disabled=busy || configured!==true;
    useForm.disabled=busy || configured!==true;
    defaults.hidden=!canDefault || diverged; defaults.disabled=busy || configured!==true;
    continueChat.hidden=!diverged; continueChat.disabled=busy || configured!==true;
    if(fileInput)fileInput.disabled=busy; if(filePreview)filePreview.disabled=busy;
    stop.hidden=!busy || uploading; newChat.disabled=busy; retry.disabled=busy;
    context.textContent=diverged?'Форма изменена вручную. Вопросы доступны; для расчёта передайте форму или продолжите по сохранённым данным чата.':hasChatData?'Данные диалога синхронизируются с формой. Можно задать вопрос или уточнить параметры.':'Чат начинает с пустых данных. Передайте параметры формы или опишите скважину.';
    if(storageFailed) context.textContent+=' Хранилище браузера недоступно: история может не сохраниться.';
  }
  function state(text, tone) { status.textContent=text; status.dataset.state=tone; }
  async function checkStatus() {
    const version=++statusVersion;
    state('Проверяем настройки…','');
    try {
      const response=await fetch(serviceUrl('backend','/api/assistant/status'),{cache:'no-store',signal:AbortSignal.timeout(5000)});
      if(!response.ok) throw Error();
      const info=await response.json(); if(version!==statusVersion) return;
      configured=info.configured===true;
      state(configured?'LLM настроена (связь ещё не проверена)':info.status==='invalid_config'?'Ошибка настроек LLM':'LLM не настроена',configured?'ready':'error');
    } catch { if(version!==statusVersion) return; configured=false; state('Backend недоступен','error'); }
    update();
  }
  function renderDiagnostics(block, response) {
    const v=response.validation || {};
    if(response.report && dashboard.report){const button=el("button","secondary","Скачать HTML-отчёт");button.type="button";button.addEventListener("click",()=>dashboard.report(response.report));block.append(button);}
    if(response.analysis && dashboard.analysis){const button=el("button","secondary","Показать подбор PI");button.type="button";button.addEventListener("click",()=>dashboard.analysis(response.analysis,true));block.append(button);}
    if(response.mode!=='conversation' && response.mode!=='scenario') {
    details(block,'Ошибки',(v.errors||[]).map(issueText),'danger',true);
    details(block,'Недостающие параметры',(v.missing||[]).map(display),'danger',true);
    details(block,'Нужно согласие на значения по умолчанию',defaultPaths(v).map(path => `${display(path)}${Array.isArray(v.defaults_requiring_consent) ? '' : `: ${v.defaults_requiring_consent[path]}`}`),'warning');
    details(block,'Предупреждения',(v.warnings||[]).map(issueText),'warning',true);
    details(block,'Источники параметров',Object.entries(response.provenance||{}).map(([path,p])=>`${display(path)}: ${Array.isArray(p.value)?p.value.join(', '):p.value} ${p.unit||''} ← ${p.source}`));
    }
    if(Array.isArray(response.sources) && response.sources.length){
      const sources=el('details','chat-details chat-sources');sources.open=true;
      sources.append(el('summary','','Найденные источники базы знаний'));
      for(const item of response.sources){
        if(!/^\/knowledge\/(?:methodology\.docx|specification\.pdf(?:#page=\d+)?)$/.test(item.url))continue;
        const section=el('div','chat-source');const link=el('a','',`[${item.id}] ${item.title} · ${item.location}`);
        link.href=item.url;link.target='_blank';link.rel='noopener noreferrer';
        const excerpt=el('details','chat-excerpt');excerpt.append(el('summary','','Показать выдержку'),el('p','',item.text));
        section.append(link,el('small','',item.cited?'Указан в ответе модели':'Найден поиском'),excerpt);sources.append(section);
      }block.append(sources);
    }
    details(block,'Этапы расчёта' ,(response.calculation_stages||[]).map(t=>`${t.stage} — ${t.ok?'успешно':'ошибка'} · ${t.duration_ms} мс`),'',true);
    details(block,'Ход выполнения запроса',(response.tool_trace||[]).map(t=>`${tools[t.tool]||t.tool} — ${t.ok?'успешно':'не выполнено'} · ${t.duration_ms} мс${t.trigger==='backend'?' · серверный запуск':''}`));
    if(Number.isFinite(response.duration_ms)) block.append(el('span','chat-author',`Время обработки: ${(response.duration_ms/1000).toFixed(2)} с`));
    scroll();
  }
  function resetSession() { clearPreview(); sid=null; diverged=false; canDefault=false; hasChatData=false; lastResponse=null; save(); }
  async function submit(message, visible=message, fresh=false, fileConfirm=null) {
    if(active || (!fileConfirm && configured!==true) || !message.trim()) return;
    if(message.length>50000) { remember('assistant','Сообщение превышает лимит 50 000 символов.','error'); return; }
    if(!fileConfirm && previewData)clearPreview();
    if(fresh) resetSession(); // explicit full form starts a new state; no stale pending errors or parameters
    const controller=new AbortController();
    active=controller; dashboard.begin(); update();
    remember('user',visible);
    const waiting=add('assistant','Обрабатываю сообщение…','pending');
    let phase='network';
    try {
      const response=await fetch(serviceUrl('backend',fileConfirm?'/api/assistant/files/confirm':'/api/assistant/chat'),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(fileConfirm||{message,...(sid?{session_id:sid}:{}),...(diverged?{conversation_only:true}:{})}),signal:controller.signal});
      phase='response';
      const data=await response.json().catch(()=>null);
      if(active!==controller) return;
      if(!response.ok) {
        if(response.status===404 || response.status===504) resetSession();
        throw Error(typeof data?.detail==='string'?data.detail:`Ошибка backend (HTTP ${response.status}).`);
      }
      if(typeof data?.session_id!=='string' || typeof data.answer!=='string' || typeof data.status!=='string' || !data.parameters || !data.validation) throw Error('Ответ ассистента не соответствует контракту.');
      sid=data.session_id;
      if(fileConfirm)clearPreview();
      phase='render';
      if(data.analysis) dashboard.analysis?.(data.analysis,true);
      if(data.mode==='scenario') { dashboard.scenario?.(data.scenario); }
      else if(data.mode!=='conversation') {
        dashboard.apply(data); lastResponse=data;
        hasChatData=Object.keys(data.parameters).length>0;
        diverged=false; canDefault=data.status==='needs_input' && defaultPaths(data.validation).length>0;
      }
      waiting.remove();
      const failed=data.status.endsWith('error') || data.status==='step_limit' || data.status==='invalid_input' || data.status==='form_conflict';
      const block=remember('assistant',data.answer,failed?'error':'',data); renderDiagnostics(block,data);
      state(data.status==='llm_error'?'Ошибка связи с настроенной моделью':'Backend ответил',data.status==='llm_error'?'error':'ready');
    } catch(error) {
      if(active!==controller) return;
      waiting.remove();
      console.error('Well AI:', phase, error);
      const note=phase==='network'?'Не удалось связаться с backend. Проверьте соединение.':phase==='render'?`Backend ответил, но интерфейс не смог обработать данные: ${error.message}`:error.message;
      remember('assistant',note,'error'); dashboard.fail(note); state('Запрос завершился ошибкой','error');
    } finally {
      if(active===controller) { active=null; dashboard.finish(); update(); if(!input.disabled) input.focus(); }
    }
  }
  if(filePreview && fileInput && fileResult){
    fileInput.addEventListener('change',()=>{previewData=null;fileResult.replaceChildren();fileResult.hidden=true;});
    filePreview.addEventListener('click',async()=>{
      if(active)return;
      const files=Array.from(fileInput.files||[]);
      fileResult.replaceChildren();fileResult.hidden=false;previewData=null;
      if(!files.length || files.length>3 || files.reduce((n,f)=>n+f.size,0)>5*1024*1024){fileResult.append(el('p','field-diagnostic','Выберите от одного до трёх файлов, суммарно не больше 5 МБ.'));return;}
      const controller=new AbortController();active=controller;uploading=true;update();
      fileResult.append(el('p','','Извлекаем параметры без запуска расчёта…'));
      try{
        const encoded=[];
        for(const f of files){const bytes=new Uint8Array(await f.arrayBuffer());let binary='';for(let i=0;i<bytes.length;i+=16384)binary+=String.fromCharCode(...bytes.subarray(i,i+16384));encoded.push({name:f.name,content:btoa(binary)});}
        const r=await fetch(serviceUrl('backend','/api/assistant/files/preview'),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({files:encoded,...(sid?{session_id:sid}:{})}),signal:controller.signal});
        const data=await r.json();if(active!==controller)return;
        if(!r.ok){if(r.status===404){sid=null;save();}throw Error(typeof data.detail==='string'?data.detail:'Ошибка чтения файлов.');}
        sid=data.session_id;previewData=data;save();fileResult.replaceChildren();
        fileResult.append(el('strong','',`Найдено параметров: ${Object.keys(data.provenance).length}`));
        for(const [path,p] of Object.entries(data.provenance))fileResult.append(el('div','file-field',`${display(path)}: ${Array.isArray(p.value)?p.value.join(', '):p.value} ${p.unit} — ${p.source}`));
        for(const e of data.errors||[])fileResult.append(el('p','field-diagnostic',e));
        for(const e of data.validation?.errors||[])fileResult.append(el('p','field-diagnostic',issueText(e)));
        if(data.validation?.missing?.length)fileResult.append(el('p','assistant-note','Не хватает: '+data.validation.missing.map(display).join(', ')));
        if(Object.keys(data.validation?.defaults_requiring_consent||{}).length)fileResult.append(el('p','assistant-note','Настройки по умолчанию требуют согласия после импорта.'));
        for(const e of data.validation?.warnings||[])fileResult.append(el('p','field-diagnostic warning',issueText(e)));
        if(!data.can_import)fileResult.append(el('p','assistant-note','Импорт заблокирован. Исправьте файл или внесите значения вручную. Если файл — скан, потребуется текстовый слой.'));
        else {
          fileResult.append(el('p','assistant-note','Проверьте значения и источники. Подтверждение заменит текущий набор параметров, а при полном вводе запустит расчёт.'));
          const btn=el('button','secondary','Подтвердить новый набор');btn.type='button';btn.addEventListener('click',()=>{if(previewData===data&&!active)submit('Подтверждаю данные из файлов','Подтверждён новый набор параметров из файлов',false,{session_id:data.session_id,preview_id:data.preview_id});});fileResult.append(btn);
        }
      }catch(e){if(active===controller){fileResult.replaceChildren(el('p','field-diagnostic',e.message));}}
      finally{if(active===controller){active=null;uploading=false;update();}}
    });
  }
  form.addEventListener('submit',e=>{ e.preventDefault(); const text=input.value.trim(); if(text && !send.disabled){ input.value=''; submit(text); } });
  input.addEventListener('keydown',e=>{ if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing){e.preventDefault(); form.requestSubmit();} });
  useForm.addEventListener('click',()=>{
    try { const data=dashboard.requestBody(true); submit(JSON.stringify(data), 'Новый набор данных из формы. Рассчитай скважину.\n'+JSON.stringify(data,null,2),true); }
    catch(error){ remember('assistant',`Не удалось передать форму: ${error.message}`,'error'); }
  });
  defaults.addEventListener('click',()=>submit('Использовать значения по умолчанию'));
  continueChat.addEventListener('click',()=>{diverged=false; save(); update(); context.textContent='Следующий ответ восстановит данные чата в форме. Можете отправить уточнение.'; input.focus();});
  newChat.addEventListener('click',()=>{resetSession(); records=[]; log.replaceChildren(); input.value=''; remember('assistant',greeting); update();});
  stop.addEventListener('click',()=>{ if(!active)return; const old=active; active=null; old.abort(); log.querySelector('.pending')?.remove(); resetSession(); remember('assistant','Ожидание остановлено. Сервер мог продолжить вычисление; его ответ не будет применён. Начат новый диалог.','error'); dashboard.fail('Ожидание ответа ассистента остановлено.'); dashboard.finish(); update(); });
  retry.addEventListener('click',checkStatus);
  function markDiverged(){ if(hasChatData){diverged=true; save(); update();} }
  dashboard.form.addEventListener('input',markDiverged);
  dashboard.form.addEventListener('reset',markDiverged);
  try {
    const saved=JSON.parse(localStorage.getItem(storageKey)||'null');
    if(saved && Array.isArray(saved.records) && saved.records.length){
      sid=typeof saved.sid==='string'?saved.sid:null; diverged=!!saved.diverged; canDefault=!!saved.canDefault; hasChatData=!!saved.hasChatData;
      records=saved.records.slice(-100); lastResponse=saved.lastResponse||null;
      for(const r of records){const b=add(r.role,r.text,r.tone); if(r.response) renderDiagnostics(b,r.response);}
      if(lastResponse && !diverged){dashboard.apply(lastResponse); restored=true;}
      const previous=records.findLast(r=>r.response?.scenario); if(previous) dashboard.scenario?.(previous.response.scenario);
    }
  } catch(error) { console.warn('Не удалось восстановить чат',error); }
  if(!records.length) remember('assistant',greeting);
  update(); checkStatus(); return restored;
}
