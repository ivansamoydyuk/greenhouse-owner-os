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

test("R3 reviewed/rejected proposal remains immutable while original is retained", () => { const s=initialState(); updateFact(s,"clarify","amount",2500); confirm(s,"clarify",2); assert.throws(()=>updateFact(s,"clarify","amount",9000),/READ_ONLY/); s.proposals[1].status="rejected"; assert.throws(()=>updateFact(s,"depot-review","amount",1),/READ_ONLY/); assert.match(s.proposals[1].source,/37 200/); });

// Final design-lock regressions. Keep all original R3 assertions above intact.
const {
  currentAction,
  parseQuantity,
  resolveCaption,
  clarifyIntent,
} = await import("../../prototype/owner-r3/demo-adapter.js");
const { RECOVERY_TEXT, CAPTION_CONFLICT } = await import(
  "../../prototype/owner-r3/fixtures.js"
);
const correctionSnapshot = (s) =>
  structuredClone({
    receivables: s.receivables,
    payables: s.payables,
    stock: s.stock,
    tasks: s.tasks,
    activity: s.activity,
  });
const correctionStorage = () => {
  const values = new Map();
  return {
    getItem: (key) => values.get(key),
    setItem: (key, value) => values.set(key, value),
  };
};
const receiptDraft = (comment = "", scenario = "receipt") => ({
  mode: "photo",
  text: "",
  comment,
  file: {
    name: "TEST-чек DEMO-09.svg",
    kind: "image",
    url: "assets/receipt.svg",
    fixture: true,
  },
  scenario,
});

test("R3 design lock composes corrected task meaning without rewriting source or persisting a contradictory title", () => {
  const s = initialState();
  s.draft.text = SCENARIO_TEXT;
  const before = correctionSnapshot(s);
  const p = prepare(s);
  const originalSources = structuredClone(s.sources);
  assert.equal(p.instruction, "Переделать полив");
  updateFact(s, p.id, "place", "İmamlı");
  updateFact(s, p.id, "when", "Завтра");
  const action = currentAction(p);
  assert.match(action, /Переделать полив/);
  assert.match(action, /İmamlı/);
  assert.match(action, /Завтра/);
  assert.doesNotMatch(action, /Озвади|Özvadi|следующей неделе|Следующая неделя/);
  assert.equal(p.source, SCENARIO_TEXT);
  assert.deepEqual(s.sources, originalSources);
  assert.deepEqual(correctionSnapshot(s), before);

  const storage = correctionStorage();
  assert.equal(persist(storage, s), true);
  const restored = loadState(storage);
  const restoredProposal = restored.proposals.find((x) => x.id === p.id);
  assert.equal(currentAction(restoredProposal), action);
  confirm(restored, p.id, restoredProposal.revision);
  const task = restored.tasks.find((x) => x.id === p.id);
  assert.equal(task.title, "Переделать полив");
  assert.equal(task.place, "İmamlı");
  assert.equal(task.when, "Завтра");
  assert.equal(task.worker, null);
  assert.equal(task.source, SCENARIO_TEXT);
  assert.equal(currentAction(task), action);
  assert.equal(persist(storage, restored), true);
  assert.deepEqual(loadState(storage).tasks[0], JSON.parse(JSON.stringify(task)));
});

test("R3 design lock immutable source and caption keys cannot be changed through a fact edit", () => {
  const s = initialState();
  s.draft.text = SCENARIO_TEXT;
  const p = prepare(s);
  for (const key of ["source", "comment"]) {
    const before = structuredClone(s);
    assert.throws(() => updateFact(s, p.id, key, "Переписанный источник"));
    assert.deepEqual(s, before);
  }
});

test("R3 design lock stock quantity correction changes one fact and confirms the matching balance only", () => {
  const s = initialState();
  const p = s.proposals.find((x) => x.id === "stock-review");
  const original = structuredClone(p);
  const before = correctionSnapshot(s);
  updateFact(s, p.id, "quantity", 125.75);
  assert.equal(p.quantity, 125.75);
  for (const key of ["product", "title", "unit", "place", "date", "source"])
    assert.equal(p[key], original[key]);
  assert.deepEqual(correctionSnapshot(s), before);
  assert.equal(canConfirm(p), true);
  confirm(s, p.id, p.revision);
  const row = s.stock.find((x) => x.id === "kno3");
  assert.equal(row.quantity, 305.75);
  assert.equal(row.movements.at(-1).quantity, 125.75);
  assert.equal(row.cost, null);
  assert.deepEqual(s.stock.slice(1), before.stock.slice(1));
  assert.deepEqual(s.payables, before.payables);
  assert.deepEqual(s.receivables, before.receivables);
  assert.equal(p.source, original.source);
});

