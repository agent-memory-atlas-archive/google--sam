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

from __future__ import annotations

import os
import threading
from dataclasses import replace
from pathlib import Path
from typing import Mapping, Optional

from .controlplane import ROLE_NODE, ControlPlaneClient, Enrollment, Transport
from .credential import MeshCredential, encode_auth_frame
from .identity import Identity

_IDENTITY_FILE = "identity.key"
_CREDENTIAL_FILE = "credential.json"


class AgentMesh:
    """A member of the mesh: an identity, the credential the control plane
    minted for it, and the client that keeps that credential fresh. join()
    puts it on the mesh over libp2p."""

    def __init__(self, identity: Identity, control_plane: ControlPlaneClient, credential: MeshCredential, state_dir: Optional[Path]):
        self.identity = identity
        self.control_plane = control_plane
        self._credential = credential
        self._state_dir = state_dir

    @property
    def peer_id(self) -> str:
        return self.identity.peer_id

    @property
    def credential(self) -> MeshCredential:
        return self._credential

    @classmethod
    def enroll(
        cls,
        control_plane_url: str,
        *,
        bootstrap_token: Optional[str] = None,
        bootstrap_token_path: Optional[str | os.PathLike[str]] = None,
        jwt: Optional[str] = None,
        state_dir: Optional[str | os.PathLike[str]] = None,
        identity: Optional[Identity] = None,
        role: str = ROLE_NODE,
        labels: Optional[Mapping[str, str]] = None,
        allow_insecure: bool = False,
        poll_interval: Optional[float] = None,
        cancel: Optional[threading.Event] = None,
        transport: Optional[Transport] = None,
    ) -> "AgentMesh":
        """Enrolls with the control plane and returns a member holding a credential.
        Exactly one of bootstrap_token, bootstrap_token_path or jwt must be given.
        A bootstrap token is better read from a file than passed as a value."""
        given = sum(v is not None for v in (bootstrap_token, bootstrap_token_path, jwt))
        if given != 1:
            raise ValueError("exactly one of bootstrap_token, bootstrap_token_path or jwt is required")
        state = Path(state_dir) if state_dir is not None else None
        identity = identity or _load_identity(state) or Identity.generate()
        control_plane = ControlPlaneClient(control_plane_url, allow_insecure=allow_insecure, transport=transport)

        enrollment: Enrollment
        if jwt is not None:
            enrollment = control_plane.register(identity, jwt, role=role, labels=labels)
        else:
            if bootstrap_token_path is not None:
                bootstrap_token = Path(bootstrap_token_path).read_text().strip()
            assert bootstrap_token is not None
            enrollment = control_plane.enroll_bootstrap(
                identity, bootstrap_token, role=role, labels=labels, poll_interval=poll_interval, cancel=cancel
            )

        # Widen trust from the one key the enrollment carries to every key the
        # control plane currently signs with, so peers holding credentials from
        # a retiring key still verify. Best effort, as in sam-node.
        control_plane_keys = [enrollment.control_plane_public_key]
        try:
            control_plane_keys = control_plane.keys(control_plane_keys)
        except Exception:  # noqa: BLE001 - the enrollment key alone still works until the next sync
            pass

        mesh = cls(
            identity,
            control_plane,
            MeshCredential(
                control_plane_url=control_plane.url,
                biscuit=enrollment.biscuit,
                expiration=enrollment.expiration,
                control_plane_keys=control_plane_keys,
                router_addresses=list(enrollment.router_addresses),
            ),
            state,
        )
        mesh.save()
        return mesh

    @classmethod
    def load(
        cls,
        state_dir: str | os.PathLike[str],
        *,
        identity: Optional[Identity] = None,
        allow_insecure: bool = False,
        transport: Optional[Transport] = None,
    ) -> "AgentMesh":
        """Resumes a member from a state directory written by an earlier enroll()."""
        state = Path(state_dir)
        identity = identity or _load_identity(state)
        if identity is None:
            raise FileNotFoundError(f"no identity in {state}; enroll first")
        credential = MeshCredential.from_json((state / _CREDENTIAL_FILE).read_text())
        control_plane = ControlPlaneClient(credential.control_plane_url, allow_insecure=allow_insecure, transport=transport)
        return cls(identity, control_plane, credential, state)

    def refresh(self) -> MeshCredential:
        """Trades the current biscuit for a fresh one and persists it. The control
        plane redeems only the last biscuit it issued, so a lost refresh result
        means re-enrolling; persisting before returning keeps that rare."""
        result = self.control_plane.refresh(self.identity, self._credential.biscuit)
        control_plane_keys = self._credential.control_plane_keys
        try:
            control_plane_keys = self.control_plane.keys(control_plane_keys)
        except Exception:  # noqa: BLE001 - a failed /keys sync must not cost the new biscuit
            pass
        self._credential = replace(
            self._credential, biscuit=result.biscuit, expiration=result.expiration, control_plane_keys=control_plane_keys
        )
        self.save()
        return self._credential

    def auth_frame(self, target_service: str = "", agent: str = "") -> bytes:
        """The frame that opens every stream to a peer: this member's biscuit plus
        the service it wants (e.g. "mcp://calculator") and the agent it speaks for."""
        return encode_auth_frame(self._credential.biscuit, target_service, agent)

    def join(self, **options):  # type: ignore[no-untyped-def]
        """Joins the mesh: connects to the routers in the credential, passes the
        auth handshake with them, reserves a relay slot and keeps the credential
        fresh. An async context manager to use under trio:

            async with mesh.join() as session: ...
        """
        from .session import join_mesh

        return join_mesh(self, **options)

    def save(self) -> None:
        """Writes identity and credential to the state directory, if one is configured."""
        if self._state_dir is None:
            return
        self._state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        _write_atomic(self._state_dir / _IDENTITY_FILE, self.identity.to_libp2p_private_key())
        _write_atomic(self._state_dir / _CREDENTIAL_FILE, self._credential.to_json().encode())


def _load_identity(state: Optional[Path]) -> Optional[Identity]:
    if state is None:
        return None
    try:
        return Identity.from_libp2p_private_key((state / _IDENTITY_FILE).read_bytes())
    except FileNotFoundError:
        return None


def _write_atomic(path: Path, data: bytes) -> None:
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    os.replace(tmp, path)
