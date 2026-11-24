# nano-kernel

A Linux 5.10 kernel for the original Jetson Nano (Tegra X1 / T210, 4 GB, B01 carrier), built from NVIDIA's R35 tree,
which NVIDIA never released for the Nano. NVIDIA's last Nano release (L4T R32.7.6) is kernel 4.9: no BTF and no
cgroup v2, so no Cilium, and no more patches. This kernel keeps CUDA working (R32.7.6's userspace: CUDA 10.2, cuDNN
8.2, TensorRT 8.2) on 5.10, so the Nanos can be Kubernetes nodes.

> **Warning.** This is an unofficial hobby project, not from or supported by NVIDIA, and it comes with **no warranty**.
> The patches change voltage, clock, thermal and memory-controller code: a wrong value can hang, corrupt or damage a
> board. It has run on two boards only, both 4 GB B01 developer kits. Keep a known-good SD card before you try it.
>
> **On any other Nano the GPU won't start as shipped.** The DVFS guard accepts only those two boards' fuse values and
> refuses every other chip (the rest of the board still boots): see [Chips differ](#chips-differ-the-dvfs-guard). Don't
> just remove the guard: derive your chip's entry.

**Status (2026-11-24):** kernel **step8u** (`5.10.216-tegra`) with DTB **step7r** runs on two Nanos, called node7 and
node8 here, as GPU nodes in a Kubernetes cluster on Debian 12. `MALU-N` in the docs, patches and log messages are
private ticket numbers; what matters from them is in the docs here.

| Path | What |
| --- | --- |
| `patches/step8u-kernel-5.10.patch` | Our changes to `kernel/kernel-5.10` (13 files) against NVIDIA's Jetson Linux R35.6.5 `public_sources`. |
| `patches/step8u-nvidia.patch` | Our changes to `kernel/nvidia` (5 files). |
| `patches/step8u-nvgpu.patch` | Our changes to `kernel/nvgpu` (5 files). |
| `config/config-step8u` | The exact `.config` (`O=out`). Differs from step8t only by `CONFIG_EXTRA_FIRMWARE`/`_DIR` (built-in xusb firmware). |
| `dts/tegra210-p3450-0000-step7r.dts` | step7r's full DT source (decompiled; compiles back to the booted DTB byte for byte, sha256 880e8ba6053e85d7386389a40dd4349f8e75f27964d7ff42cdc414a521fa29b2). It is mainline 5.10's `tegra210-p3450-0000.dts` plus the MALU-421 fragments (some from NVIDIA's R32 DT) and `step7r-add.dtsi`. |
| `dts/step7r-add.dtsi` | The step7q -> step7r fragment: padctl interrupt (SPI 49) and the hub rail (`VDD_HUB_3V3`) on usb2-1. |
| `tools/serial-menu.py` | Picks a U-Boot extlinux entry once over a serial console, for self-recovering test boots. |
| `tools/gpu-dvfs-model.py` | Recomputes R32's GPU DVFS table for a chip from its fuse values (reproduces node8's live table digit for digit). |
| `docs/analyses/` | The paper analyses behind step8u (`usb-host-5.10.md`, `dvfs-per-chip.md`) and R32's DVFS tables as dumped on node8. |

The working tree, NVIDIA's pristine sources, R32's 4.9 tree and the build output live outside the repo, in
`~/scratch/jetson-r35/` (19 GB: `k/` = this tree as built, `pristine/`, `r32/`, `out/`), the workspace path the docs
use. This repo holds what it takes to rebuild the kernel and DTB.

## What the patches change

The patches apply with `patch -p1` from `kernel/` of an unpacked R35.6.5 `public_sources` (paths `a/kernel-5.10/...`);
checked 2026-11-24: applied to NVIDIA's files they reproduce `k/` byte for byte (23 files). In brief:
- **DVFS / clocks / thermal:** core DVFS on T210 with R32's tables; CPU DFLL limited to R32's
  (`clk-tegra124-dfll-fcpu.c`); EMC scaling with actmon devfreq and DVFS prediction (`tegra30-devfreq.c`,
  `tegra210-emc-core.c`); the MALU-420 guard in `tegra210-dvfs.c` (below); R32 LA defaults; soctherm set_trips clamp;
  cbus WARN limited to pre-4.15.
- **SD (SDR104):** the retune-storm fix (Re-Tuning interrupt masked while held, TUNING_STATUS_RETUNE on the event,
  tap kept across runtime PM).
- **GPU (nvgpu):** R32 userspace ABI, railgating, gpcclk; nvmap allowed on T210; MC hot reset.
- **USB host:** `xusb-tegra210.c` checks the USB3 lane function "xusb" (was "usb3-ss": no SuperSpeed); firmware built
  in (the driver probes before root mounts; no initrd). With step7r's padctl interrupt and hub rail, all four USB-A
  ports work, USB 3 included (`docs/analyses/usb-host-5.10.md`).
- **Other:** SMMU fixes, i2c-tegra runtime-PM deadlock, r8168 MAC from DT, resolve_btfids duplicates, fuse cell.

## Chips differ: the DVFS guard

T210 DVFS picks voltage tables from per-chip fuses. node7's SoC process ID is 1 (node8: 0) and its GPU speedo value is
higher, so R32's tables give node7 lower nominals: core 1075 mV, GPU 1048 mV (node8 1125 / 1058). The MALU-420 guard
refuses to touch any regulator unless 5.10's nominals equal what R32 computes for the chip, keyed by its fuse values
(SoC speedo/process, GPU speedo/value). Unknown chips are refused: **a third Nano needs its entry** (derive it with
`tools/gpu-dvfs-model.py` and R32's tables, ideally checked against R32's own table on that chip) before its GPU will
start (`docs/analyses/dvfs-per-chip.md`). Measured on node7 under load: VDD_GPU 1015 mV at 921.6 MHz, VDD_SOC 900 mV.

## Memory

Linux sees 3697 MiB of 4096 (4.9: 3964). NVIDIA's 5.10 reserves a **256 MiB swiotlb** bounce buffer: its
`include/linux/swiotlb.h` sets `IO_TLB_SEGSIZE` to 131072 (upstream 128), and `swiotlb_init` rounds the 64 MiB default
up to that (0 slabs used when checked; `swiotlb=` can't shrink it, only a rebuild can). Candidate for MALU-425.

## Build

The workspace directory (`~/scratch/jetson-r35`) is mounted at `/w` in a rootless podman container, `jetson-r35-build`
(`docker.io/library/ubuntu:22.04` with `gcc-9-aarch64-linux-gnu` and pahole 1.22 at `/usr/local/bin/pahole122`).

**1. Sources and patches.** NVIDIA's Jetson Linux R35.6.5 sources,
<https://developer.nvidia.com/downloads/embedded/l4t/r35_release_v6.5/sources/public_sources.tbz2> (198362256 bytes,
sha256 `720a3d39bdfc03df86245233fb0815d94aab19bbe00ccb670d35cc90eda13f56`, as downloaded on 2026-11-24). Set `REPO` to
this repo's path:

```bash
cd ~/scratch/jetson-r35 && mkdir -p k out
tar xjf public_sources.tbz2 Linux_for_Tegra/source/public/kernel_src.tbz2
tar xjf Linux_for_Tegra/source/public/kernel_src.tbz2 -C k
cd k/kernel && for p in "$REPO"/patches/*.patch; do patch -p1 < "$p"; done
cp "$REPO/config/config-step8u" ../../out/.config
cp "$REPO/dts/tegra210-p3450-0000-step7r.dts" ../../
```

**2. Firmware.** The config builds in `nvidia/tegra210/xusb.bin` from `CONFIG_EXTRA_FIRMWARE_DIR`, `/w/usb/firmware`
in the container (change it if you mount the workspace elsewhere). So put NVIDIA's `tegra21x_xusb_firmware` at
`~/scratch/jetson-r35/usb/firmware/nvidia/tegra210/xusb.bin`. It's from `nvidia-l4t-xusb-firmware` 32.7.6 (any R32 Nano
has it at `/lib/firmware/`), v50.29, 126464 bytes, sha256
941873a6a70993b5c40a608cedc4608c281458c11949092d4bce125b96a92025; not kept here.

