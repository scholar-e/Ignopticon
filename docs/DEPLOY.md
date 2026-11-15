# Deploying the web app (AWS EC2)

What goes online is `earth/`: the globe at `/` and the ops page at `/ops/`, both served by `earth/dev-server.js`
(Node + Express). It is not a static site: the server answers `/api/fires*`, proxies and caches map tiles
(`/tiles/*`), and recolours the heat tiles. Everything it serves is in `earth/public/` (~140 MB); the modelling
pipeline (ELMFIRE, Python scripts, `data/`) stays on the workstation, and new forecasts or snapshots go online by
redeploying.

## How it is set up

One EC2 instance (Ubuntu 24.04, Docker) runs two containers from
[earth/deploy/ec2/docker-compose.yml](../earth/deploy/ec2/docker-compose.yml):

- **app**: [earth/Dockerfile](../earth/Dockerfile), Node 22 running `dev-server.js` on port 8080 (not exposed).
  Proxied tiles go to a Docker volume, so they survive redeploys.
- **caddy**: listens on 80/443, gets and renews a Let's Encrypt certificate, and forwards to the app. HTTPS matters:
  the ops page's offline mode (service worker) only works over it.

The site's hostname defaults to `<ip-with-dashes>.sslip.io` (a free DNS name that resolves to the IP inside it), so
no domain is needed. With your own domain, point an A record at the instance's IP and pass the domain to `deploy.sh`.

| File | Runs | Does |
| --- | --- | --- |
| [launch.sh](../earth/deploy/ec2/launch.sh) | your Mac, once | SSH key (`~/.ssh/ignopticon-ec2.pem`), security group (80/443 open, 22 from your IP only), `t4g.small` instance with a 20 GB encrypted disk, Elastic IP |
| [user-data.sh](../earth/deploy/ec2/user-data.sh) | the instance, first boot | installs Docker + compose, adds 2 GB swap |
| [deploy.sh](../earth/deploy/ec2/deploy.sh) | your Mac, every update | rsyncs `earth/` to the instance, `docker compose up -d --build`, checks the HTTPS URL |

**Cost** (us-west-2, on demand): t4g.small ~$12/month, 20 GB gp3 ~$1.60, public IPv4/Elastic IP ~$3.60: about
**$18/month** while it exists, plus data transfer out ($0.09/GB after the first 100 GB/month). A `t4g.micro`
(1 GB RAM, ~$6/month) also works for light traffic: `INSTANCE_TYPE=t4g.micro`.

## First deploy

1. Install the AWS CLI and sign in with an IAM user or SSO profile that can manage EC2 (and read SSM public
   parameters, for the Ubuntu image ID):

   ```bash
   brew install awscli
   ```

   ```bash
   aws configure
   ```

2. Create the server (prints its IP when done; region from your AWS config, else `us-west-2`):

   ```bash
   earth/deploy/ec2/launch.sh
   ```

3. Deploy the app, using the IP from step 2:

   ```bash
   earth/deploy/ec2/deploy.sh 203.0.113.10
   ```

   It prints the live URL, e.g. `https://203-0-113-10.sslip.io/` and `.../ops/`.

## Updating

After changing anything under `earth/` (new forecasts in `earth/public/data/forecasts/`, new FEDS snapshots, code),
run `deploy.sh` again with the same IP (and domain, if you use one). It deploys the files as they are on disk,
committed or not. Rebuilds take under a minute; the site is down for a few seconds while the app container
restarts.

## Deploying on git push

[.github/workflows/deploy.yml](../.github/workflows/deploy.yml) runs `deploy.sh` on GitHub's machines whenever a
push to `main` changes `earth/` (or by hand: Actions → Deploy web app → Run workflow). The server then matches what
is committed on `main`, not what is on your disk. Setup, once:

1. In the GitHub repo: **Settings → Secrets and variables → Actions**.
   - Secret `EC2_HOST`: the Elastic IP.
   - Secret `EC2_SSH_KEY`: the whole private key file, including the `BEGIN`/`END` lines. Copy it with
     `pbcopy < ~/.ssh/ignopticon-ec2.pem` and paste.
   - Variable (Variables tab, optional) `SITE_ADDRESS`: your own domain, if you use one instead of sslip.io.
2. Let GitHub's machines reach SSH: in the `ignopticon-web` security group, change the port 22 rule's source to
   **Anywhere-IPv4** (`0.0.0.0/0`). Their addresses change on every run. Ubuntu on EC2 accepts key logins only (no
   passwords), so the key is what protects the server; never commit it.

Running `deploy.sh` from your Mac still works, for trying something before committing; the next push puts the server
back to `main`.

## Operating it

```bash
ssh -i ~/.ssh/ignopticon-ec2.pem ubuntu@203.0.113.10
```

- Logs: `cd ignopticon/earth/deploy/ec2 && docker compose logs -f --tail 100`
- Restart: `docker compose restart app`
- Tile cache size: `docker system df -v | grep tiles`. It grows with every area anyone views; the disk is 20 GB.
- If port 22 is still limited to your IP, SSH stops working when your home IP changes: add the new IP to the `ignopticon-web` security group's port 22
  rule in the EC2 console.
- Stopping the instance in the console stops the compute charge (the disk and Elastic IP still cost ~$5/month).
  The containers come back by themselves on start (`restart: unless-stopped`).
- **Removing everything:** terminate the instance, release the Elastic IP, and delete the `ignopticon-web` security
  group and `ignopticon` key pair in the EC2 console. An Elastic IP left unattached is still billed.

## Server settings (environment variables)

Set in the Dockerfile; locally nothing changes (`cd earth && npm install && node dev-server.js 8080`).

| Variable | Local default | Deployed | Meaning |
| --- | --- | --- | --- |
| `PORT` | `8080` (or the first CLI argument) | `8080` | Port to listen on |
| `NODE_ENV` | unset | `production` | One-line request logs; trust the proxy for client IPs |
| `TILE_CACHE` | `earth/tile-cache` | `/data/tile-cache` | Where proxied tiles are kept (a Docker volume) |
| `WARM_MAX_JOBS` | no cap | `6` | Background tile-warming jobs per process (each fetches 1,600-8,000 tiles); `0` turns warming off |

## Before sharing the link

- **Tile licences.** Sentinel-2 cloudless (EOX) is CC BY-NC-SA 4.0: fine for the Space Apps demo, not for
  commercial use, and it needs the EOX/Copernicus credit shown on the map. USGS, NASA GIBS, LANDFIRE and AWS Terrain
  Tiles are public.
- **Open proxy.** `/tiles/*` fetches upstream on behalf of anyone, limited to the fixed sources in `TILE_SOURCES`,
  and `WARM_MAX_JOBS` caps the bulk jobs. If the link gets real traffic, put CloudFront or Cloudflare in front;
  responses already carry `Cache-Control`.
- **Forecasts are experimental.** The ops page already says "experimental, not official"; keep it that way on a
  public URL.
