// Buddy Serve: a waiter says the order, it is checked against the menu, and only then sent to the kitchen.
// It also brings the floor to the waiter: food that is ready, a guest who wants the check.
//
// Written once with Buddy XR. On glasses with a display the order is in the lens; on audio glasses it is read
// back. Nothing here knows which glasses it is on.
//
// The kitchen is whatever `send(order)` is given. The demo keeps orders in memory: there is no connection to a
// real point of sale yet.

import { Alert, Ask, List, Screen, Stat, Text, defineApp } from "../../src/index.js";

const NUMBERS = { a: 1, an: 1, one: 1, two: 2, three: 3, four: 4, five: 5, six: 6, seven: 7, eight: 8, nine: 9, ten: 10 };
const money = (n) => "$" + n.toFixed(2);
const plain = (s) => s.toLowerCase().replace(/[^a-z0-9 ]/g, " ").replace(/\s+/g, " ").trim();
const single = (w) => w.length > 3 && w.endsWith("s") ? w.slice(0, -1) : w;
const key = (s) => plain(s).split(" ").map(single).join(" ");

/** "two lattes and an avocado toast, no nuts, she is allergic to peanuts"
 *  -> { items: [{ name, qty, price }], unknown: ["..."], notes: ["no nuts"], allergies: ["peanuts"] } */
export function parseOrder(text, menu) {
  const names = new Map();
  for (const m of menu) for (const n of [m.name, ...(m.aliases || [])]) names.set(key(n), m);
  const out = { items: [], unknown: [], notes: [], allergies: [] };
  for (const part of String(text).toLowerCase().split(/\band\b|\bplus\b|\bwith\b|[,;.]/).map(plain).filter(Boolean)) {
    const allergy = part.match(/allerg(?:y|ic) to (.+)$/) || part.match(/^(?:a |an |the )?(.+) allergy$/);
    if (allergy) { out.allergies.push(allergy[1].replace(/^(a|an|the) /, "")); continue; }
    if (/^(no|without|extra|hold the) /.test(part)) { out.notes.push(part); continue; }
    const words = part.replace(/^(i would like|i ll have|can i get|we ll have|please|just) /, "").replace(/ please$/, "").split(" ");
    let qty = 1;
    if (NUMBERS[words[0]] || /^\d+$/.test(words[0])) qty = NUMBERS[words[0]] || Number(words[0]), words.shift();
    const found = names.get(words.map(single).join(" "));
    if (!found) { out.unknown.push(words.join(" ")); continue; }
    const have = out.items.find((i) => i.name === found.name);
    if (have) have.qty += qty; else out.items.push({ name: found.name, qty, price: found.price });
  }
  return out;
}

export const total = (items) => items.reduce((sum, i) => sum + i.qty * i.price, 0);
const lines = (items) => items.map((i) => ({ label: `${i.qty}× ${i.name}`, value: money(i.qty * i.price) }));

export function createServe({ menu, tables, send }) {
  const home = { screen: "home", table: null, order: null, problem: null, sent: null };
  return defineApp({
    name: "Buddy Serve",
    needs: ["mic"],
    state: { ...home, floor: [] },

    view(state, ctx) {
      if (state.screen === "table") return Screen({ id: "table-" + state.table, title: `Table ${state.table}` },
        state.problem && Alert(`${state.problem}? Not on the menu.`, { level: "warn" }),
        Text(state.problem ? "Order not sent. Say it again." : "Say the order.", { say: true }));

      if (state.screen === "confirm") return Screen({ id: "confirm-" + state.table, title: `Table ${state.table} · check` },
        List(lines(state.order.items)),
        ...state.order.notes.map((n) => Text(n)),
        // a guest's allergy is personal: shown in the lens, not read out over the dining room
        ...state.order.allergies.map((a) => Text(`Allergy: ${a}`, { privacy: "personal" })),
        Stat("Total", money(total(state.order.items))),
        Ask("Send to the kitchen?", {
          yesLabel: "Send", noLabel: "Start again",
          yes: async (c) => { await send({ table: state.table, ...state.order }); c.set({ ...home, screen: "sent", table: state.table, sent: state.order }); },
          no: (c) => c.set({ screen: "table", order: null, problem: null }),
        }));

      if (state.screen === "sent") return Screen({ id: "sent-" + state.table, title: `Table ${state.table} · sent` },
        Text(`${state.sent.items.map((i) => `${i.qty}× ${i.name}`).join(", ")} · ${money(total(state.sent.items))}`, { say: true }),
        List([{ label: "Done" }], { onSelect: (_i, _n, c) => c.set(home) }));

      const first = state.floor[0];
      return Screen({ id: "home", title: "Buddy Serve" },
        first && Alert(first.text, { level: first.kind === "check" ? "warn" : "info" }),
        first && Ask("Got it?", { yesLabel: "On my way", noLabel: "Later",
          yes: (c) => c.set((s) => ({ floor: s.floor.slice(1) })),
          no: (c) => c.set((s) => ({ floor: [...s.floor.slice(1), s.floor[0]] })) }),
        !first && Text("Say a table number to start an order."),
        List(tables.map((t) => ({ label: `Table ${t.number}`, detail: `${t.guests} guests`, number: t.number })),
          { onSelect: (item, _n, c) => c.set({ screen: "table", table: item.number, problem: null }) }));
    },

    onVoice(text, ctx) {
      const s = ctx.state;
      const table = plain(text).match(/^table (\w+)$/);
      if (s.screen === "home" || table) {
        const n = table && (NUMBERS[table[1]] || Number(table[1]));
        if (n && tables.some((t) => t.number === n)) ctx.set({ screen: "table", table: n, order: null, problem: null });
        else ctx.say(table ? "That is not one of your tables." : "Say a table number first.");
        return;
      }
      if (s.screen !== "table") return;
      const order = parseOrder(text, menu);
      if (order.unknown.length || !order.items.length) ctx.set({ problem: order.unknown[0] || text });
      else ctx.set({ screen: "confirm", order, problem: null });
    },

    // the floor: { type: "ready" | "check", table, text? } for one of this waiter's tables
    onEvent(event, ctx) {
      if (!tables.some((t) => t.number === event.table)) return;       // only your tables
      const text = event.text || (event.type === "check" ? `Table ${event.table} wants the check.` : `Table ${event.table}: food is ready.`);
      ctx.set((s) => ({ floor: [...s.floor, { kind: event.type, text }] }));
    },

    onBack(ctx) { ctx.set(home); },
  });
}

// What the demo runs on: a small menu, three tables, and a kitchen that only remembers.
export const DEMO_MENU = [
  { name: "Latte", price: 4.5 }, { name: "Cappuccino", price: 4.5 }, { name: "Espresso", price: 3 },
  { name: "Avocado Toast", price: 12, aliases: ["avo toast"] }, { name: "Eggs Benedict", price: 14, aliases: ["benedict", "eggs benny"] },
  { name: "Granola Bowl", price: 9, aliases: ["granola"] }, { name: "Orange Juice", price: 5, aliases: ["oj", "juice"] },
  { name: "Croissant", price: 4 },
];
export const DEMO_TABLES = [{ number: 1, guests: 2 }, { number: 2, guests: 4 }, { number: 4, guests: 3 }];

export function demoServe() {
  const kitchen = [];
  return { app: createServe({ menu: DEMO_MENU, tables: DEMO_TABLES, send: async (order) => { kitchen.push(order); } }), kitchen };
}
