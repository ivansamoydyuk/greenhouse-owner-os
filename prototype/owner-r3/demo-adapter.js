import { initialState, SCENARIO_TEXT, RECOVERY_TEXT, CAPTION_CONFLICT, harvestRows } from "./fixtures.js";
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
  if (["text", "voice"].includes(d.mode) && !d.file && d.text === SCENARIO_TEXT) {
    p = {
      id,
      type: "task",
      title: "Переделать полив",
      instruction: "Переделать полив",
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
  } else if (["receipt", "receipt-conflict"].includes(d.scenario) && d.file?.fixture) {
    const existing = state.proposals.find((x) => x.id === "depot-review");
    // Each capture is immutable evidence. The prepared document keeps one action identity.
    state.sources.at(-1).proposalId = existing.id;
    if (["pending", "deferred"].includes(existing.status)) existing.revision++;
    if (["pending", "deferred"].includes(existing.status) && d.scenario === "receipt-conflict" && d.comment === CAPTION_CONFLICT) {
      existing.captionConflict = true;
      existing.captionResolution = null;
    }
    state.draft = { mode: d.mode, text: "", comment: "", file: null, scenario: null };
    return existing;
  } else if (["text", "voice"].includes(d.mode) && !d.file && d.scenario === "recovery" && d.text === RECOVERY_TEXT) {
    p = {
      id, type: "unknown", title: "Нужно одно уточнение", meaning: "Неясно, что произошло",
      source: d.text, party: "TEST Серкан", when: "Завтра", date: null, worker: null,
      recovery: true, scenario: true, status: "pending", revision: 1,
    };
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
  if (p.type === "unknown" || p.captionConflict || p.actionNeedsReview) return false;
  if (p.type === "task") return !!p.instruction?.trim();
  if (p.type === "stock") return Number.isFinite(p.quantity) && p.quantity > 0 && !!p.product && ["кг", "л"].includes(p.unit) && !!p.place;
  if (p.type === "harvest") return [p.kg, p.price, p.amount].every(v => Number.isFinite(v) && v > 0) && Math.round(p.kg * p.price * 100) === Math.round(p.amount * 100);
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
      party: p.party,
      instructionEdited: p.instructionEdited,
    });
  if (p.type === "stock") {
    let row = state.stock.find((x) => x.name === p.product && x.unit === p.unit && x.place === p.place);
    if (!row) {
      row = { id: "demo-stock-" + p.id, name: p.product, category: "Удобрения", quantity: null,
        unit: p.unit, place: p.place, cost: null, movements: [] };
      state.stock.push(row);
    }
    if (row.quantity != null) row.quantity += p.quantity;
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
export function updateFact(state, id, key, value, target = "proposal") {
  const p = target === "task" ? state.tasks.find((x) => x.id === id) :
    state.proposals.find((x) => x.id === id) || state.tasks.find((x) => x.id === id);
  if (!p) throw Error("Missing card");
  if (state.proposals.includes(p) && !["pending", "deferred"].includes(p.status)) throw Error("READ_ONLY");
  const allowed = ["amount", "place", "date", "when", "title", "instruction", "quantity", "unit", "product", "kg", "price"];
  if (!allowed.includes(key)) throw Error("IMMUTABLE_SOURCE");
  if (["amount", "price", "quantity", "kg"].includes(key) && (!Number.isFinite(value) || value <= 0)) throw Error("INVALID_FACT");
  if (["product", "unit", "instruction", "title"].includes(key) && !String(value).trim()) throw Error("INVALID_FACT");
  if (key === "unit" && !["кг", "л"].includes(value)) throw Error("INVALID_FACT");
  p[key] = value;
  if (key === "product" || key === "instruction") p.title = value;
  if (["instruction", "title"].includes(key)) { p.instructionEdited = true; p.actionNeedsReview = false; }
  if (["place", "when"].includes(key) && p.instructionEdited) p.actionNeedsReview = true;
  if (key === "when") p.date = null;
  p.revision = (p.revision || 0) + 1;
  return p;
}
export function transcriptText(results) {
  return Array.from(results, (r) => r[0]?.transcript || "")
    .join(" ")
    .trim();
}

// Bounded display projections of selected facts, never a language interpreter.
export function currentAction(p) {
  if (p.type === "task" || (!p.type && p.title)) return [p.actionNeedsReview ? "Уточнить текст действия" : p.instruction || p.title, p.party, p.place, p.when].filter(Boolean).join(" · ");
  if (p.type === "stock") return `Приход: ${p.quantity} ${p.unit} ${p.product} · ${p.place}`;
  if (p.type === "harvest") return `Продажа: ${p.kg} кг × ${p.price} TRY/кг · ${p.amount} TRY · ${p.place}`;
  return p.meaning || p.title;
}
export function parseQuantity(raw) {
  const value = String(raw).replace(/\s/g, "").replace(",", ".");
  if (!/^\d{1,9}(?:\.\d{1,3})?$/.test(value) || Number(value) <= 0) throw Error("INVALID_QUANTITY");
  return Number(value);
}
export function resolveCaption(state, id, resolution) {
  const p = state.proposals.find(p => p.id === id);
  if (!p || !["pending", "deferred"].includes(p.status)) throw Error("READ_ONLY");
  if (!["document", "comment"].includes(resolution) || !p.captionConflict) throw Error("INVALID_RESOLUTION");
  p.captionConflict = false;
  p.captionResolution = resolution;
  p.kg = resolution === "document" ? 1200 : 1100;
  p.revision++;
  return p;
}
export function clarifyIntent(state, id, intent) {
  const p = state.proposals.find(p => p.id === id);
  if (!p || !["pending", "deferred"].includes(p.status)) throw Error("READ_ONLY");
  if (!p.recovery || p.type !== "unknown" || intent !== "reminder") throw Error("INVALID_INTENT");
  p.type = "task";
  p.instruction = "Связаться с Серканом";
  p.title = p.instruction;
  p.revision++;
  return p;
}
