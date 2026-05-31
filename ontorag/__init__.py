from typing import TYPE_CHECKING, Any

from ._version import __version__ as __version__

__all__ = [
    "OntoRAG",
    "QueryParam",
    "RoleLLMConfig",
    "RoleSpec",
    "ROLES",
    "__version__",
]

if TYPE_CHECKING:
    from .ontorag import (
        OntoRAG as OntoRAG,
        QueryParam as QueryParam,
        ROLES as ROLES,
        RoleLLMConfig as RoleLLMConfig,
        RoleSpec as RoleSpec,
    )


_LAZY_EXPORTS = {"OntoRAG", "QueryParam", "RoleLLMConfig", "RoleSpec", "ROLES"}


def __getattr__(name: str) -> Any:
    if name in _LAZY_EXPORTS:
        from .ontorag import OntoRAG, QueryParam, RoleLLMConfig, RoleSpec, ROLES

        values = {
            "OntoRAG": OntoRAG,
            "QueryParam": QueryParam,
            "RoleLLMConfig": RoleLLMConfig,
            "RoleSpec": RoleSpec,
            "ROLES": ROLES,
        }
        value = values[name]
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__author__ = "Zirui Guo"
__url__ = "https://github.com/machinarii/OntoRAG"
