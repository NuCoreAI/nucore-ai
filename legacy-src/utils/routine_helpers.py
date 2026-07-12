# ------------------------------------------------------------------
# Routine helper functions for fetching and enriching routine summaries based on candidate lists. 
# ------------------------------------------------------------------
from utils import get_logger
from typing import Any

logger = get_logger(__name__)

async def _get_routine_summary_from_candidates(intent_handler, candidates) -> list[dict[str, Any]]:
    """Fetch routine summaries for candidates that meet the score threshold.

    Iterates the candidate list in ``tool.args``, discards entries below
    the configured threshold, then calls
    :meth:`~nucore.NuCoreInterface.get_routine_summary` for each passing
    candidate and flattens the results into a single list.

    Args:
        candidates: List of dicts with ``routine_id`` and ``score`` keys.

    Returns:
        Flat list of routine summary dicts for all candidates that passed
        the threshold.  Empty when no candidates qualify.
    """
    score_threshold = intent_handler.config.get("threshold", 0.80)
    if not candidates:
        logger.debug("No candidates provided to _get_routine_summary_from_candidates.")
        return []


    out: list[dict[str, Any]] = []
    for r in candidates:
        if float(r.get('score', 0)) >= score_threshold:
            try:
                routine = await intent_handler.nucore_interface.get_routine_summary(r['routine_id'])
                if not routine:
                    logger.debug("Received None routine summary from Nucore interface.")
                    continue
                if isinstance(routine, list):
                    routine= routine[0] if routine else None
                    if not routine:
                        logger.debug("Received empty list routine summary from Nucore interface.")
                        continue
                # Enrich each summary dict with the full routine logic.
                if 'id' not in routine:
                    logger.debug(f"Routine summary missing 'id' field: {routine}")
                    continue
                else:
                    routine_id = _convert_routine_id_to_int(routine['id'])
                    if routine_id is None:
                        logger.debug(f"Failed to convert routine ID {routine['id']} to int, skipping enrichment with full routine logic.")
                        continue
                    routine['id'] = routine_id
                    full_routine = intent_handler.nucore_interface.all_routines.get(routine_id)
                    if full_routine is None:
                        logger.debug(f"No full routine found for routine ID: {routine_id}")
                    else:
                        # Attach the complete routine trigger/action logic for downstream use.
                        routine['routine_logic'] = _replace_device_id_with_name(intent_handler, full_routine)
                out.append(routine)
            except Exception:
                pass
    return out


async def _get_full_routines_from_candidates(intent_handler, candidates) -> list[dict[str, Any]]:
    """Fetch Full routines (with logic) for candidates that meet the score threshold. 

    Iterates the candidate list in ``tool.args``, discards entries below
    the configured threshold, then calls
    :meth:`~nucore.NuCoreInterface.get_routine_summary` for each passing
    candidate and flattens the results into a single list.

    Args:
        candidates: List of dicts with ``routine_id`` and ``score`` keys.

    Returns:
        Flat list of routine summary dicts for all candidates that passed
        the threshold.  Empty when no candidates qualify.
    """
    score_threshold = intent_handler.config.get("threshold", 0.80)

    out: list[dict[str, Any]] = []
    for r in candidates:
        if float(r.get('score', 0)) >= score_threshold:
            try:
                if not r.get('routine_id', None):
                    logger.debug(f"Candidate routine entry missing 'routine_id': {r}")
                    continue
                #routine_id = _convert_routine_id_to_int(r['routine_id'])
                routine_id = int(r['routine_id'])
                full_routine = intent_handler.nucore_interface.all_routines.get(routine_id)
                if full_routine is None:
                    logger.debug(f"No full routine found for routine ID: {routine_id}")
                else:
                    out.append(full_routine)
            except Exception:
                pass
    return out

def _replace_device_id_with_name(intent_handler, full_routine: dict[str, Any]) -> dict[str, Any]:
    """
    Scans the ``if``, ``then``, and ``else`` sections of the routine for
    ``"device"`` fields, resolves each raw address to its display name via
    :meth:`get_device_name`, and returns the deduplicated list.

    Args:
        full_routine: Full routine dict with optional ``if``/``then``/``else``
                        section lists.

    Returns:
        List of device display name strings (may be empty).
    """
    if full_routine is None:
        return []

    #first check the if section:        
    if_section: list[dict] = full_routine.get("if", [])
    then_section: list[dict] = full_routine.get("then", [])
    else_section: list[dict] = full_routine.get("else", [])
    for condition in if_section:
        if "device" in condition:
            device = condition.get("device", None)
            if device:                    
                device_name = intent_handler.nucore_interface.get_device_name(device)
                condition["device"] = device_name if device_name else device
    
    for action in then_section:
        if "device" in action:
            device = action.get("device", None)
            if device:
                device_name = intent_handler.nucore_interface.get_device_name(device)
                action["device"] = device_name if device_name else device

    for action in else_section:
        if "device" in action:
            device = action.get("device", None)
            if device:
                device_name = intent_handler.nucore_interface.get_device_name(device)
                action["device"] = device_name if device_name else device

    return full_routine 

def _convert_routine_id_to_int(routine_id: Any) -> int | None:
    """Convert a routine ID to a Python ``int``, accepting hex strings.

    The NuCore backend may return routine IDs as either plain integers or
    hexadecimal strings (e.g. ``"0x1a2b"``).  Both forms are normalised to
    ``int`` so they can be used as keys in ``all_routines``.

    Args:
        routine_id: The raw routine ID value from the LLM tool call.

    Returns:
        Integer routine ID, or ``None`` when conversion fails.
    """
    if isinstance(routine_id, int):
        return routine_id
    if isinstance(routine_id, str):
        try:
            # base-16 parsing handles both "0x…" prefixed and bare hex strings.
            return int(routine_id, 16)
        except ValueError:
            logger.debug(f"Failed to convert routine ID {routine_id} to int using both decimal and hex parsing.")
            return None

    logger.debug(f"Routine ID {routine_id} is neither int nor str, cannot convert to int.")
    return None

def _get_candidate_devices_from_routines(candidate_routines: list[dict[str, Any]]) -> list[dict[str, Any]]: 
    """
    Extracts device IDs from the ``if``, ``then``, and ``else`` sections of each routine in the candidate list, resolves them to display names via :meth:`get_device_name`, and returns a deduplicated list of device names.

    Args:
        candidate_routines: List of routine summary dicts with optional ``if``/``then``/``else`` section lists.
    Returns:
        List of device ids with score of 1.0
    """ 
    candidate_devices = []
    for routine in candidate_routines:
        if routine is None:
            return {}
        
        #first check the if section:        
        if_section: list[dict] = routine.get("if", [])
        then_section: list[dict] = routine.get("then", [])
        else_section: list[dict] = routine.get("else", [])
        device_id_list = set()
        for condition in if_section:
            if "device" in condition:
                device = condition.get("device", None)
                if device:                    
                    device_id_list.add(device)

        for action in then_section:
            if "device" in action:
                device = action.get("device", None)
                if device:
                    device_id_list.add(device)

        for action in else_section:
            if "device" in action:
                device = action.get("device", None)
                if device:
                    device_id_list.add(device)

        for device_id in device_id_list:
            try:
                candidate_devices.append(
                    {
                        "device_id": device_id,
                        "score": 1.0
                    }
                )
            except Exception as ex:
                pass

        return candidate_devices