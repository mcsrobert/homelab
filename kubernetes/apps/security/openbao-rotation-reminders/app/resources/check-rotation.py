"""Check OpenBao KV v2 custom_metadata for secrets due for rotation.

Walks <mount>/metadata/* (never reading <mount>/data/*), evaluates rotation
reminders stored in each secret's KV v2 custom_metadata, and sends a single
ntfy digest when any secret is expired, due, expiring soon, or has invalid
rotation metadata.

Supported custom_metadata keys on each KV v2 secret:
  - rotation_interval: Positive duration (<N>h, <N>d, or <N>w), measured from
    the current version's created_time.
  - expires_at: Fixed expiration date (YYYY-MM-DD, interpreted as 00:00:00Z) or
    RFC3339 timestamp with timezone offset.
  - rotation_warn_before: Optional lead time (<N>h, <N>d, or <N>w; 0 allowed).
    Defaults to DEFAULT_EXPIRES_WARN_BEFORE (14d) for expires_at, and 0d when
    only rotation_interval is set.
  - rotation_note: Optional instructions included in the reminder body.
"""

from __future__ import annotations

import json
import os
import re
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from enum import IntEnum
from typing import Any

MAX_RESPONSE_BYTES = 1 << 20  # 1 MiB
MAX_NTFY_BODY_BYTES = 4096
NTFY_TRUNCATE_LIMIT = 3900

KNOWN_META_KEYS = frozenset(
    {
        "rotation_interval",
        "expires_at",
        "rotation_warn_before",
        "rotation_note",
    }
)

DURATION_RE = re.compile(r"^([0-9]+)([hdw])$")
DATE_ONLY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class Severity(IntEnum):
    EXPIRED = 0
    DUE = 1
    UPCOMING = 2
    INVALID = 3


SECTION_HEADERS: dict[Severity, str] = {
    Severity.EXPIRED: "EXPIRED",
    Severity.DUE: "DUE FOR ROTATION",
    Severity.UPCOMING: "EXPIRING SOON",
    Severity.INVALID: "INVALID METADATA",
}


class OpenBaoError(RuntimeError):
    """Raised when communicating with OpenBao fails."""


class NtfyError(RuntimeError):
    """Raised when publishing to ntfy fails."""


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Refuse HTTP redirects so tokens and topic URLs are never forwarded."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(
            req.full_url,
            code,
            f"redirect refused (HTTP {code})",
            headers,
            fp,
        )


@dataclass(frozen=True)
class Finding:
    severity: Severity
    sort_time: datetime
    key: str
    reasons: tuple[str, ...]
    note: str = ""


def parse_duration(value: str, *, allow_zero: bool = False) -> timedelta:
    """Parse a duration like '80d', '12w', or '24h'."""
    text = value.strip()
    match = DURATION_RE.match(text)
    if not match:
        raise ValueError(f"invalid duration {value!r} (use e.g. 80d, 12w, 24h)")
    amount = int(match.group(1))
    unit = match.group(2)
    if amount == 0 and not allow_zero:
        raise ValueError(f"duration {value!r} must be positive")
    if unit == "h":
        return timedelta(hours=amount)
    if unit == "d":
        return timedelta(days=amount)
    return timedelta(weeks=amount)


def parse_rfc3339(value: str) -> datetime:
    """Parse an RFC3339 timestamp requiring an explicit timezone."""
    dt = datetime.fromisoformat(value.strip())
    if dt.tzinfo is None:
        raise ValueError(
            f"timestamp {value!r} must include a timezone (e.g. Z or +00:00)"
        )
    return dt.astimezone(timezone.utc)


def parse_expires_at(value: str) -> datetime:
    """Parse YYYY-MM-DD (UTC midnight) or an RFC3339 timestamp."""
    text = value.strip()
    if DATE_ONLY_RE.match(text):
        d = date.fromisoformat(text)
        return datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
    if "T" in text or "t" in text or " " in text:
        return parse_rfc3339(text)
    raise ValueError(
        f"invalid expires_at {value!r} (use YYYY-MM-DD or RFC3339)"
    )


