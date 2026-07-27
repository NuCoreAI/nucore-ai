
import xml.etree.ElementTree as ET
from typing import Any


@staticmethod
def _strip_xml_namespace(tag: str) -> str:
    """Return *tag* without the optional ``{namespace}`` prefix."""
    return tag.split("}", 1)[-1] if "}" in tag else tag

@staticmethod
def _coerce_xml_text(value: str | None) -> Any:
    """Convert XML text into simple Python scalar types when possible."""
    text = (value or "").strip()
    if text == "":
        return ""

    lower = text.lower()
    if lower == "true":
        return True
    if lower == "false":
        return False
    if text.isdigit():
        return int(text)
    return text

def _element_to_dict_excluding(
    element: ET.Element,
    exclude: set[str] | list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """Build a dict from *element* children while skipping excluded tags."""
    excluded = set(exclude or [])
    result: dict[str, Any] = {}

    for child in element:
        tag = _strip_xml_namespace(child.tag)
        if tag in excluded:
            continue

        if list(child):
            value: Any = _element_to_dict_excluding(child, exclude=excluded)
        else:
            value = _coerce_xml_text(child.text)

        if tag in result:
            if not isinstance(result[tag], list):
                result[tag] = [result[tag]]
            result[tag].append(value)
        else:
            result[tag] = value

    return result

