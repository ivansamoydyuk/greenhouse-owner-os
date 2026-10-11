import {observeBrowserDiagnostics} from './helpers/browser-diagnostics.mjs';
import {boundedHandshake} from './helpers/bounded-handshake.mjs';
import {observeDiscardedReadTerminal} from './helpers/discarded-read-terminal.mjs';
import {PilotCipher} from '../../src/greenhouse_owner_os/web/pilot-storage.js';
const pilotLocalKeys=new Map();
import {resetFixtureRateLimits,pilotMode,ownerLogin,ownerDraftScope,redactPilot} from './helpers/pilot-auth.mjs';
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
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
  mode: pilotMode?"PRIVATE_DAILY_TEST":"CONNECTED_TEST",
  journeys: [],
  records: {
    expenses: [],
    payments: [],
    reports: [],
    settlements: [],
    settlementEvidence: [],
    receipts: [],
    products: [],
    tasks: [],
    workerTasks: [],
  },
  errors: [],
  screenshots: [],
  homeMetrics: [],
  draftRecovery: [],
  bootstrapRecovery: [],
  captionChecks: [],
  applicationFailures: [],
};
const read = async (ctx, url) => {
  const r = await ctx.request.get(base + url);
  assert.equal(r.status(), 200, await r.text());
  return r.json();
};
let activePage;
const diagnostics=[];
const recoveryProgress=[];
async function snap(page, name) {
  await page.locator("#toast.show").waitFor({ state: "hidden" });
  await page.screenshot({
    path: path.join(out, name + ".png"),
    fullPage: true,
    mask:[page.locator("#username,#password,#secret")],
  });
  result.screenshots.push(name + ".png");
}
async function route(page, hash) {
  await page.goto(base + "/r3/connected.html#" + hash);
  await waitRenderedRoute(page, hash);
}
async function nav(page, hash) {
  await page.locator(`a[href="#${hash}"]`).first().click();
  await waitRenderedRoute(page, hash);
}
async function click(page, text) {
  await page.getByRole("button", { name: text, exact: true }).click();
}
async function fill(page, name, value) {
  const input = page.locator(`[name="${name}"]`);
  await page.waitForFunction((name) => {
    const el = document.querySelector(`[name="${name}"]`);
    return el && !el.closest("[inert]");
  }, name);
  await input.fill(String(value));
  assert.equal(
    await input.inputValue(),
    String(value),
    "entered owner fact: " + name,
  );
}
async function select(page, name, value) {
  await page.locator(`select[name="${name}"]`).selectOption(String(value));
}
async function waitRenderedRoute(page, prefix) {
  await page.waitForFunction((expected) => {
    const hash = location.hash.slice(1) || "home";
    return (
      hash.startsWith(expected) &&
      document.querySelector("#main")?.dataset.renderedRoute === hash
    );
  }, prefix);
}
async function readStoredDraft(page, scope, id = "capture/text") {
  // Read-only observation of durable state, never an injected UI/API fixture.
  let cipher,address=`${scope}:draft:${id}`;
  if(pilotMode){if(!pilotLocalKeys.has(scope)){const response=await page.request.get(base+'/pilot/draft-key');assert.equal(response.status(),200);const k=await response.json();pilotLocalKeys.set(scope,new PilotCipher(k.participant_id,k.key));}cipher=pilotLocalKeys.get(scope);address=await cipher.address(address);}
  const raw=await page.evaluate(
    async ({ address }) => {
      const db = await new Promise((resolve, reject) => {
        const request = indexedDB.open("r3-connected-test-v1", 1);
        request.onsuccess = () => resolve(request.result);
        request.onerror = () => reject(request.error);
      });
      return new Promise((resolve, reject) => {
        const tx = db.transaction("records", "readonly");
        const request = tx.objectStore("records").get(address);
        let record;
        request.onsuccess = () => {
          record = request.result;
        };
        tx.oncomplete = () => {
          db.close();
          resolve(record);
        };
        tx.onerror = () => {
          db.close();
          reject(tx.error);
        };
      });
    },
    { address },
  );
  return raw&&cipher?(await cipher.open(raw)).value:raw;
}
async function waitHash(page, starts) {
  await waitRenderedRoute(page, starts);
}
async function edit(page, action, value, selectValue = false) {
  const oldVersionLabel = await page
    .locator(".page-header .muted")
    .textContent();
  const helperPosts = [];
  const observe = (request) => {
    if (request.method() === "POST") helperPosts.push(request.url());
  };
  page.on("request", observe);
  const editButton = page.locator(`[data-action="edit:${action}"]`);
  assert.equal(await editButton.getAttribute("type"), "button");
  await editButton.click();
  if (selectValue) await select(page, "value", value);
  else await fill(page, "value", value);
  const save = page.getByRole("button", {
    name: "Сохранить факт",
    exact: true,
  });
  await save.scrollIntoViewIfNeeded();
  assert.equal(
    await save.evaluate((el) => {
      const r = el.getBoundingClientRect();
      return el.contains(
        document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2),
      );
    }),
    true,
    "focused fact save must be reachable above the footer",
  );
  assert.deepEqual(
    helperPosts,
    [],
    "opening/editing a fact must not submit it",
  );
  page.off("request", observe);
  await click(page, "Сохранить факт");
  await page.locator(".focus-sheet").waitFor({ state: "hidden" });
  await page.waitForFunction((previous) => {
    const label = document.querySelector(".page-header .muted")?.textContent;
    return (
      label?.startsWith("Версия ") &&
      label !== previous &&
      document.querySelector("#main")?.dataset.renderedRoute ===
        location.hash.slice(1)
    );
  }, oldVersionLabel);
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
    await resetFixtureRateLimits();
    const browser = await browserType.launch();
    const context = await browser.newContext({
      viewport: { width: 390, height: 844 },
      deviceScaleFactor: 1,
    });
    const page = await context.newPage();
    activePage = page;
    const diagnostic=await observeBrowserDiagnostics(page);
    diagnostics.push({engine,observer:diagnostic});
    page.on("pageerror", (error) =>
      result.applicationFailures.push({ engine, message: error.message }),
    );
    await ownerLogin(page,base);
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
    const staleReview = await context.newPage();
    await route(staleReview, "review/" + proposalId);
    await staleReview
      .getByRole("button", { name: "Подтвердить действие в TEST", exact: true })
      .waitFor();
    await page.setViewportSize({ width: 320, height: 568 });
    await edit(page, "amount", "126.00");
    await snap(page, engine + "-320-expense-review-reachable");
    await layout(page, "320-expense-review");
    await page.setViewportSize({ width: 390, height: 844 });
    proposal = await read(
      context,
      root + "/owner/confirmations/" + proposalId + "/preview",
    );
    assert.equal(proposal.version, beforeRevision + 1);
    assert.equal(proposal.source.text, original);
    assert.equal(proposal.revision.payload.amount, "126.00");
    await click(staleReview, "Подтвердить действие в TEST");
    await staleReview
      .getByRole("button", { name: "Обновить текущую запись", exact: true })
      .waitFor();
    const afterStale = await read(
      context,
      root + "/owner/confirmations/" + proposalId + "/preview",
    );
    assert.equal(afterStale.version, proposal.version);
    assert.notEqual(afterStale.state, "POSTED");
    assert.equal(afterStale.source.text, original);
    await click(staleReview, "Обновить текущую запись");
    await staleReview
      .getByText(`Версия ${proposal.version} · CONNECTED TEST`, { exact: true })
      .waitFor();
    await snap(staleReview, engine + "-390-stale-review-refreshed");
    await staleReview.close();
    result.journeys.push({
      engine,
      name: "two-tab stale confirmation rejected before any effect",
      pass: true,
    });
    await page
      .getByText("Общий расход · без указанной теплицы", { exact: true })
      .waitFor();
    assert.equal(
      await page.locator(".compact-expense-review .exact-value").count(),
      1,
    );
    assert.equal(await page.locator('[data-action^="edit:"]').count(), 6);
    assert.equal(
      await page.locator("details.review-provenance").getAttribute("open"),
      null,
    );
    await page.getByText(/Долг и аванс не создаются/).waitFor();
    await snap(page, engine + "-390-expense-review");
    // Drop the response only AFTER the server has committed; retry actual button.
    let lost = false;
    let releaseConfirmResponse;
    const doubleTapFinished = new Promise((resolve) => {
      releaseConfirmResponse = resolve;
    });
    await page.route(
      "**/owner/confirmations/" + proposalId + "/confirm",
      async (intercept) => {
        if (!lost) {
          lost = true;
          const response = await intercept.fetch();
          assert.equal(response.status(), 200);
          await doubleTapFinished;
          await intercept.abort("failed");
        } else await intercept.continue();
      },
    );
    await page
      .getByRole("button", { name: "Подтвердить действие в TEST", exact: true })
      .dblclick();
    releaseConfirmResponse();
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
    // Two actual tabs: a prior same-key DONE replay cannot overwrite a later restore.
    const staleTab = await context.newPage();
    await route(staleTab, "personal/" + taskId);
    await staleTab
      .getByRole("button", { name: "Отметить готовой", exact: true })
      .waitFor();
    await click(page, "Отметить готовой");
    await page
      .getByRole("button", { name: "Вернуть в активные", exact: true })
      .waitFor();
    await click(page, "Вернуть в активные");
    await page
      .getByRole("button", { name: "Отметить готовой", exact: true })
      .waitFor();
    const restoredTask = await read(context, root + "/owner/tasks/" + taskId);
    await click(staleTab, "Отметить готовой");
    await staleTab
      .getByText("Состояние задачи сохранено.", { exact: true })
      .waitFor();
    await staleTab
      .getByRole("button", { name: "Отметить готовой", exact: true })
      .waitFor();
    const replayedTask = await read(context, root + "/owner/tasks/" + taskId);
    assert.equal(replayedTask.version, restoredTask.version);
    assert.equal(replayedTask.state, "TO_DO");
    await snap(staleTab, engine + "-390-old-task-replay-fresh-read");
    await staleTab.close();
    result.journeys.push({
      engine,
      name: "two-tab old DONE replay preserves later restore and reads current state",
      pass: true,
    });
    // D: inline product creation, independently configured warehouse, durable original, movement.
    await route(page, "inventory");
    await nav(page, "quick/receipt");
    const helperPosts = [];
    const observeHelperPost = (request) => {
      if (request.method() === "POST") helperPosts.push(request.url());
    };
    page.on("request", observeHelperPost);
    await click(page, "Создать новый товар");
    const productName = `TEST ${engine} New Product <img src=x onerror=alert(1)>`;
    await fill(page, "name", productName);
    await select(page, "unit", "kg");
    assert.deepEqual(
      helperPosts,
      [],
      "opening inline product must not submit the receipt",
    );
    assert.equal(
      await page.locator('[data-form="receipt"] .form-error').textContent(),
      "",
    );
    page.off("request", observeHelperPost);
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
    if (engine === "webkit") {
      // This second synthetic delivery intentionally has the same date/line as Chromium.
      // Existing backend duplicate review must be exercised, never silently bypassed.
      await page.locator("#duplicate-reviewed").waitFor();
      assert.equal(await page.locator('[data-action="post-receipt"]').isDisabled(), true);
      await page.locator("#duplicate-reviewed").check();
      assert.equal(await page.locator('[data-action="post-receipt"]').isDisabled(), false);
    } else {
      assert.equal(await page.locator("#duplicate-reviewed").count(), 0);
      assert.equal(await page.locator('[data-action="post-receipt"]').isDisabled(), false);
    }
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
    if (pilotMode) {
      // Private pilot intentionally cannot enter the legacy root application.
      await page.goto(base + '/');
      await page.waitForURL('**/pilot/');
      await page.locator('#pilot-login').waitFor();
      assert.equal(await page.locator('#app,#sheet,#sheet-title').count(), 0);
      assert.equal(await page.getByRole('heading', {name:'Моя теплица',exact:true}).count(), 0);
      const dialogs=[];
      const onDialog=async dialog=>{dialogs.push(dialog.type());await dialog.dismiss();};
      page.on('dialog',onDialog);
      await route(page,'inventory');
      const maliciousRow=page.locator('.list-row').filter({has:page.locator(`a[href="#quick/receipt/${product.id}"]`)});
      assert.equal(await maliciousRow.locator('strong').first().textContent(),productName);
      assert.equal(await maliciousRow.locator('img').count(),0);
      const ordinaryRow=page.locator('.list-row').filter({has:page.locator(`a[href="#quick/receipt/${fixture.product.id}"]`)});
      assert.equal(await ordinaryRow.locator('strong').first().textContent(),fixture.product.name);
      assert.equal(await ordinaryRow.locator('img').count(),0);
      const csp=(await context.request.get(base+'/r3/connected.html')).headers()['content-security-policy'];
      assert.ok(csp.includes("script-src 'self'"));
      const scriptPolicy=csp.split(';').find(part=>part.trim().startsWith('script-src '));
      assert.ok(!scriptPolicy.includes("'unsafe-inline'")&&!scriptPolicy.includes("'unsafe-eval'"));
      await snap(page,engine+'-390-private-literal-product');
      assert.deepEqual(dialogs,[]);
      page.off('dialog',onDialog);
      result.journeys.push({engine,name:'AUD011 private legacy boundary and R3 literal product CSP',pass:true});
    } else {
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
    }
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
    const captionCheck = {
      engine,
      formInertAfterFile: await page
        .locator("[data-form=depot]")
        .evaluate((el) => el.inert),
    };
    result.captionChecks.push(captionCheck);
    await page.waitForFunction(() => {
      const form = document.querySelector("[data-form=depot]");
      return (
        form && !form.inert && !form.querySelector("[type=submit]").disabled
      );
    });
    await fill(page, "caption", `TEST ${engine} separate owner caption`);
    await select(page, "depot", fixture.counterparties.depot.id);
    await fill(
      page,
      "link_evidence",
      `TEST ${engine} explicitly the selected cut`,
    );
    await fill(page, "quality", "Grand");
    await select(page, "quality_category", "HIGHEST");
    await fill(page, "kg", "60");
    await fill(page, "price", receiptPrice);
    await fill(page, "count", "6");
    await page.getByText("Дословная строка документа", {exact:true}).click();
    await fill(
      page,
      "source_line",
      `Grand 60 kg x ${receiptPrice} TRY, ADET 6`,
    );
    captionCheck.domCaption = await page.locator("[name=caption]").inputValue();
    captionCheck.storedCaption = (
      await readStoredDraft(
        page,
        ownerDraftScope(config),
        "attach-depot/" + reportId,
      )
    ).value.caption;
    assert.equal(
      captionCheck.domCaption,
      `TEST ${engine} separate owner caption`,
    );
    assert.equal(captionCheck.storedCaption, captionCheck.domCaption);
    const captureRequest=page.waitForRequest(request=>request.method()==='POST' && request.url()===base+root+'/owner/captures');
    await click(page,'Открыть короткую проверку');
    captionCheck.requestCaption=(await captureRequest).postDataJSON().text;
    assert.equal(captionCheck.requestCaption,captionCheck.domCaption);
    await waitHash(page,'review/');
    const depotProposalId=page.url().split('#review/')[1];
    let depotProposal=await read(context,root+'/owner/confirmations/'+depotProposalId+'/preview');
    assert.equal(depotProposal.state,'NEEDS_INPUT');
    assert.equal(depotProposal.revision.basis.arithmetic.field_count,7);
    assert.equal(depotProposal.revision.basis.arithmetic.depot_count,6);
    assert.equal(depotProposal.revision.basis.arithmetic.count_discrepancy,true);
    assert.equal(depotProposal.revision.payload.arithmetic_reviewed,false);
    assert.equal(await page.locator('[data-action=confirm-proposal]').isDisabled(),true);
    await page.getByText('Расхождение: 7 веток по полю и 6 ADET по документу. Ни одно значение не заменяется.',{exact:true}).waitFor();
    await snap(page,engine+'-390-depot-mismatch');
    await page.setViewportSize({width:320,height:844});
    await layout(page,'depot-review-320');await snap(page,engine+'-320-depot-mismatch');
    await page.setViewportSize({width:390,height:844});
    await click(page,'Арифметика и расхождения проверены');
    await page.getByText('Арифметика проверена владельцем',{exact:true}).waitFor();
    assert.equal(await page.locator('[data-action=confirm-proposal]').isDisabled(),false);
    await click(page,'Подтвердить действие в TEST');
    await waitHash(page,'depot/');
    const settlementId=page.url().split('#depot/')[1];
    const settlement=await read(context,root+'/harvest/settlements/'+settlementId);
    assert.equal(settlement.status,'posted');assert.ok(settlement.issues.includes('count_discrepancy'));
    assert.equal(settlement.reconciliation.field_count,7);assert.equal(settlement.reconciliation.depot_count,6);
    assert.equal(settlement.evidence.raw_text,`TEST ${engine} separate owner caption`);
    assert.equal(settlement.lines[0].normalized_quality,'HIGHEST');
    const originalFile=await context.request.get(base+root+'/owner/captures/'+depotProposal.source.id+'/original');
    assert.deepEqual(await originalFile.body(),originalBytes);
    await page.getByText('Сервер подтвердил документ. Продажа и долг созданы один раз.',{exact:true}).waitFor();
    await page.getByText('Средний вес по ADET, кг/ADET',{exact:true}).waitFor();
    await page.reload();await page.getByText(`TEST ${engine} separate owner caption`,{exact:true}).waitFor();
    result.records.reports.push(reportId);
    result.records.settlements.push(settlementId);
    result.records.settlementEvidence.push({
      id: settlementId,
      owner_source_id: depotProposal.source.id,
      proposal_id: depotProposalId,
      report_id: reportId,
      original_sha256: createHash("sha256").update(originalBytes).digest("hex"),
      original_bytes: originalBytes.length,
      filename: `TEST-${engine}-receipt.${engine === "webkit" ? "pdf" : "png"}`,
      mime_type: engine === "webkit" ? "application/pdf" : "image/png",
      caption: `TEST ${engine} separate owner caption`,
      field_original: `TEST ${engine} field original seven cuts`,
      unit_price: receiptPrice,
    });
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
        diagnostic.checkpoint('payment-loss-handler');
        postLost = true;
        const response = await r.fetch();
        assert.equal(response.status(), 200);
        diagnostic.checkpoint('payment-post-ack');
        await r.abort("failed");
        diagnostic.checkpoint('payment-post-aborted');
      } else await r.continue();
    });
    await click(page, "Подтвердить оплату в TEST");
    await page
      .locator("#action-error")
      .filter({ hasText: "Ответ сервера не получен" })
      .waitFor();
    diagnostic.checkpoint('payment-reload-start');
    await page.reload();
    diagnostic.checkpoint('payment-reload-return');
    await page.unroute("**/v1/payments/" + paymentId + "/post?*");
    diagnostic.checkpoint('payment-unroute-return');
    await page
      .getByText(
        "Сервер подтвердил платёж. Повтор не создаёт вторую проводку.",
        { exact: true },
      )
      .waitFor();
    // A stalled read after reload must reach a usable, fail-closed recovery
    // boundary. This is separate from (and retains) the lost-post assertion above.
    const paymentHash = page.url().split('#')[1];
    const handshake = boundedHandshake({checkpoint:async entry => {
      recoveryProgress.push({engine,...entry,atMs:Date.now()});
      if(recoveryProgress.length>80)recoveryProgress.shift();
      await fs.writeFile(path.join(out,'bootstrap-recovery-progress.json'),JSON.stringify({sha:result.sha,mode:result.mode,entries:recoveryProgress},null,2));
    }});
    const rawRecords = () => page.evaluate(async () => {
      const {BrowserStore} = await import('/r3/connected-adapter.js');
      return (await new BrowserStore().all()).sort((a,b) => a.key.localeCompare(b.key));
    });
    const beforeBootstrapRecovery = await handshake('records-before',rawRecords);
    const bootstrapPosts = [];
    const observeBootstrapPost = request => { if(request.method()==='POST')bootstrapPosts.push(new URL(request.url()).pathname); };
    page.on('request', observeBootstrapPost);
    let releaseWarmup, enterWarmup, handshakeError, heldRequest;
    const warmupHeld = new Promise(resolve => releaseWarmup = resolve);
    const warmupEntered = new Promise(resolve => enterWarmup = resolve);
    const warmupPath = root + '/owner/captures';
    const warmupMatcher = url => url.pathname === warmupPath;
    try {
      await handshake('install-held-route',()=>page.route(warmupMatcher, async intercepted => {
        heldRequest = intercepted.request();
        enterWarmup();
        await warmupHeld;
        await intercepted.continue();
      }));
      await handshake('reload-held',()=>page.reload());
      await handshake('warmup-entered',()=>warmupEntered);
      await handshake('recovery-visible',()=>page.getByRole('button', {name:'Повторить загрузку страницы', exact:true}).waitFor());
      await handshake('recovery-evidence',async()=>{
        assert.equal(await page.locator('#app').getAttribute('data-bootstrap-stage'),'recovery');
        assert.equal(await page.locator('form,input,textarea,select,.dock').count(),0);
        assert.equal(page.url().split('#')[1],paymentHash);
        await snap(page, engine + '-390-bootstrap-timeout-recovery');
      });
      assert.equal(new URL(heldRequest.url()).pathname,warmupPath);
      const terminalObserver = observeDiscardedReadTerminal(page,heldRequest);
      let lateRead;
      try {
        lateRead = await handshake('late-response-terminal',()=>{
          releaseWarmup();
          return terminalObserver.promise;
        });
      } finally { terminalObserver.dispose(); }
      await handshake('remove-held-route',()=>page.unroute(warmupMatcher));
      await handshake('late-records',async()=>{
        assert.equal(await page.locator('#app').getAttribute('data-bootstrap-stage'),'recovery');
        assert.deepEqual(await rawRecords(),beforeBootstrapRecovery);
      });
      await handshake('manual-reload',()=>click(page,'Повторить загрузку страницы'));
      await handshake('posted-confirmation',()=>page.getByText('Сервер подтвердил платёж. Повтор не создаёт вторую проводку.',{exact:true}).waitFor());
      assert.equal(page.url().split('#')[1],paymentHash);
      assert.deepEqual(await handshake('records-after',rawRecords),beforeBootstrapRecovery);
      assert.deepEqual(bootstrapPosts,[]);
      result.bootstrapRecovery.push({engine,hash:paymentHash,lateRead,unchangedRecordsSha256:createHash('sha256').update(JSON.stringify(beforeBootstrapRecovery)).digest('hex'),businessPosts:0,pass:true});
      result.journeys.push({engine,name:'bounded cold-bootstrap read timeout manual reload preserves original pending commands',pass:true});
    } catch(error) {
      handshakeError=error;
      throw error;
    } finally {
      releaseWarmup();
      page.off('request',observeBootstrapPost);
      try { await handshake('cleanup-route',()=>page.unroute(warmupMatcher)); }
      catch(error) { if(!handshakeError)throw error; }
    }
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
    helperPosts.length = 0;
    page.on("request", observeHelperPost);
    await click(page, "Весь остаток");
    const beforeExplicitReview = await read(context, root + "/owner/home");
    assert.deepEqual(
      helperPosts,
      [],
      "fill-allocation must not create any payment",
    );
    assert.equal(new URL(page.url()).hash, "#" + settleHash);
    assert.equal(
      beforeExplicitReview.finance.obligations.find(
        (o) => o.obligation_id === obligation.obligation_id,
      ).remaining_amount,
      direction === "payable" ? "1100.00" : "700.00",
    );
    page.off("request", observeHelperPost);
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
    let completed;
    const lateResponseCompleted = new Promise((resolve) => {
      completed = resolve;
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
        completed();
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
    await lateResponseCompleted;
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
    // F: a real cold read and explicit warm refresh see the injected secondary outage.
    let outageReads = 0;
    await page.route("**/inventory", (r) => {
      outageReads++;
      return r.fulfill({
        status: 503,
        contentType: "application/json",
        body: '{"error":{"message":"synthetic secondary outage"}}',
      });
    });
    await route(page, "home");
    await page.reload();
    await waitRenderedRoute(page, "home");
    await page.getByText("Добрый день", { exact: true }).waitFor();
    const actualHome = await read(context, root + "/owner/home");
    assert.ok(actualHome.season[0].average_price != null);
    const averagePriceCard = page
      .locator(".harvest-stat-grid > div")
      .filter({ has: page.getByText("Средняя цена", { exact: true }) });
    const displayedAveragePrice = await averagePriceCard
      .locator("strong")
      .textContent();
    result.homeMetrics.push({
      engine,
      season: actualHome.season,
      displayedAveragePrice,
    });
    assert.ok(!displayedAveragePrice.includes("UNKNOWN"));
    if (engine === "chromium")
      assert.equal(displayedAveragePrice, "10,00 TRY/кг");
    await page.getByText(/Часть разделов не загрузилась/).waitFor();
    assert.ok(
      outageReads >= 1,
      "cold reload must actually observe injected inventory503",
    );
    assert.equal(await page.locator("#secret").count(), 0);
    const warmFailure = page.waitForResponse(
      (r) => r.url().endsWith("/inventory") && r.status() === 503,
    );
    await click(page, "Повторить загрузку разделов");
    await warmFailure;
    assert.ok(outageReads >= 2);
    await page.getByText(/Часть разделов не загрузилась/).waitFor();
    await snap(page, engine + "-390-home-partial");
    await page.unroute("**/inventory");
    const recoveredRead = page.waitForResponse(
      (r) => r.url().endsWith("/inventory") && r.status() === 200,
    );
    await click(page, "Повторить загрузку разделов");
    await recoveredRead;
    await page
      .getByText(/Часть разделов не загрузилась/)
      .waitFor({ state: "hidden" });
    await route(page, "inventory");
    await page.getByRole("heading", { name: "Склад", exact: true }).waitFor();
    assert.equal(await page.getByText(/Раздел временно недоступен/).count(), 0);
    result.journeys.push({
      engine,
      name: "F cold and warm secondary outage keeps Home authenticated and explicit retry recovers",
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
    await waitRenderedRoute(page, "capture/text");
    assert.equal(
      await page.locator("[name=text]").inputValue(),
      offlineOriginal,
    );
    const ownerScope = ownerDraftScope(config);
    const beforeExpiry = await readStoredDraft(page, ownerScope);
    assert.equal(beforeExpiry.value.text, offlineOriginal);
    await context.clearCookies();
    await click(page, "Сохранить оригинал");
    await page.waitForURL(pilotMode?"**/pilot/?return=*":"**/r3/?return=*");
    const afterExpiry = await readStoredDraft(page, ownerScope);
    assert.deepEqual(afterExpiry, beforeExpiry);
    let releaseConfig, observeConfig;
    const configHeld = new Promise((resolve) => {
      releaseConfig = resolve;
    });
    const configRequested = new Promise((resolve) => {
      observeConfig = resolve;
    });
    await page.route("**/preview/config", async (interception) => {
      observeConfig();
      await configHeld;
      await interception.continue();
    });
    await ownerLogin(page,base,{navigate:false});
    await configRequested;
    await page.goto(base + "/r3/connected.html#capture/text");
    await page.getByText("Проверяем TEST-сессию", { exact: true }).waitFor();
    assert.equal(
      await page.locator("[data-form=capture]").count(),
      0,
      "an editable draft must not render before authenticated identity",
    );
    const duringBootstrap = await readStoredDraft(page, ownerScope);
    assert.deepEqual(duringBootstrap, beforeExpiry);
    releaseConfig();
    await waitRenderedRoute(page, "capture/text");
    await page.unroute("**/preview/config");
    assert.equal(
      await page.locator("[name=text]").inputValue(),
      offlineOriginal,
    );
    const afterLogin = await readStoredDraft(page, ownerScope);
    assert.deepEqual(afterLogin, beforeExpiry);
    result.draftRecovery.push({
      engine,
      scope: ownerScope,
      beforeExpiry,
      afterExpiry,
      duringBootstrap,
      afterLogin,
      pass: true,
    });
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
    diagnostics.at(-1).completed=await diagnostic.capture();
    await context.close();
    await browser.close();
  }
  assert.deepEqual(result.applicationFailures, []);
  result.status = "PASS";
} catch (error) {
  result.status = "FAIL";
  result.errors.push({ message: redactPilot(error.message), stack: redactPilot(error.stack) });
  if (activePage && !activePage.isClosed())
    await snap(activePage, "failure").catch(() => {});
  throw Error(redactPilot(error.stack || error));
} finally {
  await fs.writeFile(
    path.join(out, "browser-results.json"),
    JSON.stringify(result, null, 2),
  );
  // Optional diagnostics must not replace the primary result or its failure.
  try {
    const metadata=await Promise.all(diagnostics.map(async({engine,observer,completed})=>({engine,...(completed||await observer.capture())})));
    await fs.writeFile(path.join(out,"browser-diagnostics.json"),JSON.stringify({sha:result.sha,engines:metadata},null,2));
  } catch {}
}
