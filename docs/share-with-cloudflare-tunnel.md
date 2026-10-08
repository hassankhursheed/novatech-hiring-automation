# Sharing the system from your PC (Cloudflare Tunnel)

A free Cloudflare quick tunnel gives the system running on your PC a public `https://` address, with no account,
no domain, no card and no router changes (the tunnel connects outward to Cloudflare).

```
Testers ──HTTPS──> Cloudflare ──tunnel──> your PC: edge proxy ──> portal  (careers, candidate + staff pages)
                                                             ├──> backend (/v1/*, /health/*)
                                                             └──> n8n     (/webhook/* only: the application form)
Only on your PC: n8n editor http://localhost:5678 · Langfuse http://localhost:3000 · database localhost:5433
```

**Good to know**
* Your PC must be on, awake and running Docker while people test. Set Windows *Sleep* to *Never* while sharing.
* The address is random (`https://<words>.trycloudflare.com`) and changes whenever the tunnel restarts (PC restart,
  Docker restart). New emails always use the current address (the `tunnel-url` helper updates it within 15 seconds);
  links in emails sent before a change stop working. For a permanent address, use a server
  ([deploy-oracle.md](deploy-oracle.md)) or a named Cloudflare Tunnel with your own domain.
* If your internet drops for a while, Cloudflare deletes the quick tunnel. The `tunnel` service notices (no
  connection to Cloudflare for 2 minutes, see `deploy/tunnel/watchdog.sh`), restarts itself and gets a new address,
  so the system comes back on its own once the connection is back; check the new link with
  `docker compose logs tunnel-url`.
* Cloudflare describes quick tunnels as a testing tool: no uptime guarantee, up to 200 requests at the same moment.

## Turn it on

In `.env` (Windows separates the files with `;`, Linux/macOS with `:`):

```
COMPOSE_FILE=docker-compose.yml;deploy/docker-compose.tunnel.yml
```

Then:

```bash
docker compose up -d --build
docker compose logs tunnel-url
```

The last line shows the public link, e.g. `public link: https://officer-gui-welfare-san.trycloudflare.com`. Share
`<link>/careers` with applicants and `<link>/staff/login` with staff testers.

If the tunnel log shows `failed to request quick Tunnel ... context deadline exceeded`, Cloudflare was slow to
answer; the tunnel retries on its own and usually connects within a minute or two.

## Everyday

| Task | Command |
|---|---|
| Show the current public link | `docker compose logs tunnel-url` |
| Pause sharing (the system keeps running on your PC) | `docker compose stop tunnel` |
| Share again (new link; emails follow automatically) | `docker compose start tunnel` |
| Use it yourself on the PC | the public link, or http://localhost:8080 (same single address, locally) |
| Turn tunnel mode off | remove the `COMPOSE_FILE` line from `.env`, then `docker compose up -d --build --remove-orphans` |

In tunnel mode the portal calls the API on its own address, so open it through the public link or
http://localhost:8080; `http://localhost:5173` (the portal alone, without the API behind it) does not load data.

## What testers can do

* **Applicants:** open `<link>/careers` and apply with their real email; they receive the confirmation and every
  later step by email (sent from your mailbox).
* **Staff:** sign in at `<link>/staff/login` with an address from `config/staff.csv`; the one-time sign-in link
  arrives by email. To give a tester their own staff account, add them to `config/staff.csv` and run
  `docker compose up -d seed`.
