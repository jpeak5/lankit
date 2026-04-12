# ENH-025: RouterOS import errors during `lankit apply` are printed as warnings and not fail-stopped

**Persona(s):** Sam, Alex, Jordan
**Surface:** CLI — `lankit apply`
**Priority:** High

## Problem

During `lankit apply`, each script is imported with:

```python
out, err = conn.run_tolerant(f"/import file={rsc.name}")
if err and "warning" not in err.lower():
    console.print(f"    [yellow]Warning:[/yellow] {err.strip()}")
```

RouterOS `/import` errors appear on stderr as lines like:
```
failure: duplicate entry
input does not match any value of interface
```

These are printed as warnings and the apply continues. The user sees a yellow warning in the middle of scrolling output, says "Keep changes?", and the router is now in a partially-applied state.

For Jordan and Alex, the result is a silent partial failure: some rules applied, some didn't, no clear summary of what succeeded. The apply returned "success" (green ✓), but the network may be misconfigured.

The check `if err and "warning" not in err.lower()` attempts to filter RouterOS warnings from errors, but this heuristic is fragile — RouterOS sometimes puts actual errors on stderr with "warning" in the text.

## Proposed fix

1. After all scripts are imported, check for any recorded import errors. If any error (non-warning) occurred:
   - Print a consolidated error summary before the "Keep changes?" prompt
   - Default the prompt to "No" (revert)
   - Include: which script failed, the error text, and "Answering 'no' will roll back to the pre-apply snapshot"

2. Add a `--strict` flag that treats any stderr output during import as a failure and auto-rolls-back without prompting.

3. Log all import stdout/stderr to a file (`ansible/generated/apply.log`) for post-mortem review.

## Acceptance criteria

- [ ] Import errors are collected across all scripts and summarized before the confirmation prompt
- [ ] Confirmation prompt defaults to "No" (revert) when any import error occurred
- [ ] Error summary names the script and the RouterOS error message
- [ ] `--strict` flag causes auto-rollback on any import error
- [ ] Import output is logged to `ansible/generated/apply.log` after each run
