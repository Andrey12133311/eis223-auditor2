"""Build-time archive regression tests; no EIS or network dependency."""
from __future__ import annotations

import gzip
import io
import os
import tarfile
import tempfile
import zipfile
from pathlib import Path

from v310_archive_scan import extract_members,archive_kind
from v295_eis_originals import assert_format

def text_reader(path):
    path=Path(path)
    if path.suffix==".pdf" and path.read_bytes().startswith(b"%PDF"):
        return "Подтверждённый текст оригинала документа"
    if path.suffix==".xml":
        return path.read_text(encoding="utf8")
    return ""

def run():
    with tempfile.TemporaryDirectory() as folder:
        folder=Path(folder)
        nested=io.BytesIO()
        with zipfile.ZipFile(nested,"w") as inner:
            inner.writestr("nested/contract.pdf",b"%PDF-1.5 original contract")
        source=folder/"sample.zip"
        with zipfile.ZipFile(source,"w") as z:
            z.writestr("terms.pdf",b"%PDF-1.7 technical requirements")
            z.writestr("price.txt","Обоснование НМЦД и другие сведения".encode("utf8"))
            z.writestr("inner.zip",nested.getvalue())
            z.writestr("../../escape.pdf",b"%PDF-MALICIOUS")
        assert_format(source.read_bytes(),".zip")
        members,warnings=extract_members(source,"32616280442","A"*32,folder,text_reader)
        assert len(members)==3,(members,warnings)
        assert all(Path(m["path"]).is_file() for m in members)
        assert any("небезопасный путь" in w for w in warnings),warnings
        assert not (folder/"escape.pdf").exists()
        assert all(len(m["text"])>=3 for m in members)
        assert len({m["sha256"] for m in members})==3
        tarball=folder/"documents.tar.gz"
        with tarfile.open(tarball,"w:gz") as t:
            data=b"%PDF-1.7 original file in tar.gz"
            info=tarfile.TarInfo("documents/contract.pdf")
            info.size=len(data)
            t.addfile(info,io.BytesIO(data))
        assert archive_kind(tarball)==".gz"
        members,warnings=extract_members(tarball,"32616280442","B"*32,folder,text_reader)
        assert len(members)==1,(members,warnings)
        assert_format(tarball.read_bytes(),".gz")
        gz=folder/"single.pdf.gz"
        with gzip.open(gz,"wb") as z:
            z.write(b"%PDF-1.7 compressed standalone")
        members,warnings=extract_members(gz,"32616280442","C"*32,folder,text_reader)
        assert len(members)==1,(members,warnings)
        assert_format(gz.read_bytes(),".gz")
        for ext,content in (
            (".rar",bytes.fromhex("526172211a07")+b"not-a-full-rar"),
            (".7z",bytes.fromhex("377abcaf271c")+b"not-a-full-7z"),
            (".cab",b"MSCF"+b"not-a-full-cab"),
        ):
            assert_format(content,ext)
        print("V310 ARCHIVE_SELFTEST_OK zip_members=3 nested=1 tar_gz=1 gzip=1 unsafe_path_rejected=1",flush=True)
if __name__=="__main__":run()
