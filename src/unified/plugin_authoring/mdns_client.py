"""A small, stdlib-only mDNS (RFC 6762) browser for the plugin-authoring
``mdns_search`` tool -- the same approach the generated ``hp_printer`` plugin
uses, generalised to any service type and to "browse everything".

It sends its questions from an *ephemeral* UDP port and never binds 5353 (the
eisy's own mDNS responder already owns that port), asking for a unicast
response so answers come straight back to that port. Each query goes out
twice, once plain and once with the unicast-response bit set, on IPv4 and on
every non-loopback IPv6 interface. Wi-Fi multicast is lossy and sleeping
devices answer late, so the window is split into rounds; each later round
re-asks for whatever record (SRV, TXT, A/AAAA) is still missing.

Blocking (sockets + ``select``) -- callers run :func:`browse` in a thread.
IPv4 multicast goes out on the OS default interface only.
"""

from __future__ import annotations

import select
import socket
import struct
import time
from typing import Any, Callable

MDNS_PORT = 5353
MDNS_V4_GROUP = "224.0.0.251"
MDNS_V6_GROUP = "ff02::fb"
META_QUERY = "_services._dns-sd._udp.local"

T_A, T_PTR, T_TXT, T_AAAA, T_SRV = 1, 12, 16, 28, 33

ROUND_SECONDS = 2.5
MAX_DISCOVERED_TYPES = 20
MAX_RESULTS = 200
_QUESTIONS_PER_PACKET = 15


def _labels(name: Any) -> list[str]:
    if isinstance(name, (list, tuple)):
        return list(name)
    return [p for p in name.rstrip(".").split(".") if p]


def build_query(questions: list[tuple[Any, int]], qu: bool = False) -> bytes:
    """questions: [(name_or_label_list, qtype)]. qu=True sets the unicast-response bit."""
    out = struct.pack(">HHHHHH", 0, 0, len(questions), 0, 0, 0)
    for name, qtype in questions:
        for label in _labels(name):
            raw = label.encode("utf-8")[:63]
            out += bytes([len(raw)]) + raw
        out += b"\x00" + struct.pack(">HH", qtype, 0x8001 if qu else 0x0001)
    return out


def _read_name(data: bytes, off: int) -> tuple[str, int]:
    labels = []
    end = None
    hops = 0
    while True:
        if off >= len(data) or hops > 64:
            raise ValueError("bad DNS name")
        ln = data[off]
        if ln == 0:
            if end is None:
                end = off + 1
            break
        if ln & 0xC0 == 0xC0:
            if off + 1 >= len(data):
                raise ValueError("bad DNS pointer")
            if end is None:
                end = off + 2
            off = ((ln & 0x3F) << 8) | data[off + 1]
            hops += 1
            continue
        labels.append(data[off + 1:off + 1 + ln].decode("utf-8", "replace"))
        off += 1 + ln
    return ".".join(labels), end


def _parse_txt(rdata: bytes) -> dict[str, str]:
    out: dict[str, str] = {}
    i = 0
    while i < len(rdata):
        ln = rdata[i]
        chunk = rdata[i + 1:i + 1 + ln].decode("utf-8", "replace")
        i += 1 + ln
        if not chunk:
            continue
        key, _sep, value = chunk.partition("=")
        if key and key not in out:
            out[key] = value
    return out


def parse_message(data: bytes) -> list[tuple[str, int, Any]]:
    """Return [(name, rtype, value)] for the A/AAAA/PTR/SRV/TXT records of an
    mDNS response. SRV value is (port, target); TXT value is a dict."""
    if len(data) < 12:
        return []
    _id, flags, qd, an, ns, ar = struct.unpack(">HHHHHH", data[:12])
    if not flags & 0x8000:  # not a response
        return []
    off = 12
    out: list[tuple[str, int, Any]] = []
    try:
        for _ in range(qd):
            _n, off = _read_name(data, off)
            off += 4
        for _ in range(an + ns + ar):
            name, off = _read_name(data, off)
            rtype, _cls, _ttl, rdlen = struct.unpack(">HHIH", data[off:off + 10])
            off += 10
            roff = off
            off += rdlen
            if rtype == T_A and rdlen == 4:
                val: Any = socket.inet_ntop(socket.AF_INET, data[roff:roff + 4])
            elif rtype == T_AAAA and rdlen == 16:
                val = socket.inet_ntop(socket.AF_INET6, data[roff:roff + 16])
            elif rtype == T_PTR:
                val, _e = _read_name(data, roff)
            elif rtype == T_SRV and rdlen >= 7:
                port = struct.unpack(">H", data[roff + 4:roff + 6])[0]
                target, _e = _read_name(data, roff + 6)
                val = (port, target)
            elif rtype == T_TXT:
                val = _parse_txt(data[roff:roff + rdlen])
            else:
                continue
            out.append((name, rtype, val))
    except (ValueError, struct.error, IndexError):
        pass
    return out


def _open_sockets() -> dict[str, socket.socket]:
    socks: dict[str, socket.socket] = {}
    try:
        s4 = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s4.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 255)
        s4.bind(("", 0))
        socks["v4"] = s4
    except OSError:
        pass
    try:
        s6 = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM)
        s6.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_MULTICAST_HOPS, 255)
        s6.bind(("::", 0))
        socks["v6"] = s6
    except OSError:
        pass
    return socks


def _v6_interfaces() -> list[int]:
    try:
        return [idx for idx, name in socket.if_nameindex() if not name.startswith("lo")]
    except (OSError, AttributeError):
        return []


