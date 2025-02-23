"""Implementations of Python fundamental objects for Byterun."""

import collections
import dis
import inspect
import types


def make_cell(value):
    """Создаём объект ячейки замыкания"""
    fn = (lambda x: lambda: x)(value)
    return fn.__closure__[0]


class Function:
    """
    Представляет функцию в виртуальной машине Byterun
    Атрибуты:
        func_code (code): Объект кода функции
        func_name (str): Имя функции
        func_defaults (tuple): Значения по умолчанию для параметров функции
        func_globals (dict): Глобальные переменные функции
        func_locals (dict): Локальные переменные функции
        func_dict (dict): Словарь атрибутов функции
        func_closure (tuple): Замыкания функции
        __name__ (str): Имя функции
        __dict__ (dict): Словарь атрибутов функции
        __doc__ (str): Документация функции
        _vm (VirtualMachine): Экземпляр виртуальной машины
        _func (function): Реальный объект Python-функции
    """
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
        "_doc",
        "_vm",
        "_func",
    ]

    def __init__(self, name, code, globs, defaults, closure, vm):
        """
        Инициализирует объект функции
        :param name: Имя функции (если None, используется имя из объекта кода)
        :param code: Объект кода функции
        :param globs: Глобальные переменные функции
        :param defaults: Значения по умолчанию для параметров функции
        :param closure: Замыкания функции.
        :param vm: Экземпляр виртуальной машины
        """
        self._vm = vm
        self.func_code = code
        self.func_name = self.__name__ = name or code.co_name
        self.func_defaults = tuple(defaults)
        self.func_globals = globs
        self.func_locals = self._vm.frame.f_locals
        self.__dict__ = {}
        self.func_closure = closure
        self._doc = code.co_consts[0] if code.co_consts else None

        @property
        def __doc__(self):
            return self._doc

        kw = {
            "argdefs": self.func_defaults,
        }
        if closure:
            kw["closure"] = tuple(make_cell(0) for _ in closure)
        self._func = types.FunctionType(code, globs, **kw)

    def __repr__(self):     # pragma: no cover
        """
        Возвращает строковое представление объекта функции
        :return: Строковое представление функции
        в формате <Function имя_функции at 0x...>
        """
        return f"<Function {self.func_name} at 0x{id(self):08x}>"

    def __get__(self, instance, owner):
        """
        Поддерживает протокол описателей (descriptors), позволяя использовать
        функцию как метод класса
        :param instance: Экземпляр класса, если функция вызывается
        через экземпляр
        :param owner: Класс, к которому принадлежит функция
        :return: Если функция вызывается через экземпляр, возвращает Method;
        иначе — саму функцию
        """
        if instance is not None:
            return Method(instance, owner, self)
        else:
            return self

    def __call__(self, *args, **kwargs):
        """
        Вызывает функцию с указанными аргументами
        :param args: Позиционные аргументы
        :param kwargs: Именованные аргументы
        :return: Результат выполнения функции
        """
        sig = inspect.signature(self._func)
        bound_args = sig.bind(*args, **kwargs)
        callargs = bound_args.arguments

        frame = self._vm.make_frame(
            self.func_code, callargs, self.func_globals, {}
        )

        CO_GENERATOR = 32  # Флаг для определения, использует ли функция yield
        if self.func_code.co_flags & CO_GENERATOR:
            gen = Generator(frame, self._vm)
            frame.generator = gen
            retval = gen
        else:
            retval = self._vm.run_frame(frame)

        return retval


class Method:
    """
    Представляет метод объекта в виртуальной машине Byterun
    Атрибуты:
        im_self (object): Экземпляр объекта, к которому привязан метод
        im_class (type): Класс, к которому принадлежит метод
        im_func (Function): Функция, реализующая метод
    """
    def __init__(self, obj, _class, func):
        """
        Инициализирует объект метода
        :param obj: Экземпляр объекта, если метод привязан к экземпляру
        :param _class: Класс, к которому принадлежит метод
        :param func: Функция, реализующая метод
        """
        self.im_self = obj
        self.im_class = _class
        self.im_func = func

    def __repr__(self):  # pragma: no cover
        """
        Возвращает строковое представление метода
        :return: Строковое представление метода в формате <Bound Method ...>
        или <Unbound Method ...>
        """
        name = f"{self.im_class.__name__}.{self.im_func.__name__}"
        if self.im_self is not None:
            return f"<Bound Method {name} of {self.im_self}>"
        else:
            return f"<Unbound Method {name}>"

    def __call__(self, *args, **kwargs):
        """
        Вызывает метод с указанными аргументами
        :param args: Позиционные аргументы
        :param kwargs: Именованные аргументы
        :return: Результат выполнения метода
        Если метод привязан к экземпляру (`im_self` не None),
        первый аргумент будет экземпляр объекта
        """
        if self.im_self is not None:
            return self.im_func(self.im_self, *args, **kwargs)
        else:
            return self.im_func(*args, **kwargs)


class Cell:
    """
    Представляет ячейку замыкания для хранения ссылок на переменные
    Ячейки замыкания используются для сохранения значений переменных,
    которые могут быть доступны из нескольких областей видимости
    Атрибуты:
        contents (object): Значение, хранимое в ячейке
    """
    def __init__(self, value):
        """
        Инициализирует ячейку замыкания
        :param value: Значение, которое будет храниться в ячейке
        """
        self.contents = value

    def get(self):
        """
        Возвращает значение, хранящееся в ячейке
        :return: Значение из ячейки
        """
        return self.contents

    def set(self, value):
        """
        Устанавливает новое значение в ячейку
        :param value: Новое значение для ячейки
        """
        self.contents = value


