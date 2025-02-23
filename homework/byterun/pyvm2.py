"""A pure-Python Python bytecode interpreter."""

import dis
import inspect
import linecache
import logging
import operator
import reprlib
import sys
import traceback

from .pyobj import Block, Frame, Function, Generator

log = logging.getLogger(__name__)

repr_obj = reprlib.Repr()
repr_obj.maxother = 120
repper = repr_obj.repr


class VirtualMachineError(Exception):
    """
    Исключение, которое возникает при ошибках в работе виртуальной машины
    """
    pass


class VirtualMachine:
    """
    Виртуальная машина Python, интерпретирующая байт-код
    """
    def __init__(self):
        """
        Инициализация виртуальной машины
        Создает стек фреймов, устанавливает текущий фрейм и переменные для
        хранения возвращаемого значения и последнего исключения
        """
        self.frames = []
        self.frame = None
        self.return_value = None
        self.last_exception = None

    def top(self):
        """
        Возвращает значение с вершины стека текущего фрейма без изменений
        """
        return self.frame.stack[-1]

    def pop(self, i=0):
        """
        Удаляет значение с вершины стека текущего фрейма
        :param i: Индекс с конца стека (по умолчанию 0)
        """
        return self.frame.stack.pop(-1 - i)

    def push(self, *vals):
        """
        Добавляет значения в стек текущего фрейма
        :param vals: Значения для добавления в стек
        """
        self.frame.stack.extend(vals)

    def popn(self, n):
        """
        Удаляет несколько значений с вершины стека текущего фрейма
        :param n: Количество значений для удаления
        :return: Список удаленных значений
        """
        if n:
            ret = self.frame.stack[-n:]
            self.frame.stack[-n:] = []
            return ret
        else:
            return []

    def peek(self, n):
        """
        Возвращает значение с указанной глубиной в стеке без его удаления
        :param n: Глубина значения в стеке
        :return: Значение с указанной глубиной
        """
        return self.frame.stack[-n]

    def jump(self, jump):
        """
        Перемещает указатель инструкции к указанному адресу
        :param jump: Адрес, куда нужно переместить указатель
        """
        self.frame.f_lasti = jump

    def push_block(self, block_type, handler=None, level=None):
        """
        Добавляет блок управления (например, цикл или обработчик исключений)
        в стек блоков
        :param block_type: Тип блока (например, "loop", "setup-except")
        :param handler: Обработчик для блока (опционально)
        :param level: Уровень стека, до которого действует блок
        """
        if level is None:
            level = len(self.frame.stack)
        self.frame.block_stack.append(Block(block_type, handler, level))

    def pop_block(self):
        """
        Удаляет верхний блок из стека блоков
        :return: Удаленный блок
        """
        return self.frame.block_stack.pop()

    def make_frame(self, code, callargs={}, f_globals=None, f_locals=None):
        """
        Создает новый фрейм для выполнения кода
        :param code: Объект кода для выполнения
        :param callargs: Аргументы вызова
        :param f_globals: Глобальные переменные
        :param f_locals: Локальные переменные
        :return: Новый фрейм
        """
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
        """
        Добавляет фрейм в стек фреймов
        :param frame: Фрейм для добавления
        """
        self.frames.append(frame)
        self.frame = frame

    def pop_frame(self):
        """
        Удаляет текущий фрейм из стека фреймов
        """
        self.frames.pop()
        if self.frames:
            self.frame = self.frames[-1]
        else:
            self.frame = None

    def print_frames(self):
        """
        Выводит информацию о всех фреймах в стеке
        """
        for f in self.frames:
            filename = f.f_code.co_filename
            lineno = f.line_number()
            print(f"File {filename}, line {lineno}, in {f.f_code.co_name}")
            linecache.checkcache(filename)
            line = linecache.getline(filename, lineno, f.f_globals)
            if line:
                print("    " + line.strip())

    def resume_frame(self, frame):
        """
        Возобновляет выполнение указанного фрейма
        :param frame: Фрейм для возобновления
        :return: Результат выполнения фрейма
        """
        frame.f_back = self.frame
        val = self.run_frame(frame)
        frame.f_back = None
        return val

    def run_code(self, code, f_globals=None, f_locals=None):
        """
        Выполняет код в новом фрейме
        :param code: Объект кода для выполнения
        :param f_globals: Глобальные переменные
        :param f_locals: Локальные переменные
        :return: Результат выполнения кода
        """
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
        """
        Раскручивает стек для указанного блока
        :param block: Блок для раскручивания
        """
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
        """
        Разбирает байт-код на имя инструкции и аргументы
        :param opoffset: Текущая позиция в байт-коде
        :return: Кортеж (имя инструкции, аргументы, новая позиция)
        """
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
        Вызывает соответствующий метод для обработки инструкции байт-кода
        :param byteName: Имя инструкции
        :param arguments: Аргументы инструкции
        :return: Причина завершения (why)
        """
        why = None
        log.info(
            f"Executing bytecode: {byteName} with arguments: {arguments}"
        )
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
                    raise VirtualMachineError(
                        f"Unknown bytecode type: {byteName}"
                    )
                why = bytecode_fn(*arguments)

        except Exception:
            self.last_exception = sys.exc_info()[:2] + (None,)
            log.exception("Caught exception during execution")
            why = "exception"

        return why

    def manage_block_stack(self, why):
        """
        Управляет стеком блоков при различных причинах завершения
        :param why: Причина завершения
        (например, "break", "continue", "exception")
        :return: Обновленная причина завершения
        """
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
        """
        Выполняет код внутри фрейма
        :param frame: Фрейм для выполнения
        :return: Результат выполнения
        """
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
        """
        Обрабатывает исключение и выводит информацию об ошибке
        :param exc: Объект исключения
        :param exc_type: Тип исключения (например, "TypeError", "ValueError")
        Выводит сообщение об ошибке в стандартный поток вывода
        и отображает трассировку стека
        """
        print(f"An exception of type {exc_type} occurred: {exc}")
        traceback.print_exc()

    def byte_LOAD_CONST(self, const):
        """
        Загружает константу на вершину стека
        :param const: Константа, которая будет загружена на стек
        Используется для добавления значений, таких
        как числа или строки, в стек исполнения
        """
        self.push(const)

    def byte_POP_TOP(self):
        """
        Удаляет верхний элемент со стека
        Используется для удаления значения с вершины стека
        без его использования
        """
        self.pop()

    def byte_DUP_TOP(self):
        """
        Дублирует два верхних элемента стека
        Создает копии двух верхних элементов стека и помещает их
        обратно на стек в том же порядке
        """
        self.push(self.top())

    def byte_DUP_TOP_TWO(self):
        """
        Дублирует два верхних элемента стека
        Создает копии двух верхних элементов стека и помещает их
        обратно на стек в том же порядке
        """
        a, b = self.popn(2)
        self.push(a, b, a, b)

    def byte_ROT_TWO(self):
        """
        Меняет местами два верхних элемента стека
        Помещает второй элемент стека на вершину,
        а первый элемент становится вторым
        """
        a, b = self.popn(2)
        self.push(b, a)

    def byte_ROT_THREE(self):
        """
        Поворачивает три верхних элемента стека
        Перемещает третий элемент стека на вершину,
        а первые два элемента опускаются на одну позицию ниже
        """
        a, b, c = self.popn(3)
        self.push(c, a, b)

    def byte_ROT_FOUR(self):
        """
        Перемещает четыре верхних элемента стека
        Первый элемент становится четвертым, второй — первым,
        третий — вторым, а четвертый — третьим
        """
        a, b, c, d = self.popn(4)
        self.push(d, a, b, c)

    def byte_DUP_TOPX(self, count):
        """
        Дублирует указанное количество верхних элементов стека
        :param count: Количество элементов для дублирования
        Каждый элемент дублируется и помещается обратно на стек
        """
        items = self.popn(count)
        for _ in [1, 2]:
            self.push(*items)

    ## Names

    def byte_LOAD_NAME(self, name):
        """
        Загружает значение переменной с заданным именем из локальных,
        глобальных или встроенных областей видимости
        :param name: Имя переменной для загрузки
        Если переменная не найдена ни в одной области видимости,
        вызывается NameError
        """
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
        """
        Сохраняет значение в переменную с указанным именем в локальной
        области видимости
        :param name: Имя переменной, куда будет сохранено значение
        Значение берется с вершины стека и удаляется после сохранения
        """
        self.frame.f_locals[name] = self.pop()

    def byte_DELETE_NAME(self, name):
        """
        Удаляет переменную с указанным именем из локальной области видимости
        :param name: Имя переменной для удаления
        Если переменная не существует, возникает ошибка
        """
        del self.frame.f_locals[name]

    def byte_LOAD_FAST(self, name):
        """
        Загружает значение локальной переменной с указанным именем
        :param name: Имя локальной переменной для загрузки
        Если переменная не существует, вызывается UnboundLocalError
        """
        if name in self.frame.f_locals:
            val = self.frame.f_locals[name]
        else:
            raise UnboundLocalError(
                f"local variable '{name}' accessed before assignment"
            )
        self.push(val)

    def byte_STORE_FAST(self, name):
        """
        Сохраняет значение в локальную переменную
        :param name: Имя локальной переменной, куда будет сохранено значение
        Значение берется с вершины стека и удаляется после сохранения
        """
        self.frame.f_locals[name] = self.pop()

    def byte_DELETE_FAST(self, name):
        """
        Удаляет локальную переменную
        :param name: Имя локальной переменной для удаления
        Если переменная не существует, возникает ошибка
        """
        del self.frame.f_locals[name]

    def byte_LOAD_GLOBAL(self, name):
        """
        Загружает значение глобальной переменной или встроенной функции
        :param name: Имя глобальной переменной или встроенной функции
        Если переменная не найдена ни в глобальных, ни во встроенных объектах,
        вызывается NameError
        """
        f = self.frame
        if name in f.f_globals:
            val = f.f_globals[name]
        elif name in f.f_builtins:
            val = f.f_builtins[name]
        else:
            raise NameError(f"global name '{name}' is undefined")
        self.push(val)

    def byte_STORE_GLOBAL(self, name):
        """
        Сохраняет значение в глобальную переменную
        :param name: Имя глобальной переменной, куда будет сохранено значение
        Значение берется с вершины стека и удаляется после сохранения
        """
        f = self.frame
        f.f_globals[name] = self.pop()

    def byte_LOAD_DEREF(self, name):
        """
        Загружает значение из ячейки замыкания
        :param name: Имя переменной, хранящейся в ячейке замыкания
        Значение извлекается из соответствующей ячейки и помещается на стек
        """
        self.push(self.frame.cells[name].get())

    def byte_STORE_DEREF(self, name):
        """
        Сохраняет значение в ячейку замыкания
        :param name: Имя переменной, хранящейся в ячейке замыкания
        Значение берется с вершины стека и удаляется после сохранения
        """
        self.frame.cells[name].set(self.pop())

    def byte_LOAD_LOCALS(self):
        """
        Загружает словарь локальных переменных текущего фрейма на стек
        После выполнения этой инструкции на вершине стека будет находиться
        словарь `f_locals` текущего фрейма
        """
        self.push(self.frame.f_locals)


    ## Operators

    UNARY_OPERATORS = {
        "POSITIVE": operator.pos,
        "NEGATIVE": operator.neg,
        "NOT": operator.not_,
        "CONVERT": repr,
        "INVERT": operator.invert,
    }

    def unaryOperator(self, op):
        """
        Выполняет унарную операцию над верхним элементом стека
        :param op: Название унарной операции
        (например, "POSITIVE", "NEGATIVE")
        Операция выполняется с использованием словаря UNARY_OPERATORS
        Результат помещается обратно на стек
        """
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
        """
        Выполняет бинарную операцию над двумя верхними элементами стека
        :param op: Название бинарной операции (например, "ADD", "MULTIPLY")
        Два верхних элемента стека извлекаются, операция выполняется с
        использованием словаря BINARY_OPERATORS, и результат помещается
        обратно на стек
        """
        y, x = self.popn(2)
        self.push(self.BINARY_OPERATORS[op](x, y))

    def inplaceOperator(self, op):
        """
        Выполняет операцию "in-place" над двумя верхними элементами стека
        :param op: Название операции "in-place"
        (например, "INPLACE_ADD", "INPLACE_MULTIPLY")
        Два верхних элемента стека извлекаются, операция выполняется с
        использованием словаря BINARY_OPERATORS, и результат
        помещается обратно на стек
        """
        y, x = self.popn(2)
        result = self.BINARY_OPERATORS[op](x, y)
        self.push(result)

    def sliceOperator(self, op):
        """
        Выполняет операцию нарезки (slicing) списка или последовательности
        :param op: Строка, описывающая тип операции
        (например, "BUILD_SLICE+2")
        Операция может быть одной из следующих:
            - Создание среза (`l[start:end]`)
            - Присвоение значения в срез (`l[start:end] = value`)
            - Удаление среза (`del l[start:end]`)
        Число аргументов (`count`) определяется последним символом строки `op`
        """
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
        """
        Выполняет операцию сравнения двух верхних элементов стека
        :param opnum: Индекс операции сравнения в списке COMPARE_OPERATORS
        Два верхних элемента стека извлекаются, операция сравнения
        выполняется, и результат помещается обратно на стек
        """
        y, x = self.popn(2)
        self.push(self.COMPARE_OPERATORS[opnum](x, y))

    ## Attributes and indexing

    def byte_LOAD_ATTR(self, attr):
        """
        Загружает значение атрибута объекта
        :param attr: Имя атрибута для загрузки.
        Объект извлекается с вершины стека, а его атрибут
        загружается через getattr
        Результат помещается обратно на стек
        """
        obj = self.pop()
        val = getattr(obj, attr)
        self.push(val)

    def byte_STORE_ATTR(self, name):
        """
        Сохраняет значение в атрибут объекта
        :param name: Имя атрибута для сохранения
        Значение и объект извлекаются с вершины стека,
        и значение сохраняется в атрибут объекта через setattr
        """
        val, obj = self.popn(2)
        setattr(obj, name, val)

    def byte_DELETE_ATTR(self, name):
        """
        Удаляет атрибут объекта
        :param name: Имя атрибута для удаления
        Объект извлекается с вершины стека,
        и его атрибут удаляется через delattr
        """
        obj = self.pop()
        delattr(obj, name)

    def byte_STORE_SUBSCR(self):
        """
        Сохраняет значение в индексируемый объект
        (например, список или словарь)
        Три верхних элемента стека извлекаются: значение, объект и индекс
        Значение сохраняется в указанном индексе объекта через присваивание
        """
        val, obj, subscr = self.popn(3)
        obj[subscr] = val

    def byte_DELETE_SUBSCR(self):
        """
        Удаляет элемент из индексируемого объекта
        (например, списка или словаря)
        Два верхних элемента стека извлекаются: объект и индекc
        Элемент по указанному индексу удаляется через оператор `del`
        """
        obj, subscr = self.popn(2)
        del obj[subscr]

    ## Building

    def byte_BUILD_TUPLE(self, count):
        """
        Создает кортеж из указанного количества элементов со стека
        :param count: Количество элементов для создания кортежа
        Элементы извлекаются с вершины стека, преобразуются в кортеж,
        и результат помещается обратно на стек
        """
        elts = self.popn(count)
        self.push(tuple(elts))

    def byte_BUILD_LIST(self, count):
        """
        Создает список из указанного количества элементов со стека
        :param count: Количество элементов для создания списка
        Элементы извлекаются с вершины стека, преобразуются в список,
        и результат помещается обратно на стек
        """
        elts = self.popn(count)
        self.push(elts)

    def byte_BUILD_SET(self, count):
        """
        Создает множество (set) из указанного количества элементов со стека
        :param count: Количество элементов для создания множества
        Элементы извлекаются с вершины стека, преобразуются в множество,
        и результат помещается обратно на стек
        """
        elts = self.popn(count)
        self.push(set(elts))

    def byte_BUILD_MAP(self, size):
        """
        Создает пустой словарь
        :param size: Размер словаря
        (игнорируется, так как создается пустой словарь)
        Пустой словарь помещается на стек.
        """
        self.push({})

    def byte_STORE_MAP(self):
        """
        Добавляет пару ключ-значение в существующий словарь
        Три верхних элемента стека извлекаются: словарь, значение и ключ
        Значение добавляется в словарь по указанному ключу,
        и обновленный словарь помещается обратно на стек
        """
        the_map, val, key = self.popn(3)
        the_map[key] = val
        self.push(the_map)

    def byte_UNPACK_SEQUENCE(self, count):
        """
        Распаковывает последовательность (например, список или кортеж)
        на отдельные элементы
        :param count: Количество элементов в последовательности
        Элементы последовательности помещаются на стек в обратном порядке
        """
        seq = self.pop()
        for x in reversed(seq):
            self.push(x)

    def byte_BUILD_SLICE(self, count):
        """
        Создает объект среза (slice) из указанных элементов стека
        :param count: Количество элементов для создания среза.
        - Если count == 2: создается срез с начальным и конечным значением
        - Если count == 3: создается срез с начальным, конечным значением
        и шагом
        Результат помещается на стек
        """
        if count == 2:
            x, y = self.popn(2)
            self.push(slice(x, y))
        elif count == 3:
            x, y, z = self.popn(3)
            self.push(slice(x, y, z))
        else:  # pragma: no cover
            raise VirtualMachineError(f"Strange BUILD_SLICE count: {count}")

    def byte_LIST_APPEND(self, count):
        """
        Добавляет элемент в список
        :param count: Индекс элемента списка относительно вершины стека
        Элемент извлекается с вершины стека и добавляется в список,
        находящийся на указанной глубине стека
        """
        val = self.pop()
        the_list = self.peek(count)
        the_list.append(val)

    def byte_SET_ADD(self, count):
        """
        Добавляет элемент в множество
        :param count: Индекс множества относительно вершины стека
        Элемент извлекается с вершины стека и добавляется во множество,
        находящееся на указанной глубине стека
        """
        val = self.pop()
        the_set = self.peek(count)
        the_set.add(val)

    def byte_MAP_ADD(self, count):
        """
        Добавляет пару ключ-значение в словарь
        :param count: Индекс словаря относительно вершины стека
        Ключ и значение извлекаются с вершины стека и добавляются в словарь,
        находящийся на указанной глубине стека
        """
        val, key = self.popn(2)
        the_map = self.peek(count)
        the_map[key] = val

    ## Printing

    if 0:
        def byte_PRINT_EXPR(self):
            """
            Выводит значение выражения в стандартный поток вывода (stdout)
            Значение берется с вершины стека, выводится через функцию `print`,
            и затем удаляется со стека
            """
            print(self.pop())

    def byte_PRINT_ITEM(self):
        """
        Выводит элемент в стандартный поток вывода (stdout)
        Элемент извлекается с вершины стека, и вызывается метод `print_item`
        для его печати. После этого элемент удаляется со стека
        """
        item = self.pop()
        self.print_item(item)

    def byte_PRINT_ITEM_TO(self):
        """
        Выводит элемент в указанный поток вывода

        Два верхних элемента стека извлекаются: первый — это объект
        для вывода,
        второй — это поток вывода (например, файл или sys.stdout).
        Метод `print_item` используется для печати элемента в указанный поток
        """
        to = self.pop()
        item = self.pop()
        self.print_item(item, to)

    def byte_PRINT_NEWLINE(self):
        """
        Выводит новую строку в стандартный поток вывода (stdout)
        Вызывает метод `print_newline` для добавления новой строки
        """
        self.print_newline()

    def byte_PRINT_NEWLINE_TO(self):
        """
        Выводит новую строку в указанный поток вывода
        Верхний элемент стека извлекается как поток вывода, и вызывается метод
        `print_newline` для добавления новой строки в этот поток
        """
        to = self.pop()
        self.print_newline(to)

    @staticmethod
    def print_item(item, to=None):
        """
        Печатает элемент в указанный поток вывода
        :param item: Объект для печати
        :param to: Поток вывода (по умолчанию sys.stdout)
        Если `to.softspace` установлен, добавляется пробел перед элементом
        Элемент печатается без завершающего символа новой строки
        Если элемент является строкой, проверяется её последний символ
        для корректной установки флага `softspace`
        """
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
        """
        Добавляет новую строку в указанный поток вывода
        :param to: Поток вывода (по умолчанию sys.stdout)
        Устанавливает флаг `softspace` в 0 после вывода новой строки
        """
        if to is None:
            to = sys.stdout
        print("", file=to)
        to.softspace = 0

    ## Jumps

    def byte_JUMP_FORWARD(self, jump):
        """
        Выполняет прыжок вперед на указанное количество байт
        :param jump: Количество байт для перемещения вперед
        """
        self.jump(jump)

    def byte_JUMP_ABSOLUTE(self, jump):
        """
        Выполняет абсолютный прыжок к указанному адресу инструкции
        :param jump: Адрес инструкции, куда нужно перейти
        """
        self.jump(jump)

    def byte_JUMP_IF_TRUE(self, jump):
        """
        Выполняет прыжок, если значение на вершине стека истинно
        :param jump: Адрес инструкции, куда нужно перейти,
        если условие выполнено
        Значение на вершине стека не удаляется
        """
        val = self.top()
        if val:
            self.jump(jump)

    def byte_JUMP_IF_FALSE(self, jump):
        """
        Выполняет прыжок, если значение на вершине стека ложно
        :param jump: Адрес инструкции, куда нужно перейти,
        если условие выполнено
        Значение на вершине стека не удаляется
        """
        val = self.top()
        if not val:
            self.jump(jump)

    def byte_POP_JUMP_IF_TRUE(self, jump):
        """
        Выполняет прыжок, если значение на вершине стека истинно
        :param jump: Адрес инструкции, куда нужно перейти,
        если условие выполнено
        Значение на вершине стека удаляется
        """
        val = self.pop()
        if val:
            self.jump(jump)

    def byte_POP_JUMP_IF_FALSE(self, jump):
        """
        Выполняет прыжок, если значение на вершине стека ложно
        :param jump: Адрес инструкции, куда нужно перейти,
        если условие выполнено
        Значение на вершине стека удаляется
        """
        val = self.pop()
        if not val:
            self.jump(jump)

    def byte_JUMP_IF_TRUE_OR_POP(self, jump):
        """
        Выполняет прыжок, если значение на вершине стека истинно
        Если условие не выполнено, удаляет значение с вершины стека
        :param jump: Адрес инструкции, куда нужно перейти,
        если условие выполнено
        """
        val = self.top()
        if val:
            self.jump(jump)
        else:
            self.pop()

    def byte_JUMP_IF_FALSE_OR_POP(self, jump):
        """
        Выполняет прыжок, если значение на вершине стека ложно
        Если условие не выполнено, удаляет значение с вершины стека
        :param jump: Адрес инструкции, куда нужно перейти,
        если условие выполнено
        """
        val = self.top()
        if not val:
            self.jump(jump)
        else:
            self.pop()

    ## Blocks

    def byte_SETUP_LOOP(self, dest):
        """
        Устанавливает блок цикла (loop)
        :param dest: Адрес инструкции, куда нужно перейти
        после завершения цикла
        Добавляет новый блок "loop" в стек блоков с указанным
        адресом обработчика
        """
        self.push_block("loop", dest)

    def byte_GET_ITER(self):
        """
        Получает итератор из объекта
        Извлекает объект с вершины стека, преобразует его в итератор
        с помощью функции `iter`, и помещает обратно на стек
        """
        self.push(iter(self.pop()))

    def byte_FOR_ITER(self, jump):
        """
        Выполняет итерацию по объекту
        :param jump: Адрес инструкции, куда нужно перейти
        при окончании итерации
        Пытается получить следующий элемент из итератора на вершине стека
        Если итерация завершена (StopIteration), удаляет итератор со стека
        и выполняет переход к указанному адресу
        """
        iterobj = self.top()
        try:
            v = next(iterobj)
            self.push(v)
        except StopIteration:
            self.pop()
            self.jump(jump)

    def byte_BREAK_LOOP(self):
        """
        Прерывает выполнение цикла
        Возвращает строку "break", которая используется для выхода
        из блока цикла
        """
        return "break"

    def byte_CONTINUE_LOOP(self, dest):
        """
        Продолжает выполнение цикла
        :param dest: Адрес инструкции, куда нужно перейти
        для продолжения цикла
        Устанавливает значение `return_value` как адрес инструкции
        для продолжения и возвращает строку "continue"
        """
        self.return_value = dest
        return "continue"

    def byte_SETUP_EXCEPT(self, dest):
        """
        Устанавливает блок обработки исключений
        :param dest: Адрес инструкции, куда нужно перейти
        при возникновении исключения
        Добавляет новый блок "setup-except" в стек блоков
        с указанным адресом обработчика
        """
        self.push_block("setup-except", dest)

    def byte_SETUP_FINALLY(self, dest):
        """
        Устанавливает блок finally
        :param dest: Адрес инструкции, куда нужно перейти для
        выполнения блока finally
        Добавляет новый блок "finally" в стек блоков с указанным
        адресом обработчика
        """
        self.push_block("finally", dest)

    def byte_END_FINALLY(self):
        """
        Завершает выполнение блока finally или обработки исключений
        Обрабатывает различные случаи завершения блока finally:
        - Если вершина стека содержит строку
        ("return", "continue", "silenced"), выполняется соответствующая логика
        - Если вершина стека содержит исключение, оно восстанавливается
        для повторной обработки
        - Если вершина стека пуста, возвращается None
        :raises VirtualMachineError: Если состояние блока неизвестно
        :return: Причина завершения блока
        ("return", "continue", "exception", None)
        """
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
        """
        Удаляет верхний блок из стека блоков

        Удаляет последний добавленный блок (например, loop, except, finally)
        из стека блоков
        """
        self.pop_block()

    def byte_RAISE_VARARGS(self, argc):
        """
        Вызывает исключение с различными аргументами
        :param argc: Количество аргументов для вызова исключения (0, 1 или 2)
        - Если argc == 0: повторно выбрасывает последнее сохраненное
        исключение
        - Если argc == 1: выбрасывает исключение с одним аргументом
        - Если argc == 2: выбрасывает исключение с основным и
        дополнительным аргументами
        - Если argc некорректен, вызывает ошибку
        :return: Причина завершения ("exception", "reraise")
        """
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
        """
        Выполняет операцию выброса исключения
        :param exc: Исключение для выброса
        :param cause: Причина исключения (опционально)
        - Если exc == None: повторно выбрасывает последнее
        сохраненное исключение
        - Если exc является классом исключения: создает экземпляр исключения
        - Если exc является экземпляром исключения: использует его напрямую
        - Если передана причина (cause), она добавляется к исключению
        через `__cause__`
        :return: Причина завершения ("exception", "reraise")
        """
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
        """
        Удаляет блок обработки исключений
        Удаляет верхний блок из стека блоков и проверяет,
        что он является блоком обработки исключений ("except-handler")
        Если это не так, вызывает ошибку
        Также очищает стек, связанный с этим блоком
        """
        block = self.pop_block()
        if block.type != "except-handler":
            raise Exception("popped block is not an except handler")
        self.unwind_block(block)

    def byte_SETUP_WITH(self, dest):
        """
        Устанавливает контекстный менеджер (`with`)
        :param dest: Адрес инструкции, куда нужно перейти после
        завершения блока `finally`
        Извлекает объект контекстного менеджера с вершины стека,
        вызывает его методы `__enter__` и `__exit__`,
        помещает их обратно на стек и добавляет блок `finally` в стек блоков
        """
        ctxmgr = self.pop()
        exit = ctxmgr.__exit__
        enter = ctxmgr.__enter__()
        self.push(exit)
        self.push(enter)
        self.push_block("finally", dest)

    def byte_WITH_CLEANUP(self):
        """
        Обрабатывает чистку контекстного менеджера
        Проверяет состояние стека и вызывает метод `__exit__`
        контекстного менеджера с соответствующими аргументами
        Если метод `__exit__` подавляет исключение, помещает
        строку `'silenced'` на стек
        Возможные случаи:
        - Если вершина стека равна `None`, вызывает `__exit__`
        без дополнительных параметров
        - Если вершина стека является строкой (`'return'` или `'continue'`),
        обрабатывает эти случаи
        - Если вершина стека является исключением, восстанавливает его и
        вызывает `__exit__`
        """
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

    ## Functions

    def byte_MAKE_FUNCTION(self, argc):
        """
        Создает новую функцию
        :param argc: Количество значений по умолчанию для параметров функции
        Извлекает имя функции, объект кода и значения по умолчанию из стека,
        создает новый объект `Function` и помещает его обратно на стек
        """
        name = self.pop()
        code = self.pop()
        defaults = self.popn(argc)
        globs = self.frame.f_globals
        fn = Function(name, code, globs, defaults, None, self)
        self.push(fn)

    def byte_LOAD_CLOSURE(self, name):
        """
        Загружает ячейку замыкания на стек
        :param name: Имя переменной, связанной с ячейкой замыкания
        Извлекает ячейку замыкания из текущего фрейма и помещает её на стек
        """
        self.push(self.frame.cells[name])

    def byte_MAKE_CLOSURE(self, argc):
        """
        Создает функцию с замыканием
        :param argc: Количество значений по умолчанию для параметров функции
        Извлекает имя функции, объект кода, значения по умолчанию и ячейки
        замыкания из стека, создает новый объект `Function` с замыканием и
        помещает его обратно на стек
        """
        name = self.pop()
        closure, code = self.popn(2)
        defaults = self.popn(argc)
        globs = self.frame.f_globals
        fn = Function(name, code, globs, defaults, closure, self)
        self.push(fn)

    def byte_CALL_FUNCTION(self, arg):
        """
        Вызывает функцию
        :param arg: Код, определяющий количество позиционных и именованных аргументов
        Извлекает функцию и её аргументы из стека, вызывает функцию с этими аргументами
        и помещает результат выполнения обратно на стек
        """
        return self.call_function(arg, [], {})

    def byte_LOAD_BUILD_CLASS(self):
        """
        Загружает специальную функцию `__build_class__` на стек
        Используется для создания классов через инструкцию `class`
        """
        self.push(__build_class__)

    def byte_CALL_FUNCTION_VAR(self, arg):
        """
        Вызывает функцию с переменным количеством позиционных аргументов
        :param arg: Код, определяющий количество позиционных и
        именованных аргументов
        Извлекает функцию, её аргументы и кортеж позиционных аргументов
        (`*args`) из стека, вызывает функцию с этими аргументами и помещает
        результат обратно на стек
        """
        args = self.pop()
        return self.call_function(arg, args, {})

    def byte_CALL_FUNCTION(self, arg):
        """
        Вызывает функцию
        :param arg: Код, определяющий количество позиционных и именованных
        аргументов
        Извлекает функцию и её аргументы из стека, вызывает функцию с этими
        аргументами и помещает результат выполнения обратно на стек
        """
        return self.call_function(arg, [], {})

    def byte_CALL_FUNCTION_VAR_KW(self, arg):
        """
        Вызывает функцию с переменным количеством позиционных и
        именованных аргументов
        :param arg: Код, определяющий количество позиционных и
        именованных аргументов
        Извлекает кортеж позиционных аргументов (`*args`) и словарь
        именованных аргументов (`**kwargs`) из стека, вызывает функцию
        с этими аргументами и помещает результат обратно на стек
        """
        args, kwargs = self.popn(2)
        return self.call_function(arg, args, kwargs)

    def byte_CALL_FUNCTION_KW(self, arg):
        """
        Вызывает функцию с именованными аргументами
        :param arg: Код, определяющий количество позиционных
        и именованных аргументов
        Извлекает словарь именованных аргументов (`**kwargs`)
        из стека, вызывает функцию с этими аргументами и помещает
        результат обратно на стек
        """
        kwargs = self.pop()
        return self.call_function(arg, [], kwargs)

    def byte_CALL_FUNCTION_EX(self, flag):
        """
        Вызывает функцию с использованием расширенного формата вызова
        :param flag: Флаг, указывающий, включены ли именованные
        аргументы (`**kwargs`)
        - Если флаг установлен (0x01), извлекаются именованные аргументы
        (`**kwargs`) и позиционные аргументы (`*args`)
        - Если флаг не установлен, используются только позиционные
        аргументы (`*args`)
        Выполняет вызов функции с указанными аргументами и помещает
        результат обратно на стек
        """
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
        """
        Общий метод для вызова функции
        :param arg: Код, определяющий количество позиционных и
        именованных аргументов
        :param args: Список позиционных аргументов (`*args`)
        :param kwargs: Словарь именованных аргументов (`**kwargs`)
        Разбирает аргументы из стека, объединяет их с переданными параметрами,
        вызывает функцию с этими аргументами и помещает результат обратно
        на стек
        Проверяет, является ли функция методом класса, и добавляет `self`
        как первый аргумент, если это необходимо
        """
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
                    f"Unbound method {func.__name__}() must be called "
                    f"with {func.__qualname__.split('.')[0]} instance "
                    f"as the first argument (got {type(posargs[0]).__name__} "
                    f"instance instead)"
                )
            func = func.im_func

        retval = func(*posargs, **namedargs)
        self.push(retval)

    def byte_RETURN_VALUE(self):
        """
        Возвращает значение из функции
        Извлекает значение с вершины стека, устанавливает его как
        `return_value` и завершает выполнение текущего фрейма
        Если текущий фрейм является генератором, помечает его как завершенный
        """
        self.return_value = self.pop()
        if self.frame.generator:
            self.frame.generator.finished = True
        return "return"

    def byte_YIELD_VALUE(self):
        """
        Возвращает значение из генератора
        Извлекает значение с вершины стека, устанавливает его
        как `return_value` и приостанавливает выполнение
        текущего фрейма до следующего вызова `send`
        """
        self.return_value = self.pop()
        return "yield"

    def byte_YIELD_FROM(self):
        """
        Выполняет операцию yield from
        Извлекает объект-итератор с вершины стека и вызывает его
        метод `send` или `next`, в зависимости от наличия значения `u`
        Если итератор завершается (StopIteration), значение из исключения
        помещается обратно на стек. В противном случае выполнение
        приостанавливается, и управление передается генератору
        """
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

    ## Importing

    def byte_IMPORT_NAME(self, name):
        """
        Импортирует модуль или пакет
        :param name: Имя модуля для импорта
        Извлекает уровень импорта и список подключаемых атрибутов
        (`fromlist`) из стека, выполняет импорт с использованием
        функции `__import__`, и помещает результат обратно на стек
        """
        level, fromlist = self.popn(2)
        frame = self.frame
        self.push(
            __import__(name, frame.f_globals, frame.f_locals, fromlist, level)
        )

    def byte_IMPORT_STAR(self):
        """
        Импортирует все открытые атрибуты из модуля (*)
        Извлекает модуль с вершины стека и добавляет все его атрибуты
        (кроме тех, которые начинаются с `_`) в локальные переменные
        текущего фрейма
        """
        mod = self.pop()
        for attr in dir(mod):
            if attr[0] != "_":
                self.frame.f_locals[attr] = getattr(mod, attr)

    def byte_IMPORT_FROM(self, name):
        """
        Импортирует конкретный атрибут из модуля
        :param name: Имя атрибута для импорта
        Извлекает модуль с вершины стека, получает указанный атрибут
        через `getattr` и помещает его обратно на стек
        """
        mod = self.top()
        self.push(getattr(mod, name))

    ## And the rest...

    def byte_EXEC_STMT(self):
        """
        Выполняет код из строки или файла
        Извлекает код, глобальные и локальные пространства имен из стека,
        и выполняет код с помощью функции `exec`
        """
        stmt, globs, locs = self.popn(3)
        exec(stmt, globs, locs)

    def byte_LOAD_BUILD_CLASS(self):
        """
        Загружает специальную функцию `__build_class__` на стек
        Используется для создания классов через инструкцию `class`
        """
        self.push(__build_class__)

    def byte_STORE_LOCALS(self):
        """
        Сохраняет словарь локальных переменных в текущем фрейме
        Извлекает словарь с локальными переменными с вершины стека и
        сохраняет его в атрибуте `f_locals` текущего фрейма
        """
        self.frame.f_locals = self.pop()

    def byte_SET_LINENO(self, lineno):
        """
        Устанавливает текущий номер строки для выполнения
        :param lineno: Номер строки, который будет установлен в
        атрибут `f_lineno` текущего фрейма
        Используется для отладки и трассировки исполнения кода
        """
        self.frame.f_lineno = lineno
