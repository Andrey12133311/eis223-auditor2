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
from collections import OrderedDict
from pathlib import Path
import threading

ARCHIVE_EXT={".zip",".rar",".7z",".tar",".gz",".tgz",".bz2",".xz",".tbz2",".txz",".cab"}
DOC_EXT={".pdf",".docx",".doc",".xlsx",".xlsm",".xls",".odt",".ods",".rtf",".xml",".txt",".csv"}
MAX_MEMBER=12*1024*1024
MAX_EXPANDED=72*1024*1024
MAX_MEMBERS=100
MAX_DEPTH=2

# Repeated legal checks must not repeatedly decompress the same solid archive.
# Only immutable expansion descriptors are retained here, never source bytes,
# text, legal findings, provenance or successful-completion flags.
_EXPANSION_VERSION="eis223-archive-expansion-20261110"
_EXPANSION_CACHE_LIMIT=64
_EXPANSION_CACHE_METADATA_LIMIT=128*1024
_expansion_cache=OrderedDict()
_expansion_guard=threading.Lock()
_expansion_locks=tuple(threading.RLock() for _ in range(32))


def _file_signature(path):
    state=path.stat()
    return (state.st_dev,state.st_ino,state.st_size,state.st_mtime_ns,state.st_ctime_ns)


def _expansion_identity(path,root):
    """Hash current original bytes; names matter for standalone gzip members."""
    before=_file_signature(path)
    with path.open("rb") as stream:
        digest=hashlib.file_digest(stream,"sha256").hexdigest()
    if before!=_file_signature(path):
        return None,None
    return (str(path.absolute()),str(root.absolute()),digest,path.suffix.lower(),
            _EXPANSION_VERSION,MAX_MEMBER,MAX_EXPANDED,MAX_MEMBERS,MAX_DEPTH,
            tuple(sorted(DOC_EXT)),tuple(sorted(ARCHIVE_EXT))),before


def _verified_member_signature(member,digest,size):
    """Verify bounded current bytes and ensure they did not change while read."""
    if member.is_symlink() or not member.is_file() or not 0<size<=MAX_MEMBER:
        return None
    before=_file_signature(member)
    if before[2]!=size:return None
    with member.open("rb") as stream:
        actual=hashlib.sha256()
        total=0
        while chunk:=stream.read(min(65536,size+1-total)):
            actual.update(chunk)
            total+=len(chunk)
            if total>size:return None
    if total!=size or actual.hexdigest()!=digest or before!=_file_signature(member):
        return None
    return before


def _valid_expansion(events,root):
    """A filename/hash in an earlier descriptor never authenticates its bytes."""
    try:
        resolved_root=root.resolve()
        checked=set()
        for event in events:
            if event[0]!="document":continue
            _,_,raw_path,digest,size,_=event
            identity=(raw_path,digest,size)
            if identity in checked:continue
            member=Path(raw_path)
            if (not member.resolve().is_relative_to(resolved_root) or
                _verified_member_signature(member,digest,size) is None):
                return False
            checked.add(identity)
        return True
    except (OSError,ValueError):
        return False


