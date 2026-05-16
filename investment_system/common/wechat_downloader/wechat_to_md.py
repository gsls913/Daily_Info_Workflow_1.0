"""
微信文章下载转Markdown工具
==========================
功能：将微信公众号文章下载并转换为Markdown格式
- 下载文章内容
- 转换为Markdown
- 下载图片到本地（过滤小图标、压缩大图）
- 更新图片引用
- 保留文字颜色、加粗、下划线、背景色
- 处理段落间距
- 支持表格
- 支持Obsidian模式
- 图片宽度自动调整
- 日志系统
- 重试机制
- Windows通知

依赖:
  pip install requests beautifulsoup4 Pillow
"""

import os
import re
import sys
import time
import uuid
import logging
from datetime import datetime
from pathlib import Path
from io import BytesIO

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

import requests
from bs4 import BeautifulSoup, NavigableString

try:
    from PIL import Image
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(SCRIPT_DIR)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from investment_system.common.markdown_utils import normalize_markdown_output

OUTPUT_DIR = os.path.join(SCRIPT_DIR, "articles")

LOG_DIR = os.path.join(SCRIPT_DIR, "logs")
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, f"download_{datetime.now().strftime('%Y%m%d')}.log")

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE, encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


def log_info(msg):
    logger.info(msg)


def log_warn(msg):
    logger.warning(msg)


def log_error(msg):
    logger.error(msg)


HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

LANGUAGE_MAP = {
    "python": "python",
    "javascript": "javascript",
    "js": "javascript",
    "typescript": "typescript",
    "ts": "typescript",
    "java": "java",
    "c": "c",
    "cpp": "cpp",
    "c++": "cpp",
    "csharp": "csharp",
    "c#": "csharp",
    "go": "go",
    "golang": "go",
    "rust": "rust",
    "ruby": "ruby",
    "php": "php",
    "swift": "swift",
    "kotlin": "kotlin",
    "scala": "scala",
    "r": "r",
    "sql": "sql",
    "html": "html",
    "css": "css",
    "shell": "shell",
    "bash": "bash",
    "sh": "bash",
    "powershell": "powershell",
    "json": "json",
    "xml": "xml",
    "yaml": "yaml",
    "yml": "yaml",
    "markdown": "markdown",
    "md": "markdown",
}

MIN_IMAGE_WIDTH = 100
MIN_IMAGE_HEIGHT = 100
MIN_IMAGE_FILE_SIZE = 5000
MAX_IMAGE_FILE_SIZE = 500 * 1024
MAX_IMAGE_DIMENSION = 1920

MAX_RETRY_ATTEMPTS = 3
RETRY_DELAY = 2

OBSIDIAN_FULL_WIDTH = 697
WECHAT_CONTENT_WIDTH = 677


def sanitize_filename(name: str) -> str:
    """清理文件名，移除非法字符"""
    invalid_chars = r'[<>:"/\\|?*]'
    name = re.sub(invalid_chars, '_', name)
    name = name.strip()
    while name.endswith('.'):
        name = name[:-1]
    return name[:100] if name else "untitled"


def is_near_black(r: int, g: int, b: int) -> bool:
    """判断颜色是否接近黑色
    
    满足以下任一条件即为近似黑色：
    1. r + g + b < 80
    2. r, g, b 彼此相差不超过20，且均小于140
    3. r, g, b 都小于60
    """
    if r + g + b < 80:
        return True
    
    max_val = max(r, g, b)
    min_val = min(r, g, b)
    if max_val - min_val <= 20 and r < 140 and g < 140 and b < 140:
        return True
    
    if r < 60 and g < 60 and b < 60:
        return True
    
    return False


def is_near_white(r: int, g: int, b: int, alpha: float = 1.0) -> bool:
    """判断颜色是否接近白色
    
    与黑色逻辑对称，使用 (256 - 值) 计算：
    1. (256-r) + (256-g) + (256-b) < 80，即 r + g + b > 688
    2. 三个差值彼此相差不超过20，且三个差值均小于140
    3. 三个差值都小于60，即 r, g, b 都大于196
    """
    if alpha < 0.5:
        return True
    
    if r + g + b > 688:
        return True
    
    dr, dg, db = 256 - r, 256 - g, 256 - b
    max_diff = max(dr, dg, db)
    min_diff = min(dr, dg, db)
    if max_diff - min_diff <= 20 and dr < 140 and dg < 140 and db < 140:
        return True
    
    if dr < 60 and dg < 60 and db < 60:
        return True
    
    return False


def parse_color(style: str) -> str:
    """从style属性中解析颜色，过滤接近黑色的颜色"""
    if not style:
        return ""
    style_lower = style.lower()
    
    color_match = re.search(r'(?<!background-)color\s*:\s*([^;]+)', style_lower)
    if color_match:
        color = color_match.group(1).strip()
        if 'rgba(0, 0, 0, 0)' in color or 'rgba(0,0,0,0)' in color:
            return ""
        if color == 'transparent':
            return ""
        
        rgb = parse_rgb_values(color)
        if rgb and is_near_black(rgb[0], rgb[1], rgb[2]):
            return ""
        
        return color
    return ""


def should_display_color(color: str) -> bool:
    """判断颜色是否需要显示（非黑色/透明）"""
    if not color:
        return False
    
    color_lower = color.lower()
    
    if 'transparent' in color_lower:
        return False
    
    if 'rgba(0, 0, 0, 0)' in color_lower or 'rgba(0,0,0,0)' in color_lower:
        return False
    
    rgb_match = re.search(r'rgb\s*\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)', color_lower)
    if rgb_match:
        r, g, b = int(rgb_match.group(1)), int(rgb_match.group(2)), int(rgb_match.group(3))
        if is_near_black(r, g, b):
            return False
    
    rgba_match = re.search(r'rgba\s*\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*([\d.]+)', color_lower)
    if rgba_match:
        r, g, b = int(rgba_match.group(1)), int(rgba_match.group(2)), int(rgba_match.group(3))
        if is_near_black(r, g, b):
            return False
    
    return True


