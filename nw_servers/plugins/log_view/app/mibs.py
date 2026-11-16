from __future__ import annotations

import logging
import os
from pathlib import Path
from functools import lru_cache

log = logging.getLogger(__name__)
MIB_DIR = Path(os.getenv("LOG_VIEW_MIB_DIR", "/mibs"))
MIB_CACHE = Path(os.getenv("LOG_VIEW_MIB_CACHE", "/data/mibs-compiled"))


@lru_cache(maxsize=1)
def mib_context():
    try:
        from pysnmp.smi import builder, compiler, rfc1902, view
        MIB_CACHE.mkdir(parents=True, exist_ok=True)
        mib_builder = builder.MibBuilder()
        compiler.add_mib_compiler(
            mib_builder,
            sources=[f"file://{MIB_DIR}"],
            destination=str(MIB_CACHE),
            ifNotAdded=True,
        )
        mib_view = view.MibViewController(mib_builder)
        modules = [p.stem for p in MIB_DIR.iterdir() if p.is_file() and p.suffix.lower() in {".mib", ".txt", ".my"}]
        modules.extend(["SNMPv2-MIB", "IF-MIB"])
        for module in dict.fromkeys(modules):
            try:
                mib_builder.load_modules(module)
            except Exception:
                continue
        return mib_builder, rfc1902, mib_view
    except Exception as exc:
        log.debug("MIB setup failed: %s", exc)
        return None


@lru_cache(maxsize=4096)
def resolve_oid(oid: str) -> str | None:
    if not oid or not MIB_DIR.is_dir():
        return None
    context = mib_context()
    if not context:
        return None
    try:
        _, rfc1902, mib_view = context
        identity = rfc1902.ObjectIdentity(tuple(int(part) for part in oid.strip(".").split(".")))
        identity.resolve_with_mib(mib_view)
        name = identity.prettyPrint()
        return name if name != oid else None
    except Exception as exc:
        log.debug("MIB resolution failed for %s: %s", oid, exc)
        return None


def validate_module(module: str) -> None:
    # Compile the newly uploaded source explicitly. The cached context can
    # otherwise resolve an older compiled module and hide an invalid upload.
    invalidate_module(module)
    context = mib_context()
    if not context:
        raise ValueError("MIB compiler could not be initialized")
    builder, _, _ = context
    try:
        builder.load_modules(module)
    except Exception as exc:
        raise ValueError(str(exc)) from exc


def invalidate_module(module: str) -> None:
    resolve_oid.cache_clear()
    mib_context.cache_clear()
    for compiled in MIB_CACHE.glob(f"{module}.*"):
        if compiled.is_file():
            compiled.unlink(missing_ok=True)
