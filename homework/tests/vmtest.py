"""Testing tools for byterun."""

import dis
import sys
import textwrap
import types
import unittest
import io

from homework.byterun.pyvm2 import VirtualMachine, VirtualMachineError

# Make this false if you need to run the debugger inside a test.
CAPTURE_STDOUT = "-s" not in sys.argv
# Make this false to see the traceback from a failure inside pyvm2.
CAPTURE_EXCEPTION = True


def dis_code(code):
    """Disassemble `code` and all the code it refers to."""
    for const in code.co_consts:
        if isinstance(const, types.CodeType):
            dis_code(const)

    print("")
    print(code)
    dis.dis(code)


class VmTestCase(unittest.TestCase):

    def assert_ok(self, code, raises=None):
        """Run `code` in our VM and in real Python: they behave the same."""

        code = textwrap.dedent(code)
        code = compile(code, "<%s>" % self.id(), "exec", 0, 1)

        # Print the disassembly so we'll see it if the test fails.
        dis_code(code)

        real_stdout = sys.stdout

        # Run the code through our VM.

        vm_stdout = io.StringIO()
        if CAPTURE_STDOUT:
            sys.stdout = vm_stdout
        vm = VirtualMachine()

        vm_value = vm_exc = None
        try:
            vm_value = vm.run_code(code)
        except VirtualMachineError:
            # If the VM code raises an error, show it.
            raise
        except AssertionError:
            # If test code fails an assert, show it.
            raise
        except Exception as e:
            # Otherwise, keep the exception for comparison later.
            if not CAPTURE_EXCEPTION:
                raise
            vm_exc = e
        finally:
            if CAPTURE_STDOUT:
                sys.stdout = real_stdout
            real_stdout.write("-- stdout ----------\n")
            real_stdout.write(vm_stdout.getvalue())

        # Run the code through the real Python interpreter, for comparison.

        py_stdout = io.StringIO()
        sys.stdout = py_stdout

        py_value = py_exc = None
        globs = {}
        try:
            exec(code, globs, globs)
            py_value = globs.get("_")  # Get the last expression result, if any
        except AssertionError:
            raise
        except Exception as e:
            py_exc = e
        finally:
            sys.stdout = real_stdout

        # Compare results
        self.assert_same_exception(vm_exc, py_exc)
        self.assertEqual(vm_stdout.getvalue(), py_stdout.getvalue())
        self.assertEqual(vm_value, py_value)
        if raises:
            self.assertIsNotNone(vm_exc)
            self.assertIsInstance(vm_exc, raises)
        else:
            self.assertIsNone(vm_exc)

    def assert_same_exception(self, e1, e2):
        """Exceptions don't implement __eq__, check it ourselves."""
        if e1 is None and e2 is None:
            # Both are None, no exception occurred
            return
        self.assertIsNotNone(e1)
        self.assertIsNotNone(e2)
        self.assertEqual(str(e1), str(e2))
        self.assertIs(type(e1), type(e2))
