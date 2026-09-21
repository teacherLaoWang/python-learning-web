"""全局配置：一份配置只在一条链路上被读一次（对应教程第 6 章的 settings 写法）。

改端口/超时不用动代码，两条路都行：
  PLW_PORT=8200 uv run uvicorn app.main:app --port 8200
  或者在项目根放一个 .env：PLW_EXEC_TIMEOUT=15
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PLW_",
        env_file=ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---- 路径 ----
    data_dir: Path = ROOT / "data"
    static_dir: Path = ROOT / "app" / "static"

    # ---- 服务 ----
    host: str = "127.0.0.1"
    port: int = 8100

    # ---- 执行沙箱（本机单用户，不是安全边界，见 README）----
    exec_timeout: float = 8.0  # 墙钟秒；超了就 killpg
    cpu_timeout: int = 5  # RLIMIT_CPU，管死循环
    max_mem_mb: int = 1024  # RLIMIT_AS
    max_output_bytes: int = 200_000  # stdout/stderr 各自上限，超出截断
    max_fsize_mb: int = 32  # 允许临时目录里写多大的文件

    @property
    def seed_path(self) -> Path:
        return self.data_dir / "seed.json"

    @property
    def notes_dir(self) -> Path:
        return self.data_dir / "notes"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "app.db"

    @property
    def db_url(self) -> str:
        return f"sqlite:///{self.db_path}"


@lru_cache
def get_settings() -> Settings:
    """lru_cache 让它变成单例：每次 Depends(get_settings) 拿到同一个对象。"""
    return Settings()
