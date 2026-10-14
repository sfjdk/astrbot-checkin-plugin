"""
🦌 每日打卡插件
==============
AstrBot QQ 人机 打卡插件

只有一个项目 🦌，每天可多次打卡（30 分钟冷却）。
🦌表 自动生成图片格式日历。
"""

__plugin_name__ = "🦌打卡"
__plugin_desc__ = "🦌 每日打卡，自动月表格图片"
__plugin_version__ = "1.3.0"
__plugin_author__ = "🦌"

from .main import DeerPlugin

__all__ = ["DeerPlugin"]
