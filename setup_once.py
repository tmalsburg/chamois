#!/usr/bin/env python3
"""One-time setup for the reading experiment. Works on Linux, macOS,
and Windows; uses only the Python standard library.

Run it once on a new machine:

    python3 setup_once.py        (Linux, macOS)
    python setup_once.py         (Windows)

It does four things:

  1. Installs uv, the Python package manager, if it is missing.
  2. Puts the pypixxlib package into vendor/, unless it is already
     there. pypixxlib is proprietary VPixx software and not on PyPI,
     so it cannot be installed by name, and VPixx offers no direct
     download for it either: the official source is the VPixx Software
     Tools installer from https://docs.vpixx.com/download. This script
     streams that installer (~290 MB) and extracts just the pypixxlib
     tarball from it — the installer is only read as an archive, never
     installed or run, so this works on all three platforms.
  3. Installs the dependencies of every demo experiment (pyglet,
     numpy, the vendored pypixxlib, and SR Research's pylink) into the
     environments that `uv run` uses.
  4. Deletes itself when everything worked.

After that, running an experiment is always just:

    uv run demo_experiment_tpx.py        (or another demo script)

No virtual environment ever needs to be activated: `uv run` reads the
header of the experiment script and finds the right environment on
its own.

The module files (chamois.py, TPx.py, EyeLink.py) and the demo scripts
have to sit in the same folder as this script, and the machine needs
internet access.
"""

import os
import shutil
import subprocess
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
# The experiment scripts whose dependencies get installed. Each has
# its own environment, so every one of them is prepared:
SCRIPTS = ["demo_experiment.py", "demo_experiment_tpx.py",
           "demo_experiment_eyelink.py",
           "demo_experiment_eyelink_multiline.py"]
TARGETS = [os.path.join(HERE, name) for name in SCRIPTS]
VENDOR_DIR = os.path.join(HERE, "vendor")
TARBALL_NAME = "pypixxlib-1.12.1.tar.gz"
VENDORED = os.path.join(VENDOR_DIR, TARBALL_NAME)
# pypixxlib is not on PyPI and VPixx offers no direct download for the
# tarball alone; it ships inside the VPixx Software Tools installer.
# This is the address the Linux link on the official download page
# (https://docs.vpixx.com/download) redirects to, pinned to the version
# this experiment was written and tested against:
INSTALLER_URL = ("https://dqpig6o87oure.cloudfront.net/software/"
                 "vpixx-setup-1-12-1.deb")
# Setting the PYPIXXLIB_URL environment variable skips the ~290 MB
# installer download and fetches a pypixxlib tarball directly from
# wherever it points (e.g. a copy on a lab server):
TARBALL_URL = os.environ.get("PYPIXXLIB_URL")
# Where the official uv installer puts the binary. It also adds that
# directory to PATH for every future terminal (by editing the shell
# profile on Linux/macOS, the user environment on Windows) — but not
# for terminals that are already open, so uv is called by its full
# path here and the closing message explains how to activate it in the
# current terminal. ~/.cargo/bin is where older installers put it.
_exe = ".exe" if os.name == "nt" else ""
UV_CANDIDATES = [os.path.expanduser(p + _exe)
                 for p in ("~/.local/bin/uv", "~/.cargo/bin/uv")]


def fail(msg):
    sys.exit(f"\nSETUP FAILED: {msg}\n(This file was NOT deleted — fix the "
             "problem and run setup_once.py again.)")


def find_uv():
    found = shutil.which("uv")
    if found:
        return found
    for path in UV_CANDIDATES:
        if os.path.exists(path):
            return path
    return None


