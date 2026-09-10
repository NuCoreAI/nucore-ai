# Node Property History Design

## Summary

This document captures the behavior described in the email thread in `NodeHistoryEmail.pdf` and translates it into a concrete engineering design for exposing node-property history in the NuCore IoX integration.

The key idea is:

- node properties can be recorded over time
- recording can be enabled or disabled through a REST endpoint
- recording state persists across IoX restarts
- historical values can be queried through a dedicated history API
- the API accepts multiple node IDs and property IDs in a single request
- time-window filtering is supported with optional `start`/`end` bounds and `oneBefore`/`oneAfter` behavior

## Source behavior

The email states the following in the recent 5.8.0 build:

- recording can be toggled via:
  - `/rest/history/node/properties/recording/on`
  - `/rest/history/node/properties/recording/off`
- the setting persists across IoX restarts
- in the current build the default is Off
- future builds are expected to default to On after database sizing is better understood
- history can be queried using:
  - `/rest/history/node/properties/get`

This is a historical-value API, not just a live node-property API.

### Controller status check

Node property history status is also exposed through the controller system status API at `/api/sys`.

The output includes a boolean flag:

```json
{"mailTo":null,"mSys":1,"htmlRole":3,"compactEmail":false,"queryOnInit":true,"pCatchUp":false,"pGracePeriod":600,"waitBusyReading":true,"nodePropertyHistory":true,"insteonSupport":false,"zwaveSupport":true,"zwaveSetting":"auto","zMatterZwave":true,"zigbeeSupport":true,"zigbeeSetting":"auto","matterSupport":true,"matterSetting":"on","lutronEnabled":false,"profileDatabaseEnabled":true,"tpmStatus":0,"biosStatus":1,"ntpHost":"pool.ntp.org","ntpActive":true,"ntpEnabled":true,"ntpInterval":86400}
```

The relevant field is:

- `nodePropertyHistory`: boolean

A value of `true` means node property history is currently enabled.

## Recording toggle endpoints

Use the following endpoints to enable or disable recording:

- Enable: `GET /rest/history/node/properties/recording/on`
- Disable: `GET /rest/history/node/properties/recording/off`

These are the canonical controller actions for toggling node-property history recording.

## Design goals

1. Provide a queryable history of node-property values over time.
2. Allow recording to be toggled without requiring a full IoX restart.
3. Support multi-node and multi-property fetches in a single request.
4. Let callers narrow results using time windows.
5. Keep the API compatible with the existing IoX REST style.

## Core API surfaces

### 1. Toggle recording state

#### Enable

- Method: `GET`
- URL: `/rest/history/node/properties/recording/on`

#### Disable

- Method: `GET`
- URL: `/rest/history/node/properties/recording/off`

A client should check `/api/sys` first when it needs to know whether the feature is enabled, then call the enable/disable endpoint if a state change is required.

#### Semantics

- on/off is a global historical-recording switch for node properties
- it persists across IoX restarts
- it probably affects all property history recording for the controller, unless a narrower scope is later introduced by the API

#### Notes

- The email indicates the default is Off in the released build.
- The project should treat this as a configuration toggle that is safe to expose through a wrapper method, but must not silently assume it is enabled.

### 2. Query property history

#### Endpoint

- Method: `GET`
- URL: `/rest/history/node/properties/get`

#### Query parameters

The email states that all parameters are optional, and that multiple nodes and/or properties may be specified by comma-separated lists.

Required conceptual parameters:

- `property` — property ID(s) to retrieve
- `node` — node ID(s) to query
- `start` — lower bound for the time window
- `end` — upper bound for the time window
- `oneBefore` — include one record immediately before the time range
- `oneAfter` — include one record immediately after the time range

#### Parameter rules

##### `property`

- Type: string or comma-separated list of strings
- Example: `CLIHUM,ST`
- Meaning: select property IDs to include in the result
- If omitted: all recorded properties for the selected node(s) may be returned, depending on controller behavior

##### `node`

- Type: string or comma-separated list of strings
- Example: `1D 6F 15 1` (URL-encoded as `1D%206F%2015%201` in the sample)
- Meaning: select one or more nodes whose property history is requested
- If omitted: controller-defined default behavior applies; the email does not state a guaranteed global behavior

##### `start`

- Type: ISO-8601 timestamp with timezone offset
- Example: `2023-12-16T21:11:50.939221-08:00`
- Meaning: lower bound on time window
- If omitted: no lower bound

##### `end`

- Type: ISO-8601 timestamp with timezone offset
- Example: `2023-12-16T21:11:50.939221-08:00`
- Meaning: upper bound on time window
- If omitted: no upper bound

##### `oneBefore`

- Type: boolean-like query flag
- Example: `oneBefore=true`
- Meaning: include the sample immediately before the earliest requested time boundary
- This is useful when clients are doing interval boundary analysis or want a contiguous view across an event boundary

##### `oneAfter`

- Type: boolean-like query flag
- Example: `oneAfter=true`
- Meaning: include the sample immediately after the latest requested time boundary

#### Example request

```text
/rest/history/node/properties/get?
property=CLIHUM,ST&
node=1D%206F%2015%201&
start=2023-12-16T21:11:50.939221-08:00&
oneBefore=true&
end=2023-12-16T21:11:50.939221-08:00&
oneAfter=true
```

