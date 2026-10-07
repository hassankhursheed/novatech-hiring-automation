#!/bin/sh
# One-time preparation of a fresh Ubuntu 24.04 server (Oracle Cloud Always Free ARM, or any x86/ARM VM):
#   Docker Engine + Compose, Node.js (builds the n8n workflows), firewall openings for HTTPS, automatic security updates.
#
#   sudo sh deploy/server-setup.sh
#
# Afterwards log out and back in once, so your user can run docker without sudo.
set -eu

[ "$(id -u)" -eq 0 ] || { echo "Run with sudo: sudo sh deploy/server-setup.sh" >&2; exit 1; }
user="${SUDO_USER:-ubuntu}"
export DEBIAN_FRONTEND=noninteractive

echo "== Packages"
apt-get update -q
apt-get install -y -q ca-certificates curl gnupg git nodejs python3 unattended-upgrades iptables-persistent

echo "== Docker Engine (official repository)"
if ! command -v docker >/dev/null 2>&1; then
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update -q
  apt-get install -y -q docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi
systemctl enable --now docker
usermod -aG docker "$user"

echo "== Firewall: allow HTTP/HTTPS (Oracle's Ubuntu image rejects everything except SSH by default)"
for port in 80 443; do
  if ! iptables -C INPUT -p tcp --dport "$port" -m state --state NEW -j ACCEPT 2>/dev/null; then
    reject=$(iptables -L INPUT --line-numbers -n | awk '/REJECT/ {print $1; exit}')
    if [ -n "$reject" ]; then
      iptables -I INPUT "$reject" -p tcp --dport "$port" -m state --state NEW -j ACCEPT
    else
      iptables -A INPUT -p tcp --dport "$port" -m state --state NEW -j ACCEPT
    fi
  fi
done
netfilter-persistent save >/dev/null

echo "== Automatic security updates"
dpkg-reconfigure -f noninteractive unattended-upgrades

echo
echo "Done: $(docker --version), $(docker compose version), Node $(node --version)."
echo "Log out and back in once (so '$user' can use docker), then run: sh deploy/configure.sh"
