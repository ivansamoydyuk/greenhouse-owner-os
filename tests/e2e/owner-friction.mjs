// Real authenticated Chromium -> Capture R2 -> review -> explicit confirmation.
// Small, frozen TRAINING-only browser sample. No holdout access, mocked parser,
// acoustic claim, physical-iPhone acceptance, production, or external dispatch.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import {execFileSync} from 'node:child_process';
import {createHash} from 'node:crypto';
import {chromium} from 'playwright';
import {navigateOwner, waitOwnerRoute} from './navigation.mjs';
import {metricDefinitions, summarizeCase, summarizeFamilies, installClickAudit, createMeasuredActions} from './friction-metrics.mjs';

const base = process.env.PREVIEW_URL || 'http://127.0.0.1:4173';
assert.ok(['127.0.0.1', 'localhost', '[::1]'].includes(new URL(base).hostname), 'Disposable local preview only');
assert.ok(process.env.PREVIEW_SECRET, 'Run through the guarded disposable preview runner');
const artifacts = process.env.E2E_ARTIFACTS || 'artifacts';
await fs.mkdir(artifacts, {recursive: true});
// Read only the released training file. Never enumerate or open holdout files.
const trainingBytes = await fs.readFile(new URL('../benchmarks/owner-language/train.jsonl', import.meta.url));
const training = new Map(trainingBytes.toString().trim().split('\n').map(line => {const row = JSON.parse(line); return [row.id, row];}));
const sample = [
  ['GOLD-0403', 'task', 'PERSONAL_TASK'],
  ['GOLD-0461', 'payable', 'PAYABLE'],
  ['GOLD-0473', 'payment', 'PAYMENT'],
  ['GOLD-0479', 'direct_expense', 'DIRECT_EXPENSE'],
  ['GOLD-0373', 'stock_receipt', 'STOCK_RECEIPT'],
  ['GOLD-0087', 'stock_application', 'STOCK_APPLICATION'],
  ['GOLD-0160', 'depot', 'DEPOT_RECEIPT'],
  ['GOLD-0168', 'harvest_worker_evidence', 'UNRESOLVED'],
  ['GOLD-0033', 'unsupported_transfer', 'UNRESOLVED'],
  ['GOLD-0387', 'worker_ambiguous_identity', 'WORKER_TASK', true],
].map(([id, family, kind, expectedBlock = false]) => {
  const row = training.get(id);
  assert.ok(row, `Frozen browser sample must remain in TRAINING: ${id}`);
  assert.equal(row.split, 'train');
  assert.deepEqual(row.expected.map(action => action.kind), [kind], `Training contract changed: ${id}`);
  assert.equal(Object.hasOwn(row, 'source_seed_ids'), false, 'Released TRAINING excludes private conversation provenance');
  // Whitelist benchmark-native metadata rather than copying private evidence
  // into browser logs or CI artifacts. Detailed source audit stays owner-only.
  return {id: row.id, family: row.family, text: row.text, split: row.split,
    measured_family: family, kind, expectedBlock};
});
// A separate authored TEST example already reviewed in preview.py and the
// iPhone suite. Never relabel this as a historical GOLD/train sample or claim
// that the runtime can generate its supplied Dari rendering.
const authoredWorker = {
  original: 'TEST Muhammed, проверьте трубу в İmamlı 2026-10-10.',
  rendering: 'TEST Muhammed، لوله را در گلخانهٔ İmamlı در تاریخ ۲۰۲۶-۱۰-۱۰ بررسی کنید.',
  provenance: 'src/greenhouse_owner_os/preview.py: ONE_AUTHORED_TEST_EXAMPLE',
};

const browser = await chromium.launch({headless: true, args: ['--no-sandbox'],
  ...(process.env.CHROME_PATH ? {executablePath: process.env.CHROME_PATH} : {})});
const context = await browser.newContext({viewport: {width: 390, height: 844}, locale: 'ru-RU',
  timezoneId: 'Europe/Istanbul', isMobile: true, hasTouch: true});
const page = await context.newPage(), errors = [], consoleLogs = [], records = [], sources = [], confirmed = [], fixtureSetup = [], cleanup = [];
const confirmationReads = [];
await installClickAudit(page);
page.on('pageerror', error => errors.push(error.message));
page.on('console', message => {consoleLogs.push({type: message.type(), text: message.text()}); if (message.type() === 'error') errors.push(message.text());});
// Capture even a committed success whose UI later fails: teardown must not
// lose a real posting merely because its following assertion failed.
page.on('response', response => {
  if (response.request().method() !== 'POST' || !/\/owner\/confirmations\/[^/]+\/confirm$/.test(new URL(response.url()).pathname)) return;
  confirmationReads.push((async () => {
    const result = await response.json();
    const record = records.find(row => row.proposal_id === result.id);
    if (response.ok() && result.effect?.id && record && !confirmed.some(row => row.effect.id === result.effect.id)) {
      confirmed.push({case_id: record.case_id, kind: record.expected_kind, proposal_id: record.proposal_id, effect: result.effect});
    }
  })());
});
const sheet = () => page.locator('#sheet');
let prefix, owner, before, refs, fatal, finalState, assignmentRecord;
let originalsVerified = null, reversalHistoryVerified = null, economicRestored = null;
const assignmentCleanup = [];
const field = name => sheet().locator(`[name="${name}"]`);
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const readPosting = id => JSON.parse(execFileSync('python', ['scripts/owner_friction_posting_evidence.py', id], {encoding: 'utf8'}));
const postingLines = (snapshot, reverse = false) => snapshot.entries.map(row => ({account: row.ledger_account_id, amount: row.amount, side: reverse ? (row.side === 'debit' ? 'credit' : 'debit') : row.side})).sort((a, b) => JSON.stringify(a).localeCompare(JSON.stringify(b)));
const equalValue = (a, b) => a == null || b == null ? a === b : String(a) === String(b)
  || (/^\d+(?:\.\d+)?$/.test(String(a)) && /^\d+(?:\.\d+)?$/.test(String(b)) && decimal(a) === decimal(b));