def parse_rgb_values(color: str) -> tuple:
    """从颜色字符串中解析RGB值"""
    if not color:
        return None
    
    color_lower = color.lower()
    
    rgb_match = re.search(r'rgb\s*\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)', color_lower)
    if rgb_match:
        return (int(rgb_match.group(1)), int(rgb_match.group(2)), int(rgb_match.group(3)))
    
    rgba_match = re.search(r'rgba\s*\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*([\d.]+)', color_lower)
    if rgba_match:
        return (int(rgba_match.group(1)), int(rgba_match.group(2)), int(rgba_match.group(3)))
    
    hex_match = re.match(r'^#([0-9a-f]{6})', color_lower)
    if hex_match:
        return (
            int(hex_match.group(1)[0:2], 16),
            int(hex_match.group(1)[2:4], 16),
            int(hex_match.group(1)[4:6], 16)
        )
    
    return None


def calculate_luminance(r: int, g: int, b: int) -> float:
    """计算相对亮度"""
    def adjust(c):
        c = c / 255.0
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    
    return 0.2126 * adjust(r) + 0.7152 * adjust(g) + 0.072 * adjust(b)


def calculate_contrast(rgb1: tuple, rgb2: tuple) -> float:
    """计算两个颜色之间的对比度"""
    l1 = calculate_luminance(rgb1[0], rgb1[1], rgb1[2])
    l2 = calculate_luminance(rgb2[0], rgb2[1], rgb2[2])
    
    lighter = max(l1, l2)
    darker = min(l1, l2)
    
    return (lighter + 0.05) / (darker + 0.05)


def is_valid_background(color: str) -> bool:
    """判断背景色是否有效（非白色/透明）"""
    if not color:
        return False
    
    color_lower = color.lower()
    
    if 'transparent' in color_lower:
        return False
    
    if 'rgba(0, 0, 0, 0)' in color_lower or 'rgba(0,0,0,0)' in color_lower:
        return False
    
    hex_match = re.match(r'^#([0-9a-f]{6})([0-9a-f]{2})?$', color_lower)
    if hex_match:
        r = int(hex_match.group(1)[0:2], 16)
        g = int(hex_match.group(1)[2:4], 16)
        b = int(hex_match.group(1)[4:6], 16)
        alpha_hex = hex_match.group(2)
        if alpha_hex:
            alpha = int(alpha_hex, 16) / 255.0
            if alpha < 0.5:
                return False
        if is_near_white(r, g, b):
            return False
    
    rgb_match = re.search(r'rgb\s*\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)', color_lower)
    if rgb_match:
        r, g, b = int(rgb_match.group(1)), int(rgb_match.group(2)), int(rgb_match.group(3))
        if is_near_white(r, g, b):
            return False
    
    rgba_match = re.search(r'rgba\s*\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*([\d.]+)', color_lower)
    if rgba_match:
        r, g, b = int(rgba_match.group(1)), int(rgba_match.group(2)), int(rgba_match.group(3))
        a = float(rgba_match.group(4))
        if is_near_white(r, g, b, a):
            return False
    
    return True


def parse_background_color(style: str) -> str:
    """从style属性中解析背景色"""
    if not style:
        return ""
    style_lower = style.lower()
    bg_match = re.search(r'background(?:-color)?\s*:\s*([^;]+)', style_lower)
    if bg_match:
        color = bg_match.group(1).strip()
        if not is_valid_background(color):
            return ""
        return color
    return ""


def rgb_to_hex(color: str) -> str:
    """将RGB颜色转换为十六进制"""
    if not color:
        return ""
    if color.startswith('#'):
        return color
    rgb_match = re.search(r'rgb\s*\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)', color)
    if rgb_match:
        r, g, b = int(rgb_match.group(1)), int(rgb_match.group(2)), int(rgb_match.group(3))
        return f"#{r:02X}{g:02X}{b:02X}"
    rgba_match = re.search(r'rgba\s*\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*([\d.]+)', color)
    if rgba_match:
        r, g, b, a = int(rgba_match.group(1)), int(rgba_match.group(2)), int(rgba_match.group(3)), float(rgba_match.group(4))
        hex_alpha = int(a * 255)
        return f"#{r:02X}{g:02X}{b:02X}{hex_alpha:02X}"
    return color


def parse_spacing(style: str) -> dict:
    """从style属性中解析间距信息"""
    result = {
        "line_height": 1.0,
        "margin_top": 0,
        "margin_bottom": 0,
        "padding_top": 0,
        "padding_bottom": 0
    }
    if not style:
        return result
    
    style = style.lower()
    
    line_height_match = re.search(r'line-height\s*:\s*([\d.]+)(em|px)?', style)
    if line_height_match:
        val = float(line_height_match.group(1))
        unit = line_height_match.group(2)
        if unit == 'px':
            val = val / 16
        result["line_height"] = val
    
    def to_em(val: float, unit: str) -> float:
        if unit == 'px':
            return val / 16
        return val
    
    def parse_value(pattern, key):
        match = re.search(pattern, style)
        if match:
            val = float(match.group(1))
            unit = match.group(2) or 'em'
            result[key] = to_em(val, unit)
    
    parse_value(r'margin-top\s*:\s*([\d.]+)(em|px)?', "margin_top")
    parse_value(r'margin-bottom\s*:\s*([\d.]+)(em|px)?', "margin_bottom")
    parse_value(r'padding-top\s*:\s*([\d.]+)(em|px)?', "padding_top")
    parse_value(r'padding-bottom\s*:\s*([\d.]+)(em|px)?', "padding_bottom")
    
    margin_shorthand = re.search(r'margin\s*:\s*([\d.]+)(em|px)?(?:\s+([\d.]+)(em|px)?)?(?:\s+([\d.]+)(em|px)?)?(?:\s+([\d.]+)(em|px)?)?', style)
    if margin_shorthand and result["margin_top"] == 0 and result["margin_bottom"] == 0:
        vals = []
        for i in range(1, 8, 2):
            if margin_shorthand.group(i):
                v = float(margin_shorthand.group(i))
                u = margin_shorthand.group(i+1) or 'em'
                vals.append(to_em(v, u))
        
        if len(vals) == 1:
            result["margin_top"] = result["margin_bottom"] = vals[0]
        elif len(vals) == 2:
            result["margin_top"] = result["margin_bottom"] = vals[0]
        elif len(vals) >= 3:
            result["margin_top"] = vals[0]
            result["margin_bottom"] = vals[2]
    
    padding_shorthand = re.search(r'padding\s*:\s*([\d.]+)(em|px)?(?:\s+([\d.]+)(em|px)?)?(?:\s+([\d.]+)(em|px)?)?(?:\s+([\d.]+)(em|px)?)?', style)
    if padding_shorthand and result["padding_top"] == 0 and result["padding_bottom"] == 0:
        vals = []
        for i in range(1, 8, 2):
            if padding_shorthand.group(i):
                v = float(padding_shorthand.group(i))
                u = padding_shorthand.group(i+1) or 'em'
                vals.append(to_em(v, u))
        
        if len(vals) == 1:
            result["padding_top"] = result["padding_bottom"] = vals[0]
        elif len(vals) == 2:
            result["padding_top"] = result["padding_bottom"] = vals[0]
        elif len(vals) >= 3:
            result["padding_top"] = vals[0]
            result["padding_bottom"] = vals[2]
    
    return result


