"""mdns_client -- the stdlib mDNS browser behind mdns_search. Real DNS
packets are built and parsed; only the sockets are faked."""

from __future__ import annotations

import socket
import struct

from unified.plugin_authoring import mdns_client as mc


def _name(name: str) -> bytes:
    out = b""
    for label in name.rstrip(".").split("."):
        raw = label.encode()
        out += bytes([len(raw)]) + raw
    return out + b"\x00"


def _rr(name: str, rtype: int, rdata: bytes) -> bytes:
    return _name(name) + struct.pack(">HHIH", rtype, 1, 120, len(rdata)) + rdata


def _response(*records: bytes) -> bytes:
    return struct.pack(">HHHHHH", 0, 0x8400, 0, len(records), 0, 0) + b"".join(records)


def _ptr(owner: str, target: str) -> bytes:
    return _rr(owner, mc.T_PTR, _name(target))


def _srv(owner: str, port: int, target: str) -> bytes:
    return _rr(owner, mc.T_SRV, struct.pack(">HHH", 0, 0, port) + _name(target))


def _txt(owner: str, *pairs: str) -> bytes:
    return _rr(owner, mc.T_TXT, b"".join(bytes([len(p)]) + p.encode() for p in pairs))


def _a(owner: str, ip: str) -> bytes:
    return _rr(owner, mc.T_A, socket.inet_pton(socket.AF_INET, ip))


def _aaaa(owner: str, ip: str) -> bytes:
    return _rr(owner, mc.T_AAAA, socket.inet_pton(socket.AF_INET6, ip))


PRINTER = "HP ENVY 7100 [17FC6E]._ipp._tcp.local"


def test_parse_message_reads_ptr_srv_txt_a_and_aaaa():
    packet = _response(
        _ptr("_ipp._tcp.local", PRINTER),
        _srv(PRINTER, 631, "hp17fc6e.local"),
        _txt(PRINTER, "rp=ipp/print", "ty=HP ENVY", "flag"),
        _a("hp17fc6e.local", "192.168.4.26"),
        _aaaa("hp17fc6e.local", "fdd2:fb5c:7140:1:b6b6:86ff:fe17:fc6e"),
    )
    records = {(n, t): v for n, t, v in mc.parse_message(packet)}
    assert records[("_ipp._tcp.local", mc.T_PTR)] == PRINTER
    assert records[(PRINTER, mc.T_SRV)] == (631, "hp17fc6e.local")
    assert records[(PRINTER, mc.T_TXT)] == {"rp": "ipp/print", "ty": "HP ENVY", "flag": ""}
    assert records[("hp17fc6e.local", mc.T_A)] == "192.168.4.26"
    assert records[("hp17fc6e.local", mc.T_AAAA)] == "fdd2:fb5c:7140:1:b6b6:86ff:fe17:fc6e"


def test_parse_message_ignores_queries_and_garbage():
    assert mc.parse_message(mc.build_query([("_ipp._tcp.local", mc.T_PTR)])) == []
    assert mc.parse_message(b"") == []
    assert mc.parse_message(_response(_ptr("a.local", "b.local"))[:-3]) == []


def test_build_query_sets_the_unicast_bit_only_when_asked():
    plain = mc.build_query([("_ipp._tcp.local", mc.T_PTR)], qu=False)
    unicast = mc.build_query([("_ipp._tcp.local", mc.T_PTR)], qu=True)
    assert plain[-2:] == b"\x00\x01"
    assert unicast[-2:] == b"\x80\x01"


class _FakeSocket:
    closed = False

    def close(self):
        self.closed = True


def _browse(service_types, answer, timeout=5.0):
    """answer(questions) -> list of response packets for that round."""
    rounds = []
    sock = _FakeSocket()

    def run_round(socks, v6, questions, duration):
        rounds.append(list(questions))
        records = []
        for packet in answer(questions):
            records.extend(mc.parse_message(packet))
        return records

    result = mc.browse(
        service_types, timeout, open_sockets=lambda: {"v4": sock}, v6_interfaces=lambda: [], run_round=run_round
    )
    return result, rounds, sock


