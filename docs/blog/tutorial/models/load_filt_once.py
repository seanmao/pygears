import numpy as np
import matplotlib.pyplot as plt
import matplotlib.image as mpimg

from pygears import gear, GearDone, config
from pygears.lib import accum, czip, flatten, mul, qrange, qround, queuemap, rom, saturate, sdp, serialize, drv, collect, qcnt, replicate, when
from pygears.typing import Queue, Uint, Ufixp, Array, saturate as type_saturate, Fixp, Int
from pygears.sim import sim, cosim

# config['debug/trace'] = ['*']


@gear
def filter(pixels: Queue[Uint[8]], coef: Queue[Fixp], *, window_num):
    coef_t = coef.dtype.data
    accum_t = Fixp[coef_t.integer + 2, coef_t.width + 2]

    window_cnt = replicate(when(coef['eot'], window_num), 3 * 3)

    mem_wr_data = czip(qcnt(coef, running=True, w_out=4, init=0),
                       coef) | flatten

    coef_rd = qrange(window_cnt['data']) \
        | flatten \
        | sdp(wr_addr_data=mem_wr_data, depth=16)

    return czip(pixels, coef_rd) \
        | queuemap(f=mul) \
        | accum(init=accum_t(0.0), cast=saturate) \
        | qround \
        | saturate(t=Uint[8])


orig_img = (mpimg.imread('../mushroom.png') * 255).astype(np.uint8)


@gear
async def window_drv(*, img) -> Array[Uint[8], 9]:
    for _ in range(3):
        for i in range(orig_img.shape[0] - 2):
            for j in range(orig_img.shape[1] - 2):
                # print(f'Calculating points {i},{j}')
                for k in range(orig_img.shape[2]):
                    yield orig_img[i:(i + 3), j:(j + 3), k].flatten()

    raise GearDone


id_coeffs = np.array([
    [0, 0, 0],
    [0, 1, 0],
    [0, 0, 0],
])

edge_coeffs = np.array([
    [0, 1, 0],
    [1, -4, 1],
    [0, 1, 0],
])

blur_coeffs = 1 / 9 * np.array([
    [1, 1, 1],
    [1, 1, 1],
    [1, 1, 1],
])

res = []

img_intf = window_drv(img=orig_img) | serialize
coef_intf = drv(
    t=Queue[Fixp[8, 16]],
    seq=[blur_coeffs.flatten(),
         edge_coeffs.flatten(),
         id_coeffs.flatten()])

filter(img_intf, coef_intf, window_num=30*30*3) \
    | int \
    | collect(result=res)

cosim('/filter', 'verilator')
sim('/tools/home/tmp/load_filt_once')
# print(res)

# print(res)
res_img_size = 30*30*3

res_img1 = np.array(res[:res_img_size], dtype=np.uint8)
res_img1.shape = (orig_img.shape[0] - 2, orig_img.shape[1] - 2,
                  orig_img.shape[2])

res_img2 = np.array(res[res_img_size:2*res_img_size], dtype=np.uint8)
res_img2.shape = (orig_img.shape[0] - 2, orig_img.shape[1] - 2,
                  orig_img.shape[2])

res_img3 = np.array(res[2*res_img_size:], dtype=np.uint8)
res_img3.shape = (orig_img.shape[0] - 2, orig_img.shape[1] - 2,
                  orig_img.shape[2])

ax1 = plt.subplot(1, 4, 1)
ax1.imshow(orig_img)
ax2 = plt.subplot(1, 4, 2)
ax2.imshow(np.array(res_img1, dtype=np.uint8))
ax3 = plt.subplot(1, 4, 3)
ax3.imshow(np.array(res_img2, dtype=np.uint8))
ax4 = plt.subplot(1, 4, 4)
ax4.imshow(np.array(res_img3, dtype=np.uint8))

plt.show()
