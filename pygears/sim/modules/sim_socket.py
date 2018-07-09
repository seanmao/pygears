import array
import asyncio
import itertools
import math
import os
import socket
from importlib import util
from math import ceil
import atexit

import jinja2

from pygears import GearDone, registry
from pygears.definitions import ROOT_DIR
from pygears.sim.modules.cosim_base import CosimBase
from pygears.svgen import svgen
from pygears.svgen.util import svgen_typedef
from pygears.typing_common.codec import code, decode
from pygears.util.fileio import save_file

from pygears.sim.modules.cosim_base import CosimNoData


def u32_repr_gen(data, dtype):
    yield int(dtype)
    for i in range(ceil(int(dtype) / 32)):
        yield data & 0xffffffff
        data >>= 32


def u32_repr(data, dtype):
    return array.array('I', u32_repr_gen(code(dtype, data), dtype))


def u32_bytes_to_int(data):
    arr = array.array('I')
    arr.frombytes(data)
    val = 0
    for val32 in reversed(arr):
        val <<= 32
        val |= val32

    return val


def u32_bytes_decode(data, dtype):
    return decode(dtype, u32_bytes_to_int(data))


j2_templates = ['runsim.j2', 'top.j2']
j2_file_names = ['run_sim.sh', 'top.sv']


def sv_cosim_gen(gear):
    pygearslib = util.find_spec("pygearslib")
    if pygearslib is not None:
        from pygearslib import sv_src_path
        registry('SVGenSystemVerilogPaths').append(sv_src_path)

    outdir = registry('SimArtifactDir')
    if 'SimSocketHooks' in registry('SimConfig'):
        hooks = registry('SimConfig')['SimSocketHooks']
    else:
        hooks = {}

    rtl_node = svgen(gear, outdir=outdir)
    sv_node = registry('SVGenMap')[rtl_node]

    port_map = {
        port.basename: port.basename
        for port in itertools.chain(rtl_node.in_ports, rtl_node.out_ports)
    }

    structs = [
        svgen_typedef(port.dtype, f"{port.basename}")
        for port in itertools.chain(rtl_node.in_ports, rtl_node.out_ports)
    ]

    base_addr = os.path.dirname(__file__)
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(base_addr),
        trim_blocks=True,
        lstrip_blocks=True)
    env.globals.update(zip=zip, int=int, print=print, issubclass=issubclass)

    context = {
        'intfs': list(sv_node.sv_port_configs()),
        'module_name': sv_node.sv_module_name,
        'dut_name': sv_node.sv_module_name,
        'dti_verif_path': os.path.abspath(
            os.path.join(ROOT_DIR, 'sim', 'dpi')),
        'param_map': sv_node.params,
        'structs': structs,
        'port_map': port_map,
        'out_path': outdir,
        'hooks': hooks,
        'activity_timeout': 1000  # in clk cycles
    }
    context['includes'] = []
    context['imports'] = registry('SVGenSystemVerilogImportPaths')

    if pygearslib is not None:
        context['includes'].append(
            os.path.abspath(os.path.join(sv_src_path, '*.sv')))

    context['includes'].append(
        os.path.abspath(os.path.join(ROOT_DIR, '..', 'svlib', '*.sv')))
    context['includes'].append(
        os.path.abspath(os.path.join(ROOT_DIR, 'cookbook', 'svlib', '*.sv')))
    context['includes'].append(os.path.abspath(os.path.join(outdir, '*.sv')))

    for templ, tname in zip(j2_templates, j2_file_names):
        res = env.get_template(templ).render(context)
        fname = save_file(tname, context['out_path'], res)
        if os.path.splitext(fname)[1] == '.sh':
            os.chmod(fname, 0o777)


class SimSocketDrv:
    def __init__(self, handler, port):
        self.handler = handler
        self.port = port

    def reset(self):
        pass


