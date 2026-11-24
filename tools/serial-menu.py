# SPDX-License-Identifier: GPL-2.0-only
# MALU-428: pick a Nano's U-Boot extlinux entry once, for a self-recovering test boot (DEFAULT stays on the known-good
# entry, so a panic reboots into it). node8's console is the CP2102 on node7 (/dev/ttyUSB0, 115200 8N1). Logs the
# console for DURATION s and, once, answers "Enter choice:" with CHOICE + CR. Writes only ordinary characters: never
# a BREAK through that adapter. If the prompt never shows, nothing is sent and the board boots its DEFAULT.
# usage (root, on node7, started before rebooting node8): python3 serial-menu.py <choice> <seconds> <logfile>
# The menu waits TIMEOUT 30 = 3 s; entries are numbered in file order from 1.
import os, sys, termios, time
dev, choice, duration, logf = "/dev/ttyUSB0", sys.argv[1], int(sys.argv[2]), sys.argv[3]
fd = os.open(dev, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
a = termios.tcgetattr(fd)
a[0] = 0; a[1] = 0; a[3] = 0                                   # iflag, oflag, lflag: raw
a[2] = termios.CS8 | termios.CREAD | termios.CLOCAL            # cflag: 8N1, no modem control, no hangup-on-close
a[4] = a[5] = termios.B115200
termios.tcsetattr(fd, termios.TCSANOW, a)
buf, sent, end = b"", False, time.time() + duration
with open(logf, "wb") as out:
    while time.time() < end:
        try:
            d = os.read(fd, 4096)
        except BlockingIOError:
            d = b""
        if d:
            out.write(d); out.flush(); buf = (buf + d)[-4096:]
            if not sent and b"Enter choice:" in buf:
                os.write(fd, choice.encode() + b"\r"); sent = True
                out.write(b"\n[[sent choice %s]]\n" % choice.encode()); out.flush()
        else:
            time.sleep(0.02)
os.close(fd)
print("sent" if sent else "not sent")
