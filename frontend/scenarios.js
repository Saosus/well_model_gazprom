import {serviceUrl} from './service-url.js';
import {drawChart} from './chart.js';
const fields={ 'well.p_wh':['Устьевое давление','атм'],'reservoir.p_res':['Пластовое давление','атм'],'reservoir.pi':['Продуктивность','м³/(сут·атм)'],'reservoir.pb':['Давление насыщения','атм'],'pvt.wct':['Обводнённость','%'],'pvt.rp':['Газовый фактор','м³/т'],'well.tubing.d':['Диаметр НКТ','м'],'well.casing.d':['Диаметр ЭК','м'],'well.tubing.h_mes':['Глубина спуска НКТ','м'],'pvt.t_res':['Пластовая температура','°C'],'well.roughness':['Шероховатость','доля']};
const fmt=x=>Number.isFinite(x)?new Intl.NumberFormat('ru-RU',{maximumSignificantDigits:6}).format(x):'—';
export function initScenarios({requestBody,applyParameters,validate}) {
  const $=id=>document.getElementById(id), form=$('scenario-form'), param=$('scenario-param'), status=$('scenario-status'), run=$('scenario-run'), graph=$('scenario-chart'), apply=$('scenario-apply');
  let selected=null, baseline=null, busy=false;
  for(const [key,[label]] of Object.entries(fields)){const o=document.createElement('option');o.value=key;o.textContent=label;param.append(o);}
  function units(){document.querySelectorAll('.scenario-unit').forEach(e=>e.textContent=`(${fields[param.value][1]})`);}
  param.addEventListener('change',units); units();
  function select(row,title){selected=row; $('scenario-chart-title').textContent=title; apply.disabled=!row?.calculation||busy; if(row?.calculation)drawChart(graph,row.calculation,row===baseline?null:baseline?.calculation);else graph.replaceChildren();}
  function render(data){if(!data)return; $('scenario-results').hidden=false; const body=$('scenario-rows');body.replaceChildren();
    baseline=data.baseline; const all=[data.baseline,...data.rows];
    all.forEach((row,i)=>{const tr=body.insertRow();const best=i>0&&i-1===data.best_index; if(best)tr.className='best'; const p=row.calculation?.operating_point; const title=i===0?'Базовый':best?`Вариант ${i} · лучший`:`Вариант ${i}`;
      for(const value of [title,`${fmt(row.value)} ${data.unit}`,fmt(p?.q_liq),fmt(p?.p_wf),fmt(row.delta?.q_liq),fmt(row.delta?.p_wf),row.error||('Рассчитан'+(row.warnings?.length?' · '+row.warnings.map(x=>x.message).join('; '):''))])tr.insertCell().textContent=value;
      const b=document.createElement('button');b.type='button';b.className='secondary';b.textContent='Показать';b.disabled=!p;b.addEventListener('click',()=>select(row,`${title} · ${data.label}: ${fmt(row.value)} ${data.unit}`));tr.insertCell().append(b);
    });
    const best=data.best_index===null?null:data.rows[data.best_index];
    status.textContent=`${data.label} · успешных вариантов ${data.rows.filter(r=>r.calculation).length} из ${data.rows.length}. `+(best?`Лучший среди проверенных: ${fmt(best.value)} ${data.unit}. `:'')+'База сохранена отдельно; оптимум между точками сетки не гарантируется.';
    const row=best||data.rows.find(r=>r.calculation)||data.baseline; select(row,`${data.label}: ${fmt(row.value)} ${data.unit}`);
  }
  form.addEventListener('submit',async e=>{e.preventDefault();if(busy)return; const lo=$('scenario-min').valueAsNumber, hi=$('scenario-max').valueAsNumber,n=$('scenario-count').valueAsNumber;
    if(!Number.isFinite(lo)||!Number.isFinite(hi)||!Number.isInteger(n)||n<1||n>8||hi<lo||(n===1&&lo!==hi)||(n>1&&lo===hi)){status.textContent='Проверьте границы и количество вариантов.';return;}
    const values=Array.from({length:n},(_,i)=>n===1?lo:i===n-1?hi:lo+(hi-lo)*i/(n-1));
    busy=true;run.disabled=true;apply.disabled=true;status.textContent='Выполняются расчёты базового и изменённых режимов…';$('scenario-results').hidden=true;selected=null;
    try {const response=await fetch(serviceUrl('backend','/api/scenarios/run'),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({baseline:requestBody(),parameter:param.value,values,objective:$('scenario-objective').value})});const data=await response.json();if(!response.ok)throw Error(typeof data.detail==='string'?data.detail:data.detail?.message||'Проверьте исходные параметры и границы.');render(data);}catch(err){status.textContent=err.message;}finally{busy=false;run.disabled=false;apply.disabled=!selected?.calculation;}
  });
  apply.addEventListener('click',()=>{if(!selected?.calculation||busy)return;if(document.getElementById('parameters').inert){status.textContent='Дождитесь завершения запроса в чате.';return;}applyParameters(selected.parameters);document.getElementById('parameters').dispatchEvent(new Event('input'));status.textContent='Выбранный режим перенесён в форму. Нажмите «Рассчитать» для обновления основного графика.';});
  new ResizeObserver(()=>{if(selected?.calculation)drawChart(graph,selected.calculation,selected===baseline?null:baseline?.calculation);}).observe(graph.parentElement);
  return {render};
}
