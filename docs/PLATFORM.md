# Buddy platform architecture, version 1.0

Locked on 20 November 2026 by the owner. **This page is a decision record and a plan. Nothing in it is built
unless the page says so.** To change a locked decision, change this page first.

## 1. What was decided

| # | Decision | Choice |
|---|---|---|
| 1 | Where it runs | **One Buddy server for each customer.** No shared cloud |
| 2 | What a customer pays for | **A base price for each active device a month, plus usage** for the modules with real running costs (Voice, Vision, Agent). Integrations are counted by connected system |
| 3 | Names | **Maestro stays the gateway for AI models**, as in the code and the app. "Central AI Orchestra" is the same thing and is not used as a second name |
| 4 | Devices in version 1 | **Glasses only.** Rings and watches stay data sources (connectors) |
| 5 | How far switches and meters reach | **Everything**: the new wearable modules and the parts Buddy already has |

Decided with these and unchanged by them:

- Buddy is a platform for wearable devices. A point of sale is one integration and does not shape the design.
- The device contract (`docs/BWCP.md`) is the name for the contract the glasses framework already has. It is not a
  second thing beside Buddy XR.
- Health information goes to no AI model. Medical records are out of scope.

No price is set on this page. Prices are a table, kept apart from the code (section 5).

## 2. The layers

```
GLASSES   audio · text · colour
   │  Bluetooth, maker's app or cloud
ADAPTER   one for each route
   │  the contract: profile · commands · events
PHONE     glasses runtime: fits lens or speech, privacy rule
   │  the link
SERVER    hearing · Claude · memory · Privacy Gateway · Maestro
   │  every change waits for a yes
ADAPTERS  one for each business system
   │
BUSINESS SYSTEMS
```

| Layer | State on 20 November 2026 |
|---|---|
| Adapters | Three written, none run on glasses |
| Contract | Exists in the code. Draft 0.1 written down; its additions are proposals |
| Glasses runtime | Built. Runs in the simulator |
| The link | Not built |
| Server | Built, for one owner |
| Business adapters | Not built for any business system |

## 3. Three rules that are never modules

They are always on. No customer can switch them off, and no price removes them.

1. **Privacy before anything leaves.** What may be shown, spoken or sent to a model is decided by how private it
   is (the Privacy Gateway on the server, the privacy rule on the glasses).
2. **Nothing changes in an outside system without a yes.**
3. **Capabilities, never brands.** An app says what the wearer should get; the device's profile decides how.

## 4. Modules

Every function belongs to a module. A module:

- sits with the component it belongs to (the components are the ones in `docs/architecture/`);
- has a **switch**;
- has a **meter** that counts one kind of thing;
- says which other modules it needs.

**Core** is always on. Being core does not mean it is not counted: the Agent meter counts the brain's requests.

### 4.1 Core (always on)

| Part | What it is |
|---|---|
| The brain | The conversation with Claude and its tools |
| Maestro | The gateway for AI models: which one does each job, what each may see, what it cost |
| Buddy Connect | The one way of calling outside services, with their keys |
| Memory | What Buddy knows and remembers |
| Security and privacy | Who can reach Buddy, the Privacy Gateway, secrets, the audit trail |
| Insights and costs | Where the ledger in section 5 lives |
| The server and the app | The connection, the pages, the phone app |

### 4.2 Modules Buddy already has

All of these exist and work today, and since 20 November 2026 each has a switch and a meter (built: section 7,
step 1). Every module is counted in the same five units, so a price can be put on whichever fits it:

| Unit | What is counted |
|---|---|
| request | A model request made for the module, with what it cost |
| use | One of the module's tools was run |
| outside call | A paid outside service was called for the module, with what it cost |
| minute | Speech heard (Voice) and time on the line (Phone calls) |
| day on | The module was on that day |

| Module | Its id | A unit that fits it |
|---|---|---|
| Voice: Talk mode and Listen-in | `voice` | Minutes |
| Phone calls | `calls` | Minutes |
| Briefing, reminders and alerts | `briefing` | Days on |
| Learning | `learning` | Uses |
| Money | `money` | Days on, requests |
| Shopping and deals | `shopping` | Uses, outside calls |
| Price book and trained models | `pricebook` | Days on |
| Investing | `investing` | Requests, outside calls |
| Travel and getting around | `travel` | Outside calls |
| Going out | `goingout` | Uses |
| News, trends and research | `news` | Uses, outside calls |
| Email, messages and apps | `email` | Days on, requests |
| Home and health | `home_health` | Days on. Health data still goes to no model |
| Making and doing things | `making` | Uses |
| Agents and skills | `agents` | Uses |
| Intelligence Platform | `intelligence` | Days on, requests |
| Fixing and explaining itself | `selfcare` | The owner's own server only; not offered to customers |

Not counted yet: connected banks, connected email accounts, lessons given, things made as their own units. They
show up as uses.

