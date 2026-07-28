# lankit — session bootstrap (pointer-only)

## Where truth lives
- Execution plan + unit status: `docs/v1-readiness-review.md` Part 4.
  Status is updated there via docs commits — never in this file.
- Revert mechanism design: `docs/design/tag-scoped-revert.md`
  (authoritative over the plan table wherever they disagree).
- Live issue queue: GitHub issues (`gh issue list`).
  `docs/enhancements/` is a frozen archive — do not file or update there.
- Mission: `docs/mission.md`. Personas: `docs/personas.md`.

## Standing rules
- NEVER run commands against the production router (192.168.88.1).
- The lab router (192.168.77.1, hAP ac) is for explicitly-authorized
  hardware validation only.
- All network changes go through the generated/Ansible pipeline —
  no manual drift.

## Mechanics
- Python env: `/opt/python/current/` (no repo-local venv; bare
  `python`/`pytest` are not on PATH).
- Run tests: `/opt/python/current/bin/python -m pytest tests/unit -q`
- Agent definitions: `.claude/agents/`

This file stays pointer-only: status updates land in the plan doc, never here.
