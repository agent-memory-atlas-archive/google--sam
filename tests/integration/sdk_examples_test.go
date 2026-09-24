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
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/google/sam/api"
)

// sdkExampleLauncher starts one of the example programs the SDK READMEs and
// the Native SDKs guide embed (sdk/js/examples, sdk/python/examples), so
// the documented programs are the ones exercised here.
type sdkExampleLauncher struct {
	name string
	cmd  func(ctx context.Context, root, example string, args ...string) (*exec.Cmd, string)
}

var sdkExampleLaunchers = []sdkExampleLauncher{
	{
		name: "js",
		cmd: func(ctx context.Context, root, example string, args ...string) (*exec.Cmd, string) {
			entry := filepath.Join(root, "sdk", "js", "build", "examples", example+".js")
			if _, err := exec.LookPath("node"); err != nil {
				return nil, "node is not installed"
			}
			if _, err := os.Stat(entry); err != nil {
				return nil, "sdk/js examples are not built (cd sdk/js && npm ci && npm run build && npm run examples)"
			}
			return exec.CommandContext(ctx, "node", append([]string{entry}, args...)...), ""
		},
	},
	{
		name: "python",
		cmd: func(ctx context.Context, root, example string, args ...string) (*exec.Cmd, string) {
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
			entry := filepath.Join(root, "sdk", "python", "examples", example+".py")
			return exec.CommandContext(ctx, python, append([]string{entry}, args...)...), ""
		},
	},
}

// sdkExampleServer is a running serve example.
type sdkExampleServer struct {
	name string
	// service is what it publishes, greeter-<lang>, as the testnet canaries do.
	service string
	peerID  string
}

var servingLine = regexp.MustCompile(`^serving (.*) as (\S+)$`)

