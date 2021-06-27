# soundcraft/installtool.py - post-install and pre-uninstall commands
#
# Implements post-install (install config files after having installed
# a wheel), and pre-uninstall (uninstall config files before
# uninstalling a wheel).
#
# Copyright (c) 2020 Jim Ramsay <i.am@jimramsay.com>
# Copyright (c) 2020,2021 Hans Ulrich Niedermann <hun@n-dimensional.de>
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.


import argparse
import shutil
import subprocess
import sys
import time
from pathlib import Path
from string import Template


try:
    import gi  # noqa: F401 'gi' imported but unused
except ModuleNotFoundError:
    print(
        """
The PyGI library must be installed from your distribution; usually called
python-gi, python-gobject, python3-gobject, pygobject, or something similar.
"""
    )
    raise

# We only need the whole gobject and GLib thing here to catch specific exceptions
from gi.repository.GLib import Error as GLibError


import pydbus

import soundcraft

import soundcraft.constants as const
from soundcraft.dbus import BUSNAME


def findDataFiles(subdir):
    """Walk through data files in the soundcraft module's ``data`` subdir``"""

    result = {}
    modulepaths = soundcraft.__path__
    for path in modulepaths:
        path = Path(path)
        datapath = path / "data" / subdir
        result[datapath] = []
        for f in datapath.glob("**/*"):
            if f.is_dir():
                continue  # ignore directories
            result[datapath].append(f.relative_to(datapath))
    return result


def serviceExePath():
    return exePath().parent / const.BASE_EXE_SERVICE


def exePath():
    exename = Path(sys.argv[0]).resolve()
    if exename.suffix == ".py":
        raise ValueError(
            "Running installtool out of a module-based execution is not supported"
        )
    return exename


def find_datadir():
    exe_path = exePath()
    print("exe_path", exe_path)
    for prefix in [Path("/usr/local"), Path("/usr"), Path("~/.local").expanduser()]:
        for sx_dir in ["bin", "sbin", "libexec"]:
            for sx in [const.BASE_EXE_INSTALLTOOL]:
                sx_path = prefix / sx_dir / sx
                print("sx_path", sx_path)
                if sx_path == exe_path:
                    return prefix / "share"
                try:
                    exe_path.relative_to(prefix)  # ignore result

                    # If this is
                    # ``/home/user/.local/share/virtualenvs/soundcraft-utils-ABCDEFG/bin/soundcraft_installtool``,
                    # then the D-Bus and XDG config can either go into
                    # ``/home/user/.local/share/virtualenvs/soundcraft-utils-ABCDEFG/share/``
                    # and be ignored, or go into
                    # ``/home/user/.local/share/`` and work. We choose
                    # the latter.
                    return prefix / "share"
                except ValueError:
                    pass  # exe_path is not a subdir of prefix
    raise ValueError(f"Exe path is not supported: {exe_path!r}")


def post_install_dbus():
    datadir = find_datadir()
    templateData = {
        "dbus_service_bin": str(serviceExePath()),
        "busname": BUSNAME,
    }

    sources = findDataFiles("dbus-1")
    for (srcpath, files) in sources.items():
        for f in files:
            src = srcpath / f
            if src.suffix == ".service":
                service_dst = datadir / "dbus-1/services" / f"{BUSNAME}.service"
                print("Installing", service_dst)
                with open(src, "r") as srcfile:
                    srcTemplate = Template(srcfile.read())
                    with open(service_dst, "w") as dstfile:
                        dstfile.write(srcTemplate.substitute(templateData))

    print("Starting D-Bus service as a test")

    bus = pydbus.SessionBus()
    dbus_service = bus.get(".DBus")
    print(f"Installtool version: {const.VERSION}")

    # Give the D-Bus a few seconds to notice the new service file
    timeout = 5
    while True:
        try:
            dbus_service.StartServiceByName(BUSNAME, 0)
            break  # service has been started, no need to try again
        except GLibError:
            # If the bus has not recognized the service config file
            # yet, the service is not bus activatable yet and thus the
            # GLibError will happen.
            if timeout == 0:
                raise
            timeout = timeout - 1

            time.sleep(1)
            continue  # starting service has failed, but try again

    our_service = bus.get(BUSNAME)
    service_version = our_service.version
    print(f"Service     version: {service_version}")

    print("Shutting down session D-Bus service...")
    # As the service should either be running at this time or
    # at the very least be bus activatable, we do not catch
    # any exceptions while shutting it down because we want to
    # see any exceptions if they happen.
    our_service.Shutdown()
    print("Session D-Bus service has been shut down")

    print("D-Bus post-install is complete")
    print(f"Run {const.BASE_EXE_GUI} or {const.BASE_EXE_CLI} as a regular user")


