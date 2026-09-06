// Routes: /api/family
//
// ── Design note: SOAP namespace abstraction ──────────────────────────────────
// All SOAP calls go through the same /services endpoint using the same namespace
// (e.g. X_Insteon_Lighting_Service:1 or X_IoX_Service:1 depending on the client).
// The client never deals with SOAP namespaces. This middleware is the abstraction
// layer — each route calls webService() with the correct action name and the
// firmware handles routing internally. This is why all family-level
// operations (regardless of family) are expressed as plain POST routes here.
//
// ── Insteon only ─────────────────────────────────────────────────────────────
// POST /family/:family/:instance/node/:address/restore      — reprogram physical device from IoX settings
// POST /family/:family/:instance/node/:address/query-engine — fire-and-forget engine version re-query
// POST /family/:family/:instance/node/:address/links/device — trigger G_DEV_ALL stream
// POST /family/:family/:instance/node/:address/links/iox    — trigger G_ISY_ALL stream
// POST /family/:family/:instance/links/stop                 — stop any in-progress DeviceSpecific stream
// POST /family/:family/:instance/node/:address/replace      — replace device; body: { newAddress, newDeviceType }
// GET  /family/:family/:instance/node/:address/plm          — read PLM scene profiles via G_CTL_SP
// POST /family/:family/:instance/node/:address/plm          — write cleanup retries via S_CLNRT_SP; body: { responderAddress, clnrt }
// POST /family/:family/:instance/restore-modem              — SOAP ReplaceModem
// POST /family/:family/:instance/delete-modem               — SOAP RemoveModem
// POST /family/:family/:instance/plm-info                   — SOAP DeviceSpecific G_PLM_INFO; returns string
// POST /family/:family/:instance/plm-links                  — SOAP DeviceSpecific G_PLM_ALL; triggers stream
// POST /family/:family/:instance/start-linking              — SOAP DiscoverNodes; optional body: { deviceType }
// POST /family/:family/:instance/set-linking-mode           — SOAP SetDeviceLinkingMode; body: { mode }
// GET  /family/:family/:instance/insteon-messaging          — SOAP DeviceSpecific G_IEO; returns current value
// POST /family/:family/:instance/insteon-messaging          — SOAP DeviceSpecific S_IEO; body: { value }
// POST /family/:family/:instance/stop-linking               — SOAP CancelNodesDiscovery; body: { flag: 1|3|4 }
// POST /family/:family/:instance/add-node                   — SOAP AddNode; body: { flag: 1|3|4, address?, name?, deviceType? }
// POST /family/:family/:instance/scene-test/raw-off         — SOAP DeviceSpecific DB op=4 (raw PLM group-off); body: { physicalGroupNum: number }
//
// ── Z-Wave Legacy only ───────────────────────────────────────────────────────
// POST /family/:family/:instance/node/:address/synchronize-node  — SOAP SyncFull
// POST /family/:family/:instance/node/:address/remove-failed     — SOAP RemoveFailedNode
// POST /family/:family/:instance/node/:address/replace-failed    — SOAP ReplaceFailedNode
// POST /family/:family/:instance/node/:address/update-neighbors  — SOAP UpdateNeighbors
// POST /family/:family/:instance/node/:address/repair-links      — SOAP RepairLinks
// POST /family/:family/:instance/include                         — SOAP IncludeDevice
// POST /family/:family/:instance/exclude                         — SOAP ExcludeDevice
// POST /family/:family/:instance/stop                            — SOAP StopIncludeExclude
// POST /family/:family/:instance/shift-primary                   — SOAP ReplicateSendPrimary
// POST /family/:family/:instance/learn-mode                      — SOAP StartLearnMode
// POST /family/:family/:instance/sync-new-deleted                — SOAP Sync
// POST /family/:family/:instance/sync-all                        — SOAP SyncFull (family-level)
// POST /family/:family/:instance/key-unprotect                   — SOAP UnprotectNetworkKey
// POST /family/:family/:instance/key-protect                     — SOAP ProtectNetworkKey
// POST /family/:family/:instance/nwi-on                          — SOAP SetNWIOn
// POST /family/:family/:instance/nwi-off                         — SOAP SetNWIOff
// POST /family/:family/:instance/sleep-on                        — SOAP SetAutoSleepOn
// POST /family/:family/:instance/sleep-off                       — SOAP SetAutoSleepOff
// POST /family/:family/:instance/freeze-on                       — SOAP SetNodeFreezeOn
// POST /family/:family/:instance/freeze-off                      — SOAP SetNodeFreezeOff
// POST /family/:family/:instance/factory-reset                   — SOAP FactoryResetDongle, body: <force>true</force>
//
// ── Multiple families ────────────────────────────────────────────────────────
// POST /family/:family/:instance/node/:address/write-updates — Insteon + Z-Wave Legacy=SOAP, ZMatter Z-Wave + Zigbee + Matter=REST, others=400
// POST /family/:family/:instance/node/:address/synchronize   — ZMatter Z-Wave + Zigbee + Matter only; body: { mode }; others=400

