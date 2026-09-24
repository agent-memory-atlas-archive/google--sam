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
from pathlib import Path

import pytest

from agent_mesh import base58
from agent_mesh.challenges import enroll_challenge
from agent_mesh.identity import Identity, canonical_peer_id, libp2p_public_key, peer_id_from_public_key, verify_ed25519

VECTORS = json.loads((Path(__file__).resolve().parents[2] / "testdata" / "identity_vectors.json").read_text())["vectors"]


@pytest.mark.parametrize("v", VECTORS, ids=[v["peer_id"] for v in VECTORS])
def test_identity_matches_go_libp2p(v):
    identity = Identity.from_seed(bytes.fromhex(v["seed"]))
    assert identity.public_key_raw.hex() == v["public_key"]
    assert identity.peer_id == v["peer_id"]
    assert identity.libp2p_public_key.hex() == v["libp2p_public_key"]
    assert identity.to_libp2p_private_key().hex() == v["libp2p_private_key"]
    assert peer_id_from_public_key(bytes.fromhex(v["public_key"])) == v["peer_id"]
    # ed25519 signatures are deterministic, so the Go signature is reproducible.
    _, _, peer, ts = v["challenge"].split(":")
    assert identity.sign(enroll_challenge(peer, int(ts))).hex() == v["signature"]
    assert verify_ed25519(identity.public_key_raw, v["challenge"].encode(), bytes.fromhex(v["signature"]))


def test_libp2p_private_key_round_trips_and_rejects_mismatch():
    identity = Identity.generate()
    assert Identity.from_libp2p_private_key(identity.to_libp2p_private_key()).peer_id == identity.peer_id

    tampered = bytearray(identity.to_libp2p_private_key())
    tampered[-1] ^= 0xFF
    with pytest.raises(ValueError, match="public half does not match"):
        Identity.from_libp2p_private_key(bytes(tampered))
    with pytest.raises(ValueError, match="not a libp2p ed25519 private key"):
        Identity.from_libp2p_private_key(bytes(tampered)[1:])


def test_generated_identities_are_distinct_and_self_verify():
    a, b = Identity.generate(), Identity.generate()
    assert a.peer_id != b.peer_id
    sig = a.sign(b"hello")
    assert a.verify(b"hello", sig)
    assert not b.verify(b"hello", sig)
    assert not verify_ed25519(b"\x00" * 31, b"hello", sig)


def test_libp2p_public_key_refuses_wrong_size():
    with pytest.raises(ValueError, match="32 bytes"):
        libp2p_public_key(b"\x00" * 33)


def test_canonical_peer_id_is_the_base58_form_for_every_encoding():
    # The CIDv1 form of a known ed25519 peer, as `peer.ToCid(id).String()` prints it.
    b58 = "12D3KooWA4Xop1JaT3MHxwYMkCepYsv4iPVopMXwCz5iHYdBfeSB"
    cidv1 = "bafzaajaiaejcaa5ba677htqqxyoxbxiy45f4bglh4tldbg5fbvpr3xegmqjfkmny"
    assert canonical_peer_id(b58) == b58
    assert canonical_peer_id(cidv1) == b58
    for bad in ("not-a-peer", ""):
        with pytest.raises(ValueError, match="is not a peer ID"):
            canonical_peer_id(bad)


@pytest.mark.parametrize("data", [b"", b"\x00", b"\x00\x00\x01\x02", bytes.fromhex("00ff"), bytes.fromhex("deadbeef")])
def test_base58_round_trips(data):
    assert base58.decode(base58.encode(data)) == data


def test_base58_known_vector_and_invalid_character():
    assert base58.encode(b"Hello World!") == "2NEpo7TZRRrLZSi2U"
    with pytest.raises(ValueError, match="invalid base58 character"):
        base58.decode("0OIl")