def get_image_dimensions(content: bytes) -> tuple:
    """获取图片尺寸"""
    if not PIL_AVAILABLE:
        return (0, 0)
    try:
        img = Image.open(BytesIO(content))
        return img.size
    except Exception:
        return (0, 0)


def compress_image(content: bytes, max_size: int = MAX_IMAGE_DIMENSION, quality: int = 85) -> bytes:
    """
    压缩图片
    
    Args:
        content: 原始图片内容
        max_size: 最大宽高
        quality: JPEG质量
    
    Returns:
        压缩后的图片内容
    """
    if not PIL_AVAILABLE:
        return content
    
    try:
        img = Image.open(BytesIO(content))
        width, height = img.size
        
        if width > max_size or height > max_size:
            if width > height:
                new_width = max_size
                new_height = int(height * max_size / width)
            else:
                new_height = max_size
                new_width = int(width * max_size / height)
            img = img.resize((new_width, new_height), Image.LANCZOS)
        
        if img.mode in ('RGBA', 'P'):
            img = img.convert('RGB')
        
        output = BytesIO()
        img.save(output, format='JPEG', quality=quality, optimize=True)
        return output.getvalue()
    except Exception:
        return content


def is_valid_image(content: bytes, min_width: int = MIN_IMAGE_WIDTH, min_height: int = MIN_IMAGE_HEIGHT) -> bool:
    """检查图片是否有效（非小图标）"""
    if PIL_AVAILABLE:
        width, height = get_image_dimensions(content)
        if width > 0 and height > 0:
            return width >= min_width and height >= min_height
    
    if len(content) < MIN_IMAGE_FILE_SIZE:
        return False
    
    return True


def get_image_display_width(img_element) -> int:
    """从HTML img元素获取显示宽度
    
    微信文章中图片宽度可能通过以下方式指定：
    1. style属性中的width: XXXpx
    2. width属性
    3. data-w或data-width属性
    
    Args:
        img_element: BeautifulSoup的img元素
    
    Returns:
        int: 显示宽度（像素），如果无法获取则返回0
    """
    width = 0
    
    style = img_element.get("style", "")
    if style:
        width_match = re.search(r'width\s*:\s*(\d+(?:\.\d+)?)\s*(px|%)?', style, re.IGNORECASE)
        if width_match:
            w = float(width_match.group(1))
            unit = width_match.group(2)
            if unit == '%':
                pass
            else:
                width = int(w)
    
    if width == 0:
        width_attr = img_element.get("width", "")
        if width_attr:
            try:
                width = int(float(width_attr))
            except (ValueError, TypeError):
                pass
    
    if width == 0:
        data_w = img_element.get("data-w", "")
        if data_w:
            try:
                width = int(float(data_w))
            except (ValueError, TypeError):
                pass
    
    if width == 0:
        data_width = img_element.get("data-width", "")
        if data_width:
            try:
                width = int(float(data_width))
            except (ValueError, TypeError):
                pass
    
    return width


def calculate_obsidian_width(img_original_width: int, display_width: int) -> int:
    """计算Obsidian中的显示宽度
    
    根据图片原始宽度和HTML中指定的显示宽度，计算Obsidian中应该显示的宽度。
    
    微信文章的默认内容区域宽度约为677px（接近Obsidian的满屏宽度697px）。
    
    逻辑：
    1. 如果display_width为0（无法获取），返回0（使用默认大小）
    2. 如果display_width >= img_original_width，说明图片没有缩小，返回0（使用默认大小）
    3. 否则，计算缩放比例，应用到Obsidian满屏宽度上
    
    Args:
        img_original_width: 图片原始宽度（像素）
        display_width: HTML中指定的显示宽度（像素）
    
    Returns:
        int: Obsidian中的显示宽度，0表示使用默认大小
    """
    if display_width == 0:
        return 0
    
    if img_original_width <= 0:
        return 0
    
    if display_width >= img_original_width:
        return 0
    
    scale = display_width / WECHAT_CONTENT_WIDTH
    
    if scale >= 0.95:
        return OBSIDIAN_FULL_WIDTH
    
    obsidian_width = int(OBSIDIAN_FULL_WIDTH * scale)
    
    if obsidian_width < 100:
        return 0
    
    return obsidian_width


def get_unique_md_filename(output_dir: str, base_filename: str) -> str:
    """获取唯一的MD文件名，避免冲突
    
    Args:
        output_dir: 输出目录
        base_filename: 基础文件名
    
    Returns:
        str: 唯一的文件名
    """
    filepath = os.path.join(output_dir, base_filename)
    
    if not os.path.exists(filepath):
        return base_filename
    
    name_without_ext = base_filename[:-3]
    counter = 1
    while True:
        new_filename = f"{name_without_ext}_{counter}.md"
        new_filepath = os.path.join(output_dir, new_filename)
        if not os.path.exists(new_filepath):
            return new_filename
        counter += 1
        if counter > 100:
            return f"{name_without_ext}_{uuid.uuid4().hex[:8]}.md"


