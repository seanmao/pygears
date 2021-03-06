import os
from pygears.hdl import synth
from pygears.util.test_utils import skip_ifndef


def test_extern_design(tmpdir):
    skip_ifndef('SYNTH_TEST')

    test_dir = os.path.dirname(__file__)

    report = synth(
        'vivado',
        design=os.path.join(test_dir, 'design.py'),
        top='/qdeal',
        outdir=tmpdir,
        util=True,
        build=True)

    assert 'util' in report


# def test_makefile(tmpdir):

#     test_dir = os.path.dirname(__file__)

#     synth(
#         'vivado',
#         design=os.path.join(test_dir, 'design.py'),
#         top='/qdeal',
#         outdir=tmpdir,
#         makefile=True)

#     os.system(f'cd {tmpdir}; make')

# test_extern_design('/tools/home/tmp/test_synth')
