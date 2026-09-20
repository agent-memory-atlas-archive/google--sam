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
	"bytes"
	"context"
	"crypto/ed25519"
	"encoding/base64"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"

	"github.com/google/sam/api"
	cpclient "github.com/google/sam/internal/controlplane/client"
	"google.golang.org/protobuf/proto"
)

// maxControlPlaneBodyBytes caps every response body read from the control
// plane or an IdP: a misbehaving or impersonated server must not be able to
// make the node buffer arbitrary amounts of memory. Bodies that carry a
// message go through cpclient.ReadBody, which turns an oversized answer into
// an error rather than a truncated message.
const maxControlPlaneBodyBytes = cpclient.MaxBodyBytes

// controlPlaneClient speaks the pull endpoints of controlPlaneURL through the
// node's transport policy.
func controlPlaneClient(controlPlaneURL string) *cpclient.Client {
	return cpclient.New(controlPlaneURL, controlPlaneHTTPClient(10*time.Second))
}

// FetchControlPlaneInfo retrieves the latest configuration from the control plane's /info endpoint.
func FetchControlPlaneInfo(ctx context.Context, controlPlaneURL string) (*api.ControlPlaneInfoResponse, error) {
	return controlPlaneClient(controlPlaneURL).FetchInfo(ctx)
}

// FetchControlPlaneKeys retrieves the full set of currently valid control
// plane public keys from /keys, the same catch-up path routers use.
// Enrollment only hands out the newest key, so this is how a node learns
// keys still in their rotation grace period, or rotations it missed while
// offline. The set is accepted only if signed by a key in trusted.
func FetchControlPlaneKeys(ctx context.Context, controlPlaneURL string, trusted []ed25519.PublicKey) ([]ed25519.PublicKey, error) {
	return controlPlaneClient(controlPlaneURL).FetchKeys(ctx, trusted)
}

// mergeTrustedKeys replaces the stored trust set with the authoritative set
// from /keys, preserving ReceivedAt for keys already known so local grace
// pruning keeps working across syncs.
func mergeTrustedKeys(existing []TrustedKey, fetched []ed25519.PublicKey, now time.Time) []TrustedKey {
	merged := make([]TrustedKey, 0, len(fetched))
	for _, key := range fetched {
		tk := TrustedKey{Key: key, ReceivedAt: now}
		for _, old := range existing {
			if bytes.Equal(old.Key, key) {
				tk.ReceivedAt = old.ReceivedAt
				break
			}
		}
		merged = append(merged, tk)
	}
	return merged
}

func publicKeysOf(keys []TrustedKey) []ed25519.PublicKey {
	out := make([]ed25519.PublicKey, 0, len(keys))
	for _, tk := range keys {
		out = append(out, tk.Key)
	}
	return out
}

// FetchMeshPolicy retrieves the latest mesh policy from the control plane's /policies endpoint using a biscuit token.
func FetchMeshPolicy(ctx context.Context, controlPlaneURL string, biscuitToken []byte) (*api.PolicyConfigGetResponse, error) {
	return controlPlaneClient(controlPlaneURL).FetchPolicy(ctx, biscuitToken)
}

// ReportNodeCatalog self-reports this node's locally registered services to
// the control plane's /nodes/catalog endpoint, so an admin can see mesh-wide
// service topology (see catalog.go's HandleNodeCatalog for why this exists
// instead of the control plane discovering it via DHT/P2P itself).
func ReportNodeCatalog(ctx context.Context, controlPlaneURL string, biscuitToken []byte, services []*api.ServiceInfo) error {
	if !strings.HasPrefix(controlPlaneURL, "http://") && !strings.HasPrefix(controlPlaneURL, "https://") {
		controlPlaneURL = "https://" + controlPlaneURL
	}
	controlPlaneURL = strings.TrimSuffix(controlPlaneURL, "/")

	payload, err := proto.Marshal(&api.NodeCatalogReport{Services: services})
	if err != nil {
		return fmt.Errorf("failed to encode catalog report: %w", err)
	}

	urlStr := controlPlaneURL + "/nodes/catalog"
	req, err := http.NewRequestWithContext(ctx, "POST", urlStr, bytes.NewReader(payload))
	if err != nil {
		return fmt.Errorf("failed to create HTTP request: %w", err)
	}
	req.Header.Set("Content-Type", "application/x-protobuf")
	req.Header.Set("Authorization", "Bearer "+base64.StdEncoding.EncodeToString(biscuitToken))

	client := controlPlaneHTTPClient(10 * time.Second)
	resp, err := client.Do(req)
	if err != nil {
		return fmt.Errorf("HTTP request failed: %w", err)
	}
	defer resp.Body.Close() //nolint:errcheck

	if resp.StatusCode != http.StatusNoContent {
		body, _ := io.ReadAll(io.LimitReader(resp.Body, 4096))
		return fmt.Errorf("control plane returned status %s: %s", resp.Status, string(body))
	}
	return nil
}
