# node8 USB host on 5.10 (step8t + step7q): root cause and proposed fix (paper only, 2026-11-13)

> Outcome: this paper analysis became step8u + step7r, booted on node7 and node8 on 2026-11-13 with every prediction met (USB 2 and SuperSpeed hubs, firmware 50.29, no boot stall; README.md).

> Some files named below aren't in this repo: `step7q.dts`, `review/SUMMARY.md`, draft patches and
> `.config` fragments (now part of `patches/` and `config/`), and paths in the private build workspace
> (`~/scratch/jetson-r35`). `MALU-N` are private ticket numbers.

Nothing was built, booted or changed on any node. Labels: **[V-src]** verified by reading source in
`~/scratch/jetson-r35/k` (identical to NVIDIA's pristine R35 for every USB file: `xhci-tegra.c`, `phy/tegra/xusb*.c`,
`xusb.h`, `soc/tegra/pmc.c`, `clk-tegra210.c`, `xhci.c` all `diff -q` clean); **[V-node]** verified from read-only
node data (journal, `/proc/device-tree`, `/sys`, `/proc/interrupts`, files in `/lib/firmware`); **[J]** judgement or
memory, not verified.

Files here: `step7q.dts` (step7q DTB decompiled with the build tree's own `out/scripts/dtc/dtc`; the DTB chain is
`out/tegra210-p3450-0000-mainline.dtb` + `out/step*-add.dtsi`, no fragment touches USB), `step7r-add.dtsi` (proposed
DT fragment), `xusb-tegra210-usb3-function-name.patch` (proposed driver patch), `xusb-firmware.config` (proposed config).

## Summary

There isn't one bug but three, stacked. Only the first is visible today; each of the others would stop USB as soon as
the one before it is fixed.

1. **The -22 (root cause of the log line).** NVIDIA's R35 `xhci-tegra.c` reads the pad controller's wake IRQ from the
   **padctl** DT node, and the mainline 5.10 DT (`tegra210.dtsi`, which step7q is built from) gives padctl no
   `interrupts`. `of_irq_get()` returns -EINVAL and the probe returns it without printing anything.
2. **Firmware can't load at that point in boot.** Once (1) is fixed, the probe gets to `request_firmware()` at ~2.5 s,
   before the root file system is mounted (~4.0 s) and with no initrd. With `CONFIG_FW_LOADER_USER_HELPER_FALLBACK=y`
   that means a 60 s boot stall and then `failed to request firmware: -110`.
3. **Nothing powers the on-board USB hub.** All four USB-A ports go through the carrier's hub. Its 3.3 V rail
   (`VDD_HUB_3V3`, GPIO A6) is referenced only from `usb3-0`'s `vbus-supply`, which NVIDIA's 5.10 padctl driver never
   reads. So the regulator stays off and holds A6 low. On 4.9, A6 is a gpio-hog driven high from boot.

