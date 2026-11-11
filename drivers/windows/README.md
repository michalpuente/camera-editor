# Windows virtual camera driver

`UnityCaptureFilter32.dll` and `UnityCaptureFilter64.dll` come from
[Unity Capture](https://github.com/schellingb/UnityCapture) (the capture filter is MIT licensed).
They are not stored in this repository: the Windows build (`.github/workflows/windows.yml`)
fetches them and puts them next to `PresenceCam.exe`.

To run from source on Windows, copy the two DLLs from that project's `Install/` folder here.
PresenceCam registers them under your chosen camera name (`regsvr32 ... /i:UnityCaptureName=<name>`),
so you do not need Unity Capture's own install scripts.
