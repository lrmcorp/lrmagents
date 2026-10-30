# lrmagents

Deploys [CowAgent](https://github.com/zhayujie/CowAgent) (web console, port 9899) to the same Tencent Lighthouse VPS as the `ln` demo (`43.152.240.47`, `int.lnint.xyz`).

- Public URL: `https://agent.lnint.xyz`. TLS terminates in the existing `ln` Caddy container, which reaches CowAgent over the shared `lnint-edge` Docker network.
- The CowAgent container is not on the `ln` network, so it cannot reach the `ln` PostgreSQL database.
- Workspace (skills, memory) and data (`config.json`, logs) live in Docker volumes `cowagent_workspace` and `cowagent_data`. They survive redeploys.

## One-time setup

1. **DNS (DNSPod, `lnint.xyz`):** add an `A` record with host `agent` and value `43.152.240.47`. Do not add an `AAAA` record.
2. **`ln` repo:** deploy the `ln` change that adds the `agent.lnint.xyz` site to `deploy/Caddyfile.domain` and joins Caddy to `lnint-edge`.
3. **This repo → Settings → Secrets and variables → Actions:**
   - Secret `VPS_SSH_KEY`: the same Tencent private key used by `lrmcorp/ln`.
   - Secret `COWAGENT_WEB_PASSWORD`: the web console password, 12 or more characters, including at least one letter (an all-digit value is parsed as a number). It is required because the agent can run shell commands inside its container.
   - Optional secrets for model keys: `CLAUDE_API_KEY`, `OPEN_AI_API_KEY`, `DEEPSEEK_API_KEY`, `GEMINI_API_KEY`. You can also set keys later in the web console.
   - Optional variable `COWAGENT_MODEL`, for example `claude-sonnet-4-5`.
   - Variable `VPS_DEPLOY_ENABLED=true`.
4. Run **Actions → Deploy CowAgent to Tencent Lighthouse → Run workflow**. Every push to `main` redeploys.

## Access without the domain

```bash
ssh -i <tencent-key.pem> -L 9899:127.0.0.1:9899 ubuntu@43.152.240.47
```

Then open http://localhost:9899/.

## Operations (on the VPS)

```bash
cd ~/cowagent && sudo docker compose -f deploy/compose.yaml logs -f --tail 100
```

The image tracks `zhayujie/chatgpt-on-wechat:latest`; each deploy pulls the newest version. To pin a version, create `~/cowagent/deploy/.env` on the VPS containing `COWAGENT_IMAGE=zhayujie/chatgpt-on-wechat:<tag>`. Deploys keep that file.
