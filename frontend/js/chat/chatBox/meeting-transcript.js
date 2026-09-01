function getMeetingMediaKind(fileLike) {
  const mime = String(fileLike?.type || '').toLowerCase();
  if (mime.startsWith('video/')) return 'video';
  if (mime.startsWith('audio/')) return 'audio';
  const name = String(fileLike?.name || '').toLowerCase();
  const extension = name.includes('.') ? name.split('.').pop() : '';
  if (['avi', 'm4v', 'mkv', 'mov', 'mp4', 'mpeg', 'mpg', 'ogv', 'webm'].includes(extension)) {
    return 'video';
  }
  if (['aac', 'aiff', 'flac', 'm4a', 'mid', 'midi', 'mp3', 'ogg', 'opus', 'wav', 'webm'].includes(extension)) {
    return 'audio';
  }
  return '';
}

function formatMeetingSelectionDetails(file) {
  if (!file) return '';
  const parts = [];
  const captureSource = String(file?.meetingCaptureSource || '');
  if (captureSource === 'microphone') {
    parts.push(getChatI18nString('chat_meeting_selection_recorded', 'Recorded'));
  } else if (captureSource === 'screen') {
    parts.push(getChatI18nString('chat_meeting_selection_screen_capture', 'Screen capture'));
  }
  const mediaKind = getMeetingMediaKind(file);
  if (mediaKind) {
    parts.push(mediaKind === 'video'
      ? getChatI18nString('chat_meeting_selection_video', 'Video')
      : getChatI18nString('chat_meeting_selection_audio', 'Audio'));
  }
  const filename = String(file?.name || '');
  const extension = filename.includes('.')
    ? filename.split('.').pop().toUpperCase()
    : getChatI18nString('chat_meeting_selection_file', 'FILE');
  if (extension && extension !== 'FILE') {
    parts.push(extension);
  }
  if (Number.isFinite(file?.size)) {
    parts.push(formatFileSizeLabel(file.size));
  }
  if (Number.isFinite(file?.meetingDurationSeconds) && file.meetingDurationSeconds > 0) {
    parts.push(formatMeetingDurationLabel(file.meetingDurationSeconds));
  }
  return parts.join(' · ');
}

function formatMeetingDurationLabel(totalSeconds) {
  const safeSeconds = Math.max(0, Math.floor(Number(totalSeconds) || 0));
  const hours = Math.floor(safeSeconds / 3600);
  const minutes = Math.floor((safeSeconds % 3600) / 60);
  const seconds = safeSeconds % 60;
  if (hours > 0) {
    return `${hours}:${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`;
  }
  return `${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`;
}

function getMeetingCaptureSourceConfig(source = meetingTranscriptState.source) {
  if (source === 'screen') {
    return {
      modeLabel: getChatI18nString('chat_meeting_screen_capture_mode', 'Screen audio capture'),
      readyStatus: getChatI18nString('chat_meeting_screen_capture_ready_status', 'Ready to capture'),
      readyDetails: getChatI18nString('chat_meeting_screen_capture_ready_details', 'Share a tab or screen with audio enabled to record meeting playback or system audio.'),
      recordingStatus: getChatI18nString('chat_meeting_screen_capture_recording_status', 'Capturing screen audio…'),
      recordingDetails: getChatI18nString('chat_meeting_screen_capture_recording_details', 'Keep the shared tab or screen open while the meeting audio plays.'),
      startLabel: getChatI18nString('chat_meeting_screen_capture_start', 'Start capture'),
      stopLabel: getChatI18nString('chat_meeting_screen_capture_stop', 'Stop capture'),
      redoLabel: getChatI18nString('chat_meeting_screen_capture_redo', 'Capture again'),
      idleHint: getChatI18nString('chat_meeting_screen_capture_hint', 'Screen audio capture works best when you share a browser tab and enable audio in the browser picker.'),
      unsupportedMessage: getChatI18nString('chat_meeting_screen_capture_unsupported', 'Screen audio capture is not supported in this browser.'),
      emptyMessage: getChatI18nString('chat_meeting_screen_capture_empty', 'No screen audio was captured. Share a tab or screen with audio enabled and try again.'),
    };
  }

  return {
    modeLabel: getChatI18nString('chat_meeting_microphone_mode', 'Microphone recording'),
    readyStatus: getChatI18nString('chat_meeting_microphone_ready_status', 'Ready to record'),
    readyDetails: getChatI18nString('chat_meeting_microphone_ready_details', 'Use your microphone to capture a live meeting, then stop when you are ready to transcribe it.'),
    recordingStatus: getChatI18nString('chat_meeting_microphone_recording_status', 'Recording microphone audio…'),
    recordingDetails: getChatI18nString('chat_meeting_microphone_recording_details', 'Leave this modal open while the meeting audio is being recorded.'),
    startLabel: getChatI18nString('chat_meeting_microphone_start', 'Start recording'),
    stopLabel: getChatI18nString('chat_meeting_microphone_stop', 'Stop recording'),
    redoLabel: getChatI18nString('chat_meeting_microphone_redo', 'Record again'),
    idleHint: getChatI18nString('chat_meeting_microphone_hint', 'When you stop, the recording is attached here and can be transcribed immediately.'),
    unsupportedMessage: getChatI18nString('chat_meeting_microphone_unsupported', 'Microphone recording is not supported in this browser.'),
    emptyMessage: getChatI18nString('chat_meeting_microphone_empty', 'No audio was captured. Check your microphone and try again.'),
  };
}

function isMeetingMicrophoneSupported() {
  return Boolean(
    typeof window !== 'undefined' &&
    window.isSecureContext !== false &&
    navigator.mediaDevices &&
    typeof navigator.mediaDevices.getUserMedia === 'function' &&
    typeof window.MediaRecorder === 'function'
  );
}

function isMeetingScreenCaptureSupported() {
  return Boolean(
    typeof window !== 'undefined' &&
    window.isSecureContext !== false &&
    navigator.mediaDevices &&
    typeof navigator.mediaDevices.getDisplayMedia === 'function' &&
    typeof window.MediaRecorder === 'function' &&
    typeof window.File === 'function'
  );
}

