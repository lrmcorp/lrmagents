"""Where a console request really comes from, for the owner's decisions.

Agents run in the same container as the console and can read its password and API
token, so a valid session or token alone does not prove the owner. Decisions (approvals,
the approval mode, vault updates) are accepted only:
- from the owner's browser: through Caddy (by its address on the shared network, which
  nothing in this container can fake) from a public client that is not this server, or
- from the WhatsApp gateway container (the owner's yes / no replies), by its address.
Without SERVER_PUBLIC_IP (a local test, no Caddy) browser requests are allowed; on the server a
Caddy that cannot be looked up means refused.
Installed as agent/lrm_origin.py.
"""
import ipaddress
import os
import re
import socket
import time

from common.log import logger

_cache = {}


def _addresses(host):
    hit = _cache.get(host)
    if hit and time.time() - hit[0] < (600 if hit[1] else 30):
        return hit[1]
    try:
        ips = {info[4][0] for info in socket.getaddrinfo(host, None)}
    except OSError:
        ips = None
    _cache[host] = (time.time(), ips)
    return ips


def _is(peer, host):
    """peer is the container named host: by its addresses, or by Docker's reverse name
    (<project>-<service>-1.<network>), since a container on two networks has two addresses."""
    ips = _addresses(host)
    if ips and peer in ips:
        return True
    key = "ptr:" + peer
    hit = _cache.get(key)
    if not hit or time.time() - hit[0] > 600:
        try:
            name = socket.gethostbyaddr(peer)[0]
        except (OSError, UnicodeError):
            name = ""
        hit = _cache[key] = (time.time(), name)
    return bool(re.match(rf"^(?:[\w.-]*[-_])?{re.escape(host)}(?:[-_]\d+)?(?:\.|$)", hit[1]))


def _public(ip):
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return not (a.is_private or a.is_loopback or a.is_link_local or a.is_reserved or a.is_unspecified)


def _server_ips():
    return {x.strip() for x in os.environ.get("SERVER_PUBLIC_IP", "").split(",") if x.strip()}


def owner_browser(env):
    """True when the request came through Caddy from a real, outside browser."""
    peer = (env.get("REMOTE_ADDR") or "").removeprefix("::ffff:")
    client = (env.get("HTTP_X_FORWARDED_FOR") or "").split(",")[0].strip().removeprefix("::ffff:")
    host = os.environ.get("CADDY_HOST", "caddy")
    if _addresses(host) is None and not _server_ips():
        return True
    via_caddy = _is(peer, host)
    ok = via_caddy and _public(client) and client not in _server_ips()
    if not ok:
        logger.warning(f"[Origin] owner decision refused: peer {'Caddy' if via_caddy else 'not Caddy'}, "
                       f"client {'public' if _public(client) else 'private or none'}")
    return ok


def from_gateway(env):
    """True when the request came from the WhatsApp gateway container."""
    peer = (env.get("REMOTE_ADDR") or "").removeprefix("::ffff:")
    return bool(peer) and _is(peer, os.environ.get("WHATSAPP_GATEWAY_HOST", "whatsapp-gateway"))


REFUSED = "Decisions are taken only in the owner's own browser or by the owner's WhatsApp reply."


def describe():
    """What the checks see on this server, for the log at startup."""
    return {h: sorted(_addresses(h) or []) for h in (os.environ.get("CADDY_HOST", "caddy"),
                                                       os.environ.get("WHATSAPP_GATEWAY_HOST", "whatsapp-gateway"))}
