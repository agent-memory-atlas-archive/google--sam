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

// Package client is how a mesh component reads from its control plane. Node
// and router share it, so the body cap, the status handling and the signature
// check on /keys are a single code path. It depends on api/ only: importing it
// pulls in none of the control plane server.
package client

import (
	"context"
	"crypto/ed25519"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"

	"google.golang.org/protobuf/proto"

	"github.com/google/sam/api"
)

// MaxBodyBytes caps every response body read from a control plane: a
// misbehaving or impersonated server must not be able to make a client
// buffer arbitrary amounts of memory.
const MaxBodyBytes = 1 << 20

// transport applies api.ValidateControlPlaneTransport to every request,
// redirects included, so a plaintext hop is refused wherever the URL came
// from. allowInsecure is read per request: the node learns the operator's
// choice after its clients exist.
type transport struct {
	allowInsecure func() bool
}

func (t transport) RoundTrip(req *http.Request) (*http.Response, error) {
	if err := api.ValidateControlPlaneTransport(req.URL.String(), t.allowInsecure()); err != nil {
		return nil, err
	}
	return http.DefaultTransport.RoundTrip(req)
}

// NewHTTPClient is the HTTP client for every request a mesh component makes
// to its control plane. A nil allowInsecure never allows plaintext.
func NewHTTPClient(timeout time.Duration, allowInsecure func() bool) *http.Client {
	if allowInsecure == nil {
		allowInsecure = func() bool { return false }
	}
	return &http.Client{Timeout: timeout, Transport: transport{allowInsecure: allowInsecure}}
}

// Client reads the pull side of the mesh protocol from one control plane.
type Client struct {
	baseURL string
	http    *http.Client
}

// New normalizes baseURL, https:// when no scheme is given and no trailing
// slash, and speaks through httpClient, which the caller builds with
// NewHTTPClient so its own transport policy applies.
func New(baseURL string, httpClient *http.Client) *Client {
	if !strings.HasPrefix(baseURL, "http://") && !strings.HasPrefix(baseURL, "https://") {
		baseURL = "https://" + baseURL
	}
	return &Client{baseURL: strings.TrimSuffix(baseURL, "/"), http: httpClient}
}

// FetchInfo is GET /info: the router addresses, the ban set and the OIDC
// details a node needs to enroll.
func (c *Client) FetchInfo(ctx context.Context) (*api.ControlPlaneInfoResponse, error) {
	var info api.ControlPlaneInfoResponse
	if err := c.get(ctx, "/info", &info); err != nil {
		return nil, err
	}
	return &info, nil
}

// FetchKeys is GET /keys: the control plane's currently valid signing keys.
// The set is accepted only if signed by a key in trusted
// (api.VerifyKeysResponse): whoever answers the URL must already be the
// control plane, not become it.
func (c *Client) FetchKeys(ctx context.Context, trusted []ed25519.PublicKey) ([]ed25519.PublicKey, error) {
	var resp api.KeysResponse
	if err := c.get(ctx, "/keys", &resp); err != nil {
		return nil, err
	}
	keys, err := api.VerifyKeysResponse(&resp, trusted, time.Now())
	if err != nil {
		return nil, fmt.Errorf("/keys response rejected: %w", err)
	}
	return keys, nil
}

func (c *Client) get(ctx context.Context, path string, msg proto.Message) error {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, c.baseURL+path, nil)
	if err != nil {
		return fmt.Errorf("failed to create HTTP request: %w", err)
	}
	resp, err := c.http.Do(req)
	if err != nil {
		return fmt.Errorf("HTTP request failed: %w", err)
	}
	defer func() { _ = resp.Body.Close() }()

	body, err := io.ReadAll(io.LimitReader(resp.Body, MaxBodyBytes))
	if err != nil {
		return fmt.Errorf("failed to read response body: %w", err)
	}
	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("control plane returned status %s: %s", resp.Status, string(body))
	}
	if err := proto.Unmarshal(body, msg); err != nil {
		return fmt.Errorf("failed to decode %s response: %w", path, err)
	}
	return nil
}