function isMeetingSourceSupported(source) {
  if (source === 'microphone') return isMeetingMicrophoneSupported();
  if (source === 'screen') return isMeetingScreenCaptureSupported();
  return true;
}

function buildMeetingCaptureTimestamp() {
  const now = new Date();
  const part = (value) => String(value).padStart(2, '0');
  return `${now.getFullYear()}${part(now.getMonth() + 1)}${part(now.getDate())}-${part(now.getHours())}${part(now.getMinutes())}${part(now.getSeconds())}`;
}

function getMeetingCaptureExtension(mimeType, source) {
  const normalized = String(mimeType || '').toLowerCase();
  if (normalized.includes('mp4')) return source === 'microphone' ? 'm4a' : 'mp4';
  if (normalized.includes('mpeg')) return 'mp3';
  if (normalized.includes('wav')) return 'wav';
  if (normalized.includes('ogg')) return 'ogg';
  if (normalized.includes('opus')) return 'opus';
  return 'webm';
}

function buildMeetingCaptureFilename(source, mimeType) {
  const prefix = source === 'screen' ? 'meeting-screen-capture' : 'meeting-audio-recording';
  const extension = getMeetingCaptureExtension(mimeType, source);
  return `${prefix}-${buildMeetingCaptureTimestamp()}.${extension}`;
}

function getPreferredMeetingCaptureMimeType(source) {
  if (typeof window.MediaRecorder?.isTypeSupported !== 'function') {
    return '';
  }

  const candidates = source === 'screen'
    ? ['video/webm;codecs=vp9,opus', 'video/webm;codecs=vp8,opus', 'video/webm', 'video/mp4']
    : ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4', 'audio/mpeg'];

  return candidates.find((candidate) => window.MediaRecorder.isTypeSupported(candidate)) || '';
}

function stopMeetingCaptureStream() {
  const { stream } = meetingTranscriptState;
  if (!stream) return;
  stream.getTracks().forEach((track) => {
    try {
      track.stop();
    } catch (_) {}
  });
  meetingTranscriptState.stream = null;
}

function clearMeetingCaptureTimer() {
  if (meetingTranscriptState.captureTimerId) {
    window.clearInterval(meetingTranscriptState.captureTimerId);
    meetingTranscriptState.captureTimerId = null;
  }
}

function updateMeetingCaptureTimer() {
  const timerEl = chatBoxMeetingCaptureTimer;
  if (!timerEl) return;
  if (meetingTranscriptState.isRecording && meetingTranscriptState.captureStartedAt) {
    const elapsedSeconds = Math.max(0, Math.floor((Date.now() - meetingTranscriptState.captureStartedAt) / 1000));
    timerEl.textContent = formatMeetingDurationLabel(elapsedSeconds);
    return;
  }
  if (Number.isFinite(meetingTranscriptState.selectedFile?.meetingDurationSeconds) && meetingTranscriptState.selectedFile.meetingDurationSeconds > 0) {
    timerEl.textContent = formatMeetingDurationLabel(meetingTranscriptState.selectedFile.meetingDurationSeconds);
    return;
  }
  timerEl.textContent = '00:00';
}

function isCaptureSelectionForSource(source = meetingTranscriptState.source) {
  return Boolean(
    meetingTranscriptState.selectedFile &&
    String(meetingTranscriptState.selectedFile.meetingCaptureSource || '') === String(source || '')
  );
}

/**
 * A discard action is useful only while there is media that the user could
 * lose: an active recording or a completed capture awaiting transcription.
 * Keeping this decision in one helper ensures microphone and screen-audio
 * capture present the same controls in the same states.
 */
function shouldShowMeetingCaptureDiscardButton(isRecording = false, hasCaptureSelection = false) {
  return Boolean(isRecording || hasCaptureSelection);
}

function getMeetingSelectedFileSource(file = meetingTranscriptState.selectedFile) {
  if (!file) return '';
  const captureSource = String(file.meetingCaptureSource || '').trim();
  return captureSource || 'upload';
}

/**
 * Return the panel controlled by a meeting source tab for the current stage.
 * Before a file exists, each tab controls its source picker. Afterwards, the
 * active tab controls the shared result panel containing the selected file and
 * its governance fields.
 */
function getMeetingSourcePanelId(source, hasSelection = false, activeSource = source) {
  if (hasSelection && source === activeSource) return 'chatBoxMeetingResultPanel';
  return source === 'upload' ? 'chatBoxMeetingUploadPanel' : 'chatBoxMeetingCapturePanel';
}

function getMeetingSourceOptionEntries() {
  return [
    [chatBoxMeetingUploadOption, 'upload'],
    [chatBoxMeetingRecordOption, 'microphone'],
    [chatBoxMeetingScreenOption, 'screen'],
  ];
}

/**
 * Keep the selected source tab associated with content that is actually
 * visible. The result panel is shared because upload and capture converge on
 * the same selected-file and governance workflow.
 */
function updateMeetingResultPanelUi() {
  const hasSelection = Boolean(meetingTranscriptState.selectedFile);
  const sourceOptions = getMeetingSourceOptionEntries();

  sourceOptions.forEach(([button, source]) => {
    button?.setAttribute(
      'aria-controls',
      getMeetingSourcePanelId(source, hasSelection, meetingTranscriptState.source),
    );
  });

  if (!chatBoxMeetingResultPanel) return;
  const activeTab = sourceOptions.find(([, source]) => source === meetingTranscriptState.source)?.[0];
  chatBoxMeetingResultPanel.hidden = !hasSelection;
  chatBoxMeetingResultPanel.classList.toggle('is-active', hasSelection);
  if (activeTab?.id) {
    chatBoxMeetingResultPanel.setAttribute('aria-labelledby', activeTab.id);
  }
}

/**
 * Transfer focus out of a source panel before that panel is hidden. This is
 * especially important when stopping a recording, because the Stop button is
 * replaced synchronously by the selected-file result.
 */
