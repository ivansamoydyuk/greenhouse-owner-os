import {historySelection,historyRoute,historyQuery,purchaseHistoryView,purchaseDetailView,stockEventView,invoiceTargetMatches} from "./purchases-connected.js";
import { financeAnalyticsView, financeDocumentView, financeSelection, financeRoute, financeQuery, financeBackRoute } from "./finance-connected.js";
import { monthlyHarvestView, harvestSelection, harvestQuery, harvestRoute } from "./harvest-connected.js";
import {
  e,
  icon,
  btn as visualButton,
  link,
  Header,
  Navigation,
  CaptureBar,
  EmptyState,
} from "./components.js";
import {
  ConnectedAPI,
  exactMoney,
  quantity,
  cents,
  decimal,
  totalByCurrency,
  paymentPayload,
  fileSource,
  CAPABILITIES,
  canRecoverSourceExpense,
  submissionSnapshot,
  estimateConfirmation,
  exactDecimalString,
  ownerFormSource,
} from "./connected-adapter.js";
import {
  proposalSummary,
  displayQuantity as legacyDisplayQuantity,
  displayMetric as legacyDisplayMetric,
} from "/assets/owner-r2.js";
import { money, displayDate, localDay } from "/assets/core.js";
import {
  expenseContext,
  expenseEffectText,
} from "/assets/owner-expense-recovery.js";
const app = document.querySelector("#app"),
  sheetRoot = document.querySelector("#sheet-root");
document.body.classList.add("connected");
// Action controls are never implicit form submissions. submit() is explicit.
const displayQuantity = (value) =>
  legacyDisplayQuantity(exactDecimalString(value));
const displayMetric = (value) => legacyDisplayMetric(exactDecimalString(value));
const btn = (...args) =>
  visualButton(...args).replace("<button ", '<button type="button" ');
let activeActionRoute = null;
let estimateReview = null;
let lastHarvestRoute = "harvest";
let lastFinanceRoute = "money";
const harvestScroll = new Map();
let config = null,
  ready = false,
  route = location.hash.slice(1) || "home",
  epoch = 0,
  busy = false,
  current = null,
  data = {},
  failures = {},
  draft = null,
  radial = false,
  readTimer = null;
const api = new ConnectedAPI({
  onAuth: () => {
    ready = false;
    epoch++;
    clearTimeout(readTimer);
    location.assign("/r3/?return=" + encodeURIComponent(location.hash));
  },
});
const title = {
  DIRECT_EXPENSE: "Расход",
  PAYABLE: "Долг",
  PAYMENT: "Оплата",
  STOCK_RECEIPT: "Товар привезли",
  STOCK_APPLICATION: "Фактическое внесение",
  PERSONAL_TASK: "Личная задача",
  WORKER_TASK: "Поручение работнику",
  DEPOT_RECEIPT: "Чек депо",
  UNRESOLVED: "Уточнить событие",
  INVOICE_MATCH: "Счёт к приходу",
};
const operationLabels = {
  payment: "Платёж",
  owner_funding: "Взнос владельца",
  receipt: "Приход",
  transfer: "Перемещение",
  application: "Внесение",
  writeoff: "Списание",
  plan: "План",
};
const statuses = {
  open: "Открыто",
  partial: "Частично оплачено",
  settled: "Погашено",
  READY: "Готово к проверке",
  NEEDS_INPUT: "Нужно уточнить",
  POSTED: "Подтверждено",
  CONFIRMED_NONPOSTING: "Подтверждено без проводки",
  DEFERRED: "Отложено",
  REJECTED: "Отклонено",
  QUEUED: "В очереди",
  RUNNING: "Подготовка",
  SUCCEEDED: "Подготовлено",
  FAILED: "Подготовка не завершена",
  TO_DO: "Активна",
  DONE: "Завершена",
  draft: "Черновик",
  posted: "Подтверждено",
  assigned: "Назначено",
  reported: "Есть отчёт",
  completed: "Завершено",
  reopened: "Возвращено в работу",
  cancelled: "Отменено",
  reversed: "Проведено обратное событие",
};
const fact = (name, value) =>
  `<div class="fact-row"><dt>${e(name)}</dt><dd>${e(value ?? "UNKNOWN")}</dd></div>`;
const facts = (values) =>
  `<dl class="fact-list">${values.map((x) => fact(...x)).join("")}</dl>`;
const notice = (text, cls = "") => `<p class="notice ${cls}">${e(text)}</p>`;
const panel = (body, cls = "") =>
  `<section class="card ${cls}">${body}</section>`;
const field = (label, name, value = "", type = "text", extra = "") =>
  `<label>${e(label)}<input name="${e(name)}" type="${type}" value="${e(value ?? "")}" ${extra}></label>`;
const area = (label, name, value = "", extra = "") =>
  `<label>${e(label)}<textarea name="${e(name)}" ${extra}>${e(value ?? "")}</textarea></label>`;
const select = (label, name, items, value = "", empty = "Выберите") =>
  `<label>${e(label)}<select name="${e(name)}" required><option value="">${e(empty)}</option>${items.map((x) => `<option value="${e(x.value ?? x.id)}" ${String(x.value ?? x.id) === String(value) ? "selected" : ""}>${e(x.label ?? x.name ?? x.original_name ?? x.display_name)}</option>`).join("")}</select></label>`;
const fileField = (label = "Фото или PDF · оригинал") =>
  `<label>${e(label)}<input type="file" name="file" accept="image/jpeg,image/png,application/pdf"></label>`;
const submit = (label = "Проверить") =>
  `<div class="confirmation-bar"><button class="btn primary full" type="submit">${e(label)}</button></div>`;
const form = (id, body, label) =>
  `<form data-form="${id}" novalidate>${body}<div class="form-error" role="alert"></div>${submit(label)}</form>`;
const stateTag = (state) =>
  `<span class="tag ${["NEEDS_INPUT", "draft", "DEFERRED"].includes(state) ? "amber" : ""}">${e(statuses[state] || state)}</span>`;
const refName = (id) => {
  const r =
    data.home?.finance?.references?.find((x) => x.id === id) ||
    data.refs?.find?.((x) => x.id === id);
  return r?.original_name || r?.name || r?.canonical_key || id || "Не указан";
};
const accounts = () => data.home?.finance?.accounts || [];
const accountName = (a) =>
  a.name || a.account_name || refName(a.money_account_id || a.id);
const areas = () => data.workers?.areas || data.home?.finance?.work_areas || [];
const entityPath = (p) => `${api.root}${p}`;
const errorBox = (error) =>
  panel(
    `<h2>Не удалось загрузить</h2><p class="form-error" role="alert">${e(error.message)}</p>${btn("Повторить", "refresh", "primary")}`,
  );
