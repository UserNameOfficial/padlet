import asyncio
import copy
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from server.app import app, sessions
from server.cookies import fixed_cookies, parse_cookie_header
from server.engine import Sender
from test_automatic import setup, STATE, URL


def test_fixed_cookie_export_and_environment(monkeypatch, tmp_path):
    path = tmp_path / "cookie.txt"
    path.write_text("ww_p\nfixture-secret\n\nww_s\nfixture-session\n", encoding="utf-8")
    monkeypatch.setenv("PADLET_COOKIE_FILE", str(path))
    monkeypatch.delenv("PADLET_COOKIE_HEADER", raising=False)
    cookies = fixed_cookies()
    assert [(c['name'], c['value']) for c in cookies] == [('ww_p', 'fixture-secret'), ('ww_s', 'fixture-session')]
    assert all(c['domain'] == '.padlet.com' and c['secure'] for c in cookies)
    monkeypatch.setenv("PADLET_COOKIE_HEADER", "Cookie: ww_p=override-secret==")
    assert fixed_cookies()[0]['value'] == 'override-secret=='


@pytest.mark.parametrize('value', ['', 'private-secret', 'x=private-secret\r\nHost: evil',
    'x=private-secret; x=other', 'x=private-secret,value', 'x=' + 'private-secret' * 2000])
def test_malformed_cookie_diagnostics_do_not_echo_secrets(value):
    with pytest.raises(ValueError) as error:
        parse_cookie_header(value)
    assert 'private-secret' not in str(error.value)


@pytest.mark.parametrize('password', [None, 'fixture-password'])
@pytest.mark.parametrize('authenticated', [True, False])
def test_cookie_identity_must_be_logged_in_before_post(monkeypatch, password, authenticated):
    async def scenario():
        state = copy.deepcopy(STATE)
        if authenticated:
            state['user'] = dict(id=456, hashid='account456', registered_at='2026-01-01', username='fixture')
        http, browsers = setup(monkeypatch, state)
        seen = []
        from server.engine import BrowserSession
        class CookieBrowser(BrowserSession):
            async def open(self, url, password=None, cookies=None):
                seen.append((password, cookies))
        monkeypatch.setattr('server.engine.BrowserSession', CookieBrowser)
        cookies = parse_cookie_header('ww_p=fixture-secret')
        sender = Sender()
        await sender.queue_run(URL, 'Title', '', 1, 0, password=password, cookies=cookies)
        await sender.worker
        assert seen == [(password, cookies)]
        if authenticated:
            assert sender.phase == 'finished' and http.post.await_count == 2
            assert all(c.kwargs['json']['author_id'] == 456 and c.kwargs['json']['author_hashid'] == 'account456'
                       for c in http.post.await_args_list)
        else:
            assert sender.phase == 'error'
            http.post.assert_not_awaited()
        assert 'fixture-secret' not in str(sender.status())
        assert browsers[0].closed
    asyncio.run(scenario())


def test_fixed_cookie_api_selection_and_no_client_override(monkeypatch, licensed_session):
    monkeypatch.setenv('PADLET_COOKIE_HEADER', 'ww_p=server-only-fixture')
    with TestClient(app) as client:
        token = licensed_session()
        sender = sessions.items[token].sender
        sender.queue_run = AsyncMock()
        headers = {'Authorization': 'Bearer ' + token, 'X-Request-ID': 'cookie-request-0001'}
        data = dict(url=URL, subject='Title', use_cookie=True, cookie='ww_p=client-override')
        result = client.post('/api/run', headers=headers, json=data)
        assert result.status_code == 200
        assert sender.queue_run.await_args.kwargs['cookies'][0]['value'] == 'server-only-fixture'
        assert 'server-only-fixture' not in result.text
        assert 'client-override' not in result.text
        assert client.post('/api/run', headers=headers, json=data).status_code == 200
        assert sender.queue_run.await_count == 1
        assert client.post('/api/run', headers=headers, json={**data, 'use_cookie':False}).status_code == 409
        headers['X-Request-ID'] = 'cookie-request-0002'
        assert client.post('/api/run', headers=headers, json={**data, 'use_cookie':False}).status_code == 200
        assert 'cookies' not in sender.queue_run.await_args.kwargs
        assert client.post('/api/cookie', headers=headers, json={'cookie':'override'}).status_code == 404


def test_missing_fixed_cookie_rejected_before_queue(monkeypatch, tmp_path, licensed_session):
    monkeypatch.delenv('PADLET_COOKIE_HEADER', raising=False)
    monkeypatch.setenv('PADLET_COOKIE_FILE', str(tmp_path / 'missing.txt'))
    with TestClient(app) as client:
        token = licensed_session()
        sender = sessions.items[token].sender
        sender.queue_run = AsyncMock()
        headers = {'Authorization': 'Bearer ' + token, 'X-Request-ID':'missing-cookie-0001'}
        result = client.post('/api/run', headers=headers, json=dict(url=URL, use_cookie=True))
        assert result.status_code == 409
        sender.queue_run.assert_not_awaited()
        assert client.post('/api/run', headers=headers, json=dict(url=URL, use_cookie=False)).status_code == 200


def test_custom_cookie_overrides_fixed_only_in_its_panel_session(monkeypatch, licensed_session):
    monkeypatch.setenv('PADLET_COOKIE_HEADER', 'ww_p=fixed-fixture')
    with TestClient(app) as client:
        first, second = licensed_session(), licensed_session()
        sender = sessions.items[first].sender
        sender.queue_run = AsyncMock()
        auth = {'Authorization': 'Bearer ' + first}
        assert client.post('/api/custom-cookie', headers=auth,
                           json={'cookie': 'ww_p=custom-secret; ww_s=custom-session'}).status_code == 200
        assert sessions.items[first].custom_cookie is not None
        assert sessions.items[second].custom_cookie is None
        state = client.get('/api/state', headers=auth).json()
        assert state['custom_cookie_active'] is True
        assert 'custom-secret' not in str(state)
        headers = {**auth, 'X-Request-ID': 'custom-cookie-00001'}
        result = client.post('/api/run', headers=headers, json={'url': URL, 'use_cookie': True})
        assert result.status_code == 200
        assert sender.queue_run.await_args.kwargs['cookies'][0]['value'] == 'custom-secret'
        assert 'custom-secret' not in result.text
        assert client.post('/api/custom-cookie', headers=auth, json={'cookie': None}).status_code == 200
        headers['X-Request-ID'] = 'custom-cookie-00002'
        assert client.post('/api/run', headers=headers, json={'url': URL, 'use_cookie': True}).status_code == 200
        assert sender.queue_run.await_args.kwargs['cookies'][0]['value'] == 'fixed-fixture'


def test_custom_cookie_validation_does_not_echo_value(licensed_session):
    with TestClient(app) as client:
        auth = {'Authorization': 'Bearer ' + licensed_session()}
        for value in ['ww_p=private-secret\nHost:evil', 'ww_p=private-secret; ww_p=other']:
            result = client.post('/api/custom-cookie', headers=auth, json={'cookie': value})
            assert result.status_code in {400, 422}
            assert 'private-secret' not in result.text
