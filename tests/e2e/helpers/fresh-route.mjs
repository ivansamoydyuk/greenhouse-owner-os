// Explicit test setup/refresh navigation. Real link/Back journeys must use
// their actual controls; page.goto with an unchanged URL does not refresh data.
export async function freshRoute(page, base, hash, ready) {
  const url = base + '/r3/connected.html#' + hash;
  if (page.url() === url) await page.reload();
  else await page.goto(url);
  await ready(hash);
}
