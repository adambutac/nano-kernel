# node7 GPU on 5.10 (step8t + step7q): why T210 DVFS fails on node7 and not node8 (paper only, 2026-11-13)

> Outcome: the per-chip guard (`patches/step8u-nvidia.patch`) went into step8u; on 2026-11-13 node7 started DVFS with exactly the derived nominals (core 1075 mV, GPU 1048 mV), ran the GPU at 1015 mV at 921.6 MHz and passed a 30-minute soak; node8 was unchanged (1125/1058).

> Some files named below aren't in this repo: `diagnosis.md` (this analysis's working name), draft patches and
> `.config` fragments (now part of `patches/` and `config/`), and paths in the private build workspace
> (`~/scratch/jetson-r35`). `MALU-N` are private ticket numbers.

Nothing was built, booted or changed on either node. Node reads were journal, `/sys/class/regulator`, thermal zone
temps, `/proc/version`, `/sys/kernel/debug/devices_deferred`, and the software-only DVFS tables in
`/sys/kernel/debug/tegra_dvfs/{gpu_dvfs_t,dvfs_table}`. Those two are printed from kernel arrays
(`dvfs_table_show`, `gpu_dvfs_t_show`, tegra-dvfs.c:2025/2173) and touch no registers.

Labels:
- **[V-src]**: read in source. "Ours" is `~/scratch/jetson-r35/k/kernel/...`, R32 is `~/scratch/jetson-r35/r32/k/...`,
  pristine is `~/scratch/jetson-r35/pristine/...`.
- **[V-node]**: read on the node.
- **[V-model]**: computed by `gpu-dvfs-model.py` (now in `tools/`). The model reproduces node8's live table exactly.
- **[J]**: judgement, not verified.

Files here:
- `diagnosis.md`: this file.
- `tegra210-dvfs-guard-per-chip.patch`: the proposed fix. It's a draft, not applied. Apply with `-p1` from
  `k/kernel/nvidia`; `patch --dry-run` was clean on a copy.
- `gpu-dvfs-model.py`: the paper model of GPU DVFS voltages.

## Summary

- **The hypothesis is disproved.** The core DVFS tables have a full SoC speedo 0 / process 1 section, and
  the selection code handles process 1. Nothing in table selection returns -EINVAL.
- **The -22 comes from our own MALU-420 guard.** It hard-codes node8's nominals: core 1125 mV and GPU 1058 mV.
- node7's fuses give different nominals: **core 1075 mV** (SoC process 1) and **GPU 1048 mV** (GPU speedo
  value 2117). These are exactly what R32's identical code computes for that chip.
- So the guard refuses, and the DVFS probe returns -EINVAL. The vdd-gpu rail therefore never becomes "ready", and
  nvgpu's platform probe defers forever.
- **The fix:** make the guard per chip, with a table keyed by fuse values that holds node8's pair (unchanged) and
  node7's pair. node8's runtime behaviour doesn't change.

## 1. The exact failing path

Both nodes run the same build (`#44 SMP PREEMPT Thu Nov 12 14:56:41 UTC 2026`, also the version string in
`out/Image-step8t`), and `Image-step8t` contains the guard string [V-node, V-src].
`CONFIG_TEGRA_210_DVFS=y` builds the DVFS driver into the Image (`out/.config:6967`) [V-src].

1. **Probe call.** `tegra_dvfs_probe` (nvidia/drivers/soc/tegra/tegra-dvfs.c:2357) calls `tegra210_init_dvfs`
   (tegra210-dvfs.c:2443), which calls `tegra210x_init_dvfs` (2293).
2. **Early attempts defer.** They return `-EPROBE_DEFER` at 2333-2336 until VDD_SOC and VDD_GPU exist. That produces
   the "Unable to get vdd-core/vdd-gpu rail ... defering probe" lines, identical on both nodes [V-node].