test("R3 design lock stock product and location corrections route to the actual matching product tuple", () => {
  const s = initialState();
  const p = s.proposals.find((x) => x.id === "stock-review");
  const original = structuredClone(p);
  const before = correctionSnapshot(s);
  updateFact(s, p.id, "product", "Кальциевая селитра");
  assert.equal(p.title, "Кальциевая селитра");
  assert.equal(p.quantity, original.quantity);
  assert.equal(p.unit, original.unit);
  assert.equal(p.place, original.place);
  updateFact(s, p.id, "place", "Yeşilöz");
  assert.match(currentAction(p), /Кальциевая селитра/);
  assert.match(currentAction(p), /Yeşilöz/);
  assert.doesNotMatch(currentAction(p), /KNO|İmamlı/);
  assert.deepEqual(correctionSnapshot(s), before);
  confirm(s, p.id, p.revision);
  assert.equal(s.stock.find((x) => x.id === "calcium").quantity, 175);
  assert.deepEqual(s.stock.find((x) => x.id === "kno3"), before.stock[0]);
  assert.equal(p.source, original.source);
  assert.deepEqual(s.payables, before.payables);
});

test("R3 design lock stock location correction does not add to another greenhouse balance", () => {
  const s = initialState();
  const p = s.proposals.find((x) => x.id === "stock-review");
  const previousStock = structuredClone(s.stock);
  updateFact(s, p.id, "place", "Özvadi");
  confirm(s, p.id, p.revision);
  assert.deepEqual(s.stock.filter((x) => previousStock.some((old) => old.id === x.id)), previousStock);
  const row = s.stock.find((x) => x.name === "KNO₃" && x.place === "Özvadi" && x.unit === "кг");
  assert.ok(row);
  assert.equal(row.quantity, null);
  assert.equal(row.cost, null);
  assert.equal(row.movements.length, 1);
  assert.equal(row.movements[0].quantity, 100);
});

test("R3 design lock incompatible stock units stay separate without conversion or invented opening balance", () => {
  const s = initialState();
  const p = s.proposals.find((x) => x.id === "stock-review");
  const before = correctionSnapshot(s);
  updateFact(s, p.id, "unit", "л");
  assert.equal(p.quantity, 100);
  assert.equal(p.product, "KNO₃");
  assert.equal(p.place, "İmamlı");
  assert.match(currentAction(p), /л/);
  confirm(s, p.id, p.revision);
  assert.deepEqual(s.stock.find((x) => x.id === "kno3"), before.stock[0]);
  const row = s.stock.find((x) => x.name === "KNO₃" && x.place === "İmamlı" && x.unit === "л");
  assert.ok(row);
  assert.equal(row.quantity, null);
  assert.equal(row.cost, null);
  assert.equal(row.movements.length, 1);
  assert.equal(row.movements[0].quantity, 100);
  assert.deepEqual(s.payables, before.payables);
  assert.deepEqual(s.receivables, before.receivables);
  const storage = correctionStorage();
  assert.equal(persist(storage, s), true);
  assert.deepEqual(loadState(storage).stock, s.stock);
});

test("R3 design lock positive quantity parser preserves decimals and refuses invalid or overprecise quantities", () => {
  for (const [raw, expected] of [["125,75", 125.75], ["1 200,125", 1200.125], ["0.001", 0.001], ["100", 100]])
    assert.equal(parseQuantity(raw), expected);
  for (const raw of ["", "0", "-1", "1.0001", "1e3", "NaN", "Infinity", "12 кг"])
    assert.throws(() => parseQuantity(raw), undefined, raw);
});

