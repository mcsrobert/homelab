#!/usr/bin/env -S uv run --script
# /// script
# dependencies = []
# ///
"""Restore missing static Longhorn volumes from their latest NAS backup.

The PersistentVolumes in git (`persistent-volume.yaml` in each app's `app/`
directory) name the Longhorn volume they expect in `spec.csi.volumeHandle`. The
replica count comes from the PV's StorageClass. After a cluster rebuild those Longhorn volumes do
not exist yet. For every static Longhorn PV whose volume is missing, this prints
(or with --apply creates) a `Volume` that restores the latest completed backup
of that volume.

It never touches an existing volume: volumes that exist are skipped, and
creation uses `kubectl create`, which fails instead of overwriting.
"""

import argparse
import json
import re
import subprocess
import sys

NS = "longhorn-system"
UNITS = {"Ki": 2**10, "Mi": 2**20, "Gi": 2**30, "Ti": 2**40}


def kubectl(*args: str, stdin: str | None = None) -> str:
    out = subprocess.run(
        ["kubectl", *args], input=stdin, capture_output=True, text=True
    )
    if out.returncode:
        sys.exit(f"kubectl {' '.join(args)} failed:\n{out.stderr}")
    return out.stdout


def items(*args: str) -> list[dict]:
    return json.loads(kubectl("get", *args, "-o", "json"))["items"]


def to_bytes(quantity: str) -> int:
    m = re.fullmatch(r"(\d+)(Ki|Mi|Gi|Ti)?", quantity)
    if not m:
        sys.exit(f"unsupported quantity {quantity!r}")
    return int(m[1]) * UNITS.get(m[2] or "", 1)


def recurring_job_labels(backup_volume: dict) -> dict[str, str]:
    """Rebuild the recurring-job labels from the info stored with the backup."""
    info = json.loads(
        backup_volume["status"].get("labels", {}).get("VolumeRecurringJobInfo", "{}")
    )
    labels = {}
    for name, job in info.items():
        for group in job.get("fromGroup") or []:
            labels[f"recurring-job-group.longhorn.io/{group}"] = "enabled"
        if job.get("fromJob"):
            labels[f"recurring-job.longhorn.io/{name}"] = "enabled"
    return labels


def manifest(handle: str, size: int, replicas: str, url: str, labels: dict) -> str:
    label_lines = "".join(f"\n    {k}: {v}" for k, v in sorted(labels.items()))
    return f"""apiVersion: longhorn.io/v1beta2
kind: Volume
metadata:
  name: {handle}
  namespace: {NS}
  labels:{label_lines or " {}"}
spec:
  size: "{size}"
  numberOfReplicas: {replicas}
  accessMode: rwo
  frontend: blockdev
  fromBackup: {url}
"""


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--apply", action="store_true", help="create the volumes")
    p.add_argument(
        "--print-all",
        action="store_true",
        help="also render manifests for volumes that already exist (never applied)",
    )
    args = p.parse_args()

    volumes = {v["metadata"]["name"] for v in items("volumes.longhorn.io", "-n", NS)}
    backup_volumes = {
        bv["spec"]["volumeName"]: bv
        for bv in items("backupvolumes.longhorn.io", "-n", NS)
    }
    backups = items("backups.longhorn.io", "-n", NS)
    classes = {sc["metadata"]["name"]: sc for sc in items("storageclass")}

    failed = False
    for pv in items("pv"):
        csi = pv["spec"].get("csi", {})
        handle = csi.get("volumeHandle", "")
        # Dynamically provisioned volumes (pvc-<uuid>) are out of scope here.
        if csi.get("driver") != "driver.longhorn.io" or handle.startswith("pvc-"):
            continue
        exists = handle in volumes
        if exists and not args.print_all:
            print(f"ok       {handle}: volume exists, skipping")
            continue

        bv = backup_volumes.get(handle)
        done = [
            b
            for b in backups
            if b["status"].get("volumeName") == handle
            and b["status"].get("state") == "Completed"
        ]
        if not bv or not done:
            print(f"MISSING  {handle}: no completed backup on the backup target")
            failed = failed or not exists
            continue
        latest = max(done, key=lambda b: b["status"]["snapshotCreatedAt"])
        size = int(latest["status"]["volumeSize"])
        pv_size = to_bytes(pv["spec"]["capacity"]["storage"])
        if size != pv_size:
            print(
                f"MISMATCH {handle}: backup is {size} bytes, PV says {pv_size}; "
                "fix the PV/PVC in git first"
            )
            failed = True
            continue

        sc = classes.get(pv["spec"].get("storageClassName", ""), {})
        replicas = sc.get("parameters", {}).get("numberOfReplicas", "3")
        yaml = manifest(
            handle,
            size,
            replicas,
            latest["status"]["url"],
            recurring_job_labels(bv),
        )
        when = latest["status"]["snapshotCreatedAt"]
        verb = "exists (not applied)" if exists else "restore"
        print(f"{verb:8} {handle}: backup {latest['metadata']['name']} from {when}")
        if args.print_all or not args.apply:
            print(yaml)
        if args.apply and not exists:
            kubectl("create", "-f", "-", stdin=yaml)
            print(f"created  {handle}")

    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
