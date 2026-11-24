#!/usr/bin/env -S uv run --script
# /// script
# dependencies = []
# ///
"""Restore Longhorn volumes from backup in two steps: plan, then apply.

1. Run plan to save the latest completed backups in a new local JSON file.
2. Review the sources, destinations, snapshot times, sizes and backup labels.
3. Run apply with that file and the same destination context and namespace.
4. Verify restore status and restored data before using the volumes.

Requires kubectl access to Longhorn and an available, synced backup target.
Supports unencrypted Longhorn v1, filesystem, ReadWriteOnce volumes without
backing images. Creates Volume objects only; PVs, PVCs, workload startup and
application verification are separate steps.

Use plan --help for restore examples and apply --help for verification and
retry instructions. Also available as: mise run restore-longhorn-volumes --
Offline tests: mise run test-longhorn-restore
"""

import argparse
from datetime import datetime, timezone
from decimal import Decimal, DecimalException, ROUND_CEILING
import json
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import parse_qs, urlsplit


class RestoreError(Exception):
    pass


class Cluster:
    def __init__(self, context, namespace):
        if not context or not context.strip():
            raise RestoreError("destination context must not be empty")
        self.context = context
        self.namespace = namespace

    def run(self, *args, document=None):
        result = subprocess.run(
            ["kubectl", "--context", self.context, "--request-timeout=30s", *args],
            input=json.dumps(document) if document is not None else None,
            capture_output=True, text=True,
        )
        if result.returncode:
            raise RestoreError(f"kubectl {' '.join(args)} failed: {result.stderr.strip()}")
        return result.stdout

    def identity(self):
        return self.run("get", "namespace", "kube-system", "-o", "jsonpath={.metadata.uid}").strip()

    def items(self, resource):
        args = ["get", resource, "-o", "json"]
        if resource.endswith(".longhorn.io"):
            args += ["-n", self.namespace]
        return json.loads(self.run(*args))["items"]


def name(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-z0-9]([a-z0-9-]{0,38}[a-z0-9])?", value):
        raise RestoreError(f"invalid Longhorn volume name (maximum 40 characters): {value!r}")
    return value


def timestamp(value):
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise RestoreError(f"snapshot timestamp has no timezone: {value}")
    return result


def to_bytes(quantity):
    match = re.fullmatch(r"([+-]?(?:\d+(?:\.\d*)?|\.\d+))(Ki|Mi|Gi|Ti|Pi|Ei|[numkMGTPE]|[eE][+-]?\d+)?", quantity)
    if not match:
        raise RestoreError(f"unsupported PV capacity: {quantity!r}")
    suffix = match[2] or ""
    try:
        if suffix.endswith("i"):
            scale = Decimal(1024) ** ("KMGTPE".index(suffix[0]) + 1)
        elif len(suffix) > 1 and suffix[0] in "eE":
            scale = Decimal(10) ** int(suffix[1:])
        else:
            scale = Decimal(10) ** {"n": -9, "u": -6, "m": -3, "": 0, "k": 3,
                                   "M": 6, "G": 9, "T": 12, "P": 15, "E": 18}[suffix]
        value = Decimal(match[1]) * scale
        if not 0 < value <= 2**63 - 1:
            raise RestoreError(f"PV capacity is outside the positive int64 range: {quantity!r}")
        return int(value.to_integral_value(rounding=ROUND_CEILING))
    except DecimalException as error:
        raise RestoreError(f"unsupported PV capacity: {quantity!r}") from error


def inventory(cluster):
    return {resource: cluster.items(resource) for resource in (
        "volumes.longhorn.io", "backups.longhorn.io", "backupvolumes.longhorn.io",
        "backuptargets.longhorn.io", "storageclass", "pv",
    )}


def recurring_job_labels(backup_volume):
    raw = (backup_volume.get("status", {}).get("labels") or {}).get("VolumeRecurringJobInfo")
    if raw is None:
        raise RestoreError("backup volume lacks VolumeRecurringJobInfo; verify its backup policy first")
    info = json.loads(raw)
    labels = {}
    for job_name, job in info.items():
        for group in job.get("fromGroup") or []:
            labels[f"recurring-job-group.longhorn.io/{group}"] = "enabled"
        if job.get("fromJob"):
            labels[f"recurring-job.longhorn.io/{job_name}"] = "enabled"
    return labels or {"recurring-job-group.longhorn.io/default": "disabled"}