// TestNativeSDKExamples runs the programs the SDK READMEs and the Native
// SDKs guide embed, unchanged, against a real mesh, configured as the
// testnet canaries are (.github/k8s/sam-sdk-canary-template.yaml). Each
// SDK's serve example enrolls with an OIDC token through SAM_JWT_PATH, the
// way a Kubernetes workload does, and publishes mcp://greeter-<lang>,
// a2a://greeter-<lang> and inference://ollama, the last forwarding to a
// stand-in for Ollama this test runs. Each SDK's call example enrolls with
// a bootstrap token, reaches the sam-node's mcp://calc and the other
// language's greeter (its own when the other toolchain is missing), naming
// providers only by what discover returned; the SDK finds the path through
// the router by itself. The first run spends the token, the later runs
// resume from the state directory without one.
func TestNativeSDKExamples(t *testing.T) {
	mesh := startSDKMesh(t)

	ollama := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/v1/models" {
			http.NotFound(w, r)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = io.WriteString(w, `{"object":"list","data":[{"id":"gemma3","object":"model","owned_by":"`+r.Header.Get("X-Peer-Id")+`"}]}`)
	}))
	t.Cleanup(ollama.Close)

	var launchers []sdkExampleLauncher
	var cmds []*exec.Cmd
	for _, l := range sdkExampleLaunchers {
		cmd, skip := l.cmd(context.Background(), mesh.root, "serve", "greeter-"+l.name)
		if skip != "" {
			t.Logf("%s SDK skipped: %s", l.name, skip)
			continue
		}
		launchers = append(launchers, l)
		cmds = append(cmds, cmd)
	}
	if len(launchers) == 0 {
		t.Skip("no SDK toolchain available; see sdk/README.md")
	}
	servers := make([]*sdkExampleServer, len(launchers))
	errs := make([]error, len(launchers))
	var wg sync.WaitGroup
	for i := range launchers {
		jwtPath := filepath.Join(t.TempDir(), "sam-token")
		jwt := mesh.mintToken(map[string]interface{}{"sub": "mock-user", "roles": []string{api.RoleNode}})
		if err := os.WriteFile(jwtPath, []byte(jwt+"\n"), 0o600); err != nil {
			t.Fatal(err)
		}
		cmds[i].Env = append(os.Environ(),
			"SAM_CONTROL_PLANE_URL="+mesh.baseURL,
			"SAM_JWT_PATH="+jwtPath,
			"SAM_STATE_DIR="+filepath.Join(t.TempDir(), "state"),
			"OLLAMA_URL="+ollama.URL,
		)
		cmds[i].Dir = mesh.root
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			servers[i], errs[i] = startExampleServer(t, launchers[i].name, "greeter-"+launchers[i].name, cmds[i])
		}(i)
	}
	wg.Wait()
	for _, err := range errs {
		if err != nil {
			t.Fatal(err)
		}
	}
	for _, s := range servers {
		waitForPeerOnRouter(t, mesh.cpPort, mesh.adminToken, s.peerID, 5*time.Second)
	}

	// The greeter a caller of one language targets: the other language's,
	// as the testnet probes do, or its own when it is the only one present.
	peerServer := func(name string) *sdkExampleServer {
		for _, s := range servers {
			if s.name != name {
				return s
			}
		}
		return servers[0]
	}
	servedBy := func(t *testing.T, out string, want *sdkExampleServer, serviceType string) {
		t.Helper()
		if got := expectLine(t, out, serviceType+"://"+want.service+" is served by "); got != want.peerID {
			t.Fatalf("%s://%s is served by %s, want the %s serve example %s", serviceType, want.service, got, want.name, want.peerID)
		}
	}

	for _, l := range launchers {
		l := l
		target := peerServer(l.name)
		t.Run(l.name+"-calls", func(t *testing.T) {
			t.Parallel()
			stateDir := filepath.Join(t.TempDir(), "state")
			tokenPath := filepath.Join(t.TempDir(), "join-token")
			if err := os.WriteFile(tokenPath, []byte(mintBootstrapToken(t, mesh.baseURL, mesh.adminToken)+"\n"), 0o600); err != nil {
				t.Fatal(err)
			}
			withToken := []string{"SAM_CONTROL_PLANE_URL=" + mesh.baseURL, "SAM_STATE_DIR=" + stateDir, "SAM_BOOTSTRAP_TOKEN_PATH=" + tokenPath}
			withoutToken := withToken[:2]

			// The first run spends the token: the sam-node's calc over MCP.
			out := runExample(t, mesh, l, withToken, "mcp://calc", "add", `{"a": 1, "b": 2}`)
			caller := expectLine(t, out, "on the mesh as ")
			expectLine(t, out, "mcp://calc is served by "+mesh.samNode.peerID.String())
			expectLine(t, out, "tools: add")
			expectLine(t, out, "fake-result:add")

			// The state directory now holds the identity and credential; the
			// token file is gone and not needed. The HTTP path: an in-process
			// A2A handler on one side, a forward to the Ollama stand-in on the
			// other. The provider sees the verified caller as X-Peer-Id, never
			// the biscuit.
			if err := os.Remove(tokenPath); err != nil {
				t.Fatal(err)
			}
			out = runExample(t, mesh, l, withoutToken, "a2a://"+target.service, "/card")
			if got := expectLine(t, out, "on the mesh as "); got != caller {
				t.Fatalf("second run joined as %s, want the identity of the first run %s", got, caller)
			}
			servedBy(t, out, target, "a2a")
			card := expectLine(t, out, "200 ")
			if !strings.Contains(card, `"path": "/card"`) && !strings.Contains(card, `"path":"/card"`) {
				t.Fatalf("a2a card %q does not echo the path", card)
			}
			if !strings.Contains(card, caller) {
				t.Fatalf("a2a card %q does not name the verified caller %s", card, caller)
			}
			out = runExample(t, mesh, l, withoutToken, "inference://ollama", "/v1/models")
			if got := expectLine(t, out, "inference://ollama is served by "); got != servers[0].peerID && (len(servers) < 2 || got != servers[1].peerID) {
				t.Fatalf("inference://ollama is served by %s, which is none of the serve examples", got)
			}
			models := expectLine(t, out, "200 ")
			if !strings.Contains(models, `"gemma3"`) || !strings.Contains(models, caller) {
				t.Fatalf("models %q: want gemma3 owned by the caller %s", models, caller)
			}

			// The state directory is the same layout in every implementation:
			// the other SDK's call example resumes this identity from it, and
			// so does a sam-node after `state import`. Each proves it by
			// reaching a greeter as the same peer.
			for _, other := range launchers {
				if other.name == l.name {
					continue
				}
				out := runExample(t, mesh, other, withoutToken, "mcp://"+target.service, "greet", `{"name": "sam"}`)
				if got := expectLine(t, out, "on the mesh as "); got != caller {
					t.Fatalf("%s resumed %s's state directory as %s, want %s", other.name, l.name, got, caller)
				}
				expectLine(t, out, "hello sam")
			}
			importedNode := importStateIntoNode(t, mesh, stateDir)
			if importedNode.peerID.String() != caller {
				t.Fatalf("sam-node imported %s's state directory as %s, want %s", l.name, importedNode.peerID, caller)
			}
			importedAPI := importedNode.waitForAPI(t)
			waitForPeerOnRouter(t, mesh.cpPort, mesh.adminToken, caller, 10*time.Second)
			answer, err := callMCPAllowError(t, importedAPI, "imported-token", "call_remote_tool", map[string]any{
				"peer_id": target.peerID, "tool_name": "mcp://" + target.service + "/greet", "arguments": map[string]any{"name": "node"},
			})
			if err != nil || !strings.Contains(answer, "hello node") {
				t.Fatalf("sam-node running %s's identity could not call %s: %v\n%s", l.name, target.service, err, answer)
			}
			// The node's A2A egress path reaches the SDK's handler too, as the
			// testnet's node probe expects.
			status, body := egressGet(t, importedAPI, "imported-token", "/sam/"+target.peerID+"/a2a/"+target.service+"/card")
			if status != 200 || !strings.Contains(body, `"caller"`) || !strings.Contains(body, caller) {
				t.Fatalf("a2a card through the node: %d %s", status, body)
			}
		})
	}
}

