import test from 'node:test';
import assert from 'node:assert/strict';
import {harvestUI} from '../../src/greenhouse_owner_os/web/harvest.js';

const coverage=(reports,known)=>({report_count:reports,known_report_count:known,unknown_report_count:reports-known,complete:reports>0&&reports===known});
function render(count,cov,analytics=[]){
 const data={work_areas:[],references:[],harvest:{settlements:[],batches:[],reports:[],attention:[],analytics,
  total_cut_count:count,cut_count_coverage:cov,cut_counts:[{name:'TEST <greenhouse>',count,count_coverage:cov}],
  unknown_cut_count:null,unknown_cut_count_coverage:coverage(1,0)}};
 return harvestUI({getData:()=>data,getCurrency:()=> 'TRY'}).render();
}

test('field harvest distinguishes no reports, UNKNOWN, partial known sum and complete known sum',()=>{
 const empty=render(null,coverage(0,0));
 const unknown=render(null,coverage(1,0));
 const partial=render(7,coverage(2,1));
 const complete=render(7,coverage(1,1));
 assert.match(empty,/Нет полевых отчётов в выбранном периоде/);
 assert.match(unknown,/Количество неизвестно: 1 из 1 отчётов без количества/);
 assert.match(partial,/Известно: 7 веток.*Неполные данные: количество неизвестно в 1 из 2 отчётов/);
 assert.match(complete,/Известно: 7 веток.*Отчёты с количеством: 1 из 1/);
 for(const html of [empty,unknown,partial,complete]){
  assert.match(html,/Неизвестное место среза/);
  assert.doesNotMatch(html,/Всего в периоде аналитики|0 деревьев|0 веток|null веток/);
  assert.match(html,/TEST &lt;greenhouse&gt;/);
  assert.match(html,/Не складывается с количеством по чекам/);
 }
});

test('missing coverage is never inferred from a legacy scalar or the capped report list',()=>{
 const html=render(7,undefined);
 assert.match(html,/Полнота полевых данных неизвестна/);
 assert.doesNotMatch(html,/Отчёты с количеством:|Всего в периоде аналитики/);
});

test('an available partial depot average is labelled with its corresponding known basis',()=>{
 const bucket={greenhouse_id:'test',name:'TEST greenhouse',currency:'TRY',total_depot_kg:'100',sales_amount:'3000.00',receivable_outstanding:'0.00',average_price_kg:'30',average_weight_kg:'10',confirmed_kg:'70',confirmed_count:7,count_coverage_complete:false,quality_mix:{FIRST:'100'}};
 const html=render(7,coverage(2,1),[bucket]);
 assert.match(html,/Средний вес · известная часть/);
 assert.match(html,/Неполное покрытие количества. Полный средний вес неизвестен/);
 assert.match(html,/Подтверждённая база среднего веса: 70 кг \/ 7 деревьев/);
 assert.match(html,/10,00 кг/);
 assert.doesNotMatch(html,/14,29/);
 const complete=render(7,coverage(1,1),[{...bucket,confirmed_kg:'100',confirmed_count:10,count_coverage_complete:true}]);
 assert.match(complete,/Количество подтверждено для всего веса/);
 assert.doesNotMatch(complete,/Средний вес · известная часть|Полный средний вес неизвестен/);
});
