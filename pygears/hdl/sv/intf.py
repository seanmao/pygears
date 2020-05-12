import fnmatch
import functools
from string import Template

from pygears import PluginBase, reg
from pygears.conf import Inject, inject, reg
from pygears.core.port import OutPort, InPort, HDLProducer
from .util import svgen_typedef
from pygears.hdl import hdlmod

dti_spy_connect_t = Template("""
dti_spy #(${intf_name}_t) _${intf_name}(clk, rst);
assign _${intf_name}.data = ${conn_name}.data;
assign _${intf_name}.valid = ${conn_name}.valid;
assign _${intf_name}.ready = ${conn_name}.ready;""")


class SVIntfGen:
    def __init__(self, intf, lang=None):
        self.intf = intf
        self.lang = lang
        if self.lang is None:
            self.lang = reg['hdl/lang']

    @property
    @functools.lru_cache()
    def traced(self):
        return any(
            fnmatch.fnmatch(self.intf.name, p)
            for p in reg['debug/trace'])

    @property
    @functools.lru_cache(maxsize=None)
    def _basename(self):
        producer_port = self.intf.producer
        if isinstance(producer_port, HDLProducer):
            #TODO: Not really a producer port
            producer_port = self.intf.consumers[0]

        port_name = producer_port.basename

        if isinstance(producer_port, InPort):
            return port_name
        elif ((not self.is_broadcast) and self.intf.consumers
              and isinstance(self.intf.consumers[0], OutPort)):
            return self.intf.consumers[0].basename
        elif hasattr(self.intf, 'var_name'):
            return self.intf.var_name
        elif self.sole_intf:
            return f'{producer_port.gear.basename}'
        else:
            return f'{producer_port.gear.basename}_{port_name}'

    @property
    def parent(self):
        prod_port = self.intf.producer

        if isinstance(prod_port, InPort):
            return prod_port.gear

        return prod_port.gear.parent

    @property
    @functools.lru_cache(maxsize=None)
    def basename(self):
        basename = self._basename
        if self.is_port_intf:
            return basename

        cnt = 0
        for c in self.parent.child:
            if hdlmod(c)._basename == basename:
                cnt += 1

        for c in self.parent.local_intfs:
            if c is self.intf:
                break

            if hdlmod(c)._basename == basename:
                cnt += 1

        for p in self.parent.out_ports:
            if p.basename == basename:
                cnt += 1

        if cnt == 0:
            basename = f'{self._basename}_s'
        else:
            basename = f'{self._basename}{cnt}_s'

        return basename

    @property
    def is_port_intf(self):
        if isinstance(self.intf.producer, InPort):
            return True
        elif ((not self.is_broadcast) and self.intf.consumers
              and isinstance(self.intf.consumers[0], OutPort)):
            return True
        else:
            return False


    @property
    def sole_intf(self):
        if self.intf.producer:
            return len(self.intf.producer.gear.out_ports) == 1
        else:
            return True

    @property
    def is_broadcast(self):
        return len(self.intf.consumers) > 1

    @property
    def outname(self):
        if self.is_broadcast:
            return f'{self.basename}_bc'
        else:
            return self.basename

    @inject
    def get_inst(self,
                 template_env,
                 spy_connect_t=Inject('svgen/spy_connection_template')):

        if self.intf.producer is None:
            return

        inst = []
        if not self.is_port_intf:
            inst.append(self.get_intf_def(self.basename, 1, template_env))

        if self.is_broadcast:
            inst.extend([
                self.get_intf_def(self.outname, len(self.intf.consumers),
                                  template_env),
                self.get_bc_module(template_env)
            ])

            if self.traced:
                for i in range(len(self.intf.consumers)):
                    intf_name = f'{self.outname}_{i}'
                    conn_name = f'{self.outname}[{i}]'
                    inst.extend(
                        svgen_typedef(self.intf.dtype, intf_name).split('\n'))

                    inst.extend(
                        spy_connect_t.substitute(
                            intf_name=intf_name,
                            conn_name=conn_name).split('\n'))

        for i, cons_port in enumerate(self.intf.consumers):
            if isinstance(cons_port, OutPort):
                if self.is_broadcast or isinstance(
                        self.intf.producer, InPort):
                    din_name = self.outname
                    index = ''
                    if self.is_broadcast:
                        index = f'[{i}]'

                    inst.append(
                        template_env.snippets.intf_intf_connect(
                            din_name, self.intf.consumers[i].basename, index))

        if self.traced:
            inst.extend(
                svgen_typedef(self.intf.dtype, self.basename).split('\n'))

            inst.extend(
                spy_connect_t.substitute(intf_name=self.basename,
                                         conn_name=self.basename).split('\n'))

        return '\n'.join(inst)

    def get_intf_def(self, name, size, template_env):
        ctx = {
            'name': name,
            'width': int(self.intf.dtype),
            'size': size,
            'type': str(self.intf.dtype)
        }

        return template_env.snippets.intf_inst(**ctx)

    def get_bc_module(self, template_env):
        inst_name = f'bc_{self.basename}'
        if inst_name.endswith('_if_s'):
            inst_name = inst_name[:-len('_if_s')]

        if self.lang == 'sv':
            bc_context = {
                'rst_name': 'rst',
                'module_name': 'bc',
                'inst_name': inst_name,
                'param_map': {
                    'SIZE': len(self.intf.consumers)
                },
                'port_map': {
                    'din': self.basename,
                    'dout': self.outname
                }
            }
        else:
            bc_context = {
                'rst_name': 'rst',
                'module_name': 'bc',
                'inst_name': inst_name,
                'param_map': {
                    'SIZE': len(self.intf.consumers),
                    'WIDTH': int(self.intf.dtype)
                },
                'port_map': {
                    'din': (self.basename, None, None),
                    'dout': (self.outname, None, None)
                }
            }

        return template_env.snippets.module_inst(**bc_context)


class SVGenIntfPlugin(PluginBase):
    @classmethod
    def bind(cls):
        reg['svgen/spy_connection_template'] = dti_spy_connect_t
