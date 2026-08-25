# THE CLAIMERS — System Audit & Migration Master Specification

> **Version:** 3.1 (Updated)  |  **Date:** May 2026
> **Covers:** Main Backend (Service 1) + Telegram Bot (Service 2) + Tampermonkey Frontend (claimers.js v5.4.2)
> **Status:** Production-ready — includes all v3.0 base requirements plus v3.1 additions below.

---

## WHAT'S NEW IN v3.1

| # | New Requirement | Section |
|---|---|---|
| A | License key is **permanent** — only admin can change it; user is notified when changed | §A |
| B | All `/_tmc` WebSocket payloads **fully encrypted** (AES-128-GCM) when `ENABLE_RSA_AUTH=true` | §B |
| C | Socket.IO ACK ping-pong: **EVENT(2) → ACK(3)** pattern on every `/_tmc` emit | §C |
| D | **5-second delay** before sending Telegram claim notification after successful drop | §D |
| E | Full error-handling catalogue for all bot commands and backend routes | §E |

---

## §A — LICENSE KEY PERMANENCE & ROTATION

### A.1 Permanence Rule

A license key (`THECLAIMERS-<uuid4>`) is **permanent for the lifetime of the user's account**.

| Who | Can change key? |
|---|---|
| User | ❌ Never — key is fixed on `/start` |
| Admin (Service 1 backend) | ✅ Via `POST /api/xr9k/lic/rotate` |
| Automated system | ❌ Never auto-rotated |

The UUID inside the key is generated exactly once during `/start` and written to the `licenses` table as the primary key. It cannot be modified by the user, by the Tampermonkey script, or by any automated process.

### A.2 Admin-Initiated Key Rotation

When an admin needs to issue a new key to a user (e.g., after compromise):

**New internal endpoint:**

```
POST /api/xr9k/lic/rotate
Headers: x-internal-token: <INTERNAL_API_SECRET>
Body: {
  "telegram_id": 7710731128,
  "new_key": "THECLAIMERS-<new-uuid4>"   ← optional; if omitted, server generates
}
```

**Backend sequence (Service 1):**

```python
@internal_bp.route('/api/xr9k/lic/rotate', methods=['POST'])
def rotate_license():
    # 1. Validate x-internal-token
    # 2. Load existing license record by telegram_id
    # 3. Generate new key if not provided: f"THECLAIMERS-{uuid4()}"
    # 4. INSERT new license row with same telegram_id, active=True
    # 5. Emit 'license_key_changed' to old key room on /_tmc:
    #      { 'new_key': new_key, 'message': 'Your license key has been updated.' }
    # 6. socketio.close_room(old_room, namespace=TMC_NS)  ← disconnect old sessions
    # 7. DELETE or mark old license row as archived in DB
    # 8. Update active_license_cache: remove old key, add new key
    # 9. Notify bot via POST /nx/v3/lic-sync with old key active=None (deleted)
    # 10. Notify bot via POST /nx/v3/lic-sync with new key data
    # 11. Notify bot via POST /nx/v3/push to deliver Telegram message to user
```

**Telegram message sent to user (via /nx/v3/push):**

```
🔑 LICENSE KEY CHANGED

Your license key has been updated by the admin.

OLD KEY: THECLAIMERS-14ab...cf44
NEW KEY: THECLAIMERS-9f3c...a21b

Please update your key in the Tampermonkey popup.
Contact @adityaofficial96 if you need help.
```

**Frontend handling of `license_key_changed` event on `/_tmc`:**

```javascript
_tmcSocket.on('license_key_changed', (data) => {
  const newKey = data.new_key;
  GM_deleteValue('license_key');         // Clear old stored key
  _licenseKey = null;
  _licenseToken = null;
  _licenseTokenExpiry = 0;
  _tmcSocket.disconnect();
  _tmcSocket = null;
  // Show popup pre-filled with new key so user can verify in one click
  _showLicensePopup(newKey,
    `🔑 Your license key was updated by the admin. New key is pre-filled below.`
  );
});
```

### A.3 Bot Cache Update on Key Rotation

When the bot receives `/nx/v3/lic-sync` with `active=null` (deleted) for the old key, it removes the entry. Then a second call with `active=true` for the new key adds the new entry.

```python
# In bot/app.py lic_sync endpoint — already handles this:
if active is None:
    license_cache.remove_by_license_key(license_key)   # old key gone
else:
    license_cache.set(tid, license_key, active, ...)    # new key added
```