function toast(message) {
  const el = document.querySelector("#toast");
  el.textContent = message;
  el.classList.add("show");
  setTimeout(() => el.classList.remove("show"), 3500);
}
function shell(body, focused = false) {
  const section = route.split("/")[0];
  app.innerHTML = `<div class="shell"><div class="demo-strip connected-mode">CONNECTED TEST · синтетические данные · ${e(config?.feature_sha ? config.feature_sha.slice(0, 8) : "сборка не подтверждена")}</div><main id="main" tabindex="-1">${body}</main>${focused ? "" : `<div class="radial-backdrop" data-action="radial-close" hidden></div><footer class="dock">${CaptureBar().replace("Источник сохраняется в DEMO", "Источник сохраняется в TEST PostgreSQL")}${Navigation(section === "personal" ? "tasks" : section, data.home?.confirmations?.n || 0)}</footer>`}</div>`;
  setRadial(false);
}
function setRadial(open) {
  radial = open;
  document.querySelector("#radial-menu")?.toggleAttribute("hidden", !open);
  document.querySelector(".radial-backdrop")?.toggleAttribute("hidden", !open);
  document
    .querySelector(".capture-toggle")
    ?.setAttribute("aria-expanded", String(open));
}
function go(to) {
  if (location.hash.slice(1) === to) return render();
  location.hash = to;
}
async function refreshData() {
  const generation = api.generation;
  const names = {
    home: "/owner/home",
    inventory: "/inventory",
    harvest: "/harvest",
    workers: "/tasks",
    queue: "/owner/confirmations",
    sources: "/owner/captures",
  };
  await Promise.all(
    Object.entries(names).map(async ([key, path]) => {
      try {
        const result = await api.entityGet(path);
        if (generation !== api.generation) return;
        data[key] = result;
        delete failures[key];
      } catch (error) {
        if (generation !== api.generation) return;
        failures[key] = error;
        data[key] = null;
      }
    }),
  );
}
function unavailable(name) {
  return panel(
    `<h2>${e(name)}</h2>${notice("Раздел временно недоступен. Другие данные остаются на экране.", "amber")}${btn("Повторить загрузку", "refresh")}`,
  );
}
function metric(label, values, to, primary = false) {
  const list = Object.entries(values || {});
  const longest = Math.max(
    0,
    ...list.map(([, value]) => displayQuantity(value).length),
  );
  const amountClass =
    longest > 19
      ? "long-money very-long-money"
      : longest > 9
        ? "long-money"
        : "";
  return link(
    `<span class="metric-label">${icon(primary ? "down" : "up")}${label}</span>${list.length ? list.map(([c, v]) => `<strong class="metric-value">${e(displayQuantity(v))}</strong><span class="currency">${e(c)}</span>`).join("") : '<strong class="metric-value no-value">Нет долгов</strong>'}<span class="metric-detail">Подтверждённые документы ${icon("arrow")}</span>`,
    to,
    `metric-card ${primary ? "mint" : ""} ${amountClass}`,
    primary ? "home-receivable" : "home-payable",
  );
}
function taskRows(items, worker = false) {
  return items
    .map((t) =>
      link(
        `<strong>${e(t.title)}</strong><small>${e(t.planned_date ? displayDate(t.planned_date) : t.relative_time_context || "Дата не указана")} · ${e(statuses[t.state || t.status] || t.state || t.status)}</small>`,
        `${worker ? "worker-task" : "personal"}/${t.id}`,
        "list-row",
      ),
    )
    .join("");
}
function home() {
  const h = data.home;
  if (!h) return unavailable("Главная");
  const f = h.finance,
    s = h.season || [];
  return `<header class="page-header home-header"><div><p class="greeting">Ваше TEST-хозяйство</p><h1>Добрый день</h1><div class="home-date">${icon("calendar")}${e(displayDate(localDay()))}</div></div><span class="brand-mark">${icon("leaf")}</span></header>${Object.keys(failures).length ? notice("Часть разделов не загрузилась: " + Object.keys(failures).join(", ") + ". Главная показывает полученный ответ сервера.", "amber") + btn("Повторить загрузку разделов", "refresh", "secondary") : ""}<div class="home-layout"><div><div class="daily-actions">${link(`${icon("wallet")} Расход`, "capture/expense", "daily-action", "home-expense")}${link(`${icon("harvest")} Сбор урожая`, "quick/harvest", "daily-action", "home-cut")}</div><div class="attention-area">${link(`<span class="attention-icon">${icon("review")}</span><div><strong>Нужно подтвердить</strong><p>Подготовленные источники</p></div><span class="count">${h.confirmations.n}</span>`, "review", "attention", "home-review")}${(h.worker_exceptions || []).map((t) => link(`${icon("warning")}<span>${e(t.title)}</span>${icon("arrow")}`, `worker-task/${t.id}`, "attention-worker")).join("")}</div><div class="financial-grid">${metric("Мне должны", totalByCurrency(f.obligations, "receivable"), "finance/receivable", true)}${metric("Я должен", totalByCurrency(f.obligations, "payable"), "finance/payable")}</div>${panel(`<div class="card-header"><h2>Деньги и результат</h2>${link("Открыть",lastFinanceRoute,"section-link")}</div><p class="note">Счета, движения, учтённые затраты и теплицы</p>`)}${panel(`<div class="card-header"><h2>Урожай сезона</h2>${link("Открыть", lastHarvestRoute, "section-link")}</div>${s.length ? s.map((x) => `<div class="harvest-head"><div><span class="eyebrow">Подтверждено в TEST · депо</span><strong class="harvest-kpi">${e(displayQuantity(x.kg))} <small>кг</small></strong></div></div><div class="harvest-stat-grid"><div><span class="label">Средний вес по ADET</span><strong>${e(displayMetric(x.average_bunch_weight))} кг</strong><small class="coverage">${e(displayQuantity(x.count_covered_kg))} кг / ${e(x.count ?? "UNKNOWN")} ADET</small></div><div><span class="label">Средняя цена</span><strong>${e(displayMetric(x.average_price))} ${e(x.currency)}/кг</strong><small class="coverage">Выручка / подтверждённый вес</small></div></div><p class="note">${x.count_coverage_complete ? "Покрытие количества полное." : "Покрытие количества частичное. Неизвестные ветки не считаются нулём."}</p>`).join("") : notice("Подтверждённых чеков депо в сезоне пока нет. Полевые срезы хранятся отдельно.")}<p class="note">${e(h.season_start)} — ${e(h.season_end)}</p>`)}</div><div><div class="section-heading"><h2>Мои задачи <span class="badge-number">${h.task_count}</span></h2>${link("Все", "tasks")}</div>${panel(taskRows(h.tasks) || EmptyState("Пока пусто", "Личные задачи появятся после подтверждения."))}<div class="section-heading"><h2>Работники</h2>${link("Открыть", "workers")}</div>${panel(h.worker_exceptions.length ? taskRows(h.worker_exceptions, true) : "<p>Нет необработанных сообщений о проблеме.</p>")}<p class="note">Workers R1 · внешняя отправка недоступна.</p></div></div>`;
}
function reviewList() {
  return (
    Header("Проверка", "Источники и бизнес-эффекты проверяются отдельно") +
    (data.queue
      ? `<div class="page-actions">${link("Исходники", "sources", "btn secondary")}</div><div class="review-list">${data.queue.items.map((p) => panel(`${stateTag(p.state)}<h2>${e(title[p.action_type] || p.action_type)}</h2><p>${e(p.revision.payload.description || p.revision.payload.title || p.source.text || p.source.filename || "Источник")}</p>${link("Проверить", `review/${p.id}`, "btn primary full")}`)).join("") || EmptyState("Всё проверено", "Новый источник можно добавить через центральный лист.")}</div>`
      : unavailable("Проверка"))
  );
}
function sourceCard(s) {
  return panel(
    `<div class="status-line">${stateTag(s.state)}<span class="tag">Сохранено на сервере</span></div><p class="original-text">${e(s.text || s.original_name || "Файл")}</p>${link("Открыть источник", `source/${s.id}`, "btn secondary")}`,
  );
}
async function sourceList() {
  const pending = await api.pendingRequests();
  return (
    Header("Исходники", "Оригиналы хранятся отдельно от действий") +
    pending
      .map((r) =>
        panel(
          `<span class="tag amber">Ответ сервера не подтверждён</span><p>${e(r.body.text || r.path)}</p>${btn("Повторить тот же запрос", "retry-request:" + r.key, "secondary")}`,
        ),
      )
      .join("") +
    (data.sources
      ? data.sources.items.map(sourceCard).join("")
      : unavailable("Исходники"))
  );
}
async function getDraft(id, initial = {}) {
  let d = await api.readDraft(id);
  if (!d) {
    d = {
      id: crypto.randomUUID(),
      captured_at: new Date().toISOString(),
      ...initial,
    };
    await api.draft(id, d);
  }
  return d;
}
function captureForm(kind) {
  return (
    Header(
      kind === "expense"
        ? "Быстрый расход"
        : kind === "voice"
          ? "Голос"
          : "Новый источник",
      "Оригинал → подготовка → проверка → подтверждение",
      true,
    ) +
    panel(
      form(
        "capture",
        `${kind === "voice" ? notice("В этом TEST доступен текстовый транскрипт или диктовка клавиатуры. Исходное аудио и новый ASR-провайдер не используются.") : ""}${["photo", "file"].includes(kind) ? fileField() : ""}${area(["photo", "file"].includes(kind) ? "Отдельный комментарий владельца" : "Исходный текст", "text", draft.text, 'maxlength="10000"')}${draft.file?.filename ? notice("Копия на телефоне: " + draft.file.filename) : ""}${notice("До ответа сервера это только копия на телефоне. Подготовка не выполняет проводок.")}`,
        "Сохранить оригинал",
      ),
    )
  );
}
function sourceDetail(s) {
  return (
    Header(
      "Оригинал",
      "Сохранён на сервере · " + (statuses[s.state] || s.state),
      true,
    ) +
    panel(
      `<span class="eyebrow">ОРИГИНАЛ</span><p class="original-text" data-testid="source-original">${e(s.text || "Текст не добавлен")}</p>${s.original_name ? `<p>${e(s.original_name)}</p><a class="btn secondary" href="${entityPath("/owner/captures/" + s.id + "/original")}" target="_blank" rel="noopener">Открыть неизменённый файл</a>` : ""}<p class="note">Источник остаётся доступен после подтверждения и исправления фактов.</p>`,
    ) +
    panel(
      `<h2>Подготовка</h2>${stateTag(s.job?.state || s.state)}<p>${e(s.job?.error_code || "")}</p>${btn("Обновить", "refresh")}${s.retryable ? btn("Повторить подготовку", "prepare:" + s.id) : ""}${(s.proposals || []).map((p) => `<div class="list-row">${link(e((title[p.action_type] || p.action_type || "Открыть предложение") + " · " + (statuses[p.state] || p.state)), `review/${p.id}`, "btn secondary")}</div>`).join("")}${notice("Если это именно оплаченный текущий расход, откройте связанный ручной режим. Долг или аванс расходом не становится.")}${canRecoverSourceExpense(s) ? btn("Оформить прямой расход", "recover-source:" + s.id, "secondary") : s.proposals?.length ? notice("Выберите существующее предложение выше. Ручной расход открывается из выбранного предложения и сохраняет его связь с оригиналом.") : notice("Дождитесь подготовки или повторите её при ошибке. Оригинал уже сохранён.")}`,
    )
  );
}
function factOrigin(p, key) {
  const origin = p.revision.provenance?.[key];
  if (!origin) return "";
  if (origin.category === "SOURCE FACT") return "из исходника";
  if (origin.category === "OWNER INPUT" || origin.category === "OWNER CORRECTION") return "подтверждено владельцем";
  if (origin.category === "LEARNED DEFAULT") return "сохранённое предположение · можно изменить";
  if (["DEFAULT", "CONTEXT DEFAULT"].includes(origin.category) || ["capture_candidate", "visible_context_default"].includes(origin.basis)) return "предложено из контекста · можно изменить";
  if (origin.category === "INFERENCE") return "предложено · можно изменить";
  return "";
}
function formOriginal(evidence) {
  const source = evidence?.structured_original;
  if (!source) return "";
  const names = {event_date:"Дата в форме",original_greenhouse:"Теплица",cut_count:"Количество веток",field_weight_kg:"Полевой вес, кг",reporter:"Сообщил",notes:"Заметка",original_product_name:"Товар",quantity:"Количество",unit:"Единица",location_id:"Идентификатор склада",destination_id:"Идентификатор склада назначения",work_area_id:"Идентификатор теплицы",product_id:"Идентификатор товара"};
  return `<details class="source-provenance" data-testid="structured-original"><summary>Оригинальная форма · значения и источник</summary><p class="note">Форма заполнена владельцем. Это не расшифровка речи.</p>${facts(Object.entries(source.fields).map(([key,value])=>[names[key]||key,value === "" ? "Не указано" : key === "notes" ? "Заметка: " + value : value]))}<p class="note">Источник ${e(source.form_id)} · ${e(source.captured_at)}</p></details>`;
}
function compactExpenseReview(p) {
  const f = p.revision.payload;
  const basis = p.revision.basis || {};
  const reviewedName = (table, id, empty) => {
    if (!id) return empty;
    const saved = basis[`${table}:${id}`];
    return (
      saved?.original_name || saved?.name || saved?.display_name || refName(id)
    );
  };
  const distribution = f.attributions?.length
    ? f.attributions
        .map(
          (a) =>
            `${reviewedName("core.work_areas", a.work_area_id, "Теплица не указана")} · ${money(a.amount, f.currency)}`,
        )
        .join("; ")
    : "Общий расход · без указанной теплицы";
  const effect =
    expenseEffectText(p) ||
    (p.state === "REJECTED"
      ? "Предложение отклонено. Расход по нему не подтверждён."
      : "После подтверждения: запишет оплаченный расход и спишет деньги с выбранного счёта. Долг и аванс не создаются.");
  return panel(
    `${stateTag(p.current_effect?.status || p.state)}<h2>${e(f.description || "За что: нужно уточнить")}</h2><p class="fact-origin">${e(factOrigin(p,"description"))}</p><strong class="exact-value">${e(f.amount == null ? "Сумма не указана" : money(f.amount, f.currency || "Валюта не указана"))}</strong>${facts(
      [
        ["Дата", displayDate(f.event_date, "Не указана") + (factOrigin(p,"event_date") ? " · " + factOrigin(p,"event_date") : "")],
        ["Валюта", (f.currency || "Не указана") + (factOrigin(p,"currency") ? " · " + factOrigin(p,"currency") : "")],
        [
          "Счёт",
          reviewedName(
            "finance.money_accounts",
            f.money_account_id,
            "Не выбран",
          ),
        ],
        [
          "Контрагент",
          reviewedName(
            "core.counterparties",
            f.counterparty_id,
            "Не указан · необязательно",
          ),
        ],
        ["Распределение", distribution],
        ...(needsPaidReview(p) ? [["Факт оплаты", f.payment_purpose === "expense" ? "Подтверждён владельцем" : "Не подтверждён"]] : []),
      ],
    )}${notice(effect)}${(p.revision.missing || []).map((m) => `<p class="form-error">${e(m)}</p>`).join("")}`,
    "compact-expense-review",
  );
}
function needsPaidReview(p) {
  return p.action_type === "DIRECT_EXPENSE" && (p.revision.provenance?.payment_purpose?.basis === "owner_payment_confirmation_required" || p.revision.payload.payment_purpose === "expense" || p.revision.missing?.some(x => x.includes("факт оплаты")));
}
function nextMissingFact(p) {
  if (p.action_type !== "DIRECT_EXPENSE" || !p.revision.missing?.length) return null;
  return ["amount", "description", "currency", "event_date", "money_account_id", "payment_purpose"].find(k => p.revision.payload[k] == null || p.revision.payload[k] === "");
}
function proposalDetail(p) {
  const done = ["POSTED", "CONFIRMED_NONPOSTING"].includes(p.state),
    context = {
      owner: data.home,
      inventory: data.inventory,
      tasks: data.workers,
    };
  return (
    Header(
      title[p.action_type] || "Проверка",
      `Версия ${p.version} · CONNECTED TEST`,
      true,
    ) +
    `<details class="source-provenance"><summary>Оригинал и источник · неизменяемые</summary>` + panel(
      `<span class="eyebrow">ОРИГИНАЛ · неизменяемый</span><p class="original-text" data-testid="source-original">${e(p.source.text || "Текст отсутствует")}</p>${p.source.has_file ? `<a class="btn secondary" href="${entityPath("/owner/captures/" + p.source.id + "/original")}" target="_blank" rel="noopener">${e(p.source.filename || "Открыть исходный файл")}</a>` : ""}`,
    ) + `</details>` +
    (p.action_type === "DIRECT_EXPENSE"
      ? compactExpenseReview(p)
      : p.action_type === "DEPOT_RECEIPT" ? compactDepotProposal(p) : proposalSummary(p, context, false)) +
    (!done && p.state !== "REJECTED" && p.action_type !== "DEPOT_RECEIPT"
      ? panel(
          `<h2>Исправить или уточнить</h2><div class="page-actions fact-actions">${Object.keys(
            p.revision.payload,
          )
            .filter((k) => editableKeys(p).includes(k))
            .map((k) => btn(e(k === "payment_purpose" && p.action_type === "DIRECT_EXPENSE" ? "Факт оплаты" : fieldLabel(k)), `edit:${k}`, nextMissingFact(p) === k ? "primary" : "secondary"))
            .join(
              "",
            )}</div>${p.action_type === "UNRESOLVED" ? btn("Оформить прямой расход", "recover:" + p.id) : ""}`,
        )
      : "") +
    (p.action_type === "DIRECT_EXPENSE"
      ? `<details class="review-provenance"><summary>Происхождение фактов и история подготовки</summary>${expenseContext(p)}<p class="note">Распределение прямого расхода по теплицам в этой карточке недоступно. Общая сумма и факт списания от этого не меняются.</p></details>`
      : "") +
    `<div class="form-error" role="alert" id="action-error"></div>${!done && p.state !== "REJECTED" ? `<div class="confirmation-bar capture-confirmation">${btn("Подтвердить действие в TEST", "confirm-proposal", "primary full", "confirm-proposal", p.revision.missing?.length || p.action_type === "UNRESOLVED" ? "disabled" : "")}</div><div class="page-actions">${btn("Отложить", "defer-proposal")}${btn("Отклонить", "reject-proposal")}</div>` : done ? notice("Сервер подтвердил действие. Оригинал и версии сохранены.") : notice("Предложение отклонено. Оригинал и версии сохранены.")}<div class="page-actions">${link("Финансы", "finance", "btn secondary")}${link("К источнику", "source/" + p.source.id, "btn secondary")}</div>`
  );
}
const labels = {
  amount: "Сумма",
  currency: "Валюта",
  event_date: "Дата",
  description: "За что",
  money_account_id: "Счёт",
  counterparty_id: "Контрагент",
  title: "Название задачи",
  original_instruction: "Текущий текст поручения",
  greenhouse_id: "Теплица",
  planned_date: "Когда",
  worker_id: "Работник",
  language: "Язык",
  rendered_text: "Проверенный перевод",
  note: "Заметка",
  due_date: "Срок оплаты",
  obligation_id: "Погашаемый долг",
  lines: "Строки",
  payment_purpose: "Назначение оплаты",
};
const fieldLabel = (k) => labels[k] || k;
function editableKeys(p) {
  if (["DIRECT_EXPENSE", "PAYABLE", "PAYMENT"].includes(p.action_type))
    return [
      "amount",
      "currency",
      "event_date",
      "description",
      "money_account_id",
      "counterparty_id",
      ...(needsPaidReview(p) ? ["payment_purpose"] : []),
      ...(p.action_type === "PAYABLE"
        ? ["due_date"]
        : p.action_type === "PAYMENT"
          ? ["obligation_id"]
          : []),
    ];
  if (p.action_type.endsWith("TASK"))
    return [
      "title",
      "original_instruction",
      "greenhouse_id",
      "planned_date",
      "worker_id",
      "language",
      "rendered_text",
      "note",
    ];
  return ["note"];
}
function finance(direction = "") {
  const f = data.home?.finance;
  if (!f) return unavailable("Финансы");
  const obligations = f.obligations.filter(
    (o) =>
      (!direction || o.direction === direction) &&
      ["open", "partial"].includes(o.settlement_status),
  );
  return (
    Header(
      direction === "payable"
        ? "Я должен"
        : direction === "receivable"
          ? "Мне должны"
          : "Финансы",
      "Только подтверждённые TEST-документы",
      !!direction,
    ) +
    `<div class="page-actions">${link("Деньги и аналитика", lastFinanceRoute, "btn primary")}${link("Мне должны", "finance/receivable", "btn secondary")}${link("Я должен", "finance/payable", "btn secondary")}</div>` +
    panel(
      `<h2>Счета</h2>${f.accounts
        .map((a) =>
          facts([
            ["Счёт", accountName(a)],
            ["Остаток", a.balance==null && a.amount==null ? "Неизвестно" : money(a.balance ?? a.amount, a.currency)],
          ]),
        )
        .join("")}`,
    ) +
    obligations
      .map((o) =>
        panel(
          `${stateTag(o.settlement_status)}<h2>${e(refName(o.counterparty_id))}</h2><p>${e(o.description || "Документ")}</p><div class="value-wrap"><strong class="exact-value">${e(money(o.remaining_amount, o.currency))}</strong></div><p class="note">Открытый остаток · ${e(o.obligation_id.slice(0, 8))}</p>${link(o.direction === "receivable" ? "Записать получение" : "Записать оплату", `settle/${o.direction}/${o.counterparty_id}/${o.currency}`, "btn primary full")}`,
        ),
      )
      .join("") +
    (!obligations.length
      ? EmptyState(
          "Нет открытых документов",
          "Подтверждённые обязательства появятся здесь.",
        )
      : "") +
    panel(
      `<h2>Последние прямые расходы</h2>${(f.direct_expenses || []).map((x) => `<div class="list-row"><strong>${e(x.description || "Прямой расход")}</strong><small>${e(money(x.amount, x.currency))} · ${e(displayDate(x.event_date))} · ${e(statuses[x.status] || x.status)}</small>${link("Подтверждённые факты", "review/" + x.proposal_id, "btn secondary")}${x.source_id ? link("Оригинал", "source/" + x.source_id, "btn secondary") : ""}</div>`).join("") || "<p>Подтверждённых прямых расходов пока нет.</p>"}<p class="note">Показано до 50 из ${e(f.direct_expenses_total ?? 0)}. ${f.direct_expenses_has_more ? "Есть более ранние записи; они не вошли в этот список." : ""}</p>`,
    ) +
    panel(
      `<h2>Операции</h2>${f.operations
        .slice(0, 20)
        .map(
          (o) =>
            `<div class="list-row"><strong>${e(o.description || operationLabels[o.kind] || o.kind)}</strong><small>${e(statuses[o.status] || o.status)} · ${e(money(o.amount || o.original_amount || "0.00", o.currency))}</small>${o.kind === "payment" ? link("Открыть платёж", "payment/" + o.id) : ""}</div>`,
        )
        .join(
          "",
        )}<p class="note">Последние ${Math.min(20, f.operations.length)} операций. Прямые расходы показаны отдельным серверным списком выше.</p>`,
    )
  );
}
function settleForm(direction, party, currency) {
  const obs = data.home.finance.obligations.filter(
      (o) =>
        o.counterparty_id === party &&
        o.currency === currency &&
        o.direction === direction &&
        ["open", "partial"].includes(o.settlement_status),
    ),
    acs = accounts().filter((a) => a.currency === currency);
  return (
    Header(
      direction === "receivable" ? "Получение денег" : "Оплата долга",
      `${refName(party)} · ${currency}`,
      true,
    ) +
    panel(
      form(
        "payment",
        `${notice("Оплата погасит только явно выбранные документы. Выручка или расход повторно не создаются.")}${select(
          "Счёт · " + currency,
          "account",
          acs.map((a) => ({
            id: a.money_account_id || a.id,
            name: accountName(a),
          })),
          draft.account,
        )}${field("Дата", "date", draft.date || localDay(), "date")}<h2>Распределение по документам</h2>${obs.map((o) => `<div class="list-row"><strong>${e(o.description || "Документ")}</strong><small>Остаток ${e(money(o.remaining_amount, currency))} · ${e(o.obligation_id.slice(0, 8))}</small>${field("В эту оплату", "allocation_" + o.obligation_id, draft["allocation_" + o.obligation_id] || "", "text", 'inputmode="decimal"')}${btn("Весь остаток", "fill-allocation:" + o.obligation_id, "secondary")}</div>`).join("")}<p class="notice">Сумма платежа: <strong id="payment-total">${e(paymentTotal())} ${e(currency)}</strong></p>${notice("На следующем экране будет серверный черновик и отдельное подтверждение.")}`,
        "Проверить оплату",
      ),
    )
  );
}
function paymentTotal(value = draft) {
  try {
    return decimal(
      Object.entries(value || {})
        .filter(([k, v]) => k.startsWith("allocation_") && v)
        .reduce((s, [k, v]) => s + cents(String(v).replace(",", ".")), 0n),
    );
  } catch {
    return "Проверьте суммы";
  }
}
function paymentDetail(p) {
  const a = accounts().find(
    (a) => (a.money_account_id || a.id) === p.money_account_id,
  );
  return (
    Header(
      p.direction === "in" ? "Получение денег" : "Оплата",
      statuses[p.status] || p.status,
      true,
    ) +
    panel(
      `${stateTag(p.status)}${facts([
        ["Контрагент", refName(p.counterparty_id)],
        ["Счёт", a ? accountName(a) : p.money_account_id],
        ["Направление", p.direction === "in" ? "Поступление" : "Списание"],
        ["Сумма", money(p.amount, p.currency)],
        ["Валюта", p.currency],
        ["Дата", displayDate(p.event_date)],
      ])}<h2>Явное распределение</h2>${(p.allocations || [])
        .map((x) => {
          const document = data.home?.finance?.obligations?.find(
            (o) => o.obligation_id === x.obligation_id,
          );
          const rows = [
            ["Документ", document?.description || "Описание не загружено"],
            [
              "Дата документа",
              document?.event_date
                ? displayDate(document.event_date)
                : "UNKNOWN",
            ],
            ["ID документа", x.obligation_id],
            ["В эту оплату", money(x.amount, p.currency)],
            [
              "Текущий остаток",
              document
                ? money(document.remaining_amount, p.currency)
                : "UNKNOWN",
            ],
          ];
          if (p.status === "draft" && document) {
            rows.push([
              "Остаток после оплаты · прогноз",
              money(
                decimal(cents(document.remaining_amount) - cents(x.amount)),
                p.currency,
              ),
            ]);
          }
          return facts(rows);
        })
        .join(
          "",
        )}${notice("Погашение документов. Новая выручка/расход не создаётся.")}<div id="action-error" class="form-error" role="alert"></div>${p.status === "draft" ? btn("Подтвердить оплату в TEST", "post-payment", "primary full") : p.status === "posted" ? notice("Сервер подтвердил платёж. Повтор не создаёт вторую проводку.") : notice("Платёж отменён обратным событием. Сохранена история оригинала.")}<div class="page-actions">${link("К финансам", "finance", "btn secondary")}</div>`,
    )
  );
}
function harvest() {
  const h = data.harvest;
  if (!h) return unavailable("Урожай");
  const coverage = h.cut_count_coverage || {};
  return (
    Header(
      "Урожай / Депо",
      "Полевые наблюдения и документы депо остаются раздельными",
    ) +
    `<div class="page-actions">${link("Сбор урожая", "quick/harvest", "btn primary")}</div>` +
    panel(
      `<h2>Полевые срезы</h2><strong class="exact-value">${e(h.total_cut_count ?? "UNKNOWN")}</strong><p class="note">Известная сумма по ${e(coverage.known_report_count ?? 0)} из ${e(coverage.report_count ?? 0)} отчётов. В ${e(coverage.unknown_report_count ?? 0)} отчётах количество неизвестно. Неизвестное не считается нулём.</p>${h.reports.map((r) => link(`<strong>${e(r.original_greenhouse)} · ${e(displayDate(r.event_date))}</strong><small>${e(r.cut_count ?? "UNKNOWN")} срезов · ${e(r.field_weight_kg ?? "UNKNOWN")} кг · ${e(r.reporter)}</small>`, `report/${r.id}`, "list-row")).join("") || "<p>Нет полевых наблюдений.</p>"}`,
    ) +
    panel(
      `<h2>Чеки депо</h2>${h.settlements.map((s) => link(`<strong>${e(refName(s.depot_id))} · ${e(money(s.total_amount, s.currency))}</strong><small>${e(statuses[s.status] || s.status)} · ${e(displayDate(s.event_date))}</small>`, `depot/${s.id}`, "list-row")).join("") || "<p>Документов депо пока нет.</p>"}`,
    ) +
    panel(
      `<h2>Подтверждённое покрытие</h2>${
        h.analytics
          .map((a) =>
            facts([
              ["Теплица", a.name],
              ["Вес депо", a.total_depot_kg + " кг"],
              ["Средний вес", a.average_weight_kg ?? "UNKNOWN"],
              ["Известное количество", a.confirmed_count],
              ["Вес с известным количеством", a.confirmed_kg + " кг"],
              ["Покрытие", a.count_coverage_complete ? "Полное" : "Частичное"],
            ]),
          )
          .join("") || "<p>Нет подтверждённого веса.</p>"
      }`,
    )
  );
}
function reportForm() {
  return (
    Header("Сбор урожая", "Записать полевое наблюдение", true) +
    panel(
      form(
        "report",
        `${select(
          "Теплица",
          "greenhouse",
          areas().filter((a) => a.kind === "greenhouse"),
          draft.greenhouse,
        )}${field("Дата", "date", draft.date || localDay(), "date")}${field("Количество веток · можно оставить неизвестным", "count", draft.count, "number", 'min="1" step="1"')}${field("Полевой вес, кг · необязательно", "kg", draft.kg, "text", 'inputmode="decimal"')}${field("Кто сообщил", "reporter", draft.reporter || "Владелец TEST")}${area("Заметка · необязательно", "original", draft.original)}<p class="note">Заполненная форма сохраняется как оригинал. Вес можно не указывать. Это полевой отчёт, без продажи или оплаты.</p>`,
        "Сохранить полевой срез",
      ),
    )
  );
}
function reportDetail(r) {
  const batches = data.harvest.batches.filter((b) =>
      b.report_ids.includes(r.id),
    ),
    linked = data.harvest.settlements.filter((s) =>
      s.batch_ids.some((id) => batches.some((b) => b.id === id)),
    );
  return (
    Header("Полевой срез", displayDate(r.event_date), true) +
    panel(
      `<h2>Оригинальная полевая запись</h2><p class="original-text" data-testid="field-original">${e(r.original_evidence?.structured_original ? "Заполненная владельцем форма" : r.original_evidence?.raw_text || "Текст не добавлен")}</p>${facts(
        [
          ["Теплица в оригинале", r.original_greenhouse],
          ["Количество веток", r.cut_count ?? "UNKNOWN"],
          ["Полевой вес, кг", r.field_weight_kg ?? "UNKNOWN"],
          ["Сообщил", r.reporter],
          ["Примечание", r.notes || "Не добавлено"],
        ],
      )}${formOriginal(r.original_evidence)}<p class="note">Исходное полевое наблюдение неизменно. Документ депо остаётся отдельным.</p>${link("Фото / PDF чека депо", `attach-depot/${r.id}`, "btn primary full")}`,
    ) +
    panel(
      `<h2>Связанные документы</h2>${linked.map((s) => link(`${e(refName(s.depot_id))} · ${e(money(s.total_amount, s.currency))}`, `depot/${s.id}`, "list-row")).join("") || "<p>Связь ещё не подтверждена.</p>"}<p class="note">Поздняя связь и дополнительные оригиналы доступны в карточке документа депо. Исходная полевая запись не изменяется.</p>`,
    )
  );
}
function depotLineFields(index) {
  const prefix=index ? `line_${index}_` : '';
  const value=k=>draft[prefix+k] || '';
  return `<fieldset class="depot-line"><legend>Строка ${index+1}</legend>${field("Качество дословно",prefix+"quality",value('quality'))}${select("Категория · решение владельца",prefix+"quality_category",[{value:'HIGHEST',label:'Высшее'},{value:'FIRST',label:'Первое'},{value:'SECOND',label:'Второе'}],value('quality_category'),'Нужно проверить')}<div class="field-grid">${field("Вес нетто, кг",prefix+"kg",value('kg'),'text','inputmode="decimal"')}${field("Цена за кг",prefix+"price",value('price'),'text','inputmode="decimal"')}${field("Сумма в документе · если есть",prefix+"stated_amount",value('stated_amount'),'text','inputmode="decimal"')}${field("ADET · если указан",prefix+"count",value('count'),'number','min="1" step="1"')}</div>${select("Использовать сумму",prefix+"confirmed_amount",[{value:'calculated',label:'Вес × цена'},{value:'stated',label:'Сумма документа'}],value('confirmed_amount') || 'calculated')}<details><summary>Дословная строка документа</summary>${field("Строка источника · необязательно",prefix+"source_line",value('source_line'))}</details>${index?btn('Убрать эту строку','remove-depot-line:'+index,'secondary'):''}</fieldset>`;
}
function depotForm(report) {
  const parties=(data.home?.finance.references || []).filter(p=>p.kind==='counterparty' && ['depot','customer','both'].includes(p.payload?.role || p.role));
  const fallback=(data.home?.finance.references || []).filter(p=>p.kind==='counterparty');
  const count=Number(draft.line_count || 1);
  const candidates=(data.harvest?.reports || []).filter(r=>r.id!==report?.id);
  return Header('Документ депо',report ? 'К срезу: '+report.original_greenhouse : 'Новый отдельный источник',true)+panel(form('depot',
    `${draft.source_id?notice('Оригинал уже сохранён. Ручные поля — проверяемая расшифровка.'):fileField()}${draft.file?.filename?'<p class="note">Файл: '+e(draft.file.filename)+'</p>':''}${draft.source_id?'<p class=note>Комментарий оригинала: '+e(draft.caption || 'Не добавлен')+'</p>':area('Отдельный комментарий владельца','caption',draft.caption)}${select('Депо','depot',(parties.length?parties:fallback).map(p=>({id:p.id,name:p.original_name||p.name})),draft.depot)}<div class="field-grid">${field('Дата чека','date',draft.date || report?.event_date || localDay(),'date')}${select('Валюта','currency',['TRY','USD','EUR','UAH'].map(c=>({value:c,label:c})),draft.currency || 'TRY')}${field('Номер документа · если есть','document_number',draft.document_number)}${field('Срок оплаты · необязательно','due_date',draft.due_date,'date')}</div><details ${report?'open':''}><summary>Связь с полевыми срезами</summary>${report?'<p>'+e(report.original_greenhouse)+' · '+e(report.cut_count ?? 'UNKNOWN')+' веток</p>':''}${candidates.map(r=>`<label class="check-label"><input type="checkbox" name="report_${e(r.id)}" ${draft['report_'+r.id]==='on'?'checked':''}><span>${e(r.original_greenhouse)} · ${e(displayDate(r.event_date))} · ${e(r.cut_count ?? 'UNKNOWN')} веток</span></label>`).join('')}${field('Основание явной связи','link_evidence',draft.link_evidence)}</details><div class="depot-lines">${Array.from({length:count},(_,i)=>depotLineFields(i)).join('')}</div>${btn('Добавить строку качества','add-depot-line','secondary full')}<div class="field-grid">${field('Итого по документу · если указано','stated_total',draft.stated_total,'text','inputmode="decimal"')}${field('Alacak · сальдо из источника','alacak_value',draft.alacak_value,'text','inputmode="decimal"')}</div><p class="note">Категория подтверждается для этого документа и депо. Сохранённая строка качества не заменяется. Alacak не считается оплатой или суммой этой продажи.</p>`, 'Открыть короткую проверку'));
}
function qualityProvenance(depotId, mappings=[],reviews=[]) {
  const unique=[...new Map(mappings.map(m=>[m.original_label+':'+m.version,m])).values()];
  return `<details class="review-provenance"><summary>Происхождение классификации качества</summary><p>Депо: ${e(refName(depotId))}. Исходные обозначения сохранены отдельно.</p>${unique.length?unique.map(m=>`<p>Справочник этого депо · версия ${e(m.version)}: ${e(m.original_label)} → ${e({HIGHEST:'Высшее',FIRST:'Первое',SECOND:'Второе'}[m.quality] || m.quality)}. Доступное соответствие; текущую категорию проверяет владелец.</p>`).join(''):'<p>Предыдущая версия справочника не использована. Категория — отдельное решение владельца для этого документа, без создания общего правила.</p>'}${reviews.length?'<p>Сохранённая ревизия проверки документа: '+e([...new Set(reviews.map(r=>r.version))].join(', '))+'. Это версия проверки, не номер справочника.</p>':''}</details>`;
}
function compactDepotProposal(p) {
  const f=p.revision.payload, arithmetic=p.revision.basis?.arithmetic || {};
  return panel(`<h2>${e(refName(f.depot_id))}</h2>${facts([['Дата',displayDate(f.event_date)],['Валюта',f.currency],['Продажа и долг',arithmetic.total_amount==null?'Не рассчитано':money(arithmetic.total_amount,f.currency)],['Эффект','Деньги не получены.'],['Полевое количество',arithmetic.field_count==null?'Неизвестно':arithmetic.field_count+' веток'],['Количество документа',arithmetic.depot_count==null?'Неизвестно':arithmetic.depot_count+' ADET']])}${(f.lines||[]).map((l,i)=>`<div class="depot-review-line"><strong>${e(l.original_quality_name)} · ${e({HIGHEST:'Высшее',FIRST:'Первое',SECOND:'Второе'}[f.qualities?.[i]] || 'Категория неизвестна')}</strong><p>${e(displayQuantity(l.net_weight_kg))} кг × ${e(displayQuantity(l.unit_price))} ${e(f.currency)}/кг</p><p>Расчёт: ${e(arithmetic.lines?.[i]?.calculated_amount ?? 'Неизвестно')} · Подтверждаемая сумма: ${e(arithmetic.lines?.[i]?.selected_amount ?? 'Неизвестно')} ${e(f.currency)}</p><p>ADET: ${e(l.count ?? 'Не указан')} · Сумма документа: ${e(l.stated_amount ?? 'Не указана')}</p>${btn('Исправить строку '+(i+1),'edit-depot-line:'+i,'secondary')}</div>`).join('')}${arithmetic.count_discrepancy?notice('Расхождение: '+arithmetic.field_count+' веток по полю и '+arithmetic.depot_count+' ADET по документу. Ни одно значение не заменяется.','amber'):''}${arithmetic.stated_total_discrepancy?notice('Итог документа не совпадает с выбранной суммой строк. Проверьте оригинал.','amber'):''}<p class="note">Полевые ветки и ADET депо остаются независимыми значениями. Выбранных партий: ${f.batch_ids.length}.</p>${(p.revision.missing||[]).map(x=>'<p class="form-error">'+e(x)+'</p>').join('')}${!f.arithmetic_reviewed?btn('Арифметика и расхождения проверены','review-depot-arithmetic','primary full'):notice('Арифметика проверена владельцем')}${qualityProvenance(f.depot_id,p.revision.basis?.quality_mappings || [])}${link('Вернуться к данным документа','edit-depot/'+p.id,'btn secondary full')}`);
}
const issueNames = {
  count_discrepancy: "Полевое количество и ADET различаются",
  weight_discrepancy: "Полевой вес отличается от веса депо",
  unallocated: "Вес и выручка не распределены по теплицам",
  missing_due_date: "Срок получения денег не указан",
  unknown_quality: "Качество требует проверки",
  amount_discrepancy: "Сумма источника отличается от расчёта",
  mixed_receipt: "Смешанный чек",
  unknown_greenhouse: "Теплица не подтверждена",
  unmatched_batch: "Нет связанной партии",
  missing_report: "Нет полевого отчёта",
  receipt_draft: "Черновик",
};
function depotDetail(s) {
  return (
    Header("Чек депо", refName(s.depot_id), true) +
    panel(
      `${stateTag(s.status)}${facts([
        ["Дата", displayDate(s.event_date)],
        [
          s.status === "draft"
            ? "Продажа после подтверждения · прогноз"
            : "Продажа",
          money(s.total_amount, s.currency),
        ],
        [
          "Остаток долга",
          s.status === "draft"
            ? "Долг ещё не создан"
            : money(s.remaining_amount, s.currency),
        ],
      ])}<h2>Оригинал документа</h2>${s.evidence.filename || s.owner_source_id ? `<a class="btn secondary" href="${entityPath(s.owner_source_id ? "/owner/captures/"+s.owner_source_id+"/original" : "/harvest/evidence/" + s.evidence.id)}" download>${e(s.evidence.filename || "Оригинальный документ")} · неизменённые байты</a>` : "<p>Файл не добавлен</p>"}<h3>Комментарий владельца · отдельно</h3><p class="original-text">${e(s.evidence.raw_text || "Не добавлен")}</p>`,
    ) +
    panel(
      `<h2>Срез и ADET</h2>${facts([
        ["Полевое количество", s.reconciliation.field_count ?? "UNKNOWN"],
        ["ADET документа", s.reconciliation.depot_count ?? "UNKNOWN"],
        ["Полевой вес, кг", s.reconciliation.field_weight_kg ?? "UNKNOWN"],
        ["Вес депо, кг", s.reconciliation.depot_weight_kg],
      ])}${s.reports.map((r) => link(e(r.original_greenhouse) + " · " + displayDate(r.event_date), "report/" + r.id, "list-row")).join("")}${notice("Расхождение не исправляет ни источник, ни второй показатель. Покрытие показывается только по известным данным.", "amber")}`,
    ) +
    panel(
      `<h2>Строки</h2>${s.lines
        .map((l) =>
          facts([
            ["Оригинальное качество", l.original_quality_name],
            ["Категория", s.owner_source_id ? ({HIGHEST:"Высшее",FIRST:"Первое",SECOND:"Второе"}[l.normalized_quality] || "Неизвестно") : "Историческая классификация · не пересмотрена"],
            ["Основание качества", s.owner_source_id ? "Решение владельца для этого депо и документа" : "Legacy · непроверенное соответствие"],
            ["Вес", l.net_weight_kg + " кг"],
            ["Цена", money(l.unit_price, s.currency)],
            ["Сумма", money(l.amount, s.currency)],
            ["ADET", l.count ?? "UNKNOWN"],
            ["Строка источника", l.source_line],
          ]),
        )
        .join("")}${facts([
        ["Средний вес по ADET, кг/ADET", s.average_weight_kg == null ? "UNKNOWN" : "≈ " + displayMetric(s.average_weight_kg)],
        ["Известное количество", s.confirmed_count],
        ["Вес с количеством", s.confirmed_kg + " кг"],
        ["Покрытие", s.count_coverage_complete ? "Полное" : "Частичное"],
      ])}`,
    ) +
    (s.status === "draft"
      ? panel(
          form(
            "post-depot",
            `<h2>Проверить расхождения</h2>${s.issues
              .filter((x) => x !== "receipt_draft")
              .map(
                (x) =>
                  `<label class="check-label"><input name="issue_${e(x)}" type="checkbox" required><span>${e(issueNames[x] || x)}</span></label>`,
              )
              .join(
                "",
              )}${area("Решение владельца по расхождениям", "reason", "")}${notice("Подтверждение создаст продажу и долг депо. Получение денег не создаётся. Исходник и полевая запись сохранятся.")}`,
            "Подтвердить чек в TEST",
          ),
        )
      : s.status === "posted"
        ? notice("Сервер подтвердил документ. Продажа и долг созданы один раз.")
        : notice(
            "Документ отменён обратным событием. Оригинал и история сохранены.",
          )) +
    (s.owner_source_id ? qualityProvenance(s.depot_id,(s.quality_reviews || []).flatMap(r=>r.basis?.quality_mappings || []),s.quality_reviews || []) : "") +
    (s.status === "posted" ? journeyActions(s) : "") +
    link("К урожаю", lastHarvestRoute, "btn secondary")
  );
}
function journeyActions(s) {
  const j=s.journey || {version:0,attachments:[],estimates:[]};
  return panel(`<h2>Связи и уточнения</h2><div class="page-actions">${link('Уточнить связь','depot-links/'+s.id,'btn secondary')}${link('Добавить оригинал','depot-attachment/'+s.id,'btn secondary')}${link('Распределить оценкой','depot-estimate/'+s.id,'btn secondary')}</div><p class="note">Новая версия сохраняет старые основания. Продажа и долг повторно не создаются. Финансовый период не пересчитывается.</p>${(j.attachments||[]).map(a=>`<a class="list-row" href="${entityPath('/harvest/evidence/'+a.evidence.id)}" target="_blank" rel="noopener">${e(a.evidence.filename || 'Дополнительный оригинал')} · ${e(a.reason)}</a>`).join('')}${j.current_estimate?'<p class="tag amber">Есть подтверждённая оценка · версия '+e(j.current_estimate.version)+'</p>':''}${(j.estimates||[]).some(x=>x.stale)?'<p class="note">Прежняя оценка устарела после изменения связи. Она не включена в показатели.</p>':''}<details><summary>История оснований · версия ${e(j.version)}</summary>${(j.link_history||[]).map(x=>'<p>'+e(x.created_at)+' · '+e(x.reason)+'</p>').join('')}${(j.estimates||[]).map(x=>'<p>Оценка '+e(x.version)+' · '+e(x.basis_description)+' · '+e(x.algorithm)+'</p>').join('')}</details>`);
}
function journeyForm(kind,s) {
  const j=s.journey;
  let body='';
  if(kind==='links') body=`<h2>Явно выбранные партии</h2>${(data.harvest?.batches||[]).map(b=>`<label class="check-label"><input type="checkbox" name="batch_${e(b.id)}" ${draft['batch_'+b.id]==='on'?'checked':''}><span>${e(b.label)} · ${e(displayDate(b.event_date))}</span></label>`).join('')}<details><summary>Добавить ещё полевые срезы</summary>${(data.harvest?.reports||[]).map(r=>`<label class="check-label"><input type="checkbox" name="report_${e(r.id)}" ${draft['report_'+r.id]==='on'?'checked':''}><span>${e(r.original_greenhouse)} · ${e(displayDate(r.event_date))} · ${e(r.cut_count ?? 'UNKNOWN')} веток</span></label>`).join('')}</details><p class="note">Дата не доказывает связь. Снятие выбора создаёт новую версию связи; прежняя остаётся в истории.</p>`;
  else if(kind==='attachment') body=`${fileField()}${draft.file?.filename?'<p>'+e(draft.file.filename)+'</p>':''}${area('Комментарий к новому оригиналу','caption',draft.caption)}`;
  else body=`<p class="note">Это приближённое основание по веткам, не измеренный вес теплицы. Качества не получают выдуманное количество веток.</p>${areas().filter(a=>a.kind==='greenhouse').map(a=>field('Основание · '+a.name,'basis_'+a.id,draft['basis_'+a.id],'text','inputmode="decimal"')).join('')}${field('Всего веток в основании, включая нераспределённые','total_basis_count',draft.total_basis_count,'text','inputmode="decimal"')}${area('На чём основана пропорция','basis_description',draft.basis_description)}`;
  return Header({links:'Связь поля и депо',attachment:'Дополнительный оригинал',estimate:'Оценка по теплицам'}[kind],refName(s.depot_id),true)+panel(form('journey-'+kind,body+area('Основание решения владельца','reason',draft.reason)+`<p class="note">Документ: ${e(displayDate(s.event_date))} · ${e(money(s.total_amount,s.currency))}. Уточнение не создаёт денег и не меняет итог документа.</p>`,kind==='estimate'?'Рассчитать и проверить':'Подтвердить '+(kind==='links'?'связь':'прикрепление')));
}
function inventory() {
  const v = data.inventory;
  if (!v) return unavailable("Склад");
  return (
    Header("Склад", "Товары, места хранения и фактические движения") +
    `<div class="page-actions">${link("Товар привезли", "quick/receipt", "btn primary")}${link("История покупок", "purchases", "btn secondary")}${link("Все движения", "stock-history", "btn secondary")}${link("Переместить", "quick/transfer", "btn secondary")}</div>` +
    notice(
      "Начальные остатки не подтверждены: UNKNOWN. Учтённый приход и сумма движений не являются проверенным абсолютным остатком.",
      "amber",
    ) +
    panel(
      `<h2>Товары и учтённые движения</h2>${
        v.products
          .map((p) => {
            const ss = v.stock.filter((s) => s.product_id === p.id);
            return `<div class="list-row"><strong>${e(p.name)}</strong><small>Проверенный физический остаток: UNKNOWN · стоимость ${ss.length && ss.every((x) => /^0(?:\.0+)?$/.test(x.uncosted_quantity)) ? "из учтённых оценённых движений" : "UNKNOWN"}</small>${ss.length ? ss.map((s) => `<p>${e(s.location_name)} · учтено ${e(displayQuantity(s.quantity))} ${e(p.base_unit)}<br><small>Без стоимости: ${e(displayQuantity(s.uncosted_quantity))} ${e(p.base_unit)}</small></p>`).join("") : "<p>Движений пока нет. Физический остаток UNKNOWN.</p>"}${link("Приход этого товара", "quick/receipt/" + p.id, "btn secondary")}${link("История товара", "stock-history/all/all/" + p.id, "btn secondary")}${link("Переместить", "quick/transfer/" + p.id, "btn secondary")}</div>`;
          })
          .join("") ||
        "<p>Товаров пока нет. Добавьте первый прямо в приходе.</p>"
      }`,
    ) +
    panel(
      `<h2>Места хранения</h2>${v.locations.map((l) => `<div class="list-row">${e(l.name)}</div>`).join("") || "<p>Не настроены. Приход заблокирован до конфигурации места хранения.</p>"}`,
    ) +
    panel(
      `<h2>История</h2>${v.events.map((ev) => link(`<strong>${e(operationLabels[ev.kind] || ev.kind)} · ${e(displayDate(ev.event_date))}</strong><small>${e(statuses[ev.status] || ev.status)} · ${(ev.payload.lines || []).map((l) => e(l.original_product_name) + " " + e(l.quantity) + " " + e(l.unit)).join("; ")}</small>`, `receipt/${ev.id}`, "list-row")).join("")}`,
    )
  );
}
function receiptForm(productId) {
  const v = data.inventory,
    p = v.products.find((p) => p.id === (draft.product || productId));
  return (
    Header("Товар привезли", "Физический приход · без счёта и оплаты", true) +
    panel(
      form(
        "receipt",
        `${select(
          "Товар",
          "product",
          v.products.filter((x) => x.active),
          draft.product || productId,
        )}${btn("Создать новый товар", "new-product", "secondary")}${select("Место хранения · настроено отдельно", "location", v.locations, draft.location)}${field("Количество", "quantity", draft.quantity, "text", 'inputmode="decimal"')}${field("Базовая единица", "unit", p?.base_unit || "", "text", "readonly")}${field("Дата прихода", "date", draft.date || localDay(), "date")}${area("Заметка · необязательно", "original", draft.original)}${fileField("Фото / PDF исходника · необязательно")}${draft.file?.filename ? notice("Копия на телефоне: " + draft.file.filename) : ""}<p class="note">Стоимость неизвестна. После проверки создаётся только приход; долг и оплата не создаются.</p>`,
        "Проверить приход",
      ),
    )
  );
}
function invoiceNewForm(receipt) {
  return Header('Счёт к приходу','Оригинал сохраняется отдельно от физического движения',true)+panel(form('invoice-source',
    facts([['Приход',displayDate(receipt.event_date)],['Товары',receipt.lines.map(l=>l.original_product_name+' · '+l.quantity+' '+l.unit).join('; ')]])+
    select('Что нужно сделать','intent',[{value:'operating_expense',label:'Новый счёт на операционный расход'},{value:'existing_payable',label:'Связать с уже учтённым долгом'}],draft.intent,'Выберите явно')+
    '<p class="note">Новый операционный счёт после отдельного подтверждения создаст расход и долг. Для актива, материала или иного неоперационного обязательства используйте уже правильно учтённый документ; классификацию система не угадывает.</p>'+
    area('Текст документа или подпись · необязательно с файлом','text',draft.text)+fileField('Фото / PDF счёта')+(draft.file?.filename?notice('Копия на телефоне: '+draft.file.filename):'')+
    '<p class="note">Сохранение оригинала и подготовка не создают второй приход, оплату или долг.</p>','Сохранить счёт и подготовить'));
}
function invoiceWorkflowView(c) {
  const {receipt,source,payable,match,documents}=c,done=p=>['POSTED','CONFIRMED_NONPOSTING'].includes(p?.state);
  let body=Header('Проверить счёт и связь','Каждое учётное действие подтверждается отдельно',true)+panel(facts([['Физический приход',displayDate(receipt.event_date)],['Товары',receipt.lines.map(l=>l.original_product_name+' · '+l.quantity+' '+l.unit).join('; ')]])+`${source.invoice_receipt_id&&source.invoice_receipt_id!==receipt.id?'<p class="note">Исходный контекст другого прихода сохранён; здесь показан приход из текущей явной ревизии связи.</p>':''}<p class="note">Приход уже учтён. Этот выбор сделан владельцем и не является извлечённым из счёта фактом.</p><details class="source-provenance"><summary>Оригинал счёта</summary><p class="original-text">${e(source.text || 'Текст не добавлялся')}</p>${source.original_name?`<a class="btn secondary" href="${entityPath('/owner/captures/'+source.id+'/original')}" download>${e(source.original_name||'Скачать оригинал')}</a>`:''}</details>`);
  if(!match) return body+panel(`<h2>Источник сохранён</h2>${stateTag(source.job?.state||source.state)}<p>${e(source.job?.error_code||'Подготовка ещё не завершена.')}</p>${btn('Обновить','refresh')}${source.retryable?btn('Повторить подготовку','prepare:'+source.id):''}${(source.proposals||[]).map(p=>link('Открыть подготовленное действие','review/'+p.id,'btn secondary')).join('')}`)+link('К истории поставки','purchase/'+receipt.id,'btn secondary');
  if(payable){const f=payable.revision.payload;body+=panel(`<h2>1. Операционный счёт</h2>${stateTag(payable.state)}${facts([['За что',f.description||'Нужно уточнить'],['Сумма',f.amount?money(f.amount,f.currency):'Не указана'],['Дата',displayDate(f.event_date,'Не указана')],['Поставщик',f.counterparty_id?refName(f.counterparty_id):'Нужно выбрать'],['Срок',displayDate(f.due_date,'Не указан')]])}<details class="source-provenance"><summary>Происхождение подготовленных фактов</summary>${['amount','currency','event_date','description','counterparty_id'].map(k=>'<p>'+e(fieldLabel(k))+': '+e(factOrigin(payable,k)||'Не установлено из источника')+'</p>').join('')}</details><p class="note">После подтверждения: операционный расход и долг поставщику. Деньги не списываются; склад повторно не меняется.</p>${!done(payable)&&payable.state!=='REJECTED'?`<div class="page-actions fact-actions">${['amount','currency','event_date','counterparty_id','description','due_date'].map(k=>btn(fieldLabel(k),'invoice-edit:'+k)).join('')}</div>${(payable.revision.missing||[]).map(m=>'<p class="form-error">'+e(m)+'</p>').join('')}${btn('Подтвердить расход и долг в TEST','invoice-confirm-payable','primary full','invoice-confirm-payable',payable.revision.missing?.length?'disabled':'')}`:done(payable)?'<p>Расход и долг подтверждены сервером. Повтор не создаёт новый документ.</p>':'<p>Предложение отклонено. Новый долг по нему не создан.</p>'}`);}
  const effectId=payable?.confirmed_effect?.effect?.id,linkedId=match.revision.payload.finance_obligation_id,selected=documents.find(d=>d.obligation_id===linkedId),canChoose=!payable||done(payable),matchDone=done(match);
  body+=panel(`<h2>${payable?'2. ':''}Явная связь с финансовым документом</h2>${stateTag(match.state)}${matchDone?'<p>Связь подтверждена. Повторного прихода, расхода и оплаты нет.</p>':!canChoose?'<p>Сначала подтвердите отдельный операционный счёт выше. Его документ появится здесь без повторного ввода суммы.</p>':(!linkedId||draft.choose==='yes')?form('invoice-link',`${!payable?'<div class="page-actions">'+['TRY','USD','EUR','UAH'].map(cur=>link(cur,'invoice-source/'+receipt.id+'/'+source.id+'/'+cur,'btn secondary')).join('')+'</div>':''}${select('Уже учтённый долг · весь документ','obligation',documents.filter(d=>d.direction==='payable'&&d.status==='posted'&&(!effectId||d.obligation_id===effectId)).map(d=>({id:d.obligation_id,name:(d.source.description||'Документ')+' · '+money(d.original_amount,d.currency)+' · '+d.counterparty_name})),draft.obligation||effectId||linkedId,'Выберите документ')}${area('Основание связи · необязательно','note',draft.note)}<p class="note">Сумма всего документа не становится стоимостью этой строки товара. Сначала проверьте связь, затем подтвердите её отдельно.</p>`,'Проверить связь'):`${facts([['Документ',selected?.source.description||'Подтверждённый документ'],['Сумма всего документа',selected?money(selected.original_amount,selected.currency):'Проверьте выбранный документ'],['Поставщик',selected?.counterparty_name||'Откройте документ'],['Приход',displayDate(receipt.event_date)]])}${(match.revision.missing||[]).map(m=>'<p class="form-error">'+e(m)+'</p>').join('')}${btn('Подтвердить только связь в TEST','invoice-confirm-link','primary full','invoice-confirm-link',match.revision.missing?.length?'disabled':'')}${btn('Изменить выбранный документ','invoice-choose-link')}`}${linkedId?link('Финансовый документ и оплаты','money-document/'+linkedId+'/'+encodeURIComponent(route),'btn secondary'):''}<p class="note">Связь не проводит склад и не устанавливает неизвестную стоимость. Приход, счёт и оплата остаются отдельными событиями.</p>`);
  return body+'<div class="form-error" id="action-error" role="alert"></div>'+`<div class="page-actions">${link('К истории поставки','purchase/'+receipt.id,'btn secondary')}${link('К исходнику','source/'+source.id,'btn secondary')}</div>`;
}
function transferForm(productId) {
  const v=data.inventory,p=v.products.find(x=>x.id===(draft.product||productId));
  return Header('Переместить товар','Между настроенными местами хранения',true)+panel(form('transfer',
    select('Товар','product',v.products.filter(x=>x.active),draft.product||productId)+
    select('Откуда','location',v.locations,draft.location)+select('Куда','destination',v.locations,draft.destination)+
    field('Количество','quantity',draft.quantity,'text','inputmode="decimal"')+
    field('Единица товара','unit',p?.base_unit||'','text','readonly')+field('Дата перемещения','date',draft.date||localDay(),'date')+
    area('Заметка · необязательно','original',draft.original)+fileField('Фото / PDF · необязательно')+
    (draft.file?.filename?notice('Копия на телефоне: '+draft.file.filename):'')+
    '<p class="note">После отдельного подтверждения создаст связанные движения между складами. Не расход, не покупка и не оплата. Неизвестная стоимость остаётся неизвестной.</p>', 'Проверить перемещение'));
}
function receiptCanConfirm(r, duplicateReviewed = false) {
  return ["receipt","transfer"].includes(r?.kind) && r.status === "draft" && !r.replacement_id &&
    r.evidence?.verification !== "HISTORY" && Array.isArray(r.issues) &&
    !r.issues.some(issue => !["cost", "review", "duplicate"].includes(issue)) &&
    (!r.issues.includes("duplicate") || duplicateReviewed);
}
function syncReceiptConfirmation() {
  if (!current || route !== "receipt/" + current.id) return;
  const button = document.querySelector('[data-action="post-receipt"]');
  if (button) button.disabled = busy || !receiptCanConfirm(current,
    !!document.querySelector('#duplicate-reviewed')?.checked);
}
function receiptDetail(r) {
  return (
    Header(r.kind === "receipt" ? "Приход товара" : (operationLabels[r.kind] || "Складское событие"), statuses[r.status] || r.status, true) +
    panel(
      `${stateTag(r.status)}${(r.payload.lines || [])
        .map((l) =>
          facts([
            ["Товар", l.original_product_name],
            ["Количество", `${l.quantity} ${l.unit}`],
            [
              "Место хранения",
              data.inventory?.locations.find((x) => x.id === l.location_id)
                ?.name || l.location_id,
            ],
            ...(l.destination_id ? [["Куда",data.inventory?.locations.find(x=>x.id===l.destination_id)?.name || l.destination_id]] : []),
            ["Стоимость", l.unit_cost ?? "UNKNOWN"],
          ]),
        )
        .join("")}${facts([
        ["Дата", displayDate(r.event_date)],
        ["Начальный остаток", "UNKNOWN"],
      ])}<p class="note">${r.kind === "transfer" ? "Только перемещение между складами; общий объём учтённых запасов сохраняется." : r.kind === "receipt" ? "Только физический приход." : r.kind === "plan" ? "План не создаёт складское движение." : "Складское событие и его исходные движения."} Общий остаток и неизвестная стоимость не становятся известными; без долга и оплаты.</p>${formOriginal(r.evidence)}<h2>Заметка владельца</h2><p class="original-text">${e(r.evidence.structured_original?.fields?.notes || r.evidence.raw_text || "Заполненная форма · отдельная заметка не добавлена")}</p>${r.evidence.filename ? `<a class="btn secondary" href="${entityPath("/inventory/events/" + r.id + "/original")}" target="_blank" rel="noopener">${e(r.evidence.filename)}</a>` : ""}${r.evidence?.verification === "HISTORY" ? notice("Исторический источник: проведение в текущие остатки недоступно.", "amber") : ""}${r.replacement_id ? notice("Для этой записи уже создано исправление. Откройте актуальную версию.", "amber") : ""}${(r.issue_messages || []).filter((_, i) => !["cost", "review"].includes(r.issues?.[i])).map((x) => notice(x, "amber")).join("")}<div id="action-error" class="form-error" role="alert"></div>${r.status === "draft" ? `${r.issues?.includes("duplicate") ? '<label class="check-label"><input type="checkbox" id="duplicate-reviewed"><span>Я проверил: это отдельный приход, не повтор.</span></label>' : ""}${btn(r.kind === "transfer" ? "Подтвердить перемещение в TEST" : "Подтвердить физический приход в TEST", "post-receipt", "primary full", "post-receipt", !receiptCanConfirm(r) ? "disabled" : "")}` : r.status === "posted" ? notice(r.kind === "transfer" ? "Сервер подтвердил перемещение. Связанные движения созданы один раз." : r.kind === "receipt" ? "Сервер подтвердил приход. Физическое движение создано один раз." : "Складское событие подтверждено. Движения и исходник показаны отдельно.") : notice("Приход отменён обратным движением. Оригинал и история сохранены.")}<div class="page-actions">${link("К складу", "inventory", "btn secondary")}${r.kind === "receipt" ? link("История поставки", "purchase/" + r.id, "btn secondary") : link("История движения", "stock-event/" + r.id, "btn secondary")}</div>`,
    ) +
    panel(
      `<h2>Движения именно этого события</h2>${
        (r.movements || [])
          .map((m) =>
            facts([
              ["Количество", m.quantity],
              ["Стоимость известная", m.known_cost],
              ["Количество без стоимости", m.uncosted_quantity],
            ]),
          )
          .join("") || "<p>Движений нет.</p>"
      }`,
    )
  );
}
function personalList(list) {
  return (
    Header("Мои задачи", "Личные дела владельца") +
    `<div class="filters">${link("Активные · " + list.counts.TO_DO, "tasks/TO_DO", "filter-chip " + (list.state === "TO_DO" ? "active" : ""))}${link("Завершённые · " + list.counts.DONE, "tasks/DONE", "filter-chip " + (list.state === "DONE" ? "active" : ""))}</div><div class="page-actions">${link("Новая личная задача", "capture/text", "btn secondary")}${link("Поручения работникам", "workers", "btn secondary")}</div>` +
    panel(
      taskRows(list.items) ||
        EmptyState(
          "Здесь пока пусто",
          "Задачи сохраняются после подтверждения Capture.",
        ),
    ) +
    `<p class="note">Показано ${list.items.length} из ${list.total}. Workers R1 ведутся отдельно.</p><div class="page-actions">${list.offset > 0 ? link("Назад", `tasks/${list.state}/${Math.max(0, list.offset - list.limit)}`, "btn secondary") : ""}${list.next_offset != null ? link("Следующие", `tasks/${list.state}/${list.next_offset}`, "btn secondary") : ""}</div>`
  );
}
function personalDetail(t) {
  return (
    Header(t.title, statuses[t.state], true) +
    panel(
      `${facts([
        [
          "Когда",
          t.planned_date
            ? displayDate(t.planned_date)
            : t.relative_time_context || "Не указано",
        ],
        [
          "Теплица",
          areas().find((a) => a.id === t.greenhouse_id)?.name || "Не указана",
        ],
        [
          "Категория",
          {
            irrigation: "Полив",
            general: "Общее дело",
            harvest: "Урожай",
            maintenance: "Обслуживание",
          }[t.category] ||
            t.category ||
            "Не указана",
        ],
      ])}<h2>Исходная формулировка</h2><p class="original-text">${e(t.original_instruction || t.note || "Не указана")}</p><div id="action-error" class="form-error" role="alert"></div>${btn(t.state === "DONE" ? "Вернуть в активные" : "Отметить готовой", "personal-state", "primary full")}<details><summary>История состояния</summary>${t.actions
        .map((a) =>
          facts([
            ["Состояние", statuses[a.state]],
            ["Версия", a.version],
            ["Время", a.created_at],
          ]),
        )
        .join(
          "",
        )}</details>${link("Все мои задачи", "tasks/" + t.state, "btn secondary")}`,
    )
  );
}
function workers() {
  const w = data.workers;
  if (!w) return unavailable("Работники");
  return (
    Header("Работники", "Workers R1 · CONNECTED TEST") +
    notice(
      "Назначение и внутренний жизненный цикл сохраняются в TEST. Внешняя отправка, Telegram и новое понимание Dari: NOT AVAILABLE.",
    ) +
    `<div class="page-actions">${link("Новое поручение", "quick/worker", "btn primary")}${link("Мои личные задачи", "tasks", "btn secondary")}</div>` +
    panel(
      `<h2>Поручения</h2>${taskRows(w.tasks, true) || "<p>Пока нет задач.</p>"}`,
    ) +
    panel(
      `<h2>Работники TEST</h2>${w.workers.map((x) => `<div class="list-row"><strong>${e(x.display_name)}</strong><small>${e(x.language)} · ${x.active ? "активен" : "неактивен"}</small></div>`).join("")}`,
    )
  );
}
function workerForm() {
  return (
    Header("Поручение работнику", "Создать черновик Workers R1", true) +
    panel(
      form(
        "worker",
        `${field("Название", "title", draft.title)}${area("Исходное поручение", "original", draft.original)}${select(
          "Теплица",
          "greenhouse",
          areas().filter((a) => a.kind === "greenhouse"),
          draft.greenhouse,
        )}${field("Дата", "date", draft.date || localDay(), "date")}${notice("Сначала сохранится исходное поручение. Назначение конкретному работнику и проверенный текст выполняются отдельно. Внешней отправки нет.")}`,
        "Создать черновик",
      ),
    )
  );
}
function workerDetail(t) {
  return (
    Header(t.title, `Workers R1 · ${statuses[t.status] || t.status}`, true) +
    panel(
      `<h2>Исходное поручение</h2><p class="original-text">${e(t.original_instruction)}</p>${facts(
        [
          ["Текущее описание", t.description],
          ["Когда", displayDate(t.planned_date)],
          ["Статус", statuses[t.status] || t.status],
          ["Цикл", t.cycle],
        ],
      )}${notice("Никакой реальной отправки. DONE задачи не создаёт расход, урожай или складское движение.")}`,
    ) +
    panel(
      `<h2>Назначения</h2>${
        t.assignments
          .map((a) =>
            facts([
              [
                "Работник",
                data.workers.workers.find((w) => w.id === a.worker_id)
                  ?.display_name || a.worker_id,
              ],
              ["Проверенный текст", a.rendered_text],
              ["Язык", a.language],
              ["Отправка", a.delivery_state || "not_sent"],
              ["Подтверждение", a.acknowledged_at || "Не подтверждено"],
            ]),
          )
          .join("") || "<p>Не назначено.</p>"
      }${
        ["draft", "assigned", "reopened"].includes(t.status)
          ? form(
              "assignment",
              `${select(
                "Работник",
                "worker",
                data.workers.workers.filter((w) => w.active),
                t.prepared_assignment?.worker_id || "",
              )}${select(
                "Язык текста",
                "language",
                [
                  { value: "ru", label: "Русский" },
                  { value: "tr", label: "Турецкий" },
                  { value: "fa", label: "Дари" },
                  { value: "uz", label: "Узбекский" },
                ],
                t.prepared_assignment?.language || "ru",
              )}${area("Проверенный текст для работника", "rendered_text", t.prepared_assignment?.rendered_text || t.original_instruction)}<label class="check-label"><input name="reviewed" type="checkbox" required><span>Я проверил содержание и язык. Это внутреннее назначение TEST.</span></label>`,
              "Сохранить назначение",
            )
          : ""
      }`,
    ) +
    panel(
      `<h2>Отчёты</h2>${t.reports.map((r) => `<div class="list-row"><strong>${e(r.claim)} · ${e(r.state)}</strong><p class="original-text">${e(r.original_text)}</p>${r.state === "submitted" ? btn("Проверить отчёт", "review-report:" + r.id) : ""}</div>`).join("") || "<p>Отчётов пока нет.</p>"}<div id="action-error" class="form-error" role="alert"></div><div class="page-actions">${t.status === "reported" ? btn("Завершить после проверки", "worker-action:complete", "primary") : ""}${["completed", "cancelled"].includes(t.status) ? btn("Вернуть в работу", "worker-action:reopen") : btn("Отменить поручение", "worker-action:cancel")}</div><details><summary>История жизненного цикла</summary>${(
        t.actions || []
      )
        .map((a) =>
          facts([
            ["Действие", a.kind],
            ["Причина", a.reason],
            ["Цикл", a.cycle],
          ]),
        )
        .join("")}</details>`,
    )
  );
}
function more() {
  return (
    Header("Ещё", "Возможности этой версии") +
    panel(
      `<div class="stack">${[
        ["Финансы", "finance"],
        ["Урожай / Депо", "harvest"],
        ["Склад", "inventory"],
        ["Работники R1", "workers"],
        ["Источники", "sources"],
      ]
        .map(([l, r]) => link(e(l) + " " + icon("arrow"), r, "btn secondary"))
        .join("")}</div>`,
    ) +
    panel(
      `<h2>Границы TEST</h2><div class="capabilities">${Object.entries(
        CAPABILITIES,
      )
        .map(([k, v]) => `<span>${e(k)}<strong>${v}</strong></span>`)
        .join(
          "",
        )}</div>${notice("Fertilizer Builder остаётся отдельным синтетическим DEMO. Его цифры не участвуют в подключённых показателях.")}${'<a class="btn secondary" href="index.html#inventory" target="_blank" rel="noopener">Открыть отдельный Fertilizer DEMO</a>'}<p class="note">Физический iPhone, камера, клавиатура и микрофон требуют отдельной проверки владельца. Канонические требования не изменены.</p>${btn("Выйти", "logout", "secondary")}`,
    )
  );
}
async function render() {
  const token = ++epoch;
  clearTimeout(readTimer);
  estimateReview=null;
  sheetRoot.innerHTML = "";
  current = null;
  draft = null;
  // Navigation can arrive while authenticated identity is still loading.
  // Never open a draft under an unverified/null owner scope.
  if (!ready) {
    shell(Header("Загружаем", "Проверяем TEST-сессию"), true);
    return;
  }
  const [page, arg, arg2, arg3] = route.split("/");
  shell(
    Header("Загружаем", "CONNECTED TEST") +
      panel('<p role="status">Читаем сохранённые данные…</p>'),
  );
  try {
    let body;
    if (page === "home") body = home();
    else if (page === "review" && arg) {
      {
        const loaded = await api.entityGet(
          "/owner/confirmations/" + arg + "/preview",
        );
        if (token !== epoch) return;
        current = loaded;
      }
      body = proposalDetail(current);
    } else if (page === "review") body = reviewList();
    else if (page === "sources") body = await sourceList();
    else if (page === "source") {
      {
        const loaded = await api.entityGet("/owner/captures/" + arg);
        if (token !== epoch) return;
        current = loaded;
      }
      body = sourceDetail(current);
      if (["QUEUED", "RUNNING"].includes(current.job?.state))
        readTimer = setTimeout(() => {
          if (route === `source/${arg}` && !busy) render();
        }, 2500);
    } else if (page === "capture") {
      {
        const loaded = await getDraft(route);
        if (token !== epoch) return;
        draft = loaded;
      }
      body = captureForm(arg || "text");
    } else if (page === "money") {
      const filters=financeSelection(route,localDay());
      const loaded=await api.entityGet("/finance/analytics?"+financeQuery(filters));
      if(token!==epoch)return;
      current={...loaded,selection:filters};
      body=financeAnalyticsView(loaded,filters);
      lastFinanceRoute=financeRoute(filters);
      await api.draft("finance-navigation",{route:lastFinanceRoute});
      if(token!==epoch)return;
    } else if (page === "money-document") {
      const loaded=await api.entityGet("/finance/analytics/documents/"+arg);
      if(token!==epoch)return;
      current=loaded;
      let back=lastFinanceRoute;
      back=financeBackRoute(arg2,back);
      body=financeDocumentView(loaded,back);
    } else if (page === "finance") body = finance(arg);
    else if (page === "settle") {
      if (!data.home) throw Error("Финансовые данные не загружены");
      {
        const loaded = await getDraft(route, { date: localDay() });
        if (token !== epoch) return;
        draft = loaded;
      }
      body = settleForm(arg, arg2, arg3);
    } else if (page === "payment") {
      {
        const loaded = await api.get("/v1/payments/" + arg);
        if (token !== epoch) return;
        current = loaded;
      }
      body = paymentDetail(current);
    } else if (page === "harvest") {
      const filters=harvestSelection(route,localDay());
      const loaded=await api.entityGet("/harvest/monthly?"+harvestQuery(filters));
      if(token!==epoch) return;
      current={...loaded,selection:filters};
      body=monthlyHarvestView(loaded,filters);
      lastHarvestRoute=harvestRoute(filters);
      await api.draft("harvest-navigation",{route:lastHarvestRoute});
      if(token!==epoch) return;
    }
    else if (page === "quick" && arg === "harvest") {
      {
        const loaded = await getDraft(route, { date: localDay() });
        if (token !== epoch) return;
        draft = loaded;
      }
      body = reportForm();
    } else if (page === "report") {
      const report = await api.entityGet("/harvest/reports/"+arg);
      if (!report)
        throw Error("Срез не найден в загруженной истории. Обновите данные.");
      const evidence = await api.entityGet(
        "/harvest/evidence/" + report.evidence_id + "/metadata",
      );
      if (token !== epoch) return;
      current = { ...report, original_evidence: evidence };
      body = reportDetail(current);
    } else if (page === "attach-depot") {
      const report = arg === "new" ? null : await api.entityGet("/harvest/reports/"+arg);
      if(token!==epoch) return;
      current=report;
      {
        const loaded = await getDraft(route, { date: current?.event_date || localDay(), line_count: "1" });
        if (token !== epoch) return;
        draft = loaded;
      }
      body = depotForm(current);
    } else if (page === "edit-depot") {
      const proposal=await api.entityGet('/owner/confirmations/'+arg+'/preview');
      const saved=await api.readDraft(route) || await api.readDraft('depot-editor:'+arg);
      if(token!==epoch) return;
      current=proposal;
      draft=saved;
      if(!draft?.id) {
        const restored=await getDraft(route,{date:proposal.revision.payload.event_date,source_id:proposal.source.id,proposal_id:proposal.id,line_count:String(proposal.revision.payload.lines.length || 1)});
        if(token!==epoch) return;
        draft=restored;
        const f=proposal.revision.payload;
        Object.assign(draft,{depot:f.depot_id,currency:f.currency,link_evidence:f.link_evidence,stated_total:f.stated_total,alacak_value:f.alacak_value,due_date:f.due_date,document_number:f.document_number});
        f.lines.forEach((l,i)=>{const pre=i?`line_${i}_`:'';Object.assign(draft,{[pre+'quality']:l.original_quality_name,[pre+'quality_category']:f.qualities[i],[pre+'kg']:l.net_weight_kg,[pre+'price']:l.unit_price,[pre+'stated_amount']:l.stated_amount,[pre+'count']:l.count,[pre+'source_line']:l.source_line});});
      }
      body=depotForm(null);
    } else if (['depot-links','depot-estimate','depot-attachment'].includes(page)) {
      const loaded=await api.entityGet('/harvest/settlements/'+arg);
      const j=await api.entityGet('/harvest/settlements/'+arg+'/journey');
      const initial={};for(const bid of j.batch_ids) initial['batch_'+bid]='on';
      const d=await getDraft(route,initial);
      if(token!==epoch) return;
      current={...loaded,journey:j};draft=d;body=journeyForm(page.slice(6),current);
    } else if (page === "depot") {
      {
        const loaded = await api.entityGet("/harvest/settlements/" + arg);
        if (token !== epoch) return;
        const journey=await api.entityGet("/harvest/settlements/"+arg+"/journey");
        if(token!==epoch) return;
        current = {...loaded,journey};
      }
      body = depotDetail(current);
    } else if(page==='invoice-new') {
      const receipt=await api.entityGet('/purchases/'+arg),saved=await getDraft(route,{intent:''});
      if(token!==epoch)return;current=receipt;draft=saved;body=invoiceNewForm(receipt);
    } else if(page==='invoice-source') {
      const receipt=await api.entityGet('/purchases/'+arg),source=await api.entityGet('/owner/captures/'+arg2);
      const proposals=await Promise.all((source.proposals||[]).filter(p=>['PAYABLE','INVOICE_MATCH'].includes(p.action_type)).map(p=>api.entityGet('/owner/confirmations/'+p.id+'/preview')));
      const payable=proposals.find(p=>p.action_type==='PAYABLE'),match=proposals.find(p=>p.action_type==='INVOICE_MATCH'),currency=arg3||payable?.revision.payload.currency||'TRY';
      const finance=await api.entityGet('/finance/analytics?date_from='+localDay()+'&date_to='+localDay()+'&currency='+encodeURIComponent(currency));
      if(!invoiceTargetMatches(arg,source,match))throw Error('Источник не связан с этим приходом в текущей ревизии. Откройте выбранный приход из истории поставок.');
      const saved=await getDraft(route,{});if(token!==epoch)return;current={receipt,source,payable,match,documents:finance.debts.documents};draft=saved;body=invoiceWorkflowView(current);
      if(!match&&['QUEUED','RUNNING'].includes(source.job?.state))readTimer=setTimeout(()=>{if(token===epoch&&!busy&&!document.activeElement?.closest('form'))render();},1500);
    } else if (['purchases','stock-history'].includes(page)) {
      const selection=historySelection(route),response=await api.entityGet((page==='purchases'?'/purchases':'/inventory/history')+'?'+historyQuery(selection));
      if(token!==epoch)return;current={...response,selection};body=purchaseHistoryView(response,selection);
    } else if (page==='purchase'||page==='stock-event') {
      const response=await api.entityGet(page==='purchase'?'/purchases/'+arg:'/inventory/history/'+arg);
      if(token!==epoch)return;current=response;
      const back=arg2?decodeURIComponent(arg2):(page==='purchase'?'purchases':'stock-history');
      body=page==='purchase'?purchaseDetailView(response,back):stockEventView(response,back);
    } else if (page === "inventory") body = inventory();
    else if (page === "quick" && ["receipt","transfer"].includes(arg)) {
      if (!data.inventory) throw Error("Склад не загружен");
      {
        const loaded = await getDraft(route, {
          date: localDay(),
          product: arg2 || "",
        });
        if (token !== epoch) return;
        draft = loaded;
      }
      body = arg === "transfer" ? transferForm(arg2) : receiptForm(arg2);
    } else if (page === "receipt") {
      {
        const loaded = await api.entityGet("/inventory/events/" + arg);
        if (token !== epoch) return;
        current = loaded;
      }
      body = receiptDetail(current);
    } else if (page === "tasks") {
      {
        const loaded = await api.entityGet(
          `/owner/tasks?state=${arg === "DONE" ? "DONE" : "TO_DO"}&offset=${Number(arg2) || 0}&limit=20`,
        );
        if (token !== epoch) return;
        current = loaded;
      }
      body = personalList(current);
    } else if (page === "personal") {
      {
        const loaded = await api.entityGet("/owner/tasks/" + arg);
        if (token !== epoch) return;
        current = loaded;
      }
      body = personalDetail(current);
    } else if (page === "workers") body = workers();
    else if (page === "quick" && arg === "worker") {
      {
        const loaded = await getDraft(route, { date: localDay() });
        if (token !== epoch) return;
        draft = loaded;
      }
      body = workerForm();
    } else if (page === "worker-task") {
      {
        const loaded = await api.entityGet("/tasks/" + arg);
        if (token !== epoch) return;
        current = loaded;
      }
      body = workerDetail(current);
    } else if (page === "more") body = more();
    else
      body =
        Header("Недоступно") +
        notice("NOT AVAILABLE · этот сценарий пока не подключён.") +
        link("На главную", "home", "btn primary");
    if (token !== epoch) return;
    shell(body);
    document.querySelector("#main").dataset.renderedRoute = route;
    document.querySelector("#main")?.focus({ preventScroll: true });
    if(/^(harvest|report|depot|money|money-document|purchases|purchase|stock-history|stock-event)(\/|$)/.test(route) && harvestScroll.has(route))
      window.scrollTo({top:harvestScroll.get(route),behavior:'instant'});
  } catch (error) {
    if (token === epoch) shell(errorBox(error));
  }
}
function values(form) {
  const result=Object.fromEntries(new FormData(form).entries());
  if(form.dataset.form==='depot' || form.dataset.form?.startsWith('journey-'))
    for(const box of form.querySelectorAll('input[type=checkbox][name]')) result[box.name]=box.checked?'on':'';
  return result;
}
function showError(error, node) {
  const target =
    node?.querySelector(".form-error") ||
    document.querySelector("#action-error") ||
    document.querySelector("main");
  if (target) {
    target.textContent = error.message;
    target.dataset.validationField = error.field || "";
    const field = error.field && node?.querySelector(`[name="${error.field}"]`);
    const errorRoute = route, errorEpoch = epoch, generation = api.generation;
    if (field) requestAnimationFrame(() => {
      if (!node.isConnected || node.inert || !field.isConnected ||
          route !== errorRoute || epoch !== errorEpoch || api.generation !== generation) return;
      field.focus({preventScroll:true});
      field.scrollIntoView({block:"center",behavior:"instant"});
    });
  }
}
async function action(run, node = null) {
  if (busy) return;
  busy = true;
  activeActionRoute = route;
  const previousInert = node?.inert;
  if (node) node.inert = true;
  const buttons = [...document.querySelectorAll("button")];
  buttons.forEach((b) => {
    b.dataset.wasDisabled = b.disabled ? "1" : "0";
    b.disabled = true;
  });
  try {
    await run();
  } catch (error) {
    showError(error, node);
    if (error.status === 409) {
      toast("Данные изменились. Обновите и проверьте текущую версию.");
      const target =
        node?.querySelector(".form-error") ||
        document.querySelector("#action-error");
      if (target) {
        const refresh = document.createElement("button");
        refresh.type = "button";
        refresh.className = "btn secondary";
        refresh.dataset.action = "refresh";
        refresh.textContent = "Обновить текущую запись";
        target.append(document.createElement("br"), refresh);
      }
    }
  } finally {
    busy = false;
    activeActionRoute = null;
    if (node) node.inert = previousInert;
    buttons.forEach((b) => {
      b.disabled = b.dataset.wasDisabled === "1";
      delete b.dataset.wasDisabled;
    });
    syncReceiptConfirmation();
  }
}
async function saveDraft(snapshot) {
  const { draft, route, fields, file } = snapshot;
  if (!draft) return;
  Object.assign(draft, fields);
  if (file) draft.file = await fileSource(file);
  await api.draft(route, draft);
}
async function clearDraft(id) {
  await api.store.delete(`${api.scope}:draft:${id}`);
}
async function finish(to, message) {
  const origin = activeActionRoute;
  await refreshData();
  toast(message);
  if (origin && route !== origin) return;
  go(to);
}
function requireText(value, label) {
  if (!String(value || "").trim()) throw Error("Укажите " + label + ".");
  return String(value).trim();
}
function integerOrUnknown(value) {
  if (value === "" || value == null) return null;
  if (
    !/^\d+$/.test(String(value)) ||
    Number(value) < 1 ||
    !Number.isSafeInteger(Number(value))
  )
    throw Error("Укажите положительное целое количество.");
  return Number(value);
}
async function submitForm(formEl, snapshot) {
  const { draft, current, route } = snapshot;
  const submittedDraft = draft,
    submittedCurrent = current;
  const name = formEl.dataset.form,
    v = snapshot.fields,
    savedRoute = route;
  if (draft) await saveDraft(snapshot);
  if (name === 'history-filters') {
    if(v.date_from&&v.date_to&&v.date_from>v.date_to)throw Error('Начало периода должно быть не позже окончания.');
    go(historyRoute(current.selection,{...v,offset:0}));
  } else if (name === "finance-filters") {
    if(!v.date_from || !v.date_to || v.date_from>v.date_to)throw Error("Проверьте даты: начало периода должно быть не позже окончания.");
    go(financeRoute(current.selection,{date_from:v.date_from,date_to:v.date_to,currency:v.currency,greenhouse_id:v.greenhouse_id || null,money_account_id:v.currency!==current.selection.currency?null:(Object.hasOwn(v,"money_account_id")?(v.money_account_id || null):current.selection.money_account_id)}));
  } else if (name === "harvest-filters") {
    go(harvestRoute(current.selection,{season_start_year:Number(v.season_start_year),greenhouse_id:v.greenhouse_id || null,currency:v.currency || current.selection.currency,allocation:v.allocation || "all",month:null}));
  } else if (name === 'invoice-source') {
    if(!['operating_expense','existing_payable'].includes(draft.intent))throw Object.assign(Error('Выберите назначение документа.'),{field:'intent'});
    if(!draft.text?.trim()&&!draft.file)throw Object.assign(Error('Добавьте текст документа или оригинальный файл.'),{field:'text'});
    const body={client_capture_id:draft.id,captured_at:draft.captured_at,timezone:'Europe/Istanbul',capture_context:'supplier_invoice',invoice_intent:draft.intent,invoice_receipt_id:current.id,mode:draft.file?(draft.file.mime_type==='application/pdf'?'file':'photo'):'text',...(draft.text?.trim()?{text:draft.text}:{}),...(draft.file||{})};
    const source=await api.entityPost('/owner/captures',body,draft.id);
    await clearDraft(savedRoute);await finish('invoice-source/'+current.id+'/'+source.id,'Оригинал счёта сохранён. Проведённых операций ещё нет.');
  } else if(name==='invoice-edit-fact') {
    const proposal=current.payable,key=formEl.dataset.field,payload={...proposal.revision.payload};
    let value=v.value===''?null:v.value;if(key==='amount'&&value)value=exactMoney(value);payload[key]=value;
    await api.entityPost('/owner/confirmations/'+proposal.id+'/revise',{expected_version:proposal.version,payload,reason:'Владелец уточнил один факт счёта: '+fieldLabel(key)},proposal.id+':invoice-edit:'+proposal.version);
    sheetRoot.innerHTML='';await finish(savedRoute,'Уточнение сохранено отдельной ревизией. Оригинал не менялся.');
  } else if(name==='invoice-link') {
    const proposal=current.match,document=current.documents.find(d=>d.obligation_id===v.obligation),effectId=current.payable?.confirmed_effect?.effect?.id;
    if(!document||document.direction!=='payable'||document.status!=='posted'||(effectId&&document.obligation_id!==effectId))throw Object.assign(Error('Выберите подтверждённый долг нужного документа.'),{field:'obligation'});
    const payload={...proposal.revision.payload,receipt_id:current.receipt.id,finance_obligation_id:document.obligation_id,note:v.note||null};
    await api.entityPost('/owner/confirmations/'+proposal.id+'/revise',{expected_version:proposal.version,payload,reason:'Владелец явно выбрал приход и уже учтённый финансовый документ'},proposal.id+':invoice-link:'+proposal.version);
    await clearDraft(savedRoute);await finish(savedRoute,'Связь подготовлена к отдельному подтверждению. Движений и оплаты нет.');
  } else if (name === "capture") {
    const kind = route.split("/")[1],
      body = {
        client_capture_id: draft.id,
        captured_at: draft.captured_at,
        timezone: "Europe/Istanbul",
        ...(kind === "expense" ? { capture_context: "quick_expense" } : {}),
        mode: draft.file
          ? draft.file.mime_type === "application/pdf"
            ? "file"
            : "photo"
          : kind === "voice"
            ? "voice_transcript"
            : "text",
        ...(draft.text?.trim() ? { text: draft.text } : {}),
        ...(draft.file || {}),
      };
    if (!body.text && !body.file_base64)
      throw Error("Добавьте исходный текст или файл.");
    const s = await api.entityPost("/owner/captures", body, draft.id);
    await clearDraft(savedRoute);
    await finish("source/" + s.id, "Оригинал сохранён на сервере.");
  } else if (name === "payment") {
    const [_, direction, party, currency] = route.split("/"),
      body = paymentPayload({
        entity: api.entity,
        party,
        direction: direction === "receivable" ? "in" : "out",
        currency,
        account: accounts().find(
          (a) => (a.money_account_id || a.id) === draft.account,
        ),
        date: draft.date,
        amount: paymentTotal(draft),
        allocations: Object.entries(draft)
          .filter(([k]) => k.startsWith("allocation_"))
          .map(([k, value]) => ({
            obligation_id: k.slice(11),
            amount: value ? String(value).replace(",", ".") : "0",
          })),
        obligations: data.home.finance.obligations,
      });
    const result = await api.mutate("/v1/payments", body, draft.id);
    await clearDraft(savedRoute);
    await finish(
      "payment/" + result.id,
      "Черновик оплаты сохранён. Нужна отдельная проверка.",
    );
  } else if (name === "report") {
    const place = areas().find((a) => a.id === draft.greenhouse);
    if (!place) throw Object.assign(Error("Выберите зарегистрированную теплицу."), {field:"greenhouse"});
    if (!draft.date) throw Object.assign(Error("Укажите дату среза."), {field:"date"});
    if (!String(draft.reporter || "").trim()) throw Object.assign(Error("Укажите, кто сообщил."), {field:"reporter"});
    const body = {
      event_date: draft.date,
      original_greenhouse: place.name,
      work_area_id: place.id,
      cut_count: integerOrUnknown(draft.count),
      field_weight_kg: draft.kg ? quantity(draft.kg) : null,
      reporter: (requireText(draft.reporter, "кто сообщил"), String(draft.reporter)),
      notes: draft.original || null,
      evidence: {
        source: "owner_form:v1:field_harvest",
        source_reference: draft.id,
        structured_original: ownerFormSource("field_harvest", draft, {
          event_date: draft.date, original_greenhouse: place.name,
          work_area_id: place.id, cut_count: draft.count,
          field_weight_kg: draft.kg, reporter: draft.reporter,
          notes: draft.original,
        }),
      },
    };
    const result = await api.entityPost("/harvest/reports", body, draft.id);
    await clearDraft(savedRoute);
    await finish(
      "report/" + result.id,
      "Полевое наблюдение сохранено на сервере.",
    );
  } else if (name === 'edit-depot-line') {
    const i=Number(formEl.dataset.index), payload=structuredClone(current.revision.payload);
    const stated=v.stated_amount?exactMoney(v.stated_amount):null;
    if(v.confirmed_amount==='stated' && stated==null) throw Error('Укажите сумму документа.');
    payload.lines[i]={...payload.lines[i],original_quality_name:requireText(v.quality,'качество'),net_weight_kg:quantity(v.kg),unit_price:exactMoney(v.price),stated_amount:stated,confirmed_amount:v.confirmed_amount==='stated'?stated:null,count:integerOrUnknown(v.count)};
    payload.qualities[i]=v.quality_category || null;payload.quality_reviewed=payload.qualities.every(Boolean);payload.arithmetic_reviewed=false;
    await api.entityPost('/owner/confirmations/'+current.id+'/revise',{expected_version:current.version,payload,reason:'Исправление одной строки владельцем'},current.id+':line:'+i+':'+current.version);
    for(const key of ['depot-editor:'+current.id,'edit-depot/'+current.id]) {
      const edited=await api.readDraft(key);
      if(edited) {const pre=i?`line_${i}_`:'';Object.assign(edited,{[pre+'quality']:v.quality,[pre+'quality_category']:v.quality_category,[pre+'kg']:v.kg,[pre+'price']:v.price,[pre+'stated_amount']:v.stated_amount,[pre+'confirmed_amount']:v.confirmed_amount,[pre+'count']:v.count});await api.draft(key,edited);}
    }
    sheetRoot.innerHTML=''; await render();
  } else if (name === "depot") {
    const report=current?.revision ? null : current;
    if(!draft.file && !draft.source_id) throw Object.assign(Error('Выберите оригинальный JPEG, PNG или PDF.'),{field:'file'});
    const selectedReports=[...(report?[report.id]:[]),...Object.entries(draft).filter(([k,v])=>k.startsWith('report_') && v==='on').map(([k])=>k.slice(7))];
    const lines=[], qualities=[];
    for(let i=0;i<Number(draft.line_count || 1);i++) {
      const pre=i?`line_${i}_`:''; const get=k=>draft[pre+k];
      const stated=get('stated_amount')?exactMoney(get('stated_amount')):null;
      if(get('confirmed_amount')==='stated' && !stated) throw Object.assign(Error('Укажите выбранную сумму документа.'),{field:pre+'stated_amount'});
      lines.push({original_quality_name:requireText(get('quality'),'качество из документа'),net_weight_kg:quantity(get('kg')),unit_price:exactMoney(get('price')),stated_amount:stated,confirmed_amount:get('confirmed_amount')==='stated'?stated:null,count:integerOrUnknown(get('count')),source_line:get('source_line') || get('quality'),allocations:[]});
      qualities.push(get('quality_category') || null);
    }
    let proposal;
    if(draft.proposal_id) proposal=await api.entityGet('/owner/confirmations/'+draft.proposal_id+'/preview');
    else {
      if(!draft.source_id) {
        const source=await api.entityPost('/owner/captures',{client_capture_id:draft.id,captured_at:draft.captured_at,timezone:'Europe/Istanbul',capture_context:'depot_receipt',mode:draft.file.mime_type==='application/pdf'?'file':'photo',...(draft.caption?.trim()?{text:draft.caption}:{}),...draft.file},draft.id+':source');
        draft.source_id=source.id; await api.draft(savedRoute,draft);
      }
      for(let i=0;i<20;i++) {
        const source=await api.entityGet('/owner/captures/'+draft.source_id);
        const found=(source.proposals||[]).find(p=>p.action_type==='DEPOT_RECEIPT');
        if(found) {proposal=await api.entityGet('/owner/confirmations/'+found.id+'/preview');break;}
        if(source.job?.state==='FAILED') throw Error('Оригинал сохранён, подготовка завершилась ошибкой. Повторите подготовку в источнике. Введённые данные сохранены на телефоне.');
        await new Promise(resolve=>setTimeout(resolve,500));
      }
      if(!proposal) throw Error('Оригинал сохранён. Подготовка ещё выполняется; повторите открытие проверки. Данные формы сохранены.');
      draft.proposal_id=proposal.id; await api.draft(savedRoute,draft);
    }
    let batchIds=proposal.revision.payload.batch_ids || [];
    if(selectedReports.length) {
      const batch=await api.entityPost('/harvest/batches',{event_date:draft.date,label:'Явно выбранные полевые срезы',report_ids:[...new Set(selectedReports)],link_evidence:requireText(draft.link_evidence,'основание связи')},draft.id+':batch');
      batchIds=[batch.id];
    }
    const payload={...proposal.revision.payload,depot_id:requireText(draft.depot,'депо'),event_date:draft.date,currency:draft.currency || 'TRY',batch_ids:batchIds,link_evidence:batchIds.length?draft.link_evidence:null,original_greenhouse:report?.original_greenhouse || proposal.revision.payload.original_greenhouse || null,lines,qualities,quality_reviewed:qualities.every(Boolean),arithmetic_reviewed:false,stated_total:draft.stated_total?exactMoney(draft.stated_total):null,alacak_value:draft.alacak_value?exactMoney(draft.alacak_value):null};
    if(draft.due_date) payload.due_date=draft.due_date; else delete payload.due_date;
    if(draft.document_number) payload.document_number=draft.document_number; else delete payload.document_number;
    await api.entityPost('/owner/confirmations/'+proposal.id+'/revise',{expected_version:proposal.version,payload,reason:'Владелец ввёл проверяемую расшифровку документа; оригинал сохранён отдельно'},draft.id+':review:'+proposal.version);
    await api.draft('depot-editor:'+proposal.id,{...draft,report_id:report?.id || draft.report_id || null});
    await finish('review/'+proposal.id,'Оригинал и текущая расшифровка сохранены. Проверьте арифметику и эффект.');
  } else if(name==='journey-links' || name==='journey-attachment' || name==='journey-estimate') {
    const kind=name.slice(8),body={expected_version:current.journey.version,confirmed:true,reason:requireText(draft.reason,'основание решения')};
    if(kind==='links') {
      body.batch_ids=Object.entries(draft).filter(([k,x])=>k.startsWith('batch_') && x==='on').map(([k])=>k.slice(6));
      const reportIds=Object.entries(draft).filter(([k,x])=>k.startsWith('report_') && x==='on').map(([k])=>k.slice(7));
      if(reportIds.length) {
        const b=await api.entityPost('/harvest/batches',{event_date:current.event_date,label:'Поздняя явно подтверждённая связь',report_ids:reportIds,link_evidence:body.reason},draft.id+':batch');
        body.batch_ids.push(b.id);
      }
    } else if(kind==='attachment') {
      if(!draft.file) throw Error('Выберите отдельный оригинальный файл.');
      body.evidence={source:'owner:harvest-attachment',source_reference:draft.id,raw_text:draft.caption || null,...draft.file};
    } else {
      body.basis=Object.entries(draft).filter(([k,x])=>k.startsWith('basis_') && k!=='basis_description' && x).map(([k,x])=>({work_area_id:k.slice(6),basis_count:quantity(x)}));
      body.total_basis_count=quantity(draft.total_basis_count);body.basis_description=requireText(draft.basis_description,'основание пропорций');
      const previewBody={...body};delete previewBody.confirmed;
      const previewGeneration=api.generation;
      const preview=await api.harvestEstimatePreview(current.id,previewBody);
      if(savedRoute!==location.hash.slice(1)) return;
      draft.estimate_body=body;draft.estimate_preview=preview;await api.draft(savedRoute,draft);
      if(savedRoute!==location.hash.slice(1) || previewGeneration!==api.generation) return;
      const rows=preview.lines || [];
      estimateReview={body:structuredClone(body),draftId:draft.id,settlementId:current.id,route:savedRoute,generation:previewGeneration};
      openSheet('Проверить оценку',`<p>Итог документа: ${e(preview.total_weight_kg)} кг · ${e(preview.total_amount)} ${e(current.currency)}. Следующие доли — ESTIMATED.</p><p>Комментарий к основанию: ${e(body.basis_description)}</p><div class="estimate-basis"><strong>Подтверждаемые пропорции</strong>${body.basis.map(x=>'<p>'+e(areas().find(a=>a.id===x.work_area_id)?.name || 'Неизвестное место')+': '+e(x.basis_count)+' веток</p>').join('')}<p>Всего: ${e(body.total_basis_count)} веток. Неизвестная доля: ${e(preview.unknown_basis_count)}.</p></div>${rows.map(x=>'<p><strong>'+e(x.original_quality_name || 'Строка качества')+'</strong> · '+e(x.work_area_id?(areas().find(a=>a.id===x.work_area_id)?.name || 'Неизвестное место'):'Нераспределено')+' · '+e(x.net_weight_kg)+' кг · '+e(x.amount)+' '+e(current.currency)+'</p>').join('')}<p class="note">Отдельный расчёт каждой строки качества. Деньги не поступают; финансовый период не меняется.</p><div id="action-error" class="form-error"></div>${btn('Подтвердить оценку','confirm-estimate','primary full')}`);sheetRoot.querySelector('[data-action=close-sheet]')?.focus();return;
    }
    await api.entityPost('/harvest/settlements/'+current.id+'/'+(kind==='links'?'links':'attachments'),body,draft.id+':'+kind);
    await clearDraft(savedRoute);await finish('depot/'+current.id,'Новая версия сохранена. Учётный эффект не повторялся.');
  } else if (name === "post-depot") {
    const required = current.issues.filter((x) => x !== "receipt_draft");
    if (required.some((x) => v["issue_" + x] !== "on"))
      throw Error("Проверьте каждое отмеченное расхождение.");
    const body = {
      expected_version: current.version,
      confirmed: true,
      reviewed_issues: required,
      review_reason: required.length
        ? requireText(v.reason, "решение по расхождениям")
        : v.reason || null,
      cash_amount: "0.00",
      advances: [],
    };
    await api.entityPost(
      "/harvest/settlements/" + current.id + "/post",
      body,
      current.id + ":post:" + current.version,
    );
    await finish(
      "depot/" + current.id,
      "Чек подтверждён сервером. Продажа и долг сохранены.",
    );
  } else if (["receipt","transfer"].includes(name)) {
    const p = data.inventory.products.find((p) => p.id === draft.product),
      location = data.inventory.locations.find((x) => x.id === draft.location);
    if (!p || !location)
      throw Object.assign(Error("Выберите товар и отдельно настроенное место хранения."), {field: !p ? "product" : "location"});
    if (!draft.quantity) throw Object.assign(Error("Укажите количество прихода."), {field:"quantity"});
    if (!draft.date) throw Object.assign(Error("Укажите дату прихода."), {field:"date"});
    const destination = name === "transfer" ? data.inventory.locations.find(x=>x.id===draft.destination) : null;
    if(name === "transfer" && (!destination || destination.id===location.id)) throw Object.assign(Error("Выберите другое настроенное место назначения."),{field:"destination"});
    const sourceKind = name === "transfer" ? "inventory_transfer" : "inventory_receipt";
    const body = {
      kind: name,
      event_date: draft.date,
      lines: [
        {
          product_id: p.id,
          original_product_name: p.name,
          quantity: quantity(draft.quantity),
          unit: p.base_unit,
          location_id: location.id,
          ...(destination ? {destination_id:destination.id} : {}),
          unit_cost: null,
          currency: null,
        },
      ],
      notes: null, // Optional comment belongs to the immutable source, not the physical payload.
      evidence: {
        source: name === "transfer" ? "owner_form:v1:inventory_transfer" : "owner_form:v1:inventory_receipt",
        locator: draft.id,
        structured_original: ownerFormSource(sourceKind, draft, {
          event_date: draft.date, product_id: p.id, original_product_name: p.name,
          location_id: location.id, ...(destination ? {destination_id:destination.id} : {}), quantity: draft.quantity, unit: p.base_unit,
          notes: draft.original,
        }),
        ...(draft.file || {}),
      },
    };
    const result = await api.entityPost("/inventory/events", body, draft.id);
    await clearDraft(savedRoute);
    await finish(
      "receipt/" + result.id,
      name === "transfer" ? "Черновик перемещения сохранён. Движений пока нет." : "Черновик прихода сохранён. Движений пока нет.",
    );
  } else if (name === "product") {
    const body = {
      name: requireText(v.name, "название товара"),
      base_unit: v.unit,
      source: "owner:r3-connected-test-product",
      aliases: [],
      category: "fertilizer",
      currency: null,
    };
    const result = await api.entityPost(
      "/inventory/products",
      body,
      draft.id + ":product",
    );
    submittedDraft.product = result.id;
    await api.draft(savedRoute, submittedDraft);
    await refreshData();
    if ((location.hash.slice(1) || "home") === savedRoute) {
      sheetRoot.innerHTML = "";
      await render();
    }
    toast("Товар сохранён. Выберите независимое место хранения.");
  } else if (name === "worker") {
    const instruction = requireText(draft.original, "исходное поручение");
    const result = await api.entityPost(
      "/tasks",
      {
        title: requireText(draft.title, "название"),
        original_instruction: instruction,
        description: instruction,
        greenhouse_id: draft.greenhouse || null,
        planned_date: draft.date || null,
        owner_language: "ru",
        category: "general",
        source: "owner:r3-connected-test",
        locator: draft.id,
      },
      draft.id,
    );
    await clearDraft(savedRoute);
    await finish(
      "worker-task/" + result.id,
      "Исходное поручение сохранено. Работнику не отправлено.",
    );
  } else if (name === "assignment") {
    if (v.reviewed !== "on")
      throw Error("Подтвердите проверку текста и языка.");
    const w = data.workers.workers.find((w) => w.id === v.worker);
    if (!w || w.language !== v.language)
      throw Error("Язык должен совпадать с языком выбранного работника.");
    await api.entityPost(
      "/tasks/" + current.id + "/assignments",
      {
        worker_id: w.id,
        rendered_text: requireText(v.rendered_text, "проверенный текст"),
        language: v.language,
        translation_source:
          v.language === "ru" ? "original" : "owner_reviewed_translation",
        translation_version: "manual-r1",
        rendering_reviewed: true,
      },
      current.id + ":assign:" + current.version + ":" + w.id,
    );
    await finish(
      route,
      "Внутреннее назначение TEST сохранено. Внешней отправки нет.",
    );
  } else if (name === "edit-fact") {
    const key = formEl.dataset.field,
      payload = structuredClone(current.revision.payload);
    let value = v.value === "" ? null : v.value;
    if (key === "amount" && value) value = exactMoney(value);
    payload[key] = value;
    await api.entityPost(
      "/owner/confirmations/" + current.id + "/revise",
      {
        expected_version: current.version,
        payload,
        reason: "Владелец уточнил один факт: " + fieldLabel(key),
      },
      current.id + ":edit:" + current.version,
    );
    sheetRoot.innerHTML = "";
    await finish(route, "Новая ревизия сохранена. Оригинал не изменён.");
  } else if (name === "worker-action") {
    const kind = formEl.dataset.kind,
      report = formEl.dataset.report || null;
    const body = {
      kind,
      reason: requireText(v.reason, "причину / результат проверки"),
      expected_version: current.version,
      expected_cycle: current.cycle,
      locator: current.id + ":" + current.version + ":" + kind,
      source: "owner:r3-connected-test",
      ...(report ? { report_id: report } : {}),
    };
    await api.entityPost(
      "/tasks/" + current.id + "/actions",
      body,
      body.locator,
    );
    sheetRoot.innerHTML = "";
    await finish(route, "Действие Workers R1 сохранено.");
  }
}
function openSheet(title, body) {
  sheetRoot.innerHTML = `<div class="focus-sheet" role="dialog" aria-modal="true" aria-label="${e(title)}"><article><div class="status-line"><h2>${e(title)}</h2>${btn(icon("close"), "close-sheet", "icon-button", "close-sheet", 'aria-label="Закрыть"')}</div>${body}</article></div>`;
  sheetRoot.querySelector("input,textarea,select")?.focus();
}
function editFact(key) {
  let control,
    value = current.revision.payload[key];
  const f = data.home?.finance,
    refs = f?.references || [];
  if (key === "money_account_id")
    control = select(
      fieldLabel(key),
      "value",
      accounts()
        .filter((a) => a.currency === current.revision.payload.currency)
        .map((a) => ({
          id: a.money_account_id || a.id,
          name: accountName(a) + " · " + a.currency,
        })),
      value,
    );
  else if (key === "counterparty_id")
    control = select(
      fieldLabel(key),
      "value",
      refs
        .filter((r) => r.kind === "counterparty")
        .map((r) => ({ id: r.id, name: r.original_name || r.name })),
      value,
      "Не указан",
    );
  else if (key === "greenhouse_id")
    control = select(
      fieldLabel(key),
      "value",
      areas().filter((a) => a.kind === "greenhouse"),
      value,
      "Не указана",
    );
  else if (key === "worker_id")
    control = select(fieldLabel(key), "value", data.workers.workers, value);
  else if (key === "payment_purpose")
    control = select("Оплата", "value", [{value:"expense",label:"Оплата действительно состоялась"}], value, "Подтвердите факт оплаты");
  else if (key === "currency")
    control = select(
      fieldLabel(key),
      "value",
      ["TRY", "USD", "EUR", "UAH"].map((x) => ({ value: x, label: x })),
      value,
    );
  else if (key === "language")
    control = select(
      fieldLabel(key),
      "value",
      ["ru", "tr", "fa", "uz"].map((x) => ({ value: x, label: x })),
      value,
    );
  else if (key === "obligation_id")
    control = select(
      fieldLabel(key),
      "value",
      f.obligations
        .filter(
          (o) =>
            o.direction === "payable" &&
            o.counterparty_id === current.revision.payload.counterparty_id &&
            o.currency === current.revision.payload.currency &&
            cents(o.remaining_amount) > 0n,
        )
        .map((o) => ({
          id: o.obligation_id,
          name:
            (o.description || "Долг") +
            " · " +
            money(o.remaining_amount, o.currency),
        })),
      value,
    );
  else if (
    [
      "description",
      "title",
      "original_instruction",
      "rendered_text",
      "note",
    ].includes(key)
  )
    control = area(fieldLabel(key), "value", value);
  else
    control = field(
      fieldLabel(key),
      "value",
      value,
      key.endsWith("date") ? "date" : "text",
      key === "amount" ? 'inputmode="decimal"' : "",
    );
  openSheet(
    "Изменить один факт",
    `<form data-form="edit-fact" data-field="${e(key)}">${control}<p class="note">Изменится только текущая ревизия. Оригинальный источник останется неизменным.</p><div class="form-error" role="alert"></div>${submit("Сохранить факт")}</form>`,
  );
}
async function recover(sourceId, proposal = null) {
  const origin = route;
  let target = proposal?.recovery?.recovery_proposal_id;
  if (!target) {
    const b = proposal
      ? { origin_proposal_id: proposal.id, expected_version: proposal.version }
      : {};
    const r = await api.entityPost(
      "/owner/captures/" + sourceId + "/direct-expense-recovery",
      b,
      sourceId +
        ":recovery" +
        (proposal ? ":" + proposal.id + ":" + proposal.version : ""),
    );
    target = r.id;
  }
  const p = await api.entityGet("/owner/confirmations/" + target + "/preview");
  if (p.source.id !== sourceId || p.action_type !== "DIRECT_EXPENSE")
    throw Error("Связь расхода с оригиналом не подтверждена.");
  await refreshData();
  if (route === origin) go("review/" + p.id);
}
document.addEventListener("submit", (event) => {
  const formEl = event.target.closest("[data-form]");
  if (!formEl) return;
  event.preventDefault();
  const snapshot = submissionSnapshot(
    { draft, current, route },
    values(formEl),
    formEl.querySelector("[name=file]")?.files?.[0] || null,
  );
  action(() => submitForm(formEl, snapshot), formEl);
});
function clearFieldValidation(input) {
  if (typeof input.value !== "string" || typeof input.checkValidity !== "function") return;
  const error = input.closest("[data-form]")?.querySelector(".form-error");
  if (!error || error.dataset.validationField !== input.name || !input.value.trim() || !input.checkValidity()) return;
  if (input.name === "quantity") {
    try { quantity(input.value); } catch { return; }
  }
  error.textContent = "";
  delete error.dataset.validationField;
}
document.addEventListener("input", (event) => {
  clearFieldValidation(event.target);
  const formEl = event.target.closest("[data-form]");
  if (
    !formEl ||
    !draft ||
    ["product", "edit-fact", "edit-depot-line", "invoice-edit-fact"].includes(formEl.dataset.form)
  )
    return;
  if (event.target.type === "file") return;
  const fields = values(formEl);
  delete fields.file;
  Object.assign(draft, fields);
  api.draft(route, draft).catch((error) => showError(error, formEl));
  if (formEl.dataset.form === "payment") {
    const total = document.querySelector("#payment-total");
    if (total) total.textContent = paymentTotal() + " " + route.split("/")[3];
  }
});
document.addEventListener("change", (event) => {
  if (event.target.id === "duplicate-reviewed" && event.target.isConnected) {
    syncReceiptConfirmation();
    return;
  }
  if (event.target.name === "file" && draft) {
    const formEl = event.target.closest("form"),
      file = event.target.files[0];
    if (file) {
      const savedDraft = draft,
        savedRoute = route;
      action(async () => {
        savedDraft.file = await fileSource(file);
        savedDraft.binary = savedDraft.file;
        await api.draft(savedRoute, savedDraft);
        toast("Копия на телефоне. На сервер ещё не сохранено.");
      }, formEl);
    }
  }
  if (
    event.target.name === "worker" &&
    event.target.closest("[data-form=assignment]")
  ) {
    const lang = event.target.closest("form").querySelector("[name=language]");
    lang.value =
      data.workers.workers.find((w) => w.id === event.target.value)?.language ||
      "";
  }
  if (event.target.name === "product" && draft) {
    draft.product = event.target.value;
    const unit = document.querySelector("[name=unit]");
    if (unit)
      unit.value =
        data.inventory.products.find((p) => p.id === draft.product)
          ?.base_unit || "";
  }
});
document.addEventListener('click',event=>{
  if(event.target.closest('a[href^="#"], [data-action="back"]') && /^(harvest|report|depot|money|money-document|purchases|purchase|stock-history|stock-event)(\/|$)/.test(route)) harvestScroll.set(route,scrollY);
},true);
document.addEventListener("click", (event) => {
  const button = event.target.closest("[data-action]");
  if (!button) return;
  // Also suppress defaults for shared navigation/action controls.
  event.preventDefault();
  const command = button.dataset.action;
  const [name, ...rest] = command.split(":"),
    arg = rest.join(":");
  if (name === "radial-toggle") return setRadial(!radial);
  if (name === "radial-close") return setRadial(false);
  if (name === "back") return history.length > 1 ? history.back() : go("home");
  if (name === "close-sheet") {
    estimateReview=null;
    sheetRoot.innerHTML = "";
    return;
  }
  if (name.startsWith("capture-")) return go("capture/" + name.slice(8));
  if (name === 'invoice-edit') {
    const p=current.payable;if(!p||!['amount','currency','event_date','counterparty_id','description','due_date'].includes(arg))return;
    const value=p.revision.payload[arg];let control;
    if(arg==='counterparty_id')control=select('Поставщик','value',(data.home?.finance.references||[]).filter(r=>r.kind==='counterparty').map(r=>({id:r.id,name:r.original_name||r.name})),value);
    else if(arg==='currency')control=select('Валюта','value',['TRY','USD','EUR','UAH'].map(value=>({value,label:value})),value);
    else if(arg==='description')control=area('За что','value',value);
    else control=field(fieldLabel(arg),'value',value,arg.endsWith('date')?'date':'text',arg==='amount'?'inputmode="decimal"':'');
    return openSheet('Уточнить один факт счёта',`<form data-form="invoice-edit-fact" data-field="${e(arg)}">${control}<p class="note">Изменится ревизия, исходник сохранится.</p><div class="form-error" role="alert"></div>${submit('Сохранить факт')}</form>`);
  }
  if (name === "edit") return editFact(arg);
  if(name==='edit-depot-line') {
    const i=Number(arg),l=current.revision.payload.lines[i];
    return openSheet('Исправить строку '+(i+1),`<form data-form="edit-depot-line" data-index="${i}">${field('Качество дословно','quality',l.original_quality_name)}${select('Категория','quality_category',[{value:'HIGHEST',label:'Высшее'},{value:'FIRST',label:'Первое'},{value:'SECOND',label:'Второе'}],current.revision.payload.qualities[i],'Нужно проверить')}${field('Вес, кг','kg',l.net_weight_kg,'text','inputmode="decimal"')}${field('Цена за кг','price',l.unit_price,'text','inputmode="decimal"')}${field('Сумма документа · необязательно','stated_amount',l.stated_amount,'text','inputmode="decimal"')}${select('Использовать сумму','confirmed_amount',[{value:'calculated',label:'Вес × цена'},{value:'stated',label:'Сумма документа'}],l.confirmed_amount===l.stated_amount && l.stated_amount!=null?'stated':'calculated')}${field('ADET · если указан','count',l.count,'number','min="1"')}<p class="note">Оригинал не изменится; арифметику нужно проверить заново.</p><div class="form-error" role="alert"></div>${submit('Сохранить исправление')}</form>`);
  }
  if (name === "add-depot-line" || name === "remove-depot-line") return action(async()=>{
    const count=Number(draft.line_count || 1);
    if(name==='add-depot-line') { if(count>=100) throw Error('Не более 100 строк'); draft.line_count=String(count+1); }
    else {
      const keys=['quality','quality_category','kg','price','stated_amount','confirmed_amount','count','source_line'];
      for(let i=Number(arg);i<count-1;i++) for(const k of keys) draft[`line_${i}_${k}`]=draft[`line_${i+1}_${k}`] || '';
      for(const k of keys) delete draft[`line_${count-1}_${k}`];
      draft.line_count=String(count-1);
    }
    await api.draft(route,draft); await render();
  });
  if (name === "new-product")
    return openSheet(
      "Новый товар",
      form(
        "product",
        field("Название", "name") +
          select(
            "Базовая единица",
            "unit",
            [
              { value: "kg", label: "кг" },
              { value: "L", label: "л" },
              { value: "piece", label: "штука" },
            ],
            "kg",
          ) +
          notice("Товар создаётся без остатка, без склада и без стоимости."),
        "Создать товар",
      ),
    );
  if (name === "fill-allocation") {
    const o = data.home.finance.obligations.find(
      (x) => x.obligation_id === arg,
    );
    draft["allocation_" + arg] = o.remaining_amount;
    const input = document.querySelector(`[name="allocation_${arg}"]`);
    input.value = o.remaining_amount;
    input.dispatchEvent(new Event("input", { bubbles: true }));
    return;
  }
  if (name === "worker-action" || name === "review-report") {
    const kind = name === "review-report" ? "review_report" : arg;
    return openSheet(
      "Проверить действие",
      `<form data-form="worker-action" data-kind="${e(kind)}" data-report="${name === "review-report" ? e(arg) : ""}">${area("Причина / результат проверки", "reason")}${notice("Это внутренний жизненный цикл TEST, без внешней отправки.")}<div class="form-error" role="alert"></div>${submit("Подтвердить действие")}</form>`,
    );
  }
  const snapshot = { draft, current, route };
  action(async () => {
    const { current, draft, route } = snapshot;
    if (name === "refresh") {
      if (!api.scope) {
        config = await api.get("/preview/config");
        api.configure(config);
      }
      const generation = api.generation;
      await refreshData();
      if (generation !== api.generation) return;
      ready = true;
      return render();
    }
    if (name === "logout") {
      await api.request("/preview/logout", { method: "POST" });
      location.assign("/r3/");
      return;
    }
    if (name === "prepare") {
      await api.entityPost(
        "/owner/captures/" + arg + "/retry-preparation",
        {},
        arg + ":retry",
      );
      return render();
    }
    if (name === "recover-source") return recover(arg);
    if (name === "recover") return recover(current.source.id, current);
    if (name === "review-depot-arithmetic") {
      await api.entityPost('/owner/confirmations/'+current.id+'/revise',{expected_version:current.version,payload:{...current.revision.payload,arithmetic_reviewed:true},reason:'Владелец проверил строки, итог, независимые количества и отсутствие оплаты'},current.id+':arithmetic:'+current.version);
      return render();
    }
    if(name==='confirm-estimate') {
      const checked=estimateConfirmation(estimateReview,{route,settlementId:current.id,generation:api.generation});
      await api.entityPost('/harvest/settlements/'+current.id+'/estimates',checked.body,checked.intent);
      estimateReview=null;sheetRoot.innerHTML='';await clearDraft(route);return finish('depot/'+current.id,'Оценка подтверждена отдельной версией. Итог продажи не изменился.');
    }
    if(name==='invoice-choose-link') {
      draft.choose='yes';await api.draft(route,draft);return render();
    }
    if(name==='invoice-confirm-payable'||name==='invoice-confirm-link') {
      const proposal=name==='invoice-confirm-payable'?current.payable:current.match;
      if(!proposal||proposal.revision.missing?.length)throw Error('Сначала уточните необходимые факты.');
      if(name==='invoice-confirm-link'&&(!invoiceTargetMatches(current.receipt.id,current.source,proposal)||!proposal.revision.payload.finance_obligation_id||draft.choose==='yes'))throw Error('Сначала проверьте выбранный документ.');
      await api.entityPost('/owner/confirmations/'+proposal.id+'/confirm',{expected_version:proposal.version,preview_digest:proposal.revision.preview_digest,confirmed:true,reason:name==='invoice-confirm-payable'?'Владелец подтвердил операционный расход и долг без оплаты':'Владелец подтвердил только явную связь счёта и прихода'},proposal.id+':confirm:'+proposal.version);
      return finish(route,name==='invoice-confirm-payable'?'Расход и долг подтверждены сервером. Теперь отдельно проверьте связь с приходом.':'Связь подтверждена. Склад и деньги повторно не проводились.');
    }
    if (name === "confirm-proposal") {
      const confirmed = await api.entityPost(
        "/owner/confirmations/" + current.id + "/confirm",
        {
          expected_version: current.version,
          preview_digest: current.revision.preview_digest,
          confirmed: true,
          reason: "Владелец проверил текущие факты и эффект",
        },
        current.id + ":confirm:" + current.version,
      );
      if(current.action_type==='DEPOT_RECEIPT' && confirmed.effect?.id) {
        for(const saved of await api.store.all()) if(saved.scope===api.scope && saved.type==='draft' && saved.value?.proposal_id===current.id)
          await api.store.delete(saved.key);
      }
      return finish(current.action_type==='DEPOT_RECEIPT' && confirmed.effect?.id ? 'depot/'+confirmed.effect.id : route, "Сервер подтвердил действие.");
    }
    if (name === "defer-proposal" || name === "reject-proposal") {
      await api.entityPost(
        "/owner/confirmations/" +
          current.id +
          "/" +
          (name === "defer-proposal" ? "defer" : "reject"),
        { expected_version: current.version, reason: "Решение владельца" },
        current.id + ":" + name + ":" + current.version,
      );
      return finish("review", "Решение сохранено.");
    }
    if (name === "post-payment") {
      await api.mutate(
        "/v1/payments/" +
          current.id +
          "/post?expected_version=" +
          current.version,
        {},
        current.id + ":post:" + current.version,
      );
      return finish(route, "Оплата подтверждена сервером.");
    }
    if (name === "post-receipt") {
      if (!receiptCanConfirm(current, !!document.querySelector('#duplicate-reviewed')?.checked))
        throw Error("Сначала уточните отмеченные факты и проверьте возможный повтор.");
      await api.entityPost(
        "/inventory/events/" + current.id + "/post",
        {
          expected_version: current.version,
          confirmed: true,
          duplicate_reviewed: !!document.querySelector("#duplicate-reviewed")
            ?.checked,
        },
        current.id + ":post:" + current.version,
      );
      return finish(route, current.kind === "transfer" ? "Перемещение подтверждено сервером." : "Физический приход подтверждён сервером.");
    }
    if (name === "personal-state") {
      await api.entityPost(
        "/owner/tasks/" + current.id + "/state",
        {
          expected_version: current.version,
          state: current.state === "DONE" ? "TO_DO" : "DONE",
        },
        current.id + ":state:" + current.version,
      );
      return finish(route, "Состояние задачи сохранено.");
    }
    if (name === "retry-request") {
      const record = (await api.records()).find((r) => r.key === arg);
      if (!record) throw Error("Запрос не найден");
      const response = await api.request(record.path, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Idempotency-Key": record.keyHeader,
        },
        body: JSON.stringify(record.body),
      });
      await api.store.put({ ...record, state: "ACK", result: response });
      await refreshData();
      toast("Повтор подтверждён сервером. Проверьте запись.");
      if ((location.hash.slice(1) || "home") === route) return render();
    }
  });
});
window.addEventListener("hashchange", () => {
  route = location.hash.slice(1) || "home";
  render();
});
window.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    estimateReview=null;
    sheetRoot.innerHTML = "";
    setRadial(false);
  }
  if (event.key === "Tab" && sheetRoot.firstChild) {
    const focusable = [
      ...sheetRoot.querySelectorAll("button,input,select,textarea,a[href]"),
    ].filter((e) => !e.disabled);
    const first = focusable[0],
      last = focusable.at(-1);
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last?.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first?.focus();
    }
  }
});
(async () => {
  try {
    config = await api.get("/preview/config");
    api.configure(config);
    const generation = api.generation;
    await refreshData();
    const navigation=await api.readDraft("harvest-navigation");
    const financeNavigation=await api.readDraft("finance-navigation");
    if (generation !== api.generation) return;
    lastHarvestRoute=navigation?.route || "harvest";
    lastFinanceRoute=financeNavigation?.route || "money";
    ready = true;
    await render();
  } catch (error) {
    shell(errorBox(error));
  }
})();