import express from 'express';
import isy from '@services/isy';
import { z } from 'zod';
import xml2js from 'xml2js';
import { BadRequest } from '@utils/httpErrors';
import { webService } from '@services/isy/isyWebService';
import { validateBody } from '@utils/middlewares/validate';
import { FAMILY_IDS, ZMATTER_BASE_PATHS } from '@utils/nodesUtils';
import { apiResponse } from '@utils/utils';
import type { PlmEntry } from '@Types/api';

const api = express.Router();

const decodeAddress = (raw: string | string[]) =>
  decodeURIComponent(Array.isArray(raw) ? raw[0] : raw);

const paramStr = (raw: string | string[]) =>
  Array.isArray(raw) ? raw[0] : raw;

const INSTEON_FAMILIES = new Set<string>([FAMILY_IDS.Insteon]);
const ZWAVE_LEGACY_FAMILIES = new Set<string>([FAMILY_IDS.ZWaveLegacy]);
const WRITE_UPDATES_FAMILIES = new Set<string>([FAMILY_IDS.Insteon, FAMILY_IDS.ZWaveLegacy, FAMILY_IDS.ZWave, FAMILY_IDS.Zigbee, FAMILY_IDS.Matter]);
const SYNCHRONIZE_FAMILIES = new Set<string>([FAMILY_IDS.ZWave, FAMILY_IDS.Zigbee, FAMILY_IDS.Matter]);

api.post('/family/:family/:instance/node/:address/restore', async (req, res) => {
  const family = paramStr(req.params.family);
  if (!INSTEON_FAMILIES.has(family)) throw BadRequest(`Restore is not supported for family ${family}`);
  const address = decodeAddress(req.params.address);
  const logTags = { ...res.locals.logTags, address };
  await webService({ service: 'RestoreDeviceFromNode', params: { id: address, flag: 0 }, logTags });
  res.json(apiResponse(null));
});

api.post('/family/:family/:instance/node/:address/query-engine', async (req, res) => {
  const family = paramStr(req.params.family);
  if (!INSTEON_FAMILIES.has(family)) throw BadRequest(`Query engine is not supported for family ${family}`);
  const address = decodeAddress(req.params.address);
  const logTags = { ...res.locals.logTags, address };
  await webService({ service: 'DeviceSpecific', params: { command: 'Q_IEO', node: address, option: '', flag: 0, CDATA: '' }, logTags });
  res.json(apiResponse(null));
});

api.post('/family/:family/:instance/node/:address/links/device', async (req, res) => {
  const family = paramStr(req.params.family);
  if (!INSTEON_FAMILIES.has(family)) throw BadRequest(`Links device is not supported for family ${family}`);
  const address = decodeAddress(req.params.address);
  const logTags = { ...res.locals.logTags, address };
  await webService({ service: 'DeviceSpecific', params: { command: 'G_DEV_ALL', node: address, option: '', flag: 1, CDATA: '0 -1' }, logTags });
  res.json(apiResponse(null));
});

api.post('/family/:family/:instance/node/:address/links/iox', async (req, res) => {
  const family = paramStr(req.params.family);
  if (!INSTEON_FAMILIES.has(family)) throw BadRequest(`Links IoX is not supported for family ${family}`);
  const address = decodeAddress(req.params.address);
  const logTags = { ...res.locals.logTags, address };
  await webService({ service: 'DeviceSpecific', params: { command: 'G_ISY_ALL', node: address, option: '', flag: 1, CDATA: '0 -1' }, logTags });
  res.json(apiResponse(null));
});

