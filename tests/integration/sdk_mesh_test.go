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
	"bufio"
	"bytes"
	"context"
	"crypto/ed25519"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/google/sam/api"
	"github.com/google/sam/internal/controlplane"
	"github.com/google/sam/internal/identity"
	"github.com/google/sam/internal/storage"
	"github.com/libp2p/go-libp2p"
	"github.com/libp2p/go-libp2p/core/host"
	"github.com/libp2p/go-libp2p/core/network"
	"github.com/libp2p/go-libp2p/core/peer"
	libp2ptls "github.com/libp2p/go-libp2p/p2p/security/tls"
	"github.com/libp2p/go-msgio"
	"github.com/multiformats/go-multiaddr"
	"google.golang.org/protobuf/proto"
)

// sdkMember is a running SDK conformance-join runner: a mesh member written
// in another language that the test drives over stdin/stdout. Both runners
// (sdk/js/src/conformance-join.ts, sdk/python/src/agent_mesh/conformance_join.py)
// speak the same line protocol.
type sdkMember struct {
	name   string
	report sdkJoinReport
	cmd    *exec.Cmd
	stdin  io.WriteCloser
	lines  *bufio.Scanner
	stderr *bytes.Buffer
}

// sdkJoinReport is the first line a runner prints once on the mesh.
type sdkJoinReport struct {
	SDK     string `json:"sdk"`
	PeerID  string `json:"peer_id"`
	Routers []struct {
		PeerID string   `json:"peer_id"`
		Addr   string   `json:"addr"`
		Roles  []string `json:"roles"`
	} `json:"routers"`
	RelayAddresses  []string `json:"relay_addresses"`
	DirectAddresses []string `json:"direct_addresses"`
	Biscuit         string   `json:"biscuit"`
}

// sdkAuthResult answers an {"cmd":"auth"} command.
type sdkAuthResult struct {
	OK         bool              `json:"ok"`
	Error      string            `json:"error"`
	PeerID     string            `json:"peer_id"`
	Roles      []string          `json:"roles"`
	Labels     map[string]string `json:"labels"`
	Expiration int64             `json:"expiration"`
}

var sdkMemberLaunchers = []sdkRunner{
	{
		name: "js",
		cmd: func(root string) (*exec.Cmd, string) {
			entry := filepath.Join(root, "sdk", "js", "dist", "conformance-join.js")
			if _, err := exec.LookPath("node"); err != nil {
				return nil, "node is not installed"
			}
			if _, err := os.Stat(entry); err != nil {
				return nil, "sdk/js is not built (cd sdk/js && npm ci && npm run build)"
			}
			return exec.Command("node", entry), ""
		},
	},
	{
		name: "python",
		cmd: func(root string) (*exec.Cmd, string) {
			python := filepath.Join(root, "sdk", "python", ".venv", "bin", "python")
			if _, err := os.Stat(python); err != nil {
				var lookErr error
				if python, lookErr = exec.LookPath("python3"); lookErr != nil {
					return nil, "python3 is not installed"
				}
			}
			if err := exec.Command(python, "-c", "import agent_mesh.session").Run(); err != nil {
				return nil, "agent_mesh is not importable with libp2p (pip install -e sdk/python)"
			}
			return exec.Command(python, "-m", "agent_mesh.conformance_join"), ""
		},
	},
}

