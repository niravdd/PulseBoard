/**
 * PulseBoard — Cognito Authentication
 *
 * Uses Amazon Cognito User Pool with SRP auth (no SDK — pure fetch to Cognito API).
 * Stores tokens in sessionStorage. Handles first-login password change.
 */
(function () {
    'use strict';

    // These are populated from the SAM stack outputs
    // Override via window.PB_CONFIG before this script loads
    const CONFIG = window.PB_CONFIG || {
        userPoolId: '',       // e.g. us-east-1_xxxxxxx
        clientId: '',         // Cognito app client ID
        region: 'us-east-1',  // Cognito region
        apiBase: '',          // CloudFront URL (or API Gateway URL)
    };

    window.PBAuth = {
        _tokens: null,
        _user: null,

        init() {
            // Try to restore session
            const stored = sessionStorage.getItem('pb_tokens');
            if (stored) {
                try {
                    this._tokens = JSON.parse(stored);
                    this._user = sessionStorage.getItem('pb_user');
                    return true;
                } catch (_) {}
            }
            return false;
        },

        isAuthenticated() {
            return !!this._tokens?.IdToken;
        },

        getIdToken() {
            return this._tokens?.IdToken || '';
        },

        getUser() {
            return this._user || '';
        },

        async login(email, password) {
            const endpoint = `https://cognito-idp.${CONFIG.region}.amazonaws.com/`;
            const response = await fetch(endpoint, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/x-amz-json-1.1',
                    'X-Amz-Target': 'AWSCognitoIdentityProviderService.InitiateAuth',
                },
                body: JSON.stringify({
                    AuthFlow: 'USER_PASSWORD_AUTH',
                    ClientId: CONFIG.clientId,
                    AuthParameters: {
                        USERNAME: email,
                        PASSWORD: password,
                    },
                }),
            });

            const data = await response.json();

            if (data.ChallengeName === 'NEW_PASSWORD_REQUIRED') {
                // First login — need to set a new password
                return { challenge: 'NEW_PASSWORD_REQUIRED', session: data.Session };
            }

            if (data.AuthenticationResult) {
                this._tokens = data.AuthenticationResult;
                this._user = email;
                sessionStorage.setItem('pb_tokens', JSON.stringify(this._tokens));
                sessionStorage.setItem('pb_user', email);
                return { success: true };
            }

            throw new Error(data.message || data.__type || 'Authentication failed');
        },

        async completeNewPassword(email, newPassword, session) {
            const endpoint = `https://cognito-idp.${CONFIG.region}.amazonaws.com/`;
            const response = await fetch(endpoint, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/x-amz-json-1.1',
                    'X-Amz-Target': 'AWSCognitoIdentityProviderService.RespondToAuthChallenge',
                },
                body: JSON.stringify({
                    ChallengeName: 'NEW_PASSWORD_REQUIRED',
                    ClientId: CONFIG.clientId,
                    ChallengeResponses: {
                        USERNAME: email,
                        NEW_PASSWORD: newPassword,
                    },
                    Session: session,
                }),
            });

            const data = await response.json();
            if (data.AuthenticationResult) {
                this._tokens = data.AuthenticationResult;
                this._user = email;
                sessionStorage.setItem('pb_tokens', JSON.stringify(this._tokens));
                sessionStorage.setItem('pb_user', email);
                return { success: true };
            }

            throw new Error(data.message || 'Password change failed');
        },

        logout() {
            this._tokens = null;
            this._user = null;
            sessionStorage.removeItem('pb_tokens');
            sessionStorage.removeItem('pb_user');
        },

        /** Make an authenticated API call. */
        async api(path, opts = {}) {
            const headers = opts.headers || {};
            headers['Authorization'] = this.getIdToken();
            if (opts.body && typeof opts.body === 'object') {
                headers['Content-Type'] = 'application/json';
                opts.body = JSON.stringify(opts.body);
            }
            opts.headers = headers;
            const url = `${CONFIG.apiBase}${path}`;
            const res = await fetch(url, opts);
            if (!res.ok) {
                const err = await res.json().catch(() => ({ error: res.statusText }));
                throw new Error(err.error || err.message || `HTTP ${res.status}`);
            }
            return res.json();
        },
    };
})();
