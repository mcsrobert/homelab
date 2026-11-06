# Home Assistant configuration reconciliation

The sidecar starts in **observe** mode with a read-only live volume. It reads
only allowlisted files, compares them with its persistent baseline, and exposes
metrics. It does not push branches, open PRs or publish files in this mode.
Its Git checkout and transaction journal live on a separate Longhorn PVC.

## Rollout

1. Merge the capture safeguards in `mcsrobert/home-assistant-config`. Install the
   developer hooks with `mise run setup` in developer checkouts. Reconciler hooks
   are supplied independently by this homelab repository.
2. Merge observation mode. Check the metrics endpoint and initial drift against
   main. Do not assume existing files were previously deployed from main.
3. Verify that the existing OpenBao `github/hass-configurator` credential, key
   `GH_TOKEN`, can read the private repository, write contents, create/update
   pull requests and read checks. Rotate it there if needed; no secret is
   committed. Take a current full-volume backup before enabling writes.
4. Change `MODE` to `capture` in a separate reviewed GitOps change. Keep the live
   mount read-only. Differences from the initial baseline become a draft PR,
   including existing differences discovered during observation. Rehearse scan
   failure and human branch edits. Review capture deletions carefully if the
   live volume does not contain repository tooling or documentation.
5. Rehearse acceptance, rejection, newer edits and crash recovery against a
   disposable volume/repository. Then change `MODE` to `deploy` and remove the
   live mount's `readOnly` flag in a separate reviewed change. The four config
   CI jobs (`validate`, `check-config`, `secrets`, `verify-artifacts`) must succeed
   on the target main revision before publication.

The scanner comes from a pinned multi-architecture Gitleaks image. An init
container copies its binary into a shared ephemeral volume. The operator-owned
pre-commit hook invokes it with trusted rules, redaction and inline allow
comments disabled. Live or captured hooks/configuration are never executed.
Hook failure, a missing executable, failed Git operations or failed checks block
publication. Observation mode does not require GitHub write permissions.

## Decisions and recovery

An open capture PR always blocks deployment, even if main merges cleanly.
Additional settled edits update the same branch with ordinary commits. Remote
human branch changes or deletion block reconciliation; no force push is used.
Capture commits and PR identity are saved before network operations so retries
reuse them. Successful merge/rebase/squash converges to current approved main.
Newer live edits after acceptance get a fresh capture PR first.

Closing without merge rejects only the recorded snapshot. Newer branch/live
edits, or closure racing publication, block rather than discard. Rejected
branches remain for recovery. An unchanged rejected snapshot is replaced by
main once, then the baseline advances; it is not reopened as a new PR.

Before publication the reconciler persists the revision and before/after hashes.
After a crash it accepts only those hashes on managed paths and finishes the
transaction. Unexpected content blocks for operator investigation. Preserve the
state PVC and backup before manually repairing a conflict. Do not delete the
journal to force progress. Restarting in observation mode with an unfinished
transaction reports `recovery_requires_deploy` and does not write files.

Per-file writes use fsync and atomic replacement. They do not make the whole
configuration atomic. Settling checks and immediate pre-write checks reduce
races but cannot guarantee preservation of saves from uncoordinated writers.
Symlinks on managed paths block. Excluded runtime files are never reset or
removed. Reload and restart remain manual: deployed files and active Home
Assistant configuration are separate states.

## Monitoring

The internal metrics service is scraped through a ServiceMonitor. Existing
Prometheus/Alertmanager routing receives reconciliation failures, unavailable
reconcilers and reviews pending over 24 hours. Observation and capture-only
rollout gates do not generate failure alerts. Metrics have bounded state labels;
no commit IDs, PR numbers, filenames, exception details or secrets are emitted.
`ha_config_last_deployment_timestamp_seconds` records file publication, not HA
activation. A zero timestamp means no publication has been recorded.

Run the state-machine tests with:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
```
