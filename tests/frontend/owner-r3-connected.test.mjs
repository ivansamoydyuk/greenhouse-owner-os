import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import {
  ConnectedAPI,
  ApiError,
  cents,
  decimal,
  exactMoney,
  quantity,
  paymentPayload,
  totalByCurrency,
  CAPABILITIES,
  canRecoverSourceExpense,
  submissionSnapshot,
} from "../../prototype/owner-r3/connected-adapter.js";
class Store {
  constructor() {
    this.m = new Map();
  }
  async get(k) {
    return structuredClone(this.m.get(k));
  }
  async claim(v) {
    const old = this.m.get(v.key);
    if (old && old.state !== "REJECTED") return structuredClone(old);
    await this.put(v);
    return structuredClone(v);
  }
  async put(v) {
    this.m.set(v.key, structuredClone(v));
  }
  async all() {
    return structuredClone([...this.m.values()]);
  }
  async delete(k) {
    this.m.delete(k);
  }
}
const config = {
  entity_id: "entity",
  owner_id: "owner",
  environment: "TEST_PREVIEW",
  feature_sha: "a".repeat(40),
  backend_connected: true,
  actual_operations: false,
};
const response = (data, status = 200) => ({
  ok: status >= 200 && status < 300,
  status,
  json: async () => data,
});
const create = (fetcher, store = new Store(), onAuth = () => {}) => {
  const a = new ConnectedAPI({ fetcher, store, onAuth });
  a.configure(config);
  return a;
};
for (const [input, expected] of [
  ["1", "1.00"],
  ["12,31", "12.31"],
  ["999999999999999999.99", "999999999999999999.99"],
])
  test("exact money " + input, () => assert.equal(exactMoney(input), expected));
for (const value of ["0", "-1", "1.001", "1e3", "NaN", "abc"])
  test("reject invalid money " + value, () =>
    assert.throws(() => exactMoney(value)),
  );
test("sum by currency never mixes currencies or closed debts", () =>
  assert.deepEqual(
    totalByCurrency(
      [
        {
          direction: "payable",
          settlement_status: "open",
          currency: "TRY",
          remaining_amount: "1.12",
        },
        {
          direction: "payable",
          settlement_status: "partial",
          currency: "TRY",
          remaining_amount: "2.33",
        },
        {
          direction: "payable",
          settlement_status: "open",
          currency: "EUR",
          remaining_amount: "7.00",
        },
        {
          direction: "payable",
          settlement_status: "settled",
          currency: "EUR",
          remaining_amount: "9.00",
        },
      ],
      "payable",
    ),
    { TRY: "3.45", EUR: "7.00" },
  ));
const obligations = [
  {
    obligation_id: "o1",
    counterparty_id: "p",
    currency: "TRY",
    direction: "payable",
    remaining_amount: "10.00",
  },
  {
    obligation_id: "o2",
    counterparty_id: "p",
    currency: "TRY",
    direction: "payable",
    remaining_amount: "20.00",
  },
];
const opts = {
  entity: "e",
  party: "p",
  direction: "out",
  currency: "TRY",
  account: { id: "a", currency: "TRY" },
  amount: "12.00",
  date: "2026-10-10",
  allocations: [
    { obligation_id: "o1", amount: "5.00" },
    { obligation_id: "o2", amount: "7.00" },
  ],
  obligations,
};
test("explicit multiple document allocations exact", () => {
  const p = paymentPayload(opts);
  assert.equal(p.amount, "12.00");
  assert.equal(p.allocations.length, 2);
  assert.equal(p.money_account_id, "a");
});
for (const [label, change] of [
  ["unallocated remainder", { amount: "13.00" }],
  ["overallocated", { amount: "11.00" }],
  ["wrong account currency", { account: { id: "a", currency: "USD" } }],
  ["wrong party", { party: "wrong" }],
  ["wrong direction", { direction: "in" }],
  [
    "over remaining",
    {
      amount: "22.00",
      allocations: [
        { obligation_id: "o1", amount: "15.00" },
        { obligation_id: "o2", amount: "7.00" },
      ],
    },
  ],
  [
    "duplicate doc",
    {
      allocations: [
        { obligation_id: "o1", amount: "6.00" },
        { obligation_id: "o1", amount: "6.00" },
      ],
    },
  ],
])
  test("payment refuses " + label, () =>
    assert.throws(() => paymentPayload({ ...opts, ...change })),
  );