api.post('/family/:family/:instance/links/stop', async (req, res) => {
  const family = paramStr(req.params.family);
  if (!INSTEON_FAMILIES.has(family)) throw BadRequest(`Links stop is not supported for family ${family}`);
  const logTags = { ...res.locals.logTags };
  await webService({ service: 'DeviceSpecific', params: { command: 'STOP', node: '', option: '', flag: 0, CDATA: '' }, logTags });
  res.json(apiResponse(null));
});

const replaceSchema = z.object({
  newAddress:    z.string(),
  newDeviceType: z.string(),
});

api.post('/family/:family/:instance/node/:address/replace', validateBody(replaceSchema), async (req, res) => {
  const family = paramStr(req.params.family);
  if (!INSTEON_FAMILIES.has(family)) throw BadRequest(`Replace is not supported for family ${family}`);
  const address = decodeAddress(req.params.address);
  const { newAddress, newDeviceType } = req.body;
  const logTags = { ...res.locals.logTags, address };
  await webService({ service: 'ReplaceDevice', params: { id: address, name: newAddress, command: newDeviceType }, logTags });
  res.json(apiResponse(null));
});

api.get('/family/:family/:instance/node/:address/plm', async (req, res) => {
  const family = paramStr(req.params.family);
  if (!INSTEON_FAMILIES.has(family)) throw BadRequest(`PLM is not supported for family ${family}`);
  const address = decodeAddress(req.params.address);
  const logTags = { ...res.locals.logTags, address };
  const soapRes = await webService({ service: 'DeviceSpecific', params: { command: 'G_CTL_SP', node: address, option: '', flag: 1, CDATA: '0 -1' }, logTags });
  const parsed = await xml2js.parseStringPromise(soapRes.data, { trim: true, explicitArray: false, mergeAttrs: true });
  const sp = parsed?.['s:Envelope']?.['s:Body']?.SceneProfiles?.SP;
  const entries: PlmEntry[] = sp
    ? (Array.isArray(sp) ? sp : [sp]).map((e: { node: string; CLNRT: string }) => ({
        address: e.node.trim(),
        clnrt: parseInt(e.CLNRT, 10),
      }))
    : [];
  res.json(apiResponse(entries));
});

const plmWriteSchema = z.object({
  responderAddress: z.string(),
  clnrt: z.number().int(),
});

api.post('/family/:family/:instance/node/:address/plm', validateBody(plmWriteSchema), async (req, res) => {
  const family = paramStr(req.params.family);
  if (!INSTEON_FAMILIES.has(family)) throw BadRequest(`PLM is not supported for family ${family}`);
  const address = decodeAddress(req.params.address);
  const { clnrt } = req.body;
  const logTags = { ...res.locals.logTags, address };
  await webService({ service: 'DeviceSpecific', params: { command: 'S_CLNRT_SP', node: null, option: address, flag: 1, CDATA: String(clnrt) }, logTags });
  res.json(apiResponse(null));
});

api.post('/family/:family/:instance/node/:address/synchronize-node', async (req, res) => {
  const family = paramStr(req.params.family);
  if (!ZWAVE_LEGACY_FAMILIES.has(family)) throw BadRequest(`Synchronize node is not supported for family ${family}`);
  const address = decodeAddress(req.params.address);
  const logTags = { ...res.locals.logTags, address };
  await webService({ service: 'SyncFull', params: { id: address }, logTags });
  res.json(apiResponse(null));
});

api.post('/family/:family/:instance/node/:address/remove-failed', async (req, res) => {
  const family = paramStr(req.params.family);
  if (!ZWAVE_LEGACY_FAMILIES.has(family)) throw BadRequest(`Remove failed node is not supported for family ${family}`);
  const address = decodeAddress(req.params.address);
  const logTags = { ...res.locals.logTags, address };
  await webService({ service: 'RemoveFailedNode', params: { id: address }, logTags });
  res.json(apiResponse(null));
});

api.post('/family/:family/:instance/node/:address/replace-failed', async (req, res) => {
  const family = paramStr(req.params.family);
  if (!ZWAVE_LEGACY_FAMILIES.has(family)) throw BadRequest(`Replace failed node is not supported for family ${family}`);
  const address = decodeAddress(req.params.address);
  const logTags = { ...res.locals.logTags, address };
  await webService({ service: 'ReplaceFailedNode', params: { id: address }, logTags });
  res.json(apiResponse(null));
});

