"""Include TenderGuru numbers in the existing DaMIA + EIS discovery queue."""
from pathlib import Path
import hashlib
OLD="""                                    fresh.append(row)
                        except Exception as exc:
                            print('V280 FEED_BRIDGE_ERROR '+type(exc).__name__,flush=True)"""
NEW="""                                    fresh.append(row)
                            # Independent TenderGuru feed; preserve DaMIA results and deduplicate.
                            try:
                                tg_rows=await _v281_tenderguru_discovery.pull()
                                for tg in tg_rows:
                                    reg=str(tg.get('regNumber') or '')
                                    if reg and reg not in known:
                                        known.add(reg)
                                        fresh.append(tg)
                            except Exception as tg_exc:
                                print('V281 TENDERGURU_BRIDGE_ERROR '+type(tg_exc).__name__,flush=True)
                        except Exception as exc:
                            print('V280 FEED_BRIDGE_ERROR '+type(exc).__name__,flush=True)"""
def patch(path):
    path=Path(path).resolve(strict=True)
    if str(path).startswith('/data/') or '/_eis223_runtime_recovery/' in str(path):raise RuntimeError('Protected')
    before=path.read_text(encoding='utf-8')
    if before.count(OLD)!=1:raise RuntimeError('Unexpected discovery source')
    after=before.replace(OLD,NEW,1)
    after+="\nimport v281_tenderguru_discovery as _v281_tenderguru_discovery\n"
    compile(after,str(path),'exec')
    path.write_text(after,encoding='utf-8')
    return hashlib.sha256(after.encode()).hexdigest()
