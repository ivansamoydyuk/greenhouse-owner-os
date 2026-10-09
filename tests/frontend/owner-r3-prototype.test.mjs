import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import {
  initialState,
  SCENARIO_TEXT,
  harvestDays,
  harvestRows,
} from "../../prototype/owner-r3/fixtures.js";
import {
  loadState,
  persist,
  pending,
  moneyTotal,
  harvestMetrics,
  currentHarvestRows,
  prepare,
  canConfirm,
  confirm,
  updateFact,
  transcriptText,
  STORAGE_KEY,
  parseAmount,
} from "../../prototype/owner-r3/demo-adapter.js";
import {
  Header,
  Navigation,
  CaptureBar,
  MetricCard,
  ReviewCard,
  TaskRow,
  StockRow,
  FilterChip,
  FactChip,
  Sheet,
  EmptyState,
  ErrorState,
  LoadingState,
  e,
  money,
} from "../../prototype/owner-r3/components.js";
test("R3 initial synthetic positions and season reconcile across shared rows", () => {
  const s = initialState(),
    m = harvestMetrics();
  assert.equal(m.kg, 14000);
  assert.equal(m.revenue, 438400);
  assert.equal(m.countedKg, 10800);
  assert.equal(m.bunches, 300);
  assert.equal(m.bunchWeight, 36);
  assert.equal(m.price, 438400 / 14000);
  assert.equal(
    harvestDays.reduce((a, r) => a + r.kg, 0),
    m.kg,
  );
  assert.equal(
    m.quality.reduce((a, x) => a + x, 0),
    m.kg,
  );
  assert.equal(m.quality[3], 500);
  assert.equal(moneyTotal(s.receivables), m.revenue - 252000);
  assert.equal(moneyTotal(s.payables), 42600);
});
test("R3 weighted ratios never average greenhouse averages", () => {
  const a = [
    {
      kg: 100,
      revenue: 4000,
      countedKg: 100,
      bunches: 5,
      quality: [100, 0, 0, 0],
    },
    {
      kg: 300,
      revenue: 18000,
      countedKg: 300,
      bunches: 10,
      quality: [0, 300, 0, 0],
    },
    {
      kg: 200,
      revenue: 10000,
      countedKg: 0,
      bunches: 0,
      quality: [0, 0, 0, 200],
    },
  ];
  const m = harvestMetrics(a);
  assert.equal(m.bunchWeight, 400 / 15);
  assert.equal(m.price, 32000 / 600);
  assert.equal(harvestMetrics([]).bunchWeight, null);
  assert.equal(harvestMetrics([]).price, null);
});
test("R3 capture exact task retains verbatim original, relative period, unknown date and worker", () => {
  const s = initialState();
  s.draft.text = SCENARIO_TEXT;
  const p = prepare(s);
  assert.equal(p.source, SCENARIO_TEXT);
  assert.equal(p.place, "Özvadi");
  assert.equal(p.when, "Следующая неделя");
  assert.equal(p.date, null);
  assert.equal(p.worker, null);
  assert.equal(s.tasks.length, 3);
  assert.equal(s.sources[0].text, SCENARIO_TEXT);
  assert.equal(canConfirm(p), true);
});
test("R3 no arbitrary text understanding or hidden parser", () => {
  for (const text of [
    "Нужно сделать что-то завтра",
    "Переделать полив на Озвади",
    "Заплатил 99999",
    "Нужно переделать полив на Озвади на следующей неделе!",
  ]) {
    const s = initialState();
    s.draft.text = text;
    const p = prepare(s);
    assert.equal(p.type, "unknown");
    assert.equal(p.source, text);
    assert.equal(canConfirm(p), false);
    assert.equal(s.activity.length, 0);
  }
});
test("R3 explicit synthetic receipt returns same existing source identity", () => {
  const s = initialState();
  for (let i = 0; i < 3; i++) {
    s.draft = {
      mode: "photo",
      text: "",
      comment: "",
      file: { name: "TEST", fixture: true },
      scenario: "receipt",
    };
    assert.equal(prepare(s).id, "depot-review");
  }
  assert.equal(s.proposals.filter((x) => x.type === "harvest").length, 1);
});
test("R3 arbitrary selected file remains unknown without invented extraction", () => {
  const s = initialState();
  s.draft.file = {
    name: "TEST.png",
    url: "data:image/png;base64,AAAA",
    kind: "image",
  };
  const p = prepare(s);
  assert.equal(p.type, "unknown");
  assert.equal(p.image, s.sources[0].file.url);
  assert.equal(canConfirm(p), false);
});
test("R3 meaningful capture required", () =>
  assert.throws(() => prepare(initialState())));
