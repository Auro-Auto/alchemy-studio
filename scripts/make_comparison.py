"""Lay out unaltered experiment images into comparison figures (requires Pillow)."""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageOps

ROOT = Path(__file__).resolve().parents[1]


def font(size):
    candidates = [Path('C:/Windows/Fonts/msyh.ttc'),
                  Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')]
    return ImageFont.truetype(str(next(p for p in candidates if p.exists())), size)


def result(case):
    files = sorted((ROOT / 'output' / case).rglob('node_94_*.png'))
    if len(files) != 1:
        raise ValueError(f'Expected one saved output for {case}; got {len(files)}')
    return files[0]


def panel(canvas, path, x, y, width, height, label):
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle((x, y, x + width, y + height), 18, fill='white')
    with Image.open(path) as source:
        im = ImageOps.contain(source.convert('RGB'), (width - 24, height - 64))
    canvas.paste(im, (x + (width - im.width) // 2, y + 12 + (height - 64 - im.height) // 2))
    draw.text((x + 16, y + height - 42), label, fill='#263349', font=font(21))


def main():
    out = ROOT / 'output' / 'previews'
    out.mkdir(exist_ok=True)
    image = Image.new('RGB', (1500, 620), '#edf0f4')
    draw = ImageDraw.Draw(image)
    draw.text((28, 18), '万物炼金台 · 同输入、同种子、同分辨率的提示词对照', fill='#14243b', font=font(30))
    panel(image, ROOT/'input/comfy_cat.png', 24, 78, 300, 245, '输入：猫角色')
    panel(image, ROOT/'input/iced_drink_cup.png', 24, 337, 300, 245, '输入：饮料杯')
    panel(image, result('fusion_smoke/20261108T044556Z_b9703c3c/001_cat_tumbler_generic'), 350, 78, 550, 504, '普通融合指令：贴图倾向')
    panel(image, result('cat_tumbler'), 926, 78, 550, 504, '指定结构与材质：立体猫杯')
    image.save(out/'prompt_comparison.jpg', quality=94)
    image = Image.new('RGB', (1500, 690), '#edf0f4')
    draw = ImageDraw.Draw(image)
    draw.text((28, 18), '万物炼金台 · 远程 GPU 实际生成样例', fill='#14243b', font=font(30))
    panel(image, result('cat_tumbler'), 24, 78, 452, 570, '角色 × 饮料杯：猫耳陶瓷杯')
    panel(image, result('cat_sneaker'), 500, 78, 476, 277, '角色 × 运动鞋：猫耳运动鞋')
    panel(image, result('real_cat_mug_refined'), 500, 371, 476, 277, '实拍猫 × 白瓷杯：单猫脸浮雕')
    panel(image, result('real_cat_mug'), 1000, 78, 476, 570, '保留的失败样例：重复猫脸')
    image.save(out/'gallery.jpg', quality=94)
    print('Saved comparison figures to output/previews')


if __name__ == '__main__':
    main()