### A.4 Bot Notification of Key Change (handle_license_changed)

Add to `bot/handlers.py`:

```python
def handle_license_changed(
    user_id: int,
    old_key: str,
    new_key: str,
) -> None:
    """
    Called via /nx/v3/push — key rotation notification already formatted
    by Service 1 before being pushed to the bot. The bot relays it.
    This function is NOT needed directly; the /nx/v3/push endpoint handles
    arbitrary pre-formatted messages. Documented here for clarity.
    """
    # Service 1 formats the full message and calls /nx/v3/push.
    # Bot just delivers it via send_message(). No extra logic needed.
    pass
```

### A.5 Updated DB Constraint

The `licenses` table must NOT use `telegram_id` as a unique index when rotation is in progress. The sequence is:

1. INSERT new row (new key, same `telegram_id`)  ← unique index would block this
2. DELETE old row
3. COMMIT

**Fix:** Drop the `UNIQUE INDEX idx_licenses_telegram_id` during rotation, or use a `rotation_pending` flag column, or perform the delete-then-insert in a single transaction:

```sql
-- Atomic rotation in one transaction:
BEGIN;
  UPDATE licenses
     SET license_key = 'THECLAIMERS-new',
         activated_at = NOW()
   WHERE telegram_id = 7710731128;
COMMIT;
-- Primary key is license_key, so this doesn't work for PK change.
-- Instead:
BEGIN;
  INSERT INTO licenses (license_key, telegram_id, active, ...)
       VALUES ('THECLAIMERS-new', 7710731128, true, ...);
  DELETE FROM licenses WHERE license_key = 'THECLAIMERS-old';
COMMIT;
-- This requires temporarily relaxing the unique telegram_id index.
-- Implementation: use a DEFERRABLE INITIALLY DEFERRED unique constraint.
```

**Recommended:** make `idx_licenses_telegram_id` a `DEFERRABLE INITIALLY DEFERRED` unique constraint so both INSERT and DELETE can coexist within one transaction.

---

## §B — FULL PAYLOAD ENCRYPTION ON `/_tmc` (ENABLE_RSA_AUTH=true)

### B.1 Overview

When `ENABLE_RSA_AUTH=true`, every event payload sent on the `/_tmc` namespace is encrypted with AES-128-GCM using a per-session key. No plaintext JSON is ever visible on the wire.

When `ENABLE_RSA_AUTH=false`, payloads are plain JSON (dev/test mode).

### B.2 Encryption Architecture

```
Client (Tampermonkey)                    Server (/_tmc namespace)
─────────────────────                    ────────────────────────
On connect:
  Generate RSA-2048 key pair
  Send public key in auth payload  ──►  Encrypt AES-128 session key
                                        with client's RSA public key
  Receive encrypted session key   ◄──  Emit 'session_key' event
  Decrypt with RSA private key
  Store AES key for this session

Every subsequent event:
  AES-GCM encrypt payload         ──►  AES-GCM decrypt payload
  Send: { iv, tag, ct } as base64      Process cleartext
                                  ◄──  AES-GCM encrypt response
  AES-GCM decrypt response             Send: { iv, tag, ct }
```

### B.3 Session Key Delivery (connect handler — Service 1)

```python
# In tmc_routes.py /_tmc connect handler:
@socketio.on('connect', namespace=TMC_NS)
def tmc_connect():
    auth = request.args  # or request.headers for token
    # ... validate JWT license token ...

    if Config.ENABLE_RSA_AUTH:
        client_rsa_pub = auth.get('rsa_pub')  # PEM string, URL-encoded
        if not client_rsa_pub:
            raise ConnectionRefusedError({'code': 400, 'message': 'RSA public key required'})

        # Generate fresh AES-128 session key (16 bytes)
        import os as _os
        aes_key = _os.urandom(16)

        # Encrypt with client's RSA public key (OAEP + SHA-256)
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding
        from cryptography.hazmat.backends import default_backend
        import base64

        pub_key = serialization.load_pem_public_key(
            client_rsa_pub.encode(), backend=default_backend()
        )
        encrypted_key = pub_key.encrypt(
            aes_key,
            padding.OAEP(
                mgf=padding.MGF1(algorithm=hashes.SHA256()),
                algorithm=hashes.SHA256(),
                label=None
            )
        )
        # Store in per-SID registry
        _tmc_session_keys[request.sid] = aes_key

        # Deliver encrypted session key to client
        socketio.emit('session_key', {
            'key': base64.b64encode(encrypted_key).decode()
        }, room=request.sid, namespace=TMC_NS)
```

