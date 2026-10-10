/** Same-origin TEST adapter. No demo ledger, fixture fallback or inferred business effects. */
export const CAPABILITIES = Object.freeze({
  capture: "CONNECTED_TEST",
  expense: "CONNECTED_TEST",
  finance: "CONNECTED_TEST",
  harvest: "CONNECTED_TEST",
  inventory: "CONNECTED_TEST",
  personalTasks: "CONNECTED_TEST",
  workersR1: "CONNECTED_TEST",
  fertilizer: "DEMO_ONLY",
  realDispatch: "NOT_AVAILABLE",
  latePostedReceiptLink: "NOT_AVAILABLE",
});
export const canRecoverSourceExpense = (source) =>
  !source.proposals?.length &&
  source.job?.state === "FAILED" &&
  source.job.attempts >= 3;

/** Capture the facts visible at submit, before file reads or durable-store awaits. */
export function submissionSnapshot(
  { draft, current, route },
  fields,
  file = null,
) {
  const submittedFields = { ...fields };
  delete submittedFields.file;
  return {
    draft: structuredClone(draft),
    current: structuredClone(current),
    route,
    fields: submittedFields,
    file,
  };
}

export const canonical = (value) =>
  JSON.stringify(value, (_, v) =>
    v && typeof v === "object" && !Array.isArray(v)
      ? Object.fromEntries(
          Object.entries(v).sort(([a], [b]) => a.localeCompare(b)),
        )
      : v,
  );
/** Expand decimal notation for display only; never convert an amount to float. */
export function exactDecimalString(value) {
  if (value == null) return null;
  const match = /^([+-]?)(\d+)(?:\.(\d+))?(?:[eE]([+-]?\d+))?$/.exec(
    String(value),
  );
  if (!match) return null;
  const [, sign, whole, fraction = "", exponent = "0"] = match;
  if (whole.length + fraction.length > 4096 || exponent.length > 6) return null;
  const power = BigInt(exponent);
  if (power < -1000n || power > 1000n) return null;
  const digits = whole + fraction;
  if (/^0+$/.test(digits)) return "0";
  // The bounded integer is a string index, never a financial value.
  const point = whole.length + Number(power);
  const expanded =
    point <= 0
      ? "0." + "0".repeat(-point) + digits
      : point >= digits.length
        ? digits + "0".repeat(point - digits.length)
        : digits.slice(0, point) + "." + digits.slice(point);
  const [integer, decimals] = expanded.split(".");
  return (
    (sign === "-" ? "-" : "") +
    integer.replace(/^0+(?=\d)/, "") +
    (decimals ? "." + decimals : "")
  );
}

