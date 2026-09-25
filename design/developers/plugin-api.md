# Plugin API

All endpoints require authentication. Responses are wrapped in `{ success: true, data: ... }`.

---

## Installed Plugins

### `GET /api/plugins`
Returns all installed plugins with state and store description. Used by AI context.

**Response:** `PluginListItem[]`
```ts
{
  profileNum: number
  nsid: string
  name: string
  isLocal: boolean
  aiSupport: boolean
  enabled: boolean
  state: 'stopped' | 'starting' | 'connected' | 'failed'
  desc: string | null
}
```

---

### `GET /api/plugins/dashboard`
Returns minimal plugin info for the dashboard (no state, no store lookup).

**Response:** `PluginListDashboardItem[]`
```ts
{ profileNum: number; nsid: string; name: string; isLocal: boolean }
```

---

### `GET /api/plugins/notices`
Returns all notices for all installed plugins, keyed by `profileNum`.

**Response:** `Record<number, PluginNotices>`
```ts
{ [profileNum]: { [noticeId: string]: string } }
```

---

### `GET /api/plugins/connections`
Returns all plugin slots with their current occupant (if any). Used when selecting a slot during install.

**Response:** `PluginConnection[]`
```ts
{ profileNum: number; name: string | null; owner: string | null }
```

---

### `GET /api/plugins/licenses`
Returns active licenses from the portal for this device.

**Response:** `PluginLicenseEntry[]`
```ts
{
  nsid: string
  name: string
  edition: string
  license: 'active' | 'inactive' | 'expired' | 'trialExpired'
  expiry: string | null
  version: string | null
  purchaseDate: string | null
  txid: string | null
}
```

---

### `GET /api/plugins/reinstall-all`
Triggers a background reinstall of all installed plugins. Returns immediately.

---

## Plugin Detail

### `GET /api/plugin/:profileNum/details`
Returns full details for a single installed plugin.

**Response:** `PluginDetails`
```ts
{
  profileNum: number; nsid: string; name: string; type: string
  isLocal: boolean; isDeveloper: boolean; edition: string
  isTrial: boolean; isLicensed: boolean; expiry: string | null
  version: string; latestVersion: string | null
  shortPoll: number; longPoll: number
  discover: boolean; authorize: boolean; fileUpload: boolean
  push: boolean; isyAccess: boolean; allowIsyAccess: boolean
  aiSupport: boolean; startedAt: string | null
}
```

---

### `GET /api/plugin/:profileNum/notices`
Returns notices for a single plugin.

**Response:** `PluginNotices` — `{ [noticeId: string]: string }`

---

### `DELETE /api/plugin/:profileNum/notice/:noticeId`
Removes a single notice.

---

### `GET /api/plugin/:profileNum/custom/:key`
Returns a custom record value. JSON keys (`notices`, `oauth`, `customparams`, `customtypedparams`, `customtypeddata`, `nsdata`, `devd`) are parsed automatically.

---

### `POST /api/plugin/:profileNum/custom/:key`
Sets a custom record value. Body is stored as JSON.

---

### `POST /api/plugin/:profileNum/config`
Saves plugin configuration.

**Body:**
```ts
{ shortPoll: number; longPoll: number; allowIsyAccess: boolean }
```

---

## Plugin Actions

### `POST /api/plugin/:profileNum/start`
Starts (or restarts if already running) the plugin.

### `POST /api/plugin/:profileNum/stop`
Stops the plugin.

### `POST /api/plugin/:profileNum/restart`
Restarts the plugin.

### `POST /api/plugin/:profileNum/discover`
Sends a `polyglot.DISCOVER` event to the plugin.

---

### `DELETE /api/plugin/:profileNum`
Uninstalls the plugin from the slot.

---

## Plugin Log

### `GET /api/plugin/:profileNum/log/download`
Downloads the plugin's `debug.log` as a `.zip` file.

**Response:** `application/zip` attachment

---

## Custom Request (AI Tools)

### `POST /api/plugin/:profileNum/request`
Sends a custom request to the plugin and waits for a response. The plugin must support `customRequest`.

**Body:**
```ts
{ payload: Record<string, unknown>; timeout?: number } // timeout in ms, default 60000
```

