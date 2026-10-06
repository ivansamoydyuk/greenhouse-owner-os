import test from 'node:test';
import assert from 'node:assert/strict';
import {inventoryUI,inventoryLabels} from '../../src/greenhouse_owner_os/web/inventory.js';
test('Inventory preserves independent physical units and escapes original names',()=>{
 const ui=inventoryUI({getData:()=>({inventory:{stock:[{product_name:'<img src=x>',location_name:'TEST',quantity:'100.000000000001',uncosted_quantity:'2',base_unit:'kg'}],products:[],attention:[],events:[],applied:[],movements:[]},work_areas:[]})});
 const html=ui.render();assert.ok(html.includes('&lt;img src=x&gt;'));assert.ok(html.includes('100.000000000001 kg'));assert.ok(html.includes('Стоимость неизвестна'));
});
test('Plans have an explicit separate owner action',()=>{assert.equal(inventoryLabels.plan,'План внесения');assert.equal(inventoryLabels.application,'Внесение');});
