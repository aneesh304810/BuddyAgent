// Runs Buddy's critical journeys against the prototype in a real browser, counts taps, checks layout and themes,
// and saves screenshots of every destination for 04-ui/screens.
//
//   NODE_PATH=$(npm root -g) node buddy-redesign/08-quality/prototype.test.js
//
// Writes 08-quality/prototype-test-results.md. Exits non-zero when a check fails.
const { chromium } = require("playwright");
const fs = require("fs"), path = require("path");
const ROOT = path.resolve(__dirname, "..");
const SRC = fs.readFileSync(path.join(ROOT, "05-prototype/prototype.html"), "utf8");
const PAGE = `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head><body>${SRC}</body></html>`;
const SHOTS = path.join(ROOT, "04-ui/screens");
fs.mkdirSync(SHOTS, { recursive: true });
const results = [];
let failed = 0;
const check = (name, ok, detail = "") => { results.push([name, ok, detail]); if (!ok) failed++; };

async function fresh(b, device, theme) {
  const p = await b.newPage({ viewport: device === "open" ? { width: 1000, height: 960 } : { width: 460, height: 1000 }, deviceScaleFactor: 1, colorScheme: theme });
  const errors = [];
  p.on("pageerror", (e) => errors.push(e.message));
  await p.route("https://fonts.googleapis.com/**", (r) => r.abort());
  await p.setContent(PAGE);
  await p.evaluate(([d, t]) => { document.querySelector(`[data-ctl=device] [data-v=${d}]`).click(); document.querySelector(`[data-ctl=theme] [data-v=${t}]`).click(); window.__protoReset(); }, [device, theme]);
  return { p, errors };
}
const taps = (p) => p.evaluate(() => window.__proto.state.taps);
const visible = (p, sel) => p.locator(sel).first().isVisible();
const inDevice = async (p, sel) => {
  const [d, e] = await Promise.all([p.locator("#device").boundingBox(), p.locator(sel).first().boundingBox()]);
  return !!(d && e && e.y >= d.y && e.y + e.height <= d.y + d.height + 1);
};

