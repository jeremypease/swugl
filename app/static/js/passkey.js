/* WebAuthn passkey helpers — registration and authentication */

function getCsrfToken() {
    const el = document.querySelector('meta[name="csrf-token"]');
    return el ? el.content : '';
}

function base64urlToBuffer(base64url) {
    const base64 = base64url.replace(/-/g, '+').replace(/_/g, '/');
    const binary = atob(base64);
    const buf = new ArrayBuffer(binary.length);
    const view = new Uint8Array(buf);
    for (let i = 0; i < binary.length; i++) view[i] = binary.charCodeAt(i);
    return buf;
}

function bufferToBase64url(buffer) {
    const bytes = new Uint8Array(buffer);
    let binary = '';
    for (const b of bytes) binary += String.fromCharCode(b);
    return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=/g, '');
}

async function jsonFetch(url, body) {
    const res = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
        body: body !== undefined ? JSON.stringify(body) : undefined,
    });
    return res;
}

// ── Registration (profile/security page) ──────────────────────────────────

async function registerPasskey() {
    const nameInput = document.getElementById('passkey-name');
    const deviceName = nameInput ? nameInput.value.trim() || 'My passkey' : 'My passkey';
    const statusEl = document.getElementById('passkey-status');

    function setStatus(msg, isError) {
        if (statusEl) { statusEl.textContent = msg; statusEl.className = isError ? 'form-error' : 'form-hint'; }
    }

    try {
        setStatus('Preparing…', false);
        const beginRes = await jsonFetch('/profile/security/passkeys/register/begin');
        if (!beginRes.ok) throw new Error('Server error starting registration.');
        const options = await beginRes.json();

        options.challenge = base64urlToBuffer(options.challenge);
        options.user.id = base64urlToBuffer(options.user.id);
        if (options.excludeCredentials) {
            options.excludeCredentials = options.excludeCredentials.map(c => ({
                ...c, id: base64urlToBuffer(c.id),
            }));
        }

        setStatus('Waiting for Face ID / Touch ID…', false);
        const credential = await navigator.credentials.create({ publicKey: options });

        const credentialJSON = {
            id: credential.id,
            rawId: bufferToBase64url(credential.rawId),
            response: {
                clientDataJSON: bufferToBase64url(credential.response.clientDataJSON),
                attestationObject: bufferToBase64url(credential.response.attestationObject),
            },
            type: credential.type,
            clientExtensionResults: credential.getClientExtensionResults(),
        };

        const completeRes = await jsonFetch('/profile/security/passkeys/register/complete', {
            credential: credentialJSON,
            device_name: deviceName,
        });
        if (!completeRes.ok) {
            const err = await completeRes.json();
            throw new Error(err.error || 'Verification failed.');
        }

        setStatus('Passkey registered!', false);
        setTimeout(() => window.location.reload(), 800);
    } catch (err) {
        if (err.name === 'NotAllowedError') {
            setStatus('Cancelled — no passkey saved.', false);
        } else {
            setStatus('Error: ' + err.message, true);
        }
    }
}

// ── Authentication (login 2FA page) ───────────────────────────────────────

async function authenticateWithPasskey() {
    const statusEl = document.getElementById('passkey-status');
    function setStatus(msg, isError) {
        if (statusEl) { statusEl.textContent = msg; statusEl.className = isError ? 'form-error' : 'form-hint'; }
    }

    try {
        setStatus('Waiting for Face ID / Touch ID…', false);
        const beginRes = await jsonFetch('/login/2fa/passkey/begin');
        if (!beginRes.ok) throw new Error('Server error.');
        const options = await beginRes.json();

        options.challenge = base64urlToBuffer(options.challenge);
        if (options.allowCredentials) {
            options.allowCredentials = options.allowCredentials.map(c => ({
                ...c, id: base64urlToBuffer(c.id),
            }));
        }

        const credential = await navigator.credentials.get({ publicKey: options });

        const credentialJSON = {
            id: credential.id,
            rawId: bufferToBase64url(credential.rawId),
            response: {
                clientDataJSON: bufferToBase64url(credential.response.clientDataJSON),
                authenticatorData: bufferToBase64url(credential.response.authenticatorData),
                signature: bufferToBase64url(credential.response.signature),
                userHandle: credential.response.userHandle
                    ? bufferToBase64url(credential.response.userHandle) : null,
            },
            type: credential.type,
            clientExtensionResults: credential.getClientExtensionResults(),
        };

        const completeRes = await jsonFetch('/login/2fa/passkey/complete', credentialJSON);
        const result = await completeRes.json();
        if (result.success) {
            window.location.href = result.redirect || '/home';
        } else {
            throw new Error(result.error || 'Authentication failed.');
        }
    } catch (err) {
        if (err.name === 'NotAllowedError') {
            setStatus('Cancelled.', false);
        } else {
            setStatus('Error: ' + err.message, true);
        }
    }
}