function moveMeetingFocusToCompletedSelection(file = meetingTranscriptState.selectedFile) {
  if (!file || !chatBoxMeetingClearSelectionButton || !chatBoxMeetingCapturePanel) return;
  if (chatBoxMeetingCapturePanel.contains(document.activeElement)) {
    chatBoxMeetingClearSelectionButton.focus();
  }
}

function updateMeetingCaptureUi() {
  const config = getMeetingCaptureSourceConfig();
  const supported = isMeetingSourceSupported(meetingTranscriptState.source);
  const hasCaptureSelection = isCaptureSelectionForSource();

  if (chatBoxMeetingCaptureModeLabel) {
    chatBoxMeetingCaptureModeLabel.textContent = config.modeLabel;
  }
  if (chatBoxMeetingCaptureHint) {
    chatBoxMeetingCaptureHint.textContent = config.idleHint;
  }
  if (chatBoxMeetingCapturePanel) {
    // Once a take exists, collapse the recorder and let the selected-file row
    // become the single source of truth. Removing that row reveals the active
    // capture source again, matching the progressive flow of the redesign.
    const capturePanelActive = meetingTranscriptState.source !== 'upload' && !meetingTranscriptState.selectedFile;
    chatBoxMeetingCapturePanel.hidden = !capturePanelActive;
    chatBoxMeetingCapturePanel.classList.toggle('is-active', capturePanelActive);
  }
  if (chatBoxMeetingRecorder) {
    chatBoxMeetingRecorder.classList.toggle('is-recording', meetingTranscriptState.isRecording);
    chatBoxMeetingRecorder.classList.toggle('is-idle', !meetingTranscriptState.isRecording);
  }
  if (chatBoxMeetingCaptureStatus) {
    if (!supported) {
      chatBoxMeetingCaptureStatus.textContent = getChatI18nString('chat_meeting_capture_unavailable', 'Unavailable');
    } else if (meetingTranscriptState.isRecording) {
      chatBoxMeetingCaptureStatus.textContent = config.recordingStatus;
    } else if (hasCaptureSelection) {
      chatBoxMeetingCaptureStatus.textContent = getChatI18nString('chat_meeting_capture_recording_ready', 'Recording ready');
    } else {
      chatBoxMeetingCaptureStatus.textContent = config.readyStatus;
    }
  }
  if (chatBoxMeetingCaptureTimer) {
    // Per-second announcements overwhelm screen-reader users. Recording state
    // changes are already announced by the adjacent polite status region.
    chatBoxMeetingCaptureTimer.setAttribute('aria-live', 'off');
  }
  if (chatBoxMeetingCaptureDetails) {
    if (!supported) {
      chatBoxMeetingCaptureDetails.textContent = config.unsupportedMessage;
    } else if (meetingTranscriptState.isRecording) {
      chatBoxMeetingCaptureDetails.textContent = config.recordingDetails;
    } else if (hasCaptureSelection) {
      chatBoxMeetingCaptureDetails.textContent = formatChatI18nString(
        'chat_meeting_capture_file_ready_details',
        '{filename} is attached and ready for transcription.',
        { filename: meetingTranscriptState.selectedFile.name },
      );
    } else {
      chatBoxMeetingCaptureDetails.textContent = config.readyDetails;
    }
  }
  if (chatBoxMeetingCaptureToggleButton) {
    const toggleLabel = meetingTranscriptState.isRecording
      ? config.stopLabel
      : (hasCaptureSelection ? config.redoLabel : config.startLabel);
    const visibleLabel = chatBoxMeetingCaptureToggleButton.querySelector('.chat-meeting-record-button__label');
    if (visibleLabel) {
      visibleLabel.textContent = toggleLabel;
    }
    chatBoxMeetingCaptureToggleButton.setAttribute('aria-label', toggleLabel);
    chatBoxMeetingCaptureToggleButton.title = toggleLabel;
    chatBoxMeetingCaptureToggleButton.disabled = meetingTranscriptState.isSubmitting || !supported;
  }
  if (chatBoxMeetingCaptureDiscardButton) {
    // Hide a no-op action instead of showing it disabled before the user has
    // recorded anything. Once recording starts, the button discards the active
    // stream; after stopping, it removes the captured file instead.
    const canDiscard = shouldShowMeetingCaptureDiscardButton(meetingTranscriptState.isRecording, hasCaptureSelection);
    chatBoxMeetingCaptureDiscardButton.hidden = !canDiscard;
    chatBoxMeetingCaptureDiscardButton.disabled = meetingTranscriptState.isSubmitting || !canDiscard;
  }
  updateMeetingCaptureTimer();
}

function buildBrowserLocalIsoString(date = new Date()) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const day = String(date.getDate()).padStart(2, '0');
  const hours = String(date.getHours()).padStart(2, '0');
  const minutes = String(date.getMinutes()).padStart(2, '0');
  const seconds = String(date.getSeconds()).padStart(2, '0');
  const timezoneOffsetMinutes = -date.getTimezoneOffset();
  const sign = timezoneOffsetMinutes >= 0 ? '+' : '-';
  const offsetHours = String(Math.floor(Math.abs(timezoneOffsetMinutes) / 60)).padStart(2, '0');
  const offsetMinutes = String(Math.abs(timezoneOffsetMinutes) % 60).padStart(2, '0');
  return `${year}-${month}-${day}T${hours}:${minutes}:${seconds}${sign}${offsetHours}:${offsetMinutes}`;
}

function getMeetingBrowserDateContext() {
  const now = new Date();
  let label = '';
  try {
    label = new Intl.DateTimeFormat(undefined, { dateStyle: 'long' }).format(now);
  } catch (_) {
    label = now.toLocaleDateString();
  }
  return {
    iso: buildBrowserLocalIsoString(now),
    label,
  };
}