test("large amount round trips without binary floats", () =>
  assert.equal(decimal(cents("9007199254740999.13")), "9007199254740999.13"));
test("quantity uses exact decimal and unknown remains explicit", () => {
  assert.equal(quantity("0,125"), "0.125");
  assert.throws(() => quantity("0"));
  assert.equal(CAPABILITIES.fertilizer, "DEMO_ONLY");
});
test("double click coalesces same mutation and retains ACK", async () => {
  let calls = 0;
  const a = create(async () => {
    calls++;
    await new Promise((r) => setTimeout(r, 10));
    return response({ id: "one" });
  });
  const [x, y] = await Promise.all([
    a.mutate("/v1/payments", { amount: "1" }, "intent"),
    a.mutate("/v1/payments", { amount: "1" }, "intent"),
  ]);
  assert.deepEqual(x, y);
  assert.equal(calls, 1);
  assert.equal((await a.pendingRequests()).length, 0);
});
test("lost response retry after reload retains exact original key and body", async () => {
  const store = new Store(),
    calls = [];
  let commitCount = 0;
  const keys = new Set();
  const a = create(async (path, o) => {
    calls.push(o);
    if (!keys.has(o.headers["Idempotency-Key"])) {
      keys.add(o.headers["Idempotency-Key"]);
      commitCount++;
    }
    throw Error("response lost after commit");
  }, store);
  await assert.rejects(a.mutate("/v1/payments", { amount: "12.00" }, "intent"));
  const b = create(async (path, o) => {
    calls.push(o);
    if (!keys.has(o.headers["Idempotency-Key"])) {
      keys.add(o.headers["Idempotency-Key"]);
      commitCount++;
    }
    return response({ id: "saved" });
  }, store);
  assert.equal(
    (await b.mutate("/v1/payments", { amount: "12.00" }, "intent")).id,
    "saved",
  );
  assert.equal(commitCount, 1);
  assert.equal(calls[0].body, calls[1].body);
  assert.equal(
    calls[0].headers["Idempotency-Key"],
    calls[1].headers["Idempotency-Key"],
  );
});
test("changed pending payment payload is blocked before second request", async () => {
  let calls = 0;
  const a = create(async () => {
    calls++;
    throw Error("lost");
  });
  await assert.rejects(a.mutate("/v1/payments", { amount: "1" }, "same"));
  await assert.rejects(
    a.mutate("/v1/payments", { amount: "2" }, "same"),
    /Предыдущая отправка/,
  );
  assert.equal(calls, 1);
});
test("definitively rejected validation may be corrected", async () => {
  let calls = 0;
  const a = create(async () =>
    ++calls === 1
      ? response({ error: { message: "bad" } }, 422)
      : response({ id: "ok" }),
  );
  await assert.rejects(a.mutate("/v1/payments", { amount: "x" }, "same"));
  assert.equal(
    (await a.mutate("/v1/payments", { amount: "2" }, "same")).id,
    "ok",
  );
});
test("secondary 503 does not expire session", async () => {
  let auth = 0;
  const a = create(
    async () => response({}, 503),
    new Store(),
    () => auth++,
  );
  await assert.rejects(a.get("/inventory"));
  assert.equal(auth, 0);
});
test("401 expires session but preserves pending original", async () => {
  let auth = 0;
  const store = new Store(),
    a = create(
      async () => response({}, 401),
      store,
      () => auth++,
    );
  await assert.rejects(
    a.entityPost("/owner/captures", { text: "original" }, "capture"),
  );
  assert.equal(auth, 1);
  assert.equal((await a.pendingRequests())[0].body.text, "original");
});
test("storage failure prevents network write and cannot falsely ACK", async () => {
  let calls = 0;
  const store = new Store();
  store.put = async () => {
    throw Error("quota");
  };
  const a = create(async () => {
    calls++;
    return response({});
  }, store);
  await assert.rejects(a.mutate("/write", {}, "i"), /quota/);
  assert.equal(calls, 0);
});
test("scope prevents reading another owner pending records", async () => {
  const a = create(async () => {
    throw Error("offline");
  });
  await assert.rejects(a.mutate("/write", { text: "private" }, "i"));
  a.configure({ ...config, owner_id: "owner2" });
  assert.equal((await a.records()).length, 0);
});
test("untrusted or incomplete preview config fails closed", () => {
  for (const c of [
    { ...config, backend_connected: false },
    { ...config, actual_operations: true },
    { ...config, owner_id: null },
    { ...config, feature_sha: null },
    { ...config, environment: "production" },
  ])
    assert.throws(() => new ConnectedAPI().configure(c));
});
test("prototype remains demo and connected imports no fixture ledger", () => {
  const source = fs.readFileSync("prototype/owner-r3/connected.js", "utf8");
  for (const forbidden of [
    "demo-adapter.js",
    "fixtures.js",
    "daily-demo.js",
    "confirmAction(",
  ])
    assert.ok(!source.includes(forbidden));
  assert.match(
    fs.readFileSync("prototype/owner-r3/app.js", "utf8"),
    /demo-adapter.js/,
  );
});
test("authority responses check epoch before assigning global current or draft", () => {
  const source = fs.readFileSync("prototype/owner-r3/connected.js", "utf8");
  assert.doesNotMatch(source, /current=await|draft=await/);
  assert.match(
    source,
    /if\s*\(token\s*!==\s*epoch\)\s*return;\s*current\s*=\s*loaded/,
  );
  assert.match(
    source,
    /if\s*\(token\s*!==\s*epoch\)\s*return;\s*draft\s*=\s*loaded/,
  );
});
test("legacy modal title is escaped at innerHTML sink AUD011", () => {
  const source = fs.readFileSync("src/greenhouse_owner_os/web/app.js", "utf8");
  assert.match(source, /<h2 id="sheet-title">\$\{e\(title\)\}/);
  assert.doesNotMatch(source, /<h2 id="sheet-title">\$\{title\}/);
});

