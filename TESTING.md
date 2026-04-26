# lankit Testing Progression

Work through these phases in order. Each phase builds confidence before
touching anything live.

---

## Phase 1 — Offline (no router needed)

Fill in `network.yml` first: `household_name`, `hosts`, `router.ip`, and all
`CHOOSE` fields. Then:

```bash
lankit status                        # verify network.yml reads correctly
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
lankit test-failsafe                 # verify auto-revert works before relying on it
```

---

## Phase 5 — Provision Pi-hole + Unbound

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

- `lankit extend` uses `yaml.dump` to write back `network.yml`, which may
  reorder keys. Review the diff before committing.
- `lankit apply` uploads `.rsc` files via SFTP to `/tmp/` on the router.
  RouterOS purges `/tmp/` on reboot — no cleanup needed.
- `lankit discover --new` wizard writes a minimal `network.yml`. The full
  template in the repo has more segments and inline docs — consider using
  it as the base instead and filling in your values.

---

## After testing — before creating the repo

Things worth addressing based on real-world findings:

- [ ] Note any commands that failed or had unexpected output
- [ ] Note any `network.yml` fields that were confusing
- [ ] Note any missing `lankit` commands you reached for
- [ ] Check whether `configure-pihole.py` needs `toml` or can use stdlib `tomllib` (Python 3.11+)
- [ ] Decide whether `lankit extend`'s yaml.dump behaviour is acceptable
