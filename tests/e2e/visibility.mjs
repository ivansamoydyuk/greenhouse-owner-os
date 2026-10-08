// Self-contained so Playwright can install this same predicate in the page.
// Chromium may retain layout rectangles for descendants of a closed <details>;
// those descendants are not visible or accessible until its summary is opened.
export function isVisibleOwnerControl(element) {
  if (!element?.getClientRects().length || getComputedStyle(element).visibility === 'hidden') return false;
  for (let ancestor = element.parentElement; ancestor; ancestor = ancestor.parentElement) {
    if (ancestor.tagName !== 'DETAILS' || ancestor.open) continue;
    const summary = [...ancestor.children].find(child => child.tagName === 'SUMMARY');
    if (!summary?.contains(element)) return false;
  }
  return true;
}