### B.4 Payload Encryption Helper (Service 1)

```python
# In license_manager.py or a new app/crypto.py module:
import base64, os
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

def encrypt_payload(aes_key: bytes, plaintext: dict) -> dict:
    """Encrypt a dict payload. Returns { iv, ct } for wire transfer."""
    if not Config.ENABLE_RSA_AUTH:
        return plaintext          # Pass-through in non-RSA mode

    import json
    iv = os.urandom(12)           # 96-bit IV (GCM standard)
    aesgcm = AESGCM(aes_key)
    ciphertext = aesgcm.encrypt(iv, json.dumps(plaintext).encode(), None)
    return {
        '__enc': True,
        'iv': base64.b64encode(iv).decode(),
        'ct': base64.b64encode(ciphertext).decode(),
    }

def decrypt_payload(aes_key: bytes, envelope: dict) -> dict:
    """Decrypt a received encrypted payload. Raises on tamper."""
    if not Config.ENABLE_RSA_AUTH or not envelope.get('__enc'):
        return envelope           # Pass-through

    import json
    iv = base64.b64decode(envelope['iv'])
    ct = base64.b64decode(envelope['ct'])
    aesgcm = AESGCM(aes_key)
    plaintext = aesgcm.decrypt(iv, ct, None)   # Raises InvalidTag on tamper
    return json.loads(plaintext)
```

### B.5 Tampermonkey Encryption (claimers.js)

```javascript
// ─── TMC Crypto ───────────────────────────────────────────────────────────
let _tmcAesKey = null;   // CryptoKey (AES-128-GCM) for this session

async function _generateRsaKeyPair() {
  return await crypto.subtle.generateKey(
    { name: 'RSA-OAEP', modulusLength: 2048,
      publicExponent: new Uint8Array([1, 0, 1]),
      hash: 'SHA-256' },
    true,  // extractable
    ['encrypt', 'decrypt']
  );
}

async function _exportPublicKeyPem(keyPair) {
  const spki = await crypto.subtle.exportKey('spki', keyPair.publicKey);
  const b64  = btoa(String.fromCharCode(...new Uint8Array(spki)));
  return `-----BEGIN PUBLIC KEY-----\n${b64.match(/.{1,64}/g).join('\n')}\n-----END PUBLIC KEY-----`;
}

async function _decryptSessionKey(keyPair, encryptedKeyB64) {
  const encBytes = Uint8Array.from(atob(encryptedKeyB64), c => c.charCodeAt(0));
  const rawKey   = await crypto.subtle.decrypt(
    { name: 'RSA-OAEP' }, keyPair.privateKey, encBytes
  );
  _tmcAesKey = await crypto.subtle.importKey(
    'raw', rawKey, { name: 'AES-GCM' }, false, ['encrypt', 'decrypt']
  );
}

async function _encryptPayload(obj) {
  if (!_tmcAesKey) return obj;   // Non-RSA mode — pass through
  const iv  = crypto.getRandomValues(new Uint8Array(12));
  const ct  = await crypto.subtle.encrypt(
    { name: 'AES-GCM', iv }, _tmcAesKey,
    new TextEncoder().encode(JSON.stringify(obj))
  );
  return {
    __enc: true,
    iv:   btoa(String.fromCharCode(...iv)),
    ct:   btoa(String.fromCharCode(...new Uint8Array(ct)))
  };
}

async function _decryptPayload(envelope) {
  if (!_tmcAesKey || !envelope?.__enc) return envelope;
  const iv  = Uint8Array.from(atob(envelope.iv), c => c.charCodeAt(0));
  const ct  = Uint8Array.from(atob(envelope.ct), c => c.charCodeAt(0));
  const pt  = await crypto.subtle.decrypt({ name: 'AES-GCM', iv }, _tmcAesKey, ct);
  return JSON.parse(new TextDecoder().decode(pt));
}
```

### B.6 Encryption Gate Summary

| `ENABLE_RSA_AUTH` | `/_tmc` payload | `/_v` payload | `/r2` SSE |
|---|---|---|---|
| `false` | Plaintext JSON | Plaintext JSON | AES-encrypted (always) |
| `true` | **AES-128-GCM encrypted** | AES-128-GCM encrypted (existing) | AES-encrypted (always) |