**3. Image and DTB:**

```bash
podman exec jetson-r35-build bash -c 'cd /w/k/kernel/kernel-5.10 && make -s ARCH=arm64 \
  CROSS_COMPILE=aarch64-linux-gnu- CC=aarch64-linux-gnu-gcc-9 LOCALVERSION=-tegra O=/w/out \
  PAHOLE=/usr/local/bin/pahole122 NVMAP_CONFIG_HANDLE_AS_FD=y -j$(nproc) Image'
podman exec -w /w jetson-r35-build /w/out/scripts/dtc/dtc -I dts -O dtb -o /w/out/tegra210-p3450-0000-step7r.dtb \
  tegra210-p3450-0000-step7r.dts
```

**4. Modules:** the same `make` with `modules`, then `modules_install INSTALL_MOD_PATH=<staging dir>`, gives
`lib/modules/5.10.216-tegra`. The two Nanos' modules (1146 .ko, signed) are step8r's build of the same tree;
step8s-step8u changed only built-in code, so they still match. Step 1 was rerun from NVIDIA's
archive on 2026-11-24 and gives the built tree; steps 2 and 4 are written up from the original build and not rerun.

**5. Install**, on an SD card that already boots through the Nano's U-Boot:

- The Image and DTB in `/boot/r35/`, the modules in `/usr/lib/modules/5.10.216-tegra` (then `depmod`), and R32's
  `/lib/firmware/{nvidia,tegra21x,gm20b}`.