## Interpretation of the API contract

The design suggests a history API with the following behavior profile:

- query is time-bounded
- query can span multiple nodes
- query can span multiple properties
- query can include neighbors around the requested window for continuity
- query result is probably a structured history set keyed by node and property ID, with timestamps and values

The exact payload keys are not shown in the email, but the design should assume something like:

```json
{
  "node": "1D 6F 15 1",
  "property": "ST",
  "history": [
    {
      "timestamp": "2023-12-16T21:11:50.939221-08:00",
      "value": "...",
      "formatted": "...",
      "uom": "...",
      "prec": 1
    }
  ]
}
```

This is consistent with the project’s existing model in `src/nucore/nodedef.py`, which already treats a property as a record containing:

- `id`
- `value`
- `formatted`
- `uom`
- `uom_name`
- `prec`
- `name`

The history API likely extends that same conceptual model by adding a timestamp dimension to each property sample.

## Technical constraints and assumptions

### Recording state

- recording is controllable at runtime
- it is persistent across IoX restarts
- default is Off in the current build described in the email

### Limits

The PDF adds a concrete operational limit for the endpoint:

- Example request: `/rest/history/node/properties/get?node=n001_oadr3ven&property=ST&start=2026-05-10T00:00:00-06:00&limit=1000`
- The API returns at most `limit` results in a single response.
- If the limit is hit before the desired end date, another request must be made using the timestamp from the last event in the current dataset.
- This means the client should page through history by time, not make a single unbounded request.
- Any user query that does not include a limit is expected to cause issues or an oversized response.

This is the most important operational rule in the design: the API is intentionally capped, and the caller must paginate using the last returned event timestamp.

This is not just a performance hint; it is a real constraint. If a client omits `limit`, it is effectively asking for an unbounded historical export and is likely to hit controller limits or return an unusable result.

The behavior is therefore:

1. request a bounded window with a sane `limit`, such as 1000;
2. inspect the last event timestamp returned;
3. if more data is needed, make a new request with `start` set to that last timestamp (or the next value after it, depending on the exact desired semantics);
4. continue until the target time window is exhausted.

The email does not explicitly describe:

- max history retention window
- maximum number of nodes/properties per query
- database size thresholds or pruning policy
- rate limits
- serialization format for large responses

These should still be treated as open design questions unless future docs clarify them.

### Limits implied by the email

Although not explicit, the email and the added limit note strongly suggest the following operational constraints:

- recording is not free; storage cost matters
- the eventual default may switch to On only when the project is comfortable with database growth
- the API is designed for bounded historical queries, not broad unbounded exports
- callers must explicitly use `limit` and paginate, or they will run into trouble
- using any of the other params in place of a proper `limit` strategy is likely to create issues

### URL conventions

The API uses the IoX REST style under `/rest/...` rather than `/api/...` for this historical data feature:

- `/rest/history/node/properties/recording/on`
- `/rest/history/node/properties/recording/off`
- `/rest/history/node/properties/get`

This pattern matches the existing IoX-style resource hierarchy and suggests a clean design where history is treated as a dedicated subresource of node properties.

## Recommended NuCore integration model

The project should wrap this in a thin async method analogous to the pattern already used for live property reads and rest node access in `src/iox/iox_wrapper.py`.

Recommended method surface:

```python
async def set_node_property_history_recording(enabled: bool) -> dict | None:
    ...

async def get_node_property_history(
    *,
    nodes: list[str] | None = None,
    properties: list[str] | None = None,
    start: str | None = None,
    end: str | None = None,
    one_before: bool = False,
    one_after: bool = False,
) -> dict | None:
    ...
```

### Input normalization rules

- convert node IDs and property IDs to the exact controller format expected by the endpoint
- join lists with commas
- URL-encode reserved characters where needed
- accept ISO-8601 timestamps with offset
- treat `one_before`/`one_after` as strict boolean flags

### Result parsing

- parse the controller response into a normalized dictionary keyed by node/property
- preserve timestamps as strings or datetime objects in the internal model layer
- retain raw controller values alongside formatted values and units for later UI or diagnostics use

## Security and operational considerations

- Treat this as a potentially expensive backend operation; do not query broad histories by default
- avoid large ad hoc history exports in the agent layer unless explicitly requested
- support filtered queries first; broad “all nodes/all properties” queries should be treated as special cases
- when default recording is Off, the caller should check this before assuming historical data exists

## Open questions

1. What is the exact response schema for `/rest/history/node/properties/get`?
2. Is `property` required to be a property ID or can it accept display names?
3. Does the controller support a bulk “all properties” query when `property` is omitted?
4. Is `node` required to be a raw address or can it take aliases/names?
5. Is there a retention limit or database-pruning policy?
6. What is the maximum query size before the API truncates or fails?
7. Is the recording toggle global, per-node, or per-property class?

## Conclusion

The historical node-property API is a first-class IoX capability, not simply a convenience feature. It is designed around:

- runtime recording toggles
- time-windowed queries
- multi-node/multi-property selection
- optional boundary records
- controller-managed storage and retention

The important engineering takeaway is that node properties already exist as a live-state model in the NuCore codebase, and this history API extends that model by adding a time dimension and a configuration layer for recording on/off behavior.
