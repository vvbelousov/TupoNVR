"""Single-process sessions and authenticated, same-origin WHEP signaling."""
import hashlib
import secrets
import time
import re

import httpx
from fastapi import HTTPException, Response


class Sessions:
    lifetime = 86400

    def __init__(self):
        self.entries = {}

    @staticmethod
    def credentials(username, password):
        return hashlib.sha256((username + '\0' + password).encode()).digest()

    def valid(self, token, username, password):
        entry = self.entries.get(token)
        return bool(entry and entry[0] > time.time() and
                    secrets.compare_digest(entry[1], self.credentials(username, password)))

    def create(self, username, password):
        # Bound memory even when a client repeatedly logs in without logging out.
        self.entries = {key: entry for key, entry in self.entries.items()
                        if self.valid(key, username, password)}
        if len(self.entries) >= 4096:
            raise HTTPException(503, 'Session capacity reached')
        token = secrets.token_urlsafe(32)
        self.entries[token] = (time.time() + self.lifetime, self.credentials(username, password))
        return token

    def revoke(self, token):
        self.entries.pop(token, None)


class MediaProxy:
    def __init__(self, base_url, sessions, credentials):
        self.client = httpx.AsyncClient(base_url=base_url, timeout=15, trust_env=False)
        self.sessions = sessions
        self.credentials = credentials
        # WHEP resource paths are secrets. Never log them or forward client URLs.
        self.resources = {}
        self.ready = True

    async def delete(self, path):
        try:
            response = await self.client.delete(path)
            if response.status_code not in (200, 204, 404):
                return False
        except httpx.HTTPError:
            return False
        self.resources.pop(path, None)
        return True

    async def reap(self):
        username, password = self.credentials()
        for path, (owner, expiry) in list(self.resources.items()):
            if expiry <= time.time() or (owner and not self.sessions.valid(owner, username, password)):
                await self.delete(path)  # Retain failures and retry next maintenance tick.

    async def forward(self, request, path, owner):
        if not self.ready:
            raise HTTPException(503, 'Media gateway initializing')
        if request.method == 'POST' and (len(self.resources) >= 4096 or
                                         sum(value[0] == owner for value in self.resources.values()) >= 128):
            raise HTTPException(429, 'Media session capacity reached')
        if request.method in ('PATCH', 'DELETE'):
            resource = self.resources.get(path)
            if not resource or resource[0] != owner or resource[1] <= time.time():
                raise HTTPException(404, 'Media session not found')
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 65536:
                raise HTTPException(413, 'Signaling request too large')
        headers = {key: request.headers[key] for key in ('content-type', 'if-match') if key in request.headers}
        try:
            upstream = await self.client.request(request.method, path, content=bytes(body), headers=headers)
        except httpx.HTTPError:
            raise HTTPException(502, 'Media gateway unavailable') from None
        forwarded = {key: upstream.headers[key] for key in
                     ('content-type', 'etag', 'accept-patch', 'accept-post', 'link') if key in upstream.headers}
        if request.method == 'POST' and upstream.status_code == 201:
            username, password = self.credentials()
            location = upstream.headers.get('location', '')
            if not re.fullmatch(re.escape(path) + r'/[0-9a-fA-F-]{36}', location):
                raise HTTPException(502, 'Invalid media session response')
            expiry = self.sessions.entries[owner][0] if owner in self.sessions.entries else time.time() + self.sessions.lifetime
            self.resources[location] = (owner, expiry)
            # Login can be revoked while waiting for the upstream offer response.
            if owner and not self.sessions.valid(owner, username, password):
                await self.delete(location)
                raise HTTPException(401, 'Authentication required')
            forwarded['location'] = '/api/media' + location
        if request.method == 'DELETE' and upstream.status_code in (200, 204, 404):
            self.resources.pop(path, None)
        forwarded['cache-control'] = 'no-store'
        # In particular, never forward MediaMTX's Basic Auth challenge.
        return Response(upstream.content, status_code=upstream.status_code, headers=forwarded)

    async def close(self):
        for path in list(self.resources):
            await self.delete(path)
        await self.client.aclose()