def storage_class_spec(storage_class):
    class_name = storage_class["metadata"]["name"]
    parameters = storage_class.get("parameters", {})
    if parameters.get("encrypted", "false") != "false" or parameters.get("backingImage"):
        raise RestoreError(f"StorageClass {class_name}: encrypted or backing-image StorageClasses are unsupported")
    supported = {
        "numberOfReplicas", "dataEngine", "encrypted", "backingImage",
        "staleReplicaTimeout", "dataLocality", "disableRevisionCounter",
        "unmapMarkSnapChainRemoved", "backupTargetName", "fsType",
        "recurringJobSelector", "fromBackup",
    }
    unknown = set(parameters) - supported
    if unknown:
        raise RestoreError(f"StorageClass {class_name}: unsupported parameters: {', '.join(sorted(unknown))}")

    result = {"dataEngine": "v1"}
    for key, default, minimum, maximum in (
        ("numberOfReplicas", None, 1, 20),
        ("staleReplicaTimeout", "2880", 1, 2**31 - 1),
    ):
        value = parameters.get(key, default)
        if (not isinstance(value, str) or not re.fullmatch(r"\d+", value)
                or not minimum <= int(value) <= maximum):
            raise RestoreError(f"StorageClass {class_name}: {key} must be an explicit integer from {minimum} to {maximum}")
        result[key] = int(value)
    revision_counter = parameters.get("disableRevisionCounter", "true")
    if revision_counter not in ("true", "false"):
        raise RestoreError(f"StorageClass {class_name}: invalid disableRevisionCounter; use true or false")
    result["revisionCounterDisabled"] = revision_counter == "true"
    for key, values in (
        ("dataEngine", {"v1"}),
        ("dataLocality", {"disabled", "best-effort", "strict-local"}),
        ("unmapMarkSnapChainRemoved", {"ignored", "disabled", "enabled"}),
    ):
        if key in parameters:
            if parameters[key] not in values:
                raise RestoreError(f"StorageClass {class_name}: unsupported {key} value {parameters[key]!r}")
            result[key] = parameters[key]
    return result


def check_source(data, source, target, backup_volume):
    source_classes = {backup_volume.get("status", {}).get("storageClassName")}
    for volume in data["volumes.longhorn.io"]:
        if volume["metadata"]["name"] == source and volume.get("spec", {}).get("encrypted"):
            raise RestoreError(f"{source}: source volume is encrypted, including legacy backups without labels")
    for pv in data["pv"]:
        spec = pv["spec"]
        csi = spec.get("csi", {})
        if csi.get("driver") == "driver.longhorn.io" and csi.get("volumeHandle") == source:
            source_classes.add(spec.get("storageClassName"))
            if source != target and (spec.get("volumeMode", "Filesystem") != "Filesystem"
                                     or spec.get("accessModes") != ["ReadWriteOnce"]):
                raise RestoreError(f"{source}: source PV must use Filesystem and ReadWriteOnce")
    for storage_class in data["storageclass"]:
        if (storage_class["metadata"]["name"] in source_classes
                and storage_class.get("parameters", {}).get("encrypted", "false") != "false"):
            raise RestoreError(f"{source}: source StorageClass is encrypted, including legacy backups without labels")


