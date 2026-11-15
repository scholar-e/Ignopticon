#!/bin/bash
# EC2 first-boot script (Ubuntu 24.04): installs Docker with the compose plugin. launch.sh passes it as user data.
set -euxo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y docker.io docker-compose-v2 rsync
systemctl enable --now docker
usermod -aG docker ubuntu
# 2 GB swap: headroom for image builds on a small instance.
fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
echo '/swapfile none swap sw 0 0' >> /etc/fstab
touch /var/lib/ignopticon-ready
