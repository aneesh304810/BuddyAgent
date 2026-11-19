#!/usr/bin/env bash
# Where the time goes on Buddy's phone calls: one line per turn, counted from the moment the other person
# stopped speaking. No words are shown: calls keep no transcript.
#   sudo bash /opt/buddy/deploy/calllog.sh          # calls in the last 2 hours
#   sudo bash /opt/buddy/deploy/calllog.sh 30       # the last 30 minutes
#   sudo bash /opt/buddy/deploy/calllog.sh live     # watch a call as it happens (Ctrl+C to stop)
#
# Reading a line:
#   call 12 timing turn 2 (tell_me): they spoke 2.0 s; +0.30 s pause noticed; +0.80 s words from Deepgram [en];
#     +0.80 s Claude asked; +2.60 s first words written (Claude); +3.00 s voice started. Slowest step: Claude, 1.80 s
#   pause noticed        Buddy's speech detector heard them stop
#   words from Deepgram  the hearing service gave the words and judged the turn finished ([en]: the language it heard)
#   Claude asked         the question went to Claude (later than the words: Buddy was still busy with the last turn)
#   first words written  Claude's first sentence was ready (or "the ready greeting", or "a filler" such as "Mm-hmm")
#   voice started        ElevenLabs' audio began playing on the line: this is the pause the caller hears
set -uo pipefail
[[ $EUID -eq 0 ]] || { echo "Run as root (sudo)."; exit 1; }
PATTERN='buddy\.calls .*(call [0-9a-zA-Z_-]+ timing|now speaking|Deepgram|ElevenLabs|audio loop held up|no reply|Claude had nothing)'
clean() { sed -u -E 's/^[0-9-]+ ([0-9:]+),[0-9]+ buddy\.calls /\1  /'; }
if [[ "${1:-}" == "live" ]]; then
  echo "Watching calls. Make or take a call now; Ctrl+C to stop."
  echo
  journalctl -fu buddy -o cat --since now | grep --line-buffered -E "$PATTERN" | clean
  exit 0
fi
MINUTES="${1:-120}"
[[ "$MINUTES" =~ ^[0-9]+$ ]] || { echo "Give a number of minutes, or 'live'."; exit 1; }
LINES_FOUND=$(journalctl -u buddy -o cat --since "-${MINUTES} min" --no-pager | grep -E "$PATTERN" | clean)
if [[ -z "$LINES_FOUND" ]]; then
  echo "No call timings in the last $MINUTES minutes."
  echo "Timings are written by calls made after the update that added them; make a call, then run this again."
  exit 0
fi
echo "$LINES_FOUND"
echo
echo "Slowest step, counted over these turns:"
echo "$LINES_FOUND" | grep -oE "Slowest step: [^,]+" | sed 's/Slowest step: //' | sort | uniq -c | sort -rn | sed 's/^/  /'
echo
echo "Longest waits before the voice started:"
echo "$LINES_FOUND" | awk 'match($0, /\+[0-9.]+ s voice started/) { w = substr($0, RSTART + 1, RLENGTH - 16); match($0, /call [0-9a-zA-Z_-]+ timing turn [0-9]+/); print w " s  " substr($0, RSTART, RLENGTH) }' \
  | sort -rn | head -5 | sed 's/^/  /'
