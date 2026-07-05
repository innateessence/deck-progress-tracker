"""
HowLongToBeat Service
Fetches game completion times from HowLongToBeat using standard library only
"""

import asyncio
import json
import ssl
import time
import urllib.request
import urllib.error
import gzip
from typing import Optional, Dict, Any, List
from difflib import SequenceMatcher

# Create SSL context that doesn't verify certificates (Steam Deck may have cert issues)
SSL_CONTEXT = ssl.create_default_context()
SSL_CONTEXT.check_hostname = False
SSL_CONTEXT.verify_mode = ssl.CERT_NONE

# Use Decky's built-in logger
import decky
logger = decky.logger


class HLTBService:
    def __init__(self):
        self.min_similarity = 0.7  # Minimum similarity threshold
        self.base_url = "https://howlongtobeat.com"
        # Use Firefox User-Agent to match the working example
        self.user_agent = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:152.0) Gecko/20100101 Firefox/152.0"
        
        # Dynamic auth values (fetched from API on first use)
        self.auth_token = None
        self.hp_key = None
        self.hp_val = None
        self.token_timestamp = 0

    def _get_auth_data_sync(self) -> Optional[Dict[str, str]]:
        """Get dynamic auth data from HLTB init endpoint.
        
        HLTB uses token-based authentication - no cookies required."""
        try:
            timestamp = int(time.time() * 1000)
            init_url = f"{self.base_url}/api/bleed/init?t={timestamp}"

            headers = {
                "User-Agent": self.user_agent,
                "Accept": "*/*",
                "Accept-Language": "en-US,en;q=0.9",
                "Prefer": "safe",
                "Referer": f"{self.base_url}/",
                # HLTB uses token auth, no cookie needed
                "DNT": "1",
                "Sec-GPC": "1",
                "Connection": "keep-alive",
                "Sec-Fetch-Dest": "empty",
                "Sec-Fetch-Mode": "cors",
                "Sec-Fetch-Site": "same-origin",
                "Pragma": "no-cache",
            }

            req = urllib.request.Request(init_url, headers=headers)

            with urllib.request.urlopen(req, timeout=10, context=SSL_CONTEXT) as response:
                result = json.loads(response.read().decode('utf-8'))
                
                auth_token = result.get('token')
                hp_key = result.get('hpKey')
                hp_val = result.get('hpVal')
                
                if auth_token and hp_key and hp_val:
                    logger.info(f"Got fresh HLTB auth data (key: {hp_key})")
                    return {
                        "token": auth_token,
                        "hp_key": hp_key,
                        "hp_val": hp_val
                    }

        except Exception as e:
            logger.error(f"Failed to get HLTB auth data: {e}")

        return None

    def _calculate_similarity(self, str1: str, str2: str) -> float:
        """Calculate string similarity using SequenceMatcher"""
        return SequenceMatcher(None, str1.lower(), str2.lower()).ratio()

    def _sanitize_game_name(self, game_name: str) -> str:
        """Sanitize game name for better HLTB search matching.
        Removes special characters that can interfere with search."""
        import re
        # Remove common suffixes that don't help with matching
        suffixes_to_remove = [
            r'\s*-\s*Steam Special Edition',
            r'\s*-\s*Special Edition',
            r'\s*-\s*Enhanced Edition',
            r'\s*-\s*Game of the Year',
            r'\s*-\s*GOTY',
            r'\s*-\s*Anniversary Edition',
            r'\s*-\s*Definitive Edition',
            r'\s*\([\d]{4}\)',  # Year in parentheses like (2008)
        ]
        result = game_name
        for suffix in suffixes_to_remove:
            result = re.sub(suffix, '', result, flags=re.IGNORECASE)

        # Replace hyphens and colons with spaces
        result = re.sub(r'[-:]+', ' ', result)
        # Remove other special characters but keep alphanumeric and spaces
        result = re.sub(r'[^\w\s]', '', result)
        # Collapse multiple spaces
        result = re.sub(r'\s+', ' ', result).strip()

        return result

    def _search_sync(self, game_name: str) -> Optional[Dict[str, Any]]:
        """Synchronous HLTB search using /api/bleed endpoint"""
        try:
            # Get fresh auth data if not available or expired (refresh every 10 minutes)
            current_time = time.time()
            is_auth_expired = (current_time - self.token_timestamp) > 600
            if not self.auth_token or is_auth_expired:
                logger.info("Fetching fresh HLTB auth data...")
                auth_data = self._get_auth_data_sync()
                if auth_data:
                    self.auth_token = auth_data["token"]
                    self.hp_key = auth_data["hp_key"]
                    self.hp_val = auth_data["hp_val"]
                    self.token_timestamp = current_time
                else:
                    logger.error("Failed to get HLTB auth data")
                    return None

            # Build headers with dynamic auth values (no cookie needed)
            headers = {
                "User-Agent": self.user_agent,
                "Accept": "*/*",
                "Accept-Language": "en-US,en;q=0.9",
                "Prefer": "safe",
                "Accept-Encoding": "gzip, deflate",
                "Referer": f"{self.base_url}/",
                "Content-Type": "application/json",
                "x-auth-token": self.auth_token,
                "x-hp-key": self.hp_key,
                "x-hp-val": self.hp_val,
                # HLTB uses token auth, no cookie required
                "DNT": "1",
                "Sec-GPC": "1",
                "Connection": "keep-alive",
                "Sec-Fetch-Dest": "empty",
                "Sec-Fetch-Mode": "cors",
                "Sec-Fetch-Site": "same-origin",
                "Priority": "u=4",
                "Pragma": "no-cache",
                "Cache-Control": "no-cache",
            }

            # Sanitize game name for better search matching
            sanitized_name = self._sanitize_game_name(game_name)

            # HLTB API payload
            payload = {
                "searchType": "games",
                "searchTerms": sanitized_name.split(),
                "searchPage": 1,
                "size": 20,
                "searchOptions": {
                    "games": {
                        "userId": 0,
                        "platform": "",
                        "sortCategory": "popular",
                        "rangeCategory": "main",
                        "rangeTime": {"min": None, "max": None},
                        "gameplay": {"perspective": "", "flow": "", "genre": "", "difficulty": ""},
                        "rangeYear": {"min": "", "max": ""},
                        "modifier": ""
                    },
                    "users": {"sortCategory": "postcount"},
                    "lists": {"sortCategory": "follows"},
                    "filter": "",
                    "sort": 0,
                    "randomizer": 0
                },
                "useCache": True,
                self.hp_key: self.hp_val
            }

            data = json.dumps(payload).encode('utf-8')
            url = f"{self.base_url}/api/bleed"

            req = urllib.request.Request(url, data=data, headers=headers, method='POST')

            with urllib.request.urlopen(req, timeout=15, context=SSL_CONTEXT) as response:
                raw_data = response.read()
                
                # Handle compression based on Content-Encoding header
                content_encoding = response.headers.get('Content-Encoding', '').lower()
                if 'gzip' in content_encoding:
                    decoded_data = gzip.decompress(raw_data)
                else:
                    decoded_data = raw_data
                
                result = json.loads(decoded_data.decode('utf-8'))

            games = result.get("data", [])
            if not games:
                return None

            # Find best match by name similarity
            best_match = None
            best_similarity = 0.0

            for game in games:
                game_title = game.get("game_name", "")
                similarity = self._calculate_similarity(game_name, game_title)

                if similarity > best_similarity:
                    best_similarity = similarity
                    best_match = game

            if not best_match or best_similarity < self.min_similarity:
                return None

            # Extract times (convert from seconds to hours)
            def to_hours(seconds):
                if seconds and seconds > 0:
                    return round(seconds / 3600, 1)
                return None

            return {
                "game_name": game_name,
                "matched_name": best_match.get("game_name"),
                "similarity": round(best_similarity, 2),
                "main_story": to_hours(best_match.get("comp_main")),
                "main_extra": to_hours(best_match.get("comp_plus")),
                "completionist": to_hours(best_match.get("comp_100")),
                "all_styles": to_hours(best_match.get("comp_all")),
                "hltb_url": f"https://howlongtobeat.com/game/{best_match.get('game_id')}"
            }

        except urllib.error.HTTPError as e:
            logger.error(f"HLTB HTTP error: {e.code} - {e.reason}")
            if hasattr(e, 'read'):
                try:
                    error_body = e.read().decode('utf-8')
                    logger.error(f"  Response: {error_body[:500]}")
                except:
                    pass
        except Exception as e:
            logger.error(f"HLTB search error: {e}")

    async def search_game(self, game_name: str) -> Optional[Dict[str, Any]]:
        """Search HLTB for game completion times"""
        if not game_name or game_name.startswith("Unknown"):
            return None

        # Skip non-game entries (Proton, Steam Runtime, etc.)
        skip_patterns = [
            "proton", "steam linux runtime", "steamworks",
            "redistributable", "directx", "vcredist"
        ]
        name_lower = game_name.lower()
        for pattern in skip_patterns:
            if pattern in name_lower:
                return None

        try:
            # Run sync request in thread pool
            result = await asyncio.to_thread(self._search_sync, game_name)

            if result:
                logger.info(f"HLTB: {result['matched_name']} (similarity: {result['similarity']:.2f})")

            return result

        except Exception as e:
            logger.error(f"HLTB search failed for {game_name}: {e}")
            return None