def get_unique_image_filename(base_dir: str, short_name: str, index: int, ext: str) -> str:
    """获取唯一的图片文件名，避免冲突
    
    Args:
        base_dir: 附件目录
        short_name: 公众号简称
        index: 图片序号
        ext: 文件扩展名
    
    Returns:
        str: 唯一的文件名
    """
    base_name = f"{short_name}_image_{index:03d}{ext}"
    filepath = os.path.join(base_dir, base_name)
    
    if not os.path.exists(filepath):
        return base_name
    
    counter = 1
    while True:
        new_name = f"{short_name}_image_{index:03d}_{counter}{ext}"
        new_filepath = os.path.join(base_dir, new_name)
        if not os.path.exists(new_filepath):
            return new_name
        counter += 1
        if counter > 1000:
            return f"{short_name}_image_{index:03d}_{uuid.uuid4().hex[:8]}{ext}"


def save_image_unique(save_dir: Path, short_name: str, index: int, ext: str, content: bytes) -> str:
    """排他写入图片，避免并发下载时同名覆盖。"""
    save_dir.mkdir(parents=True, exist_ok=True)
    for _ in range(1100):
        filename = get_unique_image_filename(str(save_dir), short_name, index, ext)
        filepath = save_dir / filename
        try:
            with open(filepath, "xb") as f:
                f.write(content)
            return filename
        except FileExistsError:
            index += 1
            continue
    filename = f"{short_name}_image_{index:03d}_{uuid.uuid4().hex[:12]}{ext}"
    filepath = save_dir / filename
    with open(filepath, "xb") as f:
        f.write(content)
    return filename


def download_image(url: str, save_dir: Path, short_name: str, index: int) -> tuple:
    """下载图片到本地，支持压缩和重试机制
    
    Args:
        url: 图片URL
        save_dir: 保存目录
        short_name: 公众号简称（用于文件名前缀）
        index: 图片序号
    
    Returns:
        tuple: (文件名, 图片原始宽度) 或 ("", 0) 失败时
    """
    url = normalize_image_download_url(url)
    for attempt in range(MAX_RETRY_ATTEMPTS):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=30)
            resp.raise_for_status()
            
            content = resp.content
            original_size = len(content)
            
            if not is_valid_image(content):
                return "", 0
            
            img_width = 0
            if PIL_AVAILABLE:
                try:
                    img = Image.open(BytesIO(content))
                    img_width = img.size[0]
                except:
                    pass
            
            if original_size > MAX_IMAGE_FILE_SIZE:
                content = compress_image(content)
                if PIL_AVAILABLE:
                    try:
                        img = Image.open(BytesIO(content))
                        img_width = img.size[0]
                    except:
                        pass
            
            content_type = resp.headers.get("Content-Type", "")
            if "jpeg" in content_type or "jpg" in content_type:
                ext = ".jpg"
            elif "png" in content_type:
                ext = ".png"
            elif "gif" in content_type:
                ext = ".gif"
            elif "webp" in content_type:
                ext = ".webp"
            else:
                url_lower = url.lower()
                if ".png" in url_lower:
                    ext = ".png"
                elif ".gif" in url_lower:
                    ext = ".gif"
                elif ".webp" in url_lower:
                    ext = ".webp"
                else:
                    ext = ".jpg"
            
            filename = save_image_unique(save_dir, short_name, index, ext, content)
            
            return filename, img_width
            
        except requests.exceptions.Timeout:
            if attempt < MAX_RETRY_ATTEMPTS - 1:
                log_warn(f"图片下载超时，重试 ({attempt + 2}/{MAX_RETRY_ATTEMPTS}): {url[:50]}...")
                time.sleep(RETRY_DELAY)
                continue
            log_warn(f"图片下载超时失败: {url[:50]}...")
            return "", 0
            
        except requests.exceptions.ConnectionError as e:
            if attempt < MAX_RETRY_ATTEMPTS - 1:
                log_warn(f"网络连接错误，重试 ({attempt + 2}/{MAX_RETRY_ATTEMPTS})")
                time.sleep(RETRY_DELAY)
                continue
            log_warn(f"图片下载连接失败: {str(e)[:50]}")
            return "", 0
            
        except Exception as e:
            log_warn(f"下载图片失败: {url[:50]}... - {e}")
            return "", 0
    
    return "", 0


def normalize_image_download_url(url: str) -> str:
    """修正 CloudFront 图片 URL 中被拼到路径后的微信参数。"""
    url = (url or "").replace("&amp;", "&").strip()
    if "cloudfront-s3.rabyte.cn" not in url:
        return url
    match = re.match(r"^(https?://[^?#]+?\.(?:png|jpe?g|gif|webp))(?:[?&].*)?$", url, flags=re.I)
    if match:
        return match.group(1)
    return url


def detect_code_language(element) -> str:
    """检测代码块的语言"""
    classes = element.get("class", [])
    if isinstance(classes, str):
        classes = classes.split()
    
    for cls in classes:
        cls_lower = cls.lower()
        if cls_lower.startswith("language-"):
            lang = cls_lower[9:]
            return LANGUAGE_MAP.get(lang, lang)
        if cls_lower.startswith("lang-"):
            lang = cls_lower[5:]
            return LANGUAGE_MAP.get(lang, lang)
        if cls_lower in LANGUAGE_MAP:
            return LANGUAGE_MAP[cls_lower]
    
    code_elem = element.find("code")
    if code_elem:
        code_classes = code_elem.get("class", [])
        if isinstance(code_classes, str):
            code_classes = code_classes.split()
        for cls in code_classes:
            cls_lower = cls.lower()
            if cls_lower.startswith("language-"):
                lang = cls_lower[9:]
                return LANGUAGE_MAP.get(lang, lang)
            if cls_lower.startswith("lang-"):
                lang = cls_lower[5:]
                return LANGUAGE_MAP.get(lang, lang)
    
    return ""