// TestNativeSDKsMesh runs one mesh: a control plane, a sam-router, a
// sam-node and one member per SDK, all real. It then walks the connectivity
// matrix. Every member authenticates with the router and gets a relay
// reservation, which the router grants only to admitted peers. Each SDK
// member reaches the sam-node directly and the other SDK member both
// directly and through the router, and on every path both ends verify each
// other's credential with the /sam/auth/1.0.0 handshake. The sam-node
// reaches each SDK member through the router. A Go peer that is itself
// admitted does the same and also checks that a forged credential gets no
// answer. Any SDK whose toolchain is missing is skipped, and the matrix
// shrinks to the members present.
func TestNativeSDKsMesh(t *testing.T) {
	root := repoRoot(t)
	oidcURL, mintToken := startCustomMockOIDC(t)

	store, err := storage.NewSQLStore("sqlite", filepath.Join(t.TempDir(), "cp.db"))
	if err != nil {
		t.Fatalf("failed to create store: %v", err)
	}
	t.Cleanup(func() { _ = store.Close() })

	const adminToken = "test-admin-token"
	srv, err := controlplane.NewServer(controlplane.Options{
		ListenAddr:            "127.0.0.1:0",
		AdminToken:            adminToken,
		OIDCIssuer:            oidcURL,
		AllowedAudiences:      []string{"sam-mesh-audience"},
		AutoApproveEnrollment: true,
	}, store)
	if err != nil {
		t.Fatalf("failed to create control plane: %v", err)
	}
	if err := srv.Start(); err != nil {
		t.Fatalf("failed to start control plane: %v", err)
	}
	t.Cleanup(func() { _ = srv.Close() })
	baseURL := "http://" + srv.Addr()
	_, portStr, _ := net.SplitHostPort(srv.Addr())
	cpPort, _ := strconv.Atoi(portStr)

	// The router enrolls through OIDC with group "routers" and the node with
	// user mock-user; the policy binds both to their roles.
	policyFile := filepath.Join(t.TempDir(), "policy.yaml")
	writePolicyWithRouter(t, policyFile, fmt.Sprintf("bindings:\n  - members: [\"user:mock-user\"]\n    role: %s\nroles: []\n", api.RoleNode))
	injectPolicyYAML(t, cpPort, adminToken, policyFile)

	routerAddr, _ := startRouter(t, t.TempDir(), cpPort, mintToken, "router")
	routerPeer := extractPeerID(routerAddr)

	ctx := context.Background()
	cpPriv, cpPub, err := store.GetCurrentKey(ctx)
	if err != nil {
		t.Fatalf("failed to load control plane signing key: %v", err)
	}

	// The Go node: a sam-node enrolled through OIDC, listening on loopback.
	nodeBin := buildBinary(t, "./cmd/sam-node")
	nodeHome := filepath.Join(t.TempDir(), "node")
	samNode := launchNode(t, nodeBin,
		append(os.Environ(), "HOME="+nodeHome, "XDG_CONFIG_HOME="+filepath.Join(nodeHome, ".config")),
		nodeHome, "run",
		"--control-plane", baseURL,
		"--jwt", mintToken(map[string]interface{}{"sub": "mock-user", "roles": []string{api.RoleNode}}),
		"--allow-loopback",
		"--api-token-path", tokenPath(t, "node-token"),
		"--log-level", "debug",
	)
	nodeAPI := samNode.waitForAPI(t)

	// One member per SDK, each listening directly on loopback too so both
	// the direct and the relayed path can be walked.
	var members []*sdkMember
	for _, launcher := range sdkMemberLaunchers {
		cmd, skip := launcher.cmd(root)
		if skip != "" {
			t.Logf("%s SDK skipped: %s", launcher.name, skip)
			continue
		}
		members = append(members, startSDKMember(t, launcher.name, cmd, root, baseURL, adminToken, routerAddr, routerPeer))
	}
	if len(members) == 0 {
		t.Skip("no SDK toolchain available; see sdk/README.md")
	}

	// The router reports every member connected to the control plane.
	waitForPeerOnRouter(t, cpPort, adminToken, samNode.peerID.String(), 5*time.Second)
	for _, m := range members {
		waitForPeerOnRouter(t, cpPort, adminToken, m.report.PeerID, 5*time.Second)
	}

	goPeer := newAdmittedGoPeer(t, ctx, cpPriv, routerAddr)

	for _, m := range members {
		m := m
		t.Run(m.name, func(t *testing.T) {
			// SDK -> sam-node, directly: the node's HandleAuthHandshake
			// verifies the member's biscuit and answers with its own.
			nodeCred := m.auth(t, samNode.p2pAddr)
			if nodeCred.PeerID != samNode.peerID.String() || !contains(nodeCred.Roles, api.RoleNode) {
				t.Fatalf("%s verified the node as %+v", m.name, nodeCred)
			}
			if !strings.Contains(samNode.log(), "Successfully authenticated peer "+m.report.PeerID) {
				t.Errorf("sam-node log does not record admitting %s", m.report.PeerID)
			}

			// sam-node -> SDK, through the router: the node reaches the member
			// on its relayed address, which the router only serves for
			// authenticated peers on both ends.
			connectPeerWithToken(t, nodeAPI, "node-token", m.report.RelayAddresses[0])

			// SDK -> every other SDK, directly and through the router.
			for _, other := range members {
				if other == m {
					continue
				}
				for _, addr := range []string{pickDirectAddr(t, other.report.DirectAddresses, other.report.PeerID), other.report.RelayAddresses[0]} {
					cred := m.auth(t, addr)
					if cred.PeerID != other.report.PeerID || !contains(cred.Roles, api.RoleNode) {
						t.Fatalf("%s verified %s at %s as %+v", m.name, other.name, addr, cred)
					}
				}
			}

			// Go peer -> SDK, through the router, both ways of the handshake.
			target := multiaddr.StringCast(m.report.RelayAddresses[0])
			sdkPeer, _ := peer.Decode(m.report.PeerID)
			dialCtx, cancel := context.WithTimeout(ctx, 10*time.Second)
			defer cancel()
			if err := goPeer.Connect(dialCtx, peer.AddrInfo{ID: sdkPeer, Addrs: []multiaddr.Multiaddr{target}}); err != nil {
				t.Fatalf("go peer could not reach %s at %s: %v", m.name, target, err)
			}
			memberBiscuit := authHandshake(t, ctx, goPeer, sdkPeer, goHostBiscuit(t, cpPriv, goPeer.ID()))
			if _, err := identity.VerifyBiscuitAndGetExpiry(memberBiscuit, sdkPeer, []ed25519.PublicKey{cpPub}, 5*time.Second); err != nil {
				t.Fatalf("%s answered with a credential that does not verify: %v", m.name, err)
			}
			if err := identity.VerifyBiscuitRole(memberBiscuit, cpPub, api.RoleNode, 5*time.Second); err != nil {
				t.Fatalf("%s credential lacks the node role: %v", m.name, err)
			}
			if want, _ := base64.StdEncoding.DecodeString(m.report.Biscuit); !bytes.Equal(want, memberBiscuit) {
				t.Fatal("member answered with a different biscuit than it reported holding")
			}
			refuseForgedFrame(t, ctx, goPeer, sdkPeer)

			// A peer that is not on the mesh cannot be reached: the address
			// names an unknown peer behind the router.
			stranger, _ := peer.Decode("12D3KooWA4Xop1JaT3MHxwYMkCepYsv4iPVopMXwCz5iHYdBfeSB")
			if res := m.authRaw(t, routerAddr+"/p2p-circuit/p2p/"+stranger.String()); res.OK {
				t.Fatalf("%s reached a peer that is not on the mesh", m.name)
			}
		})
	}

	// Everyone who ran the handshake against a member is in its admitted set.
	for _, m := range members {
		peers := m.peers(t)
		if !contains(peers, goPeer.ID().String()) {
			t.Errorf("%s admitted peers %v lack the Go peer %s", m.name, peers, goPeer.ID())
		}
		for _, other := range members {
			if other != m && !contains(peers, other.report.PeerID) {
				t.Errorf("%s admitted peers %v lack %s (%s)", m.name, peers, other.name, other.report.PeerID)
			}
		}
		m.quit(t)
	}
}