# The extraction step, run by get_tarball() below in a throwaway `uv
# run` environment: the installer's inner archive is zstd-compressed,
# which the standard library cannot read before Python 3.14, so it
# needs the zstandard package. Everything is streamed — neither the
# installer nor its unpacked contents ever touch the disk, only the
# pypixxlib tarball itself is written out.
EXTRACT_HELPER = '''
import os, shutil, sys, tarfile, urllib.request

url, out_path = sys.argv[1], sys.argv[2]


class Progress:
    """Wraps the HTTP response; prints a line every 25 MB and makes
    read(n) return exactly n bytes (an HTTP stream may return less)."""
    def __init__(self, raw):
        self.raw, self.done = raw, 0

    def read(self, n):
        parts = []
        while n > 0:
            chunk = self.raw.read(n)
            if not chunk:
                break
            parts.append(chunk)
            n -= len(chunk)
            step = 25 * 2**20
            if self.done // step != (self.done + len(chunk)) // step:
                print(f"    ... {(self.done + len(chunk)) // 2**20} MB",
                      flush=True)
            self.done += len(chunk)
        return b"".join(parts)


resp = Progress(urllib.request.urlopen(url))

# A .deb is an `ar` archive: a magic line, then members, each with a
# fixed 60-byte header. Skip ahead to the data archive:
if resp.read(8) != b"!<arch>\\n":
    sys.exit("the download is not a .deb package")
while True:
    header = resp.read(60)
    if len(header) < 60:
        sys.exit("no data archive found in the installer")
    name = header[:16].decode("ascii").strip()
    size = int(header[48:58])
    if name.startswith("data.tar"):
        break
    resp.read(size + size % 2)   # skip this member (+ padding byte)

if name.endswith(".zst"):
    try:
        from compression.zstd import ZstdFile   # Python >= 3.14
        stream = ZstdFile(resp)
    except ImportError:
        import zstandard
        stream = zstandard.ZstdDecompressor().stream_reader(resp)
    archive = tarfile.open(fileobj=stream, mode="r|")
else:
    # Older packages use gz/xz, which tarfile handles by itself:
    archive = tarfile.open(fileobj=resp, mode="r|*")

for member in archive:
    base = os.path.basename(member.name)
    if base.startswith("pypixxlib") and base.endswith(".tar.gz"):
        with open(out_path, "wb") as out:
            shutil.copyfileobj(archive.extractfile(member), out)
        print(f"Extracted {member.name}")
        sys.exit(0)
sys.exit("no pypixxlib tarball found in the installer")
'''


def get_tarball(uv):
    """Put pypixxlib into vendor/, unless it is already there: either
    a direct download (PYPIXXLIB_URL is set) or extracted from the
    official VPixx installer."""
    if os.path.exists(VENDORED):
        print(f"pypixxlib already present: {VENDORED}")
        return
    os.makedirs(VENDOR_DIR, exist_ok=True)
    if TARBALL_URL:
        download_tarball_directly()
    else:
        extract_from_installer(uv)
    if not os.path.exists(VENDORED):
        fail("could not obtain pypixxlib. Is the internet reachable? "
             "(Alternatively: download the VPixx Software Tools installer "
             "from https://docs.vpixx.com/download by hand, take "
             f"{TARBALL_NAME} from its pypixxlib folder, place it into "
             "vendor/, and run setup_once.py again. A direct URL to a "
             "tarball can also be given in the PYPIXXLIB_URL environment "
             "variable.)")
    print(f"Saved {VENDORED}")


def download_tarball_directly():
    """PYPIXXLIB_URL points straight at a pypixxlib tarball."""
    print(f"Downloading pypixxlib from:\n    {TARBALL_URL}")
    ok = False
    if shutil.which("curl"):
        ok = subprocess.run(["curl", "-Lf", "-o", VENDORED,
                             TARBALL_URL]).returncode == 0
    elif shutil.which("wget"):
        ok = subprocess.run(["wget", "-O", VENDORED,
                             TARBALL_URL]).returncode == 0
    else:
        # No curl or wget (possible on Windows): download with Python.
        try:
            urllib.request.urlretrieve(TARBALL_URL, VENDORED)
            ok = True
        except Exception as e:
            print(f"Download failed: {e}")
    if not ok and os.path.exists(VENDORED):
        os.remove(VENDORED)   # remove a half-written file


def extract_from_installer(uv):
    """Pull the pypixxlib tarball out of the official VPixx installer
    package (see EXTRACT_HELPER)."""
    print("Fetching pypixxlib from the official VPixx installer "
          "(~290 MB download; only the small\npypixxlib package is "
          "kept):\n    " + INSTALLER_URL, flush=True)
    helper = os.path.join(VENDOR_DIR, "_extract_pypixxlib.py")
    with open(helper, "w", encoding="utf-8") as f:
        f.write(EXTRACT_HELPER)
    try:
        subprocess.run([uv, "run", "--no-project", "--with", "zstandard",
                        "python", helper, INSTALLER_URL, VENDORED])
    finally:
        os.remove(helper)


