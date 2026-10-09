import { initialState, SCENARIO_TEXT, harvestRows } from "./fixtures.js";
export const STORAGE_KEY = "greenhouse-owner-r3-demo-v1";
export const pending = (s) =>
  s.proposals.filter((p) => ["pending", "deferred"].includes(p.status));
export const moneyTotal = (rows) =>
  rows.reduce((s, x) => s + Math.round(x.amount * 100), 0) / 100;
export function parseAmount(raw) {
  const normalized = raw.replace(/\s/g, "").replace(",", ".");
  if (!/^\d{1,9}(?:\.\d{1,2})?$/.test(normalized))
    throw Error("INVALID_AMOUNT");
  const [whole, fraction = ""] = normalized.split(".");
  const cents = Number(whole) * 100 + Number(fraction.padEnd(2, "0"));
  if (cents <= 0) throw Error("INVALID_AMOUNT");
  return cents / 100;
}
export function harvestMetrics(rows = harvestRows) {
  const sum = (k) => rows.reduce((s, r) => s + r[k], 0);
  const kg = sum("kg"),
    revenue = sum("revenue"),
    countedKg = sum("countedKg"),
    bunches = sum("bunches");
  return {
    kg,
    revenue,
    countedKg,
    bunches,
    price: kg ? revenue / kg : null,
    bunchWeight: bunches ? countedKg / bunches : null,
    quality: [0, 1, 2, 3].map((i) =>
      rows.reduce((s, r) => s + r.quality[i], 0),
    ),
  };
}
export function loadState(storage) {
  try {
    const x = JSON.parse(storage?.getItem(STORAGE_KEY));
    return x?.version === 1 ? x : initialState();
  } catch {
    return initialState();
  }
}
export function persist(storage, state) {
  try {
    storage?.setItem(STORAGE_KEY, JSON.stringify(state));
    return true;
  } catch {
    return false;
  }
}
export function prepare(state) {
  const d = state.draft;
  if (!d.text.trim() && !d.file) throw Error("Добавьте текст или источник.");
  const id = "capture-" + (state.sources.length + 1);
  const source = {
    id,
    text: d.text,
    file: d.file,
    comment: d.comment,
    scenario: d.scenario,
  };
  state.sources.push(structuredClone(source));
  let p;
  if (d.text === SCENARIO_TEXT) {
    p = {
      id,
      type: "task",
      title: "Переделать полив",
      instruction: SCENARIO_TEXT,
      place: "Özvadi",
      when: "Следующая неделя",
      date: null,
      worker: null,
      category: "Полив",
      source: d.text,
      status: "pending",
      revision: 1,
      scenario: true,
    };
  } else if (d.scenario === "receipt" && d.file?.fixture) {
    const existing = state.proposals.find((x) => x.id === "depot-review");
    state.sources.pop();
    if (!state.sources.some((s) => s.id === "demo-receipt")) {
      state.sources.push({ ...structuredClone(source), id: "demo-receipt" });
    }
    state.draft = {
      mode: d.mode,
      text: "",
      comment: "",
      file: null,
      scenario: null,
    };
    return existing;
  } else {
    p = {
      id,
      type: "unknown",
      title: "Источник сохранён в демо",
      meaning: "Нужна ручная проверка",
      source: d.text || d.file.name,
      image: d.file?.kind === "image" ? d.file.url : null,
      status: "pending",
      revision: 1,
      scenario: false,
    };
  }
  state.proposals.unshift(p);
  state.draft = {
    mode: d.mode,
    text: "",
    comment: "",
    file: null,
    scenario: null,
  };
  return p;
}
export function canConfirm(p) {
  if (!p || !["pending", "deferred"].includes(p.status)) return false;
  if (p.type === "payable") return Number.isFinite(p.amount) && p.amount > 0;
  if (p.type === "unknown") return false;
  return true;
}
export function confirm(state, id, revision) {
  const p = state.proposals.find((x) => x.id === id);
  if (!p || p.revision !== revision) throw Error("STALE");
  if (!canConfirm(p)) throw Error("NEEDS_INPUT");
  p.status = "confirmed";
  if (p.type === "payable")
    state.payables.push({
      id: p.id,
      name: p.title,
      amount: p.amount,
      meaning: p.meaning,
      date: "Срок не указан",
    });
  if (p.type === "task")
    state.tasks.unshift({
      id: p.id,
      title: p.instruction,
      place: p.place,
      when: p.when,
      date: p.date ?? null,
      worker: null,
      status: "todo",
      source: p.source,
      category: p.category,
    });
  if (p.type === "stock") {
    const row = state.stock.find((x) => x.id === "kno3");
    row.quantity += p.quantity;
    row.movements.push({
      id: p.id,
      label: "Демо-подтверждённый приход",
      quantity: p.quantity,
      date: "9 октября",
    });
  }
  if (p.type === "harvest") {
    state.receivables[0].amount += p.amount;
    state.activity.push({
      id: p.id,
      type: "harvest",
      kg: p.kg,
      revenue: p.amount,
      place: p.place,
    });
  }
  state.revision++;
  return p;
}
export function currentHarvestRows(state) {
  const rows = structuredClone(harvestRows);
  for (const x of state.activity.filter((x) => x.type === "harvest")) {
    const r = rows.find((r) => r.name === x.place);
    r.kg += x.kg;
    r.revenue += x.revenue;
    r.quality[3] += x.kg;
  }
  return rows;
}
export function updateFact(state, id, key, value) {
  const p =
    state.proposals.find((x) => x.id === id) ||
    state.tasks.find((x) => x.id === id);
  if (!p) throw Error("Missing card");
  if (state.proposals.includes(p) && !["pending", "deferred"].includes(p.status)) throw Error("READ_ONLY");
  p[key] = value;
  if (key === "when") p.date = null;
  p.revision = (p.revision || 0) + 1;
  return p;
}
export function transcriptText(results) {
  return Array.from(results, (r) => r[0]?.transcript || "")
    .join(" ")
    .trim();
}
