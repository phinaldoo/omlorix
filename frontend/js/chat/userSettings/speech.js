(() => {
    const SPEED_KEY = 'speech_playback_speed';
    const MIN_SPEED = 0.5;
    const MAX_SPEED = 2.0;
    const DEFAULT_SPEED = 1.0;

    const speedControl = document.getElementById('userSpeechSpeedControl');
    const speedRange = document.getElementById('userSpeechPlaybackSpeedRange');
    const speedValueLabel = document.getElementById('userSpeechPlaybackSpeedValue');

    let saveTimer = null;
    let lastPersistedValue = null;

    const t = (key, fallback) => {
        if (typeof window !== 'undefined' && typeof window.getTranslation === 'function') {
            return window.getTranslation(key, fallback);
        }
        return fallback;
    };

    const clampSpeed = (value) => {
        const numeric = Number(value);
        if (!Number.isFinite(numeric)) return DEFAULT_SPEED;
        return Math.min(MAX_SPEED, Math.max(MIN_SPEED, numeric));
    };

    const formatSpeed = (value) => `${clampSpeed(value).toFixed(1)}x`;

    const setSavingState = (isSaving) => {
        if (!speedControl) return;
        speedControl.classList.toggle('is-saving', Boolean(isSaving));
    };

    const updateSpeedDisplay = (value) => {
        const speed = clampSpeed(value);
        if (speedRange) {
            speedRange.value = String(speed);
        }
        if (speedValueLabel) {
            speedValueLabel.textContent = formatSpeed(speed);
        }
    };

    const persistSpeechSpeed = async (value) => {
        const speed = clampSpeed(value);
        if (lastPersistedValue !== null && Math.abs(lastPersistedValue - speed) < 0.001) {
            return;
        }

        setSavingState(true);
        try {
            const response = await window.authedFetch('/api/v1/users/settings/toogle', {
                method: 'PATCH',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({ speech_playback_speed: speed }),
            });

            if (!response.ok) {
                const errorText = await response.text().catch(() => '');
                throw new Error(errorText || t('user_settings_speech_speed_save_failed_status', 'Failed to save speech speed ({status})').replace('{status}', String(response.status)));
            }

            lastPersistedValue = speed;
        } catch (error) {
            console.error('Failed to persist speech playback speed', error);
            if (typeof notifyError === 'function') {
                notifyError(t('user_settings_speech_speed_save_error', 'Failed to save speech speed preference.'));
            }
        } finally {
            setSavingState(false);
        }
    };

    const schedulePersistSpeechSpeed = (value) => {
        if (saveTimer) {
            clearTimeout(saveTimer);
        }
        saveTimer = setTimeout(() => {
            persistSpeechSpeed(value);
        }, 220);
    };

    const applySpeechSpeed = (value, { persist = true, restartIfPlaying = true, source = 'user-settings' } = {}) => {
        const speed = clampSpeed(value);

        try {
            localStorage.setItem(SPEED_KEY, String(speed));
        } catch (_) {
            // Ignore localStorage write failures
        }

        if (window.AssistantSpeech && typeof window.AssistantSpeech.setPreferredSpeed === 'function') {
            window.AssistantSpeech.setPreferredSpeed(speed, { restartIfPlaying });
        }

        updateSpeedDisplay(speed);

        if (persist) {
            schedulePersistSpeechSpeed(speed);
        }

        if (typeof window.dispatchEvent === 'function') {
            window.dispatchEvent(new CustomEvent('assistantSpeechSpeedChanged', {
                detail: {
                    speed,
                    source,
                    persisted: persist,
                },
            }));
        }

        return speed;
    };

    const readInitialSpeed = (data = {}) => {
        const dataSpeed = data && typeof data.speech_playback_speed !== 'undefined'
            ? data.speech_playback_speed
            : null;

        if (dataSpeed !== null) {
            return clampSpeed(dataSpeed);
        }

        try {
            const stored = localStorage.getItem(SPEED_KEY);
            if (stored !== null) {
                return clampSpeed(stored);
            }
        } catch (_) {
            // Ignore localStorage read failures
        }

        if (window.AssistantSpeech && typeof window.AssistantSpeech.getPreferredSpeed === 'function') {
            return clampSpeed(window.AssistantSpeech.getPreferredSpeed());
        }

        return DEFAULT_SPEED;
    };

    const bindSlider = () => {
        if (!speedRange || speedRange.dataset.listenerAttached === 'true') {
            return;
        }

        speedRange.dataset.listenerAttached = 'true';
        speedRange.addEventListener('input', (event) => {
            const nextSpeed = clampSpeed(event.target.value);
            applySpeechSpeed(nextSpeed, {
                persist: true,
                restartIfPlaying: true,
                source: 'user-settings-slider',
            });
        });
    };

    const initUserSettingsSpeech = (data = {}) => {
        if (!speedControl || !speedRange || !speedValueLabel) {
            return;
        }

        bindSlider();

        const initialSpeed = readInitialSpeed(data);
        updateSpeedDisplay(initialSpeed);
        applySpeechSpeed(initialSpeed, {
            persist: false,
            restartIfPlaying: false,
            source: 'user-settings-init',
        });

        lastPersistedValue = initialSpeed;
    };

    window.initUserSettingsSpeech = initUserSettingsSpeech;
    window.updateSpeechPlaybackSpeedPreference = (value, options = {}) => {
        return applySpeechSpeed(value, options);
    };
})();
