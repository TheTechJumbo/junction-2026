"""One consenting adult's optional voice per single-household deployment.

No source recordings are persisted. Provider identifiers and consent receipts remain
in local SQLite, outside the event replication stream. Single uvicorn worker only.
"""
import asyncio
import json
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import httpx

from backend.speech.elevenlabs import ElevenLabsSpeechService
from contracts.interfaces import SpeechUnavailable

CONSENT_VERSION = 'own-adult-voice-v1'
PREVIEW = 'This is Tuttu using a voice shared by your family. This is an automated assistant, not a live call. The keys were last seen on the kitchen table.'


class VoiceError(Exception):
    def __init__(self, message, status=409, definitive=False):
        self.message, self.status, self.definitive = message, status, definitive


class FamiliarVoice:
    def __init__(self, path, standard, client=None):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute('CREATE TABLE IF NOT EXISTS voice (id INTEGER PRIMARY KEY, payload TEXT NOT NULL)')
        self.db.commit()
        self.standard = standard
        self.client = client
        self.lock = asyncio.Lock()
        self.clone = None
        # An interrupted creation has an unknown provider outcome; do not duplicate it.
        if self.state().get('status') == 'creating':
            self.save({**self.state(), 'status': 'creation_unknown'})

    def state(self):
        row = self.db.execute('SELECT payload FROM voice WHERE id=1').fetchone()
        return json.loads(row[0]) if row else {}

    def save(self, value):
        self.db.execute('INSERT OR REPLACE INTO voice VALUES (1, ?)', (json.dumps(value),))
        self.db.commit()

    def public(self):
        state = self.state()
        return {k: state.get(k) for k in ('name', 'status', 'active', 'previewed', 'consented_at')} | {
            'configured': bool(getattr(self.standard, 'api_key', None)),
            'scope': 'single_household',
        }

    async def provider(self, method, path, **kwargs):
        key = getattr(self.standard, 'api_key', None)
        if not key:
            raise VoiceError('ElevenLabs is not configured.', 503)
        if self.client is None:
            self.client = httpx.AsyncClient(trust_env=False, follow_redirects=False)
        try:
            response = await self.client.request(method, 'https://api.elevenlabs.io/v1/' + path,
                                                 headers={'xi-api-key': key}, timeout=60, **kwargs)
        except (httpx.HTTPError, TimeoutError):
            raise VoiceError('ElevenLabs did not confirm completion. Check the voice status before retrying.', 503) from None
        if response.status_code == 404 and method == 'DELETE':
            return {}
        if response.status_code not in (200, 201, 204):
            # Translate only known codes. Never expose raw upstream text or credentials.
            try:
                detail = response.json().get('detail', {})
                code = detail.get('status', '') if isinstance(detail, dict) else ''
            except (ValueError, AttributeError):
                code = ''
            messages = {
                'missing_permissions': 'The ElevenLabs key lacks permission for this action. Enable Voices → Write on this key; workspace members also need permission to create instant voice clones.',
                'invalid_api_key': 'ElevenLabs rejected the configured API key. Update the backend key and restart the backend.',
                'subscription_required': 'This ElevenLabs account does not have the subscription required for voice cloning. Check Instant Voice Cloning availability in ElevenLabs.',
                'voice_limit_reached': 'The ElevenLabs account has reached its voice limit. Manage existing voices in ElevenLabs before retrying.',
                'quota_exceeded': 'The ElevenLabs account has reached its usage allowance. Check its quota before retrying.',
                'voice_access_denied': 'This ElevenLabs key cannot access the selected voice. Check voice access in ElevenLabs.',
            }
            message = messages.get(code)
            if not message:
                message = {
                    401: 'ElevenLabs rejected authentication or permissions. Check the backend key and Voices → Write permission.',
                    403: 'ElevenLabs denied this action. Check Voices → Write, workspace cloning permission and plan eligibility.',
                    422: 'ElevenLabs rejected the voice sample or request. Try a clear 1–2 minute recording of your own voice in a supported audio format.',
                    429: 'ElevenLabs is rate-limiting requests. Wait before retrying.',
                }.get(response.status_code, 'ElevenLabs could not complete the action. Its service may be unavailable; check voice status before retrying.')
            raise VoiceError(message, 503, definitive=400 <= response.status_code < 500)
        try:
            return response.json() if response.content else {}
        except ValueError:
            raise VoiceError('ElevenLabs returned an unsupported response.', 503) from None

    async def create(self, name, audio, media):
        async with self.lock:
            if self.state():
                raise VoiceError('A voice or pending creation already exists. Delete it before adding another.')
            if not getattr(self.standard, 'api_key', None):
                raise VoiceError('ElevenLabs is not configured.', 503)
            state = {'name': name, 'status': 'creating', 'active': False, 'previewed': False,
                     'consent_version': CONSENT_VERSION, 'consented_at': datetime.now(UTC).isoformat()}
            self.save(state)
            extension = {'audio/webm': 'webm', 'audio/wav': 'wav', 'audio/mpeg': 'mp3', 'audio/mp4': 'm4a', 'audio/ogg': 'ogg'}[media]
            try:
                result = await self.provider('POST', 'voices/add', data={'name': 'Tuttu - ' + name},
                                             files={'files': ('voice.' + extension, audio, media)})
                voice_id = result.get('voice_id')
                if not isinstance(voice_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', voice_id):
                    raise VoiceError('Voice creation needs checking in ElevenLabs.', 503)
                state.update(voice_id=voice_id, status='verification_required' if result.get('requires_verification', True) else 'ready')
                self.save(state)
            except VoiceError as exc:
                if exc.definitive:
                    self.db.execute('DELETE FROM voice')
                    self.db.commit()
                else:
                    self.save({**state, 'status': 'creation_unknown'})
                raise
            return self.public()

    def service(self, state):
        if self.clone is None:
            self.clone = ElevenLabsSpeechService(api_key=self.standard.api_key, voice_id=state['voice_id'],
                model_id=self.standard.model_id, timeout_seconds=self.standard.timeout_seconds)
        return self.clone

    async def preview(self):
        async with self.lock:
            state = self.state()
            if state.get('status') != 'ready':
                raise VoiceError('Voice is not ready. If verification is required, complete it in ElevenLabs and add a new voice here.')
            audio = await self.service(state).synthesize(PREVIEW)
            self.save({**state, 'previewed': True})
            return audio

    async def select(self, active):
        async with self.lock:
            state = self.state()
            if active and (state.get('status') != 'ready' or not state.get('previewed')):
                raise VoiceError('Preview this voice successfully before enabling it.')
            if state:
                self.save({**state, 'active': active})
            return self.public()

    async def delete(self):
        async with self.lock:
            state = self.state()
            if not state:
                return self.public()
            self.save({**state, 'active': False, 'status': 'deletion_pending'})
            if self.clone:
                await self.clone.aclose()
                self.clone = None
            if not state.get('voice_id'):
                raise VoiceError('Creation outcome is unknown. Check and remove any Tuttu voice in ElevenLabs; local reset requires administrator reconciliation.', 409)
            await self.provider('DELETE', 'voices/' + state['voice_id'])
            self.db.execute('DELETE FROM voice')
            self.db.commit()
            return self.public()

    async def reconcile(self):
        async with self.lock:
            state = self.state()
            if state.get('voice_id') or state.get('status') not in ('creation_unknown', 'deletion_pending'):
                raise VoiceError('Only an unknown creation can be reset after manual provider cleanup.')
            self.db.execute('DELETE FROM voice')
            self.db.commit()
            return self.public()

    async def synthesize(self, text):
        if not self.state().get('active'):
            return await self.standard.synthesize(text)
        async with self.lock:
            state = self.state()
            if state.get('active') and state.get('status') == 'ready':
                try:
                    return await self.service(state).synthesize('Tuttu assistant, using your family’s shared voice. ' + text)
                except SpeechUnavailable:
                    pass
            return await self.standard.synthesize(text)

    async def aclose(self):
        if self.clone:
            await self.clone.aclose()
        if self.client:
            await self.client.aclose()
        self.db.close()
