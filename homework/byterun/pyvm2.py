"""A pure-Python Python bytecode interpreter."""

import dis
import inspect
import linecache
import logging
import operator
import sys
import reprlib
import traceback

from .pyobj import Frame, Block, Function, Generator

log = logging.getLogger(__name__)

repr_obj = reprlib.Repr()
repr_obj.maxother = 120
repper = repr_obj.repr


class VirtualMachineError(Exception):
    """For raising errors in the operation of the VM."""
    pass


class VirtualMachine:
    def __init__(self):
        self.frames = []
        self.frame = None
        self.return_value = None
        self.last_exception = None

    def top(self):
        return self.frame.stack[-1]

    def pop(self, i=0):
        return self.frame.stack.pop(-1 - i)

    def push(self, *vals):
        self.frame.stack.extend(vals)

    def popn(self, n):
        if n:
            ret = self.frame.stack[-n:]
            self.frame.stack[-n:] = []
            return ret
        else:
            return []

    def peek(self, n):
        return self.frame.stack[-n]

    def jump(self, jump):
        self.frame.f_lasti = jump

    def push_block(self, block_type, handler=None, level=None):
        if level is None:
            level = len(self.frame.stack)
        self.frame.block_stack.append(Block(block_type, handler, level))

    def pop_block(self):
        return self.frame.block_stack.pop()

    def make_frame(self, code, callargs={}, f_globals=None, f_locals=None):
        log.info(f"make_frame: code={code}, callargs={repper(callargs)}")
        if f_globals is not None:
            f_globals = f_globals
            if f_locals is None:
                f_locals = f_globals
        elif self.frames:
            f_globals = self.frame.f_globals
            f_locals = {}
        else:
            f_globals = f_locals = {
                "__builtins__": __builtins__,
                "__name__": "__main__",
                "__doc__": None,
                "__package__": None,
            }
        f_locals.update(callargs)
        frame = Frame(code, f_globals, f_locals, self.frame)
        return frame

    def push_frame(self, frame):
        self.frames.append(frame)
        self.frame = frame

    def pop_frame(self):
        self.frames.pop()
        if self.frames:
            self.frame = self.frames[-1]
        else:
            self.frame = None

    def print_frames(self):
        for f in self.frames:
            filename = f.f_code.co_filename
            lineno = f.line_number()
            print(f"File {filename}, line {lineno}, in {f.f_code.co_name}")
            linecache.checkcache(filename)
            line = linecache.getline(filename, lineno, f.f_globals)
            if line:
                print("    " + line.strip())

    def resume_frame(self, frame):
        frame.f_back = self.frame
        val = self.run_frame(frame)
        frame.f_back = None
        return val

    def run_code(self, code, f_globals=None, f_locals=None):
        frame = self.make_frame(code, f_globals=f_globals, f_locals=f_locals)
        val = self.run_frame(frame)
        if self.frames: # pragma: no cover
            raise VirtualMachineError("Frames left over!")
        if self.frame and self.frame.stack: # pragma: no cover
            raise VirtualMachineError(
                f"Data remains on stack! {self.frame.stack}"
            )
        return val

    def unwind_block(self, block):
        if block.type == "except-handler":
            offset = 3
        else:
            offset = 0

        while len(self.frame.stack) > block.level + offset:
            self.pop()

        if block.type == "except-handler":
            tb, value, exctype = self.popn(3)
            self.last_exception = exctype, value, tb

    def parse_byte_and_args(frame, opoffset):
        current_op = frame.f_code.co_code[opoffset]
        byte_code = current_op.opcode
        byte_name = dis.opname[byte_code]

        argument = None

        if byte_code >= dis.HAVE_ARGUMENT:
            arg_bytes = frame.f_code.co_code[opoffset + 1: opoffset + 3]
            argument = arg_bytes[0] + (arg_bytes[1] << 8)

            opoffset += 2
        else:
            opoffset += 1

        return byte_name, argument, opoffset

    # def parse_byte_and_args(self):
    #     f = self.frame
    #     opoffset = f.f_lasti
    #     instruction = f.f_code.co_code[opoffset]
    #     byteName = dis.opname[instruction]
    #     arguments = []
    #     f.f_lasti += 1
    #     if instruction >= dis.HAVE_ARGUMENT:
    #         arg = f.f_code.co_code[f.f_lasti : f.f_lasti + 2]
    #         f.f_lasti += 2
    #         arg_val = arg[0] + (arg[1] << 8)
    #         if instruction in dis.hasconst:
    #             arg = f.f_code.co_consts[arg_val]
    #         elif instruction in dis.hasname:
    #             arg = f.f_code.co_names[arg_val]
    #         elif instruction in dis.hasjrel:
    #             arg = f.f_lasti + arg_val
    #         elif instruction in dis.haslocal:
    #             arg = f.f_code.co_varnames[arg_val]
    #         elif instruction in dis.hasfree:
    #             if arg_val < len(f.f_code.co_cellvars):
    #                 arg = f.f_code.co_cellvars[arg_val]
    #             else:
    #                 var_idx = arg_val - len(f.f_code.co_cellvars)
    #                 arg = f.f_code.co_freevars[var_idx]
    #         else:
    #             arg = arg_val
    #         arguments = [arg]
    #     return byteName, arguments, opoffset

    def log(self, byteName, arguments, opoffset):
        op = f"{opoffset}: {byteName}"
        if arguments:
            op += " {arguments[0]}"
        indent = "    " * (len(self.frames) - 1)
        stack_rep = repper(self.frame.stack)
        block_stack_rep = repper(self.frame.block_stack)

        log.info(f"  {indent}data: {stack_rep}")
        log.info(f"  {indent}blks: {block_stack_rep}")
        log.info(f"{indent}{op}")

    def dispatch(self, byteName, arguments):
        """
        Обработка по имени байта соответствующими методами
        Исключения перехватываются и устанавливаются в виртуальной машине
        """
        why = None
        log.info(f"Executing bytecode: {byteName} with arguments: {arguments}")
        try:
            if byteName in ('RESUME', 'CACHE'):
                return why
            elif byteName.startswith("UNARY_"):
                self.unaryOperator(byteName[6:])
            elif byteName.startswith("BINARY_"):
                self.binaryOperator(byteName[7:])
            elif byteName.startswith("INPLACE_"):
                self.inplaceOperator(byteName[8:])
            elif 'SLICE+' in byteName:
                self.sliceOperator(byteName)
            else:
                bytecode_fn = getattr(self, f"byte_{byteName}", None)
                if not bytecode_fn:  # pragma: no cover
                    raise VirtualMachineError(f"Unknown bytecode type: {byteName}")
                why = bytecode_fn(*arguments)

        except Exception:
            self.last_exception = sys.exc_info()[:2] + (None,)
            log.exception("Caught exception during execution")
            why = "exception"

        return why

    def manage_block_stack(self, why):
        assert why != "yield"
        block = self.frame.block_stack[-1]
        if block.type == "loop" and why == "continue":
            self.jump(self.return_value)
            why = None
            return why

        self.pop_block()
        self.unwind_block(block)

        if block.type == "loop" and why == "break":
            why = None
            self.jump(block.handler)
            return why

        if why == "exception" and block.type in ["setup-except", "finally"]:
            self.push_block("except-handler")
            exctype, value, tb = self.last_exception
            self.push(tb, value, exctype)
            why = None
            self.jump(block.handler)
            return why

        elif block.type == "finally":
            if why in ("return", "continue"):
                self.push(self.return_value)
            self.push(why)
            why = None
            self.jump(block.handler)
            return why

        return why

    def run_frame(frame, self):
        why = None
        tb = None
        exc = None

        while True:
            try:
                result = frame.f_code.co_func(*frame.f_locals.values())
                why = 'return'
                break
            except Exception as e:
                tb = e.__traceback__
                exc = e
                why = 'exception'

                if isinstance(exc, TypeError):
                    handle_exception(exc, "TypeError occurred")
                elif isinstance(exc, ValueError):
                    handle_exception(exc, "ValueError occurred")
                else:
                    handle_exception(exc, "An unexpected exception occurred")

                if why == 'exception':
                    raise exc.with_traceback(tb)

        if why == 'exception':
            raise exc.with_traceback(tb)

        return result

    def handle_exception(self, exc, exc_type):
        print(f"An exception of type {exc_type} occurred: {exc}")
        traceback.print_exc()

    ## Stack manipulation

    # def byte_RESUME(self, *args):
    #     pass
    #     # frame = self.frame
    #     # if frame:
    #     #     stack_repr = repr(frame.stack)
    #     #     print(f"RESUME: Current stack state: {stack_repr}")
    #     # else:
    #     #     raise VirtualMachineError("No frame to resume")

    # def byte_CACHE(self, *args):
    #     """Реализация поведения инструкции CACHE."""
    #     pass

    def byte_LOAD_CONST(self, const):
        self.push(const)

    def byte_POP_TOP(self):
        self.pop()

    def byte_DUP_TOP(self):
        self.push(self.top())

    def byte_DUP_TOP_TWO(self):
        a, b = self.popn(2)
        self.push(a, b, a, b)

    def byte_ROT_TWO(self):
        a, b = self.popn(2)
        self.push(b, a)

    def byte_ROT_THREE(self):
        a, b, c = self.popn(3)
        self.push(c, a, b)

    def byte_ROT_FOUR(self):
        a, b, c, d = self.popn(4)
        self.push(d, a, b, c)

    def byte_DUP_TOPX(self, count):
        items = self.popn(count)
        for _ in [1, 2]:
            self.push(*items)

    ## Names

    def byte_LOAD_NAME(self, name):
        frame = self.frame
        if name in frame.f_locals:
            val = frame.f_locals[name]
        elif name in frame.f_globals:
            val = frame.f_globals[name]
        elif name in frame.f_builtins:
            val = frame.f_builtins[name]
        else:
            raise NameError(f"name '{name}' is not defined")
        self.push(val)

    def byte_STORE_NAME(self, name):
        self.frame.f_locals[name] = self.pop()

    def byte_DELETE_NAME(self, name):
        del self.frame.f_locals[name]

    def byte_LOAD_FAST(self, name):
        if name in self.frame.f_locals:
            val = self.frame.f_locals[name]
        else:
            raise UnboundLocalError(
                f"local variable '{name}' accessed before assignment"
            )
        self.push(val)

    def byte_STORE_FAST(self, name):
        self.frame.f_locals[name] = self.pop()

    def byte_DELETE_FAST(self, name):
        del self.frame.f_locals[name]

    def byte_LOAD_GLOBAL(self, name):
        f = self.frame
        if name in f.f_globals:
            val = f.f_globals[name]
        elif name in f.f_builtins:
            val = f.f_builtins[name]
        else:
            raise NameError(f"global name '{name}' is undefined")
        self.push(val)

    def byte_STORE_GLOBAL(self, name):
        f = self.frame
        f.f_globals[name] = self.pop()

    def byte_LOAD_DEREF(self, name):
        self.push(self.frame.cells[name].get())

    def byte_STORE_DEREF(self, name):
        self.frame.cells[name].set(self.pop())

    def byte_LOAD_LOCALS(self):
        self.push(self.frame.f_locals)

    # def byte_LOAD_CLOSURE(self, name):
    #     self.push(self.frame.cells[name])
    #
    # def byte_LOAD_ATTR(self, attr_name):
    #     try:
    #         obj = self.pop()
    #         value = getattr(obj, attr_name)
    #         self.push(value)
    #     except AttributeError as e:
    #         raise VirtualMachineError(f"AttributeError: {e}")
    #
    # def byte_STORE_ATTR(self, name):
    #     val, obj = self.popn(2)
    #     setattr(obj, name, val)
    #
    # def byte_DELETE_ATTR(self, name):
    #     obj = self.pop()
    #     delattr(obj, name)
    #
    # def byte_STORE_SUBSCR(self):
    #     val, obj, subscr = self.popn(3)
    #     obj[subscr] = val
    #
    # def byte_DELETE_SUBSCR(self):
    #     obj, subscr = self.popn(2)
    #     del obj[subscr]
    #
    # def byte_BINARY_SUBSCR(self):
    #     try:
    #         y, x = self.popn(2)
    #         if not isinstance(x, (list, tuple, str)):
    #             raise TypeError(f"unsupported operand type(s) for indexing: '{type(x).__name__}'")
    #         if not isinstance(y, int):
    #             raise TypeError(f"slice indices must be integers or None or have an __index__ method")
    #         self.push(x[y])
    #     except IndexError as e:
    #         log.error(f"IndexError in BINARY_SUBSCR: {e}")
    #         raise
    #     except TypeError as e:
    #         log.error(f"TypeError in BINARY_SUBSCR: {e}")
    #         raise

    ## Operators

    UNARY_OPERATORS = {
        "POSITIVE": operator.pos,
        "NEGATIVE": operator.neg,
        "NOT": operator.not_,
        "CONVERT": repr,
        "INVERT": operator.invert,
    }

    def unaryOperator(self, op):
        x = self.pop()
        self.push(self.UNARY_OPERATORS[op](x))

    BINARY_OPERATORS = {
        "POWER": pow,
        "MULTIPLY": operator.mul,
        "MATRIX_MULTIPLY": operator.matmul,
        "FLOOR_DIVIDE": operator.floordiv,
        "DIVIDE": getattr(operator, "div", lambda x, y: None),
        "TRUE_DIVIDE": operator.truediv,
        "MODULO": operator.mod,
        "ADD": operator.add,
        "SUBTRACT": operator.sub,
        "SUBSCR": operator.getitem,
        "LSHIFT": operator.lshift,
        "RSHIFT": operator.rshift,
        "AND": operator.and_,
        "XOR": operator.xor,
        "OR": operator.or_,
    }

    def binaryOperator(self, op):
        y, x = self.popn(2)
        self.push(self.BINARY_OPERATORS[op](x, y))

    def inplaceOperator(self, op):
        x, y = self.popn(2) #или y, x
        result = self.BINARY_OPERATORS[op](x, y)
        self.push(result)

    # def inplaceOperator(self, op):
    #     x, y = self.popn(2)
    #     if op == 'POWER':
    #         x **= y
    #     elif op == 'MULTIPLY':
    #         x *= y
    #     elif op in ['DIVIDE', 'FLOOR_DIVIDE']:
    #         x //= y
    #     elif op == 'TRUE_DIVIDE':
    #         x /= y
    #     elif op == 'MODULO':
    #         x %= y
    #     elif op == 'ADD':
    #         x += y
    #     elif op == 'SUBTRACT':
    #         x -= y
    #     elif op == 'LSHIFT':
    #         x <<= y
    #     elif op == 'RSHIFT':
    #         x >>= y
    #     elif op == 'AND':
    #         x &= y
    #     elif op == 'XOR':
    #         x ^= y
    #     elif op == 'OR':
    #         x |= y
    #     else:           # pragma: no cover
    #         raise VirtualMachineError("Unknown in-place operator: %r" % op)
    #     self.push(x)

    def sliceOperator(self, op):
        start = 0
        end = None
        op, count = op[:-2], int(op[-1])
        if count == 1:
            start = self.pop()
        elif count == 2:
            end = self.pop()
        elif count == 3:
            end = self.pop()
            start = self.pop()
        l = self.pop()
        if end is None:
            end = len(l)
        if op.startswith('STORE_'):
            l[start:end] = self.pop()
        elif op.startswith('DELETE_'):
            del l[start:end]
        else:
            self.push(l[start:end])

    COMPARE_OPERATORS = [
        operator.lt,
        operator.le,
        operator.eq,
        operator.ne,
        operator.gt,
        operator.ge,
        lambda x, y: x in y,
        lambda x, y: x not in y,
        lambda x, y: x is y,
        lambda x, y: x is not y,
        lambda x, y: isinstance(x, y),
        lambda x, y: not isinstance(x, y),
    ]

    def byte_COMPARE_OP(self, opnum):
        y, x = self.popn(2)
        self.push(self.COMPARE_OPERATORS[opnum](x, y))

    ## Attributes and indexing

    def byte_LOAD_ATTR(self, attr):
        obj = self.pop()
        val = getattr(obj, attr)
        self.push(val)

    def byte_STORE_ATTR(self, name):
        val, obj = self.popn(2)
        setattr(obj, name, val)

    def byte_DELETE_ATTR(self, name):
        obj = self.pop()
        delattr(obj, name)

    def byte_STORE_SUBSCR(self):
        val, obj, subscr = self.popn(3)
        obj[subscr] = val

    def byte_DELETE_SUBSCR(self):
        obj, subscr = self.popn(2)
        del obj[subscr]

    ## Building

    def byte_BUILD_TUPLE(self, count):
        elts = self.popn(count)
        self.push(tuple(elts))

    def byte_BUILD_LIST(self, count):
        elts = self.popn(count)
        self.push(elts)

    def byte_BUILD_SET(self, count):
        elts = self.popn(count)
        self.push(set(elts))

    def byte_BUILD_MAP(self, size):
        self.push({})

    # def byte_BUILD_CONST_KEY_MAP(self, count):
    #     keys = self.pop()
    #     values = self.popn(count)
    #     self.push(dict(zip(keys, values)))
    #
    # def byte_BUILD_STRING(self, count):
    #     elts = self.popn(count)
    #     self.push("".join(elts))

    def byte_STORE_MAP(self):
        the_map, val, key = self.popn(3)
        the_map[key] = val
        self.push(the_map)

    def byte_UNPACK_SEQUENCE(self, count):
        seq = self.pop()
        for x in reversed(seq):
            self.push(x)

    def byte_BUILD_SLICE(self, count):
        if count == 2:
            x, y = self.popn(2)
            self.push(slice(x, y))
        elif count == 3:
            x, y, z = self.popn(3)
            self.push(slice(x, y, z))
        else:  # pragma: no cover
            raise VirtualMachineError(f"Strange BUILD_SLICE count: {count}")

    def byte_LIST_APPEND(self, count):
        val = self.pop()
        the_list = self.peek(count)
        the_list.append(val)

    # def byte_LIST_APPEND(self, i):
    #     val = self.pop()
    #     the_list = self.peek(i)
    #     the_list.append(val)

    def byte_SET_ADD(self, count):
        val = self.pop()
        the_set = self.peek(count)
        the_set.add(val)

    def byte_MAP_ADD(self, count):
        val, key = self.popn(2)
        the_map = self.peek(count)
        the_map[key] = val

    ## Printing

    if 0:
        def byte_PRINT_EXPR(self):
            print(self.pop())

    def byte_PRINT_ITEM(self):
        item = self.pop()
        self.print_item(item)

    def byte_PRINT_ITEM_TO(self):
        to = self.pop()
        item = self.pop()
        self.print_item(item, to)

    def byte_PRINT_NEWLINE(self):
        self.print_newline()

    def byte_PRINT_NEWLINE_TO(self):
        to = self.pop()
        self.print_newline(to)

    @staticmethod
    def print_item(item, to=None):
        if to is None:
            to = sys.stdout
        if to.softspace:
            print(" ", end="", file=to)
            to.softspace = 0
        print(item, end="", file=to)
        if isinstance(item, str):
            if (not item) or (not item[-1].isspace()) or (item[-1] == " "):
                to.softspace = 1
        else:
            to.softspace = 1

    @staticmethod
    def print_newline(to=None):
        if to is None:
            to = sys.stdout
        print("", file=to)
        to.softspace = 0

    ## Jumps

    def byte_JUMP_FORWARD(self, jump):
        self.jump(jump)

    def byte_JUMP_ABSOLUTE(self, jump):
        self.jump(jump)

    def byte_JUMP_IF_TRUE(self, jump):
        val = self.top()
        if val:
            self.jump(jump)

    def byte_JUMP_IF_FALSE(self, jump):
        val = self.top()
        if not val:
            self.jump(jump)

    def byte_POP_JUMP_IF_TRUE(self, jump):
        val = self.pop()
        if val:
            self.jump(jump)

    def byte_POP_JUMP_IF_FALSE(self, jump):
        val = self.pop()
        if not val:
            self.jump(jump)

    def byte_JUMP_IF_TRUE_OR_POP(self, jump):
        val = self.top()
        if val:
            self.jump(jump)
        else:
            self.pop()

    def byte_JUMP_IF_FALSE_OR_POP(self, jump):
        val = self.top()
        if not val:
            self.jump(jump)
        else:
            self.pop()

    ## Blocks

    def byte_SETUP_LOOP(self, dest):
        self.push_block("loop", dest)

    def byte_GET_ITER(self):
        self.push(iter(self.pop()))

    def byte_FOR_ITER(self, jump):
        iterobj = self.top()
        try:
            v = next(iterobj)
            self.push(v)
        except StopIteration:
            self.pop()
            self.jump(jump)

    def byte_BREAK_LOOP(self):
        return "break"

    def byte_CONTINUE_LOOP(self, dest):
        self.return_value = dest
        return "continue"

    def byte_SETUP_EXCEPT(self, dest):
        self.push_block("setup-except", dest)

    def byte_SETUP_FINALLY(self, dest):
        self.push_block("finally", dest)

    def byte_END_FINALLY(self):
        v = self.pop()
        if isinstance(v, str):
            why = v
            if why in ("return", "continue"):
                self.return_value = self.pop()
            if why == "silenced":
                block = self.pop_block()
                assert block.type == "except-handler"
                self.unwind_block(block)
                why = None
        elif v is None:
            why = None
        elif issubclass(v, BaseException):
            exctype = v
            val = self.pop()
            tb = self.pop()
            self.last_exception = (exctype, val, tb)
            why = 'reraise'
        else:
            raise VirtualMachineError("Confused END_FINALLY")
        return why

    def byte_POP_BLOCK(self):
        self.pop_block()

    def byte_RAISE_VARARGS(self, argc):
        cause = exc = None
        if argc == 2:
            cause = self.pop()
            exc = self.pop()
        elif argc == 1:
            exc = self.pop()
        elif argc == 0:
            exc_type, val, tb = self.last_exception
            if exc_type is None:
                return "exception"
            else:
                return "reraise"
        else:
            raise VirtualMachineError(f"RAISE_VARARGS with invalid argc {argc}")
        return self.do_raise(exc, cause)

    def do_raise(self, exc, cause):
        if exc is None:  # reraise
            exc_type, val, tb = self.last_exception
            if exc_type is None:
                return "exception"
            else:
                return "reraise"

        elif isinstance(exc, type):
            exc_type = exc
            val = exc()
        elif isinstance(exc, BaseException):
            exc_type = type(exc)
            val = exc
        else:
            return "exception"

        if cause:
            if isinstance(cause, type):
                cause = cause()
            elif not isinstance(cause, BaseException):
                return "exception"

            val.__cause__ = cause

        self.last_exception = exc_type, val, val.__traceback__
        return "exception"

    def byte_POP_EXCEPT(self):
        block = self.pop_block()
        if block.type != "except-handler":
            raise Exception("popped block is not an except handler")
        self.unwind_block(block)

