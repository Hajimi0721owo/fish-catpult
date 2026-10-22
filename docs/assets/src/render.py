"""把 README 用到的像素图渲染成 PNG：横幅（banner.html 截图）和两位角色的放大立绘。

用法：python3 docs/assets/src/render.py  （需要 playwright 和 Pillow，只读本地文件，不联网）
"""
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
OUT = HERE.parent
ART = HERE.parents[2] / "src/bili_fav_relay/setup/ui/art"


# GitHub 仓库 Settings → Social preview 要 1280×640，同一个房间加高、标题卡下移即可。
SOCIAL = ("html, body { height: 640px !important; } .title { top: 196px !important; left: 48px !important; }"
          " .cast { --px: 3 !important; left: 700px !important; } .b1 { left: 740px !important; top: 150px !important; }"
          " .b2 { left: 1040px !important; top: 128px !important; }")


def banner():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for name, height, scale, extra in (("banner.png", 420, 2, ""), ("social-preview.png", 640, 1, SOCIAL)):
            page = browser.new_page(viewport={"width": 1280, "height": height}, device_scale_factor=scale)
            page.goto((HERE / "banner.html").as_uri())
            if extra:
                page.add_style_tag(content=extra)
            page.wait_for_load_state("networkidle")
            page.screenshot(path=str(OUT / name))
        browser.close()


def sprites(scale=2):
    """整数倍最近邻放大；README 里按原尺寸显示，GitHub 就不会再插值糊掉。"""
    for name in ("hachi-happy", "dsf-happy"):
        image = Image.open(ART / f"{name}.png")
        image.resize((image.width * scale, image.height * scale), Image.NEAREST).save(OUT / f"{name.split('-')[0]}.png")


if __name__ == "__main__":
    banner()
    sprites()
    print("ok:", ", ".join(sorted(p.name for p in OUT.glob("*.png"))))
