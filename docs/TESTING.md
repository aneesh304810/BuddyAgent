# Testing Buddy, feature by feature

Do it in order: each part needs the ones before it. ✅ = what you should see or hear.
If something fails, run `sudo bash /opt/buddy/deploy/doctor.sh` and note the time.

## 0. Server (Termux)
```
ssh root@buddy.<your-tailnet>.ts.net
sudo bash /opt/buddy/deploy/update.sh      ✅ {"ok":true,...} Buddy updated and running.
sudo bash /opt/buddy/deploy/audit.sh       ✅ No known vulnerabilities; 600 root:root
```

## 1. Basics (app → Talk mode)
| Say or do | ✅ |
|---|---|
| "Hey Buddy, how are you?" | Answers in your earbuds; pill says **online** |
| Type a message in the box | Same answer on screen |
| Talk over Buddy while it speaks | It stops and listens |
| "Remember my car is on level 3" → "Where's my car?" | "Level 3" |
| "Remind me in 2 minutes to drink water" | Reminder 2 minutes later, even with the screen off |
| Settings → Buddy's voice → pick one | It speaks a sample in that voice |

## 2. Hands-free and earbuds
| Do | ✅ |
|---|---|
| Put earbuds in | "I'm here. Say hey Buddy when you need me." Listen-in starts |
| "Hey Buddy, what time is it?" | Answers |
| Take earbuds out | Listening stops |
| Tap the earbud once | Buddy starts listening (Talk) |

## 3. Listen-in
| Do | ✅ |
|---|---|
| Play a Spanish video near the phone | Whispered gist in English within ~30 s |
| Say confidently "The Great Wall is visible from space" | Whispered correction |
| Ask aloud "What's the capital of Australia?" then "let me think" | Whispers "Canberra" |
| While Buddy waits for a yes, say "Hey Buddy, yes" | "To be safe… tap Approve" (room speech can't approve) |

## 4. Organiser and briefings
| Say | ✅ |
|---|---|
| "Add buy milk to my shopping list" | "Added…" |
| "Pay rent today, high priority" → "What's on my list?" | Rent first |
| "Milk's done" | "Ticked off: buy milk" |
| Wait for 8:00 / 21:00 (or set `BUDDY_MORNING_BRIEF` a few minutes ahead) | Morning briefing / evening wrap-up |

## 5. Email (needs email.sh)
| Say | ✅ |
|---|---|
| "Which email accounts do you have?" | All your accounts |
| "Anything important in my email?" | Sorted by priority (after ~5–15 min of sync) |
| "Emails from my bank about payments are urgent" | Rule saved |
| "Label Uber receipts Travel" | Asks first (Approve/Decline appears) |
| "Draft a reply to my latest email saying thanks" | Draft appears in Gmail, not sent |
| "Send it" | Reads back who/subject; sends only after **Approve** |

## 6. Money (needs bank alert emails or SimpleFIN)
| Say | ✅ |
|---|---|
| "I paid 40 soles cash for a taxi" | "Recorded." |
| "Set a 100 dollar limit on food" → add food expenses past 80 | Warning at 80% and 100% |
| "How much did I spend this month?" | Totals per currency and category |
| "What bills are coming up?" | Bills from statement emails |
| "What are my account balances?" (SimpleFIN) | Banks and cards with balances |

## 7. Search, places and deals (needs Serper key)
| Say | ✅ |
|---|---|
| "What's trending in Peru today?" | Google Trends list (works without keys) |
| "Google the best ceviche in Lima" | Google results |
| "What does Reddit say about Cusco in December?" | Reddit threads (through Google) |
| Settings → Share my location on → "Where am I?" | Your neighbourhood and city |
| "Find a spa near me" | 3 picks with ratings, why they fit you, **and the catches** |
| "Remember I hate loud clubs" → "What's my taste in nightlife?" | Saved |
| "Find the cheapest flight Miami to Lima, 10 to 17 December" | Asks first (cost), then a report: true total, hidden fees, cancellation |
| "Watch it and tell me under 450" | Asks first (monthly cost), then watch is set |

## 8. Agents and building
| Say | ✅ |
|---|---|
| "Which agents do you have?" | gmail, zapier (if set), worldclock… |
| "Add a Todoist task: dentist Friday" (Zapier) | Approve/Decline, then done |
| "I need a weather agent for my trips" | Offers to build it (~$1), asks first; later "Shall I switch it on?" |

## 9. Life log and costs
| Do | ✅ |
|---|---|
| Buddy page → **Import** → Maps Timeline or Takeout zip | "Added N to your life log" |
| "When did I last go to <city>?" | Dates and places |
| "What do you cost to run?" / Buddy page → **Costs** | Totals per provider and per feature |

## 10. Safety checks
| Do | ✅ |
|---|---|
| Open the Buddy link without `?token=` in a private tab | **bad token** / nothing works |
| Open it with Tailscale off | Page doesn't load |
| `ssh root@<public IP>` (no Tailscale) | Refused |

## Speech recognition on your server (optional)
App → Settings → Speech recognition → **Your server** → talk.
✅ Works; `doctor.sh` shows `whisper took …s`. Switch back to **Phone** if it feels slow.
