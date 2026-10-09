import {
  SCENARIO_TEXT,
  SEASON,
  harvestDays,
  initialState,
} from "./fixtures.js";
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
  parseAmount,
} from "./demo-adapter.js";
import {
  e,
  icon,
  number,
  money,
  btn,
  link,
  Header,
  Navigation,
  CaptureBar,
  AppShell,
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
  plural,
} from "./components.js";
let state = loadState(localStorage),
  route = location.hash.slice(1) || "home",
  activeProposalRevision = null,
  recognition = null,
  voiceStatus = "unsupported",
  voiceMessage = "",
  saving = false,
  saveError = "",
  sheetContext = null,
  returnFocus = null,
  inventoryQuery = "",
  lastRoute = route,
  captureAttempt = 0;
const sheetDrafts = new Map();
const app = document.querySelector("#app"),
  sheetRoot = document.querySelector("#sheet-root");
const workerName = (id) =>
  state.workers.find((w) => w.id === id)?.name || "Не выбран";
const save = () => {
  if (!persist(localStorage, state))
    toast("Память устройства недоступна. Не закрывайте страницу.");
};
function toast(t) {
  const el = document.querySelector("#toast");
  el.textContent = t;
  el.classList.add("show");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => el.classList.remove("show"), 3200);
}
const fact = (label, value) =>
  `<div class="fact-row"><dt>${e(label)}</dt><dd>${e(value)}</dd></div>`;
const sectionLink = (label, to, test = "") =>
  link(`${e(label)} ${icon("arrow")}`, to, "section-link", test);