function decimal(value) {return String(value).replace(/(\.\d*?)0+$/, '$1').replace(/\.$/, '');}
function cents(value) {
  const match = /^(-?)(\d+)(?:\.(\d{1,2}))?$/.exec(String(value));
  assert.ok(match, `Exact money string required: ${value}`);
  return (match[1] ? -1n : 1n) * (BigInt(match[2]) * 100n + BigInt((match[3] || '').padEnd(2, '0')));
}
async function api(path, body) {
  const response = body === undefined ? await context.request.get(base + path)
    : await context.request.post(base + path, {data: body, headers: {'Idempotency-Key': crypto.randomUUID()}});
  assert.ok(response.ok(), `${path}: ${response.status()} ${await response.text()}`);
  return response.json();
}
async function poll(read, label, timeout = 30000) {
  const end = Date.now() + timeout;
  while (Date.now() < end) {const value = await read(); if (value) return value; await page.waitForTimeout(80);}
  assert.fail(label);
}
async function snapshot() {
  const [home, inventory, harvest, financeReconciliation] = await Promise.all([
    api(owner + '/home'), api(prefix + '/inventory'), api(prefix + '/harvest'),
    api(`/v1/finance/reconciliation?entity_id=${refs.entity_id}`),
  ]);
  assert.equal(inventory.invariants.ok, true, 'Inventory invariants');
  assert.equal(financeReconciliation.ok, true, 'Finance reconciliation');
  // Reversal keeps immutable audit history and may create a zero stock row.
  // Compare economic positions and original authoritative objects, not row count.
  const stock = inventory.stock.filter(row => ['quantity', 'known_cost', 'uncosted_quantity'].some(key => decimal(row[key]) !== '0'))
    .map(row => Object.fromEntries(['product_id', 'location_id', 'quantity', 'known_cost', 'uncosted_quantity'].map(key => [key, key.endsWith('_id') ? row[key] : decimal(row[key])])))
    .sort((a, b) => (a.product_id + a.location_id).localeCompare(b.product_id + b.location_id));
  return {home, inventory, harvest, financeReconciliation, economic: {
    balances: home.finance.balances, business_balances: home.finance.business_balances,
    advances: home.finance.advances, opening_obligations: home.finance.obligations.filter(row => row.opening),
    stock, applied: inventory.applied, harvest_reports: harvest.reports, harvest_batches: harvest.batches,
    posted_settlements: harvest.settlements.filter(row => row.status === 'posted'),
    total_cut_count: harvest.total_cut_count, unknown_cut_count: harvest.unknown_cut_count,
    cut_counts: harvest.cut_counts, season: home.season, task_count: home.task_count,
  }};
}
async function original(sourceId) {
  const response = await context.request.get(base + owner + `/captures/${sourceId}/original`);
  assert.ok(response.ok()); return hash(await response.body());
}
async function diagnostics(label, error) {
  const stem = `${artifacts}/owner-friction-${label}`;
  await page.screenshot({path: stem + '.png', fullPage: true}).catch(() => {});
  await fs.writeFile(stem + '.html', await page.content()).catch(() => {});
  await fs.writeFile(stem + '.json', JSON.stringify({error: String(error), console: consoleLogs, runtime_and_errors: errors,
    url: page.url(), body: await page.locator('body').innerText().catch(() => '')}, null, 2)).catch(() => {});
}
function sourceFact(key, control, value, text) {return {key, control, value, origin: 'captured_source', evidence: text};}
function clarification(key, control, value, evidence) {return {key, control, value, origin: 'new_owner_fact', evidence};}
function get(object, path) {return path.split('.').reduce((value, key) => value?.[key], object);}

