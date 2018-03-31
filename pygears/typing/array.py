from .base import EnumerableGenericMeta
from .unit import Unit


class ArrayMeta(EnumerableGenericMeta):
    def keys(self):
        return list(range(int(self.args[1])))

    def __new__(cls, name, bases, namespace, args=[]):
        cls = super().__new__(cls, name, bases, namespace, args)

        if len(cls.args) < 2:
            # Generic parameter values have not been supplied
            return cls
        else:
            # If Array of Units, return Unit
            if cls.args[0] == Unit:
                return Unit

            if cls.args[1] == 1:
                # If Array of length 1, return original type
                return cls.args[0]
            elif cls.args[1] == 0:
                # If Array of length 0, return Unit
                return Unit

            # Otherwise, return the Array class
            return cls

    def __getitem__(self, index):
        if not self.is_specified():
            return super().__getitem__(index)

        index = self.index_norm(index)

        width = 0
        for i in index:
            if isinstance(i, slice):
                if (i.stop == 0) or (i.stop - i.start > len(self)):
                    raise IndexError
                width += i.stop - i.start
            else:
                if i >= len(self):
                    raise IndexError
                width += 1

        return Array[self.args[0], width]

    def __str__(self):
        return f'Array[{str(self.args[0])}, {len(self)}]'


class Array(metaclass=ArrayMeta):
    __parameters__ = ['T', 'N']