const rows = () => currentHarvestRows(state);
function chartData() {
  const d = structuredClone(harvestDays);
  d.at(-1).kg += state.activity
    .filter((x) => x.type === "harvest")
    .reduce((s, x) => s + x.kg, 0);
  return d;
}
function chart(compact = false) {
  const d = chartData(),
    max = Math.max(...d.map((x) => x.kg));
  return `<svg class="${compact ? "mini-chart" : "chart"}" viewBox="0 0 270 ${compact ? 80 : 140}" role="img" aria-label="Подтверждённый урожай по дням, ${d.map((x) => x.label + ": " + x.kg + " кг").join("; ")}">${d
    .map((x, i) => {
      const height = (x.kg / max) * (compact ? 72 : 104),
        y = (compact ? 76 : 110) - height;
      return `<rect x="${i * 30 + 3}" y="${y}" width="19" height="${height}" rx="3" fill="${i === 8 ? "#286448" : "#95c6ac"}"/>${compact ? "" : `<text x="${i * 30 + 12}" y="132" text-anchor="middle" font-size="11" fill="#627169">${e(x.label)}</text>`}`;
    })
    .join("")}</svg>`;
}
function harvestCard() {
  const m = harvestMetrics(rows());
  return `<section class="card"><div class="card-header"><h2>Урожай сезона</h2>${sectionLink("2026/27", "harvest", "home-harvest")}</div><div class="harvest-head"><div><span class="eyebrow">Подтверждено · бананы</span><strong class="harvest-kpi">${number(m.kg)} <small>кг</small></strong></div>${chart(true)}</div><div class="harvest-stat-grid"><div><span class="label">Средний вес ветки</span><strong>${number(m.bunchWeight, 1)} кг</strong><small class="coverage">${number(m.countedKg)} кг / ${m.bunches} веток</small></div><div><span class="label">Средняя цена</span><strong>${number(m.price, 2)} TRY/кг</strong><small class="coverage">Выручка / весь вес</small></div></div><p class="note no-bottom">Количество веток известно для ${number(m.countedKg)} из ${number(m.kg)} кг.</p></section>`;
}
function home() {
  const p = pending(state),
    mine = state.tasks.filter((x) => x.status === "todo");
  return `<header class="page-header home-header"><div><p class="greeting">Ваше хозяйство</p><h1>Добрый день</h1><div class="home-date">${icon("calendar")} Пятница, 9 октября 2026</div></div><span class="brand-mark">${icon("leaf")}</span></header><div class="home-layout"><div>${link(`<span class="attention-icon">${icon("review")}</span><div><strong>Нужно подтвердить</strong><p>Финансы ${p.filter((x) => x.type === "payable").length} · склад ${p.filter((x) => x.type === "stock").length} · ещё ${p.filter((x) => !["payable", "stock"].includes(x.type)).length}</p></div><span class="count">${p.length}</span>`, "review", "attention", "home-review")}<div class="financial-grid ${state.largeAmount ? "stress" : ""}">${MetricCard({ label: "Мне должны", value: moneyTotal(state.receivables), detail: state.receivables.length + " контрагента", to: "finance/receivable", primary: true, test: "home-receivable", ic: "down" })}${MetricCard({ label: "Я должен", value: moneyTotal(state.payables), detail: `${state.payables.length} обязательства`, to: "finance/payable", test: "home-payable", ic: "up" })}</div>${harvestCard()}</div><div><div class="section-heading"><h2>Мои задачи <span class="badge-number">${mine.length}</span></h2>${link("Все", "tasks")}</div><section class="card list-card">${
    mine
      .slice(0, 2)
      .map((t) => TaskRow(t))
      .join("") ||
    EmptyState("На сегодня всё", "Новые личные задачи появятся здесь.")
  }</section><div class="section-heading"><h2>Работники</h2>${link("Открыть", "workers")}</div>${link(`${icon("warning")}<div><strong>Нужен запасной фильтр</strong><p>TEST Мухаммед · İmamlı</p></div>`, "worker/muhammed", "worker-alert", "home-worker")}<p class="note">Один отчёт требует вашего решения.</p></div></div>`;
}
function review() {
  const f = state.filters.review,
    all = pending(state),
    list =
      f === "confirmed"
        ? state.proposals.filter((x) => x.status === "confirmed")
        : f === "all"
          ? all
          : all.filter((x) => x.type === f);
  return `${Header("Проверка", `${all.length} неподтверждённых действий`)}<div class="filters">${[
    ["all", "Все"],
    ["payable", "Финансы"],
    ["stock", "Склад"],
    ["harvest", "Урожай"],
    ["task", "Задачи"],
    ["confirmed", "Готово"],
  ]
    .map(([v, l]) => FilterChip(l, v, f === v, "review"))
    .join(
      "",
    )}</div>${list.length ? `<div class="review-list">${list.map((p) => (p.status === "confirmed" ? `<section class="card"><span class="tag">Подтверждено в демо</span><h2 class="detail-title">${e(p.title)}</h2><p class="note">Только локальный demo-state.</p>${link("Исходник", `review/${p.id}`, "btn secondary")}</section>` : ReviewCard(p))).join("")}</div>` : EmptyState("Здесь пока пусто", f === "confirmed" ? "Подтверждённые демо-карточки появятся здесь." : "В этом разделе нет карточек на проверку.", btn("Показать все", "filter:review:all"))}`;
}
function sourceDetails(p) {
  const file = state.sources.find(s => s.id === p.id)?.file;
  return `<details class="source-details"><summary>Исходник и история</summary><p>${e(p.source)}</p>${file?.kind === "pdf" ? `<a class="btn secondary" href="${e(file.url)}" download="${e(file.name)}" data-testid="download-source-pdf">Скачать исходный PDF</a>` : ""}${p.image ? `<img class="source-document" src="${e(p.image)}" alt="Синтетический документ, исходник">` : ""}<p class="muted">Версия ${p.revision || 1}. Источник сохранён без изменений. Все записи здесь синтетические.</p></details>`;
}
function reviewDetail(id) {
  const p = state.proposals.find((x) => x.id === id);
  if (!p)
    return (
      Header("Карточка не найдена", "", true) +
      EmptyState(
        "Этот источник недоступен",
        "Вернитесь в очередь.",
        link("К проверке", "review", "btn primary"),
      )
    );
  activeProposalRevision = p.revision;
  const editable = ["pending", "deferred"].includes(p.status);
  let body = "";
  if (p.type === "payable") {
    body = `<span class="eyebrow">Новый долг · не оплата</span><h2 class="detail-title">${e(p.title)}</h2><p>${e(p.meaning)}</p>${p.amount == null ? `<div class="notice amber">Какую сумму должны мастерской?</div>${editable ? btn("Уточнить сумму", "edit:amount:" + id, "primary full", "edit-amount") : ""}` : `<div class="detail-value">${money(p.amount)}</div>${editable ? btn("Изменить сумму", "edit:amount:" + id, "secondary", "edit-amount") : ""}`}<dl class="fact-list">${fact("Дата", p.date)}${fact("Теплица", p.place)}${fact("Оплата", "Не оплачено")}${fact("Срок оплаты", "Не указан · необязательно")}</dl><p class="note">Подтверждение добавит долг. Денежные счета не изменятся.</p>`;
  } else if (p.type === "task") {
    body = `<span class="eyebrow">Личная задача · сценарий интерфейса</span><h2 class="detail-title">${e(p.instruction)}</h2>${editable ? btn("Изменить поручение", "edit:instruction:" + id, "text-button", "edit-instruction") + FactChip("Теплица", p.place, "edit:place:" + id, "edit-place") + FactChip("Когда", p.when, "edit:when:" + id, "edit-when") : `<dl class="fact-list">${fact("Теплица", p.place)}${fact("Когда", p.when)}</dl>`}<p class="note">Полив · исполнитель не назначен. Для личной задачи точная дата необязательна.</p>`;
  } else if (p.type === "harvest") {
    body = `<span class="eyebrow">Чек депо · продажа и долг</span><h2 class="detail-title">${e(p.title)}</h2><div class="detail-value">${money(p.amount)}</div><dl class="fact-list">${fact("Бананы", number(p.kg) + " кг")}${fact("Цена", "31 TRY/кг")}${fact("Теплица", p.place)}${fact("Дата", p.date)}${fact("Качество", "Не разобрано")}${fact("Оплата", "Alacak · не оплачено")}</dl><div class="notice">Одна демо-операция: продажа ${number(p.kg)} кг и увеличение «Мне должны» на ${money(p.amount)}. Оплата не создаётся.</div>`;
  } else if (p.type === "stock") {
    body = `<span class="eyebrow">Физический приход</span><h2 class="detail-title">${e(p.title)}</h2><div class="detail-value">${number(p.quantity)} ${e(p.unit)}</div><dl class="fact-list">${fact("Склад", p.place)}${fact("Дата", p.date)}${fact("Стоимость", "Неизвестна")}${fact("Оплата", "Не подтверждена")}</dl><div class="notice">Добавится только количество в демо-складе. Долг, оплата и стоимость не создаются.</div>`;
  } else {
    body = `<span class="eyebrow">Без распознавания</span><h2 class="detail-title">${e(p.title)}</h2>${ErrorState("Нужна ручная проверка", "Для этого источника нет подготовленного сценария. Прототип не распознаёт произвольный текст или документ.")}<p class="note">Источник остаётся доступен. Неизвестные сумма, дата и содержание не заменены догадками.</p>`;
  }
  return `<div class="detail-layout">${Header("Проверка действия", "", true)}<section class="card">${body}${sourceDetails(p)}${p.status === "confirmed" ? `<div class="notice">Подтверждено в демо. Повторный эффект не создаётся.</div>` : p.status === "rejected" ? `<div class="notice">Отклонено. Исходник сохранён, эффекта нет.</div>` : `${canConfirm(p) ? btn(p.type === "task" ? "Сохранить личную задачу" : "Подтвердить в демо", `confirm:${id}`, "primary full", p.type === "task" ? "task-save" : "confirm-proposal") : ""}<div class="button-row">${btn("Позже", `defer:${id}`, "text-button")}${btn("Отклонить", `reject:${id}`, "text-button")}</div>`}</section></div>`;
}
function voiceBody() {
  const support = voiceStatus === "unsupported" || voiceStatus === "error",
    title = {
      unsupported: "Голосовой ввод недоступен",
      error: "Не удалось начать голосовой ввод",
      requesting: "Запрашиваем микрофон",
      listening: "Слушаю вас",
      stopped: "Ввод остановлен",
      text: "Текст готов к проверке",
    }[voiceStatus];
  return `<section class="voice-state ${voiceStatus === "listening" ? "listening" : ""}"><span class="voice-disc">${icon("voice")}</span><h2>${title}</h2><p>${support ? voiceMessage || "В этом браузере нет поддерживаемого речевого ввода. Можно диктовать микрофоном клавиатуры в текстовом поле." : voiceStatus === "listening" ? "Говорите естественно. «Готово» остановит ввод." : "Сохраняется только текст. Аудиозапись не создаётся."}</p>${support ? btn("Перейти к тексту", "voice-fallback", "primary full", "voice-fallback") : voiceStatus === "listening" || voiceStatus === "requesting" ? btn("Готово", "voice-stop", "primary full", "voice-stop") : btn("Продолжить голосом", "voice-start", "secondary", "voice-start")}${!support ? btn("Ввести текст", "voice-fallback", "text-button full", "voice-fallback") : ""}</section>${state.draft.text ? `<label class="capture-label" for="capture-input">Проверьте текст</label><textarea id="capture-input" data-testid="capture-input" class="capture-input" placeholder="Ваш текст">${e(state.draft.text)}</textarea>` : ""}`;
}
function capture(mode) {
  state.draft.mode = mode;
  const d = state.draft;
  let body = "";
  if (saving)
    return `<div class="capture-page">${Header("Добавить источник", "", true)}${LoadingState()}</div>`;
  if (saveError)
    return `<div class="capture-page">${Header("Источник не потерян", "", true)}${ErrorState("Не получилось подготовить", saveError, btn("Повторить", "capture-retry", "primary full", "capture-retry"))}${btn("Вернуться к тексту", "capture-edit", "text-button full", "capture-edit")}</div>`;
  if (mode === "text")
    body = `<label class="capture-label" for="capture-input">Что нужно запомнить?</label><textarea id="capture-input" data-testid="capture-input" class="capture-input" placeholder="Например, что привезли или что нужно сделать…">${e(d.text)}</textarea><p class="note">Достаточно одного сообщения. Факты проверите в следующей карточке.</p>`;
  if (mode === "voice") body = voiceBody();
  if (mode === "photo")
    body = `${d.file ? `<div class="photo-preview">${d.file.kind === "image" ? `<img src="${e(d.file.url)}" alt="Выбранный источник">` : `<section class="state">${icon("text")}<h2>Документ PDF</h2><p>Содержимое не распознаётся в этом прототипе.</p></section>`}<div class="file-name">${e(d.file.name)}</div></div>${btn("Выбрать другой", "clear-file", "text-button", "clear-file")}` : `<div class="file-buttons"><label class="file-label">${icon("camera")} Камера или изображение<input data-testid="photo-file" id="photo-file" type="file" accept="image/jpeg,image/png,image/webp,image/heic,image/heif" capture="environment"></label><label class="file-label">${icon("text")} Выбрать PDF<input data-testid="pdf-file" id="pdf-file" type="file" accept="application/pdf"></label></div>`}<label class="field">Комментарий · необязательно<input class="input" data-testid="capture-comment" id="capture-comment" value="${e(d.comment)}" placeholder="Что важно на фото?"></label><p class="note">Ваш файл остаётся на устройстве. Для произвольного файла готовых ответов нет.</p>`;
  const saveButton =
    mode !== "voice" || d.text
      ? btn("Сохранить в демо", "capture-save", "primary full", "capture-save")
      : "";
  return `<div class="capture-page" data-r3-form="capture-${mode}">${Header({ text: "Новая запись", voice: "Голос", photo: "Фото" }[mode], "", true)}${body}${saveButton}<div class="scenario-panel"><p>ГОТОВЫЙ СЦЕНАРИЙ ИНТЕРФЕЙСА · НЕ ИИ</p>${mode === "photo" ? btn(`${icon("text")} Выбрать синтетический чек депо`, "demo-photo", "secondary scenario-button full", "demo-photo") : btn(`${icon("tasks")} Попробовать задачу про полив`, "demo-irrigation", "secondary scenario-button full", "demo-irrigation")}</div><p class="screen-note">Демо-состояние хранится только в этом браузере</p></div>`;
}
function tasks() {
  const f = state.filters.tasks;
  let content = "";
  if (f === "mine") {
    const list = state.tasks;
    content = [
      ["Сегодня", list.filter((t) => t.when === "Сегодня")],
      ["Предстоящие", list.filter((t) => t.when && t.when !== "Сегодня")],
      ["Без точной даты", list.filter((t) => !t.when)],
    ]
      .map(([label, items]) =>
        items.length
          ? `<p class="list-section-label">${label}</p><section class="card list-card">${items.map((t) => TaskRow(t)).join("")}</section>`
          : "",
      )
      .join("");
  } else {
    content = `<section class="card"><div class="card-header"><h2>Черновик поручения</h2><span class="tag amber">${state.workerTask.status === "assigned" ? "Назначено в демо" : "Не назначено"}</span></div>${link(`<strong>${e(state.workerTask.title)}</strong><p class="note no-bottom">İmamlı · 10 октября · ${state.workerTask.worker ? e(workerName(state.workerTask.worker)) : "Исполнитель не выбран"}</p>`, "assignment/worker-task", "task-row", "worker-task")}</section><div class="section-heading"><h2>Работники</h2>${link("Все", "workers")}</div><section class="card list-card">${state.workers.map(workerRow).join("")}</section>`;
  }
  return `${Header("Задачи", "Планы и поручения")}<div class="filters">${FilterChip("Мои", "mine", f === "mine", "tasks", "tasks-mine")}${FilterChip("Работникам", "workers", f === "workers", "tasks", "tasks-workers")}</div>${content || EmptyState("Задач пока нет", "Добавьте поручение текстом или голосом.")}`;
}
function taskDetail(id) {
  const t =
    state.tasks.find((t) => t.id === id) ||
    state.workerTasks.find((t) => t.id === id) ||
    (id === "worker-task" && state.workerTask.status === "assigned"
      ? {
          ...state.workerTask,
          source:
            "Заранее написанный TEST-пример. Назначено в демо, не отправлено.",
        }
      : null);
  if (!t)
    return (
      Header("Задача", "", true) +
      EmptyState("Задача не найдена", "Вернитесь к списку.")
    );
  return `<div class="detail-layout">${Header(t.worker ? "Поручение" : "Моя задача", "", true)}<section class="card"><span class="tag ${t.status === "problem" ? "red" : ""}">${{ todo: "Нужно сделать", done: "Выполнено владельцем", assigned: "Назначено", problem: "Есть проблема" }[t.status]}</span><h2 class="detail-title">${e(t.title)}</h2>${!t.worker ? btn("Изменить поручение", "edit:title:" + id, "text-button", "edit-instruction") : ""}${t.worker ? `<dl class="fact-list">${fact("Где", t.place)}${fact("Когда", t.when)}${fact("Кому", workerName(t.worker))}</dl>` : `${FactChip("Теплица", t.place, "edit:place:" + id, "edit-place")}${FactChip("Когда", t.when, "edit:when:" + id, "edit-when")}`} ${t.report ? `<div class="notice amber">Отчёт: ${e(t.report)}</div>` : ""}${!t.worker ? `<p class="note">${t.date ? "Точная дата: " + new Intl.DateTimeFormat("ru-RU", { day: "numeric", month: "long", year: "numeric" }).format(new Date(t.date + "T12:00:00")) : "Точная дата не выбрана"}. Личная задача не требует назначения работника.</p>${btn(t.status === "done" ? "Вернуть в задачи" : "Отметить выполненной", `task-done:${id}`, "primary full", "task-save")}` : ""}<details class="source-details"><summary>Дополнительно</summary><p>Источник: ${e(t.source)}</p><p>История: создана в синтетическом наборе. Напоминания и повторы не подключены.</p></details></section></div>`;
}
function workerRow(w) {
  const count =
    state.workerTasks.filter((t) => t.worker === w.id).length +
    (state.workerTask.status === "assigned" && state.workerTask.worker === w.id
      ? 1
      : 0);
  return link(
    `<span class="avatar">${e(w.initials)}</span><div><strong>${e(w.name)}</strong><p>${e(w.place)} · ${e(w.language)}</p><p>${count ? count + " " + plural(count, "назначенная задача", "назначенные задачи", "назначенных задач") : "Нет назначенных задач"}</p></div>${w.issue ? `<span class="tag amber">Проблема</span>` : icon("arrow")}`,
    `worker/${w.id}`,
    "worker-row",
    `worker-${w.id}`,
  );
}
function workers() {
  return `${Header("Работники", "TEST-профили · вид владельца")}<section class="card list-card">${state.workers.map(workerRow).join("")}</section><p class="note">Здесь показаны назначения и отчёты. Онлайн-статус и фактическое выполнение не определяются.</p>`;
}
function workerDetail(id) {
  const w = state.workers.find((x) => x.id === id);
  if (!w) return EmptyState("Работник не найден", "Выберите TEST-профиль.");
  const assigned = state.workerTasks.filter((t) => t.worker === id);
  if (state.workerTask.status === "assigned" && state.workerTask.worker === id)
    assigned.push({
      ...state.workerTask,
      source:
        "Заранее написанный TEST-пример. Назначено в демо, не отправлено.",
    });
  return `<div class="detail-layout">${Header(w.name, w.place + " · " + w.language, true)}${w.issue ? `<div class="notice amber">${icon("warning")} ${e(w.issue)}<p class="small">Синтетический отчёт · 9 октября</p></div>` : ""}<div class="section-heading"><h2>Поручения</h2></div>${assigned.length ? `<section class="card list-card">${assigned.map((t) => TaskRow(t)).join("")}</section>` : EmptyState("Нет назначенных задач", "Это не означает, что работник свободен.")}<section class="card"><span class="eyebrow">ПОДГОТОВЛЕННЫЙ ДЕМО-ПРИМЕР</span><h2 class="detail-title">Проверить фильтр</h2><p class="note">İmamlı · 10 октября. Инструкция и перевод доступны до назначения.</p>${link("Проверить назначение", "assignment/worker-task", "btn primary full", "assign-open")}</section></div>`;
}
function assignment() {
  const t = state.workerTask;
  return `<div class="detail-layout" data-r3-form="assignment">${Header("Назначить поручение", "TEST · без отправки работнику", true)}<section class="card"><span class="eyebrow">ИНСТРУКЦИЯ</span><h2 class="detail-title">${e(t.title)}</h2><dl class="fact-list">${fact("Теплица", t.place)}${fact("Когда", t.when)}</dl>${FactChip("Кому", workerName(t.worker), "choose-worker", "choose-worker")}<p class="list-section-label">Содержание на дари</p><div class="translation" lang="fa" dir="rtl">${e(t.translation)}</div><div class="notice amber">Один заранее написанный TEST-пример. Общего переводчика нет. Проверьте, что числа, смысл и «не включать» сохранены.</div>${t.status === "assigned" ? "" : btn("Исправить текст на дари", "edit-translation", "secondary full", "edit-translation")}<p class="note">Назначение меняет только demo-state. Нет сообщения в Telegram и новой авторизации работника.</p>${t.status === "assigned" ? `<div class="notice">Назначено в демо. Проверку текста подтвердил владелец. Внешняя отправка не выполнялась.</div>` : btn(t.worker ? "Проверил текст · назначить в демо" : "Выбрать работника", t.worker ? "assignment-confirm" : "choose-worker", "primary full", "assignment-confirm")}</section></div>`;
}
function finance(tab) {
  const f = tab || state.filters.finance;
  state.filters.finance = f;
  const items = f === "payable" ? state.payables : state.receivables;
  return `${Header("Финансы", "Рабочая валюта · TRY", true)}<div class="financial-grid ${state.largeAmount ? "stress" : ""}">${MetricCard({ label: "Мне должны", value: moneyTotal(state.receivables), detail: "Неоплаченные продажи", to: "finance/receivable", primary: true, ic: "down" })}${MetricCard({ label: "Я должен", value: moneyTotal(state.payables), detail: "Непогашенные долги", to: "finance/payable", ic: "up" })}</div><div class="filters">${link("Должники", "finance/receivable", `filter-chip ${f === "receivable" ? "selected" : ""}`)}${link("Кредиторы", "finance/payable", `filter-chip ${f === "payable" ? "selected" : ""}`)}${link("Движения", "finance/movements", `filter-chip ${f === "movements" ? "selected" : ""}`)}</div>${f === "movements" ? `<section class="card"><h2>Последние движения</h2><div class="timeline-row"><div class="summary-pair"><strong>TEST Банановое депо</strong><strong>+252 000 TRY</strong></div><p>Оплата по ранее отражённым продажам · 8 октября</p></div><p class="note no-bottom">Синтетический пример. Оплата уменьшает долг, не создаёт вторую продажу или расход.</p></section>` : `<section class="card list-card">${items.map((x) => link(`<div class="line"><strong>${e(x.name)}</strong><span class="amount">${money(x.amount)}</span></div><p>${e(x.meaning)} · ${e(x.date)}</p>`, `counterparty/${x.id}`, "finance-item", x.id === "depot" ? "finance-depot" : "finance-" + x.id)).join("")}</section>`}${state.largeAmount ? '<div class="notice amber">Режим проверки крупной суммы: добавлена отдельная TEST-позиция другого периода. Она не входит в продажи текущего сезона.</div>' : ""}<p class="note">Суммы других валют сюда не включены. Долг не означает состоявшуюся оплату.</p>`;
}
function counterparty(id) {
  const receivable = state.receivables.find((x) => x.id === id),
    x = receivable || state.payables.find((x) => x.id === id);
  if (!x) return EmptyState("Контрагент не найден", "Вернитесь к финансам.");
  return `<div class="detail-layout">${Header("Контрагент", "", true)}<section class="card"><span class="eyebrow">${receivable ? "МНЕ ДОЛЖНЫ" : "Я ДОЛЖЕН"}</span><h2 class="detail-title">${e(x.name)}</h2><div class="detail-value">${money(x.amount)}</div><p class="muted">${e(x.date)}</p><dl class="fact-list">${fact("Содержание", x.meaning)}${fact("Валюта", "TRY")}${fact("Состояние", "Не оплачено")}</dl>${id === "depot" ? `<div class="notice">Подтверждённые продажи сезона: ${money(harvestMetrics(rows()).revenue)}. Оплачено 252 000 TRY. Другой покупатель сезона: ${money(state.receivables.find((x) => x.id === "buyer").amount)}.</div>` : ""}<details class="source-details"><summary>Источники</summary><p>Синтетические документы DEMO-S1–S3. Это пример интерфейса позиции, не реальный долг.</p></details></section></div>`;
}
function quality(m) {
  const colors = ["#34734e", "#a8c477", "#cfaa59", "#cbd1c8"],
    labels = ["Высшее", "Первое", "Второе", "Не разобрано"];
  let x = 0;
  return `<svg width="100%" height="18" viewBox="0 0 300 18" role="img" aria-label="${m.quality.map((q, i) => labels[i] + ": " + q + " кг").join(", ")}">${m.quality
    .map((q, i) => {
      const w = (q / m.kg) * 300,
        r = `<rect x="${x}" y="0" width="${w}" height="18" fill="${colors[i]}"/>`;
      x += w;
      return r;
    })
    .join(
      "",
    )}</svg><div class="quality-legend">${m.quality.map((q, i) => `<div><span>${labels[i]}</span><strong>${number(q)} кг</strong></div>`).join("")}</div>`;
}
function harvest(id) {
  const all = rows(),
    row = all.find((x) => x.id === id),
    m = harvestMetrics(row ? [row] : all);
  return `${Header(row ? row.name : "Урожай", "Сезон 2026/27 · " + SEASON, true)}<div class="split-grid"><div><section class="card"><span class="eyebrow">Подтверждённые поставки бананов</span><div class="detail-value">${number(m.kg)} кг</div><div class="harvest-stat-grid"><div><span class="label">Средний вес ветки</span><strong>${number(m.bunchWeight, 1)} кг</strong><small class="coverage">${number(m.countedKg)} кг / ${m.bunches} веток</small></div><div><span class="label">Взвешенная цена</span><strong>${number(m.price, 2)} TRY/кг</strong><small class="coverage">${number(m.revenue)} TRY / ${number(m.kg)} кг</small></div></div><p class="note">Без подтверждённого количества веток: ${number(m.kg - m.countedKg)} кг.</p>${row ? "" : chart()}</section><section class="card"><div class="card-header"><h2>Качество</h2><span class="tag">По документам</span></div>${quality(m)}</section></div><div>${row ? `<section class="card"><h2>Документы и полевые отчёты</h2><div class="timeline-row"><span class="tag">Демо-факт продажи</span><h3>Подтверждённые документы депо</h3><p>${number(m.kg)} кг · ${money(m.revenue)}</p></div><div class="timeline-row"><span class="tag amber">Свидетельство работника</span><h3>Полевой отчёт о срезке</h3><p>Количество неизвестно. Не добавлено к продажам.</p></div></section>` : `<section class="card"><h2>По теплицам</h2>${all.map((r) => link(`${icon("leaf")}<div><strong>${e(r.name)}</strong><p>${number(r.kg)} кг · ${number(r.revenue / r.kg, 2)} TRY/кг</p></div>${icon("arrow")}`, `harvest/${r.id}`, "greenhouse-row", `harvest-${r.id}`)).join("")}</section>`}${state.proposals.some((p) => p.type === "harvest" && ["pending", "deferred"].includes(p.status)) ? '<div class="notice amber">Неподтверждённый чек 1 200 кг показан в «Проверке» и не включён в продажи.</div>' : state.activity.some((x) => x.type === "harvest") ? '<div class="notice">Подтверждённый демо-чек включён в сезон. Деньги ещё не получены.</div>' : ""}</div></div>`;
}
function inventory() {
  const f = state.filters.inventory,
    q = inventoryQuery.toLocaleLowerCase("ru");
  const list = state.stock.filter(
    (x) =>
      (f === "all" || x.category === f) &&
      x.name.toLocaleLowerCase("ru").includes(q),
  );
  return `${Header("Склад", "Остатки и движения", true)}<div class="search">${icon("search")}<input class="input" id="inventory-search" data-testid="inventory-search" placeholder="Поиск товара" aria-label="Поиск товара" value="${e(inventoryQuery)}"></div><div class="filters">${[
    ["all", "Все"],
    ["Удобрения", "Удобрения"],
    ["Материалы", "Материалы"],
  ]
    .map(([v, l]) => FilterChip(l, v, f === v, "inventory"))
    .join(
      "",
    )}</div><div id="stock-results">${list.length ? `<section class="card list-card">${list.map(StockRow).join("")}</section>` : EmptyState("Товаров не найдено", "Попробуйте другое название или категорию.")}</div><p class="note">Порог минимального остатка не задан. Неизвестное количество не считается нулём.</p>`;
}
function stock(id, showMovements = false) {
  const s = state.stock.find((x) => x.id === id);
  if (!s) return EmptyState("Товар не найден", "Вернитесь к складу.");
  return `<div class="detail-layout">${Header(showMovements ? "Движения" : s.name, s.place, true)}<section class="card"><span class="eyebrow">${e(s.category)}</span><div class="detail-value">${s.quantity == null ? "Остаток неизвестен" : number(s.quantity) + " " + e(s.unit)}</div><p class="note">Стоимость неизвестна. Порог низкого остатка не задан.</p>${!showMovements ? link("Открыть движения", `stock/${id}/movements`, "btn secondary full", "stock-movements") : s.movements.length ? s.movements.map((m) => `<div class="timeline-row"><div class="summary-pair"><strong>${e(m.label)}</strong><strong>${m.quantity > 0 ? "+" : ""}${number(m.quantity)} ${e(s.unit)}</strong></div><p>${e(m.date)} · ${e(s.place)}</p></div>`).join("") : EmptyState("Нет движений", "Неизвестный остаток не заменён нулём.")}</section></div>`;
}
function more() {
  return `<div class="more-layout">${Header("Ещё", "Всё хозяйство в одном месте")}<section class="card list-card">${[
    [
      "finance/receivable",
      "wallet",
      "Финансы",
      "Долги и движения",
      "more-finance",
    ],
    ["harvest", "leaf", "Урожай", "Сезон, теплицы и качество", "more-harvest"],
    ["inventory", "box", "Склад", "Товары и движения", "more-inventory"],
    ["workers", "people", "Работники", "Поручения и отчёты", "more-workers"],
    [
      "sources",
      "text",
      "Источники",
      "Файлы и записи этого демо",
      "more-sources",
    ],
  ]
    .map(([to, ic, label, sub, test]) =>
      link(
        `${icon(ic)}<div><strong>${label}</strong><small>${sub}</small></div>${icon("arrow")}`,
        to,
        "more-row",
        test,
      ),
    )
    .join(
      "",
    )}</section><section class="card list-card">${link(`${icon("info")}<div><strong>О прототипе</strong><small>Сценарии и состояния для проверки</small></div>${icon("arrow")}`, "settings", "more-row", "more-settings")}</section><p class="note">Owner UI R3 · дизайн на согласовании.<br>Реальные сервисы не подключены.</p></div>`;
}
function sources() {
  const labels = {pending: "На проверке", deferred: "Отложено", confirmed: "Подтверждено в демо", rejected: "Отклонено · источник сохранён"};
  return `${Header("Источники", "Синтетические оригиналы и решения", true)}${state.proposals.map((p) => {
    const capture = state.sources.find(s => s.id === p.id || (p.id === "depot-review" && s.id === "demo-receipt"));
    return `<section class="card"><span class="tag">${e(labels[p.status])}</span><h2 class="detail-title">${e(p.title)}</h2><p class="code-free">${e(p.source)}</p>${capture?.comment ? `<p class="note">Комментарий: ${e(capture.comment)}</p>` : ""}${capture?.file?.kind === "pdf" ? `<a class="btn secondary" href="${e(capture.file.url)}" download="${e(capture.file.name)}">Скачать исходный PDF</a>` : ""}${p.image ? `<img class="source-document" src="${e(p.image)}" alt="Сохранённый синтетический исходник">` : ""}${link("Открыть карточку", `review/${p.id}`, "btn secondary full", `source-${p.id}`)}</section>`;
  }).join("")}`;
}
function settings() {
  return `${Header("О прототипе", "Состояния для проверки", true)}<section class="card"><h2>Синтетические TEST-данные</h2><p class="note">Подтверждения меняют только браузерный demo-state. Это не тест понимания произвольной речи. Фото и тексты не отправляются в API.</p><div class="settings-stack">${btn("Сеть: " + (state.online ? "доступна" : "нет сети"), "toggle-offline", "secondary", "toggle-offline")}${btn("Следующее сохранение: " + (state.failNext ? "ошибка" : "обычное"), "toggle-error", "secondary", "toggle-error")}${btn("Следующее подтверждение: " + (state.staleNext ? "устаревшее" : "обычное"), "toggle-stale", "secondary", "toggle-stale")}${btn("Показать пустую очередь", "toggle-empty", "secondary", "toggle-empty")}${btn("Крупная сумма: " + (state.largeAmount ? "включена" : "выключена"), "toggle-large", "secondary", "toggle-large")}${btn("Сбросить синтетический пример", "reset-demo", "danger", "reset-demo")}</div></section><p class="note">Физический iPhone, микрофон и клавиатура требуют отдельной проверки владельцем. В прототипе нет работников R2, календаря удобрений, обхода или внешней отправки.</p>`;
}
function render({ focus = false } = {}) {
  const parts = route.split("/"),
    page = parts[0],
    focused = page === "capture";
  let html;
  switch (page) {
    case "home":
      html = home();
      break;
    case "review":
      html = parts[1] ? reviewDetail(parts[1]) : review();
      break;
    case "capture":
      html = capture(parts[1] || "text");
      break;
    case "tasks":
      html = tasks();
      break;
    case "task":
      html = taskDetail(parts[1]);
      break;
    case "workers":
      html = workers();
      break;
    case "worker":
      html = workerDetail(parts[1]);
      break;
    case "assignment":
      html = assignment();
      break;
    case "finance":
      html = finance(parts[1]);
      break;
    case "counterparty":
      html = counterparty(parts[1]);
      break;
    case "harvest":
      html = harvest(parts[1]);
      break;
    case "inventory":
      html = inventory();
      break;
    case "stock":
      html = stock(parts[1], parts[2] === "movements");
      break;
    case "more":
      html = more();
      break;
    case "sources":
      html = sources();
      break;
    case "settings":
      html = settings();
      break;
    default:
      html = EmptyState(
        "Страница не найдена",
        "Вернитесь на главную.",
        link("Главная", "home", "btn primary"),
      );
  }
  app.classList.toggle("focused", focused);
  app.innerHTML = AppShell(
    html,
    ["home", "review", "tasks", "more"].includes(page)
      ? page
      : page === "task" || page === "assignment"
        ? "tasks"
        : "more",
    pending(state).length,
    focused,
  );
  if (focus) document.querySelector("#main")?.focus({ preventScroll: true });
  save();
}
function navigate(to) {
  if (route === to) {
    render();
    return;
  }
  state.positions[route] = window.scrollY;
  save();
  location.hash = to;
}
window.addEventListener("hashchange", () => {
  captureAttempt++;
  saving = false;
  if (
    route.startsWith("capture/voice") &&
    !location.hash.includes("capture/voice")
  )
    stopVoice();
  if (document.querySelector("#fact-sheet")) closeSheet();
  lastRoute = route;
  route = location.hash.slice(1) || "home";
  saveError = "";
  render({ focus: true });
  requestAnimationFrame(() => {
    window.scrollTo(0, state.positions[route] || 0);
    const storedFocus = state.focus?.[route];
    const target = storedFocus ? document.querySelector(storedFocus) : null;
    if (target) target.focus({ preventScroll: true });
    else if (route === "capture/text")
      document.querySelector("#capture-input")?.focus({ preventScroll: true });
  });
});
window.addEventListener(
  "scroll",
  () => {
    state.positions[route] = scrollY;
  },
  { passive: true },
);
window.addEventListener("pagehide", save);
function openSheet({ title, kind, id, key, value, options }) {
  if (document.querySelector("#fact-sheet")) closeSheet();
  returnFocus = document.activeElement?.getAttribute("data-testid");
  sheetContext = { kind, id, key };
  const draftKey = [kind, id, key].join(":"),
    draft = sheetDrafts.has(draftKey) ? sheetDrafts.get(draftKey) : value;
  let input = options
    ? `<label class="capture-label" for="fact-input">${e(title)}</label><select class="input" id="fact-input" data-testid="fact-input">${options.map((v) => `<option value="${e(v)}" ${v === draft ? "selected" : ""}>${e(v)}</option>`).join("")}</select>`
    : kind === "translation"
      ? `<label class="capture-label" for="fact-input">Проверяемый текст</label><textarea class="input" id="fact-input" data-testid="translation-input" rows="4" lang="fa" dir="rtl">${e(draft)}</textarea>`
      : `<label class="capture-label" for="fact-input">${e(title)}</label><input class="input" id="fact-input" data-testid="fact-input" value="${e(draft)}" ${key === "amount" ? 'inputmode="decimal" placeholder="Например, 7 000"' : ""}>`;
  sheetRoot.innerHTML = Sheet(
    title,
    input + `<p id="sheet-error" class="notice red" hidden></p>`,
    btn(
      "Отмена",
      "sheet-cancel",
      "secondary",
      "sheet-cancel",
      'type="button"',
    ) +
      btn("Сохранить", "sheet-save", "primary", "sheet-save", 'type="button"'),
  );
  const dialog = document.querySelector("#fact-sheet");
  dialog.addEventListener("cancel", (ev) => {
    ev.preventDefault();
    closeSheet();
  });
  dialog.showModal();
  document.querySelector("#fact-input")?.focus();
}
function closeSheet() {
  const el = document.querySelector("#fact-input");
  if (sheetContext && el)
    sheetDrafts.set(
      [sheetContext.kind, sheetContext.id, sheetContext.key].join(":"),
      el.value,
    );
  document.querySelector("#fact-sheet")?.close();
  sheetRoot.innerHTML = "";
  sheetContext = null;
  if (returnFocus)
    document
      .querySelector(`[data-testid="${CSS.escape(returnFocus)}"]`)
      ?.focus({ preventScroll: true });
}
function chooseWorker() {
  returnFocus = document.activeElement?.getAttribute("data-testid");
  sheetContext = { kind: "worker" };
  sheetRoot.innerHTML = Sheet(
    "Кому поручить?",
    state.workers
      .map((w) =>
        btn(
          `<span class="avatar">${e(w.initials)}</span><span>${e(w.name)}<small class="muted"> · ${e(w.place)} · ${e(w.language)}</small></span>`,
          `worker-select:${w.id}`,
          "secondary full",
          `worker-option-${w.id}`,
          'type="button"',
        ),
      )
      .join(""),
    btn("Отмена", "sheet-cancel", "secondary", "sheet-cancel", 'type="button"'),
  );
  const d = document.querySelector("#fact-sheet");
  d.addEventListener("cancel", (ev) => {
    ev.preventDefault();
    closeSheet();
  });
  d.showModal();
}
function startVoice() {
  const C = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!C) {
    voiceStatus = "unsupported";
    voiceMessage =
      "В этом браузере нет поддерживаемого речевого ввода. Откройте текст и нажмите микрофон на клавиатуре.";
    if (route === "capture/voice") render();
    return;
  }
  stopVoice();
  voiceStatus = "requesting";
  voiceMessage = "";
  try {
    recognition = new C();
    recognition.lang = "ru-RU";
    recognition.continuous = true;
    recognition.interimResults = true;
    const prefix = state.draft.text;
    recognition.onstart = () => {
      voiceStatus = "listening";
      if (route === "capture/voice") render();
    };
    recognition.onresult = (ev) => {
      state.draft.text = [prefix, transcriptText(ev.results)]
        .filter(Boolean)
        .join(" ");
      save();
      const input = document.querySelector("#capture-input");
      if (input) input.value = state.draft.text;
      else if (route === "capture/voice") render();
    };
    recognition.onerror = (ev) => {
      voiceStatus = "error";
      voiceMessage =
        ev.error === "not-allowed"
          ? "Доступ к микрофону не разрешён. Можно использовать текст и микрофон клавиатуры."
          : "Речевой ввод прервался. Введённый текст сохранён, можно продолжить вручную.";
      if (route === "capture/voice") render();
    };
    recognition.onend = () => {
      if (voiceStatus !== "error") {
        voiceStatus = state.draft.text ? "text" : "stopped";
        if (route === "capture/voice") render();
      }
    };
    recognition.start();
  } catch {
    voiceStatus = "error";
    voiceMessage = "Браузер не начал речевой ввод. Продолжите текстом.";
  }
  if (route === "capture/voice") render();
}
function stopVoice() {
  if (recognition) {
    const r = recognition;
    recognition = null;
    try {
      r.stop();
    } catch {}
  }
}
async function captureSave() {
  if (!state.draft.text.trim() && !state.draft.file) {
    toast("Добавьте текст или выберите источник.");
    document.querySelector("#capture-input")?.focus();
    return;
  }
  stopVoice();
  saveError = "";
  const attempt = ++captureAttempt;
  const origin = route;
  saving = true;
  render();
  await new Promise((r) => setTimeout(r, 450));
  if (attempt !== captureAttempt || route !== origin) return;
  saving = false;
  if (!state.online) {
    saveError =
      "Нет сети — это проверка demo-состояния. Текст и файл сохранены на устройстве. Включите сеть в «О прототипе» или повторите после восстановления.";
    render();
    return;
  }
  if (state.failNext) {
    state.failNext = false;
    save();
    saveError =
      "Сценарий ошибки подготовки. Источник сохранён в черновике. Повтор не создаст второй источник.";
    render();
    return;
  }
  const p = prepare(state);
  save();
  navigate("review/" + p.id);
}
document.addEventListener("input", (ev) => {
  const el = ev.target;
  if (el.id === "capture-input") {
    state.draft.text = el.value;
    state.draft.scenario = el.value === SCENARIO_TEXT ? "irrigation" : null;
    save();
  }
  if (el.id === "capture-comment") {
    state.draft.comment = el.value;
    save();
  }
  if (el.id === "inventory-search") {
    inventoryQuery = el.value;
    const focus = el.selectionStart;
    const q = inventoryQuery.toLocaleLowerCase("ru"),
      f = state.filters.inventory,
      list = state.stock.filter(
        (s) =>
          (f === "all" || s.category === f) &&
          s.name.toLocaleLowerCase("ru").includes(q),
      );
    document.querySelector("#stock-results").innerHTML = list.length
      ? `<section class="card list-card">${list.map(StockRow).join("")}</section>`
      : EmptyState(
          "Товаров не найдено",
          "Попробуйте другое название или категорию.",
        );
  }
  if (el.id === "fact-input" && sheetContext)
    sheetDrafts.set(
      [sheetContext.kind, sheetContext.id, sheetContext.key].join(":"),
      el.value,
    );
});
document.addEventListener("change", async (ev) => {
  const el = ev.target;
  if (!["photo-file", "pdf-file"].includes(el.id)) return;
  const f = el.files?.[0];
  if (!f) return;
  const supported = [
    "image/jpeg",
    "image/png",
    "image/webp",
    "application/pdf",
  ].includes(f.type);
  if (!supported) {
    toast("Этот файл не удалось открыть. Выберите другое фото или PDF.");
    return;
  }
  if (f.size > 5 * 1024 * 1024) {
    toast("Для демо выберите файл меньше 5 МБ.");
    return;
  }
  try {
    const url = await new Promise((resolve, reject) => {
      const r = new FileReader();
      r.onload = () => resolve(r.result);
      r.onerror = reject;
      r.readAsDataURL(f);
    });
    state.draft.file = {
      name: f.name,
      kind: f.type === "application/pdf" ? "pdf" : "image",
      url,
    };
    state.draft.scenario = null;
    save();
    render();
  } catch {
    toast("Файл не прочитан. Попробуйте выбрать его ещё раз.");
  }
});
document.addEventListener("click", async (ev) => {
  const anchor = ev.target.closest('a[href^="#"]');
  if (anchor) {
    state.focus ??= {};
    state.focus[route] = anchor.dataset.testid
      ? '[data-testid="' + CSS.escape(anchor.dataset.testid) + '"]'
      : 'a[href="' + CSS.escape(anchor.getAttribute("href")) + '"]';
    state.positions[route] = scrollY;
    save();
  }
  const b = ev.target.closest("[data-action]");
  if (!b) return;
  ev.preventDefault();
  const action = b.dataset.action;
  if (
    action.startsWith("capture-") &&
    ["capture-photo", "capture-voice", "capture-text"].includes(action)
  ) {
    const mode = action.split("-")[1];
    state.draft.mode = mode;
    save();
    navigate("capture/" + mode);
    if (mode === "voice") startVoice();
    return;
  }
  if (action === "back") {
    if (history.length > 1) history.back();
    else navigate("home");
    return;
  }
  if (action.startsWith("filter:")) {
    const [, kind, value] = action.split(":");
    state.filters[kind] = value;
    save();
    render();
    return;
  }
  if (action.startsWith("edit:")) {
    const [, key, id] = action.split(":");
    const item =
      state.proposals.find((p) => p.id === id) ||
      state.tasks.find((p) => p.id === id);
    openSheet({
      title:
        key === "amount"
          ? "Сумма долга, TRY"
          : key === "place"
            ? "Теплица"
            : ["title", "instruction"].includes(key)
              ? "Поручение"
              : "Когда сделать?",
      kind: "fact",
      id,
      key,
      value: item[key] ?? "",
      options: key === "place" ? ["Özvadi", "İmamlı", "Yeşilöz"] : null,
    });
    return;
  }
  if (action === "sheet-cancel") {
    closeSheet();
    return;
  }
  if (action === "sheet-save") {
    let value = document.querySelector("#fact-input").value;
    const c = sheetContext;
    if (c.key === "amount") {
      try {
        value = parseAmount(value);
      } catch {
        const er = document.querySelector("#sheet-error");
        er.textContent =
          "Укажите положительную сумму, до двух знаков после запятой.";
        er.hidden = false;
        return;
      }
    }
    if (c.kind === "translation") {
      if (!value.trim()) return;
      state.workerTask.translation = value;
      state.workerTask.translationReviewed = false;
    } else updateFact(state, c.id, c.key, value.trim?.() ?? value);
    sheetDrafts.delete([c.kind, c.id, c.key].join(":"));
    const rf = returnFocus;
    sheetContext = null;
    closeSheet();
    save();
    render();
    document.querySelector(`[data-testid="${CSS.escape(rf || "")}"]`)?.focus();
    return;
  }
  if (action.startsWith("confirm:")) {
    const id = action.split(":")[1];
    if (state.staleNext) {
      state.staleNext = false;
      const p = state.proposals.find((x) => x.id === id);
      p.revision++;
      save();
      openStale(id);
      return;
    }
    try {
      confirm(state, id, activeProposalRevision);
      save();
      const p = state.proposals.find((x) => x.id === id);
      toast("Подтверждено только в демо");
      navigate(p.type === "task" ? "tasks" : "review");
    } catch {
      openStale(id);
    }
    return;
  }
  if (action.startsWith("defer:") || action.startsWith("reject:")) {
    const [kind, id] = action.split(":"),
      p = state.proposals.find((x) => x.id === id);
    p.status = kind === "defer" ? "deferred" : "rejected";
    save();
    if (route !== "review") navigate("review");
    else render();
    toast(
      kind === "defer"
        ? "Отложено. Источник остаётся в очереди."
        : "Отклонено. Источник и история сохранены.",
    );
    return;
  }
  if (action === "demo-irrigation") {
    state.draft.text = SCENARIO_TEXT;
    state.draft.scenario = "irrigation";
    save();
    if (route === "capture/voice") {
      stopVoice();
      voiceStatus = "text";
    }
    render();
    return;
  }
  if (action === "demo-photo") {
    state.draft.file = {
      name: "TEST-чек DEMO-09.svg",
      kind: "image",
      url: "assets/receipt.svg",
      fixture: true,
    };
    state.draft.scenario = "receipt";
    save();
    render();
    return;
  }
  if (action === "clear-file") {
    state.draft.file = null;
    state.draft.scenario = null;
    save();
    render();
    return;
  }
  if (action === "capture-save" || action === "capture-retry") {
    await captureSave();
    return;
  }
  if (action === "capture-edit") {
    saveError = "";
    render();
    return;
  }
  if (action === "voice-fallback") {
    stopVoice();
    navigate("capture/text");
    return;
  }
  if (action === "voice-start") {
    startVoice();
    return;
  }
  if (action === "voice-stop") {
    stopVoice();
    voiceStatus = state.draft.text ? "text" : "stopped";
    render();
    return;
  }
  if (action.startsWith("task-done:")) {
    const t = state.tasks.find((x) => x.id === action.split(":")[1]);
    t.status = t.status === "done" ? "todo" : "done";
    save();
    render();
    toast("Изменено только в демо");
    return;
  }
  if (action === "choose-worker") {
    chooseWorker();
    return;
  }
  if (action.startsWith("worker-select:")) {
    state.workerTask.worker = action.split(":")[1];
    state.workerTask.translationReviewed = false;
    closeSheet();
    save();
    render();
    return;
  }
  if (action === "edit-translation") {
    openSheet({
      title: "Исправить текст на дари",
      kind: "translation",
      id: "worker-task",
      key: "translation",
      value: state.workerTask.translation,
    });
    return;
  }
  if (action === "assignment-confirm") {
    if (!state.workerTask.worker || !state.workerTask.translation.trim())
      return;
    state.workerTask.translationReviewed = true;
    state.workerTask.status = "assigned";
    save();
    render();
    toast("Назначено в демо, без внешней отправки");
    return;
  }
  if (action === "toggle-offline") {
    state.online = !state.online;
    save();
    render();
    return;
  }
  if (action === "toggle-error") {
    state.failNext = !state.failNext;
    save();
    render();
    return;
  }
  if (action === "toggle-stale") {
    state.staleNext = !state.staleNext;
    save();
    render();
    return;
  }
  if (action === "toggle-empty") {
    state.filters.review = "confirmed";
    navigate("review");
    return;
  }
  if (action === "toggle-large") {
    state.largeAmount = !state.largeAmount;
    state.receivables = state.receivables.filter((x) => x.id !== "stress");
    if (state.largeAmount)
      state.receivables.push({
        id: "stress",
        name: "TEST Крупная отдельная позиция",
        amount: 123456789.12,
        meaning: "Другой период · не входит в урожай сезона",
        date: "Срок не указан",
      });
    save();
    navigate("finance/receivable");
    return;
  }
  if (action === "reset-demo") {
    state = initialState();
    save();
    navigate("home");
    toast("Синтетический пример восстановлен");
    return;
  }
  if (action === "refresh-stale") {
    closeSheet();
    render();
    toast("Открыта актуальная версия. Проверьте факты.");
    return;
  }
});
function openStale() {
  returnFocus = "confirm-proposal";
  sheetContext = { kind: "stale" };
  sheetRoot.innerHTML = Sheet(
    "Карточка изменилась",
    `<p>Показанная версия устарела. Подтверждения нет. Откройте актуальные факты.</p>`,
    btn("Обновить карточку", "refresh-stale", "primary", "refresh-stale"),
  );
  document.querySelector("#fact-sheet").showModal();
}
window.addEventListener("online", () => {
  state.online = true;
  save();
});
window.addEventListener("offline", () => {
  state.online = false;
  save();
});
window.r3Demo = {
  reset() {
    state = initialState();
    save();
    render();
  },
  state: () => structuredClone(state),
  set(values) {
    Object.assign(state, values);
    save();
    render();
  },
};
if (!location.hash) history.replaceState(null, "", "#home");
render();
requestAnimationFrame(() => window.scrollTo(0, state.positions[route] || 0));
