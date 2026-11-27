"""Offline restore safety checks; never contact Kubernetes or read secrets."""

import argparse
from copy import deepcopy
import importlib.util
import io
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

import longhorn_restore as restore


def backup(source="bazarr-config", backup_name="backup-old", when="2026-11-22T00:00:00Z", target="default"):
    return {
        "metadata": {"name": backup_name},
        "status": {
            "volumeName": source, "backupTargetName": target, "state": "Completed",
            "snapshotCreatedAt": when, "volumeSize": str(2**30),
            "url": f"nfs://nas/longhorn?backup={backup_name}&volume={source}",
        },
    }


def add_source(data, source, storage_class="longhorn"):
    data.backups.append(backup(source))
    data.backup_volumes.append({
        "spec": {"volumeName": source, "backupTargetName": "default"},
        "status": {"size": str(2**30), "labels": {"VolumeRecurringJobInfo": json.dumps({
            "nightly": {"fromGroup": ["backup"], "fromJob": True},
        })}},
    })
    data.pvs.append({
        "metadata": {"name": source},
        "spec": {
            "csi": {"driver": "driver.longhorn.io", "volumeHandle": source},
            "capacity": {"storage": "1Gi"}, "storageClassName": storage_class,
            "accessModes": ["ReadWriteOnce"],
        },
    })


def resources():
    data = restore.Inventory(
        backup_targets=[{
            "metadata": {"name": "default"}, "spec": {"backupTargetURL": "nfs://nas/longhorn"},
            "status": {"available": True},
        }],
        storage_classes=[{
            "metadata": {"name": name}, "provisioner": "driver.longhorn.io",
            "parameters": {"numberOfReplicas": "3"},
        } for name in ("longhorn", "longhorn-no-backup")],
    )
    add_source(data, "bazarr-config")
    return data


class RestoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "restore.json"
        self.data = resources()
        self.cluster = Mock(spec=restore.Cluster)
        self.cluster.context = "homelab"
        self.cluster.namespace = "longhorn-system"
        self.cluster.identity.return_value = "cluster-uid"
        self.cluster.inventory.side_effect = lambda: deepcopy(self.data)
        self.cluster.run.side_effect = lambda *args, document=None: json.dumps(document)
        self.addCleanup(patch.stopall)
        patch("sys.stdout", new=io.StringIO()).start()
        patch("sys.stderr", new=io.StringIO()).start()

    def plan(self, **options):
        args = dict(volume=None, pv=["bazarr-config"], all_static=False,
                    storage_class=None, backup_target="default", output=self.path)
        args.update(options)
        restore.plan(argparse.Namespace(**args), self.cluster)
        return json.loads(self.path.read_text())

    def apply(self):
        restore.apply(argparse.Namespace(plan=self.path), self.cluster)

    def entry(self, target="bazarr-config"):
        selection = restore.Selection("bazarr-config", target, "longhorn")
        return restore.plan_entry(self.data, selection, "default", "longhorn-system")

    def test_plan_only_reads_and_preserves_backup_policy(self):
        plan = self.plan()
        self.cluster.run.assert_not_called()
        self.assertEqual(plan["context"], "homelab")
        self.assertEqual(plan["clusterUID"], "cluster-uid")
        manifest = plan["volumes"][0]["manifest"]
        self.assertEqual(manifest["metadata"]["labels"], {
            "recurring-job-group.longhorn.io/backup": "enabled",
            "recurring-job.longhorn.io/nightly": "enabled",
        })
        self.assertEqual(manifest["spec"]["size"], str(2**30))
        self.assertIsInstance(manifest["spec"]["numberOfReplicas"], int)
        self.assertEqual(manifest["spec"]["restoreVolumeRecurringJob"], "disabled")

    def test_clone_uses_existing_source_but_new_destination(self):
        self.data.volumes = [{"metadata": {"name": "bazarr-config"}}]
        plan = self.plan(volume=["bazarr-config=bazarr-restore-test"], pv=None,
                         storage_class="longhorn")
        item = plan["volumes"][0]
        self.assertEqual(item["target"], "bazarr-restore-test")
        self.assertEqual(item["manifest"]["metadata"]["name"], "bazarr-restore-test")
        self.assertIn("volume=bazarr-config", item["manifest"]["spec"]["fromBackup"])
        self.assertEqual(item["manifest"]["metadata"]["labels"], {
            "recurring-job-group.longhorn.io/default": "disabled",
        })
        self.assertEqual(item["manifest"]["spec"]["restoreVolumeRecurringJob"], "disabled")
        self.cluster.run.assert_not_called()

    def test_empty_recovery_job_policy_disables_default_recurring_group(self):
        self.data.backup_volumes[0]["status"]["labels"]["VolumeRecurringJobInfo"] = "{}"
        item = self.entry()
        self.assertEqual(item["manifest"]["metadata"]["labels"], {
            "recurring-job-group.longhorn.io/default": "disabled",
        })
        self.assertEqual(item["manifest"]["spec"]["restoreVolumeRecurringJob"], "disabled")

    def test_clone_does_not_require_or_copy_recurring_job_metadata(self):
        for metadata in ({}, {"VolumeRecurringJobInfo": "invalid-json"}):
            with self.subTest(metadata=metadata):
                self.data.backup_volumes[0]["status"]["labels"] = metadata
                item = self.entry(target="bazarr-restore-test")
                self.assertEqual(item["manifest"]["metadata"]["labels"], {
                    "recurring-job-group.longhorn.io/default": "disabled",
                })

    def test_clone_apply_keeps_automatic_default_recurring_group_disabled(self):
        plan = self.plan(volume=["bazarr-config=bazarr-restore-test"], pv=None,
                         storage_class="longhorn")
        planned = plan["volumes"][0]["manifest"]
        default_group = "recurring-job-group.longhorn.io/default"
        self.assertEqual(planned["metadata"]["labels"], {default_group: "disabled"})

        def admit(*args, document=None):
            admitted = deepcopy(document)
            admitted["metadata"]["labels"].setdefault(default_group, "enabled")
            return json.dumps(admitted)

        self.cluster.run.side_effect = admit
        self.apply()
        self.assertEqual(self.cluster.run.call_count, 2)
        created = self.cluster.run.call_args_list[-1].kwargs["document"]
        self.assertEqual(created["metadata"]["labels"], {default_group: "disabled"})

    def test_clone_admission_enabling_unplanned_recurring_jobs_prevents_creation(self):
        self.plan(volume=["bazarr-config=bazarr-restore-test"], pv=None,
                  storage_class="longhorn")
        for label in ("recurring-job-group.longhorn.io/backup", "recurring-job.longhorn.io/nightly"):
            with self.subTest(label=label):
                def admit(*args, document=None):
                    admitted = deepcopy(document)
                    admitted["metadata"]["labels"][label] = "enabled"
                    return json.dumps(admitted)

                self.cluster.run.reset_mock()
                self.cluster.run.side_effect = admit
                with self.assertRaisesRegex(restore.RestoreError, "admission changed planned restore fields"):
                    self.apply()
                self.assertEqual(self.cluster.run.call_count, 1)
                self.assertIn("--dry-run=server", self.cluster.run.call_args.args)

    def test_clone_retry_with_extra_enabled_recurring_job_is_rejected(self):
        plan = self.plan(volume=["bazarr-config=bazarr-restore-test"], pv=None,
                         storage_class="longhorn")
        for label in ("recurring-job-group.longhorn.io/backup", "recurring-job.longhorn.io/nightly"):
            with self.subTest(label=label):
                volume = deepcopy(plan["volumes"][0]["manifest"])
                volume["metadata"]["labels"][label] = "enabled"
                self.data.volumes = [volume]
                with self.assertRaisesRegex(restore.RestoreError, "destination exists"):
                    self.apply()
                self.cluster.run.assert_not_called()

    def test_dynamic_source_can_restore_without_live_pv(self):
        source = "pvc-12345678-1234-1234-1234-123456789abc"
        add_source(self.data, source)
        self.data.pvs = []
        plan = self.plan(volume=[f"{source}=openbao-recovered"], pv=None, storage_class="longhorn")
        self.assertEqual(plan["volumes"][0]["source"], source)

    def test_existing_destinations_are_skipped_without_claiming_health(self):
        self.data.volumes = [{"metadata": {"name": "bazarr-config"}}]
        self.assertEqual(self.plan()["volumes"], [])
        self.assertIn("health and data have not been checked", restore.sys.stderr.getvalue())
        self.cluster.run.assert_not_called()

    def test_statusless_and_incomplete_backups_do_not_hide_completed_backup(self):
        self.data.backups += [
            {"spec": {}}, {"status": {}},
            {"status": {"volumeName": "bazarr-config", "backupTargetName": "default", "state": "InProgress"}},
        ]
        self.assertEqual(self.plan()["volumes"][0]["backupName"], "backup-old")

    def test_no_completed_backup_produces_no_plan(self):
        self.data.backups = [{"spec": {}}]
        with self.assertRaisesRegex(restore.RestoreError, "no completed backup"):
            self.plan()
        self.assertFalse(self.path.exists())
        self.cluster.run.assert_not_called()

    def test_newest_backup_is_chosen_by_instant_instead_of_lexical_order(self):
        self.data.backups = [
            backup(backup_name="newer", when="2026-11-22T01:00:00+00:00"),
            backup(backup_name="older", when="2026-11-22T02:00:00+02:00"),
        ]
        self.assertEqual(self.plan()["volumes"][0]["backupName"], "newer")

    def test_other_backup_targets_are_never_selected(self):
        self.data.backups.append(
            backup(backup_name="newer-elsewhere", when="2026-11-24T00:00:00Z", target="other"))
        self.data.backup_volumes.append({
            "spec": {"volumeName": "bazarr-config", "backupTargetName": "other"},
        })
        self.assertEqual(self.plan()["volumes"][0]["backupName"], "backup-old")

    def test_unavailable_or_deleting_backup_target_is_rejected(self):
        original = deepcopy(self.data)
        for section, key, value in (("status", "available", False),
                                    ("metadata", "deletionTimestamp", "2026-11-25T00:00:00Z")):
            with self.subTest(key=key):
                self.data = deepcopy(original)
                self.data.backup_targets[0][section][key] = value
                with self.assertRaisesRegex(restore.RestoreError, "not available"):
                    self.plan()

    def test_wrong_backup_url_is_rejected(self):
        for url in ("nfs://other/longhorn?backup=x&volume=bazarr-config",
                    "nfs://nas/other?backup=x&volume=bazarr-config",
                    "nfs://nas/longhorn?backup=x&volume=other",
                    "nfs://nas/longhorn?volume=bazarr-config",
                    "nfs://nas/longhorn?backup=other-backup&volume=bazarr-config"):
            with self.subTest(url=url):
                self.data.backups[0]["status"]["url"] = url
                with self.assertRaisesRegex(restore.RestoreError, "invalid backup URL"):
                    self.entry()

    def test_missing_storage_class_never_defaults_replicas(self):
        self.data.storage_classes = []
        with self.assertRaisesRegex(restore.RestoreError, "missing Longhorn StorageClass"):
            self.plan()
        self.cluster.run.assert_not_called()

    def test_storage_class_missing_replica_count_reports_class_and_parameter(self):
        del self.data.storage_classes[0]["parameters"]["numberOfReplicas"]
        with self.assertRaisesRegex(restore.RestoreError, r"longhorn.*numberOfReplicas|numberOfReplicas.*longhorn"):
            self.plan()
        self.assertFalse(self.path.exists())
        self.cluster.run.assert_not_called()

    def test_supported_storage_class_settings_are_preserved_with_volume_types(self):
        self.data.storage_classes[0]["parameters"].update({
            "staleReplicaTimeout": "30", "disableRevisionCounter": "true",
            "dataLocality": "best-effort", "unmapMarkSnapChainRemoved": "enabled",
        })
        spec = self.entry()["manifest"]["spec"]
        self.assertEqual(spec["staleReplicaTimeout"], 30)
        self.assertIs(type(spec["staleReplicaTimeout"]), int)
        self.assertIs(spec["revisionCounterDisabled"], True)
        self.assertEqual(spec["dataLocality"], "best-effort")
        self.assertEqual(spec["unmapMarkSnapChainRemoved"], "enabled")
        self.data.storage_classes[0]["parameters"]["disableRevisionCounter"] = "false"
        self.assertIs(self.entry()["manifest"]["spec"]["revisionCounterDisabled"], False)

    def test_malformed_or_unsupported_storage_class_settings_are_rejected(self):
        original = deepcopy(self.data.storage_classes[0]["parameters"])
        for key, value in (("staleReplicaTimeout", "thirty"),
                           ("disableRevisionCounter", "maybe"),
                           ("dataLocality", "nonsense"),
                           ("unmapMarkSnapChainRemoved", "nonsense"),
                           ("unsupportedParameter", "true")):
            with self.subTest(key=key):
                self.data.storage_classes[0]["parameters"] = dict(original, **{key: value})
                with self.assertRaises(restore.RestoreError):
                    self.plan()
                self.assertFalse(self.path.exists())
                self.cluster.run.assert_not_called()

    def test_strict_local_storage_class_is_rejected(self):
        self.data.storage_classes[0]["parameters"].update(
            {"numberOfReplicas": "1", "dataLocality": "strict-local"})
        with self.assertRaisesRegex(restore.RestoreError, "strict-local"):
            self.plan()
        self.assertFalse(self.path.exists())
        self.cluster.run.assert_not_called()

    def test_apply_rejects_supported_storage_class_setting_drift_before_any_write(self):
        self.data.storage_classes[0]["parameters"]["staleReplicaTimeout"] = "30"
        self.plan()
        self.data.storage_classes[0]["parameters"]["staleReplicaTimeout"] = "60"
        with self.assertRaisesRegex(restore.RestoreError, "plan changed"):
            self.apply()
        self.cluster.run.assert_not_called()

    def test_backup_volume_and_snapshot_size_must_match(self):
        self.data.backup_volumes[0]["status"]["size"] = str(2**31)
        with self.assertRaisesRegex(restore.RestoreError, "backup volume size differs"):
            self.plan()
        self.assertFalse(self.path.exists())
        self.cluster.run.assert_not_called()

    def test_encrypted_and_non_rwo_backup_labels_are_rejected(self):
        for key, value in (("longhorn.io/volume-encrypted", "enabled"),
                           ("longhorn.io/volume-access-mode", "rwx")):
            with self.subTest(key=key):
                self.data.backups[0]["status"]["labels"] = {key: value}
                with self.assertRaisesRegex(restore.RestoreError, "encrypted or non-RWO backups"):
                    self.plan()
                self.assertFalse(self.path.exists())
                self.cluster.run.assert_not_called()

    def test_encryption_label_on_snapshot_or_backup_volume_is_rejected(self):
        original = deepcopy(self.data)
        for resource in ("backups", "backup_volumes"):
            for key in ("volume-encrypted", "longhorn.io/volume-encrypted"):
                with self.subTest(resource=resource, key=key):
                    self.data = deepcopy(original)
                    getattr(self.data, resource)[0]["status"].setdefault("labels", {})[key] = "enabled"
                    with self.assertRaisesRegex(restore.RestoreError, "encrypted"):
                        self.plan()
                    self.assertFalse(self.path.exists())
                    self.cluster.run.assert_not_called()

    def test_missing_and_disabled_encryption_labels_allow_restore(self):
        original = deepcopy(self.data)
        self.assertIs(self.entry()["manifest"]["spec"]["encrypted"], False)
        for resource in ("backups", "backup_volumes"):
            for key in ("volume-encrypted", "longhorn.io/volume-encrypted"):
                with self.subTest(resource=resource, key=key):
                    self.data = deepcopy(original)
                    getattr(self.data, resource)[0]["status"].setdefault("labels", {})[key] = "disabled"
                    self.assertIs(self.entry()["manifest"]["spec"]["encrypted"], False)

    def test_encrypted_and_backing_image_storage_classes_are_rejected(self):
        for key, value in (("encrypted", "true"), ("backingImage", "special-image")):
            with self.subTest(key=key):
                self.data.storage_classes[0]["parameters"] = {"numberOfReplicas": "3", key: value}
                with self.assertRaisesRegex(restore.RestoreError, "encrypted|backing-image"):
                    self.plan()
                self.assertFalse(self.path.exists())
                self.cluster.run.assert_not_called()

    def test_mismatched_pv_capacity_class_and_access_are_rejected(self):
        for key, value in (("capacity", {"storage": "2Gi"}),
                           ("storageClassName", "longhorn-no-backup"),
                           ("accessModes", ["ReadWriteMany"]), ("volumeMode", "Block")):
            with self.subTest(key=key):
                data = deepcopy(self.data)
                self.data.pvs[0]["spec"][key] = value
                with self.assertRaisesRegex(restore.RestoreError, "PV capacity, class or access mode"):
                    self.entry()
                self.data = data

    def test_missing_recurring_job_metadata_requires_operator_attention(self):
        self.data.backup_volumes[0]["status"]["labels"] = {}
        with self.assertRaisesRegex(restore.RestoreError, "backup policy"):
            self.plan()

    def test_second_invalid_volume_leaves_no_partial_plan(self):
        add_source(self.data, "prowlarr-config", "longhorn-no-backup")
        self.data.backups = self.data.backups[:1]
        with self.assertRaisesRegex(restore.RestoreError, "prowlarr-config: no completed backup"):
            self.plan(pv=["bazarr-config", "prowlarr-config"])
        self.assertFalse(self.path.exists())
        self.cluster.run.assert_not_called()

    def test_existing_plan_is_never_overwritten(self):
        self.path.write_text("previous reviewable plan\n")
        with self.assertRaises(FileExistsError):
            self.plan()
        self.assertEqual(self.path.read_text(), "previous reviewable plan\n")

    def test_duplicate_destinations_are_rejected(self):
        with self.assertRaisesRegex(restore.RestoreError, "duplicate destination"):
            self.plan(volume=["bazarr-config=copy", "bazarr-config=copy"], pv=None,
                      storage_class="longhorn")
        self.assertFalse(self.path.exists())

    def test_empty_destination_is_never_read_as_in_place_restore(self):
        with self.assertRaisesRegex(restore.RestoreError, "invalid Longhorn volume name"):
            self.plan(volume=["bazarr-config="], pv=None, storage_class="longhorn")
        self.assertFalse(self.path.exists())

    def test_duplicate_plan_destinations_stop_apply_before_any_write(self):
        document = self.plan()
        document["volumes"].append(deepcopy(document["volumes"][0]))
        self.path.write_text(json.dumps(document))
        with self.assertRaisesRegex(restore.RestoreError, "duplicate destination"):
            self.apply()
        self.cluster.run.assert_not_called()

    def test_unknown_pv_is_rejected(self):
        with self.assertRaisesRegex(restore.RestoreError, "Longhorn PVs not found"):
            self.plan(pv=["missing"])

    def test_all_static_omits_dynamic_and_non_longhorn_pvs(self):
        add_source(self.data, "pvc-12345678-1234-1234-1234-123456789abc")
        self.data.pvs.append({"metadata": {"name": "nfs"}, "spec": {"nfs": {}}})
        self.assertEqual(len(self.plan(pv=None, all_static=True)["volumes"]), 1)

    def test_all_static_keeps_human_named_pvc_prefix(self):
        add_source(self.data, "pvc-appdata")
        add_source(self.data, "pvc-12345678-1234-1234-1234-123456789abc")
        plan = self.plan(pv=None, all_static=True)
        self.assertEqual({item["source"] for item in plan["volumes"]}, {"bazarr-config", "pvc-appdata"})

    def test_legacy_encrypted_source_volume_is_rejected_for_unencrypted_copy(self):
        self.data.volumes = [{"metadata": {"name": "bazarr-config"},
                              "spec": {"encrypted": True}}]
        with self.assertRaisesRegex(restore.RestoreError, "source volume is encrypted"):
            self.plan(volume=["bazarr-config=test-copy"], pv=None, storage_class="longhorn-no-backup")
        self.assertFalse(self.path.exists())
        self.cluster.run.assert_not_called()

    def test_legacy_encrypted_source_class_is_rejected_when_volume_is_lost(self):
        self.data.pvs = []
        self.data.backup_volumes[0]["status"]["storageClassName"] = "longhorn"
        self.data.storage_classes[0]["parameters"]["encrypted"] = "true"
        with self.assertRaisesRegex(restore.RestoreError, "source StorageClass is encrypted"):
            self.plan(volume=["bazarr-config=test-copy"], pv=None, storage_class="longhorn-no-backup")
        self.assertFalse(self.path.exists())
        self.cluster.run.assert_not_called()

    def test_legacy_source_pv_class_is_checked_without_recorded_backup_class(self):
        self.data.storage_classes[0]["parameters"]["encrypted"] = "true"
        with self.assertRaisesRegex(restore.RestoreError, "source StorageClass is encrypted"):
            self.plan(volume=["bazarr-config=test-copy"], pv=None, storage_class="longhorn-no-backup")

    def test_raw_block_source_is_rejected_for_renamed_filesystem_copy(self):
        self.data.pvs[0]["spec"]["volumeMode"] = "Block"
        with self.assertRaisesRegex(restore.RestoreError, "source PV must use Filesystem"):
            self.plan(volume=["bazarr-config=test-copy"], pv=None, storage_class="longhorn")
        self.assertFalse(self.path.exists())

    def test_deleting_newer_backup_is_excluded_from_selection(self):
        deleting = backup(backup_name="deleting", when="2026-11-24T00:00:00Z")
        deleting["metadata"]["deletionTimestamp"] = "2026-11-25T00:00:00Z"
        self.data.backups.append(deleting)
        self.assertEqual(self.plan()["volumes"][0]["backupName"], "backup-old")

    def test_deleting_pinned_backup_or_backup_volume_stops_apply(self):
        self.plan()
        for resource in ("backups", "backup_volumes"):
            with self.subTest(resource=resource):
                metadata = getattr(self.data, resource)[0].setdefault("metadata", {})
                metadata["deletionTimestamp"] = "2026-11-25T00:00:00Z"
                with self.assertRaises(restore.RestoreError):
                    self.apply()
                self.cluster.run.assert_not_called()
                del metadata["deletionTimestamp"]

    def test_deleting_backup_volume_prevents_planning(self):
        self.data.backup_volumes[0]["metadata"] = {"deletionTimestamp": "2026-11-25T00:00:00Z"}
        with self.assertRaisesRegex(restore.RestoreError, "expected one synced backup volume"):
            self.plan()
        self.assertFalse(self.path.exists())

    def test_decimal_pv_capacity_matches_exact_backup_bytes(self):
        self.data.pvs[0]["spec"]["capacity"]["storage"] = "32768M"
        self.data.backups[0]["status"]["volumeSize"] = "32768000000"
        self.data.backup_volumes[0]["status"]["size"] = "32768000000"
        self.assertEqual(self.plan()["volumes"][0]["manifest"]["spec"]["size"], "32768000000")

    def test_apply_uses_pinned_backup_even_when_newer_backup_appears(self):
        plan = self.plan()
        self.data.backups.append(
            backup(backup_name="newer", when="2026-11-24T00:00:00Z"))
        self.apply()
        calls = self.cluster.run.call_args_list
        self.assertEqual([call.args for call in calls], [
            ("create", "--dry-run=server", "-f", "-", "-o", "json"), ("create", "-f", "-", "-o", "json"),
        ])
        self.assertEqual(calls[1].kwargs["document"], plan["volumes"][0]["manifest"])

    def test_apply_validates_entire_batch_before_any_admission_or_write(self):
        add_source(self.data, "prowlarr-config", "longhorn-no-backup")
        self.plan(pv=["bazarr-config", "prowlarr-config"])
        self.data.backups = self.data.backups[:1]
        with self.assertRaisesRegex(restore.RestoreError, "no completed backup"):
            self.apply()
        self.cluster.run.assert_not_called()

    def test_server_admission_checks_entire_batch_before_creating_anything(self):
        add_source(self.data, "prowlarr-config", "longhorn-no-backup")
        self.plan(pv=["bazarr-config", "prowlarr-config"])
        self.apply()
        calls = self.cluster.run.call_args_list
        self.assertEqual([call.args[1] for call in calls], ["--dry-run=server", "--dry-run=server", "-f", "-f"])

    def test_failed_admission_never_creates_a_volume(self):
        add_source(self.data, "prowlarr-config", "longhorn-no-backup")
        plan = self.plan(pv=["bazarr-config", "prowlarr-config"])
        self.cluster.run.side_effect = [json.dumps(plan["volumes"][0]["manifest"]),
                                        restore.RestoreError("admission denied")]
        with self.assertRaisesRegex(restore.RestoreError, "admission denied"):
            self.apply()
        self.assertTrue(all("--dry-run=server" in call.args for call in self.cluster.run.call_args_list))

    def test_admission_mutating_planned_fields_or_labels_prevents_all_writes(self):
        add_source(self.data, "prowlarr-config", "longhorn-no-backup")
        plan = self.plan(pv=["bazarr-config", "prowlarr-config"])
        changes = [("spec", "frontend", "iscsi"), ("spec", "backupTargetName", "other"),
                   ("spec", "fromBackup", "nfs://other/backup"), ("spec", "size", str(2**31)),
                   ("spec", "encrypted", True), ("spec", "numberOfReplicas", 1),
                   ("metadata", "labels", {})]
        for section, key, value in changes:
            with self.subTest(key=key):
                admitted = deepcopy(plan["volumes"][1]["manifest"])
                admitted[section][key] = value
                self.cluster.run.reset_mock()
                self.cluster.run.side_effect = [json.dumps(plan["volumes"][0]["manifest"]),
                                                json.dumps(admitted)]
                with self.assertRaisesRegex(restore.RestoreError, "admission changed planned restore fields"):
                    self.apply()
                self.assertEqual(self.cluster.run.call_count, 2)
                self.assertTrue(all("--dry-run=server" in call.args for call in self.cluster.run.call_args_list))

    def test_admission_additional_defaults_are_tolerated(self):
        self.plan()

        def admit(*args, document=None):
            admitted = deepcopy(document)
            admitted["metadata"]["labels"]["longhornvolume"] = "bazarr-config"
            admitted["metadata"]["uid"] = "server-assigned-uid"
            admitted["spec"]["staleReplicaTimeout"] = 2880
            admitted["status"] = {"state": "detached"}
            return json.dumps(admitted)

        self.cluster.run.side_effect = admit
        self.apply()
        self.assertEqual(self.cluster.run.call_count, 2)
        self.assertEqual(self.cluster.run.call_args_list[-1].args, ("create", "-f", "-", "-o", "json"))

    def test_changed_real_create_response_stops_remaining_batch(self):
        add_source(self.data, "prowlarr-config", "longhorn-no-backup")
        plan = self.plan(pv=["bazarr-config", "prowlarr-config"])
        first, second = [item["manifest"] for item in plan["volumes"]]
        changed = deepcopy(first)
        changed["spec"]["size"] = str(2**31)
        self.cluster.run.side_effect = [json.dumps(first), json.dumps(second), json.dumps(changed)]
        with self.assertRaisesRegex(restore.RestoreError, "created volume differs from plan"):
            self.apply()
        calls = self.cluster.run.call_args_list
        self.assertEqual(len(calls), 3)
        self.assertTrue(all("--dry-run=server" in call.args for call in calls[:2]))
        self.assertNotIn("--dry-run=server", calls[2].args)
        self.assertEqual(calls[2].kwargs["document"], first)
        self.assertNotIn("CREATED", restore.sys.stdout.getvalue())

    def test_standby_admission_and_retry_are_rejected(self):
        plan = self.plan()
        standby = deepcopy(plan["volumes"][0]["manifest"])
        self.assertIs(standby["spec"]["Standby"], False)
        standby["spec"]["Standby"] = True
        self.cluster.run.side_effect = [json.dumps(standby)]
        with self.assertRaisesRegex(restore.RestoreError, "admission changed planned restore fields"):
            self.apply()
        self.cluster.run.assert_called_once()
        self.assertIn("--dry-run=server", self.cluster.run.call_args.args)
        self.cluster.run.reset_mock()
        self.data.volumes = [standby]
        with self.assertRaisesRegex(restore.RestoreError, "destination exists"):
            self.apply()
        self.cluster.run.assert_not_called()

    def test_destination_appearing_after_plan_is_never_overwritten(self):
        self.plan()
        self.data.volumes = [{"metadata": {"name": "bazarr-config"}, "spec": {}}]
        with self.assertRaisesRegex(restore.RestoreError, "destination exists"):
            self.apply()
        self.cluster.run.assert_not_called()

    def test_matching_restore_is_skipped_on_retry(self):
        plan = self.plan()
        self.data.volumes = [deepcopy(plan["volumes"][0]["manifest"])]
        self.apply()
        self.cluster.run.assert_not_called()

    def test_retry_rejects_existing_volume_with_changed_fields_or_backup_labels(self):
        plan = self.plan()
        for section, key, value in (("spec", "frontend", "iscsi"),
                                    ("spec", "backupTargetName", "other"),
                                    ("spec", "encrypted", True),
                                    ("metadata", "labels", {})):
            with self.subTest(key=key):
                volume = deepcopy(plan["volumes"][0]["manifest"])
                volume[section][key] = value
                self.data.volumes = [volume]
                with self.assertRaisesRegex(restore.RestoreError, "destination exists|source volume is encrypted"):
                    self.apply()
                self.cluster.run.assert_not_called()

    def test_deleting_restore_is_not_treated_as_successful_retry(self):
        plan = self.plan()
        volume = deepcopy(plan["volumes"][0]["manifest"])
        volume["metadata"]["deletionTimestamp"] = "2026-11-24T00:00:00Z"
        self.data.volumes = [volume]
        with self.assertRaisesRegex(restore.RestoreError, "destination exists"):
            self.apply()
        self.cluster.run.assert_not_called()

    def test_context_cluster_uid_and_namespace_must_match_saved_plan(self):
        self.plan()
        for attribute, value in (("context", "other"), ("namespace", "other")):
            with self.subTest(attribute=attribute):
                previous = getattr(self.cluster, attribute)
                setattr(self.cluster, attribute, value)
                with self.assertRaisesRegex(restore.RestoreError, "cluster identity differs"):
                    self.apply()
                setattr(self.cluster, attribute, previous)
        self.cluster.identity.return_value = "rebuilt-cluster-uid"
        with self.assertRaisesRegex(restore.RestoreError, "cluster identity differs"):
            self.apply()
        self.cluster.run.assert_not_called()

    def test_tampered_manifest_or_changed_storage_class_is_rejected(self):
        original = self.plan()
        modified = deepcopy(original)
        modified["volumes"][0]["manifest"]["kind"] = "Secret"
        self.path.write_text(json.dumps(modified))
        with self.assertRaisesRegex(restore.RestoreError, "plan changed"):
            self.apply()
        self.path.write_text(json.dumps(original))
        self.data.storage_classes[0]["parameters"]["numberOfReplicas"] = "2"
        with self.assertRaisesRegex(restore.RestoreError, "plan changed"):
            self.apply()
        self.cluster.run.assert_not_called()


