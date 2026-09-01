(() => {
    'use strict';

    const CALL_ICON_START = Icons.speech;
    const CALL_ICON_STOP = Icons.stop;
    const CALL_ROUTE_PATH = '/call';
    const SILENT_WAV = 'data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YQAAAAA=';
    // Google explicitly does not guarantee transcription ordering relative to
    // turnComplete. Keep a small grace period so the trailing transcript
    // message can join the turn before it is persisted and reset.
    const GOOGLE_TRANSCRIPTION_SETTLE_MS = 300;
    // The backend may spend up to five seconds completing the provider
    // WebSocket close handshake before it releases the persisted connection
    // slot. Keep replacement attempts alive beyond that server-side bound.
    const GOOGLE_LIVE_PROXY_RELEASE_TIMEOUT_MS = 7000;
    const REALTIME_TITLE_POLL_INITIAL_MS = 750;
    const REALTIME_TITLE_POLL_MAX_MS = 5000;
    const REALTIME_TITLE_POLL_ATTEMPTS = 12;
    const REALTIME_CALL_CAPTIONS_STORAGE_KEY = 'realtime_call_show_captions';
    const REALTIME_CALL_VIEW_STORAGE_KEY = 'realtime_call_view_mode';
    const REALTIME_ORB_TAU = Math.PI * 2;
    const REALTIME_ORB_GOLDEN_ANGLE = Math.PI * (3 - Math.sqrt(5));
    const REALTIME_ORB_TILT = 0.45;
    const REALTIME_ORB_PERSPECTIVE = 2.6;
    const REALTIME_ORB_STATE_TRANSITION_MS = 700;
    const REALTIME_ORB_PROFILE_PROPERTIES = Object.freeze([
        'rotationSpeed',
        'baseAmplitude',
        'levelAmplitude',
        'noiseSpeed',
        'pulse',
        'minLevel',
    ]);
    const REALTIME_ORB_PROFILES = Object.freeze({
        idle:       { rotationSpeed: 0.25, baseAmplitude: 0.045, levelAmplitude: 0.10, noiseSpeed: 1.1, pulse: 0,     minLevel: 0.10 },
        connecting: { rotationSpeed: 0.50, baseAmplitude: 0.050, levelAmplitude: 0.06, noiseSpeed: 1.4, pulse: 0.045, minLevel: 0.18 },
        listening:  { rotationSpeed: 0.55, baseAmplitude: 0.050, levelAmplitude: 0.26, noiseSpeed: 2.3, pulse: 0,     minLevel: 0.04 },
        thinking:   { rotationSpeed: 2.30, baseAmplitude: 0.060, levelAmplitude: 0.12, noiseSpeed: 3.2, pulse: 0,     minLevel: 0.30 },
        speaking:   { rotationSpeed: 1.05, baseAmplitude: 0.055, levelAmplitude: 0.32, noiseSpeed: 2.7, pulse: 0,     minLevel: 0.12 },
    });

    function t(key, fallback) {
        if (typeof window !== 'undefined' && typeof window.getTranslation === 'function') {
            return window.getTranslation(key, fallback);
        }
        return fallback;
    }

    function formatT(key, fallback, vars = {}) {
        if (typeof window !== 'undefined' && typeof window.formatTranslation === 'function') {
            return window.formatTranslation(key, fallback, vars);
        }
        return String(t(key, fallback)).replace(/\{(\w+)\}/g, (_, token) => {
            const value = vars?.[token];
            return value === undefined || value === null ? '' : String(value);
        });
    }

    function createRealtimeTurnId() {
        if (window.crypto && typeof window.crypto.randomUUID === 'function') {
            return window.crypto.randomUUID();
        }
        const random = Math.random().toString(36).slice(2, 10);
        return `realtime-turn-${Date.now()}-${random}`;
    }

    function createEmptyTurn() {
        return {
            turnId: createRealtimeTurnId(),
            userTranscript: '',
            assistantTranscript: '',
            fileIds: [],
            interrupted: false,
            usage: null,
            providerInteractions: [],
            persistPromise: null,
            responseId: null,
            responseDone: false,
            responseHasFunctionCall: false,
            outputAudioStarted: false,
            renderedToolCallIds: new Set(),
            handledToolCallIds: new Set(),
            pendingToolCalls: new Map(),
        };
    }

    const state = {
        active: false,
        ready: false,
        connecting: false,
        stopping: false,
        transport: null,
        provider: null,
        protocolVersion: null,
        sessionId: null,
        chatId: null,
        signalingUrl: null,
        websocketUrl: null,
        sessionConfig: null,
        pc: null,
        dc: null,
        ws: null,
        localStream: null,
        remoteStream: null,
        remoteAudio: null,
        isMuted: false,
        activity: 'idle',
        assistantSpeaking: false,
        currentAssistantItemId: null,
        currentTurn: createEmptyTurn(),
        ignoredResponseIds: new Set(),
        providerEventQueue: Promise.resolve(),
        pendingRemotePlayback: false,
        audioUnlockPromise: null,
        audioOutputUnlocked: false,
        outputAudioContext: null,
        routeModeActive: false,
        routeReturnChatId: null,
        routeAutostartPending: false,
        routeAutostartPromise: null,
        startAttemptId: 0,
        callButton: null,
        googleSessionHandle: null,
        googleReconnectInFlight: false,
        googleReconnectAttempts: 0,
        googlePendingReconnectReason: null,
        googleGoAwaySeen: false,
        googleTurnCompleteTimer: null,
        googleTurnCompleteOrigin: null,
        googleToolCallControllers: new Map(),
        googleCancelledToolCallIds: new Set(),
        pendingTitlePollChatIds: new Set(),
        heartbeatTimer: null,
        heartbeatInFlightSessionId: null,
        sessionLimitTimer: null,
        sessionExpiresAt: null,
        sessionLimitSource: 'provider',
        peerDisconnectTimer: null,
        googleMicAudioContext: null,
        googleMicSource: null,
        googleMicProcessor: null,
        googleMicSilenceGain: null,
        googlePlaybackCursorTime: 0,
        googlePlaybackSources: new Set(),
        liveUserMessageId: null,
        liveAssistantMessageId: null,
        liveUserMessageContent: null,
        liveAssistantMessageContent: null,
        liveUserMessageArea: null,
        liveUserColumnWrapper: null,
        liveAssistantContainer: null,
        callViewMode: 'orb',
        callCaptionsVisible: true,
        callUi: null,
        callOrb: null,
        callOrbAudio: null,
    };

    /** Read a boolean preference without making private browsing storage fatal. */
    function readCallBooleanPreference(key, fallback) {
        try {
            const value = localStorage.getItem(key);
            return value === null ? fallback : value === 'true';
        } catch (_) {
            return fallback;
        }
    }

    /** Persist a call preference on a best-effort basis. */
    function writeCallBooleanPreference(key, value) {
        try {
            localStorage.setItem(key, String(Boolean(value)));
        } catch (_) {
            // Storage can be blocked or full; the current call still works.
        }
    }

    /** Read the last explicitly selected call view, defaulting new users to the orb. */
    function readCallViewPreference() {
        try {
            return localStorage.getItem(REALTIME_CALL_VIEW_STORAGE_KEY) === 'text' ? 'text' : 'orb';
        } catch (_) {
            return 'orb';
        }
    }

    /** Persist the selected call view without making storage availability a requirement. */
    function writeCallViewPreference(mode) {
        try {
            localStorage.setItem(REALTIME_CALL_VIEW_STORAGE_KEY, mode);
        } catch (_) {
            // Storage can be blocked or full; keep the selected view for this call.
        }
    }

    /** Return the translated visual label for the current realtime activity. */
    function getCallStatusLabel(activity, connecting = state.connecting) {
        if (connecting) return t('call_status_connecting', 'Connecting…');
        const labels = {
            idle: t('call_status_idle', 'Say something…'),
            listening: t('call_status_listening', 'Listening…'),
            thinking: t('call_status_thinking', 'Thinking…'),
            speaking: t('call_status_speaking', 'Speaking…'),
        };
        return labels[activity] || labels.idle;
    }

    /**
     * Build the dedicated call surface once and keep it mounted for later calls.
     * The shared chat transcript remains the source of truth; this surface is a
     * presentation layer that can be shown and hidden without moving messages.
     */
    function ensureCallSurface() {
        if (state.callUi?.surface?.isConnected) return state.callUi;

        const chatContainerMain = document.getElementById('chatContainerMain');
        const headerActions = document.querySelector('.main-header-actions');
        if (!chatContainerMain || !headerActions) return null;

        headerActions.querySelector('#realtimeCallHeaderControls')?.remove();

        const headerControls = document.createElement('div');
        headerControls.className = 'realtime-call-header-controls';
        headerControls.id = 'realtimeCallHeaderControls';

        const captionsButton = document.createElement('button');
        captionsButton.type = 'button';
        captionsButton.className = 'om-button realtime-call-header-button';
        captionsButton.id = 'realtimeCallCaptionsButton';
        captionsButton.setAttribute('aria-pressed', 'true');

        const viewButton = document.createElement('button');
        viewButton.type = 'button';
        viewButton.className = 'om-button realtime-call-header-button';
        viewButton.id = 'realtimeCallViewButton';

        headerControls.append(captionsButton, viewButton);
        headerActions.prepend(headerControls);

        const surface = document.createElement('section');
        surface.className = 'realtime-call-surface';
        surface.id = 'realtimeCallSurface';
        surface.innerHTML = `
            <div class="realtime-call-stage">
                <canvas class="realtime-call-orb" id="realtimeCallOrb" role="img"></canvas>
                <p class="realtime-call-status" id="realtimeCallStatus" role="status" aria-live="polite"></p>
                <div class="realtime-call-transcript" id="realtimeCallTranscript" role="status" aria-live="polite" aria-atomic="false" hidden><p id="realtimeCallTranscriptText"></p></div>
            </div>
            <footer class="realtime-call-controls">
                <button type="button" class="realtime-call-control-button" id="realtimeCallMuteButton" aria-pressed="false"></button>
                <button type="button" class="realtime-call-control-button is-end" id="realtimeCallEndButton"></button>
            </footer>
        `;
        chatContainerMain.insertBefore(surface, chatContainerMain.firstChild);

        state.callCaptionsVisible = readCallBooleanPreference(
            REALTIME_CALL_CAPTIONS_STORAGE_KEY,
            true,
        );
        state.callUi = {
            surface,
            headerControls,
            captionsButton,
            viewButton,
            canvas: surface.querySelector('#realtimeCallOrb'),
            status: surface.querySelector('#realtimeCallStatus'),
            transcript: surface.querySelector('#realtimeCallTranscript'),
            transcriptText: surface.querySelector('#realtimeCallTranscriptText'),
            muteButton: surface.querySelector('#realtimeCallMuteButton'),
            endButton: surface.querySelector('#realtimeCallEndButton'),
            statusTimer: null,
        };

        viewButton.addEventListener('click', () => {
            setCallViewMode(state.callViewMode === 'orb' ? 'text' : 'orb', { focus: true });
        });
        captionsButton.addEventListener('click', () => {
            state.callCaptionsVisible = !state.callCaptionsVisible;
            writeCallBooleanPreference(REALTIME_CALL_CAPTIONS_STORAGE_KEY, state.callCaptionsVisible);
            syncCallSurfaceState();
        });
        state.callUi.muteButton.addEventListener('click', () => {
            if (state.active) toggleMute();
        });
        state.callUi.endButton.addEventListener('click', async () => {
            if (state.callUi?.endButton) state.callUi.endButton.disabled = true;
            try {
                if (state.active || state.connecting) {
                    await stop({ reason: 'call_surface_end_button' });
                } else {
                    deactivateCallRoute({ restorePath: true, replace: true });
                }
            } finally {
                if (state.callUi?.endButton) state.callUi.endButton.disabled = false;
            }
        });

        initializeCallOrb(state.callUi.canvas);
        syncCallSurfaceTranslations();
        syncCallSurfaceState();
        return state.callUi;
    }

    /** Refresh every call-surface string after initial i18n load or a locale change. */
    function syncCallSurfaceTranslations() {
        const ui = state.callUi;
        if (!ui) return;

        const switchingToText = state.callViewMode === 'orb';
        const viewLabel = switchingToText
            ? t('call_switch_to_text', 'Switch to text view')
            : t('call_switch_to_orb', 'Switch to orb view');
        setCallButtonIcon(ui.viewButton, switchingToText ? Icons.textLines : Icons.speech);
        ui.viewButton.setAttribute('aria-label', viewLabel);
        ui.viewButton.title = viewLabel;

        const captionsLabel = state.callCaptionsVisible
            ? t('call_hide_transcript', 'Hide live transcript')
            : t('call_show_transcript', 'Show live transcript');
        setCallButtonIcon(ui.captionsButton, Icons.captions);
        ui.captionsButton.setAttribute('aria-label', captionsLabel);
        ui.captionsButton.title = captionsLabel;

        const muteLabel = state.isMuted
            ? t('chat_call_unmute_microphone', 'Unmute microphone')
            : t('chat_call_mute_microphone', 'Mute microphone');
        ui.muteButton.setAttribute('aria-label', muteLabel);
        ui.muteButton.title = muteLabel;
        ui.endButton.setAttribute('aria-label', t('chat_call_end', 'End call'));
        ui.endButton.title = t('chat_call_end', 'End call');
        ui.surface.setAttribute('aria-label', t('call_surface_aria', 'Realtime voice call'));
        ui.canvas.setAttribute('aria-label', t('call_orb_aria', 'Animated orb visualizing the current voice call state'));
        ui.transcript.setAttribute('aria-label', t('call_transcript_aria', 'Live assistant transcript'));
    }

    /** Insert only a trusted SVG; the translated accessible name lives in ARIA and title. */
    function setCallButtonIcon(button, icon) {
        if (!button) return;
        button.innerHTML = icon;
    }

    /** Paint controls, caption visibility, and activity after any state change. */
    function syncCallSurfaceState() {
        const ui = state.callUi;
        if (!ui) return;

        ui.surface.classList.toggle('is-transcript-visible', state.callCaptionsVisible);
        ui.captionsButton.setAttribute('aria-pressed', String(state.callCaptionsVisible));
        ui.captionsButton.hidden = state.callViewMode !== 'orb';
        ui.muteButton.classList.toggle('is-muted', state.isMuted);
        ui.muteButton.setAttribute('aria-pressed', String(state.isMuted));
        ui.muteButton.innerHTML = state.isMuted ? Icons.microphoneMute : Icons.microphone;
        ui.muteButton.disabled = !state.active;
        ui.endButton.innerHTML = Icons.close;
        syncCallSurfaceTranslations();
        updateCallOrbState(state.connecting ? 'connecting' : state.activity);
    }

    /** Fade between short status labels without moving the orb. */
    function updateCallStatus({ immediate = false } = {}) {
        const ui = state.callUi;
        if (!ui?.status) return;
        const nextLabel = getCallStatusLabel(state.activity, state.connecting);
        window.clearTimeout(ui.statusTimer);
        if (!immediate && ui.status.textContent === nextLabel && !ui.status.classList.contains('is-changing')) {
            return;
        }
        if (immediate || ui.status.textContent === '') {
            ui.status.textContent = nextLabel;
            ui.status.classList.remove('is-changing');
            return;
        }
        ui.status.classList.add('is-changing');
        ui.statusTimer = window.setTimeout(() => {
            ui.status.textContent = nextLabel;
            ui.status.classList.remove('is-changing');
        }, 140);
    }

    /** Show the latest model transcript below the orb when captions are enabled. */
    function updateCallTranscript(text) {
        const ui = state.callUi;
        if (!ui?.transcript || !ui.transcriptText) return;
        const normalized = String(text || '').trim();
        ui.transcriptText.textContent = normalized;
        ui.transcript.hidden = !normalized;
        if (normalized) {
            ui.transcript.scrollTop = ui.transcript.scrollHeight;
        }
    }

    /** Switch between the visual call surface and the complete chat transcript. */
    function setCallViewMode(mode, { focus = false } = {}) {
        const normalizedMode = mode === 'text' ? 'text' : 'orb';
        state.callViewMode = normalizedMode;
        writeCallViewPreference(normalizedMode);
        document.body.classList.toggle('realtime-call-text-mode', normalizedMode === 'text');
        syncCallSurfaceState();

        if (normalizedMode === 'orb') {
            requestAnimationFrame(() => {
                resizeCallOrb();
                syncCallOrbLoop();
                if (focus && window.matchMedia?.('(max-width: 600px)').matches) {
                    state.callUi?.muteButton?.focus();
                }
            });
            return;
        }

        syncCallOrbLoop();
        scrollChatAreaToBottom();
        if (focus && typeof window.focusChatInput === 'function') {
            window.focusChatInput();
        }
    }

    /** Enter the route UI, restoring the user's last view or defaulting to the orb. */
    function enterCallRouteUi({ resetMode = false } = {}) {
        const ui = ensureCallSurface();
        if (!ui) return;
        document.body.classList.add('realtime-call-route');
        if (resetMode) {
            state.callViewMode = readCallViewPreference();
            // A reusable surface must never show captions from an earlier call.
            updateCallTranscript('');
        }
        document.body.classList.toggle('realtime-call-text-mode', state.callViewMode === 'text');
        syncCallSurfaceState();
        requestAnimationFrame(() => {
            resizeCallOrb();
            syncCallOrbLoop();
        });
    }

    /** Remove route-only classes while leaving the reusable call DOM mounted. */
    function leaveCallRouteUi() {
        document.body.classList.remove('realtime-call-route', 'realtime-call-text-mode');
        syncCallOrbLoop();
    }

    /** Build a resize-aware Fibonacci sphere renderer from the standalone demo. */
    function initializeCallOrb(canvas) {
        if (!canvas || state.callOrb) return;
        const context = canvas.getContext('2d');
        if (!context) return;
        const motionQuery = typeof window.matchMedia === 'function'
            ? window.matchMedia('(prefers-reduced-motion: reduce)')
            : { matches: false };
        state.callOrb = {
            canvas,
            context,
            motionQuery,
            state: 'idle',
            profile: { ...REALTIME_ORB_PROFILES.idle },
            profileTransition: null,
            level: 0,
            rotationAngle: 0,
            noisePhase: 0,
            particles: [],
            cssSize: 0,
            dpr: 1,
            radius: 0,
            centerX: 0,
            centerY: 0,
            dotColor: '#77777b',
            reducedMotion: false,
            animationFrame: null,
            startedAt: performance.now(),
            lastFrameAt: performance.now(),
            resizeObserver: null,
        };

        if (typeof ResizeObserver === 'function') {
            state.callOrb.resizeObserver = new ResizeObserver(resizeCallOrb);
            state.callOrb.resizeObserver.observe(canvas);
        } else {
            window.addEventListener('resize', resizeCallOrb, { passive: true });
        }
        motionQuery.addEventListener?.('change', resolveCallOrbMotionPreference);
        document.addEventListener('visibilitychange', syncCallOrbLoop);
        new MutationObserver(() => {
            resolveCallOrbColor();
        }).observe(document.documentElement, {
            attributes: true,
            attributeFilter: ['data-mode'],
        });
        resizeCallOrb();
        resolveCallOrbColor();
        resolveCallOrbMotionPreference();
    }

    /** Rebuild the particle density whenever responsive layout changes the canvas. */
    function resizeCallOrb() {
        const orb = state.callOrb;
        if (!orb) return;
        const rect = orb.canvas.getBoundingClientRect();
        const cssSize = Math.max(120, Math.min(rect.width, rect.height) || 280);
        const dpr = Math.min(window.devicePixelRatio || 1, 2);
        if (orb.cssSize === cssSize && orb.dpr === dpr && orb.canvas.width) return;

        orb.cssSize = cssSize;
        orb.dpr = dpr;
        orb.canvas.width = Math.round(cssSize * dpr);
        orb.canvas.height = Math.round(cssSize * dpr);
        orb.centerX = orb.canvas.width / 2;
        orb.centerY = orb.canvas.height / 2;
        orb.radius = cssSize * dpr * 0.38;
        const count = Math.round(Math.min(2400, Math.max(720, cssSize * 6)));
        orb.particles = Array.from({ length: count }, (_, index) => {
            const y = 1 - (index / Math.max(1, count - 1)) * 2;
            const ringRadius = Math.sqrt(Math.max(0, 1 - y * y));
            const theta = index * REALTIME_ORB_GOLDEN_ANGLE;
            return {
                x: Math.cos(theta) * ringRadius,
                y,
                z: Math.sin(theta) * ringRadius,
                phase: Math.random() * REALTIME_ORB_TAU,
            };
        });
        if (orb.reducedMotion) drawStaticCallOrb();
    }

    /** Resolve the orb particle color through the current Omlorix theme tokens. */
    function resolveCallOrbColor() {
        const orb = state.callOrb;
        if (!orb) return;
        orb.dotColor = getComputedStyle(orb.canvas).color || orb.dotColor;
        if (orb.reducedMotion) drawStaticCallOrb();
    }

    /** Honor the operating-system reduced-motion preference exposed by the browser. */
    function resolveCallOrbMotionPreference() {
        const orb = state.callOrb;
        if (!orb) return;
        orb.reducedMotion = Boolean(orb.motionQuery.matches);
        if (orb.reducedMotion) {
            // A reduced-motion frame should represent the current status, not
            // a partially blended frame captured when animation was disabled.
            orb.profile = { ...(REALTIME_ORB_PROFILES[orb.state] || REALTIME_ORB_PROFILES.idle) };
            orb.profileTransition = null;
        }
        syncCallOrbLoop();
    }

    /** Read the microphone waveform and normalize normal speech to 0…1. */
    function readCallMicrophoneLevel() {
        const audio = state.callOrbAudio;
        if (!audio?.analyser || !audio.timeData || state.isMuted) return null;
        audio.analyser.getByteTimeDomainData(audio.timeData);
        let sum = 0;
        for (const sample of audio.timeData) {
            const centered = (sample - 128) / 128;
            sum += centered * centered;
        }
        const rms = Math.sqrt(sum / audio.timeData.length);
        return Math.min(1, Math.max(0, (rms - 0.018) * 7));
    }

    /** Return real microphone energy or a restrained state-specific fallback. */
    function getCallOrbTargetLevel(timeSeconds) {
        const orb = state.callOrb;
        if (!orb) return 0;
        if (orb.state === 'speaking') {
            const gate = Math.max(0, Math.sin(timeSeconds * 1.7) + Math.sin(timeSeconds * 2.9) * 0.5) / 1.5;
            const wobble = 0.55 + 0.45 * Math.sin(timeSeconds * 13 + Math.sin(timeSeconds * 4.2) * 2);
            return Math.min(1, 0.15 + gate * wobble * 0.85);
        }
        if (orb.state === 'listening') {
            const microphoneLevel = readCallMicrophoneLevel();
            if (microphoneLevel !== null) return microphoneLevel;
            return state.isMuted ? 0 : 0.10 + 0.06 * (0.5 + 0.5 * Math.sin(timeSeconds * 2.3) * Math.sin(timeSeconds * 0.7));
        }
        if (orb.state === 'thinking') return 0.35;
        if (orb.state === 'connecting') return 0.20;
        return 0.10;
    }

    /** Use a zero-slope curve at both ends so profile changes never snap. */
    function easeCallOrbStateTransition(progress) {
        const bounded = Math.min(1, Math.max(0, progress));
        return bounded * bounded * (3 - 2 * bounded);
    }

    /**
     * Blend every motion parameter from the last rendered frame. Keeping the
     * rendered profile as the transition origin also makes rapid provider
     * state changes continuous instead of restarting from an obsolete state.
     */
    function advanceCallOrbProfile(deltaSeconds) {
        const orb = state.callOrb;
        if (!orb?.profileTransition) return orb?.profile || REALTIME_ORB_PROFILES.idle;

        const transition = orb.profileTransition;
        transition.elapsedMs = Math.min(
            REALTIME_ORB_STATE_TRANSITION_MS,
            transition.elapsedMs + Math.max(0, deltaSeconds) * 1000,
        );
        const progress = easeCallOrbStateTransition(
            transition.elapsedMs / REALTIME_ORB_STATE_TRANSITION_MS,
        );
        for (const property of REALTIME_ORB_PROFILE_PROPERTIES) {
            const start = transition.from[property];
            orb.profile[property] = start + (transition.to[property] - start) * progress;
        }

        if (transition.elapsedMs >= REALTIME_ORB_STATE_TRANSITION_MS) {
            orb.profile = { ...transition.to };
            orb.profileTransition = null;
        }
        return orb.profile;
    }

    /** Draw one orb frame with depth, rotation, and audio-reactive displacement. */
    function drawCallOrb(timeSeconds, deltaSeconds) {
        const orb = state.callOrb;
        if (!orb) return;
        const profile = advanceCallOrbProfile(deltaSeconds);
        const target = Math.max(profile.minLevel, getCallOrbTargetLevel(timeSeconds));
        const easing = target > orb.level ? 12 : 4;
        orb.level += (target - orb.level) * Math.min(1, easing * deltaSeconds);
        const amplitude = profile.baseAmplitude + orb.level * profile.levelAmplitude;
        const pulseScale = profile.pulse
            ? 1 + Math.sin(timeSeconds * 3.2) * profile.pulse
            : 1;
        // Integrating phase avoids the position jump caused by multiplying
        // absolute time by a newly selected speed on every status change.
        orb.rotationAngle = (orb.rotationAngle + profile.rotationSpeed * deltaSeconds) % REALTIME_ORB_TAU;
        orb.noisePhase = (orb.noisePhase + profile.noiseSpeed * deltaSeconds) % REALTIME_ORB_TAU;
        const cosY = Math.cos(orb.rotationAngle);
        const sinY = Math.sin(orb.rotationAngle);
        const cosX = Math.cos(REALTIME_ORB_TILT);
        const sinX = Math.sin(REALTIME_ORB_TILT);
        const noiseTime = orb.noisePhase;
        const context = orb.context;

        context.clearRect(0, 0, orb.canvas.width, orb.canvas.height);
        context.fillStyle = orb.dotColor;
        const baseDot = Math.max(0.8, orb.radius * 0.008);
        for (const particle of orb.particles) {
            const rotatedX = particle.x * cosY - particle.z * sinY;
            const rotatedZ = particle.x * sinY + particle.z * cosY;
            const tiltedY = particle.y * cosX - rotatedZ * sinX;
            const tiltedZ = particle.y * sinX + rotatedZ * cosX;
            const noise = Math.sin(particle.phase + noiseTime)
                * Math.sin(rotatedX * 2.1 + noiseTime * 0.6)
                * Math.sin(tiltedY * 2.3 - noiseTime * 0.8);
            const displacement = (1 + noise * amplitude) * pulseScale;
            const scale = REALTIME_ORB_PERSPECTIVE
                / (REALTIME_ORB_PERSPECTIVE - tiltedZ * displacement);
            const screenX = orb.centerX + rotatedX * displacement * orb.radius * scale * 0.92;
            const screenY = orb.centerY + tiltedY * displacement * orb.radius * scale * 0.92;
            const depth = (tiltedZ * displacement + 1) / 2;
            context.globalAlpha = 0.25 + 0.55 * depth;
            context.beginPath();
            context.arc(screenX, screenY, baseDot * (0.6 + 0.5 * depth) * scale, 0, REALTIME_ORB_TAU);
            context.fill();
        }
        context.globalAlpha = 1;
    }

    /** Keep a useful static sphere visible when animation is reduced. */
    function drawStaticCallOrb() {
        const orb = state.callOrb;
        if (!orb) return;
        const previousLevel = orb.level;
        orb.level = 0.25;
        drawCallOrb(1.2, 0);
        orb.level = previousLevel;
    }

    /** Run exactly one requestAnimationFrame at a time while orb mode is visible. */
    function runCallOrbFrame(now) {
        const orb = state.callOrb;
        if (!orb) return;
        orb.animationFrame = null;
        const timeSeconds = (now - orb.startedAt) / 1000;
        const deltaSeconds = Math.min(0.1, (now - orb.lastFrameAt) / 1000);
        orb.lastFrameAt = now;
        drawCallOrb(timeSeconds, deltaSeconds);
        syncCallOrbLoop();
    }

    /** Pause rendering off-route, in text mode, in background tabs, or for reduced motion. */
    function syncCallOrbLoop() {
        const orb = state.callOrb;
        if (!orb) return;
        const shouldRun = document.body.classList.contains('realtime-call-route')
            && state.callViewMode === 'orb'
            && !orb.reducedMotion
            && !document.hidden;
        if (shouldRun && orb.animationFrame === null) {
            orb.lastFrameAt = performance.now();
            orb.animationFrame = requestAnimationFrame(runCallOrbFrame);
        } else if (!shouldRun && orb.animationFrame !== null) {
            cancelAnimationFrame(orb.animationFrame);
            orb.animationFrame = null;
        }
        if (!shouldRun && orb.reducedMotion) drawStaticCallOrb();
    }

    /** Apply a new realtime activity profile to the orb and translated status. */
    function updateCallOrbState(nextState) {
        const orb = state.callOrb;
        if (!orb) return;
        const normalized = REALTIME_ORB_PROFILES[nextState] ? nextState : 'idle';
        if (orb.state === normalized) {
            updateCallStatus();
            return;
        }
        orb.state = normalized;
        if (orb.reducedMotion) {
            // Reduced motion intentionally skips interpolation while preserving
            // an accurate static representation of the current call state.
            orb.profile = { ...REALTIME_ORB_PROFILES[normalized] };
            orb.profileTransition = null;
        } else {
            orb.profileTransition = {
                from: { ...orb.profile },
                to: { ...REALTIME_ORB_PROFILES[normalized] },
                elapsedMs: 0,
            };
        }
        updateCallStatus();
        if (orb.reducedMotion) drawStaticCallOrb();
    }

    /**
     * Attach a zero-volume Web Audio branch to the already-authorized mic.
     * It measures the waveform without changing what either provider receives.
     */
    async function setupCallOrbAudioAnalyser() {
        const track = state.localStream?.getAudioTracks?.()[0] || null;
        const AudioContextCtor = getAudioContextCtor();
        if (!track || !AudioContextCtor) return false;
        if (state.callOrbAudio?.trackId === track.id) return true;
        await teardownCallOrbAudioAnalyser();
        try {
            const context = new AudioContextCtor();
            const source = context.createMediaStreamSource(state.localStream);
            const analyser = context.createAnalyser();
            const silenceGain = context.createGain();
            analyser.fftSize = 256;
            silenceGain.gain.value = 0;
            source.connect(analyser);
            analyser.connect(silenceGain);
            silenceGain.connect(context.destination);
            state.callOrbAudio = {
                context,
                source,
                analyser,
                silenceGain,
                timeData: new Uint8Array(analyser.fftSize),
                trackId: track.id,
            };
            if (context.state === 'suspended') {
                await settleMediaPromiseWithin(context.resume(), 500);
            }
            return true;
        } catch (_) {
            await teardownCallOrbAudioAnalyser();
            return false;
        }
    }

    /** Release the visual-only audio graph at the end of every call. */
    async function teardownCallOrbAudioAnalyser() {
        const audio = state.callOrbAudio;
        state.callOrbAudio = null;
        if (!audio) return;
        try { audio.source?.disconnect(); } catch (_) { /* already disconnected */ }
        try { audio.analyser?.disconnect(); } catch (_) { /* already disconnected */ }
        try { audio.silenceGain?.disconnect(); } catch (_) { /* already disconnected */ }
        if (audio.context && audio.context.state !== 'closed') {
            await settleMediaPromiseWithin(audio.context.close(), 500);
        }
    }

    function createRealtimeMessageId(prefix) {
        const random = Math.random().toString(36).slice(2, 10);
        return `realtime-${prefix}-${Date.now()}-${random}`;
    }

    function isWebRtcTransport() {
        return state.transport === 'webrtc';
    }

    function isGoogleLiveTransport() {
        return state.transport === 'websocket'
            && state.protocolVersion === 'google-live-proxy-v1';
    }

    function isXaiLiveTransport() {
        return state.transport === 'websocket'
            && state.protocolVersion === 'xai-realtime-proxy-v1';
    }

    function getChatAreaContainer() {
        return document.getElementById('chatAreaContainer');
    }

    function scrollChatAreaToBottom() {
        if (typeof window.scrollChatToBottom === 'function') {
            window.scrollChatToBottom();
            return;
        }
        const scrollHost = document.getElementById('chatArea') || getChatAreaContainer();
        if (!scrollHost) return;
        scrollHost.scrollTop = scrollHost.scrollHeight;
    }

    function renderRealtimeMessageContent(target, text, { markdownEnabled }) {
        if (!target) return;
        const normalized = String(text || '');
        target.setAttribute('data-raw-content', normalized);

        if (markdownEnabled && typeof window.renderMarkdownContent === 'function') {
            window.renderMarkdownContent(target, normalized);
            return;
        }

        target.innerHTML = '';
        target.textContent = normalized;
        target.classList.remove('markdown-body');
    }

    /**
     * Keep the two live nodes for a realtime turn at the end of the transcript
     * in their semantic order. Input transcription is allowed to arrive after
     * response events, so append order cannot be used as conversation order.
     *
     * @param {HTMLElement|null} chatAreaContainer Transcript container.
     * @param {HTMLElement|null} userMessageArea Live user turn wrapper.
     * @param {HTMLElement|null} assistantMessageContainer Live assistant wrapper.
     */
    function placeLiveRealtimeMessagesInTurnOrder(
        chatAreaContainer,
        userMessageArea,
        assistantMessageContainer,
    ) {
        if (!chatAreaContainer) return;

        const userIsMounted = userMessageArea?.parentElement === chatAreaContainer;
        const assistantIsMounted = assistantMessageContainer?.parentElement === chatAreaContainer;
        if (!userIsMounted && !assistantIsMounted) return;

        // The dynamic spacer must remain the final child because the normal
        // message renderer uses it to preserve the current scroll position.
        const spacerCandidate = chatAreaContainer.querySelector('.dynamic-scroll-spacer');
        const endAnchor = spacerCandidate?.parentElement === chatAreaContainer
            ? spacerCandidate
            : null;

        // Moving both mounted nodes to the transcript end is intentional. It
        // repairs an already-reversed pair as well as preventing a late user
        // transcript from being placed beneath an early assistant response.
        if (userIsMounted) {
            chatAreaContainer.insertBefore(userMessageArea, endAnchor);
        }
        if (assistantIsMounted) {
            chatAreaContainer.insertBefore(assistantMessageContainer, endAnchor);
        }
    }

    function ensureLiveUserMessageElement() {
        const existing = state.liveUserMessageContent;
        if (existing && existing.isConnected) {
            state.liveUserMessageArea = existing.closest('.user-message-area');
            state.liveUserColumnWrapper = existing.closest('.user-message-area')?.firstElementChild || null;
            placeLiveRealtimeMessagesInTurnOrder(
                getChatAreaContainer(),
                state.liveUserMessageArea,
                state.liveAssistantContainer,
            );
            return existing;
        }

        const chatAreaContainer = getChatAreaContainer();
        if (!chatAreaContainer) return null;
        if (typeof window.ensureChatTranscriptAccessibility === 'function') {
            window.ensureChatTranscriptAccessibility(chatAreaContainer);
        }

        if (!state.liveUserMessageId) {
            state.liveUserMessageId = createRealtimeMessageId('user');
        }

        const userMessageArea = document.createElement('div');
        userMessageArea.className = 'user-message-area';
        userMessageArea.dataset.realtimeLive = 'true';

        const columnWrapper = document.createElement('div');
        columnWrapper.style.display = 'flex';
        columnWrapper.style.flexDirection = 'column';
        columnWrapper.style.gap = '10px';
        columnWrapper.style.alignItems = 'flex-end';
        columnWrapper.style.maxWidth = '100%';

        const userMessageContainer = document.createElement('div');
        userMessageContainer.className = 'user-message-container';
        userMessageContainer.dataset.userMessageId = state.liveUserMessageId;
        userMessageContainer.dataset.realtimeLive = 'true';

        const userMessage = document.createElement('div');
        userMessage.className = 'user-message';

        const userMessageContent = document.createElement('div');
        userMessageContent.id = `u-${state.liveUserMessageId}`;
        userMessageContent.className = 'user-message-content';

        userMessage.appendChild(userMessageContent);
        userMessageContainer.appendChild(userMessage);
        columnWrapper.appendChild(userMessageContainer);
        userMessageArea.appendChild(columnWrapper);
        chatAreaContainer.appendChild(userMessageArea);
        if (typeof window.applyUserMessageAccessibility === 'function') {
            window.applyUserMessageAccessibility(userMessageContainer, { messageId: state.liveUserMessageId });
        }

        state.liveUserMessageArea = userMessageArea;
        state.liveUserColumnWrapper = columnWrapper;
        state.liveUserMessageContent = userMessageContent;
        placeLiveRealtimeMessagesInTurnOrder(
            chatAreaContainer,
            state.liveUserMessageArea,
            state.liveAssistantContainer,
        );
        return userMessageContent;
    }

    function ensureLiveAssistantMessageElement() {
        const existing = state.liveAssistantMessageContent;
        if (existing && existing.isConnected) {
            state.liveAssistantContainer = existing.closest('.assistant-message-container');
            placeLiveRealtimeMessagesInTurnOrder(
                getChatAreaContainer(),
                state.liveUserMessageArea,
                state.liveAssistantContainer,
            );
            return existing;
        }

        const chatAreaContainer = getChatAreaContainer();
        if (!chatAreaContainer) return null;
        if (typeof window.ensureChatTranscriptAccessibility === 'function') {
            window.ensureChatTranscriptAccessibility(chatAreaContainer);
        }

        if (!state.liveAssistantMessageId) {
            state.liveAssistantMessageId = createRealtimeMessageId('assistant');
        }

        const assistantMessageContainer = document.createElement('div');
        assistantMessageContainer.id = `a-${state.liveAssistantMessageId}`;
        assistantMessageContainer.className = 'assistant-message-container';
        assistantMessageContainer.dataset.referenceId = state.liveAssistantMessageId;
        assistantMessageContainer.dataset.retryCount = '0';
        assistantMessageContainer.dataset.totalVersions = '1';
        assistantMessageContainer.dataset.isLatestVersion = 'true';
        assistantMessageContainer.dataset.hidden = 'false';
        assistantMessageContainer.dataset.isStreaming = 'true';
        assistantMessageContainer.dataset.realtimeLive = 'true';
        assistantMessageContainer.dataset.announceStreaming = 'true';

        const assistantMessage = document.createElement('div');
        assistantMessage.className = 'assistant-message';

        const assistantMessageContent = document.createElement('div');
        assistantMessageContent.id = `a-1-${state.liveAssistantMessageId}`;
        assistantMessageContent.className = 'assistant-message-content';
        assistantMessageContent.setAttribute('aria-live', 'off');

        assistantMessage.appendChild(assistantMessageContent);
        assistantMessageContainer.appendChild(assistantMessage);
        chatAreaContainer.appendChild(assistantMessageContainer);
        if (typeof window.applyAssistantMessageAccessibility === 'function') {
            window.applyAssistantMessageAccessibility(assistantMessageContainer, {
                messageId: state.liveAssistantMessageId,
                streaming: true,
            });
        }
        if (typeof window.announceChatMessage === 'function') {
            window.announceChatMessage('Assistant response started');
        }

        state.liveAssistantContainer = assistantMessageContainer;
        state.liveAssistantMessageContent = assistantMessageContent;
        placeLiveRealtimeMessagesInTurnOrder(
            chatAreaContainer,
            state.liveUserMessageArea,
            state.liveAssistantContainer,
        );
        return assistantMessageContent;
    }

    /**
     * Keep shared tool-call UI ahead of the assistant's final text. Realtime
     * creates its text node eagerly for streaming, while the normal chat
     * renderer creates tool blocks first and appends text afterwards.
     *
     * @param {HTMLElement|null} assistantContainer Live assistant wrapper.
     * @param {HTMLElement|null} assistantContent Live assistant text element.
     */
    function placeLiveAssistantToolsBeforeContent(assistantContainer, assistantContent) {
        const assistantMessage = assistantContent?.closest?.('.assistant-message') || null;
        if (!assistantContainer || assistantMessage?.parentElement !== assistantContainer) return;

        const toolBlocks = Array.from(assistantContainer.children || []).filter((child) => (
            child?.classList?.contains('assistant-thinking')
        ));
        toolBlocks.forEach((toolBlock) => {
            assistantContainer.insertBefore(toolBlock, assistantMessage);
        });
    }

    /**
     * Complete every live tool block once the provider moves on
     * to its final response. The normal chat stream does this while appending
     * content; realtime updates one persistent text node and must do it itself.
     *
     * @param {HTMLElement|null} assistantContainer Live assistant wrapper.
     */
    function finalizeLiveAssistantToolBlocks(assistantContainer) {
        if (!assistantContainer) return;
        const toolBlocks = Array.from(assistantContainer.children || []).filter((child) => (
            child?.classList?.contains('assistant-thinking')
        ));
        toolBlocks.forEach((toolBlock) => {
            if (typeof finalizeThinkingBlockHeader === 'function') {
                finalizeThinkingBlockHeader(toolBlock);
            }
        });
    }

    /**
     * Render a provider tool call into the active realtime assistant message.
     * The backend remains authoritative for execution and persistence; this is
     * the live counterpart of chatTranscriptRenderer's persisted block path.
     *
     * @param {string} callId Provider tool-call identifier.
     * @param {string} toolName Configured Omlorix tool name.
     * @param {object} argumentsPayload Parsed tool arguments.
     * @returns {boolean} Whether a new live tool block was rendered.
     */
    function renderLiveToolCall(callId, toolName, argumentsPayload) {
        const normalizedCallId = String(callId || '').trim();
        const normalizedToolName = String(toolName || '').trim();
        if (!normalizedCallId || !normalizedToolName) return false;

        const renderedIds = state.currentTurn.renderedToolCallIds;
        if (renderedIds instanceof Set && renderedIds.has(normalizedCallId)) {
            return false;
        }

        const contentElement = ensureLiveAssistantMessageElement();
        const assistantMessageId = String(state.liveAssistantMessageId || '').trim();
        if (!contentElement || !assistantMessageId || typeof appendAssistantTool !== 'function') {
            return false;
        }

        const currentReasoningCount = Number(state.currentTurn.assistantReasoningCount || 0);
        const nextReasoningCount = appendAssistantTool(
            assistantMessageId,
            state.currentTurn.lastAppendedMessageType || '',
            currentReasoningCount,
            null,
            normalizedToolName,
            argumentsPayload,
            {
                id: normalizedCallId,
                call_id: normalizedCallId,
                tool_call_id: normalizedCallId,
                realtime: true,
            },
        );
        state.currentTurn.assistantReasoningCount = Number.isFinite(Number(nextReasoningCount))
            ? Number(nextReasoningCount)
            : currentReasoningCount;
        state.currentTurn.lastAppendedMessageType = 't';
        if (renderedIds instanceof Set) {
            renderedIds.add(normalizedCallId);
        }

        placeLiveAssistantToolsBeforeContent(state.liveAssistantContainer, contentElement);
        scrollChatAreaToBottom();
        return true;
    }

    function renderLiveUserTranscript(text) {
        const normalized = String(text || '').trim();
        if (!normalized) return;
        const contentElement = ensureLiveUserMessageElement();
        if (!contentElement) return;
        let renderUserMarkdown = false;
        try {
            renderUserMarkdown = localStorage.getItem('render_user_messages_markdown') === 'true';
        } catch (_error) {
            renderUserMarkdown = false;
        }
        renderRealtimeMessageContent(contentElement, normalized, { markdownEnabled: renderUserMarkdown });
        scrollChatAreaToBottom();
    }

    function renderLiveAssistantTranscript(text) {
        const normalized = String(text || '').trim();
        if (!normalized) return;
        updateCallTranscript(normalized);
        const contentElement = ensureLiveAssistantMessageElement();
        if (!contentElement) return;
        if (state.currentTurn.lastAppendedMessageType === 't') {
            finalizeLiveAssistantToolBlocks(state.liveAssistantContainer);
        }
        state.currentTurn.lastAppendedMessageType = 'c';
        let renderAssistantMarkdown = true;
        try {
            renderAssistantMarkdown = localStorage.getItem('render_assistant_messages_markdown') !== 'false';
        } catch (_error) {
            renderAssistantMarkdown = true;
        }
        renderRealtimeMessageContent(contentElement, normalized, { markdownEnabled: renderAssistantMarkdown });
        scrollChatAreaToBottom();
    }

    function finalizeLiveAssistantMessage() {
        const assistantMessageId = state.liveAssistantMessageId;
        if (!assistantMessageId) return;
        const assistantContainer = document.getElementById(`a-${assistantMessageId}`);
        if (!assistantContainer) return;
        if (state.liveUserMessageId) {
            assistantContainer.dataset.referenceId = state.liveUserMessageId;
        }
        finalizeLiveAssistantToolBlocks(assistantContainer);
        if (typeof appendAssistantDone === 'function') {
            appendAssistantDone(assistantMessageId, { realtime: true });
        }
    }

    function resetLiveRealtimeMessageState() {
        state.liveUserMessageId = null;
        state.liveAssistantMessageId = null;
        state.liveUserMessageContent = null;
        state.liveAssistantMessageContent = null;
        state.liveUserMessageArea = null;
        state.liveUserColumnWrapper = null;
        state.liveAssistantContainer = null;
    }

    function notify(level, message) {
        if (!message) return;
        if (level === 'error' && typeof window.notifyError === 'function') {
            window.notifyError(message);
            return;
        }
        if (level === 'warning' && typeof window.notifyWarning === 'function') {
            window.notifyWarning(message);
            return;
        }
        if (level === 'success' && typeof window.notifySuccess === 'function') {
            window.notifySuccess(message);
            return;
        }
        if (typeof window.notifyInfo === 'function') {
            window.notifyInfo(message);
        }
    }

    function syncRealtimeWakeLock() {
        window.chatWakeLock?.syncReason?.('realtime-call', Boolean(state.active || state.connecting));
    }

    function emitRealtimeState() {
        if (typeof window === 'undefined') return;
        syncRealtimeWakeLock();
        syncCallSurfaceState();
        window.dispatchEvent(
            new CustomEvent('realtime:state', {
                detail: {
                    active: state.active,
                    connecting: state.connecting,
                    ready: state.ready,
                    muted: state.isMuted,
                    sessionId: state.sessionId,
                    chatId: state.chatId,
                    activity: state.activity,
                    viewMode: state.callViewMode,
                    captionsVisible: state.callCaptionsVisible,
                },
            })
        );
    }

    function updateCallButton() {
        if (!state.callButton) {
            state.callButton = document.getElementById('chatBoxCallButton');
        }
        if (!state.callButton) return;
        const shouldStop = state.active || state.connecting;
        state.callButton.classList.toggle('is-active-call', state.active);
        state.callButton.classList.toggle('is-connecting-call', state.connecting);
        const label = shouldStop
            ? t('chat_stop_call', 'Stop call')
            : t('chat_call', 'Start call');
        state.callButton.setAttribute('aria-label', label);
        state.callButton.title = label;
        const expectedIcon = shouldStop ? CALL_ICON_STOP : CALL_ICON_START;
        if (state.callButton.innerHTML !== expectedIcon) {
            state.callButton.innerHTML = expectedIcon;
        }
    }

    function updateActivity(nextActivity) {
        if (state.activity === nextActivity) return;
        state.activity = nextActivity;
        updateCallButton();
        emitRealtimeState();
    }

    function isCallRoutePath(pathname = window.location.pathname) {
        return String(pathname || '').trim() === CALL_ROUTE_PATH;
    }

    function syncBrowserPath(path, statePayload = null, { replace = false } = {}) {
        if (typeof window === 'undefined' || typeof history === 'undefined') return;
        const targetPath = String(path || '').trim() || '/';
        const currentPath = String(window.location?.pathname || '').trim() || '/';
        if (replace || currentPath === targetPath) {
            history.replaceState(statePayload, '', targetPath);
            return;
        }
        history.pushState(statePayload, '', targetPath);
    }

    function syncCallRouteHistory({ replace = false } = {}) {
        syncBrowserPath(CALL_ROUTE_PATH, { callMode: true, chatId: state.chatId || null }, { replace });
    }

    function syncChatRouteHistory(chatId, { replace = false } = {}) {
        const normalizedChatId = String(chatId || '').trim();
        if (!normalizedChatId) {
            syncBrowserPath('/', null, { replace });
            return;
        }
        syncBrowserPath(`/chat/${encodeURIComponent(normalizedChatId)}`, { chatId: normalizedChatId }, { replace });
    }

    function maybeAutostartFromRoute() {
        if (!state.routeModeActive || state.active || state.connecting || state.stopping) {
            return state.routeAutostartPromise;
        }
        if (state.routeAutostartPromise) {
            return state.routeAutostartPromise;
        }
        state.routeAutostartPending = true;
        // Invoke start immediately while this function is still on the click
        // event stack. Browsers use that user activation when deciding whether
        // to show the microphone permission prompt.
        state.routeAutostartPromise = start()
            .catch(() => false)
            .finally(() => {
                state.routeAutostartPending = false;
                state.routeAutostartPromise = null;
            });
        return state.routeAutostartPromise;
    }

    function activateCallRoute() {
        const currentChatId = String(getChatContext().chatId || state.chatId || '').trim() || null;
        state.routeModeActive = true;
        // Entering the dedicated call route temporarily clears the chat container.
        // Always snapshot its chat ID first (including null for a genuinely new chat)
        // so a previous call can never leak a stale destination into this start.
        state.routeReturnChatId = currentChatId;
        state.routeAutostartPending = true;
        if (typeof window.showChatStartContainer === 'function') {
            window.showChatStartContainer({ skipHistory: true, skipCallTeardown: true });
        }
        if (typeof window.showChatContainer === 'function') {
            window.showChatContainer({ skipCallTeardown: true });
        }
        enterCallRouteUi({ resetMode: true });
        syncCallRouteHistory({ replace: true });
        return maybeAutostartFromRoute();
    }

    function deactivateCallRoute({ restorePath = false, chatId = null, replace = true, stopActive = false } = {}) {
        state.routeModeActive = false;
        state.routeAutostartPending = false;
        leaveCallRouteUi();
        if (stopActive && (state.active || state.connecting) && !state.stopping) {
            stop({
                skipServerStop: false,
                silent: true,
                reason: 'call_route_left',
                preserveCallRoute: false,
            }).catch(() => {});
        }
        if (!restorePath) {
            state.routeReturnChatId = null;
            return;
        }
        const restoreChatId = String(chatId || state.routeReturnChatId || '').trim();
        state.routeReturnChatId = null;
        if (restoreChatId) {
            syncChatRouteHistory(restoreChatId, { replace });
            return;
        }
        syncBrowserPath('/', null, { replace });
    }

    function getChatContext() {
        const chatContainer = document.getElementById('chatContainer');
        const modelSelect = document.getElementById('modelSelect');
        const skillSelect = document.getElementById('skillSelect');
        let skillId = null;
        if (typeof window.getSelectedSkillIds === 'function') {
            try {
                const selectedSkillIds = window.getSelectedSkillIds();
                if (Array.isArray(selectedSkillIds) && selectedSkillIds.length > 0) {
                    const firstSkillId = String(selectedSkillIds[0] || '').trim();
                    if (firstSkillId) {
                        skillId = firstSkillId;
                    }
                }
            } catch (_) {
                skillId = null;
            }
        }
        if (!skillId && skillSelect) {
            const selectedSkill = String(skillSelect.value || '').trim();
            if (selectedSkill) {
                skillId = selectedSkill;
            }
        }
        return {
            chatId: (chatContainer?.getAttribute('data-chat-id') || '').trim() || null,
            projectId: (chatContainer?.getAttribute('data-project-id') || '').trim() || null,
            modelId: (modelSelect?.getAttribute('data-model-id') || '').trim() || null,
            skillId,
        };
    }

    function getRealtimeStartContext() {
        const context = getChatContext();
        const originatingChatId = state.routeModeActive
            ? String(state.routeReturnChatId || '').trim()
            : '';

        // The call route intentionally renders the start container before the
        // session request. Restore only the originating chat ID while retaining
        // the model, project, and skill selections from the current controls.
        if (!context.chatId && originatingChatId) {
            return {
                ...context,
                chatId: originatingChatId,
            };
        }
        return context;
    }

    async function syncChatForRealtimeSession(chatId, { preserveRoute = state.routeModeActive } = {}) {
        if (!chatId) return;
        const chatContainer = document.getElementById('chatContainer');
        const current = (chatContainer?.getAttribute('data-chat-id') || '').trim();
        if (current !== chatId && typeof window.loadChatView === 'function') {
            await window.loadChatView(chatId, false, { preserveHistory: preserveRoute });
        } else if (chatContainer) {
            chatContainer.setAttribute('data-chat-id', chatId);
        }

        if (preserveRoute) {
            enterCallRouteUi();
            syncCallRouteHistory({ replace: true });
            return;
        }
        syncChatRouteHistory(chatId, { replace: true });
    }

    function getAudioContextCtor() {
        return window.AudioContext || window.webkitAudioContext || null;
    }

    function getRealtimePeerConnectionConfig() {
        const configuredIceServers = window.realtimeCallConfig?.iceServers || window.__REALTIME_ICE_SERVERS__;
        if (Array.isArray(configuredIceServers) && configuredIceServers.length) {
            const normalized = configuredIceServers
                .filter((entry) => entry && typeof entry === 'object')
                .map((entry) => {
                    const urls = Array.isArray(entry.urls)
                        ? entry.urls.map((url) => String(url || '').trim()).filter(Boolean)
                        : String(entry.urls || '').trim();
                    if (!urls || (Array.isArray(urls) && !urls.length)) {
                        return null;
                    }
                    const iceServer = { urls };
                    if (typeof entry.username === 'string' && entry.username.trim()) {
                        iceServer.username = entry.username.trim();
                    }
                    if (typeof entry.credential === 'string' && entry.credential.trim()) {
                        iceServer.credential = entry.credential.trim();
                    }
                    return iceServer;
                })
                .filter(Boolean);
            if (normalized.length) {
                return { iceServers: normalized };
            }
        }
        return {
            // Preserve the demo/default path unless the deployer injects a better ICE configuration.
            iceServers: [{ urls: 'stun:stun.l.google.com:19302' }],
        };
    }

    function arrayBufferToBase64(buffer) {
        const bytes = new Uint8Array(buffer);
        let binary = '';
        const chunkSize = 0x8000;
        for (let index = 0; index < bytes.length; index += chunkSize) {
            const chunk = bytes.subarray(index, index + chunkSize);
            binary += String.fromCharCode(...chunk);
        }
        return btoa(binary);
    }

    function base64ToArrayBuffer(base64Value) {
        const normalized = String(base64Value || '').trim();
        if (!normalized) return new ArrayBuffer(0);
        const binary = atob(normalized);
        const bytes = new Uint8Array(binary.length);
        for (let index = 0; index < binary.length; index += 1) {
            bytes[index] = binary.charCodeAt(index);
        }
        return bytes.buffer;
    }

    function parseMimeSampleRate(mimeType, fallbackRate) {
        const normalized = String(mimeType || '').trim().toLowerCase();
        const match = normalized.match(/rate=(\d+)/);
        if (match && Number.isFinite(Number(match[1]))) {
            return Number(match[1]);
        }
        return fallbackRate;
    }

    function downsampleFloat32ToInt16(float32Data, inputSampleRate, targetSampleRate = 16000) {
        if (!(float32Data instanceof Float32Array) || !float32Data.length) {
            return null;
        }
        const normalizedInputRate = Number(inputSampleRate);
        const normalizedTargetRate = Number(targetSampleRate);
        if (!Number.isFinite(normalizedInputRate) || !Number.isFinite(normalizedTargetRate) || normalizedInputRate <= 0 || normalizedTargetRate <= 0) {
            return null;
        }

        if (normalizedInputRate === normalizedTargetRate) {
            const direct = new Int16Array(float32Data.length);
            for (let index = 0; index < float32Data.length; index += 1) {
                const sample = Math.max(-1, Math.min(1, float32Data[index]));
                direct[index] = sample < 0 ? sample * 0x8000 : sample * 0x7fff;
            }
            return direct;
        }

        const sampleRateRatio = normalizedInputRate / normalizedTargetRate;
        const outputLength = Math.max(1, Math.round(float32Data.length / sampleRateRatio));
        const result = new Int16Array(outputLength);
        let outputIndex = 0;
        let inputIndex = 0;

        while (outputIndex < outputLength) {
            const nextInputIndex = Math.min(float32Data.length, Math.round((outputIndex + 1) * sampleRateRatio));
            let sum = 0;
            let count = 0;
            for (let cursor = inputIndex; cursor < nextInputIndex; cursor += 1) {
                sum += float32Data[cursor];
                count += 1;
            }
            const average = count > 0 ? sum / count : float32Data[Math.min(inputIndex, float32Data.length - 1)];
            const sample = Math.max(-1, Math.min(1, average));
            result[outputIndex] = sample < 0 ? sample * 0x8000 : sample * 0x7fff;
            outputIndex += 1;
            inputIndex = nextInputIndex;
        }

        return result;
    }

    function pcm16ToAudioBuffer(int16Data, sampleRate) {
        if (!(int16Data instanceof Int16Array) || !int16Data.length) {
            return null;
        }
        const audioContext = ensureOutputAudioContext();
        if (!audioContext) return null;
        const buffer = audioContext.createBuffer(1, int16Data.length, sampleRate);
        const channel = buffer.getChannelData(0);
        for (let index = 0; index < int16Data.length; index += 1) {
            channel[index] = int16Data[index] / 0x8000;
        }
        return buffer;
    }

    function ensureOutputAudioContext() {
        const AudioContextCtor = getAudioContextCtor();
        if (!AudioContextCtor) return null;
        if (!state.outputAudioContext) {
            state.outputAudioContext = new AudioContextCtor();
        }
        return state.outputAudioContext;
    }

    /**
     * Wait briefly for a browser media promise without allowing Safari to hold
     * the entire Realtime connection open indefinitely.
     *
     * @param {Promise<unknown>|unknown} promise Media operation to observe.
     * @param {number} timeoutMs Maximum time to wait.
     * @returns {Promise<boolean>} True only when the operation resolves in time.
     */
    async function settleMediaPromiseWithin(promise, timeoutMs) {
        let timeoutId = null;
        const settledPromise = Promise.resolve(promise).then(
            () => true,
            () => false,
        );
        const timeoutPromise = new Promise((resolve) => {
            timeoutId = window.setTimeout(() => resolve(false), timeoutMs);
        });
        try {
            return await Promise.race([settledPromise, timeoutPromise]);
        } finally {
            if (timeoutId !== null) {
                window.clearTimeout(timeoutId);
            }
        }
    }

    async function resumeOutputAudioContext() {
        const ctx = ensureOutputAudioContext();
        if (!ctx) return false;
        if (ctx.state === 'suspended' || ctx.state === 'interrupted') {
            const resumed = await settleMediaPromiseWithin(ctx.resume(), 500);
            if (!resumed) {
                return false;
            }
        }
        return ctx.state === 'running';
    }

    function ensureRemoteStream() {
        if (!state.remoteStream) {
            state.remoteStream = new MediaStream();
        }
        return state.remoteStream;
    }

    function ensureRemoteAudioElement() {
        if (state.remoteAudio) return state.remoteAudio;

        const audioEl = document.createElement('audio');
        audioEl.autoplay = true;
        audioEl.playsInline = true;
        audioEl.preload = 'auto';
        audioEl.muted = false;
        audioEl.volume = 1;
        audioEl.setAttribute('autoplay', '');
        audioEl.setAttribute('playsinline', '');
        audioEl.style.display = 'none';
        document.body.appendChild(audioEl);

        // A WebRTC media element plays the permanent remote stream, including
        // silence between turns. It therefore cannot indicate that the model
        // is speaking. Realtime's output_audio_buffer events are authoritative.
        audioEl.addEventListener('pause', () => {
            if (!state.active) {
                updateActivity('idle');
            }
        });

        state.remoteAudio = audioEl;
        return audioEl;
    }

    async function unlockAudioPlayback() {
        if (state.audioOutputUnlocked) {
            await resumeOutputAudioContext();
            return true;
        }
        if (state.audioUnlockPromise) {
            return state.audioUnlockPromise;
        }

        state.audioUnlockPromise = (async () => {
            await resumeOutputAudioContext();
            const audioEl = ensureRemoteAudioElement();
            try {
                audioEl.muted = true;
                audioEl.volume = 0;
                audioEl.srcObject = null;
                audioEl.src = SILENT_WAV;
                const playbackStarted = await settleMediaPromiseWithin(audioEl.play(), 500);
                state.audioOutputUnlocked = playbackStarted;
            } catch (_) {
                state.audioOutputUnlocked = false;
            } finally {
                audioEl.pause();
                audioEl.removeAttribute('src');
                audioEl.load();
                audioEl.muted = false;
                audioEl.volume = 1;
                if (state.remoteStream && state.remoteStream.getAudioTracks().length > 0) {
                    audioEl.srcObject = state.remoteStream;
                }
            }
            return state.audioOutputUnlocked;
        })().finally(() => {
            state.audioUnlockPromise = null;
        });

        return state.audioUnlockPromise;
    }

    async function syncRemoteAudioPlayback() {
        const audioEl = ensureRemoteAudioElement();
        const stream = ensureRemoteStream();
        const hasAudioTrack = stream.getAudioTracks().length > 0;

        if (audioEl.srcObject !== stream) {
            audioEl.srcObject = stream;
        }

        await resumeOutputAudioContext();

        if (!hasAudioTrack) {
            state.pendingRemotePlayback = true;
            return;
        }

        try {
            const playPromise = audioEl.play();
            if (playPromise && typeof playPromise.then === 'function') {
                await playPromise;
            }
            state.pendingRemotePlayback = false;
        } catch (_) {
            state.pendingRemotePlayback = true;
        }
    }

    function stopGooglePlayback() {
        for (const source of Array.from(state.googlePlaybackSources)) {
            try {
                source.onended = null;
                source.stop();
            } catch (_) {
                // no-op
            }
        }
        state.googlePlaybackSources.clear();
        state.googlePlaybackCursorTime = 0;
        state.assistantSpeaking = false;
        state.pendingRemotePlayback = false;
    }

    async function playGoogleAudioChunk(base64Audio, mimeType) {
        const audioContext = ensureOutputAudioContext();
        if (!audioContext) return;
        await resumeOutputAudioContext();

        const normalizedMimeType = String(mimeType || '').trim().toLowerCase();
        const rawBuffer = base64ToArrayBuffer(base64Audio);
        if (!rawBuffer.byteLength) return;

        let audioBuffer = null;
        if (normalizedMimeType.includes('wav')) {
            try {
                audioBuffer = await audioContext.decodeAudioData(rawBuffer.slice(0));
            } catch (_) {
                audioBuffer = null;
            }
        }

        if (!audioBuffer) {
            const int16Data = new Int16Array(rawBuffer);
            const sampleRate = parseMimeSampleRate(normalizedMimeType, 24000);
            audioBuffer = pcm16ToAudioBuffer(int16Data, sampleRate);
        }
        if (!audioBuffer) return;

        const source = audioContext.createBufferSource();
        source.buffer = audioBuffer;
        source.connect(audioContext.destination);

        const startAt = Math.max(audioContext.currentTime + 0.02, state.googlePlaybackCursorTime || 0);
        source.start(startAt);
        state.googlePlaybackCursorTime = startAt + audioBuffer.duration;
        state.googlePlaybackSources.add(source);
        state.assistantSpeaking = true;
        updateActivity('speaking');

        source.onended = () => {
            state.googlePlaybackSources.delete(source);
            if (!state.googlePlaybackSources.size) {
                state.assistantSpeaking = false;
                state.currentTurn.outputAudioStarted = false;
                if (isXaiLiveTransport()) {
                    persistCompletedOpenAiTurnIfReady().catch((error) => {
                        console.error('Failed to persist completed xAI realtime turn', error);
                    });
                }
                if (state.active) {
                    updateActivity('listening');
                }
            }
        };
    }

    async function resumeGoogleMicrophoneAudioContext() {
        const audioContext = state.googleMicAudioContext;
        if (!audioContext || audioContext.state !== 'suspended') {
            return;
        }
        // Safari and mobile Chromium can suspend a context created after an
        // asynchronous permission/provider handshake. Bound the recovery so a
        // browser quirk cannot stall call setup.
        await settleMediaPromiseWithin(audioContext.resume(), 500);
    }

    async function retryRemotePlaybackFromGesture() {
        if (isGoogleLiveTransport() || isXaiLiveTransport()) {
            await Promise.all([
                resumeOutputAudioContext(),
                resumeGoogleMicrophoneAudioContext(),
            ]);
            return;
        }
        if (!state.pendingRemotePlayback && !state.remoteStream?.getAudioTracks().length) return;
        await unlockAudioPlayback();
        await syncRemoteAudioPlayback();
    }

    function installAudioRecoveryListeners() {
        const retry = () => {
            retryRemotePlaybackFromGesture();
        };

        document.addEventListener('click', retry, { passive: true });
        document.addEventListener('touchstart', retry, { passive: true });
        document.addEventListener('keydown', retry, { passive: true });

        document.addEventListener('visibilitychange', () => {
            if (document.visibilityState === 'visible') {
                retryRemotePlaybackFromGesture();
            }
        });
    }

    function sendRealtimeEvent(event) {
        if (isXaiLiveTransport()) {
            if (!state.ws || state.ws.readyState !== WebSocket.OPEN) {
                return false;
            }
            state.ws.send(JSON.stringify(event));
            return true;
        }
        if (!state.dc || state.dc.readyState !== 'open') {
            return false;
        }
        state.dc.send(JSON.stringify(event));
        return true;
    }

    function sendGoogleRealtimeMessage(message) {
        if (!state.ws || state.ws.readyState !== WebSocket.OPEN) {
            return false;
        }
        state.ws.send(JSON.stringify(message));
        return true;
    }

    function stopRealtimeMaintenance() {
        if (state.heartbeatTimer) {
            window.clearInterval(state.heartbeatTimer);
            state.heartbeatTimer = null;
        }
        if (state.sessionLimitTimer) {
            window.clearTimeout(state.sessionLimitTimer);
            state.sessionLimitTimer = null;
        }
    }

    async function sendRealtimeHeartbeat() {
        const sessionId = String(state.sessionId || '').trim();
        if (!state.active || !sessionId || state.stopping) return false;
        if (state.heartbeatInFlightSessionId === sessionId) return false;

        state.heartbeatInFlightSessionId = sessionId;
        try {
            const response = await window.authedFetch(`/api/v1/realtime/session/${encodeURIComponent(sessionId)}/heartbeat`, {
                method: 'POST',
            });
            if (state.sessionId !== sessionId || state.stopping) return false;
            if (response.ok) return true;

            // A rejected client heartbeat means the backend no longer owns an
            // active reservation/runtime for this direct provider connection.
            // End it locally before another call can be admitted concurrently.
            if (response.status >= 400 && response.status < 500) {
                notify('warning', state.sessionLimitSource === 'rate_limit'
                    ? t('chat_realtime_rate_limit_session_reached', 'Your realtime call minute limit was reached. Try again after the usage window resets.')
                    : t('chat_realtime_session_limit_reached', 'The provider session time limit was reached. Start a new call to continue.'));
                await stop({ skipServerStop: true, silent: true, reason: 'heartbeat_rejected' });
                return false;
            }

            console.warn(`Realtime heartbeat failed (${response.status})`);
            return false;
        } catch (error) {
            console.warn('Realtime heartbeat failed', error);
            return false;
        } finally {
            if (state.heartbeatInFlightSessionId === sessionId) {
                state.heartbeatInFlightSessionId = null;
            }
        }
    }

    function startRealtimeMaintenance(maxSessionSeconds, sessionLimitSource = 'provider', sessionExpiresAt = null) {
        stopRealtimeMaintenance();
        state.sessionLimitSource = sessionLimitSource === 'rate_limit' ? 'rate_limit' : 'provider';
        state.sessionExpiresAt = String(sessionExpiresAt || '').trim() || null;
        state.heartbeatTimer = window.setInterval(() => {
            sendRealtimeHeartbeat().catch(() => {});
        }, 60_000);

        const normalizedLimit = Number(maxSessionSeconds);
        const absoluteDeadlineMs = state.sessionExpiresAt ? Date.parse(state.sessionExpiresAt) : Number.NaN;
        const timeoutMs = Number.isFinite(absoluteDeadlineMs)
            ? Math.max(absoluteDeadlineMs - Date.now(), 0)
            : (Number.isFinite(normalizedLimit) && normalizedLimit >= 1 ? normalizedLimit * 1000 : null);
        if (timeoutMs !== null) {
            state.sessionLimitTimer = window.setTimeout(() => {
                notify('info', state.sessionLimitSource === 'rate_limit'
                    ? t('chat_realtime_rate_limit_session_reached', 'Your realtime call minute limit was reached. Try again after the usage window resets.')
                    : t('chat_realtime_session_limit_reached', 'The provider session time limit was reached. Start a new call to continue.'));
                stop({ reason: 'provider_session_limit' }).catch(() => {});
            }, timeoutMs);
        }
    }

    /**
     * Stop every track owned by a stream without allowing cleanup failures to
     * obscure the original call error.
     *
     * @param {MediaStream|null} stream Stream whose tracks should be released.
     */
    function stopLocalMediaStream(stream) {
        stream?.getTracks?.().forEach((track) => {
            try {
                track.stop();
            } catch (_) {
                // A track may already have ended while the permission request
                // or call teardown was in progress.
            }
        });
    }

    /**
     * Throw a silent cancellation when a pending microphone request belongs to
     * a start attempt that the user has already stopped.
     *
     * @param {number} startAttemptId Identifier captured by start().
     * @param {MediaStream|null} stream Newly granted stream to release on cancel.
     */
    function assertCurrentStartAttempt(startAttemptId, stream = null) {
        if (state.startAttemptId === startAttemptId && state.connecting && !state.stopping) {
            return;
        }
        stopLocalMediaStream(stream);
        const error = new Error('Realtime call start was cancelled');
        error.name = 'AbortError';
        throw error;
    }

    /**
     * Acquire microphone permission and audio before any network request. This
     * keeps the browser prompt attached to the user's original call-button click
     * and lets both OpenAI WebRTC and Google Live reuse the same stream.
     *
     * @param {number|null} startAttemptId Optional start attempt to validate.
     * @returns {Promise<MediaStream>} A stream containing a live audio track.
     */
    async function ensureLocalMicrophoneStream(startAttemptId = null) {
        const existingAudioTrack = state.localStream
            ?.getAudioTracks?.()
            .find((track) => track.readyState !== 'ended');
        if (existingAudioTrack) {
            if (startAttemptId !== null) {
                assertCurrentStartAttempt(startAttemptId);
            }
            return state.localStream;
        }

        stopLocalMediaStream(state.localStream);
        state.localStream = null;
        const stream = await navigator.mediaDevices.getUserMedia({
            audio: {
                echoCancellation: true,
                noiseSuppression: true,
                autoGainControl: true,
            },
        });

        if (startAttemptId !== null) {
            assertCurrentStartAttempt(startAttemptId, stream);
        }
        const [audioTrack] = stream.getAudioTracks();
        if (!audioTrack) {
            stopLocalMediaStream(stream);
            throw new Error(t('chat_realtime_microphone_track_missing', 'No microphone audio track is available'));
        }
        state.localStream = stream;
        return stream;
    }

    async function startGoogleMicrophoneStreaming() {
        await ensureLocalMicrophoneStream();
        const AudioContextCtor = getAudioContextCtor();
        if (!AudioContextCtor) {
            throw new Error(t('chat_realtime_web_audio_unsupported', 'Web Audio API is not supported in this browser'));
        }

        if (state.googleMicAudioContext && state.googleMicProcessor) {
            await resumeGoogleMicrophoneAudioContext();
            return;
        }

        const audioContext = new AudioContextCtor();
        state.googleMicAudioContext = audioContext;
        await resumeGoogleMicrophoneAudioContext();

        const source = audioContext.createMediaStreamSource(state.localStream);
        const processor = audioContext.createScriptProcessor(2048, 1, 1);
        const silenceGain = audioContext.createGain();
        silenceGain.gain.value = 0;

        processor.onaudioprocess = (event) => {
            if (
                !state.active
                || (!isGoogleLiveTransport() && !isXaiLiveTransport())
                || state.isMuted
            ) {
                return;
            }
            if (!state.ws || state.ws.readyState !== WebSocket.OPEN) {
                return;
            }

            const input = event.inputBuffer?.getChannelData?.(0);
            if (!(input instanceof Float32Array) || !input.length) {
                return;
            }

            const targetSampleRate = isXaiLiveTransport() ? 24000 : 16000;
            const pcm16 = downsampleFloat32ToInt16(
                input,
                audioContext.sampleRate,
                targetSampleRate,
            );
            if (!(pcm16 instanceof Int16Array) || !pcm16.length) {
                return;
            }

            if (isXaiLiveTransport()) {
                sendRealtimeEvent({
                    type: 'input_audio_buffer.append',
                    audio: arrayBufferToBase64(pcm16.buffer),
                });
            } else {
                sendGoogleRealtimeMessage({
                    realtimeInput: {
                        audio: {
                            data: arrayBufferToBase64(pcm16.buffer),
                            mimeType: 'audio/pcm;rate=16000',
                        },
                    },
                });
            }
        };

        source.connect(processor);
        processor.connect(silenceGain);
        silenceGain.connect(audioContext.destination);

        state.googleMicSource = source;
        state.googleMicProcessor = processor;
        state.googleMicSilenceGain = silenceGain;
    }

    async function stopGoogleMicrophoneStreaming() {
        const audioContext = state.googleMicAudioContext;
        try {
            state.googleMicProcessor?.disconnect?.();
        } catch (_) {
            // no-op
        }
        try {
            state.googleMicSource?.disconnect?.();
        } catch (_) {
            // no-op
        }
        try {
            state.googleMicSilenceGain?.disconnect?.();
        } catch (_) {
            // no-op
        }
        state.googleMicProcessor = null;
        state.googleMicSource = null;
        state.googleMicSilenceGain = null;
        if (audioContext) {
            try {
                await audioContext.close();
            } catch (_) {
                // no-op
            }
        }
        state.googleMicAudioContext = null;
    }

    function normalizeGoogleUsageMetadata(usageMetadata) {
        if (!usageMetadata || typeof usageMetadata !== 'object') {
            return null;
        }

        const normalized = {
            input_tokens: Number(usageMetadata.promptTokenCount || 0),
            output_tokens: Number(usageMetadata.responseTokenCount || 0),
            input_token_details: {},
            output_token_details: {},
        };

        const promptDetails = Array.isArray(usageMetadata.promptTokensDetails) ? usageMetadata.promptTokensDetails : [];
        const responseDetails = Array.isArray(usageMetadata.responseTokensDetails) ? usageMetadata.responseTokensDetails : [];

        for (const detail of promptDetails) {
            const modality = String(detail?.modality || '').trim().toUpperCase();
            const tokenCount = Number(detail?.tokenCount || 0);
            if (modality === 'AUDIO') {
                normalized.input_token_details.audio_tokens = tokenCount;
            }
        }
        for (const detail of responseDetails) {
            const modality = String(detail?.modality || '').trim().toUpperCase();
            const tokenCount = Number(detail?.tokenCount || 0);
            if (modality === 'AUDIO') {
                normalized.output_token_details.audio_tokens = tokenCount;
            }
        }

        return normalized;
    }

    function turnHasContent(turn = state.currentTurn) {
        return Boolean(
            String(turn?.userTranscript || '').trim()
            || String(turn?.assistantTranscript || '').trim()
            || (Array.isArray(turn?.fileIds) && turn.fileIds.length)
        );
    }

    function resetCurrentTurn() {
        state.currentTurn = createEmptyTurn();
        state.currentAssistantItemId = null;
    }

    /** Wait without occupying the serialized realtime provider-event queue. */
    function waitForRealtimeTitlePoll(delayMs) {
        return new Promise((resolve) => window.setTimeout(resolve, delayMs));
    }

    /**
     * Refresh a background-generated title without delaying the active call.
     *
     * The turn endpoint returns the safe message fallback immediately, then a
     * server background task replaces it with the generated title. Polling the
     * existing chat-detail endpoint keeps this secondary work independent from
     * provider audio and transcription processing.
     */
    function scheduleRealtimeTitleRefresh(chatId, initialTitle) {
        const normalizedChatId = String(chatId || '').trim();
        const normalizedInitialTitle = String(initialTitle || '').trim();
        if (!normalizedChatId || state.pendingTitlePollChatIds.has(normalizedChatId)) return;

        state.pendingTitlePollChatIds.add(normalizedChatId);
        void (async () => {
            try {
                for (let attempt = 0; attempt < REALTIME_TITLE_POLL_ATTEMPTS; attempt += 1) {
                    const delayMs = Math.min(
                        REALTIME_TITLE_POLL_INITIAL_MS * (2 ** attempt),
                        REALTIME_TITLE_POLL_MAX_MS,
                    );
                    await waitForRealtimeTitlePoll(delayMs);
                    let response;
                    try {
                        response = await window.authedFetch(
                            `/api/v1/chats/detail?chat_id=${encodeURIComponent(normalizedChatId)}`,
                        );
                    } catch (_) {
                        // Transient network failures are retried on the next
                        // bounded backoff interval.
                        continue;
                    }
                    if (!response.ok) {
                        if (response.status === 404) return;
                        continue;
                    }
                    const chat = await response.json().catch(() => ({}));
                    const refreshedTitle = String(chat?.title || '').trim();
                    if (refreshedTitle && refreshedTitle !== normalizedInitialTitle) {
                        if (typeof window.applyChatSidebarTitle === 'function') {
                            window.applyChatSidebarTitle(normalizedChatId, refreshedTitle);
                        }
                        return;
                    }
                }
            } catch {
                // Title refresh is best-effort and must never interrupt a call.
            } finally {
                state.pendingTitlePollChatIds.delete(normalizedChatId);
            }
        })();
    }

    async function handleTurnSaved(payload) {
        const chatId = payload?.chat_id || state.chatId;
        if (!chatId) return;
        const generatedTitle = String(payload?.chat_title || '').trim();
        if (generatedTitle && typeof window.applyChatSidebarTitle === 'function') {
            window.applyChatSidebarTitle(chatId, generatedTitle);
        }
        if (payload?.chat_title_pending) {
            scheduleRealtimeTitleRefresh(chatId, generatedTitle);
        }
        const persistedAssistantId = String(payload?.assistant_message_id || '').trim();
        const persistedUserId = String(payload?.user_message_id || '').trim();
        if (state.liveAssistantMessageId && persistedAssistantId) {
            const assistantContainer = document.getElementById(`a-${state.liveAssistantMessageId}`);
            if (assistantContainer) {
                assistantContainer.dataset.assistantMessageId = persistedAssistantId;
                if (persistedUserId) {
                    assistantContainer.dataset.referenceId = persistedUserId;
                }
            }
        }
        finalizeLiveAssistantMessage();
        resetLiveRealtimeMessageState();
        state.chatId = String(chatId);
        await syncChatForRealtimeSession(state.chatId, { preserveRoute: state.routeModeActive });
    }

    async function persistCurrentTurn({ errorMessage = null, consumeNextResponseDone = false } = {}) {
        if (!state.sessionId) return false;
        if (!turnHasContent() && !errorMessage) return false;
        const turn = state.currentTurn;
        const sessionId = state.sessionId;
        if (turn.persistPromise) {
            return turn.persistPromise;
        }

        const payload = {
            turn_id: turn.turnId,
            user_transcript: turn.userTranscript,
            assistant_transcript: turn.assistantTranscript,
            file_ids: turn.fileIds,
            interrupted: Boolean(turn.interrupted),
            error_message: errorMessage || null,
            usage: turn.usage || null,
            provider_interactions: Array.isArray(turn.providerInteractions)
                ? turn.providerInteractions
                : [],
        };

        const request = (async () => {
            try {
                const response = await window.authedFetch(`/api/v1/realtime/session/${encodeURIComponent(sessionId)}/turn`, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify(payload),
                });
                if (!response.ok) {
                    const errorData = await response.json().catch(() => ({}));
                    throw new Error(errorData?.detail || formatT('chat_realtime_persist_turn_failed_status', 'Failed to persist realtime turn ({status})', { status: response.status }));
                }
                const data = await response.json().catch(() => ({}));
                if (consumeNextResponseDone && turn.responseId) {
                    state.ignoredResponseIds.add(turn.responseId);
                }
                // Never let a delayed save reset a newer turn. Provider events
                // are serialized, but typed input and stop actions can still
                // run while this request is in flight.
                if (state.currentTurn === turn) {
                    if (data?.assistant_message_id || data?.user_message_id) {
                        await handleTurnSaved(data);
                    } else {
                        resetLiveRealtimeMessageState();
                    }
                    resetCurrentTurn();
                }
                return true;
            } catch (error) {
                console.error('Failed to persist realtime turn', error);
                return false;
            } finally {
                if (turn.persistPromise === request) {
                    turn.persistPromise = null;
                }
            }
        })();

        turn.persistPromise = request;
        return request;
    }

    async function interruptForNewTurn({ providerAlreadyInterrupted = false } = {}) {
        if (!turnHasContent() && !state.currentAssistantItemId && !state.assistantSpeaking) {
            return;
        }
        cancelGoogleTurnCompletion();
        state.currentTurn.interrupted = true;
        if (state.currentTurn.responseId) {
            state.ignoredResponseIds.add(state.currentTurn.responseId);
        }
        stopRemotePlaybackAndTruncate({ cancelResponse: !providerAlreadyInterrupted });
        await persistCurrentTurn({ consumeNextResponseDone: true });
    }

    function stopRemotePlaybackAndTruncate({ cancelResponse = true } = {}) {
        if (isGoogleLiveTransport()) {
            stopGooglePlayback();
            return;
        }
        if (isXaiLiveTransport()) {
            stopGooglePlayback();
            state.currentTurn.outputAudioStarted = false;
            if (cancelResponse && state.currentTurn.responseId && !state.currentTurn.responseDone) {
                sendRealtimeEvent({ type: 'response.cancel' });
            }
            return;
        }
        const audioEl = ensureRemoteAudioElement();
        try {
            audioEl.pause();
        } catch (_) {
            // no-op
        }
        state.assistantSpeaking = false;
        state.currentTurn.outputAudioStarted = false;
        state.pendingRemotePlayback = false;
        // With WebRTC and server VAD, OpenAI performs playback-aware truncation
        // automatically. Sending audio_end_ms: 0 erased the entire assistant
        // item and raced the provider's own interruption handling.
        if (cancelResponse && state.currentTurn.responseId && !state.currentTurn.responseDone) {
            sendRealtimeEvent({ type: 'response.cancel' });
        }
    }

    function attachRemoteAudio(track, streamFromEvent) {
        const stream = ensureRemoteStream();
        const audioTrack = track || streamFromEvent?.getAudioTracks?.()[0] || null;
        if (!audioTrack) {
            state.pendingRemotePlayback = true;
            return;
        }

        const alreadyAttached = stream.getAudioTracks().some((existingTrack) => existingTrack.id === audioTrack.id);
        if (!alreadyAttached) {
            stream.addTrack(audioTrack);
        }

        audioTrack.addEventListener('ended', () => {
            if (state.remoteStream) {
                state.remoteStream.removeTrack(audioTrack);
            }
        });

        syncRemoteAudioPlayback().catch(() => {});
    }

    function isCurrentProviderEventOrigin(origin) {
        return Boolean(
            origin
            && origin.startGeneration === state.startAttemptId
            && origin.sessionId === state.sessionId
            && (!origin.socket || origin.socket === state.ws)
        );
    }

    async function executeToolCall(
        item,
        {
            provider = 'openai',
            providerEventOrigin = null,
            requestContinuation = true,
        } = {},
    ) {
        const callId = String(item?.call_id || item?.id || '').trim();
        const toolName = String(item?.name || '').trim();
        const sessionId = state.sessionId;
        if (!callId || !toolName || !sessionId) {
            return;
        }
        if (providerEventOrigin && !isCurrentProviderEventOrigin(providerEventOrigin)) {
            return;
        }
        // OpenAI-compatible realtime providers can emit both
        // response.function_call_arguments.done and response.output_item.done
        // for the same call. The backend execution is idempotent as a final
        // safeguard, while this local guard also avoids submitting duplicate
        // provider outputs and continuation requests.
        const handledIds = state.currentTurn.handledToolCallIds;
        if (handledIds instanceof Set && handledIds.has(callId)) {
            return;
        }
        handledIds?.add?.(callId);

        let argumentsPayload = {};
        if (typeof item?.arguments === 'string') {
            try {
                const parsed = JSON.parse(item.arguments);
                if (parsed && typeof parsed === 'object') {
                    argumentsPayload = parsed;
                }
            } catch (_) {
                argumentsPayload = {};
            }
        } else if (item?.args && typeof item.args === 'object') {
            argumentsPayload = item.args;
        } else if (item?.arguments && typeof item.arguments === 'object') {
            argumentsPayload = item.arguments;
        }

        // Realtime provider events bypass the normal chat stream protocol, so
        // explicitly feed this call into the shared tool renderer before the
        // potentially long-running backend execution begins.
        renderLiveToolCall(callId, toolName, argumentsPayload);

        let output = '';
        const toolController = provider === 'google' ? new AbortController() : null;
        if (toolController) {
            state.googleToolCallControllers.set(callId, toolController);
        }
        try {
            const pendingResponse = await window.authedFetch(`/api/v1/realtime/session/${encodeURIComponent(sessionId)}/tool-call/pending`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    call_id: callId,
                    tool_name: toolName,
                }),
            });
            if (providerEventOrigin && !isCurrentProviderEventOrigin(providerEventOrigin)) return;
            if (!pendingResponse.ok) {
                const pendingData = await pendingResponse.json().catch(() => ({}));
                if (providerEventOrigin && !isCurrentProviderEventOrigin(providerEventOrigin)) return;
                output = String(pendingData?.detail || `Tool call was rejected (${pendingResponse.status})`);
                throw new Error(output);
            }

            const response = await window.authedFetch(`/api/v1/realtime/session/${encodeURIComponent(sessionId)}/tool-call`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    call_id: callId,
                    turn_id: state.currentTurn.turnId,
                    tool_name: toolName,
                    arguments: argumentsPayload,
                }),
                signal: toolController?.signal,
            });
            if (providerEventOrigin && !isCurrentProviderEventOrigin(providerEventOrigin)) return;
            const data = await response.json().catch(() => ({}));
            if (providerEventOrigin && !isCurrentProviderEventOrigin(providerEventOrigin)) return;
            if (!response.ok) {
                output = String(data?.detail || `Tool call failed (${response.status})`);
            } else {
                output = String(data?.output || '');
            }
        } catch (error) {
            if (error?.name === 'AbortError') {
                return;
            }
            output = output || String(error?.message || 'Tool call failed');
        } finally {
            if (toolController) {
                state.googleToolCallControllers.delete(callId);
            }
        }

        if (providerEventOrigin && !isCurrentProviderEventOrigin(providerEventOrigin)) return;
        if (provider === 'google') {
            // A Google cancellation means the provider no longer accepts a
            // response for this call. The backend may already have completed,
            // but suppressing the stale response prevents it from affecting
            // the ongoing Live conversation.
            if (state.googleCancelledToolCallIds.delete(callId)) {
                return;
            }
            sendGoogleRealtimeMessage({
                toolResponse: {
                    functionResponses: [
                        {
                            id: callId,
                            name: toolName,
                            response: {
                                output,
                            },
                        },
                    ],
                },
            });
            return;
        }

        sendRealtimeEvent({
            type: 'conversation.item.create',
            item: {
                type: 'function_call_output',
                call_id: callId,
                output,
            },
        });
        if (!requestContinuation) return;
        await waitForXaiPlaybackBeforeContinuation(providerEventOrigin);
        if (providerEventOrigin && !isCurrentProviderEventOrigin(providerEventOrigin)) return;
        sendRealtimeEvent({ type: 'response.create' });
    }

    function queueXaiToolCall(item) {
        const callId = String(item?.call_id || item?.id || '').trim();
        const toolName = String(item?.name || '').trim();
        const pendingCalls = state.currentTurn.pendingToolCalls;
        if (
            !callId
            || !toolName
            || !(pendingCalls instanceof Map)
            || state.currentTurn.handledToolCallIds?.has?.(callId)
        ) {
            return false;
        }
        // response.output_item.done and response.function_call_arguments.done
        // can describe the same call. Keeping the last complete object gives
        // the batch one canonical entry without losing parallel calls.
        pendingCalls.set(callId, item);
        return true;
    }

    async function waitForXaiPlaybackBeforeContinuation(providerEventOrigin) {
        if (!isXaiLiveTransport()) return;
        // xAI recommends submitting tool outputs immediately, then waiting for
        // any preceding audio to finish before requesting the spoken follow-up.
        const playbackDeadline = Date.now() + 30_000;
        while (
            state.googlePlaybackSources.size
            && Date.now() < playbackDeadline
            && state.active
            && (!providerEventOrigin || isCurrentProviderEventOrigin(providerEventOrigin))
        ) {
            await new Promise((resolve) => window.setTimeout(resolve, 25));
        }
    }

    async function flushPendingXaiToolCalls(providerEventOrigin) {
        const pendingCalls = state.currentTurn.pendingToolCalls;
        if (!(pendingCalls instanceof Map) || !pendingCalls.size) {
            return false;
        }
        const calls = Array.from(pendingCalls.values());
        pendingCalls.clear();

        // xAI can emit multiple function calls in one response. Resolve every
        // call and submit every output before sending exactly one response.create.
        await Promise.all(
            calls.map((item) => executeToolCall(
                item,
                {
                    providerEventOrigin,
                    requestContinuation: false,
                },
            )),
        );
        if (providerEventOrigin && !isCurrentProviderEventOrigin(providerEventOrigin)) {
            return true;
        }
        await waitForXaiPlaybackBeforeContinuation(providerEventOrigin);
        if (providerEventOrigin && !isCurrentProviderEventOrigin(providerEventOrigin)) {
            return true;
        }
        sendRealtimeEvent({ type: 'response.create' });
        return true;
    }

    function cancelGoogleTurnCompletion() {
        if (state.googleTurnCompleteTimer) {
            window.clearTimeout(state.googleTurnCompleteTimer);
            state.googleTurnCompleteTimer = null;
        }
        state.googleTurnCompleteOrigin = null;
    }

    function mergeGoogleTranscriptChunk(currentTranscript, incomingChunk) {
        const current = String(currentTranscript || '');
        const incoming = String(incomingChunk || '');
        if (!incoming) return current;
        if (!current) return incoming;

        // Some model revisions emit deltas while others occasionally send a
        // cumulative correction. Accept both without duplicating shared text.
        if (incoming.startsWith(current)) return incoming;
        if (current.endsWith(incoming)) return current;

        const maximumOverlap = Math.min(current.length, incoming.length);
        for (let overlap = maximumOverlap; overlap > 0; overlap -= 1) {
            if (current.slice(-overlap) === incoming.slice(0, overlap)) {
                return `${current}${incoming.slice(overlap)}`;
            }
        }
        return `${current}${incoming}`;
    }

    function scheduleGoogleTurnCompletion(providerEventOrigin) {
        if (!isCurrentProviderEventOrigin(providerEventOrigin)) return;
        if (state.googleTurnCompleteTimer) {
            window.clearTimeout(state.googleTurnCompleteTimer);
        }
        state.googleTurnCompleteOrigin = providerEventOrigin;
        state.googleTurnCompleteTimer = window.setTimeout(() => {
            const completionOrigin = state.googleTurnCompleteOrigin;
            state.googleTurnCompleteTimer = null;
            state.googleTurnCompleteOrigin = null;

            // Join the same queue as WebSocket messages. This guarantees the
            // save cannot reset the turn while an already-delivered trailing
            // transcription event is still mutating it.
            const queued = state.providerEventQueue
                .catch(() => {})
                .then(async () => {
                    if (!isCurrentProviderEventOrigin(completionOrigin)) return;
                    await persistCurrentTurn();
                    if (!isCurrentProviderEventOrigin(completionOrigin)) return;
                    if (!state.assistantSpeaking) {
                        updateActivity('listening');
                    }
                });
            state.providerEventQueue = queued;
            queued.catch((error) => {
                console.error('Failed to complete Google Live turn', error);
            });
        }, GOOGLE_TRANSCRIPTION_SETTLE_MS);
    }

    async function handleGoogleServerContent(serverContent, providerEventOrigin) {
        if (
            !serverContent
            || typeof serverContent !== 'object'
            || !isCurrentProviderEventOrigin(providerEventOrigin)
        ) return;

        if (serverContent.interrupted) {
            cancelGoogleTurnCompletion();
            await interruptForNewTurn();
            if (!isCurrentProviderEventOrigin(providerEventOrigin)) return;
            stopGooglePlayback();
            updateActivity('listening');
        }

        const inputTranscription = serverContent.inputTranscription;
        if (inputTranscription && typeof inputTranscription.text === 'string') {
            state.currentTurn.userTranscript = mergeGoogleTranscriptChunk(
                state.currentTurn.userTranscript,
                inputTranscription.text,
            );
            renderLiveUserTranscript(state.currentTurn.userTranscript);
            if (state.googleTurnCompleteTimer) {
                scheduleGoogleTurnCompletion(providerEventOrigin);
            }
        }

        const outputTranscription = serverContent.outputTranscription;
        if (outputTranscription && typeof outputTranscription.text === 'string') {
            state.currentTurn.assistantTranscript = mergeGoogleTranscriptChunk(
                state.currentTurn.assistantTranscript,
                outputTranscription.text,
            );
            renderLiveAssistantTranscript(state.currentTurn.assistantTranscript);
            if (state.googleTurnCompleteTimer) {
                scheduleGoogleTurnCompletion(providerEventOrigin);
            }
        }

        const parts = Array.isArray(serverContent.modelTurn?.parts) ? serverContent.modelTurn.parts : [];
        for (const part of parts) {
            if (part?.inlineData?.data) {
                await playGoogleAudioChunk(part.inlineData.data, part.inlineData.mimeType);
                if (!isCurrentProviderEventOrigin(providerEventOrigin)) return;
                continue;
            }
            if (typeof part?.text === 'string' && part.text.trim()) {
                state.currentTurn.assistantTranscript = mergeGoogleTranscriptChunk(
                    state.currentTurn.assistantTranscript,
                    part.text,
                );
                renderLiveAssistantTranscript(state.currentTurn.assistantTranscript);
            }
        }

        if (serverContent.waitingForInput && state.active && !state.assistantSpeaking) {
            updateActivity('listening');
        }

        if (serverContent.turnComplete) {
            scheduleGoogleTurnCompletion(providerEventOrigin);
        }
    }

    async function handleGoogleLiveMessage(message, providerEventOrigin) {
        if (
            !message
            || typeof message !== 'object'
            || !isCurrentProviderEventOrigin(providerEventOrigin)
        ) return;

        if (message.usageMetadata) {
            state.currentTurn.usage = normalizeGoogleUsageMetadata(message.usageMetadata);
        }

        if (message.sessionResumptionUpdate) {
            const newHandle = String(message.sessionResumptionUpdate.newHandle || '').trim();
            if (message.sessionResumptionUpdate.resumable && newHandle) {
                state.googleSessionHandle = newHandle;
            }
        }

        if (message.goAway) {
            state.googleGoAwaySeen = true;
            state.googlePendingReconnectReason = 'server_goaway';
            if (state.active && state.googleSessionHandle && !state.googleReconnectInFlight) {
                reconnectGoogleLiveSocket({ previousSocket: providerEventOrigin.socket }).catch(async (error) => {
                    console.error('Failed to reconnect after Google Live GoAway', error);
                    await stop({ skipServerStop: false, silent: true, reason: 'google_goaway_reconnect_failed' });
                });
            }
        }

        if (message.toolCallCancellation?.ids?.length) {
            for (const rawCallId of message.toolCallCancellation.ids) {
                const callId = String(rawCallId || '').trim();
                if (!callId) continue;
                state.googleCancelledToolCallIds.add(callId);
                state.googleToolCallControllers.get(callId)?.abort?.();
            }
        }

        if (message.toolCall?.functionCalls?.length) {
            for (const functionCall of message.toolCall.functionCalls) {
                await executeToolCall(functionCall, {
                    provider: 'google',
                    providerEventOrigin,
                });
                if (!isCurrentProviderEventOrigin(providerEventOrigin)) return;
            }
        }

        if (message.serverContent) {
            await handleGoogleServerContent(
                message.serverContent,
                providerEventOrigin,
            );
        }
    }

    function queueGoogleLiveMessage(message, providerEventOrigin) {
        // A WebSocket MessageEvent does not await async work. Serialize Google
        // events with OpenAI events so persistence, tool calls, reconnects, and
        // transcription updates cannot race shared turn state.
        const queued = state.providerEventQueue
            .catch(() => {})
            .then(() => handleGoogleLiveMessage(message, providerEventOrigin));
        state.providerEventQueue = queued;
        queued.catch((error) => {
            console.error('Failed to handle Google Live message', error);
        });
    }

    function responseContainsFunctionCall(response) {
        const output = Array.isArray(response?.output) ? response.output : [];
        return output.some((item) => item?.type === 'function_call');
    }

    function isBenignOpenAiCancellationError(event) {
        const error = event?.error && typeof event.error === 'object' ? event.error : {};
        const code = String(error.code || '').trim().toLowerCase();
        const message = String(error.message || event?.message || '').trim().toLowerCase();
        return (
            (code.includes('cancel') || message.includes('cancellation failed'))
            && message.includes('no active response')
        );
    }

    async function persistCompletedOpenAiTurnIfReady() {
        if (!state.currentTurn.responseDone || state.currentTurn.responseHasFunctionCall) {
            return false;
        }
        if (state.currentTurn.outputAudioStarted || state.assistantSpeaking) {
            return false;
        }
        return persistCurrentTurn();
    }

    async function handleProviderEvent(event, providerEventOrigin) {
        if (!event || typeof event !== 'object' || !isCurrentProviderEventOrigin(providerEventOrigin)) return;

        switch (event.type) {
            case 'session.created':
            case 'session.updated':
                return;

            case 'input_audio_buffer.speech_started':
                if (state.assistantSpeaking || state.currentAssistantItemId || String(state.currentTurn.assistantTranscript || '').trim()) {
                    // Server VAD has already cancelled and playback-truncated
                    // the response. Finish the old turn before queued input
                    // transcription events begin populating the new one.
                    await interruptForNewTurn({ providerAlreadyInterrupted: true });
                    if (!isCurrentProviderEventOrigin(providerEventOrigin)) return;
                }
                updateActivity('listening');
                return;

            case 'input_audio_buffer.speech_stopped':
            case 'input_audio_buffer.committed':
                updateActivity('thinking');
                return;

            case 'conversation.item.input_audio_transcription.delta':
                state.currentTurn.userTranscript = `${state.currentTurn.userTranscript || ''}${String(event.delta || '')}`;
                renderLiveUserTranscript(state.currentTurn.userTranscript);
                return;

            case 'conversation.item.input_audio_transcription.updated':
                // xAI's event is cumulative and may correct earlier words.
                state.currentTurn.userTranscript = String(
                    event.transcript || event.text || '',
                ).trim();
                renderLiveUserTranscript(state.currentTurn.userTranscript);
                return;

            case 'conversation.item.input_audio_transcription.completed':
                state.currentTurn.userTranscript = String(event.transcript || '').trim();
                renderLiveUserTranscript(state.currentTurn.userTranscript);
                return;

            case 'response.created':
                state.currentTurn.responseId = String(event.response?.id || '').trim() || null;
                state.currentTurn.responseDone = false;
                state.currentTurn.responseHasFunctionCall = false;
                state.currentTurn.outputAudioStarted = false;
                state.currentTurn.usage = null;
                state.currentAssistantItemId = null;
                ensureLiveAssistantMessageElement();
                updateActivity('thinking');
                return;

            case 'output_audio_buffer.started':
                state.currentTurn.outputAudioStarted = true;
                state.assistantSpeaking = true;
                updateActivity('speaking');
                await syncRemoteAudioPlayback();
                if (!isCurrentProviderEventOrigin(providerEventOrigin)) return;
                return;

            case 'output_audio_buffer.stopped':
            case 'output_audio_buffer.cleared':
                state.currentTurn.outputAudioStarted = false;
                state.assistantSpeaking = false;
                await persistCompletedOpenAiTurnIfReady();
                if (!isCurrentProviderEventOrigin(providerEventOrigin)) return;
                if (state.active) {
                    updateActivity('listening');
                }
                return;

            case 'response.output_item.added':
            case 'response.output_item.created':
                if (event.item?.role === 'assistant') {
                    state.currentAssistantItemId = event.item.id || null;
                }
                if (event.item?.type === 'function_call') {
                    state.currentTurn.responseHasFunctionCall = true;
                }
                return;

            case 'response.output_item.done':
                if (event.item?.type === 'function_call') {
                    state.currentTurn.responseHasFunctionCall = true;
                    if (isXaiLiveTransport()) {
                        queueXaiToolCall(event.item);
                        return;
                    }
                    await executeToolCall(event.item, { providerEventOrigin });
                    if (!isCurrentProviderEventOrigin(providerEventOrigin)) return;
                }
                return;

            case 'response.output_text.delta':
            case 'response.text.delta':
                state.currentTurn.assistantTranscript = `${state.currentTurn.assistantTranscript || ''}${String(event.delta || '')}`;
                renderLiveAssistantTranscript(state.currentTurn.assistantTranscript);
                return;

            case 'response.output_text.done':
                if (typeof event.text === 'string') {
                    state.currentTurn.assistantTranscript = event.text.trim();
                    renderLiveAssistantTranscript(state.currentTurn.assistantTranscript);
                }
                return;

            case 'response.output_audio_transcript.delta':
                state.currentTurn.assistantTranscript = `${state.currentTurn.assistantTranscript || ''}${String(event.delta || '')}`;
                renderLiveAssistantTranscript(state.currentTurn.assistantTranscript);
                return;

            case 'response.output_audio_transcript.done':
                if (typeof event.transcript === 'string') {
                    state.currentTurn.assistantTranscript = event.transcript.trim();
                    renderLiveAssistantTranscript(state.currentTurn.assistantTranscript);
                }
                return;

            case 'response.output_audio.delta':
            case 'response.audio.delta':
                if (isXaiLiveTransport() && event.delta) {
                    state.currentTurn.outputAudioStarted = true;
                    await playGoogleAudioChunk(
                        event.delta,
                        'audio/pcm;rate=24000',
                    );
                    if (!isCurrentProviderEventOrigin(providerEventOrigin)) return;
                }
                return;

            case 'response.output_audio.done':
            case 'response.audio.done':
                if (
                    isXaiLiveTransport()
                    && !state.googlePlaybackSources.size
                ) {
                    state.currentTurn.outputAudioStarted = false;
                    state.assistantSpeaking = false;
                    await persistCompletedOpenAiTurnIfReady();
                    if (!isCurrentProviderEventOrigin(providerEventOrigin)) return;
                }
                return;

            case 'response.function_call_arguments.done':
                state.currentTurn.responseHasFunctionCall = true;
                {
                    const functionCall = {
                        type: 'function_call',
                        call_id: event.call_id,
                        id: event.item_id,
                        name: event.name,
                        arguments: event.arguments,
                    };
                    if (isXaiLiveTransport()) {
                        queueXaiToolCall(functionCall);
                        return;
                    }
                    await executeToolCall(
                        functionCall,
                        { providerEventOrigin },
                    );
                }
                return;

            case 'response.done':
                {
                    const response = event.response && typeof event.response === 'object' ? event.response : {};
                    const responseId = String(response.id || '').trim();
                    if (responseId && state.ignoredResponseIds.delete(responseId)) {
                        return;
                    }

                    state.currentTurn.responseId = responseId || state.currentTurn.responseId;
                    state.currentTurn.responseHasFunctionCall = state.currentTurn.responseHasFunctionCall
                        || responseContainsFunctionCall(response);
                    state.currentTurn.usage = response.usage || null;
                    const responseStatus = String(response.status || '').trim();

                    // Preserve every terminal provider response, including the
                    // intermediate response that requested a tool. The turn
                    // endpoint commits these response-grain usage facts with
                    // the chat messages so retries remain idempotent.
                    if (
                        responseId
                        && !state.currentTurn.providerInteractions.some(
                            (interaction) => interaction?.response_id === responseId,
                        )
                    ) {
                        state.currentTurn.providerInteractions.push({
                            response_id: responseId,
                            status: responseStatus || null,
                            usage: response.usage || null,
                            error_message: String(response.status_details?.error?.message || '').trim() || null,
                            completed_at: new Date().toISOString(),
                        });
                    }

                    if (
                        isXaiLiveTransport()
                        && state.currentTurn.responseHasFunctionCall
                        && responseStatus !== 'cancelled'
                    ) {
                        for (const item of Array.isArray(response.output) ? response.output : []) {
                            if (item?.type === 'function_call') {
                                queueXaiToolCall(item);
                            }
                        }
                        await flushPendingXaiToolCalls(providerEventOrigin);
                        return;
                    }

                    // A function-call response is only the middle of a user
                    // turn. executeToolCall sends function_call_output and a
                    // continuation response.create; persisting here separated
                    // the final answer from its user message.
                    if (
                        state.currentTurn.responseHasFunctionCall
                        && responseStatus !== 'cancelled'
                    ) {
                        updateActivity('thinking');
                        return;
                    }

                    if (responseStatus === 'cancelled') {
                        state.currentTurn.pendingToolCalls?.clear?.();
                        state.currentTurn.interrupted = true;
                        state.currentTurn.outputAudioStarted = false;
                        state.assistantSpeaking = false;
                        await persistCurrentTurn();
                        if (!isCurrentProviderEventOrigin(providerEventOrigin)) return;
                        if (state.active) updateActivity('listening');
                        return;
                    }

                    state.currentTurn.responseDone = true;
                    await persistCompletedOpenAiTurnIfReady();
                    if (!isCurrentProviderEventOrigin(providerEventOrigin)) return;
                    if (!state.assistantSpeaking && state.active) {
                        updateActivity('listening');
                    }
                    return;
                }

            case 'response.cancelled':
                if (!state.assistantSpeaking && state.active) {
                    updateActivity('listening');
                }
                return;

            case 'error': {
                if (isBenignOpenAiCancellationError(event)) {
                    // response.done can cross the browser event boundary just
                    // before a local interruption request. The desired end
                    // state is already true, so this provider reply is an
                    // idempotent cancellation acknowledgement, not a call error.
                    return;
                }
                const message = String(event.error?.message || event.message || t('chat_realtime_call_error', 'Realtime call error'));
                notify('error', message);
                await persistCurrentTurn({ errorMessage: message });
                if (!isCurrentProviderEventOrigin(providerEventOrigin)) return;
                return;
            }

            default:
                return;
        }
    }

    function queueProviderEvent(event, providerEventOrigin) {
        // MessageEvent listeners do not await async handlers. Chain each event
        // so response.done cannot reset a turn while transcription or tool-call
        // events from the same ordered provider stream are still being handled.
        const queued = state.providerEventQueue
            .catch(() => {})
            .then(() => handleProviderEvent(event, providerEventOrigin));
        state.providerEventQueue = queued;
        queued.catch((error) => {
            console.error('Failed to handle realtime event', error);
        });
    }

    function setupDataChannel(dataChannel, providerEventOrigin) {
        state.dc = dataChannel;
        return new Promise((resolve, reject) => {
            let settled = false;
            const timeout = setTimeout(() => {
                if (settled) return;
                settled = true;
                reject(new Error(t('chat_realtime_data_channel_timeout', 'Timed out waiting for realtime data channel')));
            }, 12000);

            dataChannel.addEventListener('open', async () => {
                if (settled) return;
                // The ephemeral key is already bound to the complete session
                // configuration by the backend. Sending another session update
                // here can include immutable fields such as model and make an
                // otherwise healthy connection fail on newer Realtime APIs.
                settled = true;
                clearTimeout(timeout);
                resolve(true);
            });

            dataChannel.addEventListener('message', (messageEvent) => {
                try {
                    const parsed = JSON.parse(messageEvent.data);
                    queueProviderEvent(parsed, providerEventOrigin);
                } catch (_) {
                    // no-op
                }
            });

            dataChannel.addEventListener('error', () => {
                if (settled) return;
                settled = true;
                clearTimeout(timeout);
                reject(new Error(t('chat_realtime_data_channel_failed', 'Realtime data channel failed')));
            });

            dataChannel.addEventListener('close', () => {
                if (settled) return;
                settled = true;
                clearTimeout(timeout);
                reject(new Error(t('chat_realtime_data_channel_failed', 'Realtime data channel failed')));
            });
        });
    }

    function clearPeerDisconnectTimer() {
        if (!state.peerDisconnectTimer) return;
        window.clearTimeout(state.peerDisconnectTimer);
        state.peerDisconnectTimer = null;
    }

    function formatRealtimeSdpError(status, responseBody) {
        const rawBody = String(responseBody || '').trim();
        let detail = rawBody;
        if (rawBody) {
            try {
                const parsed = JSON.parse(rawBody);
                detail = String(parsed?.error?.message || parsed?.detail || rawBody).trim();
            } catch (_) {
                detail = rawBody;
            }
        }
        if (!detail) {
            detail = t('chat_realtime_provider_error_empty', 'The provider returned an empty error response');
        }
        return formatT(
            'chat_realtime_provider_connection_failed_status',
            'OpenAI Realtime connection failed ({status}): {detail}',
            { status, detail },
        );
    }

    function buildRealtimeSignalingUrl(rawUrl) {
        const invalidSessionError = () => new Error(
            t('chat_realtime_invalid_session_response', 'Invalid realtime session response'),
        );
        if (typeof rawUrl !== 'string') {
            throw invalidSessionError();
        }
        const normalizedUrl = rawUrl.trim();
        if (!normalizedUrl) {
            throw invalidSessionError();
        }

        // Signaling must terminate at Omlorix because authedFetch attaches the
        // user's application credentials. Normalize relative URLs against the
        // current page and reject malformed or cross-origin server responses.
        let parsedUrl;
        try {
            parsedUrl = new URL(normalizedUrl, window.location.href);
        } catch (_error) {
            throw invalidSessionError();
        }
        if (
            parsedUrl.origin !== window.location.origin
            || parsedUrl.protocol !== window.location.protocol
            || parsedUrl.username
            || parsedUrl.password
        ) {
            throw invalidSessionError();
        }
        return parsedUrl.toString();
    }

    /**
     * Validate a signaling response without rewriting the provider's SDP.
     *
     * Safari requires the final SDP line to retain its CRLF terminator. Using
     * trim() for assignment here removed that terminator after JSON decoding,
     * even though OpenAI and the backend had returned a valid answer. Whitespace
     * normalization is therefore used only to decide whether the value is empty.
     *
     * @param {object|null} signalingPayload Parsed signaling response.
     * @returns {string} The provider SDP exactly as received through JSON.
     */
    function readRealtimeSdpAnswer(signalingPayload) {
        const answerSdp = typeof signalingPayload?.sdp === 'string'
            ? signalingPayload.sdp
            : '';
        if (!answerSdp.trim()) {
            throw new Error(t('chat_realtime_invalid_session_response', 'Invalid realtime session response'));
        }
        return answerSdp;
    }

    async function startPeerConnection({ signalingUrl, startAttemptId }) {
        const normalizedSignalingUrl = buildRealtimeSignalingUrl(signalingUrl);
        assertCurrentStartAttempt(startAttemptId);
        const pc = new RTCPeerConnection(getRealtimePeerConnectionConfig());
        state.pc = pc;

        pc.ontrack = (event) => {
            attachRemoteAudio(event.track, event.streams[0]);
        };

        pc.onconnectionstatechange = () => {
            if (pc.connectionState === 'connected') {
                clearPeerDisconnectTimer();
                return;
            }
            if (pc.connectionState === 'disconnected') {
                // WebRTC may report a brief disconnect while ICE changes
                // route. Give it a short recovery window before ending a call.
                clearPeerDisconnectTimer();
                state.peerDisconnectTimer = window.setTimeout(() => {
                    state.peerDisconnectTimer = null;
                    if (state.pc === pc && pc.connectionState === 'disconnected' && !state.stopping) {
                        stop({ skipServerStop: false, silent: true, reason: 'peer_disconnected' }).catch(() => {});
                    }
                }, 5000);
                return;
            }
            if (pc.connectionState === 'failed' && !state.stopping) {
                clearPeerDisconnectTimer();
                stop({ skipServerStop: false, silent: true, reason: 'peer_failed' }).catch(() => {});
            }
        };

        const localStream = await ensureLocalMicrophoneStream();
        assertCurrentStartAttempt(startAttemptId);

        const [audioTrack] = localStream.getAudioTracks();
        if (!audioTrack) {
            throw new Error(t('chat_realtime_microphone_track_missing', 'No microphone audio track is available'));
        }
        // addTrack creates the send/receive audio transceiver used by OpenAI.
        // Adding a second recv-only transceiver produces two audio m-lines and
        // causes the Realtime SDP endpoint to reject the offer.
        pc.addTrack(audioTrack, localStream);

        const dataChannel = pc.createDataChannel('oai-events');
        // The channel can emit after teardown and even after a replacement
        // call starts. Keep its immutable origin with every queued event.
        const providerEventOrigin = Object.freeze({
            sessionId: state.sessionId,
            startGeneration: startAttemptId,
        });
        const dataChannelReady = setupDataChannel(dataChannel, providerEventOrigin);

        const offer = await pc.createOffer();
        assertCurrentStartAttempt(startAttemptId);
        await pc.setLocalDescription(offer);
        assertCurrentStartAttempt(startAttemptId);

        // Omlorix owns provider signaling and retains the provider call ID. The
        // browser sends only its SDP offer to the authenticated same-origin
        // endpoint and never receives an API key or ephemeral provider secret.
        const sdpResponse = await window.authedFetch(normalizedSignalingUrl, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({ sdp: offer.sdp }),
        });
        assertCurrentStartAttempt(startAttemptId);
        const sdpResponseBody = await sdpResponse.text();
        assertCurrentStartAttempt(startAttemptId);
        if (!sdpResponse.ok) {
            throw new Error(formatRealtimeSdpError(sdpResponse.status, sdpResponseBody));
        }
        let signalingPayload = null;
        try {
            signalingPayload = JSON.parse(sdpResponseBody);
        } catch (_) {
            signalingPayload = null;
        }
        const answerSdp = readRealtimeSdpAnswer(signalingPayload);

        const answer = {
            type: 'answer',
            sdp: answerSdp,
        };
        await pc.setRemoteDescription(answer);
        assertCurrentStartAttempt(startAttemptId);
        await syncRemoteAudioPlayback();
        assertCurrentStartAttempt(startAttemptId);
        await dataChannelReady;
        assertCurrentStartAttempt(startAttemptId);
    }

    async function requestRealtimeConnection(sessionHandle = null) {
        if (!state.sessionId) {
            throw new Error(t('chat_realtime_session_missing', 'Realtime session is missing'));
        }
        const response = await window.authedFetch(`/api/v1/realtime/session/${encodeURIComponent(state.sessionId)}/connection`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({
                session_handle: sessionHandle || null,
            }),
        });
        if (!response.ok) {
            const errorData = await response.json().catch(() => ({}));
            throw new Error(errorData?.detail || formatT('chat_realtime_refresh_connection_failed_status', 'Failed to refresh realtime connection ({status})', { status: response.status }));
        }
        return response.json().catch(() => ({}));
    }

    function buildRealtimeWebSocketUrl(rawUrl) {
        const normalizedUrl = String(rawUrl || '').trim();
        if (!normalizedUrl) {
            throw new Error(t('chat_realtime_websocket_url_missing', 'Realtime websocket URL is missing'));
        }

        // Realtime WebSockets must terminate at Omlorix. Refusing cross-origin
        // URLs prevents a future response regression from restoring direct
        // browser-to-provider credentials or bypassing backend enforcement.
        const parsedUrl = new URL(normalizedUrl, window.location.href);
        if (parsedUrl.origin !== window.location.origin) {
            throw new Error(t('chat_realtime_invalid_session_response', 'Invalid realtime session response'));
        }
        parsedUrl.protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        return parsedUrl.toString();
    }

    async function decodeGoogleLiveMessageData(rawData) {
        let jsonText = rawData;

        // Google sends JSON in binary WebSocket frames. Safari exposes those
        // frames as Blob objects by default, while other browsers may expose an
        // ArrayBuffer or a string. Normalize every representation before
        // parsing so setupComplete and later provider events are not discarded.
        if (rawData && typeof rawData.text === 'function') {
            jsonText = await rawData.text();
        } else if (rawData instanceof ArrayBuffer) {
            jsonText = new TextDecoder().decode(rawData);
        } else if (ArrayBuffer.isView(rawData)) {
            jsonText = new TextDecoder().decode(
                new Uint8Array(rawData.buffer, rawData.byteOffset, rawData.byteLength),
            );
        }

        if (typeof jsonText !== 'string') {
            return null;
        }

        try {
            return JSON.parse(jsonText);
        } catch (_) {
            return null;
        }
    }

    async function connectGoogleLiveSocket({ reconnect = false } = {}) {
        if (!state.websocketUrl) {
            throw new Error(t('chat_realtime_websocket_url_missing', 'Realtime websocket URL is missing'));
        }

        const socket = new WebSocket(buildRealtimeWebSocketUrl(state.websocketUrl));
        // Prefer synchronous ArrayBuffer decoding for provider binary frames.
        // The Blob fallback above remains necessary for Safari implementations
        // that have already selected their message representation.
        socket.binaryType = 'arraybuffer';
        const providerEventOrigin = {
            sessionId: state.sessionId,
            startGeneration: state.startAttemptId,
            socket,
        };

        return new Promise((resolve, reject) => {
            let settled = false;
            let setupSucceeded = false;
            let rawMessageQueue = Promise.resolve();

            const retireGoogleLiveCandidate = () => {
                socket.__omlorixIntentionalClose = true;
                if (socket.readyState < WebSocket.CLOSING) {
                    try {
                        socket.close();
                    } catch (_) {
                        // The setup error is more useful to the caller than a
                        // secondary exception raised while retiring the socket.
                    }
                }
            };

            const failGoogleLiveSetup = (error, { closeSocket = true } = {}) => {
                if (settled) return;
                settled = true;
                window.clearTimeout(timeout);

                // A failed candidate must never remain available to receive or
                // send call traffic while a later reconnect attempt is running.
                if (closeSocket) {
                    retireGoogleLiveCandidate();
                }
                reject(error);
            };

            const timeout = window.setTimeout(() => {
                failGoogleLiveSetup(new Error(t('chat_realtime_google_setup_timeout', 'Timed out waiting for Google Live session setup')));
            }, 15000);

            socket.onopen = () => {
                if (settled) {
                    // Closing a CONNECTING websocket may fail in some browser
                    // implementations. Retry retirement once it reaches OPEN.
                    retireGoogleLiveCandidate();
                }
            };

            socket.onmessage = (event) => {
                // Blob.text() is asynchronous. Keep raw frames in arrival order
                // so a later transcription or turn-complete event cannot pass
                // an earlier frame while Safari is decoding it.
                const queued = rawMessageQueue
                    .catch(() => {})
                    .then(async () => {
                        const parsed = await decodeGoogleLiveMessageData(event.data);
                        if (!parsed || typeof parsed !== 'object') {
                            return;
                        }

                        if (parsed.setupComplete && !settled) {
                            // Keep the previous socket active until Google accepts the
                            // replacement. This atomic promotion prevents microphone
                            // frames from reaching a candidate before setup completes.
                            setupSucceeded = true;
                            settled = true;
                            window.clearTimeout(timeout);
                            state.ws = socket;
                            resolve(true);
                        }

                        // Google documents setupComplete as the first server response.
                        // Do not process call traffic from an uninitialized candidate.
                        if (!setupSucceeded) {
                            return;
                        }

                        queueGoogleLiveMessage(parsed, providerEventOrigin);
                    });
                rawMessageQueue = queued;
                queued.catch((error) => {
                    if (!setupSucceeded) {
                        failGoogleLiveSetup(error);
                        return;
                    }
                    console.error('Failed to decode Google Live message', error);
                });
            };

            socket.onerror = () => {
                if (!setupSucceeded) {
                    failGoogleLiveSetup(new Error(t('chat_realtime_google_websocket_failed', 'Google Live websocket failed')));
                }
            };

            socket.onclose = async () => {
                if (state.ws === socket) {
                    state.ws = null;
                }
                if (!setupSucceeded) {
                    failGoogleLiveSetup(
                        new Error(t('chat_realtime_google_websocket_closed_setup', 'Google Live websocket closed during setup')),
                        { closeSocket: false },
                    );
                    return;
                }
                if (socket.__omlorixIntentionalClose) {
                    return;
                }
                if (state.stopping || !state.active) {
                    return;
                }
                if (state.googleReconnectInFlight) {
                    return;
                }
                state.googlePendingReconnectReason = state.googlePendingReconnectReason || (reconnect ? 'socket_reconnect' : 'socket_closed');
                try {
                    await reconnectGoogleLiveSocket();
                } catch (error) {
                    console.error('Failed to reconnect Google Live socket', error);
                    await stop({ skipServerStop: false, silent: true, reason: 'google_socket_closed' });
                }
            };
        });
    }

    async function connectXaiLiveSocket() {
        if (!state.websocketUrl) {
            throw new Error(t('chat_realtime_websocket_url_missing', 'Realtime websocket URL is missing'));
        }
        const socket = new WebSocket(buildRealtimeWebSocketUrl(state.websocketUrl));
        const providerEventOrigin = {
            sessionId: state.sessionId,
            startGeneration: state.startAttemptId,
            socket,
        };

        return new Promise((resolve, reject) => {
            let settled = false;
            const timeout = window.setTimeout(() => {
                if (settled) return;
                settled = true;
                socket.close();
                reject(new Error(t('chat_realtime_xai_setup_timeout', 'Timed out waiting for xAI realtime session setup')));
            }, 15000);

            socket.onmessage = async (event) => {
                const parsed = await decodeGoogleLiveMessageData(event.data);
                if (!parsed || typeof parsed !== 'object') return;
                if (parsed.type === 'session.updated' && !settled) {
                    settled = true;
                    window.clearTimeout(timeout);
                    state.ws = socket;
                    resolve(true);
                }
                if (settled) {
                    queueProviderEvent(parsed, providerEventOrigin);
                }
            };
            socket.onerror = () => {
                if (settled) return;
                settled = true;
                window.clearTimeout(timeout);
                reject(new Error(t('chat_realtime_xai_websocket_failed', 'xAI realtime websocket failed')));
            };
            socket.onclose = () => {
                if (state.ws === socket) state.ws = null;
                if (!settled) {
                    settled = true;
                    window.clearTimeout(timeout);
                    reject(new Error(t('chat_realtime_xai_websocket_closed_setup', 'xAI realtime websocket closed during setup')));
                    return;
                }
                if (!state.stopping && state.active) {
                    stop({
                        skipServerStop: false,
                        silent: true,
                        reason: 'xai_socket_closed',
                    }).catch(() => {});
                }
            };
        });
    }

    async function reconnectGoogleLiveSocket({ previousSocket = state.ws } = {}) {
        if (!state.active || state.stopping || state.googleReconnectInFlight) {
            return false;
        }
        if (!state.googleSessionHandle) {
            throw new Error(t('chat_realtime_google_reconnect_handle_missing', 'No Google Live session handle is available for reconnect'));
        }

        state.googleReconnectInFlight = true;
        updateActivity('thinking');
        try {
            // The backend permits one Gemini provider socket per persisted
            // quota reservation across every app replica. Retire the old
            // browser/proxy socket before claiming its replacement.
            if (
                previousSocket
                && previousSocket.readyState < WebSocket.CLOSING
            ) {
                previousSocket.__omlorixIntentionalClose = true;
                const socketClosed = new Promise((resolve) => {
                    previousSocket.addEventListener('close', resolve, { once: true });
                });
                previousSocket.close();
                await Promise.race([
                    socketClosed,
                    new Promise((resolve) => window.setTimeout(resolve, 1000)),
                ]);
            }

            let lastError = null;
            const reconnectDeadline = Date.now() + GOOGLE_LIVE_PROXY_RELEASE_TIMEOUT_MS;
            for (let attempt = 1; Date.now() < reconnectDeadline; attempt += 1) {
                state.googleReconnectAttempts = attempt;
                if (attempt > 1) {
                    await new Promise((resolve) => window.setTimeout(resolve, Math.min(2000, 250 * (2 ** (attempt - 1)))));
                }
                try {
                    const data = await requestRealtimeConnection(state.googleSessionHandle);
                    const websocketUrl = String(data?.websocket_url || '').trim();
                    if (!websocketUrl) {
                        throw new Error(t('chat_realtime_google_reconnect_url_missing', 'Google Live reconnect did not return a websocket URL'));
                    }
                    state.transport = String(data?.transport || state.transport || '').trim() || state.transport;
                    state.protocolVersion = String(data?.protocol_version || state.protocolVersion || '').trim() || state.protocolVersion;
                    state.provider = String(data?.provider || state.provider || '').trim() || state.provider;
                    state.websocketUrl = websocketUrl;
                    state.sessionConfig = data?.session || state.sessionConfig;
                    await connectGoogleLiveSocket({ reconnect: true });
                    await startGoogleMicrophoneStreaming();
                    state.googleGoAwaySeen = false;
                    state.googlePendingReconnectReason = null;
                    state.googleReconnectAttempts = 0;
                    updateActivity(state.assistantSpeaking ? 'speaking' : 'listening');
                    return true;
                } catch (error) {
                    lastError = error;
                }
            }
            const exhaustedError = new Error(t('chat_realtime_google_reconnect_exhausted', 'Google Live reconnect attempts were exhausted'));
            exhaustedError.cause = lastError;
            throw exhaustedError;
        } finally {
            state.googleReconnectInFlight = false;
        }
    }

    async function startGoogleLiveCall() {
        await connectGoogleLiveSocket();
        await startGoogleMicrophoneStreaming();
        // The socket can close after setupComplete while microphone startup is
        // still awaiting Web Audio. Fail this start instead of marking a call
        // active with no provider transport; once this check passes, start()
        // marks the call active in the same JavaScript task.
        if (!state.ws || state.ws.readyState !== WebSocket.OPEN) {
            throw new Error(t('chat_realtime_google_websocket_failed', 'Google Live websocket failed'));
        }
    }

    async function startXaiLiveCall() {
        await connectXaiLiveSocket();
        await startGoogleMicrophoneStreaming();
        if (!state.ws || state.ws.readyState !== WebSocket.OPEN) {
            throw new Error(t('chat_realtime_xai_websocket_failed', 'xAI realtime websocket failed'));
        }
    }

    async function start() {
        if (state.active || state.connecting) return true;
        if (!navigator.mediaDevices || typeof navigator.mediaDevices.getUserMedia !== 'function') {
            notify('error', t('chat_realtime_microphone_unsupported', 'Microphone access is not supported in this browser'));
            return false;
        }

        const startAttemptId = state.startAttemptId + 1;
        state.startAttemptId = startAttemptId;
        state.connecting = true;
        state.ready = false;
        cancelGoogleTurnCompletion();
        resetCurrentTurn();
        resetLiveRealtimeMessageState();
        updateActivity('idle');
        updateCallButton();
        emitRealtimeState();

        try {
            // Request permission before session creation, route synchronization,
            // or SDP negotiation can detach this work from the button click.
            await ensureLocalMicrophoneStream(startAttemptId);
            assertCurrentStartAttempt(startAttemptId);
            await setupCallOrbAudioAnalyser();
            assertCurrentStartAttempt(startAttemptId);
            // Audio output unlocking is best effort and time-bounded. Running it
            // here preserves the click/permission gesture without delaying SDP.
            await unlockAudioPlayback();
            assertCurrentStartAttempt(startAttemptId);
            const context = getRealtimeStartContext();
            const response = await window.authedFetch('/api/v1/realtime/session/start', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    chat_id: context.chatId,
                    project_id: context.projectId,
                    model_id: context.modelId,
                    skill_id: context.skillId,
                }),
            });
            if (!response.ok) {
                const errorData = await response.json().catch(() => ({}));
                const fallback = formatT('chat_realtime_start_session_failed_status', 'Failed to start realtime session ({status})', { status: response.status });
                const message = typeof resolveApiErrorMessage === 'function'
                    ? resolveApiErrorMessage(errorData, fallback)
                    : (typeof errorData?.detail === 'string' ? errorData.detail : errorData?.detail?.message) || fallback;
                const isMinuteLimit = errorData?.detail?.code === 'user_realtime_rate_limited';
                throw new Error(isMinuteLimit
                    ? t('us_rate_limits_feature_exceeded_notice', 'Minute limit reached. Try again after the reset time.')
                    : message);
            }

            const data = await response.json().catch(() => ({}));
            if (!data?.session_id || !data?.chat_id || !data?.session || !data?.transport) {
                throw new Error(t('chat_realtime_invalid_session_response', 'Invalid realtime session response'));
            }

            state.sessionId = String(data.session_id);
            state.chatId = String(data.chat_id);
            state.transport = String(data.transport || '').trim() || null;
            state.provider = String(data.provider || '').trim() || null;
            state.protocolVersion = String(data.protocol_version || '').trim() || null;
            // Validate once at the session boundary and again immediately
            // before fetch in startPeerConnection for defense in depth.
            const signalingUrl = data.signaling_url
                ? buildRealtimeSignalingUrl(data.signaling_url)
                : null;
            state.signalingUrl = signalingUrl;
            state.websocketUrl = data.websocket_url ? String(data.websocket_url) : null;
            state.sessionConfig = data.session;
            if (data.created_chat && typeof window.ensureChatSidebarRow === 'function') {
                window.ensureChatSidebarRow(state.chatId, {
                    initialTitle: '',
                    projectId: context.projectId,
                });
            }
            const maxSessionSeconds = Number(data.max_session_seconds || 0);
            const sessionLimitSource = String(data.session_limit_source || 'provider');
            state.ignoredResponseIds.clear();
            state.providerEventQueue = Promise.resolve();
            state.googleSessionHandle = null;
            state.googleReconnectAttempts = 0;
            state.googleReconnectInFlight = false;
            state.googlePendingReconnectReason = null;
            state.googleGoAwaySeen = false;
            for (const controller of state.googleToolCallControllers.values()) {
                controller.abort();
            }
            state.googleToolCallControllers.clear();
            state.googleCancelledToolCallIds.clear();
            // Mirror the immutable backend deadline for prompt UI cleanup. The
            // backend independently hangs up OpenAI or closes the Gemini proxy;
            // this client timer is convenience feedback, not quota security.
            startRealtimeMaintenance(maxSessionSeconds, sessionLimitSource, data.session_expires_at);
            await syncChatForRealtimeSession(state.chatId, { preserveRoute: state.routeModeActive });
            assertCurrentStartAttempt(startAttemptId);
            if (isWebRtcTransport()) {
                if (!window.RTCPeerConnection) {
                    throw new Error(t('chat_realtime_webrtc_unsupported', 'WebRTC is not supported in this browser'));
                }
                ensureRemoteStream();
                ensureRemoteAudioElement();
                await startPeerConnection({ signalingUrl, startAttemptId });
            } else if (isGoogleLiveTransport()) {
                await startGoogleLiveCall();
            } else if (isXaiLiveTransport()) {
                await startXaiLiveCall();
            } else {
                throw new Error(formatT('chat_realtime_unsupported_transport', 'Unsupported realtime transport: {transport}', { transport: state.transport }));
            }

            state.active = true;
            state.ready = true;
            sendRealtimeHeartbeat().catch(() => {});
            state.isMuted = false;
            if (state.routeModeActive || isCallRoutePath()) {
                state.routeModeActive = true;
                syncCallRouteHistory({ replace: true });
            }
            updateActivity('listening');
            updateCallButton();
            emitRealtimeState();
            return true;
        } catch (error) {
            if (error?.name === 'AbortError' || state.startAttemptId !== startAttemptId) {
                return false;
            }
            console.error('Failed to start realtime call', error);
            notify('error', error?.message || t('chat_realtime_start_call_failed', 'Failed to start realtime call'));
            await stop({ skipServerStop: false, silent: true, reason: 'start_failed', preserveCallRoute: true });
            return false;
        } finally {
            if (state.startAttemptId === startAttemptId) {
                state.connecting = false;
            }
            updateCallButton();
            emitRealtimeState();
        }
    }

    async function stop({ skipServerStop = false, silent = false, reason = 'client_stop', preserveCallRoute = false } = {}) {
        if (state.stopping) return true;
        state.stopping = true;
        // Invalidate a getUserMedia request that may still be awaiting the
        // user's decision. If it later resolves, start() releases its stream
        // instead of continuing the stopped call.
        state.startAttemptId += 1;
        stopRealtimeMaintenance();
        clearPeerDisconnectTimer();
        const sessionId = state.sessionId;
        const lastChatId = state.chatId;
        const restoreChatId = String(lastChatId || state.routeReturnChatId || '').trim() || null;
        const shouldRestoreFromCallRoute = state.routeModeActive && !preserveCallRoute;

        try {
            cancelGoogleTurnCompletion();
            if (turnHasContent()) {
                await persistCurrentTurn();
            }

            try {
                if (state.dc) {
                    state.dc.close();
                }
            } catch (_) {
                // no-op
            }

            try {
                if (state.ws) {
                    state.ws.close();
                }
            } catch (_) {
                // no-op
            }

            try {
                if (state.pc) {
                    state.pc.close();
                }
            } catch (_) {
                // no-op
            }

            stopLocalMediaStream(state.localStream);
            await teardownCallOrbAudioAnalyser();

            if (state.remoteAudio) {
                try {
                    state.remoteAudio.pause();
                    state.remoteAudio.removeAttribute('src');
                    state.remoteAudio.srcObject = null;
                    state.remoteAudio.load();
                } catch (_) {
                    // no-op
                }
            }

            if (state.remoteStream) {
                state.remoteStream.getTracks().forEach((track) => {
                    state.remoteStream.removeTrack(track);
                });
            }

            stopGooglePlayback();
            await stopGoogleMicrophoneStreaming();

            if (!skipServerStop && sessionId) {
                try {
                    await window.authedFetch(`/api/v1/realtime/session/${encodeURIComponent(sessionId)}/stop`, {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json',
                        },
                        body: JSON.stringify({ reason }),
                    });
                } catch (_) {
                    // no-op
                }
            }

            state.active = false;
            state.ready = false;
            state.connecting = false;
            state.transport = null;
            state.provider = null;
            state.protocolVersion = null;
            state.sessionId = null;
            state.chatId = null;
            state.signalingUrl = null;
            state.websocketUrl = null;
            state.sessionConfig = null;
            state.pc = null;
            state.dc = null;
            state.ws = null;
            state.localStream = null;
            state.remoteStream = null;
            state.remoteAudio = null;
            state.isMuted = false;
            state.assistantSpeaking = false;
            state.currentAssistantItemId = null;
            state.pendingRemotePlayback = false;
            state.ignoredResponseIds.clear();
            state.providerEventQueue = Promise.resolve();
            state.googleSessionHandle = null;
            state.googleReconnectInFlight = false;
            state.googleReconnectAttempts = 0;
            state.googlePendingReconnectReason = null;
            state.googleGoAwaySeen = false;
            state.googleTurnCompleteTimer = null;
            state.googleTurnCompleteOrigin = null;
            state.heartbeatInFlightSessionId = null;
            state.sessionExpiresAt = null;
            state.sessionLimitSource = 'provider';
            state.peerDisconnectTimer = null;
            for (const controller of state.googleToolCallControllers.values()) {
                controller.abort();
            }
            state.googleToolCallControllers.clear();
            state.googleCancelledToolCallIds.clear();
            resetCurrentTurn();
            resetLiveRealtimeMessageState();
            if (preserveCallRoute && isCallRoutePath()) {
                state.routeModeActive = true;
                enterCallRouteUi();
                syncCallRouteHistory({ replace: true });
            } else if (shouldRestoreFromCallRoute) {
                deactivateCallRoute({ restorePath: true, chatId: restoreChatId, replace: true });
            }
            updateActivity('idle');
            updateCallButton();
            emitRealtimeState();

            if (!silent) {
                notify('success', t('chat_realtime_call_ended', 'Realtime call ended'));
            }
            return true;
        } finally {
            state.stopping = false;
        }
    }

    function toggleMute() {
        if (!state.active || !state.localStream) return false;
        state.isMuted = !state.isMuted;
        state.localStream.getAudioTracks().forEach((track) => {
            track.enabled = !state.isMuted;
        });
        if (state.isMuted && isGoogleLiveTransport()) {
            // With automatic VAD enabled, Google requires this marker when a
            // microphone is turned off. The stream reopens automatically when
            // unmuting causes the next audio chunk to be sent.
            sendGoogleRealtimeMessage({
                realtimeInput: {
                    audioStreamEnd: true,
                },
            });
        }
        emitRealtimeState();
        return state.isMuted;
    }

    function interrupt() {
        if (!state.active) return false;
        state.currentTurn.interrupted = true;
        stopRemotePlaybackAndTruncate();
        updateActivity('listening');
        return true;
    }

    async function sendText(text, { fileIds = [] } = {}) {
        try {
            if (!state.active || !state.ready || !state.sessionId) return false;
            const normalizedText = String(text || '').trim();
            const normalizedFileIds = Array.isArray(fileIds)
                ? Array.from(new Set(fileIds.map((id) => String(id || '').trim()).filter(Boolean)))
                : [];
            if (!normalizedText && !normalizedFileIds.length) {
                return false;
            }

            // A response.done event may already be queued while a keyboard
            // action enters this function. Observe all provider state received
            // so far before deciding whether an explicit cancellation is needed.
            await state.providerEventQueue.catch(() => {});
            if (!state.active || !state.ready || !state.sessionId) return false;

            if (state.assistantSpeaking || state.currentAssistantItemId || String(state.currentTurn.assistantTranscript || '').trim()) {
                await interruptForNewTurn();
            }

            const response = await window.authedFetch(`/api/v1/realtime/session/${encodeURIComponent(state.sessionId)}/prepare-input`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    text: normalizedText,
                    file_ids: normalizedFileIds,
                }),
            });
            if (!response.ok) {
                const errorData = await response.json().catch(() => ({}));
                throw new Error(errorData?.detail || formatT('chat_realtime_prepare_input_failed_status', 'Failed to prepare realtime input ({status})', { status: response.status }));
            }

            const data = await response.json().catch(() => ({}));
            const displayText = String(data?.display_text || '').trim();
            const mode = String(data?.mode || 'conversation_item').trim();
            const contentParts = Array.isArray(data?.content_parts)
                ? data.content_parts.filter((part) => part && typeof part === 'object' && String(part.type || '').trim())
                : [];
            const realtimeInput = data?.realtime_input && typeof data.realtime_input === 'object'
                ? data.realtime_input
                : null;
            const resolvedFileIds = Array.isArray(data?.file_ids) ? data.file_ids.map((id) => String(id || '').trim()).filter(Boolean) : [];
            if (mode === 'conversation_item' && !contentParts.length) {
                return false;
            }
            if (
                mode === 'realtime_input'
                && (!realtimeInput || typeof realtimeInput.text !== 'string' || !realtimeInput.text.trim())
            ) {
                return false;
            }

            state.currentTurn.userTranscript = displayText;
            state.currentTurn.fileIds = resolvedFileIds;
            renderLiveUserTranscript(displayText);

            if (mode === 'realtime_input') {
                sendGoogleRealtimeMessage({
                    realtimeInput: realtimeInput,
                });
            } else {
                sendRealtimeEvent({
                    type: 'conversation.item.create',
                    item: {
                        type: 'message',
                        role: 'user',
                        content: contentParts,
                    },
                });
                sendRealtimeEvent({ type: 'response.create' });
            }
            updateActivity('thinking');
            return true;
        } catch (error) {
            console.error('Failed to send realtime text turn', error);
            notify('error', error?.message || t('chat_realtime_send_failed', 'Failed to send realtime turn'));
            return false;
        }
    }

    function isActive() {
        return Boolean(state.active);
    }

    function isReady() {
        return Boolean(state.active && state.ready);
    }

    function getSessionState() {
        return {
            active: state.active,
            ready: state.ready,
            connecting: state.connecting,
            transport: state.transport,
            provider: state.provider,
            sessionId: state.sessionId,
            chatId: state.chatId,
            muted: state.isMuted,
            activity: state.activity,
            routeModeActive: state.routeModeActive,
            viewMode: state.callViewMode,
            captionsVisible: state.callCaptionsVisible,
        };
    }

    installAudioRecoveryListeners();
    updateCallButton();
    emitRealtimeState();
    document.addEventListener('i18n:updated', () => {
        syncCallSurfaceTranslations();
        updateCallStatus({ immediate: true });
    });
    window.addEventListener('beforeunload', () => {
        window.chatWakeLock?.release?.('realtime-call');
        stop({ skipServerStop: false, silent: true, reason: 'window_unload' }).catch(() => {});
    });

    window.realtimeCall = {
        start,
        stop,
        sendText,
        toggleMute,
        interrupt,
        activateCallRoute,
        deactivateCallRoute,
        isCallRouteActive: () => Boolean(state.routeModeActive),
        isActive,
        isReady,
        getSessionState,
        setViewMode: setCallViewMode,
    };
})();
