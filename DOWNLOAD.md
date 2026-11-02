# Boot Tool: downloads

Get the latest version from the **Releases** page of this repository (right-hand side, or the "Releases" link).
Download the file for your computer:

| Computer | File |
|---|---|
| Windows | `BootTool-Windows.zip` |
| Mac with Apple chip (M1, M2, M3, M4…) | `BootTool-Mac-AppleSilicon.zip` |
| Older Mac with Intel chip | `BootTool-Mac-Intel.zip` |

Not sure which Mac you have? Apple menu → About This Mac. "Chip: Apple…" means Apple Silicon; "Processor: Intel…" means Intel.

## Windows
1. Unzip the file (right-click → Extract All).
2. Open the `BootTool` folder and double-click `BootTool.exe`.
3. If Windows says "Windows protected your PC", click **More info → Run anyway**. The app is not code-signed.
4. A black window opens and your browser opens Boot Tool. Keep the black window open while you work; close it to quit.

## Mac
1. Unzip the file (double-click it).
2. Open the `BootTool` folder. **Right-click `BootTool` → Open → Open** (the first time only; the app is not code-signed, so a plain double-click is refused).
3. A Terminal window opens and your browser opens Boot Tool. Keep the Terminal window open while you work; close it to quit.
4. If macOS still blocks it: System Settings → Privacy & Security → scroll down → **Open Anyway**.

Your converted graphics are saved in a `BootTool` folder inside your home folder (Library, Prints and Fonts). Everything runs on your own computer; nothing is uploaded.

## Making a new release (for the maintainer)
1. Change the version in `pyproject.toml` and `boottool/__init__.py`.
2. Commit, then `git tag v0.8.3` and `git push origin v0.8.3` (use your version number).
3. Wait about 15 minutes. The three downloads appear on the new Release.
