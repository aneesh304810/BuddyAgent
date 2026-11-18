import test from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { CLASSES, memoryHost, runApp } from "../src/index.js";
import { claudeFile, steps, templates, templatesFile, tutorialPage } from "../tools/site.mjs";
import { REFERENCE, prompt } from "../studio/reference.js";

const LIB = pathToFileURL(fileURLToPath(new URL("../src/index.js", import.meta.url))).href;

test("every starting point in the Studio is a complete app that runs as it is printed", async () => {
  const dir = mkdtempSync(join(tmpdir(), "buddyxr-"));
  try {
    for (const t of templates()) {
      assert.ok(t.code.includes('from "buddy-xr"') && !t.code.includes("../"), t.id);     // nothing but the framework is imported
      const file = join(dir, t.id + ".mjs");
      writeFileSync(file, t.code.replaceAll('"buddy-xr"', JSON.stringify(LIB)));
      const app = (await import(pathToFileURL(file).href)).default;
      for (const kind of ["text", "audio"]) {
        const host = memoryHost(CLASSES[kind]);
        const run = runApp(app, host);
        assert.ok(kind === "text" ? host.text.length > 5 : host.spoken.length === 1, `${t.id} on ${kind}`);
        if (t.events) run.notify(JSON.parse(t.events));
        run.stop();
      }
    }
  } finally { rmSync(dir, { recursive: true, force: true }); }
});

test("each tutorial step stands alone: it brings the lists it uses and imports only the blocks it uses", () => {
  const all = steps();
  assert.deepEqual(all.map((s) => s.step), [1, 2, 3, 4, 5]);
  assert.equal(all[0].code.split("\n")[0], 'import { Screen, Text, defineApp } from "buddy-xr";');
  assert.ok(all[2].code.includes('const PARCELS = ["12 Mill Lane", "4 Station Road", "The Old Bakery"];'));   // borrowed from step 2
  assert.ok(all[4].code.includes("const GATE_CODES =") && all[4].code.includes("const PARCELS ="));
  for (const s of all) assert.ok(s.code.includes("export default defineApp(") && !/step\d/.test(s.code), "step " + s.step);
});

test("the Studio's file of starting points is up to date, and the tutorial page prints the same code", () => {
  const onDisk = readFileSync(new URL("../studio/templates.js", import.meta.url), "utf8");
  assert.equal(onDisk, templatesFile(), "run: node tools/site.mjs");
  const page = tutorialPage();
  for (const s of steps()) assert.ok(page.includes(s.code.trim().replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")), "step " + s.step);
  assert.ok(!/waiter|kitchen|allerg|restaurant/i.test(page));          // the restaurant product is not in the open tutorial
});

test("the instructions for an assistant carry the reference and the person's own words, and the example in them runs", async () => {
  const text = prompt("  a warehouse picking list:\n show the next item and its shelf ");
  assert.ok(text.startsWith("# Writing a Buddy XR app") && text.endsWith("The app: a warehouse picking list: show the next item and its shelf\n"));
  assert.throws(() => prompt("an app"), /Say in a sentence or two/);
  for (const must of ['privacy: "personal"', 'privacy: "health"', 'Import only from "buddy-xr"', "export default defineApp"]) assert.ok(REFERENCE.includes(must), must);

  const example = REFERENCE.match(/```js\n([\s\S]*?)```/)[1];
  const dir = mkdtempSync(join(tmpdir(), "buddyxr-"));
  try {
    const file = join(dir, "example.mjs");
    writeFileSync(file, example.replaceAll('"buddy-xr"', JSON.stringify(LIB)));
    const app = (await import(pathToFileURL(file).href)).default;
    const host = memoryHost(CLASSES.text);
    runApp(app, host);
    host.input({ type: "double" });
    assert.equal(host.text, "My app\nChosen 1 times.\n› Choose me\n  Or me");
  } finally { rmSync(dir, { recursive: true, force: true }); }
  assert.equal(readFileSync(new URL("../CLAUDE.md", import.meta.url), "utf8"), claudeFile(), "run: node tools/site.mjs");
});

test("the GitHub workflows ship with the toolkit and are printed in the tutorial as they are", () => {
  const check = readFileSync(new URL("../templates/github/buddy-xr-check.yml", import.meta.url), "utf8");
  const claude = readFileSync(new URL("../templates/github/buddy-xr-claude.yml", import.meta.url), "utf8");
  assert.ok(check.includes("run: node --test") && check.includes("node bin/buddyxr.js check") && check.includes("contents: read"));
  assert.ok(claude.includes("anthropics/claude-code-action@v1") && claude.includes("secrets.ANTHROPIC_API_KEY") && claude.includes("secrets.CLAUDE_CODE_OAUTH_TOKEN"));
  assert.ok(claude.includes('"OWNER", "MEMBER", "COLLABORATOR"'));       // not for anyone who can comment
  assert.ok(!/sk-ant-|ghp_/.test(check + claude));                          // no key is ever written into a file
  for (const file of [check, claude]) assert.ok(!file.includes("\t"));     // YAML does not take tabs
  const page = tutorialPage();
  const shown = (text) => text.trim().replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  assert.ok(page.includes(shown(check)) && page.includes(shown(claude)));
});
