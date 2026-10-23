from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

from novel_ref.errors import CaptchaError, CircuitOpenError, RobotsDisallowError

logger = logging.getLogger(__name__)

CAPTCHA_MARKERS = ("验证码", "captcha", "人机验证", "access denied", "请完成验证")

# 国内常见本地代理端口（Clash / V2 / Qv2ray 等）
_COMMON_PROXIES = (
    "http://127.0.0.1:7890",
    "http://127.0.0.1:7897",
    "http://127.0.0.1:10809",
    "http://127.0.0.1:1080",
    "http://127.0.0.1:8080",
)


def _proxy_candidates(cfg: dict[str, Any]) -> list[str | None]:
    """返回尝试顺序：None=直连，其余为代理 URL。"""
    mode = str(cfg.get("proxy") or os.environ.get("NOVELREF_PROXY") or "auto").strip()
    out: list[str | None] = []
    if mode.lower() in ("", "none", "off", "direct"):
        return [None]
    if mode.lower() == "auto":
        out.append(None)
        # 环境变量代理
        for key in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy", "ALL_PROXY"):
            val = os.environ.get(key)
            if val and val not in out:
                out.append(val)
        for p in _COMMON_PROXIES:
            if p not in out:
                out.append(p)
        return out
    # 显式代理
    return [mode]


class TokenBucket:
    def __init__(self, rate: float, burst: float) -> None:
        self.rate = max(rate, 0.01)
        self.capacity = max(burst, 1.0)
        self.tokens = self.capacity
        self.updated = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        with self._lock:
            while True:
                now = time.monotonic()
                elapsed = now - self.updated
                self.updated = now
                self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
                if self.tokens >= 1.0:
                    self.tokens -= 1.0
                    return
                need = (1.0 - self.tokens) / self.rate
                time.sleep(min(need, 1.0))


class Fetcher:
    def __init__(self, cfg: dict[str, Any]) -> None:
        self.cfg = cfg
        self.timeout = float(cfg.get("timeout", 15))
        self.min_gap = float(cfg.get("min_gap", 2.5))
        self.bucket = TokenBucket(float(cfg.get("rate", 0.25)), float(cfg.get("burst", 3)))
        self.trust_env = bool(cfg.get("trust_env", True))
        if "NOVELREF_TRUST_ENV" in os.environ:
            self.trust_env = os.environ["NOVELREF_TRUST_ENV"].lower() in ("1", "true", "yes")
        self._last_req = 0.0
        self._lock = threading.Lock()
        self._captcha_hits = 0
        self._circuit_until = 0.0
        self._robots: dict[str, tuple[float, RobotFileParser | None]] = {}
        self._working_proxy: str | None | object = object()  # sentinel=未探测
        self._sessions: dict[str, requests.Session] = {}

    def _headers(self) -> dict[str, str]:
        return {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }

    def _session_for(self, proxy: str | None) -> requests.Session:
        key = proxy or "__direct__"
        if key not in self._sessions:
            s = requests.Session()
            s.headers.update(self._headers())
            s.trust_env = self.trust_env if proxy is None else False
            if proxy:
                s.proxies = {"http": proxy, "https": proxy}
            else:
                s.proxies = {}
            self._sessions[key] = s
        return self._sessions[key]

    def _ensure_circuit(self) -> None:
        if time.time() < self._circuit_until:
            raise CircuitOpenError("NovelRef circuit open after captcha")

    def trip_circuit(self) -> None:
        self._captcha_hits += 1
        thr = int(self.cfg.get("circuit_fail_threshold", 2))
        if self._captcha_hits >= thr:
            cool = float(self.cfg.get("circuit_cooldown_sec", 1800))
            self._circuit_until = time.time() + cool

    def _robots_ok(self, url: str, robots_url: str | None, session: requests.Session) -> bool:
        if not robots_url:
            return True
        host = urlparse(robots_url).netloc
        now = time.time()
        cached = self._robots.get(host)
        if cached and now - cached[0] < 86400:
            rp = cached[1]
        else:
            rp = RobotFileParser()
            try:
                resp = session.get(robots_url, timeout=min(self.timeout, 8))
                if resp.status_code == 200:
                    rp.parse(resp.text.splitlines())
                else:
                    rp = None
            except Exception:
                rp = None
            self._robots[host] = (now, rp)
        if rp is None:
            return True
        return bool(rp.can_fetch(self._headers()["User-Agent"], url))

    def _raw_get(self, session: requests.Session, url: str) -> requests.Response:
        with self._lock:
            gap = self.min_gap - (time.monotonic() - self._last_req)
            if gap > 0:
                time.sleep(gap)
            self.bucket.acquire()
            resp = session.get(url, timeout=self.timeout, allow_redirects=True)
            self._last_req = time.monotonic()
        return resp

    def _pick_working_proxy(self, probe_url: str) -> str | None:
        if self._working_proxy is not object():
            return self._working_proxy  # type: ignore[return-value]
        last_err: Exception | None = None
        for proxy in _proxy_candidates(self.cfg):
            session = self._session_for(proxy)
            try:
                resp = session.get(probe_url, timeout=min(self.timeout, 10), allow_redirects=True)
                if resp.status_code < 500:
                    self._working_proxy = proxy
                    logger.info("NovelRef network path: %s", proxy or "direct")
                    return proxy
            except Exception as e:
                last_err = e
                logger.debug("proxy probe fail %s: %s", proxy, e)
                continue
        # 全部失败也缓存 None，避免每次重探；调用方会报错
        self._working_proxy = None
        if last_err:
            logger.warning("all proxy probes failed, last=%s", last_err)
        return None

    def get_soup(self, url: str, robots_url: str | None = None) -> BeautifulSoup:
        self._ensure_circuit()
        probe = robots_url or f"{urlparse(url).scheme}://{urlparse(url).netloc}/"
        proxy = self._pick_working_proxy(probe)
        # 若已有工作路径，优先；失败则轮询其余
        order = [proxy] + [p for p in _proxy_candidates(self.cfg) if p != proxy]
        last_exc: Exception | None = None
        for p in order:
            session = self._session_for(p)
            try:
                if not self._robots_ok(url, robots_url, session):
                    raise RobotsDisallowError(f"robots disallow: {url}")
                resp = self._raw_get(session, url)
                text = resp.text or ""
                low = text.lower()
                if any(m.lower() in low or m in text for m in CAPTCHA_MARKERS):
                    self.trip_circuit()
                    raise CaptchaError(url)
                resp.raise_for_status()
                if not resp.encoding or resp.encoding.lower() == "iso-8859-1":
                    resp.encoding = resp.apparent_encoding or "utf-8"
                    text = resp.text
                self._working_proxy = p
                return BeautifulSoup(text, "lxml")
            except (CaptchaError, RobotsDisallowError, CircuitOpenError):
                raise
            except Exception as e:
                last_exc = e
                logger.debug("get_soup via %s failed: %s", p, e)
                continue
        raise RuntimeError(f"fetch failed for {url}: {last_exc}")
