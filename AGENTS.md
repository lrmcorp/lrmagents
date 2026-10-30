# Working rules

- The owner wants every change live: after validating a change, commit it and push to `origin/main`. That push runs `.github/workflows/deploy-cowagent.yml`, which rebuilds the image and redeploys CowAgent to the Tencent VPS (`43.152.240.47`).
- Do not claim a deploy succeeded until the workflow run (or a check of `https://agent.lnint.xyz`) confirms it.
- The console must stay English only and branded LRM Agents / Liando Reyfort Management. Brand edits live in `deploy/brand/`; check them with a local `docker compose -f deploy/compose.yaml build` before pushing, because `apply.py` fails the build when upstream markup moves.
- Never commit secrets. The console password and model keys come from GitHub Actions secrets.