There's also a fourth problem, which limits speed but doesn't stop USB: **SuperSpeed can't come up** with NVIDIA's
`xusb-tegra210.c`. Its lane-function table calls the USB3 function `"xusb"`, but the PCIe-lane `power_on`/`power_off`
test for `"usb3-ss"` (mainline's name). With the DT's `"xusb"` (needed, or padctl doesn't probe at all; MALU-420 boot
6), the USB3 PHY power-on is skipped. Without a fix the ports would run at USB 2.0 (480 Mb/s) only.

"Prod-settings is not available" is a warning only (section 5).

## 1. The -22, step by step

Probe: `drivers/usb/host/xhci-tegra.c:2775 tegra_xusb_probe()`. Every failure from the clocks onwards prints a
`dev_err` first; the log has none, so the -22 comes from an early silent `return`. Going through them in order:

| Step (line) | Can it be a silent -22 here? |
|---|---|
| `tegra_xusb_parse_dt` (2795) | void |
| `tegra_xusb_init_context` (2802, body 1848) | only -ENOMEM |
| `devm_ioremap_resource` / `devm_platform_ioremap_resource` x3 | -EINVAL only for a NULL resource, and that prints "invalid resource"; DT has 3 `reg` entries **[V-node]** |
| `platform_get_irq` 0 and 1 | DT has 2 interrupts **[V-node]** |
| wake IRQs (2843-2866, `return -EINVAL` at 2865) | skipped: `tegra210_soc` (4247) sets no `num_wakes` **[V-src]** |
| `tegra_xusb_padctl_get` (2868; `phy/tegra/xusb.c:1402`) | -EINVAL only without `nvidia,xusb-padctl` (present). Not -EPROBE_DEFER either, because padctl is bound: `/sys/bus/platform/devices/7009f000.padctl/driver -> tegra-xusb-padctl` **[V-node]** |
| `of_parse_phandle(... "nvidia,xusb-padctl")` (2875) | -ENODEV, not -EINVAL |
| **`tegra->padctl_irq = of_irq_get(np, 0)` (2882-2884)** | **yes** |

```c
/* xhci-tegra.c:2882 */
	tegra->padctl_irq = of_irq_get(np, 0);          /* np = the padctl node */
	if (tegra->padctl_irq <= 0)
		return (tegra->padctl_irq == 0) ? -ENODEV : tegra->padctl_irq;
```

- **Why it's -EINVAL [V-src]:** `of_irq_get` (`drivers/of/irq.c:387`) calls `of_irq_parse_one`. With no
  `interrupts-extended`, that finds the parent (the root's `interrupt-parent`, the LIC) and reads `interrupts` through
  `of_property_read_u32_index` (`irq.c:325`). That call returns `of_find_property_value_of_size()`'s `-EINVAL` for a
  missing property (`drivers/of/property.c:111`).
- **padctl has no `interrupts` [V-node]:** node8's `/proc/device-tree/padctl@7009f000/` lists `avdd-pll-uerefe-supply
  avdd-pll-utmip-supply compatible dvdd-pex-pll-supply hvdd-pex-pll-e-supply name pads phandle ports reg reset-names
  resets status`, and `step7q.dts:1301` matches. The source is mainline 5.10's `tegra210.dtsi:1041` (the node has no
  interrupts). R32's `xusb_padctl` node doesn't have one either: R32's xhci took this IRQ as its own third interrupt
  (`platform_get_irq(pdev, 2)`, 4.9 `xhci-tegra.c:3453`).
- **The same failure on another board [V-node]:** node7 was moved to the Debian/5.10 card while this was
  written (see "Notes"). Its first 5.10 boot logs the same two lines.
- **What the IRQ is [V-src, V-node]:**
  - R32 DT `tegra210-soc-base.dtsi`: `xusb@70090000 interrupts = <0 39 4>, <0 40 4>, <0 49 4>`, and the
    `tegra21x-padctl-uphy` node also has `interrupts = <0 49 0x04>`.
  - node7 under 4.9: `/proc/interrupts` showed `62: 0 0 0 0 LIC 49 Level 70090000.xusb` (0 interrupts since boot),
    and `/proc/device-tree/xusb@70090000/interrupts` = `<0 0x27 4> <0 0x28 4> <0 0x31 4>`.
  - The T194 R35 DT gives its padctl node `interrupts = <0 167 0x4>`; that's the layout NVIDIA's 5.10 driver expects.
  - On node8, SPI 49 is free (`/proc/interrupts`). Nothing else in 5.10 requests the padctl IRQ: the padctl driver
    and `tegra-xudc` have no IRQ code for it.
  - [J] From memory, mainline later added `interrupts = <GIC_SPI 49 IRQ_TYPE_LEVEL_HIGH>` to T210's padctl together
    with XUSB ELPG support (around 5.14). Not checked.
- **Handler [V-src]:** `tegra_xusb_padctl_irq` (2314) only calls `pm_runtime_resume`. It's requested threaded,
  `IRQF_ONESHOT` (3171). 4.9 requested the same line `IRQF_ONESHOT | IRQF_TRIGGER_HIGH` with a near-identical
  handler (4.9 `xhci-tegra.c:1704, 2973`), and that ran on node7 for years.
- **Small bug in passing [V-src]:** that `return` (and the -ENODEV one above it) skips `put_padctl`, leaking a padctl
  device reference and `np`. Cosmetic.

## 2. What the probe would hit next, on paper

These are the steps after 2884, checked against step7q and node8.

- **Clocks (2886-2946):** all 11 `clock-names` are present. Clock IDs match mainline's `tegra210.dtsi` (step7q
  usb@70090000). **[V-node]** for names; that each ID resolves in `clk-tegra210.c` is **[J]**: it's the unmodified
  mainline node.
- **Power domains (2948-2967):** `power-domains` present → `tegra_xusb_powerdomain_init` (2191) attaches
  `"xusb_host"`/`"xusb_ss"` by name, and both names are in the DT. The PMC `xusba`/`xusbc` nodes have clocks and resets
  (step7q.dts:1139-1158). `tegra-xudc` (700d0000) is bound with xusbb + xusba already **[V-node]**, so the xusba genpd
  works. The driver core doesn't auto-attach a device with 2 domains (`genpd_dev_pm_attach`, `domain.c:2642`)
  **[V-src]**.
- **Regulators (2969-2984):** `dvddio-pex` (VDD_PEX_1V05), `hvddio-pex` (VDD_1V8) and `avdd-usb` (VDD_3V3_SYS) are all
  registered and enabled **[V-node]** (`/sys/class/regulator`). No deferral.
- **PHYs (2993-3023):** `devm_phy_optional_get` for usb3-0..3, usb2-0..3 and hsic-0. DT `phy-names` = usb2-0, usb2-1,
  usb2-2, usb3-0, the same set 4.9 used on node7 **[V-node]**. `tegra_xusb_phy_enable` (1809) runs
  `phy_init`/`power_on` on each:
  - **usb2-1:** host mode, vbus = dummy today (log: `usb2-1: supply vbus not found, using dummy regulator`) → see 3.3.
  - **pcie-6 (usb3-0):** `tegra210_pcie_phy_init` → `tegra210_uphy_init`. The PEX UPHY is already up for the
    Ethernet PCIe link and is guarded (`if (pcie->enable) return 0;`, `xusb-tegra210.c:489`). PLLE HW sequencing is
    also guarded. `tegra210_pcie_phy_power_on` (2822) → see 3.4.
- **Firmware (3048-3055) → see 3.2.**
- **Partitions + Falcon:** `tegra_xusb_unpowergate_partitions` (2220, genpd `pm_runtime_get_sync`), then
  `tegra_xusb_config` (IPFS/FPCI), then `tegra_xusb_load_firmware` (1934). That's mainline's sequence, so registers are
  only touched after the partitions and clocks are up **[V-src]**.
- **IRQ and `usb_add_hcd`:** mailbox and padctl IRQ requests, `tegra_xusb_enable_firmware_messages`, then
  `tegra_xusb_init_usb_phy` (2560; usb-phy optional, can't fail except -ENOMEM).

## 3. Each problem and its fix

### 3.1 padctl IRQ (DT)

Add `interrupts = <0x00 0x31 0x04>` (SPI 49, level-high) to `padctl@7009f000`: `step7r-add.dtsi`.

- An alternative would be a driver patch making the IRQ optional. Rejected: the IRQ wakes the controller from ELPG,
  and dropping it would silently break wake.

### 3.2 Firmware load before the root file system (config)

**Timing [V-node]:** node8's current boot logs:

| Time | Event |
|---|---|
| 1.22 s | padctl probes |
| 2.4708 s | `pwm-regulator regulator@6: PWM regulator registration passed` |
| 2.4714 s | the xhci -22 |
| 2.483 s | first `deferred probe timeout, ignoring dependency` |
| 4.04 s | `VFS: Mounted root` |

That ordering is `deferred_probe_initcall` (`drivers/base/dd.c:314`): it flushes the deferred-probe work (xhci
retried), sets `initcalls_done`, then re-triggers (the i2c timeout lines). That work runs synchronously, before rootfs.

**Boot setup [V-node]:**
- `extlinux.conf` `debian` has no `INITRD`.
- `CONFIG_USB_XHCI_TEGRA=y` and `CONFIG_EXTRA_FIRMWARE=""` (`out/.config`).

**What happens then [V-src]:**
- The direct load fails (empty rootfs) → `firmware_fallback_sysfs`.
- `force_sysfs_fallback` = `CONFIG_FW_LOADER_USER_HELPER_FALLBACK` = y (`fallback_table.c:20`) → uevent → wait
  `loading_timeout` = 60 s (`fallback_table.c:21`).
- Nothing in early boot answers → `-ETIMEDOUT` (`firmware.h:108`) → probe fails -110, and the whole boot is held
  ~60 s before rootfs.
- On node8 at runtime, the gk20a fallback also went unanswered (it returned in 14 ms; why is unknown, not needed here).

**The blobs on node8 [V-node]:**

| File | Package | Size | sha256 | Header |
|---|---|---|---|---|
| `/lib/firmware/nvidia/tegra210/xusb.bin` (the name the 5.10 driver asks for, `tegra210_soc.firmware`) | firmware-misc-nonfree | 132608 | `48545468…a455` | v50.10, built 2015-08-10 |
| `/lib/firmware/tegra21x_xusb_firmware` (what 4.9 loads; node7 4.9 logged "Firmware timestamp: 2023-04-25 07:35:59 UTC, Version: 50.29 release") | nvidia-l4t-xusb-firmware | 126464 | `941873a6…2025` | v50.29 |

- Both headers are valid for the 5.10 `struct tegra_xusb_fw_header` (`xhci-tegra.c:284`): magic "XUSB" "FW" at 0x68,
  and `fwimg_len` at 0x64 equals the file size.

**Fix (`xusb-firmware.config`):** `CONFIG_EXTRA_FIRMWARE="nvidia/tegra210/xusb.bin"` and
`CONFIG_EXTRA_FIRMWARE_DIR=.../usb/firmware`, with the chosen blob copied to `usb/firmware/nvidia/tegra210/xusb.bin`.
- Built-in firmware is checked before the filesystem and before the fallback: `firmware_loader/main.c:738`
  **[V-src]**.
- The Image grows by ~126 KB (41.0 MB now).
- Which blob is **[J]**:
  - I'd take NVIDIA's v50.29: it's what this hardware ran on 4.9, and the 5.10 driver is NVIDIA's.
  - v50.10 is what mainline's driver was developed against. If 50.29 misbehaves (mailbox errors, controller not
    ready), it's the fallback to try.

**Alternatives:**
- **xhci-tegra as a module (`=m`):** loads after rootfs via udev, so no blob in the Image. Not chosen: NVIDIA's
  `xhci-tegra.c` may use unexported symbols (`tegrafw`, `tegra_hv_ivc_*`, powergate helpers); unverified, it would need
  a build to find out.
- **Ship an initrd:** works, but it's a new boot artefact.

### 3.3 Hub power (DT)

**Hardware [V-src/doc]:** the dev kit user guide (`nano-devkit-user-guide.txt:202-204`) says "All four are connected
to the Jetson Nano module via a USB 3.0 hub built into the carrier board". On node7 4.9 that hub enumerated as
`1-2 0bda:5411 480` and `2-1 0bda:0411 5000` **[V-node]**, i.e. on usb2-1 + usb3-0.

**R32 [V-src, V-node]:**
- The default GPIO config (`tegra210-porg-gpio-p3448-0000-b00.dtsi:75`) drives A6 high from boot. node7's live
  `gpio@6000d000/default/gpio-output-high` includes 0x06, and the `system-suspend-gpio` hog (A6, output-high) is okay.
- The plugin manager (`tegra210-porg-plugin-manager.dtsi`, fragment@5, ids 3449-0000-100/-200) makes the A6 rail
  `vdd-usb-hub-en` active-high and sets it as `usb2-1`'s `vbus-supply`. For -000 (A02) it's active-low open-drain.
- On node7 that override wasn't in the live DT (usb2-1 vbus = `vdd-ac-bat`), so there it was the hog that kept the
  hub on.

**5.10 today [V-src, V-node]:**
- step7q (mainline `tegra210-p3450-0000.dts:571`) has `VDD_HUB_3V3` (GPIO A6, active-high) only as `usb3-0`'s
  `vbus-supply`.
- NVIDIA's 5.10 padctl driver reads `vbus` only for USB2 ports: `xusb.c:794`. `struct tegra_xusb_usb3_port`
  (`xusb.h:361`) has no supply field.
- node8 `/sys/class/regulator/regulator.4`: `VDD_HUB_3V3 state=disabled users=0`.
- `regulator-fixed` without `regulator-boot-on` requests its GPIO `GPIOD_OUT_LOW` (`drivers/regulator/fixed.c:207-210`),
  so 5.10 actively holds A6 low, and step7q has no A6 hog.

**Fix (`step7r-add.dtsi`):** `usb2-1 { vbus-supply = <0x33>; }` (0x33 = `VDD_HUB_3V3`), as R32's fragment@5 does.
- `tegra210_usb2_phy_init` enables a host port's supply (`xusb-tegra210.c:1850`) during the xhci probe, before
  firmware load.
- The ignored `usb3-0` `vbus-supply` can stay.
- Alternative: `regulator-always-on` on `VDD_HUB_3V3`, which matches 4.9's real behaviour (always high). It also works
  if xhci fails, but then the hub draws power with no host behind it.

**Assumptions [J]:**
- node8's carrier is a -100/-200 (B0x) board, so A6 is active-high. Mainline's DT and R32's B00 GPIO default agree.
  The carrier EEPROM ID wasn't read.
- `VDD_HUB_3V3` is the hub's enable/supply, judging from the names and R32's wiring; no schematic available.

### 3.4 SuperSpeed lane (driver patch)

**The mismatch in `xusb-tegra210.c` [V-src]:**
- `tegra210_pcie_functions` (2624) = `"pcie-x1", "xusb", "sata", "pcie-x4"`, and `tegra210_usb3_port_map` (3182) maps
  ports with `"xusb"`.
- But `tegra210_pcie_phy_power_on`/`_off` (2844, 2859), `tegra210_sata_phy_power_on`/`_off` (3026, 3041) and the
  SATA UPHY setup (739) test `tegra_xusb_lane_check(lane, "usb3-ss")`. That's a `strcmp` against the lane's function
  name (`xusb.c:407`), so it's never true.
- So `tegra210_usb3_phy_power_on` (2656: SS_PORT_MAP, UPHY USB3 ECTL tuning, ELPG vcore-down and clamp release for
  the port) never runs, and the SS port stays mapped off and clamped.

**History and the other trees:**
- Mainline's DT name `"usb3-ss"` is rejected by this driver's `match_string` ("invalid function"). That's why step2
  switched pcie-5/6 to `"xusb"` (MALU-420 boot 6) **[V-src: mainline.dtb has usb3-ss, step2 onward xusb]**.
- R32's 4.9 driver uses `"xusb"` everywhere (4.9 `xusb-tegra210.c:507, 2324, 2372, ...`).
- The R35 file mixes the two.

**Fix:** `xusb-tegra210-usb3-function-name.patch`, five `"usb3-ss"` → `"xusb"` replacements. Notes:
- No locking change: `tegra210_usb3_phy_power_on` takes no lock and is already called with `padctl->lock` held, as in
  mainline.
- No effect on the Ethernet lane (pcie-0 is `"pcie-x1"`), and SATA is disabled.

**Without this patch:** USB works at high speed only. The hub's USB2 half enumerates on usb2-1, and no device appears
on bus 2 **[J]**.

## 4. Comparison with 4.9 (node7 before the swap, and R32 source)

| | 4.9 (R32.7.6, node7) | 5.10 step8t + step7q (node8) |
|---|---|---|
| Driver / compatible | 4.9 `xhci-tegra.c`, `nvidia,tegra210-xhci` | R35 `xhci-tegra.c`, `nvidia,tegra210-xusb` (mainline node) |
| padctl IRQ | xhci node's 3rd interrupt, SPI 49 (`platform_get_irq(pdev,2)`) | padctl node's 1st interrupt; **absent** |
| Firmware | `tegra21x_xusb_firmware` v50.29, `request_firmware_nowait`, `INITRD /boot/initrd` | `nvidia/tegra210/xusb.bin`, synchronous `request_firmware` in probe, no initrd |
| Hub enable (GPIO A6) | high from boot (gpio default + hog) | owned by `VDD_HUB_3V3`, held low, no consumer |
| USB3 lane function | `"xusb"`, power-on checks `"xusb"` | `"xusb"`, power-on checks `"usb3-ss"` |
| PHYs | usb2-0, usb2-1, usb2-2, usb3-0 | same |
| padctl prod-settings | `prod_c_bias`, `_bias_a02`, `_utmi0-3`, `_ss0-3`, `_hsic0-1` | none (warning) |
| SMMU | `iommus` on xusb | none: XUSB bypasses the SMMU (only GPU swgroup 0xaac enabled on node8 per `review/SUMMARY.md`, 2026-11-10) |
| Vbus supplies | usb2-0 vdd-usb-vbus, usb2-1 vdd-ac-bat (always-on), usb2-2 vdd-usb-vbus2: none GPIO-switched | usb2-0 VDD_5V_USB; usb2-1/2 dummy |

## 5. "Prod-settings is not available"

**[V-src]** `xusb-tegra210.c:3286-3289`: `devm_tegra_prod_get()` finds no `prod-settings` child in the padctl node, so
`prod_list = NULL`. Every use (2011, 2393, 2831, 3014) is `if (priv->prod_list)`, and a failed apply is only a
`dev_dbg`. So it's harmless for the probe.

- What's lost is NVIDIA's electrical tuning: UTMI pad drive (0x84/0xc4/0x104/0x144 mask 0x20 = 0x40), bias (0x284 mask
  0x3f = 0x3a, or 0x38 for "a02"), and the USB3 UPHY RX tuning (`prod_c_ss*`). Values read from node7's live DT before
  the swap. The driver's built-in values plus fuse calibration still apply.
- [J] Mainline runs this hardware without prod settings.
- Port them only if the first boot shows link errors. The 5.10 `tegra_prod` binding format would need checking first
  (unverified), and choosing `prod_c_bias` vs `_a02` needs the chip revision: node8 reports SKU A02 per
  `review/SUMMARY.md`.

## 6. Proposed fix and predictions

**Changes:**
1. DTB `step7r` = step7q + `usb/step7r-add.dtsi` (padctl interrupt; hub rail on usb2-1).
2. Kernel `step8u` = step8t + `usb/xusb-tegra210-usb3-function-name.patch` + `usb/xusb-firmware.config`, with
   `usb/firmware/nvidia/tegra210/xusb.bin` copied first from node8's `/lib/firmware/tegra21x_xusb_firmware` (check the
   sha256 above).
3. Docs/ticket as usual (MALU-421).

**Minimum to get USB 2.0 working:** 1 + the firmware config. The patch adds SuperSpeed.

**A cheap way to confirm the root cause alone:** boot step7r with the existing step8t kernel. Predicted (all from
source):
- No `-22`.
- `Direct firmware load for nvidia/tegra210/xusb.bin failed with error -2`, then `Falling back to sysfs fallback for:
  nvidia/tegra210/xusb.bin`.
- A ~60 s gap, then `failed to request firmware: -110` (twice) and `probe of 70090000.usb failed with error -110`.
- `VFS: Mounted root` at ~64 s instead of ~4 s.

That proves (1) and (2) without a kernel build, at the cost of a slow boot and no USB.

### Predicted boot log with all of it (first boot with `usbcore.autosuspend=-1`, see risks)

**Lines that stay:**
- `tegra-xusb-padctl 7009f000.padctl: Prod-settings is not available`
- `usb2-2: supply vbus not found, using dummy regulator`. The usb2-1 line disappears.

**New lines, in order:**
- `tegra-xusb 70090000.usb: Firmware timestamp: 2023-04-25 07:35:59 UTC, Version: 50.29 release`. With the
  Debian blob it would read `2015-08-10 09:47:54 UTC, Version: 50.10 release`.
- `xHCI Host Controller`, `new USB bus registered, assigned bus number 1`, then root hub `1d6b:0002`, `hub 1-0:1.0: USB
  hub found`.
- Bus 2 with `1d6b:0003` and `hub 2-0:1.0: USB hub found`.
- Root-hub port counts: not predicted (4.9's lines weren't captured before node7 was swapped).
- `usb 1-2: new high-speed USB device number 2 using tegra-xusb` (0bda:5411).
- With the patch: `usb 2-1: new SuperSpeed ... using tegra-xusb` (0bda:0411).

**State after boot:**
- `/sys/class/regulator`: `VDD_HUB_3V3 state=enabled users=1`.
- `/proc/interrupts`: three LIC lines for 70090000.usb/xhci (39, 40, 49). Line 49 should stay at 0 or near 0 with
  autosuspend off.
- `VFS: Mounted root` still ~4 s, and no "Falling back to sysfs fallback" for xusb.
- `lsusb -t` shows the hub at 480M (and 5000M with the patch). A USB stick in any USB-A port enumerates.

### Failure signatures (what each would mean)

| Symptom | Meaning |
|---|---|
| Still `-22` | the DT fragment didn't land (check `/proc/device-tree/padctl@7009f000/interrupts`) |
| `-16` / `failed to request padctl IRQ` | something else holds SPI 49 (nothing does today) |
| "Falling back to sysfs fallback" + 60 s + `-110` | the firmware isn't built in (name/dir wrong) |
| `DMA controller not ready 0x...` or `XHCI Controller not ready. Falcon state: 0x...` (-EIO) | Falcon didn't boot. Blob incompatibility or DMA/MC trouble; try the other blob, then look at SMMU/MC (XUSB DMA bypasses the SMMU here) |
| `failed to enable XUSB SS/Host partition` | PMC genpd problem |
| Root hubs register but no `1-2` device | hub not powered (A6 polarity wrong for this carrier, or the vbus-supply didn't attach) |
| `1-2` but no `2-1` | SuperSpeed lane still off: the patch isn't in, or a deeper UPHY issue. USB still works at 480M |
| `failed to get PHY ...` / `failed to enable PHYs` | lane/port mapping trouble in padctl |

## 7. Risks and making the test boot self-recovering

**Hang risks [J unless marked]:**
- **Register access to gated XUSB blocks.** That's the class that froze node7 twice with SDMMC. The probe sequence
  enables clocks/partitions before touching IPFS/FPCI/CSB **[V-src]**.
- **Runtime PM / ELPG** is the risky path: NVIDIA backported T210 ELPG (`tegra_xusb_enter_elpg` 3768, PMC sleepwalk,
  padctl wake) into a tree NVIDIA never shipped for T210, so it's never been run on this SoC.
  - Once the root hubs autosuspend (2 s), the controller powergates its partitions, and the padctl IRQ resumes it.
  - **First boot with `usbcore.autosuspend=-1`** keeps the root hubs, and so the controller, from runtime-suspending.
    [J] From USB core runtime-PM behaviour with a negative delay, not checked in this tree.
  - Turn ELPG on in a second boot.
  - Don't run system suspend.
- **Don't read anything under the xhci-tegra debugfs directory** (`tegra_xusb_debugfs_init`): those files read
  controller registers, the same class as the debugfs register dumps already banned on the Nanos.
- **Level IRQ storm on SPI 49:** low. Same line, trigger and handler shape as 4.9, which showed 0 counts on node7. A
  storm would be a busy CPU (threaded handler), not a wedge.
- **Power:** the hub rail plus USB devices add load on 5V_SYS. MALU-423 noted VIN sag on the thin barrel cables, so
  start with a low-power device (a USB stick, not a disk).
- **Firmware fallback:** if the config is wrong it's a 60 s stall, not a hang. The hung-task timeout (120 s) isn't
  reached.

**Self-recovering test:**
- Keep `DEFAULT debian` (step8t + step7q). Add a non-default entry (e.g. `LABEL usbtest`: Image-step8u + step7r,
  `panic=10`, `usbcore.autosuspend=-1`) and pick it once from the U-Boot menu over node8's serial console.
- A panic or hard lockup then reboots (panic=10; WDT0, 30 s) into the known-good DEFAULT. A soft wedge still needs a
  power cycle.
- **Caveat:** the serial console is the CP2102 on node7's USB, and node7 was switched to the Debian/5.10 card while this
  was written, where USB host doesn't work (same -22). Until node7 is back on 4.9 (its R32 card is the fallback per
  MALU-428), or another host takes the adapter, there's no way to pick a non-default entry.
- [J] The alternative (making the test entry DEFAULT with a userspace boot-once reset) doesn't recover from a hang
  before userspace.

## 8. Open questions / unverified

- Carrier revision and A6 polarity on node8 (assumed B0x active-high; EEPROM not read).
- Which firmware blob works best with NVIDIA's R35 driver (both headers valid; neither tried).
- Whether `CONFIG_USB_XHCI_TEGRA=m` would build (alternative to built-in firmware).
- ELPG/runtime PM correctness on T210 in this tree (only reviewed as far as noted).
- Whether prod settings matter for link quality, and the 5.10 prod binding format.
- Whether mainline added the padctl interrupt to `tegra210.dtsi` (memory only); doesn't change the fix.

## Notes

- **node7's 4.9 reference data** above was read before it was moved to the Debian/5.10 card. Everything run on node7
  was a read: `/proc/device-tree`, `/proc/interrupts`, `/proc/config.gz`, `dmesg`, `/lib/firmware` listings,
  `/sys/bus/usb/devices/*/{idVendor,idProduct,speed,product}` and extlinux.conf.
- `/sys/kernel/debug/gpio` wasn't read on node7. Nothing here depends on it.
