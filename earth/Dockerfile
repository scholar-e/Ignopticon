# Ignopticon web app: the earth globe (/) and the ops page (/ops/), served by dev-server.js.
# On EC2 it runs behind Caddy (HTTPS) via deploy/ec2/docker-compose.yml; see docs/DEPLOY.md.
# Locally: docker build -t ignopticon . && docker run -p 8080:8080 ignopticon

# SPLAT! 1.4.2 (GPL v2, https://www.qsl.net/kd2bd/splat.html; source vendored in vendor/) for radio coverage in
# navigation (coverage.js). Standard 3 arc-second mode, up to 4x4 one-degree terrain tiles per run. bzip2 and the
# C++ runtime are linked statically so the runtime image needs nothing extra.
FROM node:22-slim AS splat
RUN apt-get update && apt-get install -y --no-install-recommends g++ libbz2-dev bzip2 && rm -rf /var/lib/apt/lists/*
WORKDIR /src
COPY vendor/splat-1.4.2.tar.bz2 ./
RUN tar xjf splat-1.4.2.tar.bz2 --exclude=splat-1.4.2/README2 && cd splat-1.4.2 \
 && printf '#define MAXPAGES 16\n#define HD_MODE 0\n' > splat.h \
 && g++ -O2 -s -ffast-math itwom3.0.cpp splat.cpp -static-libstdc++ -static-libgcc -Wl,-Bstatic -lbz2 -Wl,-Bdynamic -lm -o /usr/local/bin/splat \
 && /usr/local/bin/splat | head -2

FROM node:22-slim

ENV NODE_ENV=production \
    PORT=8080 \
    TILE_CACHE=/data/tile-cache \
    WARM_MAX_JOBS=6 \
    SPLAT_BIN=/usr/local/bin/splat

WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --omit=dev && npm cache clean --force

COPY --from=splat /usr/local/bin/splat /usr/local/bin/splat
COPY dev-server.js coverage.js history.js longroute.js ./
COPY public ./public

# The tile cache (also SPLAT! terrain tiles and coverage results) is the only thing written at runtime; mount a
# volume at /data to keep it across deploys. Runs as root because Docker volumes mount root-owned.
RUN mkdir -p /data/tile-cache

EXPOSE 8080
CMD ["node", "dev-server.js"]