3. **Core nominal.** On the first attempt, `init_core_dvfs_table` (2320, which runs once) calls
   `get_core_nominal_mv_index` (1454), which calls `get_core_sku_max_mv` (1373). That function switches on
   `soc_process_id`:
   - node7, process 1: **`case 1: return 1075`** (1385-1386).
   - node8, process 0, speedo_rev 4: **`return 1125`** (1384).

   The ladder `core_voltages_mv` (tegra210-core-dvfs.c:17-19) contains both values (index 12 and index 14).
   `vdd_core_rail.nominal_millivolts` is set at 2129-2130. No error happens here [V-src].
4. **Core table rows.** The core table matches node7's speedo 0 / process 1 section at tegra210-core-dvfs.c:82-111
   (`CORE_DVFS(..., 0, 1, ...)`, 30 entries). It has the same clock set as the process 0 section (52-81) [V-src].
5. **GPU nominal.** `init_gpu_dvfs_table` calls `set_gpu_dvfs_data` (1899), which sets
   `nominal = min(max_mv 1150, vmin at fmax)` (2011-2012). That gives **1048** for GPU speedo value 2117 and
   **1058** for 2060 [V-node: both logged; V-model: reproduced].
6. **The guard fails.** Both nodes log the two lines for this:
   - node7: `tegra dvfs: vdd-core: nominal 1075mV ...` and `vdd-gpu: nominal 1048mV ...`
   - node8: `... 1125mV ...` and `... 1058mV ...`

   Then the MALU-420 guard at **tegra210-dvfs.c:2427-2437** runs [V-src, V-node]:
   ```c
   if (cpu_unmanaged && (vdd_core_rail.nominal_millivolts != 1125 ||
                         vdd_gpu_rail.nominal_millivolts != 1058)) {
           pr_err("tegra dvfs: MALU-420 guard: nominals core %dmV gpu %dmV differ from R32, not starting\n", ...);
           return -EINVAL;
   }
   ```
   `cpu_unmanaged` is true on both nodes, because the DT dvfs node has `nvidia,cpu-rail-unmanaged`. node7 logs
   `MALU-420 guard: nominals core 1075mV gpu 1048mV differ from R32, not starting` (journal line 423), then
   `tegra-dvfs: probe of dvfs failed with error -22` (line 425). **Both values differ, so either one alone would
   have tripped the guard.**
7. **Probe exits before the regulators.** `tegra_dvfs_probe` goes to `out` at tegra-dvfs.c:2369-2371, before
   `tegra_dvfs_regulator_init` (2373) and before `rail->is_ready = true` (2378). So DVFS never touched node7's
   regulators [V-src]:
   - VDD_SOC reads 1075000 µV, `min 600000`, `max 1162500` [V-node].
   - VDD_GPU is off (708000 µV) [V-node].
8. **nvgpu waits forever.** In `gk20a_tegra_probe` (nvgpu/.../platform_gk20a_tegra.c:908),
   `tegra_dvfs_get_rail_by_name("vdd-gpu")` finds the rail. The rail lists were built at 2319. But
   `tegra_dvfs_is_rail_ready()` is false, so the probe returns `-EPROBE_DEFER` (929-931).
   - `driver_common.c:414-417` logs that as `[INFO] platform probe failed`. INFO is the defer branch.
   - `devices_deferred` on node7: `57000000.gpu`, `core_dvfs_cdev_floor`, `core_dvfs_cdev_cap`, `7000c700.i2c`.
   - On node8 it's only `7000c700.i2c`, which is pre-existing [V-node].
   - `/dev` has `nvhost-ctrl` only on node7, against 13 nvhost nodes on node8 [V-node].

**Why node8 passes:** its fuses give exactly the hard-coded pair, so the probe returns 0 [V-node, V-src]. The guard was
written in MALU-420 from node8's R32 numbers (comment at 2428: "on node8, R32 (4.9) computes core 1125mV and gpu
1058mV"; MALU-420 ticket log).

**Why the nominals differ (fuses, not code):**
- **SoC process id** comes from `soc_process_speedos[0] = {1950, 2073, UINT_MAX}` (speedo-tegra210.c:48-52):
  - node7, SoC speedo 1982: process 1.
  - node8, 1948: process 0, two counts under the threshold.
- **GPU nominal:** the GPU CVB voltage falls with speedo value. node7's 2117 needs 10 mV less at 921.6 MHz than
  node8's 2060.

