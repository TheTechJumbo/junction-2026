import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.app import Settings, create_app
from backend.speech.familiar import FamiliarVoice, VoiceError
from contracts.interfaces import SpeechUnavailable


class Standard:
    api_key = 'synthetic'
    voice_id = 'standard'
    model_id = 'eleven_flash_v2_5'
    timeout_seconds = .8

    async def synthesize(self, text):
        return b'standard-audio'


class Clone:
    def __init__(self, fail=False):
        self.fail = fail
        self.text = ''

    async def synthesize(self, text):
        self.text = text
        if self.fail:
            raise SpeechUnavailable()
        return b'clone-audio'

    async def aclose(self):
        pass


def test_lifecycle_restart_preview_activation_and_delete(tmp_path):
    calls = []

    def handler(request):
        calls.append((request.method, request.url.path))
        return httpx.Response(200, json={'voice_id': 'synthetic_voice', 'requires_verification': False})

    async def run():
        path = tmp_path / 'voice.db'
        manager = FamiliarVoice(path, Standard(), httpx.AsyncClient(transport=httpx.MockTransport(handler)))
        state = await manager.create('Family', b'synthetic', 'audio/webm')
        assert state['active'] is False and 'voice_id' not in state
        with pytest.raises(VoiceError):
            await manager.select(True)
        manager.clone = Clone()
        manager.preview_clone = Clone()
        await manager.preview()
        await manager.select(True)
        assert await manager.synthesize('Keys last seen.') == b'clone-audio'
        assert 'Tuttu assistant' in manager.clone.text
        await manager.aclose()
        manager = FamiliarVoice(path, Standard(), httpx.AsyncClient(transport=httpx.MockTransport(handler)))
        assert manager.public()['active'] is True
        manager.clone = Clone(fail=True)
        assert await manager.synthesize('Unknown.') == b'standard-audio'
        await manager.delete()
        assert manager.state() == {}
        assert calls[-1] == ('DELETE', '/v1/voices/synthetic_voice')
        await manager.aclose()
    asyncio.run(run())


def test_verification_cannot_activate_and_failed_delete_disables(tmp_path):
    def handler(request):
        if request.method == 'DELETE':
            return httpx.Response(500)
        return httpx.Response(200, json={'voice_id': 'synthetic', 'requires_verification': True})

    async def run():
        manager = FamiliarVoice(tmp_path / 'voice.db', Standard(), httpx.AsyncClient(transport=httpx.MockTransport(handler)))
        await manager.create('Family', b'synthetic', 'audio/webm')
        with pytest.raises(VoiceError):
            await manager.preview()
        with pytest.raises(VoiceError):
            await manager.select(True)
        with pytest.raises(VoiceError):
            await manager.delete()
        assert manager.state()['active'] is False
        assert manager.state()['status'] == 'deletion_pending'
        await manager.aclose()
    asyncio.run(run())


def test_routes_require_family_consent_and_audio(tmp_path):
    app = create_app(Settings(api_token='family', local_data_dir=tmp_path,
                              device_tokens={'box': 'device'}), speech=Standard())
    with TestClient(app) as client:
        assert client.get('/family/voice').status_code == 401
        assert client.get('/family/voice', headers={'Authorization': 'Bearer device'}).status_code == 403
        headers = {'Authorization': 'Bearer family'}
        assert client.get('/family/voice', headers=headers).json()['scope'] == 'single_household'
        for consent, audio, expected in [('', b'RIFF' + b'x'*40, 400), ('own-adult-voice-v1', b'not audio'*5, 415)]:
            result = client.post('/family/voice', headers=headers, data={'name':'Family', 'consent':consent},
                                 files={'file': ('sample.wav', audio, 'audio/wav')})
            assert result.status_code == expected
        assert client.post('/family/voice/activate', headers=headers).status_code == 409
        assert client.post('/speak', headers=headers, json={'text':'Test'}).content == b'standard-audio'


