"""DirectShow video input names, in the order OpenCV's CAP_DSHOW numbers them (Windows only).

OpenCV opens DirectShow cameras by index only, so the index of a named camera comes from
this list. The COM interfaces are declared here instead of generated from type libraries,
because generated wrappers do not work inside a PyInstaller build.
"""

from __future__ import annotations

from ctypes import POINTER, byref, c_ulong, c_void_p
from ctypes.wintypes import DWORD

import comtypes
from comtypes import COMMETHOD, GUID, HRESULT, IUnknown
from comtypes.automation import VARIANT
from comtypes.persist import IPropertyBag

CLSID_SYSTEM_DEVICE_ENUM = GUID("{62BE5D10-60EB-11D0-BD3B-00A0C911CE86}")
CLSID_VIDEO_INPUT_DEVICE_CATEGORY = GUID("{860BB310-5D01-11D0-BD3B-00A0C911CE86}")


class IMoniker(IUnknown):
    _iid_ = GUID("{0000000F-0000-0000-C000-000000000046}")
    # Only BindToStorage is called; the methods before it keep the vtable layout right.
    _methods_ = [
        COMMETHOD([], HRESULT, "GetClassID", (["in"], c_void_p)),
        COMMETHOD([], HRESULT, "IsDirty"),
        COMMETHOD([], HRESULT, "Load", (["in"], c_void_p)),
        COMMETHOD([], HRESULT, "Save", (["in"], c_void_p), (["in"], c_ulong)),
        COMMETHOD([], HRESULT, "GetSizeMax", (["in"], c_void_p)),
        COMMETHOD([], HRESULT, "BindToObject", (["in"], c_void_p), (["in"], c_void_p), (["in"], c_void_p), (["in"], c_void_p)),
        COMMETHOD(
            [],
            HRESULT,
            "BindToStorage",
            (["in"], c_void_p, "pbc"),
            (["in"], c_void_p, "pmkToLeft"),
            (["in"], POINTER(GUID), "riid"),
            (["out"], POINTER(POINTER(IPropertyBag)), "ppvObj"),
        ),
    ]


class IEnumMoniker(IUnknown):
    _iid_ = GUID("{00000102-0000-0000-C000-000000000046}")
    _methods_ = [
        COMMETHOD([], HRESULT, "Next", (["in"], c_ulong), (["in"], POINTER(POINTER(IMoniker))), (["in"], POINTER(c_ulong))),
    ]


class ICreateDevEnum(IUnknown):
    _iid_ = GUID("{29840822-5B84-11D0-BD3B-00A0C911CE86}")
    _methods_ = [
        COMMETHOD([], HRESULT, "CreateClassEnumerator", (["in"], POINTER(GUID)), (["in"], POINTER(POINTER(IEnumMoniker))), (["in"], DWORD)),
    ]


def video_inputs() -> list[str]:
    comtypes.CoInitialize()
    device_enum = comtypes.CoCreateInstance(CLSID_SYSTEM_DEVICE_ENUM, ICreateDevEnum)
    monikers = POINTER(IEnumMoniker)()
    device_enum.CreateClassEnumerator(byref(CLSID_VIDEO_INPUT_DEVICE_CATEGORY), byref(monikers), 0)
    names: list[str] = []
    if not monikers:  # S_FALSE: no cameras at all
        return names
    while True:
        moniker, fetched = POINTER(IMoniker)(), c_ulong()
        monikers.Next(1, byref(moniker), byref(fetched))
        if not fetched.value:
            return names
        bag = moniker.BindToStorage(None, None, byref(IPropertyBag._iid_))
        names.append(str(bag.Read("FriendlyName", VARIANT(), None)))