def extract_urls_from_text(text: str) -> list:
    """从文本中提取URL"""
    url_pattern = r'(https?://[^\s<>"{}|\\^`\[\]]+)'
    return re.findall(url_pattern, text)


def is_sticker_article(html: str) -> bool:
    """检测是否是贴图文章（内容以图片为主，无js_content）"""
    soup = BeautifulSoup(html, "html.parser")
    
    content_div = soup.find("div", id="js_content")
    if not content_div:
        if 'finder' in html.lower() or 'channels' in html.lower():
            return True
        rich_media = soup.find("div", class_="rich_media")
        if rich_media:
            text = rich_media.get_text(strip=True)
            if len(text) < 200 and ('轻触阅读' in text or '微信扫一扫' in text):
                return True
    return False


def extract_article_content(html: str) -> dict:
    """从微信文章HTML中提取内容"""
    soup = BeautifulSoup(html, "html.parser")
    
    result = {
        "title": "",
        "author": "",
        "account_name": "",
        "publish_time": "",
        "content": "",
        "original_url": ""
    }
    
    og_title = soup.find("meta", property="og:title")
    if og_title:
        result["title"] = og_title.get("content", "")
    
    if not result["title"]:
        title_tag = soup.find("h1", class_="rich_media_title")
        if title_tag:
            result["title"] = title_tag.get_text(strip=True)
    
    if not result["title"]:
        title_tag = soup.find("h1")
        if title_tag:
            result["title"] = title_tag.get_text(strip=True)
    
    author_tag = soup.find("a", id="js_name")
    if author_tag:
        result["author"] = author_tag.get_text(strip=True)
    
    if not result["author"]:
        author_tag = soup.find("span", class_="rich_media_meta_nickname")
        if author_tag:
            result["author"] = author_tag.get_text(strip=True)
    
    account_tag = soup.find("strong", class_="profile_nickname")
    if account_tag:
        result["account_name"] = account_tag.get_text(strip=True)
    
    publish_time_tag = soup.find("em", id="publish_time")
    if publish_time_tag:
        result["publish_time"] = publish_time_tag.get_text(strip=True)
    
    if not result["publish_time"]:
        publish_time_tag = soup.find("span", class_="rich_media_meta_date")
        if publish_time_tag:
            result["publish_time"] = publish_time_tag.get_text(strip=True)
    
    content_div = soup.find("div", id="js_content")
    if not content_div:
        content_div = soup.find("div", class_="rich_media_content")
    if not content_div:
        content_div = soup.find("div", class_="article-content")
    
    if content_div:
        result["content"] = str(content_div)
    
    return result


