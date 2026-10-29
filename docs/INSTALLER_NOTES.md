# Installer — why it is Inno Setup and not NSIS

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

**Inno Setup** (updated Oct 2026). Inno Setup streams its payload instead of
memory-mapping it, so it has no ~2 GB ceiling, and it handles the uninstall
entry, shortcuts and folder choice natively.

```
npm run installer
```

which runs `npm run app:build` and then `tools\build_installer.ps1`, which
compiles `tools\installer.iss` with `ISCC.exe` (Inno Setup 6 or 7; install with
`winget install JRSoftware.InnoSetup.7`). The CI workflow and
`build_release.bat` compile the older `installer\EcoVisionSentinel.iss`, which
has no disk spanning and has not been updated for the current payload (below).

## Over 4 GB: disk spanning and a zip (Oct 2026)

Adding Qwen3-VL-2B for the alarm double-check (fp16 for the GPU backend, a
4-bit GGUF for the CPU one) took the compressed payload to ~7.5 GB. Inno
Setup cannot put more than ~4.2 GB in one setup file, so `tools\installer.iss`
sets `DiskSpanning=yes` and `DiskSliceSize=max`: the output is a small
`EcoVisionSentinel-Installer-<version>.exe` plus
`EcoVisionSentinel-Installer-<version>-1.bin`. They must stay in the same
folder, so `build_installer.ps1` zips them (Windows' own
`%SystemRoot%\System32	ar.exe` — Git's `tar` misreads `D:` as a remote host)
into `dist\EcoVisionSentinel-Installer-<version>.zip`. Ship the zip.

## Secrets: no `.env` in the package (fixed 22 Oct)

`next build` with `output: "standalone"` copies the project's `.env` into
`.next\standalone`, and electron-builder packs that folder — so the Oct 12
installer contained this machine's real `.env` (Telegram bot token, Roboflow
key, `SECRET_KEY`, DevTeam bootstrap login). The dashboard only reads
`NEXT_PUBLIC_*` values, which are inlined at build time, so the standalone
server never needed it. Now three layers keep it out:

1. `tools\copy_standalone_assets.js` deletes `.next\standalone\.env*` after every `next build`;
2. `package.json`'s `build.files` excludes `**/.env*`;
3. `build_installer.ps1` step [2c] refuses to compile if any `.env*` file is in `dist\win-unpacked`.

The installed app still writes its own `.env` at first run
(`electron/main.js` `writeGeneratedEnv`, fresh `SECRET_KEY` per install).
Any installer built before 22 Oct should be treated as containing the secrets.

The build gates itself on `preflight.py`, which checks that every model named
in `config.json` is present **and** bundled by `package.json`'s
`extraResources` filter — the failure mode that nearly shipped on 2026-08-22,
when that whitelist still named superseded checkpoints.

An earlier pipeline built a 7-Zip self-extracting `.exe`
(`tools\build_sfx_installer.ps1`, now retired): it was not a real installer,
and the hand-written uninstall and registration scripts it needed each had
their own bug.

## Known limitation

The installer is **unsigned**, so Windows SmartScreen warns on first run
(*More info → Run anyway*). Test this on the demo machine before the day.
