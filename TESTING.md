# lankit Testing Progression

Work through these phases in order. Each phase builds confidence before
touching anything live.

---

## Phase 1 — Offline (no router needed)

Fill in `network.yml` first: `household_name`, `hosts`, `router.ip`, and all
`CHOOSE` fields. Then:

```bash
lankit overview                      # verify network.yml reads correctly
lankit generate                      # render scripts to ansible/generated/
lankit diagram --view                # visual sanity check
lankit rollback-card                 # generate and print before you need it
```

Read through `ansible/generated/*.rsc` — they're plain text. Confirm the
VLANs, subnets, and firewall rules match what you expect before touching the
router.

---

## Phase 2 — Read-only router probes

No config changes. Confirms SSH access works.

```bash
lankit discover                      # list all DHCP leases + ARP devices
lankit commit                        # snapshot only — takes no action on config
```

---

## Phase 3 — First live apply (low-risk)

Start with a single low-impact script before committing to the full stack.

```bash
lankit apply --dry-run               # confirm connectivity, show what would run
lankit apply --script 07-bandwidth.rsc   # bandwidth queues — easy to revert
```

If anything looks wrong: `lankit rollback`

---

## Phase 4 — Full apply

```bash
lankit apply                         # full stack, prompts to keep or revert
lankit test-failsafe                 # rehearse the failsafe scheduler before relying on it
```

`lankit apply` protects the push with a tag-scoped revert bundle
(`lankit-revert.rsc`, `docs/design/tag-scoped-revert.md`), uploaded and
verified on the router *before* any script is imported. If you decline at
the keep prompt, the import errors out, or the failsafe countdown fires
because the session dropped, that bundle removes everything lankit owns
and rebuilds the last known-good state — a converge, not a raw snapshot
replay. `lankit rollback` and `lankit restore` use the same bundle.

`lankit test-failsafe` rehearses the scheduler mechanics, not the real
revert bundle: it installs the actual `lankit-failsafe` scheduler and
walks it through five phases (pre-flight cleanup, install scheduler +
sentinel rule, verify the sentinel is present, reschedule the timer in
place and verify the start-time moved, then wait for it to fire and
confirm the sentinel was removed). The on-event shape (self-delete →
import → log markers) matches production, but today the "import" is a
one-line removal of the disabled sentinel rule, not `lankit-revert.rsc` —
so this drill certifies the timer/scheduler primitive, not a full
remove-and-rebuild. A `--full` mode that drills the actual revert bundle
end-to-end is designed but not yet implemented (execution plan G1.9); use
the lab router, not a live household network, once it lands.

---

## Phase 5 — Provision Pi-hole + Unbound (skip if no Pi-hole host configured)

```bash
lankit provision                     # runs ansible/site.yml against dns_server
```

Pre-flight checks on your development machine before running:
- `pip3 show toml` — required by `configure-pihole.py`
- OUI database: first run of `lankit discover` will try to download it;
  if it fails, run once manually:
  ```python
  python3 -c "from mac_vendor_lookup import MacLookup; MacLookup().update_vendors()"
  ```

---

## Phase 6 — Portal UX tests (Playwright)

Requires the portals to be live on the app server and the CA cert trusted
on the machine running the tests.

**Install test dependencies:**

```bash
pip install -e ".[test]"
playwright install chromium
```

**Run the full suite:**

```bash
pytest tests/ -v
```

**Run a single persona:**

```bash
pytest tests/test_me.py::TestMeAsKwame -v
```

Screenshots are written to `docs/screenshots/` on each run and are
gitignored. They serve as living documentation of the current portal UI.

Open UX test gaps are tracked as GitHub issues labeled [`ux-test`](https://github.com/jpeak5/lankit/issues?q=label%3aux-test+is%3aopen).

**Network prerequisites:**

- DNS: `*.internal` must resolve to the app server from the machine running tests
- TLS: the CA cert must be installed and trusted on the test machine
- `LANKIT_APP_SERVER` env var: defaults to `10.40.0.3:5000` — override if your
  Pi's address differs
- One test (`test_https_portal_fails_without_cert`) verifies TLS enforcement
  using a strict context. If the CA cert is in the machine's system trust store,
  this test will produce a false pass — run it from a machine that hasn't
  installed the cert, or accept the false pass as a known limitation

---

## Known rough edges to flag

- `lankit apply` uploads `.rsc` files via SFTP to the router's flash root
  (RouterOS SFTP has no `/tmp`). Uploaded files persist and can be listed
  with `/file print`.
- `lankit discover --new` wizard writes a minimal `network.yml`. The full
  template in the repo has more segments and inline docs — consider using
  it as the base instead and filling in your values.