Block = collections.namedtuple("Block", "type, handler, level")
"""
Представляет блок управления (например, цикл, обработчик исключений)
Атрибуты:
    type (str): Тип блока (например, "loop", "setup-except", "finally")
    handler (int): Адрес обработчика для данного блока
    level (int): Уровень стека, до которого действует блок
"""

class Frame(object):
    """
    Представляет фрейм исполнения в виртуальной машине Byterun
    Фрейм содержит информацию о текущем состоянии выполнения кода,
    включая локальные переменные, стек данных, инструкции байт-кода и т.д.
    Атрибуты:
        f_code (code): Объект кода, связанный с этим фреймом
        f_globals (dict): Глобальные переменные фрейма
        f_locals (dict): Локальные переменные фрейма
        f_back (Frame): Предыдущий фрейм в стеке вызовов
        stack (list): Стек данных для выполнения операций
        opcodes (list): Список инструкций байт-кода для данного фрейма
        f_builtins (dict): Встроенные функции и объекты для данного фрейма
        f_lineno (int): Текущий номер строки в исполняемом коде
        f_lasti (int): Индекс последней выполненной инструкции байт-кода
        cells (dict): Словарь ячеек замыкания для переменных
        block_stack (list): Стек блоков управления (циклы, обработчики
        исключений)
        generator (Generator): Генератор, связанный с данным
        фреймом (если есть)
    """
    def __init__(self, f_code, f_globals, f_locals, f_back):
        """
        Инициализирует новый фрейм
        :param f_code: Объект кода для выполнения
        :param f_globals: Глобальные переменные фрейма
        :param f_locals: Локальные переменные фрейма
        :param f_back: Предыдущий фрейм в стеке вызовов
        """
        self.f_code = f_code
        self.f_globals = f_globals
        self.f_locals = f_locals
        self.f_back = f_back
        self.stack = []
        self.opcodes = list(dis.get_instructions(self.f_code))
        if f_back and f_back.f_globals is f_globals:
            self.f_builtins = f_back.f_builtins
        else:
            try:
                self.f_builtins = f_globals["__builtins__"]
                if hasattr(self.f_builtins, "__dict__"):
                    self.f_builtins = self.f_builtins.__dict__
            except KeyError:
                self.f_builtins = {"None": None}

        self.f_lineno = f_code.co_firstlineno
        self.f_lasti = 0

        if f_code.co_cellvars:
            self.cells = {}
            if not f_back.cells:
                f_back.cells = {}
            for var in f_code.co_cellvars:
                cell = Cell(self.f_locals.get(var))
                f_back.cells[var] = self.cells[var] = cell
        else:
            self.cells = None

        if f_code.co_freevars:
            if not self.cells:
                self.cells = {}
            for var in f_code.co_freevars:
                assert self.cells is not None
                assert f_back.cells, f"f_back.cells: {f_back.cells}"
                self.cells[var] = f_back.cells[var]

        self.block_stack = []
        self.generator = None

    def __repr__(self):
        """
        Возвращает строковое представление объекта Frame
        :return: Строку в формате `<Frame at 0x...: filename @ line_number>`,
                 где `0x...` — это адрес объекта в памяти,
                 `filename` — имя файла, связанного с фреймом,
                 `line_number` — текущий номер строки исполнения
        """
        return (
            f"<Frame at 0x{id(self):08x}: {self.f_code.co_filename} "
            f"@ {self.f_lineno}>"
        )
    def line_number(self):
        """
        Определяет текущий номер строки, которую выполняет фрейм
        :return: Номер строки, соответствующий текущей инструкции байт-кода
        Если таблица линий (`co_lnotab`) не обновлялась, вычисляет
        номер строки на основе адреса последней выполненной
        инструкции (`f_lasti`)
        """
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
    """
    Представляет генератор в виртуальной машине Byterun
    Атрибуты:
        gi_frame (Frame): Текущий фрейм, связанный с генератором
        vm (VirtualMachine): Экземпляр виртуальной машины
        started (bool): Флаг, указывающий, был ли запущен генератор
        finished (bool): Флаг, указывающий, завершил ли генератор свою работу
    """
    def __init__(self, g_frame, vm):
        """
        Инициализирует объект генератора
        :param g_frame: Фрейм, связанный с генератором
        :param vm: Экземпляр виртуальной машины
        """
        self.gi_frame = g_frame
        self.vm = vm
        self.started = False
        self.finished = False

    def __iter__(self):
        """
        Возвращает сам объект генератора как итератор
        :return: Сам объект генератора
        """
        return self

    def __next__(self):
        """
        Выполняет следующую итерацию генератора
        Эквивалент вызова метода `send(None)`
        :raises StopIteration: Если генератор завершил свою работу
        :return: Значение, возвращаемое генератором
        """
        return self.send(None)

    def send(self, value=None):
        """
        Отправляет значение в генератор и продолжает его выполнение
        :param value: Значение, отправляемое в генератор.
        :raises TypeError: Если генератор только начался и отправлено
        значение, отличное от None
        :raises StopIteration: Если генератор завершил свою работу
        :return: Значение, возвращаемое генератором
        """
        if not self.started and value is not None:
            raise TypeError(
                f"Can't send non-None value to a just-started generator"
            )
        self.gi_frame.stack.append(value)
        self.started = True
        val = self.vm.resume_frame(self.gi_frame)
        if self.finished:
            raise StopIteration(val)
        return val
