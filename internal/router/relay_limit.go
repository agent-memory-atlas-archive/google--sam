// Copyright 2026 Google LLC
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

package router

import (
	"fmt"
	"math"
	"time"

	"github.com/dustin/go-humanize"
	"github.com/libp2p/go-libp2p/p2p/protocol/circuitv2/relay"
)

// DefaultRelayLimitDuration is how long a relayed connection lives unless the operator sets it.
const DefaultRelayLimitDuration = time.Hour

// relayLimit maps operator limits (0 = none) onto go-libp2p, where only a nil
// limit is unlimited and a zero field cuts every circuit at once.
func relayLimit(duration time.Duration, data int64) *relay.RelayLimit {
	if duration <= 0 && data <= 0 {
		return nil
	}
	limit := &relay.RelayLimit{Duration: duration, Data: data}
	if duration <= 0 {
		// The relay advertises seconds as uint32; anything larger wraps.
		limit.Duration = math.MaxUint32 * time.Second
	}
	if data <= 0 {
		limit.Data = math.MaxInt64
	}
	return limit
}

// ByteSize is a flag value that accepts human sizes such as 128MiB.
type ByteSize int64

func (b *ByteSize) String() string { return humanize.IBytes(uint64(*b)) }

func (b *ByteSize) Type() string { return "size" }

func (b *ByteSize) Set(s string) error {
	n, err := humanize.ParseBytes(s)
	if err != nil {
		return err
	}
	if n > math.MaxInt64 {
		return fmt.Errorf("size %q is too large", s)
	}
	*b = ByteSize(n)
	return nil
}
