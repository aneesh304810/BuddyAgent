# Skills: ready-made, tested scripts the task agent can use

Each skill is a folder with a `SKILL.md` (front matter: name, description, tags; then how to use
it) and its script. In the sandbox they're at `/buddy/skills/<name>/` (read-only). The agent finds
them with `find_tools`. Add a skill with a pull request: a folder, a SKILL.md, a script that runs
with the sandbox's built-in libraries, and a test in `backend/tests/test_registry.py`.