def _expanded_members(path,root):
    try:
        identity,signature=_expansion_identity(path,root)
    except (OSError,ValueError):
        identity=signature=None
    if identity is None:
        return _expand_members(path,root)[0]
    lock=_expansion_locks[int(identity[2][:8],16)%len(_expansion_locks)]
    with lock:
        with _expansion_guard:
            cached=_expansion_cache.get(identity)
        if cached is not None and _valid_expansion(cached,root):
            try:
                unchanged=signature==_file_signature(path)
            except OSError:
                unchanged=False
            if unchanged:
                with _expansion_guard:
                    if identity in _expansion_cache:_expansion_cache.move_to_end(identity)
                return cached
        events,cacheable=_expand_members(path,root)
        try:
            cacheable=cacheable and signature==_file_signature(path)
        except OSError:
            cacheable=False
        # Abnormally large warning inventories remain readable but are not
        # retained. Both entry count and metadata size have fixed bounds.
        metadata_size=sum(len(str(value).encode("utf-8")) for event in events for value in event)
        with _expansion_guard:
            _expansion_cache.pop(identity,None)
            if cacheable and len(events)<=512 and metadata_size<=_EXPANSION_CACHE_METADATA_LIMIT:
                _expansion_cache[identity]=events
                while len(_expansion_cache)>_EXPANSION_CACHE_LIMIT:
                    _expansion_cache.popitem(last=False)
        return events

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
            return [(i.filename,i.file_size,index) for index,i in enumerate(items) if not i.is_dir() and not
                    (i.external_attr>>16 & 0o170000)==0o120000],[]
    if tarfile.is_tarfile(path):
        with tarfile.open(path,"r:*") as t:
            m=t.getmembers()
            if len(m)>MAX_MEMBERS:return [],["Слишком много файлов в TAR"]
            return [(i.name,i.size,index) for index,i in enumerate(m) if i.isfile()],[]
    # RAR/7z/CAB handled by libarchive's audited bsdtar utility.
    if kind in (".gz",".bz2",".xz") and not tarfile.is_tarfile(path):
        return [(path.stem,MAX_MEMBER,None)],[]
    proc=subprocess.run(["bsdtar","-tf",str(path)],capture_output=True,
                        timeout=18,check=False)
    if proc.returncode!=0:return [],["Архив libarchive не прочитан"]
    listing=proc.stdout[:70000].decode("utf-8","replace").splitlines()
    if len(listing)>MAX_MEMBERS:return [],["Слишком много файлов в архиве"]
    return [(name,MAX_MEMBER,None) for name in listing if not name.endswith("/")],[]

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

def _read_member(path,name,entry_index=None):
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as z:
            info=z.infolist()[entry_index] if entry_index is not None else z.getinfo(name)
            if info.filename!=name:raise ValueError("Запись ZIP изменилась после перечисления")
            if (info.file_size>MAX_MEMBER or info.flag_bits&1 or info.is_dir() or
                    (info.external_attr>>16 & 0o170000)==0o120000):
                raise ValueError("Большой, зашифрованный или небезопасный ZIP")
            with z.open(info) as inp:return _read_stream(inp)
    if tarfile.is_tarfile(path):
        with tarfile.open(path,"r:*") as t:
            member=t.getmembers()[entry_index] if entry_index is not None else t.getmember(name)
            if member.name!=name:raise ValueError("Запись TAR изменилась после перечисления")
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

def _expand_members(path,root):
    """Expand real bytes once, retaining the source order of files/warnings."""
    events=[]
    cacheable=True
    stats={"files":0,"bytes":0}
    seen=set()

    def warn(message):
        events.append(("warning",message))

    def visit(current,prefix,depth,ancestry=()):
        nonlocal cacheable
        if depth>MAX_DEPTH:
            warn("Превышена глубина вложенных архивов");return
        try:
            members,notes=_list(current)
        except (OSError,ValueError,RuntimeError,tarfile.TarError,
                zipfile.BadZipFile,subprocess.SubprocessError) as exc:
            cacheable=False
            warn("Не удалось открыть архив: "+type(exc).__name__);return
        for note in notes:
            warn(note)
            if note=="Архив libarchive не прочитан":cacheable=False
        counts={}
        for name,_,_ in members:counts[name]=counts.get(name,0)+1
        for name,size,entry_index in members:
            if stats["files"]>=MAX_MEMBERS:
                warn("Лимит числа вложений исчерпан");break
            if not safe_member(name):
                warn("Пропущен небезопасный путь внутри архива");continue
            extension=Path(name).suffix.lower()
            if extension not in DOC_EXT and extension not in ARCHIVE_EXT:continue
            duplicate=counts[name]>1
            if duplicate:
                # Name-based libarchive extraction cannot prove which occurrence
                # was returned. Never concatenate or invent its source identity.
                if entry_index is None:
                    warn("Повторяющееся имя внутри архива: "+name[:80])
                    continue
                # A resolved ZIP/TAR occurrence is not incomplete coverage.
                # Its displayed source name below carries the disambiguation;
                # a warning would incorrectly force endless collection retry.
            if size>MAX_MEMBER:
                warn("Слишком крупный вложенный документ");continue
            try:
                data=_read_member(current,name,entry_index)
                if not data or stats["bytes"]+len(data)>MAX_EXPANDED:
                    warn("Превышен допустимый объём распаковки");continue
                stats["files"]+=1
                stats["bytes"]+=len(data)
                sha=hashlib.sha256(data).hexdigest()
                # Byte-identical copies at the same source path remain one
                # evidence item. Keep archive boundaries/indexes separate from
                # display paths: a literal root path can resemble a nested one.
                logical_key=(ancestry,name,sha)
                if logical_key in seen:continue
                seen.add(logical_key)
                # Put generated labels before the source filename. A suffix after
                # an explicit event number would make strict legal parsers
                # interpret it as an ambiguous continuation of that number.
                shown=("[запись "+str(entry_index+1)+"] " if duplicate else "")+name
                internal=prefix+"/"+shown if prefix else shown
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
                    visit(temp_file,internal,depth+1,ancestry+((name,entry_index),))
                    continue
                if extension not in DOC_EXT:continue
                events.append(("document",internal,str(temp_file),sha,len(data),name))
            except (OSError,ValueError,KeyError,IndexError,RuntimeError,tarfile.TarError,
                    zipfile.BadZipFile,subprocess.SubprocessError,EOFError) as exc:
                cacheable=False
                warn("Ошибка вложения "+name[:70]+": "+type(exc).__name__)
    visit(path,"",0)
    return tuple(events),cacheable


