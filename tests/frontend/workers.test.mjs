import test from 'node:test';import assert from 'node:assert/strict';
import {taskStatuses,languageNames} from '../../src/greenhouse_owner_os/web/tasks.js';
test('worker report and owner completion have different visible states',()=>{assert.notEqual(taskStatuses.reported,taskStatuses.completed);assert.equal(Object.keys(languageNames).length,4);assert.ok(taskStatuses.reopened);});

test('fa storage compatibility is displayed as Dari in owner worker controls',()=>{assert.match(languageNames.fa,/Дари/);assert.match(languageNames.fa,/دری/);assert.doesNotMatch(languageNames.fa,/فارسی/);});