---

## §C — SOCKET.IO ACK PING-PONG PROTOCOL (2 → ACK 3)

### C.1 Socket.IO Wire Format Reminder

```
42["event", payload]    ← Client or Server sends EVENT (type 4=MSG, 2=EVENT)
43[ackId, result]       ← Receiver sends ACK  (type 4=MSG, 3=ACK)
```

On `/_tmc`, **every emit must use Socket.IO acknowledgments**. This creates a reliable 2→3 ping-pong delivery confirmation loop.

### C.2 Server → Client WITH ACK (Service 1)

```python
# In tmc_routes.py:
def _emit_with_ack(event: str, data: dict, room: str, timeout: float = 5.0):
    """
    Emit to a room and wait for client ACK.
    Returns True if at least one client acknowledged within timeout.
    """
    ack_received = threading.Event()

    def _on_ack(*args):
        ack_received.set()

    socketio.emit(
        event,
        data,
        room=room,
        namespace=TMC_NS,
        callback=_on_ack,
    )
    return ack_received.wait(timeout=timeout)


# Example — emitting fromTele with ACK requirement:
def emit_drop_to_license(license_key: str, code: str) -> bool:
    room   = f"license:{license_key}"
    payload = {
        'task': 'drop',
        'code': code,
        'license': license_key,
        'source': 'telegram',
        'timestamp': int(time.time() * 1000),
    }
    if Config.ENABLE_RSA_AUTH:
        aes_key = _get_session_key_for_room(room)
        payload = encrypt_payload(aes_key, payload)

    acked = _emit_with_ack('fromTele', payload, room=room)
    if not acked:
        logger.warning(f"emit_drop_to_license: no ACK received from room {room[:30]}")
    return acked
```

### C.3 Client → Server WITH ACK (claimers.js)

```javascript
// Client emits userClaim and expects server ACK within 5 s:
function _emitUserClaim(data) {
  _encryptPayload(data).then(encrypted => {
    _tmcSocket.emit('userClaim', encrypted, (ack) => {
      // Server sent ACK (43 frame) — delivery confirmed
      log(`✅ userClaim ACK received: ${JSON.stringify(ack)}`, 'debug');
    });
    // Timeout guard — if no ACK in 5 s, log a warning
    setTimeout(() => {
      log('⚠️ userClaim: no server ACK within 5s', 'warn');
    }, 5000);
  });
}

// Client emits sendBrowsers and expects server ACK:
function _emitSendBrowsers(data) {
  _encryptPayload(data).then(encrypted => {
    _tmcSocket.emit('sendBrowsers', encrypted, (ack) => {
      log(`✅ sendBrowsers ACK: ${JSON.stringify(ack)}`, 'debug');
    });
  });
}
```

### C.4 Server ACK Response Handler for Inbound Events (Service 1)

```python
# In tmc_routes.py:
@socketio.on('userClaim', namespace=TMC_NS)
def on_user_claim(data):
    """Receives claim result from Tampermonkey; sends immediate ACK."""
    # Validate SID is authenticated
    session_info = _tmc_sessions.get(request.sid)
    if not session_info:
        return {'ok': False, 'error': 'not_authenticated'}

    license_key = session_info['license_key']

    # Decrypt if RSA mode
    if Config.ENABLE_RSA_AUTH:
        aes_key = _tmc_session_keys.get(request.sid)
        if not aes_key:
            return {'ok': False, 'error': 'no_session_key'}
        try:
            data = decrypt_payload(aes_key, data)
        except Exception:
            logger.warning(f"userClaim: decrypt failed for sid={request.sid[:12]}")
            return {'ok': False, 'error': 'decrypt_failed'}

    # Immediately return ACK (the 43 frame) before any async processing
    # Queue the DB write asynchronously
    tmc_claim_queue.put({
        'license_key': license_key,
        'data': data,
        'received_at': time.time(),
    })

    return {'ok': True, 'received': True}   # This becomes the 43[ackId, result]
```

### C.5 Full Ping-Pong Flow Diagram

