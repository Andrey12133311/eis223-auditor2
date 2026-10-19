"""Bounded, read-only extraction of 223-FZ ORIGINAL archive contents.

ZIP / RAR / 7z / TAR / TAR.GZ / TGZ / TAR.BZ2 / TAR.XZ / GZ / BZ2 / XZ.
Supports nested archives to depth 2. No symlinks, traversal, code execution,
automatic password bypass, blind archive-success counters, or unbounded output.
bsdtar from libarchive-tools is used for rar/7z and as last resort.
"""
from __future__ import annotations

import bz2
import gzip
import hashlib
import io
import lzma
import os
import re
import select
import subprocess
import tarfile
import tempfile
import time
import zipfile
from pathlib import Path

ARCHIVE_EXT={".zip",".rar",".7z",".tar",".gz",".tgz",".bz2",".xz",".tbz2",".txz",".cab"}
DOC_EXT={".pdf",".docx",".doc",".xlsx",".xls",".odt",".ods",".rtf",".xml",".txt",".csv"}
MAX_MEMBER=12*1024*1024
MAX_EXPANDED=72*1024*1024
MAX_MEMBERS=100
MAX_DEPTH=2

def archive_kind(path:Path):
    ext=path.suffix.lower()
    if ext in ARCHIVE_EXT:return ext
    try:
        with path.open("rb") as f:
            head=f.read(512)
        if head.startswith(b"Rar!\x1a\x07"):return ".rar"
        if head.startswith(b"7z\xbc\xaf\x27\x1c"):return ".7z"
        if head[:4]==b"PK\x03\x04":return ".zip"
        if head[:2]==b"\x1f\x8b":return ".gz"
        if head[:3]==b"BZh":return ".bz2"
        if head[:6]==b"\xfd7zXZ\x00":return ".xz"
        if len(head)>=262 and head[257:262]==b"ustar":return ".tar"
    except OSError:pass
    return ""

def safe_member(name):
    if not isinstance(name,str) or not 1<=len(name)<=320:return False
    clean=name.replace("\\","/")
    if clean.startswith("/") or ":" in clean.split("/")[0]:return False
    return not any(x in ("",".","..") for x in clean.split("/")) and not any(ord(x)<32 for x in clean)

def _list(path):
    kind=archive_kind(path)
    if kind==".zip":
        with zipfile.ZipFile(path) as z:
            items=z.infolist()
            if len(items)>MAX_MEMBERS:return [],["Слишком много файлов в ZIP"]
            return [(i.filename,i.file_size) for i in items if not i.is_dir() and not
                    (i.external_attr>>16 & 0o170000)==0o120000],[]
    if tarfile.is_tarfile(path):
        with tarfile.open(path,"r:*") as t:
            m=t.getmembers()
            if len(m)>MAX_MEMBERS:return [],["Слишком много файлов в TAR"]
            return [(i.name,i.size) for i in m if i.isfile()],[]
    # RAR/7z/CAB handled by libarchive's audited bsdtar utility.
    if kind in (".gz",".bz2",".xz") and not tarfile.is_tarfile(path):
        return [(path.stem,MAX_MEMBER)],[]
    proc=subprocess.run(["bsdtar","-tf",str(path)],capture_output=True,
                        timeout=18,check=False)
    if proc.returncode!=0:return [],["Архив libarchive не прочитан"]
    listing=proc.stdout[:70000].decode("utf-8","replace").splitlines()
    if len(listing)>MAX_MEMBERS:return [],["Слишком много файлов в архиве"]
    return [(name,MAX_MEMBER) for name in listing if not name.endswith("/")],[]

def _read_stream(stream):
    out=stream.read(MAX_MEMBER+1)
    if len(out)>MAX_MEMBER:raise ValueError("Вложение превышает безопасный размер")
    return out

def _bsdtar_read(path,name):
    proc=subprocess.Popen(["bsdtar","-xOf",str(path),name],stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL)
    limit=time.monotonic()+24
    output=bytearray()
    try:
        while True:
            remaining=limit-time.monotonic()
            if remaining<=0:raise ValueError("Таймаут распаковки")
            readable,_,_=select.select([proc.stdout],[],[],min(1,remaining))
            if not readable:
                if proc.poll() is not None:break
                continue
            chunk=os.read(proc.stdout.fileno(),65536)
            if not chunk:break
            output.extend(chunk)
            if len(output)>MAX_MEMBER:raise ValueError("Распакованное вложение слишком большое")
        proc.wait(timeout=3)
        if proc.returncode!=0:raise ValueError("Ошибка извлечения из RAR/7z")
        return bytes(output)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=3)