api.post('/family/:family/:instance/node/:address/update-neighbors', async (req, res) => {
  const family = paramStr(req.params.family);
  if (!ZWAVE_LEGACY_FAMILIES.has(family)) throw BadRequest(`Update neighbors is not supported for family ${family}`);
  const address = decodeAddress(req.params.address);
  const logTags = { ...res.locals.logTags, address };
  await webService({ service: 'UpdateNeighbors', params: { id: address }, logTags });
  res.json(apiResponse(null));
});

api.post('/family/:family/:instance/node/:address/repair-links', async (req, res) => {
  const family = paramStr(req.params.family);
  if (!ZWAVE_LEGACY_FAMILIES.has(family)) throw BadRequest(`Repair links is not supported for family ${family}`);
  const address = decodeAddress(req.params.address);
  const logTags = { ...res.locals.logTags, address };
  await webService({ service: 'RepairLinks', params: { id: address }, logTags });
  res.json(apiResponse(null));
});

api.post('/family/:family/:instance/node/:address/write-updates', async (req, res) => {
  const address = decodeAddress(req.params.address);
  const family = paramStr(req.params.family);
  const logTags = { ...res.locals.logTags, address };

  if (!WRITE_UPDATES_FAMILIES.has(family)) throw BadRequest(`Write updates not supported for family ${family}`);

  const zmatterBase = ZMATTER_BASE_PATHS[family];
  if (zmatterBase) {
    await isy.request({ method: 'get', url: `${zmatterBase}node/${address}/links/write` }, logTags);
  } else {
    await webService({ service: 'WriteDeviceUpdates', params: { id: address }, logTags });
  }

  res.json(apiResponse(null));
});

const synchronizeSchema = z.object({
  mode: z.enum(['updateWithInterview', 'replaceWithInterview', 'update', 'replace'])
});

api.post('/family/:family/:instance/node/:address/synchronize', validateBody(synchronizeSchema), async (req, res) => {
  const address = decodeAddress(req.params.address);
  const family = paramStr(req.params.family);
  const { mode } = req.body;
  const logTags = { ...res.locals.logTags, address };

  if (!SYNCHRONIZE_FAMILIES.has(family)) throw BadRequest(`Synchronize is not supported for family ${family}`);
  const zmatterBase = ZMATTER_BASE_PATHS[family]!;

  const urlSuffixMap: Record<string, string> = {
    updateWithInterview: `node/${address}/interview/sync?force=true`,
    replaceWithInterview: `node/${address}/interview/sync/full?force=true`,
    update: `node/${address}/sync`,
    replace: `node/${address}/sync/full`,
  };

  await isy.request({ method: 'post', url: `${zmatterBase}${urlSuffixMap[mode]}` }, logTags);
  res.json(apiResponse(null));
});

// ── Z-Wave Legacy family-level routes ────────────────────────────────────────

api.post('/family/:family/:instance/include', async (req, res) => {
  const family = paramStr(req.params.family);
  if (!ZWAVE_LEGACY_FAMILIES.has(family)) throw BadRequest(`Include is not supported for family ${family}`);
  await webService({ service: 'IncludeDevice', params: {}, logTags: res.locals.logTags });
  res.json(apiResponse(null));
});

api.post('/family/:family/:instance/exclude', async (req, res) => {
  const family = paramStr(req.params.family);
  if (!ZWAVE_LEGACY_FAMILIES.has(family)) throw BadRequest(`Exclude is not supported for family ${family}`);
  await webService({ service: 'ExcludeDevice', params: {}, logTags: res.locals.logTags });
  res.json(apiResponse(null));
});

api.post('/family/:family/:instance/stop', async (req, res) => {
  const family = paramStr(req.params.family);
  if (!ZWAVE_LEGACY_FAMILIES.has(family)) throw BadRequest(`Stop is not supported for family ${family}`);
  await webService({ service: 'StopIncludeExclude', params: {}, logTags: res.locals.logTags });
  res.json(apiResponse(null));
});

