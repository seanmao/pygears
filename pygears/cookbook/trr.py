from pygears.core.gear import gear
from pygears.svgen.inst import SVGenInstPlugin
from pygears.svgen.svmod import SVModuleGen
from pygears.typing import Queue


def trr_type(dtypes):
    return Queue[dtypes[0]]


@gear
def trr(*din) -> b'trr_type(din)':
    pass


class SVGenTrr(SVModuleGen):
    @property
    def is_generated(self):
        return True

    def get_module(self, template_env):
        context = {
            'module_name': self.sv_module_name,
            'intfs': list(self.sv_port_configs())
        }
        return template_env.render_local(__file__, "trr.j2", context)


class SVGenTrrPlugin(SVGenInstPlugin):
    @classmethod
    def bind(cls):
        cls.registry['SVGenModuleNamespace'][trr] = SVGenTrr
