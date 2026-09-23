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

"""base58btc, the alphabet libp2p peer IDs are written in."""

_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_INDEX = {c: i for i, c in enumerate(_ALPHABET)}


def encode(data: bytes) -> str:
    zeros = len(data) - len(data.lstrip(b"\x00"))
    n = int.from_bytes(data, "big")
    out = []
    while n > 0:
        n, digit = divmod(n, 58)
        out.append(_ALPHABET[digit])
    return "1" * zeros + "".join(reversed(out))


def decode(text: str) -> bytes:
    zeros = len(text) - len(text.lstrip("1"))
    n = 0
    for ch in text:
        try:
            n = n * 58 + _INDEX[ch]
        except KeyError:
            raise ValueError(f"invalid base58 character {ch!r}") from None
    body = n.to_bytes((n.bit_length() + 7) // 8, "big") if n else b""
    return b"\x00" * zeros + body
