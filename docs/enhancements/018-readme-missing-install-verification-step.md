# ENH-018: README install section has no verification step

**Persona(s):** Alex, Morgan
**Surface:** README.md
**Priority:** Medium

## Problem

The README Install section is:

```bash
git clone <this repo>
cd kit
pip install -e .
```

There is no step to verify the install succeeded. A first-time user who runs these three commands doesn't know whether lankit is now on their PATH or whether the install worked. Common failure modes:

- `pip` installed into a virtualenv the user didn't activate
- Python 3.11+ not available (silently installs against 3.10, may fail later)
- `lankit` not on PATH (no `~/.local/bin` in PATH on some Linux distros)

The first sign of failure is running `lankit --version` or the quick-start `lankit overview` and getting "command not found."

## Proposed fix

Add a verification step after install:

```bash
git clone <this repo>
cd kit
pip install -e .

# Verify:
lankit --version          # should print: lankit 0.x.x
```

And add a note for the common failure mode:

```
If 'lankit' is not found after install, your ~/.local/bin may not be in PATH.
Add it: export PATH="$HOME/.local/bin:$PATH"  (add to ~/.bashrc or ~/.zshrc to persist)
```

Also add the Python version check explicitly:
```bash
python --version          # must be 3.11 or newer
```

## Acceptance criteria

- [ ] README install section includes `lankit --version` as a verification step
- [ ] README includes a note about PATH issues on Linux (`~/.local/bin`)
- [ ] README includes a Python version check step before `pip install`