[V-src, V-node]

## 2. What R32 (4.9) does for SoC speedo 0 / process 1

Everything that feeds the two nominals is identical in R32 and ours [V-src]:

| Input | R32 4.9 (r32/k/kernel/kernel-4.9/...) | Ours |
|---|---|---|
| Process-id thresholds | fuse/speedo-tegra210.c:59-63 `{1950, 2073, UINT_MAX}` | identical (speedo-tegra210.c:48-52) |
| SoC speedo value | `soc_speedo[0]` for fuse rev >= 3 (both nodes: rev 4). R32 reads `FUSE_CPU_SPEEDO_2` into `soc_speedo[2]`, ours `FUSE_SOC_SPEEDO_2`; `soc_speedo[2]` is unused at rev >= 3 | same value |
| Core max mV | drivers/soc/tegra/tegra210-dvfs.c:1368-1389, `case 1: return 1075` (1380-1381) | identical (1373-1394) |
| Core min mV | 1391-1416: SKU 0x8f gives 800 | identical |
| Core ladder and process 1 rows | tegra210-core-dvfs.c:17-19 and 82-111 | file identical |
| GPU CVB | tegra210-dvfs.c:686 (921600 row: c0 1275100, c1 8144, c2 -940, c3 808, c4 -21583, c5 226), speedo id 2 entry pll_min 818 / max_mv 1150 (around 770), nominal at 1994 | identical (684, 768, 2011) |
| NA GPCPLL | `CONFIG_TEGRA_USE_NA_GPCPLL=y` (tegra_defconfig:1095) | `=y` (out/.config:6960; node7 /proc/config.gz) |
| GPU max freq | `nvidia,gpu-max-freq-khz = <921600>` (hardware/.../tegra210-porg-p3448-common.dtsi:794) | `<0xe1000>` = 921600 |
| GPU scaling trips | -25/15/30/50/70/105 C, 950 mV on the first (tegra210-power-dvfs.dtsi:48-50, tegra210-porg-thermal.dtsi:163-189) | identical (step7q, vdd-gpu-scaling-cdev) |

The full diff of `tegra210-dvfs.c` (R32 vs pristine R35) is only kernel-API glue, EMC-related changes and the
`cpu_lp` id check. No table or core/GPU selection logic differs. `tegra210-core-dvfs.c` has no diff [V-src].

**What R32 computes for node7's chip:**
- **Core:** nominal 1075 mV (ladder index 12), min 800 mV. Thermal floor 950 mV below 15 C
  (`tegra210_core_therm_floors`, tegra210-dvfs.c:80-83). Cap 1132 mV above 86 C (85-89), which is above nominal, so
  it's inert.
- **Core rows used** (process 1). Each clock's rate at 1075 mV equals node8's process 0 rate at 1125 mV, for every
  clock in the table [V-src, compared by script]. Examples (at 800 … 1075 mV):
  - vic03: 192.0 … 627.2 MHz
  - cbus: 268.8 … 793.6 MHz
  - host1x: 81.6 … 408 MHz
  - sclk: 140.8 … 408 MHz
- **Process 1 never needs more voltage.** At every one of the 15 ladder voltages, every process 1 rate is >= the
  process 0 rate (0 exceptions). So for any clock rate, node7 needs the same or less core voltage than node8.
- **GPU:** nominal 1048 mV. The rail minimum is 818 mV, or 958 mV below 15 C. The full table [V-model] (the same
  model prints node8's live `gpu_dvfs_t` digit for digit):

```
T(C)\F(kHz)   76800  153600  230400  307200  384000  460800  537600  614400  691200  768000  844800  921600
  ..15          958     958     958     958     958     958     958     958     958     958     998    1048
15..30          818     818     818     818     818     818     818     858     898     938     988    1028
30..50          818     818     818     818     818     818     818     848     898     938     978    1018
50..70          818     818     818     818     818     818     818     848     888     928     968    1018
70..105         818     818     818     818     818     818     818     848     888     928     968    1008
105..           818     818     818     818     818     818     818     838     888     928     968    1008
```

**Not observed:** nobody dumped R32's DVFS table on node7 (`r32-dvfs-table.txt` is node8's: process 0 rows, gpu 1038
at 921.6 MHz). The node7 values above are derived, not measured.

