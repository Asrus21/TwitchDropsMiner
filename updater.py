"""
Updates the application from the GitHub releases, from inside the app.

On start (and every few hours after that), the latest release is checked. When it's newer
than the running version, a window shows its changelog and asks whether to download it.
Once downloaded (and checked against the release's SHA-256), a second question asks whether
to install it: the app then closes, a hidden PowerShell process waits for it to exit,
swaps the executable for the new one and starts it again.

Only the packaged Windows executable can replace itself. Any other copy (running from
source, Linux, macOS) gets a window pointing to the releases page instead.
"""

from __future__ import annotations

import os
import re
import sys
import base64
import asyncio
import hashlib
import logging
import subprocess
import webbrowser
from functools import partial
import tkinter as tk
from pathlib import Path
from tkinter import ttk
from typing import Any, TYPE_CHECKING

import aiohttp

from translate import _
from version import __version__
from constants import IS_PACKAGED, SELF_PATH

if TYPE_CHECKING:
    from twitch import Twitch


logger = logging.getLogger("TwitchDrops")

REPOSITORY = "Asrus21/TwitchDropsMiner"
LATEST_RELEASE_URL = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
RELEASES_PAGE_URL = f"https://github.com/{REPOSITORY}/releases/latest"
# the release asset the Windows app replaces itself with
WINDOWS_ASSET_NAME = "TwitchDropsMiner.exe"
CHECK_EVERY = 6 * 3600  # seconds
FIRST_CHECK_DELAY = 10  # let the app finish starting first


def parse_version(text: str) -> tuple[int, ...]:
    """
    "v16.1.0" -> (16, 1, 0); "16.dev" -> (16,); "16.1.0.1a2b3c4" -> (16, 1, 0).

    Only the leading all-digit parts count, so a commit id appended to a test build's
    version never reads as a newer version.
    """
    numbers: list[int] = []
    for part in text.strip().lstrip("vV").split("."):
        if not re.fullmatch(r"\d+", part):
            break
        numbers.append(int(part))
    return tuple(numbers)


def is_newer(latest: str, current: str) -> bool:
    new, old = parse_version(latest), parse_version(current)
    if not new:
        return False
    # pad, so that 16.1 == 16.1.0
    size = max(len(new), len(old))
    return new + (0,) * (size - len(new)) > old + (0,) * (size - len(old))


def can_self_update() -> bool:
    return IS_PACKAGED and sys.platform == "win32" and SELF_PATH.suffix.lower() == ".exe"


def find_asset(release: dict[str, Any]) -> dict[str, Any] | None:
    for asset in release.get("assets") or []:
        if asset.get("name") == WINDOWS_ASSET_NAME:
            return asset
    return None


def notes_to_text(markdown: str) -> str:
    """
    The release notes (Markdown) as plain text for the update window: no "#" headings,
    no ** or ` marks, bullets as "•", and without the install footer after "---".
    """
    text = re.split(r"(?m)^---\s*$", markdown, maxsplit=1)[0]
    lines: list[str] = []
    joinable = False  # whether the next wrapped line continues the previous one
    for raw in text.splitlines():
        line = raw.strip().replace("**", "").replace("`", "")
        if not line:
            lines.append("")
            joinable = False
        elif re.match(r"#{1,6}\s", line):
            lines.append(re.sub(r"^#{1,6}\s*", "", line))
            joinable = False
        elif re.match(r"[-*]\s", line):
            lines.append("• " + line[2:].strip())
            joinable = True
        elif joinable and lines:
            # Markdown wraps long lines; the window wraps them itself
            lines[-1] += " " + line
        else:
            lines.append(line)
            joinable = True
    return "\n".join(lines).strip()


def expected_sha256(asset: dict[str, Any]) -> str | None:
    # GitHub publishes the asset checksum as "sha256:<hex>"
    digest: str = asset.get("digest") or ""
    if digest.startswith("sha256:"):
        return digest.split(":", 1)[1].lower()
    return None


def sha256_of(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1 << 20), b""):
            hasher.update(block)
    return hasher.hexdigest()