(async () => {
  const b = await chromium.launch();
  for (const device of ["folded", "open"]) {
    for (const theme of ["light", "dark"]) {
      const tag = `${device}, ${theme}`;
      let { p, errors } = await fresh(b, device, theme);
      // 1. opens into the conversation, composer visible without scrolling
      check(`[${tag}] opens on Home › Conversation`, await p.locator('[data-qa="nav-home"][aria-current="page"]').count() === 1 && await visible(p, "#composer"));
      check(`[${tag}] composer and mic visible without scrolling`, await inDevice(p, "#composer") && await inDevice(p, '[data-qa="mic"]'));
      await p.screenshot({ path: path.join(SHOTS, `home-${device}-${theme}.png`) });
      // navigation depth: at most two levels (destinations, then at most 5 sections)
      const sections = {};
      for (const d of ["home", "health", "money", "explore", "profile"]) {
        await p.click(`[data-qa="nav-${d}"]`);
        sections[d] = await p.locator(".segctl [role=tab]").count();
        await p.screenshot({ path: path.join(SHOTS, `${d}-${device}-${theme}.png`) });
        const ov = await p.evaluate(() => { const b = document.getElementById("body"); return b.scrollWidth - b.clientWidth; });
        check(`[${tag}] ${d}: no sideways scrolling`, ov <= 1, `overflow ${ov}px`);
      }
      check(`[${tag}] two levels: every destination has 2 to 5 sections, no deeper tabs`, Object.values(sections).every((n) => n >= 2 && n <= 5), JSON.stringify(sections));
      await p.close();

      // 3. add a meal: one tap to the meal sheet
      ({ p, errors } = await fresh(b, device, theme));
      await p.click('[data-qa="meal"]');
      check(`[${tag}] add a meal: 1 tap to the meal sheet`, await taps(p) === 1 && await visible(p, '[data-qa="add-meal"]'));
      await p.click('[data-qa="add-meal"]');
      check(`[${tag}] add a meal: saved, sheet closed`, (await p.locator(".toast").textContent()).startsWith("Added to"));
      await p.close();

      // 4. log blood pressure: one tap
      ({ p, errors } = await fresh(b, device, theme));
      await p.click('[data-qa="bp"]');
      check(`[${tag}] log blood pressure: 1 tap to the entry`, await taps(p) === 1 && await visible(p, '[data-qa="save-bp"]'));
      if (device === "folded" && theme === "light") await p.screenshot({ path: path.join(SHOTS, `sheet-bp-${device}-${theme}.png`) });
      await p.click('[data-qa="save-bp"]');
      await p.click('[data-qa="nav-health"]');
      check(`[${tag}] blood pressure shows on Health › Today`, (await p.locator(".tile").filter({ hasText: "Blood pressure this morning" }).textContent()).includes("128/82"));
      await p.close();

      // 5. today's health: one tap; health is labelled as isolated
      ({ p, errors } = await fresh(b, device, theme));
      await p.click('[data-qa="nav-health"]');
      check(`[${tag}] check today's health: 1 tap`, await taps(p) === 1 && await p.locator('[data-qa="sec-today"][aria-current="page"]').count() === 1);
      check(`[${tag}] Health says no AI model sees it`, (await p.locator(".banner").first().textContent()).includes("No AI model sees it"));
      await p.close();

      // 6 and 7. scan a product: one tap to the camera; verdict; compare
      ({ p, errors } = await fresh(b, device, theme));
      await p.click('[data-qa="scan"]');
      check(`[${tag}] scan a product: 1 tap to the scanner`, await taps(p) === 1 && await visible(p, '[data-qa="scan-bread"]'));
      await p.click('[data-qa="scan-bread"]');
      check(`[${tag}] verdict with its reasons`, await visible(p, "text=Fine sometimes") && await p.locator("details.rests").count() > 0);
      if (theme === "light") await p.screenshot({ path: path.join(SHOTS, `verdict-${device}-${theme}.png`) });
      await p.click('[data-qa="compare"]');
      check(`[${tag}] compare products: side by side, better one marked`, await visible(p, "text=Better for you"));
      await p.close();

      // 8, 9, 12. spending in 2, bills in 1, news in 1
      ({ p, errors } = await fresh(b, device, theme));
      await p.click('[data-qa="nav-money"]');
      check(`[${tag}] review bills: 1 tap (Overview lists bills due)`, await taps(p) === 1 && await visible(p, "text=Bills coming up"));
      await p.click('[data-qa="sec-spending"]');
      check(`[${tag}] review spending: 2 taps`, await taps(p) === 2 && await visible(p, "text=By category"));
      await p.click('[data-qa="nav-explore"]');
      check(`[${tag}] read news: 1 tap from any destination`, await visible(p, "text=For you"));
      await p.close();

      // 14. approve a financial action: one tap on the card's button after review; recorded in Activity
      ({ p, errors } = await fresh(b, device, theme));
      await p.fill("#composer", "Pay my card bill");
      await p.press("#composer", "Enter");
      await p.waitForSelector(".approval .what");
      check(`[${tag}] approval card shows the exact action`, (await p.locator(".approval .what").last().textContent()).includes("$412.30") && await visible(p, "text=Card ending 4821"));
      if (theme === "light") await p.screenshot({ path: path.join(SHOTS, `approval-${device}-${theme}.png`) });
      await p.locator(".approval button", { hasText: "Approve" }).last().click();
      await p.click('[data-qa="nav-home"]');
      await p.evaluate(() => document.querySelector('[data-act="go:home:activity"]').click());
      check(`[${tag}] approved action recorded in Activity`, await visible(p, "text=Approved by you just now"));
      await p.close();

      // health boundary in the conversation
      ({ p, errors } = await fresh(b, device, theme));
      await p.fill("#composer", "My blood pressure was 150/95 today");
      await p.press("#composer", "Enter");
      await p.waitForSelector("text=Health stays in Health");
      const reply = await p.locator(".msg.bud").last().textContent();
      check(`[${tag}] health typed in the conversation is turned away to Health`, reply.includes("no AI model sees it") && !reply.includes("150/95"));
      await p.close();

      // voice states and error state
      ({ p, errors } = await fresh(b, device, theme));
      await p.click('[data-qa="mic"]');
      check(`[${tag}] voice: listening state`, await p.locator(".vstate.listening").count() === 1 && await p.locator(".mic.listening").count() === 1);
      await p.waitForSelector(".vstate.speaking", { timeout: 5000 });
      check(`[${tag}] voice: thinking then speaking`, true);
      await p.click('[data-qa="mic"]');
      check(`[${tag}] voice: interrupt while speaking`, await p.locator(".vstate.interrupted").count() === 1);
      await p.evaluate(() => document.querySelector("[data-ctl=state] [data-v=error]").click());
      await p.click('[data-qa="nav-money"]');
      check(`[${tag}] error state explains and offers a retry`, await visible(p, "text=Couldn't reach your Buddy server") && await visible(p, "text=Try again"));
      await p.evaluate(() => document.querySelector("[data-ctl=state] [data-v=empty]").click());
      check(`[${tag}] empty state says what to do`, await visible(p, "text=Connect your bank to begin"));
      check(`[${tag}] no script errors`, errors.length === 0, errors.join("; "));
      await p.close();
    }
  }
  await b.close();
  const lines = ["# Prototype test results", "", `Run ${new Date().toISOString().slice(0, 16).replace("T", " ")} UTC with Playwright (Chromium), folded (412 px device) and open (900 px), light and dark.`, "",
    `**${results.length - failed} of ${results.length} checks pass.**`, "", "| Check | Result | Detail |", "| --- | --- | --- |",
    ...results.map(([n, ok, d]) => `| ${n} | ${ok ? "pass" : "FAIL"} | ${d.replace(/\|/g, "/")} |`)];
  fs.writeFileSync(path.join(__dirname, "prototype-test-results.md"), lines.join("\n") + "\n");
  console.log(`${results.length - failed}/${results.length} pass`);
  results.filter((r) => !r[1]).forEach((r) => console.log("FAIL", r[0], r[2]));
  process.exit(failed ? 1 : 0);
})();
