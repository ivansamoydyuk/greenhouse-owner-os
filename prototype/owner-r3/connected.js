import {
  e,
  icon,
  btn,
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
} from "./connected-adapter.js";
import {
  proposalSummary,
  displayQuantity,
  displayMetric,
} from "/assets/owner-r2.js";
import { money, displayDate, localDay } from "/assets/core.js";
const app = document.querySelector("#app"),
  sheetRoot = document.querySelector("#sheet-root");
document.body.classList.add("connected");
let activeActionRoute = null;
let config = null,
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
  return `<header class="page-header home-header"><div><p class="greeting">Ваше TEST-хозяйство</p><h1>Добрый день</h1><div class="home-date">${icon("calendar")}${e(displayDate(localDay()))}</div></div><span class="brand-mark">${icon("leaf")}</span></header>${Object.keys(failures).length ? notice("Часть разделов не загрузилась: " + Object.keys(failures).join(", ") + ". Главная показывает полученный ответ сервера.", "amber") : ""}<div class="home-layout"><div><div class="daily-actions">${link(`${icon("wallet")} Расход`, "capture/expense", "daily-action", "home-expense")}${link(`${icon("harvest")} Сбор урожая`, "quick/harvest", "daily-action", "home-cut")}</div><div class="attention-area">${link(`<span class="attention-icon">${icon("review")}</span><div><strong>Нужно подтвердить</strong><p>Подготовленные источники</p></div><span class="count">${h.confirmations.n}</span>`, "review", "attention", "home-review")}${(h.worker_exceptions || []).map((t) => link(`${icon("warning")}<span>${e(t.title)}</span>${icon("arrow")}`, `worker-task/${t.id}`, "attention-worker")).join("")}</div><div class="financial-grid">${metric("Мне должны", totalByCurrency(f.obligations, "receivable"), "finance/receivable", true)}${metric("Я должен", totalByCurrency(f.obligations, "payable"), "finance/payable")}</div>${panel(`<div class="card-header"><h2>Урожай сезона</h2>${link("Открыть", "harvest", "section-link")}</div>${s.length ? s.map((x) => `<div class="harvest-head"><div><span class="eyebrow">Подтверждено в TEST · депо</span><strong class="harvest-kpi">${e(displayQuantity(x.kg))} <small>кг</small></strong></div></div><div class="harvest-stat-grid"><div><span class="label">Средний вес ветки</span><strong>${e(displayMetric(x.average_bunch_weight))} кг</strong><small class="coverage">${e(displayQuantity(x.count_covered_kg))} кг / ${e(x.count ?? "UNKNOWN")} веток</small></div><div><span class="label">Средняя цена</span><strong>${e(displayMetric(x.average_price))} ${e(x.currency)}/кг</strong><small class="coverage">Выручка / подтверждённый вес</small></div></div><p class="note">${x.count_coverage_complete ? "Покрытие количества полное." : "Покрытие количества частичное. Неизвестные ветки не считаются нулём."}</p>`).join("") : notice("Подтверждённых чеков депо в сезоне пока нет. Полевые срезы хранятся отдельно.")}<p class="note">${e(h.season_start)} — ${e(h.season_end)}</p>`)}</div><div><div class="section-heading"><h2>Мои задачи <span class="badge-number">${h.task_count}</span></h2>${link("Все", "tasks")}</div>${panel(taskRows(h.tasks) || EmptyState("Пока пусто", "Личные задачи появятся после подтверждения."))}<div class="section-heading"><h2>Работники</h2>${link("Открыть", "workers")}</div>${panel(h.worker_exceptions.length ? taskRows(h.worker_exceptions, true) : "<p>Нет необработанных сообщений о проблеме.</p>")}<p class="note">Workers R1 · внешняя отправка недоступна.</p></div></div>`;
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
    panel(
      `<span class="eyebrow">ОРИГИНАЛ · неизменяемый</span><p class="original-text" data-testid="source-original">${e(p.source.text || "Текст отсутствует")}</p>${p.source.has_file ? `<a class="btn secondary" href="${entityPath("/owner/captures/" + p.source.id + "/original")}" target="_blank" rel="noopener">${e(p.source.filename || "Открыть исходный файл")}</a>` : ""}`,
    ) +
    proposalSummary(p, context, false) +
    (!done && p.state !== "REJECTED"
      ? panel(
          `<h2>Уточнить один факт</h2><div class="page-actions">${Object.keys(
            p.revision.payload,
          )
            .filter((k) => editableKeys(p).includes(k))
            .map((k) => btn(e(fieldLabel(k)), `edit:${k}`, "secondary"))
            .join(
              "",
            )}</div>${p.action_type === "UNRESOLVED" ? btn("Оформить прямой расход", "recover:" + p.id) : ""}`,
        )
      : "") +
    `<div class="form-error" role="alert" id="action-error"></div>${!done && p.state !== "REJECTED" ? `<div class="confirmation-bar">${btn("Подтвердить действие в TEST", "confirm-proposal", "primary full", "confirm-proposal", p.revision.missing?.length || p.action_type === "UNRESOLVED" ? "disabled" : "")}</div><div class="page-actions">${btn("Отложить", "defer-proposal")}${btn("Отклонить", "reject-proposal")}</div>` : done ? notice("Сервер подтвердил действие. Оригинал и версии сохранены.") : notice("Предложение отклонено. Оригинал и версии сохранены.")}<div class="page-actions">${link("Финансы", "finance", "btn secondary")}${link("К источнику", "source/" + p.source.id, "btn secondary")}</div>`
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
    `<div class="page-actions">${link("Мне должны", "finance/receivable", "btn secondary")}${link("Я должен", "finance/payable", "btn secondary")}</div>` +
    panel(
      `<h2>Счета</h2>${f.accounts
        .map((a) =>
          facts([
            ["Счёт", accountName(a)],
            ["Остаток", money(a.balance ?? a.amount ?? "0.00", a.currency)],
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
            `<div class="list-row"><strong>${e(o.description || o.kind)}</strong><small>${e(statuses[o.status] || o.status)} · ${e(money(o.amount || o.original_amount || "0.00", o.currency))}</small>${o.kind === "payment" ? link("Открыть платёж", "payment/" + o.id) : ""}</div>`,
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
        )}${field("Дата", "date", draft.date || localDay(), "date")}${field("Количество срезов · можно оставить неизвестным", "count", draft.count, "number", 'min="1" step="1"')}${field("Полевой вес, кг · необязательно", "kg", draft.kg, "text", 'inputmode="decimal"')}${field("Кто сообщил", "reporter", draft.reporter || "Владелец TEST")}${area("Исходная формулировка", "original", draft.original)}${notice("Полевой срез не создаёт продажу, оплату или вес депо. Неизвестный вес остаётся UNKNOWN.")}`,
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
      `<h2>Оригинальная полевая запись</h2><p class="original-text" data-testid="field-original">${e(r.original_evidence?.raw_text || "Текст не добавлен")}</p>${facts(
        [
          ["Теплица в оригинале", r.original_greenhouse],
          ["Полевое количество", r.cut_count ?? "UNKNOWN"],
          ["Полевой вес, кг", r.field_weight_kg ?? "UNKNOWN"],
          ["Сообщил", r.reporter],
          ["Примечание", r.notes || "Не добавлено"],
        ],
      )}<p class="note">Исходное полевое наблюдение неизменно. Документ депо остаётся отдельным.</p>${link("Фото / PDF чека депо", `attach-depot/${r.id}`, "btn primary full")}`,
    ) +
    panel(
      `<h2>Связанные документы</h2>${linked.map((s) => link(`${e(refName(s.depot_id))} · ${e(money(s.total_amount, s.currency))}`, `depot/${s.id}`, "list-row")).join("") || "<p>Связь ещё не подтверждена.</p>"}${notice("Добавление файла прямо к неизменяемому полевому отчёту и поздняя связь уже проведённого чека: NOT AVAILABLE. Здесь создаётся новый документ депо с явной связью.")}`,
    )
  );
}
function depotForm(report) {
  const parties = (data.home?.finance.references || []).filter(
    (p) =>
      p.kind === "counterparty" &&
      (p.payload?.role === "depot" || p.role === "depot"),
  );
  const fallback = (data.home?.finance.references || []).filter(
    (p) => p.kind === "counterparty",
  );
  return (
    Header(
      "Документ депо",
      `Из полевого среза: ${report.original_greenhouse}`,
      true,
    ) +
    panel(
      form(
        "depot",
        `${notice("Оригинал чека сохранится в новом документе депо. OCR для сохранения не нужен. Полевой отчёт не изменяется.")}${fileField()}${draft.file?.filename ? notice("Копия файла на телефоне: " + draft.file.filename) : ""}${area("Отдельный комментарий владельца", "caption", draft.caption)}${select(
          "Депо",
          "depot",
          (parties.length ? parties : fallback).map((p) => ({
            id: p.id,
            name: p.original_name || p.name,
          })),
          draft.depot,
        )}${field("Дата чека", "date", draft.date || report.event_date, "date")}${field("Основание связи именно с этим срезом", "link_evidence", draft.link_evidence)}${field("Качество дословно из документа", "quality", draft.quality || "")}${field("Вес нетто, кг", "kg", draft.kg, "text", 'inputmode="decimal"')}${field("Цена, TRY/кг", "price", draft.price, "text", 'inputmode="decimal"')}${field("Сумма строки в документе, TRY · необязательно", "stated_amount", draft.stated_amount, "text", 'inputmode="decimal"')}${select(
          "Сумма для подтверждения",
          "confirmed_amount",
          [
            { value: "calculated", label: "Расчёт веса × цены" },
            { value: "stated", label: "Явно указанная сумма документа" },
          ],
          draft.confirmed_amount || "calculated",
        )}${field("ADET из чека · неизвестно если пусто", "count", draft.count, "number", 'min="1" step="1"')}${field("Строка документа дословно", "source_line", draft.source_line)}${notice("В этом ограниченном вводе одна строка чека в TRY. Многострочный чек: NOT AVAILABLE в форме R3, сохраните оригинал через Capture. Сумма строки вычисляется backend из указанного веса/цены; оплату не создаём.")}`,
        "Сохранить черновик чека",
      ),
    )
  );
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
        ["Продажа", money(s.total_amount, s.currency)],
        ["Остаток долга", money(s.remaining_amount, s.currency)],
      ])}<h2>Оригинал документа</h2>${s.evidence.filename ? `<a class="btn secondary" href="${entityPath("/harvest/evidence/" + s.evidence.id)}" target="_blank" rel="noopener">${e(s.evidence.filename)} · неизменённые байты</a>` : "<p>Файл не добавлен</p>"}<h3>Комментарий владельца · отдельно</h3><p class="original-text">${e(s.evidence.raw_text || "Не добавлен")}</p>`,
    ) +
    panel(
      `<h2>Срез и ADET</h2>${facts([
        ["Полевое количество", s.reconciliation.field_count ?? "UNKNOWN"],
        ["ADET документа", s.reconciliation.depot_count ?? "UNKNOWN"],
        ["Полевой вес", s.reconciliation.field_weight_kg ?? "UNKNOWN"],
        ["Вес депо", s.reconciliation.depot_weight_kg],
      ])}${s.reports.map((r) => link(e(r.original_greenhouse) + " · " + displayDate(r.event_date), "report/" + r.id, "list-row")).join("")}${notice("Расхождение не исправляет ни источник, ни второй показатель. Покрытие показывается только по известным данным.", "amber")}`,
    ) +
    panel(
      `<h2>Строки</h2>${s.lines
        .map((l) =>
          facts([
            ["Оригинальное качество", l.original_quality_name],
            ["Вес", l.net_weight_kg + " кг"],
            ["Цена", money(l.unit_price, s.currency)],
            ["Сумма", money(l.amount, s.currency)],
            ["ADET", l.count ?? "UNKNOWN"],
            ["Строка источника", l.source_line],
          ]),
        )
        .join("")}${facts([
        ["Средний вес по покрытию", s.average_weight_kg ?? "UNKNOWN"],
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
      : notice(
          "Сервер подтвердил документ. Продажа и долг созданы один раз.",
        )) +
    link("К урожаю", "harvest", "btn secondary")
  );
}
function inventory() {
  const v = data.inventory;
  if (!v) return unavailable("Склад");
  return (
    Header("Склад", "Товары, места хранения и фактические движения") +
    `<div class="page-actions">${link("Товар привезли", "quick/receipt", "btn primary")}</div>` +
    notice(
      "Начальные остатки не подтверждены: UNKNOWN. Учтённый приход и сумма движений не являются проверенным абсолютным остатком.",
      "amber",
    ) +
    panel(
      `<h2>Товары и учтённые движения</h2>${
        v.products
          .map((p) => {
            const ss = v.stock.filter((s) => s.product_id === p.id);
            return `<div class="list-row"><strong>${e(p.name)}</strong><small>Проверенный физический остаток: UNKNOWN · стоимость ${ss.length && ss.every((x) => /^0(?:\.0+)?$/.test(x.uncosted_quantity)) ? "из учтённых оценённых движений" : "UNKNOWN"}</small>${ss.length ? ss.map((s) => `<p>${e(s.location_name)} · учтено ${e(displayQuantity(s.quantity))} ${e(p.base_unit)}<br><small>Без стоимости: ${e(displayQuantity(s.uncosted_quantity))} ${e(p.base_unit)}</small></p>`).join("") : "<p>Движений пока нет. Физический остаток UNKNOWN.</p>"}${link("Приход этого товара", "quick/receipt/" + p.id, "btn secondary")}</div>`;
          })
          .join("") ||
        "<p>Товаров пока нет. Добавьте первый прямо в приходе.</p>"
      }`,
    ) +
    panel(
      `<h2>Места хранения</h2>${v.locations.map((l) => `<div class="list-row">${e(l.name)}</div>`).join("") || "<p>Не настроены. Приход заблокирован до конфигурации места хранения.</p>"}`,
    ) +
    panel(
      `<h2>История</h2>${v.events.map((ev) => link(`<strong>${e(ev.kind)} · ${e(displayDate(ev.event_date))}</strong><small>${e(statuses[ev.status] || ev.status)} · ${(ev.payload.lines || []).map((l) => e(l.original_product_name) + " " + e(l.quantity) + " " + e(l.unit)).join("; ")}</small>`, `receipt/${ev.id}`, "list-row")).join("")}`,
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
        )}${btn("Создать новый товар", "new-product", "secondary")}${select("Место хранения · настроено отдельно", "location", v.locations, draft.location)}${field("Количество", "quantity", draft.quantity, "text", 'inputmode="decimal"')}${field("Базовая единица", "unit", p?.base_unit || "", "text", "readonly")}${field("Дата прихода", "date", draft.date || localDay(), "date")}${area("Оригинальная запись о приходе", "original", draft.original)}${fileField("Фото / PDF исходника · необязательно")}${draft.file?.filename ? notice("Копия на телефоне: " + draft.file.filename) : ""}${notice("Стоимость UNKNOWN. Подтверждение создаст только физическое движение в выбранном месте. Долг, счёт и платёж не создаются.")}`,
        "Проверить приход",
      ),
    )
  );
}
function receiptDetail(r) {
  return (
    Header("Приход товара", statuses[r.status] || r.status, true) +
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
            ["Стоимость", l.unit_cost ?? "UNKNOWN"],
          ]),
        )
        .join("")}${facts([
        ["Дата", displayDate(r.event_date)],
        ["Начальный остаток", "UNKNOWN"],
      ])}${notice("Увеличится только учтённое физическое количество. Абсолютный остаток не подтверждён. Долг/счёт/оплата не создаются.")}<h2>Оригинал</h2><p class="original-text">${e(r.evidence.raw_text || "Текст не добавлен")}</p>${r.evidence.filename ? `<a class="btn secondary" href="${entityPath("/inventory/events/" + r.id + "/original")}" target="_blank" rel="noopener">${e(r.evidence.filename)}</a>` : ""}${(r.issue_messages || []).map((x) => notice(x, "amber")).join("")}<div id="action-error" class="form-error" role="alert"></div>${r.status === "draft" ? `${r.issues?.includes("duplicate") ? '<label class="check-label"><input type="checkbox" id="duplicate-reviewed"><span>Я проверил: это отдельный приход, не повтор.</span></label>' : ""}${btn("Подтвердить физический приход в TEST", "post-receipt", "primary full")}` : r.status === "posted" ? notice("Сервер подтвердил приход. Физическое движение создано один раз.") : notice("Приход отменён обратным движением. Оригинал и история сохранены.")}<div class="page-actions">${link("К складу", "inventory", "btn secondary")}</div>`,
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
        ["Категория", t.category || "Не указана"],
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
  sheetRoot.innerHTML = "";
  current = null;
  draft = null;
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
    } else if (page === "harvest") body = harvest();
    else if (page === "quick" && arg === "harvest") {
      {
        const loaded = await getDraft(route, { date: localDay() });
        if (token !== epoch) return;
        draft = loaded;
      }
      body = reportForm();
    } else if (page === "report") {
      const report = data.harvest?.reports.find((r) => r.id === arg);
      if (!report)
        throw Error("Срез не найден в загруженной истории. Обновите данные.");
      const evidence = await api.entityGet(
        "/harvest/evidence/" + report.evidence_id + "/metadata",
      );
      if (token !== epoch) return;
      current = { ...report, original_evidence: evidence };
      body = reportDetail(current);
    } else if (page === "attach-depot") {
      current = data.harvest?.reports.find((r) => r.id === arg);
      if (!current) throw Error("Полевой срез не найден.");
      {
        const loaded = await getDraft(route, { date: current.event_date });
        if (token !== epoch) return;
        draft = loaded;
      }
      body = depotForm(current);
    } else if (page === "depot") {
      {
        const loaded = await api.entityGet("/harvest/settlements/" + arg);
        if (token !== epoch) return;
        current = loaded;
      }
      body = depotDetail(current);
    } else if (page === "inventory") body = inventory();
    else if (page === "quick" && arg === "receipt") {
      if (!data.inventory) throw Error("Склад не загружен");
      {
        const loaded = await getDraft(route, {
          date: localDay(),
          product: arg2 || "",
        });
        if (token !== epoch) return;
        draft = loaded;
      }
      body = receiptForm(arg2);
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
    document.querySelector("#main")?.focus({ preventScroll: true });
  } catch (error) {
    if (token === epoch) shell(errorBox(error));
  }
}
function values(form) {
  return Object.fromEntries(new FormData(form).entries());
}
function showError(error, node) {
  const target =
    node?.querySelector(".form-error") ||
    document.querySelector("#action-error") ||
    document.querySelector("main");
  if (target) target.textContent = error.message;
}
async function action(run, node = null) {
  if (busy) return;
  busy = true;
  activeActionRoute = route;
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
    buttons.forEach((b) => {
      b.disabled = b.dataset.wasDisabled === "1";
      delete b.dataset.wasDisabled;
    });
  }
}
async function saveDraft(form, snapshot) {
  const { draft, route } = snapshot;
  if (!draft) return;
  const v = values(form);
  delete v.file;
  Object.assign(draft, v);
  const file = form.querySelector("[name=file]")?.files?.[0];
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
    v = values(formEl),
    savedRoute = route;
  if (draft) await saveDraft(formEl, snapshot);
  if (name === "capture") {
    const kind = route.split("/")[1],
      body = {
        client_capture_id: draft.id,
        captured_at: draft.captured_at,
        timezone: "Europe/Istanbul",
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
    if (!place) throw Error("Выберите зарегистрированную теплицу.");
    const body = {
      event_date: draft.date,
      original_greenhouse: place.name,
      work_area_id: place.id,
      cut_count: integerOrUnknown(draft.count),
      field_weight_kg: draft.kg ? quantity(draft.kg) : null,
      reporter: requireText(draft.reporter, "кто сообщил"),
      notes: null,
      evidence: {
        source: "owner:r3-connected-test",
        source_reference: draft.id,
        raw_text: requireText(draft.original, "исходную формулировку"),
      },
    };
    const result = await api.entityPost("/harvest/reports", body, draft.id);
    await clearDraft(savedRoute);
    await finish(
      "report/" + result.id,
      "Полевое наблюдение сохранено на сервере.",
    );
  } else if (name === "depot") {
    const report = current;
    if (!draft.file) throw Error("Выберите оригинальный JPEG, PNG или PDF.");
    const linkEvidence = requireText(draft.link_evidence, "основание связи");
    const batchBody = {
      event_date: report.event_date,
      label: "Срез " + report.original_greenhouse + " " + report.event_date,
      report_ids: [report.id],
      link_evidence: linkEvidence,
    };
    if (draft.confirmed_amount === "stated" && !draft.stated_amount)
      throw Error("Выбрана сумма документа: укажите её явно.");
    const body = {
      depot_id: requireText(draft.depot, "депо"),
      event_date: draft.date,
      currency: "TRY",
      batch_ids: [],
      link_evidence: linkEvidence,
      original_greenhouse: report.original_greenhouse,
      lines: [
        {
          original_quality_name: requireText(
            draft.quality,
            "качество из документа",
          ),
          net_weight_kg: quantity(draft.kg),
          unit_price: exactMoney(draft.price),
          stated_amount: draft.stated_amount
            ? exactMoney(draft.stated_amount)
            : null,
          confirmed_amount:
            draft.confirmed_amount === "stated"
              ? draft.stated_amount
                ? exactMoney(draft.stated_amount)
                : null
              : null,
          count: integerOrUnknown(draft.count),
          source_line: requireText(draft.source_line, "строку источника"),
          allocations: [],
        },
      ],
      stated_total: draft.stated_amount
        ? exactMoney(draft.stated_amount)
        : null,
      evidence: {
        source: "owner:r3-connected-test-depot",
        source_reference: draft.id,
        raw_text: draft.caption || null,
        ...draft.file,
      },
    };
    const batch = await api.entityPost(
      "/harvest/batches",
      batchBody,
      draft.id + ":batch",
    );
    body.batch_ids = [batch.id];
    const result = await api.entityPost(
      "/harvest/settlements",
      body,
      draft.id + ":settlement",
    );
    await clearDraft(savedRoute);
    await finish(
      "depot/" + result.id,
      "Исходник, комментарий и явная связь сохранены в черновике депо.",
    );
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
  } else if (name === "receipt") {
    const p = data.inventory.products.find((p) => p.id === draft.product),
      location = data.inventory.locations.find((x) => x.id === draft.location);
    if (!p || !location)
      throw Error("Выберите товар и отдельно настроенное место хранения.");
    const body = {
      kind: "receipt",
      event_date: draft.date,
      lines: [
        {
          product_id: p.id,
          original_product_name: p.name,
          quantity: quantity(draft.quantity),
          unit: p.base_unit,
          location_id: location.id,
          unit_cost: null,
          currency: null,
        },
      ],
      notes: null,
      evidence: {
        source: "owner:r3-connected-test-receipt",
        locator: draft.id,
        raw_text: requireText(draft.original, "исходную запись о приходе"),
        ...(draft.file || {}),
      },
    };
    const result = await api.entityPost("/inventory/events", body, draft.id);
    await clearDraft(savedRoute);
    await finish(
      "receipt/" + result.id,
      "Черновик прихода сохранён. Движений пока нет.",
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
  sheetRoot.querySelector("input,textarea,select,button")?.focus();
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
  const snapshot = { draft, current, route };
  action(() => submitForm(formEl, snapshot), formEl);
});
document.addEventListener("input", (event) => {
  const formEl = event.target.closest("[data-form]");
  if (
    !formEl ||
    !draft ||
    ["product", "edit-fact"].includes(formEl.dataset.form)
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
document.addEventListener("click", (event) => {
  const button = event.target.closest("[data-action]");
  if (!button) return;
  const command = button.dataset.action;
  const [name, ...rest] = command.split(":"),
    arg = rest.join(":");
  if (name === "radial-toggle") return setRadial(!radial);
  if (name === "radial-close") return setRadial(false);
  if (name === "back") return history.length > 1 ? history.back() : go("home");
  if (name === "close-sheet") {
    sheetRoot.innerHTML = "";
    return;
  }
  if (name.startsWith("capture-")) return go("capture/" + name.slice(8));
  if (name === "edit") return editFact(arg);
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
      await refreshData();
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
    if (name === "confirm-proposal") {
      await api.entityPost(
        "/owner/confirmations/" + current.id + "/confirm",
        {
          expected_version: current.version,
          preview_digest: current.revision.preview_digest,
          confirmed: true,
          reason: "Владелец проверил текущие факты и эффект",
        },
        current.id + ":confirm:" + current.version,
      );
      return finish(route, "Сервер подтвердил действие.");
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
      return finish(route, "Физический приход подтверждён сервером.");
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
    await refreshData();
    await render();
  } catch (error) {
    shell(errorBox(error));
  }
})();
