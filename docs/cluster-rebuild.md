# Rebuilding the cluster

How to bring the cluster back from git plus the Longhorn backups on the NAS.
It also covers losing a single Longhorn volume.

## What lives where

| Thing | Source of truth |
| ----- | --------------- |
| Hosts and k3s | `ansible/` |
| Flux, apps, PV and PVC objects | this repo (`kubernetes/`) |
| Secrets | SOPS files in this repo, decrypted with the age key |
| Data of the static volumes | Longhorn backups on the NAS |

The age key and the NAS share (`/volume1/longhorn`) are not in git. Keep a copy
of the key elsewhere and never reformat the share.

Each static volume is declared next to its app
(`kubernetes/apps/<ns>/<app>/app/persistent-volume.yaml` and
`persistent-volume-claim.yaml`). The PV's `csi.volumeHandle` is the name of the
Longhorn volume it expects; its StorageClass decides filesystem, replicas and
recurring jobs. Flux creates these objects, but not the Longhorn volumes: they
are restored from backup.
Until a volume exists, the pods that use it cannot start.

## Full rebuild

1. Rebuild the hosts and k3s with Ansible.
2. Bootstrap Flux: `mise run bootstrap-flux`.
3. Wait for Longhorn: `flux get kustomizations longhorn`. It reads the backup
   target from its HelmRelease values.
4. Check that Longhorn found the backups. The target must be `AVAILABLE` and
   list a backup volume per static volume:

   ```bash
   kubectl -n longhorn-system get backuptargets.longhorn.io
   kubectl -n longhorn-system get backupvolumes.longhorn.io
   ```

5. Plan the restore. This only reads from the cluster:

   ```bash
   mise run restore-longhorn-volumes
   ```

   Every static PV whose volume is missing is listed with the backup it would
   restore. Stop and look into any `MISSING` or `MISMATCH` line.
6. Create the volumes:

   ```bash
   mise run restore-longhorn-volumes -- --apply
   ```

   The script only creates volumes that do not exist, so it is safe to re-run.
7. Wait until the restores finish. A restored volume ends up `detached` or
   `attached` with `restoreRequired` false:

   ```bash
   kubectl -n longhorn-system get volumes.longhorn.io \
     -o custom-columns=NAME:.metadata.name,STATE:.status.state,ROBUSTNESS:.status.robustness,RESTORING:.status.restoreRequired
   ```

   The pods then attach their volumes on their next retry.
8. Check the result:
   - `kubectl get pv,pvc -A` shows every claim `Bound`.
   - The apps run and show their data (log in to Vaultwarden, open Home
     Assistant and so on).
   - Each restored volume has the label
     `recurring-job-group.longhorn.io/backup: enabled`, otherwise it is no
     longer backed up. The script sets it from the info stored with the backup.

## Losing one volume

If a single Longhorn volume is deleted, its PV and PVC are still there. Run
steps 5 to 8 above. The script restores only the missing volume.

## What you lose

- The restore point is the latest nightly backup (00:00), so up to a day of
  data is lost. The 15-minute snapshots are local to the volumes and are gone
  with them.
- Volumes are restored crash-consistent, like after a power loss.
- Volumes provisioned dynamically (`pvc-<uuid>`, for example OpenBao,
  VictoriaMetrics, MongoDB and Blocky) get new names on a rebuild and are not
  handled by this procedure.

## Never

- Do not change `persistentVolumeReclaimPolicy` of these PVs to `Delete`.
- Do not remove `kustomize.toolkit.fluxcd.io/prune: disabled` from the PVs and
  PVCs. It stops Flux from deleting them when a manifest leaves git or the
  Kustomization is removed.
- Do not set `force` on the app Kustomizations: it recreates objects when an
  immutable field changes.
- Do not declare Longhorn `Volume` objects in git. Flux pruning one would
  delete its data, and the backup it restores from changes every night.
