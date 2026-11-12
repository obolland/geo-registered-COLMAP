# Slice 0 review (commit 90ece8f, branch pipeline/track1)

Verdict: no blockers. Every bullet of the spec is met and the "Done when" holds on a fresh clone. Two should-fix items in .gitignore, plus nits.

Verified on a fresh clone (/private/tmp/claude-501/s0rev), following the README exactly:
- `uv sync` OK (Python 3.12, pycolmap==4.2.1 installed, `import pycolmap` reports 4.2.1).
- `uv run pytest`: 1 passed. `uv run ruff check .`: All checks passed. `uv run ruff format --check .`: 24 files already formatted.
- `uv run georoute --help` prints usage; `uv run georoute --version` prints `georoute 0.1.0`; `python -m georoute --version` exits 0.
- `uv build` succeeds (sdist and wheel). `colmap --version` here is 4.2.1; exiftool present.
- Spec bullets: pyproject/uv/3.12/numpy/pycolmap 4.2.1/pytest/ruff/uv.lock: met. src/georoute, tests/, tests/fixtures/ (.gitkeep), CLI entry point: met. Broken .gitignore line fixed (old `*.pyzwzwzw.DS_Store` run-on): met. JS duplicate, package.json, package-lock.json, run_nerfstudio_colmap.sh deleted: met, and no dangling references remain (grep clean). README has set-up, system deps, pointers to docs and research: met.
- Nothing removed that should not have been: docs/, research/ and the surviving Python and shell scripts under align_colmap_to_gpx_enu/ are intact (`git ls-tree` before and after).

## Should-fix

1. `!tests/fixtures/**` un-ignores junk under fixtures. Evidence (touched in the clone, `git status --short -uall`):
   `?? tests/fixtures/.DS_Store`, `?? tests/fixtures/a/.pytest_cache`, `?? tests/fixtures/a/__pycache__/x.pyc` all show as untracked (would be committed by `git add .`). The negation does work for the intended files (`check-ignore` attributes `tests/fixtures/a/x.JPG`, `x.bin`, `s.ubx` to the `!` rule), but it also overrides the directory and junk rules. Fix: put the negation before nothing else but add re-ignores after it, e.g.
   ```
   !tests/fixtures/**
   tests/fixtures/**/__pycache__/
   tests/fixtures/**/.DS_Store
   ```
   or narrow it to the data types that matter: `!tests/fixtures/**/*.ubx`, `!tests/fixtures/**/*.bin`, `!tests/fixtures/**/*.jpg`, `!tests/fixtures/**/*.JPG`.
2. Dataset and output coverage is thin. Only `*.ubx *.bin *.JPG *.jpg datasets/ outputs/` are ignored. Verified NOT ignored: `x.png`, `x.CR3`, `x.ply`, `.env`. Also missing: `.jpeg/.JPEG/.png`, Canon raw (`*.CR2 *.CR3`), `*.HEIC`, `*.mp4/*.mov`, COLMAP `*.db`, splat/point-cloud outputs (`*.ply *.splat *.spz *.sog`), `.env`. The pipeline's real outputs (COLMAP sparse models, ply, path_cam/tile manifests) are the likely accidental commits. Fix: add these patterns (the fixtures negation then covers the intentionally committed ones), or ignore a single `data/` and `runs/` root plus document it in the README.

## Nits

3. `.gitignore` ignores `dist/`? It does not need to: `uv build` writes `dist/.gitignore` containing `*`, so it is self-ignored. No action; noted so it is not re-reported.
4. `*.bin` ignores every `.bin` repo-wide (including any future legitimate small binary, e.g. docs assets) and `datasets/`/`outputs/` match at any depth. Anchor to the root (`/datasets/`, `/outputs/`) unless nested matches are wanted.
5. README says "COLMAP 4.2.1 or later" while `pycolmap==4.2.1` is an exact pin and the README itself says they are pinned to the same version. Say "COLMAP 4.2.1" (`brew install colmap` currently gives 4.2.1, but a later brew version would silently diverge). Optionally add `colmap --version` as a check.
6. README no longer documents the legacy scripts' usage (old README deleted wholesale, 335 lines). The scripts that remain until slice 7 (`generate_path_cam_from_aligned_colmap.py`, `generate_tile_manifest.py`) lose their only run docs; their own docstrings carry usage, so this is acceptable, but the commit message could say it.
7. `[tool.ruff] extend-exclude = ["align_colmap_to_gpx_enu"]` means "ruff check passes" is true only because legacy code is skipped. This is a reasonable, commented choice; remove the exclude in slice 7 (or slice 1 for what remains) - worth a TODO in the plan.
8. `numpy>=2.1` and `ruff>=0.8` are loose floors, but `uv.lock` pins actuals (ruff 0.17.5, pytest 9.2.0), so reproducibility is fine. `build-system` pins `uv_build>=0.12.5,<0.13.0`, which matches the installed uv 0.12.5 but will need a bump with new uv minors.
9. `tests/test_cli.py` only covers `--version` via subprocess `-m georoute`; the `georoute` console-script entry point (`georoute.cli:main`) and `--help` are verified here by hand only. A unit test calling `main(["--version"])` with `pytest.raises(SystemExit)` and capsys would cover the in-process path cheaply. `main()` returns 0 and `__main__` wraps it with `sys.exit`, which is correct.
10. `py.typed` ships but there is no type checker configured (mypy/pyright); harmless.