def entry(data, source, target, storage_class, backup_target, namespace, backup_name=None):
    name(source)
    name(target)
    targets = [t for t in data["backuptargets.longhorn.io"]
               if t["metadata"]["name"] == backup_target and t.get("status", {}).get("available") is True
               and not t["metadata"].get("deletionTimestamp")]
    if len(targets) != 1:
        raise RestoreError(f"backup target {backup_target!r} is not available")
    backup_volumes = [v for v in data["backupvolumes.longhorn.io"]
                      if v["spec"].get("volumeName") == source
                      and v["spec"].get("backupTargetName") == backup_target
                      and not v.get("metadata", {}).get("deletionTimestamp")]
    if len(backup_volumes) != 1:
        raise RestoreError(f"{source}: expected one synced backup volume on {backup_target}")
    backups = [b for b in data["backups.longhorn.io"]
               if b.get("status", {}).get("volumeName") == source
               and b.get("status", {}).get("backupTargetName") == backup_target
               and b.get("status", {}).get("state") == "Completed"
               and not b["status"].get("error")
               and not b.get("metadata", {}).get("deletionTimestamp")
               and (backup_name is None or b["metadata"]["name"] == backup_name)]
    if not backups:
        raise RestoreError(f"{source}: no completed backup on {backup_target}")
    backup = max(backups, key=lambda b: (timestamp(b["status"]["snapshotCreatedAt"]), b["metadata"]["name"]))
    status = backup["status"]
    size = int(status["volumeSize"])
    backup_volume = backup_volumes[0]
    check_source(data, source, target, backup_volume)
    volume_size = backup_volume.get("status", {}).get("size")
    if volume_size and int(volume_size) != size:
        raise RestoreError(f"{source}: backup volume size differs from snapshot size; choose a compatible backup")
    for metadata in (status, backup_volume.get("status", {})):
        labels = metadata.get("labels") or {}
        if (any(labels.get(key, "disabled") != "disabled"
                for key in ("volume-encrypted", "longhorn.io/volume-encrypted"))
                or labels.get("longhorn.io/volume-access-mode", "rwo") != "rwo"):
            raise RestoreError(f"{source}: encrypted or non-RWO backups are outside this restore procedure")
    url = urlsplit(status["url"])
    query = parse_qs(url.query)
    target_url = urlsplit(targets[0]["spec"]["backupTargetURL"])
    if (size <= 0 or not url.scheme or not url.netloc
            or (url.scheme, url.netloc, url.path.rstrip("/")) !=
               (target_url.scheme, target_url.netloc, target_url.path.rstrip("/"))
            or query.get("volume") != [source] or query.get("backup") != [backup["metadata"]["name"]]):
        raise RestoreError(f"{source}: invalid backup URL or size")
    if status.get("volumeBackingImageName"):
        raise RestoreError(f"{source}: backing-image volumes require a separate restore procedure")
    classes = [s for s in data["storageclass"] if s["metadata"]["name"] == storage_class]
    if len(classes) != 1 or classes[0].get("provisioner") != "driver.longhorn.io":
        raise RestoreError(f"{source}: missing Longhorn StorageClass {storage_class!r}")
    volume_spec = storage_class_spec(classes[0])
    for pv in data["pv"]:
        spec = pv["spec"]
        csi = spec.get("csi", {})
        if csi.get("driver") == "driver.longhorn.io" and csi.get("volumeHandle") == target:
            if (to_bytes(spec["capacity"]["storage"]) != size
                    or spec.get("storageClassName") != storage_class
                    or spec.get("accessModes") != ["ReadWriteOnce"]
                    or spec.get("volumeMode", "Filesystem") != "Filesystem"):
                raise RestoreError(f"{target}: PV capacity, class or access mode differs from restore")
    job_labels = (recurring_job_labels(backup_volume) if source == target
                  else {"recurring-job-group.longhorn.io/default": "disabled"})
    return {
        "source": source, "target": target, "storageClass": storage_class,
        "backupTarget": backup_target, "backupName": backup["metadata"]["name"],
        "snapshotCreatedAt": status["snapshotCreatedAt"],
        "manifest": {
            "apiVersion": "longhorn.io/v1beta2", "kind": "Volume",
            "metadata": {"name": target, "namespace": namespace,
                         "labels": job_labels},
            "spec": {**volume_spec, "size": str(size),
                     "accessMode": "rwo", "frontend": "blockdev",
                     "encrypted": False,
                     "Standby": False,
                     "restoreVolumeRecurringJob": "disabled",
                     "backupTargetName": backup_target, "fromBackup": status["url"]},
        },
    }


def selections(args, data):
    if args.volume:
        if not args.storage_class:
            raise RestoreError("--volume requires --storage-class (also for dynamic pvc-UUID backups)")
        result = []
        for value in args.volume:
            source, separator, target = value.partition("=")
            result.append((source, target if separator else source, args.storage_class))
        return result
    result = []
    found = set()
    for pv in data["pv"]:
        spec = pv["spec"]
        csi = spec.get("csi", {})
        if csi.get("driver") != "driver.longhorn.io":
            continue
        handle = csi["volumeHandle"]
        pv_name = pv["metadata"]["name"]
        if args.pv and pv_name not in args.pv:
            continue
        if args.all_static and re.fullmatch(r"pvc-[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", handle, re.IGNORECASE):
            continue
        found.add(pv_name)
        result.append((handle, handle, spec["storageClassName"]))
    if args.pv and set(args.pv) != found:
        raise RestoreError(f"Longhorn PVs not found: {', '.join(sorted(set(args.pv) - found))}")
    if not result:
        raise RestoreError("no Longhorn PVs selected; use --volume when PVs do not exist yet")
    return result


