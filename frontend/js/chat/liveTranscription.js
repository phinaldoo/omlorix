/**
 * Shared browser capture and WebSocket transport for live dictation providers.
 *
 * The API key never reaches the browser. This controller sends 24 kHz PCM16
 * chunks to Omlorix's same-origin authenticated proxy and reconciles transcript
 * events by item id before exposing partial or final text to composers.
 */
(function initializeLiveTranscription(global) {
    'use strict';

    const TARGET_SAMPLE_RATE = 24000;
    const READY_TIMEOUT_MS = 20000;
    const COMPLETION_TIMEOUT_MS = 20000;
    let activeSession = null;

    const isReady = () => {
        if (global.chatSetup && typeof global.chatSetup.live_transcription_ready === 'boolean') {
            return global.chatSetup.live_transcription_ready;
        }
        try {
            return localStorage.getItem('live_transcription_ready') === 'true';
        } catch (_) {
            return false;
        }
    };

    const isSupported = () => {
        const AudioContextCtor = global.AudioContext || global.webkitAudioContext;
        return Boolean(
            global.WebSocket
            && navigator.mediaDevices?.getUserMedia
            && AudioContextCtor
            && AudioContextCtor.prototype.createScriptProcessor
        );
    };

    const buildWebSocketUrl = () => {
        const scheme = global.location.protocol === 'https:' ? 'wss:' : 'ws:';
        return `${scheme}//${global.location.host}/api/v1/realtime/transcription/live`;
    };

    const resample = (input, inputRate) => {
        if (!input?.length) {
            return new Float32Array(0);
        }
        if (inputRate === TARGET_SAMPLE_RATE) {
            return new Float32Array(input);
        }
        const outputLength = Math.max(
            1,
            Math.round(input.length * TARGET_SAMPLE_RATE / inputRate)
        );
        const output = new Float32Array(outputLength);
        const scale = inputRate / TARGET_SAMPLE_RATE;
        for (let index = 0; index < outputLength; index += 1) {
            const position = index * scale;
            const left = Math.min(Math.floor(position), input.length - 1);
            const right = Math.min(left + 1, input.length - 1);
            const fraction = position - left;
            output[index] = input[left] + (input[right] - input[left]) * fraction;
        }
        return output;
    };

    const encodePcm16Base64 = (samples) => {
        const bytes = new Uint8Array(samples.length * 2);
        const view = new DataView(bytes.buffer);
        for (let index = 0; index < samples.length; index += 1) {
            const sample = Math.max(-1, Math.min(1, samples[index]));
            const value = sample < 0 ? sample * 0x8000 : sample * 0x7fff;
            view.setInt16(index * 2, Math.round(value), true);
        }
        let binary = '';
        for (let offset = 0; offset < bytes.length; offset += 0x8000) {
            binary += String.fromCharCode(...bytes.subarray(offset, offset + 0x8000));
        }
        return btoa(binary);
    };

    const buildItemTranscript = (item) => {
        const committed = String(item?.committed || '').trim();
        const tail = String(item?.final || item?.delta || '').trim();
        if (!committed) return tail;
        if (!tail || tail === committed || tail.startsWith(`${committed} `)) {
            return tail || committed;
        }
        return `${committed} ${tail}`;
    };

    const buildTranscript = (session) => Array.from(session.items.values())
        .map(buildItemTranscript)
        .filter(Boolean)
        .join(' ')
        .replace(/\s+/g, ' ')
        .trim();

    const stopCapture = (session) => {
        if (!session || session.captureStopped) {
            return;
        }
        session.captureStopped = true;
        if (session.limitTimer) {
            clearTimeout(session.limitTimer);
            session.limitTimer = null;
        }
        try {
            session.source?.disconnect();
        } catch (_) {}
        try {
            session.processor?.disconnect();
        } catch (_) {}
        try {
            session.silentGain?.disconnect();
        } catch (_) {}
        session.processor = null;
        session.source = null;
        session.silentGain = null;
        session.stream?.getTracks().forEach((track) => track.stop());
        if (session.audioContext && typeof session.audioContext.close === 'function') {
            session.audioContext.close().catch(() => {});
        }
        session.audioContext = null;
    };

    const closeSession = (session, { sendClose = true } = {}) => {
        stopCapture(session);
        if (session.readyTimer) {
            clearTimeout(session.readyTimer);
            session.readyTimer = null;
        }
        if (session.completionTimer) {
            clearTimeout(session.completionTimer);
            session.completionTimer = null;
        }
        if (sendClose && session.socket?.readyState === WebSocket.OPEN) {
            session.socket.send(JSON.stringify({ type: 'close' }));
        }
        try {
            session.socket?.close();
        } catch (_) {}
        if (activeSession === session) {
            activeSession = null;
        }
    };

    const createError = (code, detail = null) => {
        const error = new Error(code || 'live_transcription_failed');
        error.code = code || 'live_transcription_failed';
        error.detail = detail;
        error.isDictationRateLimit = code === 'user_dictation_rate_limited';
        error.isDictationInProgress = code === 'user_dictation_in_progress';
        error.isProviderRateLimit = code === 'provider_rate_limited';
        return error;
    };

    /**
     * Return whether a live startup failure can safely restart as a file
     * recording. Failures that also block microphone capture, represent an
     * application quota decision, or mean another local controller owns the
     * microphone must stay terminal. Provider and transport failures are safe
     * to retry because no live PCM has been accepted before start() resolves.
     */
    const shouldFallbackToFile = (error) => {
        const code = String(error?.code || '').trim();
        const name = String(error?.name || '').trim();
        if (
            error?.isDictationRateLimit
            || error?.isDictationInProgress
            || code === 'already_active'
            || code === 'cancelled'
        ) {
            return false;
        }
        return ![
            'AbortError',
            'NotAllowedError',
            'NotFoundError',
            'NotReadableError',
            'SecurityError',
        ].includes(name);
    };

    const beginAudioCapture = async (session, maxDurationSeconds) => {
        const AudioContextCtor = global.AudioContext || global.webkitAudioContext;
        const audioContext = new AudioContextCtor({ sampleRate: TARGET_SAMPLE_RATE });
        if (audioContext.state === 'suspended') {
            await audioContext.resume();
        }
        const source = audioContext.createMediaStreamSource(session.stream);
        // ScriptProcessor remains the most compatible code-free PCM capture
        // path for Omlorix's static frontend. The zero-gain output keeps browser
        // processing active without echoing microphone audio to the speakers.
        const processor = audioContext.createScriptProcessor(4096, 1, 1);
        const silentGain = audioContext.createGain();
        silentGain.gain.value = 0;
        processor.onaudioprocess = (event) => {
            if (
                session.captureStopped
                || session.socket.readyState !== WebSocket.OPEN
                || session.commitSent
            ) {
                return;
            }
            const input = event.inputBuffer.getChannelData(0);
            const samples = resample(input, audioContext.sampleRate);
            if (!samples.length) {
                return;
            }
            session.socket.send(JSON.stringify({
                type: 'audio',
                audio: encodePcm16Base64(samples),
            }));
        };
        source.connect(processor);
        processor.connect(silentGain);
        silentGain.connect(audioContext.destination);
        session.audioContext = audioContext;
        session.source = source;
        session.processor = processor;
        session.silentGain = silentGain;

        if (Number.isFinite(maxDurationSeconds) && maxDurationSeconds > 0) {
            session.limitTimer = setTimeout(() => {
                session.callbacks.onLimit?.();
                stop().catch(() => {});
            }, Math.max(1, maxDurationSeconds) * 1000);
        }
    };

    const start = async (callbacks = {}) => {
        if (!isReady()) {
            throw createError('configuration_unavailable');
        }
        if (!isSupported()) {
            throw createError('browser_unsupported');
        }
        if (activeSession) {
            throw createError('already_active');
        }

        const stream = await navigator.mediaDevices.getUserMedia({
            audio: {
                channelCount: 1,
                echoCancellation: true,
                noiseSuppression: true,
                autoGainControl: true,
            },
        });
        const socket = new WebSocket(buildWebSocketUrl());
        const session = {
            socket,
            stream,
            callbacks,
            items: new Map(),
            captureStopped: false,
            commitSent: false,
            cancelled: false,
            completed: false,
            readyTimer: null,
            completionTimer: null,
            limitTimer: null,
            resolveReady: null,
            rejectReady: null,
            resolveStop: null,
            rejectStop: null,
            stopPromise: null,
            audioContext: null,
            source: null,
            processor: null,
            silentGain: null,
        };
        activeSession = session;

        const readyPromise = new Promise((resolve, reject) => {
            session.resolveReady = resolve;
            session.rejectReady = reject;
        });
        session.readyTimer = setTimeout(() => {
            const error = createError('connection_timeout');
            session.rejectReady?.(error);
            callbacks.onError?.(error);
            closeSession(session);
        }, READY_TIMEOUT_MS);

        socket.addEventListener('message', async (event) => {
            let payload;
            try {
                payload = JSON.parse(event.data);
            } catch (_) {
                return;
            }
            const type = String(payload?.type || '');
            if (type === 'ready') {
                clearTimeout(session.readyTimer);
                session.readyTimer = null;
                try {
                    await beginAudioCapture(
                        session,
                        Number(payload.max_duration_seconds)
                    );
                    session.resolveReady?.({ stream });
                    callbacks.onStateChange?.('recording');
                } catch (error) {
                    session.rejectReady?.(error);
                    callbacks.onError?.(error);
                    closeSession(session);
                }
                return;
            }
            if (type === 'transcript.delta') {
                const itemId = String(payload.item_id || 'default');
                const item = session.items.get(itemId) || { delta: '', final: '' };
                item.delta += String(payload.delta || '');
                session.items.set(itemId, item);
                callbacks.onPartial?.(buildTranscript(session));
                return;
            }
            if (type === 'transcript.updated') {
                const itemId = String(payload.item_id || 'default');
                const item = session.items.get(itemId) || {
                    committed: '',
                    delta: '',
                    final: '',
                };
                const transcript = String(payload.transcript || '').trim();
                // xAI emits each evolving chunk as a replacement, then marks
                // it final before starting a new chunk. Keep locked chunks in
                // a separate prefix so the next sentence cannot replace the
                // text that was already finalized.
                if (payload.is_final) {
                    item.committed = [item.committed, transcript]
                        .filter(Boolean)
                        .join(' ');
                    item.delta = '';
                } else {
                    item.delta = transcript;
                }
                session.items.set(itemId, item);
                callbacks.onPartial?.(buildTranscript(session));
                return;
            }
            if (type === 'transcript.completed') {
                const itemId = String(payload.item_id || 'default');
                const item = session.items.get(itemId) || { delta: '', final: '' };
                item.final = String(
                    payload.transcript || item.committed || item.delta || '',
                ).trim();
                session.items.set(itemId, item);
                session.completed = true;
                const transcript = buildTranscript(session);
                callbacks.onFinal?.(transcript);
                session.resolveStop?.(transcript);
                closeSession(session);
                return;
            }
            if (type === 'limit_reached') {
                callbacks.onLimit?.();
                stop().catch(() => {});
                return;
            }
            if (type === 'error') {
                const error = createError(String(payload.code || 'provider_error'), payload.detail);
                session.rejectReady?.(error);
                session.rejectStop?.(error);
                callbacks.onError?.(error);
                closeSession(session);
            }
        });
        socket.addEventListener('error', () => {
            const error = createError('connection_failed');
            session.rejectReady?.(error);
            session.rejectStop?.(error);
            callbacks.onError?.(error);
            closeSession(session, { sendClose: false });
        });
        socket.addEventListener('close', () => {
            if (session.cancelled || session.completed || activeSession !== session) {
                return;
            }
            const error = createError('connection_closed');
            session.rejectReady?.(error);
            session.rejectStop?.(error);
            callbacks.onError?.(error);
            closeSession(session, { sendClose: false });
        });

        return readyPromise;
    };

    const stop = () => {
        const session = activeSession;
        if (!session) {
            return Promise.resolve('');
        }
        if (session.stopPromise) {
            return session.stopPromise;
        }
        stopCapture(session);
        session.commitSent = true;
        session.callbacks.onStateChange?.('transcribing');
        session.stopPromise = new Promise((resolve, reject) => {
            session.resolveStop = resolve;
            session.rejectStop = reject;
        });
        if (session.socket.readyState === WebSocket.OPEN) {
            session.socket.send(JSON.stringify({ type: 'commit' }));
        } else {
            const error = createError('connection_closed');
            session.rejectStop(error);
            closeSession(session, { sendClose: false });
        }
        session.completionTimer = setTimeout(() => {
            const error = createError('completion_timeout');
            session.rejectStop?.(error);
            session.callbacks.onError?.(error);
            closeSession(session);
        }, COMPLETION_TIMEOUT_MS);
        return session.stopPromise;
    };

    const cancel = () => {
        const session = activeSession;
        if (!session) {
            return;
        }
        session.cancelled = true;
        session.rejectStop?.(createError('cancelled'));
        closeSession(session);
        session.callbacks.onStateChange?.('idle');
    };

    global.LiveTranscription = {
        isReady,
        isSupported,
        isActive: () => Boolean(activeSession),
        shouldFallbackToFile,
        start,
        stop,
        cancel,
    };
})(window);
