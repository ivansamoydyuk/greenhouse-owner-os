export const e = (v) =>
  String(v ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
export const number = (v, d = 0) =>
  v == null
    ? "Неизвестно"
    : new Intl.NumberFormat("ru-RU", { maximumFractionDigits: d }).format(v);
export const money = (v) =>
  v == null
    ? "Неизвестно"
    : new Intl.NumberFormat("ru-RU", {
        minimumFractionDigits: Number.isInteger(v) ? 0 : 2,
        maximumFractionDigits: 2,
      }).format(v) + " TRY";
export const plural = (count, one, few, many) => {
  const n = Math.abs(count) % 100, last = n % 10;
  return n >= 11 && n <= 14 ? many : last === 1 ? one : last >= 2 && last <= 4 ? few : many;
};
const paths = {
  home: "M3 10 12 3l9 7M5 9v12h5v-7h4v7h5V9",
  review: "M9 4h6m-8 0H5v17h14V4h-2M8 12l3 3 5-6",
  tasks: "M8 5h13M8 12h13M8 19h13M3 5h.01M3 12h.01M3 19h.01",
  more: "M4 6h16M4 12h16M4 18h16",
  plus: "M12 5v14M5 12h14",
  video: "M3 5h12v14H3zM15 10l6-4v12l-6-4",
  harvest: "M12 21V9M12 13C5 13 3 9 3 4c6 0 9 3 9 9M12 17c7 0 9-4 9-9-6 0-9 3-9 9M9 21h6",
  camera: "M3 7h4l2-3h6l2 3h4v14H3zM16 13a4 4 0 1 1-8 0 4 4 0 0 1 8 0",
  voice:
    "M9 4a3 3 0 0 1 6 0v8a3 3 0 0 1-6 0V4M5 10v2a7 7 0 0 0 14 0v-2M12 19v3M8 22h8",
  text: "M5 3h10l4 4v14H5zM14 3v5h5M8 12h8M8 16h6",
  arrow: "m9 5 7 7-7 7",
  back: "m15 5-7 7 7 7",
  up: "M12 21V3m-7 7 7-7 7 7",
  down: "M12 3v18m-7-7 7 7 7-7",
  leaf: "M4 20c0-10 4-16 17-17 0 13-5 18-13 15M4 20 16 8",
  box: "m3 7 9-4 9 4v11l-9 4-9-4zM3 7l9 5 9-5M12 12v10",
  people:
    "M9 10a4 4 0 1 0 0-8 4 4 0 0 0 0 8M2 22v-4a7 7 0 0 1 14 0v4M18 3a4 4 0 0 1 0 8M19 15c3 1 3 4 3 7",
  check: "m5 12 4 4L20 5",
  close: "m5 5 14 14M19 5 5 19",
  edit: "m4 16-1 5 5-1L21 7l-4-4zM14 6l4 4",
  calendar: "M4 5h16v16H4zM4 10h16M8 2v5M16 2v5",
  clock: "M12 8v5l3 2M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0",
  info: "M12 11v6M12 7h.01M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0",
  search: "M20 20l-5-5M17 9a8 8 0 1 1-16 0 8 8 0 0 1 16 0",
  refresh: "M20 7a9 9 0 1 0 1 9M20 2v6h-6",
  wallet: "M3 6h18v15H3zM3 6V3h15M16 11h5v5h-5z",
  warning: "m12 2 11 19H1zM12 9v5M12 17h.01",
};
export const icon = (name, cls = "") =>
  `<svg class="icon ${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${paths[name] || paths.info}"/></svg>`;
export const btn = (
  text,
  action,
  cls = "secondary",
  test = action,
  extra = "",
) =>
  `<button class="btn ${cls}" data-action="${e(action)}" data-testid="${e(test)}" ${extra}>${text}</button>`;
export const link = (text, to, cls = "", test = "") =>
  `<a href="#${e(to)}" class="${cls}" ${test ? `data-testid="${e(test)}"` : ""}>${text}</a>`;
export function Header(title, subtitle = "", back = false, extra = "") {
  return `<header class="page-header">${back ? btn(icon("back"), "back", "icon-button", "back", 'aria-label="Назад"') : ""}<div><h1>${e(title)}</h1>${subtitle ? `<p class="muted">${e(subtitle)}</p>` : ""}</div>${extra}</header>`;
}
export function Navigation(page, count) {
  const item = (id, label, ic) => link(`${icon(ic)}<span>${label}</span>${id === "review" && count ? `<span class="nav-dot">${count}</span>` : ""}`, id, page === id ? "active" : "", `nav-${id}`);
  return `<nav class="navigation" aria-label="Основные разделы">${item("home", "Главная", "home")}${item("review", "Проверка", "review")}<button class="capture-toggle" data-action="radial-toggle" data-testid="radial-toggle" aria-label="AI Capture — добавить источник" aria-expanded="false" aria-controls="radial-menu"><span class="capture-orb">${icon("leaf")}</span><span>AI Capture</span></button>${item("tasks", "Задачи", "tasks")}${item("more", "Ещё", "more")}</nav>`;
}
export function CaptureBar() {
  return `<div id="radial-menu" class="radial-menu" role="group" aria-label="Добавить источник" hidden>${[
    ["photo", "Фото", "camera"], ["voice", "Голос", "voice"], ["text", "Текст", "text"],
  ].map(([id, label, ic]) => btn(`<span class="radial-orb">${icon(ic)}</span><span>${label}</span>`, `capture-${id}`, `radial-item radial-${id}`, `capture-${id}`)).join("")}<p class="radial-hint">Источник сохраняется в DEMO</p></div>`;
}
export function AppShell(body, page, count, focused = false) {
  return `<div class="shell"><div class="demo-strip">ДЕМО — данные не проводятся</div><main id="main" tabindex="-1">${body}</main>${focused ? "" : `<div class="radial-backdrop" data-action="radial-close" data-testid="radial-outside" hidden></div><footer class="dock">${CaptureBar()}${Navigation(page, count)}</footer>`}</div>`;
}
export function MetricCard({
  label,
  value,
  detail,
  to,
  primary = false,
  test,
  ic,
}) {
  return link(
    `<span class="metric-label">${icon(ic)}${e(label)}</span><strong class="metric-value">${number(value, 2)}</strong><span class="currency">TRY</span><span class="metric-detail">${e(detail)} ${icon("arrow")}</span>`,
    to,
    `metric-card ${primary ? "mint" : ""} ${number(value, 2).length > 10 ? "long-money" : ""}`,
    test,
  );
}
export const FilterChip = (label, value, selected, kind, test = "") =>
  btn(
    e(label),
    `filter:${kind}:${value}`,
    `filter-chip ${selected ? "selected" : ""}`,
    test || `${kind}-${value}`,
    'aria-pressed="' + selected + '"',
  );
export const FactChip = (label, value, action, test = "") =>
  btn(
    `<span class="fact-label">${e(label)}</span><span>${e(value || "Не указано")}</span>${icon("edit")}`,
    action,
    "fact-chip",
    test || action,
  );
export const stateBox = (kind, title, detail, action = "") =>
  `<section class="state ${kind}" role="${kind === "error" ? "alert" : "status"}"><span class="state-icon">${icon(kind === "error" ? "warning" : kind === "loading" ? "clock" : "info")}</span><h2>${e(title)}</h2><p>${e(detail)}</p>${action}</section>`;
export const EmptyState = (title, detail, action = "") =>
  stateBox("empty", title, detail, action);
export const ErrorState = (title, detail, action = "") =>
  stateBox("error", title, detail, action);
export const LoadingState = (title = "Готовим демо-карточку") =>
  stateBox(
    "loading",
    title,
    "Источник остаётся на этом устройстве. Учёт не меняется.",
  );
export function TaskRow(t, test = "") {
  return link(
    `<span class="task-icon ${t.status === "done" ? "done" : ""}">${icon(t.status === "done" ? "check" : "tasks")}</span><div><strong>${e(t.actionNeedsReview ? "Уточнить текст действия" : t.title)}</strong><p>${e(t.place || "Теплица не указана")} · ${e(t.when || "Без точной даты")}</p></div>${icon("arrow")}`,
    `task/${t.id}`,
    "task-row",
    test || `task-${t.id}`,
  );
}
export function StockRow(s) {
  return link(
    `<span class="stock-icon">${icon("box")}</span><div><strong>${e(s.name)}</strong><p>${e(s.place)}</p></div><strong class="stock-quantity">${s.quantity == null ? "Неизвестно" : number(s.quantity, 3) + " " + e(s.unit)}</strong>`,
    `stock/${s.id}`,
    "stock-row",
    `stock-${s.id}`,
  );
}
export function ReviewCard(p) {
  const type = {
    payable: "Новый долг",
    harvest: "Чек депо",
    stock: "Приход на склад",
    task: "Моя задача",
    unknown: "Нужна проверка",
  }[p.type];
  const value =
    p.type === "payable"
      ? p.amount == null
        ? "Сумма не указана"
        : money(p.amount)
      : p.type === "harvest"
        ? `${number(p.kg, 3)} кг · ${money(p.amount)}`
        : p.type === "stock"
          ? `${number(p.quantity, 3)} ${p.unit}`
          : p.type === "task"
            ? p.actionNeedsReview ? "Уточнить текст действия" : p.instruction
            : p.meaning;
  return `<article class="review-card"><div class="review-top"><span class="eyebrow">${type}</span><span class="tag ${p.type === "unknown" || p.amount === null ? "amber" : ""}">${p.status === "deferred" ? "Отложено" : p.type === "unknown" || p.amount === null ? "Уточнение" : "К проверке"}</span></div>${link(`<div class="review-summary"><div><h2>${e(p.actionNeedsReview ? "Уточнить текст действия" : p.title)}</h2>${p.type === "task" ? "" : `<strong class="review-value">${e(value)}</strong>`}<p>${e(p.place || "Теплица не указана")} · ${e(p.when || p.date || "Дата не указана")}</p></div>${p.image ? `<img class="source-thumb" src="${e(p.image)}" alt="Миниатюра синтетического источника">` : ""}</div>`, `review/${p.id}`, "review-body", `review-${p.id}`)}<div class="review-actions">${link(p.amount === null ? "Уточнить сумму" : p.type === "unknown" ? "Открыть источник" : "Проверить", `review/${p.id}`, "btn primary", `review-open-${p.id}`)}${btn("Позже", `defer:${p.id}`, "text-button", `defer-${p.id}`)}</div></article>`;
}
export const Sheet = (title, body, footer) =>
  `<dialog id="fact-sheet" class="sheet" aria-labelledby="sheet-title"><form method="dialog" class="sheet-form"><div class="sheet-header"><h2 id="sheet-title">${e(title)}</h2>${btn(icon("close"), "sheet-cancel", "icon-button", "sheet-close", 'aria-label="Закрыть" type="button"')}</div>${body}<div class="sheet-actions">${footer}</div></form></dialog>`;
