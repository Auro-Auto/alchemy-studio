# 安装指南

Windows 用户先完成 [WSL 环境配置](docs/CLASSMATE_SETUP.md)。

## 运行环境

测试环境为 Linux / WSL2 Ubuntu 22.04、Python 3.10、PyTorch 2.11.0+cu128、RTX 5070 Ti 16GB；8GB 显卡尚未实测。安装前准备 Git、curl、Python venv、NVIDIA 驱动和 Node.js 20.19+ 或 22.12+，预留至少 25GB 磁盘，并在 WSL2 内用 `nvidia-smi` 确认 GPU 可用。

~~~bash
git clone https://github.com/Auro-Auto/alchemy-studio.git
cd alchemy-studio
# 已有指定 CUDA PyTorch 环境可跳过下面四行。
python3.10 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install torch==2.11.0 torchvision==0.26.0 --index-url https://download.pytorch.org/whl/cu128
.venv/bin/python -c 'import torch; print(torch.__version__, torch.cuda.is_available())'
~~~

CUDA 检查应输出 `2.11.0+cu128` 和 `True`。

## 模型下载

~~~bash
bash scripts/setup_remote.sh "$(pwd)/.venv/bin/python"
~~~

安装器接受已有 CUDA Python 的绝对路径，创建项目 `.venv` 并复用其系统包。ComfyUI 固定为 0.44.0，commit `bdb0816a292d221efeb6ec4ae41bcdff3f91fc86`；检测到其他版本时停止安装，不覆盖已有 checkout。

[models.json](models.json) 列出约 8.26GB 生成权重的下载地址、大小与 SHA256。文件先写入 `.part`，校验后再重命名。

| 文件 | 字节数 | 目录 |
| --- | ---: | --- |
| flux-2-klein-4b-fp8.safetensors | 4070624520 | backend/ComfyUI/models/diffusion_models/ |
| qwen_3_4b_fp4_flux2.safetensors | 3848213998 | backend/ComfyUI/models/text_encoders/ |
| flux2-vae.safetensors | 336211292 | backend/ComfyUI/models/vae/ |

镜像不可达时，从 [BFL FP8 仓库](https://huggingface.co/black-forest-labs/FLUX.2-klein-4b-fp8/tree/main) 和 [Comfy 分包仓库](https://huggingface.co/Comfy-Org/Flux-2-Klein-4B/tree/main/split_files) 下载相同文件，放到上述目录，再执行校验。

~~~bash
.venv/bin/python scripts/download_models.py
~~~

## 启动应用

~~~bash
bash scripts/setup_studio.sh
.venv/bin/python scripts/prepare_segmentation.py --check
cd web
npm ci
npm run build
cd ..
.venv/bin/python scripts/start_studio.py
~~~

`setup_studio.sh` 安装应用依赖、下载约 4.57MB U2Netp 并执行 CPU 测试。分割权重保存至 `models/segmentation/u2netp.onnx`，按大小与官方 MD5 校验，使用 ONNX CPUExecutionProvider 推理。

打开 http://127.0.0.1:8890。启动器管理 ComfyUI（8188）与 Studio（8890），均绑定回环地址。首次生成需要加载模型。

~~~bash
curl http://127.0.0.1:8890/api/status
curl http://127.0.0.1:8890/api/creative-options
~~~

`/api/status` 返回的 `comfy_connected` 应为 `true`，`segmentation_available` 表示分割状态。`/api/creative-options` 返回画风与常用指令。示例原料位于 `input/`，风格和作品预览位于 `output/style_previews/`、`output/quality_presets/`。

## 远程访问

~~~bash
ssh -N -L 127.0.0.1:18890:127.0.0.1:8890 USER@GPU_HOST
~~~

打开本机 http://127.0.0.1:18890 并保持终端运行。Windows 也可使用 `scripts/connect_studio.ps1`，传入 SSH 别名及远程项目目录。

开发前端时，先建立 SSH 转发，再在 `web/` 执行 `npm run dev`。Vite 默认将 `/api` 代理到本机 18890；同机开发可将 `web/vite.config.js` 的代理目标改为 8890。

## 测试与实验

默认使用 distilled 4 steps、Euler、CFG 1，候选串行生成，像素预算为 0.5 或 1.0 MP。采样尺寸自动对齐，区域合成映回 A 的画布；局部高清单独采样编辑窗口。

~~~bash
.venv/bin/python -m pytest -q tests
.venv/bin/python scripts/evaluate_studio.py --server http://127.0.0.1:8890 --cases experiments/studio_ablation.json --output-dir output/studio_eval/new_run
.venv/bin/python scripts/evaluate_regions.py --subject-mask --output output/region_evaluation/new_run
~~~

原生 ComfyUI 工作流也可单独运行：

~~~bash
.venv/bin/python scripts/build_workflow.py
.venv/bin/python scripts/run_comfy.py --server http://127.0.0.1:8188 --workflow workflows/cat_sneaker_api.json --image-a "76=input/sneaker_black.png" --image-b "81=input/comfy_cat.png" --output-dir output/cat_sneaker/new_run
~~~

ComfyUI 编辑器导入 `*_ui.json` 后上传参考图，脚本使用 `*_api.json`。

## 故障排查

任务数据位于 `data/`，日志位于 `logs/`，示例与实验结果位于 `output/`。

| 现象 | 排查 |
| --- | --- |
| 页面无法打开 | 运行 `npm run build`，检查 8890 服务和 SSH 转发 |
| 推理未连接 | 检查 `logs/comfyui.log`、8188 端口和模型目录 |
| CUDA 不可用 | 检查驱动、WSL GPU 支持和安装器所用的 Python |
| 权重校验失败 | 核对文件名、大小与 SHA256，检查网络和镜像响应 |
| 分割不可用 | 运行 `prepare_segmentation.py --check`，缺失时加 `--download` |
| 更新后仍是旧版 | 重新构建前端，待任务结束后重启 Python 服务 |
| 取消后仍在等待 | 等待当前处理阶段结束，状态变为已取消后重试 |

Studio 使用一个 worker，确保增强与生图共用 GPU 调度锁。实验方法与指标定义见 [方法与实验](docs/METHOD.md)。