def _mdns_round(
    socks: dict[str, socket.socket], v6_ifaces: list[int], questions: list[tuple[Any, int]], duration: float
) -> list[tuple[str, int, Any]]:
    """Send the questions on every usable interface, then collect responses for `duration` seconds."""
    s4 = socks.get("v4")
    s6 = socks.get("v6")
    for start in range(0, len(questions), _QUESTIONS_PER_PACKET):
        chunk = questions[start:start + _QUESTIONS_PER_PACKET]
        pkts = (build_query(chunk, False), build_query(chunk, True))
        if s4:
            for p in pkts:
                try:
                    s4.sendto(p, (MDNS_V4_GROUP, MDNS_PORT))
                except OSError:
                    pass
        if s6:
            for idx in v6_ifaces:
                try:
                    s6.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_MULTICAST_IF, idx)
                    for p in pkts:
                        s6.sendto(p, (MDNS_V6_GROUP, MDNS_PORT, 0, idx))
                except OSError:
                    pass  # an interface that cannot multicast (tunnels, etc.)
    records: list[tuple[str, int, Any]] = []
    deadline = time.monotonic() + duration
    readable = list(socks.values())
    while readable:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        ready, _w, _x = select.select(readable, [], [], remaining)
        for s in ready:
            try:
                data, _addr = s.recvfrom(9000)
            except OSError:
                continue
            records.extend(parse_message(data))
    return records


class _State:
    def __init__(self) -> None:
        self.types: set[str] = set()  # service types learned from the meta-query
        self.instances: dict[str, tuple[str, str]] = {}  # full instance name (lower) -> (service type, instance label)
        self.srv: dict[str, tuple[int, str]] = {}
        self.txt: dict[str, dict[str, str]] = {}
        self.a4: dict[str, set[str]] = {}
        self.a6: dict[str, set[str]] = {}


def _ingest(state: _State, records: list[tuple[str, int, Any]]) -> None:
    for name, rtype, val in records:
        lname = name.lower()
        if rtype == T_PTR:
            if lname == META_QUERY:
                state.types.add(val.lower())
                continue
            owner_suffix = "." + lname
            label = val[: -len(owner_suffix)] if val.lower().endswith(owner_suffix) else val
            state.instances[val.lower()] = (lname, label)
        elif rtype == T_SRV:
            state.srv[lname] = (val[0], val[1].lower())
        elif rtype == T_TXT:
            state.txt[lname] = val
        elif rtype == T_A:
            state.a4.setdefault(lname, set()).add(val)
        elif rtype == T_AAAA:
            state.a6.setdefault(lname, set()).add(val)


def _addresses(state: _State, host: str) -> list[str]:
    v4 = sorted(state.a4.get(host, ()))
    # link-local IPv6 is unusable without an interface scope, so skip it
    v6 = [a for a in sorted(state.a6.get(host, ())) if not a.lower().startswith("fe80")]
    return v4 + v6


def _next_questions(state: _State, base: list[tuple[Any, int]], wildcard: bool) -> list[tuple[Any, int]]:
    questions = list(base)
    if wildcard:
        for service_type in sorted(state.types - {META_QUERY})[:MAX_DISCOVERED_TYPES]:
            questions.append((service_type, T_PTR))
    asked_hosts: set[str] = set()
    for full, (service_type, label) in state.instances.items():
        instance_name = [label] + _labels(service_type)
        if full not in state.srv:
            questions.append((instance_name, T_SRV))
        if full not in state.txt:
            questions.append((instance_name, T_TXT))
        srv = state.srv.get(full)
        if srv and srv[1] and not _addresses(state, srv[1]) and srv[1] not in asked_hosts:
            asked_hosts.add(srv[1])
            questions += [(srv[1], T_A), (srv[1], T_AAAA)]
    return questions


def _summarize(state: _State) -> list[dict[str, Any]]:
    results = []
    for full, (service_type, label) in state.instances.items():
        srv = state.srv.get(full)
        host = srv[1] if srv else None
        results.append(
            {
                "service_type": service_type[: -len(".local")] if service_type.endswith(".local") else service_type,
                "instance": label,
                "host": host,
                "port": srv[0] if srv else None,
                "addresses": _addresses(state, host) if host else [],
                "txt": state.txt.get(full, {}),
            }
        )
    results.sort(key=lambda r: (r["service_type"], r["instance"].lower()))
    return results[:MAX_RESULTS]


def browse(
    service_types: list[str] | None,
    timeout: float,
    *,
    open_sockets: Callable[[], dict[str, socket.socket]] = _open_sockets,
    v6_interfaces: Callable[[], list[int]] = _v6_interfaces,
    run_round: Callable[..., list[tuple[str, int, Any]]] = _mdns_round,
) -> dict[str, Any]:
    """Browse *service_types* (full strings ending in ``.local``) for about *timeout*
    seconds. With no types, ask for every service type on the network first
    (``_services._dns-sd._udp.local``) and then browse each one that answers."""
    wildcard = not service_types
    base: list[tuple[Any, int]] = [(META_QUERY, T_PTR)] if wildcard else [(t, T_PTR) for t in service_types]
    socks = open_sockets()
    if not socks:
        return {"error": "could not open any multicast socket on this host"}
    v6 = v6_interfaces()
    rounds = max(1, int(timeout / ROUND_SECONDS + 0.5))
    round_time = timeout / rounds
    state = _State()
    questions = list(base)
    try:
        for _ in range(rounds):
            _ingest(state, run_round(socks, v6, questions, round_time))
            questions = _next_questions(state, base, wildcard)
    finally:
        for s in socks.values():
            try:
                s.close()
            except OSError:
                pass
    results = _summarize(state)
    out: dict[str, Any] = {"results": results, "count": len(results), "window_seconds": timeout}
    if wildcard:
        out["service_types_seen"] = sorted(t[: -len(".local")] if t.endswith(".local") else t for t in state.types)
    return out
