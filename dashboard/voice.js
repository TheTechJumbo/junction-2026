// Recordings and preview URLs live only in this tab and are discarded on disconnect.
export function setupVoice(getToken) {
  const $ = id => document.getElementById(id);
  let recorder, stream, timer, recording, playbackURL, sourceURL, generation = 0, controller, busy = false;
  let elapsedTimer, recordingStarted = 0;
  const elapsed = () => { const seconds = Math.min(120, Math.floor((performance.now() - recordingStarted) / 1000)); return `${String(Math.floor(seconds / 60)).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')}`; };
  const message = text => { $('voice-message').textContent = text; };
  function stop() {
    clearTimeout(timer);
    clearInterval(elapsedTimer);
    if (recorder?.state === 'recording') {
      $('voice-recording-label').textContent = 'Recording stopped — microphone off';
      $('voice-recording-time').textContent = elapsed();
      $('voice-recording').dataset.active = 'false';
    }
    $('voice-record').disabled = false;
    $('voice-record').textContent = 'Record my voice';
    $('voice-file').disabled = false;
    if (recorder?.state === 'recording') recorder.stop();
    stream?.getTracks().forEach(track => track.stop());
    stream = null;
    $('voice-stop').disabled = true;
  }
  function clearAudio() {
    for (const id of ['voice-source', 'voice-preview']) { $(id).pause(); $(id).removeAttribute('src'); $(id).load(); }
    if (sourceURL) URL.revokeObjectURL(sourceURL);
    if (playbackURL) URL.revokeObjectURL(playbackURL);
    sourceURL = playbackURL = null;
    recording = null;
    $('voice-recording').hidden = true;
    $('voice-file').value = '';
  }
  async function request(path = '', body, audio = false) {
    const current = generation;
    const token = getToken();
    if (!token) throw Error('Connect to home with the family token first. Voice setup is unavailable in Example day.');
    const response = await fetch('/family/voice' + path, {
      method: path || body ? 'POST' : 'GET', body,
      headers: {Authorization: `Bearer ${token}`}, credentials: 'omit', redirect: 'error', cache: 'no-store', signal: controller.signal,
    });
    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      throw Error(error.error?.message || 'Voice action failed. Reconnect and try again.');
    }
    const result = await (audio ? response.blob() : response.json());
    if (current !== generation) throw new DOMException('Session changed', 'AbortError');
    return result;
  }
  function show(state) {
    const exists = Boolean(state.status);
    $('voice-create').hidden = exists;
    $('voice-reconcile').hidden = !['creation_unknown', 'deletion_pending'].includes(state.status);
    $('voice-existing').hidden = !exists;
    $('voice-status').textContent = !exists ? 'Standard voice selected. No family voice uploaded.'
      : `${state.name}: ${state.status.replaceAll('_', ' ')}. ${state.active ? 'Familiar voice enabled.' : 'Standard voice selected.'}`;
    $('voice-create-submit').disabled = !state.configured;
    if (!state.configured) message('ElevenLabs must be configured on the backend before adding a voice.');
    $('voice-preview-button').disabled = state.status !== 'ready';
    $('voice-activate').disabled = state.status !== 'ready' || !state.previewed || Boolean(state.active);
    $('voice-disable').disabled = !state.active;
  }
  async function run(action) {
    if (busy) return;
    busy = true;
    const current = generation;
    $('voice-actions').disabled = true;
    message('Working…');
    try { await action(); if (current === generation) message(recorder?.state === 'recording' ? 'Microphone is on. Stop when you have finished; nothing uploads until you choose Upload and create my voice.' : 'Done.'); }
    catch (error) {
      if (current === generation && error.name !== 'AbortError') {
        try { if (getToken()) show(await request()); } catch { /* retain original error */ }
        if (current === generation) message(error.message || 'Connection failed. Try again.');
      }
    }
    finally { if (current === generation) { busy = false; $('voice-actions').disabled = false; } }
  }
  async function refresh() {
    generation++; busy = false;
    controller?.abort(); controller = new AbortController();
    stop(); clearAudio();
    $('voice-consent').checked = false;
    $('voice-listener').checked = false;
    $('voice-name').value = '';
    $('voice-actions').disabled = !getToken();
    $('voice-existing').hidden = true;
    $('voice-create').hidden = !getToken();
    $('voice-status').textContent = getToken() ? 'Loading voice settings…' : 'Connect to home to manage your voice. Example day does not upload recordings.';
    message('');
    if (getToken()) await run(async () => show(await request()));
  }
  $('voice-record').onclick = () => run(async () => {
    if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) throw Error('Microphone recording needs HTTPS or localhost. You can upload an audio file instead.');
    if (recorder?.state === 'recording') throw Error('Stop the current recording first.');
    const current = generation;
    let expired = false, permissionTimer;
    const pending = navigator.mediaDevices.getUserMedia({audio: true}).then(media => {
      if (expired || current !== generation) media.getTracks().forEach(t => t.stop());
      return media;
    });
    let captured;
    try {
      captured = await Promise.race([pending, new Promise((_, reject) => {
        permissionTimer = setTimeout(() => { expired = true; reject(Error('Microphone permission timed out. Allow microphone access or upload a recording.')); }, 15000);
      })]);
    } finally { clearTimeout(permissionTimer); }
    if (current !== generation) { captured.getTracks().forEach(t => t.stop()); return; }
    clearAudio(); stream = captured;
    const type = ['audio/webm', 'audio/mp4', 'audio/ogg'].find(t => MediaRecorder.isTypeSupported(t));
    if (!type) { stop(); throw Error('This browser cannot record a supported format. Upload a recording instead.'); }
    const chunks = [];
    recorder = new MediaRecorder(stream, {mimeType:type});
    recorder.ondataavailable = e => { if (e.data.size) chunks.push(e.data); };
    recorder.onstop = () => {
      if (current !== generation) return;
      stop();
      $('voice-recording-label').textContent = 'Recording stopped — microphone off';
      $('voice-recording-time').textContent = elapsed();
      $('voice-recording').dataset.active = 'false';
      recording = new Blob(chunks, {type});
      sourceURL = URL.createObjectURL(recording); $('voice-source').src = sourceURL;
      message('Recording ready. Listen before uploading.');
    };
    recorder.start(); $('voice-stop').disabled = false;
    recordingStarted = performance.now();
    $('voice-recording').hidden = false;
    $('voice-recording').dataset.active = 'true';
    $('voice-recording-label').textContent = 'Recording — microphone on';
    $('voice-recording-time').textContent = '00:00';
    elapsedTimer = setInterval(() => { $('voice-recording-time').textContent = elapsed(); }, 250);
    $('voice-record').disabled = true;
    $('voice-record').textContent = 'Recording…';
    $('voice-file').disabled = true;
    timer = setTimeout(stop, 120000);
  });
  $('voice-stop').onclick = stop;
  $('voice-file').onchange = () => {
    stop(); recording = null;
    if (sourceURL) URL.revokeObjectURL(sourceURL);
    const file = $('voice-file').files[0];
    if (file) { sourceURL = URL.createObjectURL(file); $('voice-source').src = sourceURL; }
  };
  $('voice-create').onsubmit = event => {
    event.preventDefault();
    run(async () => {
      if (recorder?.state === 'recording') throw Error('Stop the recording before uploading.');
      const file = $('voice-file').files[0] || recording;
      if (!file || file.size > 10 * 1024 * 1024) throw Error('Choose a recording up to 10 MiB.');
      if (!$('voice-consent').checked) throw Error('Your explicit consent is required.');
      const form = new FormData();
      form.append('name', $('voice-name').value.trim());
      form.append('consent', 'own-adult-voice-v1');
      form.append('file', file, 'recording');
      show(await request('', form)); clearAudio();
    });
  };
  $('voice-preview-button').onclick = () => run(async () => {
    const blob = await request('/preview', undefined, true);
    if (playbackURL) URL.revokeObjectURL(playbackURL);
    playbackURL = URL.createObjectURL(blob); $('voice-preview').src = playbackURL;
    show(await request());
    await $('voice-preview').play().catch(() => {});
  });
  $('voice-activate').onclick = () => run(async () => {
    if (!$('voice-listener').checked) throw Error('Confirm you have previewed the voice and explained the automated assistant to the wearer.');
    show(await request('/activate'));
  });
  $('voice-disable').onclick = () => run(async () => { show(await request('/disable')); clearAudio(); });
  $('voice-delete').onclick = () => {
    if (confirm('Disable and delete this cloned voice from ElevenLabs?')) run(async () => {
      show(await request('/delete')); clearAudio();
    });
  };
  $('voice-reconcile').onclick = () => {
    if (confirm('Only continue after checking ElevenLabs and deleting any Tuttu clone from the failed creation. Have you completed that cleanup?')) run(async () => show(await request('/reconcile', 'provider-cleanup-confirmed')));
  };
  window.addEventListener('pagehide', () => { generation++; stop(); clearAudio(); controller?.abort(); });
  document.addEventListener('visibilitychange', () => { if (document.hidden) stop(); });
  return refresh;
}
