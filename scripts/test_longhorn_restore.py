"""Regression tests for Longhorn backup discovery; no cluster access required."""

import contextlib
import io
import unittest
from unittest.mock import patch

import longhorn_restore as restore


class BackupDiscoveryTests(unittest.TestCase):
    @contextlib.contextmanager
    def cluster(self, backups, *args):
        resources = {
            "volumes.longhorn.io": [],
            "backupvolumes.longhorn.io": [
                {"spec": {"volumeName": "authelia-data"}, "status": {"labels": {}}}
            ],
            "backups.longhorn.io": backups,
            "storageclass": [],
            "pv": [
                {
                    "spec": {
                        "csi": {
                            "driver": "driver.longhorn.io",
                            "volumeHandle": "authelia-data",
                        },
                        "capacity": {"storage": "1Gi"},
                    }
                }
            ],
        }
        output = io.StringIO()
        with (
            patch.object(restore, "items", side_effect=lambda *a: resources[a[0]]),
            patch.object(restore, "kubectl") as kubectl,
            patch("sys.argv", ["longhorn_restore.py", *args]),
            contextlib.redirect_stdout(output),
        ):
            yield output, kubectl

    def test_unsynced_backup_does_not_block_a_completed_backup(self):
        backups = [
            {"metadata": {"name": "unsynced-unrelated-volume"}, "spec": {}},
            {"status": {}},
            {"status": {"volumeName": "authelia-data", "state": "Pending"}},
            {
                "metadata": {"name": "completed"},
                "status": {
                    "volumeName": "authelia-data",
                    "state": "Completed",
                    "snapshotCreatedAt": "2026-11-08T00:00:00Z",
                    "volumeSize": "1073741824",
                    "url": "nfs://nas/backup?backup=completed&volume=authelia-data",
                },
            },
        ]
        for args in [(), ("--apply",)]:
            with self.subTest(args=args), self.cluster(backups, *args) as (output, kubectl):
                restore.main()
                self.assertIn("backup completed", output.getvalue())
                if args:
                    kubectl.assert_called_once()
                    self.assertEqual(kubectl.call_args.args, ("create", "-f", "-"))
                    self.assertIn("name: authelia-data", kubectl.call_args.kwargs["stdin"])
                    self.assertIn("backup=completed", kubectl.call_args.kwargs["stdin"])
                else:
                    kubectl.assert_not_called()

    def test_only_unsynced_backups_report_missing_without_creating_a_volume(self):
        with self.cluster([{"spec": {}}], "--apply") as (output, kubectl):
            with self.assertRaises(SystemExit) as error:
                restore.main()
            self.assertEqual(error.exception.code, 1)
            self.assertIn("MISSING  authelia-data", output.getvalue())
            kubectl.assert_not_called()


if __name__ == "__main__":
    unittest.main()