class FakeClock:
    """Stands in for the time module; sleeping advances the clock instantly."""

    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class InspectTests(unittest.TestCase):
    def setUp(self):
        self.volume = {
            "metadata": {"name": "bazarr-restore-test"},
            "spec": {"fromBackup": "nfs://nas/longhorn?backup=b&volume=bazarr-config",
                     "size": str(2**30), "encrypted": False, "Standby": False},
            "status": {"restoreInitiated": True, "restoreRequired": False, "state": "detached"},
        }
        self.pvs = []
        self.job_status = {"succeeded": 1}
        self.leftover = None
        self.calls = []
        self.cluster = restore.Cluster("homelab", "longhorn-system")
        self.addCleanup(patch.stopall)
        patch.object(self.cluster, "run", side_effect=self.kubectl).start()
        patch.object(restore, "time", FakeClock()).start()
        patch("sys.stdout", new=io.StringIO()).start()
        patch("sys.stderr", new=io.StringIO()).start()

    def kubectl(self, *args, document=None):
        self.calls.append((args, document))
        verb, resource = args[:2]
        if verb == "get" and args[-1] == "json":
            if resource == "job":
                return json.dumps({"status": self.job_status})
            items = [self.volume] if resource == "volumes.longhorn.io" else self.pvs
            return json.dumps({"items": items})
        if verb == "get" and resource == self.leftover:
            return f"{resource}/{args[2]}\n"
        if verb == "logs":
            return "SQLITE bazarr.db: ok\n"
        return ""

    def inspect(self):
        restore.inspect(argparse.Namespace(volume="bazarr-restore-test", pod_namespace="default", timeout=60),
                        self.cluster)

    def created(self):
        return [document for args, document in self.calls if args[0] == "create"]

    def deleted(self):
        return [args[1] for args, _ in self.calls if args[0] == "delete"]

    def test_creates_read_only_retained_inspection_and_always_cleans_up(self):
        self.inspect()
        pv, pvc, job = self.created()
        self.assertEqual(pv["spec"]["csi"]["volumeHandle"], "bazarr-restore-test")
        self.assertEqual(pv["spec"]["persistentVolumeReclaimPolicy"], "Retain")
        self.assertEqual(pvc["spec"]["volumeName"], pv["metadata"]["name"])
        pod = job["spec"]["template"]["spec"]
        container = pod["containers"][0]
        self.assertTrue(pod["volumes"][0]["persistentVolumeClaim"]["readOnly"])
        self.assertTrue(container["volumeMounts"][0]["readOnly"])
        self.assertFalse(pod["automountServiceAccountToken"])
        mounts = [mount["mountPath"] for mount in container["volumeMounts"]]
        self.assertEqual(container["command"], ["python3", "-c", restore.INSPECT_SCRIPT.read_text(), *mounts])
        self.assertEqual(self.deleted(), ["job", "pvc", "pv"])

    def test_failed_or_stuck_inspection_still_cleans_up(self):
        for job_status, error in (({"failed": 1}, "inspection failed"), ({}, "no result after 60s")):
            with self.subTest(job_status=job_status):
                self.calls.clear()
                self.job_status = job_status
                with self.assertRaisesRegex(restore.RestoreError, error):
                    self.inspect()
                self.assertEqual(self.deleted(), ["job", "pvc", "pv"])

    def test_stuck_cleanup_names_the_object_to_delete_manually(self):
        self.leftover = "pv"
        with self.assertRaisesRegex(restore.RestoreError, "bazarr-restore-test-inspect: pv was not removed"):
            self.inspect()
        self.assertEqual(self.deleted(), ["job", "pvc", "pv"])

    def test_leftover_inspection_claim_or_job_is_never_reused(self):
        for kind in ("pvc", "job"):
            with self.subTest(kind=kind):
                self.calls.clear()
                self.leftover = kind
                with self.assertRaisesRegex(restore.RestoreError, f"{kind} already exists in default"):
                    self.inspect()
                self.assertEqual(self.created(), [])
                self.assertEqual(self.deleted(), [])

    def test_only_completed_detached_restores_are_inspected(self):
        for field, value in (("spec", {"fromBackup": ""}), ("spec", {"encrypted": True}),
                             ("status", {"restoreRequired": True}), ("status", {"state": "attached"})):
            with self.subTest(field=field, value=value):
                original = deepcopy(self.volume)
                self.volume[field].update(value)
                with self.assertRaises(restore.RestoreError):
                    self.inspect()
                self.volume = original
        self.assertEqual(self.created(), [])

    def test_strict_local_volume_is_never_attached(self):
        self.volume["spec"]["dataLocality"] = "strict-local"
        with self.assertRaisesRegex(restore.RestoreError, "strict-local"):
            self.inspect()
        self.assertEqual(self.created(), [])

    def test_volume_already_used_by_a_pv_is_never_attached(self):
        self.pvs = [{"metadata": {"name": "bazarr-config"},
                     "spec": {"csi": {"driver": "driver.longhorn.io", "volumeHandle": "bazarr-restore-test"}}}]
        with self.assertRaisesRegex(restore.RestoreError, "already uses"):
            self.inspect()
        self.assertEqual(self.created(), [])
        self.assertEqual(self.deleted(), [])