def _questions_named(questions, rtype):
    return [n if isinstance(n, str) else ".".join(n) for n, t in questions if t == rtype]


def test_browse_a_specific_type_resolves_host_port_txt_and_addresses_over_later_rounds():
    def answer(questions):
        packets = []
        if "_ipp._tcp.local" in _questions_named(questions, mc.T_PTR):
            packets.append(_response(_ptr("_ipp._tcp.local", PRINTER)))
        if PRINTER in _questions_named(questions, mc.T_SRV):
            packets.append(_response(_srv(PRINTER, 631, "hp17fc6e.local")))
        if PRINTER in _questions_named(questions, mc.T_TXT):
            packets.append(_response(_txt(PRINTER, "ty=HP ENVY")))
        if "hp17fc6e.local" in _questions_named(questions, mc.T_A):
            packets.append(_response(_a("hp17fc6e.local", "192.168.4.26"), _aaaa("hp17fc6e.local", "fe80::1")))
        return packets

    result, rounds, sock = _browse(["_ipp._tcp.local"], answer, timeout=10.0)

    assert result["count"] == 1
    assert result["results"][0] == {
        "service_type": "_ipp._tcp",
        "instance": "HP ENVY 7100 [17FC6E]",
        "host": "hp17fc6e.local",
        "port": 631,
        "addresses": ["192.168.4.26"],  # link-local fe80 dropped
        "txt": {"ty": "HP ENVY"},
    }
    assert "service_types_seen" not in result
    assert len(rounds) == 4
    assert sock.closed


def test_browse_with_no_types_lists_service_types_then_browses_each_one():
    def answer(questions):
        packets = []
        if mc.META_QUERY in _questions_named(questions, mc.T_PTR):
            packets.append(_response(_ptr(mc.META_QUERY, "_ipp._tcp.local"), _ptr(mc.META_QUERY, "_http._tcp.local")))
        if "_ipp._tcp.local" in _questions_named(questions, mc.T_PTR):
            packets.append(_response(_ptr("_ipp._tcp.local", PRINTER), _srv(PRINTER, 631, "hp.local")))
        return packets

    result, rounds, _ = _browse(None, answer, timeout=7.5)

    assert result["service_types_seen"] == ["_http._tcp", "_ipp._tcp"]
    assert [r["instance"] for r in result["results"]] == ["HP ENVY 7100 [17FC6E]"]
    assert mc.META_QUERY in _questions_named(rounds[0], mc.T_PTR)
    assert "_ipp._tcp.local" in _questions_named(rounds[1], mc.T_PTR)
    assert "_http._tcp.local" in _questions_named(rounds[1], mc.T_PTR)


def test_instance_names_containing_dots_are_asked_for_as_one_label():
    dotted = "Living Room v1.2._hap._tcp.local"

    def answer(questions):
        if "_hap._tcp.local" in _questions_named(questions, mc.T_PTR) and len(questions) == 1:
            return [_response(_ptr("_hap._tcp.local", dotted))]
        return []

    _result, rounds, _ = _browse(["_hap._tcp.local"], answer, timeout=5.0)

    srv_questions = [n for n, t in rounds[1] if t == mc.T_SRV]
    assert srv_questions == [["Living Room v1.2", "_hap", "_tcp", "local"]]


def test_no_answers_gives_an_empty_result_not_an_error():
    result, _rounds, _ = _browse(["_ipp._tcp.local"], lambda questions: [], timeout=2.5)
    assert result["results"] == []
    assert result["count"] == 0


def test_no_usable_socket_is_an_error():
    result = mc.browse(["_ipp._tcp.local"], 2.5, open_sockets=lambda: {})
    assert "error" in result


def test_rounds_follow_the_timeout():
    _r, rounds, _ = _browse(["_ipp._tcp.local"], lambda q: [], timeout=1.0)
    assert len(rounds) == 1
    _r, rounds, _ = _browse(["_ipp._tcp.local"], lambda q: [], timeout=20.0)
    assert len(rounds) == 8
