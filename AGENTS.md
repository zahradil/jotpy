# Working on jotpy

## When you add or change a feature

When what the server or the browser can do changes, update the description in the same commit:

1. `README.md`: what jotpy does, the API and the data format. It describes the current state, not the history.
2. `skill-jot/SKILL.md`: the skill for an agent that works with a note or table through a share link. The server serves it at `/skill/jot/SKILL.md`. It must match what the API under `/api/share/…` really accepts and returns.

The text for the agent (`agentInstructions` in `public/app.js` and `public/sheet.js`, copied by the "for agent" buttons in the Share dialog and by the robot on a shared page) gives the agent only the skill's address and the share link. Do not put API instructions in it; they belong in the skill. Change it only when the way of handing over to the agent changes.

Cover new server behaviour with a test in `tests/`.

## Inline scripts

Pages may run only the inline scripts listed in `CONTENT_SECURITY_POLICY` in `src/jotpy/pages.py`, allowed by their hash. Put new code in a file under `public/`; if an inline script is unavoidable, add its constant to that list.

## Tests

```bash
uv run pytest
```
