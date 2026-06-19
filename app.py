import os
import re
import uuid
import shutil
import requests
from datetime import datetime
from pathlib import Path
from base64 import b64encode
from urllib.parse import quote

from flask import (
    Flask, request, render_template, send_file,
    jsonify, abort
)

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
# 百度 OCR 配置（从这里获取：https://cloud.baidu.com/product/ocr/general）
# 环境变量：BAIDU_OCR_API_KEY  /  BAIDU_OCR_SECRET_KEY
# ──────────────────────────────────────────────
BAIDU_API_KEY    = os.getenv("BAIDU_OCR_API_KEY", "")
BAIDU_SECRET_KEY = os.getenv("BAIDU_OCR_SECRET_KEY", "")


def get_baidu_token() -> str:
    """获取百度 OCR 的 access_token"""
    if not BAIDU_API_KEY or not BAIDU_SECRET_KEY:
        return ""
    url = "https://aip.baidubce.com/oauth/2.0/token"
    try:
        r = requests.get(url, params={
            "grant_type":    "client_credentials",
            "client_id":     BAIDU_API_KEY,
            "client_secret": BAIDU_SECRET_KEY,
        }, timeout=10)
        r.raise_for_status()
        return r.json().get("access_token", "")
    except Exception as e:
        print(f"[百度OCR] 获取token失败: {e}")
        return ""


def ocr_with_baidu(image_path: Path) -> str:
    """调用百度通用文字识别 API（高精度版）"""
    if not BAIDU_API_KEY or not BAIDU_SECRET_KEY:
        return "[百度OCR未配置，请在环境变量中设置 BAIDU_OCR_API_KEY 和 BAIDU_OCR_SECRET_KEY]"

    token = get_baidu_token()
    if not token:
        return "[百度OCR获取token失败]"

    with open(image_path, "rb") as f:
        img_b64 = b64encode(f.read()).decode()

    url = f"https://aip.baidubce.com/rest/2.0/ocr/v1/accurate_basic?access_token={token}"
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    data = f"image={quote(img_b64)}"

    try:
        r = requests.post(url, headers=headers, data=data, timeout=30)
        r.raise_for_status()
        result = r.json()
        if "words_result" not in result:
            return f"[百度OCR错误: {result.get('error_msg', '未知错误')}]"
        text = "\n".join(item["words"] for item in result["words_result"])
        return text.strip()
    except Exception as e:
        return f"[百度OCR调用失败: {e}]"


def ocr_image(path: Path) -> str:
    return ocr_with_baidu(path)


def ocr_pdf(path: Path) -> str:
    """PDF 转图片后 OCR（使用百度 OCR API）"""
    try:
        import fitz
        doc = fitz.open(str(path))
        texts = []
        for page in doc:
            pix = page.get_pixmap(dpi=150)
            tmp_img = path.parent / f"_tmp_ocr_{uuid.uuid4().hex}.png"
            pix.save(str(tmp_img))
            text = ocr_with_baidu(tmp_img)
            tmp_img.unlink(missing_ok=True)
            if text and not text.startswith("["):
                texts.append(text)
        return "\n".join(texts).strip() if texts else "[PDF无文字内容]"
    except ImportError:
        return "[PDF处理需要安装 PyMuPDF：pip install pymupdf]"
    except Exception as e:
        return f"[PDF解析失败: {e}]"


def sanitize(name: str) -> str:
    name = re.sub(r'[\\/*?:"<>|]', '_', name)
    name = re.sub(r'\s+', '_', name.strip())
    return name[:50] or "unnamed"


def extract_info(text: str, orig_name: str) -> tuple[str, str]:
    """
    返回 (folder_name, file_name_no_ext)
    命名规则：物料编码-日期（优先物料编码，其次供应商名称）
    """
    lines = [l.strip() for l in text.split('\n') if l.strip()]

    # ── 物料编码（优先） ──
    codes = []
    for line in lines:
        # 匹配物料编码：字母+数字、纯数字、带连字符等
        codes.extend(re.findall(r'\b([A-Za-z]{1,5}[\-_]?[0-9]{3,15})\b', line))
        codes.extend(re.findall(r'\b([0-9]{6,20})\b', line))  # 纯数字编码（6-20位）

    # ── 公司/供应商名（次优先，用于文件夹分类） ──
    company_re = re.compile(
        r'([\u4e00-\u9fa5]{2,10}'
        r'(?:公司|集团|企业|工厂|厂|科技|实业|商贸|包装|弹簧|模具|电子|机械|制造|有限|股份|责任|合伙企业)'
        r'[\u4e00-\u9fa5]*)'
    )
    companies = []
    for line in lines[:20]:
        companies.extend(company_re.findall(line))

    # ── 日期 ──
    dates = []
    for line in lines:
        for m in re.finditer(r'(\d{4})[年\-/.](\d{1,2})[月\-/.](\d{1,2})', line):
            dates.append(f"{m.group(1)}{m.group(2).zfill(2)}{m.group(3).zfill(2)}")

    # ── 标题行（备用） ──
    title = ""
    for line in lines[:5]:
        if 4 <= len(line) <= 40:
            title = line
            break

    code     = sanitize(codes[0])     if codes    else ""
    company  = sanitize(companies[0]) if companies else ""
    date     = dates[0]                if dates     else datetime.now().strftime("%Y%m%d")

    # 文件名：优先物料编码，其次供应商+日期，最后原标题
    if code:
        fname = f"{code}-{date}"
    elif company:
        fname = f"{company}-{date}"
    elif title:
        fname = f"{sanitize(title)}-{date}"
    else:
        fname = f"{sanitize(orig_name.rsplit('.', 1)[0])}-{date}"

    # 文件夹名：优先供应商名称（便于分类），其次用物料编码，最后用标题
    folder = company or code or sanitize(title) or sanitize(orig_name.rsplit('.', 1)[0])

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
    app.run(host='0.0.0.0', port=int(os.getenv("PORT", 5000)), debug=False)
