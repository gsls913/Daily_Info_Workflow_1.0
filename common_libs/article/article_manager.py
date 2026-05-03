import os
import re
import shutil
from datetime import datetime
from urllib.parse import unquote


IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg"}


def _safe_print(message):
    try:
        print(message)
    except UnicodeEncodeError:
        print(str(message).encode("gbk", errors="replace").decode("gbk"))


def check_if_read(md_file_path):
    try:
        with open(md_file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        if "- [x] **是否已读**" in content:
            return True
        return False
    except Exception as e:
        _safe_print(f"检查文件失败 {md_file_path}: {e}")
        return False


def extract_date_from_md(md_file_path, date_field="日期"):
    try:
        with open(md_file_path, 'r', encoding='utf-8') as f:
            content = f.read()

        fields = [date_field, "日期", "发布时间", "发布日期", "下载时间"]
        seen = set()
        for field in fields:
            if not field or field in seen:
                continue
            seen.add(field)
            date_pattern = rf'-\s*\*\*{re.escape(field)}\*\*:\s*(\d{{4}}-\d{{2}}-\d{{2}})'
            match = re.search(date_pattern, content)

            if match:
                date_str = match.group(1)
                try:
                    return datetime.strptime(date_str, '%Y-%m-%d')
                except ValueError:
                    return None
        return None
    except Exception as e:
        _safe_print(f"读取文件日期失败 {md_file_path}: {e}")
        return None


def _normalize_image_name(raw_name):
    name = unquote(str(raw_name or "").strip())
    if not name:
        return ""
    if "|" in name:
        name = name.split("|", 1)[0].strip()
    if "#" in name:
        name = name.split("#", 1)[0].strip()
    name = name.strip("<>").strip()
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", name):
        return ""
    name = os.path.basename(name.replace("\\", "/"))
    if os.path.splitext(name)[1].lower() not in IMAGE_EXTENSIONS:
        return ""
    return name


def extract_images_from_content(content):
    images = []

    # Obsidian wikilinks: ![[image.png]] or ![[image.png|500]]
    for match in re.findall(r'!\[\[([^\]]+)\]\]', content):
        image_name = _normalize_image_name(match)
        if image_name:
            images.append(image_name)

    # Standard Markdown images: ![alt](image.png) or ![alt](<image name.png>)
    for match in re.findall(r'!\[[^\]]*\]\(([^)]+)\)', content):
        target = match.strip()
        if target.startswith("<") and target.endswith(">"):
            target = target[1:-1]
        image_name = _normalize_image_name(target)
        if image_name:
            images.append(image_name)

    # Basic HTML image tags that sometimes survive conversion.
    for match in re.findall(r'<img\b[^>]*\bsrc=["\']([^"\']+)["\']', content, flags=re.IGNORECASE):
        image_name = _normalize_image_name(match)
        if image_name:
            images.append(image_name)

    return sorted(set(images))


def extract_images_from_md(md_file_path):
    try:
        with open(md_file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        return extract_images_from_content(content)
    except Exception as e:
        _safe_print(f"提取图片引用失败 {md_file_path}: {e}")
        return []


def _safe_attachment_path(attachment_dir, image_name):
    image_name = _normalize_image_name(image_name)
    if not image_name:
        return None

    base_dir = os.path.abspath(attachment_dir)
    image_path = os.path.abspath(os.path.join(base_dir, image_name))
    try:
        common = os.path.commonpath([base_dir, image_path])
    except ValueError:
        return None
    if common != base_dir:
        return None
    return image_path


def _image_referenced_elsewhere(search_dir, image_name, excluded_md_path):
    excluded = os.path.abspath(excluded_md_path)
    if not os.path.exists(search_dir):
        return False

    for root, _, files in os.walk(search_dir):
        for filename in files:
            if not filename.endswith(".md"):
                continue
            md_path = os.path.abspath(os.path.join(root, filename))
            if md_path == excluded:
                continue
            try:
                with open(md_path, "r", encoding="utf-8") as f:
                    content = f.read()
                if image_name in extract_images_from_content(content):
                    return True
            except Exception:
                continue
    return False


def ensure_read_folder_structure(base_dir, subfolders):
    read_folder = os.path.join(base_dir, "已读")
    os.makedirs(read_folder, exist_ok=True)

    for subfolder in subfolders:
        subfolder_path = os.path.join(read_folder, subfolder)
        os.makedirs(subfolder_path, exist_ok=True)


def archive_read_articles(source_dir, base_dir, subfolders, date_field="日期"):
    ensure_read_folder_structure(base_dir, subfolders)

    if not os.path.exists(source_dir):
        return 0

    total_archived = 0
    md_files = [f for f in os.listdir(source_dir) if f.endswith('.md')]

    for md_file in md_files:
        md_file_path = os.path.join(source_dir, md_file)

        if check_if_read(md_file_path):
            folder_name = os.path.basename(source_dir)
            if folder_name not in subfolders:
                folder_name = subfolders[-1] if subfolders else "其他"

            read_folder = os.path.join(base_dir, "已读", folder_name)
            dest_path = os.path.join(read_folder, md_file)

            if os.path.exists(dest_path):
                _safe_print(f"目标文件已存在，跳过: {md_file}")
                continue

            try:
                shutil.move(md_file_path, dest_path)
                total_archived += 1
                _safe_print(f"  ✓ 已归档: {md_file} → 已读/{folder_name}/")
            except Exception as e:
                _safe_print(f"移动文件失败 {md_file}: {e}")

    if total_archived > 0:
        _safe_print(f"📊 归档统计: 总计归档 {total_archived} 篇已读文章")

    return total_archived


def archive_read_articles_from_folders(base_dir, subfolders, date_field="日期"):
    ensure_read_folder_structure(base_dir, subfolders)

    total_archived = 0

    for folder_name in subfolders:
        folder_path = os.path.join(base_dir, folder_name)

        if not os.path.exists(folder_path):
            continue

        md_files = [f for f in os.listdir(folder_path) if f.endswith('.md')]

        if not md_files:
            continue

        archived_count = 0
        for md_file in md_files:
            md_file_path = os.path.join(folder_path, md_file)

            if check_if_read(md_file_path):
                read_folder = os.path.join(base_dir, "已读", folder_name)
                dest_path = os.path.join(read_folder, md_file)

                if os.path.exists(dest_path):
                    _safe_print(f"目标文件已存在，跳过: {md_file}")
                    continue

                try:
                    shutil.move(md_file_path, dest_path)
                    archived_count += 1
                    _safe_print(f"  ✓ 已归档: {md_file} → 已读/{folder_name}/")
                except Exception as e:
                    _safe_print(f"移动文件失败 {md_file}: {e}")

        total_archived += archived_count

    if total_archived > 0:
        _safe_print(f"📊 归档统计: 总计归档 {total_archived} 篇已读文章")

    return total_archived


def clean_old_read_articles(base_dir, subfolders, days_threshold=90, attachment_dir=None, date_field="日期"):
    read_folder = os.path.join(base_dir, "已读")

    if not os.path.exists(read_folder):
        _safe_print("已读文件夹不存在，跳过清理")
        return 0, 0

    now = datetime.now()
    total_deleted_articles = 0
    total_deleted_images = 0

    for subfolder in subfolders:
        subfolder_path = os.path.join(read_folder, subfolder)

        if not os.path.exists(subfolder_path):
            continue

        md_files = [f for f in os.listdir(subfolder_path) if f.endswith('.md')]

        if not md_files:
            continue

        deleted_articles = 0
        deleted_images = 0

        for md_file in md_files:
            md_file_path = os.path.join(subfolder_path, md_file)

            article_date = extract_date_from_md(md_file_path, date_field)

            if article_date is None:
                continue

            days_diff = (now - article_date).days

            if days_diff > days_threshold:
                images = extract_images_from_md(md_file_path) if attachment_dir else []

                try:
                    os.remove(md_file_path)
                    deleted_articles += 1
                    _safe_print(f"  ✓ 已删除: {md_file} ({days_diff}天前)")
                except Exception as e:
                    _safe_print(f"删除文件失败 {md_file}: {e}")
                    continue

                if attachment_dir:
                    for image_name in images:
                        image_path = _safe_attachment_path(attachment_dir, image_name)
                        if not image_path or not os.path.exists(image_path):
                            continue
                        if _image_referenced_elsewhere(base_dir, image_name, md_file_path):
                            _safe_print(f"  - 图片仍被其他文档引用，跳过: {image_name}")
                            continue
                        try:
                            os.remove(image_path)
                            deleted_images += 1
                            _safe_print(f"  ✓ 删除图片: {image_name}")
                        except Exception as e:
                            _safe_print(f"删除图片失败 {image_name}: {e}")

        total_deleted_articles += deleted_articles
        total_deleted_images += deleted_images

    if total_deleted_articles > 0:
        img_msg = f", {total_deleted_images} 张图片" if attachment_dir and total_deleted_images > 0 else ""
        _safe_print(f"📊 清理统计: 总计删除 {total_deleted_articles} 篇过期文章{img_msg}")

    return total_deleted_articles, total_deleted_images
