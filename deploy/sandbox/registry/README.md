# Buddy's tool registry

`tools.yaml` lists the tools the sandbox's task agent can install and use; `../skills/` holds
ready-made scripts. Both are mounted read-only into every sandbox container at `/buddy`, and the
agent searches them with `find_tools`.

## Adding a tool (pull request)

1. Add an entry to `tools.yaml`: `name`, `kind` (`pip`, `npm`, `binary` or `builtin`), `package`
   and an exact `version` (pip/npm), `purpose` and `tags` (the words the agent searches), and a
   one-line `usage`. Mark anything with side effects or terms of use as `risk: medium`.
2. For a `binary`: an `https` release URL and its `sha256` (`curl -fsSL URL | sha256sum`), plus
   `extract` for the file inside an archive. A download that doesn't match is refused.
3. Run the tests: `cd backend && python -m pytest tests/test_registry.py`.

Before adding, check the project is established and maintained (age, downloads, maintainers,
open security advisories). Buddy's weekly health check lists packages tasks keep installing that
aren't here yet: good candidates.

## Adding a skill

A folder in `../skills/` with a `SKILL.md` (front matter `name`, `description`, `tags`; then how
to run it) and a script that uses only the image's built-in libraries, plus a test in
`backend/tests/test_registry.py`.
