# 从空目录复现

Windows 游戏本的环境准备、统一安装入口、低显存尝试与验证范围见 [同学安装指南](docs/CLASSMATE_SETUP.md)。

## 环境准备

已验证 Linux / WSL2 Ubuntu 22.04、Python 3.10、PyTorch 2.11.0+cu128、RTX 5070 Ti 16GB。8GB 显卡未实测。准备 Git、curl、Python venv、NVIDIA 驱动和 Node.js 20.19+ 或 22.12+，建议预留 25GB 以上磁盘。WSL2 内先确认 nvidia-smi 可以看到 GPU。

~~~bash
git clone https://github.com/Auro-Auto/alchemy-studio.git
cd alchemy-studio
# 已有指定 CUDA PyTorch 环境可跳过下面四行。
python3.10 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install torch==2.11.0 torchvision==0.26.0 --index-url https://download.pytorch.org/whl/cu128
.venv/bin/python -c 'import torch; print(torch.__version__, torch.cuda.is_available())'
~~~

最后一行应输出 2.11.0+cu128 和 True。PyTorch 会下载 CUDA 运行库，驱动与 GPU 可用性需要在自己的机器确认。

## 安装固定版本与模型

~~~bash
bash scripts/setup_remote.sh "$(pwd)/.venv/bin/python"
~~~

也可传入已有 CUDA Python 的绝对路径。脚本创建项目 .venv，复用该解释器的系统包，安装 ComfyUI 依赖，固定 commit bdb0816a292d221efeb6ec4ae41bcdff3f91fc86（0.44.0）。已有其他版本 checkout 时会退出并保留它。

models.json 指定约 8.26GB 生成权重的下载地址、大小与 SHA256。先下载到 .part，校验通过后重命名。

| 文件 | 字节数 | 目录 |
| --- | ---: | --- |
| flux-2-klein-4b-fp8.safetensors | 4070624520 | backend/ComfyUI/models/diffusion_models/ |
| qwen_3_4b_fp4_flux2.safetensors | 3848213998 | backend/ComfyUI/models/text_encoders/ |
| flux2-vae.safetensors | 336211292 | backend/ComfyUI/models/vae/ |

镜像不可达时，从 [BFL FP8 仓库](https://huggingface.co/black-forest-labs/FLUX.2-klein-4b-fp8/tree/main) 和 [Comfy 分包仓库](https://huggingface.co/Comfy-Org/Flux-2-Klein-4B/tree/main/split_files) 下载相同文件，放到上述目录，再执行校验。

~~~bash
.venv/bin/python scripts/download_models.py
~~~

## 应用、分割与前端

~~~bash
bash scripts/setup_studio.sh
.venv/bin/python scripts/prepare_segmentation.py --check
cd web
npm ci
npm run build
cd ..
.venv/bin/python scripts/start_studio.py
~~~

setup_studio.sh 安装应用依赖、下载约 4.57MB U2Netp 并执行 CPU 测试。分割文件为 models/segmentation/u2netp.onnx，脚本校验大小与官方 MD5，保存来源记录。应用使用 CPUExecutionProvider。

打开 http://127.0.0.1:8890。启动器检查或启动 ComfyUI（8188）与 Studio（8890），绑定回环地址。首次载入模型通常比模型驻留后的生成更慢。

~~~bash
curl http://127.0.0.1:8890/api/status
curl http://127.0.0.1:8890/api/creative-options
~~~

第一个接口的 comfy_connected 应为 true，segmentation_available 对应分割状态。第二个提供 22 种画风与 19 条指令。input/ 是示例原料，output/style_previews/、output/quality_presets/ 是真实预览。

## 另一台电脑演示

~~~bash
ssh -N -L 127.0.0.1:18890:127.0.0.1:8890 USER@GPU_HOST
~~~

打开本机 http://127.0.0.1:18890 并保持终端运行。Windows 也可用 scripts/connect_studio.ps1，传入自己的 SSH 别名及远程项目目录。账号凭据由各自环境管理。

开发前端时，Vite 默认将 /api 转发至本机 18890，需要先建立转发。然后在 web/ 执行 npm run dev；同机开发可将 web/vite.config.js 的代理目标改为 8890。

## 推理与实验

默认配置为 distilled 4 steps、Euler、CFG 1、候选串行、0.5 或 1.0 MP 像素预算。尺寸会对齐。最终区域合成可回到 A 原图尺寸，局部高清集中采样编辑窗口。

~~~bash
.venv/bin/python -m pytest -q tests
.venv/bin/python scripts/evaluate_studio.py --server http://127.0.0.1:8890 --cases experiments/studio_ablation.json --output-dir output/studio_eval/new_run
.venv/bin/python scripts/evaluate_regions.py --subject-mask --output output/region_evaluation/new_run
~~~

原生 ComfyUI 工作流可单独运行，生成器保留原始模板的哈希检查。

~~~bash
.venv/bin/python scripts/build_workflow.py
.venv/bin/python scripts/run_comfy.py --server http://127.0.0.1:8188 --workflow workflows/cat_sneaker_api.json --image-a "76=input/sneaker_black.png" --image-b "81=input/comfy_cat.png" --output-dir output/cat_sneaker/new_run
~~~

编辑器导入 *_ui.json，脚本使用 *_api.json。导入 UI 工作流后仍需上传相应图片。

## 记录与故障

运行数据位于 data/，日志位于 logs/，均由部署生成。公开 output/ 包含示例与历史实验，记录里的部署目录已替换为项目相对路径。docs/BASELINE.md 是早期版本说明，安装以本文为准。

| 现象 | 排查 |
| --- | --- |
| 页面找不到 | npm run build，8890 服务或 SSH 转发 |
| 推理未连接 | logs/comfyui.log、8188、模型目录 |
| CUDA 不可用 | 驱动、WSL GPU、安装器所用解释器 |
| 权重校验失败 | 文件名、大小、SHA256、网络、镜像响应 |
| 分割不可用 | prepare_segmentation.py --check，必要时显式 --download |
| 更新后仍是旧版 | 前端重新构建，Python 等任务结束后再重启 |
| 取消后仍在等待 | 取消在安全阶段边界处理，确认状态后再重试 |

应用使用一个 worker。比较原始生成与最终恢复结果时分别报告指标，方法边界见 docs/METHOD.md。