**Corroboration:** node7's VDD_SOC sits at 1075 mV today, with nothing in Linux having set it [V-node]. [J] That is
most likely the boot firmware programming the core rail to the chip's nominal, the same 1075 that R32's table gives.

## 3. Proposed fix (draft: `tegra210-dvfs-guard-per-chip.patch`)

- **What changes.** The guard compares the computed nominals with a per-chip table, keyed by the fuse values the
  nominals come from (`soc_speedo_id`, `soc_process_id`, `gpu_speedo_id`, `gpu_speedo_value`):

  | Chip | Fuses | Core | GPU | Source |
  |---|---|---|---|---|
  | node8 | 0, 0, 2, 2060 | 1125 | 1058 | R32 measured |
  | node7 | 0, 1, 2, 2117 | 1075 | 1048 | on paper, above |

- **Unknown chips and mismatches** are still refused. The error line now also prints the fuse values.
- **One file:** `nvidia/drivers/soc/tegra/tegra210-dvfs.c`. It adds a 6-int struct, a 2-row table and a 15-line match
  function before `tegra210x_init_dvfs`, and replaces the condition at 2431-2432. Nothing before the guard changes.
- **node8 is unchanged at runtime** [V-src, by reading the patch]:
  - For node8's fuses the new condition is true exactly when the old one was (core == 1125 && gpu == 1058).
  - No new log line on success; the error text only appears on failure.
  - The Image bytes differ (new code and string), but node8's DVFS tables, voltages, log output and return values
    don't.
- **Alternatives rejected:**
  - Dropping the guard would allow any chip.
  - A DT property won't work, because both nodes boot the same DTB.
  - Bounds checks (nominal <= SKU max, <= regulator max) are weaker than "equals R32 for this chip", which was the
    point of MALU-420.
- **Not compiled** (no building allowed). [J] It uses only `tegra_sku_info` int fields (fuse.h:132-139) and file
  statics already used in this file, so it should compile cleanly.

## 4. Predictions for node7's first boot with the fix

These assume Image = step8t + this patch (USB changes are optional, see §6) and DTB step7q or step7r.

**Kernel log:**
- **Unchanged:** the five `no clock found` lines, `Unable to get vdd-core rail`, `Unable to get vdd-gpu rail`,
  `tegra210_dvfs: emc dvfs from EMC tables, max 1600000 kHz`, `tegra_dvfs: GPU-cap: registered`.
