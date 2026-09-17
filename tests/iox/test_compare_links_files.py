"""Verifies INSTEONDiagnostics._compare_links_files's MATCH/MISMATCH
comparison logic -- previously untested (compare_device_links's own tests,
tests/iox/test_plm_exclusion.py, only exercise the PLM-exclusive-operation
busy/refusal mechanism, not this comparison).
"""

from __future__ import annotations

from iox.diagnostics.insteon_diag import INSTEONDiagnostics


def _bare_diag() -> INSTEONDiagnostics:
    return object.__new__(INSTEONDiagnostics)


def _links_file(tmp_path, name: str, rows: list[str]) -> str:
    """Write a minimal fenced-CSV links file matching what
    get_dev_links_table/get_iox_links_table actually produce -- a title
    line, then a ```csv fence containing the idx,role,group,device,data
    header followed by the given rows."""
    path = tmp_path / name
    lines = ["Links Table\n", "```csv\n", "idx,role,group,device,data\n"]
    lines += [f"{row}\n" for row in rows]
    lines.append("```\n")
    path.write_text("".join(lines))
    return str(path)


def test_high_water_mark_only_on_device_side_is_still_a_match(tmp_path):
    diag = _bare_diag()
    device_path = _links_file(
        tmp_path,
        "device.txt",
        [
            "0,responder,0,PLM_ADDR,on_level=FF;ramp_rate=1F;group_or_data=00",
            "1,high_water_mark,0,00.00.00,byte1=00;byte2=00;byte3=00",
        ],
    )
    iox_path = _links_file(
        tmp_path,
        "iox.txt",
        ["0,responder,0,PLM_ADDR,on_level=FF;ramp_rate=1F;group_or_data=00"],
    )

    report = diag._compare_links_files(device_path, iox_path)

    assert report.startswith("MATCH")


def test_high_water_mark_only_on_iox_side_is_still_a_match(tmp_path):
    diag = _bare_diag()
    device_path = _links_file(
        tmp_path,
        "device.txt",
        ["0,responder,0,PLM_ADDR,on_level=FF;ramp_rate=1F;group_or_data=00"],
    )
    iox_path = _links_file(
        tmp_path,
        "iox.txt",
        [
            "0,responder,0,PLM_ADDR,on_level=FF;ramp_rate=1F;group_or_data=00",
            "1,high_water_mark,0,00.00.00,byte1=00;byte2=00;byte3=00",
        ],
    )

    report = diag._compare_links_files(device_path, iox_path)

    assert report.startswith("MATCH")


def test_a_genuine_one_sided_link_is_still_reported_as_mismatch(tmp_path):
    # The high_water_mark exclusion must not swallow a real discrepancy.
    diag = _bare_diag()
    device_path = _links_file(
        tmp_path,
        "device.txt",
        ["0,responder,0,PLM_ADDR,on_level=FF;ramp_rate=1F;group_or_data=00"],
    )
    iox_path = _links_file(tmp_path, "iox.txt", [])

    report = diag._compare_links_files(device_path, iox_path)

    assert report.startswith("MISMATCH")
    assert "Present on the device but NOT in NuCore's records" in report