function updateMeetingSelectionUi() {
  const file = meetingTranscriptState.selectedFile;
  // Reveal the destination before moving focus, then hide the old source panel
  // through updateMeetingSourceOptions() after focus is safely inside it.
  updateMeetingResultPanelUi();
  if (chatBoxMeetingSelection) {
    chatBoxMeetingSelection.hidden = !file;
  }
  if (chatBoxMeetingGovernance) {
    chatBoxMeetingGovernance.hidden = !file;
  }
  if (chatBoxMeetingSelectionName) {
    chatBoxMeetingSelectionName.textContent = file?.name || '';
  }
  if (chatBoxMeetingSelectionDetails) {
    chatBoxMeetingSelectionDetails.textContent = file ? formatMeetingSelectionDetails(file) : '';
  }
  if (chatBoxMeetingSubmitButton) {
    chatBoxMeetingSubmitButton.disabled = !file || meetingTranscriptState.isSubmitting || meetingTranscriptState.isRecording || !isMeetingGovernanceComplete();
  }
  moveMeetingFocusToCompletedSelection(file);
  updateMeetingSourceOptions();
}

function setMeetingProgressState({ visible, label = '', detail = '', percent = null } = {}) {
  if (chatBoxMeetingProgress) {
    chatBoxMeetingProgress.hidden = !visible;
  }
  if (chatBoxMeetingProgressLabel && label) {
    chatBoxMeetingProgressLabel.textContent = label;
  }
  if (chatBoxMeetingProgressDetail) {
    chatBoxMeetingProgressDetail.textContent = detail;
  }
  if (chatBoxMeetingProgressFill) {
    const nextPercent = percent == null ? 0 : clampProgress(percent);
    chatBoxMeetingProgressFill.style.width = `${nextPercent}%`;
  }
}

function setMeetingSubmittingState(nextState) {
  meetingTranscriptState.isSubmitting = Boolean(nextState);
  window.chatWakeLock?.syncReason?.('meeting-transcription', meetingTranscriptState.isSubmitting);
  chatBoxMeetingModal?.setAttribute('aria-busy', meetingTranscriptState.isSubmitting ? 'true' : 'false');
  if (chatBoxMeetingDropzone) {
    chatBoxMeetingDropzone.disabled = meetingTranscriptState.isSubmitting;
  }
  [chatBoxMeetingUploadOption, chatBoxMeetingRecordOption, chatBoxMeetingScreenOption].forEach((button) => {
    if (!button) return;
    const source = button.dataset?.meetingSource || 'upload';
    button.disabled = meetingTranscriptState.isSubmitting || meetingTranscriptState.isRecording || (source !== 'upload' && !isMeetingSourceSupported(source));
  });
  if (chatBoxMeetingClearSelectionButton) {
    chatBoxMeetingClearSelectionButton.disabled = meetingTranscriptState.isSubmitting || meetingTranscriptState.isRecording;
  }
  if (chatBoxMeetingCancelButton) {
    chatBoxMeetingCancelButton.disabled = meetingTranscriptState.isSubmitting;
  }
  if (chatBoxMeetingCloseButton) {
    chatBoxMeetingCloseButton.disabled = meetingTranscriptState.isSubmitting;
  }
  updateMeetingSelectionUi();
}

function updateMeetingSourceOptions() {
  const sourceOptions = getMeetingSourceOptionEntries();
  sourceOptions.forEach(([button, source]) => {
    if (!button) return;
    const active = meetingTranscriptState.source === source;
    const supported = isMeetingSourceSupported(source);
    button.classList.toggle('is-active', active);
    button.classList.toggle('is-disabled', !supported);
    button.setAttribute('aria-selected', active ? 'true' : 'false');
    button.tabIndex = active ? 0 : -1;
    button.disabled = meetingTranscriptState.isSubmitting || meetingTranscriptState.isRecording || !supported;
    if (!supported) {
      button.title = getMeetingCaptureSourceConfig(source).unsupportedMessage;
    } else {
      button.removeAttribute('title');
    }
  });

  // The indicator is purely visual; the tabs remain real ARIA tabs with a
  // roving tab stop, so keyboard and assistive-technology behavior is stable.
  const activeIndex = Math.max(0, sourceOptions.findIndex(([, source]) => source === meetingTranscriptState.source));
  chatBoxMeetingSourceTabs?.style.setProperty('--meeting-source-index', String(activeIndex));
  if (chatBoxMeetingCapturePanel) {
    chatBoxMeetingCapturePanel.setAttribute(
      'aria-labelledby',
      meetingTranscriptState.source === 'screen' ? 'chatBoxMeetingScreenOption' : 'chatBoxMeetingRecordOption',
    );
  }

  if (chatBoxMeetingNote) {
    chatBoxMeetingNote.textContent = meetingTranscriptState.source === 'upload'
      ? getChatI18nString('chat_meeting_note', 'The transcript file will be named from your browser date and added to this conversation as a user file.')
      : getChatI18nString('chat_meeting_capture_note', 'When you are done recording, the captured media is attached here and sent through the same meeting transcript flow.');
  }
  if (chatBoxMeetingUploadPanel) {
    const active = meetingTranscriptState.source === 'upload' && !meetingTranscriptState.selectedFile;
    chatBoxMeetingUploadPanel.hidden = !active;
    chatBoxMeetingUploadPanel.classList.toggle('is-active', active);
  }
  updateMeetingResultPanelUi();
  updateMeetingCaptureUi();
}

function resetMeetingCaptureRuntime() {
  meetingTranscriptState.captureSessionId += 1;
  clearMeetingCaptureTimer();
  stopMeetingCaptureStream();
  meetingTranscriptState.isRecording = false;
  meetingTranscriptState.mediaRecorder = null;
  meetingTranscriptState.captureChunks = [];
  meetingTranscriptState.captureMimeType = '';
  meetingTranscriptState.captureSource = '';
  meetingTranscriptState.captureStartedAt = 0;
  meetingTranscriptState.discardCaptureOnStop = false;
}