class InspectScriptTests(unittest.TestCase):
    def test_script_is_found_when_the_cli_is_symlinked(self):
        with tempfile.TemporaryDirectory() as directory:
            link = Path(directory) / "longhorn_restore.py"
            link.symlink_to(Path(restore.__file__).resolve())
            spec = importlib.util.spec_from_file_location("linked_longhorn_restore", link)
            linked = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(linked)
        self.assertEqual(linked.INSPECT_SCRIPT, restore.INSPECT_SCRIPT)
        self.assertTrue(linked.INSPECT_SCRIPT.is_file())

    def check(self, populate):
        with tempfile.TemporaryDirectory() as volume, tempfile.TemporaryDirectory() as scratch:
            populate(Path(volume))
            script = restore.INSPECT_SCRIPT.read_text()
            result = subprocess.run([sys.executable, "-I", "-c", script, volume, scratch],
                                    capture_output=True, text=True, check=False)
            return result.returncode, result.stdout

    def test_wal_database_passes_and_corruption_or_empty_volume_fails(self):
        def healthy(root):
            (root / "config").mkdir()
            connection = sqlite3.connect(root / "config" / "app.db")
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("CREATE TABLE t (v)")
            connection.execute("INSERT INTO t VALUES (1)")
            connection.commit()
            shutil.copytree(root / "config", root / "snapshot")
            connection.close()
            shutil.rmtree(root / "config")
            (root / "snapshot").rename(root / "config")

        code, output = self.check(healthy)
        self.assertEqual(code, 0, output)
        self.assertIn("SQLITE config/app.db: ok", output)

        def corrupt(root):
            healthy(root)
            database = root / "config" / "app.db"
            (root / "config" / "app.db-wal").unlink()
            data = bytearray(database.read_bytes())
            data[100:4096] = b"\xff" * (4096 - 100)
            database.write_bytes(bytes(data))

        code, output = self.check(corrupt)
        self.assertEqual(code, 1, output)
        code, output = self.check(lambda root: None)
        self.assertEqual(code, 1, output)
        self.assertIn("no files", output)


