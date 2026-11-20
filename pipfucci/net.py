"""HTTPS for update checks and downloads.

A frozen Python app has no certificate store of its own, so plain urllib fails on a Mac with
"CERTIFICATE_VERIFY_FAILED: unable to get local issuer certificate". We try, in order: the Mac's
own trust store (truststore: same certificates as Safari, including a university network's own
root certificate), then the Mozilla bundle shipped with the app (certifi), then Python's default.
"""
from __future__ import annotations

import ssl
import urllib.error
import urllib.request

_good: list = []  # the context that worked last time, tried first


def _contexts():
    out = []
    try:
        import truststore

        out.append(("system", truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)))
    except Exception:
        pass
    try:
        import certifi

        out.append(("certifi", ssl.create_default_context(cafile=certifi.where())))
    except Exception:
        pass
    out.append(("default", ssl.create_default_context()))
    return out


def _is_cert_error(e: BaseException) -> bool:
    r = getattr(e, "reason", e)
    return isinstance(r, ssl.SSLError) or isinstance(e, ssl.SSLError)


def urlopen(req, timeout: float = 20):
    """urllib.request.urlopen that verifies certificates with whatever trust store works here."""
    ctxs = _contexts()
    if _good:
        ctxs.sort(key=lambda kc: kc[0] != _good[0])
    last = None
    for name, ctx in ctxs:
        try:
            r = urllib.request.urlopen(req, timeout=timeout, context=ctx)
            _good[:] = [name]
            return r
        except urllib.error.HTTPError:
            raise  # the server answered: certificates are fine
        except Exception as e:  # noqa: BLE001
            if not _is_cert_error(e):
                raise
            last = e
    raise last


def check(url: str = "https://api.github.com/") -> str:
    """For the build machine and support: which trust store works ("system", "certifi", ...)."""
    req = urllib.request.Request(url, headers={"User-Agent": "CycleTrack"})
    with urlopen(req, timeout=20) as r:
        r.read(64)
    return _good[0] if _good else "?"