# РАЗОБРАТЬСЯ!
    def byte_SETUP_WITH(self, dest):
        ctxmgr = self.pop()
        exit = ctxmgr.__exit__
        enter = ctxmgr.__enter__()
        self.push(exit)
        self.push(enter)
        self.push_block("finally", dest)

    def byte_WITH_CLEANUP(self):
        v = w = None
        u = self.top()
        if u is None:
            exit_func = self.pop(1)
        elif isinstance(u, str):
            if u in ('return', 'continue'):
                exit_func = self.pop(2)
            else:
                exit_func = self.pop(1)
            u = None
        elif issubclass(u, BaseException):
            w, v, u = self.popn(3)
            tp, exc, tb = self.popn(3)
            exit_func = self.pop()
            self.push(tp, exc, tb)
            self.push(None)
            self.push(w, v, u)
            block = self.pop_block()
            assert block.type == 'except-handler'
            self.push_block(block.type, block.handler, block.level-1)
        else:       # pragma: no cover
            raise VirtualMachineError("Confused WITH_CLEANUP")
        exit_ret = exit_func(u, v, w)
        err = (u is not None) and bool(exit_ret)
        if err:
            self.push('silenced')


    # def byte_WITH_CLEANUP_START(self):
    #     pass
    #
    # def byte_WITH_CLEANUP_FINISH(self):
    #     pass

    ## Functions

    def byte_MAKE_FUNCTION(self, argc):
        name = self.pop()
        code = self.pop()
        defaults = self.popn(argc)
        globs = self.frame.f_globals
        fn = Function(name, code, globs, defaults, None, self)
        self.push(fn)

    def byte_LOAD_CLOSURE(self, name):
        self.push(self.frame.cells[name])

    def byte_MAKE_CLOSURE(self, argc):
        name = self.pop()
        closure, code = self.popn(2)
        defaults = self.popn(argc)
        globs = self.frame.f_globals
        fn = Function(name, code, globs, defaults, closure, self)
        self.push(fn)

    def byte_CALL_FUNCTION(self, arg):
        return self.call_function(arg, [], {})

    def byte_LOAD_BUILD_CLASS(self):
        self.push(__build_class__)

    def byte_CALL_FUNCTION_VAR(self, arg):
        args = self.pop()
        return self.call_function(arg, args, {})

    def byte_CALL_FUNCTION(self, arg):
        return self.call_function(arg, [], {})

    def byte_CALL_FUNCTION_VAR_KW(self, arg):
        args, kwargs = self.popn(2)
        return self.call_function(arg, args, kwargs)

    def byte_CALL_FUNCTION_KW(self, arg):
        kwargs = self.pop()
        return self.call_function(arg, [], kwargs)

    def byte_CALL_FUNCTION_EX(self, flag):
        if flag & 0x01:
            kwargs = self.pop()
        else:
            kwargs = {}
        args = self.pop()
        posargs = []
        func = self.pop()
        retval = func(*args, **kwargs)
        self.push(retval)

    def call_function(self, arg, args, kwargs):
        lenKw, lenPos = divmod(arg, 256)
        namedargs = {}

        for _ in range(lenKw):
            key, val = self.popn(2)
            namedargs[key] = val
        namedargs.update(kwargs)

        posargs = self.popn(lenPos)
        posargs.extend(args)

        func = self.pop()

        if hasattr(func, "im_func"):
            if func.im_self:
                posargs.insert(0, func.im_self)
            if not isinstance(posargs[0], func.im_class):
                raise TypeError(
                    f"Unbound method {func.__name__}() must be called with {func.__qualname__.split('.')[0]} instance "
                    f"as the first argument (got {type(posargs[0]).__name__} instance instead)"
                )
            func = func.im_func

        retval = func(*posargs, **namedargs)
        self.push(retval)

    def byte_RETURN_VALUE(self):
        self.return_value = self.pop()
        if self.frame.generator:
            self.frame.generator.finished = True
        return "return"

    def byte_YIELD_VALUE(self):
        self.return_value = self.pop()
        return "yield"

    def byte_YIELD_FROM(self):
        u = self.pop()
        x = self.top()

        try:
            if not isinstance(x, Generator) or u is None:
                retval = next(x)
            else:
                retval = x.send(u)
            self.return_value = retval
        except StopIteration as e:
            self.pop()
            self.push(e.value)
        else:
            self.jump(self.frame.f_lasti - 1)
            return "yield"

    # def byte_YIELD_FROM(self):
    #     u = self.pop()
    #     x = self.top()
    #
    #     try:
    #         if u is None:
    #             retval = next(x)
    #         else:
    #             retval = x.send(u)
    #         self.return_value = retval
    #     except StopIteration as e:
    #         self.pop()
    #         self.push(e.value)
    #     else:
    #         self.jump(self.frame.f_lasti - 2)
    #         return "yield"

    ## Importing

    def byte_IMPORT_NAME(self, name):
        level, fromlist = self.popn(2)
        frame = self.frame
        self.push(
            __import__(name, frame.f_globals, frame.f_locals, fromlist, level)
        )

    def byte_IMPORT_STAR(self):
        mod = self.pop()
        for attr in dir(mod):
            if attr[0] != "_":
                self.frame.f_locals[attr] = getattr(mod, attr)

    def byte_IMPORT_FROM(self, name):
        mod = self.top()
        self.push(getattr(mod, name))

    ## And the rest...

    def byte_EXEC_STMT(self):
        stmt, globs, locs = self.popn(3)
        exec(stmt, globs, locs)

    def byte_LOAD_BUILD_CLASS(self):
        self.push(__build_class__)

    def byte_STORE_LOCALS(self):
        self.frame.f_locals = self.pop()

    def byte_SET_LINENO(self, lineno):
        self.frame.f_lineno = lineno