test("R3 design lock depot weight correction retains amount and price until the owner resolves the mismatch", () => {
  const s = initialState();
  const p = s.proposals.find((x) => x.id === "depot-review");
  const original = structuredClone(p);
  const before = correctionSnapshot(s);
  assert.equal(p.price, 31);
  updateFact(s, p.id, "kg", 1100);
  assert.equal(p.kg, 1100);
  assert.equal(p.price, 31);
  assert.equal(p.amount, 37200);
  assert.equal(p.source, original.source);
  assert.equal(canConfirm(p), false);
  assert.throws(() => confirm(s, p.id, p.revision), /NEEDS_INPUT/);
  assert.deepEqual(correctionSnapshot(s), before);
  assert.equal(p.status, "pending");
  updateFact(s, p.id, "amount", 34100);
  assert.equal(p.kg, 1100);
  assert.equal(p.price, 31);
  assert.equal(canConfirm(p), true);
  confirm(s, p.id, p.revision);
  assert.equal(s.activity.at(-1).kg, 1100);
  assert.equal(s.activity.at(-1).revenue, 34100);
  assert.equal(moneyTotal(s.receivables), moneyTotal(before.receivables) + 34100);
  assert.equal(harvestMetrics(currentHarvestRows(s)).kg, 15100);
  assert.equal(p.source, original.source);
  assert.deepEqual(s.payables, before.payables);
});

test("R3 design lock depot price and amount corrections are independent and do not silently recompute", () => {
  const s = initialState();
  const p = s.proposals.find((x) => x.id === "depot-review");
  const originalSource = p.source;
  updateFact(s, p.id, "price", 32.5);
  assert.equal(p.kg, 1200);
  assert.equal(p.amount, 37200);
  assert.equal(canConfirm(p), false);
  updateFact(s, p.id, "amount", 39000);
  assert.equal(p.kg, 1200);
  assert.equal(p.price, 32.5);
  assert.equal(canConfirm(p), true);
  updateFact(s, p.id, "amount", 39001);
  assert.equal(p.kg, 1200);
  assert.equal(p.price, 32.5);
  assert.equal(canConfirm(p), false);
  assert.equal(p.source, originalSource);
});

test("R3 design lock depot consistency compares monetary cents rather than raw floating-point products", () => {
  const s = initialState();
  const p = s.proposals.find((x) => x.id === "depot-review");
  updateFact(s, p.id, "kg", 0.1);
  updateFact(s, p.id, "price", 1.1);
  updateFact(s, p.id, "amount", 0.11);
  assert.equal(canConfirm(p), true);
  updateFact(s, p.id, "amount", 0.12);
  assert.equal(canConfirm(p), false);
});

test("R3 design lock depot place correction affects only the selected greenhouse season row", () => {
  const s = initialState();
  const p = s.proposals.find((x) => x.id === "depot-review");
  const beforeRows = currentHarvestRows(s);
  const source = p.source;
  updateFact(s, p.id, "place", "İmamlı");
  assert.match(currentAction(p), /İmamlı/);
  assert.doesNotMatch(currentAction(p), /Özvadi/);
  confirm(s, p.id, p.revision);
  const afterRows = currentHarvestRows(s);
  for (const row of beforeRows) {
    const after = afterRows.find((x) => x.id === row.id);
    if (row.name === "İmamlı") {
      assert.equal(after.kg, row.kg + 1200);
      assert.equal(after.revenue, row.revenue + 37200);
    } else assert.deepEqual(after, row);
  }
  assert.equal(p.source, source);
});

test("R3 design lock photo captions remain separate immutable evidence attached to a single depot proposal", () => {
  const s = initialState();
  const depot = s.proposals.find((x) => x.id === "depot-review");
  const originalSource = depot.source;
  const initialRevision = depot.revision;
  const comments = ["Уточни вес по этому чеку", "Второй комментарий владельца"];
  const before = correctionSnapshot(s);
  for (const comment of comments) {
    const previousRevision = depot.revision;
    s.draft = receiptDraft(comment);
    assert.equal(prepare(s), depot);
    assert.equal(depot.revision, previousRevision + 1);
    assert.throws(() => confirm(s, depot.id, previousRevision), /STALE/);
    assert.deepEqual(correctionSnapshot(s), before);
    assert.equal(depot.status, "pending");
  }
  assert.equal(s.proposals.filter((x) => x.id === depot.id).length, 1);
  assert.equal(s.sources.length, 2);
  assert.notEqual(s.sources[0].id, s.sources[1].id);
  assert.deepEqual(s.sources.map((x) => x.comment), comments);
  assert.ok(s.sources.every((x) => x.proposalId === depot.id));
  assert.ok(s.sources.every((x) => x.file.name === "TEST-чек DEMO-09.svg"));
  assert.equal(depot.source, originalSource);
  assert.equal(depot.revision, initialRevision + comments.length);
  const storage = correctionStorage();
  assert.equal(persist(storage, s), true);
  assert.deepEqual(loadState(storage).sources, s.sources);
});