func startSDKMember(t *testing.T, name string, cmd *exec.Cmd, root, baseURL, adminToken, routerAddr, routerPeer string) *sdkMember {
	t.Helper()
	tokenPath := filepath.Join(t.TempDir(), "bootstrap.token")
	if err := os.WriteFile(tokenPath, []byte(mintBootstrapToken(t, baseURL, adminToken)+"\n"), 0o600); err != nil {
		t.Fatalf("failed to write bootstrap token: %v", err)
	}
	cmd.Env = append(os.Environ(),
		"SAM_CONTROL_PLANE_URL="+baseURL,
		"SAM_BOOTSTRAP_TOKEN_PATH="+tokenPath,
		"SAM_SDK_STATE_DIR="+filepath.Join(t.TempDir(), "state"),
		"SAM_SDK_LISTEN_ADDRS=/ip4/127.0.0.1/tcp/0",
	)
	cmd.Dir = root
	m := &sdkMember{name: name, cmd: cmd, stderr: &bytes.Buffer{}}
	cmd.Stderr = m.stderr
	stdin, err := cmd.StdinPipe()
	if err != nil {
		t.Fatal(err)
	}
	stdout, err := cmd.StdoutPipe()
	if err != nil {
		t.Fatal(err)
	}
	m.stdin = stdin
	m.lines = bufio.NewScanner(stdout)
	m.lines.Buffer(make([]byte, 0, 64*1024), 1024*1024)
	if err := cmd.Start(); err != nil {
		t.Fatalf("failed to start %s member: %v", name, err)
	}
	t.Cleanup(func() {
		_ = stdin.Close()
		_ = cmd.Process.Kill()
		if cmd.ProcessState == nil {
			_ = cmd.Wait()
		}
		if t.Failed() {
			t.Logf("%s member stderr:\n%s", name, m.stderr.String())
		}
	})

	if line := m.readLine(t, 20*time.Second); json.Unmarshal(line, &m.report) != nil {
		t.Fatalf("%s member printed no join report, got: %q", name, line)
	}
	if len(m.report.Routers) != 1 || m.report.Routers[0].PeerID != routerPeer {
		t.Fatalf("%s report routers %+v, want the router %s", name, m.report.Routers, routerPeer)
	}
	if !contains(m.report.Routers[0].Roles, api.RoleRouter) {
		t.Fatalf("%s: router credential roles %v lack %s", name, m.report.Routers[0].Roles, api.RoleRouter)
	}
	if _, err := peer.Decode(m.report.PeerID); err != nil {
		t.Fatalf("%s report peer_id %q: %v", name, m.report.PeerID, err)
	}
	if len(m.report.RelayAddresses) == 0 {
		t.Fatalf("%s: no relay address, the router did not grant a reservation", name)
	}
	for _, a := range m.report.RelayAddresses {
		if !strings.HasPrefix(a, routerAddr) || !strings.HasSuffix(a, "/p2p-circuit/p2p/"+m.report.PeerID) {
			t.Fatalf("%s relay address %s is not <router>/p2p-circuit/p2p/<self> for router %s", name, a, routerAddr)
		}
	}
	return m
}

