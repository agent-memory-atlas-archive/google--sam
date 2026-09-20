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

package integration_test

import (
	"bytes"
	"crypto/ed25519"
	"crypto/rand"
	"encoding/json"
	"fmt"
	"io"

	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"net/http"
	"net/http/httptest"

	"github.com/biscuit-auth/biscuit-go/v2/parser"
	"github.com/google/sam/api"
	"github.com/libp2p/go-libp2p"
	"github.com/libp2p/go-libp2p/core/host"
	"github.com/libp2p/go-libp2p/core/network"
	"github.com/libp2p/go-msgio"
	"google.golang.org/protobuf/proto"
)

// init forces the biscuit-go parser to build its underlying participle
// lexer and reflection caches synchronously at startup. This prevents
// a known data race when multiple goroutines parse facts concurrently.
func init() {
	_, _ = parser.FromStringFact(`warmup("cache")`)
}

type safeBuffer struct {
	mu  sync.Mutex
	buf bytes.Buffer
}

func (s *safeBuffer) Write(p []byte) (n int, err error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.buf.Write(p)
}

func (s *safeBuffer) String() string {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.buf.String()
}

// TestSelfHealingHTTPFallback: the routers a node stored can be gone by its
// next start (a redeploy moves every router's address); the node must ask the
// control plane for the current ones and reach one. The assertions are the two
// ends of that path: the control plane saw the request, and the router that
// only exists since the address change completed an auth handshake with the
// node. Neither depends on what the node logs.
func TestSelfHealingHTTPFallback(t *testing.T) {
	nodeBin := buildBinary(t, "./cmd/sam-node")

	var mu sync.Mutex
	var currentP2PAddr string
	var infoRequests atomic.Int32

	pub, priv, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatalf("Failed to generate control plane key: %v", err)
	}

	// createNewHost is a router as the node sees it: the auth handshake, and a
	// channel closed once a node has completed it.
	createNewHost := func() (host.Host, <-chan struct{}) {
		newH, err := libp2p.New(libp2p.ListenAddrStrings("/ip4/127.0.0.1/tcp/0"))
		if err != nil {
			t.Fatal(err)
		}
		authenticated := make(chan struct{})
		var once sync.Once
		newH.SetStreamHandler(api.AuthProtocolID, func(s network.Stream) {
			defer func() { _ = s.Close() }()
			reader := msgio.NewVarintReaderSize(s, 1024*64)
			msg, err := reader.ReadMsg()
			if err != nil {
				return
			}
			defer reader.ReleaseMsg(msg)

			writer := msgio.NewVarintWriter(s)
			resp := &api.AuthResponse{
				Success: true,
				Biscuit: createMockBiscuitToken(t, newH.ID().String(), priv, api.RoleRouter, nil),
			}
			respBytes, err := proto.Marshal(resp)
			if err != nil {
				t.Errorf("marshal auth response: %v", err)
				return
			}
			if err := writer.WriteMsg(respBytes); err != nil {
				t.Errorf("write auth response: %v", err)
				return
			}
			once.Do(func() { close(authenticated) })
		})
		return newH, authenticated
	}

	h, _ := createNewHost()
	defer func() { _ = h.Close() }()

	mu.Lock()
	currentP2PAddr = h.Addrs()[0].String() + "/p2p/" + h.ID().String()
	mu.Unlock()

	mux := http.NewServeMux()

	// Mock OIDC server for device flow
	mux.HandleFunc("/.well-known/openid-configuration", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]interface{}{
			"issuer":                 "http://" + r.Host,
			"token_endpoint":         "http://" + r.Host + "/token",
			"authorization_endpoint": "http://" + r.Host + "/auth",
		})
	})
	mux.HandleFunc("/device/code", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]interface{}{
			"device_code":               "dev_code_123",
			"user_code":                 "ABCD-1234",
			"verification_uri":          "http://example.com/verify",
			"verification_uri_complete": "http://example.com/verify?code=ABCD-1234",
			"expires_in":                60,
			"interval":                  1,
		})
	})
	mux.HandleFunc("/token", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]string{
			"access_token": "test-jwt-token",
			"id_token":     "test-jwt-token",
		})
	})

	mux.HandleFunc("/register", func(w http.ResponseWriter, r *http.Request) {
		mu.Lock()
		addr := currentP2PAddr
		mu.Unlock()

		body, err := io.ReadAll(r.Body)
		if err != nil {
			http.Error(w, "Failed to read body", http.StatusBadRequest)
			return
		}
		var enrollReq api.EnrollRequest
		if err := proto.Unmarshal(body, &enrollReq); err != nil {
			http.Error(w, "Invalid request", http.StatusBadRequest)
			return
		}

		resp := &api.EnrollResponse{
			BiscuitToken:          createMockBiscuitToken(t, enrollReq.PeerId, priv, api.RoleNode, nil),
			ControlPlanePublicKey: pub,
			RouterAddresses:       []string{addr},
		}
		data, err := proto.Marshal(resp)
		if err != nil {
			t.Errorf("marshal /register: %v", err)
			http.Error(w, err.Error(), http.StatusInternalServerError)
			return
		}
		w.Header().Set("Content-Type", "application/x-protobuf")
		if _, err := w.Write(data); err != nil {
			t.Errorf("write /register: %v", err)
		}
	})
	mux.HandleFunc("/info", func(w http.ResponseWriter, r *http.Request) {
		infoRequests.Add(1)
		mu.Lock()
		addr := currentP2PAddr
		mu.Unlock()
		resp := &api.ControlPlaneInfoResponse{
			OidcIssuer:      "http://" + r.Host,
			ClientId:        "sam-mesh-audience",
			Audience:        "sam-mesh-audience",
			RouterAddresses: []string{addr},
		}
		data, err := proto.Marshal(resp)
		if err != nil {
			t.Errorf("marshal /info: %v", err)
			http.Error(w, err.Error(), http.StatusInternalServerError)
			return
		}
		w.Header().Set("Content-Type", "application/x-protobuf")
		if _, err := w.Write(data); err != nil {
			t.Errorf("write /info: %v", err)
		}
	})

	httpServer := httptest.NewServer(mux)
	defer httpServer.Close()

	tmpHome := t.TempDir()
	env := append(os.Environ(),
		"HOME="+tmpHome,
		"XDG_CONFIG_HOME="+filepath.Join(tmpHome, ".config"),
		"BROWSER=echo",
	)

	// Step 1: Enroll via Join (using mock OIDC)
	joinStdout, joinStderr, err := runCommandWithCallback(
		t,
		repoRoot(t),
		5*time.Second,
		env,
		"",
		nodeBin,
		"join",
		httpServer.URL,
	)
	if err != nil {
		t.Fatalf("Join failed: %v\nstdout:\n%s\nstderr:\n%s", err, joinStdout, joinStderr)
	}

	out := joinStdout + joinStderr
	if !strings.Contains(out, "Successfully joined the Sovereign Agent Mesh!") {
		t.Fatalf("Join did not succeed:\n%s", out)
	}

	// Step 2: Simulate router changing its P2P port (HTTP URL stays the same).
	// The stored address now points at nothing; only /info knows the new one.
	_ = h.Close()
	newRouter, authenticated := createNewHost()
	defer func() { _ = newRouter.Close() }()

	mu.Lock()
	currentP2PAddr = newRouter.Addrs()[0].String() + "/p2p/" + newRouter.ID().String()
	mu.Unlock()
	infoRequests.Store(0)

	// Step 3: Start sam-node run, with everything it needs to stay up: a
	// node that reaches the router and then exits has not healed.
	runCmd := exec.Command(nodeBin, "run",
		"--listen", "/ip4/127.0.0.1/tcp/0",
		"--bind-addr", fmt.Sprintf("127.0.0.1:%d", getFreePort(t)),
		"--api-token-path", tokenPath(t, "fallback-test-token"),
	)
	runCmd.Env = env
	var output safeBuffer
	runCmd.Stdout = &output
	runCmd.Stderr = &output

	if err := runCmd.Start(); err != nil {
		t.Fatal(err)
	}
	exited := make(chan error, 1)
	go func() { exited <- runCmd.Wait() }()
	defer func() {
		_ = runCmd.Process.Kill()
		<-exited
	}()

	select {
	case <-authenticated:
	case err := <-exited:
		t.Fatalf("sam-node run exited (%v) before authenticating with the moved router.\nOutput:\n%s", err, output.String())
	case <-time.After(5 * time.Second):
		t.Fatalf("sam-node run never authenticated with the moved router.\nOutput:\n%s", output.String())
	}
	if infoRequests.Load() == 0 {
		t.Fatal("the node reached the moved router without asking the control plane for its address")
	}

	// Reaching the router is not the end: the node must stay up on it.
	select {
	case err := <-exited:
		t.Fatalf("sam-node run exited (%v) right after authenticating.\nOutput:\n%s", err, output.String())
	case <-time.After(500 * time.Millisecond):
	}
}
