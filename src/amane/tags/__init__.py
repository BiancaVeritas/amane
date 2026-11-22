"""影片标签映射与演员 / 片商显示名映射."""

from .lookup import VendorNames, load_translations, load_vendor_translations, vendor_names, vendor_roots
from .mapping import (
    LEGACY_VENDOR_FILES,
    MAPPING_FILES,
    MAPPING_GROUPS,
    VENDOR_ROLES,
    ActorMappingStore,
    MappingEntry,
    MappingGroup,
    MappingRegistry,
    MappingStore,
    TagMappingStore,
    VendorMappingStore,
    VendorRole,
    detect_language,
)

__all__ = [
    "LEGACY_VENDOR_FILES",
    "MAPPING_FILES",
    "MAPPING_GROUPS",
    "VENDOR_ROLES",
    "ActorMappingStore",
    "MappingEntry",
    "MappingGroup",
    "MappingRegistry",
    "MappingStore",
    "TagMappingStore",
    "VendorMappingStore",
    "VendorNames",
    "VendorRole",
    "detect_language",
    "load_translations",
    "load_vendor_translations",
    "vendor_names",
    "vendor_roots",
]