async function runCase(test) {
  // Navigation to the starting screen is setup, before the capture counter.
  if (await sheet().isVisible()) await sheet().getByRole('button', {name: 'Закрыть', exact: true}).click();
  await navigateOwner(page, 'home');
  const caseBefore = await snapshot();
  const record = {case_id: test.id, family: test.measured_family, training_family: test.family,
    split: test.split, evidence_scope: test.split === 'train' ? 'Privacy-minimized TEST benchmark phrase' : 'Explicit authored TEST preview example',
    captured_text: test.text, expected_kind: test.kind,
    expected_outcome: test.expectedBlock ? 'blocked_ambiguous_identity' : test.kind === 'UNRESOLVED' ? 'safely_unresolved' : 'completed',
    status: 'running', operations: [], mode: 'text', browser: 'Chromium mobile emulation'};
  records.push(record);
  const measured = createMeasuredActions(page, record);
  try {
    await measured.click(page.locator('[data-r2-mode=text]'), 'Open Text capture');
    await measured.transcript(field('text'), test.text);
    const saved = page.waitForResponse(response => response.request().method() === 'POST' && new URL(response.url()).pathname === owner + '/captures');
    await measured.click(sheet().getByRole('button', {name: 'Сохранить источник', exact: true}), 'Save source');
    const savedResponse = await saved;
    assert.equal(savedResponse.status(), 201);
    const savedSource = await savedResponse.json();
    // This is the measurement boundary: the real capture POST has succeeded.
    measured.setPhase('review'); record.source_id = savedSource.id;
    sources.push({id: savedSource.id, text: test.text, hash: await original(savedSource.id)});
    await sheet().waitFor({state: 'hidden'});
    const source = await poll(async () => {
      const result = await api(owner + `/captures/${savedSource.id}`);
      assert.notEqual(result.job.state, 'FAILED', `Preparation failed: ${test.id}`);
      return result.proposals.length ? result : null;
    }, `No prepared proposal for ${test.id}`);
    assert.equal(source.text, test.text, 'Exact source preservation');
    assert.equal(source.proposals.length, 1, 'One expected slot; do not silently ignore extra effects');
    record.proposal_id = source.proposals[0].id;
    let proposal = await api(owner + `/confirmations/${record.proposal_id}/preview`);
    record.initial_kind = proposal.action_type; record.initial_missing = proposal.revision.missing;
    record.initial_prepared_fields = proposal.revision.payload;
    assert.equal(proposal.action_type, test.kind, `${test.id}: wrong route cannot be repaired by silently changing its business meaning`);
    // Capture/preparation alone must never post a business effect.
    assert.ok(['READY', 'NEEDS_INPUT'].includes(proposal.state), `Unexpected prepared state ${proposal.state}`);
    assert.deepEqual((await snapshot()).economic, caseBefore.economic, 'Saving/preparing a source cannot change domain positions');
    await measured.click(page.locator('#nav [data-page=review]'), 'Open confirmation queue');
    await waitOwnerRoute(page, 'review');
    await measured.click(sheet().locator(`[data-proposal="${record.proposal_id}"] [data-r2-open]`), 'Open source proposal');
    await sheet().locator('#r2-confirm').waitFor();

    if (test.kind === 'UNRESOLVED' || test.expectedBlock) {
      assert.equal(await sheet().locator('#r2-confirm').isDisabled(), true, 'Unsupported source must remain non-confirmable');
      assert.ok(proposal.revision.missing.length);
      if (test.expectedBlock) {
        assert.equal(proposal.revision.payload.worker_id, null, 'Unknown/ambiguous Asif must never become the unrelated TEST worker');
        assert.equal(proposal.revision.payload.rendered_text, null, 'No invented translation');
        assert.ok(proposal.revision.missing.some(reason => reason.includes('Работник')));
      }
      record.status = test.expectedBlock ? 'blocked' : 'safely_unresolved'; record.blockers = proposal.revision.missing;
      record.final_prepared_fields = proposal.revision.payload;
      // Rejection and closing are teardown, not fabricated taps-to-confirm.
      measured.setPhase('cleanup');
      await measured.click(sheet().locator('#r2-reject'), 'Reject unsupported TEST source');
      await poll(async () => (await api(owner + `/confirmations/${record.proposal_id}/preview`)).state === 'REJECTED', 'Rejected source must persist');
      await page.waitForFunction(() => !document.querySelector('#sheet').open || document.querySelector('#sheet-title')?.textContent === 'Неподтверждённые');
      if (await sheet().isVisible()) {
        await sheet().getByRole('heading', {name: 'Неподтверждённые', exact: true}).waitFor();
        await measured.click(sheet().getByRole('button', {name: 'Закрыть', exact: true}), 'Close TEST queue');
      } else await waitOwnerRoute(page, 'review', {dialog: false});
      assert.deepEqual((await snapshot()).economic, caseBefore.economic, 'Rejecting unsupported source creates no business effect');
      record.rejection_verified = true;
      return;
    }

    const capturedDay = new Intl.DateTimeFormat('en-CA', {timeZone: 'Europe/Istanbul', year: 'numeric', month: '2-digit', day: '2-digit'}).format(new Date(source.captured_at));
    const tomorrow = new Date(capturedDay + 'T12:00:00Z'); tomorrow.setUTCDate(tomorrow.getUTCDate() + 1);
    const f = (key, control, value) => sourceFact(key, control, value, test.text);
    let desired = [];
    if (test.kind === 'PERSONAL_TASK') desired = [f('planned_date', 'planned_date', tomorrow.toISOString().slice(0, 10)), f('greenhouse_id', 'greenhouse_id', refs.ozvadi.id)];
    if (test.kind === 'WORKER_TASK') {
      assert.equal(proposal.revision.payload.worker_id, refs.worker.id, 'Explicit known TEST worker must be prepared without identity re-entry');
      assert.equal(proposal.revision.payload.language, 'fa');
      assert.equal(proposal.revision.payload.rendered_text, null, 'Generic runtime must not fabricate an authored translation');
      assert.ok(proposal.revision.missing.some(reason => /перевод/i.test(reason)), 'Missing reviewed translation must block confirmation');
      assert.equal(await sheet().locator('#r2-confirm').isDisabled(), true);
      record.confirmation_checkpoint = 'unassigned_worker_draft';
      record.initial_confirmation_blocker = 'missing_reviewed_Dari_rendering';
      record.translation_source = authoredWorker.provenance;
      desired = [f('planned_date', 'planned_date', '2026-10-10'), f('greenhouse_id', 'greenhouse_id', refs.imamli.id),
        {key: 'rendered_text', control: 'rendered_text', value: authoredWorker.rendering, origin: 'manual_translation',
          evidence: 'Manually entered exact pre-authored TEST rendering from ' + authoredWorker.provenance + '; no translation service or newly inferred business fact.'}];
    }
    if (test.kind === 'PAYABLE') desired = [f('amount', 'amount', '5000'), f('currency', 'currency', 'TRY'), f('counterparty_id', 'counterparty_id', refs.serkan.id)];
    if (test.kind === 'PAYMENT') desired = [f('amount', 'amount', '2500'), f('currency', 'currency', 'TRY'), f('counterparty_id', 'counterparty_id', refs.serkan.id),
      clarification('money_account_id', 'money_account_id', refs.cash.id, 'Explicit synthetic clarification: this TEST payment used Cash TR; the captured phrase did not name an account.'),
      clarification('obligation_id', 'obligation_id', refs.opening.obligation_id, 'Explicit synthetic clarification: settle the known 2600 TRY opening payable to Serkan. The phrase says old debt but does not identify its obligation ID.')];
    if (test.kind === 'DIRECT_EXPENSE') desired = [f('amount', 'amount', '500'), f('currency', 'currency', 'TRY'), f('money_account_id', 'money_account_id', refs.cash.id)];
    if (test.kind.startsWith('STOCK_')) {
      assert.equal(proposal.revision.payload.lines.length, 1, 'Do not discard an unknown/missing product line');
      desired = [f('event_date', 'event_date', capturedDay), f('lines.0.product_id', 'product_0', refs.product.id),
        f('lines.0.quantity', 'quantity_0', test.kind === 'STOCK_RECEIPT' ? '100' : '20'),
        f('lines.0.location_id', 'location_0', refs.store.id), f('lines.0.unit', 'unit_0', 'kg')];
      if (test.kind === 'STOCK_APPLICATION') desired.push(f('lines.0.greenhouse_id', 'greenhouse_0', refs.imamli.id));
      assert.equal(proposal.revision.payload.lines[0].unit_cost, null, 'Unknown cost must never become zero');
    }
    if (test.kind === 'DEPOT_RECEIPT') {
      assert.equal(proposal.revision.payload.lines.length, 1, 'One exact receipt line');
      assert.equal(proposal.revision.payload.lines[0].original_quality_name, 'Grand');
      assert.equal(decimal(proposal.revision.payload.alacak_value), '2000');
      assert.deepEqual(proposal.revision.payload.batch_ids, [], 'No guessed link between depot and worker evidence');
      desired = [f('depot_id', 'depot_id', refs.depot.id), f('event_date', 'event_date', '2026-10-09'),
        f('lines.0.net_weight_kg', 'kg_0', '100'), f('lines.0.unit_price', 'price_0', '20')];
    }
    const edits = desired.filter(item => !equalValue(get(proposal.revision.payload, item.key), item.value));
    if (edits.length || test.kind === 'DEPOT_RECEIPT' || (test.kind === 'PAYMENT' && proposal.revision.payload.payment_purpose !== 'debt')) {
      await measured.click(sheet().locator('#r2-edit'), 'Edit prepared facts');
      await sheet().locator('#r2-edit-form').waitFor();
      // Party/account selection may change available debt options. The intended
      // exact TEST debt is selected last, in the same actual editor session.
      for (const item of edits) await measured.field(field(item.control), item.value, item);
      if (test.kind === 'DEPOT_RECEIPT') {
        // This mapping is scoped synthetic review knowledge, not a global rule
        // and not a source fact. Do not persist learned mappings for later tests.
        const quality = field('quality_0');
        if (await quality.inputValue() !== 'HIGHEST') await measured.field(quality, 'HIGHEST', clarification('qualities.0', 'quality_0', 'HIGHEST', 'TEST-only owner decision for this one Görgün Grand line.'));
        await measured.acknowledge(field('quality_reviewed'), {key: 'quality_reviewed', newFact: true,
          evidence: 'Explicit TEST-only scoped owner interpretation: this Görgün Grand line is HIGHEST. Captured source contains the original label, not its normalization.'});
        await measured.acknowledge(field('arithmetic_reviewed'), {key: 'arithmetic_reviewed',
          evidence: 'Review the already captured arithmetic 100 kg × 20 TRY = 2000 TRY. Alacak is not payment.'});
        assert.equal(await field('remember_quality_mapping').isChecked(), false);
      }
      await measured.click(sheet().getByRole('button', {name: 'Сохранить исправления', exact: true}), 'Save owner corrections');
      await sheet().locator('#r2-confirm').waitFor();
      proposal = await api(owner + `/confirmations/${record.proposal_id}/preview`);
    }
    record.final_prepared_fields = proposal.revision.payload;
    for (const item of desired) assert.ok(equalValue(get(proposal.revision.payload, item.key), item.value), `Reviewed field mismatch ${test.id}:${item.key}`);
    if (test.kind === 'PAYABLE') assert.equal(proposal.revision.payload.due_date, null, 'A due date absent from source is optional, not fabricated');
    if (test.kind.startsWith('STOCK_')) assert.equal(proposal.revision.payload.lines[0].unit_cost, null);
    if (proposal.revision.missing.length || await sheet().locator('#r2-confirm').isDisabled()) {
      record.status = 'blocked'; record.blockers = proposal.revision.missing;
      // A blocked representative is visible in the report and fails the gate;
      // it is never counted as zero-input successful confirmation.
      assert.fail(`Supported sample still blocked: ${test.id}: ${proposal.revision.missing.join('; ')}`);
    }
    const responsePromise = page.waitForResponse(response => response.request().method() === 'POST' && new URL(response.url()).pathname === owner + `/confirmations/${record.proposal_id}/confirm`);
    const expensePreviewPromise = test.kind === 'DIRECT_EXPENSE' ? page.waitForResponse(response => response.request().method() === 'GET' && new URL(response.url()).pathname === owner + `/confirmations/${record.proposal_id}/preview`) : null;
    await measured.click(sheet().locator('#r2-confirm'), 'Confirm reviewed TEST effect');
    const response = await responsePromise, result = await response.json();
    if (response.ok() && result.effect && !confirmed.some(row => row.effect.id === result.effect.id)) confirmed.push({case_id: test.id, kind: test.kind, proposal_id: record.proposal_id, effect: result.effect});
    const expectedState = test.kind === 'WORKER_TASK' ? 'CONFIRMED_NONPOSTING' : 'POSTED';
    assert.equal(response.status(), 200, JSON.stringify(result)); assert.equal(result.state, expectedState);
    assert.ok(result.effect?.id, 'Authoritative effect recorded for reversal');
    if (expensePreviewPromise) {
      // DirectExpense keeps a fresh, read-only receipt open after Confirm so
      // current posting/reversal truth is not replaced by a historical toast.
      const previewResponse = await expensePreviewPromise, current = await previewResponse.json();
      assert.equal(previewResponse.status(), 200, JSON.stringify(current));
      assert.equal(current.id, record.proposal_id); assert.equal(current.source.id, record.source_id);
      assert.equal(current.action_type, 'DIRECT_EXPENSE'); assert.equal(current.state, 'POSTED');
      assert.equal(current.current_effect.id, result.effect.id); assert.equal(current.current_effect.status, 'posted');
      const receipt = sheet().locator(`[data-proposal="${record.proposal_id}"]`);
      await receipt.getByText('Расход подтверждён. Деньги списаны с выбранного счёта; долг и аванс не созданы.', {exact: true}).waitFor();
      assert.equal(await receipt.locator(':scope > small').first().innerText(), 'Расход · Подтверждено');
      assert.equal(await sheet().locator('#r2-confirm').isDisabled(), true);
      assert.equal(await sheet().locator('#r2-edit,#r2-defer,#r2-reject,#r2-reopen').count(), 0);
      record.current_read_only_receipt = {proposal_id: current.id, source_id: current.source.id,
        effect_id: current.current_effect.id, state: current.state, effect_status: current.current_effect.status};
      await page.screenshot({path: `${artifacts}/owner-friction-${test.id}-posted-receipt.png`, fullPage: true});
      // Receipt dismissal is a real logged click after successful Confirm,
      // outside the measured capture-to-confirm interval.
      measured.setPhase('post_confirmation');
      await measured.click(sheet().getByRole('button', {name: 'Закрыть', exact: true}), 'Dismiss confirmed read-only expense receipt');
    }
    await sheet().waitFor({state: 'hidden'});
    record.status = 'completed'; record.authoritative_effect_id = result.effect.id;
    assert.equal((await api(owner + `/confirmations/${record.proposal_id}/preview`)).state, expectedState);
    if (test.kind === 'WORKER_TASK') {
      const task = await api(prefix + `/tasks/${result.effect.id}`);
      assert.equal(result.effect.not_sent, true); assert.equal(task.status, 'draft'); assert.deepEqual(task.assignments, []);
      assert.equal(task.original_instruction, authoredWorker.original);
      assert.deepEqual(task.prepared_assignment, {worker_id: refs.worker.id, language: 'fa', rendered_text: authoredWorker.rendering});
      record.worker_task_audit = {task_id: task.id, state: task.status, original_instruction: task.original_instruction,
        prepared_assignment: task.prepared_assignment, assignments_count: 0, actual_dispatch: false};
      assert.equal(record.operations.filter(operation => operation.kind === 'field_edit' && operation.fact_origin === 'manual_translation').length, 1,
        'The missing Dari rendering was repaired by one actual manual field entry');
      record.missing_rendering_repaired_by_manual_entry = true;
    }
    const afterHome = await api(owner + '/home'), beforeBalances = caseBefore.home.finance.balances.TRY, afterBalances = afterHome.finance.balances.TRY;
    const cashDelta = test.kind === 'PAYMENT' ? -250000n : test.kind === 'DIRECT_EXPENSE' ? -50000n : 0n;
    const payableDelta = test.kind === 'PAYABLE' ? 500000n : test.kind === 'PAYMENT' ? -250000n : 0n;
    assert.equal(cents(afterBalances.cash) - cents(beforeBalances.cash), cashDelta, 'Only the confirmed route changes cash');
    assert.equal(cents(afterBalances.payable) - cents(beforeBalances.payable), payableDelta, 'Only the confirmed route changes payable');
    assert.equal(cents(afterBalances.receivable) - cents(beforeBalances.receivable), test.kind === 'DEPOT_RECEIPT' ? 200000n : 0n, 'Alacak creates depot receivable, never payment');
    assert.deepEqual(afterHome.finance.advances, caseBefore.home.finance.advances, 'No invented advance');
    if (test.kind === 'STOCK_RECEIPT' || test.kind === 'STOCK_APPLICATION') {
      const inventory = await api(prefix + '/inventory');
      const physical = inventory.stock.find(row => row.product_id === refs.product.id && row.location_id === refs.store.id);
      assert.equal(decimal(physical.quantity), test.kind === 'STOCK_RECEIPT' ? '100' : '80');
      assert.equal(decimal(physical.known_cost), '0'); assert.equal(decimal(physical.uncosted_quantity), decimal(physical.quantity));
      assert.equal(inventory.invariants.ok, true);
    }
    if (test.kind === 'DEPOT_RECEIPT') {
      const harvest = await api(prefix + '/harvest'), settlement = harvest.settlements.find(row => row.id === result.effect.id);
      assert.equal(settlement.status, 'posted'); assert.equal(decimal(settlement.remaining_amount), '2000');
      assert.equal(settlement.payment_id, null); assert.deepEqual(settlement.batch_ids, []);
      assert.equal(settlement.lines[0].original_quality_name, 'Grand');
      assert.equal(decimal(settlement.reconciliation.depot_weight_kg), '100');
    }
  } catch (error) {
    if (record.status !== 'blocked') record.status = 'failed';
    record.error = String(error); await diagnostics(test.id, error); throw error;
  }
}

