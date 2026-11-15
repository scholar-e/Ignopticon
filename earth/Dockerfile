# Ignopticon web app: the earth globe (/) and the ops page (/ops/), served by dev-server.js.
# On EC2 it runs behind Caddy (HTTPS) via deploy/ec2/docker-compose.yml; see docs/DEPLOY.md.
# Locally: docker build -t ignopticon . && docker run -p 8080:8080 ignopticon
FROM node:22-slim

ENV NODE_ENV=production \
    PORT=8080 \
    TILE_CACHE=/data/tile-cache \
    WARM_MAX_JOBS=6

WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --omit=dev && npm cache clean --force

COPY dev-server.js ./
COPY public ./public

# The tile cache is the only thing written at runtime; mount a volume at /data to keep it across deploys.
# Runs as root because Docker volumes mount root-owned.
RUN mkdir -p /data/tile-cache

EXPOSE 8080
CMD ["node", "dev-server.js"]