test("R3 design lock arbitrary photo caption does not fabricate semantic reconciliation", () => {
  const s = initialState();
  s.draft = receiptDraft(CAPTION_CONFLICT);
  const p = prepare(s);
  assert.equal(Boolean(p.captionConflict), false);
  assert.equal(p.kg, 1200);
  assert.equal(p.amount, 37200);
  assert.equal(canConfirm(p), true);
  assert.equal(s.sources[0].comment, CAPTION_CONFLICT);

  s.draft = {
    ...receiptDraft(CAPTION_CONFLICT, "receipt-conflict"),
    file: { name: "unknown.png", kind: "image", url: "data:image/png;base64,AAAA" },
  };
  const unknown = prepare(s);
  assert.equal(unknown.type, "unknown");
  assert.equal(canConfirm(unknown), false);
  assert.equal(Boolean(unknown.captionConflict), false);
});

test("R3 design lock explicit caption contradiction blocks effects until owner clarification and preserves both sources", () => {
  const s = initialState();
  const before = correctionSnapshot(s);
  s.draft = receiptDraft(CAPTION_CONFLICT, "receipt-conflict");
  const p = prepare(s);
  const source = p.source;
  const evidence = structuredClone(s.sources);
  const revision = p.revision;
  assert.equal(p.captionConflict, true);
  assert.equal(canConfirm(p), false);
  assert.throws(() => confirm(s, p.id, p.revision), /NEEDS_INPUT/);
  assert.deepEqual(correctionSnapshot(s), before);
  resolveCaption(s, p.id, "document");
  assert.equal(p.captionConflict, false);
  assert.ok(p.revision > revision);
  assert.equal(p.kg, 1200);
  assert.equal(p.price, 31);
  assert.equal(p.amount, 37200);
  assert.equal(p.source, source);
  assert.deepEqual(s.sources, evidence);
  assert.equal(canConfirm(p), true);
  confirm(s, p.id, p.revision);
  assert.equal(s.activity.filter((x) => x.id === p.id).length, 1);
  assert.throws(() => resolveCaption(s, p.id, "document"), /READ_ONLY/);
});

test("R3 design lock repeated receipt evidence never reopens or duplicates a confirmed operation", () => {
  const s = initialState();
  s.draft = receiptDraft("Первый комментарий");
  const p = prepare(s);
  confirm(s, p.id, p.revision);
  const confirmedProposal = structuredClone(p);
  const before = correctionSnapshot(s);
  const firstSource = structuredClone(s.sources[0]);
  s.draft = receiptDraft(CAPTION_CONFLICT, "receipt-conflict");
  const repeated = prepare(s);
  assert.equal(repeated.id, p.id);
  assert.deepEqual(repeated, confirmedProposal);
  assert.equal(s.sources.length, 2);
  assert.deepEqual(s.sources[0], firstSource);
  assert.equal(s.sources[1].comment, CAPTION_CONFLICT);
  assert.equal(s.sources[1].proposalId, p.id);
  assert.equal(canConfirm(repeated), false);
  assert.throws(() => confirm(s, p.id, p.revision), /NEEDS_INPUT/);
  assert.deepEqual(correctionSnapshot(s), before);
});

