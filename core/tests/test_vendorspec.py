"""The converter of a vendor's published specification into the dictionary files, on an invented specification."""
import csv
import zipfile
from pathlib import Path

from schemalyser import vendorspec


def _page(table, description, key, columns):
    """A table's page in the published layout, from invented names."""
    rows = "".join(f'<tr onmouseover="x"><td>{name}</td><td>{n + 1}</td><td width="90%"/></tr>' for n, name in enumerate(key))
    body = ""
    for n, (name, kind, discontinued, said, org, entries) in enumerate(columns):
        body += (f'<tr style="background-color: rgb(238, 238, 238);"><td class="T1Head" style="padding: 5px;">{n + 1}</td>'
                 f'<td class="T1Head" style="padding-right: 12px;">{name}</td><td class="T1Head" style="padding-right: 12px;">{kind}</td>'
                 f'<td>{discontinued}</td><td width="90%"/></tr>'
                 f'<tr><td style="min-width: 0px;"/><td colspan="10"><table class="SubList List"><tbody><tr><td></td></tr>'
                 f'<tr><td style="white-space: normal;"> {said}</td></tr>')
        if org:
            body += f"<tr><td>May contain organization-specific values: {org}</td></tr>"
        if entries:
            body += "<tr><td> Category Entries: </td></tr>" + "".join(
                f'<tr><td style="padding-left: 100px;word-break: break-all;white-space: pre-wrap;overflow-wrap: anywhere;">{e}</td></tr>' for e in entries)
        body += '</tbody></table></td></tr><tr style="border-bottom-width: 1px;"><td colspan="10"/></tr>'
    return f"""<!DOCTYPE html><HTML><HEAD></HEAD><body><div id="oContent">
<table class="Header2"><tbody><tr><td>{table}</td></tr></tbody></table>
<table class="KeyValue"><tbody><tr><td width="10%" align="left" class="T1Head">Description: </td><td class="T1Value" colspan="5">
<table class="SubList"><tbody><tr><td class="T1Value" style="white-space: normal;">{description}</td></tr></tbody></table></td></tr></tbody></table>
<br/><table class="SubHeader3"><tbody><tr><td>Primary Key</td></tr></tbody></table>
<table class="List"><tbody><tr><th>Column Name</th><th>Ordinal Position</th><th width="90%"/></tr>{rows}</tbody></table>
<br/><table class="SubHeader3"><tbody><tr><td>Column Information</td></tr></tbody></table>
<table class="SubList List"><tbody><tr><th style="min-width: 0px;"/><th>Name</th><th>Type</th><th>Discontinued?</th><th width="90%"/></tr>{body}</tbody></table>
</div></body></HTML>"""


PAGES = {
    "SHEET": ("A sheet of observations, one row for each sheet.", ["SHEET_KEY"], [
        ("SHEET_KEY", "NUMERIC", "No", "The unique ID of the sheet.", "", []),
        ("CHARTED_IN", "VARCHAR", "No", "The contact the sheet was charted in.", "No", []),
        ("STATUS_C_NAME", "VARCHAR", "No", "The status of the sheet.", "No", ["Open", "Closed &amp; filed"])]),
    "READING": ("A reading on a sheet.", ["SHEET_KEY", "LINE"], [
        ("SHEET_KEY", "NUMERIC", "No", "The sheet.", "", []),
        ("LINE", "INTEGER", "No", "The line.", "", []),
        ("TAKEN_AT", "DATETIME (Local)", "No", "The instant the reading was taken.", "", []),
        ("OLD_VALUE", "VARCHAR", "Yes", "Discontinued.", "Yes", ["A", "B"])]),
}


def _spec(folder):
    index = "<html><body><ul>" + "".join(f'<li><a href="./{t}.htm" title="Documentation for the table {t}" target="_self" >{t}</a></li>' for t in PAGES) + "</ul></body></html>"
    (folder / "_index.htm").write_text(index)
    for table, (description, key, columns) in PAGES.items():
        (folder / f"{table}.htm").write_text(_page(table, description, key, columns))
    (folder / "CRStyleSheet.css").write_text("body {}")
    (folder / "NOTES.htm").write_text("<html><body>Not a table's page.</body></html>")


def test_the_specification_is_read_into_the_three_files_from_a_folder_or_a_zip(tmp_path):
    folder = tmp_path / "spec"
    folder.mkdir()
    _spec(folder)
    catalogue, tables, entries, counts = vendorspec.convert(folder)
    assert counts == {"tables": 2, "columns": 7, "entries": 4, "unread": 1}
    assert tables == [["READING", "SHEET_KEY LINE", "A reading on a sheet."], ["SHEET", "SHEET_KEY", "A sheet of observations, one row for each sheet."]]
    assert ["", "READING", "TAKEN_AT", 3, "DATETIME (Local)", "", "NO", "No", "", "The instant the reading was taken."] in catalogue
    assert ["", "READING", "OLD_VALUE", 4, "VARCHAR", "", "NO", "Yes", "Yes", "Discontinued."] in catalogue
    assert ["", "SHEET", "SHEET_KEY", 1, "NUMERIC", "", "YES", "No", "", "The unique ID of the sheet."] in catalogue
    # Entries belong to the column whose block holds them, and the text is unescaped.
    assert entries == [["READING", "OLD_VALUE", "A"], ["READING", "OLD_VALUE", "B"], ["SHEET", "STATUS_C_NAME", "Open"], ["SHEET", "STATUS_C_NAME", "Closed & filed"]]
    archive = tmp_path / "spec.zip"
    with zipfile.ZipFile(archive, "w") as out:
        for path in folder.iterdir():
            out.write(path, f"DocGen_x\\{path.name}")
    assert vendorspec.convert(archive)[:3] == (catalogue, tables, entries)
    out = tmp_path / "out"
    assert vendorspec.main([str(archive), "--out", str(out)]) == 0
    written = list(csv.reader((out / "spec-catalogue.csv").open(encoding="utf-8")))
    assert written[0] == vendorspec.CATALOGUE_HEADINGS and len(written) == 8
    assert list(csv.reader((out / "spec-tables.csv").open()))[1] == ["READING", "SHEET_KEY LINE", "A reading on a sheet."]
    assert len(list(csv.reader((out / "spec-category-entries.csv").open()))) == 5
    # The two files are what the dictionary reader takes.
    from schemalyser import datadict
    dictionary = datadict.load(out / "spec-catalogue.csv", out / "spec-tables.csv")
    assert dictionary.table("READING").primary_key() == ("SHEET_KEY", "LINE") and dictionary.description("SHEET") .startswith("A sheet")


def test_a_folder_without_a_table_page_writes_nothing(tmp_path, capsys):
    (tmp_path / "x.htm").write_text("<html><body>nothing</body></html>")
    assert vendorspec.main([str(tmp_path), "--out", str(tmp_path / "out")]) == 1
    assert "nothing was written" in capsys.readouterr().err