```
Telegram User    Service 1 (/_tmc)         Tampermonkey
─────────────    ─────────────────         ────────────
/drop CODE  ──►
              42["fromTele", {enc_payload}]  ──►
                                         43[ackId, {ok:true}]  ──►  (ACK back)
              (ack received — delivery confirmed)
                                         42["userClaim", {enc_result}]  ──►
              43[ackId, {ok:true,received:true}]  ◄──
              (claim logged to DB asynchronously)
              POST /nx/v3/push ──► Bot  ──► Telegram notification (after 5s delay)
```

---

## §D — 5-SECOND CLAIM NOTIFICATION DELAY

### D.1 Rule

When a `userClaim` packet arrives at Service 1 with `claimed=1` and `type=drop`, the Telegram notification to the license owner is **delayed by exactly 5 seconds** from the moment the packet is received.

**Why:** Prevents notification spam when many clients simultaneously claim the same code and ensures the DB write completes before the notification fires.

### D.2 Implementation (Service 1 — tmc_routes.py)

```python
# In on_user_claim handler, after decrypting and validating:
import eventlet

def _send_claim_notification_delayed(telegram_id: int, code: str,
                                     username: str, currency: str,
                                     claimed: bool, delay: float = 5.0):
    """Fire-and-forget delayed notification. Never blocks the WS handler."""
    def _notify():
        eventlet.sleep(delay)   # Non-blocking — yields to eventlet hub
        status_str = "✅ CLAIMED" if claimed else "❌ NOT CLAIMED"
        _call_bot_notify(
            telegram_id,
            f"⚡ <b>CLAIM RESULT</b>\n\n"
            f"🎟️ Code: <code>{code}</code>\n"
            f"👤 User: <code>{username}</code>\n"
            f"💰 Currency: {currency.upper()}\n"
            f"Status: {status_str}\n"
            f"⏰ {_now_ist()}"
        )

    eventlet.spawn_n(_notify)


# In the userClaim handler:
@socketio.on('userClaim', namespace=TMC_NS)
def on_user_claim(data):
    # ... decrypt, validate, queue DB write ...
    task_type = data.get('type', 'drop')
    results   = data.get('result', [])

    if task_type == 'drop' and results:
        r = results[0]
        username = r.get('username', 'unknown')
        currency = r.get('currency', 'usdt')
        claimed  = int(r.get('claimed', 0)) == 1

        # 5-second delayed Telegram notification
        _send_claim_notification_delayed(
            telegram_id=session_info['telegram_id'],
            code=data.get('code', ''),
            username=username,
            currency=currency,
            claimed=claimed,
            delay=5.0,
        )

    elif task_type == 'reload' and results:
        r = results[0]
        if int(r.get('claimed', 0)) == 1:
            # Reload notifications are NOT delayed (spec §9)
            _notify_reload_claimed(
                session_info['telegram_id'],
                r.get('username', 'unknown')
            )

    return {'ok': True, 'received': True}
```

### D.3 Reload Notification — No Delay

Reload notifications (`type=reload`, `claimed=1`) are sent **immediately** (no 5-second delay). The delay applies only to drop claims.

| Claim type | Notification delay |
|---|---|
| `type=drop`, `claimed=1` | **5 seconds** |
| `type=drop`, `claimed=0` | **5 seconds** |
| `type=reload`, `claimed=1` | **Immediate** |
| `type=reload`, `claimed=0` | No notification |

---

## §E — COMPLETE ERROR HANDLING CATALOGUE

### E.1 Telegram Bot Command Errors

| Scenario | Handler response |
|---|---|
| Backend unreachable on `/start` registration | "⚠️ Registration Error — try again or contact @adityaofficial96" |
| Backend unreachable on `/drop` | "⚠️ DROP FAILED — could not reach backend" |
| Backend returns 0 connected clients on `/drop` | "⚠️ NO CLAIMERS CONNECTED — start Tampermonkey first" |
| Backend timeout on `/reload` (12 s) | "⚠️ NO RESPONSE — are your claimers connected?" |
| Backend timeout on `/connected` (12 s) | "⚠️ NO RESPONSE — are your claimers connected?" |
| License not in cache AND backend returns 404 | Show "❌ NO LICENSE FOUND — send /start" |
| License inactive (active=false) | "🔴 LICENSE NOT ACTIVE — contact @adityaofficial96" |
| Rate limit exceeded | "⏳ SLOW DOWN — wait X seconds" (exact seconds shown) |
| Code contains forbidden keyword | "❌ Code contains a reserved word: `<kw>`" |
| Code > 64 chars | "❌ Code too long (max 64 characters)" |
| Invalid callback_data (unknown button) | `answer_callback_query` only, no message sent |
| Telegram send_message returns ok=False | Logged at ERROR level; request continues |
| ForceReply reply from wrong user | Silently ignored (security) |
| ForceReply reply after 5-min TTL | Silently ignored (expired) |