test("R3 design lock explicit unknown-event storyboard preserves Serkan and tomorrow through one clarification", () => {
  const s = initialState();
  s.draft.text = RECOVERY_TEXT;
  s.draft.scenario = "recovery";
  const before = correctionSnapshot(s);
  const p = prepare(s);
  assert.equal(p.type, "unknown");
  assert.equal(p.recovery, true);
  assert.equal(p.party, "TEST Серкан");
  assert.equal(p.when, "Завтра");
  assert.equal(p.date, null);
  assert.equal(p.worker, null);
  assert.equal(p.source, RECOVERY_TEXT);
  assert.equal(canConfirm(p), false);
  assert.equal(s.draft.text, "");
  const evidence = structuredClone(s.sources);
  clarifyIntent(s, p.id, "reminder");
  assert.equal(p.type, "task");
  assert.equal(p.instruction, "Связаться с Серканом");
  assert.equal(p.title, "Связаться с Серканом");
  assert.equal(p.party, "TEST Серкан");
  assert.equal(p.when, "Завтра");
  assert.equal(p.date, null);
  assert.equal(p.worker, null);
  assert.equal(p.source, RECOVERY_TEXT);
  assert.match(currentAction(p), /Серкан/);
  assert.match(currentAction(p), /Завтра/);
  assert.deepEqual(s.sources, evidence);
  assert.deepEqual(correctionSnapshot(s), before);
  assert.equal(canConfirm(p), true);
  const storage = correctionStorage();
  assert.equal(persist(storage, s), true);
  const restored = loadState(storage);
  const prepared = restored.proposals.find((x) => x.id === p.id);
  confirm(restored, prepared.id, prepared.revision);
  const task = restored.tasks.find((x) => x.id === p.id);
  assert.equal(task.title, "Связаться с Серканом");
  assert.equal(task.party, "TEST Серкан");
  assert.equal(task.when, "Завтра");
  assert.equal(task.date, null);
  assert.equal(task.worker, null);
  assert.equal(task.source, RECOVERY_TEXT);
  assert.deepEqual(restored.sources, evidence);
  assert.equal(restored.draft.text, "");
});

test("R3 design lock recovery is explicit fixture-only rather than a new arbitrary text parser", () => {
  for (const [text, scenario] of [
    [RECOVERY_TEXT, null],
    ["Серкану завтра нужно заплатить 9000", "recovery"],
    ["Серкану завтра нужно...", "recovery"],
  ]) {
    const s = initialState();
    s.draft.text = text;
    s.draft.scenario = scenario;
    const p = prepare(s);
    assert.equal(p.type, "unknown");
    assert.equal(Boolean(p.recovery), false);
    assert.equal(p.party ?? null, null);
    assert.equal(canConfirm(p), false);
    assert.equal(p.source, text);
    const before = structuredClone(s);
    assert.throws(() => clarifyIntent(s, p.id, "reminder"));
    assert.deepEqual(s, before);
  }
});

