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

import json
import stat
import time
import urllib.parse

import pytest

from agent_mesh._proto import sam_pb2 as pb
from agent_mesh.credential import decode_auth_response
from agent_mesh.identity import Identity
from agent_mesh.mesh import AgentMesh

CP_KEY = Identity.generate()


class FakeControlPlane:
    """Approves everything and hands out numbered biscuits."""

    def __init__(self, keys_ok=True):
        self.issued = 0
        self.keys_ok = keys_ok

    def _signed_keys(self):
        ts = int(time.time() * 1000)
        unsigned = pb.KeysResponse(public_keys=[CP_KEY.public_key_raw], timestamp=ts)
        return pb.KeysResponse(public_keys=[CP_KEY.public_key_raw], timestamp=ts, signatures=[CP_KEY.sign(unsigned.SerializeToString(deterministic=True))])

    def transport(self, method, url, headers, body):
        path = urllib.parse.urlsplit(url).path
        if (method, path) == ("POST", "/enroll"):
            self.issued += 1
            return 200, pb.BootstrapEnrollResponse(
                status=pb.ENROLLMENT_STATUS_APPROVED,
                biscuit_token=f"biscuit-{self.issued}".encode(),
                control_plane_public_key=CP_KEY.public_key_raw,
                router_addresses=["/dns4/router.example/tcp/4001/p2p/12D3KooWP8iKhDf3iCMo2H3butNVfdTUtYwYWYQ75jTGnynXPFMp"],
                expiration=int(time.time()) + 3600,
            ).SerializeToString()
        if (method, path) == ("POST", "/refresh"):
            self.issued += 1
            return 200, pb.TokenRefreshResponse(biscuit_token=f"biscuit-{self.issued}".encode(), expires_at=int(time.time()) + 7200).SerializeToString()
        if (method, path) == ("GET", "/keys"):
            return (200, self._signed_keys().SerializeToString()) if self.keys_ok else (500, b"boom")
        return 404, f"no route for {method} {path}".encode()


def test_enroll_persists_load_resumes_refresh_rotates(tmp_path):
    cp = FakeControlPlane()
    token_path = tmp_path / "bootstrap.token"
    token_path.write_text("sbt_secret\n")
    state = tmp_path / "state"

    mesh = AgentMesh.enroll("http://127.0.0.1:1", bootstrap_token_path=token_path, state_dir=state, transport=cp.transport)
    assert mesh.credential.biscuit == b"biscuit-1"
    assert mesh.credential.control_plane_keys == [CP_KEY.public_key_raw]
    assert len(mesh.credential.router_addresses) == 1
    assert mesh.credential.time_to_live_seconds() > 3500

    # Secrets on disk are owner-only.
    for name in ("identity.key", "credential.json"):
        assert stat.S_IMODE((state / name).stat().st_mode) == 0o600, name
    assert stat.S_IMODE(state.stat().st_mode) == 0o700

    resumed = AgentMesh.load(state, transport=cp.transport)
    assert resumed.peer_id == mesh.peer_id
    assert resumed.credential.biscuit == b"biscuit-1"
    assert resumed.control_plane.url == "http://127.0.0.1:1"

    resumed.refresh()
    assert resumed.credential.biscuit == b"biscuit-2"
    on_disk = json.loads((state / "credential.json").read_text())
    assert on_disk["biscuit"] == "YmlzY3VpdC0y"  # base64("biscuit-2")

    # Re-enrolling from the same directory keeps the identity.
    again = AgentMesh.enroll("http://127.0.0.1:1", bootstrap_token="sbt_secret", state_dir=state, transport=cp.transport)
    assert again.peer_id == mesh.peer_id


def test_enroll_without_state_dir_keeps_enrollment_key_when_keys_fails():
    cp = FakeControlPlane(keys_ok=False)
    mesh = AgentMesh.enroll("http://127.0.0.1:1", bootstrap_token="sbt_secret", transport=cp.transport)
    assert mesh.credential.control_plane_keys == [CP_KEY.public_key_raw]
    mesh.save()
    mesh.refresh()
    assert mesh.credential.biscuit == b"biscuit-2"


def test_enroll_refuses_ambiguous_credentials():
    cp = FakeControlPlane()
    with pytest.raises(ValueError, match="exactly one of"):
        AgentMesh.enroll("http://127.0.0.1:1", transport=cp.transport)
    with pytest.raises(ValueError, match="exactly one of"):
        AgentMesh.enroll("http://127.0.0.1:1", bootstrap_token="a", jwt="b", transport=cp.transport)
    assert cp.issued == 0


def test_load_without_identity_says_enroll_first(tmp_path):
    with pytest.raises(FileNotFoundError, match="enroll first"):
        AgentMesh.load(tmp_path)


def test_auth_frame_is_the_protobuf_with_this_members_biscuit():
    cp = FakeControlPlane()
    mesh = AgentMesh.enroll("http://127.0.0.1:1", bootstrap_token="sbt_secret", transport=cp.transport)
    frame = pb.AuthFrame.FromString(mesh.auth_frame("mcp://calculator", "agent:acme.example:bot"))
    assert frame.biscuit == b"biscuit-1"
    assert frame.target_service == "mcp://calculator"
    assert frame.agent == "agent:acme.example:bot"

    resp = decode_auth_response(pb.AuthResponse(success=False, error="denied").SerializeToString())
    assert resp.success is False
    assert resp.error == "denied"