// importStateIntoNode runs `sam-node state import` on a fresh data directory
// and starts a node from it, so the node runs as the member the directory
// holds. The import refuses a token: the identity is already enrolled.
func importStateIntoNode(t *testing.T, mesh *sdkMesh, stateDir string) *backgroundNode {
	t.Helper()
	nodeBin := buildBinary(t, "./cmd/sam-node")
	nodeHome := filepath.Join(t.TempDir(), "imported")
	dataDir := filepath.Join(nodeHome, "data")
	importCmd := exec.Command(nodeBin, "state", "import", stateDir, "--data-dir", dataDir)
	importCmd.Dir = mesh.root
	if out, err := importCmd.CombinedOutput(); err != nil {
		t.Fatalf("sam-node state import: %v\n%s", err, out)
	}
	return launchNode(t, nodeBin,
		append(os.Environ(), "HOME="+nodeHome, "XDG_CONFIG_HOME="+filepath.Join(nodeHome, ".config")),
		nodeHome, "run",
		"--data-dir", dataDir,
		"--control-plane", mesh.baseURL,
		"--allow-loopback",
		"--api-token-path", tokenPath(t, "imported-token"),
		"--discovery-interval", "100ms",
		"--log-level", "debug",
	)
}

// startExampleServer starts a configured serve example and waits for its
// "serving ... as <peer>" line. Safe to call from several goroutines at
// once, so it reports failures instead of ending the test.
func startExampleServer(t *testing.T, name, service string, cmd *exec.Cmd) (*sdkExampleServer, error) {
	stderr := &bytes.Buffer{}
	cmd.Stderr = stderr
	stdout, err := cmd.StdoutPipe()
	if err != nil {
		return nil, err
	}
	if err := cmd.Start(); err != nil {
		return nil, fmt.Errorf("failed to start %s serve example: %w", name, err)
	}
	t.Cleanup(func() {
		_ = cmd.Process.Signal(os.Interrupt)
		done := make(chan struct{})
		go func() { _ = cmd.Wait(); close(done) }()
		select {
		case <-done:
		case <-time.After(5 * time.Second):
			_ = cmd.Process.Kill()
			<-done
		}
		if t.Failed() {
			t.Logf("%s serve example stderr:\n%s", name, stderr.String())
		}
	})

	lines := make(chan string, 1)
	go func() {
		scanner := bufio.NewScanner(stdout)
		for scanner.Scan() {
			if m := servingLine.FindStringSubmatch(scanner.Text()); m != nil {
				lines <- scanner.Text()
				return
			}
		}
		close(lines)
	}()
	select {
	case line, ok := <-lines:
		if !ok {
			return nil, fmt.Errorf("%s serve example exited before serving\nstderr:\n%s", name, stderr.String())
		}
		m := servingLine.FindStringSubmatch(line)
		for _, want := range []string{"mcp://" + service, "a2a://" + service, "inference://ollama"} {
			if !strings.Contains(m[1], want) {
				return nil, fmt.Errorf("%s serve example serves %q, want %s among them", name, m[1], want)
			}
		}
		return &sdkExampleServer{name: name, service: service, peerID: m[2]}, nil
	case <-time.After(30 * time.Second):
		return nil, fmt.Errorf("%s serve example did not report serving within 30s\nstderr:\n%s", name, stderr.String())
	}
}

// runExample runs a call example to completion and returns its stdout.
func runExample(t *testing.T, mesh *sdkMesh, l sdkExampleLauncher, env []string, args ...string) string {
	t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	cmd, skip := l.cmd(ctx, mesh.root, "call", args...)
	if skip != "" {
		t.Fatalf("%s: %s", l.name, skip)
	}
	cmd.Env = append(os.Environ(), env...)
	cmd.Dir = mesh.root
	stderr := &bytes.Buffer{}
	cmd.Stderr = stderr
	stdout, err := cmd.Output()
	if err != nil {
		t.Fatalf("%s call %v failed: %v\nstdout:\n%s\nstderr:\n%s", l.name, args, err, stdout, stderr.String())
	}
	return string(stdout)
}

// expectLine returns the rest of the first stdout line starting with prefix.
func expectLine(t *testing.T, out, prefix string) string {
	t.Helper()
	for _, line := range strings.Split(out, "\n") {
		if strings.HasPrefix(line, prefix) {
			return strings.TrimSpace(strings.TrimPrefix(line, prefix))
		}
	}
	t.Fatalf("no line starting with %q in output:\n%s", prefix, out)
	return ""
}