api.post('/family/:family/:instance/shift-primary', async (req, res) => {
  const family = paramStr(req.params.family);
  if (!ZWAVE_LEGACY_FAMILIES.has(family)) throw BadRequest(`Shift primary is not supported for family ${family}`);
  await webService({ service: 'ReplicateSendPrimary', params: {}, logTags: res.locals.logTags });
  res.json(apiResponse(null));
});

api.post('/family/:family/:instance/learn-mode', async (req, res) => {
  const family = paramStr(req.params.family);
  if (!ZWAVE_LEGACY_FAMILIES.has(family)) throw BadRequest(`Learn mode is not supported for family ${family}`);
  await webService({ service: 'StartLearnMode', params: {}, logTags: res.locals.logTags });
  res.json(apiResponse(null));
});

api.post('/family/:family/:instance/sync-new-deleted', async (req, res) => {
  const family = paramStr(req.params.family);
  if (!ZWAVE_LEGACY_FAMILIES.has(family)) throw BadRequest(`Sync new/deleted is not supported for family ${family}`);
  await webService({ service: 'Sync', params: {}, logTags: res.locals.logTags });
  res.json(apiResponse(null));
});

api.post('/family/:family/:instance/sync-all', async (req, res) => {
  const family = paramStr(req.params.family);
  if (!ZWAVE_LEGACY_FAMILIES.has(family)) throw BadRequest(`Sync all is not supported for family ${family}`);
  await webService({ service: 'SyncFull', params: {}, logTags: res.locals.logTags });
  res.json(apiResponse(null));
});

api.post('/family/:family/:instance/key-unprotect', async (req, res) => {
  const family = paramStr(req.params.family);
  if (!ZWAVE_LEGACY_FAMILIES.has(family)) throw BadRequest(`Key unprotect is not supported for family ${family}`);
  await webService({ service: 'UnprotectNetworkKey', params: {}, logTags: res.locals.logTags });
  res.json(apiResponse(null));
});

api.post('/family/:family/:instance/key-protect', async (req, res) => {
  const family = paramStr(req.params.family);
  if (!ZWAVE_LEGACY_FAMILIES.has(family)) throw BadRequest(`Key protect is not supported for family ${family}`);
  await webService({ service: 'ProtectNetworkKey', params: {}, logTags: res.locals.logTags });
  res.json(apiResponse(null));
});

api.post('/family/:family/:instance/nwi-on', async (req, res) => {
  const family = paramStr(req.params.family);
  if (!ZWAVE_LEGACY_FAMILIES.has(family)) throw BadRequest(`NWI on is not supported for family ${family}`);
  await webService({ service: 'SetNWIOn', params: {}, logTags: res.locals.logTags });
  res.json(apiResponse(null));
});

api.post('/family/:family/:instance/nwi-off', async (req, res) => {
  const family = paramStr(req.params.family);
  if (!ZWAVE_LEGACY_FAMILIES.has(family)) throw BadRequest(`NWI off is not supported for family ${family}`);
  await webService({ service: 'SetNWIOff', params: {}, logTags: res.locals.logTags });
  res.json(apiResponse(null));
});

api.post('/family/:family/:instance/sleep-on', async (req, res) => {
  const family = paramStr(req.params.family);
  if (!ZWAVE_LEGACY_FAMILIES.has(family)) throw BadRequest(`Sleep on is not supported for family ${family}`);
  await webService({ service: 'SetAutoSleepOn', params: {}, logTags: res.locals.logTags });
  res.json(apiResponse(null));
});

api.post('/family/:family/:instance/sleep-off', async (req, res) => {
  const family = paramStr(req.params.family);
  if (!ZWAVE_LEGACY_FAMILIES.has(family)) throw BadRequest(`Sleep off is not supported for family ${family}`);
  await webService({ service: 'SetAutoSleepOff', params: {}, logTags: res.locals.logTags });
  res.json(apiResponse(null));
});

api.post('/family/:family/:instance/freeze-on', async (req, res) => {
  const family = paramStr(req.params.family);
  if (!ZWAVE_LEGACY_FAMILIES.has(family)) throw BadRequest(`Freeze on is not supported for family ${family}`);
  await webService({ service: 'SetNodeFreezeOn', params: {}, logTags: res.locals.logTags });
  res.json(apiResponse(null));
});