function finalizeMeetingCapture({ source, mimeType, chunks, discard = false, durationSeconds = 0 } = {}) {
  const config = getMeetingCaptureSourceConfig(source);
  resetMeetingCaptureRuntime();

  if (discard) {
    updateMeetingSelectionUi();
    return;
  }

  const blob = new Blob(Array.isArray(chunks) ? chunks : [], { type: mimeType || (source === 'screen' ? 'video/webm' : 'audio/webm') });
  if (!blob.size) {
    notifyWarning?.(config.emptyMessage);
    updateMeetingSelectionUi();
    return;
  }

  const file = new File([blob], buildMeetingCaptureFilename(source, blob.type), {
    type: blob.type || mimeType || (source === 'screen' ? 'video/webm' : 'audio/webm'),
    lastModified: Date.now(),
  });
  file.meetingCaptureSource = source;
  file.meetingDurationSeconds = Math.max(0, Math.floor(durationSeconds || 0));
  setMeetingSelectedFile(file);
  notifySuccess?.(source === 'screen'
    ? getChatI18nString('chat_meeting_screen_capture_ready_to_transcribe', 'Screen audio capture is ready to transcribe.')
    : getChatI18nString('chat_meeting_microphone_ready_to_transcribe', 'Recording is ready to transcribe.'));
}

function stopMeetingCapture({ discard = false } = {}) {
  if (!meetingTranscriptState.isRecording) {
    if (discard) {
      resetMeetingCaptureRuntime();
      updateMeetingSelectionUi();
    }
    return;
  }

  meetingTranscriptState.discardCaptureOnStop = Boolean(discard);
  const recorder = meetingTranscriptState.mediaRecorder;
  if (!recorder) {
    const source = meetingTranscriptState.captureSource || meetingTranscriptState.source;
    finalizeMeetingCapture({
      source,
      mimeType: meetingTranscriptState.captureMimeType,
      chunks: meetingTranscriptState.captureChunks,
      discard: meetingTranscriptState.discardCaptureOnStop,
      durationSeconds: meetingTranscriptState.captureStartedAt ? (Date.now() - meetingTranscriptState.captureStartedAt) / 1000 : 0,
    });
    return;
  }

  if (recorder.state !== 'inactive') {
    recorder.stop();
  } else {
    const source = meetingTranscriptState.captureSource || meetingTranscriptState.source;
    finalizeMeetingCapture({
      source,
      mimeType: meetingTranscriptState.captureMimeType,
      chunks: meetingTranscriptState.captureChunks,
      discard: meetingTranscriptState.discardCaptureOnStop,
      durationSeconds: meetingTranscriptState.captureStartedAt ? (Date.now() - meetingTranscriptState.captureStartedAt) / 1000 : 0,
    });
  }
}

async function startMeetingCapture() {
  if (meetingTranscriptState.isSubmitting || meetingTranscriptState.isRecording) {
    return;
  }

  const source = meetingTranscriptState.source;
  if (source === 'upload') {
    chatBoxMeetingDropzone?.click();
    return;
  }
  if (!isMeetingSourceSupported(source)) {
    notifyError?.(getMeetingCaptureSourceConfig(source).unsupportedMessage);
    return;
  }

  let stream = null;

  try {
    stream = source === 'screen'
      ? await navigator.mediaDevices.getDisplayMedia({
          video: {
            cursor: 'always',
          },
          audio: true,
        })
      : await navigator.mediaDevices.getUserMedia({ audio: true });

    const hasAudioTrack = stream.getAudioTracks().length > 0;
    if (!hasAudioTrack) {
      throw new Error(getMeetingCaptureSourceConfig(source).emptyMessage);
    }

    const mimeType = getPreferredMeetingCaptureMimeType(source);
    const recorderOptions = mimeType ? { mimeType } : {};
    const mediaRecorder = new MediaRecorder(stream, recorderOptions);
    const sessionId = meetingTranscriptState.captureSessionId + 1;

    meetingTranscriptState.captureSessionId = sessionId;
    meetingTranscriptState.isRecording = true;
    meetingTranscriptState.stream = stream;
    meetingTranscriptState.mediaRecorder = mediaRecorder;
    meetingTranscriptState.captureChunks = [];
    meetingTranscriptState.captureMimeType = mediaRecorder.mimeType || mimeType || '';
    meetingTranscriptState.captureSource = source;
    meetingTranscriptState.captureStartedAt = Date.now();
    meetingTranscriptState.discardCaptureOnStop = false;

    clearMeetingCaptureTimer();
    meetingTranscriptState.captureTimerId = window.setInterval(() => {
      if (meetingTranscriptState.captureSessionId !== sessionId) return;
      updateMeetingCaptureTimer();
    }, 1000);
    updateMeetingSelectionUi();

    mediaRecorder.ondataavailable = (event) => {
      if (meetingTranscriptState.captureSessionId !== sessionId) return;
      if (event.data && event.data.size > 0) {
        meetingTranscriptState.captureChunks.push(event.data);
      }
    };

    mediaRecorder.onerror = (event) => {
      console.error('Meeting recorder error:', event.error);
      notifyError?.(getChatI18nString('chat_dictation_recording_failed', 'Recording failed. Please try again.'));
      stopMeetingCapture({ discard: true });
    };

    mediaRecorder.onstop = () => {
      if (meetingTranscriptState.captureSessionId !== sessionId) return;
      finalizeMeetingCapture({
        source,
        mimeType: meetingTranscriptState.captureMimeType || mediaRecorder.mimeType || mimeType,
        chunks: meetingTranscriptState.captureChunks,
        discard: meetingTranscriptState.discardCaptureOnStop,
        durationSeconds: meetingTranscriptState.captureStartedAt ? (Date.now() - meetingTranscriptState.captureStartedAt) / 1000 : 0,
      });
    };

    stream.getTracks().forEach((track) => {
      track.addEventListener('ended', () => {
        if (meetingTranscriptState.captureSessionId !== sessionId || !meetingTranscriptState.isRecording) return;
        stopMeetingCapture();
      }, { once: true });
    });

    mediaRecorder.start(1000);
  } catch (error) {
    if (stream) {
      stream.getTracks().forEach((track) => {
        try {
          track.stop();
        } catch (_) {}
      });
    }

    const name = String(error?.name || '');
    const message = String(error?.message || '').trim();
    const cancelled = name === 'AbortError' || name === 'NotAllowedError' || /cancel|permission denied/i.test(message);
    if (cancelled) {
      notifyInfo?.(source === 'screen'
        ? getChatI18nString('chat_meeting_screen_capture_cancelled', 'Screen audio capture cancelled.')
        : getChatI18nString('chat_meeting_microphone_cancelled', 'Recording cancelled.'));
      return;
    }
	    const recordingError = getChatI18nString('chat_dictation_could_not_start_recording', 'Could not start recording. Please try again.');
	    notifyError?.(message ? `${recordingError} (${message})` : recordingError);
    resetMeetingCaptureRuntime();
    updateMeetingSelectionUi();
  }
}