// Safari visual viewport shrinks with the software keyboard; keep focused sheets
// within that visible region without moving or resetting the owner's draft.
function updateVisibleViewport() {
  const viewport = window.visualViewport;
  if (!viewport) return;
  document.documentElement.style.setProperty('--connected-visual-height', `${viewport.height}px`);
  document.documentElement.style.setProperty('--connected-visual-top', `${viewport.offsetTop}px`);
}
window.visualViewport?.addEventListener('resize', () => {
  updateVisibleViewport();
  requestAnimationFrame(revealFocusedCaptureField);
});
window.visualViewport?.addEventListener('scroll', updateVisibleViewport);
updateVisibleViewport();

// Scroll only if the active form's visible input line would be behind the dock
// or software keyboard. Never re-render or clear the owner's draft.
function revealFocusedCaptureField() {
  const field = document.activeElement;
  if (!field?.matches("input:not([type=file]), textarea, select") ||
      !field.closest('[data-form="report"], [data-form="receipt"], [data-form="depot"], [data-form^="journey-"], [data-form="edit-depot-line"]')) return;
  const viewport = window.visualViewport;
  const top = (viewport?.offsetTop || 0) + 16;
  const dockTop = document.querySelector('.dock')?.getBoundingClientRect().top ?? Infinity;
  const bottom = Math.min((viewport?.offsetTop || 0) + (viewport?.height || innerHeight) - 16, dockTop - 16);
  if (bottom <= top) return;
  const rect = field.getBoundingClientRect(), style = getComputedStyle(field);
  const point = field.tagName === 'TEXTAREA'
    ? rect.top + parseFloat(style.paddingTop || '0') + (parseFloat(style.lineHeight) || 20) / 2
    : rect.top + rect.height / 2;
  if (point < top || point > bottom) window.scrollBy({top: point - (top + bottom) / 2, behavior: 'instant'});
}
document.addEventListener('focusin', () => requestAnimationFrame(revealFocusedCaptureField));