### E.2 Bot Internal Endpoint Errors

| Route | Scenario | HTTP response |
|---|---|---|
| `POST /wh/z7q2/tg` | Missing/invalid secret token | 403 Forbidden |
| `POST /wh/z7q2/tg` | Malformed JSON body | 200 OK (silently dropped — never 4xx to Telegram) |
| `POST /nx/v3/push` | Missing x-internal-token | 401 Unauthorized |
| `POST /nx/v3/push` | Missing telegram_id or message | 400 Bad Request |
| `POST /nx/v3/push` | Telegram API rejects message | 502 Bad Gateway |
| `POST /nx/v3/lic-sync` | Missing x-internal-token | 401 Unauthorized |
| `POST /nx/v3/lic-sync` | Missing license_key or telegram_id | 400 Bad Request |
| `POST /nx/v3/lic-sync` | Unknown license_key (no-op) | 200 OK (cache miss is not an error) |

### E.3 Service 1 `/_tmc` Namespace Errors

| Scenario | Server response |
|---|---|
| Missing `token` in auth | `raise ConnectionRefusedError({'code': 401, 'message': 'Token required'})` |
| JWT expired | `raise ConnectionRefusedError({'code': 401, 'message': 'Token expired'})` |
| JWT signature invalid | `raise ConnectionRefusedError({'code': 401, 'message': 'Invalid token'})` |
| License not in active cache | `raise ConnectionRefusedError({'code': 403, 'message': 'License not active'})` |
| License banned | `raise ConnectionRefusedError({'code': 403, 'message': 'License banned'})` |
| Username limit reached | `raise ConnectionRefusedError({'code': 429, 'message': 'Username limit reached'})` |
| RSA mode: missing rsa_pub | `raise ConnectionRefusedError({'code': 400, 'message': 'RSA public key required'})` |
| RSA mode: decrypt failure on userClaim | Return ACK `{'ok': False, 'error': 'decrypt_failed'}` |
| DB write failure in claim queue | Log ERROR; do NOT disconnect client; retry up to 3× |
| Bot service unreachable for notification | Log WARNING; silently drop; WS session continues |
| `emit_drop_to_license`: no ACK within 5 s | Log WARNING; return `connected_clients=0` to bot |

### E.4 Service 1 Internal API Errors

| Route | Scenario | HTTP response |
|---|---|---|
| All `/api/xr9k/*` | Missing x-internal-token | 401 |
| `POST /api/xr9k/lic/reg` | telegram_id already has a license | 409 Conflict `{'error': 'already_registered'}` |
| `GET /api/xr9k/lic/info` | telegram_id not found | 404 Not Found |
| `POST /api/xr9k/lic/drop` | License not in active cache | 404 `{'error': 'license_not_active'}` |
| `POST /api/xr9k/lic/drop` | No clients in room | 200 `{'ok': true, 'connected_clients': 0}` |
| `POST /api/xr9k/lic/rl` | No clients respond within 5 s | 200 `{'results': []}` |
| `GET /api/xr9k/lic/browsers` | No clients respond within 5 s | 200 `{'browsers': 0, 'accounts': []}` |
| `POST /api/xr9k/lic/rotate` | telegram_id not found | 404 |
| `POST /api/xr9k/lic/rotate` | DB transaction fails | 500 with rollback |

### E.5 Encryption Error Handling

| Scenario | Action |
|---|---|
| Client sends non-encrypted payload when RSA mode on | Return ACK `{'ok': False, 'error': 'encryption_required'}` |
| AES-GCM authentication tag mismatch (tampered) | Log SECURITY WARNING with SID; disconnect client |
| Client's RSA public key is malformed | Refuse connection 400 |
| Session key missing for a connected SID | Disconnect SID; client will reconnect |

---

## COMPLETE FILE LIST — SERVICE 2 (theclaimers-bot/)