api.post('/family/:family/:instance/freeze-off', async (req, res) => {
  const family = paramStr(req.params.family);
  if (!ZWAVE_LEGACY_FAMILIES.has(family)) throw BadRequest(`Freeze off is not supported for family ${family}`);
  await webService({ service: 'SetNodeFreezeOff', params: {}, logTags: res.locals.logTags });
  res.json(apiResponse(null));
});

api.post('/family/:family/:instance/factory-reset', async (req, res) => {
  const family = paramStr(req.params.family);
  if (!ZWAVE_LEGACY_FAMILIES.has(family)) throw BadRequest(`Factory reset is not supported for family ${family}`);
  await webService({ service: 'FactoryResetDongle', params: { force: 'true' }, logTags: res.locals.logTags });
  res.json(apiResponse(null));
});

// ── Insteon family-level routes ────────────────────────────────────────────────

api.post('/family/:family/:instance/restore-modem', async (req, res) => {
  const family = paramStr(req.params.family);
  if (!INSTEON_FAMILIES.has(family)) throw BadRequest(`Restore modem is not supported for family ${family}`);
  await webService({ service: 'ReplaceModem', params: {}, logTags: res.locals.logTags });
  res.json(apiResponse(null));
});

api.post('/family/:family/:instance/delete-modem', async (req, res) => {
  const family = paramStr(req.params.family);
  if (!INSTEON_FAMILIES.has(family)) throw BadRequest(`Delete modem is not supported for family ${family}`);
  await webService({ service: 'RemoveModem', params: {}, logTags: res.locals.logTags });
  res.json(apiResponse(null));
});

api.post('/family/:family/:instance/plm-info', async (req, res) => {
  const family = paramStr(req.params.family);
  if (!INSTEON_FAMILIES.has(family)) throw BadRequest(`PLM info is not supported for family ${family}`);
  const soapRes = await webService({ service: 'DeviceSpecific', params: { command: 'G_PLM_INFO', node: '', option: '', flag: 1, CDATA: '' }, logTags: res.locals.logTags });
  // G_PLM_INFO returns a SOAP envelope where the <info> tag contains plain text, not nested XML.
  // xml2js fails on it, so extract the info string directly with a regex.
  const info: string = (soapRes.data as string).match(/<info>([\S\s]*?)<\/info>/)?.[1]?.trim() ?? soapRes.data as string;
  res.json(apiResponse(info));
});

api.post('/family/:family/:instance/plm-links', async (req, res) => {
  const family = paramStr(req.params.family);
  if (!INSTEON_FAMILIES.has(family)) throw BadRequest(`PLM links is not supported for family ${family}`);
  await webService({ service: 'DeviceSpecific', params: { command: 'G_PLM_ALL', node: '', option: '', flag: 1, CDATA: '0 -1' }, logTags: res.locals.logTags });
  res.json(apiResponse(null));
});

const startLinkingSchema = z.object({ deviceType: z.string().optional() });

api.post('/family/:family/:instance/start-linking', async (req, res) => {
  const family = paramStr(req.params.family);
  if (!INSTEON_FAMILIES.has(family)) throw BadRequest(`Start linking is not supported for family ${family}`);
  const { deviceType } = startLinkingSchema.parse(req.body ?? {});
  await webService({ service: 'DiscoverNodes', params: deviceType ? { ntype: deviceType } : {}, logTags: res.locals.logTags });
  res.json(apiResponse(null));
});

const setLinkingModeSchema = z.object({ mode: z.string() });

api.post('/family/:family/:instance/set-linking-mode', validateBody(setLinkingModeSchema), async (req, res) => {
  const family = paramStr(req.params.family);
  if (!INSTEON_FAMILIES.has(family)) throw BadRequest(`Set linking mode is not supported for family ${family}`);
  const { mode } = req.body;
  await webService({ service: 'SetDeviceLinkingMode', params: { mode }, logTags: res.locals.logTags });
  res.json(apiResponse(null));
});

