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

"""What a dial must do that py-libp2p does not: resolve a /dnsaddr router
address through its TXT records and keep the transports this host has
(dial_addrs), and give up on an address nobody answers (dial)."""

import multiaddr
import pytest
import trio
import trio.testing
from libp2p.peer.id import ID
from libp2p.peer.peerinfo import PeerInfo
from multiaddr.resolvers import DNSResolver

from agent_mesh.host import dial_addrs, peer_info
from agent_mesh.session import DIAL_TIMEOUT, dial

ROUTER = "12D3KooWG1pA6goegCncqwbZLSr8pnjUZ6JMAAe6SmnHTgUNCk88"
OTHER = "12D3KooWGvdRCJLYATauVWfsieF2j3a2wXZoEQJUS2MsvRdDtgLM"


class FakeTXT:
    """A dnspython answer: one TXT record per string."""

    def __init__(self, strings):
        self._strings = strings

    def __iter__(self):
        for s in self._strings:
            yield type("TXT", (), {"strings": [s.encode()]})()

    def __len__(self):
        return len(self._strings)


@pytest.fixture
def testnet_dns(monkeypatch):
    """The _dnsaddr records a testnet publishes: two routers, TCP and QUIC each."""
    records = {
        "_dnsaddr.bootstrap.example": [
            f"dnsaddr=/ip4/203.0.113.1/udp/4501/quic-v1/p2p/{ROUTER}",
            f"dnsaddr=/ip4/203.0.113.1/tcp/4501/p2p/{ROUTER}",
            f"dnsaddr=/ip4/203.0.113.2/tcp/4501/p2p/{OTHER}",
            f"dnsaddr=/ip4/203.0.113.2/udp/4501/quic-v1/p2p/{OTHER}",
        ],
        "_dnsaddr.quic-only.example": [f"dnsaddr=/ip4/203.0.113.3/udp/4501/quic-v1/p2p/{ROUTER}"],
    }

    class FakeDNS:
        async def resolve(self, name, rdtype):
            assert rdtype == "TXT"
            return FakeTXT(records.get(str(name).rstrip("."), []))

    def init(self):
        self._resolver = FakeDNS()

    monkeypatch.setattr(DNSResolver, "__init__", init)


def test_dnsaddr_router_address_resolves_to_its_tcp_address(testnet_dns):
    async def main():
        addrs = await dial_addrs(multiaddr.Multiaddr(f"/dnsaddr/bootstrap.example/p2p/{ROUTER}"))
        assert [str(a) for a in addrs] == [f"/ip4/203.0.113.1/tcp/4501/p2p/{ROUTER}"]
        info = await peer_info(multiaddr.Multiaddr(f"/dnsaddr/bootstrap.example/p2p/{ROUTER}"))
        assert str(info.peer_id) == ROUTER and len(info.addrs) == 1

    trio.run(main)


def test_addresses_without_a_transport_this_host_has_are_an_error(testnet_dns):
    async def main():
        with pytest.raises(RuntimeError, match="TCP"):
            await dial_addrs(multiaddr.Multiaddr(f"/dnsaddr/quic-only.example/p2p/{ROUTER}"))
        with pytest.raises(RuntimeError, match="TCP"):
            await dial_addrs(multiaddr.Multiaddr(f"/ip4/203.0.113.9/udp/4501/quic-v1/p2p/{ROUTER}"))
        with pytest.raises(RuntimeError, match="no address"):
            await dial_addrs(multiaddr.Multiaddr(f"/dnsaddr/nowhere.example/p2p/{ROUTER}"))

    trio.run(main)


def test_a_concrete_address_passes_through():
    async def main():
        ma = multiaddr.Multiaddr(f"/ip4/127.0.0.1/tcp/4001/p2p/{ROUTER}")
        assert await dial_addrs(ma) == [ma]

    trio.run(main)


def test_a_dial_nobody_answers_ends_at_the_timeout():
    """A provider record can name a pod a rollout replaced; its SYNs go
    unanswered. The dial ends at DIAL_TIMEOUT, not at the kernel's."""

    class Host:
        async def connect(self, info):
            await trio.sleep_forever()

    async def main():
        started = trio.current_time()
        with pytest.raises(ConnectionError, match=f"within {DIAL_TIMEOUT:g}s"):
            await dial(Host(), PeerInfo(ID.from_base58(ROUTER), []))
        assert trio.current_time() - started == pytest.approx(DIAL_TIMEOUT)

    trio.run(main, clock=trio.testing.MockClock(autojump_threshold=0))
