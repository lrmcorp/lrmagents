# lrmagents

Deploys [CowAgent](https://github.com/zhayujie/CowAgent) (web console, port 9899) to the same Tencent Lighthouse VPS as the `ln` demo (`43.152.240.47`, `int.lnint.xyz`).

- Public URL: `https://agent.lnint.xyz` now; `https://lrmcorporation.id` once that domain is switched on (see below). TLS terminates in the existing `ln` Caddy container, which reaches CowAgent over the shared `lnint-edge` Docker network.
- The CowAgent container is not on the `ln` network, so it cannot reach the `ln` PostgreSQL database.
- Workspace (skills, memory) and data (`config.json`, logs) live in Docker volumes `cowagent_workspace` and `cowagent_data`. They survive redeploys.

## LRM brand and look

The console is built from `deploy/Dockerfile`, a thin layer over the upstream CowAgent image. It is branded **LRM Agents / Liando Reyfort Management** and follows the LRMsmm look:
- **Logo:** the wordmark appears on the login and welcome screens, and the RL mark is the favicon, the sidebar icon and the agent avatar.
- **Look:** a white sidebar with an emerald active item, a `#f1f2f1` canvas, white cards, `#015f44` primary buttons, and Plus Jakarta Sans.
- **Language:** English only, with no language switcher. `COW_LANG=en` makes the agent work in English too.
- **Name:** the agent calls itself LRM Agents. Upstream docs, website and GitHub links are hidden.

It opens in light mode. Viewers can switch to the matching dark mode, which uses a dark sidebar.

![LRM Agents console](docs/lrm-console.png)

- `deploy/brand/apply.py`: the build-time edits. Each must match upstream, so an upstream change fails the deploy rather than shipping CowAgent branding.
- `deploy/brand/theme.json`: light and dark palettes, added as the built-in **LRM** color theme.
- `deploy/brand/brand.css`: the light sidebar, logo sizing, hidden links, corners and focus ring.
- `deploy/brand/img/`: the logo, wordmarks and favicon, generated from the supplied LRM artwork.
- `deploy/brand/fonts/`: Plus Jakarta Sans under the SIL Open Font License.

## One-time setup

1. **DNS (DNSPod, `lnint.xyz`):** add an `A` record with host `agent` and value `43.152.240.47`. Do not add an `AAAA` record.
2. **`ln` repo:** deploy the `ln` change that adds the `agent.lnint.xyz` site to `deploy/Caddyfile.domain` and joins Caddy to `lnint-edge`.
3. **This repo → Settings → Secrets and variables → Actions:**
   - Secret `VPS_SSH_KEY`: the same Tencent private key used by `lrmcorp/ln`.
   - Secret `COWAGENT_WEB_PASSWORD`: the web console password, 12 or more characters, including at least one letter (an all-digit value is parsed as a number). It is required because the agent can run shell commands inside its container.
   - Optional secrets for model keys: `CLAUDE_API_KEY`, `OPEN_AI_API_KEY`, `DEEPSEEK_API_KEY`, `GEMINI_API_KEY`. You can also set keys later in the web console.
   - Optional variable `COWAGENT_MODEL`, for example `claude-sonnet-4-5`.
   - Variable `VPS_DEPLOY_ENABLED=true`.
4. Run **Actions → Deploy CowAgent to Tencent Lighthouse → Run workflow**. Every push to `main` rebuilds and redeploys automatically.

## Move the console to lrmcorporation.id

1. At the DNS provider for `lrmcorporation.id`, add `A` records for `@` and `www`, both pointing to `43.152.240.47`. Remove any `AAAA` records for those names.
2. In **both** `lrmcorp/ln` and `lrmcorp/lrmagents`, add the repository variable `AGENT_DOMAIN=lrmcorporation.id,www.lrmcorporation.id`.
3. Re-run the `ln` deploy workflow, which makes Caddy request certificates for the new hosts. Then re-run this repository's workflow, which updates the console's public URL and checks HTTPS.

`agent.lnint.xyz` stops being served after the switch. Delete the variable to go back to it.

## WhatsApp

`whatsapp-bridge/` connects a WhatsApp Business number through Meta's official **WhatsApp Cloud API**:
- Meta delivers each message to `https://agent.lnint.xyz/whatsapp/webhook`, and the bridge checks Meta's signature on it.
- The bridge passes the text to CowAgent's OpenAI-compatible API. Each phone number gets its own conversation, so the agent remembers context.
- The reply goes back through the Cloud API, split into parts of up to 4,000 characters, with read receipts and a typing indicator while the agent works.
- **Only allowlisted numbers get answered.** The agent can run commands, so strangers are ignored. Non-text messages get a short explanation.

The bridge reaches CowAgent with a random API token generated once on the VPS (`~/cowagent/.env.api-token`). CowAgent's API stays switched off until WhatsApp is configured.

### Connect your number

1. At [developers.facebook.com](https://developers.facebook.com), create an app of type **Business** and add the **WhatsApp** product. Add your business phone number in **WhatsApp → API Setup**, or test with Meta's test number first.
2. Create a permanent access token. In Business Settings → **System users**, add a system user, give it the app and your WhatsApp account, and generate a token with `whatsapp_business_messaging` and `whatsapp_business_management`. The 24-hour token on the API Setup page expires.
3. In **lrmcorp/lrmagents → Settings → Secrets and variables → Actions**, add:
   - Secret `WHATSAPP_ACCESS_TOKEN`: the token from step 2.
   - Secret `WHATSAPP_APP_SECRET`: from App settings → Basic → App secret.
   - Secret `WHATSAPP_VERIFY_TOKEN`: any long random string you choose.
   - Variable `WHATSAPP_PHONE_NUMBER_ID`: the **Phone number ID** from API Setup. This is not the phone number itself.
   - Variable `WHATSAPP_ALLOWED_NUMBERS`: the numbers that may use the agent, comma-separated with country code, for example `6281234567890,6289876543210`.
4. Run the deploy workflow. Its summary should say `WhatsApp bridge: true`.
5. In the Meta app, go to **WhatsApp → Configuration → Webhook**. Set the callback URL to `https://agent.lnint.xyz/whatsapp/webhook` and the verify token to the same value as `WHATSAPP_VERIFY_TOKEN`. Save, then subscribe to the **messages** field.
6. Message your WhatsApp Business number from an allowlisted phone.

The webhook needs the public HTTPS domain: the `agent` DNS record must exist first. Meta's rules apply. You can reply freely within 24 hours of a user's message, but messages the business starts outside that window need approved templates; the bridge only replies.

## Access without the domain

```bash
ssh -i <tencent-key.pem> -L 9899:127.0.0.1:9899 ubuntu@43.152.240.47
```

Then open http://localhost:9899/.

## Operations (on the VPS)

```bash
cd ~/cowagent && sudo docker compose -f deploy/compose.yaml logs -f --tail 100
```

The base image tracks `zhayujie/chatgpt-on-wechat:latest`; each deploy rebuilds on the newest version. To pin a version, create `~/cowagent/deploy/.env` on the VPS containing `COWAGENT_IMAGE=zhayujie/chatgpt-on-wechat:<tag>`. Deploys keep that file.