api.get('/family/:family/:instance/insteon-messaging', async (req, res) => {
  const family = paramStr(req.params.family);
  if (!INSTEON_FAMILIES.has(family)) throw BadRequest(`Insteon messaging is not supported for family ${family}`);
  const soapRes = await webService({ service: 'DeviceSpecific', params: { command: 'G_IEO', node: '', option: '', flag: 0, CDATA: '' }, logTags: res.locals.logTags });
  const parsed = await xml2js.parseStringPromise(soapRes.data, { trim: true, explicitArray: false, mergeAttrs: true });
  const value: number = parseInt(parsed?.['s:Envelope']?.['s:Body']?.DeviceSpecificResponse?.flag ?? '2', 10);
  res.json(apiResponse(value));
});

const insteonMessagingSchema = z.object({ value: z.number().int() });

api.post('/family/:family/:instance/insteon-messaging', validateBody(insteonMessagingSchema), async (req, res) => {
  const family = paramStr(req.params.family);
  if (!INSTEON_FAMILIES.has(family)) throw BadRequest(`Insteon messaging is not supported for family ${family}`);
  const { value } = req.body;
  await webService({ service: 'DeviceSpecific', params: { command: 'S_IEO', node: '', option: '', flag: value, CDATA: '' }, logTags: res.locals.logTags });
  res.json(apiResponse(null));
});

const stopLinkingSchema = z.object({ flag: z.union([z.literal(1), z.literal(3), z.literal(4)]) });

api.post('/family/:family/:instance/stop-linking', validateBody(stopLinkingSchema), async (req, res) => {
  const family = paramStr(req.params.family);
  if (!INSTEON_FAMILIES.has(family)) throw BadRequest(`Stop linking is not supported for family ${family}`);
  const { flag } = req.body;
  await webService({ service: 'CancelNodesDiscovery', params: { flag }, logTags: res.locals.logTags });
  res.json(apiResponse(null));
});

const addNodeSchema = z.object({
  address:    z.string().optional(),
  name:       z.string().optional(),
  deviceType: z.string().optional(),
  flag:       z.union([z.literal(1), z.literal(3), z.literal(4)]),
});

api.post('/family/:family/:instance/add-node', validateBody(addNodeSchema), async (req, res) => {
  const family = paramStr(req.params.family);
  if (!INSTEON_FAMILIES.has(family)) throw BadRequest(`Add node is not supported for family ${family}`);
  const { address, name, deviceType, flag } = req.body;
  const params: Record<string, string | number | null> = { flag };
  if (address)    params['id']    = address;
  if (name)       params['name']  = name;
  if (deviceType) params['ntype'] = deviceType;
  await webService({ service: 'AddNode', params, logTags: res.locals.logTags });
  res.json(apiResponse(null));
});

// POST /family/:family/:instance/scene-test/raw-off
// Sends a raw PLM group-off command (DeviceSpecific DB op=4) to turn off all members of an
// Insteon scene at the PLM level. physicalGroupNum is the <deviceGroup> value from /rest/nodes.
const sceneTestRawOffSchema = z.object({ physicalGroupNum: z.number().int().min(1).max(254) });

api.post('/family/:family/:instance/scene-test/raw-off', validateBody(sceneTestRawOffSchema), async (req, res) => {
  const family = paramStr(req.params.family);
  if (!INSTEON_FAMILIES.has(family)) throw BadRequest(`Scene test raw-off is not supported for family ${family}`);
  const { physicalGroupNum } = req.body;
  // op=4 = SEND_INSTEON_RAW — writes bytes directly to the PLM serial port.
  // Byte breakdown:
  //   0x02 (2)               — STX, PLM serial start-of-text marker
  //   0x61 (97)              — PLM command: SEND_ALL_LINK_COMMAND (broadcast to an ALL-Link group)
  //   {physicalGroupNum}     — ALL-Link group number (1–254): which scene to address
  //   0x13 (19)              — ALL_LINK_CMD1: Insteon command byte 1, 0x13 = All-Link Off
  //   0x00 (0)               — ALL_LINK_CMD2: command byte 2, zero (no parameter for plain off)
  const cdata = `<IDB><op>4</op><data><byte>2</byte><byte>97</byte><byte>${physicalGroupNum}</byte><byte>19</byte><byte>0</byte></data></IDB>`;
  await webService({ service: 'DeviceSpecific', params: { command: 'DB', node: '', option: ' ', flag: '48', CDATA: cdata }, logTags: res.locals.logTags });
  res.json(apiResponse(null));
});

export default api;