def install_uv():
    print("uv not found — installing it...")
    if os.name == "nt":
        # The official Windows installer is a PowerShell script:
        installer = ('powershell -ExecutionPolicy ByPass -c '
                     '"irm https://astral.sh/uv/install.ps1 | iex"')
        if subprocess.run(installer, shell=True).returncode == 0:
            return
    else:
        installer = None
        if shutil.which("curl"):
            installer = "curl -LsSf https://astral.sh/uv/install.sh | sh"
        elif shutil.which("wget"):
            installer = "wget -qO- https://astral.sh/uv/install.sh | sh"
        if installer and subprocess.run(installer, shell=True).returncode == 0:
            return
    # Last resort on any platform: pip.
    print("Official installer unavailable — trying pip...")
    if subprocess.run([sys.executable, "-m", "pip", "install",
                       "--user", "uv"]).returncode != 0:
        fail("could not install uv. Install it manually "
             "(https://docs.astral.sh/uv/) and run setup_once.py again.")


def offline_hint():
    """The one-time setting that lets `uv run` work off the network."""
    if os.name == "nt":
        return ('    mkdir "%APPDATA%\\uv" 2>nul\n'
                '    echo offline = true > "%APPDATA%\\uv\\uv.toml"')
    return ("    mkdir -p ~/.config/uv\n"
            "    printf 'offline = true\\n' > ~/.config/uv/uv.toml")


def check_offline_start(uv):
    """Rehearse an unplugged start now, while the network is still up.

    `uv run` re-resolves the script's dependencies at every launch, and
    building the vendored pypixxlib tarball fetches its build backend
    from PyPI. On a machine deliberately kept off the network -- lab
    machines usually are, so that nothing updates itself between
    sessions -- that turns into

        Failed to download `pypixxlib @ file://.../vendor/...`
        Failed to fetch: `https://pypi.org/simple/setuptools/`
        Request failed after 3 retries

    before the experiment window ever opens. Nothing is wrong with
    pypixxlib when that happens: uv just will not fall back to its own
    cache unless it is told to. Repeating the install with --offline
    proves the cache holds everything, and the setting printed at the
    end tells uv to work from that cache from then on.
    """
    print("\nChecking that the experiment can also start without internet...")
    ok = all(subprocess.run([uv, "sync", "--script", target, "--offline"],
                            cwd=HERE).returncode == 0
             for target in TARGETS)
    if ok:
        print("  ...yes: everything it needs is cached on this machine.")
    else:
        print("  ...NO: the cache is incomplete (see the error above). "
              "This machine will\n  only be able to start the experiment "
              "while it is on the network.")
    return ok


def main():
    # The module files must already be here; only the tarball can be
    # fetched.
    for what in SCRIPTS + ["chamois.py", "TPx.py", "EyeLink.py"]:
        if not os.path.exists(os.path.join(HERE, what)):
            fail(f"{what} not found next to setup_once.py — copy the whole "
                 "folder to this machine.")

    uv = find_uv()
    if uv is None:
        install_uv()
        uv = find_uv()
        if uv is None:
            fail("uv was installed but is not on PATH yet. Open a NEW "
                 "terminal and run setup_once.py again.")
    print(f"uv found: {uv}")

    get_tarball(uv)

    print("Installing all dependencies (pyglet, numpy, vendored pypixxlib, pylink)...")
    for target in TARGETS:
        if subprocess.run([uv, "sync", "--script", target],
                          cwd=HERE).returncode != 0:
            fail("dependency installation failed (see the error above; is "
                 "the internet reachable?).")

    offline_ok = check_offline_start(uv)

    if offline_ok:
        print("\n" + "=" * 60)
        print("THIS MACHINE CAN NOW RUN UNPLUGGED. To make that the "
              "default, run this once:\n")
        print(offline_hint())
        print("\nAfter that uv works from its local cache and never "
              "contacts PyPI, so the\nexperiment starts with the network "
              "cable out. (Delete that uv.toml again\nwhenever you plug "
              "the machine back in to update or add a dependency.)")
    print("\n" + "=" * 60)
    print("ALL SET. From now on, run an experiment with:")
    print("\n    uv run demo_experiment_tpx.py        (TRACKPixx)")
    print("    uv run demo_experiment_eyelink.py    (EyeLink)\n")
    if not shutil.which("uv"):
        # uv was installed a moment ago: future terminals will find it
        # (the installer takes care of PATH), only this one does not yet.
        if os.name == "nt":
            print("NOTE: the `uv` command is not known to THIS terminal "
                  "yet — open a new terminal\nbefore running the command "
                  "above.\n")
        else:
            print("NOTE: the `uv` command is not known to THIS terminal "
                  "yet. Open a new terminal,\nor activate it right here "
                  "with:\n\n"
                  '    export PATH="$HOME/.local/bin:$PATH"\n')
    print("=" * 60)

    os.remove(os.path.abspath(__file__))
    print("(setup_once.py has deleted itself — it is no longer needed.)")


if __name__ == "__main__":
    main()
