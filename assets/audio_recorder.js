// Audio Recorder mit MediaRecorder API
let mediaRecorder = null;
let audioChunks = [];
let audioStream = null;
let onCompleteCallback = null;
let onErrorCallback = null;
let recordingTimer = null;
let recordingStartTime = null;

// Formatiert Sekunden als MM:SS
function formatDuration(seconds) {
    const mins = Math.floor(seconds / 60);
    const secs = seconds % 60;
    return `${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
}

// Aktualisiert die Aufnahmedauer-Anzeige
function updateDurationDisplay() {
    if (recordingStartTime) {
        const elapsed = Math.floor((Date.now() - recordingStartTime) / 1000);
        // Versuche mehrere mögliche Element-Selektoren
        let durationEl = document.getElementById('recording-duration');
        if (!durationEl) {
            // Fallback: Suche nach dem Element über CSS-Klassen oder andere Attribute
            durationEl = document.querySelector('[id="recording-duration"]');
        }
        if (!durationEl) {
            // Zweiter Fallback: Suche nach einem span mit monospace Font im Recording-Bereich
            const spans = document.querySelectorAll('span');
            for (const span of spans) {
                if (span.textContent && span.textContent.match(/^\d{2}:\d{2}$/)) {
                    durationEl = span;
                    break;
                }
            }
        }
        if (durationEl) {
            durationEl.textContent = formatDuration(elapsed);
        } else {
            console.log('Duration element not found, elapsed:', elapsed);
        }
    }
}

// Startet die Audio-Aufnahme
async function startAudioRecording() {
    try {
        // Mikrofon-Zugriff anfordern
        audioStream = await navigator.mediaDevices.getUserMedia({ 
            audio: {
                echoCancellation: true,
                noiseSuppression: true,
                sampleRate: 44100
            } 
        });
        
        // MediaRecorder mit WebM/Opus für beste Kompatibilität
        const mimeType = MediaRecorder.isTypeSupported('audio/webm;codecs=opus') 
            ? 'audio/webm;codecs=opus' 
            : 'audio/webm';
        
        mediaRecorder = new MediaRecorder(audioStream, { mimeType });
        audioChunks = [];
        
        mediaRecorder.ondataavailable = (event) => {
            if (event.data.size > 0) {
                audioChunks.push(event.data);
            }
        };
        
        mediaRecorder.onstop = async () => {
            // Timer stoppen
            if (recordingTimer) {
                clearInterval(recordingTimer);
                recordingTimer = null;
            }
            recordingStartTime = null;
            
            // Audio-Blob erstellen
            const audioBlob = new Blob(audioChunks, { type: mimeType });
            
            // Stream stoppen
            if (audioStream) {
                audioStream.getTracks().forEach(track => track.stop());
                audioStream = null;
            }
            
            // In Base64 konvertieren
            const reader = new FileReader();
            reader.onloadend = () => {
                const base64Audio = reader.result.split(',')[1];
                // Speichere das Ergebnis global für den Callback
                window._lastAudioRecording = {
                    audioData: base64Audio,
                    mimeType: mimeType
                };
                console.log('Audio-Aufnahme abgeschlossen, Größe:', audioBlob.size, 'bytes');
            };
            reader.readAsDataURL(audioBlob);
        };
        
        mediaRecorder.onerror = (event) => {
            console.error('MediaRecorder Fehler:', event.error);
            window._lastAudioError = event.error.message || 'Aufnahmefehler';
            // Timer stoppen bei Fehler
            if (recordingTimer) {
                clearInterval(recordingTimer);
                recordingTimer = null;
            }
            recordingStartTime = null;
        };
        
        // Aufnahme starten
        mediaRecorder.start(1000); // Alle 1000ms Daten sammeln
        console.log('Audio-Aufnahme gestartet');
        
        // Timer mit kurzer Verzögerung starten, damit das DOM sich aktualisieren kann
        recordingStartTime = Date.now();
        setTimeout(() => {
            updateDurationDisplay(); // Erste Aktualisierung nach DOM-Update
            recordingTimer = setInterval(updateDurationDisplay, 1000);
        }, 100);
        
        return true;
        
    } catch (error) {
        console.error('Fehler beim Starten der Aufnahme:', error);
        let errorMessage = 'Mikrofon-Zugriff nicht möglich.';
        if (error.name === 'NotAllowedError') {
            errorMessage = 'Mikrofon-Zugriff wurde verweigert. Bitte erlauben Sie den Zugriff in den Browser-Einstellungen.';
        } else if (error.name === 'NotFoundError') {
            errorMessage = 'Kein Mikrofon gefunden. Bitte schließen Sie ein Mikrofon an.';
        }
        window._lastAudioError = errorMessage;
        return false;
    }
}

// Stoppt die Audio-Aufnahme und gibt Promise zurück
function stopAudioRecording() {
    return new Promise((resolve, reject) => {
        if (!mediaRecorder || mediaRecorder.state === 'inactive') {
            reject('Keine aktive Aufnahme');
            return;
        }
        
        const mimeType = mediaRecorder.mimeType;
        // Timer sofort stoppen und Anzeige aktualisieren
        if (recordingTimer) {
            clearInterval(recordingTimer);
            recordingTimer = null;
        }
        // Setze Startzeit zurück damit updateDurationDisplay nicht mehr läuft
        const nowElapsed = recordingStartTime ? Math.floor((Date.now() - recordingStartTime) / 1000) : 0;
        recordingStartTime = null;
        // Aktualisiere Anzeige einmalig auf den letzten Wert
        try {
            const durationEl = document.getElementById('recording-duration') || document.querySelector('[id="recording-duration"]');
            if (durationEl) {
                durationEl.textContent = formatDuration(nowElapsed);
            }
        } catch (e) {
            console.debug('Could not update duration display on stop:', e);
        }

        mediaRecorder.onstop = async () => {
            // Audio-Blob erstellen
            const audioBlob = new Blob(audioChunks, { type: mimeType });

            // Stream stoppen
            if (audioStream) {
                audioStream.getTracks().forEach(track => track.stop());
                audioStream = null;
            }

            // In Base64 konvertieren
            const reader = new FileReader();
            reader.onloadend = () => {
                const base64Audio = reader.result.split(',')[1];
                resolve(JSON.stringify({
                    audioData: base64Audio,
                    mimeType: mimeType
                }));
            };
            reader.onerror = () => {
                reject('Fehler beim Lesen der Audio-Daten');
            };
            reader.readAsDataURL(audioBlob);
        };

        mediaRecorder.stop();
        console.log('Audio-Aufnahme gestoppt');
    });
}

// Synchrone Stop-Funktion die das Ergebnis in einem globalen Objekt speichert
function stopAudioRecordingSync() {
    if (mediaRecorder && mediaRecorder.state !== 'inactive') {
        mediaRecorder.stop();
        console.log('Audio-Aufnahme gestoppt (sync)');
        return true;
    }
    return false;
}

// Holt die letzte Aufnahme
function getLastRecording() {
    const result = window._lastAudioRecording || null;
    window._lastAudioRecording = null;
    return result ? JSON.stringify(result) : null;
}

// Holt den letzten Fehler
function getLastError() {
    const error = window._lastAudioError || null;
    window._lastAudioError = null;
    return error;
}

// Prüft ob Aufnahme aktiv ist
function isRecording() {
    return mediaRecorder && mediaRecorder.state === 'recording';
}

// Bricht die Aufnahme ab ohne zu speichern
function cancelAudioRecording() {
    if (audioStream) {
        audioStream.getTracks().forEach(track => track.stop());
        audioStream = null;
    }
    if (mediaRecorder && mediaRecorder.state !== 'inactive') {
        mediaRecorder.stop();
    }
    audioChunks = [];
    mediaRecorder = null;
    window._lastAudioRecording = null;
    window._lastAudioError = null;
}

// Exportiere Funktionen global
window.audioRecorder = {
    start: startAudioRecording,
    stop: stopAudioRecording,
    stopSync: stopAudioRecordingSync,
    getLastRecording: getLastRecording,
    getLastError: getLastError,
    cancel: cancelAudioRecording,
    isRecording: isRecording
};