**Response:** `Record<string, unknown> | null`

---

## OAuth

### `POST /api/plugin/:profileNum/oauth/cloudlink`
Stage 1 of the OAuth flow. Registers a cloudlink redirect with the portal.

**Body:** `{ redirect: string }` — the URL the portal should redirect back to after auth

**Response:** `{ id: string }` — cloudlink ID used as the OAuth `state` parameter

---

### `POST /api/plugin/:profileNum/oauth/token`
Stage 4–5 of the OAuth flow. Exchanges the auth code for tokens and forwards them to the plugin via MQTT.

**Body:** `{ code?: string; errorCode?: string }`

---

## AI

### `GET /api/plugin/:profileNum/prompt`
Returns the AI prompt for the plugin (from local store or portal).

**Response:** `string | null`

---

### `GET /api/plugin/:profileNum/tools`
Returns the AI tools definition for the plugin (from local store or portal).

**Response:** `Record<string, unknown> | null`

---

## Production Store

### `GET /api/plugins/store/prod/list/active`
Returns all active plugins from the portal store.

### `GET /api/plugins/store/prod/list/all`
Returns all plugins (active and inactive) from the portal store.

### `GET /api/plugins/store/prod/list/developer`
Returns the developer's own plugins from the portal. Requires developer role.

### `GET /api/plugins/store/prod/entry/:nsid`
Returns a single store entry.

### `GET /api/plugins/store/prod/actions/:nsid`
Returns a store entry with computed `action` per purchase option based on the device's licenses.

**Computed action values:**
| Action | Meaning |
|---|---|
| `purchase` | No license, paid option |
| `activate` | No license, free/trial option — or licensed beta with a newer version available |
| `install` | Valid license, ready to install |
| `renew` | Expired recurring license |
| `blocked` | Expired trial or beta |

### `PUT /api/plugins/store/prod/entry`
Creates a new store entry on the portal. Requires developer role.

### `POST /api/plugins/store/prod/entry/:nsid`
Updates an existing store entry on the portal. Requires developer role.

### `DELETE /api/plugins/store/prod/entry/:nsid`
Deletes a store entry from the portal. Requires developer role.

### `POST /api/plugins/store/prod/archive/:nsid`
Uploads a plugin archive (`.zip`, `.tgz`, `.tar.gz`, max 200 MB) to the portal. Requires developer role.

**Body:** `multipart/form-data` with field `archive`

---

## Store — Activation & Purchase

### `POST /api/plugins/store/prod/activate/free`
Activates a free plugin license.

**Body:** `{ nsid: string; optionID: number }`

### `POST /api/plugins/store/prod/activate/trial`
Activates a trial license.

**Body:** `{ nsid: string; optionID: number }`

### `POST /api/plugins/store/prod/activate/beta`
Activates a beta license.

**Body:** `{ nsid: string; optionID: number }`

### `GET /api/plugins/store/prod/purchase/:nsid`
Returns purchase info for a plugin from the portal.

### `POST /api/plugins/store/prod/purchase/order/create`
Creates a PayPal order.

**Body:**
```ts
{ nsid: string; optionID: number; partDescription: string; part: string; description: string; paymentSource: string }
```

### `POST /api/plugins/store/prod/purchase/order/capture`
Captures a PayPal order after user approval.

**Body:** `{ orderID: string; paymentID?: string; paymentSource: string }`

---

## Store — Install

### `POST /api/plugins/store/prod/install`
Installs a plugin from the production store and starts it.

**Body:**
```ts
{ nsid: string; purchaseOptionId: string; profileNum?: number }
```
`profileNum` is optional — omit for auto slot selection.

**Response:** `{ profileNum: number }`
** If not profileNum, one is auto selected **

---

### `POST /api/plugins/store/local/install`
Installs a plugin from the local dev store and starts it.

**Body:**
```ts
{ nsid: string; profileNum?: number }
```

**Response:** `{ profileNum: number }`
** If not profileNum, one is auto selected **

---

## Local Dev Store

### `GET /api/plugins/store/local/list`
Returns all local dev plugins.

