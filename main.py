#!/usr/bin/env python3

import logging
import os
import platform
import re
import subprocess
import textwrap
import warnings
from pathlib import Path
from shutil import copy
import yaml

import click
import pyperclip
from appdirs import user_config_dir
from daemonize import Daemonize


logging.basicConfig(level=os.environ.get("LOGLEVEL", "INFO"))
log = logging.getLogger("inkscape-figures")


def select(prompt, options, rofi_args=[], fuzzy=True):
    optionstr = "\n".join(option.replace("\n", " ") for option in options)

    args = ["rofi", "-markup"]

    if fuzzy:
        args += ["-matching", "fuzzy"]

    args += ["-dmenu", "-p", prompt, "-format", "s", "-i"]
    args += rofi_args
    args = [str(arg) for arg in args]

    result = subprocess.run(
        args, input=optionstr, stdout=subprocess.PIPE, universal_newlines=True
    )

    returncode = result.returncode
    stdout = result.stdout.strip()

    selected = stdout.strip()

    try:
        index = [opt.strip() for opt in options].index(selected)
    except ValueError:
        index = -1

    if returncode == 0:
        code = 0
    if returncode == 1:
        code = -1
    if returncode > 9:
        code = returncode - 9
    else:
        code = -1

    return code, index, selected


def inkscape(path):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", ResourceWarning)
        subprocess.Popen(["inkscape", str(path)])


def indent(text, indentation=0):
    lines = text.split("\n")
    return "\n".join(" " * indentation + line for line in lines)


def beautify(name):
    return name.replace("_", " ").replace("-", " ").title()


def latexTemplate(name, caption):
    label = caption.replace("-", "_").replace(" ", "_").lower()
    caption = caption.replace("-", " ").replace("_", " ").title()

    return "\n".join(
        (
            r"\begin{figure}[H]",
            r"    \centering",
            "",
            rf"    \incfig{{{name}}}",
            "",
            rf"    \caption{{{caption}}}",
            rf"    \label{{fig:{label}}}",
            r"\end{figure}",
        )
    )


def importFile(name, path):
    import importlib.util as util

    spec = util.spec_from_file_location(name, path)

    if not spec:
        return
    if not spec.loader:
        return

    module = util.module_from_spec(spec)

    spec.loader.exec_module(module)
    return module


userDir = Path(user_config_dir("lesson-manager"))

if not userDir.is_dir():
    userDir.mkdir()

rootsFile = userDir / "roots"
configFile = userDir / "config.yaml"
template = userDir / "template.svg"
configFile = yaml.safe_load(configFile.read_text())
currentCourseDir = Path(configFile["current_course"])

# Create the roots file if it does not exist
if not rootsFile.is_file():
    rootsFile.touch()

# Check if there is a template file for the current course
if currentCourseDir.is_file():
    template = Path(str(currentCourseDir) + "/figures/template.svg")

# If the template file does not exist, copy the default template
# to the current course directory
if not template.is_file():
    source = str(Path(__file__).parent / "template.svg")
    destination = str(template)
    copy(source, destination)


def addRoot(path):
    path = str(path)
    roots = getRoots()
    if path in roots:
        return None

    roots.append(path)
    rootsFile.write_text("\n".join(roots))


def getRoots():
    return [root for root in rootsFile.read_text().split("\n") if root != ""]


@click.group()
def cli():
    pass


@cli.command()
@click.option("--daemon/--no-daemon", default=True)
def watch(daemon):
    """
    Watches for figures.
    """
    if platform.system() == "Linux":
        watcher_cmd = watchDaemonInotify
    else:
        watcher_cmd = watchDaemonFSwatch

    if daemon:
        daemon = Daemonize(
            app="inkscape-figures",
            pid="/tmp/inkscape-figures.pid",
            action=watcher_cmd,
        )
        daemon.start()
        log.info("Watching figures.")
    else:
        log.info("Watching figures.")
        watcher_cmd()