def extract_members(archive_path,reg,uid,data_dir,extractor):
    """Return (real, independently readable document records, warnings).

    Reuse only unchanged, freshly hash-checked expanded members. The current
    extractor still runs for each document (and owns its text-cache revision),
    including every previously unreadable file. Structural warnings persist;
    a partial archive never becomes complete merely because of a cache hit.
    """
    path=Path(archive_path)
    root=Path(data_dir)/"eis223_archive_members"/str(reg)/str(uid)
    records=[]
    warnings=[]
    for event in _expanded_members(path,root):
        if event[0]=="warning":
            warnings.append(event[1])
            continue
        _,internal,raw_path,sha,size,name=event
        temp_file=Path(raw_path)
        # Keep legacy warning wording (the leaf member's original name) while
        # every successful record retains its full disambiguated source path.
        try:
            if temp_file.suffix.lower() in (".txt",".csv"):
                if temp_file.is_symlink():raise ValueError("Replaced archive member")
                before=_file_signature(temp_file)
                with temp_file.open("rb") as stream:
                    data=_read_stream(stream)
                # Decode exactly the bytes authenticated for this record. A
                # replacement after expansion must not borrow the old SHA256.
                if (len(data)!=size or hashlib.sha256(data).hexdigest()!=sha or
                    before!=_file_signature(temp_file)):
                    raise ValueError("Changed archive member")
                decoded=data.decode("utf-8-sig","replace")
                if decoded.count("\ufffd")>len(decoded)*.08:
                    decoded=data.decode("cp1251","replace")
                text=decoded[:2000000]
            else:
                before=_verified_member_signature(temp_file,sha,size)
                if before is None:raise ValueError("Changed archive member")
                text=str(extractor(temp_file) or "")[:2000000]
                # Parsers open the filename themselves. Bind their output to
                # the same file generation that was hash-checked immediately
                # before extraction, including atomic replacement/in-place edits.
                if temp_file.is_symlink() or before!=_file_signature(temp_file):
                    raise ValueError("Archive member changed during extraction")
        except Exception:
            warnings.append("Не извлечён текст: "+name[:80]);continue
        if len(text.strip())<3:
            warnings.append("Нет читаемого текста: "+name[:80]);continue
        records.append({"name":internal,"path":temp_file,"sha256":sha,
                        "text":text,"bytes":size})
    # Member display names feed the stored URL fragment. Reserve every proposed
    # name first, then disambiguate collisions with nested/literal paths without
    # stealing a genuine member's name. Work is bounded by MAX_MEMBERS.
    names={}
    for record in records:names[record["name"]]=names.get(record["name"],0)+1
    reserved=set(names)
    for index,record in enumerate(records):
        name=record["name"]
        if names[name]<2:continue
        suffix=index+1
        candidate="[источник "+str(suffix)+"] "+name
        while candidate in reserved:
            suffix+=1
            candidate="[источник "+str(suffix)+"] "+name
        reserved.add(candidate)
        record["name"]=candidate
    return records,warnings[:30]
