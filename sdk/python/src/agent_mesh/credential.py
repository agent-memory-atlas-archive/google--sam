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

import base64
import json
import time
from dataclasses import dataclass, field

from ._proto import sam_pb2 as pb


@dataclass(frozen=True)
class MeshCredential:
    """What a member holds after enrolling: its biscuit and what it trusts."""

    control_plane_url: str
    # The biscuit the control plane minted for this identity.
    biscuit: bytes
    # Unix seconds at which the biscuit expires.
    expiration: int
    # Every control plane signing key currently trusted (rotation keeps several valid).
    control_plane_keys: list[bytes] = field(default_factory=list)
    # Router multiaddrs, `/p2p/<peer id>` suffixed, as handed out at enrollment.
    router_addresses: list[str] = field(default_factory=list)

    def time_to_live_seconds(self, now: float | None = None) -> int:
        """Seconds of validity left on the biscuit; negative once expired."""
        return self.expiration - int(time.time() if now is None else now)

    def to_json(self) -> str:
        return json.dumps(
            {
                "control_plane_url": self.control_plane_url,
                "biscuit": base64.b64encode(self.biscuit).decode(),
                "expiration": self.expiration,
                "control_plane_keys": [base64.b64encode(k).decode() for k in self.control_plane_keys],
                "router_addresses": list(self.router_addresses),
            },
            indent=2,
        ) + "\n"

    @classmethod
    def from_json(cls, text: str) -> "MeshCredential":
        raw = json.loads(text)
        try:
            return cls(
                control_plane_url=str(raw["control_plane_url"]),
                biscuit=base64.b64decode(raw["biscuit"]),
                expiration=int(raw["expiration"]),
                control_plane_keys=[base64.b64decode(k) for k in raw.get("control_plane_keys", [])],
                router_addresses=[str(a) for a in raw.get("router_addresses", [])],
            )
        except (KeyError, TypeError, ValueError) as err:
            raise ValueError("malformed credential file") from err


def encode_auth_frame(biscuit: bytes, target_service: str = "", agent: str = "") -> bytes:
    """The first frame on every mesh stream (/sam/auth/1.0.0, /sam/mcp/1.0.0):
    the caller's biscuit, the service it wants and the agent it speaks for.
    Framing (varint length prefix) is the transport's job."""
    return pb.AuthFrame(biscuit=biscuit, target_service=target_service, agent=agent).SerializeToString()


def decode_auth_response(data: bytes) -> pb.AuthResponse:
    """The peer's answer to an AuthFrame, carrying its own biscuit on success."""
    return pb.AuthResponse.FromString(data)
