# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Verification of a peer's biscuit, mirroring internal/identity.verifyBiscuit:
signed by a trusted control plane key, authority block only, unexpired, and
bound to the peer at the other end of the connection."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional, Sequence

import biscuit_auth as ba

ROLE_ROUTER = "sam:role:router"

# Datalog evaluation budget, as in internal/identity.AuthorizerOptions.
AUTHORIZER_MAX_FACTS = 1000
AUTHORIZER_MAX_ITERATIONS = 100
AUTHORIZER_MAX_TIME = timedelta(seconds=1)

_EXPIRATION_CHECK = "check if time($t), expiration($e), $t <= $e"


class BiscuitVerificationError(Exception):
    pass


@dataclass(frozen=True)
class VerifiedBiscuit:
    """What a verified peer biscuit says about its holder."""

    # The peer the token is bound to (its node() fact).
    peer_id: str
    # When the token lapses; the earliest expiration() fact.
    expiration: datetime
    # The trusted key that verified the signature.
    verifying_key: bytes
    roles: list[str] = field(default_factory=list)
    labels: dict[str, str] = field(default_factory=dict)


def _limits() -> ba.AuthorizerLimits:
    limits = ba.AuthorizerBuilder().limits()
    limits.max_facts = AUTHORIZER_MAX_FACTS
    limits.max_iterations = AUTHORIZER_MAX_ITERATIONS
    limits.max_time = AUTHORIZER_MAX_TIME
    return limits


def verify_peer_biscuit(
    biscuit: bytes,
    expected_peer_id: str,
    trusted_keys: Sequence[bytes],
    now: Optional[datetime] = None,
) -> VerifiedBiscuit:
    """Verifies a biscuit received from expected_peer_id over an authenticated
    connection. Every trusted key is tried, so a token minted under a retiring
    key still verifies during rotation."""
    if not trusted_keys:
        raise BiscuitVerificationError("no trusted control plane key to verify against")
    now = now or datetime.now(timezone.utc)

    token = None
    verifying_key = b""
    last_err: Exception | None = None
    for key in trusted_keys:
        try:
            token = ba.Biscuit.from_bytes(biscuit, ba.PublicKey.from_bytes(bytes(key), ba.Algorithm.Ed25519))
            verifying_key = bytes(key)
            break
        except Exception as err:  # noqa: BLE001 - biscuit-python raises several types here
            last_err = err
    if token is None:
        raise BiscuitVerificationError(f"biscuit is not signed by a trusted control plane key: {last_err}")

    # Appending needs no root key, so appended blocks are the one place a
    # holder can put Datalog of their own. SAM tokens are authority-only.
    if token.block_count() != 1:
        raise BiscuitVerificationError(
            f"biscuit carries appended blocks; SAM tokens are authority-block only ({token.block_count() - 1})"
        )

    builder = ba.AuthorizerBuilder()
    builder.set_limits(_limits())
    builder.add_fact(ba.Fact("time({now})", {"now": now}))
    builder.add_check(ba.Check(_EXPIRATION_CHECK))
    builder.add_policy(ba.Policy("allow if true"))
    authorizer = builder.build(token)
    try:
        authorizer.authorize()
    except Exception as err:  # noqa: BLE001
        raise BiscuitVerificationError(f"biscuit is expired or fails its checks: {err}") from err

    def strings(rule: str) -> list[str]:
        return [f.terms[0] for f in authorizer.query(ba.Rule(rule)) if isinstance(f.terms[0], str)]

    if expected_peer_id not in strings("p($p) <- node($p)"):
        raise BiscuitVerificationError(f"biscuit is not bound to peer {expected_peer_id}")

    expirations = [f.terms[0] for f in authorizer.query(ba.Rule("e($e) <- expiration($e)")) if isinstance(f.terms[0], datetime)]
    if not expirations:
        raise BiscuitVerificationError("biscuit carries no expiration fact")

    labels = {
        f.terms[0]: f.terms[1]
        for f in authorizer.query(ba.Rule("l($k, $v) <- label($k, $v)"))
        if isinstance(f.terms[0], str) and isinstance(f.terms[1], str)
    }

    return VerifiedBiscuit(
        peer_id=expected_peer_id,
        expiration=min(expirations),
        verifying_key=verifying_key,
        roles=strings("r($r) <- role($r)"),
        labels=labels,
    )


def require_role(verified: VerifiedBiscuit, role: str) -> None:
    """Requires role(<role>) on an already verified token, as identity.RequireRole."""
    if role not in verified.roles:
        raise BiscuitVerificationError(f"biscuit lacks expected role {role!r}")
