"""Authenticated management routes; device credentials cannot manage family voices."""
from fastapi import Depends, Request
from fastapi.responses import JSONResponse, Response
from starlette.formparsers import MultiPartException, MultiPartParser

from backend.speech.familiar import CONSENT_VERSION, VoiceError
from contracts.interfaces import SpeechUnavailable

LIMIT = 10 * 1024 * 1024


def install_voice_routes(app, manager, authenticate, bounded_body, error_type):
    def family_only(device=Depends(authenticate)):
        if device is not None:
            raise error_type(403, 'forbidden', 'Use the family access token to manage voices.')

    auth = [Depends(family_only)]

    @app.exception_handler(VoiceError)
    async def voice_error(request, exc):
        return JSONResponse({'error': {'code': 'voice_unavailable', 'message': exc.message}},
                            status_code=exc.status, headers={'Cache-Control': 'no-store'})

    @app.get('/family/voice', dependencies=auth)
    async def status():
        return JSONResponse(manager.public(), headers={'Cache-Control': 'no-store'})

    @app.post('/family/voice', dependencies=auth)
    async def create(request: Request):
        raw = await bounded_body(request, LIMIT + 65536)

        class Parser(MultiPartParser):
            spool_max_size = LIMIT + 65537

        async def chunks():
            yield raw

        try:
            form = await Parser(request.headers, chunks(), max_files=1, max_fields=2,
                                max_part_size=LIMIT).parse()
            try:
                name, consent, upload = form.get('name'), form.get('consent'), form.get('file')
                if (len(form.multi_items()) != 3 or not isinstance(name, str)
                        or not 1 <= len(name.strip()) <= 60 or any(not c.isprintable() for c in name)
                        or consent != CONSENT_VERSION or not hasattr(upload, 'read')):
                    raise error_type(400, 'invalid_input', 'Provide your name, own-adult-voice consent and one recording.')
                audio = await upload.read(LIMIT + 1)
                media = (upload.content_type or '').split(';')[0]
                valid = {
                    'audio/webm': audio.startswith(b'\x1aE\xdf\xa3'),
                    'audio/wav': audio.startswith(b'RIFF') and audio[8:12] == b'WAVE',
                    'audio/mpeg': audio.startswith(b'ID3') or audio[:2] in (b'\xff\xfb', b'\xff\xf3', b'\xff\xf2'),
                    'audio/mp4': audio[4:8] == b'ftyp',
                    'audio/ogg': audio.startswith(b'OggS'),
                }
                if len(audio) > LIMIT:
                    raise error_type(413, 'too_large', 'Recording exceeds 10 MiB.')
                if len(audio) < 32 or not valid.get(media):
                    raise error_type(415, 'unsupported_media', 'Use a WebM, WAV, MP3, M4A or Ogg audio recording.')
                # Header checking is not a decoder; ElevenLabs validates the audio sample.
                return await manager.create(name.strip(), audio, media)
            finally:
                await form.close()
        except MultiPartException:
            raise error_type(400, 'invalid_input', 'Invalid voice upload.') from None

    @app.post('/family/voice/preview', dependencies=auth)
    async def preview():
        try:
            audio = await manager.preview()
        except SpeechUnavailable:
            raise error_type(503, 'speech_unavailable', 'Preview failed; standard voice remains available. Try again.') from None
        return Response(audio, media_type='audio/mpeg', headers={'Cache-Control': 'no-store'})

    @app.post('/family/voice/activate', dependencies=auth)
    async def activate():
        return await manager.select(True)

    @app.post('/family/voice/disable', dependencies=auth)
    async def disable():
        return await manager.select(False)

    @app.post('/family/voice/delete', dependencies=auth)
    async def delete():
        return await manager.delete()

    @app.post('/family/voice/reconcile', dependencies=auth)
    async def reconcile(request: Request):
        if await bounded_body(request, 128) != b'provider-cleanup-confirmed':
            raise error_type(400, 'invalid_input', 'Confirm manual deletion in ElevenLabs before resetting.')
        return await manager.reconcile()
