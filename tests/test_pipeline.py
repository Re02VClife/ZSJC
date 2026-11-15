# -*- coding: utf-8 -*-
"""
检测管线回归测试（不需要 GUI）。

覆盖：
1. GPU 能力自检（不可用时自动跳过 GPU 用例）
2. GPU 角点 + 光流位移估计精度
3. GPU / CPU 运动分析一致性
4. GPU 差分掩膜与 CPU 逐像素一致
5. 完整检测的运镜过滤（纯平移应判为「全局平移」）
6. 端到端序列：CPU 与 GPU 判定结果一致
7. 视频帧源（OpenCV 后端）读取 / seek / 解码后端标注
8. CPU vs GPU 性能实测（仅打印，硬门槛宽松）

运行：
    .venv\\Scripts\\python.exe tests\\test_pipeline.py
或：
    pytest tests/test_pipeline.py -v
"""
import os
import sys
import time
import tempfile
from collections import deque

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import opencv_env  # noqa: F401,E402 —— 必须先于 cv2
import numpy as np  # noqa: E402
import cv2  # noqa: E402

import detection as D  # noqa: E402
import gpu_backend as G  # noqa: E402
from config import CONFIG  # noqa: E402
from video_source import FrameSource  # noqa: E402


# ======================== 测试素材 ========================

