#!/usr/bin/env python3
"""
PixiBB Image Scraper
Scrapes album images from PixiBB (pixibb.com)
Note: Videos require premium authentication - this scraper handles images only
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
    videos: List[Dict] = None


class PixiBBScraper:
    BASE_URL = "https://sexy.pixibb.com"
    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
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

    def _extract_slug(self, url: str) -> str:
        """Extract album slug from URL"""
        parsed = urlparse(url)
        path = parsed.path.strip('/')
        # Remove /videos/ suffix if present
        path = path.replace('/videos/', '/')
        return path.rstrip('/')

    def _parse_album_info(self, html: str, url: str) -> tuple[AlbumInfo, str, str]:
        """Extract album metadata from HTML, returns (info, post_id, nonce)"""
        # Extract title from breadcrumb or Yoast JSON
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

        # Extract image count
        img_match = re.search(r'(\d+)\s*photos?', html, re.IGNORECASE)
        image_count = int(img_match.group(1)) if img_match else 0

        # Extract video count
        vid_match = re.search(r'(\d+)\s*videos?', html, re.IGNORECASE)
        video_count = int(vid_match.group(1)) if vid_match else 0

        # Extract galleryModeData for API access
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
            images=[],
            videos=[]
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
                else:
                    print(f"API error: HTTP {response.status}")
        except Exception as e:
            print(f"API request failed: {e}")

        return []

    async def scrape_album(self, album_url: str) -> AlbumInfo:
        """Scrape complete album"""
        # Normalize URL - remove /videos/ suffix if present
        album_url = album_url.replace('/videos/', '/')

        print(f"🔍 Fetching album page...")

        # Fetch album page
        html = await self.fetch_page(album_url)
        if not html:
            raise Exception(f"Failed to fetch album: {album_url}")

        # Parse album info
        info, post_id, nonce = self._parse_album_info(html, album_url)

        print(f"📁 Album: {info.title}")
        print(f"📸 Images: {info.image_count}, 🎬 Videos: {info.video_count}")

        if post_id and nonce:
            print(f"🔑 Post ID: {post_id}")
            print(f"📡 Fetching images from API...")

            # Fetch all images from API
            image_urls = await self.fetch_api_images(post_id, nonce)

            # Convert to image dict format
            images = []
            for url in image_urls:
                filename = url.split('/')[-1].split('?')[0]
                images.append({
                    'url': url,
                    'filename': filename,
                    'type': 'full'
                })

            info.images = images
            print(f"✅ Retrieved {len(info.images)} images from API")
        else:
            print("⚠️ Could not extract API credentials, falling back to HTML parsing...")
            # Fallback: extract from HTML (limited)
            info.images = self._extract_images_from_html(html)
            print(f"⚠️ HTML fallback: found {len(info.images)} images")

        # Note about videos
        if info.video_count > 0:
            print(f"\n⚠️ VIDEO NOTICE:")
            print(f"   This album has {info.video_count} video(s)")
            print(f"   Videos require premium authentication")
            print(f"   API endpoint: POST /wp-json/pixibb/v1/video-direct")
            print(f"   Requires: WordPress login cookie")

        return info

    def _extract_images_from_html(self, html: str) -> List[Dict]:
        """Fallback: Extract image URLs from album page HTML"""
        images = []
        seen_urls = set()

        # Pattern: wpmedia URLs
        wpmedia_pattern = r'https://wpmedia\d+\.pixibb\.com/[^"\'\s<>]+\.(?:jpg|jpeg|png|gif|webp)'
        wpmedia_urls = re.findall(wpmedia_pattern, html, re.IGNORECASE)

        for url in wpmedia_urls:
            if url not in seen_urls:
                seen_urls.add(url)
                filename = url.split('/')[-1].split('?')[0]
                images.append({
                    'url': url,
                    'filename': filename,
                    'type': 'full'
                })

        return images

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

    def export_to_json(self, info: AlbumInfo, output_path: str):
        """Export album info to JSON"""
        data = {
            'title': info.title,
            'url': info.url,
            'post_id': info.post_id,
            'image_count': info.image_count,
            'video_count': info.video_count,
            'video_note': 'Videos require premium authentication - not accessible via scraper',
            'images_scraped': len(info.images),
            'images': info.images
        }

        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        print(f"💾 Exported to: {output_path}")

    async def download_images(self, info: AlbumInfo, output_dir: str, max_concurrent: int = 5):
        """Download all images to a directory"""
        if not info.images:
            print("No images to download")
            return

        # Create output directory
        safe_title = re.sub(r'[^\w\-_.]', '_', info.title)[:50]
        download_dir = os.path.join(output_dir, safe_title)
        os.makedirs(download_dir, exist_ok=True)

        print(f"\n📥 Downloading {len(info.images)} images to: {download_dir}")

        semaphore = asyncio.Semaphore(max_concurrent)

        async def download_one(img: Dict, index: int):
            async with semaphore:
                url = img['url']
                filename = img['filename']
                filepath = os.path.join(download_dir, filename)

                # Skip if already exists
                if os.path.exists(filepath):
                    print(f"  ✓ [{index+1}/{len(info.images)}] {filename} (exists)")
                    return True

                try:
                    async with self.session.get(url, timeout=60) as response:
                        if response.status == 200:
                            content = await response.read()
                            with open(filepath, 'wb') as f:
                                f.write(content)
                            print(f"  ✓ [{index+1}/{len(info.images)}] {filename}")
                            return True
                        else:
                            print(f"  ✗ [{index+1}/{len(info.images)}] {filename} (HTTP {response.status})")
                            return False
                except Exception as e:
                    print(f"  ✗ [{index+1}/{len(info.images)}] {filename} ({e})")
                    return False

        # Download all images
        tasks = [download_one(img, i) for i, img in enumerate(info.images)]
        results = await asyncio.gather(*tasks)

        success_count = sum(1 for r in results if r)
        print(f"\n✅ Downloaded {success_count}/{len(info.images)} images")


async def main():
    import sys

    # Get album URL from command line or use default
    if len(sys.argv) > 1:
        album_url = sys.argv[1]
    else:
        album_url = "https://sexy.pixibb.com/81-2023-09-25-265-photos-12-videos-sexy-girl/"

    async with PixiBBScraper() as scraper:
        info = await scraper.scrape_album(album_url)

        # Export to JSON
        safe_title = re.sub(r'[^\w\-_.]', '_', info.title)[:50]
        json_file = f"{safe_title}.json"
        scraper.export_to_json(info, json_file)

        # Ask to download images
        print(f"\nDownload images? (y/n): ", end="")
        # Auto-answer yes for non-interactive
        answer = "y"  # input().strip().lower()

        if answer in ('y', 'yes'):
            await scraper.download_images(info, "./downloads")

        # Print summary
        print(f"\n{'='*50}")
        print(f"SCRAPING COMPLETE")
        print(f"{'='*50}")
        print(f"Album: {info.title}")
        print(f"Post ID: {info.post_id}")
        print(f"Expected images: {info.image_count}")
        print(f"Scraped images: {len(info.images)}")
        print(f"Videos: {info.video_count} (premium only)")
        print(f"\nVideo API info:")
        print(f"  Endpoint: POST /wp-json/pixibb/v1/video-direct")
        print(f"  Body: {{\"video_id\": \"<id>\"}}")
        print(f"  Requires: WordPress auth cookie + nonce")


if __name__ == "__main__":
    asyncio.run(main())