test("two clients with one intent cannot send changed payload under a new server identity", async () => {
  const store = new Store(),
    requests = [];
  const transport = async (p, o) => {
    requests.push(o);
    return response({ id: "one" });
  };
  const a = create(transport, store),
    b = create(transport, store);
  const result = await Promise.allSettled([
    a.mutate("/v1/payments", { amount: "1" }, "one"),
    b.mutate("/v1/payments", { amount: "2" }, "one"),
  ]);
  assert.equal(result.filter((x) => x.status === "fulfilled").length, 1);
  assert.equal(requests.length, 1);
});

test("source-only expense recovery requires exhausted preparation and no existing proposal", () => {
  assert.equal(
    canRecoverSourceExpense({
      proposals: [],
      job: { state: "FAILED", attempts: 3 },
    }),
    true,
  );
  for (const source of [
    { proposals: [{ id: "existing" }], job: { state: "FAILED", attempts: 3 } },
    { proposals: [], job: { state: "FAILED", attempts: 2 } },
    { proposals: [], job: { state: "SUCCEEDED", attempts: 3 } },
    { proposals: [], job: { state: "QUEUED", attempts: 0 } },
    { proposals: [], job: { state: "RUNNING", attempts: 1 } },
  ])
    assert.equal(canRecoverSourceExpense(source), false);
});

test("default browser fetch preserves its global receiver", async () => {
  const native = globalThis.fetch;
  let called = false;
  globalThis.fetch = function () {
    assert.equal(
      this,
      globalThis,
      "browser fetch rejects a class instance receiver",
    );
    called = true;
    return Promise.resolve(response({ ok: true }));
  };
  try {
    const api = new ConnectedAPI({ store: new Store() });
    assert.deepEqual(await api.get("/preview/config"), { ok: true });
    assert.equal(called, true);
  } finally {
    globalThis.fetch = native;
  }
});

test("failed reads do not claim an uncertain business commit", async () => {
  const api = create(async () => {
    throw Error("offline");
  });
  await assert.rejects(
    api.get("/preview/config"),
    /Не удалось получить данные/,
  );
  await assert.rejects(
    api.mutate("/write", { amount: "1.00" }, "intent"),
    /Действие могло сохраниться/,
  );
});

