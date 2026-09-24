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