test("R3 design lock review separates original evidence, owner context, current action and contradiction affordances", () => {
  const app = fs.readFileSync("prototype/owner-r3/app.js", "utf8");
  for (const label of ["ОРИГИНАЛ", "ТЕКУЩЕЕ ПОДТВЕРЖДАЕМОЕ ДЕЙСТВИЕ", "Комментарий владельца", "Что произошло?"])
    assert.ok(app.includes(label), label);
  for (const selector of ["source-original", "current-action", "owner-comment", "caption-conflict"])
    assert.ok(app.includes(selector), selector);
  assert.doesNotMatch(app, /function\s+(?:parseCaption|reconcileCaption|parseUnknownIntent)\s*\(/);
});

test("R3 design lock money metrics retain every digit and visible currency under the shared no-wrap contract", () => {
  for (const value of [186400, 123456789.12, 999999999.99]) {
    const html = MetricCard({ label: "Мне должны", value, detail: "TEST", to: "finance/receivable" });
    const numeric = html.match(/<strong\b[^>]*class="[^"]*metric-value[^\"]*"[^>]*>([^<]+)<\/strong>/)?.[1];
    assert.ok(numeric);
    assert.equal(numeric.replace(/[\s\u00a0\u202f]/g, ""), String(value).replace(".", ","));
    assert.match(html, /class="currency"[^>]*>TRY<\/span>/);
    assert.doesNotMatch(numeric, /[eE][+-]?\d|…/);
  }
  const css = fs.readFileSync("prototype/owner-r3/styles.css", "utf8");
  const numericRules = [...css.matchAll(/([^{}]+)\{([^{}]*)\}/g)]
    .filter((match) => match[1].split(",").some((selector) => selector.trim() === ".metric-value"))
    .map((match) => match[2]);
  assert.ok(numericRules.some((rule) => /white-space:\s*nowrap/.test(rule)));
  assert.ok(numericRules.some((rule) => /overflow-wrap:\s*normal/.test(rule)));
  assert.ok(numericRules.some((rule) => /word-break:\s*normal/.test(rule)));
});

test("R3 design lock caption clarification applies the explicitly chosen weight without silently changing price or amount", () => {
  for (const resolution of ["comment", "document"]) {
    const s = initialState();
    const before = correctionSnapshot(s);
    s.draft = receiptDraft(CAPTION_CONFLICT, "receipt-conflict");
    const p = prepare(s);
    const source = p.source;
    const evidence = structuredClone(s.sources);
    updateFact(s, p.id, "kg", 1050);
    assert.equal(p.captionConflict, true);
    assert.equal(canConfirm(p), false);
    const previousRevision = p.revision;
    resolveCaption(s, p.id, resolution);
    assert.equal(p.captionResolution, resolution);
    assert.equal(p.captionConflict, false);
    assert.equal(p.revision, previousRevision + 1);
    assert.equal(p.kg, resolution === "comment" ? 1100 : 1200);
    assert.equal(p.price, 31);
    assert.equal(p.amount, 37200);
    assert.equal(p.source, source);
    assert.deepEqual(s.sources, evidence);
    assert.deepEqual(correctionSnapshot(s), before);
    if (resolution === "comment") {
      assert.equal(canConfirm(p), false);
      assert.throws(() => confirm(s, p.id, p.revision), /NEEDS_INPUT/);
      assert.deepEqual(correctionSnapshot(s), before);
      updateFact(s, p.id, "amount", 34100);
      assert.equal(p.kg, 1100);
      assert.equal(p.price, 31);
    }
    assert.equal(canConfirm(p), true);
    confirm(s, p.id, p.revision);
    assert.equal(s.activity.at(-1).kg, resolution === "comment" ? 1100 : 1200);
    assert.equal(s.activity.at(-1).revenue, resolution === "comment" ? 34100 : 37200);
    assert.equal(p.source, source);
    assert.deepEqual(s.sources, evidence);
  }
});

test("R3 design lock manually edited task prose requires review after a subsequent place or time correction", () => {
  for (const [key, value] of [["place", "İmamlı"], ["when", "Завтра"]]) {
    const s = initialState();
    s.draft.text = SCENARIO_TEXT;
    const before = correctionSnapshot(s);
    const p = prepare(s);
    const evidence = structuredClone(s.sources);
    updateFact(s, p.id, "instruction", "Проверить насос");
    assert.equal(p.instructionEdited, true);
    assert.equal(canConfirm(p), true);
    updateFact(s, p.id, key, value);
    assert.equal(p[key], value);
    assert.equal(p.instruction, "Проверить насос");
    assert.equal(p.actionNeedsReview, true);
    assert.equal(canConfirm(p), false);
    assert.throws(() => confirm(s, p.id, p.revision), /NEEDS_INPUT/);
    assert.deepEqual(correctionSnapshot(s), before);
    assert.equal(p.source, SCENARIO_TEXT);
    assert.deepEqual(s.sources, evidence);
    updateFact(s, p.id, "instruction", "Проверить насос");
    assert.equal(p.actionNeedsReview, false);
    assert.equal(canConfirm(p), true);
    confirm(s, p.id, p.revision);
    assert.equal(s.tasks[0].title, "Проверить насос");
    assert.equal(s.tasks[0][key], value);
    assert.equal(s.tasks[0].source, SCENARIO_TEXT);
    assert.deepEqual(s.sources, evidence);
  }
});

test("R3 design lock explicit saved-task editing cannot mutate the same-ID confirmed proposal", () => {
  const s = initialState();
  s.draft.text = SCENARIO_TEXT;
  const p = prepare(s);
  confirm(s, p.id, p.revision);
  const closedProposal = structuredClone(p);
  const task = s.tasks.find((x) => x.id === p.id);
  assert.ok(task);
  assert.notEqual(task, p);
  updateFact(s, task.id, "place", "İmamlı", "task");
  assert.equal(task.place, "İmamlı");
  assert.deepEqual(p, closedProposal);
  updateFact(s, task.id, "when", "Завтра", "task");
  assert.equal(task.when, "Завтра");
  assert.equal(task.source, SCENARIO_TEXT);
  assert.deepEqual(p, closedProposal);
  const before = structuredClone(s);
  assert.throws(() => updateFact(s, p.id, "place", "Yeşilöz"), /READ_ONLY/);
  assert.deepEqual(s, before);
  const storage = correctionStorage();
  assert.equal(persist(storage, s), true);
  const restored = loadState(storage);
  assert.deepEqual(restored.proposals.find((x) => x.id === p.id), closedProposal);
  assert.equal(restored.tasks.find((x) => x.id === p.id).place, "İmamlı");
  assert.equal(restored.tasks.find((x) => x.id === p.id).when, "Завтра");
});

test("R3 design lock saved-task clarification hides stale prose until the owner reviews the action", () => {
  const s = initialState();
  s.draft.text = SCENARIO_TEXT;
  const p = prepare(s);
  confirm(s, p.id, p.revision);
  const proposal = structuredClone(p);
  const task = s.tasks.find((x) => x.id === p.id);
  updateFact(s, task.id, "title", "Проверить насос на Озвади", "task");
  assert.match(TaskRow(task), /Проверить насос на Озвади/);
  updateFact(s, task.id, "place", "İmamlı", "task");
  assert.equal(task.actionNeedsReview, true);
  assert.equal(task.title, "Проверить насос на Озвади");
  assert.match(TaskRow(task), /Уточнить текст действия/);
  assert.match(TaskRow(task), /İmamlı/);
  assert.doesNotMatch(TaskRow(task), /Проверить насос|Озвади/);
  assert.deepEqual(p, proposal);
  updateFact(s, task.id, "title", "Проверить насос", "task");
  assert.equal(task.actionNeedsReview, false);
  assert.match(TaskRow(task), /Проверить насос/);
  assert.doesNotMatch(TaskRow(task), /Уточнить текст действия|Озвади/);
  assert.equal(task.source, SCENARIO_TEXT);
  assert.deepEqual(p, proposal);

  const app = fs.readFileSync("prototype/owner-r3/app.js", "utf8");
  const doneHandler = app.slice(app.indexOf('if (action.startsWith("task-done:"))'), app.indexOf('if (action === "choose-worker")'));
  assert.match(doneHandler, /if\s*\(!t\s*\|\|\s*t\.actionNeedsReview\)\s*return;/);
  assert.ok(doneHandler.indexOf("t.actionNeedsReview") < doneHandler.indexOf("t.status ="));
});

test("R3 design lock changed or blank caption cannot reuse a stale explicit contradiction scenario", () => {
  for (const comment of ["", "Вес верный, нужно уточнить дату", "На чеке должно быть 900 кг."]) {
    const s = initialState();
    const before = correctionSnapshot(s);
    const original = s.proposals.find((x) => x.id === "depot-review").source;
    s.draft = receiptDraft(comment, "receipt-conflict");
    const p = prepare(s);
    assert.equal(p.id, "depot-review");
    assert.equal(Boolean(p.captionConflict), false);
    assert.equal(p.kg, 1200);
    assert.equal(p.price, 31);
    assert.equal(p.amount, 37200);
    assert.equal(p.source, original);
    assert.equal(s.sources[0].comment, comment);
    assert.equal(canConfirm(p), true);
    assert.deepEqual(correctionSnapshot(s), before);
    assert.throws(() => resolveCaption(s, p.id, "comment"), /INVALID_RESOLUTION/);
    assert.equal(p.kg, 1200);
  }
  const app = fs.readFileSync("prototype/owner-r3/app.js", "utf8");
  assert.match(app, /state\.draft\.scenario\s*===\s*"receipt-conflict"\s*&&\s*el\.value\s*!==\s*CAPTION_CONFLICT\)\s*state\.draft\.scenario\s*=\s*"receipt"/);
});
