#!/usr/bin/env python3
"""
多进程并行离线提取（N 个进程同时跑视频不同片段）。
用法: python extract_parallel.py <视频文件> [--workers 4] [--scale 0.35]
"""
import sys, os, time, argparse, subprocess, json, tempfile, shutil
from multiprocessing import Pool, cpu_count
from pathlib import Path

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)
import opencv_env  # noqa: F401  —— 必须先于 cv2
from video_source import normalize_video_path

EXTRACT_PY = os.path.join(SCRIPT_DIR, "extract.py")
PYTHON = sys.executable


def get_video_duration(video_path):
    """用 ffprobe 获取视频时长"""
    # 找 ffprobe
    for ffprobe in ["ffprobe", "ffprobe.exe"]:
        try:
            subprocess.check_call([ffprobe, "-version"],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            break
        except Exception:
            continue
    else:
        # 尝试 winget 路径
        base = Path(os.environ["LOCALAPPDATA"]) / "Microsoft/WinGet/Packages"
        for d in base.glob("Gyan.FFmpeg.Shared_*"):
            for exe in d.glob("ffmpeg-*/bin/ffprobe.exe"):
                ffprobe = str(exe)
                break
    try:
        out = subprocess.check_output(
            [ffprobe, "-v", "quiet", "-show_entries", "format=duration",
             "-of", "csv=p=0", video_path],
            text=True, stderr=subprocess.DEVNULL)
        return float(out.strip())
    except Exception:
        cap = __import__('cv2').VideoCapture(video_path)
        dur = cap.get(__import__('cv2').CAP_PROP_FRAME_COUNT) / cap.get(__import__('cv2').CAP_PROP_FPS)
        cap.release()
        return dur


def process_segment(args):
    """单个进程：处理视频的一个时间段"""
    video_path, start_sec, duration_sec, scale, seg_id, tmpdir, use_gpu = args
    out_file = os.path.join(tmpdir, f"seg_{seg_id}.csv")
    progress_file = os.path.join(tmpdir, f"seg_{seg_id}.progress")

    cmd = [
        PYTHON, EXTRACT_PY,
        video_path,
        "--output", out_file,
        "--scale", str(scale),
        "--seek-start", str(start_sec),
        "--max-duration", str(duration_sec),
        "--progress-file", progress_file,
    ]
    if not use_gpu:
        cmd.append("--no-gpu")

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    # 每个子进程单线程 OpenCV，避免 N 进程 × M 线程在少数核心上超订
    env["ZSJC_CV_THREADS"] = "1"

    t0 = time.perf_counter()
    log_file = os.path.join(tmpdir, f"seg_{seg_id}.log")
    try:
        with open(log_file, "w", encoding="utf-8") as lf:
            subprocess.run(cmd, check=True, env=env,
                           stdout=lf, stderr=subprocess.STDOUT,
                           timeout=duration_sec * 3 + 120)
    except subprocess.TimeoutExpired:
        pass
    except subprocess.CalledProcessError as e:
        with open(log_file, "a") as lf:
            lf.write(f"\nEXIT CODE: {e.returncode}\n")
    elapsed = time.perf_counter() - t0

    return {"seg_id": seg_id, "file": out_file, "elapsed": elapsed, "progress_file": progress_file}


def main():
    parser = argparse.ArgumentParser(description="多进程并行离线提取")
    parser.add_argument("video", help="视频文件路径")
    parser.add_argument("--workers", "-w", type=int, default=min(cpu_count(), 8),
                        help=f"并行进程数 (默认 CPU 核数, 最多 8)")
    parser.add_argument("--scale", "-s", type=float, default=0.5, help="缩放比例")
    parser.add_argument("--output", "-o", default="cel_result.csv", help="输出 CSV")
    parser.add_argument("--gpu", action="store_true",
                        help="子进程使用 GPU 光流（多进程会争抢同一张卡，默认关闭）")
    parser.add_argument("--overlap", type=float, default=0.0,
                        help="片段重叠秒数（重叠会重复计数，默认 0）")
    args = parser.parse_args()

    # 不支持 OpenCV 模式的视频（FFmpegNvDecSource 才支持 seek）
    video_path = normalize_video_path(args.video)
    duration = get_video_duration(video_path)
    if duration <= 0:
        print("ERROR: 无法获取视频时长")
        sys.exit(1)

    workers = args.workers
    overlap = args.overlap
    seg_dur = duration / workers + overlap
    print(f"视频时长: {duration:.1f}s, {workers} 进程并行, 每段 ~{seg_dur:.0f}s, "
          f"scale={args.scale}, GPU={'on' if args.gpu else 'off'}")
    print("=" * 60)

    # 创建临时目录
    tmpdir = tempfile.mkdtemp(prefix="cel_parallel_")

    # 构建任务
    tasks = []
    for i in range(workers):
        start = max(0, i * (duration / workers) - (overlap if i > 0 else 0))
        tasks.append((video_path, start, seg_dur, args.scale, i, tmpdir, args.gpu))

    t_start = time.perf_counter()

    # 并行执行 + 进度轮询
    pool = Pool(processes=workers)
    async_result = pool.map_async(process_segment, tasks)
    pool.close()

    # 进度条
    bar_width = 30
    while not async_result.ready():
        # 读取各段进度
        seg_progress = []
        for i in range(workers):
            pf = os.path.join(tmpdir, f"seg_{i}.progress")
            try:
                with open(pf, "r") as f:
                    lines = f.read().strip().split("\n")
                    pct = float(lines[0]) if lines else 0
                    fps = lines[1] if len(lines) > 1 else "?"
                    cels = lines[2] if len(lines) > 2 else "?"
            except Exception:
                pct = 0; fps = "?"; cels = "?"
            seg_progress.append((pct, fps, cels))

        # 绘制进度条 (ASCII-safe)
        line = "\r  "
        for i, (pct, fps, cels) in enumerate(seg_progress):
            filled = int(bar_width * pct / 100)
            bar = "#" * filled + "-" * (bar_width - filled)
            line += f"[{bar}] {pct:5.1f}%  "
        elapsed = time.perf_counter() - t_start
        line += f"| {elapsed:.0f}s"
        try:
            print(line, end="", flush=True)
        except UnicodeEncodeError:
            print(line.encode('ascii', 'replace').decode(), end="", flush=True)
        time.sleep(0.3)

    results = async_result.get()
    print()  # 换行

    total_elapsed = time.perf_counter() - t_start
    max_worker_time = max(r["elapsed"] for r in results)

    # 合并结果：各段独立统计，总张数 = 各段张数之和
    # （旧实现取 max，等于只算了最后一个非空段的张数，结果必然偏小）
    total_filtered = 0
    all_csv_content = []
    seg_stats = []

    for r in sorted(results, key=lambda x: x["seg_id"]):
        seg_file = r["file"]
        if not os.path.isfile(seg_file):
            seg_stats.append((r["seg_id"], 0, 0))
            continue
        with open(seg_file, "r", encoding="utf-8-sig") as f:
            lines = [l.strip() for l in f if l.strip()]
        # 跳过表头
        data_lines = [l for l in lines if l and l[0].isdigit()]
        seg_cels = 0
        if data_lines:
            last = data_lines[-1].split(",")
            if len(last) >= 4:
                seg_cels = int(last[3])
        total_filtered += seg_cels
        seg_stats.append((r["seg_id"], seg_cels, len(data_lines)))
        all_csv_content.extend(data_lines)

    # 按时间排序并去重（重叠段会产生相同 time_sec 的行）
    seen = set()
    merged_lines = []
    for line in sorted(all_csv_content, key=lambda x: float(x.split(",")[0])):
        t = line.split(",")[0]
        if t in seen:
            continue
        seen.add(t)
        merged_lines.append(line)

    # 写入合并结果
    with open(args.output, "w", encoding="utf-8-sig", newline="") as f:
        f.write("time_sec,cels_per_sec,raw_per_sec,total_cels,move_type\n")
        f.write("\n".join(merged_lines) + "\n")

    # 清理
    shutil.rmtree(tmpdir, ignore_errors=True)

    print("=" * 60)
    print("各段张数: " + "  ".join(f"#{sid}:{cels}" for sid, cels, _ in seg_stats))
    print(f"总耗时: {total_elapsed:.1f}s (并行, {workers} 进程)")
    print(f"单段最长: {max_worker_time:.1f}s")
    speedup = duration / max_worker_time if max_worker_time > 0 else 0
    print(f"加速比: {speedup:.1f}x (vs 单进程)")
    print(f"过滤后总张数: {total_filtered}")
    print(f"结果: {args.output}")


if __name__ == "__main__":
    main()
