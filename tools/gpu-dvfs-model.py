#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Paper model of 5.10/R32 T210 GPU DVFS voltages (set_gpu_dvfs_data, tegra210-dvfs.c:1899) for the Nano.
Inputs: NA_FREQ_CVB_TABLE (tegra210-dvfs.c:665-686), gpu_cvb_dvfs_table speedo id 2 (pll_min 818, max_mv 1150),
DT gpu-max-freq 921600 kHz, GPU scaling trips -25/15/30/50/70/105 C with a 950 mV floor on the first,
VDD_GPU alignment offset 708000 uV / step 10000 uV (logged by both nodes). Rounding as in drivers/clk/tegra/cvb.c.
Check: speedo 2060 reproduces node8's live /sys/kernel/debug/tegra_dvfs/gpu_dvfs_t exactly."""
import sys

def drc(n, d):  # DIV_ROUND_CLOSEST, d > 0
    return (n + d // 2) // d if n >= 0 else -((-n + d // 2) // d)

def cvb(s, c0, c1, c2, ss=100):
    return drc((drc(c2 * s, ss) + c1) * s, ss) + c0

def cvb_t(s, t, c3, c4, c5, ss=100, ts=10):
    return drc((drc(c3 * s, ss) + c4 + drc(c5 * t, ts)) * t, ts)

def round_cvb(mv, off=708000, step=10000, vs=1000):
    uv = max(mv * 1000, off * vs) - off * vs
    return (-(-uv // (step * vs)) * step + off) // 1000

def round_up(mv, off=708000, step=10000):
    uv = max(mv * 1000, off) - off
    return ((uv + step - 1) // step * step + off) // 1000

C0 = [814294, 856185, 898077, 939968, 981860, 1023751, 1065642, 1107534, 1149425, 1191317, 1233208, 1275100]
F = [76800 * (i + 1) for i in range(12)]
TRIPS = [-25, 15, 30, 50, 70, 105]
FLOORS = [round_up(max(818, 950)), 818, 818, 818, 818, 818]

for s in [int(a) for a in sys.argv[1:]] or [2060, 2117]:
    M = [[0] * len(F) for _ in TRIPS]
    for i, c0 in enumerate(C0):
        for j, t in enumerate(TRIPS):
            mv = max(round_cvb(cvb(s, c0, 8144, -940) + cvb_t(s, t, 808, -21583, 226)), FLOORS[j])
            M[j][i] = mv
            if j and M[j - 1][i] < mv:
                M[j - 1][i] = mv
    print("GPU speedo value", s)
    labels = ["  ..15", "15..30", "30..50", "50..70", "70..105", "105.."]
    print("T(C)\\F(kHz) " + " ".join("%7d" % f for f in F))
    for j, row in enumerate(M):
        print("%-11s " % labels[j] + " ".join("%7d" % v for v in row))
    print("nominal (vmin at fmax) %d mV\n" % min(1150, max(r[-1] for r in M)))
