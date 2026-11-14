# Rebuilding the cluster

How to bring the cluster back from git plus the Longhorn backups on the NAS.
It also covers losing a single Longhorn volume.

## What lives where

| Thing | Source of truth |
| ----- | --------------- |
| Hosts and k3s | `ansible/` |
| Flux, apps, PV and PVC objects | this repo (`kubernetes/`) |
| Bootstrap secrets (`cluster-secrets`, OpenBao unseal key) | SOPS files in this repo, decrypted with the age key |
| All other secrets | OpenBao, on the dynamic volume of `security/data-openbao-0` |
| Data of the static and dynamic volumes | Longhorn backups on the NAS |

The age key and the NAS share (`/volume1/longhorn`) are not in git. Keep a copy
of the key elsewhere and never reformat the share.

Each static PV is declared next to its app in
`kubernetes/apps/<ns>/<app>/app/persistent-volume.yaml`. The PVC is declared in
the HelmRelease's app-template `persistence` values. `forceRename` preserves its
existing name, `volumeName` selects the static PV, and `retain: true` keeps the
claim if Helm removes it from the release. The PV's `csi.volumeHandle` is the
name of the Longhorn volume it expects. Its `claimRef` reserves it for the
app's namespaced PVC. Flux manages the PV; Helm manages and mounts the PVC.

The installed helm-controller enables ownership adoption by default, so it can
adopt the existing manually created PVCs without recreating them or manually
adding Helm ownership metadata. Keep `install.disableTakeOwnership` and
`upgrade.disableTakeOwnership` unset. Names, bindings, storage classes and sizes
match the existing claims; Kubernetes defaults their volume mode to Filesystem.
Flux does not create the Longhorn volumes: they are restored from backup.
The restore script takes the replica count from the StorageClass and restores
recurring-job labels from the backup; the CSI driver defaults to ext4.
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
8. Restore OpenBao, see [Dynamic volumes](#dynamic-volumes). Until then it
   runs on a new, empty volume and every `ExternalSecret` fails.
9. Check the result:
   - `kubectl get pv,pvc -A` shows every claim `Bound`.
   - The apps run and show their data (log in to Vaultwarden, open Home
     Assistant and so on).
   - Each restored volume has the label
     `recurring-job-group.longhorn.io/backup: enabled`, otherwise it is no
     longer backed up. The script sets it from the info stored with the backup.

## Dynamic volumes

Volumes provisioned from a `volumeClaimTemplate` or a chart-managed PVC are
named `pvc-<uuid>` and are not declared in git. After a rebuild their workloads
start on new, empty volumes, and the restore script skips them. The ones in the
backup group are OpenBao, the Habitica MongoDB, Blocky and VictoriaLogs.

OpenBao matters most: it holds every secret that is not in SOPS. It
initializes itself on the empty volume, so it looks healthy while the
`homelab` mount is empty. Its unseal key is in SOPS, so the restored data
unseals without further steps.

List the backup volume of each claim:

```bash
kubectl -n longhorn-system get backupvolumes.longhorn.io -o json | jq -r '
  .items[] | select(.status.labels.KubernetesStatus)
  | [.spec.volumeName, (.status.labels.KubernetesStatus | fromjson
     | .namespace + "/" + .pvcName)] | @tsv'
```

Then follow Longhorn's
[StatefulSet restore procedure](https://longhorn.io/docs/latest/snapshots-and-backups/backup-and-restore/restore-statefulset/)
for the claim, for OpenBao `security/data-openbao-0`:

1. Suspend the HelmRelease and scale the StatefulSet to zero:

   ```bash
   flux suspend helmrelease openbao -n security
   kubectl -n security scale statefulset openbao --replicas=0
   ```

2. Delete the empty claim. Its reclaim policy is `Delete`, so the empty
   volume goes with it:

   ```bash
   kubectl -n security delete pvc data-openbao-0
   ```

3. In the Longhorn UI, restore the latest backup of the backup volume found
   above, then use **Create PV/PVC** on the restored volume with the claim
   name `data-openbao-0` in namespace `security`.
4. Resume: `flux resume helmrelease openbao -n security`.
5. Check that the secrets are back: `flux get kustomizations` shows the apps
   Ready and `kubectl get externalsecrets -A` shows `SecretSynced`.

## Losing one volume

If a single static Longhorn volume is deleted, its PV and PVC are still there.
Run steps 5 to 7 and 9 above. The script restores only the missing volume.

## What you lose

- The restore point is the latest nightly backup (00:00), so up to a day of
  data is lost. The 15-minute snapshots are local to the volumes and are gone
  with them.
- Volumes are restored crash-consistent, like after a power loss.
- Dynamic volumes without a backup (VictoriaMetrics, the Immich database
  volumes, T3 Code) start empty. The Immich database has its own Barman
  backups in Garage.

## Never

- Do not change `persistentVolumeReclaimPolicy` of these PVs to `Delete`.
- Do not remove `kustomize.toolkit.fluxcd.io/prune: disabled` from the PVs. It
  stops Flux from deleting them when a manifest leaves git or the
  Kustomization is removed.
- Keep `retain: true` on the app-template PVCs. It adds
  `helm.sh/resource-policy: keep`, which prevents Helm from deleting claims
  during an upgrade, rollback or uninstall.
- Do not set `force` on the app Kustomizations: it recreates objects when an
  immutable field changes.
- Do not enable force replacement on the HelmReleases, or change the PVC names
  or PV bindings during an upgrade.
- Do not declare Longhorn `Volume` objects in git. Flux pruning one would
  delete its data, and the backup it restores from changes every night.
