"""Test doubles for the Xiaozhi adapter, usable without ESP32 hardware."""

from app.xiaozhi_adapter.testing.fake_device import (
    FakeTool,
    FakeXiaozhiDevice,
    LoopbackTransport,
)

__all__ = ["FakeTool", "FakeXiaozhiDevice", "LoopbackTransport"]