**Response:** `DevStoreListItem[]`
```ts
{ nsid: string; name: string; type: 'python3' | 'node'; path: string; updatedAt: string }
```

---

### `GET /api/plugins/store/local/entry/:nsid`
Returns a single local dev plugin.

**Response:** `DevStore` — full plugin definition including all fields below.

---

### `PUT /api/plugins/store/local/entry`
Creates a new local dev plugin. Requires developer role. Validates that `path`, `executable`, and `runAs` are accessible on the filesystem.

**Body:**
```ts
{
  name: string          // max 15 chars
  type: 'python3' | 'node'
  path: string          // absolute path on eisy
  executable: string    // entry point filename
  runAs: string         // OS user
  desc?: string
  nsdata?: string       // JSON
  oauth?: string        // JSON
  customParams?: string // JSON
  devd?: string         // JSON
  aiPrompt?: string
  aiTools?: string      // JSON
  isyAccess?: boolean
  discover?: boolean
  authorize?: boolean
  fileUpload?: boolean
  shortPoll?: number
  longPoll?: number
  nsInfoPoll?: number
}
```

---

### `POST /api/plugins/store/local/entry/:nsid`
Updates an existing local dev plugin. All fields are optional. Requires developer role.
Also syncs the changes to the installed plugin record if the plugin is installed.

---

### `DELETE /api/plugins/store/local/entry/:nsid`
Deletes a local dev plugin. Requires developer role.

---

## PG3 Migration

### `GET /api/migration/plugins`
Returns all plugins currently installed in PG3, enriched with an `existsInProdStore` flag indicating whether the plugin can be migrated.

**Response:** `PluginMigrationItem[]`
```ts
{
  profileNum: number
  nsid: string
  name: string
  store: string | null
  migrated: boolean
  devMode: boolean
  existsInProdStore: boolean
}
```

---

### `POST /api/migration/plugins/:profileNum/migrate`
Migrates a plugin from PG3 to eisy-ui. The slot must be free or owned by PG3.

Migration steps:
1. Validates the slot is available
2. Retrieves PG3 plugin config
3. Fetches the store entry from the portal
4. Downloads plugin data files from PG3
5. Disables the plugin in PG3 *(point of no return)*
6. Validates the license
7. Installs the plugin in eisy-ui
8. Restores config, custom records, and data files
9. Starts the plugin

On failure after step 5, automatically rolls back by uninstalling from eisy-ui and re-enabling in PG3.

---

### `POST /api/migration/plugins/:profileNum/revert`
Reverts a migrated plugin back to PG3. Stops and uninstalls from eisy-ui, then re-enables in PG3.

---

### `DELETE /api/migration/plugins/:profileNum`
Removes the plugin from PG3 entirely (after migration is complete).

---

## File Manager

All file manager endpoints operate on the plugin's `data/` directory.

### `GET /api/plugin/:profileNum/filemanager/files`
Lists the root of the data directory.

### `GET /api/plugin/:profileNum/filemanager/files/*`
Lists a subdirectory. The path after `files/` is the directory id (e.g. `files/subdir`).

**Response:**
```ts
{ id: string; type: 'file' | 'folder'; size?: number; date: string }[]
```

---

### `GET /api/plugin/:profileNum/filemanager/info`
Returns disk usage of the data directory.

**Response:** `[{ used: number }]` (bytes)

---

### `POST /api/plugin/:profileNum/filemanager/upload?id=:parentId`
Uploads a file into the data directory. Max 200 MB.

**Body:** `multipart/form-data` with field `file`. Optional `name` field overrides the filename.

Notifies the plugin via MQTT after upload.

---

### `POST /api/plugin/:profileNum/filemanager/files/*`
Creates a folder.

**Body:** `{ name: string; type: 'folder' }`

---

### `PUT /api/plugin/:profileNum/filemanager/files/*`
Renames a file or folder.

**Body:** `{ operation: 'rename'; name: string }`

---

### `PUT /api/plugin/:profileNum/filemanager/files`
Moves or copies files.

**Body:** `{ operation: 'move' | 'copy'; ids: string[]; target: string }`

---

### `DELETE /api/plugin/:profileNum/filemanager/files`
Deletes files or folders.

**Body:** `{ ids: string[] }`
