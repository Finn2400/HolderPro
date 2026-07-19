#!/usr/bin/env python3
"""Render the provisional SVG into native packaging icon formats."""

from __future__ import annotations

import argparse
import os
import platform
import subprocess
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image  # noqa: E402
from PySide6.QtCore import QByteArray, QRectF, Qt  # noqa: E402
from PySide6.QtGui import QImage, QPainter  # noqa: E402
from PySide6.QtSvg import QSvgRenderer  # noqa: E402


SIZES = (16, 32, 64, 128, 256, 512, 1024)


def render(svg: bytes, destination: Path, size: int) -> None:
    image = QImage(size, size, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    renderer = QSvgRenderer(QByteArray(svg))
    if not renderer.isValid():
        raise RuntimeError("invalid HolderPro SVG icon")
    renderer.render(painter, QRectF(0, 0, size, size))
    painter.end()
    if not image.save(str(destination), "PNG"):
        raise RuntimeError(f"could not write {destination}")


def main() -> int:
    parser = argparse.ArgumentParser()
    root = Path(__file__).resolve().parents[2]
    parser.add_argument(
        "--source",
        type=Path,
        default=root / "src/holderpro/assets/holderpro.svg",
        help="single source-of-truth SVG (provisional until trademark review)",
    )
    parser.add_argument("--assets", type=Path, default=root / "packaging/assets")
    args = parser.parse_args()
    assets = args.assets.resolve()
    assets.mkdir(parents=True, exist_ok=True)
    svg = args.source.resolve().read_bytes()
    pngs: dict[int, Path] = {}
    for size in SIZES:
        path = assets / f"holderpro-{size}.png"
        render(svg, path, size)
        pngs[size] = path
    images = [Image.open(pngs[size]).convert("RGBA") for size in (16, 32, 64, 128, 256)]
    images[-1].save(assets / "HolderPro.ico", format="ICO", append_images=images[:-1])

    if platform.system() == "Darwin":
        iconset = assets / "HolderPro.iconset"
        iconset.mkdir(exist_ok=True)
        mapping = {
            "icon_16x16.png": 16,
            "icon_16x16@2x.png": 32,
            "icon_32x32.png": 32,
            "icon_32x32@2x.png": 64,
            "icon_128x128.png": 128,
            "icon_128x128@2x.png": 256,
            "icon_256x256.png": 256,
            "icon_256x256@2x.png": 512,
            "icon_512x512.png": 512,
            "icon_512x512@2x.png": 1024,
        }
        for name, size in mapping.items():
            Image.open(pngs[size]).save(iconset / name)
        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(assets / "HolderPro.icns")], check=True)
    print(assets)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
