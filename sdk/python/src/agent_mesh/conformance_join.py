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

"""Mesh member driven by tests/integration/sdk_mesh_test.go: enrolls, joins
the mesh through a real router, prints one JSON line describing the session,
then takes JSON commands on stdin, one per line, until stdin closes:

  {"cmd": "auth", "addr": "<multiaddr>"}  connect (through a relay when the
                                           address says /p2p-circuit) and run
                                           the auth handshake
  {"cmd": "peers"}                          peers that authenticated to us
  {"cmd": "quit"}                           leave the mesh and exit

Each command gets one JSON line back. Same environment as conformance.py; the
JavaScript SDK ships the same runner (dist/conformance-join.js).
"""

import base64
import json
import logging
import os
import sys

import trio

from .mesh import AgentMesh
from .session import MeshSession


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"{name} is required")
    return value


def _emit(obj: dict) -> None:
    print(json.dumps(obj), flush=True)


async def _handle(session: MeshSession, command: dict) -> dict:
    cmd = command.get("cmd")
    if cmd == "auth":
        try:
            verified = await session.authenticate(command["addr"])
        except Exception as err:  # noqa: BLE001 - the driver wants the failure, not a dead runner
            return {"cmd": cmd, "ok": False, "error": f"{type(err).__name__}: {err}"}
        return {
            "cmd": cmd,
            "ok": True,
            "peer_id": verified.peer_id,
            "roles": verified.roles,
            "labels": verified.labels,
            "expiration": int(verified.expiration.timestamp()),
        }
    if cmd == "peers":
        return {"cmd": cmd, "authenticated_peers": sorted(session.authenticated_peers)}
    return {"cmd": cmd, "ok": False, "error": f"unknown command {cmd!r}"}


async def main() -> None:
    # stdout carries the protocol lines only; every log goes to stderr.
    logging.basicConfig(stream=sys.stderr, level=logging.WARNING, force=True)
    control_plane_url = _require_env("SAM_CONTROL_PLANE_URL")
    bootstrap_token_path = _require_env("SAM_BOOTSTRAP_TOKEN_PATH")
    state_dir = _require_env("SAM_SDK_STATE_DIR")
    allow_insecure = os.environ.get("SAM_INSECURE_CONTROL_PLANE") == "1"
    listen = [a for a in os.environ.get("SAM_SDK_LISTEN_ADDRS", "").split(",") if a]

    mesh = AgentMesh.enroll(
        control_plane_url,
        bootstrap_token_path=bootstrap_token_path,
        state_dir=state_dir,
        allow_insecure=allow_insecure,
        poll_interval=0.2,
    )
    async with mesh.join(listen_addrs=listen) as session:
        _emit(
            {
                "sdk": "python",
                "peer_id": session.peer_id,
                "routers": [{"peer_id": r.peer_id, "addr": str(r.addr), "roles": r.credential.roles} for r in session.routers],
                "relay_addresses": session.relay_addresses,
                "direct_addresses": [str(a) for a in session.host.get_addrs()],
                "biscuit": base64.b64encode(mesh.credential.biscuit).decode(),
            }
        )
        while True:
            line = await trio.to_thread.run_sync(sys.stdin.readline)
            if not line:
                break
            line = line.strip()
            if not line:
                continue
            try:
                command = json.loads(line)
            except json.JSONDecodeError as err:
                _emit({"ok": False, "error": f"not JSON: {err}"})
                continue
            if command.get("cmd") == "quit":
                _emit({"cmd": "quit", "ok": True})
                break
            _emit(await _handle(session, command))


if __name__ == "__main__":
    trio.run(main)