async function measurePreparedAssignment(config) {
  if (!config.r2_fixtures) {
    assignmentRecord = {status: 'not_available', reason: 'ONE_AUTHORED_TEST_EXAMPLE fixture disabled in this lifecycle',
      TAPS_TO_ASSIGN: null, TAPS_TO_CONFIRM: null, MANUAL_OWNER_INPUT_COUNT: null,
      CLARIFICATION_COUNT: null, MANUAL_TRANSLATION_INPUT_COUNT: null, actual_dispatch: false};
    return;
  }
  const fixture = config.worker_assignment_fixture;
  assert.equal(fixture.translation_scope, 'ONE_AUTHORED_TEST_EXAMPLE'); assert.equal(fixture.real_dispatch, false);
  assert.equal(fixture.worker_id, refs.worker.id);
  const task = await api(prefix + `/tasks/${fixture.task_id}`);
  assert.equal(task.status, 'draft'); assert.deepEqual(task.assignments, []);
  assert.equal(task.original_instruction, authoredWorker.original);
  assert.deepEqual(task.prepared_assignment, {worker_id: refs.worker.id, language: 'fa', rendered_text: authoredWorker.rendering});
  sources.push({id: fixture.source_id, text: authoredWorker.original, hash: await original(fixture.source_id)});
  if (await sheet().isVisible()) await sheet().getByRole('button', {name: 'Закрыть', exact: true}).click();
  await navigateOwner(page, 'tasks');
  const record = {case_id: 'authored-prepared-Dari-assignment', family: 'prepared_assignment', split: 'authored_TEST_fixture',
    status: 'running', operations: [], source_id: fixture.source_id, task_id: task.id,
    confirmation_checkpoint: 'internal_worker_assignment', actual_dispatch: false,
    measurement_window: 'Existing Tasks overview -> task -> reviewed prepared assignment -> internal assignment persisted',
    upstream_preparation: 'Excluded: existing authored source, reviewed rendering and worker draft were fixture setup. This is not a zero-input full source-to-assignment claim.'};
  assignmentRecord = record;
  const measured = createMeasuredActions(page, record); measured.setPhase('review');
  try {
    await measured.click(page.locator(`[data-task="${task.id}"]`).first(), 'Open prepared TEST worker draft');
    await sheet().locator('#task-assign').waitFor();
    await measured.click(sheet().locator('#task-assign'), 'Review prepared TEST assignment');
    const form = sheet().locator('#task-assign-form'); await form.waitFor();
    assert.equal(await form.locator('#assign-worker').inputValue(), refs.worker.id);
    assert.equal(await form.locator('#assign-prepared-text').innerText(), authoredWorker.rendering);
    assert.equal(await form.locator('.task-assignment-copy blockquote').innerText(), authoredWorker.original);
    assert.equal(await form.locator('#assign-manual').evaluate(node => node.open), false);
    const button = form.locator('button.primary'); assert.equal(await button.isEnabled(), true);
    assignmentCleanup.push({task_id: task.id, original: authoredWorker.original, source_id: fixture.source_id});
    const saved = page.waitForResponse(response => response.request().method() === 'POST' && new URL(response.url()).pathname === prefix + `/tasks/${task.id}/assignments`);
    await measured.click(button, 'Assign reviewed TEST Dari rendering internally');
    const response = await saved; assert.equal(response.status(), 201, await response.text());
    const assignment = await response.json();
    await sheet().getByRole('heading', {name: 'Кому', exact: true}).waitFor();
    const assigned = await api(prefix + `/tasks/${task.id}`);
    assert.equal(assigned.original_instruction, authoredWorker.original); assert.equal(assigned.status, 'assigned');
    assert.equal(assigned.assignments.length, 1);
    const rendering = assigned.assignments[0];
    assert.equal(rendering.id, assignment.id); assert.equal(rendering.worker_id, refs.worker.id);
    assert.equal(rendering.language, 'fa'); assert.equal(rendering.rendered_text, authoredWorker.rendering);
    assert.equal(rendering.translation_source, 'owner_reviewed_translation'); assert.equal(rendering.rendering_reviewed, true);
    assert.ok(assigned.actions.some(action => action.kind === 'assign' && action.assignment_id === assignment.id), 'Actual assignment audit action');
    record.status = 'completed';
    record.audit = {original_instruction: assigned.original_instruction, assignment: rendering,
      action: assigned.actions.find(action => action.kind === 'assign' && action.assignment_id === assignment.id)};
  } catch (error) {record.status = 'failed'; record.error = String(error); await diagnostics(record.case_id, error); throw error;}
  finally {
    const summary = summarizeCase(record);
    assignmentRecord = {...summary, TAPS_TO_ASSIGN: summary.TAPS_TO_CONFIRM, TAPS_TO_CONFIRM: null,
      INITIAL_CAPTURE_TAPS: null, INITIAL_CAPTURE_ENTRY_COUNT: null};
  }
}

