# 万物炼金台：双图融合基线

已在家中电脑的 RTX 5070 Ti 16GB 上完成 FLUX.2 klein 4B distilled 的双参考图复现，能将角色特征融入杯子、运动鞋等物体。当前交付是可重复运行的生成基线；完整课程项目的分割、区域约束、质量筛选和专用交互界面尚未实现。

![提示词对照](output/previews/prompt_comparison.jpg)

上图两次实验使用相同输入、种子 42 和 0.5MP 设置。普通融合指令生成杯身贴图；明确规定耳朵、眼镜、尾巴的结构位置和陶瓷材质后，得到立体猫杯。这是一次提示词对照，尚不能当作普遍有效的方法结论。

## 立即使用

当前远程 ComfyUI 已启动，本机 SSH 隧道可用时打开 **http://127.0.0.1:18888**。这是本机访问地址，推理在家中电脑执行。

网页工作流侧栏的 `alchemy-lab` 文件夹已放好 5 个预设及对应原图，可直接打开 `cat_tumbler_ui.json` 运行。已通过 userdata 接口确认预设可见。下面的手动导入方式适用于迁移到其他机器。

网页导入 `workflows/cat_tumbler_ui.json`，在图像节点 76 上传 `input/iced_drink_cup.png`，节点 81 上传 `input/comfy_cat.png`，然后运行。也可导入 `cat_sneaker_ui.json` 或 `real_cat_mug_refined_ui.json` 并换成对应素材。

断开后，在 PowerShell 中运行 `scripts/connect_ui.ps1` 并保持终端打开。已有隧道占用 18888 时不需要再开一个。

在本项目目录执行以下命令，可批量复现普通提示词、不同种子和 1MP 实验：

```powershell
python scripts/run_experiments.py --cases experiments/fusion_smoke.json --server http://127.0.0.1:18888 --output-dir output/fusion_smoke
```

完整环境和单次运行命令见 [REPRODUCTION.md](REPRODUCTION.md)。后端位于 `.`。后端停止后，可在远程项目目录执行 `.venv/bin/python scripts/start_backend.py`。

## 本次实测

FP8 生成模型 + FP4 文本编码器，4 步 Euler，CFG=1；所有时间为本次单次观测，未做大样本性能统计。

| 案例 | 输出尺寸 | 后端执行 | 上传到下载总耗时 | 人工查看 |
|---|---:|---:|---:|---|
| 官方手袋＋标志 | 368×672 | 5.79秒 | 7.26秒 | 形状保留、颜色和标志迁移成功 |
| 猫角色＋运动鞋 | 960×528 | 3.24秒 | 4.94秒 | 猫耳和配色融入鞋；眼镜细节未完整遵循 |
| 猫角色＋饮料杯，seed 42 | 592×880 | 3.43秒 | 5.12秒 | 立体猫耳、眼镜、尾巴把手 |
| 同输入，普通融合提示 | 592×880 | 3.27秒 | 5.20秒 | 退化为杯身贴图，保留作对照 |
| 结构提示，seed 43 | 592×880 | 3.25秒 | 5.23秒 | 仍得到立体猫杯，细节变化 |
| 结构提示，1MP | 832×1248 | 6.47秒 | 8.47秒 | 立体猫杯，材质细节更丰富 |
| 实拍猫＋白瓷杯，初次 | 1168×880 | 6.88秒 | 8.79秒 | 重复猫脸／四只眼睛，视觉失败 |
| 实拍猫＋白瓷杯，改提示＋seed | 1168×880 | 6.76秒 | 8.35秒 | 单猫脸浮雕；耳朵仍未到杯口 |

后端时间来自 ComfyUI history 的 execution_start / execution_success 时间戳；总耗时包含上传、轮询与下载。第一次调用包含模型加载，其余调用使用同一已启动后端，缓存与模型驻留会影响速度。

1 秒间隔的 `nvidia-smi` 采样中，全卡显存最高 **10166 MiB，约 9.93 GiB**，并非精确的模型独占峰值。因此 **4060／8GB 本地演示尚未验证**，需要单独适配和测量，不能直接套用本机结果。

![实际样例与失败记录](output/previews/gallery.jpg)

8 次运行均完成推理和文件回传，但执行成功不等于图像质量合格。实拍猫的两次结果同时改变了提示词与种子，不能把改善只归因于提示词。当前只有少量素材，不能估计泛化成功率或身份保真度。

## 已保存的复现材料

- `input/`：输入原图；`sources.json` 和 `additional_sources.json` 记录来源与哈希。
- `workflows/`：原始官方模板、可导入 UI 工作流、可提交 API 工作流。
- `scripts/`：构建、单次运行、批量实验、结果汇总、后端启动及权重校验下载脚本。
- `output/`：全部原始输出和每次 `run.json`，包含提示词、种子、工作流、history；没有删除视觉失败结果。
- `output/results.json`、`output/results.csv`：可搬迁的实验索引。`output/previews/` 仅排版展示，未修补生成内容。
- `models.json`：3 个权重的来源、字节数及 SHA-256，约 8.26GB，均已校验。
- `logs/`：后端 commit、依赖快照、安装报告、运行日志、显存采样与环境说明。远程与本机时钟不同，不用跨机器时间戳直接相减。

从现有 CUDA Python 重建本次 Linux/WSL 环境可运行 `bash scripts/setup_remote.sh /path/to/cuda-env/bin/python`。该脚本固定后端 commit，要求已验证的 Torch 2.11.0+cu128；`requirements-frozen.txt` 是本次环境快照，包含继承包，不是可移植的完整锁文件。

## 后续创新落点

建议将题目具体化为 **“面向结构与外观分工的可控双图物体融合”**：让用户指定谁提供结构、谁提供外观，以及需要保留的功能部件，再用分割区域／结构约束降低背景串入、贴图化和重复部件。把原始双图工作流作为基线，分别比较角色分工提示、主体分割、区域约束和候选筛选的贡献。

这些模块目前仍待实现和验证。仅修改提示词、接入模型或换界面，不应写成算法创新。评价应同时覆盖主体特征保留、功能结构保留、单物体一致性、重复部件率、人工偏好和延迟，并公开固定素材、种子及失败结果。

## 上游与归属

- [BFL FLUX.2 官方项目](https://github.com/black-forest-labs/flux2) 与 [klein 4B 模型卡](https://huggingface.co/black-forest-labs/FLUX.2-klein-4B)。
- [ComfyUI](https://github.com/Comfy-Org/ComfyUI) 与 [官方 klein 工作流说明](https://docs.comfy.org/tutorials/flux/flux-2-klein)。
- 后端未修改核心推理代码。模型、后端及输入素材分别遵循各自许可，详见复现说明和输入来源文件；实拍猫素材的来源记录包含非商业许可限制。