def ps_quote(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def install_and_restart(new_exe: Path) -> None:
    """
    Starts a hidden PowerShell that waits for this app to exit, swaps the executable
    and starts the new one. The caller closes the app right after this.
    """
    target = SELF_PATH
    # a one-file executable runs as two processes (the bootloader and Python),
    # and the file stays locked until both are gone
    pids = ",".join(str(pid) for pid in {os.getpid(), os.getppid()})
    script = "\n".join([
        "$ErrorActionPreference = 'SilentlyContinue'",
        f"Wait-Process -Id {pids} -Timeout 120",
        "for ($i = 0; $i -lt 60; $i++) {",
        f"  try {{ Move-Item -LiteralPath {ps_quote(str(new_exe))} "
        f"-Destination {ps_quote(str(target))} -Force -ErrorAction Stop; break }}",
        "  catch { Start-Sleep -Milliseconds 500 }",
        "}",
        f"Start-Process -FilePath {ps_quote(str(target))} "
        f"-WorkingDirectory {ps_quote(str(target.parent))}",
    ])
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    subprocess.Popen(
        [
            "powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
            "-WindowStyle", "Hidden", "-EncodedCommand", encoded,
        ],
        creationflags=flags,
        close_fds=True,
    )


class UpdateWindow:
    """
    A small non-blocking window: the app keeps mining while it's open.
    """

    def __init__(self, root: tk.Misc, title: str):
        self._done: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        self.top = top = tk.Toplevel(root)
        top.title(title)
        top.resizable(True, True)
        top.protocol("WM_DELETE_WINDOW", lambda: self._finish("close"))
        frame = ttk.Frame(top, padding=12)
        frame.pack(fill="both", expand=True)
        self.message = ttk.Label(frame, wraplength=460, justify="left")
        self.message.pack(anchor="w", fill="x")
        self.text = tk.Text(frame, width=64, height=14, wrap="word", relief="solid", borderwidth=1)
        self.buttons = ttk.Frame(frame)
        self.buttons.pack(anchor="e", pady=(10, 0))
        self._frame = frame

    def show_text(self, text: str) -> None:
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.insert("1.0", text.strip())
        self.text.configure(state="disabled")
        self.text.pack(before=self.buttons, fill="both", expand=True, pady=(8, 0))

    def hide_text(self) -> None:
        self.text.pack_forget()

    def set_message(self, text: str) -> None:
        self.message.configure(text=text)

    def set_buttons(self, *buttons: tuple[str, str]) -> None:
        for child in self.buttons.winfo_children():
            child.destroy()
        for label, result in buttons:
            ttk.Button(
                self.buttons, text=label, command=partial(self._finish, result)
            ).pack(side="left", padx=(6, 0))
        self.top.lift()
        self.top.focus_force()

    def _finish(self, result: str) -> None:
        if not self._done.done():
            self._done.set_result(result)

    async def choice(self) -> str:
        result = await self._done
        self._done = asyncio.get_running_loop().create_future()
        return result

    def close(self) -> None:
        try:
            self.top.destroy()
        except tk.TclError:
            pass


class Updater:
    def __init__(self, twitch: Twitch):
        self._twitch = twitch
        # versions the user said "not now" to, in this run of the app
        self._declined: set[str] = set()

    def _session(self) -> aiohttp.ClientSession:
        return aiohttp.ClientSession(
            headers={"User-Agent": f"TwitchDropsMiner/{__version__}"},
            timeout=aiohttp.ClientTimeout(total=None, sock_connect=15, sock_read=60),
        )

    def _proxy(self) -> str | None:
        proxy = self._twitch.settings.proxy
        return str(proxy) if proxy else None

    async def run(self) -> None:
        await asyncio.sleep(FIRST_CHECK_DELAY)
        while True:
            try:
                await self.check()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # never let the update check take the app down
                logger.warning(f"Update check failed: {exc}")
            await asyncio.sleep(CHECK_EVERY)

    async def fetch_latest(self) -> dict[str, Any] | None:
        async with self._session() as session:
            async with session.get(
                LATEST_RELEASE_URL,
                proxy=self._proxy(),
                headers={"Accept": "application/vnd.github+json"},
            ) as response:
                if response.status != 200:
                    logger.info(f"Update check: GitHub answered HTTP {response.status}")
                    return None
                return await response.json(content_type=None)

    async def check(self) -> None:
        if not self._twitch.settings.check_updates:
            return
        release = await self.fetch_latest()
        if release is None or release.get("draft") or release.get("prerelease"):
            return
        latest: str = release.get("tag_name") or ""
        if not is_newer(latest, __version__) or latest in self._declined:
            return
        logger.info(f"Update available: {latest} (running {__version__})")
        await self.offer(release)

    async def offer(self, release: dict[str, Any]) -> None:
        latest: str = release["tag_name"]
        version = latest.lstrip("vV")
        gui = self._twitch.gui
        gui.grab_attention(sound=False)
        window = UpdateWindow(gui._root, _("update", "title"))
        try:
            window.set_message(
                _("update", "available").format(new=version, current=__version__)
                + "\n\n" + _("update", "changes")
            )
            window.show_text(notes_to_text(release.get("body") or ""))
            asset = find_asset(release) if can_self_update() else None
            if asset is None:
                # can't replace itself: point to the releases page
                window.set_message(
                    _("update", "available").format(new=version, current=__version__)
                    + "\n" + _("update", "manual") + "\n\n" + _("update", "changes")
                )
                window.set_buttons(
                    (_("update", "open_page"), "page"), (_("update", "later"), "later")
                )
                if await window.choice() == "page":
                    webbrowser.open(release.get("html_url") or RELEASES_PAGE_URL)
                self._declined.add(latest)
                return
            window.set_buttons((_("update", "download"), "download"), (_("update", "later"), "later"))
            if await window.choice() != "download":
                self._declined.add(latest)
                return
            new_exe = await self.download(asset, window)
            if new_exe is None:
                window.set_buttons((_("update", "later"), "later"))
                await window.choice()
                return
            window.hide_text()
            window.set_message(_("update", "downloaded").format(new=version))
            window.set_buttons((_("update", "install"), "install"), (_("update", "later"), "later"))
            if await window.choice() != "install":
                # the file stays, the next check installs it without downloading again
                self._declined.add(latest)
                return
            window.set_message(_("update", "installing"))
            window.set_buttons()
            self._twitch.print(_("update", "installing"))
            install_and_restart(new_exe)
            gui.close()
        finally:
            window.close()

    async def download(self, asset: dict[str, Any], window: UpdateWindow) -> Path | None:
        target = SELF_PATH.with_name(f"{SELF_PATH.stem}.update.exe")
        expected = expected_sha256(asset)
        # already downloaded earlier (the user said "not now" to installing it)
        if target.exists() and expected is not None and sha256_of(target) == expected:
            return target
        partial = target.with_suffix(".part")
        size: int = asset.get("size") or 0
        window.set_buttons()
        try:
            async with self._session() as session:
                async with session.get(
                    asset["browser_download_url"], proxy=self._proxy()
                ) as response:
                    response.raise_for_status()
                    done = 0
                    with partial.open("wb") as file:
                        async for block in response.content.iter_chunked(1 << 16):
                            file.write(block)
                            done += len(block)
                            percent = f"{done * 100 // size}%" if size else f"{done >> 20} MB"
                            window.set_message(_("update", "downloading").format(percent=percent))
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
            partial.unlink(missing_ok=True)
            window.set_message(_("update", "download_failed").format(error=exc))
            return None
        if expected is not None and sha256_of(partial) != expected:
            partial.unlink(missing_ok=True)
            window.set_message(_("update", "verify_failed"))
            return None
        partial.replace(target)
        return target


def cleanup_leftovers() -> None:
    """
    Removes an interrupted download. A finished one is kept: it's moved into place when
    installed, and reused (after checking it again) if the user installs it later.
    """
    if not can_self_update():
        return
    try:
        SELF_PATH.with_name(f"{SELF_PATH.stem}.update.part").unlink(missing_ok=True)
    except OSError:
        pass