def format_delta(delta: timedelta) -> str:
    """Format a non-negative timedelta as a compact human-readable string."""
    total_seconds = max(0, int(delta.total_seconds()))
    days, rem = divmod(total_seconds, 86400)
    hours = rem // 3600
    if days >= 1:
        return f"{days}d"
    if hours >= 1:
        return f"{hours}h"
    minutes = max(1, rem // 60)
    return f"{minutes}m"


def escape_path(path: str) -> str:
    """Percent-encode each slash-separated segment of an OpenBao path."""
    return "/".join(urllib.parse.quote(seg, safe="") for seg in path.split("/"))


def build_ssl_context(ca_file: str | None) -> ssl.SSLContext:
    ctx = ssl.create_default_context(cafile=ca_file or None)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    return ctx


class OpenBaoClient:
    """Minimal, metadata-only OpenBao KV v2 client."""

    def __init__(
        self,
        address: str,
        mount: str,
        ca_file: str | None,
        timeout: float,
    ) -> None:
        parsed = urllib.parse.urlparse(address)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError("OPENBAO_ADDR must be a valid http(s) URL")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError(
                "OPENBAO_ADDR must not contain credentials, query, or fragment"
            )
        self.base = address.rstrip("/")
        self.mount = mount.strip("/")
        if not self.mount:
            raise ValueError("OPENBAO_MOUNT must not be empty")
        self.timeout = timeout
        ssl_ctx = build_ssl_context(ca_file)
        self._opener = urllib.request.build_opener(
            _NoRedirectHandler(),
            urllib.request.HTTPSHandler(context=ssl_ctx),
        )

    def _request(
        self,
        method: str,
        path: str,
        *,
        token: str | None = None,
        body: dict[str, Any] | None = None,
        query: str | None = None,
        allow_404: bool = False,
    ) -> dict[str, Any] | None:
        url = f"{self.base}{path}"
        if query:
            url = f"{url}?{query}"
        data = json.dumps(body).encode("utf-8") if body is not None else None
        headers: dict[str, str] = {}
        if data is not None:
            headers["Content-Type"] = "application/json"
        if token:
            headers["X-Vault-Token"] = token
        req = urllib.request.Request(
            url, data=data, headers=headers, method=method
        )
        try:
            with self._opener.open(req, timeout=self.timeout) as resp:
                raw = resp.read(MAX_RESPONSE_BYTES + 1)
                if len(raw) > MAX_RESPONSE_BYTES:
                    raise OpenBaoError(f"response too large for {path}")
                if not raw:
                    return None
                try:
                    decoded = json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise OpenBaoError(
                        f"invalid JSON response from {path}"
                    ) from exc
                if not isinstance(decoded, dict):
                    raise OpenBaoError(
                        f"unexpected JSON payload from {path}"
                    )
                return decoded
        except urllib.error.HTTPError as exc:
            if allow_404 and exc.code == 404:
                return None
            # Never include response body in errors.
            raise OpenBaoError(
                f"HTTP {exc.code} calling {method} {path}"
            ) from None
        except urllib.error.URLError as exc:
            raise OpenBaoError(
                f"request failed calling {method} {path}: {exc.reason}"
            ) from None

    def login_kubernetes(
        self, auth_mount: str, role: str, jwt: str
    ) -> tuple[str, bool]:
        path = f"/v1/auth/{escape_path(auth_mount.strip('/'))}/login"
        resp = self._request(
            "POST", path, body={"role": role, "jwt": jwt}
        )
        auth = (resp or {}).get("auth")
        if not isinstance(auth, dict) or not auth.get("client_token"):
            raise OpenBaoError("kubernetes login response missing client_token")
        token = str(auth["client_token"])
        revocable = auth.get("token_type") != "batch"
        return token, revocable

    def revoke_self(self, token: str) -> None:
        self._request("POST", "/v1/auth/token/revoke-self", token=token)

    def list_keys(self, token: str, prefix: str = "") -> list[str]:
        """Recursively list all KV v2 secret paths under the mount."""
        keys: list[str] = []
        encoded_mount = escape_path(self.mount)
        stack = [prefix]
        seen_prefixes: set[str] = set()

        while stack:
            current = stack.pop()
            if current in seen_prefixes:
                continue
            seen_prefixes.add(current)

            if current:
                path = f"/v1/{encoded_mount}/metadata/{escape_path(current)}"
            else:
                path = f"/v1/{encoded_mount}/metadata/"

            resp = self._request(
                "GET", path, token=token, query="list=true", allow_404=True
            )
            if resp is None:
                continue
            data = resp.get("data")
            if not isinstance(data, dict):
                raise OpenBaoError(f"missing data in list response for {path}")
            entries = data.get("keys") or []
            if not isinstance(entries, list):
                raise OpenBaoError(f"invalid keys list in response for {path}")

            for entry in entries:
                if not isinstance(entry, str):
                    continue
                full = f"{current}{entry}"
                if entry.endswith("/"):
                    stack.append(full)
                else:
                    keys.append(full)

        keys.sort()
        return keys

    def read_metadata(self, token: str, key: str) -> dict[str, Any] | None:
        encoded_mount = escape_path(self.mount)
        path = f"/v1/{encoded_mount}/metadata/{escape_path(key)}"
        resp = self._request("GET", path, token=token, allow_404=True)
        if resp is None:
            return None
        data = resp.get("data")
        if not isinstance(data, dict):
            raise OpenBaoError(f"missing data in metadata response for {key}")
        return data


def _current_version_created(
    metadata: dict[str, Any], now: datetime
) -> datetime:
    current_version = metadata.get("current_version")
    versions = metadata.get("versions")
    if not isinstance(current_version, int) or current_version <= 0:
        raise ValueError("secret has no active current version")
    if not isinstance(versions, dict):
        raise ValueError("secret metadata missing versions map")
    version = versions.get(str(current_version))
    if not isinstance(version, dict):
        raise ValueError(
            f"current version {current_version} has no metadata entry"
        )
    if version.get("destroyed"):
        raise ValueError(f"current version {current_version} is destroyed")
    deletion_time = version.get("deletion_time") or ""
    if deletion_time:
        deleted_at = parse_rfc3339(str(deletion_time))
        if deleted_at <= now:
            raise ValueError(f"current version {current_version} is deleted")
    created_raw = version.get("created_time") or ""
    if not created_raw:
        raise ValueError(
            f"current version {current_version} missing created_time"
        )
    return parse_rfc3339(str(created_raw))


def evaluate_secret(
    key: str,
    metadata: dict[str, Any],
    now: datetime,
    default_expires_warn_before: timedelta,
) -> Finding | None:
    """Evaluate one secret's KV v2 metadata and return a Finding if actionable."""
    raw_custom = metadata.get("custom_metadata")
    if not raw_custom:
        return None
    if not isinstance(raw_custom, dict):
        return Finding(
            severity=Severity.INVALID,
            sort_time=now,
            key=key,
            reasons=("custom_metadata is not an object",),
        )

    custom: dict[str, str] = {
        str(k): str(v) for k, v in raw_custom.items() if v is not None
    }

    errors: list[str] = []
    for meta_key in sorted(custom):
        lower = meta_key.lower()
        if (lower.startswith("rotation_") or lower.startswith("expire")) and (
            meta_key not in KNOWN_META_KEYS
        ):
            errors.append(f"unknown metadata key {meta_key!r}")

    has_interval = "rotation_interval" in custom
    has_expires = "expires_at" in custom
    has_warn = "rotation_warn_before" in custom
    has_note = "rotation_note" in custom

    note = " ".join(custom.get("rotation_note", "").split())

    if not has_interval and not has_expires:
        if has_warn or has_note:
            errors.append(
                "rotation_warn_before/rotation_note set without "
                "rotation_interval or expires_at"
            )
        if errors:
            return Finding(
                severity=Severity.INVALID,
                sort_time=now,
                key=key,
                reasons=tuple(errors),
                note=note,
            )
        return None

    warn_override: timedelta | None = None
    if has_warn:
        try:
            warn_override = parse_duration(
                custom["rotation_warn_before"], allow_zero=True
            )
        except ValueError as exc:
            errors.append(f"rotation_warn_before: {exc}")

    severity: Severity | None = None
    earliest_time: datetime | None = None
    reasons: list[str] = []

    def record(sev: Severity, ts: datetime, reason: str) -> None:
        nonlocal severity, earliest_time
        reasons.append(reason)
        if severity is None or sev < severity:
            severity = sev
        if earliest_time is None or ts < earliest_time:
            earliest_time = ts

    if has_expires:
        try:
            expires_at = parse_expires_at(custom["expires_at"])
        except ValueError as exc:
            errors.append(f"expires_at: {exc}")
        else:
            warn_before = (
                warn_override
                if warn_override is not None
                else default_expires_warn_before
            )
            date_str = expires_at.strftime("%Y-%m-%d")
            if now >= expires_at:
                ago = format_delta(now - expires_at)
                record(
                    Severity.EXPIRED,
                    expires_at,
                    f"expired {date_str} ({ago} ago)",
                )
            elif now >= expires_at - warn_before:
                remaining = format_delta(expires_at - now)
                record(
                    Severity.UPCOMING,
                    expires_at,
                    f"expires {date_str} (in {remaining})",
                )

    if has_interval:
        try:
            interval = parse_duration(custom["rotation_interval"])
        except ValueError as exc:
            errors.append(f"rotation_interval: {exc}")
        else:
            try:
                created_at = _current_version_created(metadata, now)
            except ValueError as exc:
                errors.append(str(exc))
            else:
                due_at = created_at + interval
                warn_before = (
                    warn_override if warn_override is not None else timedelta(0)
                )
                date_str = due_at.strftime("%Y-%m-%d")
                interval_label = custom["rotation_interval"].strip()
                if now >= due_at:
                    overdue = format_delta(now - due_at)
                    record(
                        Severity.DUE,
                        due_at,
                        f"due {date_str} ({overdue} overdue, interval {interval_label})",
                    )
                elif warn_before > timedelta(0) and now >= due_at - warn_before:
                    remaining = format_delta(due_at - now)
                    record(
                        Severity.UPCOMING,
                        due_at,
                        f"due {date_str} (in {remaining}, interval {interval_label})",
                    )

    if errors:
        reasons.extend(f"metadata error: {e}" for e in errors)
        if severity is None:
            severity = Severity.INVALID
            earliest_time = now

    if severity is None:
        return None

    return Finding(
        severity=severity,
        sort_time=earliest_time or now,
        key=key,
        reasons=tuple(reasons),
        note=note,
    )


def format_notification(
    findings: list[Finding],
) -> tuple[str, str, str, str]:
    """Return (title, priority, tags, body) for an ntfy notification."""
    ordered = sorted(findings, key=lambda f: (f.severity, f.sort_time, f.key))
    has_expired = any(f.severity == Severity.EXPIRED for f in ordered)
    only_invalid = all(f.severity == Severity.INVALID for f in ordered)

    count = len(ordered)
    if count == 1:
        only = ordered[0]
        match only.severity:
            case Severity.EXPIRED:
                title = f"OpenBao: {only.key} expired"
            case Severity.DUE:
                title = f"OpenBao: {only.key} due for rotation"
            case Severity.UPCOMING:
                title = f"OpenBao: {only.key} expiring soon"
            case Severity.INVALID:
                title = f"OpenBao: {only.key} has invalid rotation metadata"
    elif has_expired:
        title = f"OpenBao: {count} secrets expired or due for rotation"
    elif only_invalid:
        title = f"OpenBao: {count} secrets have invalid rotation metadata"
    else:
        title = f"OpenBao: {count} secrets due for rotation"

    priority = "urgent" if has_expired else "default"
    tags = "rotating_light,key" if has_expired else "key"

    sections: list[str] = []
    for sev in Severity:
        group = [f for f in ordered if f.severity == sev]
        if not group:
            continue
        lines = [f"{SECTION_HEADERS[sev]}:"]
        for item in group:
            lines.append(f"- {item.key}: {'; '.join(item.reasons)}")
            if item.note:
                lines.append(f"  Note: {item.note}")
        sections.append("\n".join(lines))

    body = "\n\n".join(sections)
    encoded = body.encode("utf-8")
    if len(encoded) > MAX_NTFY_BODY_BYTES:
        suffix = "\n... (truncated; check pod logs for full list)"
        kept: list[str] = []
        used = 0
        for line in body.splitlines():
            line_bytes = len(line.encode("utf-8")) + 1
            if used + line_bytes > NTFY_TRUNCATE_LIMIT:
                break
            kept.append(line)
            used += line_bytes
        body = "\n".join(kept) + suffix

    return title, priority, tags, body


def publish_ntfy(
    ntfy_url: str,
    topic: str,
    *,
    title: str,
    priority: str,
    tags: str,
    body: str,
    click_url: str | None,
    timeout: float,
) -> None:
    clean_topic = topic.strip()
    if not clean_topic or any(c in clean_topic for c in "/ \t\r\n?&#"):
        raise NtfyError("NTFY_TOPIC is empty or contains invalid characters")

    parsed = urllib.parse.urlparse(ntfy_url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise NtfyError("NTFY_URL must be a valid http(s) URL")

    target = f"{ntfy_url.rstrip('/')}/{urllib.parse.quote(clean_topic, safe='')}"
    safe_title = title.encode("ascii", "replace").decode("ascii")
    headers = {
        "Title": safe_title,
        "Priority": priority,
        "Tags": tags,
        "Content-Type": "text/plain; charset=utf-8",
    }
    if click_url:
        headers["Click"] = click_url

    opener = urllib.request.build_opener(
        _NoRedirectHandler(),
        urllib.request.HTTPSHandler(context=build_ssl_context(None)),
    )
    req = urllib.request.Request(
        target, data=body.encode("utf-8"), headers=headers, method="POST"
    )
    try:
        with opener.open(req, timeout=timeout) as resp:
            resp.read(1024)
    except urllib.error.HTTPError as exc:
        # Never log target URL because it contains the private NTFY_TOPIC.
        raise NtfyError(f"ntfy publish failed with HTTP {exc.code}") from None
    except urllib.error.URLError as exc:
        raise NtfyError(f"ntfy publish failed: {exc.reason}") from None


def _require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ValueError(f"required environment variable {name} is not set")
    return value


def _read_credential(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as fh:
            value = fh.read().strip()
    except OSError as exc:
        raise OpenBaoError(f"failed to read JWT file {path}: {exc}") from None
    if not value:
        raise OpenBaoError(f"JWT file {path} is empty")
    return value


def main() -> int:
    try:
        address = _require_env("OPENBAO_ADDR")
        mount = _require_env("OPENBAO_MOUNT")
        role = _require_env("OPENBAO_K8S_ROLE")
        auth_mount = os.environ.get("OPENBAO_K8S_AUTH_MOUNT", "kubernetes")
        ca_file = os.environ.get("OPENBAO_CA_FILE") or None
        jwt_file = os.environ.get(
            "OPENBAO_JWT_FILE", "/var/run/secrets/openbao/token"
        )
        ui_url = os.environ.get("OPENBAO_UI_URL", "").strip() or None
        ntfy_url = os.environ.get("NTFY_URL", "https://ntfy.sh").strip()
        dry_run = os.environ.get("DRY_RUN", "").strip().lower() in (
            "1",
            "true",
            "yes",
        )
        ntfy_topic = (
            os.environ.get("NTFY_TOPIC", "").strip()
            if dry_run
            else _require_env("NTFY_TOPIC")
        )
        default_warn = parse_duration(
            os.environ.get("DEFAULT_EXPIRES_WARN_BEFORE", "14d"),
            allow_zero=True,
        )
        timeout = float(os.environ.get("HTTP_TIMEOUT_SECONDS", "10"))
    except ValueError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 1

    now = datetime.now(timezone.utc)
    client = OpenBaoClient(address, mount, ca_file, timeout)

    token: str | None = None
    revocable = False
    findings: list[Finding] = []
    scanned = 0
    tracked = 0

    try:
        jwt = _read_credential(jwt_file)
        token, revocable = client.login_kubernetes(auth_mount, role, jwt)
        keys = client.list_keys(token)
        for key in keys:
            scanned += 1
            metadata = client.read_metadata(token, key)
            if metadata is None:
                continue
            custom = metadata.get("custom_metadata") or {}
            if isinstance(custom, dict) and any(
                str(k).lower().startswith(("rotation_", "expire"))
                for k in custom
            ):
                tracked += 1
            finding = evaluate_secret(key, metadata, now, default_warn)
            if finding is not None:
                findings.append(finding)
    except OpenBaoError as exc:
        print(f"openbao error: {exc}", file=sys.stderr)
        return 1
    finally:
        if token and revocable:
            try:
                client.revoke_self(token)
            except OpenBaoError as exc:
                print(
                    f"warning: token revocation failed: {exc}",
                    file=sys.stderr,
                )

    print(
        f"scanned={scanned} tracked={tracked} findings={len(findings)}",
        flush=True,
    )
    if not findings:
        return 0

    title, priority, tags, body = format_notification(findings)
    print(f"--- {title} (priority={priority}) ---\n{body}", flush=True)

    if dry_run:
        return 0

    try:
        publish_ntfy(
            ntfy_url,
            ntfy_topic,
            title=title,
            priority=priority,
            tags=tags,
            body=body,
            click_url=ui_url,
            timeout=timeout,
        )
    except NtfyError as exc:
        print(f"notification error: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
