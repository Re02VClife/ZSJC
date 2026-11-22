# AGENTS.md — 项目指引

动画「作画张数」检测工具（Python 3.11 + OpenCV + tkinter）。
GUI 实时截屏统计，离线可批量提取。详细说明见 `README.md`，实测记录见 `日志.md`。

## 运行

```bash
.venv\Scripts\python main.py                          # GUI
.venv\Scripts\python extract.py 视频.mp4 -o out.csv   # 离线提取
.venv\Scripts\python tests\test_pipeline.py           # 回归测试（11 项）
```

## 模块

| 文件 | 职责 |
|---|---|
| `opencv_env.py` | 注册 OpenCV/CUDA DLL 搜索路径，**必须最先 import** |
| `gpu_backend.py` | CUDA 能力自检 + GPU 原语封装 |
| `detection.py` | 差分 / 光流 / 哈希 / 运镜判定 |
| `video_source.py` | dxcam 实时 / OpenCV 视频 / NVDEC 管道 |
| `main.py` | 主程序状态机 + 捕获循环 |
| `ui.py` `widgets.py` `preview.py` | 设置窗口、可拖面板、预览 |
| `extract.py` `extract_parallel.py` `batch_extract.py` | 离线提取与批量 |
| `kazumi_processor.py` | kazumi 番剧缓存导入（GUI / CLI 后台模式） |
| `build.py` → `ZhangShuJianCe.py` | 合并成单文件，**生成物，不要手改** |

## 必须遵守的约定

1. **import 顺序**：任何模块 `import cv2` 之前先 `import opencv_env`，否则 DLL 加载失败。
2. **改判定逻辑必须验证**：`tests/test_pipeline.py` 全过，再用固定素材跑 `extract.py` 对比张数（基线见下）。张数变化 >1% 视为回归。
3. **GPU 的边界**：帧像素 ≥ `GPU_FLOW_MIN_PIXELS`（默认 20 万）才走 GPU 光流；1080p + `SCALE_FACTOR=0.5`（768×405）下 GPU 与 CPU 基本持平，不要假设开 GPU 会变快。
4. **OpenCV CUDA 绑定的坑**：多数函数不填充传入的 `dst`，必须用返回值；`lk.calc` 要 4~6 个位置参数；角点用 `createGoodFeaturesToTrackDetector`。异常要记录并回退，禁止 `except: pass`。
5. **两条解码路径必须一致**：`FFmpegNvDecSource` 与 `FrameSource` 走相同的 crop → 灰度 → 缩放顺序，否则张数会差 20%+。
6. **配置集中**：阈值都在 `config.py` 的 `CONFIG`；新增参数要同时进设置窗口并持久化到 `cel_counter_settings.json`。

## 打包

```bash
python build.py                                                       # 合并单文件
python -m PyInstaller main.spec --noconfirm                           # 完整版（含 CUDA，约 1.4GB）
.venv-lite\Scripts\python -m PyInstaller main_lite.spec --noconfirm   # 轻量版（CPU，约 150MB）
```

发布流程：打 tag → `git push origin <tag>` → GitHub Release 上传 `dist\*.zip`。

## 基线（1080p / 24fps / 8 个标注素材）

| 素材 | CPU 张数 | GPU 张数 |
|---|---|---|
| 书记舞 | 685 | 687 |
| 从零开始 | 660 | 654 |
| 四宫辉夜 | 138 | 140 |
| 四月是你的谎言 | 816 | 812 |
| 坏苹果 | 5332 | 5272 |
| 恋柱 | 840 | 816 |
| 败犬女主 | 50 | 52 |
| jntm | 693 | 693 |

纯 CPU 吞吐（1080p，完整检测）：1 核 64fps / 2 核 102 / 4 核 143 / 8 核 144（单进程 4 核后饱和）。

## 文档习惯

- 改动原理、踩坑、实测数据写进 `日志.md`（按日期分节）。
- 用户可见行为变化同步 `README.md` 和 Release 说明。
- 临时脚本用完即删，不要留在仓库根目录。
