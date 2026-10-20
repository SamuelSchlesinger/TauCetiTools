"""Small shared filesystem and system-CA helpers."""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Mapping, MutableMapping
from pathlib import Path

_COMMON_CA_BUNDLES = (
    "/etc/ssl/certs/ca-certificates.crt",  # Debian, Ubuntu, NixOS
    "/etc/pki/tls/certs/ca-bundle.crt",  # Fedora, RHEL
    "/etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem",
    "/etc/ssl/ca-bundle.pem",  # openSUSE
    "/etc/ssl/cert.pem",  # macOS, Alpine
)


def _ssl_cert_candidates(env: Mapping[str, str]) -> tuple[str | None, ...]:
    # get_default_verify_paths().cafile is shadowed by the process's SSL_CERT_FILE, which may
    # differ from *env*. openssl_cafile is the interpreter's environment-independent default.
    import ssl

    openssl_default = ssl.get_default_verify_paths().openssl_cafile
    return (env.get("NIX_SSL_CERT_FILE"), openssl_default, *_COMMON_CA_BUNDLES)


def ensure_ssl_cert_file(env: MutableMapping[str, str] | None = None) -> str | None:
    """Put a usable system CA bundle in *env* when its Python has no working default.

    Preserve an explicit ``SSL_CERT_FILE`` even when it is unusual: operator configuration wins.
    Otherwise prefer Nix's advertised bundle, the interpreter's existing default, then common
    system locations. Return the selected path, or ``None`` when no usable bundle can be found.
    """
    base = os.environ if env is None else env
    configured = base.get("SSL_CERT_FILE")
    if configured:
        return configured
    for raw in _ssl_cert_candidates(base):
        if not raw:
            continue
        try:
            candidate = Path(raw).expanduser()
            if candidate.is_file():
                base["SSL_CERT_FILE"] = str(candidate)
                return str(candidate)
        except (OSError, RuntimeError):
            continue
    return None


def atomic(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with open(tmp, "w", opener=lambda name, flags: os.open(name, flags, 0o600)) as f:
            json.dump(value, f)
            f.flush()
            os.fsync(f.fileno())
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)