export function cents(value) {
  const s = String(value);
  if (!/^-?\d+(\.\d{1,2})?$/.test(s)) throw Error("Некорректная точная сумма");
  const negative = s.startsWith("-"),
    [whole, fraction = ""] = s.replace("-", "").split(".");
  return (
    (BigInt(whole) * 100n + BigInt(fraction.padEnd(2, "0"))) *
    (negative ? -1n : 1n)
  );
}
export function decimal(value) {
  const n = BigInt(value),
    abs = n < 0n ? -n : n;
  return `${n < 0n ? "-" : ""}${abs / 100n}.${String(abs % 100n).padStart(2, "0")}`;
}
export function exactMoney(value) {
  const s = String(value).trim().replace(",", ".");
  if (!/^\d{1,18}(\.\d{1,2})?$/.test(s) || cents(s) <= 0n)
    throw Error("Укажите положительную сумму до двух знаков после запятой.");
  return decimal(cents(s));
}
export function quantity(value) {
  const s = String(value).trim().replace(",", ".");
  if (!/^\d{1,18}(\.\d{1,3})?$/.test(s) || !/[1-9]/.test(s))
    throw Error(
      "Укажите положительное количество до трёх знаков после запятой.",
    );
  return s;
}
export function paymentPayload({
  entity,
  party,
  direction,
  currency,
  account,
  amount,
  date,
  allocations,
  obligations,
}) {
  amount = exactMoney(amount);
  if (!account || account.currency !== currency)
    throw Error("Выберите счёт в валюте долга.");
  if (!["in", "out"].includes(direction)) throw Error("Укажите направление.");
  const seen = new Set(),
    checked = allocations
      .filter((x) => x.amount && cents(x.amount) !== 0n)
      .map((x) => {
        const o = obligations.find((o) => o.obligation_id === x.obligation_id),
          n = exactMoney(x.amount);
        if (
          !o ||
          seen.has(x.obligation_id) ||
          o.counterparty_id !== party ||
          o.currency !== currency ||
          o.direction !== (direction === "in" ? "receivable" : "payable") ||
          cents(n) > cents(o.remaining_amount)
        )
          throw Error(
            "Проверьте выбранный документ, валюту и открытый остаток.",
          );
        seen.add(x.obligation_id);
        return { obligation_id: x.obligation_id, amount: n };
      });
  if (checked.reduce((sum, x) => sum + cents(x.amount), 0n) !== cents(amount))
    throw Error(
      "Распределение должно точно совпадать с суммой. Нераспределённый аванс здесь не создаётся.",
    );
  return {
    entity_id: entity,
    counterparty_id: party,
    money_account_id: account.money_account_id || account.id,
    direction,
    amount,
    currency,
    event_date: date,
    source: "owner:r3-connected-test",
    reason: "Владелец проверил документы и распределение",
    allocations: checked,
  };
}
export function totalByCurrency(obligations, direction) {
  const sums = {};
  for (const o of obligations) {
    if (
      o.direction !== direction ||
      !["open", "partial"].includes(o.settlement_status)
    )
      continue;
    sums[o.currency] = (sums[o.currency] || 0n) + cents(o.remaining_amount);
  }
  return Object.fromEntries(
    Object.entries(sums).map(([c, n]) => [c, decimal(n)]),
  );
}
export class ApiError extends Error {
  constructor(message, status, data) {
    super(message);
    this.status = status;
    this.data = data;
  }
}
export class BrowserStore {
  async db() {
    return new Promise((resolve, reject) => {
      const r = indexedDB.open("r3-connected-test-v1", 1);
      r.onupgradeneeded = () =>
        r.result.createObjectStore("records", { keyPath: "key" });
      r.onsuccess = () => resolve(r.result);
      r.onerror = () =>
        reject(
          Error("Память телефона недоступна. На сервер ещё не сохранено."),
        );
    });
  }
  async transact(mode, fn) {
    const db = await this.db();
    return new Promise((resolve, reject) => {
      const tx = db.transaction("records", mode),
        request = fn(tx.objectStore("records"));
      let result;
      request.onsuccess = () => {
        result = request.result;
      };
      tx.oncomplete = () => {
        db.close();
        resolve(result);
      };
      tx.onerror = tx.onabort = () => {
        db.close();
        reject(
          Error(
            "Не удалось сохранить копию на телефоне. Оставьте окно открытым.",
          ),
        );
      };
    });
  }
  async claim(record) {
    const db = await this.db();
    return new Promise((resolve, reject) => {
      const tx = db.transaction("records", "readwrite"),
        objectStore = tx.objectStore("records");
      const read = objectStore.get(record.key);
      let result;
      read.onsuccess = () => {
        result = read.result;
        if (!result || result.state === "REJECTED") {
          result = record;
          objectStore.put(record);
        }
      };
      tx.oncomplete = () => {
        db.close();
        resolve(result);
      };
      tx.onerror = tx.onabort = () => {
        db.close();
        reject(
          Error(
            "Не удалось закрепить исходный запрос на телефоне. На сервер не отправлено.",
          ),
        );
      };
    });
  }
  async put(record) {
    return this.transact("readwrite", (s) => s.put(record));
  }
  async get(key) {
    return this.transact("readonly", (s) => s.get(key));
  }
  async all() {
    return this.transact("readonly", (s) => s.getAll());
  }
  async delete(key) {
    return this.transact("readwrite", (s) => s.delete(key));
  }
}
export class ConnectedAPI {
  constructor({
    fetcher = globalThis.fetch.bind(globalThis),
    store = new BrowserStore(),
    onAuth = () => {},
  } = {}) {
    this.fetcher = fetcher;
    this.store = store;
    this.onAuth = onAuth;
    this.scope = null;
    this.generation = 0;
    this.inflight = new Map();
  }
  configure(config) {
    if (
      !config.entity_id ||
      !config.owner_id ||
      config.environment !== "TEST_PREVIEW" ||
      !config.feature_sha ||
      config.backend_connected !== true ||
      config.actual_operations !== false
    )
      throw Error("Не подтверждён изолированный CONNECTED TEST.");
    const next = `${config.entity_id}:${config.owner_id}`;
    if (this.scope !== next) {
      this.generation++;
      this.scope = next;
    }
    this.entity = config.entity_id;
    this.root = `/v1/entities/${this.entity}`;
  }
  async request(path, options = {}) {
    let response;
    try {
      response = await this.fetcher(path, {
        credentials: "same-origin",
        cache: "no-store",
        ...options,
      });
    } catch {
      throw new ApiError(
        ["GET", "HEAD"].includes((options.method || "GET").toUpperCase())
          ? "Не удалось получить данные сервера. Проверьте соединение и повторите загрузку."
          : "Ответ сервера не получен. Действие могло сохраниться; повторите с тем же ключом.",
        0,
      );
    }
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      if (response.status === 401) {
        this.generation++;
        this.onAuth();
      }
      const reason =
        data?.error?.message ||
        (typeof data.detail === "string" ? data.detail : null) ||
        (Array.isArray(data.detail)
          ? data.detail.map((x) => `${x.loc?.at(-1)}: ${x.msg}`).join("; ")
          : null);
      throw new ApiError(
        response.status === 401
          ? "Сессия закончилась. Войдите снова; копии и ключи повтора сохранены."
          : reason || `Раздел недоступен (HTTP ${response.status}).`,
        response.status,
        data,
      );
    }
    return data;
  }
  get(path) {
    return this.request(path);
  }
  entityGet(path) {
    return this.get(this.root + path);
  }
  async mutate(path, body, intent) {
    if (!this.scope) throw Error("Сначала войдите.");
    if (!intent) throw Error("Нужен стабильный идентификатор действия.");
    const scope = this.scope,
      generation = this.generation,
      payload = canonical(body);
    // Identity belongs to a logical operation, never its mutable business facts.
    // The server enforces payload binding even between racing browser tabs.
    const bytes = new TextEncoder().encode(canonical({ scope, path, intent }));
    const digest = Array.from(
      new Uint8Array(await crypto.subtle.digest("SHA-256", bytes)),
      (x) => x.toString(16).padStart(2, "0"),
    ).join("");
    const key = `${scope}:request:${digest}`;
    const pending = this.inflight.get(key);
    if (pending) {
      if (pending.payload !== payload)
        throw Error(
          "Предыдущая отправка содержит другие факты. Сначала проверьте её результат.",
        );
      return pending.promise;
    }
    const run = (async () => {
      const proposed = {
        key,
        scope,
        type: "request",
        path,
        body,
        keyHeader: `r3:${digest}`,
        state: "PENDING",
        created: new Date().toISOString(),
      };
      const saved = await this.store.claim(proposed);
      if (canonical(saved.body) !== payload)
        throw Error(
          "Предыдущая отправка этого действия ещё не разрешена. Повторите исходный запрос; не меняйте факты до проверки результата.",
        );
      if (this.scope !== scope || this.generation !== generation)
        throw Error("Сессия изменилась. Повторите после входа.");
      try {
        const result = await this.request(path, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "Idempotency-Key": saved.keyHeader,
          },
          body: payload,
        });
        await this.store.put({ ...saved, state: "ACK", result });
        if (this.scope !== scope || this.generation !== generation)
          throw Error(
            "Ответ прежней сессии сохранён. Обновите текущую запись.",
          );
        return result;
      } catch (error) {
        if ([400, 413, 415, 422].includes(error.status))
          await this.store.put({
            ...saved,
            state: "REJECTED",
            error: error.message,
          });
        else if (error.status === 409)
          await this.store.put({
            ...saved,
            state: "CONFLICT",
            error: error.message,
          });
        else if (error.status !== 401)
          await this.store.put({ ...saved, error: error.message });
        throw error;
      }
    })();
    this.inflight.set(key, { payload, promise: run });
    try {
      return await run;
    } finally {
      this.inflight.delete(key);
    }
  }
  entityPost(path, body, intent) {
    return this.mutate(this.root + path, body, intent);
  }
  async draft(id, value) {
    await this.store.put({
      key: `${this.scope}:draft:${id}`,
      scope: this.scope,
      type: "draft",
      id,
      value,
    });
  }
  async readDraft(id) {
    return (await this.store.get(`${this.scope}:draft:${id}`))?.value;
  }
  async records() {
    return (await this.store.all()).filter((x) => x.scope === this.scope);
  }
  async pendingRequests() {
    return (await this.records()).filter(
      (x) => x.type === "request" && x.state === "PENDING",
    );
  }
}
export async function fileSource(file) {
  if (!file) return {};
  if (
    !["image/jpeg", "image/png", "application/pdf"].includes(file.type) ||
    file.size > 5 * 1024 * 1024
  )
    throw Error("Исходник: JPEG, PNG или PDF до 5 МБ. HEIC пока недоступен.");
  const bytes = new Uint8Array(await file.arrayBuffer());
  let binary = "";
  for (let i = 0; i < bytes.length; i += 8192)
    binary += String.fromCharCode(...bytes.subarray(i, i + 8192));
  return {
    file_base64: btoa(binary),
    filename: file.name,
    mime_type: file.type,
  };
}
