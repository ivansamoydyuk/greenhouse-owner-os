import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import { chromium, webkit } from "playwright";
import { navigateOwner, revealOwnerControl } from "./navigation.mjs";
import { syntheticPixelPng } from "../fixtures/owner-r3-media.mjs";

function syntheticPdfReceipt(price = "10.00") {
  const stream = `BT /F1 18 Tf 40 760 Td (TEST DEPOT RECEIPT - Grand 60 kg x ${price} TRY - ADET 6) Tj ET`;
  const objects = [
    "<< /Type /Catalog /Pages 2 0 R >>",
    "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
    "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
    "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    `<< /Length ${Buffer.byteLength(stream)} >>\nstream\n${stream}\nendstream`,
  ];
  let pdf = "%PDF-1.4\n";
  const offsets = [0];
  objects.forEach((o, i) => {
    offsets.push(Buffer.byteLength(pdf));
    pdf += `${i + 1} 0 obj\n${o}\nendobj\n`;
  });
  const xref = Buffer.byteLength(pdf);
  pdf += `xref\n0 6\n0000000000 65535 f \n${offsets
    .slice(1)
    .map((n) => String(n).padStart(10, "0") + " 00000 n ")
    .join(
      "\n",
    )}\ntrailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`;
  return Buffer.from(pdf);
}
const base = process.env.PREVIEW_URL || "http://127.0.0.1:4173";
const out = process.env.E2E_ARTIFACTS || "artifacts/r3-connected";
await fs.mkdir(out, { recursive: true });
const result = {
  sha: process.env.OWNER_PREVIEW_FEATURE_SHA,
  mode: "CONNECTED_TEST",
  journeys: [],
  records: {
    expenses: [],
    payments: [],
    reports: [],
    settlements: [],
    receipts: [],
    products: [],
    tasks: [],
    workerTasks: [],
  },
  errors: [],
  screenshots: [],
  applicationFailures: [],
};
const read = async (ctx, url) => {
  const r = await ctx.request.get(base + url);
  assert.equal(r.status(), 200, await r.text());
  return r.json();
};
let activePage;
async function snap(page, name) {
  await page.screenshot({
    path: path.join(out, name + ".png"),
    fullPage: true,
  });
  result.screenshots.push(name + ".png");
}
async function route(page, hash) {
  await page.goto(base + "/r3/connected.html#" + hash);
  await page.waitForSelector("#main");
  await page
    .getByText("Читаем сохранённые данные…", { exact: true })
    .waitFor({ state: "hidden" });
}
async function nav(page, hash) {
  await page.locator(`a[href="#${hash}"]`).first().click();
  await page
    .getByText("Читаем сохранённые данные…", { exact: true })
    .waitFor({ state: "hidden" });
}
async function click(page, text) {
  await page.getByRole("button", { name: text, exact: true }).click();
}
async function fill(page, name, value) {
  await page.locator(`[name="${name}"]`).fill(String(value));
}
async function select(page, name, value) {
  await page.locator(`[name="${name}"]`).selectOption(String(value));
}
async function waitHash(page, starts) {
  await page.waitForFunction(
    (prefix) => location.hash.startsWith("#" + prefix),
    starts,
  );
  await page
    .getByText("Читаем сохранённые данные…", { exact: true })
    .waitFor({ state: "hidden" });
}
async function edit(page, action, value, selectValue = false) {
  await page.locator(`[data-action="edit:${action}"]`).click();
  if (selectValue) await select(page, "value", value);
  else await fill(page, "value", value);
  await click(page, "Сохранить факт");
  await page.locator(".focus-sheet").waitFor({ state: "hidden" });
  await page
    .getByText("Читаем сохранённые данные…", { exact: true })
    .waitFor({ state: "hidden" });
}
async function layout(page, name) {
  const checks = await page.evaluate(() => ({
    pageWidth: document.documentElement.clientWidth,
    scrollWidth: document.documentElement.scrollWidth,
    overflows: [
      ...document.querySelectorAll(
        "input,select,textarea,.metric-value,.exact-value",
      ),
    ]
      .filter(
        (e) =>
          e.getBoundingClientRect().width && e.scrollWidth > e.clientWidth + 2,
      )
      .map((e) => ({
        tag: e.tagName,
        text: e.textContent,
        width: e.clientWidth,
        scroll: e.scrollWidth,
      })),
  }));
  assert.ok(
    checks.scrollWidth <= checks.pageWidth + 2,
    JSON.stringify({ name, ...checks }),
  );
  assert.deepEqual(checks.overflows, [], JSON.stringify({ name, ...checks }));
}
try {
  for (const [engine, browserType] of [
    ["chromium", chromium],
    ["webkit", webkit],
  ]) {
    const browser = await browserType.launch();
    const context = await browser.newContext({
      viewport: { width: 390, height: 844 },
      deviceScaleFactor: 1,
    });
    const page = await context.newPage();
    activePage = page;
    page.on("pageerror", (error) =>
      result.applicationFailures.push({ engine, message: error.message }),
    );
    await page.goto(base + "/r3/");
    await page.locator("#secret").fill(process.env.PREVIEW_SECRET);
    await click(page, "Войти");
    await page.waitForURL("**/connected.html");
    await page.getByText("Добрый день", { exact: true }).waitFor();
    const config = await read(context, "/preview/config"),
      root = "/v1/entities/" + config.entity_id;
    assert.equal(config.feature_sha, result.sha);
    assert.equal(config.backend_connected, true);
    assert.equal(config.actual_operations, false);
    assert.equal(config.synthetic_only, true);
    const fixture = config.synthetic_fixture;
    await snap(page, engine + "-390-home");
    await layout(page, "home");
    // A: actual source controls, durable original, one-fact revisions and trusted confirmation.
    await page.locator("[data-testid=home-expense]").click();
    const original = `TEST ${engine}: купил фильтр за 125 TRY наличными 10.10.2026`;
    await fill(page, "text", original);
    await click(page, "Сохранить оригинал");
    await waitHash(page, "source/");
    const sourceId = page.url().split("#source/")[1];
    await page.locator('a[href^="#review/"]').first().click();
    await waitHash(page, "review/");
    const manualRecovery = page.getByRole("button", {
      name: "Оформить прямой расход",
      exact: true,
    });
    if (await manualRecovery.count()) {
      const originHash = new URL(page.url()).hash;
      await manualRecovery.click();
      await page.waitForFunction(
        (previous) => location.hash !== previous,
        originHash,
      );
      await waitHash(page, "review/");
    }
    const proposalId = page.url().split("#review/")[1];
    let proposal = await read(
      context,
      root + "/owner/confirmations/" + proposalId + "/preview",
    );
    assert.equal(proposal.source.text, original);
    assert.equal(proposal.action_type, "DIRECT_EXPENSE");
    for (const [key, value, isSelect] of [
      ["description", `TEST ${engine} фильтр после уточнения`, false],
      ["amount", "125.00", false],
      ["currency", "TRY", true],
      ["event_date", "2026-10-10", false],
      ["money_account_id", fixture.money_accounts[0].id, true],
    ]) {
      if (proposal.revision.payload[key] !== value) {
        await edit(page, key, value, isSelect);
        proposal = await read(
          context,
          root + "/owner/confirmations/" + proposalId + "/preview",
        );
      }
    }
    const beforeRevision = proposal.version;
    await edit(page, "amount", "126.00");
    proposal = await read(
      context,
      root + "/owner/confirmations/" + proposalId + "/preview",
    );
    assert.equal(proposal.version, beforeRevision + 1);
    assert.equal(proposal.source.text, original);
    assert.equal(proposal.revision.payload.amount, "126.00");
    await snap(page, engine + "-390-expense-review");
    // Drop the response only AFTER the server has committed; retry actual button.
    let lost = false;
    await page.route(
      "**/owner/confirmations/" + proposalId + "/confirm",
      async (intercept) => {
        if (!lost) {
          lost = true;
          const response = await intercept.fetch();
          assert.equal(response.status(), 200);
          await intercept.abort("failed");
        } else await intercept.continue();
      },
    );
    await click(page, "Подтвердить действие в TEST");
    await page
      .locator("#action-error")
      .filter({ hasText: "Ответ сервера не получен" })
      .waitFor();
    await page.reload();
    await page
      .getByText("Сервер подтвердил действие. Оригинал и версии сохранены.", {
        exact: true,
      })
      .waitFor();
    proposal = await read(
      context,
      root + "/owner/confirmations/" + proposalId + "/preview",
    );
    assert.equal(proposal.state, "POSTED");
    assert.equal(proposal.source.text, original);
    assert.equal(
      (
        await context.request.get(
          base + root + "/owner/captures/" + sourceId + "/original",
        )
      ).status(),
      200,
    );
    result.records.expenses.push({
      source_id: sourceId,
      proposal_id: proposalId,
      original,
      amount: "126.00",
      effect: proposal.current_effect,
    });
    await page.unroute("**/owner/confirmations/" + proposalId + "/confirm");
    await route(page, "sources");
    await page
      .getByRole("button", { name: "Повторить тот же запрос", exact: true })
      .first()
      .click();
    await page
      .getByText("Повтор подтверждён сервером. Проверьте запись.", {
        exact: true,
      })
      .waitFor();
    await route(page, "finance");
    await page
      .getByText(`TEST ${engine} фильтр после уточнения`, { exact: true })
      .waitFor();
    result.journeys.push({
      engine,
      name: "A source revision trusted expense lost response hard reload",
      pass: true,
    });
    // E: all personal tasks beyond Home three, DONE, hard reload, completed and restore.
    await route(page, "tasks");
    assert.ok((await page.locator('a[href^="#personal/"]').count()) >= 5);
    await page.locator('a[href^="#personal/"]').last().click();
    const taskId = page.url().split("#personal/")[1];
    await click(page, "Отметить готовой");
    await page
      .getByRole("button", { name: "Вернуть в активные", exact: true })
      .waitFor();
    await page.reload();
    await page
      .getByRole("button", { name: "Вернуть в активные", exact: true })
      .waitFor();
    await nav(page, "tasks/DONE");
    await page.locator(`a[href="#personal/${taskId}"]`).click();
    await click(page, "Вернуть в активные");
    await page
      .getByRole("button", { name: "Отметить готовой", exact: true })
      .waitFor();
    result.records.tasks.push(taskId);
    await snap(page, engine + "-390-personal-restored");
    result.journeys.push({
      engine,
      name: "E all tasks DONE reload completed restore",
      pass: true,
    });
    // D: inline product creation, independently configured warehouse, durable original, movement.
    await route(page, "inventory");
    await nav(page, "quick/receipt");
    await click(page, "Создать новый товар");
    const productName = `TEST ${engine} New Product <img src=x onerror=alert(1)>`;
    await fill(page, "name", productName);
    await select(page, "unit", "kg");
    await click(page, "Создать товар");
    await page.locator(".focus-sheet").waitFor({ state: "hidden" });
    let inv = await read(context, root + "/inventory");
    const product = inv.products.find((p) => p.name === productName);
    assert.ok(product);
    await select(page, "location", fixture.location.id);
    await fill(page, "quantity", "12.5");
    await fill(page, "original", `TEST ${engine} original receipt 12.5 kg`);
    await click(page, "Проверить приход");
    await waitHash(page, "receipt/");
    const receiptId = page.url().split("#receipt/")[1];
    await snap(page, engine + "-390-receipt-review");
    await click(page, "Подтвердить физический приход в TEST");
    await page
      .getByText(
        "Сервер подтвердил приход. Физическое движение создано один раз.",
        { exact: true },
      )
      .waitFor();
    await page.reload();
    await page
      .getByText(
        "Сервер подтвердил приход. Физическое движение создано один раз.",
        { exact: true },
      )
      .waitFor();
    inv = await read(context, root + "/inventory");
    assert.equal(inv.opening_state, "UNINITIALIZED_VERIFY");
    assert.equal(
      inv.stock.find((s) => s.product_id === product.id).quantity,
      "12.5",
    );
    result.records.products.push(product.id);
    result.records.receipts.push({
      id: receiptId,
      quantity: "12.5",
      product_id: product.id,
      location_id: fixture.location.id,
    });
    await snap(page, engine + "-390-receipt-posted");
    result.journeys.push({
      engine,
      name: "D inline product receipt exact physical movement UNKNOWN opening",
      pass: true,
    });
    // Existing product at a different independently configured location.
    assert.equal(fixture.locations.length, 2);
    await route(page, "quick/receipt/" + fixture.product.id);
    await select(page, "location", fixture.locations[1].id);
    await fill(page, "quantity", "3");
    await fill(
      page,
      "original",
      `TEST ${engine} existing product other location`,
    );
    await click(page, "Проверить приход");
    await waitHash(page, "receipt/");
    const existingReceiptId = page.url().split("#receipt/")[1];
    await click(page, "Подтвердить физический приход в TEST");
    await page
      .getByText(
        "Сервер подтвердил приход. Физическое движение создано один раз.",
        { exact: true },
      )
      .waitFor();
    result.records.receipts.push({
      id: existingReceiptId,
      quantity: "3",
      product_id: fixture.product.id,
      location_id: fixture.locations[1].id,
    });
    // AUD011: malicious stored product also remains literal in the reachable old modal.
    await page.goto(base + "/");
    await page
      .getByRole("heading", { name: "Моя теплица", exact: true })
      .waitFor();
    await navigateOwner(page, "inventory");
    await (
      await revealOwnerControl(
        page.locator(`[data-iproduct="${product.id}"]`).first(),
      )
    ).click();
    assert.equal(await page.locator("#sheet-title").textContent(), productName);
    assert.equal(await page.locator("#sheet-title img").count(), 0);
    const csp = (await context.request.get(base + "/")).headers()[
      "content-security-policy"
    ];
    assert.ok(csp.includes("script-src 'self'"));
    await snap(page, engine + "-390-legacy-literal-product");
    await page
      .locator("#sheet")
      .getByRole("button", { name: "Закрыть", exact: true })
      .click();
    await (
      await revealOwnerControl(
        page.locator(`[data-iproduct="${fixture.product.id}"]`).first(),
      )
    ).click();
    assert.equal(
      await page.locator("#sheet-title").textContent(),
      fixture.product.name,
    );
    result.journeys.push({
      engine,
      name: "AUD011 stored adversarial and ordinary legacy modal title",
      pass: true,
    });
    // C: immutable field original, new depot document with actual binary and caption, explicit link.
    await route(page, "quick/harvest");
    await select(page, "greenhouse", fixture.areas[0].id);
    await fill(page, "count", "7");
    await fill(page, "kg", "70");
    await fill(page, "original", `TEST ${engine} field original seven cuts`);
    await click(page, "Сохранить полевой срез");
    await waitHash(page, "report/");
    const reportId = page.url().split("#report/")[1];
    await page.reload();
    await page
      .getByText(`TEST ${engine} field original seven cuts`, { exact: true })
      .waitFor();
    await nav(page, "attach-depot/" + reportId);
    const receiptPrice = engine === "webkit" ? "123456789.12" : "10.00";
    const originalBytes =
      engine === "webkit"
        ? syntheticPdfReceipt(receiptPrice)
        : syntheticPixelPng();
    await page.locator("[name=file]").setInputFiles({
      name: `TEST-${engine}-receipt.${engine === "webkit" ? "pdf" : "png"}`,
      mimeType: engine === "webkit" ? "application/pdf" : "image/png",
      buffer: originalBytes,
    });
    await fill(page, "caption", `TEST ${engine} separate owner caption`);
    await select(page, "depot", fixture.counterparties.depot.id);
    await fill(
      page,
      "link_evidence",
      `TEST ${engine} explicitly the selected cut`,
    );
    await fill(page, "quality", "Grand");
    await fill(page, "kg", "60");
    await fill(page, "price", receiptPrice);
    await fill(page, "count", "6");
    await fill(
      page,
      "source_line",
      `Grand 60 kg x ${receiptPrice} TRY, ADET 6`,
    );
    await click(page, "Сохранить черновик чека");
    await waitHash(page, "depot/");
    const settlementId = page.url().split("#depot/")[1];
    const settlement = await read(
      context,
      root + "/harvest/settlements/" + settlementId,
    );
    assert.ok(settlement.issues.includes("count_discrepancy"));
    assert.equal(settlement.reconciliation.field_count, 7);
    assert.equal(settlement.reconciliation.depot_count, 6);
    assert.equal(
      settlement.evidence.raw_text,
      `TEST ${engine} separate owner caption`,
    );
    const originalFile = await context.request.get(
      base + root + "/harvest/evidence/" + settlement.evidence.id,
    );
    assert.deepEqual(await originalFile.body(), originalBytes);
    await snap(page, engine + "-390-depot-mismatch");
    for (const check of await page
      .locator("input[type=checkbox][name^=issue_]")
      .all())
      await check.check();
    await fill(
      page,
      "reason",
      "TEST: sources remain distinct; count and weight mismatch reviewed; unallocated geography remains unknown.",
    );
    await click(page, "Подтвердить чек в TEST");
    await page
      .getByText(
        "Сервер подтвердил документ. Продажа и долг созданы один раз.",
        { exact: true },
      )
      .waitFor();
    await page.reload();
    await page
      .getByText(`TEST ${engine} separate owner caption`, { exact: true })
      .waitFor();
    result.records.reports.push(reportId);
    result.records.settlements.push(settlementId);
    result.journeys.push({
      engine,
      name: "C field original file caption explicit depot link mismatch",
      pass: true,
    });
    // B: each engine exercises actual partial and full settlement for its direction.
    const direction = engine === "chromium" ? "payable" : "receivable",
      party =
        fixture.counterparties[
          direction === "payable" ? "supplier" : "customer"
        ].id;
    await route(page, "finance/" + direction);
    const settleHash = `settle/${direction}/${party}/TRY`;
    await nav(page, settleHash);
    await select(page, "account", fixture.money_accounts[0].id);
    const alloc = page.locator("[name^=allocation_]");
    assert.equal(await alloc.count(), 1);
    await alloc.fill("100.00");
    let paymentLost = false;
    await page.route("**/v1/payments", async (r) => {
      if (r.request().method() === "POST" && !paymentLost) {
        paymentLost = true;
        const response = await r.fetch();
        assert.equal(response.status(), 201);
        await r.abort("failed");
      } else await r.continue();
    });
    await click(page, "Проверить оплату");
    await page
      .locator(".form-error")
      .filter({ hasText: "Ответ сервера не получен" })
      .waitFor();
    await page.locator("[name^=allocation_]").fill("101.00");
    await click(page, "Проверить оплату");
    await page
      .locator(".form-error")
      .filter({ hasText: "Предыдущая отправка" })
      .waitFor();
    await page.locator("[name^=allocation_]").fill("100.00");
    await page.reload();
    await page.locator("[name^=allocation_]").waitFor();
    assert.equal(
      await page.locator("[name^=allocation_]").inputValue(),
      "100.00",
    );
    await click(page, "Проверить оплату");
    await page.unroute("**/v1/payments");
    await waitHash(page, "payment/");
    let paymentId = page.url().split("#payment/")[1];
    const obligation = (
      await read(context, root + "/owner/home")
    ).finance.obligations.find(
      (o) => o.counterparty_id === party && o.direction === direction,
    );
    assert.ok(obligation?.description);
    await page.getByText(obligation.description, { exact: true }).waitFor();
    await page.getByText("Текущий остаток", { exact: true }).waitFor();
    await page
      .getByText("Остаток после оплаты · прогноз", { exact: true })
      .waitFor();
    await snap(page, engine + "-390-payment-review");
    let postLost = false;
    await page.route("**/v1/payments/" + paymentId + "/post?*", async (r) => {
      if (!postLost) {
        postLost = true;
        const response = await r.fetch();
        assert.equal(response.status(), 200);
        await r.abort("failed");
      } else await r.continue();
    });
    await click(page, "Подтвердить оплату в TEST");
    await page
      .locator("#action-error")
      .filter({ hasText: "Ответ сервера не получен" })
      .waitFor();
    await page.reload();
    await page.unroute("**/v1/payments/" + paymentId + "/post?*");
    await page
      .getByText(
        "Сервер подтвердил платёж. Повтор не создаёт вторую проводку.",
        { exact: true },
      )
      .waitFor();
    result.records.payments.push(paymentId);
    await route(page, "sources");
    await page
      .getByRole("button", { name: "Повторить тот же запрос", exact: true })
      .first()
      .click();
    await page
      .getByText("Повтор подтверждён сервером. Проверьте запись.", {
        exact: true,
      })
      .waitFor();
    await route(page, settleHash);
    await select(page, "account", fixture.money_accounts[0].id);
    await click(page, "Весь остаток");
    await click(page, "Проверить оплату");
    await waitHash(page, "payment/");
    paymentId = page.url().split("#payment/")[1];
    await click(page, "Подтвердить оплату в TEST");
    await page
      .getByText(
        "Сервер подтвердил платёж. Повтор не создаёт вторую проводку.",
        { exact: true },
      )
      .waitFor();
    await page.reload();
    await page
      .getByText(
        "Сервер подтвердил платёж. Повтор не создаёт вторую проводку.",
        { exact: true },
      )
      .waitFor();
    result.records.payments.push(paymentId);
    result.journeys.push({
      engine,
      name: `B ${direction} partial and full allocated settlement`,
      pass: true,
    });
    // Navigation race: late detail A must never replace the authority of displayed B.
    await route(page, "tasks");
    const choices = await page
      .locator('a[href^="#personal/"]')
      .evaluateAll((nodes) =>
        nodes.slice(0, 2).map((x) => x.getAttribute("href").slice(1)),
      );
    assert.equal(choices.length, 2);
    let release;
    const held = new Promise((resolve) => {
      release = resolve;
    });
    let intercepted;
    const started = new Promise((resolve) => {
      intercepted = resolve;
    });
    await page.route(
      "**/owner/tasks/" + choices[0].split("/")[1],
      async (r) => {
        const response = await r.fetch();
        intercepted();
        await held;
        await r.fulfill({ response });
      },
    );
    await page.locator(`a[href="#${choices[0]}"]`).click();
    await started;
    await page.goBack();
    await page.locator(`a[href="#${choices[1]}"]`).click();
    await page
      .getByRole("button", { name: "Отметить готовой", exact: true })
      .waitFor();
    release();
    await page.waitForTimeout(100);
    await click(page, "Отметить готовой");
    await page
      .getByRole("button", { name: "Вернуть в активные", exact: true })
      .waitFor();
    assert.equal(
      (await read(context, root + "/owner/tasks/" + choices[0].split("/")[1]))
        .state,
      "TO_DO",
    );
    assert.equal(
      (await read(context, root + "/owner/tasks/" + choices[1].split("/")[1]))
        .state,
      "DONE",
    );
    await click(page, "Вернуть в активные");
    await page
      .getByRole("button", { name: "Отметить готовой", exact: true })
      .waitFor();
    await page.unroute("**/owner/tasks/" + choices[0].split("/")[1]);
    result.journeys.push({
      engine,
      name: "late A detail cannot target visible B action",
      pass: true,
    });
    // Existing Workers R1 original and reviewed assignment; no external dispatch.
    await route(
      page,
      "worker-task/" + fixture.worker_assignment_fixture.task_id,
    );
    await page
      .getByRole("heading", { name: "Исходное поручение", exact: true })
      .waitFor();
    await snap(page, engine + "-390-worker-original");
    if (engine === "chromium") {
      await page.locator("[name=reviewed]").check();
      await click(page, "Сохранить назначение");
      await page
        .getByText(
          "Внутреннее назначение TEST сохранено. Внешней отправки нет.",
          { exact: true },
        )
        .waitFor();
    }
    result.records.workerTasks.push(fixture.worker_assignment_fixture.task_id);
    // F: secondary module HTTP failure is partial data, never authentication loss.
    await page.route("**/inventory", (r) =>
      r.fulfill({
        status: 503,
        contentType: "application/json",
        body: '{"error":{"message":"synthetic secondary outage"}}',
      }),
    );
    await route(page, "home");
    await page.getByText("Добрый день", { exact: true }).waitFor();
    await page.getByText(/Часть разделов не загрузилась/).waitFor();
    assert.equal(await page.locator("#secret").count(), 0);
    await snap(page, engine + "-390-home-partial");
    await page.unroute("**/inventory");
    await route(page, "inventory");
    await page.getByRole("heading", { name: "Склад", exact: true }).waitFor();
    assert.equal(await page.getByText(/Раздел временно недоступен/).count(), 0);
    result.journeys.push({
      engine,
      name: "F secondary outage keeps owner Home authenticated",
      pass: true,
    });
    // Both engines and all affected widths; actual clicks/navigation and actual API.
    for (const width of [320, 390, 700, 768, 820, 900, 1280]) {
      await page.setViewportSize({ width, height: 844 });
      for (const [hash, name] of [
        ["home", "home"],
        ["finance", "finance"],
        ["harvest", "harvest"],
        ["inventory", "inventory"],
        ["tasks", "tasks"],
        ["workers", "workers"],
        ["more", "more"],
      ]) {
        await route(page, hash);
        await layout(page, `${engine}-${width}-${name}`);
        await snap(page, `${engine}-${width}-${name}`);
      }
    }
    await route(page, "capture/text");
    const offlineOriginal = `TEST ${engine} offline source remains original`;
    await fill(page, "text", offlineOriginal);
    await context.setOffline(true);
    await click(page, "Сохранить оригинал");
    await page
      .locator(".form-error")
      .filter({ hasText: "Ответ сервера не получен" })
      .waitFor();
    assert.equal(
      await page
        .getByText("Оригинал сохранён на сервере.", { exact: true })
        .count(),
      0,
    );
    await context.setOffline(false);
    await page.reload();
    assert.equal(
      await page.locator("[name=text]").inputValue(),
      offlineOriginal,
    );
    await context.clearCookies();
    await click(page, "Сохранить оригинал");
    await page.waitForURL("**/r3/?return=*");
    await page.locator("#secret").fill(process.env.PREVIEW_SECRET);
    await click(page, "Войти");
    await page.waitForURL("**/connected.html");
    await route(page, "capture/text");
    assert.equal(
      await page.locator("[name=text]").inputValue(),
      offlineOriginal,
    );
    await click(page, "Сохранить оригинал");
    await waitHash(page, "source/");
    assert.equal(
      (
        await read(
          context,
          root + "/owner/captures/" + page.url().split("#source/")[1],
        )
      ).text,
      offlineOriginal,
    );
    result.journeys.push({
      engine,
      name: "offline persisted original reload session expiry recovery",
      pass: true,
    });
    await context.close();
    await browser.close();
  }
  assert.deepEqual(result.applicationFailures, []);
  result.status = "PASS";
} catch (error) {
  result.status = "FAIL";
  result.errors.push({ message: error.message, stack: error.stack });
  if (activePage && !activePage.isClosed())
    await snap(activePage, "failure").catch(() => {});
  throw error;
} finally {
  await fs.writeFile(
    path.join(out, "browser-results.json"),
    JSON.stringify(result, null, 2),
  );
}
