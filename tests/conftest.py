from __future__ import annotations

import pytest

from app.config import Settings


@pytest.fixture(scope="session")
def fast_settings(tmp_path_factory) -> Settings:
    """测试用配置：限额更紧，跑得快，且把数据目录挪到 tmp_path，不污染 data/。"""
    root = tmp_path_factory.mktemp("plw-data")
    return Settings(
        data_dir=root,
        exec_timeout=3.0,
        cpu_timeout=2,
        max_mem_mb=256,
        max_output_bytes=5_000,
        max_fsize_mb=4,
    )
