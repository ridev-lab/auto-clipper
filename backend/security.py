"""Protection de l'API locale contre les sites web tiers.

Le serveur n'écoute que sur 127.0.0.1, mais n'importe quelle page ouverte
dans le navigateur peut tenter de lui envoyer des requêtes :
- requêtes « cross-site » (CSRF) : une page externe qui POSTe vers
  http://localhost:8000 → refusées grâce aux en-têtes Origin / Sec-Fetch-Site
  et au type de contenu ;
- « DNS rebinding » : un domaine externe qui pointe vers 127.0.0.1 pour lire
  les réponses → refusé grâce à la vérification de l'en-tête Host.
S'y ajoutent des en-têtes de sécurité (CSP, anti-iframe, nosniff).
"""
from __future__ import annotations

from urllib.parse import urlsplit

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from clipper.config import get_config

LOOPBACK = {"localhost", "127.0.0.1", "::1"}
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
BODY_TYPES = {"application/json", "application/octet-stream"}

CSP = "; ".join([
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self'",
    "img-src 'self' data: blob: https://*.ytimg.com https://*.ggpht.com",
    "media-src 'self' blob:",
    "font-src 'self'",
    "connect-src 'self'",
    "object-src 'none'",
    "base-uri 'none'",
    "form-action 'self'",
    "frame-ancestors 'none'",
])

SECURITY_HEADERS = [
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
    (b"cross-origin-opener-policy", b"same-origin"),
    (b"cross-origin-resource-policy", b"same-origin"),
    (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
    (b"content-security-policy", CSP.encode()),
]


def allowed_hosts() -> set[str]:
    srv = get_config()["server"]
    hosts = set(LOOPBACK)
    extra = srv.get("allowed_hosts") or []
    hosts.update(str(h).lower() for h in extra)
    host = str(srv.get("host", "127.0.0.1")).lower()
    if host not in ("0.0.0.0", "::"):
        hosts.add(host)
    return hosts


def _hostname(value: str) -> str:
    """Nom d'hôte sans le port (gère [::1]:8000)."""
    value = value.strip().lower()
    if value.startswith("["):
        return value[1 : value.find("]")] if "]" in value else value
    return value.rsplit(":", 1)[0] if value.count(":") == 1 else value


class LocalOnlyMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app
        self.hosts = allowed_hosts()

    async def _reject(self, send: Send, status: int, msg: str) -> None:
        body = ('{"detail": "%s"}' % msg).encode()
        await send({
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
        })
        await send({"type": "http.response.body", "body": body})

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}

        # 1. DNS rebinding : seul un Host local est accepté
        if _hostname(headers.get("host", "")) not in self.hosts:
            await self._reject(send, 400, "Hote non autorise")
            return

        # 2. CSRF : les requêtes qui modifient quelque chose doivent venir de l'interface
        if scope["method"] not in SAFE_METHODS:
            origin = headers.get("origin")
            if origin and _hostname(urlsplit(origin).netloc) not in self.hosts:
                await self._reject(send, 403, "Origine refusee")
                return
            if headers.get("sec-fetch-site") == "cross-site":
                await self._reject(send, 403, "Requete inter-sites refusee")
                return
            ctype = headers.get("content-type", "").split(";")[0].strip().lower()
            if ctype and ctype not in BODY_TYPES:
                await self._reject(send, 415, "Type de contenu refuse")
                return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                existing = {k.lower() for k, _ in message.get("headers", [])}
                message["headers"] = list(message.get("headers", [])) + [
                    (k, v) for k, v in SECURITY_HEADERS if k not in existing
                ]
            await send(message)

        await self.app(scope, receive, send_with_headers)
