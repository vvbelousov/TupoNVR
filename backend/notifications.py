"""Debounced incidents and a bounded SQLite outbox for generic webhooks."""
import asyncio
import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from urllib.parse import urlsplit

import httpx
from db import db

log = logging.getLogger('nvr.notifications')


class Webhooks:
    def __init__(self):
        self.url = os.getenv('WEBHOOK_URL', '')
        self.token = os.getenv('WEBHOOK_TOKEN', '')
        self.debounce = int(os.getenv('WEBHOOK_DEBOUNCE_SECONDS', '60'))
        self.cooldown = int(os.getenv('WEBHOOK_COOLDOWN_SECONDS', '600'))
        if self.debounce < 0 or self.cooldown < 0:
            raise ValueError('Webhook timing must be nonnegative')
        if self.url:
            parsed = urlsplit(self.url)
            _ = parsed.port
            if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.fragment:
                raise ValueError('WEBHOOK_URL must be an HTTP(S) URL without userinfo or fragment')
        self.client = httpx.AsyncClient(timeout=5, trust_env=False, follow_redirects=False)

    def observe(self, observations, now=None):
        if not self.url:
            return
        now = time.time() if now is None else now
        with db() as c:
            c.execute('DELETE FROM webhook_events WHERE created_at<? AND (delivered_at IS NOT NULL OR failed=1)', (now - 7 * 86400,))
            for key, observation in observations.items():
                state = c.execute('SELECT * FROM alert_states WHERE key=?', (key,)).fetchone()
                since = state['failed_since'] if state else None
                notified = bool(state['notified']) if state else False
                last_failure = state['last_failure'] if state else 0
                if observation['failing']:
                    since = now if since is None else since
                    if not notified and now - since >= self.debounce and now - last_failure >= self.cooldown:
                        if self._enqueue(c, observation, 'failure', now):
                            notified = True
                            last_failure = now
                else:
                    since = None
                    if notified:
                        event = 'recovery' if observation.get('expected', True) else 'resolved'
                        if self._enqueue(c, observation, event, now):
                            notified = False
                c.execute('INSERT INTO alert_states(key,failed_since,notified,last_failure) VALUES(?,?,?,?) ON CONFLICT(key) DO UPDATE SET failed_since=excluded.failed_since,notified=excluded.notified,last_failure=excluded.last_failure', (key, since, int(notified), last_failure))
            for row in c.execute('SELECT key FROM alert_states').fetchall():
                if row['key'] not in observations:
                    c.execute('DELETE FROM alert_states WHERE key=?', (row['key'],))

    def _enqueue(self, c, observation, state, now):
        if c.execute('SELECT COUNT(*) FROM webhook_events WHERE delivered_at IS NULL AND failed=0').fetchone()[0] >= 1000:
            log.warning('webhook_queue_full')
            return False
        eid = str(uuid.uuid4())
        # Explicit allowlist: never send URLs, credentials, names, paths, or raw errors.
        payload = {k: observation[k] for k in ('kind', 'camera_id', 'destination', 'reason') if k in observation}
        payload.update(id=eid, state=state, occurred_at=datetime.fromtimestamp(now, timezone.utc).isoformat())
        c.execute('INSERT INTO webhook_events(id,payload,created_at,next_attempt) VALUES(?,?,?,?)', (eid, json.dumps(payload), now, now))
        return True

    async def deliver_one(self, now=None):
        if not self.url:
            return
        now = time.time() if now is None else now
        with db() as c:
            # FIFO: a recovery must not overtake a failure awaiting retry.
            event = c.execute('SELECT * FROM webhook_events WHERE delivered_at IS NULL AND failed=0 ORDER BY created_at,rowid LIMIT 1').fetchone()
        if not event or event['next_attempt'] > now:
            return
        attempts = event['attempts'] + 1
        headers = {'X-NVR-Event-ID': event['id']}
        if self.token:
            headers['Authorization'] = f'Bearer {self.token}'
        try:
            response = await self.client.post(self.url, json=json.loads(event['payload']), headers=headers)
            response.raise_for_status()
        except (httpx.HTTPError, OSError):
            with db() as c:
                c.execute('UPDATE webhook_events SET attempts=?,next_attempt=?,failed=? WHERE id=?', (attempts, now + min(300, 5 * 2 ** attempts), int(attempts >= 3), event['id']))
            log.warning('webhook_delivery_failed attempt=%d', attempts)
        else:
            with db() as c:
                c.execute('UPDATE webhook_events SET attempts=?,delivered_at=? WHERE id=?', (attempts, now, event['id']))

    def status(self):
        with db() as c:
            pending = c.execute('SELECT COUNT(*) FROM webhook_events WHERE delivered_at IS NULL AND failed=0').fetchone()[0]
            failed = c.execute('SELECT COUNT(*) FROM webhook_events WHERE failed=1').fetchone()[0]
            last = c.execute('SELECT MAX(delivered_at) FROM webhook_events').fetchone()[0]
        return {'enabled': bool(self.url), 'pending': pending, 'failed': failed,
                'last_delivery': datetime.fromtimestamp(last, timezone.utc).isoformat() if last else None}

    async def run(self):
        while True:
            try:
                await self.deliver_one()
            except Exception:
                log.warning('webhook_worker_failed')
            await asyncio.sleep(1)

    async def close(self):
        await self.client.aclose()
