#!/usr/bin/env -S uv run --script
# /// script
# dependencies = []
# ///
"""Restore Longhorn volumes from backup: plan, apply, then inspect.

1. Run plan to save the latest completed backups in a new local JSON file.
2. Review the sources, destinations, snapshot times, sizes and backup labels.
3. Run apply with that file and the same destination context and namespace.
4. Run inspect on each restored volume once its restore has completed.

Requires kubectl access to Longhorn and an available, synced backup target.
Supports unencrypted Longhorn v1, filesystem, ReadWriteOnce volumes without
backing images. apply creates Volume objects only; inspect briefly attaches a
restored volume read-only. Production PVs, PVCs and workloads are separate.

Use plan --help for restore examples, apply --help for retries and
inspect --help for verification. Also available as: mise run restore-longhorn-volumes --
Offline tests: mise run test-longhorn-restore
"""

import argparse
import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import ROUND_CEILING, Decimal, DecimalException
from pathlib import Path
from typing import NamedTuple
from urllib.parse import parse_qs, urlsplit

INSPECT_IMAGE = "docker.io/library/python:3.15.2-alpine@sha256:0f9552bb28711333da16915ec146f5e6bb44a7fdac9ced15dc17c2e0206346dd"
INSPECT_SCRIPT = Path(__file__).resolve().with_name("longhorn_restore_inspect.py")

PLAN_VERSION = 1
LONGHORN_DRIVER = "driver.longhorn.io"
ENCRYPTION_LABELS = ("volume-encrypted", "longhorn.io/volume-encrypted")
RECURRING_JOB_PREFIXES = ("recurring-job.longhorn.io/", "recurring-job-group.longhorn.io/")
DEFAULT_GROUP_DISABLED = {"recurring-job-group.longhorn.io/default": "disabled"}

VOLUME_NAME = re.compile(r"[a-z0-9]([a-z0-9-]{0,38}[a-z0-9])?")
DYNAMIC_VOLUME_NAME = re.compile(r"pvc-[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", re.IGNORECASE)
QUANTITY = re.compile(
    r"([+-]?(?:\d+(?:\.\d*)?|\.\d+))(Ki|Mi|Gi|Ti|Pi|Ei|[numkMGTPE]|[eE][+-]?\d+)?"
)
BINARY_EXPONENTS = {"Ki": 1, "Mi": 2, "Gi": 3, "Ti": 4, "Pi": 5, "Ei": 6}
DECIMAL_EXPONENTS = {
    "n": -9,
    "u": -6,
    "m": -3,
    "": 0,
    "k": 3,
    "M": 6,
    "G": 9,
    "T": 12,
    "P": 15,
    "E": 18,
}

STORAGE_CLASS_PARAMETERS = {
    "numberOfReplicas",
    "dataEngine",
    "encrypted",
    "backingImage",
    "staleReplicaTimeout",
    "dataLocality",
    "disableRevisionCounter",
    "unmapMarkSnapChainRemoved",
    "backupTargetName",
    "fsType",
    "recurringJobSelector",
    "fromBackup",
}
INTEGER_PARAMETERS = {  # name: (default, minimum, maximum)
    "numberOfReplicas": (None, 1, 20),
    "staleReplicaTimeout": ("2880", 1, 2**31 - 1),
}
CHOICE_PARAMETERS = {
    "dataEngine": {"v1"},
    "dataLocality": {"disabled", "best-effort"},
    "unmapMarkSnapChainRemoved": {"ignored", "disabled", "enabled"},
}


# Inspection attaches through a PV without node affinity, which strict-local
# volumes reject on any node but the one that holds their only replica.
STRICT_LOCAL_UNSUPPORTED = (
    "strict-local data locality is unsupported; restore into a class without it"
)


class RestoreError(Exception):
    pass


class Selection(NamedTuple):
    source: str
    target: str
    storage_class: str

    @classmethod
    def checked(cls, source, target, storage_class):
        return cls(valid_volume_name(source), valid_volume_name(target), storage_class)


