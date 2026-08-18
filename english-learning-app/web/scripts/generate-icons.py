#!/usr/bin/env python3
"""PWA アイコンを生成する。

外部の画像ライブラリを入れずに済むよう、PNG を直接書き出す。アイコンは
コミット対象だが、色や字形を変えたくなったときに手作業へ戻らないよう、
生成手順をこのスクリプトに残しておく。

使い方:
    python3 scripts/generate-icons.py
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

OUT_DIR = Path(__file__).resolve().parent.parent / "public" / "icons"

# アプリのアクセス色（globals.css の --color-accent 系と揃える）。
TOP = (59, 130, 246)
BOTTOM = (29, 78, 216)
GLYPH = (255, 255, 255)


def _blend(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))  # type: ignore[return-value]


def _rounded_alpha(x: float, y: float, size: int, radius: float) -> float:
    """角丸の内外判定。境界 1px 分を線形に落としてジャギーを抑える。"""
    if radius <= 0:
        return 1.0
    cx = min(max(x, radius), size - radius)
    cy = min(max(y, radius), size - radius)
    dx, dy = x - cx, y - cy
    distance = (dx * dx + dy * dy) ** 0.5
    if distance <= radius - 1:
        return 1.0
    if distance >= radius:
        return 0.0
    return radius - distance


def _in_glyph(x: float, y: float, size: int) -> bool:
    """大文字 E を矩形の組み合わせで描く。"""
    x0, x1 = size * 0.30, size * 0.71
    y0, y1 = size * 0.27, size * 0.73
    thickness = size * 0.082
    if not (x0 <= x <= x1 and y0 <= y <= y1):
        return False
    if x <= x0 + thickness:  # 縦棒
        return True
    if y <= y0 + thickness:  # 上の横棒
        return True
    if y >= y1 - thickness:  # 下の横棒
        return True
    middle = (y0 + y1) / 2
    if abs(y - middle) <= thickness / 2 and x <= x0 + (x1 - x0) * 0.76:  # 中央の横棒
        return True
    return False


def render(size: int, *, rounded: bool) -> bytes:
    radius = size * 0.22 if rounded else 0.0
    rows = bytearray()
    for py in range(size):
        rows.append(0)  # filter type 0 (None)
        y = py + 0.5
        base = _blend(TOP, BOTTOM, y / size)
        for px in range(size):
            x = px + 0.5
            alpha = _rounded_alpha(x, y, size, radius)
            colour = GLYPH if _in_glyph(x, y, size) else base
            rows.extend((colour[0], colour[1], colour[2], round(alpha * 255)))
    return bytes(rows)


def _chunk(tag: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + tag
        + payload
        + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
    )


def write_png(path: Path, size: int, *, rounded: bool) -> None:
    header = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)  # RGBA, 8bit
    data = zlib.compress(render(size, rounded=rounded), 9)
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", header) + _chunk(b"IDAT", data) + _chunk(b"IEND", b"")
    )
    print(f"{path.name}: {size}x{size} ({path.stat().st_size} bytes)")


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    write_png(OUT_DIR / "icon-192.png", 192, rounded=True)
    write_png(OUT_DIR / "icon-512.png", 512, rounded=True)
    # maskable はセーフゾーンを OS 側が切るため、角丸を付けず全面塗りにする。
    write_png(OUT_DIR / "icon-maskable-512.png", 512, rounded=False)
    # iOS は独自にマスクをかけるので、こちらも全面塗りで渡す。
    write_png(OUT_DIR / "apple-touch-icon.png", 180, rounded=False)


if __name__ == "__main__":
    main()
