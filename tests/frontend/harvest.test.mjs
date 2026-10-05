import test from 'node:test';
import assert from 'node:assert/strict';
import {lineAmount} from '../../src/greenhouse_owner_os/web/harvest.js';
test('exact three-decimal kg × two-decimal price rounds half up',()=>{assert.equal(lineAmount('1234.50','25.75'),'31788.38');assert.equal(lineAmount('0.001','5'),'0.01');assert.equal(lineAmount('9007199254740993','0.01'),'90071992547409.93');});
test('invalid quantities are not coerced',()=>{for(const kg of ['1e3','-1','1.0001','unknown'])assert.throws(()=>lineAmount(kg,'30'));});

import {readFileSync} from 'node:fs';
test('normal owner manual evidence uses business provenance',()=>{const source=readFileSync(new URL('../../src/greenhouse_owner_os/web/harvest.js',import.meta.url),'utf8');assert.ok(source.includes("source:'owner_mobile:manual'"));assert.ok(!source.includes('test:owner-harvest-manual'));});