def html_to_markdown(
    html_content: str, 
    image_dir: Path, 
    obsidian_mode: bool = False,
    attachment_dir: Path = None,
    short_name: str = ""
) -> tuple:
    """将HTML内容转换为Markdown
    
    Args:
        html_content: HTML内容
        image_dir: 图片保存目录（当attachment_dir为None时使用）
        obsidian_mode: 是否使用Obsidian格式
        attachment_dir: 图片附件统一存放目录（如果指定，图片会保存到这里）
        short_name: 公众号简称（用于图片命名前缀）
    """
    soup = BeautifulSoup(html_content, "html.parser")
    images = []
    image_index = 0
    video_count = 0
    
    actual_image_dir = attachment_dir if attachment_dir else image_dir
    
    for img in soup.find_all("img"):
        src = img.get("src") or img.get("data-src") or img.get("data-original")
        if src and src.startswith("http"):
            image_index += 1
            filename, img_width = download_image(src, actual_image_dir, short_name, image_index)
            if filename:
                display_width = get_image_display_width(img)
                obsidian_width = calculate_obsidian_width(img_width, display_width)
                images.append((src, filename, obsidian_width))
                img["src"] = filename
                img["data-src"] = filename
                img["obsidian_width"] = obsidian_width
            else:
                img.decompose()
    
    for video in soup.find_all("mpvideo"):
        video_count += 1
        video.replace_with(f"\n\n> 🎥 **视频位置 {video_count}** - 请在[原文](#)中观看\n\n")
    
    for video in soup.find_all("video"):
        video_count += 1
        video.replace_with(f"\n\n> 🎥 **视频位置 {video_count}** - 请在[原文](#)中观看\n\n")
    
    for iframe in soup.find_all("iframe"):
        src = iframe.get("src", "")
        if "video" in src.lower() or "player" in src.lower():
            video_count += 1
            iframe.replace_with(f"\n\n> 🎥 **视频位置 {video_count}** - 请在[原文](#)中观看\n\n")
    
    result_lines = []
    last_was_block = True
    prev_margin_bottom_px = 0
    SPACING_THRESHOLD_PX = 10
    
    def format_image(filename: str, alt: str = "图片", width: int = 0) -> str:
        if obsidian_mode:
            if width > 0:
                return f"![[{filename}|{width}]]\n"
            return f"![[{filename}]]\n"
        else:
            return f"![{alt}]({filename})\n"
    
    def get_element_style(element) -> dict:
        if not element or not hasattr(element, 'get'):
            return {"margin_top": 0, "margin_bottom": 0, "padding_top": 0, "padding_bottom": 0}
        style = element.get("style", "")
        return parse_spacing(style)
    
    def em_to_px(val_em: float) -> float:
        return val_em * 16
    
    def check_spacing_and_add_break(spacing: dict):
        nonlocal prev_margin_bottom_px, last_was_block
        current_margin_top_px = em_to_px(spacing.get("margin_top", 0) + spacing.get("padding_top", 0))
        total_spacing_px = prev_margin_bottom_px + current_margin_top_px
        if total_spacing_px > SPACING_THRESHOLD_PX and not last_was_block:
            result_lines.append("")
    
    def update_prev_margin_bottom(spacing: dict):
        nonlocal prev_margin_bottom_px
        prev_margin_bottom_px = em_to_px(spacing.get("margin_bottom", 0) + spacing.get("padding_bottom", 0))
    
    def process_table(table_node):
        """处理表格"""
        rows = []
        for tr in table_node.find_all("tr"):
            cells = []
            for cell in tr.find_all(["th", "td"]):
                cell_text = cell.get_text(strip=True)
                cell_text = cell_text.replace("|", "\\|").replace("\n", " ")
                cells.append(cell_text)
            if cells:
                rows.append(cells)
        
        if not rows:
            return ""
        
        max_cols = max(len(row) for row in rows)
        
        md_lines = []
        for i, row in enumerate(rows):
            while len(row) < max_cols:
                row.append("")
            md_lines.append("| " + " | ".join(row) + " |")
            if i == 0:
                md_lines.append("| " + " | ".join(["---"] * max_cols) + " |")
        
        return "\n".join(md_lines) + "\n"
    
    def process_node(node, parent_style=None, inherited_tags=None):
        nonlocal last_was_block
        
        if inherited_tags is None:
            inherited_tags = {"bold": False, "italic": False, "underline": False, "color": "", "background": ""}
        
        if isinstance(node, NavigableString):
            text = str(node)
            if not text.strip():
                return
            
            formatted = text
            
            if inherited_tags["italic"]:
                formatted = f"<i>{formatted}</i>"
            
            if inherited_tags["bold"]:
                formatted = f"<b>{formatted}</b>"
            
            if inherited_tags["underline"]:
                formatted = f"<u>{formatted}</u>"
            
            display_color = inherited_tags["color"]
            display_background = inherited_tags["background"]
            
            if display_color and display_background:
                text_rgb = parse_rgb_values(display_color)
                bg_rgb = parse_rgb_values(display_background)
                
                if text_rgb and bg_rgb:
                    contrast = calculate_contrast(text_rgb, bg_rgb)
                    if contrast < 4.5:
                        bg_brightness = (bg_rgb[0] * 299 + bg_rgb[1] * 587 + bg_rgb[2] * 114) / 1000
                        if bg_brightness > 128:
                            display_color = ""
                        else:
                            display_color = "rgb(255, 255, 255)"
            
            if display_color and should_display_color(display_color):
                formatted = f'<span style="color:{display_color}">{formatted}</span>'
            
            if display_background:
                bg_hex = rgb_to_hex(display_background)
                if bg_hex:
                    formatted = f'<mark style="background: {bg_hex};">{formatted}</mark>'
            
            urls = extract_urls_from_text(formatted)
            for url in urls:
                if f"[{url}]" not in formatted:
                    formatted = formatted.replace(url, f"[{url}]({url})")
            
            if result_lines and not result_lines[-1].endswith('\n'):
                result_lines.append(formatted)
            else:
                result_lines.append(formatted)
            last_was_block = False
            return
        
        if node.name in [None, "script", "style", "noscript"]:
            return
        
        new_tags = inherited_tags.copy()
        
        if node.name in ["strong", "b"]:
            new_tags["bold"] = True
        elif node.name in ["em", "i"]:
            new_tags["italic"] = True
        elif node.name == "u":
            new_tags["underline"] = True
        
        style = node.get("style", "") if hasattr(node, 'get') else ""
        color = parse_color(style)
        if color:
            new_tags["color"] = color
        
        bg_color = parse_background_color(style)
        if bg_color:
            new_tags["background"] = bg_color
        
        if node.name == "img":
            src = node.get("src", "")
            alt = node.get("alt", "图片")
            obsidian_width = node.get("obsidian_width", 0)
            if src and not src.startswith("http"):
                result_lines.append(format_image(src, alt, obsidian_width))
                last_was_block = True
            return
        
        if node.name == "br":
            result_lines.append("\n")
            return
        
        if node.name == "table":
            table_md = process_table(node)
            if table_md:
                result_lines.append("\n" + table_md)
                last_was_block = True
            return
        
        if node.name == "pre":
            code_node = node.find("code")
            if code_node:
                code = code_node.get_text()
            else:
                code = node.get_text()
            if code:
                language = detect_code_language(node)
                result_lines.append(f"\n```{language}\n{code.strip()}\n```\n")
                last_was_block = True
            return
        
        if node.name == "blockquote":
            for child in node.children:
                if isinstance(child, str):
                    text = child.strip()
                    if text:
                        result_lines.append(f"> {text}\n")
                elif child.name == "p":
                    text = child.get_text(strip=True)
                    if text:
                        result_lines.append(f"> {text}\n")
                elif hasattr(child, 'children'):
                    process_node(child, inherited_tags=new_tags)
            last_was_block = True
            return
        
        if node.name == "ul":
            def process_list_item(li, indent_level):
                indent = "\t" * indent_level
                li_content = []
                for child in li.children:
                    if child.name in ["ul", "ol"]:
                        for nested_li in child.find_all("li", recursive=False):
                            process_list_item(nested_li, indent_level + 1)
                    elif isinstance(child, str):
                        text = str(child).strip()
                        if text:
                            li_content.append(text)
                    elif hasattr(child, 'children'):
                        original_lines = result_lines[:]
                        result_lines.clear()
                        process_node(child, inherited_tags=new_tags)
                        content = "".join(result_lines).strip()
                        result_lines[:] = original_lines
                        if content:
                            li_content.append(content)
                
                if li_content:
                    content_text = " ".join(li_content)
                    result_lines.append(f"{indent}- {content_text}\n")
            
            for li in node.find_all("li", recursive=False):
                process_list_item(li, 0)
            last_was_block = True
            return
        
        if node.name == "ol":
            def process_ol_item(li, indent_level, start_num):
                indent = "\t" * indent_level
                li_content = []
                nested_counter = 1
                for child in li.children:
                    if child.name == "ul":
                        for nested_li in child.find_all("li", recursive=False):
                            process_list_item_nested(nested_li, indent_level + 1)
                    elif child.name == "ol":
                        for nested_li in child.find_all("li", recursive=False):
                            process_ol_item(nested_li, indent_level + 1, nested_counter)
                            nested_counter += 1
                    elif isinstance(child, str):
                        text = str(child).strip()
                        if text:
                            li_content.append(text)
                    elif hasattr(child, 'children'):
                        original_lines = result_lines[:]
                        result_lines.clear()
                        process_node(child, inherited_tags=new_tags)
                        content = "".join(result_lines).strip()
                        result_lines[:] = original_lines
                        if content:
                            li_content.append(content)
                
                if li_content:
                    content_text = " ".join(li_content)
                    result_lines.append(f"{indent}{start_num}. {content_text}\n")
            
            def process_list_item_nested(li, indent_level):
                indent = "\t" * indent_level
                li_content = []
                for child in li.children:
                    if child.name in ["ul", "ol"]:
                        for nested_li in child.find_all("li", recursive=False):
                            process_list_item_nested(nested_li, indent_level + 1)
                    elif isinstance(child, str):
                        text = str(child).strip()
                        if text:
                            li_content.append(text)
                    elif hasattr(child, 'children'):
                        original_lines = result_lines[:]
                        result_lines.clear()
                        process_node(child, inherited_tags=new_tags)
                        content = "".join(result_lines).strip()
                        result_lines[:] = original_lines
                        if content:
                            li_content.append(content)
                
                if li_content:
                    content_text = " ".join(li_content)
                    result_lines.append(f"{indent}- {content_text}\n")
            
            for idx, li in enumerate(node.find_all("li", recursive=False), 1):
                process_ol_item(li, 0, idx)
            last_was_block = True
            return
        
        if node.name in ["h1", "h2", "h3", "h4", "h5", "h6"]:
            spacing = get_element_style(node)
            check_spacing_and_add_break(spacing)
            
            level = int(node.name[1])
            prefix = "#" * level
            text = node.get_text(strip=True)
            if text:
                result_lines.append(f"{prefix} {text}\n")
                last_was_block = True
            update_prev_margin_bottom(spacing)
            return
        
        if node.name == "p":
            spacing = get_element_style(node)
            check_spacing_and_add_break(spacing)
            
            for child in node.children:
                process_node(child, inherited_tags=new_tags)
            result_lines.append("\n")
            last_was_block = True
            update_prev_margin_bottom(spacing)
            return
        
        if node.name == "section":
            spacing = get_element_style(node)
            check_spacing_and_add_break(spacing)
            
            for child in node.children:
                process_node(child, inherited_tags=new_tags)
            
            result_lines.append("\n")
            last_was_block = True
            update_prev_margin_bottom(spacing)
            return
        
        if node.name == "code":
            text = node.get_text(strip=True)
            if text:
                result_lines.append(f"`{text}`")
            return
        
        if node.name == "a":
            href = node.get("href", "")
            text = node.get_text(strip=True)
            if href and text and not href.startswith("#"):
                result_lines.append(f"[{text}]({href})")
                return
        
        for child in node.children:
            process_node(child, inherited_tags=new_tags)
    
    content_div = soup.find("div", id="js_content")
    if content_div:
        for child in content_div.children:
            process_node(child)
    else:
        for child in soup.children:
            process_node(child)
    
    markdown = "".join(result_lines)
    
    markdown = re.sub(r'\n[ \t]+\n', '\n\n', markdown)
    markdown = re.sub(r'\n{3,}', '\n\n', markdown)
    markdown = markdown.strip()
    
    return markdown, images, video_count


