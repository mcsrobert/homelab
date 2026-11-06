#!/usr/bin/env python3
"""Fail-closed HA file reconciliation. Git's worktree is never the live volume."""
from datetime import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen


class Blocked(Exception):
    pass


def digest(data):
    return hashlib.sha256(data).hexdigest()


def atomic(path, data, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.reconciler-', dir=path.parent)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def safe_path(root, name):
    parts = PurePosixPath(name).parts
    if not parts or name.startswith('/') or any(p in ('.', '..', '.git') for p in parts):
        raise Blocked('unsafe_path')
    path = root
    for part in parts:
        path = path / part
        if path.is_symlink():
            raise Blocked('symlink')
    return path


class Reconciler:
    def __init__(self, live, state, repo='mcsrobert/home-assistant-config', mode='observe',
                 hook='/opt/reconciler', settle=10):
        if mode not in ('observe', 'capture', 'deploy'):
            raise ValueError('Unknown mode')
        self.live, self.root = Path(live), Path(state)
        self.repo, self.mode, self.hook, self.settle = repo, mode, hook, settle
        self.root.mkdir(parents=True, exist_ok=True)
        self.work = self.root / 'git'
        self.record = self.root / 'state.json'
        self.state = json.loads(self.record.read_text()) if self.record.exists() else {}
        self.metrics = {'last_poll_success_timestamp_seconds': 0,
                        'last_deployment_timestamp_seconds': self.state.get('deployed_at', 0),
                        'pending_review': 0, 'drift': 0, 'blocked': 1}
        self.reason = 'starting'
        self.env = dict(os.environ, GIT_TERMINAL_PROMPT='0', GIT_CONFIG_NOSYSTEM='1',
                        GIT_CONFIG_GLOBAL='/dev/null', GIT_AUTHOR_NAME='HA config capture',
                        GIT_AUTHOR_EMAIL='ha-config-capture@users.noreply.github.com',
                        GIT_COMMITTER_NAME='HA config capture',
                        GIT_COMMITTER_EMAIL='ha-config-capture@users.noreply.github.com')

    def git(self, *args, data=None):
        # Do not log stdout/stderr: rejected captures can contain secrets.
        result = subprocess.run(['git', '-c', 'core.hooksPath=' + self.hook,
                                 '-c', 'credential.helper=', '-c',
                                 'credential.https://github.com.helper=!f() { test "$1" = get && '
                                 'printf "username=x-access-token\\npassword=%s\\n" "$GH_TOKEN"; }; f',
                                 *args], cwd=self.work, input=data, capture_output=True,
                                env=self.env, timeout=120)
        if result.returncode:
            raise Blocked('git_failed')
        return result.stdout

    def save(self):
        for key, revision in (('baseline', self.state.get('baseline')),
                              ('capture', self.state.get('pending', {}).get('head')),
                              ('transaction', self.state.get('transaction', {}).get('revision'))):
            if revision:
                self.git('update-ref', 'refs/reconciler/' + key, revision)
        atomic(self.record, json.dumps(self.state, sort_keys=True).encode())

    def api(self, path, body=None, method=None):
        request = Request('https://api.github.com/repos/' + self.repo + '/' + path,
                          data=json.dumps(body).encode() if body is not None else None,
                          headers={'Authorization': 'Bearer ' + os.environ['GH_TOKEN'],
                                   'Accept': 'application/vnd.github+json',
                                   'X-GitHub-Api-Version': '2022-11-28'}, method=method)
        try:
            with urlopen(request, timeout=30) as response:
                return json.load(response)
        except HTTPError:
            raise Blocked('github_failed') from None

    def fetch(self):
        if not (self.work / '.git').exists():
            self.work.mkdir(parents=True, exist_ok=True)
            self.git('init', '-q')
            self.git('remote', 'add', 'origin', 'https://github.com/' + self.repo + '.git')
        self.git('fetch', '--no-tags', 'origin', '+refs/heads/main:refs/remotes/origin/main')
        return self.git('rev-parse', 'origin/main').decode().strip()

    def tree(self, revision):
        result = {}
        for entry in self.git('ls-tree', '-r', '-z', revision).split(b'\0'):
            if not entry:
                continue
            metadata, name = entry.split(b'\t', 1)
            mode, kind, oid = metadata.split()
            if mode not in (b'100644', b'100755') or kind != b'blob':
                raise Blocked('unsupported_git_entry')
            name = name.decode()
            safe_path(self.work, name)
            if Path(name).name == 'secrets.yaml' or name.endswith(('.sops.yaml', '.key', '.pem')):
                raise Blocked('forbidden_tracked_file')
            result[name] = self.git('cat-file', 'blob', oid.decode())
        return result

    def snapshot(self, policy):
        """Apply approved ignore rules in scratch space, never live .gitignore rules."""
        paths = []
        for base, dirs, files in os.walk(self.live, followlinks=False):
            # These runtime directories are excluded independently of the policy.
            dirs[:] = [d for d in dirs if d not in ('.git', '.storage', '__pycache__',
                                                   'custom_components', 'www', 'deps')]
            for name in files:
                path = Path(base, name)
                relative = path.relative_to(self.live).as_posix()
                if name == 'secrets.yaml' or name.endswith(('.sops.yaml', '.key', '.pem')):
                    continue
                paths.append(relative)
        # check-ignore uses the approved main policy, not the candidate's policy.
        self.clear_worktree()
        self.git('read-tree', '--empty')
        atomic(self.work / '.gitignore', policy, 0o644)
        if paths:
            process = subprocess.run(['git', 'check-ignore', '--no-index', '-z', '--stdin'],
                                     cwd=self.work, input='\0'.join(paths).encode() + b'\0',
                                     capture_output=True, env=self.env, timeout=30)
            if process.returncode not in (0, 1):
                raise Blocked('policy_failed')
            ignored = set(process.stdout.decode().split('\0'))
        else:
            ignored = set()
        return {name: safe_path(self.live, name).read_bytes() for name in paths if name not in ignored}

    @staticmethod
    def hashes(files):
        return {name: digest(data) for name, data in files.items()}

    def settled(self, policy):
        first = self.snapshot(policy)
        time.sleep(self.settle)
        second = self.snapshot(policy)
        if self.hashes(first) != self.hashes(second):
            raise Blocked('unsettled')
        return second

    def clear_worktree(self):
        for path in self.work.iterdir():
            if path.name == '.git':
                continue
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
            else:
                path.unlink()

    def capture(self, files):
        pending = self.state.get('pending')
        base = pending['head'] if pending else self.state['baseline']
        # Only this isolated scratch checkout is reset. Live files are never reset.
        self.git('reset', '--hard', base)
        self.clear_worktree()
        for name, data in files.items():
            atomic(safe_path(self.work, name), data, 0o644)
        self.git('add', '--force', '--', *sorted(files)) if files else None
        self.git('add', '-u')
        if not os.access(Path(self.hook, 'pre-commit'), os.X_OK):
            raise Blocked('scanner_missing')
        try:
            self.git('commit', '-m', 'chore(config): capture live Home Assistant changes')
        except Blocked:
            raise Blocked('pre_commit_failed') from None
        head = self.git('rev-parse', 'HEAD').decode().strip()
        if pending:
            pending['head'] = head
        else:
            self.state['pending'] = {'head': head, 'published': None,
                                     'branch': 'capture/ha-' + head[:16]}
        # Save before network effects; retry push/PR creation using the same identity.
        self.save()

    def publish(self):
        pending = self.state['pending']
        branch, head = pending['branch'], pending['head']
        remote = self.git('ls-remote', 'origin', 'refs/heads/' + branch).decode().split()
        remote_head = remote[0] if remote else None
        pr = self.api('pulls/' + str(pending['pr'])) if 'pr' in pending else None
        if pr and pr['state'] == 'closed':
            if pr['head']['sha'] != head or remote_head not in (None, head):
                raise Blocked('decision_has_newer_edits')
            return pr
        if remote_head is None and pending['published']:
            raise Blocked('human_branch_edits')
        if remote_head not in (None, head, pending['published']):
            raise Blocked('human_branch_edits')
        if remote_head != head:
            # No force pushes. A racing human push is rejected by Git.
            pending['publishing_at'] = time.time()
            self.save()
            self.git('push', 'origin', head + ':refs/heads/' + branch)
        pending['published'] = head
        self.save()
        if 'pr' not in pending:
            prs = self.api('pulls?state=all&head=' + self.repo.split('/')[0] + ':' + branch)
            pr = prs[0] if prs else self.api('pulls', {
                'title': 'chore(config): capture live Home Assistant edits',
                'head': branch, 'base': 'main', 'draft': True,
                'body': 'Captured live YAML/configuration changes. An open capture PR blocks '
                        'deployment. Merge to accept; close without merging to reject only '
                        'this recorded snapshot. Reload/restart remains manual.'})
            pending['pr'] = pr['number']
            self.save()
        return self.api('pulls/' + str(pending['pr']))

    def checks(self, revision):
        runs = self.api('commits/' + revision + '/check-runs?per_page=100')
        required = {'validate', 'check-config', 'secrets', 'verify-artifacts'}
        # GitHub returns most recent first; ignore older reruns of the same job.
        latest = {}
        for run in runs['check_runs']:
            if run['app']['slug'] == 'github-actions':
                latest.setdefault(run['name'], run)
        if not all(latest.get(name, {}).get('conclusion') == 'success' for name in required):
            raise Blocked('ci_not_successful')

    def deploy(self, revision, target, live):
        self.checks(revision)
        self.state['transaction'] = {'revision': revision, 'before': self.hashes(live),
                                      'after': self.hashes(target)}
        self.save()
        self.recover()

    def recover(self):
        transaction = self.state['transaction']
        target = self.tree(transaction['revision'])
        before, after = transaction['before'], transaction['after']
        for name in sorted(before.keys() | after.keys()):
            path = safe_path(self.live, name)
            current = digest(path.read_bytes()) if path.exists() else None
            if current not in (before.get(name), after.get(name)):
                raise Blocked('recovery_conflict')
        for name in sorted(before.keys() | after.keys()):
            path = safe_path(self.live, name)
            current = digest(path.read_bytes()) if path.exists() else None
            if current == after.get(name):
                continue
            if current != before.get(name):
                raise Blocked('concurrent_edit')
            if name in target:
                atomic(path, target[name], 0o644)
            else:
                path.unlink()
        self.state['baseline'] = transaction['revision']
        self.state['deployed_at'] = time.time()
        self.state.pop('transaction')
        self.state.pop('pending', None)
        self.save()
        self.metrics['last_deployment_timestamp_seconds'] = self.state['deployed_at']
        self.metrics['pending_review'] = 0
        self.metrics['blocked'] = 0
        self.reason = 'ready'

    def poll(self):
        main = self.fetch()
        target = self.tree(main)
        policy = target['.gitignore']
        if 'transaction' in self.state:
            if self.mode != 'deploy':
                raise Blocked('recovery_requires_deploy')
            self.recover()
        live = self.settled(policy)
        if 'baseline' not in self.state:
            self.state['baseline'] = main
            self.save()
        baseline = self.tree(self.state['baseline'])
        pending = self.state.get('pending')
        self.metrics['drift'] = int(self.hashes(live) != self.hashes(baseline))
        self.metrics['pending_review'] = int(bool(pending))
        if self.mode == 'observe':
            self.reason = 'observing'
            self.metrics['blocked'] = 1
            return
        if pending:
            pr = self.publish()
            if pr['head']['sha'] != pending['head']:
                raise Blocked('human_branch_edits')
            captured = self.tree(pending['head'])
            if pr['state'] == 'closed':
                closed_at = datetime.fromisoformat(pr['closed_at'].replace('Z', '+00:00')).timestamp()
                if closed_at <= pending.get('publishing_at', 0):
                    raise Blocked('decision_has_newer_edits')
                # The first fetch may have raced the PR decision. Refresh before
                # accepting it, and require the merged revision to be on main.
                main = self.fetch()
                target = self.tree(main)
                if pr['merged']:
                    if not pr.get('merge_commit_sha'):
                        raise Blocked('merge_not_on_main')
                    try:
                        self.git('merge-base', '--is-ancestor', pr['merge_commit_sha'], main)
                    except Blocked:
                        raise Blocked('merge_not_on_main') from None
                if pr['merged'] and self.hashes(live) != self.hashes(captured):
                    # Keep newer edits on a fresh branch based on the accepted capture.
                    self.state['baseline'] = pending['head']
                    self.state.pop('pending')
                    self.save()
                    self.capture(live)
                    self.publish()
                    raise Blocked('pending_review')
                if self.hashes(live) != self.hashes(captured):
                    raise Blocked('decision_has_newer_edits')
                if self.mode != 'deploy':
                    raise Blocked('decision_requires_deploy')
                # Both merge and rejection converge to approved main; CI must pass.
                # An already-equal target advances the baseline without file writes.
                self.deploy(main, target, live)
                return
            if self.hashes(live) != self.hashes(captured):
                self.capture(live)
                self.publish()
            raise Blocked('pending_review')
        if self.hashes(live) != self.hashes(baseline):
            self.capture(live)
            self.publish()
            self.metrics['pending_review'] = 1
            raise Blocked('pending_review')
        if self.mode == 'capture':
            raise Blocked('deployment_disabled')
        if main != self.state['baseline']:
            self.deploy(main, target, live)
        self.reason = 'ready'
        self.metrics['blocked'] = 0

    def tick(self):
        try:
            self.poll()
            self.metrics['last_poll_success_timestamp_seconds'] = time.time()
        except Blocked as error:
            self.reason = str(error)
            self.metrics['blocked'] = 1
        except Exception:
            # Never include exception details or file content in logs/metrics.
            self.reason = 'unexpected_error'
            self.metrics['blocked'] = 1
        print(json.dumps({'state': self.reason}), flush=True)

    def exposition(self):
        text = ''.join('ha_config_' + key + ' ' + str(value) + '\n'
                       for key, value in sorted(self.metrics.items()))
        return text + 'ha_config_state{reason="' + self.reason + '"} 1\n'


def main():
    reconciler = Reconciler(os.getenv('LIVE_PATH', '/live'), os.getenv('STATE_PATH', '/state'),
                            mode=os.getenv('MODE', 'observe'),
                            settle=int(os.getenv('SETTLE_SECONDS', '10')))
    # Only one process may reconcile this state volume, including during rollouts.
    lock = open(reconciler.root / 'lock', 'w')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path not in ('/metrics', '/healthz'):
                self.send_error(404)
                return
            body = reconciler.exposition().encode() if self.path == '/metrics' else b'ok\n'
            self.send_response(200)
            self.send_header('Content-Type', 'text/plain; version=0.0.4')
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
            pass

    server = HTTPServer(('0.0.0.0', 9090), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    while True:
        reconciler.tick()
        time.sleep(int(os.getenv('POLL_SECONDS', '60')))


if __name__ == '__main__':
    main()