def plan(args, cluster):
    identity = cluster.identity()
    data = inventory(cluster)
    existing = {v["metadata"]["name"] for v in data["volumes.longhorn.io"]}
    entries = []
    seen = set()
    for source, target, storage_class in selections(args, data):
        name(source)
        name(target)
        if target in seen:
            raise RestoreError(f"duplicate destination: {target}")
        seen.add(target)
        if target in existing:
            print(f"SKIP {target}: already exists; health and data have not been checked", file=sys.stderr)
            continue
        item = entry(data, source, target, storage_class, args.backup_target, cluster.namespace)
        entries.append(item)
        print(f"RESTORE {source} -> {target}: {item['backupName']} ({item['snapshotCreatedAt']})", file=sys.stderr)
    document = {"version": 1, "context": cluster.context, "clusterUID": identity,
                "namespace": cluster.namespace, "createdAt": datetime.now(timezone.utc).isoformat(),
                "volumes": entries}
    if not identity:
        raise RestoreError("could not identify destination cluster")
    with args.output.open("x") as output:
        output.write(json.dumps(document, indent=2) + "\n")
    print(f"Plan saved to {args.output}; {len(entries)} volume(s). No changes made.", file=sys.stderr)


def matches_manifest(volume, manifest):
    metadata = volume.get("metadata", {})
    return (not metadata.get("deletionTimestamp")
            and all(metadata.get(key) == manifest["metadata"][key] for key in ("name", "namespace"))
            and all(volume.get("spec", {}).get(key) == value for key, value in manifest["spec"].items())
            and all(metadata.get("labels", {}).get(key) == value
                    for key, value in manifest["metadata"]["labels"].items())
            and all(key in manifest["metadata"]["labels"]
                    for key, value in (metadata.get("labels") or {}).items()
                    if value == "enabled" and key.startswith(("recurring-job.longhorn.io/", "recurring-job-group.longhorn.io/"))))