def test_unknown_creation_never_retries_and_survives_restart(tmp_path):
    def handler(request):
        raise httpx.ReadTimeout('sensitive upstream error')

    async def run():
        path = tmp_path / 'voice.db'
        manager = FamiliarVoice(path, Standard(), httpx.AsyncClient(transport=httpx.MockTransport(handler)))
        with pytest.raises(VoiceError, match='did not confirm'):
            await manager.create('Family', b'synthetic', 'audio/webm')
        await manager.aclose()
        manager = FamiliarVoice(path, Standard())
        assert manager.state()['status'] == 'creation_unknown'
        with pytest.raises(VoiceError, match='already exists'):
            await manager.create('Family', b'synthetic', 'audio/webm')
        await manager.aclose()
    asyncio.run(run())


def test_http_lifecycle_and_deleted_voice_never_used(tmp_path):
    app = create_app(Settings(api_token='family', local_data_dir=tmp_path), speech=Standard())
    calls = []

    def handler(request):
        calls.append(request.method)
        return httpx.Response(200, json={'voice_id': 'synthetic', 'requires_verification': False})

    app.state.familiar_voice.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with TestClient(app, headers={'Authorization': 'Bearer family'}) as client:
        result = client.post('/family/voice', data={'name':'Family', 'consent':'own-adult-voice-v1'},
                             files={'file': ('sample.wav', b'RIFFxxxxWAVE' + b'x'*32, 'audio/wav')})
        assert result.status_code == 200
        assert result.json()['active'] is False
        assert client.post('/family/voice/activate').status_code == 409
        app.state.familiar_voice.clone = Clone()
        app.state.familiar_voice.preview_clone = Clone()
        assert client.post('/family/voice/preview').content == b'clone-audio'
        assert client.post('/family/voice/activate').json()['active'] is True
        assert client.post('/speak', json={'text':'Keys.'}).content == b'clone-audio'
        assert client.post('/family/voice/disable').json()['active'] is False
        assert client.post('/speak', json={'text':'Keys.'}).content == b'standard-audio'
        assert client.post('/family/voice/delete').status_code == 200
        assert client.post('/family/voice/delete').status_code == 200
        assert calls == ['POST', 'DELETE']
        assert client.post('/speak', json={'text':'Keys.'}).content == b'standard-audio'


def test_rejected_creation_can_retry_but_reconcile_cannot_forget_known_voice(tmp_path):
    async def run():
        manager = FamiliarVoice(tmp_path / 'voice.db', Standard(), httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: httpx.Response(403))))
        with pytest.raises(VoiceError):
            await manager.create('Family', b'synthetic', 'audio/webm')
        assert manager.state() == {}
        manager.save({'status':'creation_unknown'})
        await manager.reconcile()
        assert manager.state() == {}
        manager.save({'voice_id':'synthetic', 'status':'deletion_pending'})
        with pytest.raises(VoiceError):
            await manager.reconcile()
        await manager.aclose()
    asyncio.run(run())


@pytest.mark.parametrize(('code', 'expected'), [
    ('missing_permissions', 'Voices → Write'),
    ('subscription_required', 'subscription'),
    ('voice_limit_reached', 'voice limit'),
    ('quota_exceeded', 'usage allowance'),
    ('unknown_code', 'authentication or permissions'),
])
def test_provider_errors_are_actionable_without_leaking_messages(tmp_path, code, expected):
    async def run():
        manager = FamiliarVoice(tmp_path / 'voice.db', Standard(), httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: httpx.Response(401, json={
                'detail': {'status': code, 'message': 'private-upstream-secret'}
            }))))
        with pytest.raises(VoiceError) as error:
            await manager.create('Family', b'synthetic', 'audio/webm')
        assert expected in error.value.message
        assert 'private-upstream-secret' not in error.value.message
        assert manager.state() == {}
        await manager.aclose()
    asyncio.run(run())


def test_preview_has_separate_cold_start_budget(tmp_path):
    async def run():
        manager = FamiliarVoice(tmp_path / 'voice.db', Standard())
        state = {'voice_id': 'synthetic'}
        assert manager.service(state).timeout_seconds == .8
        assert manager.service(state, preview=True).timeout_seconds == 20
        assert manager.clone is not manager.preview_clone
        await manager.aclose()
    asyncio.run(run())
