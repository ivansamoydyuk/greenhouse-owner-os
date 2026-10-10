import test from 'node:test';
import assert from 'node:assert/strict';
import {harvestSelection,harvestQuery,harvestRoute,harvestNumber,chartHeight,monthlyHarvestView} from '../../prototype/owner-r3/harvest-connected.js';
test('October belongs to the new half-open harvest season',()=>{
  assert.equal(harvestSelection('harvest','2026-10-01').season_start_year,2026);
  assert.equal(harvestSelection('harvest','2026-09-30').season_start_year,2025);
});
test('route retains greenhouse period metric currency and month on return',()=>{
  const x={greenhouse_id:'greenhouse-1',season_start_year:2026,metric:'revenue',currency:'EUR',month:'2027-01',allocation:'all'};
  assert.deepEqual(harvestSelection(harvestRoute(x),'2026-10-10'),x);
  assert.equal(harvestQuery(x),'season_start_year=2026&metric=revenue&currency=EUR&allocation=all&greenhouse_id=greenhouse-1');
});
test('unknown is not formatted as a zero',()=>{
  assert.equal(harvestNumber(null),'Неизвестно');
  assert.equal(harvestNumber('0'),'0');
  assert.equal(harvestNumber('123456789012345678.12'),'123 456 789 012 345 678,12');
});
test('chart scale never rounds authoritative amounts through floating arithmetic',()=>{
  assert.equal(chartHeight('500000000000000000.000','1000000000000000000.000'),50);
  assert.equal(chartHeight(null,'100'),0);
});
const f={greenhouse_id:null,season_start_year:2026,metric:'branches',currency:'TRY',month:null};
const empty={value:null,exact_value:null,estimated_value:null,unallocated_value:null,coverage:{known:0,missing:0,total:0,complete:false},events:[]};
test('empty month and season render no-record state without fake zero',()=>{
  const html=monthlyHarvestView({greenhouses:[],months:[{...empty,month:'2026-10'}],total:empty},f);
  assert.match(html,/Нет записей/);assert.match(html,/Неизвестно|отсутствие известного значения/);
  assert.doesNotMatch(html,/harvest-total[^>]*>0</);
});
test('partial coverage and estimates remain visible with original event links',()=>{
  const summary={value:'7',exact_value:'7',estimated_value:'0',unallocated_value:'0',coverage:{known:1,missing:1,total:2,complete:false},events:[{kind:'report',id:'a',event_date:'2026-10-01',value:'7',name:'İmamlı'}]};
  const html=monthlyHarvestView({greenhouses:[],months:[{...summary,month:'2026-10'}],total:summary},{...f,month:'2026-10'});
  assert.match(html,/Полных записей 1 из 2/);assert.match(html,/#report\/a/);assert.match(html,/ADET депо — отдельное/);
});
test('server source labels and greenhouse labels are escaped',()=>{
  const summary={...empty,value:'1',events:[{kind:'report',id:'a',name:'<img onerror=alert(1)>',event_date:'2026-10-10',value:'1'}]};
  const html=monthlyHarvestView({greenhouses:[{id:'x',name:'<script>x</script>'}],months:[{...summary,month:'2026-10'}],total:summary},f);
  assert.doesNotMatch(html,/<script>|<img/);assert.match(html,/&lt;img/);
});
