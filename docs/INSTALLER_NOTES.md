# Installer — why it is a 7-Zip SFX and not NSIS

`package.json`'s `build.win.target` is `dir` only. That is deliberate, and this
file exists because the reason cannot live in `package.json`: electron-builder
validates its `build` object against a strict schema and **rejects unknown keys**,
including comment keys like `_note`. Adding one fails the build outright with
`configuration has an unknown property`.

## What was tried

`nsis` was configured and run twice. Both attempts failed identically:

```
⨯ makensis.exe process failed ERR_ELECTRON_BUILDER_CANNOT_EXECUTE
  File: failed creating mmap of "...ecovision-security-sentinel-1.0.0-x64.nsis.7z"
```

The second attempt used `--prepackaged dist\win-unpacked`, i.e. it skipped
packaging entirely and only built the installer from an already-verified tree.
It failed the same way, which rules out packaging as the cause.

## Why it cannot work here

`makensis.exe` is a 32-bit binary and memory-maps the whole payload archive.
This app's payload is:

| component | size |
|---|---|
| `python-env` (torch + CUDA runtime) | 4.5 GB |
| `app.asar.unpacked` | 533 MB |
| `weights` (5 models + sidecars) | 157 MB |
| **total uncompressed** | **5.9 GB** |
| **compressed (.7z)** | **2.16 GB** |

2.16 GB is past what a 32-bit address space can map. The bulk is torch's CUDA
runtime, which GPU inference genuinely requires — it is not removable bloat.
(The `.lib` import libraries, the one obviously strippable component, are
already excluded by electron-builder.)

**Do not re-add `nsis` without first getting the compressed payload under
roughly 2 GB.** It will fail, and the failure looks like a packaging problem
rather than a size limit.

## What is shipped instead

`tools\build_sfx_installer.ps1` builds a 7-Zip self-extracting `.exe`, which has
no such ceiling and behaves like an installer: it prompts, extracts to a chosen
folder, and launches the app.

```
npm run app:build
powershell -ExecutionPolicy Bypass -File tools\build_sfx_installer.ps1
```

The script gates itself on `preflight.py` and additionally verifies that every
model named in the *packaged* `config.json` is present in the *packaged*
`weights` folder — the failure mode that nearly shipped on 2026-08-22, when
`package.json`'s weights whitelist still named three superseded checkpoints.

## Known limitation

The installer is **unsigned**, so Windows SmartScreen warns on first run
(*More info → Run anyway*). Test this on the demo machine before the day.
