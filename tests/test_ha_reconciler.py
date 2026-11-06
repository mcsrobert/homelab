"""State-machine safety tests with real Git and a fake GitHub boundary."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / 'kubernetes/apps/home-automation/home-assistant/app/resources/reconciler/reconcile.py'
spec = importlib.util.spec_from_file_location('reconciler', SOURCE)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class ReconcilerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.live = self.root / 'live'
        self.live.mkdir()
        self.hook = self.root / 'hooks'
        self.hook.mkdir()
        hook = self.hook / 'pre-commit'
        hook.write_text('#!/bin/sh\nexit 0\n')
        hook.chmod(0o755)
        self.r = m.Reconciler(self.live, self.root / 'state', mode='deploy', hook=str(self.hook), settle=0)
        self.r.work.mkdir()
        self.r.git('init', '-q')
        self.r.git('config', 'user.name', 'Test')
        self.r.git('config', 'user.email', 'test@example.com')
        self.policy = b'/*\n!/.gitignore\n!/*.yaml\n!/packages/\nsecrets.yaml\n*.key\n*.pem\n'
        self.base = self.commit({'.gitignore': self.policy, 'configuration.yaml': b'original\n'})
        self.target = {'.gitignore': self.policy, 'configuration.yaml': b'original\n'}
        for name, data in self.target.items():
            (self.live / name).write_bytes(data)
        self.r.state = {'baseline': self.base}
        self.r.fetch = lambda: self.base
        self.r.checks = lambda revision: None
        self.pr = {'number': 1, 'state': 'open', 'merged': False,
                   'head': {'sha': self.base}, 'closed_at': '2099-01-01T00:00:00Z'}
        self.r.api = self.api
        self.pushed = []
        original_git = self.r.git

        def git(*args, **kwargs):
            if args[0] == 'ls-remote':
                return (self.pushed[-1] + '\trefs/heads/capture\n').encode() if self.pushed else b''
            if args[0] == 'push':
                head = args[2].split(':')[0]
                self.pushed.append(head)
                self.pr['head']['sha'] = head
                return b''
            return original_git(*args, **kwargs)
        self.r.git = git

    def api(self, path, body=None):
        if path.startswith('pulls?'):
            return []
        return self.pr

    def commit(self, files):
        for name, data in files.items():
            path = self.r.work / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        self.r.git('add', '.')
        self.r.git('commit', '-qm', 'test: fixture')
        return self.r.git('rev-parse', 'HEAD').decode().strip()

    def edit(self, content=b'edited\n'):
        (self.live / 'configuration.yaml').write_bytes(content)

    def capture(self):
        self.edit()
        with self.assertRaisesRegex(m.Blocked, 'pending_review'):
            self.r.poll()

    def test_observe_never_captures_or_writes(self):
        self.r.mode = 'observe'
        self.edit()
        self.r.poll()
        self.assertNotIn('pending', self.r.state)
        self.assertEqual((self.live / 'configuration.yaml').read_bytes(), b'edited\n')

    def test_allowlist_and_runtime_exclusions(self):
        (self.live / 'secrets.yaml').write_text('excluded')
        (self.live / 'bad.sops.yaml').write_text('excluded')
        (self.live / 'runtime.db').write_text('excluded')
        (self.live / '.storage').mkdir()
        (self.live / '.storage' / 'file.yaml').write_text('excluded')
        (self.live / 'packages').mkdir()
        (self.live / 'packages' / 'new.yaml').write_text('included')
        snapshot = self.r.snapshot(self.policy)
        self.assertEqual(set(snapshot), {'.gitignore', 'configuration.yaml', 'packages/new.yaml'})

    def test_live_policy_cannot_expand_capture(self):
        (self.live / '.gitignore').write_text('')
        (self.live / 'runtime.db').write_text('excluded')
        self.assertNotIn('runtime.db', self.r.snapshot(self.policy))

    def test_symlink_blocks_without_reading_target(self):
        (self.live / 'configuration.yaml').unlink()
        (self.live / 'configuration.yaml').symlink_to('/etc/passwd')
        with self.assertRaisesRegex(m.Blocked, 'symlink'):
            self.r.snapshot(self.policy)

    def test_pre_commit_failure_blocks_capture_and_deploy(self):
        (self.hook / 'pre-commit').write_text('#!/bin/sh\nexit 1\n')
        self.edit()
        with self.assertRaisesRegex(m.Blocked, 'pre_commit_failed'):
            self.r.poll()
        self.assertNotIn('pending', self.r.state)
        self.assertFalse(self.pushed)
        self.assertEqual((self.live / 'configuration.yaml').read_bytes(), b'edited\n')

    def test_open_pr_blocks_even_when_main_can_merge(self):
        self.capture()
        head = self.r.state['pending']['head']
        with self.assertRaisesRegex(m.Blocked, 'pending_review'):
            self.r.poll()
        self.assertEqual(self.r.state['pending']['head'], head)
        self.assertEqual(len(self.pushed), 1)

    def test_new_edits_update_same_pr(self):
        self.capture()
        branch = self.r.state['pending']['branch']
        self.edit(b'newer\n')
        with self.assertRaisesRegex(m.Blocked, 'pending_review'):
            self.r.poll()
        self.assertEqual(self.r.state['pending']['branch'], branch)
        self.assertEqual(len(self.pushed), 2)

    def test_rejection_restores_only_managed_files(self):
        self.capture()
        (self.live / 'secrets.yaml').write_text('excluded')
        self.pr['state'] = 'closed'
        self.r.poll()
        self.assertEqual((self.live / 'configuration.yaml').read_bytes(), b'original\n')
        self.assertEqual((self.live / 'secrets.yaml').read_text(), 'excluded')
        self.assertNotIn('pending', self.r.state)
        self.r.poll()
        self.assertEqual(len(self.pushed), 1)

    def test_rejection_preserves_newer_edits(self):
        self.capture()
        self.edit(b'newer\n')
        self.pr['state'] = 'closed'
        with self.assertRaisesRegex(m.Blocked, 'decision_has_newer_edits'):
            self.r.poll()
        self.assertEqual((self.live / 'configuration.yaml').read_bytes(), b'newer\n')

    def test_merge_with_newer_edits_creates_fresh_capture(self):
        self.capture()
        branch = self.r.state['pending']['branch']
        self.pr['state'] = 'closed'
        self.pr['merged'] = True
        self.pr['merge_commit_sha'] = self.r.state['pending']['head']
        self.r.fetch = lambda: self.pr['merge_commit_sha']
        self.edit(b'newer\n')
        # New draft PR response represents a different PR.
        old_publish = self.r.publish
        calls = []
        def publish():
            if not calls:
                calls.append(True)
                return old_publish()
            self.r.state['pending']['pr'] = 2
            return {'state': 'open'}
        self.r.publish = publish
        with self.assertRaisesRegex(m.Blocked, 'pending_review'):
            self.r.poll()
        self.assertNotEqual(self.r.state['pending']['branch'], branch)
        self.assertEqual((self.live / 'configuration.yaml').read_bytes(), b'newer\n')

    def test_crash_recovery_finishes_partial_publication(self):
        target = {'.gitignore': self.policy, 'configuration.yaml': b'approved\n', 'new.yaml': b'new\n'}
        revision = self.commit(target)
        self.r.state['transaction'] = {'revision': revision,
            'before': self.r.hashes(self.target), 'after': self.r.hashes(target)}
        self.r.save()
        self.edit(b'approved\n')  # first file already published before crash
        restarted = m.Reconciler(self.live, self.r.root, settle=0)
        restarted.recover()
        self.assertEqual((self.live / 'new.yaml').read_bytes(), b'new\n')
        self.assertEqual(restarted.state['baseline'], revision)
        self.assertNotIn('transaction', restarted.state)

    def test_recovery_conflict_preserves_human_edits(self):
        self.r.state['transaction'] = {'revision': self.base,
            'before': self.r.hashes(self.target), 'after': self.r.hashes(self.target)}
        self.edit(b'human\n')
        with self.assertRaisesRegex(m.Blocked, 'recovery_conflict'):
            self.r.recover()
        self.assertIn('transaction', self.r.state)

    def test_human_branch_edit_blocks(self):
        self.capture()
        self.pushed.append('0' * 40)
        with self.assertRaisesRegex(m.Blocked, 'human_branch_edits'):
            self.r.poll()

    def test_ci_missing_or_failed_blocks(self):
        del self.r.checks
        self.r.api = lambda path: {'check_runs': []}
        with self.assertRaisesRegex(m.Blocked, 'ci_not_successful'):
            self.r.checks(self.base)

    def test_squash_merge_advances_baseline_without_rewriting_live(self):
        self.capture()
        captured = self.r.state['pending']['head']
        approved = self.r.tree(captured)
        # A separate commit with identical content models a squash/rebase merge.
        self.r.git('reset', '--hard', self.base)
        revision = self.commit(approved)
        self.r.fetch = lambda: revision
        self.pr['state'] = 'closed'
        self.pr['merged'] = True
        self.pr['merge_commit_sha'] = revision
        modified = (self.live / 'configuration.yaml').stat().st_mtime_ns
        self.r.poll()
        self.assertEqual(self.r.state['baseline'], revision)
        self.assertEqual((self.live / 'configuration.yaml').stat().st_mtime_ns, modified)
        self.assertNotIn('pending', self.r.state)

    def test_closure_during_publication_blocks_rejection(self):
        self.capture()
        self.pr['state'] = 'closed'
        self.pr['closed_at'] = '2000-01-01T00:00:00Z'
        with self.assertRaisesRegex(m.Blocked, 'decision_has_newer_edits'):
            self.r.poll()
        self.assertEqual((self.live / 'configuration.yaml').read_bytes(), b'edited\n')

    def test_nonexecutable_hook_blocks_capture(self):
        (self.hook / 'pre-commit').chmod(0o644)
        self.edit()
        with self.assertRaisesRegex(m.Blocked, 'scanner_missing'):
            self.r.poll()
        self.assertFalse(self.pushed)

    def test_unsettled_files_block(self):
        with patch.object(self.r, 'snapshot', side_effect=[{'file': b'a'}, {'file': b'b'}]):
            with self.assertRaisesRegex(m.Blocked, 'unsettled'):
                self.r.poll()

    def test_deployment_applies_additions_and_deletions(self):
        target = {'.gitignore': self.policy, 'new.yaml': b'new\n'}
        self.r.git('rm', 'configuration.yaml')
        revision = self.commit(target)
        self.r.fetch = lambda: revision
        self.r.poll()
        self.assertFalse((self.live / 'configuration.yaml').exists())
        self.assertEqual((self.live / 'new.yaml').read_bytes(), b'new\n')

    def test_metrics_do_not_leak_exception_details(self):
        self.r.fetch = lambda: (_ for _ in ()).throw(RuntimeError('sensitive detail'))
        self.r.tick()
        self.assertIn('unexpected_error', self.r.exposition())
        self.assertNotIn('sensitive detail', self.r.exposition())


if __name__ == '__main__':
    unittest.main()
