#!/usr/bin/env sh
set -eu

# The published frontend is an untrusted edge by default. The launcher enables
# forwarding-chain preservation only after it has made this port loopback-only
# and placed its own header-sanitizing reverse proxy in front.
/usr/local/bin/render-forwarded-for.sh \
  /etc/nginx/omlorix-default.conf.template \
  /etc/nginx/conf.d/default.conf
