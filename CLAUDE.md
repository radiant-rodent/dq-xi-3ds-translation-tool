# Notes for agents working on this repository

## Before every push: regenerate the published patch files

The web patcher (`docs/`, GitHub Pages) serves two generated files that must match the
translation in the repository:

- `docs/patch/DQXI_EN.dq11patch` – browser patch (players patch their own dump)
- `docs/patch/DQXI_EN_layeredfs.zip` – Luma3DS LayeredFS download (the page links to it)

Whenever anything that changes the exported game has changed since they were last built
(translations in the project database, `dq11tool/` export code, fonts, images, fixes,
code caves...), regenerate both **before pushing** and commit them **in the same commit**
as the change:

```
python -m dq11tool.patchfile workspace --site docs/patch
git add docs/patch
```

Both files are deterministic: if the translation did not change, they come out
byte-identical and git shows no change. A diff in them means players get a new version.

If `dq11tool/ctr/build.py`, `dq11tool/ctr/cia.py`, `dq11tool/blz.py`/`native/blz.c`,
`dq11tool/patchfile.py` or `docs/dq11patch.js` changed, also check that the browser patcher
still reproduces the tool's .3ds and .cia byte for byte:

```
python -m dq11tool.patchfile workspace --patch out.dq11patch --3ds reference.3ds
python -c "from dq11tool.ctr.cia import build_cia; build_cia('reference.3ds', 'reference.cia')"
node tests/patcher_test.mjs <Japanese .3ds> out.dq11patch js.3ds reference.3ds js.cia reference.cia
```

## Never commit

- ROMs or extracted game data (`*.3ds`, `*.cia`, `workspace/`), emulator data (`_emutest/`)
- `dq11tool/data/m7_win_p14.bcfnt` and `dq11tool/data/logo_en.png` (copyrighted assets,
  gitignored; see README)
- API keys (`workspace/secrets.json`), personal names, e-mail addresses or local paths

## More

Emulator testing, crash debugging and the engine patches: [docs/AGENT_GUIDE.md](docs/AGENT_GUIDE.md).
