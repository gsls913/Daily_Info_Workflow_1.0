"""
小宇宙播客解析器
通过解析网页中的 __NEXT_DATA__ 获取播客和单集信息
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Dict, List, Optional
from pathlib import Path

import requests


@dataclass
class Episode:
    """播客单集信息"""
    eid: str
    pid: str
    title: str
    description: str = ""
    duration: int = 0
    pub_date: str = ""
    audio_url: str = ""
    play_count: int = 0
    clap_count: int = 0
    comment_count: int = 0
    favorite_count: int = 0
    image_url: str = ""
    status: str = ""
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
    
    @property
    def duration_formatted(self) -> str:
        """格式化时长"""
        if self.duration <= 0:
            return "未知"
        hours = self.duration // 3600
        minutes = (self.duration % 3600) // 60
        seconds = self.duration % 60
        if hours > 0:
            return f"{hours}:{minutes:02d}:{seconds:02d}"
        return f"{minutes}:{seconds:02d}"


@dataclass
class Podcast:
    """播客信息"""
    pid: str
    title: str
    author: str = ""
    description: str = ""
    subscription_count: int = 0
    episode_count: int = 0
    image_url: str = ""
    episodes: List[Episode] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        result = asdict(self)
        result["episodes"] = [e.to_dict() for e in self.episodes]
        return result


def parse_episode(data: Dict[str, Any]) -> Episode:
    """解析单集数据"""
    return Episode(
        eid=data.get("eid", ""),
        pid=data.get("pid", ""),
        title=data.get("title", ""),
        description=data.get("description", "")[:500] + "..." if len(data.get("description", "")) > 500 else data.get("description", ""),
        duration=data.get("duration", 0),
        pub_date=data.get("pubDate", ""),
        audio_url=data.get("enclosure", {}).get("url", ""),
        play_count=data.get("playCount", 0),
        clap_count=data.get("clapCount", 0),
        comment_count=data.get("commentCount", 0),
        favorite_count=data.get("favoriteCount", 0),
        image_url=data.get("image", {}).get("picUrl", ""),
        status=data.get("status", ""),
    )


def fetch_podcast_page(url: str, timeout: int = 30) -> str:
    """
    获取播客页面内容
    
    Args:
        url: 播客主页URL
        timeout: 超时时间
    
    Returns:
        HTML内容
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    
    response = requests.get(url, headers=headers, timeout=timeout)
    response.raise_for_status()
    return response.text


def extract_next_data(html: str) -> Optional[Dict[str, Any]]:
    """
    从HTML中提取 __NEXT_DATA__ JSON数据
    
    Args:
        html: HTML内容
    
    Returns:
        解析后的JSON数据
    """
    pattern = r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>'
    match = re.search(pattern, html, re.DOTALL)
    
    if not match:
        return None
    
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        return None


def parse_podcast_from_page(html: str) -> Optional[Podcast]:
    """
    从页面HTML解析播客信息
    
    Args:
        html: HTML内容
    
    Returns:
        Podcast对象
    """
    next_data = extract_next_data(html)
    
    if not next_data:
        return None
    
    props = next_data.get("props", {})
    page_props = props.get("pageProps", {})
    podcast_data = page_props.get("podcast", {})
    
    if not podcast_data:
        return None
    
    podcast = Podcast(
        pid=podcast_data.get("pid", ""),
        title=podcast_data.get("title", ""),
        author=podcast_data.get("author", ""),
        description=podcast_data.get("description", "")[:500] if podcast_data.get("description") else "",
        subscription_count=podcast_data.get("subscriptionCount", 0),
        episode_count=podcast_data.get("episodeCount", 0),
        image_url=podcast_data.get("image", {}).get("picUrl", ""),
    )
    
    episodes_data = podcast_data.get("episodes", [])
    for ep_data in episodes_data:
        episode = parse_episode(ep_data)
        podcast.episodes.append(episode)
    
    return podcast


def fetch_podcast_info(url: str, timeout: int = 30) -> Optional[Podcast]:
    """
    获取播客信息
    
    Args:
        url: 播客主页URL
        timeout: 超时时间
    
    Returns:
        Podcast对象
    """
    html = fetch_podcast_page(url, timeout)
    return parse_podcast_from_page(html)


def print_podcast_info(podcast: Podcast):
    """打印播客信息"""
    print("\n" + "=" * 60)
    print(f"播客名称: {podcast.title}")
    print(f"播客ID: {podcast.pid}")
    print(f"作者: {podcast.author}")
    print(f"订阅数: {podcast.subscription_count:,}")
    print(f"单集总数: {podcast.episode_count}")
    print(f"本次获取: {len(podcast.episodes)} 个单集")
    print(f"描述: {podcast.description[:100]}...")
    print("=" * 60)
    
    print(f"\n单集列表:\n")
    
    for i, ep in enumerate(podcast.episodes, 1):
        print(f"[{i}] {ep.title}")
        print(f"    发布: {ep.pub_date}")
        print(f"    时长: {ep.duration_formatted}")
        print(f"    播放: {ep.play_count:,} | 点赞: {ep.clap_count} | 评论: {ep.comment_count}")
        if ep.audio_url:
            print(f"    音频: {ep.audio_url}")
        print()


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 2:
        print("用法: python xiaoyuzhou_parser.py <播客URL>")
        print("示例: python xiaoyuzhou_parser.py https://www.xiaoyuzhoufm.com/podcast/62a44d14273a9f1de2a38c24")
        sys.exit(1)
    
    url = sys.argv[1]
    print(f"正在获取: {url}")
    
    try:
        podcast = fetch_podcast_info(url)
        
        if podcast:
            print_podcast_info(podcast)
            
            output_file = f"podcast_{podcast.pid}.json"
            with open(output_file, "w", encoding="utf-8") as f:
                json.dump(podcast.to_dict(), f, ensure_ascii=False, indent=2)
            print(f"\n数据已保存到: {output_file}")
        else:
            print("解析失败，未找到播客数据")
            
    except Exception as e:
        print(f"错误: {e}")
        import traceback
        traceback.print_exc()