@dataclass
class Inventory:
    """Cluster objects that a restore plan is derived from and checked against."""

    volumes: list = field(default_factory=list)
    backups: list = field(default_factory=list)
    backup_volumes: list = field(default_factory=list)
    backup_targets: list = field(default_factory=list)
    storage_classes: list = field(default_factory=list)
    pvs: list = field(default_factory=list)

    def pvs_using(self, volume):
        return [pv for pv in self.pvs if longhorn_handle(pv) == volume]


class Cluster:
    def __init__(self, context, namespace):
        # kubectl would fall back to the current context.
        if not context or not context.strip():
            raise RestoreError("destination context must not be empty")
        self.context = context
        self.namespace = namespace

    def run(self, *args, document=None):
        result = subprocess.run(
            ["kubectl", "--context", self.context, "--request-timeout=30s", *args],
            input=None if document is None else json.dumps(document),
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode:
            raise RestoreError(f"kubectl {' '.join(args)} failed: {result.stderr.strip()}")
        return result.stdout

    def identity(self):
        uid = self.run("get", "namespace", "kube-system", "-o", "jsonpath={.metadata.uid}").strip()
        if not uid:
            raise RestoreError("could not identify destination cluster")
        return uid

    def items(self, resource, namespace=None):
        output = self.run("get", resource, *namespace_args(namespace), "-o", "json")
        return json.loads(output)["items"]

    def inventory(self):
        def longhorn(kind):
            return self.items(f"{kind}.longhorn.io", self.namespace)

        return Inventory(
            volumes=longhorn("volumes"),
            backups=longhorn("backups"),
            backup_volumes=longhorn("backupvolumes"),
            backup_targets=longhorn("backuptargets"),
            storage_classes=self.items("storageclass"),
            pvs=self.items("pv"),
        )

    def exists(self, kind, name, namespace=None):
        found = self.run(
            "get", kind, name, *namespace_args(namespace), "--ignore-not-found", "-o", "name"
        )
        return bool(found.strip())

    def delete(self, kind, name, namespace=None):
        """Delete an object if it exists and wait until it is gone."""
        flags = ("--ignore-not-found", "--wait=false", "--cascade=foreground")
        self.run("delete", kind, name, *namespace_args(namespace), *flags)
        wait_until(
            lambda: not self.exists(kind, name, namespace),
            timeout=300,
            interval=2,
            error=f"{name}: {kind} was not removed; delete it manually",
        )


def namespace_args(namespace):
    return ["-n", namespace] if namespace else []


def wait_until(check, *, timeout, interval, error):
    """Call check until it returns a true value, and return that value."""
    deadline = time.monotonic() + timeout
    while not (result := check()):
        if time.monotonic() > deadline:
            raise RestoreError(error)
        time.sleep(interval)
    return result


def log(message):
    print(message, file=sys.stderr)


def status(obj):
    return obj.get("status") or {}


def is_deleting(obj):
    return bool((obj.get("metadata") or {}).get("deletionTimestamp"))


def longhorn_handle(pv):
    csi = pv["spec"].get("csi") or {}
    return csi.get("volumeHandle") if csi.get("driver") == LONGHORN_DRIVER else None


def is_rwo_filesystem(pv_spec):
    filesystem = pv_spec.get("volumeMode", "Filesystem") == "Filesystem"
    return filesystem and pv_spec.get("accessModes") == ["ReadWriteOnce"]


def is_encrypted(storage_class):
    return (storage_class.get("parameters") or {}).get("encrypted", "false") != "false"


def valid_volume_name(value):
    if not isinstance(value, str) or not VOLUME_NAME.fullmatch(value):
        raise RestoreError(f"invalid Longhorn volume name (maximum 40 characters): {value!r}")
    return value


def parse_timestamp(value):
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise RestoreError(f"snapshot timestamp has no timezone: {value}")
    return result


def to_bytes(quantity):
    """Convert a Kubernetes quantity to bytes, rounding a fraction up."""
    match = QUANTITY.fullmatch(quantity)
    if not match:
        raise RestoreError(f"unsupported PV capacity: {quantity!r}")
    number, suffix = match[1], match[2] or ""
    try:
        if suffix in BINARY_EXPONENTS:
            scale = Decimal(1024) ** BINARY_EXPONENTS[suffix]
        elif suffix in DECIMAL_EXPONENTS:
            scale = Decimal(10) ** DECIMAL_EXPONENTS[suffix]
        else:
            scale = Decimal(10) ** int(suffix[1:])
        value = Decimal(number) * scale
    except DecimalException as error:
        raise RestoreError(f"unsupported PV capacity: {quantity!r}") from error
    if not 0 < value <= 2**63 - 1:
        raise RestoreError(f"PV capacity is outside the positive int64 range: {quantity!r}")
    return int(value.to_integral_value(rounding=ROUND_CEILING))


def select_volumes(args, inventory):
    if args.volume:
        return mapped_selections(args.volume, args.storage_class)
    return pv_selections(inventory.pvs, args.pv, args.all_static)


def mapped_selections(mappings, storage_class):
    if not storage_class:
        raise RestoreError("--volume requires --storage-class (also for dynamic pvc-UUID backups)")
    selections = []
    for mapping in mappings:
        source, separator, target = mapping.partition("=")
        selections.append(Selection.checked(source, target if separator else source, storage_class))
    return selections


def pv_selections(pvs, names, all_static):
    selections, found = [], set()
    for pv in pvs:
        handle = longhorn_handle(pv)
        if handle is None or (names and pv["metadata"]["name"] not in names):
            continue
        if all_static and DYNAMIC_VOLUME_NAME.fullmatch(handle):
            continue
        found.add(pv["metadata"]["name"])
        selections.append(Selection.checked(handle, handle, pv["spec"]["storageClassName"]))
    if missing := set(names or ()) - found:
        raise RestoreError(f"Longhorn PVs not found: {', '.join(sorted(missing))}")
    if not selections:
        raise RestoreError("no Longhorn PVs selected; use --volume when PVs do not exist yet")
    return selections


def check_unique_targets(selections):
    targets = set()
    for selection in selections:
        if selection.target in targets:
            raise RestoreError(f"duplicate destination: {selection.target}")
        targets.add(selection.target)


def available_backup_target(inventory, name):
    targets = [
        target
        for target in inventory.backup_targets
        if target["metadata"]["name"] == name
        and status(target).get("available") is True
        and not is_deleting(target)
    ]
    if len(targets) != 1:
        raise RestoreError(f"backup target {name!r} is not available")
    return targets[0]


def synced_backup_volume(inventory, source, backup_target):
    backup_volumes = [
        backup_volume
        for backup_volume in inventory.backup_volumes
        if backup_volume["spec"].get("volumeName") == source
        and backup_volume["spec"].get("backupTargetName") == backup_target
        and not is_deleting(backup_volume)
    ]
    if len(backup_volumes) != 1:
        raise RestoreError(f"{source}: expected one synced backup volume on {backup_target}")
    return backup_volumes[0]


def completed_backup(inventory, source, backup_target, backup_name=None):
    """The named backup, or else the newest completed backup of the source."""
    backups = [
        backup
        for backup in inventory.backups
        if status(backup).get("volumeName") == source
        and status(backup).get("backupTargetName") == backup_target
        and status(backup).get("state") == "Completed"
        and not status(backup).get("error")
        and not is_deleting(backup)
        and (backup_name is None or backup["metadata"]["name"] == backup_name)
    ]
    if not backups:
        raise RestoreError(f"{source}: no completed backup on {backup_target}")
    return max(
        backups,
        key=lambda backup: (
            parse_timestamp(status(backup)["snapshotCreatedAt"]),
            backup["metadata"]["name"],
        ),
    )


def check_source(inventory, source, target, backup_volume):
    """Reject encrypted sources, even without backup labels, and non-RWO copy sources."""
    if any(
        volume["metadata"]["name"] == source and (volume.get("spec") or {}).get("encrypted")
        for volume in inventory.volumes
    ):
        raise RestoreError(
            f"{source}: source volume is encrypted, including legacy backups without labels"
        )
    source_pvs = inventory.pvs_using(source)
    if source != target and not all(is_rwo_filesystem(pv["spec"]) for pv in source_pvs):
        raise RestoreError(f"{source}: source PV must use Filesystem and ReadWriteOnce")
    source_classes = {status(backup_volume).get("storageClassName")}
    source_classes.update(pv["spec"].get("storageClassName") for pv in source_pvs)
    if any(
        storage_class["metadata"]["name"] in source_classes and is_encrypted(storage_class)
        for storage_class in inventory.storage_classes
    ):
        raise RestoreError(
            f"{source}: source StorageClass is encrypted, including legacy backups without labels"
        )


def check_backup(source, backup, backup_volume, size, target_url):
    recorded_size = status(backup_volume).get("size")
    if recorded_size and int(recorded_size) != size:
        raise RestoreError(
            f"{source}: backup volume size differs from snapshot size; choose a compatible backup"
        )
    for labels in (
        status(backup).get("labels") or {},
        status(backup_volume).get("labels") or {},
    ):
        if (
            any(labels.get(key, "disabled") != "disabled" for key in ENCRYPTION_LABELS)
            or labels.get("longhorn.io/volume-access-mode", "rwo") != "rwo"
        ):
            raise RestoreError(
                f"{source}: encrypted or non-RWO backups are outside this restore procedure"
            )
    url = urlsplit(status(backup)["url"])
    query = parse_qs(url.query)
    if (
        size <= 0
        or not url.scheme
        or not url.netloc
        or location(url) != location(urlsplit(target_url))
        or query.get("volume") != [source]
        or query.get("backup") != [backup["metadata"]["name"]]
    ):
        raise RestoreError(f"{source}: invalid backup URL or size")
    if status(backup).get("volumeBackingImageName"):
        raise RestoreError(f"{source}: backing-image volumes require a separate restore procedure")


def location(url):
    return url.scheme, url.netloc, url.path.rstrip("/")


def longhorn_storage_class(inventory, source, name):
    classes = [
        storage_class
        for storage_class in inventory.storage_classes
        if storage_class["metadata"]["name"] == name
    ]
    if len(classes) != 1 or classes[0].get("provisioner") != LONGHORN_DRIVER:
        raise RestoreError(f"{source}: missing Longhorn StorageClass {name!r}")
    return classes[0]


def storage_class_spec(storage_class):
    """Volume spec fields that Longhorn CSI would derive from the StorageClass."""
    parameters = storage_class.get("parameters") or {}

    def invalid(problem):
        return RestoreError(f"StorageClass {storage_class['metadata']['name']}: {problem}")

    if is_encrypted(storage_class) or parameters.get("backingImage"):
        raise invalid("encrypted or backing-image StorageClasses are unsupported")
    if unknown := set(parameters) - STORAGE_CLASS_PARAMETERS:
        raise invalid(f"unsupported parameters: {', '.join(sorted(unknown))}")
    spec = {"dataEngine": "v1"}
    for key, (default, minimum, maximum) in INTEGER_PARAMETERS.items():
        value = parameters.get(key, default)
        if not (isinstance(value, str) and value.isdecimal() and minimum <= int(value) <= maximum):
            raise invalid(f"{key} must be an explicit integer from {minimum} to {maximum}")
        spec[key] = int(value)
    if parameters.get("dataLocality") == "strict-local":
        raise invalid(STRICT_LOCAL_UNSUPPORTED)
    revision_counter = parameters.get("disableRevisionCounter", "true")
    if revision_counter not in ("true", "false"):
        raise invalid("invalid disableRevisionCounter; use true or false")
    spec["revisionCounterDisabled"] = revision_counter == "true"
    for key, choices in CHOICE_PARAMETERS.items():
        if key in parameters:
            if parameters[key] not in choices:
                raise invalid(f"unsupported {key} value {parameters[key]!r}")
            spec[key] = parameters[key]
    return spec


def check_destination_pv(inventory, target, storage_class, size):
    for pv in inventory.pvs_using(target):
        spec = pv["spec"]
        if (
            to_bytes(spec["capacity"]["storage"]) != size
            or spec.get("storageClassName") != storage_class
            or not is_rwo_filesystem(spec)
        ):
            raise RestoreError(f"{target}: PV capacity, class or access mode differs from restore")


def recurring_job_labels(source, target, backup_volume):
    """Keep the backed-up recurring jobs in place; a renamed copy gets none."""
    if source != target:
        return dict(DEFAULT_GROUP_DISABLED)
    labels = status(backup_volume).get("labels") or {}
    if "VolumeRecurringJobInfo" not in labels:
        raise RestoreError(
            "backup volume lacks VolumeRecurringJobInfo; verify its backup policy first"
        )
    jobs = {}
    for job_name, job in json.loads(labels["VolumeRecurringJobInfo"]).items():
        for group in job.get("fromGroup") or []:
            jobs[f"recurring-job-group.longhorn.io/{group}"] = "enabled"
        if job.get("fromJob"):
            jobs[f"recurring-job.longhorn.io/{job_name}"] = "enabled"
    return jobs or dict(DEFAULT_GROUP_DISABLED)


def plan_entry(inventory, selection, backup_target, namespace, backup_name=None):
    """Check one restore and describe the Volume that apply would create for it."""
    source, target, storage_class = selection
    target_url = available_backup_target(inventory, backup_target)["spec"]["backupTargetURL"]
    backup_volume = synced_backup_volume(inventory, source, backup_target)
    backup = completed_backup(inventory, source, backup_target, backup_name)
    size = int(status(backup)["volumeSize"])
    check_source(inventory, source, target, backup_volume)
    check_backup(source, backup, backup_volume, size, target_url)
    volume_spec = storage_class_spec(longhorn_storage_class(inventory, source, storage_class))
    check_destination_pv(inventory, target, storage_class, size)
    return {
        "source": source,
        "target": target,
        "storageClass": storage_class,
        "backupTarget": backup_target,
        "backupName": backup["metadata"]["name"],
        "snapshotCreatedAt": status(backup)["snapshotCreatedAt"],
        "manifest": {
            "apiVersion": "longhorn.io/v1beta2",
            "kind": "Volume",
            "metadata": {
                "name": target,
                "namespace": namespace,
                "labels": recurring_job_labels(source, target, backup_volume),
            },
            "spec": {
                **volume_spec,
                "size": str(size),
                "accessMode": "rwo",
                "frontend": "blockdev",
                "encrypted": False,
                "Standby": False,
                "restoreVolumeRecurringJob": "disabled",
                "backupTargetName": backup_target,
                "fromBackup": status(backup)["url"],
            },
        },
    }


def plan(args, cluster):
    identity = cluster.identity()
    inventory = cluster.inventory()
    selections = select_volumes(args, inventory)
    check_unique_targets(selections)
    existing = {volume["metadata"]["name"] for volume in inventory.volumes}
    entries = []
    for selection in selections:
        if selection.target in existing:
            log(f"SKIP {selection.target}: already exists; health and data have not been checked")
            continue
        entry = plan_entry(inventory, selection, args.backup_target, cluster.namespace)
        entries.append(entry)
        log(
            f"RESTORE {selection.source} -> {selection.target}: "
            f"{entry['backupName']} ({entry['snapshotCreatedAt']})"
        )
    document = {
        "version": PLAN_VERSION,
        "context": cluster.context,
        "clusterUID": identity,
        "namespace": cluster.namespace,
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "volumes": entries,
    }
    with args.output.open("x") as output:
        output.write(json.dumps(document, indent=2) + "\n")
    log(f"Plan saved to {args.output}; {len(entries)} volume(s). No changes made.")


def matches_manifest(volume, manifest):
    """Whether a Volume keeps every planned field and enables no other recurring job."""
    metadata = volume.get("metadata") or {}
    labels = metadata.get("labels") or {}
    planned = manifest["metadata"]
    enabled_jobs = {
        key
        for key, value in labels.items()
        if value == "enabled" and key.startswith(RECURRING_JOB_PREFIXES)
    }
    return (
        not metadata.get("deletionTimestamp")
        and metadata.get("name") == planned["name"]
        and metadata.get("namespace") == planned["namespace"]
        and contains(volume.get("spec") or {}, manifest["spec"])
        and contains(labels, planned["labels"])
        and enabled_jobs <= planned["labels"].keys()
    )


def contains(actual, expected):
    return all(actual.get(key) == value for key, value in expected.items())


def check_plan_destination(document, cluster):
    if (
        document["version"] != PLAN_VERSION
        or document["context"] != cluster.context
        or document["namespace"] != cluster.namespace
        or not document["clusterUID"]
        or document["clusterUID"] != cluster.identity()
    ):
        raise RestoreError(
            "plan version, context, namespace or cluster identity differs; make a new plan"
        )


def pending_manifests(entries, inventory, namespace):
    """Recheck every planned restore; return the Volumes that do not exist yet."""
    selections = [
        Selection.checked(entry["source"], entry["target"], entry["storageClass"])
        for entry in entries
    ]
    check_unique_targets(selections)
    existing = {volume["metadata"]["name"]: volume for volume in inventory.volumes}
    pending = []
    for entry, selection in zip(entries, selections):
        target = selection.target
        expected = plan_entry(
            inventory, selection, entry["backupTarget"], namespace, entry["backupName"]
        )
        if expected != entry:
            raise RestoreError(
                f"{target}: backup, StorageClass, labels or plan changed; make a new plan"
            )
        if target not in existing:
            pending.append(entry["manifest"])
        elif matches_manifest(existing[target], entry["manifest"]):
            print(f"SKIP {target}: matching restore exists; check restore status separately")
        else:
            raise RestoreError(f"{target}: destination exists and does not match this restore plan")
    return pending


def create_volumes(cluster, manifests):
    # Validate every admission before the first write. Creation is still not atomic.
    for manifest in manifests:
        admitted = json.loads(
            cluster.run("create", "--dry-run=server", "-f", "-", "-o", "json", document=manifest)
        )
        if not matches_manifest(admitted, manifest):
            raise RestoreError(
                f"{manifest['metadata']['name']}: admission changed planned restore fields"
            )
    for manifest in manifests:
        created = json.loads(cluster.run("create", "-f", "-", "-o", "json", document=manifest))
        if not matches_manifest(created, manifest):
            raise RestoreError(
                f"{manifest['metadata']['name']}: created volume differs from plan; "
                "stop and inspect it before retrying"
            )
        print(f"CREATED {manifest['metadata']['name']}; restore is asynchronous")


def apply(args, cluster):
    document = json.loads(args.plan.read_text())
    check_plan_destination(document, cluster)
    inventory = cluster.inventory()
    create_volumes(cluster, pending_manifests(document["volumes"], inventory, cluster.namespace))


def restored_volume(cluster, name):
    volumes = [
        volume
        for volume in cluster.items("volumes.longhorn.io", cluster.namespace)
        if volume["metadata"]["name"] == name
    ]
    if len(volumes) != 1:
        raise RestoreError(f"{name}: Longhorn volume not found")
    volume = volumes[0]
    spec, state = volume.get("spec") or {}, status(volume)
    if not spec.get("fromBackup") or spec.get("encrypted") or spec.get("Standby"):
        raise RestoreError(f"{name}: only unencrypted restored volumes can be inspected")
    if spec.get("dataLocality") == "strict-local":
        raise RestoreError(f"{name}: {STRICT_LOCAL_UNSUPPORTED}")
    if (
        is_deleting(volume)
        or state.get("restoreInitiated") is not True
        or state.get("restoreRequired") is not False
        or state.get("state") != "detached"
    ):
        raise RestoreError(f"{name}: wait until the restore completed and the volume is detached")
    return volume


def check_inspection_conflicts(cluster, volume_name, object_name, pod_namespace):
    for pv in cluster.items("pv"):
        if pv["metadata"]["name"] == object_name or longhorn_handle(pv) == volume_name:
            raise RestoreError(
                f"{volume_name}: a PV already uses this volume or name {object_name}"
            )
    for kind in ("pvc", "job"):
        if cluster.exists(kind, object_name, pod_namespace):
            raise RestoreError(f"{object_name}: {kind} already exists in {pod_namespace}")


def inspect_manifests(volume, size, pod_namespace):
    name = f"{volume}-inspect"
    labels = {
        "app.kubernetes.io/name": "longhorn-restore-inspect",
        "app.kubernetes.io/instance": volume,
    }
    pv = {
        "apiVersion": "v1",
        "kind": "PersistentVolume",
        "metadata": {"name": name, "labels": labels},
        "spec": {
            "capacity": {"storage": str(size)},
            "accessModes": ["ReadWriteOnce"],
            "persistentVolumeReclaimPolicy": "Retain",
            "storageClassName": "",
            "volumeMode": "Filesystem",
            "claimRef": {"namespace": pod_namespace, "name": name},
            "csi": {"driver": LONGHORN_DRIVER, "fsType": "ext4", "volumeHandle": volume},
        },
    }
    pvc = {
        "apiVersion": "v1",
        "kind": "PersistentVolumeClaim",
        "metadata": {"name": name, "namespace": pod_namespace, "labels": labels},
        "spec": {
            "accessModes": ["ReadWriteOnce"],
            "storageClassName": "",
            "volumeName": name,
            "volumeMode": "Filesystem",
            "resources": {"requests": {"storage": str(size)}},
        },
    }
    container = {
        "name": "inspect",
        "image": INSPECT_IMAGE,
        "command": ["python3", "-c", INSPECT_SCRIPT.read_text(), "/volume", "/scratch"],
        "env": [{"name": "SQLITE_TMPDIR", "value": "/scratch"}],
        "resources": {
            "requests": {"cpu": "10m", "memory": "64Mi"},
            "limits": {"memory": "1Gi"},
        },
        # Root with DAC_OVERRIDE reads files owned by any app user; the volume is mounted read-only.
        "securityContext": {
            "runAsUser": 0,
            "runAsNonRoot": False,
            "readOnlyRootFilesystem": True,
            "allowPrivilegeEscalation": False,
            "capabilities": {"drop": ["ALL"], "add": ["DAC_OVERRIDE"]},
        },
        "volumeMounts": [
            {"name": "volume", "mountPath": "/volume", "readOnly": True},
            {"name": "scratch", "mountPath": "/scratch"},
        ],
    }
    job = {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {"name": name, "namespace": pod_namespace, "labels": labels},
        "spec": {
            "backoffLimit": 0,
            "template": {
                "metadata": {"labels": labels},
                "spec": {
                    "restartPolicy": "Never",
                    "automountServiceAccountToken": False,
                    "enableServiceLinks": False,
                    "securityContext": {"seccompProfile": {"type": "RuntimeDefault"}},
                    "containers": [container],
                    "volumes": [
                        {
                            "name": "volume",
                            "persistentVolumeClaim": {"claimName": name, "readOnly": True},
                        },
                        {"name": "scratch", "emptyDir": {"sizeLimit": str(size)}},
                    ],
                },
            },
        },
    }
    return pv, pvc, job


def wait_for_job(cluster, name, namespace, timeout):
    """Wait until the Job finishes and return whether it succeeded."""

    def finished():
        job = json.loads(cluster.run("get", "job", name, "-n", namespace, "-o", "json"))
        job_status = status(job)
        return job_status if job_status.get("succeeded") or job_status.get("failed") else None

    job_status = wait_until(
        finished,
        timeout=timeout,
        interval=5,
        error=f"{name}: no result after {timeout}s; check pod events before retrying",
    )
    return bool(job_status.get("succeeded"))


def inspect(args, cluster):
    volume_name = valid_volume_name(args.volume)
    object_name = f"{volume_name}-inspect"
    volume = restored_volume(cluster, volume_name)
    check_inspection_conflicts(cluster, volume_name, object_name, args.pod_namespace)
    manifests = inspect_manifests(volume_name, int(volume["spec"]["size"]), args.pod_namespace)
    try:
        for manifest in manifests:
            cluster.run("create", "-f", "-", "-o", "name", document=manifest)
        succeeded = wait_for_job(cluster, object_name, args.pod_namespace, args.timeout)
        print(cluster.run("logs", f"job/{object_name}", "-n", args.pod_namespace), end="")
        if not succeeded:
            raise RestoreError(f"{volume_name}: inspection failed; see the output above")
        print(
            f"PASSED {volume_name}: files listed and every SQLite database passed integrity_check"
        )
    finally:
        cluster.delete("job", object_name, args.pod_namespace)
        cluster.delete("pvc", object_name, args.pod_namespace)
        cluster.delete("pv", object_name)
        log(f"Removed {object_name} job, PVC and PV; {volume_name} is kept")


PLAN_EPILOG = """Examples:
  Restore independent test copies while the production volumes stay in use:
    python3 scripts/longhorn_restore.py plan --context homelab \\
      --volume bazarr-config=bazarr-restore-test \\
      --volume prowlarr-config=prowlarr-restore-test \\
      --storage-class longhorn --output /tmp/restore-test.json

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
  Backups made before the longhorn-backup class was removed still record it;
  restore those with --storage-class longhorn.
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
"""

APPLY_EPILOG = """Example (after reviewing the JSON plan):
  python3 scripts/longhorn_restore.py apply --context homelab \\
    /tmp/restore-test.json

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

Then verify the data with inspect once the restore has completed. Do not
start a copied production app or expose restored configuration/credentials.

This command does not create PVs/PVCs, rebind claims, stop workloads or wait
for completion. Preserve production volumes and source backups.
"""

INSPECT_EPILOG = """Example (after the restore of a test copy completed):
  python3 scripts/longhorn_restore.py inspect --context homelab \\
    --volume bazarr-restore-test

Only an unencrypted volume restored from backup can be inspected, once Longhorn
reports restoreInitiated=true, restoreRequired=false and state detached, and
no PV uses it yet. inspect creates a PV, PVC and Job named <volume>-inspect in
--pod-namespace. The Job mounts the volume read-only, prints file counts and
sizes per top-level entry and runs PRAGMA integrity_check on a copy of every
SQLite database, including its WAL. It fails on an empty volume or any
integrity error. Mounting may replay the ext4 journal on the restored copy.

The Job, PVC and PV are always deleted afterwards; the PV uses Retain, so the
Longhorn volume is kept. Check the printed files against the source app before
treating the restore as verified. Delete test volumes separately once no
longer needed.
"""


def kube_context(value):
    if not value.strip():
        raise argparse.ArgumentTypeError("destination context must not be empty")
    return value


def build_parser():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    commands = parser.add_subparsers(dest="command", required=True)

    def add_command(name, handler, summary, epilog):
        command = commands.add_parser(
            name,
            help=summary,
            description=summary,
            epilog=epilog,
            formatter_class=argparse.RawDescriptionHelpFormatter,
        )
        command.set_defaults(handler=handler)
        command.add_argument(
            "--context",
            required=True,
            type=kube_context,
            help="explicit destination kube context",
        )
        command.add_argument(
            "--namespace",
            default="longhorn-system",
            help="Longhorn namespace (default: longhorn-system)",
        )
        return command

    plan_command = add_command(
        "plan",
        plan,
        "Read cluster metadata and save a restore plan; never changes the cluster.",
        PLAN_EPILOG,
    )
    selection = plan_command.add_mutually_exclusive_group(required=True)
    selection.add_argument(
        "--pv", action="append", help="restore the volume of this PV; repeatable"
    )
    selection.add_argument(
        "--all-static",
        action="store_true",
        help="all live Longhorn PVs except pvc-UUID volumes",
    )
    selection.add_argument(
        "--volume",
        action="append",
        help="SOURCE[=DESTINATION]; repeatable, including pvc-UUID sources",
    )
    plan_command.add_argument("--storage-class", help="required with --volume")
    plan_command.add_argument(
        "--backup-target",
        default="default",
        help="Longhorn backup target name (default: default)",
    )
    plan_command.add_argument(
        "--output", type=Path, required=True, help="new JSON plan file; never overwrites"
    )

    apply_command = add_command(
        "apply",
        apply,
        "Create missing volumes from a reviewed plan; changes the cluster.",
        APPLY_EPILOG,
    )
    apply_command.add_argument("plan", type=Path, help="reviewed JSON plan produced by plan")

    inspect_command = add_command(
        "inspect",
        inspect,
        "Check the files and SQLite databases of a restored volume; "
        "creates and removes a temporary Job.",
        INSPECT_EPILOG,
    )
    inspect_command.add_argument(
        "--volume", required=True, help="restored Longhorn volume to inspect"
    )
    inspect_command.add_argument(
        "--pod-namespace",
        default="default",
        help="namespace for the temporary PVC and Job (default: default)",
    )
    inspect_command.add_argument(
        "--timeout", type=int, default=600, help="seconds to wait for the Job (default: 600)"
    )
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    # Malformed plans and cluster objects raise built-in errors; report them the same way.
    try:
        args.handler(args, Cluster(args.context, args.namespace))
    except (RestoreError, OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        parser.exit(1, f"ERROR: {error}\n")


if __name__ == "__main__":
    main()