### 4.3 Wearable modules (new)

| Module | Sits with | Needs | Meter | Built? |
|---|---|---|---|---|
| Devices: connect glasses, read what they can do | A new Wearables component | Core | **Active devices a month** (the base price) | Adapters written, not run on glasses |
| Link: join the device to Buddy's brain | The server and the app | Devices | Included with Devices | No |
| Voice on a device | Voice | Devices, Link | Minutes heard, characters spoken | Hearing exists for the phone; not on glasses |
| Vision: understand a photo | New | Devices, Link | Images | No |
| Agent: reasoning and actions for a wearer | The brain, Maestro | Link | Requests | The brain exists; not joined to glasses |
| Knowledge: a customer's own memory and research | Memory, Intelligence Platform | Core | Documents stored, lookups | Exists for one owner |
| Integrations: one adapter for each business system | Buddy Connect, Agents | Core | Connected systems a month, actions sent | No business system connected |
| Governance: roles, policy, audit log | Security and privacy | Core | Seats a month | Exists for one owner; no roles for staff |
| Studio and SDK: build apps | Wearables | none | Free at first | Built, runs in the simulator |

### 4.4 How a switch works

- One list in the code names every module, its component, what it needs and its meter
  (`backend/buddy/modules.py`).
- One setting on each server says which modules are on: `BUDDY_MODULES`, in the server's root-only settings.
  Because each customer has their own server (decision 1), this is a setting on that server, not a shared table
  of customers.
- **The switch belongs to whoever runs the server.** `sudo bash /opt/buddy/deploy/modules.sh` lists the modules;
  `... off investing` and `... on investing` switch one and restart Buddy. In a conversation Buddy can say which
  modules are on and what each was used for. It cannot switch one.
- A module that is off does not exist for the conversation: its tools are not offered to the brain, and a call
  to one is refused. The background loops that belong to one module (email, price watches, bank feeds, the tutors)
  do not start.
- Switching a module on switches on what it needs, or refuses and says what is missing. A module that is not
  built cannot be switched on.
- **On the owner's own server everything Buddy has today stays on.** With the setting unset, nothing changes. A new
  customer's server starts with `BUDDY_MODULES=core`.

Not gated yet: the app's pages, and the jobs inside the loop that speaks up on its own (the morning briefing, the
deals collector, the market scan and the rest). With their module off they still run.

## 5. Metering, pricing and charging

Three separate things.

**The meter** counts. One ledger on each server, with one line a day for each module and unit:

| Field | Meaning |
|---|---|
| day | The day, in UTC |
| module | Which module |
| unit | What is counted: request, use, outside call, minute, day on. Later: device-month, image, action, seat |
| who | The device or the person, where there is one. Empty today |
| amount | How many that day |
| our cost | What it cost to run, where Buddy knows it |

It is written as things happen, at the three places Buddy already logs its work: every model request, every tool
run and every paid outside call is counted for the module it belongs to. Lines are kept for three years. A meter
that fails never breaks a reply. Counting started on the day this was installed; earlier use is not in it.

**The price list** is the operator's table: a CSV file with the columns `module,unit,price_usd`, named by the
setting `BUDDY_PRICE_LIST`. It is edited without touching code and is not on this page. Under decision 2 it has a
base price for each active device a month, usage prices for Voice, Vision and Agent, and a price for each connected
system under Integrations. A unit with no price is counted and not charged.

**The statement** for a month shows, for each module, what was counted, what it cost to run and, where there is a
price list, what it comes to: `sudo bash /opt/buddy/deploy/modules.sh statement 2026-11`, or ask Buddy "what did
each module cost this month?".

**Charging** (an invoice, a card) needs a payment company, terms and tax handling. It is not built and comes last.

## 6. Not decided, or out of scope

- Every price.
- The payment company and the terms of sale.
- A shared cloud for all customers (decided against for now: decision 1).
- Rings, watches and other wearables as devices (decision 4).
- Healthcare and medical records.
- The name "MasterOS". The module is called Governance here.
- Separate specifications for intelligence and for enterprise integration. There is one written contract, for
  devices.
- A certification programme, a device registry, an SDK for makers.
- When the device contract becomes 1.0. Proposed: after one device has run one app end to end.

## 7. Proposed build order (each step waits for the owner's go)

1. **The module list, the switches and the ledger**, applied first to the parts Buddy already has.
   **Built on 20 November 2026.** Tested by `backend/tests/test_modules.py`; not yet run on a server. Left for
   later in this step: gating the app's pages and the jobs inside the proactive loop.
2. **The link** between the glasses runtime on the phone and the server, with the additions to the contract in
   `docs/BWCP.md` section 4.
3. **One device, one app, end to end** on real glasses.
4. **The first business adapter**, chosen by the first customer's system.
5. Statements from the ledger; charging last.