function clearMeetingCaptureSelection() {
  if (!meetingTranscriptState.selectedFile) return;
  if (String(meetingTranscriptState.selectedFile.meetingCaptureSource || '') !== String(meetingTranscriptState.source || '')) {
    return;
  }
  setMeetingSelectedFile(null);
}

/**
 * Confirms and discards the media for the currently selected capture source.
 * Active recordings are stopped without preserving their chunks; completed
 * recordings are removed from the pending transcription selection.
 */
async function discardMeetingCapture() {
  const isRecording = meetingTranscriptState.isRecording;
  const hasCaptureSelection = isCaptureSelectionForSource();
  if (meetingTranscriptState.isSubmitting || !shouldShowMeetingCaptureDiscardButton(isRecording, hasCaptureSelection)) {
    return;
  }

  // Destructive recording actions use the shared accessible confirmation modal
  // rather than a native browser confirmation dialog.
  if (typeof window.showWarningConfirm !== 'function') {
    notifyError?.(getChatI18nString('chat_meeting_discard_confirmation_unavailable', 'Unable to confirm discarding this recording. Please reload and try again.'));
    return;
  }

  const confirmed = await window.showWarningConfirm({
    title: getChatI18nString('chat_meeting_discard_confirmation_title', 'Discard recording?'),
    message: getChatI18nString(
      isRecording ? 'chat_meeting_discard_active_confirmation_message' : 'chat_meeting_discard_saved_confirmation_message',
      isRecording
        ? 'This stops the recording and permanently removes the audio captured so far.'
        : 'This permanently removes the captured recording. It cannot be transcribed afterward.',
    ),
    confirmLabel: getChatI18nString('chat_meeting_discard_confirmation_confirm', 'Discard recording'),
    cancelLabel: getChatI18nString('common_cancel', 'Cancel'),
    danger: true,
  });

  if (!confirmed || meetingTranscriptState.isSubmitting) {
    return;
  }

  // The recording may have stopped while the confirmation dialog was open.
  // Re-read state so this never discards a newly changed source or selection.
  if (meetingTranscriptState.isRecording) {
    stopMeetingCapture({ discard: true });
  } else if (isCaptureSelectionForSource()) {
    clearMeetingCaptureSelection();
  }
}

function setMeetingSource(nextSource, { force = false } = {}) {
  const normalizedSource = ['upload', 'microphone', 'screen'].includes(nextSource) ? nextSource : 'upload';
  if (!force && meetingTranscriptState.isRecording && normalizedSource !== meetingTranscriptState.source) {
    notifyWarning?.(getChatI18nString('chat_meeting_stop_recording_before_switching', 'Stop the current recording before switching meeting sources.'));
    return;
  }
  if (normalizedSource !== 'upload' && !isMeetingSourceSupported(normalizedSource)) {
    notifyWarning?.(getMeetingCaptureSourceConfig(normalizedSource).unsupportedMessage);
    return;
  }

  if (getMeetingSelectedFileSource() && getMeetingSelectedFileSource() !== normalizedSource) {
    setMeetingSelectedFile(null);
  }
  meetingTranscriptState.source = normalizedSource;
  updateMeetingSourceOptions();
}

function getMeetingLegalBasisValue() {
  // The legal-basis control is rendered with the shared custom-select widget,
  // so its selected value is read through the custom-select helper rather than
  // a native `.value` property.
  const value = window.getCustomSelectValue?.(chatBoxMeetingLegalBasis);
  return String(value || '').trim();
}

function getMeetingLegalBasisDetailsValue() {
  return String(chatBoxMeetingLegalBasisDetails?.value || '').trim();
}

function getMeetingRetentionDaysValue() {
  const rawValue = String(chatBoxMeetingRetentionDays?.value || '').trim();
  if (!rawValue) return null;
  const parsed = Number.parseInt(rawValue, 10);
  if (!Number.isFinite(parsed)) return null;
  return parsed;
}

function isMeetingGovernanceComplete() {
  const retentionDays = getMeetingRetentionDaysValue();
  return Boolean(
    chatBoxMeetingConsentCheckbox?.checked &&
    getMeetingLegalBasisValue() &&
    getMeetingLegalBasisDetailsValue() &&
    Number.isInteger(retentionDays) &&
    retentionDays >= 1 &&
    retentionDays <= 3650
  );
}

function resetMeetingModalState({ keepFocus = false } = {}) {
  stopMeetingCapture({ discard: true });
  resetMeetingCaptureRuntime();
  meetingTranscriptState.selectedFile = null;
  meetingTranscriptState.uploadProgress = 0;
  meetingTranscriptState.source = 'upload';
  if (chatBoxMeetingConsentCheckbox) {
    chatBoxMeetingConsentCheckbox.checked = false;
  }
  if (chatBoxMeetingLegalBasis) {
    // Reset the custom-select back to its placeholder (empty) value.
    window.setCustomSelectValue?.('meeting_legal_basis', '');
  }
  if (chatBoxMeetingLegalBasisDetails) {
    chatBoxMeetingLegalBasisDetails.value = '';
  }
  if (chatBoxMeetingRetentionDays) {
    chatBoxMeetingRetentionDays.value = '30';
  }
  setMeetingSubmittingState(false);
  setMeetingProgressState({ visible: false, detail: '', percent: 0 });
  if (chatBoxMeetingFileInput) {
    chatBoxMeetingFileInput.value = '';
  }
  if (!keepFocus) {
    meetingTranscriptState.lastFocusedElement = null;
  }
  updateMeetingSourceOptions();
}