def synth_texture(h, w, seed=0):
    """平滑随机纹理：保证有足够角点，接近真实动画背景。"""
    rng = np.random.default_rng(seed)
    noise = rng.integers(0, 255, (h, w), dtype=np.uint8)
    base = cv2.GaussianBlur(noise, (0, 0), 2.5)
    # 叠加一些硬边（角色轮廓的替身），让角点更稳定
    cv2.rectangle(base, (w // 5, h // 4), (w // 2, h // 2), 200, 2)
    cv2.circle(base, (w * 3 // 4, h * 2 // 3), min(h, w) // 8, 60, 2)
    return base


def shift_img(img, dx, dy):
    M = np.float32([[1, 0, dx], [0, 1, dy]])
    return cv2.warpAffine(img, M, (img.shape[1], img.shape[0]),
                          borderMode=cv2.BORDER_REPLICATE)


def make_sequence(n=24, h=540, w=960, seed=1):
    """合成一段"动画"：背景 + 随机局部改动 + 偶尔整帧平移。"""
    base = synth_texture(h, w, seed)
    frames = []
    for i in range(n):
        f = base.copy()
        # 每 3 帧换一块"作画"区域
        rng = np.random.default_rng(seed + i // 3)
        x = int(rng.integers(0, w - 200))
        y = int(rng.integers(0, h - 120))
        f[y:y + 100, x:x + 160] = int(rng.integers(0, 255))
        # 每 8 帧整帧平移一次（模拟运镜）
        if (i // 8) % 2 == 1:
            f = shift_img(f, 6, -4)
        frames.append(f)
    return frames


# ======================== GPU 相关 ========================

def _require_flow():
    info = G.probe()
    if not info.flow_ready:
        print(f"  [skip] GPU 光流不可用: {info}")
        return None
    return info


def test_gpu_probe():
    info = G.probe()
    print(f"  GPUInfo: {info}")
    if not info.available:
        print("  [skip] 无 CUDA 设备")
        return
    assert info.diff, f"GPU 差分不可用: {info.errors}"
    assert info.morph, f"GPU 形态学不可用: {info.errors}"
    assert info.corners, f"GPU 角点不可用: {info.errors}"
    assert info.flow, f"GPU 光流不可用: {info.errors}"
    assert info.warp, f"GPU warpAffine 不可用: {info.errors}"


def test_gpu_flow_shift_accuracy():
    info = _require_flow()
    if info is None:
        return
    a = synth_texture(360, 640, seed=3)
    b = shift_img(a, 5, -3)
    ctx = G.GPUContext(info)
    ctx.upload_pair(a, b)
    p0 = ctx.corners("prev")
    assert p0 is not None and len(p0) >= 20, "GPU 角点过少"
    p1, status = ctx.track(p0, target="curr")
    ok = status == 1
    assert int(ok.sum()) >= 20, f"有效光流点不足: {int(ok.sum())}"
    dx = float(np.median(p1[ok, 0] - p0[ok, 0]))
    dy = float(np.median(p1[ok, 1] - p0[ok, 1]))
    print(f"  真值 (5,-3) / GPU 估计 ({dx:.2f},{dy:.2f})，有效点 {int(ok.sum())}")
    assert abs(dx - 5) <= 1.0, f"dx 偏差过大: {dx}"
    assert abs(dy + 3) <= 1.0, f"dy 偏差过大: {dy}"


def test_motion_cpu_gpu_consistent():
    info = _require_flow()
    if info is None:
        return
    a = synth_texture(420, 760, seed=4)
    b = shift_img(a, 5, -4)
    ctx = G.GPUContext(info)

    cpu = D.unified_motion_analysis(a, b, True, use_gpu=False)
    ctx.upload_pair(a, b)
    gpu = D.unified_motion_analysis(a, b, True, use_gpu=True, gpu_ctx=ctx,
                                    frames_uploaded=True)
    print(f"  CPU -> shift={cpu[0]} dx={cpu[1]:.2f} dy={cpu[2]:.2f} "
          f"layer={cpu[4]} zoom={cpu[5]}")
    print(f"  GPU -> shift={gpu[0]} dx={gpu[1]:.2f} dy={gpu[2]:.2f} "
          f"layer={gpu[4]} zoom={gpu[5]}")

    assert cpu[0] is True and gpu[0] is True, "应检测到全局平移"
    assert abs(cpu[1] - gpu[1]) <= 1.0, "dx 不一致"
    assert abs(cpu[2] - gpu[2]) <= 1.0, "dy 不一致"
    assert cpu[4] == gpu[4] and cpu[5] == gpu[5], "图层/缩放判定不一致"


def test_gpu_diff_mask_matches_cpu():
    info = G.probe()
    if not info.diff_ready:
        print("  [skip] GPU 差分不可用")
        return
    a = synth_texture(240, 400, seed=5)
    b = a.copy()
    b[60:150, 100:260] = 230
    ctx = G.GPUContext(info)
    ctx.upload_pair(a, b)
    thr = D.adaptive_threshold(b)
    ratio_gpu, mask_gpu = ctx.diff_mask(thr)
    _d, mask_cpu, ratio_cpu = D.compute_diff_mask(a, b, thr)
    print(f"  ratio gpu={ratio_gpu:.6f} cpu={ratio_cpu:.6f}")
    assert abs(ratio_gpu - ratio_cpu) < 1e-9
    assert np.array_equal(mask_gpu.download(), mask_cpu), "掩膜不一致"


# ======================== 检测逻辑 ========================

def test_static_frame_is_not_new_cel():
    a = synth_texture(324, 576, seed=6)
    detected, reason = D.basic_is_new_cel(a, a.copy())
    assert detected is False and reason == "静止"


def test_large_change_is_new_cel():
    a = synth_texture(324, 576, seed=7)
    b = a.copy()
    b[40:280, 40:520] = 180
    detected, reason = D.basic_is_new_cel(a, b)
    print(f"  {detected}, {reason}")
    assert detected is True


def test_global_translation_filtered():
    a = synth_texture(540, 960, seed=8)
    b = shift_img(a, 7, -5)
    detected, reason = D.full_is_new_cel(a, b, True, use_gpu=False)
    print(f"  full_is_new_cel -> {detected}, {reason}")
    assert detected is False, f"纯平移不应算新作画: {reason}"
    assert reason == "全局平移", f"过滤原因应为全局平移，实际: {reason}"


def test_full_detection_cpu_gpu_match():
    info = _require_flow()
    if info is None:
        return
    frames = make_sequence(n=18, h=540, w=960, seed=9)
    ctx = G.GPUContext(info)
    mismatches = []
    for idx, (prev, curr) in enumerate(zip(frames, frames[1:])):
        cpu = D.full_is_new_cel(prev, curr, True, use_gpu=False)
        ctx.upload_pair(prev, curr)
        gpu = D.full_is_new_cel(prev, curr, True, use_gpu=True,
                                gpu_ctx=ctx, frames_uploaded=True)
        if cpu[0] != gpu[0]:
            mismatches.append((idx, cpu, gpu))
    print(f"  18 对帧，判定不一致 {len(mismatches)} 处")
    for m in mismatches[:5]:
        print("   ", m)
    assert len(mismatches) <= 1, f"CPU/GPU 判定差异过大: {mismatches}"


def test_pipeline_smoke_realtime_shape():
    """实时检测尺寸（324x576，低于 GPU 门槛）下完整跑一遍，确认不会崩。"""
    frames = make_sequence(n=12, h=324, w=576, seed=10)
    last = None
    total = 0
    for f in frames:
        if last is None:
            last = f
            continue
        _d, _m, ratio = D.compute_diff_mask(last, f)
        basic, _reason = D.basic_is_new_cel(last, f, ratio)
        if basic:
            det, _ = D.full_is_new_cel(last, f, True, use_gpu=False,
                                       precomputed_ratio=ratio)
            total += int(det)
        last = f
    print(f"  324x576 合成序列新张数: {total}")
    assert total > 0


# ======================== 视频帧源 ========================

def test_frame_source_video_and_seek():
    tmpdir = tempfile.mkdtemp(prefix="zsjc_test_")
    path = os.path.join(tmpdir, "sample.mp4")
    h, w = 360, 640
    writer = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"mp4v"), 24.0, (w, h))
    assert writer.isOpened(), "无法创建测试视频"
    for i in range(48):
        frame = np.full((h, w, 3), 40, np.uint8)
        cv2.rectangle(frame, (10 + i * 8, 60), (120 + i * 8, 260), (200, 120, 30), -1)
        writer.write(frame)
    writer.release()

    src = FrameSource(mode="video", video_path=path, scale_factor=0.5,
                      use_gpu=False, hw_accel=True, hash_enabled=True)
    try:
        print(f"  decode_backend={src.decode_backend} shape={src.frame_shape}")
        assert src.video_fps > 0
        assert src.frame_shape is not None
        got = 0
        deadline = time.time() + 5
        while got < 10 and time.time() < deadline:
            item = src.next_frame()
            if item[0] is None:
                time.sleep(0.005)
                continue
            ts, small, _bgr, h256, oph = item
            assert small.shape == src.frame_shape
            assert h256 is not None and h256.shape == (256,)
            assert isinstance(oph, int)
            got += 1
        assert got >= 10, f"只读到 {got} 帧"

        src.seek(1.0)
        item = None
        deadline = time.time() + 3
        while time.time() < deadline:
            item = src.next_frame()
            if item[0] is not None:
                break
            time.sleep(0.01)
        assert item is not None and item[0] is not None, "seek 后读不到帧"
        print(f"  seek 1.0s 后首帧 ts={item[0]:.2f}")
        assert item[0] >= 0.5
    finally:
        src.close()
        try:
            os.remove(path)
            os.rmdir(tmpdir)
        except OSError:
            pass


# ======================== 性能 ========================

def test_perf_cpu_vs_gpu_flow():
    info = _require_flow()
    if info is None:
        return
    frames = make_sequence(n=16, h=540, w=960, seed=11)
    ctx = G.GPUContext(info)

    t0 = time.perf_counter()
    for prev, curr in zip(frames, frames[1:]):
        D.unified_motion_analysis(prev, curr, True, use_gpu=False)
    cpu_ms = (time.perf_counter() - t0) / 15 * 1000

    t0 = time.perf_counter()
    for prev, curr in zip(frames, frames[1:]):
        ctx.upload_pair(prev, curr)
        D.unified_motion_analysis(prev, curr, True, use_gpu=True,
                                  gpu_ctx=ctx, frames_uploaded=True)
    gpu_ms = (time.perf_counter() - t0) / 15 * 1000
    print(f"  540x960 运动分析: CPU {cpu_ms:.2f} ms / GPU {gpu_ms:.2f} ms "
          f"(含 upload)")
    # 只做宽松门槛：GPU 不应比 CPU 慢太多，否则说明门槛/实现有问题
    assert gpu_ms < cpu_ms * 1.5, f"GPU 光流异常慢: {gpu_ms:.2f} vs {cpu_ms:.2f}"


# ======================== 手动运行入口 ========================

def _main():
    tests = [(name, obj) for name, obj in sorted(globals().items())
             if name.startswith("test_") and callable(obj)]
    failed = []
    for name, fn in tests:
        print(f"\n[RUN] {name}")
        t0 = time.perf_counter()
        try:
            fn()
            print(f"[PASS] {name}  ({time.perf_counter()-t0:.2f}s)")
        except Exception as exc:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            failed.append((name, exc))
            print(f"[FAIL] {name}: {exc}")
    print("\n" + "=" * 60)
    print(f"通过 {len(tests) - len(failed)}/{len(tests)}")
    if failed:
        for name, exc in failed:
            print(f"  失败: {name}: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(_main())
