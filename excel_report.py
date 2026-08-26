from __future__ import annotations

from datetime import date, datetime
from io import BytesIO
import re

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.table import Table, TableStyleInfo
import pandas as pd


BRAND_BLUE = "053782"
BRAND_RED = "E00A1D"
LIGHT_BLUE = "EAF0FA"
LIGHT_RED = "FCE8EA"
LIGHT_ORANGE = "FDE8D0"
LIGHT_YELLOW = "FFF4CC"
LIGHT_GREEN = "E7F4EC"
TEXT = "1F2937"


def build_excel_report(
    findings: pd.DataFrame,
    schedule: pd.DataFrame,
    metadata: pd.DataFrame,
    changes: pd.DataFrame | None = None,
) -> bytes:
    """Create a formatted Excel workbook for review and handover."""
    workbook = Workbook()
    workbook.remove(workbook.active)
    workbook.properties.title = "Spielplanprüfung"
    workbook.properties.subject = "Regelprüfung und Datenstände"
    workbook.properties.creator = "TSV Weilheim Handball - Spielplaner"

    _add_frame_sheet(workbook, "Prüfergebnisse", findings, "Pruefergebnisse")
    _add_frame_sheet(workbook, "Spielplan", schedule, "Spielplan")
    if changes is not None and not changes.empty:
        _add_frame_sheet(workbook, "Änderungen", changes, "Aenderungen")
    _add_metadata_sheet(workbook, metadata)

    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


def _add_frame_sheet(
    workbook: Workbook,
    title: str,
    frame: pd.DataFrame,
    table_name: str,
) -> None:
    sheet = workbook.create_sheet(title)
    sheet.sheet_view.showGridLines = False
    sheet.freeze_panes = "A2"
    sheet.append(list(frame.columns))
    for row in frame.itertuples(index=False, name=None):
        sheet.append([_excel_value(value) for value in row])

    _style_header(sheet)
    if len(frame):
        table = Table(displayName=table_name, ref=sheet.dimensions)
        table.tableStyleInfo = TableStyleInfo(
            name="TableStyleMedium2",
            showFirstColumn=False,
            showLastColumn=False,
            showRowStripes=True,
            showColumnStripes=False,
        )
        sheet.add_table(table)
    else:
        sheet.auto_filter.ref = sheet.dimensions

    _apply_formats(sheet, frame)
    _fit_columns(sheet)
    sheet.auto_filter.ref = sheet.dimensions


def _add_metadata_sheet(workbook: Workbook, metadata: pd.DataFrame) -> None:
    sheet = workbook.create_sheet("Metadaten", 0)
    sheet.sheet_view.showGridLines = False
    sheet.merge_cells("A1:D1")
    title = sheet["A1"]
    title.value = "Spielplanprüfung: Datenstände"
    title.font = Font(name="Aptos Display", size=18, bold=True, color=BRAND_BLUE)
    title.alignment = Alignment(vertical="center")
    sheet.row_dimensions[1].height = 30
    sheet.append([])
    sheet.append(list(metadata.columns))
    for row in metadata.itertuples(index=False, name=None):
        sheet.append([_excel_value(value) for value in row])
    _style_header(sheet, row=3)
    sheet.freeze_panes = "A4"
    _fit_columns(sheet, minimum=16, maximum=55)
    sheet.column_dimensions["A"].width = max(sheet.column_dimensions["A"].width, 24)


def _style_header(sheet: object, row: int = 1) -> None:
    for cell in sheet[row]:
        cell.fill = PatternFill("solid", fgColor=BRAND_BLUE)
        cell.font = Font(name="Aptos", bold=True, color="FFFFFF")
        cell.alignment = Alignment(vertical="center", wrap_text=True)
    sheet.row_dimensions[row].height = 25


def _apply_formats(sheet: object, frame: pd.DataFrame) -> None:
    columns = {str(cell.value): cell.column for cell in sheet[1]}
    for column_name, column_index in columns.items():
        for cell in list(sheet.columns)[column_index - 1][1:]:
            cell.font = Font(name="Aptos", color=TEXT)
            cell.alignment = Alignment(vertical="top", wrap_text=True)
            if column_name in {"Datum", "Anwurf"} and isinstance(
                cell.value, (date, datetime)
            ):
                cell.number_format = (
                    "DD.MM.YYYY HH:MM" if column_name == "Anwurf" else "DD.MM.YYYY"
                )

    if "Priorität" in columns:
        fills = {
            "Hoch": LIGHT_RED,
            "Mittel": LIGHT_ORANGE,
            "Niedrig": LIGHT_YELLOW,
        }
        column = columns["Priorität"]
        for row in range(2, sheet.max_row + 1):
            value = str(sheet.cell(row, column).value or "")
            if value in fills:
                sheet.cell(row, column).fill = PatternFill(
                    "solid", fgColor=fills[value]
                )

    if "Status" in columns:
        fills = {
            "Geändert": LIGHT_ORANGE,
            "Hinzugefügt": LIGHT_GREEN,
            "Entfernt": LIGHT_RED,
        }
        column = columns["Status"]
        for row in range(2, sheet.max_row + 1):
            value = str(sheet.cell(row, column).value or "")
            if value in fills:
                sheet.cell(row, column).fill = PatternFill(
                    "solid", fgColor=fills[value]
                )


def _fit_columns(sheet: object, minimum: int = 12, maximum: int = 42) -> None:
    for column_index, cells in enumerate(sheet.columns, start=1):
        letter = get_column_letter(column_index)
        longest = max(
            (len(str(cell.value)) for cell in cells if cell.value is not None),
            default=minimum,
        )
        sheet.column_dimensions[letter].width = min(max(longest + 2, minimum), maximum)


def _excel_value(value: object) -> object:
    if value is None or (not isinstance(value, str) and pd.isna(value)):
        return ""
    if isinstance(value, pd.Timestamp):
        parsed = value.to_pydatetime()
        return parsed.replace(tzinfo=None) if parsed.tzinfo else parsed
    if isinstance(value, (date, datetime, int, float, bool)):
        return value
    text = re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F]", "", str(value))
    if text.startswith(("=", "+", "-", "@")):
        return f"'{text}"
    return text