function setMeetingSelectedFile(file) {
  meetingTranscriptState.selectedFile = file || null;
  updateMeetingSelectionUi();
}

function isMeetingModalOpen() {
  return Boolean(chatBoxMeetingOverlay?.classList.contains('open'));
}

/**
 * Keep keyboard focus inside the modal while it is open. Hidden and disabled
 * controls are excluded so the focus order always follows the visible state.
 */
function trapMeetingModalFocus(event) {
  if (event.key !== 'Tab' || !chatBoxMeetingModal) return;
  const focusable = Array.from(chatBoxMeetingModal.querySelectorAll(
    'button:not([disabled]), [href], input:not([disabled]), textarea:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])',
  )).filter((element) => !element.hidden && element.getAttribute('aria-hidden') !== 'true' && element.getClientRects().length > 0);
  if (!focusable.length) return;

  const first = focusable[0];
  const last = focusable[focusable.length - 1];
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
}

function setMeetingModalOpen(open) {
  if (!chatBoxMeetingOverlay || !chatBoxMeetingModal) return;
  const shouldOpen = Boolean(open);
  chatBoxMeetingOverlay.hidden = !shouldOpen;
  chatBoxMeetingOverlay.classList.toggle('open', shouldOpen);
  chatBoxMeetingOverlay.setAttribute('aria-hidden', shouldOpen ? 'false' : 'true');
  document.body.classList.toggle('modal-open', shouldOpen);
  if (!shouldOpen) {
    resetMeetingModalState({ keepFocus: true });
    meetingTranscriptState.lastFocusedElement?.focus?.();
    meetingTranscriptState.lastFocusedElement = null;
    return;
  }
  requestAnimationFrame(() => {
    (meetingTranscriptState.source === 'upload' ? chatBoxMeetingDropzone : chatBoxMeetingCaptureToggleButton)?.focus?.();
  });
}

function openMeetingModal() {
  if (!chatBoxMeetingOverlay || !chatBoxMeetingModal) return;
  meetingTranscriptState.lastFocusedElement = document.activeElement;
  resetMeetingModalState({ keepFocus: true });
  setMeetingModalOpen(true);
}

function closeMeetingModal({ force = false } = {}) {
  if (meetingTranscriptState.isSubmitting && !force) {
    return;
  }
  setMeetingModalOpen(false);
}

function handleMeetingFileSelection(files) {
  if (meetingTranscriptState.isRecording) {
    notifyWarning?.(getChatI18nString('chat_meeting_stop_recording_before_file_change', 'Stop the current recording before choosing a different meeting file.'));
    return;
  }
  const list = Array.isArray(files) ? files : Array.from(files || []);
  if (!list.length) {
    return;
  }
  const candidate = list.find((file) => getMeetingMediaKind(file));
  if (!candidate) {
    notifyWarning?.(getChatI18nString('chat_meeting_choose_audio_video_file', 'Choose an audio or video file for the meeting transcript.'));
    return;
  }
  setMeetingSource('upload', { force: true });
  setMeetingSelectedFile(candidate);
}

function upsertChatUploadedFileCache(file) {
  const fileId = String(file?.file_id ?? file?.id ?? '').trim();
  if (!fileId) return;
  const normalized = {
    ...file,
    id: file?.id || fileId,
    file_id: fileId,
  };
  const nextList = Array.isArray(cachedUploadedFiles.list) ? [...cachedUploadedFiles.list] : [];
  const existingIndex = nextList.findIndex((entry) => String(entry?.file_id ?? entry?.id ?? '') === fileId);
  if (existingIndex >= 0) {
    nextList.splice(existingIndex, 1);
  }
  nextList.unshift(normalized);
  cachedUploadedFiles.list = nextList;
  cachedUploadedFiles.lastFetched = Date.now();
  chatBoxUploadedFilesById.set(fileId, normalized);
}

async function ensureMeetingTranscriptVisible(result) {
  const normalizedChatId = String(result?.chat_id || '').trim();
  if (!normalizedChatId) {
    return;
  }

  try {
    if (typeof window.getCachedFilesList === 'function') {
      window.getCachedFilesList({ forceRefresh: true }).catch(() => {});
    }
  } catch (_) {}

  if (typeof window.initChatList === 'function') {
    try {
      await window.initChatList();
    } catch (_) {}
  }

  const chatContainerEl = document.getElementById('chatContainer');
  const currentChatId = String(chatContainerEl?.getAttribute('data-chat-id') || '').trim();
  const message = result?.message || null;
  const isSameChat = currentChatId && currentChatId === normalizedChatId;

  if (isSameChat && message?.id) {
    const files = Array.isArray(message.documents) ? message.documents : [];
    appendUserContent(message.id, '', files);
    appendAssistantContainer(message.id, { announce: true });
    if (typeof window.scrollUserMessageToTop === 'function') {
        requestAnimationFrame(() => window.scrollUserMessageToTop(message.id));
    }
    if (typeof window.moveChatRowToTop === 'function') {
      window.moveChatRowToTop(normalizedChatId);
    }
    return;
  }

  if (typeof window.loadChatView === 'function') {
    await window.loadChatView(normalizedChatId, false);
  } else {
    history.pushState({ chatId: normalizedChatId }, '', `/chat/${encodeURIComponent(normalizedChatId)}`);
  }

  const nextProjectId = String(result?.project_id || '').trim();
  if (nextProjectId && typeof window.loadProject === 'function') {
    window.loadProject(nextProjectId, normalizedChatId);
  }
}

