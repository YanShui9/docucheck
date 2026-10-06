"""Build the two one-page, fully synthetic PDF fixtures."""

from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "examples" / "dev-pack-001"
FONT_CANDIDATES = (
    Path("C:/Windows/Fonts/simhei.ttf"),
    Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
    Path("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc"),
)

DOCUMENTS = {
    "requirements.pdf": (
        "模拟办公笔记本采购需求",
        [
            "采购任务：开发样例 001",
            "已装内存至少 16 GB",
            "标称存储至少 512 GB",
            "屏幕尺寸至少 14 英寸",
            "主机重量不超过 1.6 kg",
            "质保期限至少 24 个月",
            "采购数量 10 台",
            "含税单价不超过 6000 元/台",
            "订单确认后 14 个自然日内交付",
        ],
    ),
    "vendor-a-spec.pdf": (
        "模拟供应商 A 产品规格书",
        [
            "供应商：模拟供应商 A",
            "产品型号 DC-14-A",
            "已装内存 32 GB",
            "标称 SSD 1 TB",
            "屏幕尺寸 14 英寸",
            "主机重量 1.8 kg",
        ],
    ),
}


def register_chinese_font() -> str:
    for candidate in FONT_CANDIDATES:
        if candidate.is_file():
            pdfmetrics.registerFont(TTFont("DocuCheckCJK", str(candidate)))
            return "DocuCheckCJK"
    raise RuntimeError("Chinese font unavailable; set up SimHei or Noto Sans CJK")


def render_document(path: Path, title: str, lines: list[str], font: str) -> None:
    page = canvas.Canvas(str(path), pagesize=A4, pageCompression=0)
    page.setTitle(title)
    page.setAuthor("DocuCheck synthetic development fixture")
    width, height = A4
    page.setFont(font, 17)
    page.drawString(56, height - 70, title)
    page.setFont(font, 11)
    page.drawString(56, height - 100, "完全模拟资料 · 仅供开发与测试")
    top = height - 150
    for index, line in enumerate(lines):
        page.drawString(56, top - index * 30, line)
    page.setFont(font, 9)
    page.drawRightString(width - 56, 42, "第 1 页 / 共 1 页")
    page.showPage()
    page.save()


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    font = register_chinese_font()
    for filename, (title, lines) in DOCUMENTS.items():
        render_document(OUTPUT / filename, title, lines, font)


if __name__ == "__main__":
    main()
