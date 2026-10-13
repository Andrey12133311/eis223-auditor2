"""Startup smoke test: exercise the real bounded EIS archive extraction engine.

Only synthetic local files; never touches the EIS, purchasing DB, audit
counts or Railway volumes. Intended to detect broken container dependencies.
"""
from __future__ import annotations

import gzip
import io
import tarfile
import tempfile
import zipfile
from pathlib import Path

from v310_archive_scan import extract_members, safe_member

def run():
    with tempfile.TemporaryDirectory(prefix="eis223-archive-test-") as temp:
        folder=Path(temp)
        inner=io.BytesIO()
        with zipfile.ZipFile(inner,"w") as z:
            z.writestr("Вложенный_документ.txt","Второй читаемый документ закупки".encode("utf-8"))
        outer=folder/"documents.zip"
        with zipfile.ZipFile(outer,"w") as z:
            z.writestr("Техническое_задание.txt","Текст технического задания".encode("utf-8"))
            z.writestr("subfolder/inner.zip",inner.getvalue())
            z.writestr("../traversal.txt","Никогда не распаковывать за пределы архива")
        stub=lambda _: (_ for _ in ()).throw(RuntimeError("Expected .txt direct decoding"))
        docs,warns=extract_members(outer,"32616280442","smoke-zip",folder,stub)
        assert len(docs)==2,(len(docs),warns)
        assert all(len(d["text"])>=3 for d in docs)
        assert not (folder/"traversal.txt").exists()
        assert any("небезопасный" in w for w in warns),warns

        tarpath=folder/"documents.tar.gz"
        with tarfile.open(tarpath,"w:gz") as z:
            contents="Проверяемый документ в TAR.GZ".encode()
            info=tarfile.TarInfo("Документация.txt")
            info.size=len(contents)
            z.addfile(info,io.BytesIO(contents))
        tar_docs,tar_warnings=extract_members(tarpath,"32616280442","smoke-tar",folder,stub)
        assert len(tar_docs)==1,(len(tar_docs),tar_warnings)

        gzp=folder/"single.txt.gz"
        with gzip.open(gzp,"wb") as z:
            z.write("Единственный документ в GZIP".encode())
        gz_docs,gz_warnings=extract_members(gzp,"32616280442","smoke-gz",folder,stub)
        assert len(gz_docs)==1,(len(gz_docs),gz_warnings)
        assert not safe_member("../escape.txt")
        print("V312 ARCHIVE_SELFTEST_OK zip=2 nested=1 tar_gz=1 gzip=1 traversal=blocked",flush=True)

if __name__=="__main__":
    run()
