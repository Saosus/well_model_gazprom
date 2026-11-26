import assert from 'node:assert/strict';
import {reportText,csvText} from '../frontend/analysis.js';
const row={name:'<script>alert(1)</script>',parameters:{reservoir:{p_res:200,pi:1,pb:150}},calculation:{ipr:{q_liq:[100,0],p_wf:[1,200]},vlp:{q_liq:[0,100],p_wf:[50,150]},operating_point:{q_liq:50,p_wf:100}},measured:{q_liq:40,p_wf:90},comparison:{flow:{delta:10,percent:25},pressure:{delta:10,percent:11.111111},threshold:10,flagged:true,notes:[]},issues:[]};
const html=reportText([row]);assert.ok(html.includes('<svg'));assert.ok(html.includes('<polyline'));assert.ok(html.includes('Рабочая точка: 50,00 м³/сут, 100,00 атм'));assert.ok(!html.includes('<script>'));assert.ok(html.includes('&lt;script&gt;'));assert.ok(html.includes('Коэффициент продуктивности'));
const md=reportText([row],'md');assert.ok(md.includes('## \\<script\\>'));assert.ok(md.includes('Координаты кривых'));assert.ok(md.includes('<svg'));
const csv=csvText([{...row,name:'=1+1'}]);assert.ok(csv.includes(`"'=1+1"`));assert.ok(csv.includes('"25"'));
const bad=reportText([{...row,calculation:null,comparison:null,error:'Нет пересечения'}]);assert.ok(bad.includes('Нет пересечения'));assert.ok(!bad.includes('<svg'));assert.ok(!bad.includes('Рабочая точка: 50'));
console.log('Stage 6 exports: Markdown, HTML graph, failed calculation, CSV formula protection — OK');
