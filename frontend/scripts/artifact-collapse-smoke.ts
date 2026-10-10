import assert from "node:assert/strict";

interface Locator {
  click(): Promise<void>;
  press(key: string): Promise<void>;
  getAttribute(name: string): Promise<string | null>;
  isVisible(): Promise<boolean>;
  isEnabled(): Promise<boolean>;
  getByRole(role: string, options: { name: string | RegExp; exact?: boolean }): Locator;
  locator(selector: string): Locator;
}

interface BrowserTab {
  playwright: Pick<Locator, "getByRole" | "locator">;
}

// Run against a real, loaded research conversation with a pending coding review.
// This only expands/collapses documents; it never approves or rejects research.
export async function verifyArtifactCollapse(tab: BrowserTab): Promise<void> {
  const workspace = tab.playwright.getByRole("region", { name: "研究工作区", exact: true });
  const stages = [
    { navigation: /^研究/, title: "研究提案" },
    { navigation: /^实验设计/, title: "实验方案" },
    { navigation: /^编码/, title: "代码方案" },
    { navigation: /^执行/, title: "实验记录" },
    { navigation: /^报告/, title: "研究报告" },
  ];

  for (const stage of stages) {
    await workspace.getByRole("button", { name: stage.navigation }).click();
    const document = workspace.getByRole("region", { name: stage.title, exact: true });
    const expand = document.getByRole("button", { name: `展开${stage.title}`, exact: true });
    assert.equal(await expand.getAttribute("aria-expanded"), "false", `${stage.title} starts collapsed`);
    const contentId = await expand.getAttribute("aria-controls");
    assert.ok(contentId, "Disclosure controls a named content region");
    const content = document.locator(`[id="${contentId}"]`);
    assert.equal(await content.isVisible(), false, "Collapsed content is hidden");
    await expand.press("Enter");
    const collapse = document.getByRole("button", { name: `收起${stage.title}`, exact: true });
    assert.equal(await collapse.getAttribute("aria-expanded"), "true");
    assert.equal(await content.isVisible(), true, "Enter expands content");
    await collapse.press("Space");
    assert.equal(await expand.getAttribute("aria-expanded"), "false");
    assert.equal(await content.isVisible(), false, "Space collapses content");
  }

  await workspace.getByRole("button", { name: /^编码/ }).click();
}

// Open the real coding review dialog and wait for its document to load first.
export async function verifyCollapsedReview(tab: BrowserTab): Promise<void> {
  const workspace = tab.playwright.getByRole("region", { name: "研究工作区", exact: true });
  const dialog = tab.playwright.getByRole("dialog", { name: "审核编码方案", exact: true });
  const collapse = dialog.getByRole("button", { name: "收起代码方案", exact: true });
  assert.equal(await collapse.getAttribute("aria-expanded"), "true", "Review opens expanded");
  const approve = dialog.getByRole("button", { name: "批准并继续", exact: true });
  assert.equal(await approve.isEnabled(), true, "Real document has passed validation before this check");
  await collapse.click();
  assert.equal(await approve.isVisible(), true, "Collapse preserves review controls");
  assert.equal(await approve.isEnabled(), true, "Collapse does not change review validity");
  await dialog.getByRole("button", { name: "展开代码方案", exact: true }).click();
  await dialog.getByRole("button", { name: "稍后审核", exact: true }).click();
  assert.equal(await dialog.isVisible(), false);
  assert.equal(await workspace.getByRole("button", { name: "展开代码方案", exact: true }).isVisible(), true);
  assert.equal(await workspace.getByRole("button", { name: "审核方案", exact: true }).isEnabled(), true);
}
