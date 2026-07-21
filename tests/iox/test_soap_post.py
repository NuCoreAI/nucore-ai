"""IoXWrapper.soap_post -- thin wrapper over post() that fills in the
SOAP-specific headers (Content-Type/SOAPAction) so callers only supply the
XML envelope and, when needed, the SOAPAction value.
"""

from __future__ import annotations

from iox.iox_wrapper import IoXWrapper


class FakeResp:
    status_code = 200


def _bare_wrapper() -> IoXWrapper:
    return object.__new__(IoXWrapper)


def test_soap_post_sets_xml_content_type_and_soap_action():
    wrapper = _bare_wrapper()
    calls = []
    wrapper.post = lambda path, body, headers: (calls.append((path, body, headers)), FakeResp())[1]

    envelope = "<soap:Envelope>...</soap:Envelope>"
    result = wrapper.soap_post("/services", envelope, soap_action="urn:udi.com:service:X#Method")

    assert calls == [
        (
            "/services",
            envelope,
            {"Content-Type": "text/xml; charset=utf-8", "SOAPAction": "urn:udi.com:service:X#Method"},
        )
    ]
    assert result.status_code == 200


def test_soap_post_omits_soap_action_header_when_not_given():
    wrapper = _bare_wrapper()
    calls = []
    wrapper.post = lambda path, body, headers: (calls.append((path, body, headers)), FakeResp())[1]

    wrapper.soap_post("/services", "<soap:Envelope/>")

    (call,) = calls
    assert "SOAPAction" not in call[2]
    assert call[2] == {"Content-Type": "text/xml; charset=utf-8"}


def test_soap_post_caller_headers_override_defaults():
    wrapper = _bare_wrapper()
    calls = []
    wrapper.post = lambda path, body, headers: (calls.append((path, body, headers)), FakeResp())[1]

    wrapper.soap_post(
        "/services",
        "<soap:Envelope/>",
        soap_action="urn:udi.com:service:X#Method",
        headers={"Content-Type": "text/xml; charset=iso-8859-1"},
    )

    (call,) = calls
    assert call[2] == {
        "Content-Type": "text/xml; charset=iso-8859-1",
        "SOAPAction": "urn:udi.com:service:X#Method",
    }
