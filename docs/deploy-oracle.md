# Deploying on Oracle Cloud (Always Free)

One free ARM server runs the whole system exactly as on a development PC: PostgreSQL, the backend, the portal,
n8n (with its task runner), self-hosted Langfuse, and an HTTPS proxy (Caddy) in front.

```
Internet ──HTTPS──> Caddy ──> portal   (careers page, candidate + staff pages)   https://<portal domain>
                         ├──> backend  (API used by the portal)                   https://<api domain>
                         └──> n8n      (webhooks only; the application form)      https://<n8n domain>/webhook/...
Your PC  ──SSH tunnel──> n8n editor :5678 · Langfuse :3000 · database :5433       (never public)
```

Time: about one hour, most of it waiting for builds. Cost: $0 within Oracle's Always Free limits.

## 1. Oracle account and server

1. Sign up at **oracle.com/cloud/free** → *Start for free*. A card is needed to verify your identity; Always Free
   resources are not charged. Choose the **home region** carefully, it cannot be changed: one close to your users
   (for Pakistan: India West (Mumbai), India South (Hyderabad), UAE East (Dubai) or Singapore).
2. Console menu → **Compute → Instances → Create instance**:
   * **Name**: `novatech`
   * **Image**: *Change image* → **Canonical Ubuntu 24.04**
   * **Shape**: *Change shape* → **Ampere** → `VM.Standard.A1.Flex` → **2 OCPUs, 12 GB memory**
     (if it says "out of capacity", try another availability domain or try again later)
   * **Networking**: create a new virtual cloud network with a **public subnet**; *Assign a public IPv4 address*: yes
   * **SSH keys**: *Generate a key pair for me* → **Download private key** (keep it safe; it is the only way in)
   * **Boot volume**: set the size to **100 GB** (the free allowance is 200 GB in total)
   * **Create**, wait until the state is *Running*, and note the **Public IPv4 address**.
3. Open HTTPS in Oracle's firewall: on the instance page → **Primary VNIC → Subnet → Security** (or *Security
   Lists*) → *Default Security List* → **Add Ingress Rules**, twice:
   * Source CIDR `0.0.0.0/0`, IP protocol **TCP**, destination port **80**
   * Source CIDR `0.0.0.0/0`, IP protocol **TCP**, destination port **443**

   Do not open any other port (22 for SSH is open already).

**Keep the server from being reclaimed:** Oracle may reclaim an Always Free server that stays almost idle for 7 days
(CPU, network *and* memory all under 20%). The full stack with Langfuse uses more than 20% of the memory. To remove
the risk completely, upgrade the account to *Pay As You Go* (Billing → Upgrade); you still pay nothing while you stay
within the Always Free limits, and the A1 allowance goes back to 4 OCPUs / 24 GB.

## 2. Domains (free)

1. Sign in at **duckdns.org** (with Google or GitHub).
2. Create three subdomains and set each one's **current ip** to the server's public IP, for example:
   * `novatech-careers` → the portal
   * `novatech-api` → the API
   * `novatech-n8n` → the n8n webhooks
3. Check: `nslookup novatech-careers.duckdns.org` returns the server's IP.

Your own domain works the same way: three `A` records pointing to the server's IP.

## 3. Connect to the server (from your PC)

In PowerShell, move the downloaded key to `C:\Users\<you>\.ssh\oracle-novatech.key` and restrict it (OpenSSH
refuses keys other users can read):

```powershell
icacls "$env:USERPROFILE\.ssh\oracle-novatech.key" /inheritance:r
icacls "$env:USERPROFILE\.ssh\oracle-novatech.key" /grant:r "$($env:USERNAME):(R)"
ssh -i "$env:USERPROFILE\.ssh\oracle-novatech.key" ubuntu@<server IP>
```

## 4. Install (on the server)

```bash
git clone https://github.com/hassankhursheed/novatech-hiring-automation.git
cd novatech-hiring-automation
sudo sh deploy/server-setup.sh     # Docker, Node.js, firewall for 80/443, automatic security updates
exit                               # log out once so docker works without sudo, then ssh in again
cd novatech-hiring-automation
sh deploy/configure.sh             # domains, mailbox + App password, Mistral keys -> .env and config/staff.csv
sh deploy/up.sh                    # build and start everything; installs nightly backup + daily slot top-up
```

`configure.sh` asks for the three domains, an email for the certificates, the sending mailbox and its App password,
and both Mistral keys (typed hidden). It generates fresh random secrets: never copy a development `.env` to a server.

## 5. Private access: the SSH tunnel

The n8n editor, Langfuse and the database are not on the internet. Open them through the tunnel (keep this window
open while you work):

```powershell
ssh -i "$env:USERPROFILE\.ssh\oracle-novatech.key" -L 5678:localhost:5678 -L 3000:localhost:3000 -L 5434:localhost:5433 ubuntu@<server IP>
```

| On your PC | Is the server's |
|---|---|
| http://localhost:5678 | n8n editor |
| http://localhost:3000 | Langfuse (sign in with `LANGFUSE_INIT_USER_EMAIL` / `LANGFUSE_INIT_USER_PASSWORD` from the server's `.env`) |
| `localhost:5434` | business database (pgAdmin; port 5434 so it does not clash with your local copy on 5433) |

## 6. n8n

1. With the tunnel open, go to **http://localhost:5678** and create the **owner account** (a strong password; turn
   on two-factor authentication in *Settings → Personal*).
2. On the server:

   ```bash
   sh deploy/n8n-setup.sh   # creates the 4 credentials from .env, deploys and publishes all 13 workflows
   ```

## 7. Check

1. `https://<portal domain>/careers` opens with a padlock; apply with your own email: the confirmation arrives.
2. `https://<portal domain>/staff/login` → enter the HR address (your mailbox with `+hr`, see `config/staff.csv`)
   → the sign-in link arrives → the HR portal shows the application.
3. `https://<api domain>/health/ready` returns `"status":"ok"`.
4. In the n8n editor (tunnel) → *Executions*: the intake ran without errors.

## Operating it

| Task | Command (on the server, in the project folder) |
|---|---|
| Update to the latest code | `git pull && sh deploy/up.sh && sh deploy/n8n-setup.sh` |
| Status / logs | `docker compose ps` · `docker compose logs -f backend n8n caddy` |
| Change staff | edit `config/staff.csv`, then `docker compose run --rm seed` |
| Backups | nightly at 02:15 into `backups/<date>/` (14 days kept); copy them to your PC regularly: `scp -i <key> -r ubuntu@<ip>:novatech-hiring-automation/backups .` |
| Keep `.env` safe | copy the server's `.env` to a private place once; `N8N_ENCRYPTION_KEY` is needed to restore n8n |
| Clean test data | `sh scripts/purge-applications.sh` (test domains only) or `--all` |

Limits on the free setup: Mistral free key about 30 AI requests per minute; Gmail about 500 (Workspace: 2,000)
recipients per day. Interview slots are generated from each interviewer's weekly hours whenever an invitation is
sent (and topped up daily for the next 15 days).
