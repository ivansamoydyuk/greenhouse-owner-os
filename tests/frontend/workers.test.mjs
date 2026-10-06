import test from 'node:test';import assert from 'node:assert/strict';
import {taskStatuses,languageNames} from '../../src/greenhouse_owner_os/web/tasks.js';
test('worker report and owner completion have different visible states',()=>{assert.notEqual(taskStatuses.reported,taskStatuses.completed);assert.equal(Object.keys(languageNames).length,4);assert.ok(taskStatuses.reopened);});