- `tegra210_adma` blacklisted (`/etc/modprobe.d/`).
- A new `/boot/extlinux/extlinux.conf` entry, with DEFAULT left on the known-good one (see
  [Testing a new build](#testing-a-new-build-paper-first)):

```
LABEL step8u
      LINUX /boot/r35/Image-step8u
      FDT /boot/r35/tegra210-p3450-0000-step7r.dtb
      APPEND root=PARTUUID=<root> rw rootwait rootfstype=ext4 console=ttyS0,115200n8 net.ifnames=0 panic=10 systemd.unified_cgroup_hierarchy=1
```

## Userspace

CUDA needs R32.7.6's userspace (CUDA 10.2, cuDNN 8.2, TensorRT 8.2), the last one NVIDIA released for T210. On Debian 12
it comes from NVIDIA's apt repo (`repo.download.nvidia.com/jetson/{common,t210}`, `r32.7`): `nvidia-l4t-core`, `-init`,
`-firmware`, `-cuda`, `-3d-core` and `-xusb-firmware`, `cuda-toolkit-10-2`, `libcudnn8`, `libnvinfer8` and what they pull
in. Before installing any of it:

- **Pin NVIDIA's boot and kernel packages out.** They write the QSPI boot flash or replace the kernel. In
  `/etc/apt/preferences.d/`:

  ```
  Package: nvidia-l4t-bootloader nvidia-l4t-kernel nvidia-l4t-kernel-dtbs nvidia-l4t-kernel-headers nvidia-l4t-initrd nvidia-l4t-oem-config
  Pin: release *
  Pin-Priority: -1
  ```

- **Create `/opt/nvidia/l4t-packages/.nv-l4t-disable-boot-fw-update-in-preinstall` and keep it.** Without it
  `nvidia-l4t-xusb-firmware`'s postinst flashes QSPI. With it, `nvidia-l4t-init`'s postinst rewrites `/etc/fstab`,
  `hosts` and `hostname`: save and restore them.
- **Mask `nv-l4t-bootloader-config`** (shipped and enabled by `nvidia-l4t-init`): it configures the QSPI bootloader.

This is a summary; the deployment scripts for the two Nanos are in a private repo.

## Testing a new build (paper first)

Settle a change on paper first, so that boots confirm rather than explore (hangs cost power cycles): write the mechanism from the source of both the working
reference (R32 4.9) and the target (R35 5.10), with the TRM where registers are involved; write predictions, including
what would show failure; then boot only to confirm them.

- Every test boot self-recovers: an extlinux entry with `softlockup_panic=1 hung_task_panic=1
  sysctl.kernel.panic_on_rcu_stall=1` and `panic=10`, while DEFAULT stays on the known-good entry, picked once with
  `tools/serial-menu.py`. node8's serial console is the CP2102 on node7 (`/dev/ttyUSB0`, 115200 8N1; needs node7's USB,
  i.e. step8u). node7 has no console: there the test entry is DEFAULT and node7's R32 card is the way back.
- Never send a BREAK through that adapter. Never read clock-gated Tegra registers via `/dev/mem` or register-dump
  debugfs files (it froze a Nano twice).
- On a Nano's GPU, run one CUDA process at a time and budget memory first: about 2.4 GiB was spare
  under Kubernetes on these boards.

## Open

- MALU-425: port NVIDIA's GPU stack as out-of-tree modules onto a mainline kernel (research).
- The fan: 5.10 loads `pwm_fan` (states 0-3 = PWM 0/64/128/255) but no thermal zone binds it; a fitted 5 V PWM fan
  would stay at state 0 (a DT thermal binding, step7s, when a fan is fitted;
  MALU-463).

## Licence

GPL-2.0-only ([LICENSE](LICENSE)). Each patch hunk is under the licence of the file it changes: GPL-2.0 for all but
`tools/bpf/resolve_btfids/main.c` (LGPL-2.1 or BSD-2-Clause). The DT source derives from mainline Linux's and NVIDIA's
GPL-2.0 device trees. NVIDIA's firmware and userspace aren't included and have their own licences. NVIDIA, Jetson and
Tegra are NVIDIA's trademarks.