class SimSocketInputDrv(SimSocketDrv):
    def close(self):
        self.handler.sendall(b'\x00\x00\x00\x00')
        self.handler.close()
        # del self.handler

    def send(self, data):
        pkt = u32_repr(data, self.port.dtype).tobytes()
        self.handler.sendall(pkt)

    def ready(self):
        try:
            self.handler.recv(4)
            return True
        except socket.error:
            return False


class SimSocketOutputDrv(SimSocketDrv):
    def read(self):
        buff_size = math.ceil(int(self.port.dtype) / 8)
        if buff_size < 4:
            buff_size = 4
        if buff_size % 4:
            buff_size += 4 - (buff_size % 4)
        try:
            data = self.handler.recv(buff_size)
            return u32_bytes_decode(data, self.port.dtype)
        except socket.error:
            raise CosimNoData

    def ack(self):
        try:
            self.handler.sendall(b'\x01\x00\x00\x00')
        except socket.error:
            raise GearDone


class SimSocketSynchro:
    def __init__(self, handler):
        self.synchro_handler = handler

    def cycle(self):
        pass

    def forward(self):
        try:
            self.synchro_handler.sendall(b'\x00\x00\x00\x00')
            self.synchro_handler.recv(4)
        except socket.error:
            raise GearDone

    def back(self):
        try:
            self.synchro_handler.sendall(b'\x00\x00\x00\x00')
            self.synchro_handler.recv(4)
        except socket.error:
            raise GearDone

    def sendall(self, pkt):
        self.synchro_handler.sendall(pkt)

    def recv(self, buff_size):
        return self.synchro_handler.recv(buff_size)


class SimSocket(CosimBase):
    def __init__(self, gear):
        super().__init__(gear)

        # Create a TCP/IP socket
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

        # Bind the socket to the port
        server_address = ('localhost', 1234)
        print('starting up on %s port %s' % server_address)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        self.sock.bind(server_address)

        # Listen for incoming connections
        self.sock.listen(len(gear.in_ports) + len(gear.out_ports))
        self.handlers = {}

        registry('SimConfig')['SimSocket'] = self

    def finish(self):
        print("Closing socket server")
        super().finish()

        self.sock.close()

    def send_req(self, req, dtype):
        # print('SimSocket sending request...')
        data = None

        # Send request
        pkt = req.to_bytes(4, byteorder='little')
        self.handlers[self.SYNCHRO_HANDLE_NAME].sendall(
            b'\x01\x00\x00\x00' + pkt)

        # Get random data
        while data is None:
            try:
                buff_size = math.ceil(int(dtype) / 8)
                if buff_size < 4:
                    buff_size = 4
                if buff_size % 4:
                    buff_size += 4 - (buff_size % 4)
                data = self.handlers[self.SYNCHRO_HANDLE_NAME].recv(buff_size)
            except socket.error:
                print('SVRandSocket: socket error on {SVRAND_CONN_NAME}')
        data = u32_bytes_decode(data, dtype)
        return data

    def setup(self):
        atexit.register(self.finish)

        sv_cosim_gen(self.gear)

        self.loop = asyncio.get_event_loop()

        print(self.gear.argnames)

        total_conn_num = len(self.gear.argnames) + len(self.gear.outnames) + 1
        while len(self.handlers) != total_conn_num:
            print("Wait for connection")
            conn, addr = self.sock.accept()

            msg = conn.recv(1024)
            port_name = msg.decode()

            if port_name == self.SYNCHRO_HANDLE_NAME:
                self.handlers[self.SYNCHRO_HANDLE_NAME] = SimSocketSynchro(
                    conn)
                conn.setblocking(True)
            else:
                for p in self.gear.in_ports:
                    if p.basename == port_name:
                        self.handlers[port_name] = SimSocketInputDrv(conn, p)
                        break
                for p in self.gear.out_ports:
                    if p.basename == port_name:
                        self.handlers[port_name] = SimSocketOutputDrv(conn, p)
                        break
                conn.setblocking(False)

            print(f"Connection received for {port_name}")
