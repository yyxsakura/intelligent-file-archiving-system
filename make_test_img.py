from PIL import Image, ImageDraw, ImageFont

def make_img(path, lines):
    img = Image.new('RGB', (600, 400), color='white')
    draw = ImageDraw.Draw(img)
    font = ImageFont.truetype("/usr/share/fonts/opentype/noto/NotoSerifCJK-Bold.ttc", 22)
    y = 40
    for line in lines:
        draw.text((50, y), line, fill='black', font=font)
        y += 50
    img.save(path)
    print(f"已创建: {path}")

# 图片1：供应商A
make_img('/workspace/filesorter/uploads/_test1.png', [
    "东莞普印包装有限公司",
    "发货单",
    "物料编码: PY-8899001",
    "日期: 2025年06月01日",
    "规格: A4 500张/包",
])

# 图片2：供应商B
make_img('/workspace/filesorter/uploads/_test2.png', [
    "深圳市华强电子制造有限公司",
    "采购订单",
    "订单号: HC-20250618-003",
    "日期: 2025年06月18日",
    "金额: ￥128,000.00",
])
