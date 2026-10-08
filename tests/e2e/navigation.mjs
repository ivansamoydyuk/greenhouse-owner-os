// Exercise the visible owner information architecture. Never force-click a
// hidden legacy tab or write the route directly to skip an actual user action.
import assert from 'node:assert/strict';

export const ownerRoutes = Object.freeze({
  home: {primary: 'home', heading: 'Моя теплица'},
  review: {primary: 'review', heading: 'Проверка'},
  overview: {primary: 'overview', heading: 'Финансы'},
  debts: {primary: 'overview', heading: 'Долги и авансы'},
  operations: {primary: 'overview', heading: 'Операции'},
  more: {primary: 'more', heading: 'Ещё'},
  harvest: {primary: 'more', heading: 'Урожай / Депо'},
  inventory: {primary: 'more', heading: 'Удобрения / Запасы'},
  tasks: {primary: 'more', heading: 'Работники / Задачи'},
  inbox: {primary: 'more', heading: 'Входящие'},
  parties: {primary: 'more', heading: 'Контрагенты'},
});

export async function waitOwnerRoute(page, route, {dialog = route === 'review'} = {}) {
  assert.ok(ownerRoutes[route], `Unknown owner route: ${route}`);
  await page.waitForURL(url => url.hash === `#${route}`);
  await page.waitForFunction(() => document.querySelector('#app')?.getAttribute('aria-busy') === 'false');
  // A primary Review click deliberately opens the queue over the review landing.
  await page.locator('#app').getByRole('heading', {name: ownerRoutes[route].heading, exact: true}).waitFor();
  if (dialog) await page.locator('#sheet').waitFor({state: 'visible'});
}

export async function navigateOwner(page, route) {
  const entry = ownerRoutes[route];
  assert.ok(entry, `Unknown owner route: ${route}`);
  await page.locator(`#nav [data-page="${entry.primary}"]`).click();
  await waitOwnerRoute(page, entry.primary);
  if (route !== entry.primary) {
    await page.locator(`#app [data-go="${route}"]`).first().click();
    await waitOwnerRoute(page, route);
  }
}

// The legacy manual flow is intentionally available under progressive
// disclosure. Open its real disclosure before using it; do not force-click.
export async function revealOwnerControl(control) {
  // A refreshed module may close its disclosures again. Open every ancestor,
  // outermost first, using the same visible summaries as the owner.
  const ancestors = control.locator('xpath=ancestor::details');
  for (let index = 0; index < await ancestors.count(); index++) {
    const disclosure = ancestors.nth(index);
    if (!await disclosure.evaluate(node => node.open)) {
      await disclosure.locator(':scope > summary').click();
    }
  }
  return control;
}

export async function openManualCapture(page) {
  const trigger = await revealOwnerControl(page.locator('#global-capture'));
  await trigger.click();
}
