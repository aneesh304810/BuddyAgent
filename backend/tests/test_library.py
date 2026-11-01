"""The library: uploads kept encrypted, read, searchable, with facts; vault items locked; forget forgets."""

import asyncio
import io
import json

import pytest
from fastapi.testclient import TestClient

from buddy import library
from buddy.memory import Memory
from buddy.tools import ToolError

LEASE = ("RESIDENTIAL LEASE\n\nTenant: Aneesh Nair. Landlord: Oak Street Homes.\n\n"
         "Monthly rent is $2,100, due on the 1st. Pets: one cat allowed with a $300 deposit.\n\n"
         "The lease ends on 2027-06-30.").encode()


def fake_understand(sensitive=False, facts=None):
    async def understand(row, sample, image, pdf):
        assert image is None or row["kind"] == "image"
        return {"title": "Lease, 12 Oak St" if "LEASE" in sample else row["name"], "doc_type": "lease",
                "summary": "Residential lease, rent $2,100 a month, ends 2027-06-30.", "sensitive": sensitive,
                "text": "Passport no. X1234567, expires 2029-03-14" if image else "",
                "facts": facts if facts is not None else [
                    {"fact": "Rent $2,100 due on the 1st", "important": True},
                    {"fact": "One cat allowed with a $300 pet deposit", "important": False}]}
    return understand


@pytest.fixture
def lib(tmp_path, monkeypatch):
    monkeypatch.setattr(library, "LIB_DIR", tmp_path / "library")
    return library.Library(Memory(":memory:"), understand=fake_understand())


def test_a_document_is_stored_encrypted_read_and_searchable(lib):
    item = lib.add("lease.txt", LEASE, note="my new lease")
    blob = (library.LIB_DIR / f"{item['id']}.bin").read_bytes()
    assert b"rent" not in blob.lower()  # encrypted at rest
    assert library.original(lib.memory, item["id"])[0] == LEASE
    done = asyncio.run(lib.ingest(item["id"], LEASE))
    assert done["status"] == "ready" and done["important"] == ["Rent $2,100 due on the 1st"]
    assert lib.memory.recall("cat") == ["One cat allowed with a $300 pet deposit (from lease.txt)"]  # quiet fact saved
    assert lib.memory.recall("rent") == []  # important ones wait for the user's yes
    hits = lib.search("what does the lease say about pets?")
    assert hits[0]["file"] == "lease.txt" and "one cat allowed" in hits[0]["text"]
    assert "Saved Lease, 12 Oak St." in library.report(done)
    assert lib.save_facts(item["id"], done["important"]) == "Saved 1 fact from lease.txt."
    again = lib.add("copy.txt", LEASE)
    assert again["duplicate"] and "already saved" in library.report(again)


def test_spreadsheets_and_json_lists_become_tables(lib):
    csv_bytes = b"date,store,amount\n2026-01-03,Costco,120.50\n2026-01-10,Publix,45.10\n"
    item = lib.add("spend.csv", csv_bytes)
    done = asyncio.run(lib.ingest(item["id"], csv_bytes))
    tables = json.loads(done["tables"])
    assert tables[0]["columns"] == ["date", "store", "amount"] and tables[0]["rows"] == 2
    assert lib.search("Costco")[0]["file"] == "spend.csv"
    from openpyxl import Workbook
    wb = Workbook()
    wb.active.title = "Budget"
    wb.active.append(["month", "food"])
    wb.active.append(["Jan", 400])
    buf = io.BytesIO()
    wb.save(buf)
    item = lib.add("budget.xlsx", buf.getvalue())
    sheets = json.loads(asyncio.run(lib.ingest(item["id"], buf.getvalue()))["tables"])
    assert sheets[0]["sheet"] == "Budget" and sheets[0]["columns"] == ["month", "food"]
    data = json.dumps([{"name": "Netflix", "usd": 17.99}, {"name": "Spotify", "usd": 11.99}]).encode()
    item = lib.add("subs.json", data)
    assert json.loads(asyncio.run(lib.ingest(item["id"], data))["tables"])[0]["rows"] == 2


def test_vault_items_stay_locked_until_the_user_says_yes(tmp_path, monkeypatch):
    monkeypatch.setattr(library, "LIB_DIR", tmp_path / "library")
    lib = library.Library(Memory(":memory:"), understand=fake_understand(sensitive=True, facts=[]))
    photo = b"\xff\xd8\xff fake jpeg"
    item = lib.add("passport.jpg", photo)
    done = asyncio.run(lib.ingest(item["id"], photo))
    assert done["vault"] == 1 and "in the vault" in library.report(done)
    assert lib.search("passport")[0]["text"].startswith("[vault item")
    assert lib.open(item["id"])["locked"]

    class Thrift:
        def ask_action(self, question, run):
            self.run = run
            return "APPROVAL NEEDED: " + question
    thrift = Thrift()
    out = asyncio.run(library.run_tool(lib, {"action": "unlock", "id": item["id"]}, thrift))
    assert out == "APPROVAL NEEDED: OK to open your passport.jpg for me?" and lib.open(item["id"]).get("locked")
    asyncio.run(thrift.run())
    assert "X1234567" in lib.open(item["id"])["text"]


def test_forget_removes_the_file_text_and_facts(lib):
    item = lib.add("lease.txt", LEASE)
    asyncio.run(lib.ingest(item["id"], LEASE))
    assert "Forgot Lease, 12 Oak St" in lib.forget(item["id"])
    assert lib.search("cat") == [] and lib.memory.recall("cat") == []
    assert not (library.LIB_DIR / f"{item['id']}.bin").exists()
    with pytest.raises(ToolError):
        lib.open(item["id"])


def test_chunks_break_long_text_into_passages():
    text = "\n\n".join(f"Paragraph {i}. " + "word " * 120 for i in range(6))
    parts = library.chunks(text)
    assert len(parts) == 3 and all(len(p.split()) <= 260 for p in parts)


def test_upload_endpoint_reads_in_the_background_and_reports(tmp_path, monkeypatch):
    from buddy import server
    monkeypatch.setattr(library, "LIB_DIR", tmp_path / "library")
    monkeypatch.setattr(library.Library, "_understand", lambda self, *a: fake_understand()(*a))
    said = []

    async def deliver(kind, text, **extra):
        said.append((kind, text))
        return True
    monkeypatch.setattr(server, "deliver", deliver)
    with TestClient(server.app) as c:
        r = c.post("/api/library?name=lease.txt&note=my%20lease", content=LEASE,
                   headers={"Authorization": f"Bearer {server.settings.access_token}"})
        assert r.status_code == 200 and r.json()["status"] == "reading"
        for _ in range(50):
            if said:
                break
            c.portal.call(asyncio.sleep, 0.05)
    assert said and said[0][0] == "library" and "Shall I remember these?" in said[0][1]
