# @sam-mesh/sdk

Native JavaScript SDK for joining a SAM agent mesh from inside the agent
process. It replaces the `sam-node` sidecar for agents written for Node.js:
the agent enrolls with the control plane, joins the mesh through a router,
finds services and calls them, publishes services of its own, and follows
the control plane's keys, bans and policy while it runs.

Guide: [sam-mesh.dev/docs/guides/native-sdks](https://sam-mesh.dev/docs/guides/native-sdks/),
from an empty machine to two programs on a mesh.
Source: [github.com/google/sam/tree/main/sdk/js](https://github.com/google/sam/tree/main/sdk/js).

## Install

```bash
npm install @sam-mesh/sdk @modelcontextprotocol/sdk zod
```

Requires Node.js 22.18 or later.

## Use

Both programs below are in
[`examples/`](https://github.com/google/sam/tree/main/sdk/js/examples) and
run against a real mesh in the repository's tests. They read the mesh from
`SAM_CONTROL_PLANE_URL` and the enrollment token from
`SAM_BOOTSTRAP_TOKEN_PATH`; the guide shows how to get both from `sam-one`
or from the operator of an existing mesh.

Find a service and call it:

<!-- embed: sdk/js/examples/call.ts -->
```ts
// Finds a service on the mesh and calls it: a tool of an MCP service, or a
// path of an inference or A2A service.
//
//   node call.js mcp://greeter greet '{"name": "Ada"}'
//   node call.js a2a://greeter /card
//   node call.js inference://ollama /v1/models
//
// SAM_CONTROL_PLANE_URL names the mesh. The first run enrolls with the file
// SAM_BOOTSTRAP_TOKEN_PATH (a token the mesh operator gave you) or
// SAM_JWT_PATH (a workload identity token your platform issues, such as a
// Kubernetes projected service account token), and keeps the identity and
// credential in SAM_STATE_DIR; later runs resume from there without it.
import { homedir } from "node:os";
import { AgentMesh, type DiscoveredProvider } from "@sam-mesh/sdk";

const [service = "mcp://greeter", toolOrPath = "greet", args = '{"name": "world"}'] = process.argv.slice(2);

const mesh = await AgentMesh.enroll({
  controlPlaneUrl: process.env.SAM_CONTROL_PLANE_URL ?? "https://mesh.example.com",
  bootstrapTokenPath: process.env.SAM_BOOTSTRAP_TOKEN_PATH,
  jwtPath: process.env.SAM_JWT_PATH,
  stateDir: process.env.SAM_STATE_DIR ?? `${homedir()}/.config/sam-mesh/caller`,
  // A plaintext http:// control plane is otherwise accepted only on loopback.
  allowInsecure: process.env.SAM_INSECURE_CONTROL_PLANE === "true",
});
const session = await mesh.join();
console.log(`on the mesh as ${session.peerId}`);

const providers = await session.discover(service);
if (providers.length === 0) {
  throw new Error(`no member of the mesh serves ${service}`);
}
// A provider record can outlive its member; the first that answers is used.
let provider: DiscoveredProvider | undefined;
for (const candidate of providers) {
  try {
    await session.connect(candidate);
    provider = candidate;
    break;
  } catch (err) {
    console.error(`${candidate.peerId}: ${(err as Error).message}`);
  }
}
if (provider === undefined) {
  throw new Error(`no provider of ${service} is reachable`);
}
console.log(`${service} is served by ${provider.peerId}`);

if (service.startsWith("mcp://")) {
  const tools = await session.listTools(provider, service);
  console.log(`tools: ${tools.map((t) => t.name).join(", ")}`);
  const result = await session.callTool(provider, service, toolOrPath, JSON.parse(args));
  console.log(result.text.join("\n"));
} else {
  const response = await session.request(provider, service, toolOrPath);
  console.log(response.status, response.text());
}

await session.close();
```
<!-- /embed -->

Publish services of your own:

<!-- embed: sdk/js/examples/serve.ts -->
```ts
// Publishes services on the mesh and answers callers until stopped: an MCP
// tool, an A2A endpoint and, when OLLAMA_URL is set, the Ollama server running
// beside this program as an inference service. The mesh policy decides which
// members may call; the SDK turns the others away before anything reaches
// this code or Ollama.
//
//   node serve.js            # publishes mcp://greeter and a2a://greeter
//   node serve.js greeter-2  # the same under another name
//
// SAM_CONTROL_PLANE_URL names the mesh. The first run enrolls with the file
// SAM_BOOTSTRAP_TOKEN_PATH (a token the mesh operator gave you) or
// SAM_JWT_PATH (a workload identity token your platform issues, such as a
// Kubernetes projected service account token), and keeps the identity and
// credential in SAM_STATE_DIR; later runs resume from there without it.
import { homedir } from "node:os";
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { AgentMesh } from "@sam-mesh/sdk";
import { z } from "zod";

const [name = "greeter"] = process.argv.slice(2);

const mesh = await AgentMesh.enroll({
  controlPlaneUrl: process.env.SAM_CONTROL_PLANE_URL ?? "https://mesh.example.com",
  bootstrapTokenPath: process.env.SAM_BOOTSTRAP_TOKEN_PATH,
  jwtPath: process.env.SAM_JWT_PATH,
  stateDir: process.env.SAM_STATE_DIR ?? `${homedir()}/.config/sam-mesh/${name}`,
  // A plaintext http:// control plane is otherwise accepted only on loopback.
  allowInsecure: process.env.SAM_INSECURE_CONTROL_PLANE === "true",
});
const session = await mesh.join();

await session.serve({
  type: "mcp",
  name,
  createServer: () => {
    const server = new McpServer({ name, version: "1.0.0" });
    server.registerTool("greet", { description: "Greets someone by name", inputSchema: { name: z.string() } }, async ({ name: who }) => ({
      content: [{ type: "text", text: `hello ${who}` }],
    }));
    return server;
  },
});

await session.serve({
  type: "a2a",
  name,
  target: (request, caller) => Response.json({ name, path: new URL(request.url).pathname, caller: caller.peerId }),
});

if (process.env.OLLAMA_URL !== undefined) {
  await session.serve({ type: "inference", name: "ollama", target: process.env.OLLAMA_URL });
}

console.log(`serving ${session.servedServices.map((s) => `${s.type}://${s.name}`).join(", ")} as ${session.peerId}`);

const stop = () => void session.close().then(() => process.exit(0));
process.on("SIGINT", stop);
process.on("SIGTERM", stop);
```
<!-- /embed -->

`enroll` reuses the identity and credential saved in `stateDir` when they
are still valid for that control plane, and needs exactly one of
`bootstrapTokenPath`, `bootstrapToken` or `jwt` otherwise. Read tokens from
a file or the environment; do not put them on a command line.

A plaintext `http://` control plane is accepted only on loopback. Pass
`allowInsecure: true` for a network you trust.

## License

Apache-2.0. Issues and contributions at
[github.com/google/sam](https://github.com/google/sam).
