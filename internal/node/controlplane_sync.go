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

package node

import (
	"context"
	"errors"
	"fmt"
	"math/rand"
	"time"

	"github.com/libp2p/go-libp2p/core/peer"
)

// The node reads three things from the control plane while it runs: the
// signing keys it verifies every credential against (/keys), the ban set and
// router addresses (/info), and the mesh policy (/policies). They are pulled
// together, by one loop, on one interval. Gossip events are notifications
// that bring the next pull forward; they are never the only way state
// arrives, because a once-published event with no replay is missed by any
// node that was down, partitioned, or simply enrolled later.

// syncControlPlane is the node's single pull from the control plane. Each
// part is attempted even when another fails, so a policy outage does not
// stop a key rotation from landing; the errors are reported together.
func (n *SamNode) syncControlPlane(ctx context.Context) error {
	if n.Store == nil {
		return errors.New("node has no store")
	}
	controlPlaneURL, err := n.Store.LoadControlPlaneURL()
	if err != nil || controlPlaneURL == "" {
		return errors.New("control plane URL not found in store")
	}
	var errs []error
	if err := n.syncTrustedKeys(ctx, controlPlaneURL); err != nil {
		errs = append(errs, fmt.Errorf("keys: %w", err))
	}
	if err := n.syncMeshInfo(ctx, controlPlaneURL); err != nil {
		errs = append(errs, fmt.Errorf("info: %w", err))
	}
	if err := n.syncMeshPolicy(ctx); err != nil {
		errs = append(errs, fmt.Errorf("policy: %w", err))
	}
	return errors.Join(errs...)
}

// syncTrustedKeys replaces the trust set with the control plane's current
// /keys answer and persists it. Enrollment hands out only the newest key, so
// this is how a node learns the key still in its rotation grace period (which
// routers and peers may still be signed by) and any successor it did not hear
// about. Verification against the keys already trusted keeps whoever answers
// the URL from becoming the trust root; an empty answer is a failure so the
// set is never wiped.
func (n *SamNode) syncTrustedKeys(ctx context.Context, controlPlaneURL string) error {
	n.keysMu.RLock()
	existing := append([]TrustedKey(nil), n.trustedKeys...)
	n.keysMu.RUnlock()
	if len(existing) == 0 {
		return errors.New("no trusted control plane keys to verify /keys against")
	}
	keys, err := FetchControlPlaneKeys(ctx, controlPlaneURL, publicKeysOf(existing))
	if err != nil {
		return err
	}
	if len(keys) == 0 {
		return errors.New("/keys returned no keys")
	}
	merged := mergeTrustedKeys(existing, keys, time.Now())
	n.keysMu.Lock()
	n.trustedKeys = merged
	snapshot := append([]TrustedKey(nil), merged...)
	n.keysMu.Unlock()
	n.persistTrustedKeys(snapshot)
	logger.Debugf("Synced %d valid control plane keys", len(merged))
	return nil
}

// syncMeshInfo reads /info: the router addresses are persisted for the next
// start, and the ban set is reconciled against the revocation cache, which is
// how a running node learns a ban or an unban it got no event for.
func (n *SamNode) syncMeshInfo(ctx context.Context, controlPlaneURL string) error {
	// Taken before the request: a ban recorded after this instant cannot be
	// in the answer, so its absence must not be read as an unban.
	fetchedAt := time.Now()
	info, err := FetchControlPlaneInfo(ctx, controlPlaneURL)
	if err != nil {
		return err
	}
	if len(info.RouterAddresses) > 0 {
		pubKey, _, loadErr := n.Store.LoadMeshConfig()
		switch {
		case loadErr != nil:
			logger.Warnf("Failed to load mesh config; not persisting router addresses: %v", loadErr)
		case len(pubKey) > 0:
			if saveErr := n.Store.SaveMeshConfig(pubKey, info.RouterAddresses); saveErr != nil {
				logger.Warnf("Failed to persist router addresses: %v", saveErr)
			}
		}
	}
	n.reconcileBannedPeers(info.GetBannedPeerIds(), fetchedAt)
	return nil
}

