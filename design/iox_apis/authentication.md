# Authentication

eisy-ui uses JWT-based authentication. All protected API routes require a valid access token. Tokens are issued as ES256 JWTs (NIST P-256 / prime256v1).

## Token Types

| Token | Header `typ` | Expiry | Purpose |
|---|---|---|---|
| Access token | `at+jwt` | 1 hour | Sent with every API request |
| Refresh token | `rt+jwt` | 30 days | Used once to obtain a new token pair |

Tokens carry these claims: `sub` (username), `uuid` (device UUID), `iss`, `aud`, `iat`, `exp`, and optionally `isUDI`.

---

## POST /api/login

Authenticates the user and returns a token pair. Supports four login modes.

**Local access only:** this endpoint is only reachable over the direct HTTP/HTTPS connection to the device. Requests routed through the portal proxy or the internal unix socket (used by PG3) are silently ignored.

### Login modes

#### 1. Cloud login — username/password
Authenticates against the portal API. Updates local cached credentials.

```json
{
  "username": "user@example.com",
  "password": "secret"
}
```

#### 2. Cloud login — portal OAuth2 access token
Authenticates using a portal OAuth2 access token. Does not update local credentials.

```json
{
  "portalAccessToken": "<portal-oauth2-token>"
}
```

#### 3. Local login — username/password
Authenticates against locally cached credentials. No portal connectivity required.

```json
{
  "username": "user@example.com",
  "password": "secret",
  "local": true
}
```

#### 4. Local login — portal JWT
Authenticates by verifying a portal-issued JWT locally (using the portal public key). No portal connectivity required.

```json
{
  "portalJwt": "<portal-jwt>",
  "local": true
}
```

### Validation rules
- Exactly one of `username`, `portalAccessToken`, or `portalJwt` must be provided.
- `password` is required when `username` is provided.
- `local: true` is required with `portalJwt`.
- `local` must be absent or `false` with `portalAccessToken`.

### Response `200 OK`

```json
{
  "successful": true,
  "data": {
    "accessToken": "<jwt>",
    "refreshToken": "<jwt>",
    "isDeveloper": false,
    "accountId": "507f1f77bcf86cd799439011",
    "emailVerified": true,
    "isLocalLogin": false,
    "pg3AuthResult": { ... },
    "pg3AuthFailed": false,
    "pg3AuthError": null
  }
}
```

`pg3AuthResult` contains the response body from PG3's `POST /auth` call. `pg3AuthFailed` and `pg3AuthError` are only relevant when PG3 is enabled.

### Error responses

| Status | Condition |
|---|---|
| `400` | Request body fails schema validation |
| `403` | Invalid credentials |
| `503` | IoX has not yet responded (system not ready) |

---

## API Authentication

All authenticated routes require the access token in the `Authorization` header.

### Bearer (JWT) — primary method

```
Authorization: Bearer <accessToken>
```

The middleware accepts both locally-issued and portal-issued access tokens. It inspects the `iss` claim to select the correct public key for verification. The token's `uuid` claim must match the device UUID.

### Basic Auth — secondary method

```
Authorization: Basic <base64(username:password)>
```

Credentials are verified against the portal. On ISY/UDX routes (`/rest`, `/services`), the password may be a locally-issued JWT instead of a plain password (used for plugin-to-ISY communication).

### Unauthenticated sources

Requests tagged as `proxy` or `internal` source bypass authentication entirely.

---

## POST /api/jwt/refresh

Exchanges a valid refresh token for a new access token + refresh token pair. The old refresh token is invalidated after use (one-time use).

### Request

```json
{
  "refreshToken": "<jwt>"
}
```

### Response `200 OK`

```json
{
  "successful": true,
  "data": {
    "accessToken": "<jwt>",
    "refreshToken": "<jwt>"
  }
}
```

### Error responses

| Status | Condition |
|---|---|
| `400` | Missing or malformed body |
| `403` | Token is invalid, expired, or has already been used |

---

## POST /api/jwt/logout

Invalidates the refresh token, preventing it from being used for future renewals. The access token is not invalidated server-side — it will expire naturally after 1 hour.

**Local access only:** same restriction as `/api/login` — only reachable over the direct HTTP/HTTPS connection to the device.

### Request

```json
{
  "refreshToken": "<jwt>",
  "accessToken": "<jwt>"
}
```

`accessToken` is optional and currently unused server-side.

### Response `200 OK`

```json
{
  "successful": true,
  "data": null
}
```

### Error responses

| Status | Condition |
|---|---|
| `400` | Missing or malformed body |
| `403` | Refresh token is invalid, expired, or already used |

---

## Typical auth flow

```
POST /api/login
  → store accessToken (memory) + refreshToken (secure storage)

Every API call:
  → Authorization: Bearer <accessToken>

On 401/JWT error:
  → POST /api/jwt/refresh  (send refreshToken, store new pair)
  → retry original request

On logout:
  → POST /api/jwt/logout  (send refreshToken)
  → discard both tokens locally
```
