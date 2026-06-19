import os
import re
import uuid
import shutil
from datetime import datetime
from pathlib import Path

from flask import (
    Flask, request, render_template, send_file,
    jsonify, abort
)
from PIL import Image
import pytesseract
import fitz   # PyMuPDF

# ──────────────────────────────────────────────
# 配置
# ──────────────────────────────────────────────
BASE_DIR    = Path(__file__).parent
UPLOAD_DIR  = BASE_DIR / "uploads"      # 共享文件根目录
UPLOAD_DIR.mkdir(exist_ok=True)

ALLOWED_EXT = {'.jpg', '.jpeg', '.png', '.gif', '.bmp',
                '.tiff', '.webp', '.pdf'}
MAX_CONTENT = 50 * 1024 * 1024   # 50 MB

app = Flask(__name__)
app.secret_key = os.urandom(32)
app.config['MAX_CONTENT_LENGTH'] = MAX_CONTENT


# ──────────────────────────────────────────────
# OCR & 命名
# ──────────────────────────────────────────────

def ocr_image(path: Path) -> str:
    try:
        img = Image.open(path)
        return pytesseract.image_to_string(img, lang='chi_sim+eng').strip()
    except Exception as e:
        return f"[OCR 失败: {e}]"


def ocr_pdf(path: Path) -> str:
    try:
        doc = fitz.open(str(path))
        parts = [page.get_text("text").strip() for page in doc]
        full = "\n".join(p for p in parts if p)
        if len(full) > 30:
            return full
        # 原生文字太少 → OCR 第一页
        pix = doc[0].get_pixmap(dpi=200)
        tmp = path.parent / "_tmp_ocr.png"
        pix.save(str(tmp))
        result = ocr_image(tmp)
        tmp.unlink(missing_ok=True)
        return result
    except Exception as e:
        return f"[PDF 解析失败: {e}]"


def sanitize(name: str) -> str:
    name = re.sub(r'[\\/*?:"<>|]', '_', name)
    name = re.sub(r'\s+', '_', name.strip())
    return name[:50] or "unnamed"


def extract_info(text: str, orig_name: str) -> tuple[str, str]:
    """
    返回 (folder_name, file_name_no_ext)
    """
    lines = [l.strip() for l in text.split('\n') if l.strip()]

    # ── 公司/供应商名 ──
    company_re = re.compile(
        r'([\u4e00-\u9fa5]{2,10}'
        r'(?:公司|集团|企业|工厂|厂|科技|实业|商贸|包装|弹簧|模具|电子|机械|制造|有限|股份|责任|合伙企业)'
        r'[\u4e00-\u9fa5]*)'
    )
    companies = []
    for line in lines[:20]:
        companies.extend(company_re.findall(line))

    # ── 物料编码 ──
    codes = []
    for line in lines:
        codes.extend(re.findall(r'\b([A-Za-z]{1,5}[\-_]?[0-9]{3,15})\b', line))

    # ── 日期 ──
    dates = []
    for line in lines:
        for m in re.finditer(r'(\d{4})[年\-/.](\d{1,2})[月\-/.](\d{1,2})', line):
            dates.append(f"{m.group(1)}{m.group(2).zfill(2)}{m.group(3).zfill(2)}")

    # ── 标题行 ──
    title = ""
    for line in lines[:5]:
        if 4 <= len(line) <= 40:
            title = line
            break

    company = sanitize(companies[0]) if companies else ""
    code    = sanitize(codes[0])    if codes    else ""
    date    = dates[0]              if dates     else datetime.now().strftime("%Y%m%d")

    folder = company or sanitize(title) or sanitize(orig_name.rsplit('.', 1)[0])
    fname  = (f"{code}-{date}" if code
               else f"{sanitize(title)}-{date}" if title
               else f"{sanitize(orig_name.rsplit('.', 1)[0])}-{date}")
    return folder, fname


