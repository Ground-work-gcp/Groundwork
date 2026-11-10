"""CSV parsing and field decoding (ported from ``parse.ts``).

Every decoder returns a value **or an error**, never a silently coerced fallback.
``float("1,23,456.78")`` fails and ``parseFloat`` of it is ``1``; both are worse than
refusing, because a reconciliation over quietly mangled inputs reports a confident number
about data that was never right.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Generic, Literal, TypeVar

T = TypeVar("T")

_JS_TRIM = re.compile(r"^[\s﻿]+|[\s﻿]+$")


def js_trim(text: str) -> str:
    """``String.prototype.trim``: also strips U+FEFF, which ``str.strip`` keeps."""
    return _JS_TRIM.sub("", text)


@dataclass
class CsvRow:
    line: int
    cells: list[str]
    raw: str


def parse_csv(text: str) -> tuple[list[str], list[CsvRow]]:
    """RFC 4180 plus a UTF-8 BOM and CRLF endings. Quoted fields may hold commas and newlines."""
    source = re.sub(r"^﻿", "", text).replace("\r\n", "\n")
    rows: list[CsvRow] = []
    cells: list[str] = []
    field = ""
    quoted = False
    line = 1
    row_start = 1
    row_raw = ""

    def end_row() -> None:
        nonlocal cells, field, row_raw, row_start
        cells.append(field)
        field = ""
        if not (len(cells) == 1 and cells[0] == ""):
            rows.append(CsvRow(row_start, cells, row_raw))
        cells = []
        row_raw = ""
        row_start = line + 1

    i = 0
    n = len(source)
    while i < n:
        char = source[i]
        if char != "\n" or quoted:
            row_raw += char
        if quoted:
            if char == '"':
                if i + 1 < n and source[i + 1] == '"':
                    field += '"'
                    i += 1
                    row_raw += '"'
                else:
                    quoted = False
            else:
                if char == "\n":
                    line += 1
                field += char
            i += 1
            continue
        if char == '"' and field == "":
            quoted = True
        elif char == ",":
            cells.append(field)
            field = ""
        elif char == "\n":
            end_row()
            line += 1
        else:
            field += char
        i += 1
    if field != "" or cells or row_raw != "":
        end_row()

    header = [js_trim(cell) for cell in rows.pop(0).cells] if rows else []
    return header, rows


@dataclass
class Decoded(Generic[T]):
    ok: bool
    value: T | None = None
    error: str = ""


def _ok(value: T) -> Decoded[T]:
    return Decoded(True, value)


def _fail(error: str) -> Decoded:
    return Decoded(False, None, error)


_MONEY = re.compile(r"^([0-9]+)(?:\.([0-9]{1,2}))?$")
_MONEY_STRIP = re.compile(r"[₹$\s,]")
_MAX_SAFE = 2**53 - 1


def parse_minor_units(raw: str) -> Decoded[int]:
    """``"11,57,147.71"`` → ``115714771``. Integer arithmetic on the two halves, never a float.

    Accepts Indian and Western grouping, a currency symbol, a sign and accounting
    parentheses; rejects a third decimal place.
    """
    text = js_trim(raw)
    if text == "":
        return _fail("empty")
    sign = 1
    if text.startswith("(") and text.endswith(")"):
        sign = -1
        text = text[1:-1]
    text = _MONEY_STRIP.sub("", text)
    if text.startswith("-"):
        sign *= -1
        text = text[1:]
    elif text.startswith("+"):
        text = text[1:]
    match = _MONEY.match(text)
    if not match:
        return _fail(f'not a money amount: "{js_trim(raw)}"')
    whole, fraction = match.group(1), match.group(2) or ""
    value = int(whole) * 100 + int(fraction.ljust(2, "0"))
    if value > _MAX_SAFE:
        return _fail(f'amount out of range: "{js_trim(raw)}"')
    return _ok(sign * value)


# Magpie's name for the same decoder.
parse_paise = parse_minor_units

DateFormat = Literal["ISO", "DMY", "MDY"]

_ISO = re.compile(r"^([0-9]{4})-([0-9]{2})-([0-9]{2})(?:[T ].*)?$")
_DMY = re.compile(r"^([0-9]{1,2})[/-]([0-9]{1,2})[/-]([0-9]{4})$")


def parse_date(raw: str, fmt: DateFormat) -> Decoded[str]:
    """An ISO ``YYYY-MM-DD`` date, or an error.

    **The format is declared per source, never sniffed per row**: ``03/06/2026`` is 3 June
    in Mumbai and 6 March in New York. ``MDY`` is Groundwork's addition for US records.
    """
    text = js_trim(raw)
    if text == "":
        return _fail("empty")
    if fmt == "ISO":
        match = _ISO.match(text)
        if not match:
            return _fail(f'not an ISO date: "{text}"')
        year, month, day = int(match.group(1)), int(match.group(2)), int(match.group(3))
    else:
        match = _DMY.match(text)
        if not match:
            label = "dd/mm/yyyy" if fmt == "DMY" else "mm/dd/yyyy"
            return _fail(f'not a {label} date: "{text}"')
        first, second, year = int(match.group(1)), int(match.group(2)), int(match.group(3))
        day, month = (first, second) if fmt == "DMY" else (second, first)
    # JavaScript's Date.UTC maps years 0-99 onto 1900-1999, so the TS rejects them.
    if year < 100:
        return _fail(f'not a real calendar date: "{text}"')
    try:
        date(year, month, day)
    except ValueError:
        return _fail(f'not a real calendar date: "{text}"')
    return _ok(f"{year:04d}-{month:02d}-{day:02d}")


def parse_enum(raw: str, allowed: tuple[str, ...]) -> Decoded[str]:
    text = js_trim(raw).lower()
    for value in allowed:
        if value.lower() == text:
            return _ok(value)
    return _fail(f'expected one of {" | ".join(allowed)}, got "{js_trim(raw)}"')


def parse_text(raw: str, *, required: bool = True) -> Decoded[str]:
    text = js_trim(raw)
    if text == "" and required:
        return _fail("empty")
    return _ok(text)


_INTEGER = re.compile(r"^-?[0-9]+$")


def parse_integer(raw: str) -> Decoded[int]:
    text = js_trim(raw).replace(",", "")
    if text == "":
        return _fail("empty")
    if not _INTEGER.match(text):
        return _fail(f'not a whole number: "{js_trim(raw)}"')
    return _ok(int(text))
