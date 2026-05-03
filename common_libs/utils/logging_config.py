import os
import sys
import logging
from datetime import datetime


def setup_logging(log_dir, log_prefix="download", retention_days=30):
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, f"{log_prefix}_{datetime.now().strftime('%Y%m%d')}.log")

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(message)s',
        handlers=[
            logging.FileHandler(log_file, encoding='utf-8'),
            logging.StreamHandler(sys.stdout)
        ]
    )
    logger = logging.getLogger(__name__)

    for handler in logger.handlers:
        if isinstance(handler, logging.StreamHandler):
            handler.stream = sys.stdout

    return logger


def clean_old_logs(log_dir, retention_days=30):
    if not os.path.exists(log_dir):
        return

    try:
        now = datetime.now()
        cleaned_count = 0
        for filename in os.listdir(log_dir):
            if not filename.endswith('.log'):
                continue

            filepath = os.path.join(log_dir, filename)
            try:
                file_time = datetime.fromtimestamp(os.path.getmtime(filepath))
                if (now - file_time).days > retention_days:
                    os.remove(filepath)
                    cleaned_count += 1
            except Exception:
                pass

        if cleaned_count > 0:
            print(f"已清理 {cleaned_count} 个过期日志文件")
    except Exception as e:
        print(f"清理日志失败: {e}")


def get_log_functions(logger):
    def log_info(msg):
        logger.info(msg)

    def log_warn(msg):
        logger.warning(msg)

    def log_error(msg):
        logger.error(msg)

    return log_info, log_warn, log_error