// reconcileBannedPeers makes the revocation cache match the control plane's
// ban set as of fetchedAt. Bans recorded at or after fetchedAt are kept even
// when absent from the answer: the answer predates them and cannot speak to
// them (see the router's reconcileBannedPeers for the same rule).
func (n *SamNode) reconcileBannedPeers(peerIDs []string, fetchedAt time.Time) {
	if n.revokedPeers == nil {
		return
	}
	banned := make(map[string]peer.ID, len(peerIDs))
	for _, id := range peerIDs {
		p, err := peer.Decode(id)
		if err != nil {
			logger.Warnf("Ignoring undecodable banned peer ID %q from the control plane: %v", id, err)
			continue
		}
		banned[p.String()] = p
	}
	fetchedAtMs := fetchedAt.UnixMilli()
	for _, key := range n.revokedPeers.Keys() {
		if _, still := banned[key]; still {
			continue
		}
		if bannedAt, ok := n.revokedPeers.Peek(key); ok && bannedAt >= fetchedAtMs {
			continue
		}
		logger.Infof("Peer %s is no longer banned by the control plane; lifting the local ban", key)
		n.revokedPeers.Remove(key)
	}
	for key, p := range banned {
		if n.revokedPeers.Contains(key) {
			continue
		}
		logger.Infow("peer banned by the control plane", "event", meshEventBanned, "peer", key)
		n.banPeer(p, fetchedAtMs)
	}
}

// banPeer records the ban and evicts the peer: the cache entry is what the
// gater and the auth path consult, and dropping the admission is what stops
// the relay ACL from honouring a session that was already established.
func (n *SamNode) banPeer(p peer.ID, bannedAtMs int64) {
	if n.revokedPeers != nil {
		n.revokedPeers.Add(p.String(), bannedAtMs)
	}
	n.authPeers.Delete(p)
	if n.Host != nil {
		_ = n.Host.Network().ClosePeer(p)
	}
}

// triggerControlPlaneSync asks the sync loop to run soon, after a random
// delay of up to the configured jitter so a fleet told at once does not hit
// the control plane at once. A pending request is not duplicated.
func (n *SamNode) triggerControlPlaneSync() {
	select {
	case n.controlPlaneSyncTrigger <- struct{}{}:
	default:
	}
}

// startControlPlaneSyncLoop pulls once shortly after start, then every
// interval and whenever triggered. Failures are logged at Warn once and at
// Debug while they persist, with an Info on recovery.
func (n *SamNode) startControlPlaneSyncLoop(ctx context.Context, interval time.Duration) {
	if interval <= 0 || n.Store == nil {
		return
	}
	go func() {
		timer := time.NewTimer(2 * time.Second)
		defer timer.Stop()
		failures := 0
		for {
			select {
			case <-ctx.Done():
				return
			case <-timer.C:
			case <-n.controlPlaneSyncTrigger:
				if !timer.Stop() {
					select {
					case <-timer.C:
					default:
					}
				}
				jitter := n.config.ControlPlaneSyncJitter
				if jitter > 0 {
					select {
					case <-ctx.Done():
						return
					case <-time.After(time.Duration(rand.Int63n(int64(jitter)))):
					}
				}
			}
			timer.Reset(interval)

			err := n.syncControlPlane(ctx)
			switch {
			case err != nil && failures == 0:
				logger.Warnf("Control plane sync failed: %v", err)
			case err != nil:
				logger.Debugf("Control plane sync still failing (%d consecutive): %v", failures+1, err)
			case failures > 0:
				logger.Infof("Control plane sync recovered after %d failures", failures)
			}
			if err != nil {
				failures++
			} else {
				failures = 0
			}
		}
	}()
}
