#!/usr/bin/env python3
"""
批量处理 kazumi 缓存 TS 分片。
按番剧分组 → 合并 TS 为 MP4 → 并行提取张数。
用法: python batch_extract.py [--dir 缓存目录] [--scale 0.35] [--workers 4]
"""
import sys, os, time, argparse, subprocess, json, glob, shutil, tempfile
from multiprocessing import Pool, cpu_count
from pathlib import Path
from collections import defaultdict

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PYTHON = sys.executable
EXTRACT_PARALLEL = os.path.join(SCRIPT_DIR, "extract_parallel.py")

KAZUMI_DIR = os.path.join(os.environ["APPDATA"], "com.example", "kazumi", "downloads")


def find_episodes(cache_dir):
    """扫描缓存目录，返回 {番剧名/集号: [ts文件列表]}"""
    episodes = defaultdict(list)
    for ts_file in Path(cache_dir).rglob("seg_*.ts"):
        # 默认 kazumi 路径: downloads/29648_giriGiriLove/1/seg_00000.ts
        # 非默认路径（如 E:\kazhumi Anime\554779_giriGiriLove\1\...）
        # 不能只取 parent.name，否则不同番剧的 "1" 会撞在一起。
        parts = ts_file.parts
        try:
            idx = parts.index("downloads")
            episode = "/".join(parts[idx + 1:-1])  # e.g. "29648_giriGiriLove/1"
        except ValueError:
            episode = "/".join(parts[-3:-1]) if len(parts) >= 3 else ts_file.parent.name
        episodes[episode].append(str(ts_file))
    # 排序每个番剧的分片
    for ep in episodes:
        episodes[ep].sort()
    return dict(episodes)


def merge_ts_to_mp4(ts_files, output_mp4, tmpdir):
    """用 FFmpeg concat 合并 TS 分片为单个 MP4"""
    # 写 concat 列表
    concat_file = os.path.join(tmpdir, "concat.txt")
    with open(concat_file, "w") as f:
        for ts in ts_files:
            f.write(f"file '{ts}'\n")
    # FFmpeg concat (流拷贝，不重编码，极快)
    result = subprocess.run([
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0",
        "-i", concat_file,
        "-c", "copy",
        output_mp4
    ], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg concat failed: {result.stderr[-200:]}")
    return output_mp4


def process_episode(args):
    """处理单个番剧：合并 → 提取 → 返回结果"""
    episode_name, ts_files, scale, workers, tmpdir_base = args
    t0 = time.perf_counter()

    ep_safe = episode_name.replace("/", "_").replace("\\", "_")
    ep_dir = os.path.join(tmpdir_base, ep_safe)
    os.makedirs(ep_dir, exist_ok=True)

    # Step 1: 合并 TS → MP4
    merged_mp4 = os.path.join(ep_dir, "merged.mp4")
    merge_ts_to_mp4(ts_files, merged_mp4, ep_dir)

    # Step 2: 提取张数
    out_csv = os.path.join(ep_dir, "cel_result.csv")
    cmd = [PYTHON, EXTRACT_PARALLEL, merged_mp4,
           "--scale", str(scale), "--workers", str(workers),
           "--output", out_csv]

    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, timeout=3600)
    elapsed = time.perf_counter() - t0

    # 解析结果
    total_cels = 0
    for line in result.stdout.split("\n"):
        if "过滤后总张数:" in line:
            total_cels = int(line.split(":")[-1].strip())

    return {
        "episode": episode_name,
        "segments": len(ts_files),
        "cels": total_cels,
        "elapsed": elapsed,
        "csv": out_csv,
        "mp4": merged_mp4,
    }


def main():
    parser = argparse.ArgumentParser(description="批量处理 kazumi TS 缓存")
    parser.add_argument("--dir", default=KAZUMI_DIR, help="kazumi 缓存目录")
    parser.add_argument("--scale", "-s", type=float, default=0.5, help="缩放比例")
    parser.add_argument("--workers", "-w", type=int, default=2, help="并行番剧数")
    parser.add_argument("--ep-workers", type=int, default=8, help="每部番剧的并行进程数")
    parser.add_argument("--keep-mp4", action="store_true", help="保留合并后的 MP4")
    parser.add_argument("--output", "-o", default="batch_result.json", help="汇总输出 JSON")
    args = parser.parse_args()

    # 扫描番剧
    print("扫描缓存目录...")
    episodes = find_episodes(args.dir)
    if not episodes:
        print(f"未找到 TS 文件: {args.dir}")
        sys.exit(1)

    total_segments = sum(len(v) for v in episodes.values())
    print(f"找到 {len(episodes)} 部番剧, {total_segments} 个 TS 分片")
    print("=" * 60)

    # 按分片数量排序（轻量优先）
    sorted_eps = sorted(episodes.items(), key=lambda x: len(x[1]))

    # 临时目录
    tmpdir_base = tempfile.mkdtemp(prefix="cel_batch_")

    # 构建任务
    tasks = [(name, files, args.scale, args.ep_workers, tmpdir_base)
             for name, files in sorted_eps]

    results = []
    t_start = time.perf_counter()

    # 逐番剧处理（合并是 IO 密集型，提取是 GPU 密集型，限并发）
    for i, task in enumerate(tasks):
        name = task[0]
        n_seg = len(task[1])
        print(f"\n[{i+1}/{len(tasks)}] {name} ({n_seg} 分片)...")

        r = process_episode(task)
        results.append(r)

        dur_min = (sum(ts["elapsed"] for ts in results) / 60) if results else 0
        print(f"  ✓ {r['cels']} 张 | {r['elapsed']:.0f}s | {n_seg / r['elapsed'] * 6:.0f}x 实时")

        # 没勾选保留就删 MP4（省空间）
        if not args.keep_mp4 and os.path.isfile(r["mp4"]):
            os.remove(r["mp4"])

    total_elapsed = time.perf_counter() - t_start

    # 汇总
    total_cels = sum(r["cels"] for r in results)
    print("\n" + "=" * 60)
    print(f"完成! {len(results)} 部番剧 | {total_cels} 总张数 | {total_elapsed/60:.1f} 分钟")
    print("=" * 60)

    # 输出详情
    for r in results:
        print(f"  {r['episode']:40s}  {r['cels']:6d} 张  {r['segments']:4d} 分片  {r['elapsed']:.0f}s")

    # 保存 JSON
    summary = {
        "total_cels": total_cels,
        "total_time_min": round(total_elapsed / 60, 1),
        "episodes": [{k: r[k] for k in ["episode", "cels", "segments", "elapsed"]} for r in results]
    }
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    print(f"\n汇总: {args.output}")

    # 清理
    shutil.rmtree(tmpdir_base, ignore_errors=True)


if __name__ == "__main__":
    main()
