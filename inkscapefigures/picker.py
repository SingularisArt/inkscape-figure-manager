import subprocess
import platform

SYSTEM_NAME = platform.system()


def get_picker_cmd(picker_args=None, fuzzy=True):
    if SYSTEM_NAME == "Linux":
        args = ["rofi", "-sort", "-no-levenshtein-sort", "-no-fixed-num-lines"]
        if fuzzy:
            args += ["-matching", "fuzzy"]
        args += ["-dmenu", "-p", "Select Figure", "-format", "s", "-i", "-lines", "5"]
    elif SYSTEM_NAME == "Darwin":
        args = ["choose"]
    else:
        raise ValueError("No supported picker for {}".format(SYSTEM_NAME))

    if picker_args is not None:
        args += picker_args

    return [str(arg) for arg in args]


def pick(options, picker_args=None, fuzzy=True):
    optionstr = "\n".join(option.replace("\n", " ") for option in options)
    cmd = get_picker_cmd(picker_args=picker_args, fuzzy=fuzzy)
    result = subprocess.run(
        cmd, input=optionstr, stdout=subprocess.PIPE, universal_newlines=True
    )
    returncode = result.returncode
    stdout = result.stdout.strip()

    selected = stdout.strip()
    try:
        index = [opt.strip() for opt in options].index(selected)
    except ValueError:
        index = -1

    if returncode == 0:
        key = 0
    elif returncode == 1:
        key = -1
    elif returncode > 9:
        key = returncode - 9

    return key, index, selected
