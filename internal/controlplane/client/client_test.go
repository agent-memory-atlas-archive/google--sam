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

package client

import (
	"context"
	"crypto/ed25519"
	"crypto/rand"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"google.golang.org/protobuf/proto"

	"github.com/google/sam/api"
)

func writeProto(t *testing.T, w http.ResponseWriter, msg proto.Message) {
	t.Helper()
	data, err := proto.Marshal(msg)
	if err != nil {
		t.Errorf("proto.Marshal(%T): %v", msg, err)
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}
	w.Header().Set("Content-Type", "application/x-protobuf")
	if _, err := w.Write(data); err != nil {
		t.Errorf("write %T: %v", msg, err)
	}
}

func TestFetchInfo(t *testing.T) {
	want := &api.ControlPlaneInfoResponse{
		RouterAddresses: []string{"/ip4/10.0.0.1/tcp/4501"},
		BannedPeerIds:   []string{"12D3KooWBanned"},
	}
	var gotPath string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotPath = r.URL.Path
		writeProto(t, w, want)
	}))
	defer srv.Close()

	// A trailing slash on the base URL must not double up in the path.
	c := New(srv.URL+"/", NewHTTPClient(time.Second, nil))
	info, err := c.FetchInfo(context.Background())
	if err != nil {
		t.Fatalf("FetchInfo: %v", err)
	}
	if gotPath != "/info" {
		t.Errorf("request path = %q, want /info", gotPath)
	}
	if !proto.Equal(info, want) {
		t.Errorf("FetchInfo = %v, want %v", info, want)
	}
}

func TestFetchKeys(t *testing.T) {
	trustedPub, trustedPriv, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	successorPub, successorPriv, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	strangerPub, strangerPriv, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}

	serve := func(t *testing.T, pubs []ed25519.PublicKey, privs []ed25519.PrivateKey) *Client {
		t.Helper()
		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			resp := &api.KeysResponse{}
			for _, p := range pubs {
				resp.PublicKeys = append(resp.PublicKeys, p)
			}
			if privs != nil {
				if err := api.SignKeysResponse(resp, privs, time.Now()); err != nil {
					t.Errorf("SignKeysResponse: %v", err)
					http.Error(w, err.Error(), http.StatusInternalServerError)
					return
				}
			}
			writeProto(t, w, resp)
		}))
		t.Cleanup(srv.Close)
		return New(srv.URL, NewHTTPClient(time.Second, nil))
	}

	t.Run("a set vouched for by a trusted key is adopted whole", func(t *testing.T) {
		c := serve(t, []ed25519.PublicKey{trustedPub, successorPub}, []ed25519.PrivateKey{trustedPriv, successorPriv})
		keys, err := c.FetchKeys(context.Background(), []ed25519.PublicKey{trustedPub})
		if err != nil {
			t.Fatalf("FetchKeys: %v", err)
		}
		if len(keys) != 2 || !keys[0].Equal(trustedPub) || !keys[1].Equal(successorPub) {
			t.Errorf("FetchKeys = %d keys, want trusted then successor", len(keys))
		}
	})

	t.Run("a set signed only by a stranger is rejected", func(t *testing.T) {
		c := serve(t, []ed25519.PublicKey{strangerPub}, []ed25519.PrivateKey{strangerPriv})
		if _, err := c.FetchKeys(context.Background(), []ed25519.PublicKey{trustedPub}); err == nil {
			t.Fatal("FetchKeys accepted a set no trusted key signed")
		}
	})

	t.Run("an unsigned set is rejected", func(t *testing.T) {
		c := serve(t, []ed25519.PublicKey{trustedPub, strangerPub}, nil)
		if _, err := c.FetchKeys(context.Background(), []ed25519.PublicKey{trustedPub}); err == nil {
			t.Fatal("FetchKeys accepted an unsigned set")
		}
	})
}

func TestErrors(t *testing.T) {
	t.Run("non-200 carries the status and body", func(t *testing.T) {
		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			http.Error(w, "store down", http.StatusServiceUnavailable)
		}))
		defer srv.Close()
		c := New(srv.URL, NewHTTPClient(time.Second, nil))
		_, err := c.FetchInfo(context.Background())
		if err == nil || !strings.Contains(err.Error(), "503") || !strings.Contains(err.Error(), "store down") {
			t.Fatalf("FetchInfo error = %v, want status and body", err)
		}
	})

	t.Run("a body that is not the message is an error", func(t *testing.T) {
		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			if _, err := w.Write([]byte("not protobuf")); err != nil {
				t.Errorf("write: %v", err)
			}
		}))
		defer srv.Close()
		c := New(srv.URL, NewHTTPClient(time.Second, nil))
		if _, err := c.FetchInfo(context.Background()); err == nil || !strings.Contains(err.Error(), "decode /info") {
			t.Fatalf("FetchInfo error = %v, want a decode error naming the path", err)
		}
	})

	t.Run("an oversized body is cut at the cap, not buffered", func(t *testing.T) {
		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			if _, err := w.Write(make([]byte, MaxBodyBytes+1)); err != nil {
				t.Errorf("write: %v", err)
			}
		}))
		defer srv.Close()
		c := New(srv.URL, NewHTTPClient(time.Second, nil))
		if _, err := c.FetchInfo(context.Background()); err == nil {
			t.Fatal("a body over the cap must not decode into a valid answer")
		}
	})
}

// The control plane is the trust root, so a plaintext hop to it is refused
// unless the operator opted in; loopback is the standalone case and is fine.
// The choice is read per request, since the node learns it after its clients
// exist.
func TestHTTPClientTransportPolicy(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		writeProto(t, w, &api.ControlPlaneInfoResponse{})
	}))
	defer srv.Close()
	// httptest binds 127.0.0.1; spell it as a non-loopback name that the
	// transport must refuse before any connection is attempted.
	nonLoopbackURL := strings.Replace(srv.URL, "127.0.0.1", "sam-control-plane.invalid", 1)

	allow := false
	httpClient := NewHTTPClient(time.Second, func() bool { return allow })

	if _, err := New(srv.URL, httpClient).FetchInfo(context.Background()); err != nil {
		t.Fatalf("loopback plaintext must be accepted: %v", err)
	}
	_, err := New(nonLoopbackURL, httpClient).FetchInfo(context.Background())
	if !errors.Is(err, api.ErrInsecureControlPlaneURL) {
		t.Fatalf("plaintext to a non-loopback host: err = %v, want %v", err, api.ErrInsecureControlPlaneURL)
	}

	// With the opt-in the request is attempted; the name does not resolve,
	// which is a dial error, not the policy error.
	allow = true
	_, err = New(nonLoopbackURL, httpClient).FetchInfo(context.Background())
	if err == nil || errors.Is(err, api.ErrInsecureControlPlaneURL) {
		t.Fatalf("with the opt-in the policy must not be what fails: %v", err)
	}
}