test("R3 amount missing gives no effect; positive amount unlocks one confirmation", () => {
  const s = initialState();
  assert.equal(canConfirm(s.proposals[0]), false);
  assert.throws(() => confirm(s, "clarify", 1));
  updateFact(s, "clarify", "amount", 2500);
  assert.equal(canConfirm(s.proposals[0]), true);
  confirm(s, "clarify", 2);
  assert.equal(moneyTotal(s.payables), 45100);
  assert.throws(() => confirm(s, "clarify", 2));
  assert.equal(moneyTotal(s.payables), 45100);
});
test("R3 stale revision has zero effect and retains source", () => {
  const s = initialState();
  const original = s.proposals[0].source;
  updateFact(s, "clarify", "amount", 2500);
  assert.throws(() => confirm(s, "clarify", 1), /STALE/);
  assert.equal(moneyTotal(s.payables), 42600);
  assert.equal(s.proposals[0].source, original);
});
test("R3 source preparation alone leaves finance, inventory and task records unchanged", () => {
  const s = initialState(),
    before = JSON.stringify([s.receivables, s.payables, s.stock, s.tasks]);
  s.draft.text = SCENARIO_TEXT;
  prepare(s);
  assert.equal(
    JSON.stringify([s.receivables, s.payables, s.stock, s.tasks]),
    before,
  );
});
test("R3 reviewed task fact edits retained while source stays immutable", () => {
  const s = initialState();
  s.draft.text = SCENARIO_TEXT;
  const p = prepare(s);
  updateFact(s, p.id, "place", "İmamlı");
  updateFact(s, p.id, "date", "2026-10-13");
  confirm(s, p.id, p.revision);
  assert.equal(s.tasks[0].place, "İmamlı");
  assert.equal(s.tasks[0].date, "2026-10-13");
  assert.equal(s.tasks[0].worker, null);
  assert.equal(s.tasks[0].source, SCENARIO_TEXT);
});
test("R3 deferred actions remain in unresolved count; rejection and confirmation remove only one", () => {
  const s = initialState();
  s.proposals[0].status = "deferred";
  assert.equal(pending(s).length, 4);
  s.proposals[1].status = "rejected";
  assert.equal(pending(s).length, 3);
  assert.equal(s.proposals[1].source.includes("DEMO-09"), true);
});
test("R3 demo stock arrival creates quantity only, never synthetic price or debt", () => {
  const s = initialState(),
    p = s.proposals.find((x) => x.id === "stock-review");
  confirm(s, p.id, p.revision);
  assert.equal(s.stock[0].quantity, 280);
  assert.equal(s.stock[0].cost, null);
  assert.equal(
    s.stock[0].movements.reduce((a, x) => a + x.quantity, 0),
    280,
  );
  assert.equal(moneyTotal(s.payables), 42600);
});
test("R3 confirmed depot updates shared season, receivable and unknown quality only", () => {
  const s = initialState(),
    p = s.proposals.find((x) => x.id === "depot-review");
  confirm(s, p.id, p.revision);
  const m = harvestMetrics(currentHarvestRows(s));
  assert.equal(m.kg, 15200);
  assert.equal(m.revenue, 475600);
  assert.equal(m.quality[3], 1700);
  assert.equal(m.countedKg, 10800);
  assert.equal(moneyTotal(s.receivables), m.revenue - 252000);
  assert.equal(moneyTotal(s.payables), 42600);
});
test("R3 worker assignment counts derive from real matching synthetic records", () => {
  const s = initialState();
  for (const w of s.workers)
    assert.equal(
      w.tasks,
      s.workerTasks.filter((t) => t.worker === w.id).length,
    );
  assert.equal(s.workerTask.worker, null);
  assert.equal(s.workerTask.translationReviewed, false);
});
test("R3 unknown inventory is not zero or a fabricated low-stock alert", () => {
  const s = initialState();
  assert.equal(s.stock[2].quantity, null);
  assert.match(StockRow(s.stock[2]), /Неизвестно/);
  assert.doesNotMatch(StockRow(s.stock[2]), /Низкий/);
});
test("R3 names are synthetic and safe; no generated avatars or future channels", () => {
  const s = initialState();
  assert.ok(s.workers.every((w) => w.name.startsWith("TEST ")));
  assert.ok(
    [...s.receivables, ...s.payables].every((x) => x.name.startsWith("TEST ")),
  );
  assert.ok(s.workers.every((w) => !("online" in w)));
});
test("R3 cumulative speech results are replaced rather than appended repeatedly", () => {
  const results = [
    [{ transcript: "Нужно переделать" }],
    [{ transcript: "полив" }],
  ];
  assert.equal(transcriptText(results), "Нужно переделать полив");
  assert.equal(transcriptText(results), "Нужно переделать полив");
});
test("R3 storage isolated key, malformed recovery and storage errors are explicit", () => {
  const map = new Map(),
    storage = { getItem: (k) => map.get(k), setItem: (k, v) => map.set(k, v) };
  const s = initialState();
  s.draft.text = "Не потерять";
  assert.equal(persist(storage, s), true);
  assert.equal(loadState(storage).draft.text, "Не потерять");
  assert.deepEqual([...map.keys()], [STORAGE_KEY]);
  map.set(STORAGE_KEY, "broken");
  assert.equal(loadState(storage).version, 1);
  assert.equal(
    persist(
      {
        setItem() {
          throw Error("quota");
        },
      },
      s,
    ),
    false,
  );
});
test("R3 HTML templates escape source content", () => {
  assert.equal(e('<script>"&'), "&lt;script&gt;&quot;&amp;");
  assert.doesNotMatch(
    ReviewCard({ ...initialState().proposals[0], title: "<img onerror=bad>" }),
    /<img onerror/,
  );
});
test("R3 reusable named components are present and generate semantic controls", () => {
  for (const fn of [
    Header,
    Navigation,
    CaptureBar,
    MetricCard,
    ReviewCard,
    TaskRow,
    StockRow,
    FilterChip,
    FactChip,
    Sheet,
    EmptyState,
    ErrorState,
    LoadingState,
  ])
    assert.equal(typeof fn, "function");
  assert.match(Sheet("TEST", "", ""), /<dialog/);
  assert.match(CaptureBar(), /capture-photo/);
  assert.match(CaptureBar(), /capture-voice/);
  assert.match(CaptureBar(), /capture-text/);
});
test("R3 no bulk confirmation, fabricated statuses or hidden auth in demo adapter", () => {
  const adapter = fs.readFileSync("prototype/owner-r3/demo-adapter.js", "utf8");
  assert.doesNotMatch(
    adapter,
    /\bfetch\s*\(|XMLHttpRequest|WebSocket|\/v1\/|greenhouse_owner_os|confirmAll/i,
  );
  const app = fs.readFileSync("prototype/owner-r3/app.js", "utf8");
  assert.doesNotMatch(app, /\bfetch\s*\(|XMLHttpRequest|WebSocket|\/v1\//);
  assert.doesNotMatch(app, /Подтвердить всё|Всё в порядке|>Свободен</);
});
test("R3 focus, one dialog, scale and reduced motion affordances", () => {
  const html = fs.readFileSync("prototype/owner-r3/index.html", "utf8"),
    css = fs.readFileSync("prototype/owner-r3/styles.css", "utf8");
  assert.doesNotMatch(html, /user-scalable=no|maximum-scale=1/);
  assert.match(css, /:focus-visible/);
  assert.match(css, /safe-area-inset-bottom/);
  assert.match(css, /prefers-reduced-motion/);
});

test("R3 decimal amount review preserves cents and accepts binary-float-sensitive 1.10", () => {
  assert.equal(parseAmount("1,10"), 1.1);
  assert.equal(parseAmount("7,50"), 7.5);
  assert.match(money(1.1), /1,10/);
  assert.match(money(123456789.12), /789,12/);
  assert.throws(() => parseAmount("1,111"));
  assert.throws(() => parseAmount("-1"));
  assert.throws(() => parseAmount(""));
});
test("R3 free-text timing edit clears conflicting exact date", () => {
  const s = initialState();
  updateFact(s, "irrigation", "when", "Следующая неделя");
  assert.equal(s.tasks[0].date, null);
  assert.equal(s.tasks[0].when, "Следующая неделя");
  assert.equal(s.tasks[0].source, "Синтетическая личная задача");
});
