#!/usr/bin/env python3
"""Собрать МАЛЕНЬКИЙ FAT32-образ (test.img) для быстрых прогонов харнесса.
~512 КБ, кластеры по 512 байт, пара файлов + подкаталог + намеренно
осиротевшие (lost) кластеры. Нестрогая валидация FAT32 в плагине принимает его,
хотя число кластеров ниже строгого минимума FAT32.
"""
import struct
from pathlib import Path

SEC = 512
SPC = 1
RESV = 32
NFAT = 2
FATSZ = 8                     # секторов FAT -> 1024 записи
DATACLUS = 1000
TOTSEC = RESV + NFAT * FATSZ + DATACLUS
DATA0 = RESV + NFAT * FATSZ   # первый сектор данных (кластер 2)
EOC = 0x0FFFFFFF

img = bytearray(TOTSEC * SEC)

# --- BPB ---
img[0:3] = b"\xEB\xFE\x90"
img[3:11] = b"MSDOS5.0"
struct.pack_into("<H", img, 11, SEC)
img[13] = SPC
struct.pack_into("<H", img, 14, RESV)
img[16] = NFAT
struct.pack_into("<I", img, 32, TOTSEC)
struct.pack_into("<I", img, 36, FATSZ)
struct.pack_into("<I", img, 44, 2)        # кластер корня
struct.pack_into("<H", img, 48, 1)        # FSInfo
img[82:90] = b"FAT32   "
img[510] = 0x55
img[511] = 0xAA


def fat_set(c, v):
    for f in range(NFAT):
        struct.pack_into("<I", img, (RESV + f * FATSZ) * SEC + c * 4, v)


def clus_off(c):
    return (DATA0 + (c - 2) * SPC) * SEC


fat_set(0, 0x0FFFFFF8)
fat_set(1, 0x0FFFFFFF)

_next = [2]
def alloc(n):
    cl = list(range(_next[0], _next[0] + n))
    _next[0] += n
    for i, c in enumerate(cl):
        fat_set(c, cl[i + 1] if i + 1 < len(cl) else EOC)
    return cl


def dirent(off, name, attr, clus, size):
    img[off:off + 11] = name.ljust(11)[:11].encode("ascii")
    img[off + 11] = attr
    struct.pack_into("<H", img, off + 20, (clus >> 16) & 0xFFFF)
    struct.pack_into("<H", img, off + 26, clus & 0xFFFF)
    struct.pack_into("<I", img, off + 28, size)


root = alloc(1)                          # кластер 2
roff = clus_off(root[0])
# файл: HELLO.TXT (1100 байт -> 3 кластера)
f1 = alloc(3)
for c in f1:
    img[clus_off(c):clus_off(c) + 512] = b"H" * 512
dirent(roff + 0, "HELLO   TXT", 0x20, f1[0], 1100)
# подкаталог: SUBDIR (1 кластер)
sd = alloc(1)
dirent(roff + 32, "SUBDIR     ", 0x10, sd[0], 0)
soff = clus_off(sd[0])
dirent(soff + 0, ".          ", 0x10, sd[0], 0)
dirent(soff + 32, "..         ", 0x10, 0, 0)
# файл в подкаталоге: A.BIN (2000 байт -> 4 кластера)
fa = alloc(4)
for c in fa:
    img[clus_off(c):clus_off(c) + 512] = b"\xAA" * 512
dirent(soff + 64, "A       BIN", 0x20, fa[0], 2000)
# лишние пустые подкаталоги в корне (чтобы провоцировать переполнение BFS-очереди); env EXTRA_DIRS
import os
extra = int(os.environ.get("EXTRA_DIRS", "0"))
for i in range(extra):
    d = alloc(1)
    dirent(roff + 64 + i * 32, f"DIR{i+2}".ljust(8)[:8] + "   ", 0x10, d[0], 0)
    so = clus_off(d[0])
    dirent(so + 0, ".          ", 0x10, d[0], 0)
    dirent(so + 32, "..         ", 0x10, 0, 0)

# LOST-цепочка: 5 кластеров заняты, но ни на что не ссылаются
lost = alloc(5)

Path(__file__).resolve().parent.joinpath("test.img").write_bytes(img)

allocated = 1 + 3 + 1 + 4 + 5             # root+f1+sd+fa+lost
free = DATACLUS - allocated
print(f"test.img: {TOTSEC*SEC} bytes, {DATACLUS} clusters")
print(f"  expected: files=2 filebytes={1100+2000} dirs=1 dirbytes={1*512}")
print(f"  free={free} lost={len(lost)} totalClusters={DATACLUS}")
print(f"  geometry: bytes/clus={SPC*512} totalDiskSpace={DATACLUS*SPC*512}")
