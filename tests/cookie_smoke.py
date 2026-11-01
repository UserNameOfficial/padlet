"""Real Chromium with fixture cookies and fully mocked Padlet traffic."""
import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server.browser import BrowserSession
from server.cookies import parse_cookie_header
from server.engine import Sender

URL = 'https://padlet.com/example/board'
BOOTSTRAP = '<meta name="csrf-token" content="fixture"><script>window.ww={uid:"fixture"}</script>'


async def main():
    posts, deleted, identities = [], [], []
    use_cookie, password_gate, expired = False, False, False

    async def route(self, route):
        request = route.request
        assert urlsplit(request.url).hostname == 'padlet.com'
        path = urlsplit(request.url).path
        cookie = (await request.all_headers()).get('cookie', '')
        logged_in = 'ww_p=fixture-account' in cookie
        if request.method == 'DELETE':
            index = int(path.rsplit('/', 1)[1].removeprefix('post')) - 1
            assert logged_in == identities[index]
            deleted.append(path)
            return await route.fulfill(status=204, body='')
        assert logged_in == use_cookie  # Includes the first navigation, before any post.
        if request.url == URL:
            if password_gate:
                if request.method == 'POST':
                    assert parse_qs(request.post_data)['password'] == ['fixture-password']
                else:
                    gate = {'showPasswordInput': True, 'passwordSubmitUrl': URL}
                    return await route.fulfill(content_type='text/html', body=
                        '<meta name="csrf-token" content="fixture"><script>window.ww={vueStartingState:' + json.dumps(gate) + '}</script>')
            return await route.fulfill(content_type='text/html', body=BOOTSTRAP)
        if path.startswith('/api/11/padlet-starting-state'):
            user = dict(id=456, hashid='account456', registered_at='2026-01-01') if logged_in and not expired else dict(id=10, hashid='guest', registered_at=None)
            return await route.fulfill(json=dict(canIWrite=True, user=user, wall=dict(id=22, hashid='board')))
        if path == '/api/5/wall_sections' or path.endswith('/custom-post-properties'):
            return await route.fulfill(json={'data': []})
        if path.endswith('/post-properties'):
            return await route.fulfill(json={'data': {'attributes': {'required_fields': {}}}})
        if path == '/api/9/wishes' and request.method == 'POST':
            payload = request.post_data_json
            assert payload['author_id'] == (456 if logged_in else 10)
            posts.append(payload)
            identities.append(logged_in)
            return await route.fulfill(status=201, json={'data': {'id': len(posts), 'attributes': {'hashid': f'post{len(posts)}'}}})
        raise AssertionError('Unexpected mocked route')

    sender = Sender()
    with patch.object(BrowserSession, 'route', route):
        try:
            for use_cookie, password_gate in [(True, False), (False, False), (True, True), (False, True)]:
                await sender.queue_run(URL, 'Title', '', 1, 0,
                    password='fixture-password' if password_gate else None,
                    cookies=parse_cookie_header('ww_p=fixture-account') if use_cookie else None)
                await sender.worker
                assert sender.phase == 'finished' and sender.sent == 1, sender.status()
            assert identities == [True, False, True, False]
            use_cookie, password_gate, expired = True, False, True
            await sender.queue_run(URL, 'Title', '', 1, 0, cookies=parse_cookie_header('ww_p=fixture-account'))
            await sender.worker
            assert sender.phase == 'error' and len(posts) == 4
            assert 'fixture-account' not in json.dumps(sender.status())
            await sender.queue_delete()
            await sender.delete_worker
            assert sender.phase == 'deleted' and len(deleted) == 4, sender.status()
        finally:
            await sender.shutdown()
    print(json.dumps({'cookie_and_guest': True, 'password_combinations': 4,
                      'expired_cookie_no_posts': True, 'deletion_identity_preserved': True, 'real_posts': 0}))


if __name__ == '__main__':
    asyncio.run(main())