class CapacityTests(unittest.TestCase):
    def test_quantities_are_compared_as_exact_bytes(self):
        for quantity, expected in (("1Gi", 2**30), ("32768M", 32768000000),
                                   ("32.768G", 32768000000), ("32768e6", 32768000000),
                                   ("1E+3", 1000), ("1E", 10**18), ("1536Mi", 3 * 2**29),
                                   ("1.5Gi", 3 * 2**29), ("1500m", 2), ("1000u", 1),
                                   ("1Pi", 2**50), ("+1024", 1024)):
            with self.subTest(quantity=quantity):
                self.assertEqual(restore.to_bytes(quantity), expected)

    def test_invalid_or_nonpositive_capacities_are_rejected(self):
        for quantity in ("", "1K", "NaN", "1GB", "-1Gi", "0", "1e100"):
            with self.subTest(quantity=quantity), self.assertRaises(restore.RestoreError):
                restore.to_bytes(quantity)


class KubectlTests(unittest.TestCase):
    @patch.object(restore.subprocess, "run")
    def test_empty_context_cannot_reach_kubectl(self, run):
        with self.assertRaisesRegex(restore.RestoreError, "context must not be empty"):
            restore.Cluster("", "longhorn-system")
        run.assert_not_called()

    @patch.object(restore.subprocess, "run")
    def test_reads_use_explicit_context_and_only_query_safe_resources(self, run):
        run.return_value = Mock(returncode=0, stdout='{"items": []}', stderr="")
        restore.Cluster("homelab", "longhorn-system").inventory()
        queried = []
        for call in run.call_args_list:
            command = call.args[0]
            self.assertEqual(command[:5], ["kubectl", "--context", "homelab", "--request-timeout=30s", "get"])
            queried.append(command[5])
            self.assertIsNone(call.kwargs["input"])
        self.assertNotIn("secret", queried)
        self.assertNotIn("externalsecret", queried)
        self.assertIn("backups.longhorn.io", queried)

    @patch.object(restore.subprocess, "run")
    def test_inventory_fills_each_field_from_its_own_query(self, run):
        def kubectl(command, **_):
            query = command[5:-2]
            return Mock(returncode=0, stdout=json.dumps({"items": [query]}), stderr="")

        run.side_effect = kubectl
        longhorn = ["-n", "longhorn-system"]
        self.assertEqual(restore.Cluster("homelab", "longhorn-system").inventory(), restore.Inventory(
            volumes=[["volumes.longhorn.io", *longhorn]],
            backups=[["backups.longhorn.io", *longhorn]],
            backup_volumes=[["backupvolumes.longhorn.io", *longhorn]],
            backup_targets=[["backuptargets.longhorn.io", *longhorn]],
            storage_classes=[["storageclass"]],
            pvs=[["pv"]],
        ))

    @patch.object(restore.subprocess, "run")
    def test_unidentified_cluster_is_rejected(self, run):
        run.return_value = Mock(returncode=0, stdout="\n", stderr="")
        with self.assertRaisesRegex(restore.RestoreError, "could not identify destination cluster"):
            restore.Cluster("homelab", "longhorn-system").identity()