def maybeRecompileFigure(filepath):
    filepath = Path(filepath)
    if filepath.suffix != ".svg":
        log.debug(
            "File has changed, but is nog an svg {}".format(filepath.suffix),
        )
        return

    log.info("Recompiling %s", filepath)

    pdfPath = filepath.parent / (filepath.stem + ".pdf")
    name = filepath.stem

    inkscapeVersion = subprocess.check_output(
        ["inkscape", "--version"], universal_newlines=True
    )
    log.debug(inkscapeVersion)

    inkscapeVersion = re.findall(r"[0-9.]+", inkscapeVersion)[0]
    inkscapeVersionNumber = [
        int(part)
        for part in inkscapeVersion.split(
            ".",
        )
    ]

    inkscapeVersionNumber = inkscapeVersionNumber + [0] * (
        3 - len(inkscapeVersionNumber)
    )

    if inkscapeVersionNumber < [1, 0, 0]:
        command = [
            "inkscape",
            "--export-area-page",
            "--export-dpi",
            "300",
            "--export-pdf",
            pdfPath,
            "--export-latex",
            filepath,
        ]
    else:
        command = [
            "inkscape",
            filepath,
            "--export-area-page",
            "--export-dpi",
            "300",
            "--export-type=pdf",
            "--export-latex",
            "--export-filename",
            pdfPath,
        ]

    log.debug("Running command:")
    log.debug(textwrap.indent(" ".join(str(e) for e in command), "  "))

    completedProcess = subprocess.run(command)

    if completedProcess.returncode != 0:
        log.error("Return code %s", completedProcess.returncode)
    else:
        log.debug("Command succeeded")

    template = latexTemplate(name, beautify(name))
    pyperclip.copy(template)
    log.debug("Copying LaTeX template:")
    log.debug(textwrap.indent(template, "    "))


def watchDaemonInotify():
    import inotify.adapters
    from inotify.constants import IN_CLOSE_WRITE

    while True:
        roots = getRoots()

        i = inotify.adapters.Inotify()
        i.add_watch(str(rootsFile), mask=IN_CLOSE_WRITE)

        log.info("Watching directories: " + ", ".join(getRoots()))
        for root in roots:
            try:
                i.add_watch(root, mask=IN_CLOSE_WRITE)
            except Exception:
                log.debug("Could not add root %s", root)

        for event in i.event_gen(yield_nones=False):
            if event is None:
                return

            (_, _, path, filename) = event

            if path == str(rootsFile):
                log.info("The roots file has been updated. Updating watches.")
                for root in roots:
                    try:
                        i.remove_watch(root)
                        log.debug("Removed root %s", root)
                    except Exception:
                        log.debug("Could not remove root %s", root)
                break

            # A file has changed
            path = Path(path) / filename
            maybeRecompileFigure(path)


def watchDaemonFSwatch():
    while True:
        roots = getRoots()
        log.info("Watching directories: " + ", ".join(roots))
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", ResourceWarning)
            p = subprocess.Popen(
                ["fswatch", *roots, str(userDir)],
                stdout=subprocess.PIPE,
                universal_newlines=True,
            )

        while True:
            if not p.stdout:
                return

            filepath = p.stdout.readline().strip()

            if filepath == str(rootsFile):
                log.info("The roots file has been updated. Updating watches.")
                p.terminate()
                log.debug("Removed main watch %s")
                break
            maybeRecompileFigure(filepath)


@cli.command()
@click.argument("title")
@click.argument(
    "root",
    default=os.getcwd(),
    type=click.Path(exists=False, file_okay=False, dir_okay=True),
)
def create(title, root):
    title = title.strip()
    fileName = title.replace(" ", "-").lower() + ".svg"
    figures = Path(root).absolute()
    if not figures.exists():
        figures.mkdir()

    figurePath = figures / fileName

    if figurePath.exists():
        print(title + " 2")
        return

    copy(str(template), str(figurePath))
    addRoot(figures)
    inkscape(figurePath)

    leadingSpaces = len(title) - len(title.lstrip())
    print(
        indent(
            latexTemplate(figurePath.stem, title),
            indentation=leadingSpaces,
        )
    )


@cli.command()
@click.argument(
    "root",
    default=os.getcwd(),
    type=click.Path(exists=True, file_okay=False, dir_okay=True),
)
def edit(root):
    figures = Path(root).absolute()

    files = figures.glob("*.svg")
    files = sorted(files, key=lambda f: f.stat().st_mtime, reverse=True)

    names = [beautify(f.stem) for f in files]
    _, index, selected = select("Select figure", names)
    if selected:
        path = files[index]
        addRoot(figures)
        inkscape(path)

        template = latexTemplate(path.stem, beautify(path.stem))
        pyperclip.copy(template)
        log.debug("Copying LaTeX template:")
        log.debug(textwrap.indent(template, "    "))


if __name__ == "__main__":
    cli()