# ──────────────────────────────────────────────
# 文件列表
# ──────────────────────────────────────────────

def _human_size(n: int) -> str:
    for u in ('B', 'KB', 'MB', 'GB'):
        if n < 1024:
            return f"{n:.1f} {u}"
        n /= 1024
    return f"{n:.1f} TB"


def list_files() -> list[dict]:
    result = []
    for folder in sorted(UPLOAD_DIR.iterdir()):
        if not folder.is_dir():
            continue
        for f in sorted(folder.iterdir()):
            if not f.is_file():
                continue
            st = f.stat()
            result.append({
                "folder":   folder.name,
                "filename": f.name,
                "size":     _human_size(st.st_size),
                "mtime":    datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M"),
                "rel":      f"{folder.name}/{f.name}",
            })
    return result


# ──────────────────────────────────────────────
# 路由
# ──────────────────────────────────────────────

@app.route('/')
def index():
    return render_template('index.html', files=list_files())


@app.route('/upload', methods=['POST'])
def upload():
    files = request.files.getlist('files')
    results = []

    for f in files:
        if not f or not f.filename:
            continue
        orig = f.filename
        ext  = Path(orig).suffix.lower()
        if ext not in ALLOWED_EXT:
            results.append({"orig": orig, "status": "error", "msg": "不支持的格式"})
            continue

        # 临时存放
        tmp = UPLOAD_DIR / f"_tmp_{uuid.uuid4().hex}{ext}"
        f.save(str(tmp))

        # OCR
        text = ocr_pdf(tmp) if ext == '.pdf' else ocr_image(tmp)

        # 推导命名
        folder_name, file_name = extract_info(text, orig)
        new_name = f"{file_name}{ext}"

        target_dir = UPLOAD_DIR / folder_name
        target_dir.mkdir(exist_ok=True)
        target = target_dir / new_name

        # 重名处理
        counter = 1
        while target.exists():
            target = target_dir / f"{file_name}_{counter}{ext}"
            counter += 1

        shutil.move(str(tmp), str(target))

        results.append({
            "orig":        orig,
            "status":      "ok",
            "folder":      folder_name,
            "newname":     target.name,
            "ocr_preview": text[:150].replace('\n', ' '),
        })

    return jsonify(results)


@app.route('/download/<path:rel>')
def download(rel: str):
    target = (UPLOAD_DIR / rel).resolve()
    try:
        target.relative_to(UPLOAD_DIR.resolve())
    except ValueError:
        abort(403)
    if not target.is_file():
        abort(404)
    return send_file(str(target), as_attachment=True, download_name=target.name)


@app.route('/delete_file', methods=['POST'])
def delete_file():
    rel = request.json.get('rel', '')
    target = (UPLOAD_DIR / rel).resolve()
    try:
        target.relative_to(UPLOAD_DIR.resolve())
    except ValueError:
        return jsonify(ok=False, msg="非法路径"), 400
    if target.is_file():
        target.unlink()
        if not any(target.parent.iterdir()):
            target.parent.rmdir()
        return jsonify(ok=True)
    return jsonify(ok=False, msg="文件不存在"), 404


@app.route('/delete_folder', methods=['POST'])
def delete_folder():
    name = request.json.get('folder', '')
    target = (UPLOAD_DIR / name).resolve()
    try:
        target.relative_to(UPLOAD_DIR.resolve())
    except ValueError:
        return jsonify(ok=False, msg="非法路径"), 400
    if target.is_dir():
        shutil.rmtree(str(target))
        return jsonify(ok=True)
    return jsonify(ok=False, msg="文件夹不存在"), 404


@app.route('/clear_all', methods=['POST'])
def clear_all():
    for item in UPLOAD_DIR.iterdir():
        if item.is_dir():
            shutil.rmtree(str(item))
        else:
            item.unlink()
    return jsonify(ok=True)


@app.route('/files_json')
def files_json():
    return jsonify(list_files())


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