def _read_member(path,name):
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as z:
            info=z.getinfo(name)
            if info.file_size>MAX_MEMBER or info.flag_bits&1:raise ValueError("Большой или зашифрованный ZIP")
            with z.open(info) as inp:return _read_stream(inp)
    if tarfile.is_tarfile(path):
        with tarfile.open(path,"r:*") as t:
            member=t.getmember(name)
            if not member.isfile() or member.size>MAX_MEMBER:raise ValueError("Небезопасный TAR")
            with t.extractfile(member) as inp:return _read_stream(inp)
    ext=archive_kind(path)
    if ext==".gz":
        with gzip.open(path,"rb") as inp:return _read_stream(inp)
    if ext==".bz2":
        with bz2.open(path,"rb") as inp:return _read_stream(inp)
    if ext==".xz":
        with lzma.open(path,"rb") as inp:return _read_stream(inp)
    return _bsdtar_read(path,name)

def extract_members(archive_path,reg,uid,data_dir,extractor):
    """Return (real, independently readable document records, warnings).

    A record is counted as checked ONLY when its own extracted text is
    nonempty. The archive itself stays stored as one official source file.
    """
    path=Path(archive_path)
    root=Path(data_dir)/"eis223_archive_members"/str(reg)/str(uid)
    records=[]
    warnings=[]
    stats={"files":0,"bytes":0}
    seen=set()

    def visit(current,prefix,depth):
        if depth>MAX_DEPTH:
            warnings.append("Превышена глубина вложенных архивов");return
        try:
            members,notes=_list(current)
        except (OSError,ValueError,RuntimeError,tarfile.TarError,
                zipfile.BadZipFile,subprocess.SubprocessError) as exc:
            warnings.append("Не удалось открыть архив: "+type(exc).__name__);return
        warnings.extend(notes)
        for name,size in members:
            if stats["files"]>=MAX_MEMBERS:
                warnings.append("Лимит числа вложений исчерпан");break
            if not safe_member(name):
                warnings.append("Пропущен небезопасный путь внутри архива");continue
            extension=Path(name).suffix.lower()
            if extension not in DOC_EXT and extension not in ARCHIVE_EXT:continue
            if size>MAX_MEMBER:
                warnings.append("Слишком крупный вложенный документ");continue
            try:
                data=_read_member(current,name)
                if not data or stats["bytes"]+len(data)>MAX_EXPANDED:
                    warnings.append("Превышен допустимый объём распаковки");continue
                stats["files"]+=1
                stats["bytes"]+=len(data)
                sha=hashlib.sha256(data).hexdigest()
                internal=prefix+"/"+name if prefix else name
                logical_key=(internal,sha)
                if logical_key in seen:continue
                seen.add(logical_key)
                folder=root/sha[:2]
                folder.mkdir(parents=True,exist_ok=True,mode=0o700)
                temp_file=folder/(sha+extension)
                # The filename is only a cache key, not proof of its bytes.
                # Restore damaged/replaced entries from the verified archive
                # before extracting text with that archive member's SHA256.
                # Do not follow a cached symlink or read unbounded extra data.
                reusable=False
                try:
                    if not temp_file.is_symlink():
                        with temp_file.open("rb") as cached:
                            previous=cached.read(len(data)+1)
                        reusable=(len(previous)==len(data) and
                                  hashlib.sha256(previous).hexdigest()==sha)
                except OSError:
                    pass
                if not reusable:
                    handle,tmp=tempfile.mkstemp(dir=folder,prefix=".extract-")
                    try:
                        with os.fdopen(handle,"wb") as dest:dest.write(data)
                        os.chmod(tmp,0o600)
                        os.replace(tmp,temp_file)
                    finally:
                        if os.path.exists(tmp):os.unlink(tmp)
                if extension in ARCHIVE_EXT and depth<MAX_DEPTH:
                    visit(temp_file,internal,depth+1)
                    continue
                if extension not in DOC_EXT:continue
                try:
                    if extension in (".txt",".csv"):
                        decoded=data.decode("utf-8-sig","replace")
                        if decoded.count("\ufffd")>len(decoded)*.08:
                            decoded=data.decode("cp1251","replace")
                        text=decoded[:2000000]
                    else:
                        text=str(extractor(temp_file) or "")[:2000000]
                except Exception as exc:
                    warnings.append("Не извлечён текст: "+name[:80]);continue
                if len(text.strip())<3:
                    warnings.append("Нет читаемого текста: "+name[:80]);continue
                records.append({"name":internal,"path":temp_file,"sha256":sha,
                                "text":text,"bytes":len(data)})
            except (OSError,ValueError,KeyError,RuntimeError,tarfile.TarError,
                    zipfile.BadZipFile,subprocess.SubprocessError,EOFError) as exc:
                warnings.append("Ошибка вложения "+name[:70]+": "+type(exc).__name__)
    visit(path,"",0)
    return records,warnings[:30]
