# Native SDKs

This directory holds the SDKs that let an agent join a SAM mesh from inside
its own process, without a `sam-node` sidecar. Two languages are in scope:

- [`js/`](js/) — `@sam-mesh/sdk`, TypeScript for Node.js, built on
  [js-libp2p](https://github.com/libp2p/js-libp2p).
- [`python/`](python/) — `sam-mesh` (import `agent_mesh`), built on
  [py-libp2p](https://github.com/libp2p/py-libp2p).

The motivation is [issue #480](https://github.com/google/sam/issues/480). The
sidecar works, but it makes every agent deployment manage a second process,
a local port and a token, which does not fit serverless platforms and splits
tracing between the agent and the network layer. An earlier attempt at a
Python package (`sam-mcp-python`) only wrapped the sidecar's HTTP API and was
removed in favour of this work.

Both SDKs speak the mesh protocol directly. There is no SDK-specific
endpoint anywhere in the mesh; a node built with an SDK is, to every other
node and to the control plane, a `sam-node`.

## What exists today

Milestones 1 to 5 are implemented and tested in both languages:

| Capability | JS | Python |
| --- | --- | --- |
| ed25519 identity, libp2p key encodings, peer ID | yes | yes |
| Enrollment with a bootstrap token (`POST /enroll`, `GET /enroll/status` polling) | yes | yes |
| Enrollment with an OIDC token (`POST /register`) | yes | yes |
| Credential refresh (`POST /refresh`), also in the background while joined | yes | yes |
| Signed key-set sync (`GET /keys`) | yes | yes |
| Persisted state (identity and credential, owner-only files) | yes | yes |
| Biscuit verification of a peer's credential (signature, expiry, peer binding, roles, labels) | yes | yes |
| libp2p host as `sam-node` configures it (TCP, TLS, yamux) | yes | yes |
| `/sam/auth/1.0.0`, both sides; join = handshake with a router and check its role | yes | yes |
| Circuit relay v2 reservation on the router; dial and accept through it | yes | yes |
| Service discovery in the mesh DHT (`/sam/kad/1.0.0`) | yes | yes |
| `/sam/mcp/1.0.0` client: list and call a provider's tools, or its catalog | yes | yes |
| Caller-side label requirements on the provider's credential | yes | yes |
| Provider authorizer: the baseline Datalog and the mesh policy (`GET /policies`), evaluated as `sam-node` does | yes | yes |
| `/sam/mcp/1.0.0` server: publish MCP services with an in-process MCP server; the member's own catalog | yes | yes |
| `/libp2p-http` server: publish inference and A2A services, forwarded to a local URL or an in-process handler | yes | yes |
| `/libp2p-http` client: call inference and A2A services on the mesh | yes | yes |
| DHT provide with reprovide, `POST /nodes/catalog` self-report | yes | yes |
| Control plane pull on `sam-node`'s interval: `/keys` verified against the trusted set, credential refresh after a rotation, `/info` bans and router addresses | yes | yes |
| Gossip events from the control plane (`/sam/mesh/events/v1`, StrictSign): ban enforced at once, key rotation adopted, policy update pulls | yes | yes |
| Banned peers refused: connections dropped and denied, handshakes and requests refused, dials refused | yes | yes |
| Published to a registry from the release workflow | npm `@sam-mesh/sdk` | PyPI `sam-mesh` |

A member built this way is on the mesh and uses it in both directions: it
finds a service in the DHT and calls it through the router, verifying the
provider on every call, and it publishes services of its own that a
`sam-node` or another SDK member discovers and calls, authorizing every
caller with the same Datalog the Go node evaluates. It follows the control
plane while it runs: a ban reaches it as a gossip event within a second and
again with the next pull, and a key rotation makes it refresh its credential
under the new key.

Facts about the libp2p implementations that the SDKs work around, each
pinned by a test:

- py-libp2p 0.7 advertises early muxer negotiation in TLS ALPN but does not
  complete it, and go-libp2p then refuses the mux upgrade. The Python host
  sets no ALPN muxer list, so the muxer is negotiated with
  multistream-select (`sdk/python/src/agent_mesh/host.py`).
- js-libp2p's `@libp2p/tls` reads the libp2p extension from
  `extensions[0]` of the peer certificate. py-libp2p's default certificate
  puts BasicConstraints and KeyUsage first, so the Python host uses a
  certificate template with only the libp2p extension.
- py-libp2p's circuit relay v2 client sends its protobufs without the
  varint length prefix that go-libp2p (and js-libp2p) use, so it cannot talk
  to a router. The Python SDK carries the relay protocol itself
  (`sdk/python/src/agent_mesh/relay.py`, `sdk/python/proto/circuit.proto`)
  on top of py-libp2p's raw-connection upgrade.
- go-libp2p's relay leaves private addresses out of a reservation, so a
  router on loopback (every test) grants a reservation that lists no
  address. js-libp2p falls back to the connection's address; the Python SDK
  does the same.
- js-libp2p reserves a relay slot when it starts listening on
  `<relay>/p2p-circuit`, and a router refuses that before the auth
  handshake. The JS SDK starts the listener after the handshake, through the
  transport manager, which is not on the public `Libp2p` interface.
- py-libp2p's Kademlia client takes a protocol prefix but its provider
  lookups still speak `/ipfs/kad/1.0.0`, so it cannot reach the mesh DHT.
  The Python SDK does a bounded GET_PROVIDERS walk itself on
  `/sam/kad/1.0.0`, seeded by the routers (`agent_mesh/discovery.py`).
  js-libp2p's `@libp2p/kad-dht` takes the protocol and works as is.
- go-libp2p-kad-dht keys provider records by the multihash, not the CID.
  `sdk/testdata/service_keys.json` pins the keys both SDKs derive against
  `internal/node/service.go`.
- go-libp2p-kad-dht stores an ADD_PROVIDER record only when the sender is
  the provider it names and lists at least one address, and it answers
  nothing. The Python SDK sends the record itself to the routers and to the
  closer peers they name (`agent_mesh/discovery.py`); js-libp2p's
  `contentRouting.provide` does the same in client mode.
- go-libp2p-http is plain HTTP/1.1 on a stream, one request per stream,
  with `Host` set to the peer ID. The JS SDK bridges the stream to a Node
  duplex and runs Node's HTTP server and client on it; the Python SDK
  drives `h11` over the stream.
- Every Go component runs GossipSub with `StrictSign`. `@libp2p/gossipsub`
  (the libp2p-maintained package; `@chainsafe/libp2p-gossipsub` stops at
  libp2p 2) and py-libp2p's `GossipSub` with `strict_signing=True` both
  exchange messages with `sam-router`. py-libp2p's Pubsub learns of peers
  through a notifee it registers when constructed, so it is built before
  the first connection; it also opens a meshsub stream to every new peer
  and its host logs an error for peers that do not run pubsub, which is
  expected on a mesh with plain peers.
- py-libp2p's connection gate is address-based, not peer-based, so the
  Python SDK enforces a ban by disconnecting the peer and refusing its
  handshakes, requests and dials; js-libp2p's `connectionGater` also denies
  the connection itself, as sam-node's gater does.

## Wire contract

Everything below is what the Go implementation does today, with the source
file that defines it. The SDKs must match it byte for byte; when a Go change
alters one of these, the SDKs change with it.

### Identity

- The identity is an ed25519 key pair. The public key travels in the libp2p
  protobuf encoding `PublicKey{Type: Ed25519, Data: <32 bytes>}`, which is
  the bytes `08 01 12 20` followed by the key. Private keys persist as
  `PrivateKey{Type: Ed25519, Data: <seed || public>}`, bytes `08 01 12 40`
  followed by 64 bytes; this is what `sam-node` writes to its store.
- The peer ID is the identity multihash (`00 24`) of the public key encoding,
  written in base58btc. The control plane refuses an enrollment whose
  `peer_id` is not derived from `public_key`
  (`internal/controlplane/server.go`, `pID.MatchesPublicKey`).

### Control plane (protobuf over HTTP)

All requests and responses are `application/x-protobuf` bodies of the
messages in `api/sam.proto`. Bodies are capped at 1 MiB on both sides.

| Endpoint | Request | Response | Proof of possession |
| --- | --- | --- | --- |
| `GET /info` | — | `ControlPlaneInfoResponse` | none |
| `POST /enroll` | `BootstrapEnrollRequest` | `BootstrapEnrollResponse` | sign `sam:enroll:<peer_id>:<ts>` |
| `GET /enroll/status?peer_id=` | headers `X-Sam-Challenge-Ts`, `X-Sam-Challenge-Sig` (base64url, unpadded) | `BootstrapEnrollResponse` | sign `sam:enroll-status:<peer_id>:<ts>` |
| `POST /register` | `EnrollRequest` | `EnrollResponse` | sign `sam:register:<peer_id>:<ts>` |
| `POST /refresh` | `TokenRefreshRequest`, header `Authorization: Bearer <base64 biscuit>` | `TokenRefreshResponse` | sign `sam:refresh:<peer_id>:<ts>` |
| `GET /keys` | — | `KeysResponse` | none; see below |
| `GET /policies` | header `Authorization: Bearer <base64 biscuit>` | `PolicyConfigGetResponse{datalog_rules}` | none; the biscuit must belong to an admitted node |
| `POST /nodes/catalog` | `NodeCatalogReport`, header `Authorization: Bearer <base64 biscuit>` | `204` | none; the reporting peer is read from the biscuit |

- `<ts>` is unix milliseconds and must be within 5 minutes of the control
  plane's clock (`challengeMaxAge`). Challenges are defined in
  `api/network.go`.
- A bootstrap enrollment answers `PENDING` until an operator approves it,
  unless the control plane runs with auto-approval. The client polls
  `/enroll/status` at the returned `poll_interval_seconds`.
- `/refresh` redeems only the last biscuit the control plane issued for the
  peer. A client must persist the new biscuit before using it; losing it
  means re-enrolling.
- `/keys` signatures cover the deterministic protobuf encoding of
  `KeysResponse{public_keys, timestamp}` with `signatures` cleared, one
  signature per key by that key. The receiver accepts the set when a key it
  already trusts (the enrollment key) vouches for it and the timestamp is
  within 5 minutes (`api/trust.go`). Rotation keeps several keys valid, so
  a member must trust the whole set to verify peers enrolled under a
  retiring key.
- A plaintext `http://` control plane URL is accepted only for a loopback
  host (`api.ValidateControlPlaneTransport`). The SDKs expose the same
  explicit opt-in (`allowInsecure` / `allow_insecure`).
- Bootstrap tokens are secrets. The SDKs read them from a file or a value the
  caller already holds; the conformance runners take a path in the
  environment. Nothing takes a token as a command-line argument.

### libp2p host (`internal/node/node.go`)

- Transports: `libp2p.DefaultTransports` (TCP, QUIC, WebSocket).
- Security: **TLS only** (`libp2p.Security(libp2ptls.ID, libp2ptls.New)`).
  There is no Noise on `sam-node` or `sam-router`. js-libp2p has TLS;
  py-libp2p gained it in
  [libp2p/py-libp2p#831](https://github.com/libp2p/py-libp2p/pull/831) and
  has passed the libp2p transport interoperability suite against the other
  implementations since
  [libp2p/test-plans#798](https://github.com/libp2p/test-plans/pull/798).
- Muxer: yamux (libp2p default).
- Relay: circuit relay v2, with the routers as static relays; hole punching
  is on. A node behind NAT is reached through a router.
- DHT: Kademlia with protocol prefix `/sam`, mode auto.

### Streams

Two protocols, both starting with a length-prefixed protobuf frame. Framing
is the go-msgio varint style (unsigned varint byte length, then the bytes),
with a 64 KiB cap on the first frame.

- `/sam/auth/1.0.0` (`HandleAuthHandshake`, and the router's equivalent).
  Client sends `AuthFrame{biscuit}`; server verifies the biscuit against the
  control plane keys, checks it is bound to the connection's peer ID and not
  revoked, and answers `AuthResponse{success, biscuit}` with its own
  credential. The client verifies that credential the same way and, for a
  router, requires the router role. This is how a member joins: connect to a
  router, run this handshake, and the router admits it to the relay and
  gossip.
- `/sam/mcp/1.0.0` (`WithBiscuitAuth` then `HandleMCPStream`). Client sends
  `AuthFrame{biscuit, target_service: "mcp://<service>", agent}`; server
  authorizes the request with the Biscuit authorizer described below and
  answers `AuthResponse`. The stream then carries MCP JSON-RPC messages, each
  one varint-length-prefixed (`StreamTransport` in `internal/node/gate.go`).
  An empty `target_service` selects the node's own catalog tools.

### Discovery (`internal/node/service.go`)

A provider announces a service as a DHT provider record for
`cidv1(raw, sha256("sam:service:<type>[:<name>]"))`, once for the type and
once for the name (`<type>` is `mcp`, `inference` or `a2a`). A consumer
looks up providers for the same CID, dials one and opens `/sam/mcp/1.0.0`.
Gossip topics under `/sam/discovery/v1/...` carry `ServiceAnnounce` for
interest-scoped updates; the DHT is the source of truth.

### Authorization on the provider side (`internal/node/middleware.go`)

A node that serves a tool evaluates the caller's biscuit with an authorizer
that has, in this order: the biscuit's own authority facts (`node`, `role`,
`label`, `agent`), injected facts `service(<type>, <name>)`,
`connection_peer_id(<peer>)`, `time(<now>)` and optionally `agent(<claim>)`,
the baseline checks, rules and policies from `api/datalog.go`, the
`target_fact` facts derived from the provider's own biscuit, and the mesh
policy rules. Deny by default.

All of that Datalog is text every Biscuit implementation parses, and none
of it is derived in the SDK:

- The baseline (checks, rules, policies, fact names) is generated from
  `api/datalog.go` by `hack/gen-sdk-datalog` into `sdk/js/src/gen/datalog.ts`
  and `sdk/python/src/agent_mesh/_gen/datalog.json`;
  `hack/verify-sdk-generated.sh` fails when they are stale.
- The mesh policy arrives rendered: `GET /policies` answers
  `PolicyConfigGetResponse{datalog_rules}`, one rule per entry, rendered by
  the control plane with `api.BuildPolicyRules`. `sam-node` adds the same
  text; no member compiles roles and bindings itself. An unparseable entry
  rejects the whole response.
- Presence-only facts carry the single term `true`
  (`target_unrestricted(true)`, `granted_service_all_types(true)`,
  `agent_authorized(true)`, …). biscuit-go accepts a predicate with no
  terms; biscuit-rust, and so biscuit-python and biscuit-wasm, do not.
- An unconditional rule is written `head <- true`, which every parser
  accepts as a rule (`role("dev") <- true` for a
  `sam:system:authenticated` binding).

## Plan

Each milestone lands in both languages with its tests before the next one
starts. Where a milestone needs a Go change, the Go change lands first.

### Milestone 1 — identity and enrollment (done)

Described above. Tested by unit tests in each SDK against a fake control
plane and by `tests/integration/sdk_enroll_test.go`, which runs each SDK's
conformance runner against a real control plane in manual-approval mode,
approves the request through `/admin`, and checks the biscuits the SDK
holds against the control plane's records.

### Milestone 2 — join the mesh (done)

- libp2p host per SDK, configured as `sam-node`'s: TCP, TLS, yamux,
  identify, circuit relay v2 client. JS: `libp2p`, `@libp2p/tcp`,
  `@libp2p/tls`, `@chainsafe/libp2p-yamux`, `@libp2p/circuit-relay-v2`,
  `@libp2p/identify`. Python: `libp2p>=0.7` (`security.tls`,
  `stream_muxer.yamux`) plus the SDK's own relay client; py-libp2p is
  trio-based, so `join()` is an async context manager.
- Biscuit verification of a peer's credential: signature under any trusted
  control plane key, authority block only, expiry, binding to the
  connection peer ID, roles and labels. JS: `@biscuit-auth/biscuit-wasm`,
  instantiated by hand so no Node flag is needed; Python: `biscuit-python`.
  `sdk/testdata/biscuit_vectors.json` holds tokens minted by
  `internal/identity` (valid, other peer, expired, untrusted key, appended
  block, garbage) and both verifiers agree with the Go one on all of them.
- `/sam/auth/1.0.0` on both sides. Join connects to the routers in the
  credential, runs the handshake, requires the router role under the key
  that verified the token, and reserves a relay slot; the router grants one
  only to an authenticated peer. Inbound handshakes are answered the way
  `HandleAuthHandshake` does: a credential that does not verify gets a
  closed stream and nothing else.
- `session.authenticate(addr)` connects to any peer, directly or through a
  router (`/p2p-circuit`), and runs the handshake; `session.authenticatedPeers`
  is the admitted set. Background refresh is driven by the biscuit's
  expiration, as `StartRenewalLoop` does.
- Tests. Unit: each SDK joins an in-process fake router (its own libp2p
  with the handshake and a relay), and refuses a relay without the router
  role, a router that trusts another control plane, and a refused
  reservation. Integration: `tests/integration/sdk_mesh_test.go` runs one
  mesh with a control plane, a `sam-router`, a `sam-node` and one member per
  SDK, all real, then walks the connectivity matrix: every member on the
  router's lease; each SDK member verifies the `sam-node` and the other SDK
  member on both the direct and the relayed path, and is verified by them;
  the `sam-node` reaches each member through the router; an admitted Go peer
  does the same and sends a forged frame; an address behind the router for
  a peer that is not on the mesh fails. About 7 seconds.

### Milestone 3 — call tools (done)

- Discovery: `session.discover("mcp://calc")`, or `(type, name)`, or a type
  alone, looks the mesh DHT up for the service key
  `internal/node/service.go` derives. JS: `@libp2p/kad-dht` in client mode
  on `/sam/kad/1.0.0`; Python: the SDK's own bounded GET_PROVIDERS walk on
  the same protocol (see the facts above).
- Naming a peer: every call takes a `Peer`, which is a provider `discover`
  returned, a peer id, or a multiaddr. For the first two the SDK dials the
  advertised addresses and `<router>/p2p-circuit/p2p/<peer>` through every
  admitted router, as `preparePeerAddrs` in `internal/node/mcp.go` does; a
  multiaddr is dialed as given.
- `/sam/mcp/1.0.0` client: `session.openMCP(peer, "mcp://<name>")` sends
  the `AuthFrame` naming the service, verifies the provider's credential
  and the caller's required labels (`checkPeerLabels`), then runs the
  official MCP client over the varint-framed stream. JS: a `Transport` for
  `@modelcontextprotocol/sdk`; Python: a pair of memory streams pumped to
  and from the libp2p stream for `mcp.ClientSession`. `""` as the target is
  the provider's own catalog (`list_local_services`, `get_mesh_info`).
- `session.listTools(peer, service)` and `session.callTool(peer, service,
  tool, args)` on top of that.
- Tests. Unit: each SDK calls a tool on an in-process provider that serves
  `/sam/mcp/1.0.0` as `sam-node` does with the official MCP server behind
  it, reads its catalog, and is refused for missing labels, an untrusted
  provider, a forged caller credential and an unknown service. Integration:
  the mesh of `sdk_mesh_test.go` has an MCP backend behind the `sam-node`
  (`calc`, tool `add`); each SDK member discovers it in the DHT, lists and
  calls `add` through the router, reads the node's catalog and is refused a
  service the node does not have. The runners took `discover`, `tools` and
  `call` commands for it.

### Milestone 4 — serve tools (done)

- Datalog as the contract, Go side: the baseline moved into a generated
  artifact both SDKs embed, the control plane renders the mesh policy as
  `datalog_rules`, and `sam-node` consumes that text like any other member.
  See the authorization section above.
- Provider authorizer in each SDK (`authorizer.ts`, `authorizer.py`), built
  from the artifact and `datalog_rules`, evaluated on every inbound
  `AuthFrame` and every `/libp2p-http` request in the order
  `internal/node/middleware.go` uses.
- `/sam/mcp/1.0.0` server: read the `AuthFrame`, authorize, answer with the
  member's credential, then hand the stream to the service's MCP server
  (`@modelcontextprotocol/sdk` `McpServer`; `mcp.server.mcpserver.MCPServer`
  run over pumped streams). A denied caller gets `AuthResponse{success:
  false}` with the reason; a granted service the member does not publish
  closes the stream after the answer, as `sam-node` does. The empty target is
  the member's catalog (`list_local_services`).
- `/libp2p-http` server for inference and A2A: the path is
  `/<type>/<name>/<upstream>`, the biscuit is `X-Sam-Biscuit`; after
  authorization the request goes to a local URL or an in-process handler
  with the biscuit and agent headers stripped and `X-Peer-Id` set to the
  verified caller. `session.request(addr, "inference://<name>", path)` is
  the client side.
- `session.serve(spec)` registers the service, fetches the mesh policy on
  the first call and re-reads it on `sam-node`'s sync interval, announces
  the service in the DHT (type and name, reprovided periodically) and
  reports it to `POST /nodes/catalog`.
- Tests. Unit: each SDK's handlers behind an in-process host, called with
  its own clients: an MCP tool, the catalog, a proxied backend that sees
  `X-Peer-Id` and never the biscuit, an in-process handler, and refusals for
  a role that grants nothing, an ungranted service, a missing service, a
  missing biscuit and a dotted path; plus the authorizer alone against the
  decisions `internal/node/middleware_test.go` pins. Integration: in the
  mesh of `sdk_mesh_test.go` each SDK member publishes `mcp://echo-<sdk>`
  and `inference://llm-<sdk>`; the `sam-node` calls the tool with
  `call_remote_tool` and the inference endpoint through its egress proxy,
  the other SDK member discovers both in the DHT and calls them through the
  router, and a Go peer the control plane vouches for but whose role the
  policy grants nothing is refused at the `AuthResponse` and with 403 on
  the HTTP path, then served once it presents a node-role token. About 9
  seconds for the whole mesh.

### Milestone 5 — parity and release (done)

- Control plane pull (`session.sync()`, `mesh.syncControlPlane()`), as
  `SyncControlPlane` in `internal/node/controlplane_sync.go`: `/keys`
  verified against the keys already trusted, so whoever answers the URL
  cannot become the trust root; a credential refresh when a key trusted now
  was unknown when the credential was issued (`issuedUnderKeys` in the
  credential file); `/info` for router addresses and the ban set,
  reconciled with the rule that a ban recorded after the request went out
  survives an answer that omits it; the mesh policy when serving. Runs
  once shortly after join, then on `sam-node`'s interval with the same
  jitter, and whenever an event triggers it.
- Gossip events on `/sam/mesh/events/v1`: the topic validator rejects
  anything not signed by a trusted control plane key and ignores stale
  events; `BANNED` evicts the peer at once, `KEY_ROTATION` adopts the key
  and triggers a pull, `POLICY_UPDATE` triggers a pull. Bans are not
  persisted; a restarted member reads them from `/info`.
- Ban enforcement: the auth handshake, the MCP handshake and HTTP ingress
  refuse a banned peer before looking at its token; `connect()` refuses to
  dial one; its connections are dropped when the ban lands.
- Publishing: `.github/workflows/release.yml` stamps the release tag's
  version on both packages (`hack/sdk-version.sh`) and publishes
  `@sam-mesh/sdk` to npm and `sam-mesh` to PyPI through trusted
  publishing. The job runs only when the repository variable
  `PUBLISH_SDKS` is `true`, so a release before the registries are set up
  stays green. One-time setup by a package owner: on npmjs.com, create the
  `sam-mesh` organization, publish `@sam-mesh/sdk` 0.1.0 once by hand
  (`cd sdk/js && npm publish --access public`; the trusted-publisher
  settings live on the package page, which exists only after that), then
  register `google/sam` with workflow `release.yml` under the package's
  Settings, Trusted publishing; on pypi.org, add a pending publisher for
  project `sam-mesh` with the same repository and workflow (environment
  left empty), which reserves the name and lets the first tag create the
  project. Then set `PUBLISH_SDKS`. No publishing token is stored in the
  repository.
- Docs: `site/content/docs/guides/native-sdks.md`.
- Tests. Unit: a fake control plane rotates its key and bans a peer; each
  SDK learns both from a pull, refreshes under the new key and only under
  it, drops and refuses the banned peer, and lifts the ban when the control
  plane does; `BanSet` and `verifyMeshEvent` alone. Integration: the mesh
  of `sdk_mesh_test.go` gets the control plane's real event publisher; an
  enrolled Go peer admitted by every member is banned with
  `POST /admin/revoke` and each member learns it from the gossip event
  through the router without pulling, refuses the peer's next handshake,
  and a pull agrees; then the control plane rotates its key and announces
  it, and every member ends up trusting both keys, holding a credential
  that verifies only under the new one, and still authenticating with the
  `sam-node`.

### Later

- The connector interface for platforms (issue #480): `Attach`, `Detach`,
  `Refresh`, `Status` and the agent bundle, so a scheduler places SDK
  members the way it places `sam-box` sandboxes.
- Reading the control plane's events also for router address changes, and
  re-dialing a router that replaced another.

### Non-goals

- Rewriting the control plane, router or sandbox components; they stay Go.
- A browser build. Node.js only until the transport story for browsers
  (WebTransport or WebRTC to routers) is designed.
- Any SDK-only wire protocol. If an SDK needs something the Go node does not
  speak, the Go node learns it first.

## Working on the SDKs

```bash
# Regenerate protobuf bindings and the Datalog artifact after editing
# api/sam.proto or api/datalog.go
./hack/gen-sdk-proto.sh

# JavaScript
cd sdk/js && npm ci && npm test && npm run build && npm run examples

# Python
python3 -m venv sdk/python/.venv
sdk/python/.venv/bin/pip install -e 'sdk/python[test]'
sdk/python/.venv/bin/pytest sdk/python/tests

# Both against a real control plane, router and sam-node, and the example
# programs the docs embed against the same
go test ./tests/integration -run TestNativeSDK -v
```

`make sdk-test` runs all of the above. The integration tests skip an SDK
whose toolchain is missing, and CI (`.github/workflows/sdk.yml`) installs
both so nothing skips there. The SDK members in the mesh test are the
runners `sdk/js/src/conformance-join.ts` and
`sdk/python/src/agent_mesh/conformance_join.py`: each joins, prints what it
holds, then takes JSON commands on stdin (`auth`, `discover`, `tools`,
`call`, `serve`, `http`, `peers`, `sync`, `banned`, `quit`) so the Go test
can drive both languages through the same script.

The programs the package READMEs and the Native SDKs guide show are
`sdk/js/examples/*.ts` and `sdk/python/examples/*.py`. The Markdown embeds
them between `<!-- embed: <path> -->` and `<!-- /embed -->` markers;
`make sdk-docs` (`go run ./hack/gen-sdk-docs sdk site/content/docs`) copies
the files in, `hack/verify-sdk-generated.sh` fails when a copy is stale, and
`TestNativeSDKExamples` runs the files themselves against a mesh. Edit the
example, regenerate, commit both.

`sdk/js/.npmrc` pins the public registry so `package-lock.json` never
resolves packages through a local mirror; the verify script checks the
lockfile too.

Interoperability facts that the tests pin: `sdk/testdata/identity_vectors.json`
holds key encodings, peer IDs and challenge signatures produced with
go-libp2p, `sdk/testdata/biscuit_vectors.json` holds tokens minted by the
control plane's code, and every SDK reproduces or agrees with them.

The package READMEs (`sdk/js/README.md`, `sdk/python/README.md`) are the
registry pages on npm and PyPI: they hold what a user of the package needs
and nothing about this repository's internals. Everything below is for
contributors.

### Layout

Both SDKs mirror the same Go code, one module per concern; the Go file each
one follows is named so a change on one side can be carried to the others.

| JavaScript (`sdk/js/src/`) | Python (`sdk/python/src/agent_mesh/`) | Mirrors |
| --- | --- | --- |
| `identity.ts` | `identity.py` | ed25519 key pair, libp2p key encodings, peer ID |
| `controlplane.ts` | `controlplane.py` | `/info`, `/keys`, `/enroll`, `/enroll/status`, `/register`, `/refresh`, `/policies`, `/nodes/catalog`, with the challenges of `api/network.go` |
| `credential.ts` | `credential.py` | what a member holds, `AuthFrame` encoding, `issuedUnderKeys` |
| `mesh.ts` | `mesh.py` | `AgentMesh`: enroll (resumes an unexpired credential in the state directory before spending a token), load, refresh, `syncControlPlane` as `SyncControlPlane` in `internal/node/controlplane_sync.go`; state directory (`identity.key`, `credential.json`, the same layout in both languages) |
| `biscuit.ts` | `biscuit.py` | verification of a peer's credential, as `internal/identity.verifyBiscuit` |
| `host.ts` | `host.py` | the libp2p host as `internal/node/node.go` configures it, plus gossipsub and the connection gater |
| `auth.ts` | `auth.py` | `/sam/auth/1.0.0` on both sides, as `HandleAuthHandshake` |
| — | `relay.py` | circuit relay v2 client (py-libp2p's cannot talk to go-libp2p) |
| `session.ts` | `session.py` | `MeshSession`: join, relay reservation, refresh loop, control plane sync loop, gossip events, `serve()` |
| `discovery.ts` | `discovery.py` | service keys and DHT lookups as `internal/node/service.go`; Python also provides, py-libp2p's client cannot |
| `mcp.ts` | `mcp_client.py` | MCP over `/sam/mcp/1.0.0`, the client side of `internal/node/gate.go` |
| `authorizer.ts` | `authorizer.py` | the provider authorizer, as `internal/node.(*SamNode).Authorize`, over the generated baseline and `datalog_rules` |
| `serve.ts` | `serve.py` | `/sam/mcp/1.0.0` server, `/libp2p-http` server and client, service registry, as `WithBiscuitAuth` and `StartIngressServer` |
| `sync.ts` | `sync.py` | ban set and mesh event verification, as `reconcileBannedPeers` and `verifyEvent` |
| `conformance.ts`, `conformance-join.ts` | `conformance.py`, `conformance_join.py` | the runners the integration tests drive |
| `../examples/` | `../../examples/` | the programs the docs embed and `TestNativeSDKExamples` runs |
| `gen/` | `_proto/`, `_gen/` | generated by `hack/gen-sdk-proto.sh` from `api/sam.proto`, `sdk/python/proto/circuit.proto` and `api/datalog.go` |

Unit tests sit beside the code (`*.test.ts`, `tests/test_*.py`) and build a
fake control plane and router in the process; the real ones are exercised
only by `tests/integration`.