def parse_publish_date(publish_date_str: str) -> str:
    """解析发布日期字符串，返回YYYYMMDD格式
    
    Args:
        publish_date_str: 日期字符串，如 "2026-04-06 10:30:00" 或 "2026-04-06"
    
    Returns:
        str: YYYYMMDD格式日期，如 "20260406"
    """
    if not publish_date_str:
        return datetime.now().strftime('%Y%m%d')
    
    try:
        for fmt in ['%Y-%m-%d %H:%M:%S', '%Y-%m-%d', '%Y/%m/%d', '%Y%m%d']:
            try:
                dt = datetime.strptime(publish_date_str[:19], fmt)
                return dt.strftime('%Y%m%d')
            except ValueError:
                continue
        
        return datetime.now().strftime('%Y%m%d')
    except Exception:
        return datetime.now().strftime('%Y%m%d')


def generate_filename(publish_date: str, short_name: str, title: str) -> str:
    """生成Markdown文件名
    
    Args:
        publish_date: 发布日期字符串
        short_name: 公众号简称
        title: 文章标题
    
    Returns:
        str: 文件名，如 "20260406_聚义_投资最重要的三件事.md"
    """
    date_str = parse_publish_date(publish_date)
    safe_title = sanitize_filename(title)
    safe_short_name = sanitize_filename(short_name) if short_name else "未知"
    
    filename = f"{date_str}_{safe_short_name}_{safe_title}.md"
    return filename


def show_windows_notification(title: str, message: str, app_id: str = "Microsoft.PowerToys"):
    """发送 Windows 原生 Toast 通知
    
    Args:
        title: 通知标题
        message: 通知内容
        app_id: 应用程序ID
    """
    import subprocess
    
    try:
        ps_script = f'''
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null

$template = @"
<toast>
    <visual>
        <binding template="ToastText02">
            <text id="1">{title}</text>
            <text id="2">{message}</text>
        </binding>
    </visual>
</toast>
"@

$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml($template)
$toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("{app_id}").Show($toast)
'''
        
        result = subprocess.run(
            ['powershell', '-command', ps_script],
            capture_output=True,
            text=True,
            timeout=10
        )
        
        if result.returncode != 0:
            pass
        
    except Exception:
        pass


