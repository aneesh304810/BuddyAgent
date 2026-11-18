#!/usr/bin/env node
// buddyxr check <app.js>   run an app on every kind of glasses and say where it would not work or lose something
// buddyxr glasses          list the glasses the framework knows and what each can do
// buddyxr sim [port]       serve the simulator on this machine (default port 8770)
//
// <app.js> must export an app as `default`, or a function `demo()` / `demoServe()` returning { app }.

import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { extname, join, normalize, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { PROFILES, check, works } from "../src/index.js";

const [, , command, arg] = process.argv;
const root = resolve(fileURLToPath(import.meta.url), "../..");

async function load(file) {
  const mod = await import(pathToFileURL(resolve(file)).href);
  const made = mod.default || (mod.demo && mod.demo()) || (mod.demoServe && mod.demoServe());
  const app = made && made.app ? made.app : made;
  if (!app || typeof app.view !== "function") throw new Error(`${file} does not export an app (a default export, or demo() returning { app }).`);
  return app;
}

if (command === "glasses") {
  for (const p of Object.values(PROFILES)) {
    const has = [p.display ? `${p.display.kind} display ${p.display.lines}×${p.display.cols}${p.display.images ? " with images" : ""}` : "no display",
      p.speaker ? "speaker" : "no speaker", p.mic ? "microphone" : "no microphone", p.camera ? "camera" : "no camera",
      p.input.length ? p.input.join("/") : "no tap", p.route === "direct" ? "direct from the phone" : "through the maker's app"];
    console.log(`${p.id.padEnd(11)} ${p.name}\n${" ".repeat(12)}${has.join(", ")}${p.tested === false ? "\n" + " ".repeat(12) + "not yet run on hardware" : ""}`);
  }
} else if (command === "check" && arg) {
  const app = await load(arg);
  const found = check(app);
  console.log(`${app.name}\n`);
  for (const id of Object.keys(PROFILES)) {
    const mine = found.filter((f) => f.profile === id);
    console.log(`${works(found, id) ? "works  " : "FAILS  "}${PROFILES[id].name}`);
    for (const f of mine) console.log(`         ${f.level}: ${f.text}`);
  }
  const failing = Object.keys(PROFILES).filter((id) => !works(found, id));
  console.log(`\n${Object.keys(PROFILES).length - failing.length} of ${Object.keys(PROFILES).length} work.`);
  process.exitCode = failing.some((id) => PROFILES[id].display || PROFILES[id].speaker) ? 1 : 0;   // glasses that cannot output at all do not fail the check
} else if (command === "sim") {
  const types = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".json": "application/json" };
  const port = Number(arg) || 8770;
  createServer(async (req, res) => {
    const path = normalize(decodeURIComponent(new URL(req.url, "http://x").pathname)).replace(/^(\.\.[/\\])+/, "");
    const file = join(root, path === "/" ? "simulator/index.html" : path.endsWith("/") ? path + "index.html" : path);
    if (!file.startsWith(root + "/")) { res.writeHead(403); return res.end(); }
    let body;
    try { body = await readFile(file); } catch { res.writeHead(404); return res.end("Not found"); }
    res.writeHead(200, { "Content-Type": types[extname(file)] || "application/octet-stream" });
    res.end(body);
  }).listen(port, "127.0.0.1", () => console.log(`Simulator: http://localhost:${port}/simulator/`));
} else {
  console.log("buddyxr check <app.js> | buddyxr glasses | buddyxr sim [port]");
  process.exitCode = command ? 1 : 0;
}
