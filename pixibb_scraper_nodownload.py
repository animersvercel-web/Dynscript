#!/usr/bin/env python3
"""
PixiBB Image Scraper - No Local Storage Version
Extracts image URLs without saving to disk
Can send directly to Telegram or other platforms
"""

import re
import json
import asyncio
import aiohttp
from urllib.parse import urljoin, urlparse
from dataclasses import dataclass
from typing import List, Optional, Dict
import os


@dataclass
class AlbumInfo:
    title: str
    url: str
    image_count: int
    video_count: int
    post_id: str = ""
    images: List[Dict] = None


class PixiBBScraper:
    BASE_URL = "https://sexy.pixibb.com"
    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    }

    def __init__(self):
        self.session: Optional[aiohttp.ClientSession] = None

    async def __aenter__(self):
        self.session = aiohttp.ClientSession(headers=self.HEADERS)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            await self.session.close()

    def _parse_album_info(self, html: str, url: str) -> tuple[AlbumInfo, str, str]:
        """Extract album metadata from HTML, returns (info, post_id, nonce)"""
        title = "Unknown"

        # Try breadcrumb current-item
        title_match = re.search(r'class="post post-post current-item"[^>]*>([^<]+)</span>', html)
        if title_match:
            title = title_match.group(1).strip()

        # Try Yoast JSON
        if title == "Unknown":
            yoast_match = re.search(r'<script type="application/ld\+json"[^>]*class="yoast-schema-graph"[^>]*>(.*?)</script>', html, re.DOTALL)
            if yoast_match:
                try:
                    yoast_data = json.loads(yoast_match.group(1))
                    for item in yoast_data.get('@graph', []):
                        if item.get('@type') == 'Article':
                            title = item.get('headline', title)
                            break
                except:
                    pass

        # Extract image/video count
        img_match = re.search(r'(\d+)\s*photos?', html, re.IGNORECASE)
        image_count = int(img_match.group(1)) if img_match else 0

        vid_match = re.search(r'(\d+)\s*videos?', html, re.IGNORECASE)
        video_count = int(vid_match.group(1)) if vid_match else 0

        # Extract galleryModeData
        post_id = ""
        nonce = ""
        gallery_match = re.search(r'var galleryModeData = ({[^;]+});', html)
        if gallery_match:
            try:
                gallery_data = json.loads(gallery_match.group(1))
                post_id = gallery_data.get('postId', '')
                nonce = gallery_data.get('nonce', '')
            except:
                pass

        info = AlbumInfo(
            title=title,
            url=url,
            image_count=image_count,
            video_count=video_count,
            post_id=post_id,
            images=[]
        )

        return info, post_id, nonce

    async def fetch_api_images(self, post_id: str, nonce: str) -> List[str]:
        """Fetch all images from the gallery API"""
        if not post_id or not nonce:
            return []

        api_url = f"{self.BASE_URL}/wp-json/v1/gallery-images/{post_id}"

        try:
            headers = {**self.HEADERS, "X-WP-Nonce": nonce}
            async with self.session.get(api_url, headers=headers, timeout=30) as response:
                if response.status == 200:
                    data = await response.json()
                    if data.get('success') and isinstance(data.get('images'), list):
                        return data['images']
        except Exception as e:
            print(f"API request failed: {e}")

        return []

    async def scrape_album(self, album_url: str) -> AlbumInfo:
        """Scrape complete album - only extracts URLs, NO download"""
        album_url = album_url.replace('/videos/', '/')

        print(f"🔍 Fetching album page...")

        html = await self.fetch_page(album_url)
        if not html:
            raise Exception(f"Failed to fetch album: {album_url}")

        info, post_id, nonce = self._parse_album_info(html, album_url)

        print(f"📁 Album: {info.title}")
        print(f"📸 Images: {info.image_count}, 🎬 Videos: {info.video_count}")

        if post_id and nonce:
            print(f"🔑 Post ID: {post_id}")
            print(f"📡 Fetching image URLs from API...")

            image_urls = await self.fetch_api_images(post_id, nonce)

            images = []
            for url in image_urls:
                filename = url.split('/')[-1].split('?')[0]
                images.append({
                    'url': url,
                    'filename': filename,
                    'type': 'full'
                })

            info.images = images
            print(f"✅ Retrieved {len(info.images)} image URLs")
        else:
            print("⚠️ Could not extract API credentials")
            info.images = []

        return info

    async def fetch_page(self, url: str) -> str:
        """Fetch a page and return HTML content"""
        try:
            async with self.session.get(url, timeout=30) as response:
                if response.status == 200:
                    return await response.text()
                else:
                    print(f"Error fetching {url}: HTTP {response.status}")
                    return ""
        except Exception as e:
            print(f"Error fetching {url}: {e}")
            return ""

    def export_urls_to_txt(self, info: AlbumInfo, output_path: str):
        """Export just the URLs to a text file (for wget/curl)"""
        with open(output_path, 'w', encoding='utf-8') as f:
            for img in info.images:
                f.write(f"{img['url']}\n")
        print(f"💾 URLs exported to: {output_path}")

    def export_to_json(self, info: AlbumInfo, output_path: str):
        """Export album info to JSON"""
        data = {
            'title': info.title,
            'url': info.url,
            'post_id': info.post_id,
            'image_count': info.image_count,
            'video_count': info.video_count,
            'images_count': len(info.images),
            'images': info.images
        }

        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        print(f"💾 Metadata exported to: {output_path}")