func (m *sdkMember) send(t *testing.T, command map[string]string) []byte {
	t.Helper()
	line, _ := json.Marshal(command)
	if _, err := m.stdin.Write(append(line, '\n')); err != nil {
		t.Fatalf("%s member: write command: %v", m.name, err)
	}
	return m.readLine(t, 20*time.Second)
}

// authRaw asks the member to connect to addr and run the auth handshake.
func (m *sdkMember) authRaw(t *testing.T, addr string) sdkAuthResult {
	t.Helper()
	var res sdkAuthResult
	if line := m.send(t, map[string]string{"cmd": "auth", "addr": addr}); json.Unmarshal(line, &res) != nil {
		t.Fatalf("%s member: auth answered %q", m.name, line)
	}
	return res
}

// auth is authRaw that must succeed.
func (m *sdkMember) auth(t *testing.T, addr string) sdkAuthResult {
	t.Helper()
	res := m.authRaw(t, addr)
	if !res.OK {
		t.Fatalf("%s member could not authenticate with %s: %s", m.name, addr, res.Error)
	}
	if res.Expiration <= time.Now().Unix() {
		t.Fatalf("%s member accepted a credential from %s that is already expired", m.name, addr)
	}
	return res
}

func (m *sdkMember) peers(t *testing.T) []string {
	t.Helper()
	var res struct {
		AuthenticatedPeers []string `json:"authenticated_peers"`
	}
	if line := m.send(t, map[string]string{"cmd": "peers"}); json.Unmarshal(line, &res) != nil {
		t.Fatalf("%s member: peers answered %q", m.name, line)
	}
	return res.AuthenticatedPeers
}

func (m *sdkMember) quit(t *testing.T) {
	t.Helper()
	m.send(t, map[string]string{"cmd": "quit"})
	_ = m.stdin.Close()
	exited := make(chan error, 1)
	go func() { exited <- m.cmd.Wait() }()
	select {
	case err := <-exited:
		if err != nil {
			t.Errorf("%s member exited with %v\nstderr:\n%s", m.name, err, m.stderr.String())
		}
	case <-time.After(15 * time.Second):
		_ = m.cmd.Process.Kill()
		<-exited
		t.Errorf("%s member did not exit after quit\nstderr:\n%s", m.name, m.stderr.String())
	}
}

// readLine returns the member's next stdout line or fails after timeout; a
// runner that hangs must not hang the test.
func (m *sdkMember) readLine(t *testing.T, timeout time.Duration) []byte {
	t.Helper()
	type result struct {
		line []byte
		err  error
	}
	ch := make(chan result, 1)
	go func() {
		if m.lines.Scan() {
			ch <- result{line: append([]byte(nil), m.lines.Bytes()...)}
			return
		}
		err := m.lines.Err()
		if err == nil {
			err = io.EOF
		}
		ch <- result{err: err}
	}()
	select {
	case r := <-ch:
		if r.err != nil {
			t.Fatalf("%s member stdout ended: %v\nstderr:\n%s", m.name, r.err, m.stderr.String())
		}
		return r.line
	case <-time.After(timeout):
		t.Fatalf("%s member printed nothing within %v\nstderr:\n%s", m.name, timeout, m.stderr.String())
		return nil
	}
}

