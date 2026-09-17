
# INSTEON DIAGNOSTICS

## How INSTEON links work (use this to reason about anything not covered below)
The PLM is the conduit between the UI/software and INSTEON devices. Every working device relationship is a pair of link records, one on each side, and they serve two different purposes:
- PLM -> device, PLM as `controller`, device as `responder`: the PLM sends this device commands (on/off/dim/etc) AND can query it directly (a Status Request), with the device answering synchronously over this same link. This is the on-demand, request/response path -- both control and on-demand status reads depend on it.
- device -> PLM, device as `controller`, PLM as `responder`: this exists for devices that can report a *local, unsolicited* change of state on their own initiative (a physical switch pressed, a sensor tripping) -- the device broadcasts that change without being asked, and the PLM, as responder on this link, picks it up. This is the asynchronous/push path, and only matters for devices capable of originating that broadcast.

Don't conflate the two: "can't query/read status on demand" and "can't control" both point at the PLM->X link (same link carries both). "Doesn't automatically report when it changes locally" points at the X->PLM link -- that's the one that's missing/broken when a customer says a device's automatic/unsolicited status updates aren't showing up, not on-demand reads. Never describe this as the device "controlling" the PLM -- `controller`/`responder` here just mean "which side of this link can initiate traffic on it," not an instruction-following relationship.

## Known fixes, in order of likelihood
- PLM enabled but not connected: confirm it's on a USB serial port and the udx service is running. If udx is running and it's still not connected, the PLM hardware has failed -- customer needs a new one, and must restore it after.
- PLM connected but links missing/broken: ask whether this is a new, never-restored PLM before concluding it "lost" its links -- same fix (restore) either way, but frame it correctly for the customer.
- Intermittent (not total) failures, especially across multiple otherwise-healthy devices: signal noise is the most common cause. Have the customer move the PLM to an outlet not shared with other transformers/power supplies before assuming hardware failure -- this resolves the majority of cases.
- Only if none of the above helps: recommend a new PLM + restore.

# Diagnosing no status feedback
0. If device is not none and it's a group or folder return error("Cannot run diagnostics on a group or folder, only individual devices.")
1. PLM sanity gate: _plm_sanity_gate() 
   - Checks whether the PLM is connected and its links are intact.
   - If the PLM is new or never restored, it may not have any links yet.
   - Returns early if the PLM is not in a healthy state, preventing further per-device checks.
2. get all the PLM links regardless of their state: _get_all_plm_links()
   - Retrieves the complete set of links stored on the PLM.
   - Used to cross-check against the expected links and identify any missing or broken links.
   - Helps in diagnosing issues where the PLM is connected but some links are missing or incorrect.
    * if no links, RETURN --> it indicates that the PLM may have lost its links, it's defective, or has never been restored, and further investigation or restoration is needed. 
3. PLM has links 
    3.a. if device is not none:
        a. make sure the PLM has `responder` links for all the device groups (the plm is responder and device is the controller) - if not error  -> recommendation -> restore PLM and if failed restore device, if failed remove device link it back in
        b. make sure iox/nucore link table has `responder` links to the PLM (the PLM is the controller the device is the responder) - if not error, recommendation -> restore device, if failed remove device link it back in
        c. compare_device_links(device) - if not matching, error, recommendation -> either restore device if not fixed, remove and link the device again    
    3.b. if device is none:
        take a two sample devices and do 3.a for each of them



    