try {
  const login = await context.request.post(base + '/preview/login', {data: {secret: process.env.PREVIEW_SECRET}});
  assert.ok(login.ok());
  const config = await api('/preview/config');
  assert.equal(config.environment, 'TEST_PREVIEW'); assert.equal(config.actual_operations, false);
  prefix = `/v1/entities/${config.entity_id}`; owner = prefix + '/owner';
  let home = await api(owner + '/home');
  const inventory = await api(prefix + '/inventory');
  const findRef = (kind, name) => home.finance.references.find(row => row.kind === kind && row.original_name === name);
  const product = inventory.products.filter(row => row.active && row.aliases.includes('KNO3'));
  if (!product.length) {
    const created = await api(prefix + '/inventory/products', {name: 'TEST KNO3', aliases: ['KNO3'], base_unit: 'kg', currency: 'TRY', source: 'test:owner-friction-training'});
    product.push(created); fixtureSetup.push({kind: 'TEST product reference', id: created.id, name: created.name});
  }
  if (!findRef('counterparty', 'Görgün')) {
    const created = await api(prefix + '/counterparties', {name: 'Görgün', role: 'depot', source: 'test:owner-friction-training'});
    fixtureSetup.push({kind: 'TEST depot reference', id: created.id, name: 'Görgün'}); home = await api(owner + '/home');
  }
  assert.equal(product.length, 1, 'One unambiguous exact KNO3 alias in the disposable preview');
  assert.equal(product[0].name, 'TEST KNO3');
  let store = inventory.locations.find(row => row.name === 'TEST A');
  if (!store) {store = await api(prefix + '/inventory/locations', {name: 'TEST A', source: 'test:owner-friction-training'}); fixtureSetup.push({kind: 'TEST storage reference', id: store.id, name: store.name});}
  refs = {entity_id: config.entity_id, product: product[0], store,
    serkan: findRef('counterparty', 'Серкан Ремонт насосов'), cash: findRef('money_account', 'Cash TR'), depot: findRef('counterparty', 'Görgün'),
    imamli: home.finance.work_areas.find(row => row.name === 'İmamlı'), ozvadi: home.finance.work_areas.find(row => row.name === 'Özvadi')};
  for (const [key, value] of Object.entries(refs)) assert.ok(value, `Missing explicit preview reference: ${key}`);
  refs.opening = home.finance.obligations.find(row => row.opening && row.counterparty_id === refs.serkan.id && row.remaining_amount === '2600.00');
  assert.ok(refs.opening, 'Known TEST opening payable needed for bounded clarification');
  const workers = (await api(prefix + '/tasks')).workers;
  refs.worker = config.r2_fixtures ? workers.find(worker => worker.id === config.worker_assignment_fixture.worker_id)
    : workers.find(worker => worker.display_name === 'TEST Muhammed');
  if (!refs.worker) {
    refs.worker = await api(prefix + '/workers', {display_name: 'TEST Muhammed', native_name: 'TEST محمد', language: 'fa', source: 'test:owner-friction-authorized-rendering'});
    fixtureSetup.push({kind: 'TEST Dari worker with no external contact', id: refs.worker.id, name: refs.worker.display_name});
  }
  assert.equal(refs.worker.display_name, 'TEST Muhammed'); assert.equal(refs.worker.language, 'fa');
  assert.equal(refs.worker.contact, null); assert.equal(refs.worker.telegram_ref, null);
  before = await snapshot();
  assert.equal(decimal(before.inventory.stock.find(row => row.product_id === refs.product.id && row.location_id === store.id)?.quantity ?? '0'), '0');
  await page.goto(base); await navigateOwner(page, 'home');
  for (const test of sample) {try {await runCase(test);} catch (error) {fatal ||= error;}}
  try {
    await runCase({id: 'authored-known-worker-Dari-runtime', measured_family: 'worker_Dari_runtime', family: 'worker_assignment',
      split: 'authored_TEST_fixture',
      text: authoredWorker.original, kind: 'WORKER_TASK', expectedBlock: false});
  } catch (error) {fatal ||= error;}
  try {await measurePreparedAssignment(config);} catch (error) {fatal ||= error;}
  assert.deepEqual(errors, []);
} catch (error) {
  fatal ||= error; await diagnostics('failure', error);
} finally {
  await Promise.all(confirmationReads).catch(error => {fatal ||= error;});
  // API writes below are explicitly TEST teardown, excluded from measurements.
  // Reverse in LIFO order so application reverses before its supporting receipt.
  for (const item of [...confirmed].reverse()) {
    try {
      const effect = item.effect, reason = 'TEST measured owner friction teardown';
      const beforePosting = ['PAYABLE', 'PAYMENT', 'DIRECT_EXPENSE'].includes(item.kind) ? readPosting(effect.posting_id) : null;
      const body = {expected_version: effect.version, event_date: effect.event_date, reason};
      let result;
      if (item.kind === 'PERSONAL_TASK') result = await api(owner + `/tasks/${effect.id}/state`, {expected_version: effect.version, state: 'DONE'});
      else if (item.kind === 'WORKER_TASK') {
        const task = await api(prefix + `/tasks/${effect.id}`);
        result = await api(prefix + `/tasks/${effect.id}/actions`, {kind: 'cancel', expected_version: task.version,
          reason, source: 'test:owner-friction-cleanup', locator: effect.id});
        assert.equal(result.original_instruction, authoredWorker.original); assert.deepEqual(result.assignments, []);
        assert.deepEqual(result.prepared_assignment, {worker_id: refs.worker.id, language: 'fa', rendered_text: authoredWorker.rendering});
        assert.ok(result.actions.some(action => action.kind === 'cancel'));
      }
      else if (item.kind === 'PAYABLE') result = await api(`/v1/obligations/${effect.id}/reverse`, body);
      else if (item.kind === 'PAYMENT') result = await api(`/v1/payments/${effect.id}/reverse`, body);
      else if (item.kind === 'DIRECT_EXPENSE') result = await api(owner + `/direct-expenses/${effect.id}/reverse`, body);
      else if (item.kind.startsWith('STOCK_')) result = await api(prefix + `/inventory/events/${effect.id}/reverse`, {event_date: effect.event_date, reason});
      else if (item.kind === 'DEPOT_RECEIPT') result = await api(prefix + `/harvest/settlements/${effect.id}/reverse`, body);
      assert.equal(item.kind === 'PERSONAL_TASK' ? result.state : result.status,
        item.kind === 'PERSONAL_TASK' ? 'DONE' : item.kind === 'WORKER_TASK' ? 'cancelled' : 'reversed');
      let historyVerified = null;
      if (!['PERSONAL_TASK', 'WORKER_TASK'].includes(item.kind)) {
        assert.equal(result.id, effect.id, 'Reversal retains original authoritative record identity');
        if (['PAYABLE', 'PAYMENT', 'DIRECT_EXPENSE'].includes(item.kind)) {
          const originalId = item.kind === 'DIRECT_EXPENSE' ? result.posting_id : result.original_posting_id;
          assert.equal(originalId, effect.posting_id, 'Original immutable posting retained through the documented response projection');
          assert.ok(result.reversal_posting_id, 'Explicit reversing posting recorded');
          assert.notEqual(result.reversal_posting_id, originalId);
          assert.deepEqual(readPosting(originalId), beforePosting, 'Original posting row and every entry remain byte-equivalent JSON');
          const reversal = readPosting(result.reversal_posting_id);
          assert.equal(beforePosting.posting.id, originalId); assert.equal(beforePosting.posting.source_id, effect.id);
          assert.equal(reversal.posting.reversal_of_id, originalId); assert.equal(reversal.posting.source_id, effect.id);
          assert.equal(reversal.posting.entity_id, beforePosting.posting.entity_id); assert.equal(reversal.posting.currency, beforePosting.posting.currency);
          assert.deepEqual(postingLines(reversal), postingLines(beforePosting, true), 'Reversal compensates every immutable original entry');
          item.posting_evidence = {original: beforePosting, reversal, original_unchanged_after_reversal: true};
        } else if (item.kind.startsWith('STOCK_')) {
          assert.deepEqual(result.payload, effect.payload, 'Inventory reversal preserves original payload');
          assert.ok(result.movements.length >= 2, 'Original and reversing stock movements retained');
        } else {
          assert.equal(result.evidence_id, effect.evidence_id, 'Depot reversal retains original evidence');
          assert.deepEqual(result.lines, effect.lines, 'Depot reversal retains immutable receipt lines');
        }
        historyVerified = true;
      }
      cleanup.push({case_id: item.case_id, effect_id: effect.id, ok: true, reversal_history_verified: historyVerified, ...(item.posting_evidence ? {posting_evidence: item.posting_evidence} : {})});
    } catch (error) {cleanup.push({case_id: item.case_id, ok: false, error: String(error)}); fatal ||= error;}
  }
  const reversedItems = confirmed.filter(item => !['PERSONAL_TASK', 'WORKER_TASK'].includes(item.kind));
  if (reversedItems.length) reversalHistoryVerified = reversedItems.every(item => cleanup.some(row => row.case_id === item.case_id && row.ok && row.reversal_history_verified));
  for (const item of assignmentCleanup) {
    try {
      const task = await api(prefix + `/tasks/${item.task_id}`);
      if (task.assignments.length) {
        const cancelled = await api(prefix + `/tasks/${item.task_id}/actions`, {kind: 'cancel', expected_version: task.version,
          reason: 'TEST measured prepared assignment teardown', source: 'test:owner-friction-cleanup', locator: item.task_id});
        assert.equal(cancelled.status, 'cancelled'); assert.equal(cancelled.original_instruction, item.original);
        assert.equal(cancelled.assignments.length, 1); assert.equal(cancelled.assignments[0].rendered_text, authoredWorker.rendering);
        assert.ok(cancelled.actions.some(action => action.kind === 'assign')); assert.ok(cancelled.actions.some(action => action.kind === 'cancel'));
        assignmentRecord.teardown = {cancelled: true, original_preserved: true, actual_assignment_audit_retained: true};
      }
    } catch (error) {fatal ||= error; cleanup.push({case_id: 'authored-prepared-Dari-assignment', ok: false, error: String(error)});}
  }
  try {
    if (sources.length) originalsVerified = false;
    for (const source of sources) {
      assert.equal(await original(source.id), source.hash, 'Original immutable through edit/confirm/reversal');
      const value = await api(owner + `/captures/${source.id}`);
      assert.equal(value.text, source.text);
      // Clear only this suite's unconfirmed sources, preserving audit/originals.
      for (const slot of value.proposals) if (!['POSTED', 'CONFIRMED_NONPOSTING', 'REJECTED'].includes(slot.state)) {
        const proposal = await api(owner + `/confirmations/${slot.id}/preview`);
        await api(owner + `/confirmations/${slot.id}/reject`, {expected_version: proposal.version});
      }
    }
    if (sources.length) originalsVerified = true;
    if (before) {
      economicRestored = false;
      finalState = await snapshot();
      assert.deepEqual(finalState.economic, before.economic, 'Finance, Inventory economic positions and original Harvest evidence restored exactly');
      for (const item of confirmed.filter(row => row.kind === 'DEPOT_RECEIPT')) {
        const settlement = finalState.harvest.settlements.find(row => row.id === item.effect.id);
        assert.equal(settlement.status, 'reversed');
        assert.equal(decimal(settlement.reconciliation.depot_weight_kg), '100');
        assert.equal(settlement.payment_id, null);
      }
      economicRestored = true;
    }
  } catch (error) {fatal ||= error; cleanup.push({ok: false, error: String(error), stage: 'restored domain reconciliation'});}
  const cases = records.map(summarizeCase);
  if (fatal) await diagnostics('failure', fatal);
  const report = {schema_version: 1, ok: !fatal, exact_sha: execFileSync('git', ['rev-parse', 'HEAD'], {encoding: 'utf8'}).trim(),
    measurement: 'Actual executed browser subset, not corpus required-field deficit proxy',
    physical_iphone_acceptance: false, acoustic_recognition_tested: false, browser: 'Chromium mobile emulation', viewport: {width: 390, height: 844},
    comparison: 'Current exact SHA only. No recreated baseline or unverified before/after claim.',
    training_sha256: hash(trainingBytes), holdout_accessed: false, definitions: metricDefinitions,
    input_scope: 'Ten unmodified training phrases plus one separately scoped pre-authored TEST Dari runtime capture. Historical language is not current operational truth; the supplied Dari rendering is not runtime translation.',
    fixture_setup_excluded: fixtureSetup, cases, by_family: summarizeFamilies(cases.filter(row => row.split === 'train')),
    supplementary_runtime_cases: cases.filter(row => row.split !== 'train'),
    prepared_assignment_measurement: assignmentRecord ?? {status: 'not_run', TAPS_TO_ASSIGN: null, TAPS_TO_CONFIRM: null},
    denominators: {training_browser_cases: cases.filter(row => row.split === 'train').length,
      authored_runtime_cases: cases.filter(row => row.split !== 'train').length, prepared_assignment_separate: true},
    teardown: {cleanup, originals_preserved: originalsVerified,
      finance_reconciliation_ok: finalState?.financeReconciliation.ok ?? null,
      inventory_invariants_ok: finalState?.inventory.invariants.ok ?? null,
      economic_positions_restored: economicRestored,
      immutable_reversal_history_retained: reversalHistoryVerified}, console: consoleLogs, errors, error: fatal ? String(fatal) : null};
  await fs.writeFile(`${artifacts}/owner-friction-metrics.json`, JSON.stringify(report, null, 2));
  console.log(JSON.stringify({owner_friction_metrics: `${artifacts}/owner-friction-metrics.json`, ok: report.ok, by_family: report.by_family}));
  await browser.close();
}
if (fatal) throw fatal;
console.log('PASS measured TRAINING source/review/edit/confirm friction; exact originals preserved, unsupported sources blocked, synthetic effects reversed, Finance/Inventory/Harvest reconciled. Physical iPhone acceptance remains pending.');