async function submitMeetingTranscript() {
  if (meetingTranscriptState.isSubmitting) {
    return;
  }
  const selectedFile = meetingTranscriptState.selectedFile;
  if (!selectedFile) {
    notifyWarning?.(getChatI18nString('chat_meeting_choose_recording_first', 'Choose a meeting recording first.'));
    return;
  }
  if (!chatBoxMeetingConsentCheckbox?.checked) {
    notifyWarning?.(getChatI18nString('meeting_transcript_error_consent_required', 'Confirm participant notice and consent before transcribing.'));
    chatBoxMeetingConsentCheckbox?.focus?.();
    return;
  }
  const legalBasis = getMeetingLegalBasisValue();
  if (!legalBasis) {
    notifyWarning?.(getChatI18nString('meeting_transcript_error_legal_basis_required', 'Choose a legal basis before transcribing.'));
    // Focus the custom-select trigger button instead of a native control.
    chatBoxMeetingLegalBasis?.querySelector?.('.admin-select-trigger')?.focus?.();
    return;
  }
  const legalBasisDetails = getMeetingLegalBasisDetailsValue();
  if (!legalBasisDetails) {
    notifyWarning?.(getChatI18nString('meeting_transcript_error_legal_basis_details_required', 'Add the legal-basis details before transcribing.'));
    chatBoxMeetingLegalBasisDetails?.focus?.();
    return;
  }
  const retentionDays = getMeetingRetentionDaysValue();
  if (!Number.isInteger(retentionDays) || retentionDays < 1 || retentionDays > 3650) {
    notifyWarning?.(getChatI18nString('meeting_transcript_error_retention_days_required', 'Enter a retention window between 1 and 3650 days.'));
    chatBoxMeetingRetentionDays?.focus?.();
    return;
  }

  setMeetingSubmittingState(true);
  setMeetingProgressState({
    visible: true,
    label: getChatI18nString('chat_meeting_uploading', 'Uploading meeting…'),
    detail: getChatI18nString('chat_meeting_progress_preparing', 'Preparing the recording for transcription'),
    percent: 2,
  });

  const browserDate = getMeetingBrowserDateContext();
  const chatContainerEl = document.getElementById('chatContainer');
  const chatId = String(chatContainerEl?.getAttribute('data-chat-id') || '').trim();
  const projectId = String(chatContainerEl?.getAttribute('data-project-id') || '').trim();
  const formData = new FormData();
  formData.append('media', selectedFile);
  formData.append('browser_date_iso', browserDate.iso);
  formData.append('browser_date_label', browserDate.label);
  formData.append('consent_confirmed', chatBoxMeetingConsentCheckbox?.checked ? 'true' : 'false');
  formData.append('legal_basis', legalBasis);
  formData.append('legal_basis_details', legalBasisDetails);
  formData.append('retention_days', String(retentionDays));
  if (chatId) {
    formData.append('chat_id', chatId);
  }
  if (projectId) {
    formData.append('project_id', projectId);
  }

  const adapter = (input, init) => {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      const method = init?.method || 'GET';
      const url = typeof input === 'string'
        ? input
        : (input && typeof input === 'object' && 'url' in input
          ? input.url
          : String(input || ''));

      xhr.upload.addEventListener('progress', (event) => {
        if (!event.lengthComputable) return;
        const percent = event.total > 0 ? (event.loaded / event.total) * 100 : 0;
        meetingTranscriptState.uploadProgress = clampProgress(percent);
        setMeetingProgressState({
          visible: true,
          label: getChatI18nString('chat_meeting_uploading', 'Uploading meeting…'),
          detail: formatChatI18nString(
            'chat_meeting_progress_uploaded_percent',
            '{percent}% uploaded',
            { percent: Math.round(meetingTranscriptState.uploadProgress) },
          ),
          percent: Math.max(4, meetingTranscriptState.uploadProgress),
        });
      });

      xhr.upload.addEventListener('load', () => {
        setMeetingProgressState({
          visible: true,
          label: getChatI18nString('chat_meeting_transcribing', 'Transcribing meeting…'),
          detail: getChatI18nString('chat_meeting_progress_transcribing_detail', 'Extracting audio, transcribing, and saving the transcript file'),
          percent: 100,
        });
      });

      xhr.addEventListener('load', () => {
        const rawHeaders = xhr.getAllResponseHeaders() || '';
        let responseHeaders;
        if (typeof Headers !== 'undefined') {
          const headers = new Headers();
          rawHeaders.split(/[\r\n]+/).forEach((line) => {
            if (!line) return;
            const parts = line.split(': ');
            const header = parts.shift();
            const value = parts.join(': ');
            if (header) {
              headers.append(header, value);
            }
          });
          responseHeaders = headers;
        } else {
          responseHeaders = {};
        }
        resolve(new Response(xhr.responseText ?? '', {
          status: xhr.status,
          statusText: xhr.statusText,
          headers: responseHeaders,
        }));
      });

      xhr.addEventListener('error', () => reject(new Error(getChatI18nString('chat_meeting_upload_failed', 'Meeting upload failed'))));
      xhr.open(method, url);
      if (init?.headers instanceof Headers) {
        init.headers.forEach((value, key) => xhr.setRequestHeader(key, value));
      } else if (init?.headers && typeof init.headers === 'object') {
        Object.entries(init.headers).forEach(([key, value]) => {
          if (value !== undefined && value !== null) {
            xhr.setRequestHeader(key, value);
          }
        });
      }
      xhr.send(init?.body ?? null);
    });
  };

  try {
    const response = await window.authedFetch('/api/v1/chats/meetings/transcribe', {
      method: 'POST',
      body: formData,
      adapter,
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      const errorMessage = formatChatTranscriptionErrorMessage(
        payload,
        getChatI18nString('chat_meeting_transcription_failed_retry', 'Meeting transcription failed. Please try again.'),
        response.status,
      );
      throw new Error(errorMessage);
    }

    if (payload?.file) {
      upsertChatUploadedFileCache(payload.file);
    }
    await ensureMeetingTranscriptVisible(payload);
    closeMeetingModal({ force: true });
    notifySuccess?.(getChatI18nString('chat_meeting_transcript_added', 'Meeting transcript added to chat.'));
  } catch (error) {
    console.error('Meeting transcription failed', error);
    notifyError?.(error?.message || getChatI18nString('chat_meeting_transcription_failed', 'Meeting transcription failed.'));
  } finally {
    setMeetingSubmittingState(false);
    setMeetingProgressState({ visible: false, detail: '', percent: 0 });
  }
}
