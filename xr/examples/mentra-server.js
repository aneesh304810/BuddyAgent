// Buddy Serve on MentraOS: a complete app server. NOT YET RUN: it needs a MentraOS developer account, an app
// registered in its console, and a pair of supported glasses (or its simulated glasses).
//
// To try it:
//   1. Register an app at console.mentra.glass with a package name and this server's public address, and give it
//      the microphone permission.
//   2. In this folder:   npm install @mentra/sdk
//   3. Set the two values MentraOS gave you in your shell (never in a file that is committed, never in chat):
//        export PACKAGE_NAME=com.yourname.buddyserve
//        export MENTRAOS_API_KEY=...
//   4. node examples/mentra-server.js      and expose the port to the internet as MentraOS's guide describes.
//
// Everything shown and said passes through MentraOS's cloud. With the default below, a guest's allergy (marked
// personal) is NOT sent to the glasses: the wearer is told to open their phone. Set ALLOW_CLOUD=yes only if you
// accept that route for personal details. Health content is never sent either way.

import { AppServer } from "@mentra/sdk";
import { mentraHost, runApp } from "../src/index.js";
import { demoServe } from "../apps/serve/app.js";

const need = (name) => { const v = process.env[name]; if (!v) { console.error(`Set ${name} first.`); process.exit(1); } return v; };

class BuddyServe extends AppServer {
  runs = new Map();

  async onSession(session, sessionId) {
    const host = mentraHost(session, { onProblem: (e) => console.error("glasses:", e.message) });
    const run = runApp(demoServe().app, host, { allowCloud: process.env.ALLOW_CLOUD === "yes", onError: (e) => console.error("app:", e.message) });
    this.runs.set(sessionId, { run, host });
  }

  async onStop(sessionId) {
    const held = this.runs.get(sessionId);
    if (held) { held.run.stop(); held.host.close(); this.runs.delete(sessionId); }
  }
}

new BuddyServe({ packageName: need("PACKAGE_NAME"), apiKey: need("MENTRAOS_API_KEY"), port: Number(process.env.PORT) || 3000 }).start();