def apply(args, cluster):
    document = json.loads(args.plan.read_text())
    if (document["version"] != 1 or document["context"] != cluster.context
            or document["namespace"] != cluster.namespace
            or not document["clusterUID"] or document["clusterUID"] != cluster.identity()):
        raise RestoreError("plan version, context, namespace or cluster identity differs; make a new plan")
    data = inventory(cluster)
    existing = {v["metadata"]["name"]: v for v in data["volumes.longhorn.io"]}
    pending = []
    seen = set()
    for item in document["volumes"]:
        target = name(item["target"])
        if target in seen:
            raise RestoreError(f"duplicate destination: {target}")
        seen.add(target)
        expected = entry(data, item["source"], target, item["storageClass"],
                         item["backupTarget"], cluster.namespace, item["backupName"])
        if expected != item:
            raise RestoreError(f"{target}: backup, StorageClass, labels or plan changed; make a new plan")
        if target in existing:
            volume = existing[target]
            if not matches_manifest(volume, item["manifest"]):
                raise RestoreError(f"{target}: destination exists and does not match this restore plan")
            print(f"SKIP {target}: matching restore exists; check restore status separately")
        else:
            pending.append(item["manifest"])
    # Validate every admission before the first write. Creation is still not atomic.
    for manifest in pending:
        admitted = json.loads(cluster.run("create", "--dry-run=server", "-f", "-", "-o", "json", document=manifest))
        if not matches_manifest(admitted, manifest):
            raise RestoreError(f"{manifest['metadata']['name']}: admission changed planned restore fields")
    for manifest in pending:
        created = json.loads(cluster.run("create", "-f", "-", "-o", "json", document=manifest))
        if not matches_manifest(created, manifest):
            raise RestoreError(f"{manifest['metadata']['name']}: created volume differs from plan; stop and inspect it before retrying")
        print(f"CREATED {manifest['metadata']['name']}; restore is asynchronous")


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    commands = parser.add_subparsers(dest="command", required=True)
    descriptions = {
        "plan": "Read cluster metadata and save a restore plan; never changes the cluster.",
        "apply": "Create missing volumes from a reviewed plan; changes the cluster.",
    }
    examples = {
        "plan": """Examples:
  Restore an independent test copy while the production volume stays in use:
    python3 scripts/longhorn_restore.py plan --context homelab \\
      --volume bazarr-config=bazarr-restore-test \\
      --storage-class longhorn --output /tmp/bazarr-restore.json

  Prowlarr uses a different StorageClass, so plan it separately:
    python3 scripts/longhorn_restore.py plan --context homelab \\
      --volume prowlarr-config=prowlarr-restore-test \\
      --storage-class longhorn-backup --output /tmp/prowlarr-restore.json

  Recover missing volumes using existing PV definitions:
    python3 scripts/longhorn_restore.py plan --context homelab \\
      --pv bazarr-config --pv prowlarr-config --output /tmp/recovery.json

Selection:
  --pv derives capacity and StorageClass from the selected live PVs.
  --all-static selects all live Longhorn PVs except pvc-UUID volumes.
  If PVs were lost in a rebuild, or a source is dynamically named, identify
  its original volume name from Longhorn backup metadata and use --volume
  SOURCE[=DESTINATION] with an explicit --storage-class. Repeat --volume for
  sources sharing that class. Omit =DESTINATION to retain the source name.
  --backup-target selects another synced target (default: default).

Legacy backups may omit encryption labels. The planner also checks the source
Volume and the recorded/source PV StorageClass when available. If that evidence
was lost, verify the original encryption configuration or inspect a restored
device in isolation before treating it as an unencrypted filesystem. Do not
attach an unverified legacy restore to a production workload.

Find source volume names and their latest backup times:
  kubectl --context homelab -n longhorn-system get backupvolumes.longhorn.io \\
    -o custom-columns=VOLUME:.spec.volumeName,TARGET:.spec.backupTargetName,LAST:.status.lastBackupAt

Restores under the original name retain the backed-up recurring-job labels.
Renamed copies have recurring jobs disabled; enable a backup policy separately
if a copy becomes permanent. The selected StorageClass must explicitly set
numberOfReplicas. Its staleReplicaTimeout, disableRevisionCounter, dataLocality
and unmapMarkSnapChainRemoved settings are preserved; other Volume parameters
are rejected. Omitted timeout/revision settings use Longhorn CSI defaults.
Filesystem settings belong to the separately managed PV. The backup URL,
backup target and job labels come from this restore plan, overriding the
StorageClass's dynamic-provisioning defaults for those fields.

Review the saved JSON before apply. It pins each backup URL and snapshot time
to this destination cluster's identity. Keep plans local; use a new filename
for a new plan. Existing destinations are skipped without checking their
health or data; an empty plan does not demonstrate a successful recovery.
""",
        "apply": """Example (after reviewing the JSON plan):
  python3 scripts/longhorn_restore.py apply --context homelab \\
    /tmp/bazarr-restore.json

The context, namespace and destination cluster identity must match the plan.
All pinned backups and server admission responses are checked before the
first creation. Existing destinations are never overwritten. Creation is
not atomic: after a partial failure, inspect the volumes and retry the same
plan. Matching restores are skipped; conflicting volumes stop execution.
A newer backup never replaces the reviewed restore point. If the pinned
backup disappears or the metadata changes, make and review a new plan.
Real create responses are checked too. A mismatch stops the remaining batch;
the created volume is kept for inspection and is never automatically deleted.

Creation is asynchronous. Check the destination volume, for example:
  kubectl --context homelab -n longhorn-system get volumes.longhorn.io \\
    bazarr-restore-test -o yaml

Before attaching the inspection workload, confirm restoreInitiated=true,
restoreRequired=false, state detached, and no restore errors. Robustness can
normally be unknown while detached; after attachment, check that it is healthy.
Verify expected files and database integrity through an isolated read-only
mount. Use distinct test PV/PVC names, the test volume's CSI handle and reclaim
policy Retain. Do not start a copied production app or expose restored
configuration/credentials.

This command does not create PVs/PVCs, rebind claims, stop workloads, wait for
completion or clean up test resources. Remove only verified, unreferenced
test resources after data checks; preserve production volumes and backups.
""",
    }
    for command in ("plan", "apply"):
        sub = commands.add_parser(
            command, help=descriptions[command], description=descriptions[command],
            epilog=examples[command], formatter_class=argparse.RawDescriptionHelpFormatter,
        )
        sub.add_argument("--context", required=True, help="explicit destination kube context")
        sub.add_argument("--namespace", default="longhorn-system", help="Longhorn namespace (default: longhorn-system)")
        if command == "plan":
            selection = sub.add_mutually_exclusive_group(required=True)
            selection.add_argument("--pv", action="append", help="restore the volume of this PV; repeatable")
            selection.add_argument("--all-static", action="store_true", help="all live Longhorn PVs except pvc-UUID volumes")
            selection.add_argument("--volume", action="append", help="SOURCE[=DESTINATION]; repeatable, including pvc-UUID sources")
            sub.add_argument("--storage-class", help="required with --volume")
            sub.add_argument("--backup-target", default="default", help="Longhorn backup target name (default: default)")
            sub.add_argument("--output", type=Path, required=True, help="new JSON plan file; never overwrites")
        else:
            sub.add_argument("plan", type=Path, help="reviewed JSON plan produced by plan")
    args = parser.parse_args(argv)
    if not args.context.strip():
        parser.error("destination context must not be empty")
    try:
        cluster = Cluster(args.context, args.namespace)
        (plan if args.command == "plan" else apply)(args, cluster)
    except (RestoreError, OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        parser.exit(1, f"ERROR: {error}\n")


if __name__ == "__main__":
    main()
