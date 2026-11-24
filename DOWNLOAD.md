# Boot Tool: downloads

Get the latest version from the **Releases** page of this repository (right-hand side, or the "Releases" link).
Download the file for your computer:

| Computer | File |
|---|---|
| Windows | `BootTool-Windows.zip` |
| Mac with Apple chip (M1, M2, M3, M4…) | `BootTool-Mac-AppleSilicon.zip` |
| Older Mac with Intel chip | `BootTool-Mac-Intel.zip` |
| Linux (64-bit Intel/AMD) | `BootTool-Linux.tar.gz` |

Not sure which Mac you have? Apple menu → About This Mac. "Chip: Apple…" means Apple Silicon; "Processor: Intel…" means Intel.

## Windows
1. Unzip the file (right-click → Extract All).
2. Open the `BootTool` folder and double-click `BootTool.exe`.
3. If Windows says "Windows protected your PC", click **More info → Run anyway**. The app is not code-signed.
4. A black window opens and your browser opens Boot Tool. Keep the black window open while you work; close it to quit.

## Mac
1. Unzip the file (double-click it).
2. **Important, first time only:** macOS blocks downloaded apps that are not signed with a paid Apple account, and may say "Python.framework is damaged". It is not damaged; this one line fixes it.
   - Open **Terminal** (Cmd+Space, type Terminal, press Enter).
   - Type `xattr -cr ` (with a space after it), then **drag the `BootTool` folder from Finder into the Terminal window** and press Enter. Nothing is printed when it works.
3. Open the `BootTool` folder. **Right-click `BootTool` → Open → Open**.
4. A Terminal window opens and your browser opens Boot Tool. Keep the Terminal window open while you work; close it to quit.
5. If macOS still blocks it: System Settings → Privacy & Security → scroll down → **Open Anyway**.

If a window asks to install "command line developer tools", click **Cancel**: you opened the wrong file. Download `BootTool-Mac-AppleSilicon.zip` or `BootTool-Mac-Intel.zip` (not "Source code") from the Releases page.

## Linux
Works on 64-bit Intel/AMD Linux from about 2022 on (Ubuntu 22.04, Debian 12, Fedora 36, Linux Mint 21 or newer).
1. Extract the file (right-click → Extract Here, or `tar -xzf BootTool-Linux.tar.gz`).
2. Open a terminal in the `BootTool` folder and run `./BootTool`.
3. Your browser opens Boot Tool. Keep the terminal open while you work; close it (or press Ctrl+C) to quit.

Your converted graphics are saved in a `BootTool` folder inside your home folder (Library, Prints and Fonts). Everything runs on your own computer; nothing is uploaded.

## Making a new release (for the maintainer)
1. On GitHub, open **Releases** → **Draft a new release**.
2. Under **Choose a tag**, type the new version, such as `v0.9.1`, and pick **Create new tag on publish**.
   It must be a small `v` and then two to four numbers with dots between them: `v0.9.1`, `v1.0` or `v1.0.10`.
   A number can be `0`, but can't start with `0` otherwise. Not `0.9.1` (no `v`), `v1` (one number),
   `v0.09.1` (`09`) or `v0.9.1-beta`.
   The downloads take their version number from this tag, so nothing needs changing in the code first.
   For a test version, still use a plain number and tick **Set as a pre-release**.
3. Click **Generate release notes**, then **Publish release**.
4. The release page appears straight away; the downloads follow when they are built and checked.
   Wait about 15 minutes. The four downloads appear on the new Release. Each app is started once and used
   (open a picture, convert it, make plates) before it is published, so a broken download is never released.

If no downloads have appeared after about 30 minutes, open the **Actions** tab.
- **No Build downloads run for your release at all:** check the tag. Does it start with a small `v` and follow
  the rule above? Did you pick **Create new tag on publish** (only a new tag starts the build)? If you picked
  an existing tag from an earlier release, delete only this new release (never that tag) and make it again
  with a new tag. If the new tag is wrong, fix it as in the last point below. If the tag looks right, ask for
  help before deleting anything.
- **Still running (yellow):** GitHub is busy; wait a bit longer.
- **Cancelled:** GitHub sometimes runs out of Mac machines. Open the run and click **Re-run failed jobs**.
- **Failed (red ❌):** open it and read the message. If it says the tag is not a version number, fix it as
  below. For anything else, click **Re-run failed jobs** once; if it fails again, ask for help before deleting
  anything.
- **To fix a wrong tag you just made (only then):** delete the release, then delete that tag too (deleting a
  release leaves its tag behind): on the **Releases** page open **Tags** and use the delete (trash) button next to
  it. Never delete the tag of an earlier release. Then make the release again with the right tag. Until it's
  fixed, the empty release is visible to everyone.
