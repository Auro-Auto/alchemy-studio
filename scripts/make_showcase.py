"""Lay out saved images for the README. Does not run inference or download files."""
from pathlib import Path
import json

from PIL import Image, ImageDraw, ImageFont, ImageOps


ROOT = Path(__file__).resolve().parents[1]
PAPER = "#f7f4ec"
INK = "#263d32"
FONT_PATHS = (
    Path("C:/Windows/Fonts/msyh.ttc"),
    Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
    Path("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc"),
)


def font(size):
    path = next((path for path in FONT_PATHS if path.is_file()), None)
    if path is None:
        raise FileNotFoundError("Install Microsoft YaHei or Noto Sans CJK to render Chinese labels.")
    return ImageFont.truetype(str(path), size)


def board(destination, title, cards, *, columns, box, footer=None):
    """Recompose complete saved images with one label per panel."""
    margin, gap, top, caption = 32, 18, 90, 48
    width, height = box
    rows = (len(cards) + columns - 1) // columns
    footer_height = 38 if footer else 0
    canvas = Image.new("RGB", (
        margin * 2 + columns * width + (columns - 1) * gap,
        top + rows * (height + caption + gap) + 4 + footer_height,
    ), PAPER)
    draw = ImageDraw.Draw(canvas)
    draw.text((margin, 20), title, fill=INK, font=font(35))
    for index, (label, relative) in enumerate(cards):
        path = ROOT / relative
        if not path.is_file():
            raise FileNotFoundError(path)
        x = margin + (index % columns) * (width + gap)
        y = top + (index // columns) * (height + caption + gap)
        with Image.open(path) as source:
            tile = ImageOps.pad(
                source.convert("RGB"), box,
                method=Image.Resampling.LANCZOS, color="#ebe7dc",
            )
        canvas.paste(tile, (x, y))
        draw.text((x, y + height + 8), label, fill=INK, font=font(22))
    if footer:
        draw.text((margin, canvas.height - 31), footer, fill="#59635a", font=font(16))
    path = ROOT / destination
    path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(path, quality=93, optimize=True)
    return destination


def main():
    # Import labels from the same catalog used by the interface.
    import sys
    sys.path.insert(0, str(ROOT))
    from studio.creative import STYLES

    rendered = []
    rendered.append(board(
        "output/creative_styles/style-atlas.jpg", "风格对比",
        [(style.name, f"output/style_previews/{identifier}.jpg")
         for identifier, style in STYLES.items()],
        columns=4, box=(240, 180),
    ))
    rendered.append(board(
        "output/styles_memes/style-comparison.jpg", "风格对比",
        [(label, f"output/styles_memes/{name}.png") for label, name in (
            ("原样", "owl_original"), ("漫画", "meme_early_class"),
            ("黏土", "owl_clay"), ("水墨", "owl_ink"),
            ("像素", "owl_pixel"), ("复古海报", "owl_poster"),
        )], columns=3, box=(322, 242),
    ))
    presets = json.loads((ROOT / "input/meme_presets.json").read_text(encoding="utf-8"))
    rendered.append(board(
        "output/styles_memes/meme-gallery.jpg", "热梗创作",
        [(item["title"], f"output/styles_memes/{item['id']}.png") for item in presets],
        columns=2, box=(454, 292),
    ))
    rendered.append(board(
        "output/scu_detail/architecture/wangjiang-design-board.jpg", "望江北门",
        [
            ("望江北门", "input/scu_wangjiang.png"),
            ("马克杯", "output/scu_detail/architecture/previews/scu_gate_mug_123_detail.jpg"),
            ("随行杯", "output/scu_detail/architecture/previews/scu_gate_tumbler_123_region.jpg"),
        ], columns=3, box=(430, 440),
        footer="望江北门摄影：Kcx36 · CC BY-SA 4.0",
    ))
    rendered.append(board(
        "output/previews/studio-method-comparison.jpg", "生成方式对比",
        [(label, f"output/studio_eval/20261108T051221Z/{name}.png") for label, name in (
            ("基础融合", "tumbler_baseline_1"),
            ("结构化生成", "tumbler_roles_1"),
            ("主体分离", "tumbler_foreground_1"),
            ("背景保留", "realcat_background_lock_1"),
        )], columns=4, box=(360, 490),
    ))
    print("Rendered " + str(len(rendered)) + " showcase boards from saved images.")


if __name__ == "__main__":
    main()
