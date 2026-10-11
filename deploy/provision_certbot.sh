#!/usr/bin/env bash
# =============================================================================
# deploy/provision_certbot.sh — Automated TLS provisioning via Let's Encrypt
# =============================================================================
#
# Usage:
#   sudo ./provision_certbot.sh <domain> <email>
#
# Examples:
#   sudo ./provision_certbot.sh candidate-asciicker.rikiworld.com info@yuuz.co
#   sudo ./provision_certbot.sh current.rikiworld.com info@yuuz.co
#
# Prerequisites:
#   - Ubuntu/Debian VPS with apt-get
#   - nginx installed and running with a server block for <domain>
#   - DNS A record for <domain> pointing to this machine's public IP
#   - Port 80 open (for HTTP-01 ACME challenge)
#   - Port 443 open (for HTTPS after issuance)
#   - Must be run as root (sudo)
#
# What this script does:
#   1. Validates DNS resolution points to this machine
#   2. Installs certbot + nginx plugin if not present
#   3. Obtains a Let's Encrypt certificate via --nginx plugin (HTTP-01 challenge)
#   4. Enables the certbot auto-renewal systemd timer
#   5. Verifies HTTPS is active on the domain
#
# RQ-099 (canon §2 VPS Lifecycle / TLS Cert Management)
# Closes FL-2435
# =============================================================================

set -euo pipefail

# ── Argument validation ──────────────────────────────────────────────────────

if [[ $# -lt 2 ]]; then
    echo "Usage: sudo $0 <domain> <email>"
    echo "  domain  — FQDN with DNS A record pointing to this VPS"
    echo "  email   — contact email for Let's Encrypt account & expiry notices"
    exit 1
fi

DOMAIN="$1"
EMAIL="$2"

# ── Root check ───────────────────────────────────────────────────────────────

if [[ $EUID -ne 0 ]]; then
    echo "ERROR: This script must be run as root (sudo)."
    exit 1
fi

# ── DNS pre-flight ───────────────────────────────────────────────────────────
# certbot HTTP-01 challenge will fail if the domain does not resolve to this
# machine. Check early and give the operator a clear message.

echo "==> Checking DNS resolution for ${DOMAIN}..."

DNS_IP=$(dig +short "$DOMAIN" A 2>/dev/null | head -1 || true)

if [[ -z "$DNS_IP" ]]; then
    echo "ERROR: DNS lookup for '${DOMAIN}' returned no A record."
    echo ""
    echo "  certbot requires the domain to resolve to this machine so it can"
    echo "  complete the HTTP-01 ACME challenge on port 80."
    echo ""
    echo "  Action: create an A record pointing ${DOMAIN} to this VPS's"
    echo "  public IP, wait for propagation, then re-run this script."
    exit 1
fi

# Get our own public IP for comparison
MY_IP=$(curl -s --max-time 5 https://api.ipify.org 2>/dev/null || \
        curl -s --max-time 5 https://ifconfig.me 2>/dev/null || true)

if [[ -n "$MY_IP" && "$DNS_IP" != "$MY_IP" ]]; then
    echo "WARNING: DNS for ${DOMAIN} resolves to ${DNS_IP},"
    echo "         but this machine's public IP appears to be ${MY_IP}."
    echo ""
    echo "  If the DNS record is stale or points elsewhere, the ACME HTTP-01"
    echo "  challenge will fail because Let's Encrypt will reach the wrong host."
    echo ""
    read -r -p "  Continue anyway? [y/N] " REPLY
    if [[ ! "$REPLY" =~ ^[Yy]$ ]]; then
        echo "Aborted. Fix DNS and re-run."
        exit 1
    fi
else
    echo "  DNS resolves ${DOMAIN} -> ${DNS_IP} (matches this machine). OK."
fi

# ── nginx check ──────────────────────────────────────────────────────────────

echo "==> Verifying nginx is installed and running..."

if ! command -v nginx &>/dev/null; then
    echo "ERROR: nginx is not installed. Install nginx first:"
    echo "  apt-get update && apt-get install -y nginx"
    exit 1
fi

if ! systemctl is-active --quiet nginx; then
    echo "WARNING: nginx is installed but not running. Starting it..."
    systemctl start nginx
fi

# Verify there is a server block for this domain
if ! nginx -T 2>/dev/null | grep -q "server_name.*${DOMAIN}"; then
    echo "ERROR: No nginx server block found for '${DOMAIN}'."
    echo "  Deploy the nginx config first (deploy/nginx/asciicker-*-host.conf)"
    echo "  then re-run this script."
    exit 1
fi

# ── Install certbot ──────────────────────────────────────────────────────────

echo "==> Checking certbot installation..."

if command -v certbot &>/dev/null; then
    echo "  certbot already installed: $(certbot --version 2>&1)"
else
    echo "  Installing certbot and nginx plugin..."
    apt-get update -qq
    apt-get install -y certbot python3-certbot-nginx
    echo "  Installed: $(certbot --version 2>&1)"
fi

# ── Obtain certificate ───────────────────────────────────────────────────────

echo "==> Requesting Let's Encrypt certificate for ${DOMAIN}..."

# --nginx:            use the nginx plugin (modifies server block in-place)
# --non-interactive:  no TTY prompts — fail rather than block
# --agree-tos:        accept the ACME ToS
# --redirect:         add HTTP->HTTPS redirect
# --email:            account recovery + expiry warnings
# --domain:           the FQDN to certify

certbot --nginx \
    --non-interactive \
    --agree-tos \
    --redirect \
    --email "$EMAIL" \
    --domain "$DOMAIN"

echo "  Certificate issued successfully."

# ── Enable auto-renewal timer ────────────────────────────────────────────────

echo "==> Enabling certbot auto-renewal timer..."

systemctl enable --now certbot.timer

# Verify the timer is active
if systemctl is-active --quiet certbot.timer; then
    echo "  certbot.timer is active. Certs will renew automatically."
else
    echo "WARNING: certbot.timer did not start. Check: systemctl status certbot.timer"
fi

# ── Verify TLS ───────────────────────────────────────────────────────────────

echo "==> Verifying HTTPS is active on ${DOMAIN}..."

# Give nginx a moment to reload after certbot modifies the config
sleep 2

HTTP_STATUS=$(curl -sI --max-time 10 "https://${DOMAIN}" 2>/dev/null | head -1 || true)

if [[ -z "$HTTP_STATUS" ]]; then
    echo "WARNING: Could not reach https://${DOMAIN}"
    echo "  This may be a firewall issue (port 443 must be open) or DNS propagation delay."
    echo "  Manual check: curl -I https://${DOMAIN}"
    exit 1
fi

echo "  Response: ${HTTP_STATUS}"

if echo "$HTTP_STATUS" | grep -qE "^HTTP/[12].* [23]"; then
    echo ""
    echo "=== TLS provisioning complete ==="
    echo "  Domain:   https://${DOMAIN}"
    echo "  Renewal:  automatic via certbot.timer"
    echo "  Verify:   sudo certbot certificates"
    echo ""
else
    echo "WARNING: HTTPS responded but with unexpected status: ${HTTP_STATUS}"
    echo "  Investigate: curl -vI https://${DOMAIN}"
fi
