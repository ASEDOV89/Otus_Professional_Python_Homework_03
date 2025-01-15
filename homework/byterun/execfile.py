"""Execute files of Python code."""

import importlib.util
import io
import os
import sys
import tokenize
import types

from .pyvm2 import VirtualMachine


def open_source(fname):
    """Open a source file the best way."""
    with open(fname, "rb") as file:
        encoding, _ = tokenize.detect_encoding(file.readline)
    return io.open(fname, "r", encoding=encoding)


NoSource = Exception


def exec_code_object(code, env):
    vm = VirtualMachine()
    vm.run_code(code, f_globals=env)


import builtins

BUILTINS = builtins


def run_python_module(modulename, args):
    """Run a python module, as though with ``python -m name args...``.

    `modulename` is the name of the module, possibly a dot-separated name.
    `args` is the argument array to present as sys.argv, including the first
    element naming the module being executed.

    """
    glo, loc = globals(), locals()
    try:
        # Find the module spec
        spec = importlib.util.find_spec(modulename)
        if spec is None:
            raise NoSource("No module named %r" % modulename)
        if spec.submodule_search_locations is not None:
            # It's a package, find the __main__ module
            main_name = "__main__"
            spec = importlib.util.find_spec(main_name, package=modulename)
            if spec is None or spec.origin is None:
                raise NoSource(
                    "No module named %r in package %r" % (main_name, modulename)
                )
            pathname = spec.origin
            packagename = modulename
        else:
            packagename = None
            pathname = spec.origin
        if pathname is None:
            raise NoSource("module does not live in a file: %r" % modulename)
    except ImportError as err:
        raise NoSource(str(err))

    # Finally, hand the file off to run_python_file for execution.
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

    # Set sys.argv and the first path element properly.
    old_argv = sys.argv.copy()
    old_path0 = sys.path[0]
    sys.argv = args
    if package:
        sys.path[0] = ""
    else:
        sys.path[0] = os.path.abspath(os.path.dirname(filename))

    try:
        # Open the source file.
        try:
            source_file = open_source(filename)
        except IOError:
            raise NoSource("No file to run: %r" % filename)

        try:
            source = source_file.read()
        finally:
            source_file.close()

        # We have the source.  `compile` still needs the last line to be clean,
        # so make sure it is, then compile a code object from it.
        if not source or source[-1] != "\n":
            source += "\n"
        code = compile(source, filename, "exec")

        # Execute the source file.
        exec_code_object(code, main_mod.__dict__)
    finally:
        # Restore the old __main__
        sys.modules["__main__"] = old_main_mod

        # Restore the old argv and path
        sys.argv = old_argv
        sys.path[0] = old_path0
