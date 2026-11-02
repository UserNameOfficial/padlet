import asyncio
import json
import os
import secrets
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from server.license_store import LicenseError, OWNER_ID, Store


@pytest.fixture(params=['sqlite', 'firestore'])
def ledger(request, tmp_path):
    if request.param == 'sqlite':
        store = Store(tmp_path / 'custom.sqlite3', 'custom-test-secret-' * 3)
    else:
        if not os.getenv('FIRESTORE_EMULATOR_HOST'):
            pytest.skip('Firestore emulator required')
        assert os.environ['FIRESTORE_EMULATOR_HOST'] in {'127.0.0.1:8088', 'localhost:8088'}
        from google.auth.credentials import AnonymousCredentials
        from google.cloud import firestore
        from server.firestore_store import FirestoreStore
        client = firestore.Client(project='demo-padlet-key-tests', credentials=AnonymousCredentials())
        store = FirestoreStore(client, 'custom-test-secret-' * 3, 'test_' + secrets.token_hex(8))
    yield store
    store.close()


def test_custom_key_permanent_login_usage_and_revocation(ledger):
    key = ledger.prepare_custom(OWNER_ID, 111, '나의 영구키-Abc!')
    assert key['key'] == '나의 영구키-Abc!'
    assert key['remaining'] is None and key['expires'] is None
    assert ledger.available(111) == []
    with pytest.raises(LicenseError):
        ledger.verify_key(key['key'], 'pending')
    ledger.delivered(key['id'], 111)
    assert ledger.verify_key('  나의 영구키-abc!  ', 'client')['license_id'] == key['id']
    for _ in range(3):
        assert ledger.consume(key['id'], secrets.token_hex(16)) == {'remaining': None, 'expires': None}
    event = ledger.next_event()
    assert key['key'] not in str(event)
    assert json.loads(event['payload'])['kind'] == 'forever'
    assert ledger.revoke(OWNER_ID, key['id']) == 1
    with pytest.raises(LicenseError):
        ledger.check(key['id'])
    with pytest.raises(LicenseError, match='이미 발급'):
        ledger.prepare_custom(OWNER_ID, 222, key['key'])


def test_custom_key_permissions_and_validation(ledger):
    with pytest.raises(LicenseError, match='관리자'):
        ledger.prepare_custom(222, 111, 'custom')
    for value in ['', '  ', 'a' * 65, 'line\nbreak', 'zero\u200bwidth']:
        with pytest.raises(LicenseError):
            ledger.prepare_custom(OWNER_ID, 111, value)
    assert ledger.prepare_custom(OWNER_ID, 111, '가')['key'] == '가'
    assert ledger.prepare_custom(OWNER_ID, 111, 'a' * 64)['key'] == 'a' * 64


def test_custom_duplicate_transaction_and_failed_delivery(ledger):
    def prepare(index):
        try:
            return ledger.prepare_custom(OWNER_ID, 111, 'Custom' if index % 2 else 'CUSTOM')
        except LicenseError:
            return None
    with ThreadPoolExecutor(max_workers=4) as pool:
        keys = [key for key in pool.map(prepare, range(8)) if key]
    assert len(keys) == 1
    key = keys[0]
    ledger.delivery_failed(key['id'], 111)
    ledger.delivery_failed(key['id'], 111)
    assert ledger.available(111) == []
    with pytest.raises(LicenseError):
        ledger.verify_key(key['key'], 'failed')


def test_random_keys_share_duplicate_namespace(ledger):
    key = ledger.prepare(111, ledger.grant(OWNER_ID, 111, 'uses', 1))
    with pytest.raises(LicenseError, match='이미 발급'):
        ledger.prepare_custom(OWNER_ID, 111, key['key'].lower())


@pytest.mark.parametrize('dm_fails', [False, True])
def test_custom_command_delivery(tmp_path, dm_fails):
    from keybot.bot import Bot
    from keybot.client import LicenseError as BotLicenseError
    store = Store(tmp_path / 'bot.sqlite3', 'custom-test-secret-' * 3)
    async def scenario():
        bot = Bot(store, None, 123)
        try:
            command = bot.tree.get_command('커스텀키발급')
            interaction = SimpleNamespace(user=SimpleNamespace(id=OWNER_ID), guild_id=123,
                response=SimpleNamespace(defer=AsyncMock(), is_done=lambda: True),
                followup=SimpleNamespace(send=AsyncMock()))
            assert await command.checks[0](interaction)
            with pytest.raises(LicenseError):
                await command.checks[0](SimpleNamespace(user=SimpleNamespace(id=222), guild_id=123))
            with pytest.raises(BotLicenseError):
                await command.checks[0](SimpleNamespace(user=SimpleNamespace(id=OWNER_ID), guild_id=456))
            recipient = SimpleNamespace(id=111, send=AsyncMock(
                side_effect=RuntimeError('DM disabled') if dm_fails else None))
            await command.callback(interaction, recipient, '내키-Abc')
            interaction.response.defer.assert_awaited_once_with(ephemeral=True)
            assert '영구' in recipient.send.await_args.args[0]
            assert '내키-Abc' not in str(interaction.followup.send.await_args_list)
            assert store.available(111) == []
            if dm_fails:
                with pytest.raises(LicenseError):
                    store.verify_key('내키-Abc', 'client')
            else:
                assert store.verify_key('내키-Abc', 'client')['expires'] is None
        finally:
            await bot.close()
    asyncio.run(scenario())
