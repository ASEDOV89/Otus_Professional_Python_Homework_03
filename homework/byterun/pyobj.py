"""Implementations of Python fundamental objects for Byterun."""

import collections
import inspect
import types


def make_cell(value):
    """Создает объект ячейки замыкания."""
    fn = (lambda x: lambda: x)(value)
    return fn.__closure__[0]


class Function:
    __slots__ = [
        "func_code",
        "func_name",
        "func_defaults",
        "func_globals",
        "func_locals",
        "func_dict",
        "func_closure",
        "__name__",
        "__dict__",
        "__doc__",
        "_vm",
        "_func",
    ]

    def __init__(self, name, code, globs, defaults, closure, vm):
        self._vm = vm
        self.func_code = code
        self.func_name = self.__name__ = name or code.co_name
        self.func_defaults = tuple(defaults)
        self.func_globals = globs
        self.func_locals = self._vm.frame.f_locals
        self.__dict__ = {}
        self.func_closure = closure
        self.__doc__ = code.co_consts[0] if code.co_consts else None

        kw = {
            "argdefs": self.func_defaults,
        }
        if closure:
            kw["closure"] = tuple(make_cell(0) for _ in closure)
        self._func = types.FunctionType(code, globs, **kw)

    def __repr__(self):
        return f"<Function {self.func_name} at 0x{id(self):08x}>"

    def __get__(self, instance, owner):
        if instance is not None:
            return Method(instance, owner, self)
        else:
            return self

    def __call__(self, *args, **kwargs):
        # Получение аргументов функции
        sig = inspect.signature(self._func)
        bound_args = sig.bind(*args, **kwargs)
        callargs = bound_args.arguments

        frame = self._vm.make_frame(self.func_code, callargs, self.func_globals, {})

        CO_GENERATOR = 32
        if self.func_code.co_flags & CO_GENERATOR:
            gen = Generator(frame, self._vm)
            frame.generator = gen
            retval = gen
        else:
            retval = self._vm.run_frame(frame)

        return retval


class Method:
    def __init__(self, obj, _class, func):
        self.im_self = obj
        self.im_class = _class
        self.im_func = func

    def __repr__(self):
        name = f"{self.im_class.__name__}.{self.im_func.__name__}"
        if self.im_self is not None:
            return f"<Bound Method {name} of {self.im_self}>"
        else:
            return f"<Unbound Method {name}>"

    def __call__(self, *args, **kwargs):
        if self.im_self is not None:
            return self.im_func(self.im_self, *args, **kwargs)
        else:
            return self.im_func(*args, **kwargs)


class Cell:
    def __init__(self, value):
        self.contents = value

    def get(self):
        return self.contents

    def set(self, value):
        self.contents = value


Block = collections.namedtuple("Block", "type handler level")


class Frame(object):
    def __init__(self, f_code, f_globals, f_locals, f_back):
        self.f_code = f_code
        self.f_globals = f_globals
        self.f_locals = f_locals
        self.f_back = f_back
        self.stack = []
        if f_back and f_back.f_globals is f_globals:
            # If we share the globals, we share the builtins.
            self.f_builtins = f_back.f_builtins
        else:
            try:
                self.f_builtins = f_globals["__builtins__"]
                if hasattr(self.f_builtins, "__dict__"):
                    self.f_builtins = self.f_builtins.__dict__
            except KeyError:
                # No builtins! Make up a minimal one with None.
                self.f_builtins = {"None": None}

        self.f_lineno = f_code.co_firstlineno
        self.f_lasti = 0

        self.cells = {}
        if f_code.co_cellvars:
            if not f_back.cells:
                f_back.cells = {}
            for var in f_code.co_cellvars:
                # Make a cell for the variable in our locals, or None.
                cell = Cell(self.f_locals.get(var))
                f_back.cells[var] = self.cells[var] = cell

        if f_code.co_freevars:
            for var in f_code.co_freevars:
                assert f_back.cells, f"f_back.cells: {f_back.cells}"
                self.cells[var] = f_back.cells[var]

        self.block_stack = []
        self.generator = None

    def __repr__(self):
        return f"<Frame at 0x{id(self):08x}: {self.f_code.co_filename}, line {self.f_lineno}>"

    def line_number(self):
        """Get the current line number the frame is executing."""
        # We don't keep f_lineno up to date, so calculate it based on the
        # instruction address and the line number table.
        lnotab = self.f_code.co_lnotab
        byte_increments = lnotab[0::2]
        line_increments = lnotab[1::2]

        byte_num = 0
        line_num = self.f_code.co_firstlineno

        for byte_incr, line_incr in zip(byte_increments, line_increments):
            byte_num += byte_incr
            if byte_num > self.f_lasti:
                break
            line_num += line_incr

        return line_num


class Generator(object):
    def __init__(self, g_frame, vm):
        self.gi_frame = g_frame
        self.vm = vm
        self.started = False
        self.finished = False

    def __iter__(self):
        return self

    def __next__(self):
        return self.send(None)

    def send(self, value=None):
        if not self.started and value is not None:
            raise TypeError("Can't send non-None value to a just-started generator")
        self.gi_frame.stack.append(value)
        self.started = True
        val = self.vm.resume_frame(self.gi_frame)
        if self.finished:
            raise StopIteration(val)
        return val