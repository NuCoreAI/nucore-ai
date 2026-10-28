"""ProviderDispatchLLMAdapter._normalize -- the alias table used to resolve
a profile's configured ``provider`` string to its canonical client-registry
key. Covers only the ``openai-responses`` alias added for
OpenAIResponsesAdapter (see provider_clients.py and runtime.py's
native-web-search-provider check) -- the rest of the table has no dedicated
test file yet.
"""

from __future__ import annotations

from unified.provider_dispatch_adapter import ProviderDispatchLLMAdapter


def test_openai_responses_hyphenated_alias_normalizes_to_canonical_underscored_key():
    assert ProviderDispatchLLMAdapter._normalize("openai-responses") == "openai_responses"


def test_openai_responses_canonical_key_normalizes_to_itself():
    assert ProviderDispatchLLMAdapter._normalize("openai_responses") == "openai_responses"