test("explicit server conflict keeps immutable request but is not an uncertain pending send", async () => {
  const api = create(async () => response({ detail: "stale revision" }, 409));
  await assert.rejects(
    api.mutate("/confirm", { expected_version: 1 }, "one"),
    /stale revision/,
  );
  assert.equal((await api.records())[0].state, "CONFLICT");
  assert.equal((await api.pendingRequests()).length, 0);
  await assert.rejects(
    api.mutate("/confirm", { expected_version: 2 }, "one"),
    /Предыдущая отправка/,
  );
});

test("submitted facts are isolated from live edits across file and store awaits", async () => {
  const live = {
    draft: { id: "intent", text: "clicked original", allocation_a: "100.00" },
    current: {
      id: "shown",
      version: 3,
      revision: { payload: { amount: "100.00" } },
    },
    route: "capture/text",
  };
  const fields = { text: "clicked original", allocation_a: "100.00" };
  const file = { name: "clicked.png" };
  const snapshot = submissionSnapshot(live, fields, file);
  let release;
  const paused = new Promise((resolve) => {
    release = resolve;
  });
  const sent = (async () => {
    await paused;
    Object.assign(snapshot.draft, snapshot.fields);
    return {
      text: snapshot.draft.text,
      amount: snapshot.draft.allocation_a,
      version: snapshot.current.version,
      file: snapshot.file.name,
      route: snapshot.route,
    };
  })();
  live.draft.text = "later different original";
  live.draft.allocation_a = "999.00";
  live.current.version = 4;
  live.current.revision.payload.amount = "999.00";
  live.route = "another/record";
  fields.text = "later form value";
  release();
  assert.deepEqual(await sent, {
    text: "clicked original",
    amount: "100.00",
    version: 3,
    file: "clicked.png",
    route: "capture/text",
  });
  assert.equal(snapshot.current.revision.payload.amount, "100.00");
});

test("form submission captures values before making its form inert and render readiness is published", () => {
  const source = fs.readFileSync(
    new URL("../../prototype/owner-r3/connected.js", import.meta.url),
    "utf8",
  );
  assert.match(
    source,
    /const snapshot = submissionSnapshot\([\s\S]*?values\(formEl\)[\s\S]*?action\(\(\) => submitForm/,
  );
  assert.match(
    source,
    /const previousInert = node\?\.inert;[\s\S]*?node\.inert = true/,
  );
  assert.match(source, /node\.inert = previousInert/);
  assert.match(
    source,
    /if \(token !== epoch\) return;\n    shell\(body\);\n    document.querySelector\("#main"\)\.dataset.renderedRoute = route/,
  );
});

test("connected expense review keeps facts and effects compact before optional provenance", () => {
  const source = fs.readFileSync(
    new URL("../../prototype/owner-r3/connected.js", import.meta.url),
    "utf8",
  );
  assert.match(source, /function compactExpenseReview/);
  assert.match(source, /basis\[`\$\{table\}:\$\{id\}`\]/);
  assert.match(source, /Общий расход · без указанной теплицы/);
  assert.match(source, /Долг и аванс не создаются/);
  assert.ok(
    source.indexOf("Уточнить один факт") <
      source.indexOf('<details class="review-provenance">'),
  );
  assert.match(source, /irrigation: "Полив"/);
});

test("connected data-action helpers suppress implicit browser form submission", () => {
  const source = fs.readFileSync(
    new URL("../../prototype/owner-r3/connected.js", import.meta.url),
    "utf8",
  );
  const handler = source.slice(
    source.indexOf('document.addEventListener("click"'),
  );
  assert.ok(
    handler.indexOf("event.preventDefault()") <
      handler.indexOf('if (name === "new-product")'),
  );
  assert.ok(
    handler.indexOf("event.preventDefault()") <
      handler.indexOf('if (name === "fill-allocation")'),
  );
  assert.match(source, /type="submit"/);
  assert.ok(
    source.includes(
      'visualButton(...args).replace("<button ", \'<button type="button" \'',
    ),
  );
});