def download_wechat_article(
    url: str, 
    output_dir: str = None, 
    obsidian_mode: bool = False,
    attachment_dir: str = None,
    short_name: str = ""
) -> dict:
    """下载微信文章并转换为Markdown
    
    Args:
        url: 微信文章链接
        output_dir: Markdown文件输出目录
        obsidian_mode: 是否使用Obsidian格式
        attachment_dir: 图片附件统一存放目录（如果指定，图片会保存到这里而不是文章目录下）
        short_name: 公众号简称（用于文件名前缀和图片命名）
    """
    if output_dir is None:
        output_dir = OUTPUT_DIR
    
    os.makedirs(output_dir, exist_ok=True)
    
    if attachment_dir:
        os.makedirs(attachment_dir, exist_ok=True)
    
    download_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    log_info(f"\n{'=' * 60}")
    log_info("📥 微信文章下载器")
    if obsidian_mode:
        log_info("💎 Obsidian模式")
    log_info(f"{'=' * 60}")
    log_info(f"🔗 URL: {url[:60]}...")
    
    try:
        log_info("\n⏳ 正在获取文章内容...")
        resp = requests.get(url, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        resp.encoding = "utf-8"
        
        html = resp.text
        
        if is_sticker_article(html):
            log_warn("⚠️ 这是贴图文章，暂不支持下载，已跳过")
            return {"success": False, "error": "贴图文章不支持下载"}
        
        log_info("✅ 文章内容获取成功")
        
        log_info("\n⏳ 正在解析文章...")
        article = extract_article_content(html)
        
        if not article["title"]:
            article["title"] = f"微信文章_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        
        log_info(f"📝 标题: {article['title']}")
        if article["author"]:
            log_info(f"👤 公众号: {article['author']}")
        if article["publish_time"]:
            log_info(f"📅 时间: {article['publish_time']}")
        
        account_name = article["author"] or "未知"
        account_short_name = short_name if short_name else account_name[:4]
        
        if attachment_dir:
            image_dir = Path(attachment_dir)
        else:
            image_dir = Path(output_dir)
        image_dir.mkdir(parents=True, exist_ok=True)
        
        log_info(f"\n⏳ 正在转换为Markdown并下载图片...")
        markdown, images, video_count = html_to_markdown(
            article["content"], 
            image_dir, 
            obsidian_mode=True,
            attachment_dir=Path(attachment_dir) if attachment_dir else None,
            short_name=account_short_name
        )
        
        md_content = []
        
        if article["author"]:
            md_content.append(f"- **公众号**: #{article['author']}")
        if article["account_name"]:
            md_content.append(f"- **作者**: {article['account_name']}")
        if article["publish_time"]:
            md_content.append(f"- **发布时间**: {article['publish_time']}")
        md_content.append(f"- **原文链接**: [{article['title']}]({url})")
        md_content.append(f"- [ ] **是否已读**")
        md_content.append(f"- **人工标签**: ")
        md_content.append(f"- **我的评价**: ")
        
        md_content = [line for line in md_content if line]
        
        md_content.append("")
        md_content.append("---")
        md_content.append("")
        md_content.append("# 正文")
        md_content.append("")
        md_content.append(markdown)
        
        md_content.append("")
        md_content.append("---")
        md_content.append(f"下载时间: {download_time}")
        md_content.append(f"唯一标识: {url}")
        
        final_md = normalize_markdown_output("\n".join(md_content))
        
        base_filename = generate_filename(article["publish_time"], account_short_name, article["title"])
        md_filename = get_unique_md_filename(output_dir, base_filename)
        md_file = os.path.join(output_dir, md_filename)
        
        with open(md_file, "w", encoding="utf-8") as f:
            f.write(final_md)
        
        log_info(f"\n✅ 转换完成!")
        log_info(f"📁 保存位置: {md_file}")
        log_info(f"🖼️ 图片数量: {len(images)}")
        if video_count > 0:
            log_info(f"🎥 视频数量: {video_count} (已标记位置)")
        
        show_windows_notification(
            "微信文章下载完成",
            f"{article['title'][:30]}... - {len(images)}张图片"
        )
        
        return {
            "success": True,
            "title": article["title"],
            "author": article["author"],
            "output_dir": output_dir,
            "md_file": md_file,
            "md_filename": md_filename,
            "image_count": len(images),
            "video_count": video_count
        }
        
    except requests.exceptions.RequestException as e:
        log_error(f"\n❌ 网络请求失败: {e}")
        return {"success": False, "error": str(e)}
    except Exception as e:
        log_error(f"\n❌ 处理失败: {e}")
        return {"success": False, "error": str(e)}


def interactive_mode():
    """交互模式"""
    print("\n" + "=" * 60)
    print("📥 微信文章下载转Markdown工具")
    print("=" * 60)
    print("输入微信文章URL，按回车下载")
    print("输入 'o' 或 'obsidian' 切换Obsidian模式")
    print("输入 'q' 或 'quit' 退出")
    print("=" * 60)
    
    obsidian_mode = False
    
    while True:
        print("\n" + "-" * 60)
        if obsidian_mode:
            print("💎 Obsidian模式已开启")
        url = input("请输入微信文章URL: ").strip()
        
        if url.lower() in ["q", "quit", "exit"]:
            print("\n👋 再见!")
            break
        
        if url.lower() in ["o", "obsidian"]:
            obsidian_mode = not obsidian_mode
            status = "开启" if obsidian_mode else "关闭"
            print(f"💎 Obsidian模式已{status}")
            continue
        
        if not url:
            print("⚠️ URL不能为空")
            continue
        
        if "mp.weixin.qq.com" not in url:
            print("⚠️ 请输入有效的微信公众号文章链接")
            continue
        
        result = download_wechat_article(url, obsidian_mode=obsidian_mode)
        
        if result["success"]:
            print(f"\n🎉 下载成功: {result['title']}")


def main():
    """主函数"""
    obsidian_mode = False
    url = None
    
    args = sys.argv[1:]
    for arg in args:
        if arg.lower() in ["-o", "--obsidian", "-obsidian"]:
            obsidian_mode = True
        elif "mp.weixin.qq.com" in arg:
            url = arg
    
    if url:
        download_wechat_article(url, obsidian_mode=obsidian_mode)
    else:
        interactive_mode()


if __name__ == "__main__":
    main()

