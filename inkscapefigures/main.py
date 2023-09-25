#!/usr/bin/env python3

import pyperclip
import click
import platform
import os
import re
import logging
import subprocess
import textwrap
import warnings

from appdirs import user_config_dir
from pathlib import Path
from shutil import copy
from daemonize import Daemonize

from .picker import pick

logging.basicConfig(level=os.environ.get("LOGLEVEL", "INFO"))
log = logging.getLogger("inkscape-figures")


def inkscape(path):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", ResourceWarning)
        subprocess.Popen(["inkscape", str(path)])


def indent(text, indentation=0):
    lines = text.split("\n")
    return "\n".join(" " * indentation + line for line in lines)


def beautify(name):
    return name.replace("_", " ").replace("-", " ").title()


def latex_template(name, title):
    label = title.replace("-", "_").replace(" ", "_").lower()
    title = title.replace("-", " ").replace("_", " ").title()

    return "\n".join(
        (
            r"\begin{figure}[ht]",
            r"    \centering",
            rf"    \incfig{{{name}}}",
            rf"    \caption{{{title}}}",
            rf"    \label{{fig:{label}}}",
            r"\end{figure}",
        )
    )


def import_file(name, path):
    import importlib.util as util

    spec = util.spec_from_file_location(name, path)
    module = util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


user_dir = Path(user_config_dir("inkscape-figures", "Castel"))

if not user_dir.is_dir():
    user_dir.mkdir()

roots_file = user_dir / "roots"
template = user_dir / "template.svg"
config = user_dir / "config.py"

if not roots_file.is_file():
    roots_file.touch()

if not template.is_file():
    source = str(Path(__file__).parent / "template.svg")
    destination = str(template)
    copy(source, destination)

if config.exists():
    config_module = import_file("config", config)
    latex_template = config_module.latex_template


def add_root(path):
    path = str(path)
    roots = get_roots()
    if path in roots:
        return None

    roots.append(path)
    roots_file.write_text("\n".join(roots))


def get_roots():
    return [root for root in roots_file.read_text().split("\n") if root != ""]


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
        watcher_cmd = watch_daemon_inotify
    else:
        watcher_cmd = watch_daemon_fswatch

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


def maybe_recompile_figure(filepath):
    filepath = Path(filepath)
    if filepath.suffix != ".svg":
        log.debug(
            "File has changed, but is nog an svg {}".format(filepath.suffix),
        )
        return

    log.info("Recompiling %s", filepath)

    pdf_path = filepath.parent / (filepath.stem + ".pdf")
    name = filepath.stem

    inkscape_version = subprocess.check_output(
        ["inkscape", "--version"], universal_newlines=True
    )
    log.debug(inkscape_version)

    inkscape_version = re.findall(r"[0-9.]+", inkscape_version)[0]
    inkscape_version_number = [
        int(part)
        for part in inkscape_version.split(
            ".",
        )
    ]

    inkscape_version_number = inkscape_version_number + [0] * (
        3 - len(inkscape_version_number)
    )

    if inkscape_version_number < [1, 0, 0]:
        command = [
            "inkscape",
            "--export-area-page",
            "--export-dpi",
            "300",
            "--export-pdf",
            pdf_path,
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
            pdf_path,
        ]

    log.debug("Running command:")
    log.debug(textwrap.indent(" ".join(str(e) for e in command), "    "))

    completed_process = subprocess.run(command)

    if completed_process.returncode != 0:
        log.error("Return code %s", completed_process.returncode)
    else:
        log.debug("Command succeeded")

    template = latex_template(name, beautify(name))
    pyperclip.copy(template)
    log.debug("Copying LaTeX template:")
    log.debug(textwrap.indent(template, "    "))


def watch_daemon_inotify():
    import inotify.adapters
    from inotify.constants import IN_CLOSE_WRITE

    while True:
        roots = get_roots()

        i = inotify.adapters.Inotify()
        i.add_watch(str(roots_file), mask=IN_CLOSE_WRITE)

        log.info("Watching directories: " + ", ".join(get_roots()))
        for root in roots:
            try:
                i.add_watch(root, mask=IN_CLOSE_WRITE)
            except Exception:
                log.debug("Could not add root %s", root)

        for event in i.event_gen(yield_nones=False):
            (_, type_names, path, filename) = event

            if path == str(roots_file):
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
            maybe_recompile_figure(path)


def watch_daemon_fswatch():
    while True:
        roots = get_roots()
        log.info("Watching directories: " + ", ".join(roots))
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", ResourceWarning)
            p = subprocess.Popen(
                ["fswatch", *roots, str(user_dir)],
                stdout=subprocess.PIPE,
                universal_newlines=True,
            )

        while True:
            filepath = p.stdout.readline().strip()

            if filepath == str(roots_file):
                log.info("The roots file has been updated. Updating watches.")
                p.terminate()
                log.debug("Removed main watch %s")
                break
            maybe_recompile_figure(filepath)


@cli.command()
@click.argument("title")
@click.argument(
    "root",
    default=os.getcwd(),
    type=click.Path(exists=False, file_okay=False, dir_okay=True),
)
def create(title, root):
    title = title.strip()
    file_name = title.replace(" ", "-").lower() + ".svg"
    figures = Path(root).absolute()
    if not figures.exists():
        figures.mkdir()

    figure_path = figures / file_name

    if figure_path.exists():
        print(title + " 2")
        return

    copy(str(template), str(figure_path))
    add_root(figures)
    inkscape(figure_path)

    leading_spaces = len(title) - len(title.lstrip())
    print(indent(latex_template(figure_path.stem, title), indentation=leading_spaces))


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
    _, index, selected = pick(names)
    if selected:
        path = files[index]
        add_root(figures)
        inkscape(path)

        template = latex_template(path.stem, beautify(path.stem))
        pyperclip.copy(template)
        log.debug("Copying LaTeX template:")
        log.debug(textwrap.indent(template, "    "))


if __name__ == "__main__":
    cli()
