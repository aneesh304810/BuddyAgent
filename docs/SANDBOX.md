# The code sandbox: Buddy does coding work for you on your server

Ask Buddy things like:

- *"Clone github.com/me/app, run the tests and fix whatever fails."*
- *"In my app repo, add a /health endpoint with a test."*
- *"Write a script that renames my photos by date and test it on some samples."*

A coding agent (Claude Sonnet by default) works on it in the background, in a workspace on
your DigitalOcean server. It reads the code, runs commands, edits files and runs the tests,
then tells you what it did. The full report (steps, how it checked, the diff) is in
**Reports**. If it changed a GitHub repo, Buddy asks **"Shall I push the changes to a new buddy
branch?"** and pushes only after your yes, to a branch named `buddy/...`, never to `main`.
You open the pull request yourself.

## Set it up (once, about 5 minutes)

In Termux, SSH into the server:

```
sudo bash /opt/buddy/deploy/update.sh
sudo bash /opt/buddy/deploy/sandbox.sh
```

For private repos and pushing (optional):

```
sudo bash /opt/buddy/deploy/sandbox.sh token
```

Make the token on github.com → Settings → Developer settings → **Fine-grained tokens**:
*Only select repositories* (just the ones Buddy may work on), permission **Contents: Read and
write**. Paste it only into that hidden prompt.

Other commands: `sandbox.sh status` (running? workspaces, disk), `sandbox.sh off`.

## How it's kept safe

Code from the internet is untrusted, so it never runs next to Buddy:

- **A separate service** (`buddy-sandbox`, its own user) is the only thing allowed to use
  Docker. Buddy can only ask it for a few things over a local socket: create a workspace, run
  a command, read or write a file, clone, show the diff, push.
- **Every command runs in a throwaway container**: not root, no Linux capabilities, read-only
  system, limits on CPU (1.5 cores), memory (2 GB), processes and time (30 minutes at most).
  Only that workspace's folder is mounted: no Buddy files, keys, database or settings.
- **Network**: the containers reach the internet (to install packages) but not your server,
  your tailnet, private networks or DigitalOcean's metadata service.
- **The GitHub token** is kept in the sandbox's own settings (`/etc/buddy-sandbox/sandbox.env`,
  root only), not Buddy's, and used only to clone and push, never in a container that runs
  code. Before a push the repo's git config is reset and its hooks are ignored, so code in
  the repo can't redirect the push or steal the token.
- **The coding agent has only the sandbox tools**: text in a repo can't make it read your email,
  spend money or message anyone. And nothing is pushed without your yes.
- **Limits**: each task stops at $3 (`BUDDY_CODE_TASK_CAP_USD`) or 40 steps
  (`BUDDY_CODE_MAX_STEPS`); Buddy asks before starting one. At most 10 workspaces of 4 GB each.

Workspaces are kept between tasks (say "in the app workspace..." to continue earlier work).
Ask "what workspaces do I have?" or "delete the app workspace" to tidy up.

Disk: the sandbox image is about 1 GB. On a small droplet, check `df -h` now and then.

Settings: `BUDDY_CODE_MODEL` (default: the main chat model; set `claude-opus-5-5` for hard
work), `BUDDY_CODE_TASK_CAP_USD`, `BUDDY_CODE_MAX_STEPS`.
