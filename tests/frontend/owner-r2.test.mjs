import test from 'node:test';
import assert from 'node:assert/strict';
import {receiptTotal} from '../../src/greenhouse_owner_os/web/owner-r2.js';
test('depot preview totals use exact line arithmetic, not binary floats',()=>{
 assert.equal(receiptTotal([{net_weight_kg:'100',unit_price:'20'},{net_weight_kg:'50',unit_price:'15'},{net_weight_kg:'25',unit_price:'10'}]),'3000.00');
 assert.equal(receiptTotal([{net_weight_kg:'0.125',unit_price:'0.20'}]),'0.03');
});
