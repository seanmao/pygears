import asyncio

from pygears import registry
from pygears.registry import PluginBase


def operator_func_from_namespace(cls, name):
    def wrapper(self, *args, **kwargs):
        try:
            operator_func = registry('IntfOperNamespace')[name]
            return operator_func(self, *args, **kwargs)
        except KeyError as e:
            raise Exception(f'Operator {name} is not supported.')

    return wrapper


def operator_methods_gen(cls):
    for name in cls.OPERATOR_SUPPORT:
        setattr(cls, name, operator_func_from_namespace(cls, name))
    return cls


@operator_methods_gen
class Intf:
    OPERATOR_SUPPORT = ['__or__', '__getitem__', '__neg__',
                        '__add__', '__sub__', '__mul__', '__div__']

    def __init__(self, dtype):
        self.consumers = []
        self.dtype = dtype
        self.producer = None
        self._in_queue = None
        self._out_queues = []
        self._done = False

    def source(self, port):
        self.producer = port
        port.consumer = self

    def disconnect(self, port):
        if port in self.consumers:
            self.consumers.remove(port)
            port.producer = None
        elif port == self.producer:
            port.consumer = None
            self.producer = None

    def connect(self, port):
        self.consumers.append(port)
        port.producer = self

    @property
    def in_queue(self):
        if self._in_queue is None:
            if self.producer is not None:
                self._in_queue = self.producer.queue

        return self._in_queue

    def get_consumer_queue(self, port):
        i = self.consumers.index(port)
        return self.out_queues[i]

    @property
    def out_queues(self):
        if self._out_queues:
            return self._out_queues

        if len(self.consumers) == 1 and self.in_queue:
            return [self.in_queue]
        else:
            self._out_queues = [asyncio.Queue(maxsize=1) for _ in self.consumers]

            return self._out_queues

    async def put(self, val):
        for q in self.out_queues:
            q.put_nowait(val)

        await asyncio.wait([q.join() for q in self.out_queues])

    def finish(self):
        self._done = True
        for q, c in zip(self.out_queues, self.consumers):
            c.finish()
            for task in q._getters:
                task.cancel()

    async def pull(self):
        if self._done:
            raise asyncio.CancelledError

        return await self.in_queue.get()

    def ack(self):
        return self.in_queue.task_done()

    async def get(self):
        val = await self.pull()
        self.ack()
        return val

    async def __aenter__(self):
        return await self.pull()

    async def __aexit__(self, exception_type, exception_value, traceback):
        self.ack()

    def __hash__(self):
        return id(self)


class IntfOperPlugin(PluginBase):
    @classmethod
    def bind(cls):
        cls.registry['IntfOperNamespace'] = {}
