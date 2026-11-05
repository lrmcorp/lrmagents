# Working rules

- The owner wants every change live: after validating a change, commit it and push to `origin/main`. That push runs `.github/workflows/deploy-cowagent.yml`, which rebuilds the image and redeploys LRMagent to the Tencent VPS (`43.152.240.47`).
- Do not claim a deploy succeeded until the workflow run (or a check of `https://agent.lnint.xyz`) confirms it.
- The console must stay English only and branded LRMagent / Liando Reyfort Management. Brand edits live in `deploy/brand/`; check them with a local `docker compose -f deploy/compose.yaml build` before pushing, because `apply.py` fails the build when upstream markup moves.
- Never commit secrets. The console password and model keys come from GitHub Actions secrets.
- Call the product **LRMagent** in UI, docs and messages. It is built on the open-source CowAgent, but that name stays only where code must match upstream.
- Reviews follow the antislop skills in `.claude/skills/` (antislop, -code, -ui, -human, -layoutmobile, -copywriting): read them before reviewing or building UI or code.
- End-to-end tests live in `e2e/` (the `e2e` framework, no AI model needed): run the console image on 9899 and the gateway on 8181, then `cd e2e && npx e2e run` and `npx e2e run --config e2e.gateway.config.ts` (desktop and phone sizes; they fail on page errors and sideways scrolling). Needs Node 22.22.3+ or 24; `CDP_URL` attaches to an existing Chrome where Playwright cannot download one.
