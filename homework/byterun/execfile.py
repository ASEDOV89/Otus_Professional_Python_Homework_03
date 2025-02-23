"""Execute files of Python code."""

import importlib.util
import io
import os
import sys
import tokenize
import types
import builtins

from .pyvm2 import VirtualMachine


def open_source(fname):
    """Open a source file the best way."""
    try:
        with tokenize.open(fname) as file:
            return file
    except (ImportError, FileNotFoundError, IOError):
        try:
            with open(fname, "rb") as file:
                encoding, _ = tokenize.detect_encoding(file.readline)
            return io.open(fname, "r", encoding=encoding)
        except (FileNotFoundError, IOError) as e:
            print(f"Error opening file {fname}: {e}")
            raise

NoSource = Exception


def exec_code_object(code, env):
    vm = VirtualMachine()
    vm.run_code(code, f_globals=env)

BUILTINS = builtins


def run_python_module(modulename, args):
    """Run a python module, as though with ``python -m name args...``.

    `modulename` is the name of the module, possibly a dot-separated name.
    `args` is the argument array to present as sys.argv, including the first
    element naming the module being executed.

    """
    try:
        spec = importlib.util.find_spec(modulename)
        if spec is None:
            raise NoSource(f"No module named {modulename!r}")
        if spec.submodule_search_locations is not None:
            main_name = "__main__"
            spec = importlib.util.find_spec(main_name, package=modulename)
            if spec is None or spec.origin is None:
                raise NoSource(
                    f"No module named {main_name!r} in package {modulename!r}"
                )
            pathname = spec.origin
            packagename = modulename
        else:
            packagename = None
            pathname = spec.origin
        if pathname is None:
            raise NoSource(f"module does not live in a file: {modulename!r}")
    except ImportError as err:
        raise NoSource(str(err))

    args[0] = pathname
    run_python_file(pathname, args, package=packagename)


def run_python_file(filename, args, package=None):
    """Run a python file as if it were the main program on the command line.

    `filename` is the path to the file to execute, it need not be a .py file.
    `args` is the argument array to present as sys.argv, including the first
    element naming the file being executed.  `package` is the name of the
    enclosing package, if any.

    """
    # Create a module to serve as __main__
    old_main_mod = sys.modules["__main__"]
    main_mod = types.ModuleType("__main__")
    sys.modules["__main__"] = main_mod
    main_mod.__file__ = filename
    if package:
        main_mod.__package__ = package
    main_mod.__builtins__ = BUILTINS

    old_argv = sys.argv.copy()
    old_path0 = sys.path[0]
    sys.argv = args
    if package:
        sys.path[0] = ""
    else:
        sys.path[0] = os.path.abspath(os.path.dirname(filename))

    try:
        try:
            source_file = open_source(filename)
        except IOError:
            raise NoSource(f"No file to run: {filename!r}")

        try:
            source = source_file.read()
        finally:
            source_file.close()

        if not source or source[-1] != "\n":
            source += "\n"
        code = compile(source, filename, "exec")

        exec_code_object(code, main_mod.__dict__)
    finally:
        sys.modules["__main__"] = old_main_mod

        sys.argv = old_argv
        sys.path[0] = old_path0