- `tegra dvfs: vdd-core: nominal 1075mV, offset 600000uV, step 12500uV, scaling enabled` [V-src: same computation
  as today's log line 421].
- `tegra dvfs: vdd-gpu: nominal 1048mV, offset 708000uV, step 10000uV, scaling enabled`.
- **Gone:** the `MALU-420 guard` line and `probe of dvfs failed with error -22`.
- **New, as on node8:**
  - `tegra_dvfs: vdd-gpu-vts: registered`
  - `tegra_core_action core_dvfs_cdev_floor: Tegra CORE DVFS 'floor cooling device' registered`
  - the same line for `cap`
- **nvgpu:**
  - `gm20b_init_clk_setup_sw ... GPCPLL initial settings: NA mode, M=..., N=..., P=...`. NA mode because
    `gpu_speedo_id` is 2 (tegra210-dvfs.c:2028-2029). N and P aren't predicted; node8 shows M=1, N=34, P=3.
  - Then `gk20a_scale_init ... enabled scaling for GPU`, plus the benign `cde: could not fetch the firmware` that
    node8 also shows.
  - No `platform probe failed`.

**State after boot:**
- **Devices:**
  - `/dev`: 13 `nvhost-*` nodes, the same set as node8 (as-gpu, ctrl, ctrl-gpu, ctxsw-gpu, dbg-gpu, gpu, nvsched-gpu,
    power-gpu, prof-ctx-gpu, prof-dev-gpu, prof-gpu, sched-gpu, tsg-gpu).
  - `devices_deferred`: only `7000c700.i2c`.
- **VDD_SOC:**
  - Scaling starts from 1075 (no initial change: `dvfs_rail_connect_to_regulator` only lowers if above nominal,
    tegra-dvfs.c:448-455).
  - Then about **900000 µV idle and under TensorRT**, the same as node8 today (900000 [V-node]). EMC 1600 MHz needs
    900 mV in this DT's EMC table. That doesn't depend on process (`tegra210_predict_emc_rate`,
    tegra210-emc-core.c:1841). Other clocks need the same or less than on node8.
  - **Never above 1075000.** Not below 800000, or 950000 when the SoC is below 15 C.
- **VDD_GPU:**
  - About 818 mV idle when powered (node8 reads 818772; [J] PWM quantisation). Rail-gated and off when idle is
    also fine.
  - At 921.6 MHz: 1028 mV (15-30 C), 1018 (30-70 C), 1008 (>70 C), 1048 only below 15 C.
  - **Never above 1048 mV.**
- **`/sys/kernel/debug/tegra_dvfs/dvfs_table`** (software table, safe to read):
  - `vdd-core 800 … 1075` (13 columns).
  - `vic03 192.0 192.0 307.2 345.6 409.6 486.4 524.8 550.4 576.0 588.8 614.4 614.4 627.2`.
  - `emc 204.0 ×4 then 1600.0 ×9` [J: assumes node7 reads the same EMC table as node8; same DTB].
  - `vdd-gpu` and `gbus` rows match the §2 table row for the current temperature. node7 sat at 26 C when this
    was written, which is the 15..30 row: `818 ×7, 858, 898, 938, 988, 1028`.
- **`gpu_dvfs_t`** equals the §2 table.
- **CUDA:** deviceQuery and the TensorRT/CUDA tests pass as on node8.

**Failure signatures:**

| Symptom | Meaning |
|---|---|
| Guard line now ends `(SoC speedo 0 process 1, GPU speedo 2 value 2117)` with nominals other than 1075/1048 | my derivation is wrong (e.g. trips or alignment differ on node7). Don't widen the table; re-derive. |
| Guard line with 1075/1048 and the old text | the patch isn't in the Image |
| Guard prints different fuse values | node7's fuse decode changed; the table row doesn't match |
| `tegra_dvfs: failed to connect`, `failed lower vdd-core voltage` | regulator problem, not table |
| `tegra-dvfs: rate ... too high for dvfs on <clk>` | some clock above its process 1 max at 1075 mV (not expected: maxima equal node8's) |
| DVFS OK but `57000000.gpu` still deferred / nvgpu errors | a separate nvgpu issue (MC reset, railgate), not DVFS |
| Oopses, GPU MMU/PBDMA faults or SD/EMC errors under load | voltage margin. Revert to step8t (GPU off, VDD_SOC fixed at 1075). |

## 5. Risks and unverified assumptions

**Voltages the fix would allow on node7** [V-src, V-model]:
- **VDD_SOC 800-1075 mV**, or 950-1075 below 15 C.
- **VDD_GPU 818-1048 mV**, or 958-1048 below 15 C.
- **Same as R32.** These are exactly R32's values for this chip, by identical code, tables and DT inputs; derived,
  not observed on node7 under R32.
- **Compared with node8:** node7 runs 50 mV less core maximum, and 10-20 mV less GPU voltage at each frequency. That
  is by design (faster silicon), and node8 is 2 counts from the process boundary.
- **Compared with node7 today:** the core rail drops from a fixed 1075 to about 900 idle (as node8 does since
  MALU-421 step7m), and the GPU rail is used for the first time.

**Risks [J unless marked]:**
- **Undervolt if the paper derivation misses an R32 input.** For example, an R32-only DT override on node7's
  carrier. The main mitigation is the guard itself: it still requires the computed nominals to equal the table
  values.
  - **Cheapest way to remove this risk:** boot node7's R32 card once (MALU-428 fallback) and read 4.9's DVFS table,
    the same way `r32-dvfs-table.txt` was taken on node8. Check that `vdd-core` ends at 1075, the process 1 rows
    match, and the GPU row at 921.6 MHz matches the §2 table. That turns the node7 row from "paper" into "measured".
- **node7 has no serial console.** The CP2102 is on node7, wired to node8's UART. A hang on node7's test boot needs a
  manual power cycle or a card swap. Use a non-default extlinux entry if node7's bootloader menu can be reached;
  otherwise accept a card-swap recovery.
- **The CPU DFLL OPP table is identical on both nodes** (900-1092 mV, logged), even though node7's CPU speedo is
  2114 against 2042. I didn't examine why. It's outside this fix and outside the GPU failure. The DFLL guard
  (`clk-tegra124-dfll-fcpu.c:840-866`) passes on both.
- **The table is static per chip.** A swapped module or a third Nano is refused (intended). Any future change to the
  GPU trips, `gpu-max-freq-khz` or the regulator ranges in the DT will trip the guard on both nodes (intended).
- **Not compiled or run.**

**Unverified assumptions:**
1. R32 4.9 on node7 actually produced 1075/1048. This is derived from identical source and DT; not observed.
2. node7's VDD_SOC 1075 mV comes from the boot firmware's per-process nominal. Only the reading is verified.
3. node7's EMC timing table and min_volt values equal node8's. It's the same DTB, but the EMC row wasn't readable on
   node7 because DVFS never started.
4. VDD_GPU readback will show 818772 for 818 mV, as on node8.
5. The patch compiles without warnings.

## 6. Interaction with the USB fix (`~/scratch/jetson-r35/usb/`)

There's no file overlap [V-src]:
- **USB fix:**
  - `step7r-add.dtsi`: padctl `interrupts`, `usb2-1 vbus-supply`.
  - `xusb-firmware.config`: `CONFIG_EXTRA_FIRMWARE*`.
  - `xusb-tegra210-usb3-function-name.patch`: `kernel-5.10/drivers/phy/tegra/xusb-tegra210.c`, applied with `-p1`
    from `k/kernel/kernel-5.10`.
- **This fix:** only `nvidia/drivers/soc/tegra/tegra210-dvfs.c`, applied with `-p1` from `k/kernel/nvidia`.
- **It needs no DT change.** It works with step7q or step7r: step7r touches neither the `dvfs` node nor VDD_SOC or
  VDD_GPU.
- **Both need a new Image,** so one build works: "step8u" = step8t + both patches + the firmware config.

**Runtime interplay [J]:**
- **xusb clocks are core DVFS clocks** (`xusb_*_src`, at most 336 MHz, needing 850 mV on both process rows). With
  DVFS running on node7 they're under the 900 mV EMC floor, so there's no voltage change.
- **Without this fix, node7's xusb would run with no DVFS at all**, fixed at 1075. That's harmless, but it means the
  USB predictions differ between nodes only in that respect.
- **Test order:** the USB plan's self-recovering test entry needs node8's serial console, which is on node7's USB.
  Fixing USB on node7 (this build) is what makes that console reachable again from a 5.10 node7.
- **node8 sees no DVFS change** from this patch, so booting step8u on node8 tests only the USB changes there.

## Summary

- **Where it fails:** node7's DVFS probe fails at our MALU-420 guard (tegra210-dvfs.c:2431-2436), not in NVIDIA's
  table selection. The guard accepts only node8's nominals (1125/1058).
- **Why node7 is different:** its fuses (SoC process 1, GPU speedo value 2117) give 1075/1048. These are exactly the
  values R32 4.9's identical code and tables give for this chip.
- **Why there's no GPU:** the probe returns -22 before the regulators are connected, so `vdd-gpu` is never ready and
  nvgpu defers forever.
- **The fix:** a per-chip guard table, with node8's row unchanged and node7's row added (1075/1048).
- **node7 after the fix:**
  - VDD_SOC 800-1075 mV, about 900 idle.
  - VDD_GPU 818-1048 mV, 1018 at 921.6 MHz between 30 and 70 C.
  - 13 nvhost nodes and working CUDA.
- **Before relying on the node7 row:** a one-off R32 boot of node7 to read 4.9's DVFS table would turn its values from
  derived into measured.
