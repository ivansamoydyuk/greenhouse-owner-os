// Explicit, synthetic UI scenarios. These are not operational records or AI output.
export const SCENARIO_TEXT = 'Нужно переделать полив на Озвади на следующей неделе';
export const SEASON = '1–9 октября 2026';
export const harvestRows = [
  {id:'yesiloz',name:'Yeşilöz',kg:4800,revenue:144000,countedKg:4800,bunches:140,quality:[3200,1100,300,200]},
  {id:'imamli',name:'İmamlı',kg:3600,revenue:115200,countedKg:3600,bunches:100,quality:[2400,800,300,100]},
  {id:'ozvadi',name:'Özvadi',kg:5600,revenue:179200,countedKg:2400,bunches:60,quality:[3600,1400,400,200]}
];
export const harvestDays = [{label:'1 окт',kg:1800},{label:'2',kg:2400},{label:'3',kg:1600},{label:'4',kg:0},{label:'5',kg:2800},{label:'6',kg:2000},{label:'7',kg:1400},{label:'8',kg:800},{label:'9',kg:1200}];
export function initialState(){return {
 version:1,revision:0,online:true,failNext:false,staleNext:false,
 proposals:[
  {id:'clarify',type:'payable',title:'TEST Мастерская',meaning:'Ремонт креплений',amount:null,currency:'TRY',place:'İmamlı',date:'9 октября',source:'TEST Мастерская отремонтировала крепления в İmamlı. Сумму уточню. Не оплачено.',status:'pending',revision:1},
  {id:'depot-review',type:'harvest',title:'TEST Банановое депо',meaning:'Поставка бананов',amount:37200,kg:1200,currency:'TRY',place:'Özvadi',date:'9 октября',source:'Синтетический чек № DEMO-09: 1 200 кг × 31 TRY = 37 200 TRY. Alacak. Не оплачено.',image:'assets/receipt.svg',status:'pending',revision:1},
  {id:'stock-review',type:'stock',title:'KNO₃',meaning:'Физический приход',quantity:100,unit:'кг',place:'İmamlı',date:'9 октября',source:'Привезли 100 кг KNO3 в İmamlı. Стоимость неизвестна. Не оплачено.',status:'pending',revision:1},
  {id:'unknown-review',type:'unknown',title:'Документ не полностью читается',meaning:'Неполный источник',source:'TEST: общий итог на второй странице не виден.',image:'assets/incomplete.svg',status:'pending',revision:1}
 ],
 receivables:[{id:'depot',name:'TEST Банановое депо',amount:176000,meaning:'Неоплаченные поставки',date:'До 14 октября'},{id:'buyer',name:'TEST Покупатель',amount:10400,meaning:'Поставка бананов',date:'Срок не указан'}],
 payables:[{id:'repair',name:'TEST Ремонт',amount:7000,meaning:'Ремонт насоса',date:'Срок не указан'},{id:'supplier',name:'TEST Поставщик материалов для обслуживания тепличных конструкций',amount:35600,meaning:'Общий расход',date:'До 16 октября'}],
 tasks:[{id:'irrigation',title:'Проверить линии капельного полива',place:'Özvadi',when:'Сегодня',date:'2026-10-09',worker:null,status:'todo',source:'Синтетическая личная задача',category:'Полив'}, {id:'film',title:'Проверить запас плёнки перед ремонтом северной стороны теплицы',place:'İmamlı',when:'12 октября',date:'2026-10-12',worker:null,status:'todo',source:'Синтетическая личная задача',category:'Ремонт'}, {id:'undated',title:'Выбрать дату обслуживания',place:null,when:null,date:null,worker:null,status:'todo',source:'Синтетическая личная задача'}],
 workerTasks:[{id:'filter-report',title:'Проверить запасной фильтр',place:'İmamlı',when:'Сегодня',worker:'muhammed',status:'problem',report:'Нужен запасной фильтр',source:'Синтетический отчёт работника'},{id:'leaves',title:'Осмотреть листья бананов',place:'Özvadi',when:'Сегодня',worker:'inayat',status:'assigned',report:null,source:'Синтетическое поручение'},{id:'door',title:'Проверить боковую дверь',place:'Özvadi',when:'12 октября',worker:'inayat',status:'assigned',report:null,source:'Синтетическое поручение'}],
 workerTask:{id:'worker-task',title:'Проверить фильтр. Не включать насос.',place:'İmamlı',date:'2026-10-10',when:'10 октября',worker:null,status:'draft',translation:'فیلتر را بررسی کنید. پمپ را روشن نکنید.',translationReviewed:false},
 workers:[{id:'muhammed',name:'TEST Мухаммед',initials:'М',place:'İmamlı',language:'Дари',tasks:1,issue:'Нужен запасной фильтр'},{id:'umar',name:'TEST Умар',initials:'У',place:'Yeşilöz',language:'Дари',tasks:0,issue:null},{id:'inayat',name:'TEST Инаят',initials:'И',place:'Özvadi',language:'Дари',tasks:2,issue:null}],
 stock:[{id:'kno3',name:'KNO₃',category:'Удобрения',quantity:180,unit:'кг',place:'İmamlı',cost:null,movements:[{id:'s1',label:'Физический приход',quantity:200,date:'6 октября'},{id:'s2',label:'Подтверждённое внесение',quantity:-20,date:'8 октября'}]},{id:'calcium',name:'Кальциевая селитра',category:'Удобрения',quantity:75,unit:'кг',place:'Yeşilöz',cost:null,movements:[]},{id:'film-stock',name:'Плёнка для бокового проветривания, усиленная',category:'Материалы',quantity:null,unit:'м²',place:'Özvadi',cost:null,movements:[]}],
 sources:[], draft:{mode:'text',text:'',comment:'',file:null,scenario:null}, filters:{review:'all',tasks:'mine',finance:'receivable',inventory:'all'}, positions:{}, activity:[], largeAmount:false
};}