```
theclaimers-bot/
├── bot/
│   ├── __init__.py          Empty package marker
│   ├── app.py               Flask factory: webhook + /nx/v3/push + /nx/v3/lic-sync + /health
│   ├── handlers.py          /start /drop /reload /connected /license + callback + ForceReply
│   ├── license_cache.py     Thread-safe in-memory dual-index license store
│   ├── rate_limiter.py      Per-user sliding-window rate limiter
│   ├── helpers.py           now_ist(), shorten_key(), format_time_left()
│   ├── backend_client.py    HTTP client for Service 1 internal API
│   └── telegram_api.py      Thin wrapper over Telegram Bot API (requests-based)
├── bot_wsgi.py              Gunicorn WSGI entry point + startup routine
├── bot_requirements.txt     Pinned dependencies
├── setup_webhook.py         One-shot webhook registration utility
├── render.yaml              Render deployment configuration
├── .env.example             Environment variable template
└── .gitignore               Excludes .env, __pycache__, etc.
```

---

## UPDATED ENVIRONMENT VARIABLES (v3.1 additions)

### Service 1 — New in v3.1

| Variable | Required | Description |
|---|---|---|
| (none new) | — | All v3.0 vars unchanged |

### Service 2 — Updated

| Variable | Required | Description |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | ✅ | From @BotFather |
| `TELEGRAM_WEBHOOK_SECRET` | ✅ | Random 64-char hex |
| `INTERNAL_API_SECRET` | ✅ | Same as Service 1 |
| `BACKEND_INTERNAL_URL` | ✅ | Service 1 public URL |
| `BOT_PUBLIC_URL` | Optional | Auto-detected from `RENDER_EXTERNAL_URL` |

---

## UPDATED MIGRATION CHECKLIST (v3.1 additions)

### Phase 1 — Database (new in v3.1)
- [ ] Make `idx_licenses_telegram_id` a DEFERRABLE INITIALLY DEFERRED constraint (for key rotation)

### Phase 2 — Backend Service 1 (new in v3.1)
- [ ] Add `POST /api/xr9k/lic/rotate` endpoint to `app/routes/internal.py`
- [ ] Add `license_key_changed` event emit in rotate handler
- [ ] Add `_tmc_session_keys` dict and `encrypt_payload()` / `decrypt_payload()` in `license_manager.py`
- [ ] Add RSA public key handshake in `/_tmc` connect handler (gated on `ENABLE_RSA_AUTH`)
- [ ] Add `session_key` emit after RSA handshake
- [ ] Wrap all `/_tmc` emits with `encrypt_payload()` (no-op when `ENABLE_RSA_AUTH=false`)
- [ ] Add `_emit_with_ack()` helper; use for all `fromTele` and `getBrowsers` emits
- [ ] Change `userClaim` and `sendBrowsers` handlers to return ACK dict
- [ ] Add `_send_claim_notification_delayed()` with 5-second `eventlet.sleep`

### Phase 3 — Bot Service 2 (new in v3.1)
- [ ] Verify `/nx/v3/lic-sync` handles `active=null` (key deletion/rotation)
- [ ] Verify `/nx/v3/push` relays key-changed notification text correctly

### Phase 4 — Frontend claimers.js (new in v3.1)
- [ ] Add `_tmcAesKey`, `_generateRsaKeyPair()`, `_exportPublicKeyPem()`
- [ ] Add RSA public key to `/_tmc` connect auth (gated on server mode)
- [ ] Handle `session_key` event → decrypt → store `_tmcAesKey`
- [ ] Wrap all outbound emits with `_encryptPayload()` (async)
- [ ] Wrap all inbound events with `_decryptPayload()` (async)
- [ ] Add ACK callbacks to `_emitUserClaim()` and `_emitSendBrowsers()`
- [ ] Add `license_key_changed` handler → clear key → pre-fill popup with new key

### Phase 5 — End-to-End Testing (new in v3.1)
- [ ] Admin calls `/api/xr9k/lic/rotate` → old room gets `license_key_changed` → popup pre-filled → Telegram message sent
- [ ] With `ENABLE_RSA_AUTH=true` → network tab shows encrypted `{__enc,iv,ct}` payloads (no readable JSON)
- [ ] userClaim emitted → server returns `43[ackId,{ok:true}]` within 1 s
- [ ] fromTele emitted → client returns ACK within 1 s
- [ ] Successful drop → Telegram notification arrives exactly 5 s after userClaim received
- [ ] Reload claim → Telegram notification arrives immediately (no delay)
- [ ] AES-GCM tampered payload → SID disconnected, SECURITY WARNING in logs

---

*Version 3.1 — May 2026 — All sections complete and production-ready.*