class CommandLineTests(unittest.TestCase):
    ARGS = {
        "plan": ["--pv", "bazarr-config", "--output", "plan.json"],
        "apply": ["plan.json"],
        "inspect": ["--volume", "bazarr-restore-test"],
    }

    def setUp(self):
        self.addCleanup(patch.stopall)
        self.stderr = patch("sys.stderr", new=io.StringIO()).start()
        self.cluster = patch.object(restore, "Cluster").start()

    def test_cli_requires_selection_and_context_without_cluster_access(self):
        for args in ([], ["plan", "--context", "homelab", "--output", "plan.json"],
                     ["plan", "--pv", "bazarr-config", "--output", "plan.json"],
                     ["apply", "plan.json"]):
            with self.subTest(args=args):
                with self.assertRaises(SystemExit) as error:
                    restore.main(args)
                self.assertEqual(error.exception.code, 2)
                self.cluster.assert_not_called()

    def test_empty_context_is_rejected_before_constructing_cluster(self):
        for command, args in self.ARGS.items():
            for context in ("", "  "):
                with self.subTest(command=command, context=context):
                    with self.assertRaises(SystemExit) as error:
                        restore.main([command, "--context", context, *args])
                    self.assertEqual(error.exception.code, 2)
                    self.cluster.assert_not_called()

    def test_each_command_runs_against_the_named_context(self):
        for command, args in self.ARGS.items():
            with self.subTest(command=command), patch.object(restore, command) as handler:
                self.cluster.reset_mock()
                restore.main([command, "--context", "homelab", *args])
                self.cluster.assert_called_once_with("homelab", "longhorn-system")
                handler.assert_called_once()
                self.assertIs(handler.call_args.args[1], self.cluster.return_value)

    def test_restore_errors_exit_with_a_message_instead_of_a_traceback(self):
        with patch.object(restore, "apply", side_effect=restore.RestoreError("plan changed")):
            with self.assertRaises(SystemExit) as error:
                restore.main(["apply", "--context", "homelab", "plan.json"])
        self.assertEqual(error.exception.code, 1)
        self.assertEqual(self.stderr.getvalue(), "ERROR: plan changed\n")


if __name__ == "__main__":
    unittest.main()