// newAdmittedGoPeer is a libp2p host configured like sam-node's, connected
// to the router and past its auth handshake, so the router relays for it.
func newAdmittedGoPeer(t *testing.T, ctx context.Context, cpPriv ed25519.PrivateKey, routerAddr string) host.Host {
	t.Helper()
	h, err := libp2p.New(
		libp2p.NoListenAddrs,
		libp2p.Security(libp2ptls.ID, libp2ptls.New),
		libp2p.EnableRelay(),
	)
	if err != nil {
		t.Fatalf("failed to create go peer: %v", err)
	}
	t.Cleanup(func() { _ = h.Close() })
	info, err := peer.AddrInfoFromString(routerAddr)
	if err != nil {
		t.Fatalf("router address %s: %v", routerAddr, err)
	}
	connectCtx, cancel := context.WithTimeout(ctx, 10*time.Second)
	defer cancel()
	if err := h.Connect(connectCtx, *info); err != nil {
		t.Fatalf("go peer could not connect to the router: %v", err)
	}
	routerBiscuit := authHandshake(t, ctx, h, info.ID, goHostBiscuit(t, cpPriv, h.ID()))
	if err := identity.VerifyBiscuitRole(routerBiscuit, cpPriv.Public().(ed25519.PublicKey), api.RoleRouter, 5*time.Second); err != nil {
		t.Fatalf("router credential lacks the router role: %v", err)
	}
	return h
}

func goHostBiscuit(t *testing.T, cpPriv ed25519.PrivateKey, id peer.ID) []byte {
	t.Helper()
	b, err := identity.MintBootstrapBiscuitToken(cpPriv, id, api.RoleNode, time.Now().Add(time.Hour), nil, nil)
	if err != nil {
		t.Fatalf("failed to mint biscuit for %s: %v", id, err)
	}
	return b
}

// authHandshake runs the client side of /sam/auth/1.0.0 against target and
// returns the credential target answers with.
func authHandshake(t *testing.T, ctx context.Context, h host.Host, target peer.ID, biscuit []byte) []byte {
	t.Helper()
	streamCtx, cancel := context.WithTimeout(network.WithAllowLimitedConn(ctx, "auth"), 10*time.Second)
	defer cancel()
	s, err := h.NewStream(streamCtx, target, api.AuthProtocolID)
	if err != nil {
		t.Fatalf("open auth stream to %s: %v", target, err)
	}
	defer func() { _ = s.Close() }()
	_ = s.SetDeadline(time.Now().Add(10 * time.Second))
	frame, _ := proto.Marshal(&api.AuthFrame{Biscuit: biscuit})
	if err := msgio.NewVarintWriter(s).WriteMsg(frame); err != nil {
		t.Fatalf("write auth frame to %s: %v", target, err)
	}
	msg, err := msgio.NewVarintReaderSize(s, 64*1024).ReadMsg()
	if err != nil {
		t.Fatalf("read auth response from %s: %v", target, err)
	}
	var resp api.AuthResponse
	if err := proto.Unmarshal(msg, &resp); err != nil {
		t.Fatalf("auth response from %s: %v", target, err)
	}
	if !resp.Success {
		t.Fatalf("%s refused the handshake: %s", target, resp.Error)
	}
	return resp.Biscuit
}

// refuseForgedFrame checks that target answers a frame it cannot verify
// with a closed stream and nothing else, as sam-node does.
func refuseForgedFrame(t *testing.T, ctx context.Context, h host.Host, target peer.ID) {
	t.Helper()
	s, err := h.NewStream(network.WithAllowLimitedConn(ctx, "auth"), target, api.AuthProtocolID)
	if err != nil {
		t.Fatalf("open auth stream for the forged frame: %v", err)
	}
	defer func() { _ = s.Close() }()
	forged, _ := proto.Marshal(&api.AuthFrame{Biscuit: []byte("not a biscuit")})
	if err := msgio.NewVarintWriter(s).WriteMsg(forged); err != nil {
		t.Fatalf("write forged frame: %v", err)
	}
	_ = s.SetReadDeadline(time.Now().Add(5 * time.Second))
	if msg, err := msgio.NewVarintReaderSize(s, 64*1024).ReadMsg(); err == nil {
		t.Fatalf("%s answered a forged frame with %d bytes", target, len(msg))
	}
}

// pickDirectAddr is the member's loopback TCP address with its peer ID.
func pickDirectAddr(t *testing.T, addrs []string, peerID string) string {
	t.Helper()
	for _, a := range addrs {
		if strings.HasPrefix(a, "/ip4/127.0.0.1/tcp/") {
			if strings.Contains(a, "/p2p/") {
				return a
			}
			return a + "/p2p/" + peerID
		}
	}
	t.Fatalf("no loopback TCP address among %v", addrs)
	return ""
}

func contains(values []string, want string) bool {
	for _, v := range values {
		if v == want {
			return true
		}
	}
	return false
}
