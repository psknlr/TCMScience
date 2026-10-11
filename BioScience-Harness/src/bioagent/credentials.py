"""Credentials for the sources that need a person's own account.

Some services answer only a registered user: IEU OpenGWAS asks for a personal token on
every data endpoint, for example. TCMScience never holds such a credential itself. A
person who has one, and has accepted the service's terms, gives it to the process that
calls the service through an environment variable the source's card names (the runner, or
a CI job's secret). From there:

* it is sent only to the source's own address, only over https, and only as the header
  the card names (never in a URL, where it would end up in records and logs);
* it is read when the call is made, never written: not to a cache, a record, a log, a job's
  environment, or anything shown to a model;
* where it is not set, the call is refused before any request, with how to obtain one and
  the terms that come with it; nothing is tried without it.
"""

from __future__ import annotations

import os
import urllib.parse
from dataclasses import dataclass
from typing import Any, Mapping

__all__ = ["Credential", "CredentialMissing", "PREFIX", "configured", "describe",
           "headers_for"]

#: every credential's environment variable starts with this, and the runner keeps them
#: out of the environments of the jobs it starts
PREFIX = "BIOAGENT_CREDENTIAL_"


@dataclass(frozen=True)
class Credential:
    """What a source needs, and how it is sent. ``env`` is the variable that holds it."""

    env: str
    header: str = "Authorization"
    template: str = "Bearer {}"
    #: how a person obtains one (where to register or log in)
    obtain: str = ""
    #: what they agree to by obtaining one (the service's terms, in short)
    terms: str = ""

    def __post_init__(self) -> None:
        if not self.env.startswith(PREFIX):
            raise ValueError(f"credential variable {self.env!r} must start with {PREFIX}")
        if "{}" not in self.template:
            raise ValueError("a credential template must hold the value's place: {}")


class CredentialMissing(RuntimeError):
    """The source needs a credential that is not configured here, or may not be sent."""


def configured(cred: Credential, environ: Mapping[str, str] | None = None) -> bool:
    env = os.environ if environ is None else environ
    return bool(str(env.get(cred.env, "")).strip())


def describe(cred: Credential, environ: Mapping[str, str] | None = None) -> dict[str, Any]:
    """What may be shown of a credential: whether it is set, never its value."""
    return {"env": cred.env, "configured": configured(cred, environ), "header": cred.header,
            "obtain": cred.obtain, "terms": cred.terms}


def headers_for(cred: Credential, url: str, host: str,
                environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """The header that carries the credential to ``url``. Refused (``CredentialMissing``)
    when it is not set, when the URL is not https, or when it is not the source's own
    host."""
    env = os.environ if environ is None else environ
    value = str(env.get(cred.env, "")).strip()
    if not value:
        raise CredentialMissing(
            f"this source answers only a registered user, and no credential is set here: "
            f"set {cred.env} on the machine that calls it (the runner)"
            + (f"; {cred.obtain}" if cred.obtain else "")
            + (f". By obtaining one you accept: {cred.terms}" if cred.terms else ""))
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https":
        raise CredentialMissing(f"a credential is sent only over https, not to {url}")
    if (parts.hostname or "").lower() != host.lower():
        raise CredentialMissing(f"a credential is sent only to the source's own host "
                                f"({host}), not to {parts.hostname}")
    if any(c in value for c in "\r\n\0"):
        raise CredentialMissing(f"{cred.env} holds a line break; it is not sent")
    return {cred.header: cred.template.format(value)}
