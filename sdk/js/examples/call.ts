// Finds a service on the mesh and calls it: a tool of an MCP service, or a
// path of an inference or A2A service.
//
//   node call.js mcp://greeter greet '{"name": "Ada"}'
//   node call.js a2a://greeter /card
//   node call.js inference://ollama /v1/models
//
// SAM_CONTROL_PLANE_URL names the mesh. SAM_BOOTSTRAP_TOKEN_PATH is the file
// holding the token the mesh operator gave you; the first run spends it and
// keeps the identity and credential in SAM_STATE_DIR, later runs resume from
// there without it.
import { homedir } from "node:os";
import { AgentMesh } from "@sam-mesh/sdk";

const [service = "mcp://greeter", toolOrPath = "greet", args = '{"name": "world"}'] = process.argv.slice(2);

const mesh = await AgentMesh.enroll({
  controlPlaneUrl: process.env.SAM_CONTROL_PLANE_URL ?? "https://mesh.example.com",
  bootstrapTokenPath: process.env.SAM_BOOTSTRAP_TOKEN_PATH,
  stateDir: process.env.SAM_STATE_DIR ?? `${homedir()}/.config/sam-mesh/caller`,
});
const session = await mesh.join();
console.log(`on the mesh as ${session.peerId}`);

const [provider] = await session.discover(service);
if (provider === undefined) {
  throw new Error(`no member of the mesh serves ${service}`);
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
