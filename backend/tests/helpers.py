"""Signed-in clients and small builders shared by the integration tests."""

from __future__ import annotations

import itertools
from pathlib import Path

from fastapi.testclient import TestClient

DOCS = Path(__file__).resolve().parent / "fixtures" / "case_documents"
WEB = {"x-groundwork-client": "web"}
_counter = itertools.count(1)


def unique(prefix: str) -> str:
    return f"{prefix}{next(_counter)}"


def sign_up(app, organization: str | None = None) -> tuple[TestClient, dict]:
    client = TestClient(app)
    username = unique("admin")
    response = client.post(
        "/api/v1/auth/sign-up",
        json={
            "organization": organization or unique("Legal Aid "),
            "username": username,
            "full_name": "Avery Admin",
            "email": f"{username}@example.org",
            "password": "correct-horse-battery",
        },
    )
    assert response.status_code == 201, response.text
    client.headers.update(WEB)
    return client, response.json()["user"]


def add_member(admin: TestClient, app, role: str, name: str = "Member Person") -> tuple[TestClient, dict]:
    username = unique(role)
    response = admin.post(
        "/api/v1/settings/users",
        json={"username": username, "full_name": name, "email": f"{username}@example.org", "password": "another-long-password", "role": role},
    )
    assert response.status_code == 201, response.text
    member = TestClient(app)
    signed = member.post("/api/v1/auth/sign-in", json={"username": username, "password": "another-long-password"})
    assert signed.status_code == 200, signed.text
    member.headers.update(WEB)
    return member, signed.json()["user"]


def new_case(client: TestClient, title: str = "Parker heirs property — 418 Magnolia Rd") -> dict:
    response = client.post(
        "/api/v1/cases",
        json={
            "title": title,
            "description": "Family seeking to clear title to the home place.",
            "property": {"address_line": "418 Magnolia Road", "county": "Hollins", "state": "GA", "parcel_number": "14-022-0113"},
            "parties": [{"name": "Denise Parker Holmes", "roles": ["client", "heir"]}],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def upload(client: TestClient, case_id: str, filename: str, content: bytes | None = None, content_type: str = "text/plain") -> dict:
    data = content if content is not None else (DOCS / filename).read_bytes()
    response = client.post(f"/api/v1/cases/{case_id}/documents", files={"file": (filename, data, content_type)})
    assert response.status_code == 201, response.text
    return response.json()["document"]


def simple_pdf(pages: list[str]) -> bytes:
    """A minimal valid PDF with one text line per page (Helvetica), for the pypdf path."""
    objects: list[bytes] = []
    kids = " ".join(f"{3 + 2 * i} 0 R" for i in range(len(pages)))
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode())
    font_ref = 3 + 2 * len(pages)
    for i, text in enumerate(pages):
        content_ref = 4 + 2 * i
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {content_ref} 0 R /Resources << /Font << /F1 {font_ref} 0 R >> >> >>".encode()
        )
        lines = "".join(f"BT /F1 12 Tf 72 {720 - 18 * n} Td ({line}) Tj ET\n" for n, line in enumerate(text.split("\n")))
        stream = lines.encode()
        objects.append(b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"endstream")
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)