def post_install_xdg():
    datadir = find_datadir()
    print("Using datadir", datadir)
    sources = findDataFiles("xdg")
    for (srcpath, files) in sources.items():
        for f in files:
            src = srcpath / f
            if src.suffix == ".desktop":
                subprocess.run(["xdg-desktop-menu", "install", "--novendor", str(src)])
            elif src.suffix == ".png":
                for size in (16, 24, 32, 48, 256):
                    subprocess.run(
                        [
                            "xdg-icon-resource",
                            "install",
                            "--novendor",
                            "--size",
                            str(size),
                            str(src),
                        ]
                    )
            elif src.suffix == ".svg":
                scalable_icondir = datadir / "icons/hicolor/scalable/apps"
                scalable_icondir.mkdir(parents=True, exist_ok=True)
                shutil.copy(src, scalable_icondir)
    print("Installed all XDG application launcher files")


def post_install():
    post_install_dbus()
    post_install_xdg()


def pre_uninstall_dbus():
    datadir = find_datadir()
    service_dst = datadir / "dbus-1/services" / f"{BUSNAME}.service"

    bus = pydbus.SessionBus()
    dbus_service = bus.get(".DBus")
    if not dbus_service.NameHasOwner(BUSNAME):
        print("Service not running")
    else:
        service = bus.get(BUSNAME)
        service_version = service.version
        print(f"Shutting down service version {service_version}")
        service.Shutdown()
        print("Stopped")

    print(f"Removing {service_dst}")
    try:
        service_dst.unlink()
    except FileNotFoundError:
        pass  # no service file to remove

    print("D-Bus service is unregistered")


def pre_uninstall_xdg():
    datadir = find_datadir()
    print("Using datadir", datadir)
    sources = findDataFiles("xdg")
    for (srcpath, files) in sources.items():
        for f in files:
            print(f"Uninstalling {f.name}")
            if f.suffix == ".desktop":
                subprocess.run(["xdg-desktop-menu", "uninstall", "--novendor", f.name])
            elif f.suffix == ".png":
                for size in (16, 24, 32, 48, 256):
                    subprocess.run(
                        ["xdg-icon-resource", "uninstall", "--size", str(size), f.name]
                    )
            elif f.suffix == ".svg":
                scalable_icondir = datadir / "icons/hicolor/scalable/apps"
                svg = scalable_icondir / f.name
                try:
                    svg.unlink()
                except FileNotFoundError:
                    pass  # svg file not found
    print("Removed all XDG application launcher files")


def pre_uninstall():
    pre_uninstall_dbus()
    pre_uninstall_xdg()


def main():
    parser = argparse.ArgumentParser(
        description=f"hook {const.PACKAGE} into the system post-install (or do the reverse)"
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s ({const.PACKAGE}) {const.VERSION}",
    )

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--post-install",
        help=f"Install and set up {const.PACKAGE} and exit",
        action="store_true",
    )
    group.add_argument(
        "--pre-uninstall",
        help="Undo any installation and setup performed by --post-install and exit",
        action="store_true",
    )

    args = parser.parse_args()
    if args.post_install:
        post_install()
    elif args.pre_uninstall:
        pre_uninstall()