class TelegramSender:
    """Send images directly to Telegram without saving to disk"""

    def __init__(self, bot_token: str, chat_id: str):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.base_url = f"https://api.telegram.org/bot{bot_token}"

    async def send_text(self, session: aiohttp.ClientSession, text: str):
        """Send text message"""
        url = f"{self.base_url}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "HTML"
        }
        try:
            async with session.post(url, json=payload, timeout=30) as resp:
                return resp.status == 200
        except Exception as e:
            print(f"Failed to send text: {e}")
            return False

    async def send_photo(self, session: aiohttp.ClientSession, photo_url: str, caption: str = ""):
        """Send photo by URL (no local file needed)"""
        url = f"{self.base_url}/sendPhoto"
        payload = {
            "chat_id": self.chat_id,
            "photo": photo_url,
            "caption": caption[:1024] if caption else ""
        }
        try:
            async with session.post(url, json=payload, timeout=60) as resp:
                if resp.status == 200:
                    return True
                else:
                    error = await resp.text()
                    print(f"Failed to send photo: {error[:100]}")
                    return False
        except Exception as e:
            print(f"Failed to send photo: {e}")
            return False

    async def send_photos_batch(self, image_urls: List[str], album_title: str = ""):
        """Send multiple photos directly from URLs"""
        async with aiohttp.ClientSession() as session:
            # Send album title first
            if album_title:
                await self.send_text(session, f"📁 <b>{album_title}</b>\n🖼 {len(image_urls)} photos")

            # Send photos in batches of 10 (Telegram limit for media group)
            # But sending one by one for now to avoid complexity
            success_count = 0
            for i, url in enumerate(image_urls, 1):
                print(f"  📤 Sending photo {i}/{len(image_urls)}: {url.split('/')[-1][:30]}...")
                if await self.send_photo(session, url, f"Photo {i}/{len(image_urls)}"):
                    success_count += 1
                # Small delay to avoid rate limiting
                await asyncio.sleep(0.5)

            await self.send_text(session, f"✅ Done! Sent {success_count}/{len(image_urls)} photos")
            return success_count


async def main():
    import sys

    # Get album URL
    if len(sys.argv) > 1:
        album_url = sys.argv[1]
    else:
        album_url = "https://sexy.pixibb.com/81-2023-09-25-265-photos-12-videos-sexy-girl/"

    # Check if telegram mode
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    async with PixiBBScraper() as scraper:
        info = await scraper.scrape_album(album_url)

        # Export metadata only (small file)
        safe_title = re.sub(r'[^\w\-_.]', '_', info.title)[:50]
        json_file = f"{safe_title}.json"
        scraper.export_to_json(info, json_file)

        # Export URLs list
        txt_file = f"{safe_title}_urls.txt"
        scraper.export_urls_to_txt(info, txt_file)

        # If Telegram config exists, send directly
        if bot_token and chat_id:
            print(f"\n📤 Sending to Telegram...")
            sender = TelegramSender(bot_token, chat_id)
            image_urls = [img['url'] for img in info.images]
            await sender.send_photos_batch(image_urls, info.title)
        else:
            print(f"\n💡 To send directly to Telegram, set env vars:")
            print(f"   export TELEGRAM_BOT_TOKEN='your_bot_token'")
            print(f"   export TELEGRAM_CHAT_ID='your_chat_id'")
            print(f"\n📋 Or use the URLs from {txt_file} with wget:")
            print(f"   wget -i {txt_file} -P ./downloads/")

        # Print summary
        print(f"\n{'='*50}")
        print(f"SCRAPING COMPLETE")
        print(f"{'='*50}")
        print(f"Album: {info.title}")
        print(f"Images found: {len(info.images)}")
        print(f"Metadata: {json_file}")
        print(f"URLs list: {txt_file}")
        print(f"\n⚠️ No files saved to disk - only URLs extracted!")


if __name__ == "__main__":
    asyncio.run(main())
